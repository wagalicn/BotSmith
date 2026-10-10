"""
配置页面 HTTP 层测试

用 aiohttp 自带的测试客户端起真服务，走真实路由和中间件。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

from botkit.ui import service
from botkit.ui.server import STATIC_DIR, create_app


@pytest.fixture
def project(tmp_path: Path):
    (tmp_path / "bots").mkdir()
    return tmp_path


@pytest.fixture
def ready_bot(project: Path):
    service.create_new_bot(project, "hr-bot")
    (project / "bots" / "hr-bot" / ".env").write_text(
        "WECHAT_BOT_ID=id-1\n"
        "WECHAT_BOT_SECRET=secret-1\n"
        "LLM_API_URL=https://x.invalid/v1\n"
        "LLM_API_KEY=sk-1\n"
        "LLM_MODEL=qwen\n"
        "MCP_URL=https://x.invalid/mcp\n",
        encoding="utf-8",
    )
    return project / "bots" / "hr-bot"


@pytest.fixture
async def client(project: Path):
    async with TestClient(TestServer(create_app(project))) as test_client:
        yield test_client


# --------------------------------------------------------------------------
# 静态资源
# --------------------------------------------------------------------------


async def test_index_served(client):
    resp = await client.get("/")
    assert resp.status == 200
    text = await resp.text()
    assert "botkit" in text
    assert "app.js" in text


async def test_static_files_exist():
    """页面靠这三个文件，缺一个就白屏"""
    for name in ("index.html", "app.js", "style.css"):
        assert (STATIC_DIR / name).is_file(), name


async def test_static_js_served(client):
    resp = await client.get("/static/app.js")
    assert resp.status == 200
    assert "botkit" in await resp.text()


# --------------------------------------------------------------------------
# 前端与 HTML 的一致性
#
# JS 里写错一个 id，页面会静默少一块功能或者直接报错，浏览器外看不出来。
# 这两个检查把这类问题挡在提交前。
# --------------------------------------------------------------------------


def _read_static(name: str) -> str:
    return (STATIC_DIR / name).read_text(encoding="utf-8")


def test_js_selectors_exist_in_html():
    import re

    html = _read_static("index.html")
    js = _read_static("app.js")

    ids_in_html = set(re.findall(r'\bid="([^"]+)"', html))
    # app.js 里用 $("#xxx") 或 getElementById("xxx") 取的元素
    used = set(re.findall(r'\$\("#([A-Za-z0-9_-]+)"\)', js))
    used |= set(re.findall(r'getElementById\("([A-Za-z0-9_-]+)"\)', js))

    # 动态创建的元素不在 HTML 里，排除掉
    dynamic = {"validation-banner"}
    missing = sorted(used - ids_in_html - dynamic)
    assert not missing, f"app.js 引用了 HTML 里不存在的 id：{missing}"


def test_js_tabs_match_html_panels():
    import re

    html = _read_static("index.html")
    js = _read_static("app.js")

    panels = set(re.findall(r'data-panel="([^"]+)"', html))
    # TABS 数组里的 id
    tabs_block = js[js.index("const TABS"): js.index("// -------", js.index("const TABS"))]
    tabs = set(re.findall(r'id:\s*"([A-Za-z0-9_-]+)"', tabs_block))

    assert tabs == panels, f"分区对不上：JS 有 {sorted(tabs)}，HTML 有 {sorted(panels)}"


def test_html_inputs_have_labels():
    """
    每个 input/select/textarea 要么有 id（JS 会配 label），要么带 aria-label。
    静态写在 HTML 里的控件在这里检查。
    """
    import re

    html = _read_static("index.html")
    controls = re.findall(r"<(input|select|textarea)\b([^>]*)>", html)
    for tag, attrs in controls:
        has_id = 'id="' in attrs
        has_aria = "aria-label" in attrs
        assert has_id or has_aria, f"<{tag}{attrs}> 既没有 id 也没有 aria-label"


# --------------------------------------------------------------------------
# 选项
# --------------------------------------------------------------------------


async def test_options_endpoint(client):
    resp = await client.get("/api/options")
    assert resp.status == 200
    data = await resp.json()
    assert data["llm_clients"]
    assert data["session_scopes"]
    assert data["message_fields"]


# --------------------------------------------------------------------------
# 列表 / 读 / 存
# --------------------------------------------------------------------------


async def test_list_bots_empty(client):
    resp = await client.get("/api/bots")
    assert resp.status == 200
    assert (await resp.json())["bots"] == []


async def test_list_bots(client, ready_bot):
    data = await (await client.get("/api/bots")).json()
    assert [b["name"] for b in data["bots"]] == ["hr-bot"]
    assert data["bots"][0]["ok"] is True


async def test_read_bot(client, ready_bot):
    resp = await client.get("/api/bots/hr-bot")
    assert resp.status == 200
    data = await resp.json()
    assert data["name"] == "hr-bot"
    assert data["values"]["name"] == "hr-bot"
    assert data["validation"]["ok"]


async def test_read_missing_bot_returns_400(client):
    resp = await client.get("/api/bots/nope")
    assert resp.status == 400
    assert "找不到" in (await resp.json())["error"]


async def test_read_bot_path_traversal_blocked(client):
    """URL 里塞 .. 不能读到 bots/ 外面的东西"""
    for name in ("..", "%2e%2e", "..%2f..", "a%2f..%2f.."):
        resp = await client.get(f"/api/bots/{name}")
        assert resp.status in (400, 404), name
        if resp.status == 400:
            assert "error" in await resp.json()


async def test_create_bot(client):
    resp = await client.post("/api/bots", json={"name": "brand-new"})
    assert resp.status == 201
    assert (await resp.json())["name"] == "brand-new"

    listed = await (await client.get("/api/bots")).json()
    assert [b["name"] for b in listed["bots"]] == ["brand-new"]


async def test_create_bot_invalid_name(client):
    resp = await client.post("/api/bots", json={"name": "1bad name"})
    assert resp.status == 400
    assert "不合法" in (await resp.json())["error"]


async def test_create_bot_duplicate(client, ready_bot):
    resp = await client.post("/api/bots", json={"name": "hr-bot"})
    assert resp.status == 400


async def test_save_bot(client, ready_bot):
    data = await (await client.get("/api/bots/hr-bot")).json()
    values = data["values"]
    values["session"]["max_turns"] = 3

    resp = await client.put(
        "/api/bots/hr-bot",
        json={"values": values, "prompt": "# 角色\n新提示词", "env": {"LLM_MODEL": "m2"}},
    )
    assert resp.status == 200
    assert (await resp.json())["validation"]["ok"]

    again = await (await client.get("/api/bots/hr-bot")).json()
    assert again["values"]["session"]["max_turns"] == 3
    assert again["prompt"].startswith("# 角色")


async def test_save_bot_invalid_enum(client, ready_bot):
    resp = await client.put(
        "/api/bots/hr-bot", json={"values": {"llm": {"client": "curl"}}}
    )
    assert resp.status == 400
    assert "llm.client" in (await resp.json())["error"]


async def test_save_bot_bad_json(client, ready_bot):
    resp = await client.put(
        "/api/bots/hr-bot",
        data="不是 json",
        headers={"Content-Type": "application/json"},
    )
    assert resp.status == 400
    assert "JSON" in (await resp.json())["error"]


async def test_save_bot_non_object_body(client, ready_bot):
    resp = await client.put("/api/bots/hr-bot", json=["列表不行"])
    assert resp.status == 400


async def test_delete_bot(client, ready_bot):
    resp = await client.delete("/api/bots/hr-bot")
    assert resp.status == 200
    assert (await resp.json())["name"] == "hr-bot"

    listed = await (await client.get("/api/bots")).json()
    assert listed["bots"] == []


async def test_delete_missing_bot(client):
    resp = await client.delete("/api/bots/nope")
    assert resp.status == 400
    assert "找不到" in (await resp.json())["error"]


async def test_delete_path_traversal_blocked(client, ready_bot):
    for name in ("..", "%2e%2e"):
        resp = await client.delete(f"/api/bots/{name}")
        assert resp.status in (400, 404), name


async def test_check_prompt_endpoint(client):
    resp = await client.post(
        "/api/prompt/check",
        json={"prompt": "# 角色\n你是助手，帮用户处理问题。用中文回答。"},
    )
    assert resp.status == 200
    data = await resp.json()
    assert data["char_count"] > 0
    assert data["total"] >= 1
    assert isinstance(data["items"], list)


async def test_check_prompt_empty(client):
    resp = await client.post("/api/prompt/check", json={"prompt": ""})
    data = await resp.json()
    assert data["char_count"] == 0
    assert any("空" in s for s in data["suggestions"])


async def test_validate_endpoint(client, ready_bot):
    resp = await client.post("/api/bots/hr-bot/validate")
    assert resp.status == 200
    data = await resp.json()
    assert data["ok"] is True
    assert "hr-bot" in data["summary"]


async def test_validate_endpoint_reports_error(client):
    await client.post("/api/bots", json={"name": "fresh"})
    data = await (await client.post("/api/bots/fresh/validate")).json()
    assert data["ok"] is False
    assert "WECHAT_BOT_ID" in data["error"]


# --------------------------------------------------------------------------
# 探测
# --------------------------------------------------------------------------


async def test_probe_mcp_requires_valid_config(client):
    await client.post("/api/bots", json={"name": "fresh"})
    resp = await client.post("/api/bots/fresh/probe/mcp")
    assert resp.status == 400
    assert "还没通过校验" in (await resp.json())["error"]


async def test_probe_mcp_reports_connection_failure(client, ready_bot):
    """连不上要给出能定位的原因，不是一句 TaskGroup"""
    resp = await client.post("/api/bots/hr-bot/probe/mcp")
    assert resp.status == 200
    data = await resp.json()
    server = data["servers"][0]
    assert server["ok"] is False
    assert server["error"]
    assert "TaskGroup" not in server["error"]
    assert data["identity_used"]


async def test_probe_mcp_lists_tools(client, project, monkeypatch):
    """
    用 in-memory MCP 服务端验证：探测要返回**全部**工具（忽略白名单），
    否则页面上就只能看到已经勾上的那几个，没法再勾新的。
    """
    from contextlib import AsyncExitStack

    from mcp import Client as McpClient

    import botkit.ui.service as svc
    from test_mcp_client import make_oa_server

    service.create_new_bot(project, "probe-bot")
    bot_dir = project / "bots" / "probe-bot"
    (bot_dir / ".env").write_text(
        "WECHAT_BOT_ID=i\nWECHAT_BOT_SECRET=s\n"
        "LLM_API_URL=https://x.invalid/v1\nLLM_API_KEY=k\nLLM_MODEL=m\n"
        "MCP_URL=https://x.invalid/mcp\n",
        encoding="utf-8",
    )
    # 白名单只留一个，探测结果应该仍然是全部
    data = svc.read_bot(project, "probe-bot")
    data["values"]["mcp"]["servers"][0]["allow_tools"] = ["get_system_config"]
    svc.save_bot(project, "probe-bot", {"values": data["values"]})

    fake = make_oa_server()

    async def connector(stack: AsyncExitStack, server, headers):
        return await stack.enter_async_context(McpClient(fake))

    real_client_cls = svc.McpToolClient
    monkeypatch.setattr(
        svc, "McpToolClient", lambda servers: real_client_cls(servers, connector=connector)
    )

    async with TestClient(TestServer(create_app(project))) as test_client:
        resp = await test_client.post("/api/bots/probe-bot/probe/mcp")
        payload = await resp.json()

    server = payload["servers"][0]
    assert server["ok"] is True
    names = [t["name"] for t in server["tools"]]
    assert "get_system_config" in names
    assert "create_it_ticket" in names
    assert "get_weather" in names  # 白名单外的也要列出来，供勾选

    ticket = next(t for t in server["tools"] if t["name"] == "create_it_ticket")
    params = {p["name"]: p for p in ticket["params"]}
    assert params["work_code"]["required"] is True


async def test_probe_wecom_requires_valid_config(client):
    await client.post("/api/bots", json={"name": "fresh"})
    resp = await client.post("/api/bots/fresh/probe/wecom")
    assert resp.status == 400
    assert "还没通过校验" in (await resp.json())["error"]


# --------------------------------------------------------------------------
# 只允许本机访问
# --------------------------------------------------------------------------


async def test_remote_requests_rejected(project):
    """
    页面能读写密钥，必须挡住非本机请求 —— 即使有人把监听地址改成 0.0.0.0。
    """
    from aiohttp import web

    app = create_app(project)

    @web.middleware
    async def pretend_remote(request, handler):
        request = request.clone()
        return await handler(request)

    # 直接调中间件，模拟一个来自别的机器的请求
    from botkit.ui.server import local_only_middleware

    class FakeRequest:
        remote = "10.1.2.3"
        method = "GET"
        path = "/api/bots"

    async def never_called(_request):
        raise AssertionError("远程请求不该走到 handler")

    response = await local_only_middleware(FakeRequest(), never_called)
    assert response.status == 403


async def test_local_requests_allowed(project):
    from botkit.ui.server import local_only_middleware

    class FakeRequest:
        remote = "127.0.0.1"
        method = "GET"
        path = "/api/bots"

    called = False

    async def handler(_request):
        nonlocal called
        called = True
        from aiohttp import web

        return web.json_response({})

    await local_only_middleware(FakeRequest(), handler)
    assert called


# --------------------------------------------------------------------------
# 异常处理
# --------------------------------------------------------------------------


async def test_unexpected_error_becomes_500_json(project, monkeypatch):
    import botkit.ui.server as server_module

    def boom(*_args, **_kwargs):
        raise RuntimeError("内部炸了")

    monkeypatch.setattr(server_module.service, "list_bots", boom)

    async with TestClient(TestServer(create_app(project))) as test_client:
        resp = await test_client.get("/api/bots")
        assert resp.status == 500
        assert "内部炸了" in (await resp.json())["error"]


async def test_unknown_route_404(client):
    resp = await client.get("/api/nope")
    assert resp.status == 404
