"""CLI 与脚手架测试"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from botkit.cli import build_parser, main
from botkit.config import load_bot_config
from botkit.scaffold import (
    TEMPLATE_FILES,
    ScaffoldError,
    create_bot,
    render,
    validate_name,
)


# --------------------------------------------------------------------------
# 名字校验
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name", ["hr-assistant", "crm_bot", "bot1", "a", "My-Bot_2"]
)
def test_valid_names(name):
    assert validate_name(name) == name


@pytest.mark.parametrize(
    "name",
    [
        "",
        "   ",
        "1bot",  # 数字开头
        "my bot",  # 空格
        "bot/../etc",  # 路径穿越
        "bot.name",  # 点
        "机器人",  # 非 ASCII
        "-bot",
    ],
)
def test_invalid_names(name):
    with pytest.raises(ScaffoldError):
        validate_name(name)


def test_name_is_trimmed():
    assert validate_name("  hr-bot  ") == "hr-bot"


def test_invalid_name_error_gives_example():
    with pytest.raises(ScaffoldError) as ei:
        validate_name("my bot")
    assert "hr-assistant" in str(ei.value)


# --------------------------------------------------------------------------
# 模板渲染
# --------------------------------------------------------------------------


def test_render_replaces_placeholders():
    out = render("我是 {{BOT_NAME}}，在 {{BOT_DIR}}", "hr-bot", "bots/hr-bot")
    assert out == "我是 hr-bot，在 bots/hr-bot"


def test_no_placeholders_left_in_generated_files(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    target = create_bot("hr-assistant")
    for out_name in TEMPLATE_FILES.values():
        content = (target / out_name).read_text(encoding="utf-8")
        assert "{{" not in content, f"{out_name} 里还有未替换的占位符"


# --------------------------------------------------------------------------
# 生成骨架
# --------------------------------------------------------------------------


def test_creates_all_expected_files(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    target = create_bot("hr-assistant")

    assert target == (tmp_path / "bots" / "hr-assistant").resolve()
    for out_name in TEMPLATE_FILES.values():
        assert (target / out_name).is_file(), f"缺少 {out_name}"


def test_generated_bot_name_is_used(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    target = create_bot("hr-assistant")

    raw = yaml.safe_load((target / "bot.yaml").read_text(encoding="utf-8"))
    assert raw["name"] == "hr-assistant"
    assert "hr-assistant" in (target / "prompt.md").read_text(encoding="utf-8")
    assert "hr-assistant" in (target / "README.md").read_text(encoding="utf-8")


def test_custom_parent_dir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    target = create_bot("hr-bot", parent="my-bots")
    assert target == (tmp_path / "my-bots" / "hr-bot").resolve()
    assert (target / "bot.yaml").is_file()


def test_refuses_to_overwrite_non_empty_dir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    create_bot("hr-bot")
    with pytest.raises(ScaffoldError, match="已存在且不是空的"):
        create_bot("hr-bot")


def test_force_overwrites(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    target = create_bot("hr-bot")
    (target / "bot.yaml").write_text("被我改坏了", encoding="utf-8")

    create_bot("hr-bot", force=True)
    assert "被我改坏了" not in (target / "bot.yaml").read_text(encoding="utf-8")


def test_force_keeps_unrelated_files(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    target = create_bot("hr-bot")
    (target / ".env").write_text("WECHAT_BOT_ID=已经填好的\n", encoding="utf-8")

    create_bot("hr-bot", force=True)
    # 重新生成不该把已经填好的 .env 干掉
    assert "已经填好的" in (target / ".env").read_text(encoding="utf-8")


def test_empty_existing_dir_is_fine(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "bots" / "hr-bot").mkdir(parents=True)
    target = create_bot("hr-bot")
    assert (target / "bot.yaml").is_file()


# --------------------------------------------------------------------------
# 生成的骨架必须是可用的
# --------------------------------------------------------------------------


def test_generated_bot_yaml_is_valid_yaml(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    target = create_bot("hr-bot")
    raw = yaml.safe_load((target / "bot.yaml").read_text(encoding="utf-8"))
    assert isinstance(raw, dict)


def test_generated_bot_loads_after_filling_env(tmp_path, monkeypatch):
    """
    这是脚手架最重要的保证：生成出来的东西，填上密钥就能通过校验。
    模板和配置层一旦对不上（比如模板里写了个不存在的字段），这里就会红。
    """
    monkeypatch.chdir(tmp_path)
    target = create_bot("hr-bot")

    (target / ".env").write_text(
        "WECHAT_BOT_ID=bot-id-123\n"
        "WECHAT_BOT_SECRET=bot-secret-456\n"
        "LLM_API_URL=https://example.invalid/compatible-mode/v1\n"
        "LLM_API_KEY=sk-key-789\n"
        "LLM_MODEL=qwen3.7-plus\n"
        "MCP_URL=https://example.invalid/mcp\n",
        encoding="utf-8",
    )

    cfg = load_bot_config(target)
    assert cfg.name == "hr-bot"
    assert cfg.llm.client == "openai"
    assert cfg.wecom.bot_id == "bot-id-123"
    assert cfg.mcp.servers[0].url == "https://example.invalid/mcp"
    assert cfg.identity.pattern == r"^[LM]\d{6}$"
    assert cfg.session.scope == "group_user"
    assert "hr-bot" in cfg.messages.welcome
    # hooks.py 全注释掉，应该加载成"无 hook"
    assert cfg.hooks_file is not None


def test_generated_hooks_file_loads_with_no_hooks(tmp_path, monkeypatch):
    from botkit.hooks import load_hooks

    monkeypatch.chdir(tmp_path)
    target = create_bot("hr-bot")
    hooks = load_hooks(target / "hooks.py")
    assert hooks.implemented == []


def test_generated_env_example_covers_all_placeholders(tmp_path, monkeypatch):
    """bot.yaml 里用到的每个 ${VAR} 都得在 .env.example 里有"""
    import re

    monkeypatch.chdir(tmp_path)
    target = create_bot("hr-bot")

    yaml_text = (target / "bot.yaml").read_text(encoding="utf-8")
    env_text = (target / ".env.example").read_text(encoding="utf-8")

    # 只看没被注释掉的行
    active = "\n".join(
        line for line in yaml_text.splitlines() if not line.strip().startswith("#")
    )
    used = set(re.findall(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", active))
    declared = set(re.findall(r"^([A-Z_][A-Z0-9_]*)=", env_text, re.MULTILINE))

    assert used, "模板里应该有 ${VAR} 占位符"
    assert used <= declared, f".env.example 里缺少：{sorted(used - declared)}"


def test_generated_prompt_is_not_empty(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    target = create_bot("hr-bot")
    assert len((target / "prompt.md").read_text(encoding="utf-8").strip()) > 100


# --------------------------------------------------------------------------
# CLI 参数解析
# --------------------------------------------------------------------------


def test_parser_requires_subcommand():
    with pytest.raises(SystemExit):
        build_parser().parse_args([])


def test_parser_validate():
    args = build_parser().parse_args(["validate", "bots/hr-assistant"])
    assert args.command == "validate"
    assert args.bot_dir == "bots/hr-assistant"


def test_parser_run():
    args = build_parser().parse_args(["run", "bots/hr-assistant"])
    assert args.command == "run"
    assert args.bot_dir == "bots/hr-assistant"


def test_parser_ui_defaults():
    args = build_parser().parse_args(["ui"])
    assert args.command == "ui"
    assert args.host == "127.0.0.1"  # 默认只监听本机
    assert args.port == 8771
    assert args.no_browser is False


def test_parser_ui_options():
    args = build_parser().parse_args(
        ["ui", "--port", "9000", "--no-browser", "--root", "somewhere"]
    )
    assert args.port == 9000
    assert args.no_browser is True
    assert args.root == "somewhere"


def test_parser_new_defaults():
    args = build_parser().parse_args(["new", "hr-bot"])
    assert args.command == "new"
    assert args.name == "hr-bot"
    assert args.dir == "bots"
    assert args.force is False


def test_parser_new_options():
    args = build_parser().parse_args(["new", "hr-bot", "--dir", "custom", "--force"])
    assert args.dir == "custom"
    assert args.force is True


def test_unknown_subcommand():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["deploy"])


# --------------------------------------------------------------------------
# CLI 端到端
# --------------------------------------------------------------------------


def test_cli_new_then_validate(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)

    assert main(["new", "hr-bot"]) == 0
    out = capsys.readouterr().out
    assert "已生成 bot 骨架" in out
    assert "validate" in out  # 提示了下一步

    target = tmp_path / "bots" / "hr-bot"
    (target / ".env").write_text(
        "WECHAT_BOT_ID=id\nWECHAT_BOT_SECRET=secret\n"
        "LLM_API_URL=https://x.invalid/v1\nLLM_API_KEY=sk-key\n"
        "LLM_MODEL=m\nMCP_URL=https://x.invalid/mcp\n",
        encoding="utf-8",
    )

    assert main(["validate", "bots/hr-bot"]) == 0
    out = capsys.readouterr().out
    assert "配置校验通过" in out
    assert "hr-bot" in out


def test_cli_validate_reports_failure(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    main(["new", "hr-bot"])
    capsys.readouterr()

    # 没有 .env，占位符解析不了
    assert main(["validate", "bots/hr-bot"]) == 1
    err = capsys.readouterr().err
    assert "配置校验失败" in err
    assert "WECHAT_BOT_ID" in err


def test_cli_validate_masks_secrets(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    main(["new", "hr-bot"])
    target = tmp_path / "bots" / "hr-bot"
    (target / ".env").write_text(
        "WECHAT_BOT_ID=id\nWECHAT_BOT_SECRET=super-secret-value\n"
        "LLM_API_URL=https://x.invalid/v1\nLLM_API_KEY=sk-super-secret\n"
        "LLM_MODEL=m\nMCP_URL=https://x.invalid/mcp\n",
        encoding="utf-8",
    )
    capsys.readouterr()

    main(["validate", "bots/hr-bot"])
    out = capsys.readouterr().out
    assert "super-secret-value" not in out
    assert "sk-super-secret" not in out


def test_cli_new_duplicate_reports_error(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    main(["new", "hr-bot"])
    capsys.readouterr()

    assert main(["new", "hr-bot"]) == 1
    assert "生成失败" in capsys.readouterr().err


def test_cli_new_invalid_name_reports_error(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert main(["new", "1invalid"]) == 1
    assert "生成失败" in capsys.readouterr().err


def test_cli_validate_missing_dir(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert main(["validate", "nope"]) == 1
    assert "bot 目录不存在" in capsys.readouterr().err


# --------------------------------------------------------------------------
# 参考实现也要能过校验
# --------------------------------------------------------------------------


def test_bots_dir_ships_empty_but_present():
    """
    仓库里不带示例 bot（同事自己建），但 bots/ 目录本身要在 ——
    否则复制或 clone 之后第一条命令就找不到路径。
    """
    bots = Path(__file__).resolve().parent.parent / "bots"
    assert bots.is_dir()
    assert (bots / ".gitkeep").is_file(), "缺少 .gitkeep，空目录会在复制/git 时丢掉"
