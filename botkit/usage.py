"""
用量落盘统计

骨架内置的本地用量记录：每轮对话结束后把这轮的 token / 耗时 / 工具次数等
（TurnStats）追加写到 Agent 目录下，按月分文件。这样不接任何外部系统，
Agent 自己目录里就留了一份可查、可聚合的用量账，配置页「用量」tab 直接读它。

和 on_usage hook 的关系：两者独立并行。落盘是骨架保底（本地留痕，先写），
on_usage 是把用量报给外部系统（业务侧自理，后写）。外部上报挂了不影响本地账，
反之亦然。

UsageStore 是抽象接口（record / list_months / read_month）。当前内置
JsonlUsageStore：按月写 bots/<agent>/usage/usage-YYYYMM.jsonl，一行一轮。
JSONL 选型是因为它既能逐行追加（写便宜、不怕进程中断），又好按行聚合，
且天然按月切分、体积可控——比纯文本日志好聚合，又不用上数据库。

落盘属「尽力而为」：写盘失败只记日志，绝不影响已经给用户的回复，也不影响
on_usage 上报。usage/ 目录含用户标识，默认进 .gitignore，不提交。
"""

from __future__ import annotations

import json
import logging
import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from botkit.hooks import TurnStats

logger = logging.getLogger("botkit.usage")

USAGE_DIRNAME = "usage"
# 文件名形如 usage-202610.jsonl
_FILE_PREFIX = "usage-"
_FILE_SUFFIX = ".jsonl"


def _month_tag(ts: float) -> str:
    """时间戳（epoch 秒）→ 本地月份标签 YYYYMM"""
    return datetime.fromtimestamp(ts).strftime("%Y%m")


def _is_month_tag(tag: str) -> bool:
    """校验 YYYYMM 形式，防止把乱七八糟的文件名当月份"""
    return len(tag) == 6 and tag.isdigit()


class UsageStore(ABC):
    """
    用量存储接口。

    定义「记录一轮用量」和「按月读取」应提供的能力，不绑定具体后端。内置实现
    是 JsonlUsageStore（Agent 目录下按月 JSONL）；调用方（build_pipeline 等）
    只依赖本接口。

    from_config 是便捷工厂，当前固定返回 JSONL 实现，落盘到传入的 bot 目录。
    将来若要换存储（如集中式库），实现同一接口即可，对话逻辑不变。
    """

    @classmethod
    def from_config(cls, bot_dir: str | Path) -> "UsageStore":
        """按 bot 目录建一个用量存储。当前固定返回 JSONL 实现。"""
        return JsonlUsageStore(bot_dir)

    @abstractmethod
    def record(self, stats: "TurnStats") -> None:
        """
        记录一轮用量。尽力而为：失败只记日志，绝不抛出影响主流程。
        """
        ...

    @abstractmethod
    def list_months(self) -> list[str]:
        """已有记录的月份标签（YYYYMM），按时间倒序（最新在前）。"""
        ...

    @abstractmethod
    def read_month(self, month: str) -> list[dict[str, Any]]:
        """
        读某个月（YYYYMM）的全部记录，按写入顺序返回。坏行跳过。
        月份不合法或文件不存在时返回空列表。
        """
        ...


class JsonlUsageStore(UsageStore):
    """
    按月 JSONL 落盘：bots/<agent>/usage/usage-YYYYMM.jsonl，一行一轮对话。

    写用追加（append），不覆盖、不读改写，进程中断最多丢当前这一行。
    目录按需创建。所有写操作吞异常——用量账再重要也不能拖垮对话。
    """

    def __init__(
        self,
        bot_dir: str | Path,
        *,
        clock: Callable[[], float] | None = None,
    ):
        """
        bot_dir  Agent 目录（BotConfig.bot_dir），usage/ 建在它下面
        clock    取当前时间的函数，测试里可注入假时钟
        """
        self.usage_dir = Path(bot_dir) / USAGE_DIRNAME
        self._clock = clock or time.time

    # -- 写 ----------------------------------------------------------------

    def _file_for(self, month: str) -> Path:
        return self.usage_dir / f"{_FILE_PREFIX}{month}{_FILE_SUFFIX}"

    @staticmethod
    def _to_row(stats: "TurnStats", ts: float) -> dict[str, Any]:
        """TurnStats → 要落盘的一行。ts 作为记录时间（ISO8601，带本地时区）。"""
        return {
            "ts": datetime.fromtimestamp(ts).astimezone().isoformat(timespec="seconds"),
            "request_id": stats.request_id,
            "identity": stats.identity,
            "source": stats.source,
            "model": stats.model,
            "input_tokens": stats.input_tokens,
            "output_tokens": stats.output_tokens,
            "total_tokens": stats.total_tokens,
            "tool_calls": stats.tool_calls,
            "elapsed_ms": stats.elapsed_ms,
            "success": stats.success,
            "error": stats.error,
            "question": stats.question,
        }

    def record(self, stats: "TurnStats") -> None:
        """追加一行到当月文件。失败只记日志，不抛。"""
        try:
            now = self._clock()
            row = self._to_row(stats, now)
            self.usage_dir.mkdir(parents=True, exist_ok=True)
            path = self._file_for(_month_tag(now))
            line = json.dumps(row, ensure_ascii=False)
            with path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            logger.exception("用量落盘失败（已忽略，不影响本轮回复）")

    # -- 读 ----------------------------------------------------------------

    def list_months(self) -> list[str]:
        """扫 usage/ 目录里的 usage-YYYYMM.jsonl，返回月份标签倒序。"""
        if not self.usage_dir.is_dir():
            return []
        months: list[str] = []
        for p in self.usage_dir.glob(f"{_FILE_PREFIX}*{_FILE_SUFFIX}"):
            tag = p.name[len(_FILE_PREFIX): -len(_FILE_SUFFIX)]
            if _is_month_tag(tag):
                months.append(tag)
        months.sort(reverse=True)
        return months

    def read_month(self, month: str) -> list[dict[str, Any]]:
        """读某月全部记录，坏行跳过。月份非法或文件不在返回空。"""
        if not _is_month_tag(month):
            return []
        path = self._file_for(month)
        if not path.is_file():
            return []
        rows: list[dict[str, Any]] = []
        try:
            with path.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rows.append(json.loads(line))
                    except json.JSONDecodeError:
                        logger.warning("用量记录有坏行，已跳过：%s", path.name)
        except OSError:
            logger.exception("读取用量文件失败：%s", path)
            return []
        return rows


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """
    把一个月的记录聚合成指标卡需要的数字。

    返回：对话数、总/输入/输出 token、工具调用次数、成功数、成功率（0~1，
    无记录时为 None）、平均耗时毫秒。纯计算，给 UI 聚合接口和测试共用。
    """
    turns = len(rows)
    input_tokens = sum(int(r.get("input_tokens") or 0) for r in rows)
    output_tokens = sum(int(r.get("output_tokens") or 0) for r in rows)
    total_tokens = sum(int(r.get("total_tokens") or 0) for r in rows)
    tool_calls = sum(int(r.get("tool_calls") or 0) for r in rows)
    success_count = sum(1 for r in rows if r.get("success", True))
    elapsed_sum = sum(int(r.get("elapsed_ms") or 0) for r in rows)
    return {
        "turns": turns,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "tool_calls": tool_calls,
        "success_count": success_count,
        "success_rate": (success_count / turns) if turns else None,
        "avg_elapsed_ms": (elapsed_sum // turns) if turns else 0,
    }
