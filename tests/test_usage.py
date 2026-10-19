"""
用量落盘（botkit/usage.py）测试

覆盖：
- JsonlUsageStore 按月写文件、文件名/目录结构
- record 把 TurnStats 各字段落成一行 JSON
- list_months 倒序、只认合法 YYYYMM
- read_month 读回、坏行跳过、非法月份/缺文件返回空
- summarize 聚合（token/成功率/平均耗时，空输入）
- record 吞异常：目录不可写时不抛
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from botkit.hooks import TurnStats
from botkit.usage import JsonlUsageStore, UsageStore, summarize


def _stats(**kw) -> TurnStats:
    """造一个带合理默认值的 TurnStats，测试里只覆盖关心的字段"""
    base = dict(
        input_tokens=100,
        output_tokens=20,
        model="test-model",
        tool_calls=1,
        elapsed_ms=1500,
        identity="L220104",
        question="你好",
        source="preview",
        success=True,
        error="",
        agent_name="test-bot",
        request_id="req-abc",
    )
    base.update(kw)
    return TurnStats(**base)


# 固定在 2026-10 的一个时刻，便于断言文件名
_OCT = 1760000000.0  # 2026-10 附近（本地时区可能略有偏差，但同月）


def _clock(ts: float):
    return lambda: ts


class TestJsonlWrite:
    def test_record_creates_monthly_file(self, tmp_path: Path):
        store = JsonlUsageStore(tmp_path, clock=_clock(_OCT))
        store.record(_stats())

        files = list((tmp_path / "usage").glob("usage-*.jsonl"))
        assert len(files) == 1
        # 文件名形如 usage-YYYYMM.jsonl
        name = files[0].name
        assert name.startswith("usage-") and name.endswith(".jsonl")
        tag = name[len("usage-"): -len(".jsonl")]
        assert len(tag) == 6 and tag.isdigit()

    def test_record_writes_all_fields(self, tmp_path: Path):
        store = JsonlUsageStore(tmp_path, clock=_clock(_OCT))
        store.record(_stats(input_tokens=200, output_tokens=50, tool_calls=3))

        files = list((tmp_path / "usage").glob("usage-*.jsonl"))
        line = files[0].read_text(encoding="utf-8").strip()
        row = json.loads(line)
        assert row["input_tokens"] == 200
        assert row["output_tokens"] == 50
        assert row["total_tokens"] == 250
        assert row["tool_calls"] == 3
        assert row["model"] == "test-model"
        assert row["identity"] == "L220104"
        assert row["source"] == "preview"
        assert row["success"] is True
        assert row["request_id"] == "req-abc"
        assert "ts" in row

    def test_record_appends(self, tmp_path: Path):
        store = JsonlUsageStore(tmp_path, clock=_clock(_OCT))
        store.record(_stats(question="一"))
        store.record(_stats(question="二"))
        store.record(_stats(question="三"))

        rows = store.read_month(store.list_months()[0])
        assert [r["question"] for r in rows] == ["一", "二", "三"]

    def test_different_months_different_files(self, tmp_path: Path):
        nov = _OCT + 40 * 24 * 3600  # 跨到下一个月
        JsonlUsageStore(tmp_path, clock=_clock(_OCT)).record(_stats())
        JsonlUsageStore(tmp_path, clock=_clock(nov)).record(_stats())

        files = sorted((tmp_path / "usage").glob("usage-*.jsonl"))
        assert len(files) == 2

    def test_record_swallows_errors(self, tmp_path: Path, monkeypatch):
        """写盘失败不能抛出去影响主流程"""
        store = JsonlUsageStore(tmp_path, clock=_clock(_OCT))

        def boom(*a, **k):
            raise OSError("disk full")

        monkeypatch.setattr(Path, "mkdir", boom)
        # 不应抛
        store.record(_stats())


class TestJsonlRead:
    def test_list_months_sorted_desc(self, tmp_path: Path):
        usage = tmp_path / "usage"
        usage.mkdir()
        for tag in ("202608", "202610", "202609"):
            (usage / f"usage-{tag}.jsonl").write_text("", encoding="utf-8")
        store = JsonlUsageStore(tmp_path)
        assert store.list_months() == ["202610", "202609", "202608"]

    def test_list_months_ignores_junk(self, tmp_path: Path):
        usage = tmp_path / "usage"
        usage.mkdir()
        (usage / "usage-202610.jsonl").write_text("", encoding="utf-8")
        (usage / "usage-notamonth.jsonl").write_text("", encoding="utf-8")
        (usage / "readme.txt").write_text("", encoding="utf-8")
        store = JsonlUsageStore(tmp_path)
        assert store.list_months() == ["202610"]

    def test_list_months_empty_when_no_dir(self, tmp_path: Path):
        store = JsonlUsageStore(tmp_path)
        assert store.list_months() == []

    def test_read_month_skips_bad_lines(self, tmp_path: Path):
        usage = tmp_path / "usage"
        usage.mkdir()
        content = (
            json.dumps({"input_tokens": 1}) + "\n"
            + "这不是 json\n"
            + "\n"  # 空行
            + json.dumps({"input_tokens": 2}) + "\n"
        )
        (usage / "usage-202610.jsonl").write_text(content, encoding="utf-8")
        store = JsonlUsageStore(tmp_path)
        rows = store.read_month("202610")
        assert [r["input_tokens"] for r in rows] == [1, 2]

    def test_read_month_bad_tag(self, tmp_path: Path):
        store = JsonlUsageStore(tmp_path)
        assert store.read_month("nope") == []
        assert store.read_month("2026") == []

    def test_read_month_missing_file(self, tmp_path: Path):
        store = JsonlUsageStore(tmp_path)
        assert store.read_month("202610") == []


class TestSummarize:
    def test_empty(self):
        s = summarize([])
        assert s["turns"] == 0
        assert s["total_tokens"] == 0
        assert s["success_rate"] is None
        assert s["avg_elapsed_ms"] == 0

    def test_aggregates(self):
        rows = [
            {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120,
             "tool_calls": 1, "elapsed_ms": 1000, "success": True},
            {"input_tokens": 200, "output_tokens": 30, "total_tokens": 230,
             "tool_calls": 2, "elapsed_ms": 3000, "success": False},
        ]
        s = summarize(rows)
        assert s["turns"] == 2
        assert s["input_tokens"] == 300
        assert s["output_tokens"] == 50
        assert s["total_tokens"] == 350
        assert s["tool_calls"] == 3
        assert s["success_count"] == 1
        assert s["success_rate"] == 0.5
        assert s["avg_elapsed_ms"] == 2000

    def test_success_defaults_true(self):
        """没有 success 字段的老记录算成功"""
        s = summarize([{"input_tokens": 1}])
        assert s["success_count"] == 1
        assert s["success_rate"] == 1.0


def test_from_config_returns_jsonl(tmp_path: Path):
    store = UsageStore.from_config(tmp_path)
    assert isinstance(store, JsonlUsageStore)
    assert store.usage_dir == tmp_path / "usage"
