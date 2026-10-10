"""
运行时：把配置组装成一个跑起来的Agent

分两层：
- MessagePipeline  一条消息进来该怎么处理，纯逻辑，不碰企微 SDK，可单测
- run_bot          把 pipeline 接到企微长连接上，处理事件注册和流式回复

这样拆是因为消息处理的分支不少（身份校验、空消息、重置词、hook 拦截、
LLM、异常降级），全塞在 SDK 回调里没法测。
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from botkit.config import BotConfig
from botkit.engine import BotEngine, IdentityRejected, identity_matches
from botkit.hooks import HookContext, Hooks, load_hooks
from botkit.llm import create_backend
from botkit.logging_setup import setup_logging
from botkit.mcp_client import McpToolClient
from botkit.session import SessionStore

logger = logging.getLogger("botkit.runtime")

ProgressCallback = Callable[[str], Awaitable[None]]
# 纯观测回调：每次工具调用后拿到 (工具名, 实际参数, 结果)。仅预览测试用。
ToolCallObserver = Callable[[str, "dict[str, Any]", str], Awaitable[None]]

# 消息开头的 @Agent。只去掉开头连续的 @xxx，不动正文里的 @
# —— 正文里可能有邮箱（zhang@example.com）或 @同事，全局删会把内容吃掉。
_LEADING_MENTION = re.compile(r"^\s*(?:@[^\s@]+\s*)+")


def strip_mention(text: str) -> str:
    """去掉消息开头的 @Agent"""
    return _LEADING_MENTION.sub("", text or "").strip()


# 回复的来源，方便测试和日志区分走了哪条分支
KIND_IDENTITY_INVALID = "identity_invalid"
KIND_EMPTY = "empty_input"
KIND_RESET = "reset"
KIND_INTERCEPT = "intercept"
KIND_HOOK = "hook"
KIND_LLM = "llm"
KIND_ERROR = "error"


@dataclass
class Reply:
    """一次消息处理的产物"""

    text: str
    kind: str


class MessagePipeline:
    """一条文本消息的处理流程"""

    def __init__(
        self,
        config: BotConfig,
        engine: BotEngine,
        sessions: SessionStore,
        hooks: Hooks | None = None,
    ):
        self.config = config
        self.engine = engine
        self.sessions = sessions
        self.hooks = hooks or Hooks()

    # -- 身份 --------------------------------------------------------------

    @staticmethod
    def wecom_userid(frame: dict[str, Any]) -> str:
        return ((frame.get("body") or {}).get("from") or {}).get("userid") or ""

    async def resolve_identity(self, frame: dict[str, Any]) -> str | None:
        """
        算出当前用户的身份。

        source=wecom_userid 时用企微 userid，但如果 bot 实现了
        resolve_identity hook（比如 userid 需要映射成 OA 工号），优先用 hook 的结果。
        source=hook 时完全由 hook 决定，hook 没实现或返回 None 就是解析失败。
        """
        source = self.config.identity.source

        if self.hooks.has("resolve_identity"):
            resolved = await self.hooks.resolve_identity(frame)
            if resolved:
                return resolved
            if source == "hook":
                return None
            logger.warning(
                "resolve_identity hook 没给出身份，回退到企微 userid"
            )
        elif source == "hook":
            logger.error(
                "identity.source 配成了 hook，但 %s 里没有实现 resolve_identity",
                self.hooks.source,
            )
            return None

        return self.wecom_userid(frame)

    # -- 主流程 ------------------------------------------------------------

    async def handle(
        self,
        frame: dict[str, Any],
        on_progress: ProgressCallback | None = None,
        on_tool_call: ToolCallObserver | None = None,
    ) -> Reply:
        """
        处理一条文本消息，返回该发给用户的内容。

        这个方法不抛异常：任何问题都转成 messages.error 的回复，
        Agent宁可说"出错了"也不能装死。
        """
        body = frame.get("body") or {}
        raw_userid = self.wecom_userid(frame)
        content = strip_mention((body.get("text") or {}).get("content", ""))
        chattype = body.get("chattype") or ""
        session_key = self.sessions.key_for(frame)
        msgs = self.config.messages

        logger.info(
            "收到消息 [%s] userid=%s 会话=%s: %s",
            chattype,
            raw_userid,
            session_key,
            content,
        )

        # 1. 身份解析与校验
        #
        # 这里有两种失败，要分开看：
        #   identity is None  解析失败（hook 没给出身份），无论有没有配
        #                     pattern 都必须拒绝 —— 我们根本不知道是谁在说话
        #   格式不匹配        配了 pattern 才判，比如工号形如 L220104
        identity = await self.resolve_identity(frame)
        if identity is None:
            logger.warning(
                "身份解析失败，拒绝处理。企微 userid=%r identity.source=%s",
                raw_userid,
                self.config.identity.source,
            )
            return Reply(msgs.identity_invalid, KIND_IDENTITY_INVALID)

        if not identity_matches(identity, self.config.identity.pattern):
            logger.warning(
                "身份校验未通过，拒绝处理。企微 userid=%r 解析后身份=%r 期望格式=%s",
                raw_userid,
                identity,
                self.config.identity.pattern,
            )
            return Reply(msgs.identity_invalid, KIND_IDENTITY_INVALID)

        ctx = HookContext(
            identity=identity,
            session_key=session_key,
            config=self.config,
            logger=logger,
            frame=frame,
        )

        # 2. 空消息
        if not content:
            return Reply(msgs.empty_input, KIND_EMPTY)

        # 3. 重置指令
        if content in msgs.reset_keywords:
            self.sessions.clear(session_key)
            logger.info("已清空会话 %s", session_key)
            return Reply(msgs.reset_done, KIND_RESET)

        # 4. 配置式前置拦截：命中 intercepts 规则就直接回固定文案，不进 LLM。
        #    先于 hook 执行 —— 配置是常规手段，hook 是逃生舱。
        for rule in self.config.intercepts:
            if rule.hit(content):
                logger.info("命中 intercepts 规则，未进入 LLM：%s", content)
                return Reply(rule.reply, KIND_INTERCEPT)

        # 5. hook 前置拦截：命中就不进 LLM，省 token 也更快
        intercepted = await self.hooks.on_message(content, ctx)
        if intercepted is not None:
            logger.info("on_message hook 拦截了本条消息，未进入 LLM")
            return Reply(intercepted, KIND_HOOK)

        # 6. 进 LLM
        if on_progress:
            await on_progress(msgs.thinking)

        history = self.sessions.get_history(session_key)

        try:
            reply = await self.engine.chat(
                content,
                identity=identity,
                history=history,
                on_progress=on_progress,
                hook_context=ctx,
                on_tool_call=on_tool_call,
            )
        except IdentityRejected:
            # 上面已经校验过，走到这里说明配置或代码有问题
            logger.exception("引擎拒绝了身份，这不该发生")
            return Reply(msgs.identity_invalid, KIND_IDENTITY_INVALID)
        except BaseExceptionGroup as eg:
            # TaskGroup 里的异常会被包成组，只打组本身看不出真正的错
            _log_exception_group(eg)
            return Reply(msgs.error, KIND_ERROR)
        except Exception:
            logger.exception("处理消息时出错")
            return Reply(msgs.error, KIND_ERROR)

        # 配置式回复加工：给 LLM 正常回复追加固定后缀（署名等）。
        # 先于 before_reply hook —— 配置是常规手段，hook 可再改写。
        suffix = msgs.reply_suffix
        if suffix and reply:
            reply = reply + suffix

        reply = await self.hooks.before_reply(reply, ctx)

        # 7. 存历史，供下一轮做上下文
        self.sessions.append_turn(session_key, content, reply)
        logger.info(
            "已回复 userid=%s，会话 %s 现有 %d 轮历史",
            raw_userid,
            session_key,
            self.sessions.turn_count(session_key),
        )
        return Reply(reply, KIND_LLM)


def _log_exception_group(eg: BaseException) -> None:
    """把 TaskGroup 异常组里的子异常逐个展开打出来，否则根本看不出哪儿错了"""
    logger.error("处理消息时出现异常组: %s", eg)
    for i, exc in enumerate(getattr(eg, "exceptions", ()), 1):
        if isinstance(exc, BaseExceptionGroup):
            logger.error("子异常 #%d 是嵌套的异常组：", i)
            _log_exception_group(exc)
            continue
        logger.error(
            "子异常 #%d: %s: %s",
            i,
            type(exc).__name__,
            exc,
            exc_info=exc,
        )


# --------------------------------------------------------------------------
# 组装
# --------------------------------------------------------------------------


def build_pipeline(config: BotConfig) -> tuple[MessagePipeline, BotEngine]:
    """按配置组装出 pipeline。engine 一并返回，方便调用方收尾时关闭它"""
    hooks = load_hooks(config.hooks_file)
    mcp = McpToolClient(config.mcp.servers)
    backend = create_backend(config.llm)
    engine = BotEngine(config, mcp, backend, hooks=hooks)
    sessions = SessionStore.from_config(config.session)
    return MessagePipeline(config, engine, sessions, hooks), engine


async def _start_chat_api(config: BotConfig, pipeline: MessagePipeline) -> Any:
    """在当前事件循环里起 chat HTTP 服务，返回 AppRunner 供收尾时清理。"""
    from aiohttp import web

    from botkit.api_server import create_api_app

    app = create_api_app(config, pipeline, hooks=pipeline.hooks)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host="0.0.0.0", port=config.api.port)
    await site.start()
    logger.info(
        "chat 接口已启动：POST http://0.0.0.0:%d/chat（鉴权：%s）",
        config.api.port,
        "Bearer token" if config.api.token else "无",
    )
    return runner


def serve_chat_api(config: BotConfig) -> None:
    """只起 chat HTTP 接口，不连企微。用于纯 API 部署（对接 Dify/门户）。"""
    setup_logging(config.name, config.log_level)
    pipeline, engine = build_pipeline(config)

    logger.info("正在启动 chat 接口服务 %s（不连企微）", config.name)
    logger.info("提示词来源  : %s", config.prompt_source)
    logger.info("LLM 后端    : %s（%s / %s）", config.llm.client, config.llm.api_url, config.llm.model)
    logger.info("会话隔离    : %s（保留 %d 轮，%d 秒过期）",
                config.session.scope, config.session.max_turns, config.session.ttl_seconds)

    async def main() -> None:
        runner = await _start_chat_api(config, pipeline)
        try:
            await asyncio.Event().wait()  # 挂住直到取消
        finally:
            await runner.cleanup()

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("收到中断信号，正在退出")
    finally:
        try:
            asyncio.run(engine.aclose())
        except Exception:
            logger.debug("关闭 LLM 后端时出错（忽略）", exc_info=True)
        logger.info("chat 接口服务 %s 已停止", config.name)


def run_bot(config: BotConfig) -> None:
    """
    启动Agent，阻塞直到进程被中断。

    根据配置决定跑什么：
    - 配了企微凭证 → 连企微长连接；
    - api.enabled → 同时起 chat HTTP 接口；
    - 只开了 chat（没企微）→ 转交 serve_chat_api。
    """
    if not config.has_wecom:
        # 没企微凭证：只可能是纯 chat 部署
        if config.api.enabled:
            serve_chat_api(config)
            return
        # config 校验已保证「至少一样」，这里理论到不了
        raise RuntimeError("既没有企微凭证也没开 chat 接口，无法启动")

    from aibot import WSClient, WSClientOptions, generate_req_id

    setup_logging(config.name, config.log_level)

    pipeline, engine = build_pipeline(config)

    logger.info("正在启动Agent %s", config.name)
    logger.info("提示词来源  : %s", config.prompt_source)
    logger.info("LLM 后端    : %s（%s / %s）", config.llm.client, config.llm.api_url, config.llm.model)
    for server in config.mcp.servers:
        allow = "、".join(server.allow_tools) if server.allow_tools else "(全部)"
        logger.info("MCP 服务端  : %s %s 工具=%s", server.name, server.url, allow)
    logger.info("会话隔离    : %s（保留 %d 轮，%d 秒过期）",
                config.session.scope, config.session.max_turns, config.session.ttl_seconds)
    logger.info("已加载 hook : %s", "、".join(pipeline.hooks.implemented) or "(无)")

    ws_client = WSClient(
        WSClientOptions(bot_id=config.wecom.bot_id, secret=config.wecom.bot_secret)
    )

    # -- 连接类事件 ----------------------------------------------------

    def on_connected() -> None:
        logger.info("WebSocket 已连接")

    def on_authenticated() -> None:
        logger.info("认证成功，Agent %s 已上线", config.name)

    def on_disconnected(reason: Any) -> None:
        logger.warning("连接断开：%s", reason)

    def on_error(err: Any) -> None:
        logger.error("SDK 错误：%s", err)

    # -- 消息 ----------------------------------------------------------

    async def on_text(frame: dict[str, Any]) -> None:
        stream_id = generate_req_id("stream")

        async def on_progress(label: str) -> None:
            # 中间进度用 finish=False 刷新同一条流式消息。
            # 推送失败不该影响主流程，用户最多是看不到进度提示。
            try:
                await ws_client.reply_stream(frame, stream_id, label, False)
            except Exception as e:
                logger.debug("推送进度失败（忽略）：%s", e)

        reply = await pipeline.handle(frame, on_progress=on_progress)

        try:
            await ws_client.reply_stream(frame, stream_id, reply.text, True)
        except Exception:
            logger.exception("发送回复失败，本轮回复丢失")

    async def on_enter_chat(frame: dict[str, Any]) -> None:
        try:
            await ws_client.reply_welcome(
                frame,
                {"msgtype": "text", "text": {"content": config.messages.welcome}},
            )
        except Exception:
            logger.exception("发送欢迎语失败")

    # 程序化注册，不用装饰器 —— 这样 WSClient 是函数内的局部对象，
    # 一个进程里理论上可以跑多个 bot，也方便测试
    ws_client.on("connected", on_connected)
    ws_client.on("authenticated", on_authenticated)
    ws_client.on("disconnected", on_disconnected)
    ws_client.on("error", on_error)
    ws_client.on("message.text", on_text)
    ws_client.on("event.enter_chat", on_enter_chat)

    if config.api.enabled:
        # 企微 + chat 并存：自己管 event loop，把企微连接和 aiohttp 一起挂上。
        async def main() -> None:
            runner = await _start_chat_api(config, pipeline)
            try:
                await ws_client.connect()   # 非阻塞，连上即返回
                await asyncio.Event().wait()  # 挂住直到 Ctrl+C
            finally:
                await runner.cleanup()

        try:
            asyncio.run(main())
        except KeyboardInterrupt:
            logger.info("收到中断信号，正在退出")
        finally:
            try:
                ws_client.disconnect()
            except Exception:
                logger.debug("断开企微连接时出错（忽略）", exc_info=True)
            try:
                asyncio.run(engine.aclose())
            except Exception:
                logger.debug("关闭 LLM 后端时出错（忽略）", exc_info=True)
            logger.info("Agent %s 已停止", config.name)
    else:
        # 只连企微：沿用 SDK 自带的阻塞式 run（内部管 loop）
        try:
            ws_client.run()
        except KeyboardInterrupt:
            logger.info("收到中断信号，正在退出")
        finally:
            try:
                asyncio.run(engine.aclose())
            except Exception:
                logger.debug("关闭 LLM 后端时出错（忽略）", exc_info=True)
            logger.info("Agent %s 已停止", config.name)
