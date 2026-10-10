"""
会话上下文管理

按会话 key 保存多轮对话历史（user/assistant 消息），支持：
- scope 策略决定谁跟谁共享上下文（群内按人隔离 / 整群共享 / 跨群按人）
- 轮次上限，防止 context 越来越长、token 越来越贵
- 空闲过期清理，防止内存无限膨胀

SessionStore 是会话存储的抽象接口（key_for / get_history / append_turn /
clear / turn_count / active_sessions）。当前内置的 InMemorySessionStore 把数据
存在进程内的 dict 里，重启即丢，对单 Agent 场景够用。将来门户水平扩展时会再加
一个 RedisSessionStore 实现同一接口，调用方（build_pipeline 等）只依赖接口、
不感知具体实现，换存储后端无需改动对话逻辑。
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from typing import Any, Callable

from botkit.config import SessionConfig

# scope 取值
SCOPE_GROUP_USER = "group_user"
SCOPE_GROUP = "group"
SCOPE_USER = "user"


def build_session_key(frame: dict[str, Any], scope: str) -> str:
    """
    从企微消息帧算出会话隔离 key。

    scope 语义：
      group_user  群聊按「群+人」隔离，单聊按人。同一群里两个人互不干扰。
      group       群聊整群共享一份上下文，单聊按人。适合协作类场景。
      user        同一个人在所有会话里共享一份上下文（跨群记忆）。

    群聊里拿不到 chatid 时（理论上不该发生）退化成按人隔离，
    这样最坏情况是丢了群维度，而不是把不同群的对话混在一起。
    """
    body = frame.get("body") or {}
    userid = (body.get("from") or {}).get("userid") or ""
    chatid = body.get("chatid") or ""
    chattype = body.get("chattype") or ""
    is_group = chattype == "group" and bool(chatid)

    if scope == SCOPE_USER:
        return f"user:{userid}"

    if scope == SCOPE_GROUP:
        if is_group:
            return f"group:{chatid}"
        return f"single:{userid}"

    # 默认 group_user
    if is_group:
        return f"group:{chatid}:{userid}"
    return f"single:{userid}"


class SessionStore(ABC):
    """
    会话存储接口。

    定义多轮对话历史存储应提供的能力，不绑定具体后端。内置实现是
    InMemorySessionStore（进程内 dict）；门户水平扩展阶段会加
    RedisSessionStore，两者实现同一组方法，调用方只依赖本接口。

    from_config 是便捷工厂，当前返回内存实现；将来可按配置
    （session.backend: memory|redis）切换具体实现，调用方代码不变。
    """

    @classmethod
    def from_config(
        cls, cfg: SessionConfig, *, clock: Callable[[], float] | None = None
    ) -> SessionStore:
        """按配置建一个会话存储。当前固定返回内存实现。"""
        return InMemorySessionStore(
            max_turns=cfg.max_turns,
            ttl_seconds=cfg.ttl_seconds,
            scope=cfg.scope,
            clock=clock,
        )

    @abstractmethod
    def key_for(self, frame: dict[str, Any]) -> str:
        """按本 store 的 scope 算出消息帧对应的会话 key"""
        ...

    @abstractmethod
    def get_history(self, key: str) -> list[dict[str, str]]:
        """取会话历史，返回可直接拼进 LLM messages 的列表（副本）"""
        ...

    @abstractmethod
    def append_turn(self, key: str, user_message: str, assistant_reply: str) -> None:
        """追加一轮对话（1 条 user + 1 条 assistant）"""
        ...

    @abstractmethod
    def clear(self, key: str) -> None:
        """清空某个会话"""
        ...

    @abstractmethod
    def turn_count(self, key: str) -> int:
        """该会话当前保留了几轮"""
        ...

    @abstractmethod
    def active_sessions(self) -> int:
        """当前活跃会话数，用于日志和排查"""
        ...


class InMemorySessionStore(SessionStore):
    """多轮对话历史存储（进程内 dict 实现，重启即丢）"""

    def __init__(
        self,
        max_turns: int = 10,
        ttl_seconds: int = 1800,
        scope: str = SCOPE_GROUP_USER,
        *,
        clock: Callable[[], float] | None = None,
    ):
        """
        max_turns   每个会话最多保留的对话轮数（1 轮 = 1 条 user + 1 条 assistant）
        ttl_seconds 会话空闲多久后过期清理
        scope       会话隔离粒度，见 build_session_key
        clock       取当前时间的函数，测试里可以注入假时钟
        """
        self.max_turns = max_turns
        self.ttl_seconds = ttl_seconds
        self.scope = scope
        self._clock = clock or time.time
        # key -> {"messages": [...], "last_active": ts}
        self._store: dict[str, dict[str, Any]] = {}

    # -- key ---------------------------------------------------------------

    def key_for(self, frame: dict[str, Any]) -> str:
        """按本 store 的 scope 算出消息帧对应的会话 key"""
        return build_session_key(frame, self.scope)

    # -- 读写 --------------------------------------------------------------

    def _now(self) -> float:
        return self._clock()

    def _cleanup(self) -> None:
        now = self._now()
        expired = [
            k
            for k, v in self._store.items()
            if now - v["last_active"] > self.ttl_seconds
        ]
        for k in expired:
            del self._store[k]

    def get_history(self, key: str) -> list[dict[str, str]]:
        """
        取会话历史，返回可直接拼进 LLM messages 的列表。
        过期的会话返回空列表。返回的是副本，调用方改不到内部状态。
        """
        self._cleanup()
        entry = self._store.get(key)
        if not entry:
            return []
        return [dict(m) for m in entry["messages"]]

    def append_turn(self, key: str, user_message: str, assistant_reply: str) -> None:
        """追加一轮对话，并按 max_turns 截断最早的部分"""
        entry = self._store.get(key)
        if entry is None:
            entry = {"messages": [], "last_active": self._now()}
            self._store[key] = entry

        entry["messages"].append({"role": "user", "content": user_message})
        entry["messages"].append({"role": "assistant", "content": assistant_reply})

        limit = self.max_turns * 2
        if len(entry["messages"]) > limit:
            entry["messages"] = entry["messages"][-limit:]

        entry["last_active"] = self._now()

    def clear(self, key: str) -> None:
        """清空某个会话（用户说"重新开始"时用）"""
        self._store.pop(key, None)

    def turn_count(self, key: str) -> int:
        """该会话当前保留了几轮"""
        entry = self._store.get(key)
        if not entry:
            return 0
        return len(entry["messages"]) // 2

    def active_sessions(self) -> int:
        """当前活跃会话数（先清过期）。用于日志和排查内存占用"""
        self._cleanup()
        return len(self._store)
