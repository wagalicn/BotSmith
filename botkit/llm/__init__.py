"""
LLM 后端

按 llm.client 配置挑一个后端。所有后端产出完全一致的 LlmResponse / StreamChunk，
engine.py 不感知底下用的是哪个、也不感知协议差异。

说 OpenAI Chat Completions 协议的：

    openai  （默认）官方 SDK。自带重试、超时、类型化 tool_calls。
            api_url 填到 /v1 为止。
    aiohttp 裸 HTTP。用于不完全遵守 OpenAI 规范的自建端点或网关，
            能拿到原始 JSON 自己兜。api_url 填完整端点。

说 Anthropic Messages 协议的：

    anthropic  裸 HTTP。给「只提供 Messages API」的端点用，典型是
               Amazon Bedrock 上的 Claude（它不支持 /chat/completions），
               也可直连 Anthropic 官方。api_url 填到 /v1 为止。
"""

from __future__ import annotations

from botkit.config import LlmConfig
from botkit.llm.base import (
    LlmBackend,
    LlmError,
    LlmResponse,
    TokenUsage,
    ToolCall,
    build_assistant_message,
    normalize_response,
    parse_tool_arguments,
    parse_usage,
)

__all__ = [
    "LlmBackend",
    "LlmError",
    "LlmResponse",
    "TokenUsage",
    "ToolCall",
    "build_assistant_message",
    "create_backend",
    "normalize_response",
    "parse_tool_arguments",
    "parse_usage",
]


def create_backend(cfg: LlmConfig) -> LlmBackend:
    """按配置创建后端。配置层已校验过 client 取值，这里不会走到 else"""
    if cfg.client == "openai":
        from botkit.llm.openai_backend import OpenAIBackend

        return OpenAIBackend(cfg)

    if cfg.client == "aiohttp":
        from botkit.llm.aiohttp_backend import AiohttpBackend

        return AiohttpBackend(cfg)

    if cfg.client == "anthropic":
        from botkit.llm.anthropic_backend import AnthropicBackend

        return AnthropicBackend(cfg)

    raise LlmError(f"不认识的 LLM 后端：{cfg.client}")
