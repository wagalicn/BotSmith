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

from botkit.ui import service
from botkit.ui.service import UiError

logger = logging.getLogger("botkit.ui")

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
        return _fail(f"服务端出错（{type(e).__name__}）：{e}", status=500)


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
        return _fail("配置页面只允许本机访问", status=403)
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


async def _json_body(request: web.Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except Exception as e:
        raise UiError(f"请求体不是合法 JSON：{e}") from e
    if not isinstance(payload, dict):
        raise UiError("请求体应该是一个 JSON 对象")
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


# --------------------------------------------------------------------------
# 组装
# --------------------------------------------------------------------------


def create_app(project_root: Path | str = ".") -> web.Application:
    app = web.Application(middlewares=[local_only_middleware, error_middleware])
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
