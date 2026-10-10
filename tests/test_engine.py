"""
LLM 引擎测试

用假后端（按脚本返回预设响应）驱动引擎，MCP 侧用 in-memory 真服务端，
这样测的是引擎的编排逻辑，不需要网络也不烧 token。
"""

from __future__ import annotations

import json
import logging
from contextlib import AsyncExitStack
from pathlib import Path

import pytest
from mcp import Client
from mcp.server import MCPServer

from botkit.config import (
    BotConfig,
    IdentityConfig,
    IdentityInject,
    LlmConfig,
    McpConfig,
    McpServerConfig,
    MessagesConfig,
    SessionConfig,
    WecomConfig,
)
from botkit.engine import BotEngine, IdentityRejected, identity_matches
from botkit.hooks import HookContext, Hooks
from botkit.llm.base import LlmError, normalize_response
from botkit.mcp_client import McpToolClient
from test_mcp_client import make_oa_server


# --------------------------------------------------------------------------
# 测试脚手架
# --------------------------------------------------------------------------


class ScriptedBackend:
    """
    假 LLM 后端：按预设脚本依次返回。
    脚本每项是 (content, tool_calls_raw)，或者一个要抛出的异常。
    同时记录每次收到的 messages 和 tools，供断言用。
    """

    name = "scripted"

    def __init__(self, script):
        self._script = list(script)
        self.calls: list[tuple[list[dict], list[dict]]] = []
        self.closed = False

    async def complete(self, messages, tools):
        self.calls.append(([dict(m) for m in messages], tools))
        if not self._script:
            raise AssertionError("假后端脚本用完了，说明引擎多调了一次 LLM")
        step = self._script.pop(0)
        if isinstance(step, Exception):
            raise step
        content, tool_calls = step
        return normalize_response(content, tool_calls)

    async def aclose(self):
        self.closed = True

    @property
    def call_count(self) -> int:
        return len(self.calls)


def tool_call(name: str, args: dict, call_id: str = "c1") -> dict:
    return {
        "id": call_id,
        "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)},
    }


class InMemoryConnector:
    def __init__(self, mapping):
        self._mapping = mapping
        self.headers_seen: list[dict] = []

    async def __call__(self, stack: AsyncExitStack, server, headers):
        self.headers_seen.append(dict(headers))
        return await stack.enter_async_context(Client(self._mapping[server.name]))


def make_config(
    *,
    prompt: str = "你是 IT 助手。",
    pattern: str | None = r"^[LM]\d{6}$",
    inject: list[IdentityInject] | None = None,
    allow_tools: list[str] | None = None,
    max_tool_rounds: int = 6,
    messages: MessagesConfig | None = None,
) -> BotConfig:
    return BotConfig(
        name="test-bot",
        bot_dir=Path("."),
        wecom=WecomConfig(bot_id="id", bot_secret="secret"),
        llm=LlmConfig(
            api_url="http://fake/v1",
            api_key="k",
            model="m",
            max_tool_rounds=max_tool_rounds,
        ),
        prompt_text=prompt,
        prompt_source="inline",
        mcp=McpConfig(
            servers=[
                McpServerConfig(
                    name="oa",
                    url="http://fake/mcp",
                    allow_tools=allow_tools
                    if allow_tools is not None
                    else ["get_system_config", "create_it_ticket", "failing_tool"],
                )
            ]
        ),
        identity=IdentityConfig(
            pattern=pattern,
            inject=inject
            if inject is not None
            else [IdentityInject(tool="create_it_ticket", arg="work_code")],
        ),
        session=SessionConfig(),
        messages=messages or MessagesConfig(),
        hooks_file=None,
        log_level="INFO",
    )


def make_engine(
    backend,
    config: BotConfig | None = None,
    hooks: Hooks | None = None,
    servers: dict[str, MCPServer] | None = None,
):
    cfg = config or make_config()
    connector = InMemoryConnector(servers or {"oa": make_oa_server()})
    mcp = McpToolClient(cfg.mcp.servers, connector=connector)
    return BotEngine(cfg, mcp, backend, hooks=hooks), connector


class ProgressRecorder:
    def __init__(self):
        self.labels: list[str] = []

    async def __call__(self, label: str):
        self.labels.append(label)


# --------------------------------------------------------------------------
# 身份校验
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "identity,expected",
    [
        ("L220104", True),
        ("M999999", True),
        ("l220104", False),  # 小写不行
        ("L22010", False),  # 位数不够
        ("L2201045", False),  # 位数超了
        ("X220104", False),  # 前缀不对
        ("", False),
        (None, False),
        ("L220104 ", False),
    ],
)
def test_identity_matches(identity, expected):
    assert identity_matches(identity, r"^[LM]\d{6}$") is expected


def test_no_pattern_means_no_validation():
    assert identity_matches(None, None) is True
    assert identity_matches("", None) is True
    assert identity_matches("随便什么", None) is True


async def test_invalid_identity_never_reaches_llm_or_tools():
    """非法工号必须在碰到 LLM 和 MCP 之前就被拦住"""
    backend = ScriptedBackend([("不该被调用", [])])
    engine, connector = make_engine(backend)

    with pytest.raises(IdentityRejected) as ei:
        await engine.chat("我电脑坏了", identity="不合法的工号")

    assert backend.call_count == 0  # LLM 一次都没调
    assert connector.headers_seen == []  # MCP 一次都没连
    assert "不合法的工号" in str(ei.value)


async def test_empty_identity_rejected_when_pattern_configured():
    backend = ScriptedBackend([("x", [])])
    engine, _ = make_engine(backend)
    with pytest.raises(IdentityRejected):
        await engine.chat("你好", identity=None)
    assert backend.call_count == 0


async def test_bot_without_pattern_accepts_any_identity():
    backend = ScriptedBackend([("你好", [])])
    engine, _ = make_engine(backend, config=make_config(pattern=None))
    reply = await engine.chat("你好", identity="任意字符串")
    assert reply == "你好"


# --------------------------------------------------------------------------
# 基本对话
# --------------------------------------------------------------------------


async def test_reply_without_tool_calls():
    backend = ScriptedBackend([("你好，有什么可以帮你", [])])
    engine, _ = make_engine(backend)
    reply = await engine.chat("你好", identity="L220104")
    assert reply == "你好，有什么可以帮你"
    assert backend.call_count == 1


async def test_system_prompt_contains_prompt_file_and_identity():
    backend = ScriptedBackend([("ok", [])])
    engine, _ = make_engine(backend, config=make_config(prompt="# 角色\n你是测试助手。"))
    await engine.chat("你好", identity="L220104")

    system = backend.calls[0][0][0]
    assert system["role"] == "system"
    assert "你是测试助手" in system["content"]
    assert "L220104" in system["content"]


async def test_history_is_injected_between_system_and_user():
    backend = ScriptedBackend([("ok", [])])
    engine, _ = make_engine(backend)
    history = [
        {"role": "user", "content": "上一轮问题"},
        {"role": "assistant", "content": "上一轮回答"},
    ]
    await engine.chat("这一轮问题", identity="L220104", history=history)

    messages = backend.calls[0][0]
    assert [m["role"] for m in messages] == ["system", "user", "assistant", "user"]
    assert messages[1]["content"] == "上一轮问题"
    assert messages[3]["content"] == "这一轮问题"


async def test_tools_are_passed_to_llm():
    backend = ScriptedBackend([("ok", [])])
    engine, _ = make_engine(backend)
    await engine.chat("你好", identity="L220104")

    tools = backend.calls[0][1]
    names = {t["function"]["name"] for t in tools}
    assert "get_system_config" in names
    assert "create_it_ticket" in names


async def test_empty_reply_raises():
    """模型既不调工具也不说话，是异常情况，交给上层回 messages.error"""
    backend = ScriptedBackend([("   ", [])])
    engine, _ = make_engine(backend)
    with pytest.raises(LlmError, match="空回复"):
        await engine.chat("你好", identity="L220104")


# --------------------------------------------------------------------------
# function calling 循环
# --------------------------------------------------------------------------


async def test_tool_result_is_fed_back_to_llm():
    backend = ScriptedBackend(
        [
            (None, [tool_call("get_system_config", {})]),
            ("查到了：OA 和 ERP", []),
        ]
    )
    engine, _ = make_engine(backend)
    reply = await engine.chat("有哪些系统", identity="L220104")

    assert reply == "查到了：OA 和 ERP"
    assert backend.call_count == 2

    # 第二次请求里应该有 assistant(tool_calls) + tool(结果)
    second = backend.calls[1][0]
    assert second[-2]["role"] == "assistant"
    assert second[-2]["tool_calls"][0]["function"]["name"] == "get_system_config"
    assert second[-1]["role"] == "tool"
    assert second[-1]["tool_call_id"] == "c1"
    assert "OA" in second[-1]["content"]  # 真实工具返回的内容


async def test_multi_round_tool_calls():
    backend = ScriptedBackend(
        [
            (None, [tool_call("get_system_config", {}, "c1")]),
            (
                None,
                [
                    tool_call(
                        "create_it_ticket",
                        {"work_code": "L220104", "title": "网络故障"},
                        "c2",
                    )
                ],
            ),
            ("工单已创建，单号 T001", []),
        ]
    )
    engine, _ = make_engine(backend)
    reply = await engine.chat("我电脑连不上网", identity="L220104")

    assert "T001" in reply
    assert backend.call_count == 3


async def test_multiple_tool_calls_in_one_round():
    backend = ScriptedBackend(
        [
            (
                None,
                [
                    tool_call("get_system_config", {}, "c1"),
                    tool_call("get_system_config", {}, "c2"),
                ],
            ),
            ("都查完了", []),
        ]
    )
    engine, _ = make_engine(backend)
    reply = await engine.chat("查两次", identity="L220104")

    assert reply == "都查完了"
    second = backend.calls[1][0]
    tool_msgs = [m for m in second if m["role"] == "tool"]
    assert [m["tool_call_id"] for m in tool_msgs] == ["c1", "c2"]


async def test_tool_error_is_fed_back_not_raised():
    """工具失败要把错误交给 LLM，让它能告诉用户或换个方式重试"""
    backend = ScriptedBackend(
        [
            (None, [tool_call("failing_tool", {})]),
            ("这个功能暂时不可用，我已经记录", []),
        ]
    )
    engine, _ = make_engine(backend)
    reply = await engine.chat("试试", identity="L220104")

    assert reply == "这个功能暂时不可用，我已经记录"
    tool_msg = backend.calls[1][0][-1]
    assert "工具执行出错" in tool_msg["content"]


async def test_max_tool_rounds_returns_configured_message():
    msgs = MessagesConfig(tool_rounds_exceeded="轮次太多了，换个说法吧")
    # 每轮都要求调工具，永远不给文本回复
    backend = ScriptedBackend(
        [(None, [tool_call("get_system_config", {})]) for _ in range(3)]
    )
    engine, _ = make_engine(
        backend, config=make_config(max_tool_rounds=3, messages=msgs)
    )
    reply = await engine.chat("死循环", identity="L220104")

    assert reply == "轮次太多了，换个说法吧"
    assert backend.call_count == 3  # 严格按上限，不多调


async def test_llm_error_propagates():
    """后端挂了让异常上抛，由 runtime 决定回什么（走 messages.error）"""
    backend = ScriptedBackend([LlmError("模拟 LLM 502")])
    engine, _ = make_engine(backend)
    with pytest.raises(LlmError, match="502"):
        await engine.chat("你好", identity="L220104")


async def test_llm_error_in_second_round_propagates():
    backend = ScriptedBackend(
        [
            (None, [tool_call("get_system_config", {})]),
            LlmError("第二轮挂了"),
        ]
    )
    engine, _ = make_engine(backend)
    with pytest.raises(LlmError, match="第二轮挂了"):
        await engine.chat("你好", identity="L220104")


# --------------------------------------------------------------------------
# 身份强制注入（安全边界）
# --------------------------------------------------------------------------


async def test_identity_is_injected_into_tool_args():
    backend = ScriptedBackend(
        [
            (None, [tool_call("create_it_ticket", {"title": "网络故障"})]),
            ("好了", []),
        ]
    )
    engine, _ = make_engine(backend)
    await engine.chat("报个故障", identity="L220104")

    # 真实工具会把 work_code 回显出来
    tool_msg = backend.calls[1][0][-1]
    assert "提交人=L220104" in tool_msg["content"]


async def test_llm_forged_identity_is_overwritten(caplog):
    """LLM 编造别人的工号时必须被覆盖成可信身份，并留下日志"""
    backend = ScriptedBackend(
        [
            (
                None,
                [
                    tool_call(
                        "create_it_ticket",
                        {"work_code": "L999999", "title": "冒名工单"},
                    )
                ],
            ),
            ("好了", []),
        ]
    )
    engine, _ = make_engine(backend)
    with caplog.at_level(logging.WARNING, logger="botkit.engine"):
        await engine.chat("帮 L999999 报个故障", identity="L220104")

    tool_msg = backend.calls[1][0][-1]
    assert "提交人=L220104" in tool_msg["content"]
    assert "L999999" not in tool_msg["content"]
    assert any(
        "L999999" in r.getMessage() and "覆盖" in r.getMessage()
        for r in caplog.records
    )


async def test_forged_identity_is_also_corrected_in_conversation_history():
    """
    回灌给 LLM 的 assistant 消息要反映实际发出的参数。
    否则模型在自己的历史里看到伪造的工号，可能照着它回答用户。
    """
    backend = ScriptedBackend(
        [
            (
                None,
                [tool_call("create_it_ticket", {"work_code": "L999999", "title": "t"})],
            ),
            ("好了", []),
        ]
    )
    engine, _ = make_engine(backend)
    await engine.chat("报故障", identity="L220104")

    assistant_msg = backend.calls[1][0][-2]
    args = json.loads(assistant_msg["tool_calls"][0]["function"]["arguments"])
    assert args["work_code"] == "L220104"
    assert args["title"] == "t"  # 其他参数不动


async def test_hook_rewritten_args_also_synced_to_history():
    def before_tool_call(tool_name, args, ctx):
        args["title"] = "hook 改的标题"
        return args

    backend = ScriptedBackend(
        [
            (None, [tool_call("create_it_ticket", {"title": "原标题"})]),
            ("好了", []),
        ]
    )
    engine, _ = make_engine(
        backend, hooks=Hooks({"before_tool_call": before_tool_call})
    )
    await engine.chat("报故障", identity="L220104")

    assistant_msg = backend.calls[1][0][-2]
    args = json.loads(assistant_msg["tool_calls"][0]["function"]["arguments"])
    assert args["title"] == "hook 改的标题"


async def test_unchanged_args_leave_history_untouched():
    """参数没被改过时不动原始记录，保持模型输出原样"""
    raw = tool_call("get_system_config", {})
    backend = ScriptedBackend([(None, [raw]), ("好了", [])])
    engine, _ = make_engine(backend)
    await engine.chat("查配置", identity="L220104")

    assistant_msg = backend.calls[1][0][-2]
    assert assistant_msg["tool_calls"][0]["function"]["arguments"] == "{}"


async def test_multiple_tool_calls_sync_to_correct_index():
    """一轮里多个工具调用，参数要同步到各自对应的那一条"""
    backend = ScriptedBackend(
        [
            (
                None,
                [
                    tool_call("get_system_config", {}, "c1"),
                    tool_call(
                        "create_it_ticket", {"work_code": "L999999", "title": "t"}, "c2"
                    ),
                ],
            ),
            ("好了", []),
        ]
    )
    engine, _ = make_engine(backend)
    await engine.chat("报故障", identity="L220104")

    assistant_msg = next(
        m for m in backend.calls[1][0] if m.get("role") == "assistant" and m.get("tool_calls")
    )
    calls = assistant_msg["tool_calls"]
    assert calls[0]["function"]["arguments"] == "{}"  # 第一个没动
    assert json.loads(calls[1]["function"]["arguments"])["work_code"] == "L220104"


async def test_injection_only_applies_to_configured_tool():
    """没配 inject 的工具不该被塞参数"""
    backend = ScriptedBackend(
        [
            (None, [tool_call("get_system_config", {})]),
            ("好了", []),
        ]
    )
    engine, _ = make_engine(backend)
    await engine.chat("查配置", identity="L220104")

    assistant_msg = backend.calls[1][0][-2]
    args = json.loads(assistant_msg["tool_calls"][0]["function"]["arguments"])
    assert "work_code" not in args


async def test_no_inject_config_means_no_injection():
    backend = ScriptedBackend(
        [
            (None, [tool_call("create_it_ticket", {"work_code": "L999999", "title": "t"})]),
            ("好了", []),
        ]
    )
    engine, _ = make_engine(backend, config=make_config(inject=[]))
    await engine.chat("报故障", identity="L220104")

    tool_msg = backend.calls[1][0][-1]
    # 没配注入时，LLM 给什么就用什么
    assert "提交人=L999999" in tool_msg["content"]


# --------------------------------------------------------------------------
# 进度回调
# --------------------------------------------------------------------------


async def test_progress_uses_configured_label_per_tool():
    msgs = MessagesConfig(
        tool_progress={
            "get_system_config": "正在匹配系统配置...",
            "create_it_ticket": "正在创建工单...",
            "default": "正在调用工具...",
        }
    )
    backend = ScriptedBackend(
        [
            (None, [tool_call("get_system_config", {}, "c1")]),
            (None, [tool_call("create_it_ticket", {"title": "t"}, "c2")]),
            ("好了", []),
        ]
    )
    engine, _ = make_engine(backend, config=make_config(messages=msgs))
    progress = ProgressRecorder()
    await engine.chat("报故障", identity="L220104", on_progress=progress)

    assert progress.labels == ["正在匹配系统配置...", "正在创建工单..."]


async def test_progress_falls_back_to_default():
    msgs = MessagesConfig(tool_progress={"default": "处理中..."})
    backend = ScriptedBackend(
        [
            (None, [tool_call("get_system_config", {})]),
            ("好了", []),
        ]
    )
    engine, _ = make_engine(backend, config=make_config(messages=msgs))
    progress = ProgressRecorder()
    await engine.chat("查配置", identity="L220104", on_progress=progress)

    assert progress.labels == ["处理中..."]


async def test_no_progress_callback_is_fine():
    backend = ScriptedBackend(
        [
            (None, [tool_call("get_system_config", {})]),
            ("好了", []),
        ]
    )
    engine, _ = make_engine(backend)
    assert await engine.chat("查配置", identity="L220104") == "好了"


# --------------------------------------------------------------------------
# MCP 身份透传与连接复用
# --------------------------------------------------------------------------


async def test_identity_header_sent_to_mcp():
    backend = ScriptedBackend([("ok", [])])
    engine, connector = make_engine(backend)
    await engine.chat("你好", identity="L220104")
    assert connector.headers_seen == [{"X-MCP-Identity": "L220104"}]


async def test_one_handshake_per_turn_across_multiple_tool_calls():
    backend = ScriptedBackend(
        [
            (None, [tool_call("get_system_config", {}, "c1")]),
            (None, [tool_call("get_system_config", {}, "c2")]),
            ("好了", []),
        ]
    )
    engine, connector = make_engine(backend)
    await engine.chat("查两次", identity="L220104")
    assert len(connector.headers_seen) == 1


# --------------------------------------------------------------------------
# hook 挂点
# --------------------------------------------------------------------------


async def test_before_tool_call_hook_can_rewrite_args():
    seen = {}

    def before_tool_call(tool_name, args, ctx):
        seen["tool"] = tool_name
        seen["identity"] = ctx.identity
        args["title"] = "被 hook 改过的标题"
        return args

    backend = ScriptedBackend(
        [
            (None, [tool_call("create_it_ticket", {"title": "原标题"})]),
            ("好了", []),
        ]
    )
    engine, _ = make_engine(
        backend, hooks=Hooks({"before_tool_call": before_tool_call})
    )
    await engine.chat("报故障", identity="L220104")

    assert seen == {"tool": "create_it_ticket", "identity": "L220104"}
    tool_msg = backend.calls[1][0][-1]
    assert "被 hook 改过的标题" in tool_msg["content"]


async def test_before_tool_call_runs_after_identity_inject():
    """hook 能看到已经注入的身份，也能覆盖它（hook 是最后一道）"""
    captured = {}

    def before_tool_call(tool_name, args, ctx):
        captured["work_code_seen"] = args.get("work_code")
        return args

    backend = ScriptedBackend(
        [
            (None, [tool_call("create_it_ticket", {"title": "t"})]),
            ("好了", []),
        ]
    )
    engine, _ = make_engine(
        backend, hooks=Hooks({"before_tool_call": before_tool_call})
    )
    await engine.chat("报故障", identity="L220104")

    assert captured["work_code_seen"] == "L220104"


async def test_after_tool_call_hook_can_rewrite_result():
    def after_tool_call(tool_name, result, ctx):
        return "结果被 hook 替换了"

    backend = ScriptedBackend(
        [
            (None, [tool_call("get_system_config", {})]),
            ("好了", []),
        ]
    )
    engine, _ = make_engine(backend, hooks=Hooks({"after_tool_call": after_tool_call}))
    await engine.chat("查配置", identity="L220104")

    tool_msg = backend.calls[1][0][-1]
    assert tool_msg["content"] == "结果被 hook 替换了"


async def test_failing_hook_degrades_to_default():
    def before_tool_call(tool_name, args, ctx):
        raise RuntimeError("hook 写崩了")

    backend = ScriptedBackend(
        [
            (None, [tool_call("create_it_ticket", {"title": "原标题"})]),
            ("好了", []),
        ]
    )
    engine, _ = make_engine(
        backend, hooks=Hooks({"before_tool_call": before_tool_call})
    )
    reply = await engine.chat("报故障", identity="L220104")

    # hook 崩了不影响主流程，参数用未改写的原值
    assert reply == "好了"
    tool_msg = backend.calls[1][0][-1]
    assert "原标题" in tool_msg["content"]


async def test_hook_context_is_passed_through():
    seen = {}

    def before_tool_call(tool_name, args, ctx):
        seen["session_key"] = ctx.session_key
        seen["bot_name"] = ctx.config.name
        return args

    backend = ScriptedBackend(
        [
            (None, [tool_call("get_system_config", {})]),
            ("好了", []),
        ]
    )
    engine, _ = make_engine(
        backend, hooks=Hooks({"before_tool_call": before_tool_call})
    )
    ctx = HookContext(identity="L220104", session_key="group:c1:L220104")
    ctx.config = engine.config
    await engine.chat("查配置", identity="L220104", hook_context=ctx)

    assert seen == {"session_key": "group:c1:L220104", "bot_name": "test-bot"}


# --------------------------------------------------------------------------
# 收尾
# --------------------------------------------------------------------------


async def test_aclose_closes_backend():
    backend = ScriptedBackend([])
    engine, _ = make_engine(backend)
    await engine.aclose()
    assert backend.closed
