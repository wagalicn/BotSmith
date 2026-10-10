"""Hook 加载器与降级行为测试"""

from __future__ import annotations

import logging
import textwrap
from pathlib import Path

import pytest

from botkit.hooks import HOOK_NAMES, HookContext, HookError, Hooks, load_hooks


@pytest.fixture
def hooks_file(tmp_path: Path):
    def _write(source: str, name: str = "hooks.py") -> Path:
        path = tmp_path / name
        path.write_text(textwrap.dedent(source), encoding="utf-8")
        return path

    return _write


def ctx() -> HookContext:
    return HookContext(identity="L220104", session_key="single:L220104")


# --------------------------------------------------------------------------
# 没有 hooks 文件
# --------------------------------------------------------------------------


async def test_no_hooks_file_uses_defaults():
    hooks = load_hooks(None)
    assert hooks.implemented == []
    assert await hooks.resolve_identity({}) is None
    assert await hooks.on_message("你好", ctx()) is None
    assert await hooks.before_tool_call("t", {"a": 1}, ctx()) == {"a": 1}
    assert await hooks.after_tool_call("t", "结果", ctx()) == "结果"
    assert await hooks.before_reply("回复", ctx()) == "回复"


async def test_missing_hooks_file_warns_and_uses_defaults(tmp_path, caplog):
    with caplog.at_level(logging.WARNING, logger="botkit.hooks"):
        hooks = load_hooks(tmp_path / "not-there.py")
    assert hooks.implemented == []
    assert any("not-there.py" in r.getMessage() for r in caplog.records)


async def test_empty_hooks_file_uses_defaults(hooks_file):
    hooks = load_hooks(hooks_file("# 什么都没写\n"))
    assert hooks.implemented == []
    assert await hooks.before_reply("原样", ctx()) == "原样"


async def test_all_commented_out_hooks_file(hooks_file):
    """脚手架生成的 hooks.py 就是这个样子，必须能正常跑"""
    hooks = load_hooks(
        hooks_file(
            """
            # def on_message(text, ctx):
            #     return "被注释掉了"
            """
        )
    )
    assert hooks.implemented == []
    assert await hooks.on_message("你好", ctx()) is None


# --------------------------------------------------------------------------
# 部分实现
# --------------------------------------------------------------------------


async def test_only_declared_hooks_are_overridden(hooks_file):
    hooks = load_hooks(
        hooks_file(
            """
            def before_reply(text, ctx):
                return text + "（加了签名）"
            """
        )
    )
    assert hooks.implemented == ["before_reply"]
    assert await hooks.before_reply("回复", ctx()) == "回复（加了签名）"
    # 没实现的还是默认行为
    assert await hooks.on_message("你好", ctx()) is None
    assert await hooks.after_tool_call("t", "结果", ctx()) == "结果"


async def test_all_five_hooks_can_be_implemented(hooks_file):
    hooks = load_hooks(
        hooks_file(
            """
            def resolve_identity(frame):
                return "L000001"

            def on_message(text, ctx):
                return None

            def before_tool_call(tool_name, args, ctx):
                return args

            def after_tool_call(tool_name, result, ctx):
                return result

            def before_reply(text, ctx):
                return text
            """
        )
    )
    assert hooks.implemented == list(HOOK_NAMES)
    for name in HOOK_NAMES:
        assert hooks.has(name)


async def test_source_is_recorded(hooks_file):
    path = hooks_file("def before_reply(text, ctx):\n    return text\n")
    hooks = load_hooks(path)
    assert str(path) in hooks.source


# --------------------------------------------------------------------------
# 同步 / 异步
# --------------------------------------------------------------------------


async def test_sync_hook(hooks_file):
    hooks = load_hooks(
        hooks_file(
            """
            def before_reply(text, ctx):
                return "同步：" + text
            """
        )
    )
    assert await hooks.before_reply("x", ctx()) == "同步：x"


async def test_async_hook(hooks_file):
    hooks = load_hooks(
        hooks_file(
            """
            import asyncio

            async def before_reply(text, ctx):
                await asyncio.sleep(0)
                return "异步：" + text
            """
        )
    )
    assert await hooks.before_reply("x", ctx()) == "异步：x"


async def test_mixed_sync_and_async_hooks(hooks_file):
    hooks = load_hooks(
        hooks_file(
            """
            def on_message(text, ctx):
                return None

            async def before_reply(text, ctx):
                return text.upper()
            """
        )
    )
    assert await hooks.on_message("hi", ctx()) is None
    assert await hooks.before_reply("hi", ctx()) == "HI"


# --------------------------------------------------------------------------
# on_message 短路
# --------------------------------------------------------------------------


async def test_on_message_short_circuits(hooks_file):
    hooks = load_hooks(
        hooks_file(
            """
            MENU = "我能帮你：1) 创建工单  2) 查询进度"

            def on_message(text, ctx):
                if text.strip() in ("帮助", "help", "?"):
                    return MENU
                return None
            """
        )
    )
    assert await hooks.on_message("帮助", ctx()) == "我能帮你：1) 创建工单  2) 查询进度"
    assert await hooks.on_message("help", ctx()) == "我能帮你：1) 创建工单  2) 查询进度"
    assert await hooks.on_message("我电脑坏了", ctx()) is None


async def test_on_message_receives_context(hooks_file):
    hooks = load_hooks(
        hooks_file(
            """
            def on_message(text, ctx):
                return f"{ctx.identity} 说了 {text}"
            """
        )
    )
    assert await hooks.on_message("你好", ctx()) == "L220104 说了 你好"


# --------------------------------------------------------------------------
# 异常降级
# --------------------------------------------------------------------------


async def test_failing_resolve_identity_degrades(hooks_file, caplog):
    hooks = load_hooks(
        hooks_file(
            """
            def resolve_identity(frame):
                raise RuntimeError("查表挂了")
            """
        )
    )
    with caplog.at_level(logging.ERROR, logger="botkit.hooks"):
        assert await hooks.resolve_identity({}) is None
    assert any("resolve_identity" in r.getMessage() for r in caplog.records)
    # 堆栈要在日志里，否则没法定位
    assert any(r.exc_info for r in caplog.records)


async def test_failing_on_message_continues_normal_flow(hooks_file, caplog):
    hooks = load_hooks(
        hooks_file(
            """
            def on_message(text, ctx):
                raise ValueError("拦截逻辑写错了")
            """
        )
    )
    with caplog.at_level(logging.ERROR, logger="botkit.hooks"):
        assert await hooks.on_message("你好", ctx()) is None
    assert any("on_message" in r.getMessage() for r in caplog.records)


async def test_failing_before_tool_call_uses_original_args(hooks_file, caplog):
    hooks = load_hooks(
        hooks_file(
            """
            def before_tool_call(tool_name, args, ctx):
                raise KeyError("少了个字段")
            """
        )
    )
    with caplog.at_level(logging.ERROR, logger="botkit.hooks"):
        assert await hooks.before_tool_call("t", {"a": 1}, ctx()) == {"a": 1}


async def test_failing_after_tool_call_uses_original_result(hooks_file, caplog):
    hooks = load_hooks(
        hooks_file(
            """
            def after_tool_call(tool_name, result, ctx):
                raise RuntimeError("加工挂了")
            """
        )
    )
    with caplog.at_level(logging.ERROR, logger="botkit.hooks"):
        assert await hooks.after_tool_call("t", "原结果", ctx()) == "原结果"


async def test_failing_before_reply_uses_original_text(hooks_file, caplog):
    hooks = load_hooks(
        hooks_file(
            """
            def before_reply(text, ctx):
                raise RuntimeError("格式化挂了")
            """
        )
    )
    with caplog.at_level(logging.ERROR, logger="botkit.hooks"):
        assert await hooks.before_reply("原回复", ctx()) == "原回复"


async def test_failing_async_hook_degrades(hooks_file, caplog):
    hooks = load_hooks(
        hooks_file(
            """
            async def before_reply(text, ctx):
                raise RuntimeError("异步 hook 挂了")
            """
        )
    )
    with caplog.at_level(logging.ERROR, logger="botkit.hooks"):
        assert await hooks.before_reply("原回复", ctx()) == "原回复"


# --------------------------------------------------------------------------
# 返回值类型不对
# --------------------------------------------------------------------------


async def test_wrong_return_type_from_before_tool_call(hooks_file, caplog):
    hooks = load_hooks(
        hooks_file(
            """
            def before_tool_call(tool_name, args, ctx):
                return "应该返回 dict 的"
            """
        )
    )
    with caplog.at_level(logging.ERROR, logger="botkit.hooks"):
        assert await hooks.before_tool_call("t", {"a": 1}, ctx()) == {"a": 1}
    assert any("应该返回 dict" in r.getMessage() for r in caplog.records)


async def test_wrong_return_type_from_before_reply(hooks_file, caplog):
    hooks = load_hooks(
        hooks_file(
            """
            def before_reply(text, ctx):
                return 12345
            """
        )
    )
    with caplog.at_level(logging.ERROR, logger="botkit.hooks"):
        assert await hooks.before_reply("原回复", ctx()) == "原回复"


async def test_wrong_return_type_from_on_message(hooks_file, caplog):
    hooks = load_hooks(
        hooks_file(
            """
            def on_message(text, ctx):
                return ["列表不行"]
            """
        )
    )
    with caplog.at_level(logging.ERROR, logger="botkit.hooks"):
        assert await hooks.on_message("你好", ctx()) is None


async def test_wrong_return_type_from_resolve_identity(hooks_file, caplog):
    hooks = load_hooks(
        hooks_file(
            """
            def resolve_identity(frame):
                return 220104
            """
        )
    )
    with caplog.at_level(logging.ERROR, logger="botkit.hooks"):
        assert await hooks.resolve_identity({}) is None


async def test_wrong_return_type_from_after_tool_call(hooks_file, caplog):
    hooks = load_hooks(
        hooks_file(
            """
            def after_tool_call(tool_name, result, ctx):
                return {"不是": "字符串"}
            """
        )
    )
    with caplog.at_level(logging.ERROR, logger="botkit.hooks"):
        assert await hooks.after_tool_call("t", "原结果", ctx()) == "原结果"


# --------------------------------------------------------------------------
# 加载失败
# --------------------------------------------------------------------------


def test_syntax_error_raises_hook_error(hooks_file):
    """文件明明存在却加载不了，静默跳过只会让人更难查"""
    with pytest.raises(HookError) as ei:
        load_hooks(hooks_file("def broken(\n"))
    assert "hooks" in str(ei.value)
    assert "SyntaxError" in str(ei.value)


def test_import_error_raises_hook_error(hooks_file):
    with pytest.raises(HookError) as ei:
        load_hooks(hooks_file("import 这个模块不存在\n"))
    assert "hooks" in str(ei.value)


def test_missing_dependency_raises_hook_error(hooks_file):
    with pytest.raises(HookError, match="ModuleNotFoundError"):
        load_hooks(hooks_file("import a_package_that_does_not_exist_12345\n"))


def test_error_at_module_level_raises_hook_error(hooks_file):
    with pytest.raises(HookError, match="ZeroDivisionError"):
        load_hooks(hooks_file("X = 1 / 0\n"))


# --------------------------------------------------------------------------
# 拼错名字的提示
# --------------------------------------------------------------------------


def test_typo_in_hook_name_warns(hooks_file, caplog):
    """写了 on_messages（多个 s）这类拼错，要提示不会被调用"""
    with caplog.at_level(logging.WARNING, logger="botkit.hooks"):
        hooks = load_hooks(
            hooks_file(
                """
                def on_messages(text, ctx):
                    return "拼错了"
                """
            )
        )
    assert hooks.implemented == []
    messages = " ".join(r.getMessage() for r in caplog.records)
    assert "on_messages" in messages
    assert "on_message" in messages  # 同时列出正确的名字


def test_private_helpers_do_not_warn(hooks_file, caplog):
    with caplog.at_level(logging.WARNING, logger="botkit.hooks"):
        hooks = load_hooks(
            hooks_file(
                """
                def _lookup(userid):
                    return userid

                def resolve_identity(frame):
                    return _lookup("L000001")
                """
            )
        )
    assert hooks.implemented == ["resolve_identity"]
    assert not any("_lookup" in r.getMessage() for r in caplog.records)


def test_imported_functions_do_not_warn(hooks_file, caplog):
    with caplog.at_level(logging.WARNING, logger="botkit.hooks"):
        load_hooks(
            hooks_file(
                """
                from json import dumps

                def before_reply(text, ctx):
                    return text
                """
            )
        )
    assert not any("dumps" in r.getMessage() for r in caplog.records)


def test_non_callable_with_hook_name_is_ignored(hooks_file, caplog):
    with caplog.at_level(logging.ERROR, logger="botkit.hooks"):
        hooks = load_hooks(hooks_file("on_message = '不是函数'\n"))
    assert hooks.implemented == []
    assert any("不是函数" in r.getMessage() for r in caplog.records)


# --------------------------------------------------------------------------
# 多个 bot 各自的 hooks 不互相污染
# --------------------------------------------------------------------------


def test_no_pycache_left_in_bot_dir(hooks_file, tmp_path):
    """
    bot 目录是给同事看的，应该只有那几个配置文件。
    加载 hooks.py 不该在里面留下 __pycache__。
    """
    path = hooks_file("def before_reply(text, ctx):\n    return text\n")
    load_hooks(path)
    assert not (tmp_path / "__pycache__").exists()


def test_bytecode_setting_is_restored(hooks_file):
    """改了全局的 dont_write_bytecode 必须还原，别影响进程里其他 import"""
    import sys

    before = sys.dont_write_bytecode
    load_hooks(hooks_file("def before_reply(text, ctx):\n    return text\n"))
    assert sys.dont_write_bytecode == before


def test_bytecode_setting_restored_on_error(hooks_file):
    import sys

    before = sys.dont_write_bytecode
    with pytest.raises(HookError):
        load_hooks(hooks_file("X = 1 / 0\n"))
    assert sys.dont_write_bytecode == before


def test_two_bots_with_same_filename_do_not_collide(tmp_path):
    bot_a = tmp_path / "bot_a"
    bot_b = tmp_path / "bot_b"
    bot_a.mkdir()
    bot_b.mkdir()
    (bot_a / "hooks.py").write_text(
        "def before_reply(text, ctx):\n    return 'A:' + text\n", encoding="utf-8"
    )
    (bot_b / "hooks.py").write_text(
        "def before_reply(text, ctx):\n    return 'B:' + text\n", encoding="utf-8"
    )

    hooks_a = load_hooks(bot_a / "hooks.py")
    hooks_b = load_hooks(bot_b / "hooks.py")
    assert hooks_a._impls["before_reply"]("x", None) == "A:x"
    assert hooks_b._impls["before_reply"]("x", None) == "B:x"


# --------------------------------------------------------------------------
# 直接构造 Hooks
# --------------------------------------------------------------------------


async def test_hooks_can_be_constructed_directly():
    hooks = Hooks({"before_reply": lambda text, ctx: text + "!"})
    assert await hooks.before_reply("hi", ctx()) == "hi!"
    assert hooks.implemented == ["before_reply"]


async def test_hook_context_defaults():
    c = HookContext()
    assert c.identity is None
    assert c.session_key is None
    assert c.config is None
    assert c.frame is None
    assert c.logger is not None


# --------------------------------------------------------------------------
# register_routes 与 base_path（门户防返工：路由前缀）
# --------------------------------------------------------------------------


def test_no_register_routes_means_no_routes():
    """没实现 register_routes 的 bot：has_routes 为假，调用是空操作"""
    hooks = Hooks()
    assert hooks.has_routes is False
    # 不该抛异常
    hooks.register_routes(app="app", config="cfg", pipeline="pipe", base_path="/x")


def test_legacy_three_arg_register_routes_called_without_base_path():
    """
    老 bot 的 register_routes(app, config, pipeline) 三参签名：
    即使框架传了 base_path，也必须按老的三参调用，行为完全不变。
    """
    calls = []

    def register_routes(app, config, pipeline):
        calls.append((app, config, pipeline))

    hooks = Hooks(register_routes=register_routes)
    assert hooks.has_routes is True

    # 框架传了 base_path，但老签名接不到，应被静默忽略（按三参调用）
    hooks.register_routes(app="A", config="C", pipeline="P", base_path="/agent")
    assert calls == [("A", "C", "P")]


def test_new_register_routes_receives_base_path():
    """新 bot 显式声明 base_path 形参时，框架应把前缀传进去"""
    captured = {}

    def register_routes(app, config, pipeline, base_path=""):
        captured["base_path"] = base_path
        captured["args"] = (app, config, pipeline)

    hooks = Hooks(register_routes=register_routes)
    hooks.register_routes(app="A", config="C", pipeline="P", base_path="/agent")
    assert captured["base_path"] == "/agent"
    assert captured["args"] == ("A", "C", "P")


def test_new_register_routes_default_base_path_is_empty():
    """新 bot 在单 Agent 模式下（框架不传 base_path）拿到的前缀是空串"""
    captured = {}

    def register_routes(app, config, pipeline, base_path=""):
        captured["base_path"] = base_path

    hooks = Hooks(register_routes=register_routes)
    hooks.register_routes(app="A", config="C", pipeline="P")
    assert captured["base_path"] == ""


def test_register_routes_with_var_keyword_receives_base_path():
    """带 **kwargs 的 register_routes 也应能收到 base_path"""
    captured = {}

    def register_routes(app, config, pipeline, **kwargs):
        captured.update(kwargs)

    hooks = Hooks(register_routes=register_routes)
    hooks.register_routes(app="A", config="C", pipeline="P", base_path="/agent")
    assert captured.get("base_path") == "/agent"


def test_accepts_base_path_detection():
    """_accepts_base_path 的直接单测：覆盖声明、未声明、**kwargs 三种情况"""
    from botkit.hooks import _accepts_base_path

    assert _accepts_base_path(lambda app, config, pipeline: None) is False
    assert _accepts_base_path(lambda app, config, pipeline, base_path="": None) is True
    assert _accepts_base_path(lambda app, config, pipeline, **kw: None) is True


def test_register_routes_loaded_from_file_with_base_path(hooks_file):
    """
    端到端：从 hooks.py 文件加载一个声明了 base_path 的 register_routes，
    确认 load_hooks → Hooks.register_routes 整条链路把前缀透传进去。
    """
    hooks = load_hooks(
        hooks_file(
            """
            _seen = {}

            def register_routes(app, config, pipeline, base_path=""):
                _seen["base_path"] = base_path
                app.setdefault("routes", []).append(base_path + "/analyze")

            def get_seen():
                return _seen
            """
        )
    )
    assert hooks.has_routes is True
    fake_app: dict = {}
    hooks.register_routes(app=fake_app, config=None, pipeline=None, base_path="/demand")
    assert fake_app["routes"] == ["/demand/analyze"]
