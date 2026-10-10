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

import logging
from typing import Any

import aiohttp

from botkit.config import LlmConfig
from botkit.llm.base import LlmError, LlmResponse, normalize_response

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
        try:
            async with session.post(
                self.cfg.api_url, headers=headers, json=body
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

    return normalize_response(content, [tc for tc in raw_tool_calls if isinstance(tc, dict)])
