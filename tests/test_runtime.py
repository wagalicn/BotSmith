"""
运行时消息流程测试

测的是 MessagePipeline —— 一条消息进来走哪条分支、回什么、要不要存历史。
不碰企微 SDK。
"""

from __future__ import annotations

import logging

import pytest

from botkit.config import Intercept, IdentityConfig, MessagesConfig, SessionConfig
from botkit.engine import BotEngine, IdentityRejected
from botkit.hooks import Hooks
from botkit.llm.base import LlmError
from botkit.mcp_client import McpError
from botkit.runtime import (
    KIND_EMPTY,
    KIND_ERROR,
    KIND_HOOK,
    KIND_IDENTITY_INVALID,
    KIND_INTERCEPT,
    KIND_LLM,
    KIND_RESET,
    MessagePipeline,
    strip_mention,
)
from botkit.session import SessionStore
from test_engine import ScriptedBackend, make_config, make_engine


# --------------------------------------------------------------------------
# 脚手架
# --------------------------------------------------------------------------


def frame(
    content: str,
    userid: str = "L220104",
    chatid: str = "chat-1",
    chattype: str = "group",
) -> dict:
    return {
        "body": {
            "from": {"userid": userid},
            "chatid": chatid,
            "chattype": chattype,
            "text": {"content": content},
        }
    }


class RecordingEngine:
    """假引擎：记录调用，返回预设回复或抛出预设异常"""

    def __init__(self, reply="引擎的回复", raises=None, config=None):
        self._reply = reply
        self._raises = raises
        self.calls = []
        self.config = config or make_config()

    async def chat(
        self,
        user_message,
        identity,
        history=None,
        on_progress=None,
        hook_context=None,
        on_tool_call=None,
    ):
        self.calls.append(
            {
                "message": user_message,
                "identity": identity,
                "history": history,
                "ctx": hook_context,
                "on_tool_call": on_tool_call,
            }
        )
        if on_progress:
            await on_progress("引擎的进度提示")
        if self._raises:
            raise self._raises
        return self._reply

    async def aclose(self):
        pass

    @property
    def call_count(self):
        return len(self.calls)


def make_pipeline(
    engine=None,
    *,
    messages=None,
    identity=None,
    session=None,
    hooks=None,
):
    cfg = make_config(messages=messages)
    if identity is not None:
        cfg.identity = identity
    if session is not None:
        cfg.session = session
    eng = engine or RecordingEngine(config=cfg)
    eng.config = cfg
    sessions = SessionStore.from_config(cfg.session)
    return MessagePipeline(cfg, eng, sessions, hooks or Hooks()), eng, sessions


class ProgressRecorder:
    def __init__(self):
        self.labels = []

    async def __call__(self, label):
        self.labels.append(label)


# --------------------------------------------------------------------------
# @ 前缀剥离
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("@IT助手 我电脑坏了", "我电脑坏了"),
        ("  @IT助手   我电脑坏了  ", "我电脑坏了"),
        ("@IT助手", ""),
        ("我电脑坏了", "我电脑坏了"),
        ("@bot1 @bot2 帮忙", "帮忙"),
        ("", ""),
    ],
)
def test_strip_mention(raw, expected):
    assert strip_mention(raw) == expected


def test_strip_mention_keeps_email_in_body():
    """
    只剥离开头的 @机器人。正文里的邮箱和 @同事 必须保留
    —— 原实现用全局替换，会把 zhang@example.com 的域名部分吃掉。
    """
    assert (
        strip_mention("@IT助手 我的邮箱 zhang@example.com 收不到邮件")
        == "我的邮箱 zhang@example.com 收不到邮件"
    )
    assert strip_mention("@IT助手 麻烦 @张三 一起看下") == "麻烦 @张三 一起看下"


def test_strip_mention_handles_none():
    assert strip_mention(None) == ""


# --------------------------------------------------------------------------
# 正常路径
# --------------------------------------------------------------------------


async def test_normal_message_goes_to_engine():
    pipeline, engine, _ = make_pipeline()
    reply = await pipeline.handle(frame("@IT助手 我电脑连不上网"))

    assert reply.kind == KIND_LLM
    assert reply.text == "引擎的回复"
    assert engine.call_count == 1
    assert engine.calls[0]["message"] == "我电脑连不上网"
    assert engine.calls[0]["identity"] == "L220104"


async def test_history_is_saved_and_reused():
    pipeline, engine, sessions = make_pipeline()
    await pipeline.handle(frame("第一句"))
    await pipeline.handle(frame("第二句"))

    # 第二次调用应该带上第一轮的历史
    assert engine.calls[0]["history"] == []
    assert engine.calls[1]["history"] == [
        {"role": "user", "content": "第一句"},
        {"role": "assistant", "content": "引擎的回复"},
    ]


async def test_sessions_isolated_per_person_in_group():
    pipeline, engine, _ = make_pipeline(
        session=SessionConfig(scope="group_user")
    )
    await pipeline.handle(frame("A 说的", userid="L220104"))
    await pipeline.handle(frame("B 说的", userid="L220105"))

    # B 不该看到 A 的历史
    assert engine.calls[1]["history"] == []


async def test_group_scope_shares_history():
    pipeline, engine, _ = make_pipeline(session=SessionConfig(scope="group"))
    await pipeline.handle(frame("A 说的", userid="L220104"))
    await pipeline.handle(frame("B 说的", userid="L220105"))

    assert engine.calls[1]["history"][0]["content"] == "A 说的"


async def test_progress_thinking_then_engine_progress():
    pipeline, _, _ = make_pipeline()
    progress = ProgressRecorder()
    await pipeline.handle(frame("我电脑坏了"), on_progress=progress)

    assert progress.labels == ["正在处理，请稍候...", "引擎的进度提示"]


async def test_custom_thinking_message():
    msgs = MessagesConfig(thinking="让我想想...")
    pipeline, _, _ = make_pipeline(messages=msgs)
    progress = ProgressRecorder()
    await pipeline.handle(frame("你好"), on_progress=progress)
    assert progress.labels[0] == "让我想想..."


# --------------------------------------------------------------------------
# 空消息
# --------------------------------------------------------------------------


async def test_empty_message_returns_configured_text():
    msgs = MessagesConfig(empty_input="请描述你的问题。")
    pipeline, engine, _ = make_pipeline(messages=msgs)
    reply = await pipeline.handle(frame("@IT助手"))

    assert reply.kind == KIND_EMPTY
    assert reply.text == "请描述你的问题。"
    assert engine.call_count == 0


async def test_whitespace_only_message_is_empty():
    pipeline, engine, _ = make_pipeline()
    reply = await pipeline.handle(frame("@IT助手     "))
    assert reply.kind == KIND_EMPTY
    assert engine.call_count == 0


async def test_empty_message_does_not_save_history():
    pipeline, _, sessions = make_pipeline()
    f = frame("@IT助手")
    await pipeline.handle(f)
    assert sessions.get_history(sessions.key_for(f)) == []


# --------------------------------------------------------------------------
# 重置关键词
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "keyword", ["重新开始", "重来", "清空对话", "重置", "reset"]
)
async def test_reset_keywords(keyword):
    pipeline, engine, _ = make_pipeline()
    reply = await pipeline.handle(frame(keyword))
    assert reply.kind == KIND_RESET
    assert engine.call_count == 0


async def test_reset_clears_history():
    pipeline, engine, sessions = make_pipeline()
    f = frame("第一句")
    await pipeline.handle(f)
    assert sessions.turn_count(sessions.key_for(f)) == 1

    await pipeline.handle(frame("重新开始"))
    assert sessions.turn_count(sessions.key_for(f)) == 0

    await pipeline.handle(frame("重新开始之后的第一句"))
    assert engine.calls[-1]["history"] == []


async def test_reset_uses_configured_message():
    msgs = MessagesConfig(reset_done="好的，从头开始。")
    pipeline, _, _ = make_pipeline(messages=msgs)
    reply = await pipeline.handle(frame("重置"))
    assert reply.text == "好的，从头开始。"


async def test_custom_reset_keywords():
    msgs = MessagesConfig(reset_keywords=["清空", "忘掉"])
    pipeline, engine, _ = make_pipeline(messages=msgs)

    assert (await pipeline.handle(frame("清空"))).kind == KIND_RESET
    assert (await pipeline.handle(frame("忘掉"))).kind == KIND_RESET
    # 默认词在自定义后不再生效
    assert (await pipeline.handle(frame("重新开始"))).kind == KIND_LLM


async def test_reset_keyword_only_matches_whole_message():
    """"重置一下密码" 不该被当成重置指令"""
    pipeline, engine, _ = make_pipeline()
    reply = await pipeline.handle(frame("重置一下我的密码"))
    assert reply.kind == KIND_LLM
    assert engine.call_count == 1


# --------------------------------------------------------------------------
# 身份校验
# --------------------------------------------------------------------------


async def test_invalid_identity_rejected_before_engine(caplog):
    msgs = MessagesConfig(identity_invalid="无法识别你的工号。")
    pipeline, engine, _ = make_pipeline(messages=msgs)

    with caplog.at_level(logging.WARNING, logger="botkit.runtime"):
        reply = await pipeline.handle(frame("我电脑坏了", userid="wo1234abcd=="))

    assert reply.kind == KIND_IDENTITY_INVALID
    assert reply.text == "无法识别你的工号。"
    assert engine.call_count == 0  # 引擎完全没被调用
    # 日志里要有原始 userid，否则没法排查
    assert any("wo1234abcd==" in r.getMessage() for r in caplog.records)


async def test_invalid_identity_does_not_save_history():
    pipeline, _, sessions = make_pipeline()
    f = frame("我电脑坏了", userid="非法")
    await pipeline.handle(f)
    assert sessions.get_history(sessions.key_for(f)) == []


async def test_missing_userid_rejected_when_pattern_configured():
    pipeline, engine, _ = make_pipeline()
    reply = await pipeline.handle(frame("你好", userid=""))
    assert reply.kind == KIND_IDENTITY_INVALID
    assert engine.call_count == 0


async def test_no_pattern_accepts_any_userid():
    pipeline, engine, _ = make_pipeline(identity=IdentityConfig(pattern=None))
    reply = await pipeline.handle(frame("你好", userid="wo1234abcd=="))
    assert reply.kind == KIND_LLM
    assert engine.calls[0]["identity"] == "wo1234abcd=="


async def test_identity_checked_before_empty_message_check():
    """身份不合法时，连"消息是空的"都不用回 —— 直接告诉他身份问题"""
    pipeline, _, _ = make_pipeline()
    reply = await pipeline.handle(frame("@IT助手", userid="非法工号"))
    assert reply.kind == KIND_IDENTITY_INVALID


async def test_identity_checked_before_reset_keyword():
    pipeline, _, _ = make_pipeline()
    reply = await pipeline.handle(frame("重新开始", userid="非法工号"))
    assert reply.kind == KIND_IDENTITY_INVALID


# --------------------------------------------------------------------------
# resolve_identity hook
# --------------------------------------------------------------------------


async def test_resolve_identity_hook_maps_userid():
    """企微 userid 不是 OA 工号时，用 hook 做映射"""
    table = {"wo_encrypted_1": "L220104"}

    def resolve_identity(frame):
        return table.get(frame["body"]["from"]["userid"])

    pipeline, engine, _ = make_pipeline(
        hooks=Hooks({"resolve_identity": resolve_identity})
    )
    reply = await pipeline.handle(frame("你好", userid="wo_encrypted_1"))

    assert reply.kind == KIND_LLM
    assert engine.calls[0]["identity"] == "L220104"


async def test_resolve_identity_hook_failure_is_rejected():
    def resolve_identity(frame):
        return None  # 表里查不到

    pipeline, engine, _ = make_pipeline(
        identity=IdentityConfig(source="hook", pattern=r"^[LM]\d{6}$"),
        hooks=Hooks({"resolve_identity": resolve_identity}),
    )
    reply = await pipeline.handle(frame("你好", userid="wo_unknown"))

    assert reply.kind == KIND_IDENTITY_INVALID
    assert engine.call_count == 0


async def test_source_hook_without_hook_implementation_is_rejected(caplog):
    pipeline, engine, _ = make_pipeline(
        identity=IdentityConfig(source="hook", pattern=None)
    )
    with caplog.at_level(logging.ERROR, logger="botkit.runtime"):
        reply = await pipeline.handle(frame("你好"))

    assert reply.kind == KIND_IDENTITY_INVALID
    assert engine.call_count == 0
    assert any("resolve_identity" in r.getMessage() for r in caplog.records)


async def test_resolution_failure_rejected_even_without_pattern():
    """
    身份"解析失败"和"格式不符"是两回事。
    没配 pattern 也不能放过解析失败的请求 —— 我们根本不知道是谁在说话。
    """
    def resolve_identity(frame):
        return None

    pipeline, engine, _ = make_pipeline(
        identity=IdentityConfig(source="hook", pattern=None),
        hooks=Hooks({"resolve_identity": resolve_identity}),
    )
    reply = await pipeline.handle(frame("你好"))
    assert reply.kind == KIND_IDENTITY_INVALID
    assert engine.call_count == 0


async def test_hook_returning_none_falls_back_to_userid_when_source_is_wecom(caplog):
    def resolve_identity(frame):
        return None

    pipeline, engine, _ = make_pipeline(
        hooks=Hooks({"resolve_identity": resolve_identity})
    )
    with caplog.at_level(logging.WARNING, logger="botkit.runtime"):
        reply = await pipeline.handle(frame("你好", userid="L220104"))

    assert reply.kind == KIND_LLM
    assert engine.calls[0]["identity"] == "L220104"


async def test_crashing_resolve_identity_hook_falls_back():
    def resolve_identity(frame):
        raise RuntimeError("查表挂了")

    pipeline, engine, _ = make_pipeline(
        hooks=Hooks({"resolve_identity": resolve_identity})
    )
    reply = await pipeline.handle(frame("你好", userid="L220104"))
    assert reply.kind == KIND_LLM
    assert engine.calls[0]["identity"] == "L220104"


# --------------------------------------------------------------------------
# on_message hook
# --------------------------------------------------------------------------


async def test_on_message_hook_short_circuits_llm():
    def on_message(text, ctx):
        if text == "帮助":
            return "我能帮你创建工单"
        return None

    pipeline, engine, _ = make_pipeline(hooks=Hooks({"on_message": on_message}))

    reply = await pipeline.handle(frame("帮助"))
    assert reply.kind == KIND_HOOK
    assert reply.text == "我能帮你创建工单"
    assert engine.call_count == 0

    reply = await pipeline.handle(frame("我电脑坏了"))
    assert reply.kind == KIND_LLM
    assert engine.call_count == 1


async def test_on_message_hook_receives_context():
    seen = {}

    def on_message(text, ctx):
        seen["identity"] = ctx.identity
        seen["session_key"] = ctx.session_key
        seen["bot"] = ctx.config.name
        seen["has_frame"] = ctx.frame is not None
        return "拦了"

    pipeline, _, _ = make_pipeline(hooks=Hooks({"on_message": on_message}))
    await pipeline.handle(frame("随便", userid="L220104", chatid="c9"))

    assert seen["identity"] == "L220104"
    assert seen["session_key"] == "group:c9:L220104"
    assert seen["bot"] == "test-bot"
    assert seen["has_frame"] is True


async def test_on_message_runs_after_reset_keyword():
    """重置指令优先于 hook 拦截，否则用户没法清空对话"""
    def on_message(text, ctx):
        return "hook 拦下了一切"

    pipeline, _, _ = make_pipeline(hooks=Hooks({"on_message": on_message}))
    reply = await pipeline.handle(frame("重新开始"))
    assert reply.kind == KIND_RESET


async def test_hook_intercepted_reply_not_saved_to_history():
    def on_message(text, ctx):
        return "帮助菜单"

    pipeline, _, sessions = make_pipeline(hooks=Hooks({"on_message": on_message}))
    f = frame("帮助")
    await pipeline.handle(f)
    # 帮助菜单这类固定回复不进上下文，避免污染后续对话
    assert sessions.get_history(sessions.key_for(f)) == []


# --------------------------------------------------------------------------
# before_reply hook
# --------------------------------------------------------------------------


async def test_before_reply_hook_applied_to_llm_reply():
    def before_reply(text, ctx):
        return text + "\n\n—— IT 助手"

    pipeline, _, _ = make_pipeline(hooks=Hooks({"before_reply": before_reply}))
    reply = await pipeline.handle(frame("你好"))
    assert reply.text == "引擎的回复\n\n—— IT 助手"


async def test_before_reply_result_is_what_gets_saved():
    def before_reply(text, ctx):
        return text + "（已加工）"

    pipeline, _, sessions = make_pipeline(hooks=Hooks({"before_reply": before_reply}))
    f = frame("你好")
    await pipeline.handle(f)
    history = sessions.get_history(sessions.key_for(f))
    assert history[1]["content"] == "引擎的回复（已加工）"


async def test_before_reply_not_applied_to_error_message():
    def before_reply(text, ctx):
        return text + "（加工过）"

    engine = RecordingEngine(raises=LlmError("挂了"))
    pipeline, _, _ = make_pipeline(engine, hooks=Hooks({"before_reply": before_reply}))
    reply = await pipeline.handle(frame("你好"))
    assert reply.kind == KIND_ERROR
    assert "加工过" not in reply.text


# --------------------------------------------------------------------------
# 异常降级
# --------------------------------------------------------------------------


async def test_llm_error_becomes_configured_error_message():
    msgs = MessagesConfig(error="AI 服务暂时不可用，稍后再试。")
    engine = RecordingEngine(raises=LlmError("502"))
    pipeline, _, _ = make_pipeline(engine, messages=msgs)
    reply = await pipeline.handle(frame("你好"))

    assert reply.kind == KIND_ERROR
    assert reply.text == "AI 服务暂时不可用，稍后再试。"


async def test_mcp_error_becomes_error_message():
    engine = RecordingEngine(raises=McpError("连不上中台"))
    pipeline, _, _ = make_pipeline(engine)
    reply = await pipeline.handle(frame("你好"))
    assert reply.kind == KIND_ERROR


async def test_unexpected_exception_becomes_error_message(caplog):
    engine = RecordingEngine(raises=RuntimeError("意外错误"))
    pipeline, _, _ = make_pipeline(engine)
    with caplog.at_level(logging.ERROR, logger="botkit.runtime"):
        reply = await pipeline.handle(frame("你好"))

    assert reply.kind == KIND_ERROR
    assert any(r.exc_info for r in caplog.records)


async def test_exception_group_subexceptions_are_logged(caplog):
    """
    TaskGroup 抛出的异常组，只打组本身看不出真正的错在哪，
    必须逐个展开。这段逻辑在排查 MCP/asyncio 问题时很关键。
    """
    eg = BaseExceptionGroup(
        "task group 挂了",
        [ValueError("第一个子错误"), KeyError("第二个子错误")],
    )
    engine = RecordingEngine(raises=eg)
    pipeline, _, _ = make_pipeline(engine)

    with caplog.at_level(logging.ERROR, logger="botkit.runtime"):
        reply = await pipeline.handle(frame("你好"))

    assert reply.kind == KIND_ERROR
    messages = " ".join(r.getMessage() for r in caplog.records)
    assert "第一个子错误" in messages
    assert "第二个子错误" in messages
    assert "ValueError" in messages
    assert "KeyError" in messages


async def test_nested_exception_group_is_unwrapped(caplog):
    inner = BaseExceptionGroup("内层", [ValueError("深处的错误")])
    outer = BaseExceptionGroup("外层", [inner])
    engine = RecordingEngine(raises=outer)
    pipeline, _, _ = make_pipeline(engine)

    with caplog.at_level(logging.ERROR, logger="botkit.runtime"):
        await pipeline.handle(frame("你好"))

    messages = " ".join(r.getMessage() for r in caplog.records)
    assert "深处的错误" in messages


async def test_error_does_not_save_history():
    engine = RecordingEngine(raises=LlmError("挂了"))
    pipeline, _, sessions = make_pipeline(engine)
    f = frame("你好")
    await pipeline.handle(f)
    assert sessions.get_history(sessions.key_for(f)) == []


async def test_identity_rejected_from_engine_is_handled(caplog):
    engine = RecordingEngine(raises=IdentityRejected("L220104", "^X"))
    pipeline, _, _ = make_pipeline(engine)
    with caplog.at_level(logging.ERROR, logger="botkit.runtime"):
        reply = await pipeline.handle(frame("你好"))
    assert reply.kind == KIND_IDENTITY_INVALID


async def test_pipeline_never_raises():
    """pipeline 不能抛异常 —— 机器人宁可说"出错了"也不能装死"""
    class Exploding:
        config = make_config()

        async def chat(self, *a, **kw):
            raise BaseException("非常严重的错误")

    pipeline, _, _ = make_pipeline(Exploding())
    # BaseException 不被 except Exception 捕获，确认这种极端情况的行为
    with pytest.raises(BaseException, match="非常严重"):
        await pipeline.handle(frame("你好"))


# --------------------------------------------------------------------------
# 单聊
# --------------------------------------------------------------------------


async def test_single_chat_works():
    pipeline, engine, _ = make_pipeline()
    reply = await pipeline.handle(
        frame("你好", chattype="single", chatid="")
    )
    assert reply.kind == KIND_LLM
    assert engine.calls[0]["identity"] == "L220104"


async def test_single_chat_session_key():
    pipeline, _, sessions = make_pipeline()
    f = frame("你好", chattype="single", chatid="")
    assert sessions.key_for(f) == "single:L220104"


# --------------------------------------------------------------------------
# 与真引擎联调
# --------------------------------------------------------------------------


async def test_pipeline_with_real_engine_end_to_end():
    """pipeline + 真 BotEngine + 假 LLM 后端 + in-memory MCP 走一遍完整链路"""
    import json

    backend = ScriptedBackend(
        [
            (
                None,
                [
                    {
                        "id": "c1",
                        "function": {
                            "name": "create_it_ticket",
                            "arguments": json.dumps(
                                {"work_code": "L999999", "title": "网络故障"}
                            ),
                        },
                    }
                ],
            ),
            ("工单已创建，单号 T001", []),
        ]
    )
    engine, _ = make_engine(backend)
    sessions = SessionStore.from_config(engine.config.session)
    pipeline = MessagePipeline(engine.config, engine, sessions, Hooks())

    progress = ProgressRecorder()
    reply = await pipeline.handle(
        frame("@IT助手 我电脑连不上网"), on_progress=progress
    )

    assert reply.kind == KIND_LLM
    assert "T001" in reply.text
    assert progress.labels[0] == "正在处理，请稍候..."
    # 身份被强制注入，工单提交人是可信身份而不是 LLM 编的
    tool_msg = backend.calls[1][0][-1]
    assert "提交人=L220104" in tool_msg["content"]
    # 历史存下来了
    assert sessions.turn_count(sessions.key_for(frame("x"))) == 1

# --------------------------------------------------------------------------
# intercepts（配置式前置拦截）
# --------------------------------------------------------------------------


async def test_intercept_exact_match_short_circuits_llm():
    pipeline, engine, _ = make_pipeline()
    pipeline.config.intercepts = [Intercept(reply="帮助内容", match=["帮助", "help"])]

    reply = await pipeline.handle(frame("@IT助手 帮助"))
    assert reply.kind == KIND_INTERCEPT
    assert reply.text == "帮助内容"
    assert engine.call_count == 0


async def test_intercept_regex_match():
    pipeline, engine, _ = make_pipeline()
    pipeline.config.intercepts = [
        Intercept(reply="在的", match_regex=r"^在吗[?？]?$")
    ]

    reply = await pipeline.handle(frame("在吗？"))
    assert reply.kind == KIND_INTERCEPT
    assert reply.text == "在的"
    assert engine.call_count == 0


async def test_intercept_miss_goes_to_llm():
    pipeline, engine, _ = make_pipeline()
    pipeline.config.intercepts = [Intercept(reply="帮助内容", match=["帮助"])]

    reply = await pipeline.handle(frame("我电脑坏了"))
    assert reply.kind == KIND_LLM
    assert engine.call_count == 1


async def test_intercept_runs_before_on_message_hook():
    """配置式拦截优先于 on_message hook"""
    def on_message(text, ctx):
        return "hook 回复"

    pipeline, _, _ = make_pipeline(hooks=Hooks({"on_message": on_message}))
    pipeline.config.intercepts = [Intercept(reply="配置拦截", match=["帮助"])]

    reply = await pipeline.handle(frame("帮助"))
    assert reply.kind == KIND_INTERCEPT
    assert reply.text == "配置拦截"


async def test_intercept_runs_after_reset_keyword():
    """重置指令优先于配置拦截，用户始终能清空对话"""
    pipeline, _, _ = make_pipeline()
    pipeline.config.intercepts = [Intercept(reply="拦下一切", match_regex=r".*")]

    reply = await pipeline.handle(frame("重置"))
    assert reply.kind == KIND_RESET


async def test_intercept_reply_not_saved_to_history():
    pipeline, _, sessions = make_pipeline()
    pipeline.config.intercepts = [Intercept(reply="帮助菜单", match=["帮助"])]

    f = frame("帮助")
    await pipeline.handle(f)
    assert sessions.get_history(sessions.key_for(f)) == []


# --------------------------------------------------------------------------
# reply_suffix（配置式回复后缀）
# --------------------------------------------------------------------------


async def test_reply_suffix_appended_to_llm_reply():
    msgs = MessagesConfig(reply_suffix="\n\n—— 小助手")
    pipeline, _, _ = make_pipeline(messages=msgs)

    reply = await pipeline.handle(frame("你好"))
    assert reply.kind == KIND_LLM
    assert reply.text == "引擎的回复\n\n—— 小助手"


async def test_reply_suffix_saved_to_history():
    msgs = MessagesConfig(reply_suffix=" [尾]")
    pipeline, _, sessions = make_pipeline(messages=msgs)

    f = frame("你好")
    await pipeline.handle(f)
    history = sessions.get_history(sessions.key_for(f))
    assert history[-1]["content"] == "引擎的回复 [尾]"


async def test_reply_suffix_not_appended_to_error():
    msgs = MessagesConfig(reply_suffix=" [尾]", error="出错了")
    engine = RecordingEngine(raises=LlmError("挂了"))
    pipeline, _, _ = make_pipeline(engine, messages=msgs)

    reply = await pipeline.handle(frame("你好"))
    assert reply.kind == KIND_ERROR
    assert reply.text == "出错了"


async def test_reply_suffix_then_before_reply_hook():
    """配置后缀先加，before_reply hook 后加（能进一步改写）"""
    msgs = MessagesConfig(reply_suffix=" [配置尾]")

    def before_reply(text, ctx):
        return text + " [hook尾]"

    pipeline, _, _ = make_pipeline(
        messages=msgs, hooks=Hooks({"before_reply": before_reply})
    )
    reply = await pipeline.handle(frame("你好"))
    assert reply.text == "引擎的回复 [配置尾] [hook尾]"
