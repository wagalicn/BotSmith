"""
MCP 工具客户端

职责：
- 连接一个或多个 MCP 服务端，拉取工具列表
- 按 allow_tools / deny_tools 过滤，只把该 bot 需要的工具暴露给 LLM
- 把 MCP 的工具 schema 转成 OpenAI function calling 格式
- 执行工具调用，透传身份头

关于连接复用：
MCP 的 Client 是「进 async with 才连接、出来就断」的一次性对象。
原来的实现每次 list_tools / call_tool 都新建连接，一轮对话下来要握手好几次。
这里改成一轮对话开一个 McpSession，session 内按服务端懒连接并缓存，
整轮复用，退出时统一关闭。身份头是在建连时定的，所以一轮一 session
天然不会出现串用户身份的问题。

用法：
    mcp = McpToolClient(cfg.mcp.servers)
    async with mcp.session(identity="L220104") as sess:
        tools = await sess.list_tools_openai()
        result = await sess.call_tool("get_system_config", {})
"""

from __future__ import annotations

import json
import logging
from contextlib import AsyncExitStack
from typing import Any

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.types import TextContent

from botkit.config import McpServerConfig

logger = logging.getLogger("botkit.mcp")

# 建连和读取超时。读超时给得长，因为 MCP 服务端会把响应流挂住一段时间。
# 数值与 MCP SDK 自己的默认值一致。
CONNECT_TIMEOUT = 30.0
READ_TIMEOUT = 300.0


class McpError(Exception):
    """MCP 层面的错误（连不上、拿不到工具等），会让本轮消息处理失败"""


async def default_connector(
    stack: AsyncExitStack, server: McpServerConfig, headers: dict[str, str]
) -> Client:
    """
    默认建连方式：Streamable HTTP。

    身份头必须挂在自己造的 httpx2.AsyncClient 上
    —— streamable_http_client 只接受 url / http_client / terminate_on_close，
    没有 headers 参数。

    请求头 = 身份头 + 服务端配置的额外固定头（如 Authorization: Bearer xxx）。
    额外头写在后面，同名时以它为准。
    """
    merged_headers = {**headers, **(server.headers or {})}
    http_client = await stack.enter_async_context(
        httpx2.AsyncClient(
            headers=merged_headers,
            timeout=httpx2.Timeout(CONNECT_TIMEOUT, read=READ_TIMEOUT),
        )
    )
    transport = streamable_http_client(server.url, http_client=http_client)
    return await stack.enter_async_context(Client(transport))


class McpSession:
    """
    一轮对话内的 MCP 连接集合。

    进入 async with 时不建连，第一次真正用到某个服务端时才连，
    之后整轮复用同一条连接。
    """

    def __init__(
        self,
        servers: list[McpServerConfig],
        identity: str | None = None,
        connector: Any = None,
    ):
        self._servers = {s.name: s for s in servers}
        self._identity = identity
        self._connector = connector or default_connector
        self._stack: AsyncExitStack | None = None
        self._clients: dict[str, Client] = {}
        # 工具名 -> 服务端名。list_tools_openai 之后才有内容
        self._routes: dict[str, str] = {}
        self._tools_cache: list[dict[str, Any]] | None = None
        # 每个服务端建连次数，用来确认「一轮只握手一次」
        self._connect_counts: dict[str, int] = {}

    # -- 生命周期 -----------------------------------------------------------

    async def __aenter__(self) -> McpSession:
        self._stack = AsyncExitStack()
        await self._stack.__aenter__()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        stack, self._stack = self._stack, None
        self._clients.clear()
        if stack is not None:
            await stack.aclose()

    def _headers(self, server: McpServerConfig) -> dict[str, str]:
        """身份头。identity_header 配成空表示这个服务端不需要身份"""
        if server.identity_header and self._identity:
            return {server.identity_header: self._identity}
        return {}

    async def _client_for(self, server_name: str) -> Client:
        """取（必要时建立）某个服务端的连接"""
        cached = self._clients.get(server_name)
        if cached is not None:
            return cached

        if self._stack is None:
            raise McpError("McpSession 必须在 async with 块里使用")

        server = self._servers[server_name]
        try:
            client = await self._connector(
                self._stack, server, self._headers(server)
            )
        except Exception as e:
            raise McpError(
                f"连接 MCP 服务端 {server.name}（{server.url}）失败：{describe_exception(e)}"
            ) from e

        logger.info("已连接 MCP 服务端 %s（%s）", server.name, server.url)
        self._clients[server_name] = client
        self._connect_counts[server_name] = self._connect_counts.get(server_name, 0) + 1
        return client

    @property
    def connect_counts(self) -> dict[str, int]:
        """各服务端的建连次数。一轮对话里每个服务端应该只有 1 次"""
        return dict(self._connect_counts)

    # -- 工具发现 -----------------------------------------------------------

    async def _list_tools_raw(self, client: Client) -> list[Any]:
        """把分页拉完。多数服务端一页返回完，但不能假设一定如此"""
        tools: list[Any] = []
        cursor: str | None = None
        while True:
            page = await client.list_tools(cursor=cursor)
            tools.extend(page.tools)
            if page.next_cursor is None:
                return tools
            cursor = page.next_cursor

    async def list_tools_openai(self) -> list[dict[str, Any]]:
        """
        拉取所有服务端的工具，过滤后转成 OpenAI function calling 格式。
        同一 session 内只拉一次，之后直接返回缓存。
        """
        if self._tools_cache is not None:
            return self._tools_cache

        if self._stack is None:
            raise McpError("McpSession 必须在 async with 块里使用")

        result: list[dict[str, Any]] = []
        self._routes = {}
        failures: list[str] = []

        for name, server in self._servers.items():
            try:
                client = await self._client_for(name)
                raw_tools = await self._list_tools_raw(client)
            except Exception as e:
                # 多服务端时不能因为一个挂了就整轮失败，记 ERROR 后继续
                detail = describe_exception(e)
                failures.append(f"{name}: {detail}")
                logger.error("从 MCP 服务端 %s 拉取工具失败：%s", name, detail)
                continue

            kept, skipped = _filter_tools(raw_tools, server)
            if skipped:
                logger.info(
                    "服务端 %s 的 %d 个工具被白/黑名单过滤掉：%s",
                    name,
                    len(skipped),
                    "、".join(skipped),
                )

            for tool in kept:
                if tool.name in self._routes:
                    logger.warning(
                        "工具名 %s 在服务端 %s 和 %s 上重复，保留先出现的 %s。"
                        "建议用 allow_tools 把重复的那个排掉，避免调用打到意料之外的服务端。",
                        tool.name,
                        self._routes[tool.name],
                        name,
                        self._routes[tool.name],
                    )
                    continue
                self._routes[tool.name] = name
                result.append(_to_openai_tool(tool))

        if not result and failures:
            raise McpError(
                "所有 MCP 服务端都拿不到工具：\n"
                + "\n".join(f"  - {f}" for f in failures)
            )

        if failures:
            logger.warning(
                "有 %d 个 MCP 服务端不可用，本轮只能使用其余服务端的工具", len(failures)
            )

        logger.info(
            "本轮可用工具 %d 个：%s",
            len(result),
            "、".join(self._routes) or "(无)",
        )
        self._tools_cache = result
        return result

    @property
    def routes(self) -> dict[str, str]:
        """工具名 -> 服务端名的路由表（调过 list_tools_openai 之后才有内容）"""
        return dict(self._routes)

    # -- 工具调用 -----------------------------------------------------------

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> str:
        """
        调用工具，返回给 LLM 看的文本。

        工具执行失败不抛异常，而是把错误文本返回给 LLM
        —— 这是 function calling 的正常一环，LLM 看到错误可以换个参数重试
        或者告诉用户。真正抛异常的只有「连不上服务端」这种基础设施问题。
        """
        server_name = self._routes.get(tool_name)
        if server_name is None:
            # LLM 有时会编造工具名，或者被白名单挡掉了
            available = "、".join(self._routes) or "(无)"
            logger.warning("LLM 请求了不存在的工具 %s，可用工具：%s", tool_name, available)
            return (
                f"工具 {tool_name} 不存在或未对本Agent开放。"
                f"可用的工具是：{available}"
            )

        logger.info(
            "调用工具 %s@%s，参数=%s，身份=%s",
            tool_name,
            server_name,
            _safe_json(arguments),
            self._identity,
        )

        try:
            client = await self._client_for(server_name)
            resp = await client.call_tool(tool_name, arguments)
        except Exception as e:
            logger.exception("调用工具 %s 时出错", tool_name)
            return f"工具调用失败：{describe_exception(e)}"

        text = _extract_text(resp)

        if resp.is_error:
            logger.warning("工具 %s 返回错误：%s", tool_name, text[:300])
            return f"工具执行出错：{text}"

        logger.info("工具 %s 返回 %d 字", tool_name, len(text))
        return text


class McpToolClient:
    """按配置创建 MCP session 的工厂"""

    def __init__(self, servers: list[McpServerConfig], connector: Any = None):
        """
        connector 只在测试里传：默认走 Streamable HTTP，
        测试传一个用 in-memory transport 的实现，就不需要真的起 HTTP 服务。

        servers 允许为空 —— 此时这个 bot 不接任何工具，就是纯 LLM 对话。
        """
        self.servers = servers
        self._connector = connector

    def session(self, identity: str | None = None) -> McpSession:
        """开一个一轮对话用的 session。identity 会作为身份头发给各服务端"""
        return McpSession(self.servers, identity=identity, connector=self._connector)


# --------------------------------------------------------------------------
# 辅助函数
# --------------------------------------------------------------------------


def _filter_tools(
    tools: list[Any], server: McpServerConfig
) -> tuple[list[Any], list[str]]:
    """
    按白/黑名单过滤。返回 (保留的工具, 被过滤掉的工具名)。

    allow_tools 为空表示全部放开；配了就只保留列表里的。
    deny_tools 优先级更高，白名单里也会被排掉。
    """
    allow = set(server.allow_tools)
    deny = set(server.deny_tools)

    kept: list[Any] = []
    skipped: list[str] = []
    for tool in tools:
        if tool.name in deny:
            skipped.append(tool.name)
            continue
        if allow and tool.name not in allow:
            skipped.append(tool.name)
            continue
        kept.append(tool)

    # 白名单里写了但服务端没有的，通常是拼错了或者中台改了工具名
    if allow:
        actual = {t.name for t in tools}
        missing = sorted(allow - actual)
        if missing:
            logger.warning(
                "服务端 %s 的 allow_tools 里这些工具不存在：%s。"
                "请检查是否拼错，或中台是否改了工具名",
                server.name,
                "、".join(missing),
            )

    return kept, skipped


def _to_openai_tool(tool: Any) -> dict[str, Any]:
    """MCP Tool → OpenAI function calling 的 tool 定义"""
    schema = tool.input_schema or {"type": "object", "properties": {}}
    # OpenAI 要求 parameters 是 object 类型的 JSON Schema
    if "type" not in schema:
        schema = {**schema, "type": "object"}
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description or tool.title or tool.name,
            "parameters": schema,
        },
    }


def _extract_text(resp: Any) -> str:
    """
    把工具返回汇总成一段文本。

    优先取 TextContent；没有文本块时退回 structured_content
    （有些工具只返回结构化结果）。其他类型的块用占位符标注，
    免得 LLM 以为工具什么都没返回。
    """
    parts: list[str] = []
    non_text: list[str] = []

    for block in resp.content or []:
        if isinstance(block, TextContent):
            if block.text:
                parts.append(block.text)
        else:
            non_text.append(type(block).__name__)

    if parts:
        return "\n".join(parts)

    structured = getattr(resp, "structured_content", None)
    if structured is not None:
        return _safe_json(structured)

    if non_text:
        return f"（工具返回了非文本内容：{'、'.join(non_text)}）"

    return "（工具无返回内容）"


def _safe_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        return repr(value)


def describe_exception(exc: BaseException, depth: int = 0) -> str:
    """
    把异常描述成一句人能看懂的话。

    MCP SDK 内部用 anyio TaskGroup，连接失败时抛出来的是异常组，
    str() 只会得到 "unhandled errors in a TaskGroup (1 sub-exception)"
    —— 完全看不出到底是端口没开、证书不对还是 404。这里把组拆开，
    把真正的原因提上来。
    """
    if isinstance(exc, BaseExceptionGroup) and depth < 5:
        inner = [describe_exception(e, depth + 1) for e in exc.exceptions]
        if len(inner) == 1:
            return inner[0]
        return "；".join(inner)

    text = str(exc).strip()
    if not text:
        return type(exc).__name__
    # 我们自己抛的 McpError 消息里已经交代清楚了，不用再加类名
    if isinstance(exc, McpError):
        return text
    return f"{type(exc).__name__}: {text}"
