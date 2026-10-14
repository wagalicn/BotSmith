"""
/chat 的 SSE 流式 HTTP 层测试

用 aiohttp 测试客户端起真 app，带 Accept: text/event-stream 打 /chat，
验证逐段 SSE 事件；同时验证不带该头时仍走原来的 JSON 响应（回归）。
"""

from __future__ import annotations

import json

import pytest
from aiohttp.test_utils import TestClient, TestServer

from botkit.api_server import create_api_app
from botkit.config import ApiConfig, IdentityConfig

from test_engine import make_config
from test_runtime import RecordingEngine


def _make_app(reply="你好呀", token="", raises=None):
    from botkit.hooks import Hooks
    from botkit.runtime import MessagePipeline
    from botkit.session import SessionStore

    cfg = make_config()
    cfg.name = "demo"
    cfg.identity = IdentityConfig(pattern=None)
    cfg.api = ApiConfig(enabled=True, token=token)
    engine = RecordingEngine(reply=reply, raises=raises, config=cfg)
    sessions = SessionStore.from_config(cfg.session)
    pipeline = MessagePipeline(cfg, engine, sessions, Hooks())
    return create_api_app(cfg, pipeline, hooks=Hooks())


@pytest.fixture
async def client():
    async with TestClient(TestServer(_make_app())) as c:
        yield c


def _parse_sse(text: str):
    """把 SSE 原始文本解析成 [(event, data_dict), ...]"""
    events = []
    for block in text.strip().split("\n\n"):
        if not block.strip():
            continue
        event = None
        data = None
        for line in block.splitlines():
            if line.startswith("event:"):
                event = line[len("event:"):].strip()
            elif line.startswith("data:"):
                data = json.loads(line[len("data:"):].strip())
        events.append((event, data))
    return events


# --------------------------------------------------------------------------
# SSE 流式
# --------------------------------------------------------------------------


async def test_sse_streams_tokens_and_done(client):
    resp = await client.post(
        "/chat",
        json={"message": "hi", "user": "u1", "session_id": "s1"},
        headers={"Accept": "text/event-stream"},
    )
    assert resp.status == 200
    assert "text/event-stream" in resp.headers["Content-Type"]

    events = _parse_sse(await resp.text())

    # 文本增量：delta 拼起来是完整回复
    deltas = [d["delta"] for ev, d in events if ev is None and "delta" in d]
    assert "".join(deltas) == "你好呀"

    # 结束事件带 kind 和 session_id
    done = [d for ev, d in events if ev == "done"]
    assert len(done) == 1
    assert done[0]["kind"] == "llm"
    assert done[0]["session_id"] == "s1"


async def test_sse_progress_event(client):
    """RecordingEngine 会推一条进度，应出现在 SSE 的 progress 事件里"""
    resp = await client.post(
        "/chat",
        json={"message": "hi", "user": "u1"},
        headers={"Accept": "text/event-stream"},
    )
    events = _parse_sse(await resp.text())
    progress = [d["text"] for ev, d in events if ev == "progress"]
    assert "引擎的进度提示" in progress


async def test_sse_error_event_on_engine_failure():
    from botkit.llm.base import LlmError

    app = _make_app(raises=LlmError("炸了"))
    async with TestClient(TestServer(app)) as c:
        resp = await c.post(
            "/chat",
            json={"message": "hi", "user": "u1"},
            headers={"Accept": "text/event-stream"},
        )
        # SSE 一旦开流就是 200，错误通过 error 事件告知
        assert resp.status == 200
        events = _parse_sse(await resp.text())
        kinds = [ev for ev, _ in events]
        # 引擎异常被 handle_stream 转成 KIND_ERROR 文本段，末尾 done
        assert "done" in kinds


# --------------------------------------------------------------------------
# 不带 Accept 头：仍走原来的 JSON（回归）
# --------------------------------------------------------------------------


async def test_non_sse_still_returns_json(client):
    resp = await client.post(
        "/chat", json={"message": "hi", "user": "u1", "session_id": "s1"}
    )
    assert resp.status == 200
    assert resp.headers["Content-Type"].startswith("application/json")
    body = await resp.json()
    assert body["reply"] == "你好呀"
    assert body["kind"] == "llm"
    assert body["session_id"] == "s1"


async def test_sse_still_requires_auth():
    """SSE 路径也要过鉴权：配了 token 不带就 401（在开流之前）"""
    app = _make_app(token="sek")
    async with TestClient(TestServer(app)) as c:
        resp = await c.post(
            "/chat",
            json={"message": "hi", "user": "u1"},
            headers={"Accept": "text/event-stream"},
        )
        assert resp.status == 401


async def test_sse_missing_message_still_400():
    """缺 message 这类参数错误在开流前就返回 400，不走 SSE"""
    app = _make_app()
    async with TestClient(TestServer(app)) as c:
        resp = await c.post(
            "/chat",
            json={"user": "u1"},
            headers={"Accept": "text/event-stream"},
        )
        assert resp.status == 400
