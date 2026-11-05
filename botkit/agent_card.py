"""
Agent 名片（Agent Card）

Agent 的自我介绍：叫什么、做什么、擅长什么、怎么调用。门户做 Agent 目录、
别的 Agent 想找人帮忙，都先看这张名片。

字段和命名（驼峰）参考 A2A 协议 1.0 的 AgentCard：
https://a2a-protocol.org/latest/specification/
先把「名片」这一块对齐，将来要和其他系统的 Agent 互联时，名片可以直接沿用。

目前**不兼容** A2A：框架还没有实现 A2A 的消息接口，所以 supportedInterfaces
里只有框架自己的 HTTP 对话接口（POST /chat），协议绑定名是自定义的
botkit-http（A2A 规定 protocolBinding 是开放字符串，允许扩展）。A2A 客户端遇到
不认识的绑定会跳过它，不会误用。

访问方式：GET /.well-known/agent-card.json，和 Agent 的其他路由一样要 Bearer
鉴权（只有 /health 免鉴权）。网关里是 /{agent}/.well-known/agent-card.json。
"""

from __future__ import annotations

from typing import Any

from botkit.config import BotConfig, CardSkill

# 名片的路径，用 A2A 约定的 well-known 位置（相对 Agent 自己的根路径）。
CARD_PATH = "/.well-known/agent-card.json"

# 框架自己的 HTTP 对话接口（POST /chat，支持整段 JSON 和 SSE 流式）在名片里的
# 协议绑定名和版本。
CHAT_BINDING = "botkit-http"
CHAT_BINDING_VERSION = "1"


def _skill_to_dict(skill: CardSkill) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": skill.id,
        "name": skill.name,
        "description": skill.description,
        # A2A 要求 tags 必须有，没有标签就是空列表
        "tags": list(skill.tags),
    }
    if skill.examples:
        out["examples"] = list(skill.examples)
    return out


def build_agent_card(config: BotConfig, base_url: str) -> dict[str, Any]:
    """
    按配置生成名片。

    base_url 是这个 Agent 对外的根地址：单 Agent 时形如 http://host:9000，
    网关里形如 http://host:9100/IThelp-bot。结尾的 / 会被去掉。
    """
    card = config.card
    return {
        "name": config.name,
        "description": card.description,
        "version": card.version,
        "supportedInterfaces": [
            {
                "url": f"{base_url.rstrip('/')}/chat",
                "protocolBinding": CHAT_BINDING,
                "protocolVersion": CHAT_BINDING_VERSION,
            }
        ],
        # 这几个开关现在描述的是 botkit-http 接口：/chat 的请求头带
        # Accept: text/event-stream 就走 SSE 流式，没有推送和扩展名片。
        "capabilities": {
            "streaming": True,
            "pushNotifications": False,
            "extendedAgentCard": False,
        },
        "securitySchemes": {
            "bearer": {"httpAuthSecurityScheme": {"scheme": "Bearer"}},
        },
        "securityRequirements": [{"schemes": {"bearer": {"list": []}}}],
        "defaultInputModes": ["text/plain"],
        "defaultOutputModes": ["text/plain"],
        "skills": [_skill_to_dict(s) for s in card.skills],
    }
