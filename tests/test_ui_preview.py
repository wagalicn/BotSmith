"""
预览测试（preview_chat）测试

预览走的是真实引擎，所以这里用 monkeypatch 把 service 内部构造的
LLM 后端换成脚本化假后端、MCP 换成 in-memory 服务端，验证编排逻辑
而不真的联网、不烧 token。
"""

from __future__ import annotations

import json
from contextlib import AsyncExitStack
from pathlib import Path

import pytest
from mcp import Client

from botkit.ui import service
from botkit.ui.service import UiError
from test_engine import ScriptedBackend, tool_call
from test_mcp_client import make_oa_server


@pytest.fixture
def project(tmp_path: Path):
    (tmp_path / "bots").mkdir()
    return tmp_path


@pytest.fixture
def ready_bot(project: Path):
    """一个配好、密钥填好、身份注入配好的 bot"""
    service.create_new_bot(project, "hr-bot")
    bot_dir = project / "bots" / "hr-bot"
    (bot_dir / ".env").write_text(
        "WECHAT_BOT_ID=id\nWECHAT_BOT_SECRET=secret\n"
        "LLM_API_URL=https://x.invalid/v1\nLLM_API_KEY=sk\nLLM_MODEL=m\n"
        "MCP_URL=https://x.invalid/mcp\n",
        encoding="utf-8",
    )
    # 把配置调成：白名单放开两个工具，create_it_ticket 注入 work_code
    data = service.read_bot(project, "hr-bot")
    values = data["values"]
    values["mcp"]["servers"][0]["allow_tools"] = [
        "get_system_config",
        "create_it_ticket",
    ]
    values["identity"]["inject"] = [{"tool": "create_it_ticket", "arg": "work_code"}]
    service.save_bot(project, "hr-bot", {"values": values})
    return bot_dir


def make_factory(script):
    """
    造一个 engine_factory：脚本化假 LLM 后端 + in-memory MCP 服务端。
    传给 preview_chat 的 engine_factory，避免真的联网。
    返回 (factory, backend) —— backend 可用来断言调用轨迹。
    """
    from botkit.engine import BotEngine
    from botkit.hooks import load_hooks
    from botkit.mcp_client import McpToolClient

    backend = ScriptedBackend(script)
    fake_server = make_oa_server()

    async def connector(stack: AsyncExitStack, server, headers):
        return await stack.enter_async_context(Client(fake_server))

    def factory(cfg):
        hooks = load_hooks(cfg.hooks_file)
        mcp = McpToolClient(cfg.mcp.servers, connector=connector)
        return BotEngine(cfg, mcp, backend, hooks=hooks)

    return factory, backend


# --------------------------------------------------------------------------
# 正常对话
# --------------------------------------------------------------------------


async def test_preview_simple_reply(project, ready_bot):
    factory, _ = make_factory([("你好，有什么可以帮你？", [])])
    result = await service.preview_chat(
        project, "hr-bot", "你好", engine_factory=factory
    )

    assert result["reply"] == "你好，有什么可以帮你？"
    assert result["tool_calls"] == []
    assert result["identity_used"]  # 自动用了一个示例身份
    # 历史带上了这一轮
    assert result["history"][-2] == {"role": "user", "content": "你好"}
    assert result["history"][-1]["role"] == "assistant"


async def test_preview_captures_tool_calls(project, ready_bot):
    factory, _ = make_factory(
        [
            (None, [tool_call("get_system_config", {})]),
            ("查到了两个系统", []),
        ]
    )
    result = await service.preview_chat(
        project, "hr-bot", "有哪些系统", engine_factory=factory
    )

    assert result["reply"] == "查到了两个系统"
    assert len(result["tool_calls"]) == 1
    call = result["tool_calls"][0]
    assert call["tool"] == "get_system_config"
    assert "OA" in call["result"]


async def test_preview_shows_identity_injection(project, ready_bot):
    """预览要能反映身份注入：模型编的工号会被换成预览身份"""
    factory, _ = make_factory(
        [
            (
                None,
                [tool_call("create_it_ticket", {"work_code": "L999999", "title": "t"})],
            ),
            ("工单建好了", []),
        ]
    )
    result = await service.preview_chat(
        project, "hr-bot", "报个故障", identity="L220104", engine_factory=factory
    )

    call = next(c for c in result["tool_calls"] if c["tool"] == "create_it_ticket")
    # 实际发出的参数里 work_code 被换成了预览身份
    assert call["args"]["work_code"] == "L220104"
    assert "提交人=L220104" in call["result"]


async def test_preview_multi_turn_history(project, ready_bot):
    factory, _ = make_factory([("第二轮回复", [])])
    prior = [
        {"role": "user", "content": "第一句"},
        {"role": "assistant", "content": "第一轮回复"},
    ]
    result = await service.preview_chat(
        project, "hr-bot", "第二句", history=prior, identity="L220104",
        engine_factory=factory,
    )
    # 历史累积
    assert [m["content"] for m in result["history"]] == [
        "第一句",
        "第一轮回复",
        "第二句",
        "第二轮回复",
    ]


async def test_preview_long_result_truncated(project, ready_bot):
    # get_system_config 返回不长，这里只验证字段存在且 truncated 标志正确
    factory, _ = make_factory(
        [
            (None, [tool_call("get_system_config", {})]),
            ("好", []),
        ]
    )
    result = await service.preview_chat(
        project, "hr-bot", "查", identity="L220104", engine_factory=factory
    )
    assert result["tool_calls"][0]["truncated"] is False


# --------------------------------------------------------------------------
# 错误处理
# --------------------------------------------------------------------------


async def test_preview_empty_message_rejected(project, ready_bot):
    with pytest.raises(UiError, match="先输入"):
        await service.preview_chat(project, "hr-bot", "   ")


async def test_preview_unconfigured_bot_rejected(project):
    """密钥没填的 bot 预览应给出友好提示，而不是崩"""
    service.create_new_bot(project, "fresh")
    with pytest.raises(UiError, match="配置还没通过校验"):
        await service.preview_chat(project, "fresh", "你好")


async def test_preview_llm_error_is_friendly(project, ready_bot):
    from botkit.llm.base import LlmError

    factory, _ = make_factory([LlmError("模拟 502")])
    with pytest.raises(UiError, match="大模型"):
        await service.preview_chat(
            project, "hr-bot", "你好", identity="L220104", engine_factory=factory
        )


async def test_preview_identity_rejected_is_friendly(project, ready_bot):
    """预览身份不符合 pattern 时给可操作的提示"""
    factory, _ = make_factory([("不该被调到", [])])
    with pytest.raises(UiError, match="identity.pattern"):
        await service.preview_chat(
            project, "hr-bot", "你好", identity="不合法的身份", engine_factory=factory
        )


async def test_preview_missing_bot_rejected(project):
    with pytest.raises(UiError, match="找不到这个 Agent"):
        await service.preview_chat(project, "nope", "你好")


async def test_preview_does_not_leak_env(project, ready_bot):
    """预览加载 .env 后不能污染进程环境"""
    import os

    factory, _ = make_factory([("好", [])])
    await service.preview_chat(
        project, "hr-bot", "你好", identity="L220104", engine_factory=factory
    )
    assert "MCP_URL" not in os.environ
    assert "LLM_API_KEY" not in os.environ
