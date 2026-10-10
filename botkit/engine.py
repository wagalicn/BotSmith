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
from typing import Any, Awaitable, Callable

from botkit.config import BotConfig, ContentWatcher
from botkit.hooks import HookContext, Hooks
from botkit.llm.base import LlmBackend, LlmError
from botkit.mcp_client import McpToolClient

logger = logging.getLogger("botkit.engine")

ProgressCallback = Callable[[str], Awaitable[None]]
# (工具名, 实际发出的参数, 工具返回的文本)
ToolCallObserver = Callable[[str, "dict[str, Any]", str], Awaitable[None]]


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

        async with self.mcp.session(identity=identity) as sess:
            tools = await sess.list_tools_openai()

            for round_i in range(1, max_rounds + 1):
                logger.debug("第 %d/%d 轮请求 LLM", round_i, max_rounds)
                resp = await self.backend.complete(messages, tools)

                if not resp.wants_tools:
                    reply = (resp.content or "").strip()
                    if not reply:
                        logger.warning("LLM 既没有工具调用也没有文本回复")
                        raise LlmError("LLM 返回了空回复")
                    logger.info("第 %d 轮得到最终回复（%d 字）", round_i, len(reply))
                    return reply

                # 有工具调用，先把 assistant 消息放进上下文
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
                    # 和实际发出的调用一致。否则模型会在自己的历史里看到
                    # 被覆盖掉的旧参数（比如伪造的工号），可能照着它回答用户。
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

        # 轮次用完还没给出文本回复，通常是模型在反复调工具打转
        logger.warning("工具调用轮次达到上限 %d，放弃本轮", max_rounds)
        return self.config.messages.tool_rounds_exceeded

    async def aclose(self) -> None:
        await self.backend.aclose()


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
