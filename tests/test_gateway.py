"""多 Agent 网关 HTTP 层测试：api_server.build_gateway_app / GET /agents

用 aiohttp 自带测试客户端起真网关 app，走真实的 add_subapp 路由与鉴权。
每个 Agent 用假引擎（RecordingEngine）组真 MessagePipeline，不碰网络。
"""

from __future__ import annotations

import pytest
from aiohttp.test_utils import TestClient, TestServer

from botkit.api_server import GatewayAgent, build_gateway_app
from botkit.config import ApiConfig, IdentityConfig

from test_engine import make_config
from test_runtime import RecordingEngine


def _make_agent(
    name: str, reply: str, token: str | None = None
) -> GatewayAgent:
    """造一个可挂载的网关 Agent：真 MessagePipeline + 假引擎。"""
    from botkit.hooks import Hooks
    from botkit.runtime import MessagePipeline
    from botkit.session import SessionStore

    cfg = make_config()
    cfg.name = name
    cfg.identity = IdentityConfig(pattern=None)  # 不校验身份，简化 /chat 测试
    cfg.api = ApiConfig(
        enabled=True,
        token=token if token is not None else f"{name}-token",
    )
    engine = RecordingEngine(reply=reply, config=cfg)
    sessions = SessionStore.from_config(cfg.session)
    pipeline = MessagePipeline(cfg, engine, sessions, Hooks())
    return GatewayAgent(name=name, config=cfg, pipeline=pipeline)


def _auth(name: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {name}-token"}


@pytest.fixture
async def gateway_client():
    agents = [
        _make_agent("demand", "我是 demand"),
        _make_agent("rom", "我是 rom"),
        GatewayAgent(name="broken", error="配置坏了"),  # 不可用
    ]
    app = build_gateway_app(agents)
    async with TestClient(TestServer(app)) as client:
        yield client


# --------------------------------------------------------------------------
# 路由到正确的 Agent
# --------------------------------------------------------------------------


async def test_chat_routes_to_correct_agent(gateway_client):
    r1 = await gateway_client.post(
        "/demand/chat",
        json={"message": "你好", "user": "u1"},
        headers=_auth("demand"),
    )
    assert r1.status == 200
    assert (await r1.json())["reply"] == "我是 demand"

    r2 = await gateway_client.post(
        "/rom/chat",
        json={"message": "你好", "user": "u1"},
        headers=_auth("rom"),
    )
    assert r2.status == 200
    assert (await r2.json())["reply"] == "我是 rom"


async def test_each_agent_has_its_own_health(gateway_client):
    r = await gateway_client.get("/demand/health")
    assert r.status == 200
    body = await r.json()
    assert body["ok"] is True
    assert body["bot"] == "demand"


async def test_broken_agent_has_no_routes(gateway_client):
    # 坏 Agent 不挂路由
    r = await gateway_client.post(
        "/broken/chat", json={"message": "x", "user": "u1"}
    )
    assert r.status == 404


# --------------------------------------------------------------------------
# GET /agents 与网关 /health
# --------------------------------------------------------------------------


async def test_agents_lists_all_with_status(gateway_client):
    r = await gateway_client.get("/agents")
    assert r.status == 200
    agents = {a["name"]: a for a in (await r.json())["agents"]}

    assert set(agents) == {"demand", "rom", "broken"}
    assert agents["demand"]["ok"] is True
    assert agents["demand"]["path"] == "/demand/chat"
    assert agents["broken"]["ok"] is False
    assert agents["broken"]["error"] == "配置坏了"


async def test_gateway_health_counts(gateway_client):
    r = await gateway_client.get("/health")
    assert r.status == 200
    body = await r.json()
    assert body["ok"] is True
    assert body["agents"] == 3
    assert body["available"] == 2  # broken 不算


# --------------------------------------------------------------------------
# per-Agent 鉴权：各用各的 token
# --------------------------------------------------------------------------


async def test_per_agent_auth_isolated():
    agents = [
        _make_agent("secure-a", "A", token="sek-a"),
        _make_agent("secure-b", "B", token="sek-b"),
    ]
    app = build_gateway_app(agents)
    async with TestClient(TestServer(app)) as client:
        # 不带 token → 401
        r = await client.post(
            "/secure-a/chat", json={"message": "x", "user": "u1"}
        )
        assert r.status == 401

        # 各自 token 只能访问所属 Agent
        r = await client.post(
            "/secure-a/chat",
            json={"message": "x", "user": "u1"},
            headers={"Authorization": "Bearer sek-a"},
        )
        assert r.status == 200

        r = await client.post(
            "/secure-b/chat",
            json={"message": "x", "user": "u1"},
            headers={"Authorization": "Bearer sek-a"},
        )
        assert r.status == 401

        r = await client.post(
            "/secure-b/chat",
            json={"message": "x", "user": "u1"},
            headers={"Authorization": "Bearer sek-b"},
        )
        assert r.status == 200


async def test_empty_gateway():
    """没有任何 Agent 时，/agents 空、/health 计数为 0"""
    app = build_gateway_app([])
    async with TestClient(TestServer(app)) as client:
        r = await client.get("/agents")
        assert (await r.json())["agents"] == []
        r = await client.get("/health")
        body = await r.json()
        assert body["agents"] == 0 and body["available"] == 0
