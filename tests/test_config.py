"""配置加载层测试"""

from __future__ import annotations

from pathlib import Path

import pytest

from botkit.config import (
    ConfigError,
    MessagesConfig,
    config_summary,
    load_bot_config,
    mask_secret,
)
from conftest import MINIMAL_YAML


# --------------------------------------------------------------------------
# 正常加载
# --------------------------------------------------------------------------


def test_loads_minimal_config(bot_dir_factory, base_env):
    bot_dir = bot_dir_factory()
    cfg = load_bot_config(bot_dir)

    assert cfg.name == "test-bot"
    assert cfg.bot_dir == bot_dir.resolve()
    assert cfg.llm.model == "test-model"
    assert cfg.llm.client == "openai"  # 默认后端
    assert cfg.prompt_text == "你是一个测试机器人。"
    assert len(cfg.mcp.servers) == 1
    assert cfg.mcp.servers[0].name == "main"


def test_env_placeholders_are_substituted(bot_dir_factory, base_env):
    cfg = load_bot_config(bot_dir_factory())
    assert cfg.wecom.bot_id == "bot-id-1234"
    assert cfg.wecom.bot_secret == "bot-secret-5678"
    assert cfg.llm.api_key == "sk-test-key"


def test_placeholder_embedded_in_longer_string(bot_dir_factory, base_env):
    base_env.setenv("MCP_HOST", "mcp.internal")
    yaml_text = MINIMAL_YAML.replace(
        "url: https://example.invalid/mcp", "url: https://${MCP_HOST}:8081/mcp"
    )
    cfg = load_bot_config(bot_dir_factory(yaml_text=yaml_text))
    assert cfg.mcp.servers[0].url == "https://mcp.internal:8081/mcp"


def test_defaults_are_applied(bot_dir_factory, base_env):
    cfg = load_bot_config(bot_dir_factory())
    assert cfg.session.scope == "group_user"
    assert cfg.session.max_turns == 10
    assert cfg.session.ttl_seconds == 1800
    assert cfg.llm.max_tool_rounds == 6
    assert cfg.llm.timeout_seconds == 60.0
    assert cfg.log_level == "INFO"
    assert "重新开始" in cfg.messages.reset_keywords
    assert cfg.identity.source == "wecom_userid"
    assert cfg.identity.pattern is None
    assert cfg.identity.inject == []


# --------------------------------------------------------------------------
# 缺失环境变量
# --------------------------------------------------------------------------


def test_missing_env_var_reports_field_path(bot_dir_factory, base_env):
    base_env.delenv("TEST_LLM_KEY", raising=False)
    with pytest.raises(ConfigError) as ei:
        load_bot_config(bot_dir_factory())
    msg = str(ei.value)
    assert "llm.api_key" in msg
    assert "TEST_LLM_KEY" in msg


def test_all_missing_env_vars_reported_at_once(bot_dir_factory, base_env):
    base_env.delenv("TEST_BOT_ID", raising=False)
    base_env.delenv("TEST_LLM_KEY", raising=False)
    with pytest.raises(ConfigError) as ei:
        load_bot_config(bot_dir_factory())
    msg = str(ei.value)
    # 两个问题一次性都报出来，不用改一个跑一次
    assert "TEST_BOT_ID" in msg
    assert "TEST_LLM_KEY" in msg
    assert "wecom.bot_id" in msg
    assert "llm.api_key" in msg


def test_process_env_wins_over_dotenv(bot_dir_factory, monkeypatch):
    """容器/CI 注入的环境变量优先于 .env 文件"""
    monkeypatch.setenv("TEST_BOT_ID", "来自环境变量")
    monkeypatch.setenv("TEST_BOT_SECRET", "s")
    monkeypatch.setenv("TEST_LLM_KEY", "k")
    bot_dir = bot_dir_factory(files={".env": "TEST_BOT_ID=来自文件\n"})
    cfg = load_bot_config(bot_dir)
    assert cfg.wecom.bot_id == "来自环境变量"


def test_shadowed_dotenv_vars_are_reported(bot_dir_factory, monkeypatch, caplog):
    """
    进程环境盖住 .env 是有意设计（方便容器注入），但必须说出来
    —— 否则 shell 里残留一个同名变量，人改了 .env 却不生效，很难查。
    """
    monkeypatch.setenv("TEST_BOT_ID", "残留的旧值")
    monkeypatch.setenv("TEST_BOT_SECRET", "s")
    monkeypatch.setenv("TEST_LLM_KEY", "k")
    bot_dir = bot_dir_factory(files={".env": "TEST_BOT_ID=我刚改的新值\n"})

    with caplog.at_level("WARNING", logger="botkit.config"):
        load_bot_config(bot_dir)

    messages = " ".join(r.getMessage() for r in caplog.records)
    assert "TEST_BOT_ID" in messages
    assert "盖住" in messages


def test_no_warning_when_dotenv_value_matches_env(bot_dir_factory, monkeypatch, caplog):
    monkeypatch.setenv("TEST_BOT_ID", "一样的值")
    monkeypatch.setenv("TEST_BOT_SECRET", "s")
    monkeypatch.setenv("TEST_LLM_KEY", "k")
    bot_dir = bot_dir_factory(files={".env": "TEST_BOT_ID=一样的值\n"})

    with caplog.at_level("WARNING", logger="botkit.config"):
        load_bot_config(bot_dir)

    assert not any("盖住" in r.getMessage() for r in caplog.records)


def test_dotenv_file_is_loaded(bot_dir_factory, monkeypatch):
    monkeypatch.delenv("TEST_BOT_ID", raising=False)
    monkeypatch.delenv("TEST_BOT_SECRET", raising=False)
    monkeypatch.delenv("TEST_LLM_KEY", raising=False)
    bot_dir = bot_dir_factory(
        files={
            ".env": """\
            TEST_BOT_ID=from-dotenv-id
            TEST_BOT_SECRET=from-dotenv-secret
            TEST_LLM_KEY=from-dotenv-key
            """
        }
    )
    cfg = load_bot_config(bot_dir)
    assert cfg.wecom.bot_id == "from-dotenv-id"
    assert cfg.llm.api_key == "from-dotenv-key"


# --------------------------------------------------------------------------
# 相对路径解析
# --------------------------------------------------------------------------


def test_prompt_file_resolved_relative_to_bot_yaml(bot_dir_factory, base_env):
    yaml_text = MINIMAL_YAML.replace("file: prompt.md", "file: prompts/main.md")
    bot_dir = bot_dir_factory(
        yaml_text=yaml_text,
        prompt=None,
        files={"prompts/main.md": "子目录里的提示词"},
    )
    cfg = load_bot_config(bot_dir)
    assert cfg.prompt_text == "子目录里的提示词"
    assert cfg.prompt_source.endswith("main.md")


def test_prompt_file_relative_to_bot_dir_not_cwd(bot_dir_factory, base_env, monkeypatch):
    """cwd 变了也不影响解析，路径始终相对 bot.yaml"""
    bot_dir = bot_dir_factory()
    monkeypatch.chdir(bot_dir.parent)
    cfg = load_bot_config(bot_dir)
    assert cfg.prompt_text == "你是一个测试机器人。"


def test_missing_prompt_file_reports_path(bot_dir_factory, base_env):
    bot_dir = bot_dir_factory(prompt=None)
    with pytest.raises(ConfigError, match="prompt.file 指向的文件不存在"):
        load_bot_config(bot_dir)


def test_inline_prompt(bot_dir_factory, base_env):
    yaml_text = MINIMAL_YAML.replace(
        "prompt:\n  file: prompt.md", "prompt:\n  inline: 内联提示词内容"
    )
    cfg = load_bot_config(bot_dir_factory(yaml_text=yaml_text, prompt=None))
    assert cfg.prompt_text == "内联提示词内容"
    assert cfg.prompt_source == "inline"


def test_prompt_file_and_inline_conflict(bot_dir_factory, base_env):
    yaml_text = MINIMAL_YAML.replace(
        "prompt:\n  file: prompt.md", "prompt:\n  file: prompt.md\n  inline: 也写了内联"
    )
    with pytest.raises(ConfigError, match="只能配一个"):
        load_bot_config(bot_dir_factory(yaml_text=yaml_text))


def test_empty_prompt_file_rejected(bot_dir_factory, base_env):
    with pytest.raises(ConfigError, match="提示词文件是空的"):
        load_bot_config(bot_dir_factory(prompt="   \n  "))


# --------------------------------------------------------------------------
# 枚举值校验
# --------------------------------------------------------------------------


def test_invalid_session_scope(bot_dir_factory, base_env):
    yaml_text = MINIMAL_YAML + "\nsession:\n  scope: per_person\n"
    with pytest.raises(ConfigError) as ei:
        load_bot_config(bot_dir_factory(yaml_text=yaml_text))
    msg = str(ei.value)
    assert "session.scope" in msg
    assert "group_user" in msg  # 报错里列出可用值


def test_invalid_llm_client(bot_dir_factory, base_env):
    yaml_text = MINIMAL_YAML.replace(
        "llm:\n  api_url:", "llm:\n  client: httpx\n  api_url:"
    )
    with pytest.raises(ConfigError) as ei:
        load_bot_config(bot_dir_factory(yaml_text=yaml_text))
    msg = str(ei.value)
    assert "llm.client" in msg
    assert "openai" in msg and "aiohttp" in msg


def test_valid_llm_clients_accepted(bot_dir_factory, base_env):
    for client in ("openai", "aiohttp"):
        yaml_text = MINIMAL_YAML.replace(
            "llm:\n  api_url:", f"llm:\n  client: {client}\n  api_url:"
        )
        cfg = load_bot_config(bot_dir_factory(yaml_text=yaml_text, dir_name=f"b-{client}"))
        assert cfg.llm.client == client


def test_invalid_log_level(bot_dir_factory, base_env):
    yaml_text = MINIMAL_YAML + "\nlogging:\n  level: VERBOSE\n"
    with pytest.raises(ConfigError, match="logging.level"):
        load_bot_config(bot_dir_factory(yaml_text=yaml_text))


def test_log_level_is_case_insensitive(bot_dir_factory, base_env):
    yaml_text = MINIMAL_YAML + "\nlogging:\n  level: debug\n"
    cfg = load_bot_config(bot_dir_factory(yaml_text=yaml_text))
    assert cfg.log_level == "DEBUG"


def test_openai_client_rejects_chat_completions_url(bot_dir_factory, base_env):
    """openai 后端要 base_url，填完整端点是最容易犯的错，要给出可操作的提示"""
    yaml_text = MINIMAL_YAML.replace(
        "api_url: https://example.invalid/compatible-mode/v1",
        "api_url: https://example.invalid/compatible-mode/v1/chat/completions",
    )
    with pytest.raises(ConfigError) as ei:
        load_bot_config(bot_dir_factory(yaml_text=yaml_text))
    msg = str(ei.value)
    assert "/v1" in msg
    assert "aiohttp" in msg  # 提示另一个后端接受完整 URL


def test_aiohttp_client_accepts_full_url(bot_dir_factory, base_env):
    yaml_text = MINIMAL_YAML.replace(
        "llm:\n  api_url: https://example.invalid/compatible-mode/v1",
        "llm:\n  client: aiohttp\n  api_url: https://example.invalid/compatible-mode/v1/chat/completions",
    )
    cfg = load_bot_config(bot_dir_factory(yaml_text=yaml_text))
    assert cfg.llm.api_url.endswith("/chat/completions")


# --------------------------------------------------------------------------
# 必填与类型
# --------------------------------------------------------------------------


def test_missing_mcp_section_allowed(bot_dir_factory, base_env):
    """mcp 段可选：整段省略 = 纯 LLM 对话，不接工具，servers 为空"""
    yaml_text = MINIMAL_YAML.split("mcp:")[0]
    cfg = load_bot_config(bot_dir_factory(yaml_text=yaml_text))
    assert cfg.mcp.servers == []


def test_empty_mcp_servers_allowed(bot_dir_factory, base_env):
    """显式写了 mcp.servers: [] 同样合法，等价于不接任何工具"""
    yaml_text = MINIMAL_YAML.split("mcp:")[0] + "mcp:\n  servers: []\n"
    cfg = load_bot_config(bot_dir_factory(yaml_text=yaml_text))
    assert cfg.mcp.servers == []


def test_mcp_server_without_url_skipped(bot_dir_factory, base_env):
    """没填 url 的 server 视为空行跳过：页面预置的空服务端行不该拦住加载"""
    yaml_text = MINIMAL_YAML.split("mcp:")[0] + (
        "mcp:\n"
        "  servers:\n"
        "    - name: MCP\n"
        '      url: ""\n'
    )
    cfg = load_bot_config(bot_dir_factory(yaml_text=yaml_text))
    assert cfg.mcp.servers == []


def test_mcp_mixes_configured_and_empty_server(bot_dir_factory, base_env):
    """一个配好的 + 一个空行：只保留配好的那个"""
    yaml_text = MINIMAL_YAML.split("mcp:")[0] + (
        "mcp:\n"
        "  servers:\n"
        "    - name: main\n"
        "      url: https://example.invalid/mcp\n"
        "    - name: empty\n"
        '      url: ""\n'
    )
    cfg = load_bot_config(bot_dir_factory(yaml_text=yaml_text))
    assert [s.name for s in cfg.mcp.servers] == ["main"]


def test_duplicate_mcp_server_name_rejected(bot_dir_factory, base_env):
    yaml_text = MINIMAL_YAML + """
    - name: main
      url: https://other.invalid/mcp
"""
    with pytest.raises(ConfigError, match="重复"):
        load_bot_config(bot_dir_factory(yaml_text=yaml_text))


def test_unknown_top_level_key_rejected(bot_dir_factory, base_env):
    """写错的 key 要报错，不能静默忽略（否则同事以为配了其实没生效）"""
    yaml_text = MINIMAL_YAML + "\nmessage:\n  welcome: 拼错了应该是 messages\n"
    with pytest.raises(ConfigError) as ei:
        load_bot_config(bot_dir_factory(yaml_text=yaml_text))
    msg = str(ei.value)
    assert "message" in msg
    assert "messages" in msg  # 列出正确的可用项


def test_unknown_nested_key_rejected(bot_dir_factory, base_env):
    yaml_text = MINIMAL_YAML + "\nsession:\n  max_turn: 5\n"
    with pytest.raises(ConfigError) as ei:
        load_bot_config(bot_dir_factory(yaml_text=yaml_text))
    assert "max_turn" in str(ei.value)


def test_non_positive_numbers_rejected(bot_dir_factory, base_env):
    yaml_text = MINIMAL_YAML + "\nsession:\n  max_turns: 0\n"
    with pytest.raises(ConfigError, match="必须大于 0"):
        load_bot_config(bot_dir_factory(yaml_text=yaml_text))


def test_wrong_type_reports_actual_type(bot_dir_factory, base_env):
    yaml_text = MINIMAL_YAML + "\nsession:\n  max_turns: 十\n"
    with pytest.raises(ConfigError, match="应该是整数"):
        load_bot_config(bot_dir_factory(yaml_text=yaml_text))


def test_missing_bot_yaml(tmp_path: Path):
    empty = tmp_path / "nothing"
    empty.mkdir()
    with pytest.raises(ConfigError, match="没有 bot.yaml"):
        load_bot_config(empty)


def test_missing_bot_dir(tmp_path: Path):
    with pytest.raises(ConfigError, match="bot 目录不存在"):
        load_bot_config(tmp_path / "does-not-exist")


def test_invalid_yaml_syntax(bot_dir_factory, base_env):
    with pytest.raises(ConfigError, match="不是合法的 YAML"):
        load_bot_config(bot_dir_factory(yaml_text="name: [unclosed\n"))


# --------------------------------------------------------------------------
# identity
# --------------------------------------------------------------------------


def test_identity_inject_and_pattern(bot_dir_factory, base_env):
    yaml_text = (
        MINIMAL_YAML
        + """
identity:
  pattern: "^[LM]\\\\d{6}$"
  inject:
    - tool: create_it_ticket
      arg: work_code
"""
    )
    cfg = load_bot_config(bot_dir_factory(yaml_text=yaml_text))
    assert cfg.identity.pattern == r"^[LM]\d{6}$"
    assert cfg.identity.inject[0].tool == "create_it_ticket"
    assert cfg.identity.inject[0].arg == "work_code"
    assert cfg.inject_for("create_it_ticket") == ["work_code"]
    assert cfg.inject_for("get_system_config") == []


def test_invalid_identity_pattern(bot_dir_factory, base_env):
    yaml_text = MINIMAL_YAML + '\nidentity:\n  pattern: "[unclosed"\n'
    with pytest.raises(ConfigError, match="不是合法的正则"):
        load_bot_config(bot_dir_factory(yaml_text=yaml_text))


def test_identity_inject_requires_tool_and_arg(bot_dir_factory, base_env):
    yaml_text = MINIMAL_YAML + "\nidentity:\n  inject:\n    - tool: only_tool\n"
    with pytest.raises(ConfigError, match="identity.inject\\[0\\].arg"):
        load_bot_config(bot_dir_factory(yaml_text=yaml_text))


# --------------------------------------------------------------------------
# messages
# --------------------------------------------------------------------------


def test_messages_partial_override_keeps_defaults(bot_dir_factory, base_env):
    yaml_text = MINIMAL_YAML + '\nmessages:\n  welcome: "自定义欢迎语"\n'
    cfg = load_bot_config(bot_dir_factory(yaml_text=yaml_text))
    assert cfg.messages.welcome == "自定义欢迎语"
    # 没覆盖的保留默认
    assert cfg.messages.error == MessagesConfig().error
    assert cfg.messages.reset_keywords == MessagesConfig().reset_keywords


def test_tool_progress_fallback_to_default(bot_dir_factory, base_env):
    yaml_text = (
        MINIMAL_YAML
        + """
messages:
  tool_progress:
    create_it_ticket: "正在创建工单..."
"""
    )
    cfg = load_bot_config(bot_dir_factory(yaml_text=yaml_text))
    assert cfg.messages.progress_for("create_it_ticket") == "正在创建工单..."
    # 未配置的工具走 default，default 未显式配置时自动补上
    assert cfg.messages.progress_for("unknown_tool") == "正在调用工具..."


def test_tool_progress_custom_default(bot_dir_factory, base_env):
    yaml_text = (
        MINIMAL_YAML
        + """
messages:
  tool_progress:
    default: "查询中，稍等"
"""
    )
    cfg = load_bot_config(bot_dir_factory(yaml_text=yaml_text))
    assert cfg.messages.progress_for("whatever") == "查询中，稍等"


def test_reply_suffix_parsed_and_keeps_leading_newlines(bot_dir_factory, base_env):
    """reply_suffix 不被 strip，前导换行要保留（署名常带换行）"""
    yaml_text = MINIMAL_YAML + '\nmessages:\n  reply_suffix: "\\n\\n—— 小助手"\n'
    cfg = load_bot_config(bot_dir_factory(yaml_text=yaml_text))
    assert cfg.messages.reply_suffix == "\n\n—— 小助手"


def test_reply_suffix_defaults_empty(bot_dir_factory, base_env):
    cfg = load_bot_config(bot_dir_factory(yaml_text=MINIMAL_YAML))
    assert cfg.messages.reply_suffix == ""


# --------------------------------------------------------------------------
# intercepts
# --------------------------------------------------------------------------


def test_intercepts_defaults_empty(bot_dir_factory, base_env):
    cfg = load_bot_config(bot_dir_factory(yaml_text=MINIMAL_YAML))
    assert cfg.intercepts == []


def test_intercepts_parsed(bot_dir_factory, base_env):
    yaml_text = (
        MINIMAL_YAML
        + """
intercepts:
  - match: ["帮助", "help"]
    reply: "帮助内容"
  - match_regex: "^在吗[?？]?$"
    reply: "在的"
"""
    )
    cfg = load_bot_config(bot_dir_factory(yaml_text=yaml_text))
    assert len(cfg.intercepts) == 2
    assert cfg.intercepts[0].match == ["帮助", "help"]
    assert cfg.intercepts[0].reply == "帮助内容"
    assert cfg.intercepts[1].match_regex == "^在吗[?？]?$"
    # hit 行为
    assert cfg.intercepts[0].hit("帮助")
    assert not cfg.intercepts[0].hit("我要帮助")  # 精确匹配，子串不算
    assert cfg.intercepts[1].hit("在吗？")


def test_intercept_requires_match_or_regex(bot_dir_factory, base_env):
    yaml_text = MINIMAL_YAML + '\nintercepts:\n  - reply: "只有回复"\n'
    with pytest.raises(ConfigError, match="至少要配 match"):
        load_bot_config(bot_dir_factory(yaml_text=yaml_text))


def test_intercept_requires_reply(bot_dir_factory, base_env):
    yaml_text = MINIMAL_YAML + '\nintercepts:\n  - match: ["hi"]\n'
    with pytest.raises(ConfigError, match="intercepts\\[0\\].reply"):
        load_bot_config(bot_dir_factory(yaml_text=yaml_text))


def test_intercept_invalid_regex_rejected(bot_dir_factory, base_env):
    yaml_text = (
        MINIMAL_YAML
        + '\nintercepts:\n  - match_regex: "[unclosed"\n    reply: "x"\n'
    )
    with pytest.raises(ConfigError, match="不是合法的正则"):
        load_bot_config(bot_dir_factory(yaml_text=yaml_text))


# --------------------------------------------------------------------------
# hooks
# --------------------------------------------------------------------------


def test_hooks_auto_discovered_when_section_omitted(bot_dir_factory, base_env):
    bot_dir = bot_dir_factory(files={"hooks.py": "def on_message(text, ctx):\n    return None\n"})
    cfg = load_bot_config(bot_dir)
    assert cfg.hooks_file is not None
    assert cfg.hooks_file.name == "hooks.py"


def test_no_hooks_when_absent(bot_dir_factory, base_env):
    cfg = load_bot_config(bot_dir_factory())
    assert cfg.hooks_file is None


def test_explicit_hooks_file(bot_dir_factory, base_env):
    yaml_text = MINIMAL_YAML + "\nhooks:\n  file: custom_hooks.py\n"
    bot_dir = bot_dir_factory(yaml_text=yaml_text, files={"custom_hooks.py": "# empty\n"})
    cfg = load_bot_config(bot_dir)
    assert cfg.hooks_file is not None
    assert cfg.hooks_file.name == "custom_hooks.py"


def test_explicit_missing_hooks_file_warns_but_loads(bot_dir_factory, base_env, caplog):
    """按约定跳过而不是崩，但要打 WARNING，避免同事以为 hook 生效了"""
    yaml_text = MINIMAL_YAML + "\nhooks:\n  file: nope.py\n"
    with caplog.at_level("WARNING"):
        cfg = load_bot_config(bot_dir_factory(yaml_text=yaml_text))
    assert cfg.hooks_file is None
    assert any("nope.py" in r.getMessage() for r in caplog.records)


# --------------------------------------------------------------------------
# 摘要输出
# --------------------------------------------------------------------------


def test_mask_secret():
    assert mask_secret("abcdefghij") == "abcd******"
    assert mask_secret("abc") == "***"
    assert mask_secret("") == "(空)"


def test_config_summary_masks_secrets(bot_dir_factory, base_env):
    cfg = load_bot_config(bot_dir_factory())
    summary = config_summary(cfg)
    # 明文密钥不能出现在摘要里
    assert "bot-secret-5678" not in summary
    assert "sk-test-key" not in summary
    # 但要能看出配的是哪一个
    assert "sk-t" in summary
    assert "test-bot" in summary
    assert "main" in summary
