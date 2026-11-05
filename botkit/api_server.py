"""
对外 chat HTTP 接口（/chat）

把Agent包成一个 HTTP 端点，供 Dify、自研门户或任何第三方调用。
和企微长连接共用同一套对话逻辑（build_pipeline），只是入口从企微消息帧
换成了 HTTP 请求。

契约：
    POST /chat
      Header: Authorization: Bearer <token>   （API 启用时必填）
      Body:   {"message": "...", "user": "L220104", "session_id": "可选"}
      Resp:   {"reply": "...", "kind": "llm", "session_id": "..."}

    GET /health -> {"ok": true, "bot": "<name>"}   （探活，免鉴权）

自定义端点：bot 可以在自己的 hooks.py 里实现 register_routes(app, config,
pipeline)，往这个 app 上挂它特有的路由（比如某个 bot 的结构化分析端点）。
框架在应用级中间件统一执行 Bearer 鉴权和请求体大小限制；业务 handler
仍负责校验自己的字段和权限。

设计要点：
- user / session_id 由调用方（门户/Dify）提供，本服务不发号、不做账号体系。
- 同一 (user, session_id) 的连续请求 = 连续多轮对话，上下文由 SessionStore 维持。
- 不传 session_id 时退化成「按 user 隔离」（每个用户一条上下文）。
- 绑 0.0.0.0：必须经受控网络或 TLS 反向代理暴露；框架负责 Bearer 鉴权，
  MCP 服务端仍需做最终身份授权。配置页面继续只绑本机。
"""

from __future__ import annotations

import hmac
import json
import logging
from dataclasses import dataclass
from typing import Any

from aiohttp import web

from botkit.config import (
    BotConfig,
    get_message_lang,
    reset_message_lang,
    set_message_lang,
)
from botkit.hooks import Hooks
from botkit.runtime import MessagePipeline

logger = logging.getLogger("botkit.api")

# 明确限制所有 Agent API（包括 register_routes 自定义端点）的请求体。
# 字段上限还会在 /chat 解析后单独检查，避免超长身份、会话 key 和 prompt。
MAX_REQUEST_BYTES = 1024 * 1024
MAX_MESSAGE_CHARS = 100_000
MAX_USER_CHARS = 256
MAX_SESSION_ID_CHARS = 256
HEALTH_ROUTE_NAME = "botkit-health"


def _m(zh: str, en: str) -> str:
    """按当前语言选中/英消息。默认中文。"""
    return en if get_message_lang() == "en" else zh


def _lang_from_header(accept_language: str) -> str:
    """从 Accept-Language 头粗略判断中/英。默认中文。"""
    first = (accept_language or "").lower().split(",")[0].strip()
    return "en" if first.startswith("en") else "zh"

PIPELINE = web.AppKey("pipeline", MessagePipeline)
CONFIG = web.AppKey("config", BotConfig)


def check_auth(request: web.Request, cfg: BotConfig) -> web.Response | None:
    """
    校验 Authorization: Bearer <token>。

    正常配置加载会保证 API 启用时 token 非空；空 token 分支仅保留给直接
    构造配置的测试和向后兼容调用。所有 Agent 子应用路由由安全中间件统一调用。
    """
    token = cfg.api.token
    if not token:
        return None
    auth = request.headers.get("Authorization", "")
    expected = f"Bearer {token}"
    if not hmac.compare_digest(auth.encode("utf-8"), expected.encode("utf-8")):
        return web.json_response(
            {"error": _m(
                "未授权：请在请求头带 Authorization: Bearer <token>",
                "Unauthorized: send Authorization: Bearer <token> in the header",
            )},
            status=401,
        )
    return None


@web.middleware
async def api_security_middleware(
    request: web.Request, handler: Any
) -> web.StreamResponse:
    """
    对内置和 register_routes 自定义端点统一执行鉴权。

    只有框架命名的 liveness 路由免鉴权。语言上下文也在这里设置，确保在
    handler 之前返回的 401 能遵循 Accept-Language。
    """
    language_token = set_message_lang(
        _lang_from_header(request.headers.get("Accept-Language", ""))
    )
    try:
        route_name = getattr(request.match_info.route, "name", None)
        if route_name != HEALTH_ROUTE_NAME:
            cfg: BotConfig = request.app[CONFIG]
            unauthorized = check_auth(request, cfg)
            if unauthorized is not None:
                return unauthorized
        return await handler(request)
    finally:
        reset_message_lang(language_token)


def _text_field(
    payload: dict[str, Any], name: str, max_chars: int
) -> tuple[str, web.Response | None]:
    """读取并限制 /chat 字符串字段；不把对象或数字静默转成字符串。"""
    raw = payload.get(name)
    if raw is None:
        return "", None
    if not isinstance(raw, str):
        return "", web.json_response(
            {"error": _m(
                f"{name} 应该是字符串",
                f"{name} must be a string",
            )},
            status=400,
        )
    value = raw.strip()
    if len(value) > max_chars:
        return "", web.json_response(
            {"error": _m(
                f"{name} 过长，最多允许 {max_chars} 个字符",
                f"{name} is too long; the limit is {max_chars} characters",
            )},
            status=413,
        )
    return value, None


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
    # 对外接口按调用方的 Accept-Language 返回中/英错误；默认中文。
    # 用 try/finally 恢复，避免污染后续请求。
    token = set_message_lang(_lang_from_header(request.headers.get("Accept-Language", "")))
    try:
        try:
            payload = await request.json()
        except web.HTTPRequestEntityTooLarge:
            raise
        except Exception:
            return web.json_response(
                {"error": _m("请求体不是合法 JSON", "The request body is not valid JSON")},
                status=400,
            )
        if not isinstance(payload, dict):
            return web.json_response(
                {"error": _m("请求体应该是一个 JSON 对象", "The request body should be a JSON object")},
                status=400,
            )

        message, error = _text_field(payload, "message", MAX_MESSAGE_CHARS)
        if error is not None:
            return error
        user, error = _text_field(payload, "user", MAX_USER_CHARS)
        if error is not None:
            return error
        session_id, error = _text_field(
            payload, "session_id", MAX_SESSION_ID_CHARS
        )
        if error is not None:
            return error

        if not message:
            return web.json_response(
                {"error": _m("缺少 message", "Missing message")}, status=400
            )
        if not user:
            return web.json_response(
                {"error": _m(
                    "缺少 user（调用方要提供用户标识）",
                    "Missing user (the caller must provide a user identifier)",
                )},
                status=400,
            )

        pipeline: MessagePipeline = request.app[PIPELINE]
        frame = _frame_from_request(message, user, session_id)

        # 流式：调用方带 Accept: text/event-stream 时走 SSE 逐段推送
        if _wants_sse(request):
            return await _stream_chat(request, pipeline, frame, session_id)

        try:
            reply = await pipeline.handle(frame, source="api")
        except Exception:
            logger.exception("处理 /chat 请求时出错 user=%s", user)
            return web.json_response(
                {"error": _m("服务端处理出错，请查看日志", "Server error while processing, check the logs")},
                status=500,
            )

        return web.json_response(
            {"reply": reply.text, "kind": reply.kind, "session_id": session_id}
        )
    finally:
        reset_message_lang(token)


def _wants_sse(request: web.Request) -> bool:
    """调用方是否要 SSE 流式（Accept 里带 text/event-stream）"""
    return "text/event-stream" in request.headers.get("Accept", "")


def _sse_event(data: dict[str, Any], event: str | None = None) -> bytes:
    """拼一条 SSE 帧。data 用 JSON 编码，避免 token 里的换行破坏协议。"""
    prefix = f"event: {event}\n" if event else ""
    return (prefix + f"data: {json.dumps(data, ensure_ascii=False)}\n\n").encode("utf-8")


async def _stream_chat(
    request: web.Request,
    pipeline: MessagePipeline,
    frame: dict[str, Any],
    session_id: str,
) -> web.StreamResponse:
    """
    用 SSE 把回复逐段推给调用方。事件约定：
    - 文本增量：`data: {"delta": "..."}`
    - 进度提示：`event: progress` + `data: {"text": "..."}`
    - 结束：    `event: done` + `data: {"kind": "...", "session_id": "..."}`

    一旦开始 streaming，HTTP 状态码已是 200，中途出错只能用 error 事件告知，
    没法再改状态码——这是 SSE 的固有特性。
    """
    resp = web.StreamResponse(
        status=200,
        headers={
            "Content-Type": "text/event-stream; charset=utf-8",
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # 关掉 Nginx 缓冲，token 才能实时到
        },
    )
    await resp.prepare(request)

    async def on_progress(label: str) -> None:
        try:
            await resp.write(_sse_event({"text": label}, event="progress"))
        except Exception:
            logger.debug("推送 SSE 进度失败（忽略）", exc_info=True)

    last_kind = "llm"
    try:
        async for kind, piece in pipeline.handle_stream(
            frame, on_progress=on_progress, source="api"
        ):
            last_kind = kind
            await resp.write(_sse_event({"delta": piece}))
    except Exception:
        logger.exception("SSE 流式处理出错")
        await resp.write(_sse_event({"message": "服务端处理出错"}, event="error"))
        await resp.write_eof()
        return resp

    await resp.write(
        _sse_event({"kind": last_kind, "session_id": session_id}, event="done")
    )
    await resp.write_eof()
    return resp


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
    if not config.api.token:
        raise ValueError("创建 Agent API 时必须配置非空的 api.token")

    app = web.Application(
        middlewares=[api_security_middleware],
        client_max_size=MAX_REQUEST_BYTES,
    )
    app[CONFIG] = config
    app[PIPELINE] = pipeline
    app.router.add_post("/chat", _handle_chat)
    app.router.add_get("/health", _handle_health, name=HEALTH_ROUTE_NAME)

    if hooks is not None and hooks.has_routes:
        logger.info("加载 bot 自定义路由（register_routes），前缀=%r", base_path)
        hooks.register_routes(app, config, pipeline, base_path=base_path)

    return app


# --------------------------------------------------------------------------
# 多 Agent 网关：一个进程、一个端口挂多个 Agent
#
# 命名空间 /{agent}/...：每个 Agent 用 create_api_app 建一个子 app（裸路径
# /chat、/health、以及它自己 register_routes 挂的端点），再用 aiohttp 的
# add_subapp 统一加 /{agent} 前缀。对外表现为 /{agent}/chat 等。
#
# 关键：子 app 用 base_path=""（裸路径），前缀完全交给 add_subapp，避免和
# base_path 机制叠加导致前缀重复。每个子 app 自带独立的 CONFIG/PIPELINE，
# _handle_chat 取到的就是对应 Agent 的 config/pipeline —— per-Agent 的
# pipeline/engine/SessionStore 隔离与按各自 api.token 鉴权，天然成立。
# --------------------------------------------------------------------------


@dataclass
class GatewayAgent:
    """
    网关里的一个 Agent 条目。

    pipeline 非 None 表示可挂载；为 None（通常连带 error）表示配置加载失败，
    不挂它的路由，但仍在 GET /agents 里标为不可用。
    """

    name: str
    config: BotConfig | None = None
    pipeline: MessagePipeline | None = None
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.pipeline is not None and self.config is not None


AGENTS = web.AppKey("agents", list)


async def _handle_agents(request: web.Request) -> web.Response:
    """
    GET /agents：列出网关里所有 Agent 及在线状态（名字、是否可用、错误原因）。

    免鉴权——这是给门户列卡片用的发现接口，不涉及对话内容或密钥。
    """
    agents: list[GatewayAgent] = request.app[AGENTS]
    return web.json_response(
        {
            "agents": [
                {
                    "name": a.name,
                    "ok": a.ok,
                    "path": f"/{a.name}/chat",
                    "error": a.error,
                }
                for a in agents
            ]
        }
    )


async def _handle_gateway_health(request: web.Request) -> web.Response:
    """网关整体探活，免鉴权。available 是能对话的 Agent 数。"""
    agents: list[GatewayAgent] = request.app[AGENTS]
    return web.json_response(
        {
            "ok": True,
            "agents": len(agents),
            "available": sum(1 for a in agents if a.ok),
        }
    )


def build_gateway_app(agents: list[GatewayAgent]) -> web.Application:
    """
    组装多 Agent 网关的父 app（纯函数，不起端口，方便用 TestClient 测）。

    - 每个可用 Agent（pipeline 非 None）用 create_api_app 建子 app（base_path=""，
      裸路径），add_subapp(f"/{name}") 挂到 /{name} 命名空间下；
    - 不可用的 Agent 不挂路由，只在 GET /agents 里报告；
    - 父 app 提供 GET /agents 和 GET /health（都免鉴权）。
    """
    app = web.Application()
    app[AGENTS] = agents
    app.router.add_get("/agents", _handle_agents)
    app.router.add_get("/health", _handle_gateway_health)

    for a in agents:
        if not a.ok:
            logger.warning("Agent %s 不可用，不挂载：%s", a.name, a.error)
            continue
        assert a.config is not None and a.pipeline is not None  # ok 已保证
        hooks = getattr(a.pipeline, "hooks", None)
        sub = create_api_app(a.config, a.pipeline, hooks=hooks, base_path="")
        app.add_subapp(f"/{a.name}", sub)
        logger.info("网关已挂载 Agent：/%s/chat", a.name)

    return app
