"""
openai 官方 SDK 后端（默认）

用 AsyncOpenAI 打 OpenAI 兼容端点（百炼、vLLM、OneAPI 等都可以）。
好处是自带重试、超时、连接池和类型化的 tool_calls，不用自己解析。

注意 base_url 要填到 /v1 为止，不含 /chat/completions，SDK 会自己拼路径。
配置层已经对这一点做了检查并给出提示。
"""

from __future__ import annotations

import logging
from typing import Any

from openai import APIError, AsyncOpenAI, OpenAIError

from botkit.config import LlmConfig
from botkit.llm.base import LlmError, LlmResponse, normalize_response

logger = logging.getLogger("botkit.llm.openai")


class OpenAIBackend:
    name = "openai"

    def __init__(self, cfg: LlmConfig):
        self.cfg = cfg
        self._client = AsyncOpenAI(
            api_key=cfg.api_key,
            base_url=cfg.api_url,
            timeout=cfg.timeout_seconds,
        )

    async def complete(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> LlmResponse:
        kwargs: dict[str, Any] = {
            "model": self.cfg.model,
            "messages": messages,
            "stream": False,
        }
        # tools 传空列表有些端点会报 400，没工具时干脆不带这个字段
        if tools:
            kwargs["tools"] = tools

        try:
            resp = await self._client.chat.completions.create(**kwargs)
        except APIError as e:
            # 鉴权、模型名错、限流这类，body 里通常有可读的原因
            raise LlmError(
                f"LLM 接口报错（{type(e).__name__}）：{getattr(e, 'message', str(e))}"
            ) from e
        except OpenAIError as e:
            raise LlmError(f"调用 LLM 失败（{type(e).__name__}）：{e}") from e
        except Exception as e:
            raise LlmError(f"调用 LLM 时出现意外错误（{type(e).__name__}）：{e}") from e

        if not resp.choices:
            raise LlmError("LLM 返回里没有 choices，无法取得回复")

        msg = resp.choices[0].message
        raw_tool_calls: list[dict[str, Any]] = []
        for tc in msg.tool_calls or []:
            fn = getattr(tc, "function", None)
            if fn is None:
                # 非 function 类型的 tool_call（比如 custom），本框架用不到
                logger.warning("忽略非 function 类型的 tool_call：%s", tc)
                continue
            raw_tool_calls.append(
                {
                    "id": tc.id,
                    "function": {"name": fn.name, "arguments": fn.arguments},
                }
            )

        return normalize_response(msg.content, raw_tool_calls)

    async def aclose(self) -> None:
        await self._client.close()
