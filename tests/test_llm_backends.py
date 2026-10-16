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


def cfg(
    client: str = "openai",
    api_url: str = "http://fake/v1",
    http_proxy: str | None = None,
) -> LlmConfig:
    return LlmConfig(
        api_url=api_url,
        api_key="sk-test",
        model="test-model",
        client=client,
        http_proxy=http_proxy,
    )


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

    def post(self, url, headers=None, json=None, proxy=None):
        self.post_calls.append(
            {"url": url, "headers": headers, "json": json, "proxy": proxy}
        )
        if isinstance(self._response, Exception):
            raise self._response
        return self._response

    async def close(self):
        self.closed = True


def make_aiohttp_backend(response, http_proxy=None):
    return AiohttpBackend(
        cfg("aiohttp", "http://fake/v1/chat/completions", http_proxy=http_proxy),
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


# --------------------------------------------------------------------------
# 流式聚合器 StreamAccumulator
# --------------------------------------------------------------------------


def test_stream_accumulator_text_only():
    from botkit.llm.base import StreamAccumulator

    acc = StreamAccumulator()
    assert acc.add_text("你") == "你"
    acc.add_text("好")
    acc.add_text("")  # 空增量忽略
    acc.add_text(None)
    assert acc.text == "你好"

    resp = acc.finish()
    assert resp.content == "你好"
    assert not resp.wants_tools
    assert resp.message == {"role": "assistant", "content": "你好"}


def test_stream_accumulator_tool_calls_merged_by_index():
    from botkit.llm.base import StreamAccumulator

    acc = StreamAccumulator()
    # 一个工具的 arguments 分多片到达
    acc.add_tool_call_delta(0, call_id="c1", name="create_it_ticket")
    acc.add_tool_call_delta(0, arguments='{"title"')
    acc.add_tool_call_delta(0, arguments=':"修电脑"}')
    # 第二个工具
    acc.add_tool_call_delta(1, call_id="c2", name="get_system_config", arguments="{}")

    resp = acc.finish()
    assert resp.wants_tools
    assert [tc.name for tc in resp.tool_calls] == [
        "create_it_ticket",
        "get_system_config",
    ]
    assert resp.tool_calls[0].arguments == {"title": "修电脑"}
    assert resp.tool_calls[0].id == "c1"


def test_stream_accumulator_empty_finish():
    from botkit.llm.base import StreamAccumulator

    resp = StreamAccumulator().finish()
    assert resp.content is None
    assert not resp.wants_tools


# --------------------------------------------------------------------------
# openai 后端流式
# --------------------------------------------------------------------------


class FakeDelta:
    def __init__(self, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls


class FakeStreamChoice:
    def __init__(self, delta):
        self.delta = delta


class FakeStreamChunk:
    def __init__(self, delta):
        self.choices = [FakeStreamChoice(delta)]


class FakeToolCallDelta:
    def __init__(self, index, id=None, name=None, arguments=None):
        self.index = index
        self.id = id
        self.function = FakeFunction(name, arguments) if (name or arguments) else None


class FakeStream:
    """模拟 openai SDK stream=True 时返回的异步可迭代对象"""

    def __init__(self, chunks):
        self._chunks = chunks

    def __aiter__(self):
        self._it = iter(self._chunks)
        return self

    async def __anext__(self):
        try:
            return next(self._it)
        except StopIteration:
            raise StopAsyncIteration


async def _collect(aiter):
    return [c async for c in aiter]


async def test_openai_stream_text_tokens():
    backend = make_openai_backend(
        FakeStream(
            [
                FakeStreamChunk(FakeDelta(content="你")),
                FakeStreamChunk(FakeDelta(content="好")),
                FakeStreamChunk(FakeDelta(content="呀")),
            ]
        )
    )
    chunks = await _collect(backend.stream([{"role": "user", "content": "hi"}], []))

    deltas = [c.delta for c in chunks if not c.done]
    assert deltas == ["你", "好", "呀"]

    done = chunks[-1]
    assert done.done is True
    assert done.response.content == "你好呀"
    assert not done.response.wants_tools
    # stream=True 要真的传给 SDK
    assert backend._client.chat.completions.kwargs["stream"] is True


async def test_openai_stream_tool_calls():
    backend = make_openai_backend(
        FakeStream(
            [
                FakeStreamChunk(
                    FakeDelta(
                        tool_calls=[
                            FakeToolCallDelta(0, id="c1", name="get_system_config")
                        ]
                    )
                ),
                FakeStreamChunk(
                    FakeDelta(tool_calls=[FakeToolCallDelta(0, arguments="{}")])
                ),
            ]
        )
    )
    chunks = await _collect(backend.stream([], []))

    # 工具调用轮通常没有文本增量
    assert [c.delta for c in chunks if not c.done] == []
    done = chunks[-1]
    assert done.done is True
    assert done.response.wants_tools
    assert done.response.tool_calls[0].name == "get_system_config"


async def test_openai_stream_wraps_error():
    backend = make_openai_backend(OpenAIError("连不上"))
    with pytest.raises(LlmError, match="连不上"):
        await _collect(backend.stream([], []))


# --------------------------------------------------------------------------
# aiohttp 后端流式
# --------------------------------------------------------------------------


class FakeStreamContent:
    """模拟 aiohttp resp.content：按行异步产出 bytes"""

    def __init__(self, lines):
        self._lines = lines

    def __aiter__(self):
        self._it = iter(self._lines)
        return self

    async def __anext__(self):
        try:
            return next(self._it)
        except StopIteration:
            raise StopAsyncIteration


class FakeStreamResponse:
    def __init__(self, lines, status=200, text=""):
        self.status = status
        self.content = FakeStreamContent(lines)
        self._text = text

    async def text(self):
        return self._text

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


def _sse(obj: dict) -> bytes:
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n".encode("utf-8")


async def test_aiohttp_stream_text_tokens():
    lines = [
        _sse({"choices": [{"delta": {"content": "你"}}]}),
        _sse({"choices": [{"delta": {"content": "好"}}]}),
        b"data: [DONE]\n",
    ]
    backend = make_aiohttp_backend(FakeStreamResponse(lines))
    chunks = await _collect(backend.stream([{"role": "user", "content": "hi"}], []))

    assert [c.delta for c in chunks if not c.done] == ["你", "好"]
    done = chunks[-1]
    assert done.done is True
    assert done.response.content == "你好"
    # stream=True 要真的写进请求体
    assert backend._session.post_calls[0]["json"]["stream"] is True


async def test_aiohttp_stream_tool_calls():
    lines = [
        _sse(
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "c1",
                                    "function": {
                                        "name": "create_it_ticket",
                                        "arguments": '{"title"',
                                    },
                                }
                            ]
                        }
                    }
                ]
            }
        ),
        _sse(
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {"index": 0, "function": {"arguments": ':"修"}'}}
                            ]
                        }
                    }
                ]
            }
        ),
        b"data: [DONE]\n",
    ]
    backend = make_aiohttp_backend(FakeStreamResponse(lines))
    chunks = await _collect(backend.stream([], []))

    done = chunks[-1]
    assert done.response.wants_tools
    assert done.response.tool_calls[0].name == "create_it_ticket"
    assert done.response.tool_calls[0].arguments == {"title": "修"}


async def test_aiohttp_stream_skips_bad_lines():
    """坏分片、空行、非 data 行都跳过，不炸"""
    lines = [
        b"\n",
        b": keep-alive comment\n",
        b"data: {not json}\n",
        _sse({"choices": [{"delta": {"content": "好"}}]}),
        b"data: [DONE]\n",
    ]
    backend = make_aiohttp_backend(FakeStreamResponse(lines))
    chunks = await _collect(backend.stream([], []))
    assert [c.delta for c in chunks if not c.done] == ["好"]


async def test_aiohttp_stream_http_error():
    backend = make_aiohttp_backend(
        FakeStreamResponse([], status=500, text="boom")
    )
    with pytest.raises(LlmError, match="500"):
        await _collect(backend.stream([], []))


# --------------------------------------------------------------------------
# llm.http_proxy 代理配置
# --------------------------------------------------------------------------


async def test_openai_backend_no_proxy_by_default():
    """不配 http_proxy 时不建代理 client（行为与以前一致）"""
    backend = OpenAIBackend(cfg("openai"))
    assert backend._http_client is None
    await backend.aclose()


async def test_openai_backend_builds_proxy_client():
    """配了 http_proxy 时建一个走代理的 http client 传给 SDK"""
    backend = OpenAIBackend(cfg("openai", http_proxy="http://127.0.0.1:7890"))
    assert backend._http_client is not None
    # 收尾要能把自建的 client 关掉，不留未关闭告警
    await backend.aclose()
    assert backend._http_client.is_closed


async def test_aiohttp_backend_no_proxy_by_default():
    """不配代理时 post 的 proxy 参数为 None"""
    backend = make_aiohttp_backend(
        FakeResponse(payload={"choices": [{"message": {"content": "ok"}}]})
    )
    await backend.complete([{"role": "user", "content": "hi"}], [])
    assert backend._session.post_calls[0]["proxy"] is None


async def test_aiohttp_backend_passes_proxy():
    """配了代理时 post 带上 proxy=代理地址"""
    backend = make_aiohttp_backend(
        FakeResponse(payload={"choices": [{"message": {"content": "ok"}}]}),
        http_proxy="http://127.0.0.1:7890",
    )
    await backend.complete([{"role": "user", "content": "hi"}], [])
    assert backend._session.post_calls[0]["proxy"] == "http://127.0.0.1:7890"


async def test_aiohttp_stream_passes_proxy():
    """流式 stream() 那处 post 也要带上 proxy（容易漏的一处）"""
    lines = [
        _sse({"choices": [{"delta": {"content": "好"}}]}),
        b"data: [DONE]\n",
    ]
    backend = make_aiohttp_backend(
        FakeStreamResponse(lines), http_proxy="http://127.0.0.1:7890"
    )
    await _collect(backend.stream([], []))
    assert backend._session.post_calls[0]["proxy"] == "http://127.0.0.1:7890"


# --------------------------------------------------------------------------
# anthropic 后端（Claude 原生 Messages 协议）
# --------------------------------------------------------------------------


def anthropic_cfg(api_url="http://fake/anthropic/v1", http_proxy=None, max_tokens=None):
    return LlmConfig(
        api_url=api_url,
        api_key="sk-test",
        model="anthropic.claude-sonnet-5",
        client="anthropic",
        http_proxy=http_proxy,
        max_tokens=max_tokens,
    )


def make_anthropic_backend(response, **kw):
    from botkit.llm.anthropic_backend import AnthropicBackend

    return AnthropicBackend(
        anthropic_cfg(**kw), session=FakeAiohttpSession(response)
    )


def _msg_response(blocks, stop_reason="end_turn"):
    return FakeResponse(payload={"content": blocks, "stop_reason": stop_reason})


# -- 后端选择 ---------------------------------------------------------------


def test_create_backend_picks_anthropic():
    from botkit.llm.anthropic_backend import AnthropicBackend

    backend = create_backend(anthropic_cfg())
    assert isinstance(backend, AnthropicBackend)
    assert backend.name == "anthropic"


# -- 请求转换：messages ------------------------------------------------------


def test_convert_messages_lifts_system_to_top_level():
    from botkit.llm.anthropic_backend import _convert_messages

    system, converted = _convert_messages(
        [
            {"role": "system", "content": "你是助手"},
            {"role": "user", "content": "你好"},
        ]
    )
    assert system == "你是助手"
    assert converted == [{"role": "user", "content": "你好"}]


def test_convert_messages_joins_multiple_system():
    from botkit.llm.anthropic_backend import _convert_messages

    system, _ = _convert_messages(
        [
            {"role": "system", "content": "第一段"},
            {"role": "system", "content": "第二段"},
            {"role": "user", "content": "hi"},
        ]
    )
    assert system == "第一段\n\n第二段"


def test_convert_messages_assistant_tool_calls_become_tool_use():
    from botkit.llm.anthropic_backend import _convert_messages

    _, converted = _convert_messages(
        [
            {"role": "user", "content": "报修"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "c1",
                        "function": {"name": "create_it_ticket", "arguments": '{"a":1}'},
                    }
                ],
            },
        ]
    )
    block = converted[-1]["content"][0]
    assert converted[-1]["role"] == "assistant"
    assert block["type"] == "tool_use"
    assert block["id"] == "c1"
    assert block["name"] == "create_it_ticket"
    # arguments 的 JSON 字符串要解析成对象
    assert block["input"] == {"a": 1}


def test_convert_messages_tool_result_becomes_user_block():
    from botkit.llm.anthropic_backend import _convert_messages

    _, converted = _convert_messages(
        [
            {"role": "user", "content": "q"},
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "c1", "function": {"name": "t", "arguments": "{}"}}
            ]},
            {"role": "tool", "tool_call_id": "c1", "content": "工具结果"},
        ]
    )
    last = converted[-1]
    assert last["role"] == "user"
    assert last["content"][0] == {
        "type": "tool_result",
        "tool_use_id": "c1",
        "content": "工具结果",
    }


def test_convert_messages_merges_consecutive_tool_results():
    """
    一轮调多个工具时会产生多条 role=tool，转换后都是 user。
    Messages API 要求 user/assistant 交替，必须合并成一条，否则请求被拒。
    """
    from botkit.llm.anthropic_backend import _convert_messages

    _, converted = _convert_messages(
        [
            {"role": "user", "content": "q"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {"id": "c1", "function": {"name": "t1", "arguments": "{}"}},
                    {"id": "c2", "function": {"name": "t2", "arguments": "{}"}},
                ],
            },
            {"role": "tool", "tool_call_id": "c1", "content": "结果1"},
            {"role": "tool", "tool_call_id": "c2", "content": "结果2"},
        ]
    )
    roles = [m["role"] for m in converted]
    # 角色必须交替，不能出现连续 user
    assert roles == ["user", "assistant", "user"]
    # 两个工具结果合并进同一条消息
    assert len(converted[-1]["content"]) == 2
    assert [b["tool_use_id"] for b in converted[-1]["content"]] == ["c1", "c2"]


def test_convert_messages_keeps_assistant_text_with_tool_calls():
    """模型边说话边调工具时，文本和 tool_use 都要保留"""
    from botkit.llm.anthropic_backend import _convert_messages

    _, converted = _convert_messages(
        [
            {"role": "user", "content": "q"},
            {
                "role": "assistant",
                "content": "我查一下",
                "tool_calls": [
                    {"id": "c1", "function": {"name": "t", "arguments": "{}"}}
                ],
            },
        ]
    )
    types = [b["type"] for b in converted[-1]["content"]]
    assert types == ["text", "tool_use"]


def test_convert_messages_broken_tool_arguments_become_empty():
    from botkit.llm.anthropic_backend import _convert_messages

    _, converted = _convert_messages(
        [
            {"role": "user", "content": "q"},
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "c1", "function": {"name": "t", "arguments": "{不是 json"}}
            ]},
        ]
    )
    assert converted[-1]["content"][0]["input"] == {}


# -- 请求转换：tools --------------------------------------------------------


def test_convert_tools_renames_parameters_to_input_schema():
    from botkit.llm.anthropic_backend import _convert_tools

    out = _convert_tools(
        [
            {
                "type": "function",
                "function": {
                    "name": "t",
                    "description": "说明",
                    "parameters": {"type": "object", "properties": {"a": {}}},
                },
            }
        ]
    )
    assert out == [
        {
            "name": "t",
            "description": "说明",
            "input_schema": {"type": "object", "properties": {"a": {}}},
        }
    ]


def test_convert_tools_skips_malformed():
    from botkit.llm.anthropic_backend import _convert_tools

    assert _convert_tools([{"type": "function"}, {"function": {}}]) == []


# -- 响应解析 ---------------------------------------------------------------


def test_parse_messages_response_text():
    from botkit.llm.anthropic_backend import _parse_messages_response

    resp = _parse_messages_response(
        {"content": [{"type": "text", "text": "你好"}], "stop_reason": "end_turn"}
    )
    assert resp.content == "你好"
    assert not resp.wants_tools


def test_parse_messages_response_joins_text_blocks():
    from botkit.llm.anthropic_backend import _parse_messages_response

    resp = _parse_messages_response(
        {"content": [{"type": "text", "text": "你"}, {"type": "text", "text": "好"}]}
    )
    assert resp.content == "你好"


def test_parse_messages_response_tool_use():
    from botkit.llm.anthropic_backend import _parse_messages_response

    resp = _parse_messages_response(
        {
            "content": [
                {"type": "tool_use", "id": "toolu_1", "name": "t", "input": {"a": 1}}
            ],
            "stop_reason": "tool_use",
        }
    )
    assert resp.wants_tools
    tc = resp.tool_calls[0]
    assert tc.id == "toolu_1"
    assert tc.name == "t"
    assert tc.arguments == {"a": 1}


def test_parse_messages_response_error_payload():
    from botkit.llm.anthropic_backend import _parse_messages_response

    with pytest.raises(LlmError, match="额度"):
        _parse_messages_response({"type": "error", "error": {"message": "额度不足"}})


def test_parse_messages_response_missing_content():
    from botkit.llm.anthropic_backend import _parse_messages_response

    with pytest.raises(LlmError, match="content"):
        _parse_messages_response({"id": "msg_1"})


# -- complete 端到端 --------------------------------------------------------


async def test_anthropic_complete_builds_request():
    backend = make_anthropic_backend(
        _msg_response([{"type": "text", "text": "好"}])
    )
    resp = await backend.complete(
        [{"role": "system", "content": "你是助手"}, {"role": "user", "content": "hi"}],
        [],
    )
    assert resp.content == "好"

    call = backend._session.post_calls[0]
    # 路径自己拼 /messages
    assert call["url"] == "http://fake/anthropic/v1/messages"
    body = call["json"]
    assert body["model"] == "anthropic.claude-sonnet-5"
    assert body["system"] == "你是助手"
    assert body["messages"] == [{"role": "user", "content": "hi"}]
    assert body["stream"] is False
    # 没配 max_tokens 时兜底 8192
    assert body["max_tokens"] == 8192
    # tools 为空时不带这个字段
    assert "tools" not in body


async def test_anthropic_uses_x_api_key_by_default():
    """
    Anthropic 官方及兼容网关用 x-api-key。绝不能同时带 authorization ——
    Anthropic 对同时出现两个认证头的请求直接返回 401。
    """
    backend = make_anthropic_backend(
        _msg_response([{"type": "text", "text": "ok"}]),
        api_url="https://api.anthropic.com/v1",
    )
    await backend.complete([{"role": "user", "content": "hi"}], [])
    headers = backend._session.post_calls[0]["headers"]
    assert headers["x-api-key"] == "sk-test"
    assert "Authorization" not in headers
    assert headers["anthropic-version"]


@pytest.mark.parametrize(
    "api_url",
    [
        "https://bedrock-mantle.us-east-1.api.aws/anthropic/v1",
        "https://bedrock-runtime.us-east-1.amazonaws.com/anthropic/v1",
    ],
)
async def test_anthropic_uses_bearer_on_bedrock(api_url):
    """Bedrock 认 Authorization: Bearer，这时不能带 x-api-key"""
    backend = make_anthropic_backend(
        _msg_response([{"type": "text", "text": "ok"}]), api_url=api_url
    )
    await backend.complete([{"role": "user", "content": "hi"}], [])
    headers = backend._session.post_calls[0]["headers"]
    assert headers["Authorization"] == "Bearer sk-test"
    assert "x-api-key" not in headers


def test_is_bedrock_host_detection():
    from botkit.llm.anthropic_backend import _is_bedrock_host

    assert _is_bedrock_host("https://bedrock-mantle.us-east-1.api.aws/anthropic/v1")
    assert _is_bedrock_host("https://bedrock-runtime.us-east-1.amazonaws.com/openai/v1")
    assert not _is_bedrock_host("https://api.anthropic.com/v1")
    assert not _is_bedrock_host("http://my-gateway.internal/anthropic/v1")
    assert not _is_bedrock_host("")


async def test_anthropic_max_tokens_from_config():
    backend = make_anthropic_backend(
        _msg_response([{"type": "text", "text": "ok"}]), max_tokens=1024
    )
    await backend.complete([{"role": "user", "content": "hi"}], [])
    assert backend._session.post_calls[0]["json"]["max_tokens"] == 1024


async def test_anthropic_passes_tools():
    backend = make_anthropic_backend(_msg_response([{"type": "text", "text": "ok"}]))
    await backend.complete(
        [{"role": "user", "content": "hi"}],
        [{"type": "function", "function": {"name": "t", "parameters": {}}}],
    )
    tools = backend._session.post_calls[0]["json"]["tools"]
    assert tools[0]["name"] == "t"
    assert "input_schema" in tools[0]


async def test_anthropic_complete_http_error_includes_body():
    backend = make_anthropic_backend(
        FakeResponse(status=400, text='{"error":"bad model"}')
    )
    with pytest.raises(LlmError) as ei:
        await backend.complete([], [])
    assert "400" in str(ei.value)
    assert "bad model" in str(ei.value)


async def test_anthropic_passes_proxy():
    backend = make_anthropic_backend(
        _msg_response([{"type": "text", "text": "ok"}]),
        http_proxy="http://127.0.0.1:7890",
    )
    await backend.complete([{"role": "user", "content": "hi"}], [])
    assert backend._session.post_calls[0]["proxy"] == "http://127.0.0.1:7890"


# -- stream 端到端 ----------------------------------------------------------


async def test_anthropic_stream_text_deltas():
    lines = [
        _sse({"type": "message_start"}),
        _sse({"type": "content_block_start", "index": 0,
              "content_block": {"type": "text"}}),
        _sse({"type": "content_block_delta", "index": 0,
              "delta": {"type": "text_delta", "text": "你"}}),
        _sse({"type": "content_block_delta", "index": 0,
              "delta": {"type": "text_delta", "text": "好"}}),
        _sse({"type": "message_stop"}),
    ]
    backend = make_anthropic_backend(FakeStreamResponse(lines))
    chunks = await _collect(backend.stream([{"role": "user", "content": "hi"}], []))

    assert [c.delta for c in chunks if not c.done] == ["你", "好"]
    done = chunks[-1]
    assert done.done is True
    assert done.response.content == "你好"
    assert not done.response.wants_tools
    assert backend._session.post_calls[0]["json"]["stream"] is True


async def test_anthropic_stream_tool_use_assembled_from_partial_json():
    """工具参数通过 input_json_delta 分片到达，要拼起来"""
    lines = [
        _sse({"type": "content_block_start", "index": 0,
              "content_block": {"type": "tool_use", "id": "toolu_1",
                                "name": "create_it_ticket"}}),
        _sse({"type": "content_block_delta", "index": 0,
              "delta": {"type": "input_json_delta", "partial_json": '{"title"'}}),
        _sse({"type": "content_block_delta", "index": 0,
              "delta": {"type": "input_json_delta", "partial_json": ':"修电脑"}'}}),
        _sse({"type": "message_stop"}),
    ]
    backend = make_anthropic_backend(FakeStreamResponse(lines))
    chunks = await _collect(backend.stream([], []))

    # 工具轮没有文本增量
    assert [c.delta for c in chunks if not c.done] == []
    done = chunks[-1]
    assert done.response.wants_tools
    tc = done.response.tool_calls[0]
    assert tc.id == "toolu_1"
    assert tc.name == "create_it_ticket"
    assert tc.arguments == {"title": "修电脑"}


async def test_anthropic_stream_skips_bad_lines():
    lines = [
        b"\n",
        b"event: content_block_delta\n",  # event 行忽略，类型在 data 里
        b"data: {not json}\n",
        _sse({"type": "content_block_delta", "index": 0,
              "delta": {"type": "text_delta", "text": "好"}}),
        _sse({"type": "message_stop"}),
    ]
    backend = make_anthropic_backend(FakeStreamResponse(lines))
    chunks = await _collect(backend.stream([], []))
    assert [c.delta for c in chunks if not c.done] == ["好"]


async def test_anthropic_stream_http_error():
    backend = make_anthropic_backend(FakeStreamResponse([], status=500, text="boom"))
    with pytest.raises(LlmError, match="500"):
        await _collect(backend.stream([], []))


async def test_anthropic_stream_error_event():
    lines = [
        _sse({"type": "error", "error": {"message": "超载了"}}),
    ]
    backend = make_anthropic_backend(FakeStreamResponse(lines))
    with pytest.raises(LlmError, match="超载"):
        await _collect(backend.stream([], []))


async def test_anthropic_aclose_does_not_close_injected_session():
    backend = make_anthropic_backend(_msg_response([]))
    session = backend._session
    await backend.aclose()
    assert session.closed is False
