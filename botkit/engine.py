"""
LLM 对话引擎

一轮消息的处理：
1. 校验身份（配了 identity.pattern 才校验）
2. 拼 system prompt（prompt.md 内容 + 当前用户身份）
3. 开一个 MCP session，拉取本 bot 可用的工具
4. function calling 循环：模型要调工具就调，调完把结果回灌，直到模型给出文本回复
5. 循环里对工具参数做身份强制注入（安全边界）和 hook 改写

身份强制注入是这里最关键的一段：LLM 生成的 work_code 这类身份参数
一律用企微侧拿到的可信身份覆盖掉，避免 LLM 编造别人的工号去提工单。
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, AsyncIterator, Awaitable, Callable

from botkit.config import BotConfig, ContentWatcher
from botkit.hooks import HookContext, Hooks
from botkit.llm.base import LlmBackend, LlmError, TokenUsage
from botkit.mcp_client import McpToolClient

logger = logging.getLogger("botkit.engine")

ProgressCallback = Callable[[str], Awaitable[None]]
# (工具名, 实际发出的参数, 工具返回的文本)
ToolCallObserver = Callable[[str, "dict[str, Any]", str], Awaitable[None]]
# 一轮对话收尾时回调：(本轮累计 token 用量, 工具调用总次数)。
# engine 只把它掌握的这两样交出去，其余（耗时/问题/成败）由 pipeline 补全。
UsageReporter = Callable[["TokenUsage", int], Awaitable[None]]


class IdentityRejected(Exception):
    """身份不合法。上层应该回复 messages.identity_invalid 并终止本轮"""

    def __init__(self, identity: str | None, pattern: str):
        self.identity = identity
        self.pattern = pattern
        super().__init__(
            f"身份 {identity!r} 不匹配 identity.pattern（{pattern}）"
        )


def identity_matches(identity: str | None, pattern: str | None) -> bool:
    """
    校验身份。pattern 为 None 表示这个 bot 不需要校验身份。

    注意空身份：配了 pattern 就意味着这个 bot 依赖身份，
    拿不到身份时必须判为不合法，不能放过去。
    """
    if pattern is None:
        return True
    if not identity:
        return False
    return re.match(pattern, identity) is not None


class BotEngine:
    """一个 bot 的对话引擎"""

    def __init__(
        self,
        config: BotConfig,
        mcp: McpToolClient,
        backend: LlmBackend,
        hooks: Hooks | None = None,
    ):
        self.config = config
        self.mcp = mcp
        self.backend = backend
        self.hooks = hooks or Hooks()
        # 提示词/技能文件运行中被更新时（如 Demand 发布新锚点集），不重启也能生效
        self._content = ContentWatcher(config)

    # -- system prompt -----------------------------------------------------

    def build_system_prompt(self, identity: str | None) -> str:
        """
        prompt.md 的内容加上当前用户身份。

        把身份写进 system 而不是让 LLM 去问，是因为身份参数本来就会被
        强制注入，让模型知道「已经有了」可以避免它多问一句。

        拼之前先检查提示词/技能文件有没有更新，有就用最新内容。
        """
        self._content.refresh()
        prompt = self.config.prompt_text
        # 启用的技能作为补充知识拼进来，帮模型理解业务说明/术语
        skills = getattr(self.config, "skills_text", "")
        if skills:
            prompt = f"{prompt}\n\n# 补充知识（技能）\n{skills}"
        if identity:
            prompt = f"{prompt}\n\n# 当前用户身份\n{identity}"
        return prompt

    # -- 主流程 ------------------------------------------------------------

    async def chat(
        self,
        user_message: str,
        identity: str | None,
        history: list[dict[str, str]] | None = None,
        on_progress: ProgressCallback | None = None,
        hook_context: HookContext | None = None,
        on_tool_call: ToolCallObserver | None = None,
        on_usage: "UsageReporter | None" = None,
    ) -> str:
        """
        处理一次用户消息，返回最终回复文本。

        history      之前的多轮对话（user/assistant 交替），提供上下文记忆
        on_progress  推送中间进度的回调，用于流式刷新「正在查询...」
        hook_context 传给 hook 的上下文，不传就地造一个
        on_tool_call 纯观测回调，每次工具调用后拿到 (工具名, 实际参数, 结果)。
                     只用于预览测试这类想看清链路的场景，不传就完全无影响，
                     也不改变任何行为。

        LLM 调不通时抛 LlmError，MCP 连不上时抛 McpError，
        由上层决定怎么回复用户（走 messages.error）。
        """
        pattern = self.config.identity.pattern
        if not identity_matches(identity, pattern):
            # 走到这里说明上层没先校验。抛异常而不是继续，
            # 免得没有可信身份的请求摸到工具。
            raise IdentityRejected(identity, pattern or "")

        ctx = hook_context or HookContext(
            identity=identity, config=self.config, logger=logger
        )

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self.build_system_prompt(identity)}
        ]
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": user_message})

        max_rounds = self.config.llm.max_tool_rounds
        # 一轮对话里 function calling 可能调多次 LLM，token 要跨轮累加
        total_usage = TokenUsage()
        llm_calls = 0
        tool_calls_total = 0

        async with self.mcp.session(identity=identity) as sess:
            tools = await sess.list_tools_openai()

            for round_i in range(1, max_rounds + 1):
                logger.debug("第 %d/%d 轮请求 LLM", round_i, max_rounds)
                resp = await self.backend.complete(messages, tools)
                total_usage = total_usage + resp.usage
                llm_calls += 1

                if not resp.wants_tools:
                    reply = (resp.content or "").strip()
                    if not reply:
                        logger.warning("LLM 既没有工具调用也没有文本回复")
                        raise LlmError("LLM 返回了空回复")
                    logger.info("第 %d 轮得到最终回复（%d 字）", round_i, len(reply))
                    _log_usage(total_usage, llm_calls)
                    await _report_usage(on_usage, total_usage, tool_calls_total)
                    return reply

                # 有工具调用：身份注入、hook 改写、调用并回灌结果
                tool_calls_total += await self._execute_tool_calls(
                    sess, resp, messages, identity, ctx, on_progress, on_tool_call
                )

        # 轮次用完还没给出文本回复，通常是模型在反复调工具打转
        logger.warning("工具调用轮次达到上限 %d，放弃本轮", max_rounds)
        _log_usage(total_usage, llm_calls)
        await _report_usage(on_usage, total_usage, tool_calls_total)
        return self.config.messages.tool_rounds_exceeded

    async def chat_stream(
        self,
        user_message: str,
        identity: str | None,
        history: list[dict[str, str]] | None = None,
        on_progress: ProgressCallback | None = None,
        hook_context: HookContext | None = None,
        on_tool_call: ToolCallObserver | None = None,
        on_usage: "UsageReporter | None" = None,
    ) -> AsyncIterator[str]:
        """
        chat 的流式版本：逐段 yield 最终回复的文本增量（打字机效果）。

        和 chat 的行为完全一致（同样的身份校验、system prompt、工具循环、
        身份注入、hook），区别只在产出文本时用 backend.stream 逐 token 吐出。
        工具调用轮本身不是给用户看的文本，不往外 yield；进度仍走 on_progress
        回调。轮次耗尽时 yield 一句 tool_rounds_exceeded 文案作为收尾。

        与 chat 一样：LLM 调不通抛 LlmError，MCP 连不上抛 McpError。
        注意：一旦开始 yield 文本后再抛异常，调用方会收到半截输出——但空回复
        （没 yield 过任何 token）时才会抛 LlmError，所以不会出现「推了一半又报错」。
        """
        pattern = self.config.identity.pattern
        if not identity_matches(identity, pattern):
            raise IdentityRejected(identity, pattern or "")

        ctx = hook_context or HookContext(
            identity=identity, config=self.config, logger=logger
        )

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self.build_system_prompt(identity)}
        ]
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": user_message})

        max_rounds = self.config.llm.max_tool_rounds
        total_usage = TokenUsage()
        llm_calls = 0
        tool_calls_total = 0

        async with self.mcp.session(identity=identity) as sess:
            tools = await sess.list_tools_openai()

            for round_i in range(1, max_rounds + 1):
                logger.debug("第 %d/%d 轮请求 LLM（流式）", round_i, max_rounds)

                produced = ""
                resp = None
                async for chunk in self.backend.stream(messages, tools):
                    if chunk.done:
                        resp = chunk.response
                        break
                    if chunk.delta:
                        produced += chunk.delta
                        yield chunk.delta

                if resp is None:
                    raise LlmError("流式返回没有收尾（缺少 done 片段）")

                total_usage = total_usage + resp.usage
                llm_calls += 1

                if not resp.wants_tools:
                    # 这一轮是最终答复
                    if not produced.strip() and not (resp.content or "").strip():
                        logger.warning("LLM 既没有工具调用也没有文本回复")
                        raise LlmError("LLM 返回了空回复")
                    logger.info("第 %d 轮得到最终回复（流式）", round_i)
                    _log_usage(total_usage, llm_calls)
                    await _report_usage(on_usage, total_usage, tool_calls_total)
                    return

                # 工具调用轮：和 chat 完全一样地处理
                tool_calls_total += await self._execute_tool_calls(
                    sess, resp, messages, identity, ctx, on_progress, on_tool_call
                )

        logger.warning("工具调用轮次达到上限 %d，放弃本轮", max_rounds)
        _log_usage(total_usage, llm_calls)
        await _report_usage(on_usage, total_usage, tool_calls_total)
        yield self.config.messages.tool_rounds_exceeded

    async def _execute_tool_calls(
        self,
        sess: Any,
        resp: Any,
        messages: list[dict[str, Any]],
        identity: str | None,
        ctx: HookContext,
        on_progress: ProgressCallback | None,
        on_tool_call: ToolCallObserver | None,
    ) -> None:
        """
        处理一轮工具调用：把 assistant 消息入上下文，对每个 tool_call 做身份
        强制注入、hook 改写、参数同步，调用工具并把结果回灌进 messages。

        chat 和 chat_stream 共用这段逻辑，确保两条路径行为完全一致。

        返回本轮处理的工具调用数，供上层累加进用量统计。
        """
        assistant_message = resp.message
        messages.append(assistant_message)

        for index, tc in enumerate(resp.tool_calls):
            args = dict(tc.arguments)

            # 安全边界：可信身份覆盖 LLM 生成的值
            for arg_name in self.config.inject_for(tc.name):
                if args.get(arg_name) not in (None, "", identity):
                    logger.warning(
                        "LLM 给工具 %s 的 %s 传了 %r，已用可信身份 %r 覆盖",
                        tc.name,
                        arg_name,
                        args.get(arg_name),
                        identity,
                    )
                args[arg_name] = identity

            args = await self.hooks.before_tool_call(tc.name, args, ctx)

            # 参数被改过就同步回 assistant 消息，让上下文里记录的调用
            # 和实际发出的调用一致。
            if args != tc.arguments:
                _sync_tool_call_args(assistant_message, index, args)

            if on_progress:
                await on_progress(self.config.messages.progress_for(tc.name))

            result = await sess.call_tool(tc.name, args)
            result = await self.hooks.after_tool_call(tc.name, result, ctx)

            if on_tool_call:
                await on_tool_call(tc.name, args, result)

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result,
                }
            )

        return len(resp.tool_calls)

    async def aclose(self) -> None:
        await self.backend.aclose()


async def _report_usage(
    on_usage: "UsageReporter | None", usage: TokenUsage, tool_calls: int
) -> None:
    """把本轮用量交给上层回调（pipeline 会据此触发 on_usage hook）。不传则无操作。"""
    if on_usage is None:
        return
    await on_usage(usage, tool_calls)


def _log_usage(usage: TokenUsage, llm_calls: int) -> None:
    """
    一轮对话结束时记一次 token 消耗。

    带工具调用的对话会多次请求 LLM（每次都把全部历史重发一遍），所以这里
    打的是**本轮累计**，比单次调用的数字更能反映真实成本。

    端点不返回用量时（usage 全为 0）不打这行，免得日志里一堆没意义的 0。
    """
    if not usage:
        return
    logger.info(
        "本轮 token：输入 %d，输出 %d，合计 %d（%d 次 LLM 调用）",
        usage.input_tokens,
        usage.output_tokens,
        usage.total_tokens,
        llm_calls,
    )


def _sync_tool_call_args(
    assistant_message: dict[str, Any], index: int, args: dict[str, Any]
) -> None:
    """把实际发出的参数写回 assistant 消息里对应的 tool_call"""
    tool_calls = assistant_message.get("tool_calls") or []
    if index >= len(tool_calls):
        return
    try:
        tool_calls[index]["function"]["arguments"] = json.dumps(args, ensure_ascii=False)
    except (TypeError, ValueError, KeyError):
        logger.debug("同步 tool_call 参数失败，保留原值", exc_info=True)
