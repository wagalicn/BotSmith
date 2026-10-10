"""
两个 LLM 后端的适配层测试

重点是「后端返回 → 统一 LlmResponse」这一层的转换，
以及 aiohttp 后端对不规范响应的兜底（这是保留它的理由）。
"""

from __future__ import annotations

import json

import pytest
from openai import OpenAIError

from botkit.config import LlmConfig
from botkit.llm import create_backend
from botkit.llm.aiohttp_backend import AiohttpBackend, _parse_openai_payload
from botkit.llm.base import (
    LlmError,
    build_assistant_message,
    normalize_response,
    parse_tool_arguments,
)
from botkit.llm.openai_backend import OpenAIBackend


def cfg(client: str = "openai", api_url: str = "http://fake/v1") -> LlmConfig:
    return LlmConfig(api_url=api_url, api_key="sk-test", model="test-model", client=client)


# --------------------------------------------------------------------------
# 后端选择
# --------------------------------------------------------------------------


def test_create_backend_picks_openai():
    backend = create_backend(cfg("openai"))
    assert isinstance(backend, OpenAIBackend)
    assert backend.name == "openai"


def test_create_backend_picks_aiohttp():
    backend = create_backend(cfg("aiohttp", "http://fake/v1/chat/completions"))
    assert isinstance(backend, AiohttpBackend)
    assert backend.name == "aiohttp"


def test_create_backend_rejects_unknown():
    bad = cfg()
    bad.client = "curl"  # 绕过配置层校验，模拟内部误用
    with pytest.raises(LlmError, match="不认识的 LLM 后端"):
        create_backend(bad)


# --------------------------------------------------------------------------
# 共用的规范化逻辑
# --------------------------------------------------------------------------


def test_parse_tool_arguments_ok():
    assert parse_tool_arguments('{"a": 1, "中文": "值"}', "t") == {"a": 1, "中文": "值"}


def test_parse_tool_arguments_handles_empty():
    assert parse_tool_arguments("", "t") == {}
    assert parse_tool_arguments(None, "t") == {}


def test_parse_tool_arguments_handles_broken_json(caplog):
    """LLM 生成的 JSON 有时是坏的，不能因此崩掉整轮对话"""
    assert parse_tool_arguments("{不是 json", "my_tool") == {}
    assert any("my_tool" in r.getMessage() for r in caplog.records)


def test_parse_tool_arguments_rejects_non_object():
    assert parse_tool_arguments("[1, 2]", "t") == {}
    assert parse_tool_arguments('"字符串"', "t") == {}


def test_build_assistant_message_text_only():
    msg = build_assistant_message("你好", [])
    assert msg == {"role": "assistant", "content": "你好"}


def test_build_assistant_message_with_tool_calls():
    resp = normalize_response(
        None, [{"id": "c1", "function": {"name": "t", "arguments": '{"a":1}'}}]
    )
    assert resp.message["role"] == "assistant"
    assert resp.message["content"] is None
    tc = resp.message["tool_calls"][0]
    assert tc == {
        "id": "c1",
        "type": "function",
        "function": {"name": "t", "arguments": '{"a":1}'},
    }


def test_normalize_response_parses_arguments():
    resp = normalize_response(
        None, [{"id": "c1", "function": {"name": "t", "arguments": '{"a": 1}'}}]
    )
    assert resp.wants_tools
    assert resp.tool_calls[0].name == "t"
    assert resp.tool_calls[0].arguments == {"a": 1}
    assert resp.tool_calls[0].raw_arguments == '{"a": 1}'


def test_normalize_response_skips_tool_call_without_name(caplog):
    resp = normalize_response(None, [{"id": "c1", "function": {"arguments": "{}"}}])
    assert resp.tool_calls == []


def test_normalize_response_handles_object_arguments():
    """少数端点把 arguments 直接返回成对象而不是 JSON 字符串"""
    resp = normalize_response(
        None, [{"id": "c1", "function": {"name": "t", "arguments": {"a": 1}}}]
    )
    assert resp.tool_calls[0].arguments == {"a": 1}
    assert json.loads(resp.tool_calls[0].raw_arguments) == {"a": 1}


def test_normalize_response_generates_missing_id():
    resp = normalize_response(None, [{"function": {"name": "t", "arguments": "{}"}}])
    assert resp.tool_calls[0].id == "call_0"


# --------------------------------------------------------------------------
# openai 后端
# --------------------------------------------------------------------------


class FakeFunction:
    def __init__(self, name, arguments):
        self.name = name
        self.arguments = arguments


class FakeToolCall:
    def __init__(self, id, name, arguments):
        self.id = id
        self.function = FakeFunction(name, arguments)


class FakeMessage:
    def __init__(self, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls


class FakeChoice:
    def __init__(self, message):
        self.message = message


class FakeCompletion:
    def __init__(self, choices):
        self.choices = choices


class FakeCompletions:
    def __init__(self, result):
        self._result = result
        self.kwargs = None

    async def create(self, **kwargs):
        self.kwargs = kwargs
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


class FakeOpenAIClient:
    def __init__(self, result):
        self.chat = type("Chat", (), {})()
        self.chat.completions = FakeCompletions(result)
        self.closed = False

    async def close(self):
        self.closed = True


def make_openai_backend(result):
    backend = OpenAIBackend(cfg("openai"))
    backend._client = FakeOpenAIClient(result)
    return backend


async def test_openai_backend_text_reply():
    backend = make_openai_backend(
        FakeCompletion([FakeChoice(FakeMessage(content="你好"))])
    )
    resp = await backend.complete([{"role": "user", "content": "hi"}], [])
    assert resp.content == "你好"
    assert not resp.wants_tools


async def test_openai_backend_tool_calls():
    backend = make_openai_backend(
        FakeCompletion(
            [
                FakeChoice(
                    FakeMessage(
                        content=None,
                        tool_calls=[
                            FakeToolCall("c1", "get_system_config", "{}"),
                            FakeToolCall("c2", "create_it_ticket", '{"title":"t"}'),
                        ],
                    )
                )
            ]
        )
    )
    resp = await backend.complete([{"role": "user", "content": "hi"}], [])
    assert [tc.name for tc in resp.tool_calls] == [
        "get_system_config",
        "create_it_ticket",
    ]
    assert resp.tool_calls[1].arguments == {"title": "t"}


async def test_openai_backend_omits_empty_tools():
    """tools 传空列表有些端点会 400，没工具时不该带这个字段"""
    backend = make_openai_backend(
        FakeCompletion([FakeChoice(FakeMessage(content="ok"))])
    )
    await backend.complete([{"role": "user", "content": "hi"}], [])
    assert "tools" not in backend._client.chat.completions.kwargs


async def test_openai_backend_passes_tools_when_present():
    backend = make_openai_backend(
        FakeCompletion([FakeChoice(FakeMessage(content="ok"))])
    )
    tools = [{"type": "function", "function": {"name": "t"}}]
    await backend.complete([{"role": "user", "content": "hi"}], tools)
    kwargs = backend._client.chat.completions.kwargs
    assert kwargs["tools"] == tools
    assert kwargs["model"] == "test-model"
    assert kwargs["stream"] is False


async def test_openai_backend_wraps_sdk_error():
    backend = make_openai_backend(OpenAIError("鉴权失败"))
    with pytest.raises(LlmError, match="鉴权失败"):
        await backend.complete([], [])


async def test_openai_backend_wraps_unexpected_error():
    backend = make_openai_backend(RuntimeError("意外炸了"))
    with pytest.raises(LlmError, match="意外炸了"):
        await backend.complete([], [])


async def test_openai_backend_empty_choices():
    backend = make_openai_backend(FakeCompletion([]))
    with pytest.raises(LlmError, match="没有 choices"):
        await backend.complete([], [])


async def test_openai_backend_aclose():
    backend = make_openai_backend(FakeCompletion([]))
    await backend.aclose()
    assert backend._client.closed


# --------------------------------------------------------------------------
# aiohttp 后端
# --------------------------------------------------------------------------


class FakeResponse:
    def __init__(self, status=200, payload=None, text=""):
        self.status = status
        self._payload = payload
        self._text = text or json.dumps(payload, ensure_ascii=False) if payload else text

    async def text(self):
        return self._text

    async def json(self, content_type=None):
        if self._payload is None:
            raise ValueError("不是 JSON")
        return self._payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class FakeAiohttpSession:
    def __init__(self, response):
        self._response = response
        self.closed = False
        self.post_calls = []

    def post(self, url, headers=None, json=None):
        self.post_calls.append({"url": url, "headers": headers, "json": json})
        if isinstance(self._response, Exception):
            raise self._response
        return self._response

    async def close(self):
        self.closed = True


def make_aiohttp_backend(response):
    return AiohttpBackend(
        cfg("aiohttp", "http://fake/v1/chat/completions"),
        session=FakeAiohttpSession(response),
    )


async def test_aiohttp_backend_text_reply():
    backend = make_aiohttp_backend(
        FakeResponse(payload={"choices": [{"message": {"content": "你好"}}]})
    )
    resp = await backend.complete([{"role": "user", "content": "hi"}], [])
    assert resp.content == "你好"


async def test_aiohttp_backend_posts_to_full_url_with_auth():
    backend = make_aiohttp_backend(
        FakeResponse(payload={"choices": [{"message": {"content": "ok"}}]})
    )
    await backend.complete([{"role": "user", "content": "hi"}], [])
    call = backend._session.post_calls[0]
    assert call["url"] == "http://fake/v1/chat/completions"
    assert call["headers"]["Authorization"] == "Bearer sk-test"
    assert call["json"]["model"] == "test-model"
    assert "tools" not in call["json"]


async def test_aiohttp_backend_tool_calls():
    backend = make_aiohttp_backend(
        FakeResponse(
            payload={
                "choices": [
                    {
                        "message": {
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "c1",
                                    "type": "function",
                                    "function": {
                                        "name": "get_system_config",
                                        "arguments": "{}",
                                    },
                                }
                            ],
                        }
                    }
                ]
            }
        )
    )
    resp = await backend.complete([], [])
    assert resp.tool_calls[0].name == "get_system_config"


async def test_aiohttp_backend_http_error_includes_body():
    backend = make_aiohttp_backend(FakeResponse(status=401, text='{"error":"bad key"}'))
    with pytest.raises(LlmError) as ei:
        await backend.complete([], [])
    msg = str(ei.value)
    assert "401" in msg
    assert "bad key" in msg  # 原始响应体要能看到，方便排查


async def test_aiohttp_backend_non_json_response():
    backend = make_aiohttp_backend(FakeResponse(status=200, text="<html>502</html>"))
    with pytest.raises(LlmError, match="不是合法 JSON"):
        await backend.complete([], [])


async def test_aiohttp_backend_aclose_does_not_close_injected_session():
    """注入的 session 归调用方管，后端不该替它关掉"""
    session = FakeAiohttpSession(FakeResponse(payload={"choices": []}))
    backend = AiohttpBackend(cfg("aiohttp"), session=session)
    await backend.aclose()
    assert session.closed is False


# --------------------------------------------------------------------------
# aiohttp 后端存在的理由：应付不规范的响应
# --------------------------------------------------------------------------


def test_parse_payload_tolerates_extra_fields():
    """多塞的自定义字段不影响解析"""
    resp = _parse_openai_payload(
        {
            "id": "x",
            "自定义字段": {"随便": "什么"},
            "usage": {"total_tokens": 1},
            "choices": [
                {
                    "message": {"content": "你好", "推理过程": "隐藏的思考"},
                    "finish_reason": "stop",
                    "厂商扩展": 1,
                }
            ],
        }
    )
    assert resp.content == "你好"


def test_parse_payload_error_field_with_200():
    """有些端点用 200 返回错误，错误信息在 error 字段"""
    with pytest.raises(LlmError, match="额度不足"):
        _parse_openai_payload({"error": {"message": "额度不足"}})


def test_parse_payload_missing_choices():
    with pytest.raises(LlmError, match="没有可用的 choices"):
        _parse_openai_payload({"id": "x"})


def test_parse_payload_missing_message():
    with pytest.raises(LlmError, match="message 缺失"):
        _parse_openai_payload({"choices": [{"finish_reason": "stop"}]})


def test_parse_payload_non_dict_top_level():
    with pytest.raises(LlmError, match="顶层不是对象"):
        _parse_openai_payload(["不该是列表"])


def test_parse_payload_non_string_content_is_coerced():
    resp = _parse_openai_payload({"choices": [{"message": {"content": 12345}}]})
    assert resp.content == "12345"


def test_parse_payload_tool_calls_not_a_list_is_ignored():
    resp = _parse_openai_payload(
        {"choices": [{"message": {"content": "ok", "tool_calls": "坏数据"}}]}
    )
    assert resp.tool_calls == []
    assert resp.content == "ok"


def test_parse_payload_skips_non_dict_tool_call_entries():
    resp = _parse_openai_payload(
        {
            "choices": [
                {
                    "message": {
                        "content": None,
                        "tool_calls": [
                            "坏条目",
                            {"id": "c1", "function": {"name": "t", "arguments": "{}"}},
                        ],
                    }
                }
            ]
        }
    )
    assert [tc.name for tc in resp.tool_calls] == ["t"]
