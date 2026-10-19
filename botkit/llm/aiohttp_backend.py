"""
aiohttp 裸 HTTP 后端

为什么保留这个：不是所有「OpenAI 兼容」端点都真的兼容。常见情况有
响应里多塞自定义字段、tool_calls 结构有偏差、鉴权头非标准、
choices 里少字段等。这些情况 openai 官方 SDK 会在 pydantic 校验阶段
直接抛错，拿不到原始响应；裸 HTTP 能把原始 JSON 拿到手自己兜，
排查时也能直接看到对方到底返了什么。

和 openai 后端的区别：
- api_url 要填完整端点（含 /chat/completions），不做拼接
- 没有自动重试，失败就是失败
"""

from __future__ import annotations

import json
import logging
from typing import Any, AsyncIterator

import aiohttp

from botkit.config import LlmConfig
from botkit.llm.base import (
    LlmError,
    LlmResponse,
    StreamAccumulator,
    StreamChunk,
    normalize_response,
    parse_usage,
)

logger = logging.getLogger("botkit.llm.aiohttp")

# 报错时回显的响应体长度上限，避免把整个响应刷进日志
_ERROR_BODY_LIMIT = 500


class AiohttpBackend:
    name = "aiohttp"

    def __init__(self, cfg: LlmConfig, session: aiohttp.ClientSession | None = None):
        self.cfg = cfg
        self._session = session
        self._owns_session = session is None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self.cfg.timeout_seconds)
            )
            self._owns_session = True
        return self._session

    async def complete(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> LlmResponse:
        body: dict[str, Any] = {
            "model": self.cfg.model,
            "messages": messages,
            "stream": False,
        }
        if tools:
            body["tools"] = tools

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.cfg.api_key}",
        }

        session = await self._get_session()
        # 配了代理就按请求级 proxy 走转发；没配时传 None 等同不走代理。
        proxy = self.cfg.http_proxy or None
        try:
            async with session.post(
                self.cfg.api_url, headers=headers, json=body, proxy=proxy
            ) as resp:
                text = await resp.text()
                if resp.status != 200:
                    raise LlmError(
                        f"LLM 返回 HTTP {resp.status}：{text[:_ERROR_BODY_LIMIT]}"
                    )
                try:
                    data = await resp.json(content_type=None)
                except Exception as e:
                    raise LlmError(
                        f"LLM 返回的不是合法 JSON（{e}）：{text[:_ERROR_BODY_LIMIT]}"
                    ) from e
        except LlmError:
            raise
        except aiohttp.ClientError as e:
            raise LlmError(f"请求 LLM 失败（{type(e).__name__}）：{e}") from e
        except TimeoutError as e:
            raise LlmError(f"请求 LLM 超时（{self.cfg.timeout_seconds:g}s）") from e

        return _parse_openai_payload(data)

    async def stream(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> AsyncIterator[StreamChunk]:
        body: dict[str, Any] = {
            "model": self.cfg.model,
            "messages": messages,
            "stream": True,
        }
        if tools:
            body["tools"] = tools

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.cfg.api_key}",
        }

        acc = StreamAccumulator()
        session = await self._get_session()
        # 同 complete：配了代理就带上，没配传 None。
        proxy = self.cfg.http_proxy or None
        try:
            async with session.post(
                self.cfg.api_url, headers=headers, json=body, proxy=proxy
            ) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    raise LlmError(
                        f"LLM 返回 HTTP {resp.status}：{text[:_ERROR_BODY_LIMIT]}"
                    )
                async for raw_line in resp.content:
                    line = raw_line.decode("utf-8", "replace").strip()
                    if not line or not line.startswith("data:"):
                        continue
                    payload = line[len("data:"):].strip()
                    if payload == "[DONE]":
                        break
                    try:
                        data = json.loads(payload)
                    except json.JSONDecodeError:
                        logger.warning("流式分片不是合法 JSON，跳过：%s", payload[:200])
                        continue
                    for piece in _iter_stream_deltas(data, acc):
                        yield piece
        except LlmError:
            raise
        except aiohttp.ClientError as e:
            raise LlmError(f"请求 LLM 失败（{type(e).__name__}）：{e}") from e
        except TimeoutError as e:
            raise LlmError(f"请求 LLM 超时（{self.cfg.timeout_seconds:g}s）") from e

        yield StreamChunk(done=True, response=acc.finish())

    async def aclose(self) -> None:
        if self._session is not None and self._owns_session and not self._session.closed:
            await self._session.close()


def _parse_openai_payload(data: Any) -> LlmResponse:
    """
    从 OpenAI 兼容的响应体里取出回复和工具调用。
    对结构做防御性检查，因为这个后端存在的意义就是应付不规范的端点。
    """
    if not isinstance(data, dict):
        raise LlmError(f"LLM 返回的顶层不是对象，而是 {type(data).__name__}")

    # 有些端点把错误也用 200 返回，错误信息放 error 字段
    if "error" in data and not data.get("choices"):
        raise LlmError(f"LLM 返回错误：{data['error']}")

    choices = data.get("choices")
    if not choices or not isinstance(choices, list):
        raise LlmError(f"LLM 返回里没有可用的 choices：{str(data)[:_ERROR_BODY_LIMIT]}")

    first = choices[0]
    if not isinstance(first, dict):
        raise LlmError(f"choices[0] 不是对象，而是 {type(first).__name__}")

    msg = first.get("message")
    if not isinstance(msg, dict):
        raise LlmError(
            f"choices[0].message 缺失或格式不对：{str(first)[:_ERROR_BODY_LIMIT]}"
        )

    content = msg.get("content")
    if content is not None and not isinstance(content, str):
        content = str(content)

    raw_tool_calls = msg.get("tool_calls") or []
    if not isinstance(raw_tool_calls, list):
        logger.warning("tool_calls 不是列表，忽略：%r", raw_tool_calls)
        raw_tool_calls = []

    return normalize_response(
        content,
        [tc for tc in raw_tool_calls if isinstance(tc, dict)],
        parse_usage(data.get("usage")),
    )


def _iter_stream_deltas(data: Any, acc: StreamAccumulator):
    """
    从一个流式分片（OpenAI 兼容的 chat.completion.chunk）里取出增量，
    喂进累加器，并把文本增量作为 StreamChunk 产出。

    结构：{"choices": [{"delta": {"content": "...", "tool_calls": [...]}}]}
    对不规范结构做防御，坏分片直接跳过（这正是裸 HTTP 后端存在的意义）。
    """
    if not isinstance(data, dict):
        return

    # 用量在 choices 之前处理：带 usage 的分片通常没有 choices，
    # 放到后面会被提前 return 掉。OpenAI 协议下流式默认不给 usage
    # （需请求时带 stream_options.include_usage），端点给了就记下。
    usage = parse_usage(data.get("usage"))
    if usage is not None:
        acc.set_usage(usage)

    choices = data.get("choices")
    if not choices or not isinstance(choices, list):
        return
    first = choices[0]
    if not isinstance(first, dict):
        return
    delta = first.get("delta")
    if not isinstance(delta, dict):
        return

    text = delta.get("content")
    if isinstance(text, str) and text:
        acc.add_text(text)
        yield StreamChunk(delta=text)

    for i, tc in enumerate(delta.get("tool_calls") or []):
        if not isinstance(tc, dict):
            continue
        fn = tc.get("function") or {}
        acc.add_tool_call_delta(
            tc.get("index", i) or 0,
            call_id=tc.get("id"),
            name=fn.get("name") if isinstance(fn, dict) else None,
            arguments=fn.get("arguments") if isinstance(fn, dict) else None,
        )
