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
from typing import Any, Protocol

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
class LlmResponse:
    content: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)
    message: dict[str, Any] = field(default_factory=dict)

    @property
    def wants_tools(self) -> bool:
        return bool(self.tool_calls)


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
    content: str | None, raw_tool_calls: list[dict[str, Any]]
) -> LlmResponse:
    """
    把「文本 + 原始 tool_calls 字典列表」变成 LlmResponse。
    raw_tool_calls 里每项形如
        {"id": ..., "function": {"name": ..., "arguments": "<json 字符串>"}}
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
    )
