"""
配置页面的 HTTP 层

aiohttp（本来就是依赖）+ 一个静态单页，没有构建步骤 ——
同事拿到目录就能跑，不需要装 Node。

安全上的取舍：只监听 127.0.0.1，不做登录。这个页面能读写真实密钥、
能改磁盘上的文件，所以它被设计成"本机小工具"而不是"内部网站"。
不要改成 0.0.0.0。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Awaitable, Callable

from aiohttp import web

from botkit.config import get_message_lang, reset_message_lang, set_message_lang
from botkit.ui import service
from botkit.ui.service import UiError

logger = logging.getLogger("botkit.ui")


def _m(zh: str, en: str) -> str:
    """按当前语言选中/英消息（和 config/service 共用同一个 contextvar）。"""
    return en if get_message_lang() == "en" else zh


def _lang_from_header(accept_language: str) -> str:
    """从 Accept-Language 头粗略判断中/英。默认中文（和后端默认一致）。"""
    al = (accept_language or "").lower()
    # 浏览器切成英文时头形如 "en-US,en;q=0.9"；只要排在中文前面就按英文
    first = al.split(",")[0].strip()
    if first.startswith("en"):
        return "en"
    return "zh"

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8771

STATIC_DIR = Path(__file__).parent / "static"

Handler = Callable[[web.Request], Awaitable[web.StreamResponse]]

# 项目根目录（bots/ 的父目录）。用 AppKey 而不是裸字符串，aiohttp 推荐这么写
PROJECT_ROOT = web.AppKey("project_root", Path)


def _root(request: web.Request) -> Path:
    return request.app[PROJECT_ROOT]


def _fail(message: str, status: int = 400) -> web.Response:
    return web.json_response({"error": message}, status=status)


@web.middleware
async def lang_middleware(request: web.Request, handler: Handler) -> web.StreamResponse:
    """
    按请求的 Accept-Language 设置后端报错/摘要的语言，请求结束后恢复。

    放在最外层：这样 error_middleware 的兜底消息、以及各 handler 调到的
    service / config 层报错都能按浏览器选的语言输出。CLI / 运行时不经过这里，
    默认永远中文。
    """
    lang = _lang_from_header(request.headers.get("Accept-Language", ""))
    token = set_message_lang(lang)
    try:
        return await handler(request)
    finally:
        reset_message_lang(token)


@web.middleware
async def error_middleware(request: web.Request, handler: Handler) -> web.StreamResponse:
    """
    把异常统一转成 JSON。UiError 的消息是写给用户看的，直接透出；
    其他异常记完整堆栈，只给用户一句概要。
    """
    try:
        return await handler(request)
    except UiError as e:
        return _fail(str(e))
    except web.HTTPException:
        raise
    except Exception as e:
        logger.exception("处理 %s %s 时出错", request.method, request.path)
        return _fail(_m(
            f"服务端出错（{type(e).__name__}）：{e}",
            f"Server error ({type(e).__name__}): {e}",
        ), status=500)


@web.middleware
async def local_only_middleware(
    request: web.Request, handler: Handler
) -> web.StreamResponse:
    """
    只放行本机请求。即使有人把监听地址改成 0.0.0.0，也还有这一道。
    这个页面能改配置和读密钥，不该被别的机器访问。
    """
    peer = request.remote or ""
    if peer not in ("127.0.0.1", "::1", "localhost"):
        logger.warning("拒绝了来自 %s 的请求（配置页面只允许本机访问）", peer)
        return _fail(_m(
            "配置页面只允许本机访问",
            "The config page is accessible from the local machine only",
        ), status=403)
    return await handler(request)


# --------------------------------------------------------------------------
# 接口
# --------------------------------------------------------------------------


async def api_options(request: web.Request) -> web.Response:
    return web.json_response(service.form_options())


async def api_list_bots(request: web.Request) -> web.Response:
    return web.json_response({"bots": service.list_bots(_root(request))})


async def api_read_bot(request: web.Request) -> web.Response:
    name = request.match_info["name"]
    return web.json_response(service.read_bot(_root(request), name))


async def api_create_bot(request: web.Request) -> web.Response:
    payload = await _json_body(request)
    name = str(payload.get("name") or "").strip()
    result = service.create_new_bot(_root(request), name)
    return web.json_response(result, status=201)


async def api_save_bot(request: web.Request) -> web.Response:
    name = request.match_info["name"]
    payload = await _json_body(request)
    return web.json_response(service.save_bot(_root(request), name, payload))


async def api_delete_bot(request: web.Request) -> web.Response:
    name = request.match_info["name"]
    return web.json_response(service.delete_bot(_root(request), name))


async def api_export_bot(request: web.Request) -> web.Response:
    name = request.match_info["name"]
    return web.json_response(service.export_bot(_root(request), name))


async def api_import_bot(request: web.Request) -> web.Response:
    payload = await _json_body(request)
    result = service.import_bot(_root(request), payload)
    return web.json_response(result, status=201)


async def api_list_skills(request: web.Request) -> web.Response:
    return web.json_response({"skills": service.list_skills(_root(request))})


async def api_upload_skill(request: web.Request) -> web.Response:
    payload = await _json_body(request)
    result = service.upload_skill(
        _root(request),
        str(payload.get("name", "")),
        str(payload.get("content", "")),
    )
    return web.json_response(result, status=201)


async def api_delete_skill(request: web.Request) -> web.Response:
    name = request.match_info["name"]
    return web.json_response(service.delete_skill(_root(request), name))


async def api_check_prompt(request: web.Request) -> web.Response:
    payload = await _json_body(request)
    return web.json_response(service.check_prompt(str(payload.get("prompt", ""))))


async def api_usage(request: web.Request) -> web.Response:
    name = request.match_info["name"]
    month = request.query.get("month") or None
    return web.json_response(service.read_usage(_root(request), name, month))


async def api_validate_bot(request: web.Request) -> web.Response:
    name = request.match_info["name"]
    bot_dir = service.bot_dir_for(_root(request), name)
    return web.json_response(service.validate_bot_dir(bot_dir))


async def api_probe_mcp(request: web.Request) -> web.Response:
    name = request.match_info["name"]
    server = request.query.get("server") or None
    result = await service.probe_mcp(_root(request), name, server)
    return web.json_response(result)


async def api_probe_wecom(request: web.Request) -> web.Response:
    name = request.match_info["name"]
    wait = request.query.get("wait") == "1"
    result = await service.probe_wecom(
        _root(request), name, wait_for_message=wait
    )
    return web.json_response(result)


async def api_preview(request: web.Request) -> web.Response:
    name = request.match_info["name"]
    payload = await _json_body(request)
    history = payload.get("history")
    if history is not None and not isinstance(history, list):
        raise UiError("history 应该是消息列表")
    result = await service.preview_chat(
        _root(request),
        name,
        str(payload.get("message", "")),
        history=history,
        identity=(payload.get("identity") or None),
    )
    return web.json_response(result)


def _sse_event(data: Any, event: str | None = None) -> bytes:
    """拼一条 SSE 帧，data 用 JSON 编码（避免文本里的换行破坏协议）。"""
    import json

    prefix = f"event: {event}\n" if event else ""
    return (prefix + f"data: {json.dumps(data, ensure_ascii=False)}\n\n").encode("utf-8")


async def api_preview_stream(request: web.Request) -> web.StreamResponse:
    """
    预览对话的 SSE 流式版（打字机）。事件：
      data: {"delta": "..."}                     文本增量
      event: tool  / data: {tool,args,result,...} 一次工具调用
      event: done  / data: {identity_used,history} 收尾
      event: error / data: {message}              进 LLM 后的错误

    开流前的错误（配置没过校验、身份不符）仍以普通 JSON（4xx）返回，
    由 error_middleware 处理——所以这里先取生成器的第一个事件触发前置校验，
    拿到第一个事件后再 prepare 响应、开始写流。
    """
    name = request.match_info["name"]
    payload = await _json_body(request)
    history = payload.get("history")
    if history is not None and not isinstance(history, list):
        raise UiError(_m("history 应该是消息列表", "history should be a message list"))

    agen = service.preview_chat_stream(
        _root(request),
        name,
        str(payload.get("message", "")),
        history=history,
        identity=(payload.get("identity") or None),
    )

    # 先触发前置校验：开流前的 UiError 在这里抛出，交给 error_middleware 转 JSON，
    # 此时还没 prepare，可以正常返回 4xx。
    try:
        first = await agen.__anext__()
    except StopAsyncIteration:
        first = None

    resp = web.StreamResponse(
        status=200,
        headers={
            "Content-Type": "text/event-stream; charset=utf-8",
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
    await resp.prepare(request)

    async def write_event(item: tuple[str, Any]) -> None:
        kind, data = item
        if kind == "delta":
            await resp.write(_sse_event({"delta": data}))
        elif kind == "tool":
            await resp.write(_sse_event(data, event="tool"))
        elif kind == "error":
            await resp.write(_sse_event({"message": data}, event="error"))
        elif kind == "done":
            await resp.write(_sse_event(data, event="done"))

    try:
        if first is not None:
            await write_event(first)
        async for item in agen:
            await write_event(item)
    except Exception:
        logger.exception("预览 SSE 写流时出错 name=%s", name)
        await resp.write(_sse_event({"message": "服务端处理出错"}, event="error"))

    await resp.write_eof()
    return resp


async def _json_body(request: web.Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except Exception as e:
        raise UiError(_m(
            f"请求体不是合法 JSON：{e}",
            f"The request body is not valid JSON: {e}",
        )) from e
    if not isinstance(payload, dict):
        raise UiError(_m(
            "请求体应该是一个 JSON 对象",
            "The request body should be a JSON object",
        ))
    return payload


# --------------------------------------------------------------------------
# 静态页面
# --------------------------------------------------------------------------


async def index(request: web.Request) -> web.StreamResponse:
    target = STATIC_DIR / "index.html"
    if not target.is_file():
        return web.Response(text="缺少 index.html，安装可能不完整", status=500)
    return web.FileResponse(
        target, headers={"Cache-Control": "no-store"}
    )


async def static_file(request: web.Request) -> web.StreamResponse:
    """
    服务 static/ 下的资源，禁用缓存。

    配置页面是本机小工具、会频繁迭代，浏览器缓存住旧的 app.js / i18n.js
    会让"改了不生效"（典型表现：新按钮点了没反应）。所以这里统一 no-store，
    不走 add_static 的默认缓存策略。
    """
    name = request.match_info["name"]
    target = (STATIC_DIR / name).resolve()
    # 防目录穿越：解析后必须仍在 STATIC_DIR 内
    base = STATIC_DIR.resolve()
    if not target.is_relative_to(base) or not target.is_file():
        return web.Response(text="not found", status=404)
    return web.FileResponse(target, headers={"Cache-Control": "no-store"})


# --------------------------------------------------------------------------
# 组装
# --------------------------------------------------------------------------


def create_app(project_root: Path | str = ".") -> web.Application:
    app = web.Application(
        middlewares=[lang_middleware, local_only_middleware, error_middleware]
    )
    app[PROJECT_ROOT] = Path(project_root).resolve()

    app.router.add_get("/", index)
    app.router.add_get("/api/options", api_options)
    app.router.add_get("/api/bots", api_list_bots)
    app.router.add_post("/api/bots", api_create_bot)
    # import 要在 {name} 通配之前注册，否则 /api/bots/import 会被当成 name=import
    app.router.add_post("/api/bots/import", api_import_bot)
    app.router.add_get("/api/bots/{name}", api_read_bot)
    app.router.add_get("/api/bots/{name}/export", api_export_bot)
    app.router.add_put("/api/bots/{name}", api_save_bot)
    app.router.add_delete("/api/bots/{name}", api_delete_bot)
    app.router.add_post("/api/bots/{name}/validate", api_validate_bot)
    app.router.add_post("/api/bots/{name}/probe/mcp", api_probe_mcp)
    app.router.add_post("/api/bots/{name}/probe/wecom", api_probe_wecom)
    app.router.add_post("/api/bots/{name}/preview", api_preview)
    app.router.add_post("/api/bots/{name}/preview/stream", api_preview_stream)
    app.router.add_get("/api/bots/{name}/usage", api_usage)
    app.router.add_post("/api/prompt/check", api_check_prompt)
    app.router.add_get("/api/skills", api_list_skills)
    app.router.add_post("/api/skills", api_upload_skill)
    app.router.add_delete("/api/skills/{name}", api_delete_skill)

    if STATIC_DIR.is_dir():
        app.router.add_static("/static/", STATIC_DIR, name="static")

    return app


def serve(
    project_root: Path | str = ".",
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    open_browser: bool = True,
) -> None:
    """启动配置页面，阻塞直到 Ctrl+C"""
    root = Path(project_root).resolve()
    url = f"http://{host}:{port}/"

    print()
    print("=" * 62)
    print("  botkit 配置页面已启动")
    print()
    print(f"  在浏览器里打开： {url}")
    print(f"  Agent 目录：     {root / 'bots'}")
    print()
    print("  只有这台机器能访问。按 Ctrl+C 停止。")
    print("=" * 62)
    print()

    if open_browser:
        _open_browser_later(url)

    app = create_app(root)
    try:
        web.run_app(app, host=host, port=port, print=None)
    except OSError as e:
        # 端口被占用是最常见的启动失败原因
        print(f"\n启动失败：{e}")
        print(f"端口 {port} 可能被占用。换一个：python -m botkit ui --port {port + 1}")
        raise SystemExit(1) from e
    except KeyboardInterrupt:
        pass
    finally:
        print("\n配置页面已停止。")


def _open_browser_later(url: str, delay: float = 0.8) -> None:
    """等服务起来再开浏览器，否则会打开一个连不上的页面"""
    import threading
    import webbrowser

    def opener() -> None:
        try:
            webbrowser.open(url)
        except Exception:
            logger.debug("自动打开浏览器失败（忽略）", exc_info=True)

    threading.Timer(delay, opener).start()
