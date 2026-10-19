"""
LLM 后端的统一接口

两个后端（openai 官方 SDK / aiohttp 裸 HTTP）都收敛到这里定义的形状，
engine.py 不需要知道底下用的是哪个。

统一的产物是 LlmResponse：
- message   直接能 append 进 messages 的 assistant 消息（OpenAI wire 格式）
- tool_calls 解析好的工具调用列表
- content   纯文本回复（没有工具调用时就是最终回复）

message 是我们自己按固定形状拼的，不是把后端返回的对象整个 dump 回去。
这样两个后端产出完全一致，也不会把某个端点特有的多余字段带进下一轮请求
（有些兼容端点会因为多余字段直接报 400）。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Protocol

logger = logging.getLogger("botkit.llm")


class LlmError(Exception):
    """调用 LLM 失败（网络、鉴权、返回格式不对等）"""


@dataclass
class ToolCall:
    """一次工具调用请求"""

    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    raw_arguments: str = ""


@dataclass
class TokenUsage:
    """
    一次 LLM 调用消耗的 token。

    字段名用协议中立的 input/output：OpenAI 协议叫 prompt_tokens /
    completion_tokens，Anthropic Messages 叫 input_tokens / output_tokens，
    各后端解析时统一到这里。

    支持相加，方便把一轮对话里多次 LLM 调用（function calling 循环会调多次）
    累加成总量。
    """

    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def __add__(self, other: "TokenUsage | None") -> "TokenUsage":
        if other is None:
            return TokenUsage(self.input_tokens, self.output_tokens)
        return TokenUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
        )

    def __bool__(self) -> bool:
        """全为 0 时视为「没拿到用量」，日志里可据此跳过"""
        return bool(self.input_tokens or self.output_tokens)


def parse_usage(raw: Any) -> TokenUsage | None:
    """
    从响应里的 usage 字段解析出 TokenUsage。

    同时认两套命名（OpenAI 的 prompt/completion、Anthropic 的 input/output），
    所以三个后端都能直接用它。拿不到就返回 None —— 有些端点不返回用量，
    这不是错误，只是没有这项数据。
    """
    if not isinstance(raw, dict):
        return None

    def _int(*keys: str) -> int:
        for key in keys:
            val = raw.get(key)
            if isinstance(val, bool):
                continue
            if isinstance(val, (int, float)):
                return int(val)
        return 0

    usage = TokenUsage(
        input_tokens=_int("input_tokens", "prompt_tokens"),
        output_tokens=_int("output_tokens", "completion_tokens"),
    )
    return usage if usage else None


@dataclass
class LlmResponse:
    content: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)
    message: dict[str, Any] = field(default_factory=dict)
    # 本次调用的 token 用量。None = 端点没返回这项数据
    usage: TokenUsage | None = None

    @property
    def wants_tools(self) -> bool:
        return bool(self.tool_calls)


@dataclass
class StreamChunk:
    """
    流式补全的一个片段（SSE 用）。

    一次 stream() 调用会先 yield 若干个带 delta 的 chunk（文本增量，done=False），
    最后 yield 一个 done=True 的 chunk，并在 response 里带上整轮聚合好的
    LlmResponse（含完整 content 和 tool_calls）。

    这样调用方（engine）既能实时拿到文本增量做打字机效果，又能在收尾时像
    非流式 complete 一样判断这一轮是不是要调工具（response.wants_tools）。
    """

    delta: str = ""
    done: bool = False
    response: LlmResponse | None = None


class LlmBackend(Protocol):
    """所有后端都要实现这个"""

    name: str

    async def complete(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> LlmResponse:
        """
        跑一次补全。失败必须抛 LlmError（而不是返回 None），
        让上层能区分「模型说没工具要调」和「根本没调通」。
        """
        ...

    def stream(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> AsyncIterator[StreamChunk]:
        """
        流式补全（SSE 用）。返回一个异步迭代器，先吐若干文本增量 chunk，
        最后吐一个 done=True 的 chunk（带整轮聚合好的 LlmResponse）。

        失败同样抛 LlmError。工具调用轮通常没有文本增量，done chunk 的
        response.wants_tools 为真，由上层决定执行工具后再开下一轮流式。
        """
        ...

    async def aclose(self) -> None:
        """释放连接资源"""
        ...


# --------------------------------------------------------------------------
# 两个后端共用的规范化逻辑
# --------------------------------------------------------------------------


def parse_tool_arguments(raw: str | None, tool_name: str) -> dict[str, Any]:
    """
    工具参数是 LLM 生成的 JSON 字符串，可能不合法。
    解析失败时返回空 dict —— 工具那边会因为缺参数报错，
    错误信息回灌给 LLM，它自己会重试。比在这里抛异常好。
    """
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as e:
        logger.warning("工具 %s 的参数不是合法 JSON（%s）：%s", tool_name, e, raw[:200])
        return {}
    if not isinstance(parsed, dict):
        logger.warning("工具 %s 的参数不是对象：%s", tool_name, raw[:200])
        return {}
    return parsed


def build_assistant_message(
    content: str | None, tool_calls: list[ToolCall]
) -> dict[str, Any]:
    """按 OpenAI wire 格式拼 assistant 消息，用于回灌进下一轮 messages"""
    message: dict[str, Any] = {"role": "assistant"}
    # 有 tool_calls 时 content 通常是 None，但有些模型会同时给出思考文本
    message["content"] = content if content else None
    if tool_calls:
        message["tool_calls"] = [
            {
                "id": tc.id,
                "type": "function",
                "function": {"name": tc.name, "arguments": tc.raw_arguments},
            }
            for tc in tool_calls
        ]
    return message


def normalize_response(
    content: str | None,
    raw_tool_calls: list[dict[str, Any]],
    usage: TokenUsage | None = None,
) -> LlmResponse:
    """
    把「文本 + 原始 tool_calls 字典列表」变成 LlmResponse。
    raw_tool_calls 里每项形如
        {"id": ..., "function": {"name": ..., "arguments": "<json 字符串>"}}

    usage 可选：端点返回了 token 用量就带上，供上层累加和记日志。
    """
    tool_calls: list[ToolCall] = []
    for i, raw in enumerate(raw_tool_calls or []):
        fn = raw.get("function") or {}
        name = fn.get("name") or ""
        if not name:
            logger.warning("忽略一个没有工具名的 tool_call：%s", raw)
            continue
        raw_args = fn.get("arguments") or ""
        if not isinstance(raw_args, str):
            # 少数端点直接返回对象而不是 JSON 字符串
            raw_args = json.dumps(raw_args, ensure_ascii=False)
        tool_calls.append(
            ToolCall(
                id=raw.get("id") or f"call_{i}",
                name=name,
                arguments=parse_tool_arguments(raw_args, name),
                raw_arguments=raw_args,
            )
        )

    return LlmResponse(
        content=content,
        tool_calls=tool_calls,
        message=build_assistant_message(content, tool_calls),
        usage=usage,
    )


# --------------------------------------------------------------------------
# 流式聚合：把增量到达的文本和 tool_calls 片段拼成完整的 LlmResponse
# --------------------------------------------------------------------------


class StreamAccumulator:
    """
    流式响应里，文本和 tool_calls 都是一小段一小段到达的。这个累加器把它们
    按到达顺序拼起来，结束时产出和非流式 complete 完全一致的 LlmResponse。

    tool_calls 的增量按 index 归位：同一个 index 的多个片段，name 取第一个
    非空的，arguments 字符串按到达顺序拼接。这是 OpenAI 流式 tool_calls 的
    标准增量形态，两个后端都复用这套逻辑。
    """

    def __init__(self) -> None:
        self._text_parts: list[str] = []
        # index -> {"id": str, "name": str, "arguments": str}
        self._tool_calls: dict[int, dict[str, str]] = {}
        self._usage: TokenUsage | None = None

    def add_text(self, delta: str | None) -> str:
        """累加一段文本增量，返回这段增量（空则返回空串，便于调用方直接推送）"""
        if not delta:
            return ""
        self._text_parts.append(delta)
        return delta

    def add_tool_call_delta(
        self,
        index: int,
        *,
        call_id: str | None = None,
        name: str | None = None,
        arguments: str | None = None,
    ) -> None:
        """累加一个 tool_call 增量片段（按 index 归位）"""
        slot = self._tool_calls.setdefault(
            index, {"id": "", "name": "", "arguments": ""}
        )
        if call_id:
            slot["id"] = call_id
        if name:
            slot["name"] = name
        if arguments:
            slot["arguments"] += arguments

    def set_usage(self, usage: TokenUsage | None) -> None:
        """
        记下这次流式调用的 token 用量。可多次调用，按字段合并：
        **非零值覆盖旧值，而不是相加。**

        覆盖而非累加是为了同时适配两种协议：
        - OpenAI：用量在最后一个 chunk 一次性给全，覆盖即正确；
        - Anthropic：input_tokens 在 message_start 给，output_tokens 在
          message_delta 给，而且后者是**累积值**（不是增量），
          累加会把中间状态重复计入。

        跨轮累加是另一回事（一轮对话里 function calling 会多次调 LLM），
        那个用 TokenUsage 的 `+`，由 engine 负责。
        """
        if usage is None:
            return
        if self._usage is None:
            self._usage = TokenUsage(usage.input_tokens, usage.output_tokens)
            return
        if usage.input_tokens:
            self._usage.input_tokens = usage.input_tokens
        if usage.output_tokens:
            self._usage.output_tokens = usage.output_tokens

    @property
    def text(self) -> str:
        return "".join(self._text_parts)

    def finish(self) -> LlmResponse:
        """把累积的内容拼成和非流式 complete 一致的 LlmResponse"""
        raw_tool_calls = [
            {
                "id": self._tool_calls[idx]["id"],
                "function": {
                    "name": self._tool_calls[idx]["name"],
                    "arguments": self._tool_calls[idx]["arguments"],
                },
            }
            for idx in sorted(self._tool_calls)
        ]
        content = self.text or None
        return normalize_response(content, raw_tool_calls, self._usage)
