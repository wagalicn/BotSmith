"""
对外 chat HTTP 接口（/chat）

把Agent包成一个 HTTP 端点，供 Dify、自研门户或任何第三方调用。
和企微长连接共用同一套对话逻辑（build_pipeline），只是入口从企微消息帧
换成了 HTTP 请求。

契约（无状态透传）：
    POST /chat
      Header: Authorization: Bearer <token>   （配了 token 才校验）
      Body:   {"message": "...", "user": "L220104", "session_id": "可选"}
      Resp:   {"reply": "...", "kind": "llm", "session_id": "..."}

    GET /health -> {"ok": true, "bot": "<name>"}   （探活，免鉴权）

自定义端点：bot 可以在自己的 hooks.py 里实现 register_routes(app, config,
pipeline)，往这个 app 上挂它特有的路由（比如某个 bot 的结构化分析端点）。
框架只负责在组装完 /chat、/health 后调用它，具体路由是 bot 的业务，不进
骨架。鉴权可复用 _check_auth。

设计要点：
- user / session_id 由调用方（门户/Dify）提供，本服务不发号、不做账号体系。
- 同一 (user, session_id) 的连续请求 = 连续多轮对话，上下文由 SessionStore 维持。
- 不传 session_id 时退化成「按 user 隔离」（每个用户一条上下文）。
- 绑 0.0.0.0：这个服务只对话、不读写密钥或磁盘配置，暴露到内网是安全的
  （和「配置页面」只绑本机不同）。
"""

from __future__ import annotations

import logging
from typing import Any

from aiohttp import web

from botkit.config import BotConfig
from botkit.hooks import Hooks
from botkit.runtime import MessagePipeline

logger = logging.getLogger("botkit.api")

PIPELINE = web.AppKey("pipeline", MessagePipeline)
CONFIG = web.AppKey("config", BotConfig)


def check_auth(request: web.Request, cfg: BotConfig) -> web.Response | None:
    """
    校验 Authorization: Bearer <token>。

    配了 token 才校验；通过（或没配 token）返回 None，失败返回 401 响应。
    /chat 用它做鉴权，bot 通过 register_routes 挂的自定义路由也可以复用。
    """
    token = cfg.api.token
    if not token:
        return None
    auth = request.headers.get("Authorization", "")
    if auth != f"Bearer {token}":
        return web.json_response(
            {"error": "未授权：请在请求头带 Authorization: Bearer <token>"},
            status=401,
        )
    return None


def _frame_from_request(message: str, user: str, session_id: str) -> dict[str, Any]:
    """
    把 HTTP 请求拼成一个企微消息帧的形状，好复用 pipeline.handle。

    session_id 放进 chatid + chattype=group，让 SessionStore 按它隔离上下文：
    同一 (user, session_id) 续接，换 session_id 换会话。
    不传 session_id 就当单聊，按 user 隔离。
    """
    body: dict[str, Any] = {
        "from": {"userid": user},
        "text": {"content": message},
    }
    if session_id:
        body["chattype"] = "group"
        body["chatid"] = f"api:{session_id}"
    else:
        body["chattype"] = "single"
    return {"body": body}


async def _handle_chat(request: web.Request) -> web.Response:
    cfg: BotConfig = request.app[CONFIG]

    # 鉴权：配了 token 才校验 Authorization: Bearer <token>
    unauthorized = check_auth(request, cfg)
    if unauthorized is not None:
        return unauthorized

    try:
        payload = await request.json()
    except Exception:
        return web.json_response({"error": "请求体不是合法 JSON"}, status=400)
    if not isinstance(payload, dict):
        return web.json_response({"error": "请求体应该是一个 JSON 对象"}, status=400)

    message = str(payload.get("message") or "").strip()
    user = str(payload.get("user") or "").strip()
    session_id = str(payload.get("session_id") or "").strip()

    if not message:
        return web.json_response({"error": "缺少 message"}, status=400)
    if not user:
        return web.json_response({"error": "缺少 user（调用方要提供用户标识）"}, status=400)

    pipeline: MessagePipeline = request.app[PIPELINE]
    frame = _frame_from_request(message, user, session_id)

    try:
        reply = await pipeline.handle(frame)
    except Exception:
        logger.exception("处理 /chat 请求时出错 user=%s", user)
        return web.json_response(
            {"error": "服务端处理出错，请查看日志"}, status=500
        )

    return web.json_response(
        {"reply": reply.text, "kind": reply.kind, "session_id": session_id}
    )


async def _handle_health(request: web.Request) -> web.Response:
    cfg: BotConfig = request.app[CONFIG]
    return web.json_response({"ok": True, "bot": cfg.name})


def create_api_app(
    config: BotConfig,
    pipeline: MessagePipeline,
    hooks: Hooks | None = None,
    base_path: str = "",
) -> web.Application:
    """
    组装 chat API 的 aiohttp app。pipeline 由调用方建好传入（和企微复用同一个）。

    传入 hooks 且 bot 实现了 register_routes 时，会让 bot 往这个 app 上挂它
    自己的自定义路由（骨架不感知具体是什么端点）。

    base_path 是自定义路由的前缀（默认 ""）：单 Agent 模式传空（裸路径，
    现状不变）；将来多 Agent 网关模式传 "/{agent}"，透传给 bot 的
    register_routes。注意 /chat、/health 是框架内置端点，仍挂在裸路径上；
    base_path 只影响 bot 通过 register_routes 挂的自定义路由。
    """
    app = web.Application()
    app[CONFIG] = config
    app[PIPELINE] = pipeline
    app.router.add_post("/chat", _handle_chat)
    app.router.add_get("/health", _handle_health)

    if hooks is not None and hooks.has_routes:
        logger.info("加载 bot 自定义路由（register_routes），前缀=%r", base_path)
        hooks.register_routes(app, config, pipeline, base_path=base_path)

    return app
