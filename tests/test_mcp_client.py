"""
MCP 客户端测试

用 MCP SDK 的 in-memory transport（Client(MCPServer 实例)）起假服务端，
走的是真实协议层（工具会被真的列出、校验、调用），但不需要网络和端口。
"""

from __future__ import annotations

import logging
from contextlib import AsyncExitStack

import pytest
from mcp import Client
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from botkit.config import McpServerConfig
from botkit.mcp_client import (
    McpError,
    McpToolClient,
    _extract_text,
    _to_openai_tool,
    describe_exception,
)


# --------------------------------------------------------------------------
# 假 MCP 服务端
# --------------------------------------------------------------------------


def make_oa_server() -> MCPServer:
    """模拟中台：两个 IT 工单工具 + 几个不相关的工具"""
    mcp = MCPServer("oa-fake")

    @mcp.tool()
    def get_system_config() -> str:
        """获取应用系统、系统模块、需求选项配置"""
        return "系统1=OA(模块: 登录/审批), 系统2=ERP(模块: 采购)"

    @mcp.tool()
    def create_it_ticket(
        work_code: str, title: str, detail: str = "", urgency: int = 3
    ) -> str:
        """创建 IT 工单"""
        return f"工单已创建 单号=T001 提交人={work_code} 标题={title} 紧急度={urgency}"

    @mcp.tool()
    def get_weather(city: str) -> str:
        """查天气，和 IT 工单无关，应该被白名单挡掉"""
        return f"{city} 晴"

    @mcp.tool()
    def delete_everything() -> str:
        """危险工具，应该被黑名单挡掉"""
        return "boom"

    @mcp.tool()
    def failing_tool() -> str:
        """总是失败的工具，用来测 is_error"""
        raise ToolError("这个工具坏了")

    return mcp


def make_crm_server() -> MCPServer:
    """第二个服务端，用来测多 server 路由和工具名冲突"""
    mcp = MCPServer("crm-fake")

    @mcp.tool()
    def get_customer(name: str) -> str:
        """查客户信息"""
        return f"客户 {name} 状态正常"

    @mcp.tool()
    def get_system_config() -> str:
        """和 oa 服务端重名的工具，用来测冲突处理"""
        return "CRM 的配置（不该被调到）"

    return mcp


class RecordingConnector:
    """
    测试用 connector：把 McpServerConfig.name 映射到一个 in-memory MCPServer。
    同时记录每次建连收到的 headers，用来验证身份头。
    """

    def __init__(self, servers_by_name: dict[str, MCPServer], fail: set[str] | None = None):
        self._servers = servers_by_name
        self._fail = fail or set()
        self.headers_seen: list[tuple[str, dict[str, str]]] = []
        self.connect_calls: list[str] = []

    async def __call__(
        self, stack: AsyncExitStack, server: McpServerConfig, headers: dict[str, str]
    ) -> Client:
        self.connect_calls.append(server.name)
        self.headers_seen.append((server.name, dict(headers)))
        if server.name in self._fail:
            raise ConnectionError(f"模拟 {server.name} 连不上")
        return await stack.enter_async_context(Client(self._servers[server.name]))


@pytest.fixture
def oa_only():
    """单服务端，白名单只放两个 IT 工单工具"""
    server_cfg = McpServerConfig(
        name="oa",
        url="http://fake/mcp",
        identity_header="X-MCP-Identity",
        allow_tools=["get_system_config", "create_it_ticket"],
    )
    connector = RecordingConnector({"oa": make_oa_server()})
    return McpToolClient([server_cfg], connector=connector), connector


# --------------------------------------------------------------------------
# 工具发现与白名单
# --------------------------------------------------------------------------


async def test_allow_tools_filters_to_whitelist(oa_only):
    client, _ = oa_only
    async with client.session(identity="L220104") as sess:
        tools = await sess.list_tools_openai()

    names = {t["function"]["name"] for t in tools}
    assert names == {"get_system_config", "create_it_ticket"}
    # 白名单外的工具不能出现
    assert "get_weather" not in names
    assert "delete_everything" not in names


async def test_empty_allow_tools_exposes_everything():
    cfg = McpServerConfig(name="oa", url="http://fake/mcp", allow_tools=[])
    connector = RecordingConnector({"oa": make_oa_server()})
    async with McpToolClient([cfg], connector=connector).session() as sess:
        tools = await sess.list_tools_openai()

    names = {t["function"]["name"] for t in tools}
    assert names == {
        "get_system_config",
        "create_it_ticket",
        "get_weather",
        "delete_everything",
        "failing_tool",
    }


async def test_deny_tools_wins_over_allow():
    cfg = McpServerConfig(
        name="oa",
        url="http://fake/mcp",
        allow_tools=["get_system_config", "delete_everything"],
        deny_tools=["delete_everything"],
    )
    connector = RecordingConnector({"oa": make_oa_server()})
    async with McpToolClient([cfg], connector=connector).session() as sess:
        tools = await sess.list_tools_openai()

    names = {t["function"]["name"] for t in tools}
    assert names == {"get_system_config"}


async def test_deny_tools_alone():
    cfg = McpServerConfig(
        name="oa", url="http://fake/mcp", deny_tools=["get_weather", "failing_tool"]
    )
    connector = RecordingConnector({"oa": make_oa_server()})
    async with McpToolClient([cfg], connector=connector).session() as sess:
        tools = await sess.list_tools_openai()

    names = {t["function"]["name"] for t in tools}
    assert names == {"get_system_config", "create_it_ticket", "delete_everything"}


async def test_typo_in_allow_tools_logs_warning(caplog):
    """白名单里写了服务端不存在的工具名，要给出提示（常见于拼错或中台改名）"""
    cfg = McpServerConfig(
        name="oa", url="http://fake/mcp", allow_tools=["get_system_confg"]
    )
    connector = RecordingConnector({"oa": make_oa_server()})
    with caplog.at_level(logging.WARNING, logger="botkit.mcp"):
        async with McpToolClient([cfg], connector=connector).session() as sess:
            tools = await sess.list_tools_openai()

    assert tools == []
    assert any("get_system_confg" in r.getMessage() for r in caplog.records)


# --------------------------------------------------------------------------
# OpenAI schema 转换
# --------------------------------------------------------------------------


async def test_openai_tool_shape(oa_only):
    client, _ = oa_only
    async with client.session() as sess:
        tools = await sess.list_tools_openai()

    by_name = {t["function"]["name"]: t for t in tools}
    ticket = by_name["create_it_ticket"]

    assert ticket["type"] == "function"
    fn = ticket["function"]
    assert fn["description"]  # 描述必须有，LLM 靠它选工具
    params = fn["parameters"]
    assert params["type"] == "object"
    assert "work_code" in params["properties"]
    assert "title" in params["properties"]
    # 有默认值的参数不应该是必填
    assert "work_code" in params["required"]
    assert "urgency" not in params.get("required", [])


def test_to_openai_tool_falls_back_for_missing_description():
    class FakeTool:
        name = "t1"
        title = "标题"
        description = None
        input_schema = {"type": "object", "properties": {}}

    out = _to_openai_tool(FakeTool())
    assert out["function"]["description"] == "标题"


def test_to_openai_tool_adds_object_type_when_missing():
    class FakeTool:
        name = "t1"
        title = None
        description = "d"
        input_schema = {"properties": {}}

    out = _to_openai_tool(FakeTool())
    assert out["function"]["parameters"]["type"] == "object"


def test_to_openai_tool_handles_null_schema():
    class FakeTool:
        name = "t1"
        title = None
        description = "d"
        input_schema = None

    out = _to_openai_tool(FakeTool())
    assert out["function"]["parameters"] == {"type": "object", "properties": {}}


# --------------------------------------------------------------------------
# 工具调用
# --------------------------------------------------------------------------


async def test_call_tool_returns_text(oa_only):
    client, _ = oa_only
    async with client.session(identity="L220104") as sess:
        await sess.list_tools_openai()
        result = await sess.call_tool("get_system_config", {})

    assert "OA" in result
    assert "ERP" in result


async def test_call_tool_passes_arguments(oa_only):
    client, _ = oa_only
    async with client.session(identity="L220104") as sess:
        await sess.list_tools_openai()
        result = await sess.call_tool(
            "create_it_ticket",
            {"work_code": "L220104", "title": "电脑连不上网", "urgency": 0},
        )

    assert "T001" in result
    assert "L220104" in result
    assert "电脑连不上网" in result
    assert "紧急度=0" in result


async def test_tool_error_is_returned_not_raised():
    """
    v2 里工具失败是 is_error=True 的正常返回，不抛异常。
    要把错误文本交给 LLM，让它能重试或者告诉用户，而不是让整条消息挂掉。
    """
    cfg = McpServerConfig(name="oa", url="http://fake/mcp", allow_tools=["failing_tool"])
    connector = RecordingConnector({"oa": make_oa_server()})
    async with McpToolClient([cfg], connector=connector).session() as sess:
        await sess.list_tools_openai()
        result = await sess.call_tool("failing_tool", {})

    assert "工具执行出错" in result
    assert "这个工具坏了" in result


async def test_unknown_tool_returns_hint_with_available_tools(oa_only):
    """LLM 编造工具名时，要告诉它有哪些可用工具，而不是直接崩"""
    client, _ = oa_only
    async with client.session() as sess:
        await sess.list_tools_openai()
        result = await sess.call_tool("make_coffee", {})

    assert "make_coffee" in result
    assert "不存在" in result
    assert "get_system_config" in result  # 列出可用工具


async def test_whitelisted_out_tool_is_not_callable(oa_only):
    """被白名单挡掉的工具，即使服务端有，也不能被调到"""
    client, _ = oa_only
    async with client.session() as sess:
        await sess.list_tools_openai()
        result = await sess.call_tool("delete_everything", {})

    assert "不存在或未对本Agent开放" in result


# --------------------------------------------------------------------------
# 身份头
# --------------------------------------------------------------------------


async def test_identity_header_is_sent(oa_only):
    client, connector = oa_only
    async with client.session(identity="L220104") as sess:
        await sess.list_tools_openai()

    assert connector.headers_seen == [("oa", {"X-MCP-Identity": "L220104"})]


async def test_no_identity_header_when_not_configured():
    cfg = McpServerConfig(name="oa", url="http://fake/mcp", identity_header=None)
    connector = RecordingConnector({"oa": make_oa_server()})
    async with McpToolClient([cfg], connector=connector).session(identity="L220104") as sess:
        await sess.list_tools_openai()

    assert connector.headers_seen == [("oa", {})]


async def test_no_identity_header_when_identity_missing(oa_only):
    client, connector = oa_only
    async with client.session(identity=None) as sess:
        await sess.list_tools_openai()

    assert connector.headers_seen == [("oa", {})]


# --------------------------------------------------------------------------
# 连接复用
# --------------------------------------------------------------------------


async def test_connection_reused_within_one_turn(oa_only):
    """一轮对话里多次调用工具，只应该握手一次"""
    client, connector = oa_only
    async with client.session(identity="L220104") as sess:
        await sess.list_tools_openai()
        await sess.call_tool("get_system_config", {})
        await sess.call_tool("get_system_config", {})
        await sess.call_tool(
            "create_it_ticket", {"work_code": "L220104", "title": "t"}
        )

    assert connector.connect_calls == ["oa"]
    assert sess.connect_counts == {"oa": 1}


async def test_new_session_reconnects(oa_only):
    """不同轮对话之间是独立连接（身份头可能不同，不能复用）"""
    client, connector = oa_only
    async with client.session(identity="L220104") as sess:
        await sess.list_tools_openai()
    async with client.session(identity="L220105") as sess:
        await sess.list_tools_openai()

    assert connector.connect_calls == ["oa", "oa"]
    assert connector.headers_seen == [
        ("oa", {"X-MCP-Identity": "L220104"}),
        ("oa", {"X-MCP-Identity": "L220105"}),
    ]


async def test_tools_cached_within_session(oa_only):
    client, connector = oa_only
    async with client.session() as sess:
        first = await sess.list_tools_openai()
        second = await sess.list_tools_openai()

    assert first is second
    assert connector.connect_calls == ["oa"]


async def test_session_must_be_used_in_context_manager(oa_only):
    client, _ = oa_only
    sess = client.session()
    with pytest.raises(McpError, match="async with"):
        await sess.list_tools_openai()


# --------------------------------------------------------------------------
# 多服务端
# --------------------------------------------------------------------------


async def test_tools_from_multiple_servers_are_merged():
    servers = [
        McpServerConfig(
            name="oa", url="http://oa/mcp", allow_tools=["get_system_config"]
        ),
        McpServerConfig(name="crm", url="http://crm/mcp", allow_tools=["get_customer"]),
    ]
    connector = RecordingConnector({"oa": make_oa_server(), "crm": make_crm_server()})
    async with McpToolClient(servers, connector=connector).session() as sess:
        tools = await sess.list_tools_openai()

    names = {t["function"]["name"] for t in tools}
    assert names == {"get_system_config", "get_customer"}
    assert sess.routes == {"get_system_config": "oa", "get_customer": "crm"}


async def test_call_is_routed_to_the_right_server():
    servers = [
        McpServerConfig(
            name="oa", url="http://oa/mcp", allow_tools=["get_system_config"]
        ),
        McpServerConfig(name="crm", url="http://crm/mcp", allow_tools=["get_customer"]),
    ]
    connector = RecordingConnector({"oa": make_oa_server(), "crm": make_crm_server()})
    async with McpToolClient(servers, connector=connector).session() as sess:
        await sess.list_tools_openai()
        oa_result = await sess.call_tool("get_system_config", {})
        crm_result = await sess.call_tool("get_customer", {"name": "张三"})

    assert "OA" in oa_result
    assert "张三" in crm_result


async def test_duplicate_tool_name_keeps_first_and_warns(caplog):
    servers = [
        McpServerConfig(name="oa", url="http://oa/mcp"),
        McpServerConfig(name="crm", url="http://crm/mcp"),
    ]
    connector = RecordingConnector({"oa": make_oa_server(), "crm": make_crm_server()})
    with caplog.at_level(logging.WARNING, logger="botkit.mcp"):
        async with McpToolClient(servers, connector=connector).session() as sess:
            tools = await sess.list_tools_openai()
            result = await sess.call_tool("get_system_config", {})

    # 只保留一份定义
    dupes = [t for t in tools if t["function"]["name"] == "get_system_config"]
    assert len(dupes) == 1
    # 保留先出现的那个服务端
    assert sess.routes["get_system_config"] == "oa"
    assert "OA" in result
    assert "不该被调到" not in result
    assert any(
        "get_system_config" in r.getMessage() and "重复" in r.getMessage()
        for r in caplog.records
    )


async def test_one_server_down_degrades_gracefully(caplog):
    """多服务端时一个挂了不该让整轮失败，剩下的工具还能用"""
    servers = [
        McpServerConfig(name="oa", url="http://oa/mcp", allow_tools=["get_system_config"]),
        McpServerConfig(name="crm", url="http://crm/mcp", allow_tools=["get_customer"]),
    ]
    connector = RecordingConnector(
        {"oa": make_oa_server(), "crm": make_crm_server()}, fail={"crm"}
    )
    with caplog.at_level(logging.ERROR, logger="botkit.mcp"):
        async with McpToolClient(servers, connector=connector).session() as sess:
            tools = await sess.list_tools_openai()

    names = {t["function"]["name"] for t in tools}
    assert names == {"get_system_config"}
    assert any("crm" in r.getMessage() for r in caplog.records)


async def test_all_servers_down_raises():
    """单服务端连不上（或全部连不上）是基础设施问题，应该让本轮失败"""
    servers = [McpServerConfig(name="oa", url="http://oa/mcp")]
    connector = RecordingConnector({"oa": make_oa_server()}, fail={"oa"})
    with pytest.raises(McpError, match="所有 MCP 服务端都拿不到工具"):
        async with McpToolClient(servers, connector=connector).session() as sess:
            await sess.list_tools_openai()


async def test_no_servers_configured_yields_no_tools():
    """不配任何服务端 = 纯 LLM 对话：能正常开 session，工具列表为空"""
    async with McpToolClient([]).session() as sess:
        tools = await sess.list_tools_openai()
    assert tools == []


# --------------------------------------------------------------------------
# 返回内容提取
# --------------------------------------------------------------------------


def test_extract_text_joins_multiple_blocks():
    from mcp.types import TextContent

    class Resp:
        content = [
            TextContent(type="text", text="第一段"),
            TextContent(type="text", text="第二段"),
        ]
        structured_content = None

    assert _extract_text(Resp()) == "第一段\n第二段"


def test_extract_text_falls_back_to_structured_content():
    class Resp:
        content = []
        structured_content = {"ticket": "T001", "名称": "工单"}

    out = _extract_text(Resp())
    assert "T001" in out
    assert "工单" in out  # 中文不能被转成 \uXXXX


def test_extract_text_marks_non_text_blocks():
    class ImageBlock:
        pass

    class Resp:
        content = [ImageBlock()]
        structured_content = None

    assert "非文本内容" in _extract_text(Resp())


def test_extract_text_empty():
    class Resp:
        content = []
        structured_content = None

    assert _extract_text(Resp()) == "（工具无返回内容）"


# --------------------------------------------------------------------------
# 异常描述
# --------------------------------------------------------------------------


def test_describe_plain_exception():
    assert describe_exception(ConnectionError("连不上")) == "ConnectionError: 连不上"


def test_describe_exception_without_message():
    assert describe_exception(ValueError()) == "ValueError"


def test_describe_unwraps_single_exception_group():
    """
    MCP SDK 内部用 anyio TaskGroup，连接失败抛的是异常组，
    str() 只有 "unhandled errors in a TaskGroup"，看不出真正原因。
    """
    eg = BaseExceptionGroup(
        "unhandled errors in a TaskGroup", [ConnectionError("All connection attempts failed")]
    )
    out = describe_exception(eg)
    assert out == "ConnectionError: All connection attempts failed"
    assert "TaskGroup" not in out


def test_describe_joins_multiple_subexceptions():
    eg = BaseExceptionGroup("组", [ValueError("错误一"), KeyError("错误二")])
    out = describe_exception(eg)
    assert "错误一" in out
    assert "错误二" in out


def test_describe_unwraps_nested_groups():
    inner = BaseExceptionGroup("内", [TimeoutError("超时了")])
    outer = BaseExceptionGroup("外", [inner])
    assert describe_exception(outer) == "TimeoutError: 超时了"


def test_describe_omits_redundant_mcp_error_prefix():
    """我们自己抛的 McpError 消息已经自解释，不用再加类名"""
    out = describe_exception(McpError("连接 MCP 服务端 oa 失败：端口没开"))
    assert out == "连接 MCP 服务端 oa 失败：端口没开"


async def test_connection_failure_message_is_actionable():
    """连不上时的报错必须能定位到原因，而不是一句 TaskGroup"""

    class FailingConnector:
        async def __call__(self, stack, server, headers):
            raise BaseExceptionGroup(
                "unhandled errors in a TaskGroup",
                [ConnectionError("All connection attempts failed")],
            )

    servers = [McpServerConfig(name="oa", url="http://127.0.0.1:9/mcp")]
    with pytest.raises(McpError) as ei:
        async with McpToolClient(servers, connector=FailingConnector()).session() as sess:
            await sess.list_tools_openai()

    msg = str(ei.value)
    assert "All connection attempts failed" in msg
    assert "http://127.0.0.1:9/mcp" in msg  # 报出是哪个地址
    assert "TaskGroup" not in msg
