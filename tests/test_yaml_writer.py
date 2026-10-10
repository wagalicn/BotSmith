"""
带注释 bot.yaml 生成器测试

核心保证：生成的文件既是合法 YAML、又能被 load_bot_config 正常加载、
而且值一个不丢。页面存盘走的是这条路，出错会直接毁掉同事的配置。
"""

from __future__ import annotations

import pytest
import yaml

from botkit.config import MessagesConfig, load_bot_config
from botkit.yaml_writer import dump_bot_yaml


def full_values() -> dict:
    """一份把所有字段都填满的配置"""
    return {
        "name": "hr-assistant",
        "wecom": {"bot_id": "${WECHAT_BOT_ID}", "bot_secret": "${WECHAT_BOT_SECRET}"},
        "llm": {
            "client": "openai",
            "api_url": "${LLM_API_URL}",
            "api_key": "${LLM_API_KEY}",
            "model": "${LLM_MODEL}",
            "timeout_seconds": 90,
            "max_tool_rounds": 8,
        },
        "prompt": {"file": "prompt.md"},
        "mcp": {
            "servers": [
                {
                    "name": "oa",
                    "url": "${MCP_URL}",
                    "identity_header": "X-MCP-Identity",
                    "allow_tools": ["get_system_config", "create_it_ticket"],
                    "deny_tools": ["delete_everything"],
                },
                {
                    "name": "crm",
                    "url": "${CRM_MCP_URL}",
                    "identity_header": "X-Identity",
                    "allow_tools": ["get_customer"],
                    "deny_tools": [],
                },
            ]
        },
        "identity": {
            "source": "wecom_userid",
            "pattern": r"^[LM]\d{6}$",
            "inject": [
                {"tool": "create_it_ticket", "arg": "work_code"},
                {"tool": "submit_expense", "arg": "applicant"},
            ],
        },
        "session": {"scope": "group", "max_turns": 5, "ttl_seconds": 600},
        "messages": {
            "welcome": "你好，我是 HR 助手：请假、报销都能办。",
            "empty_input": "请描述一下你要办什么。",
            "thinking": "正在处理，请稍候...",
            "reset_done": "已清空对话。",
            "error": "出错了，请看日志。",
            "identity_invalid": "认不出你的工号。",
            "tool_rounds_exceeded": "绕不出来了，换个说法。",
            "reset_keywords": ["重新开始", "重置", "reset"],
            "reply_suffix": "\n\n—— HR 助手",
            "tool_progress": {
                "submit_leave": "正在提交请假...",
                "query_attendance": "正在查考勤...",
                "default": "正在处理...",
            },
        },
        "intercepts": [
            {"match": ["帮助", "help"], "reply": "我能帮你办请假和报销。"},
            {"match_regex": r"^在吗[?？]?$", "reply": "在的，请说需求。"},
        ],
        "hooks": {"file": "hooks.py"},
        "logging": {"level": "DEBUG"},
    }


def write_bot(bot_dir, values: dict, prompt: str = "你是测试助手。"):
    bot_dir.mkdir(parents=True, exist_ok=True)
    (bot_dir / "bot.yaml").write_text(dump_bot_yaml(values), encoding="utf-8")
    (bot_dir / "prompt.md").write_text(prompt, encoding="utf-8")
    return bot_dir


# --------------------------------------------------------------------------
# 输出是合法 YAML
# --------------------------------------------------------------------------


def test_output_is_valid_yaml():
    raw = yaml.safe_load(dump_bot_yaml(full_values()))
    assert isinstance(raw, dict)


def test_output_keeps_comments():
    """注释是给手改配置的人看的文档，不能因为过了一次页面就没了"""
    text = dump_bot_yaml(full_values())
    assert "# 企业微信凭证" in text
    assert "# 会话上下文" in text
    assert "identity.inject" not in text or "安全边界" in text
    # 注释行数应该相当可观，说明确实带了说明
    comment_lines = [l for l in text.splitlines() if l.strip().startswith("#")]
    assert len(comment_lines) > 30


def test_output_mentions_both_edit_paths():
    text = dump_bot_yaml(full_values())
    assert "botkit ui" in text
    assert "手改" in text


def test_note_can_be_disabled():
    text = dump_bot_yaml(full_values(), generated_note=False)
    assert "botkit ui" not in text


# --------------------------------------------------------------------------
# round-trip：值一个不丢
# --------------------------------------------------------------------------


def test_round_trip_preserves_everything():
    values = full_values()
    raw = yaml.safe_load(dump_bot_yaml(values))

    assert raw["name"] == "hr-assistant"
    assert raw["wecom"] == values["wecom"]
    assert raw["llm"] == values["llm"]
    assert raw["prompt"] == {"file": "prompt.md"}
    assert raw["mcp"]["servers"][0] == values["mcp"]["servers"][0]
    assert raw["identity"]["pattern"] == r"^[LM]\d{6}$"
    assert raw["identity"]["inject"] == values["identity"]["inject"]
    assert raw["session"] == values["session"]
    assert raw["logging"] == {"level": "DEBUG"}
    for key, expected in values["messages"].items():
        assert raw["messages"][key] == expected, key
    # 前置拦截 round-trip
    assert raw["intercepts"][0]["match"] == ["帮助", "help"]
    assert raw["intercepts"][0]["reply"] == "我能帮你办请假和报销。"
    assert raw["intercepts"][1]["match_regex"] == r"^在吗[?？]?$"
    assert raw["intercepts"][1]["reply"] == "在的，请说需求。"


def test_round_trip_is_stable():
    """生成 → 解析 → 再生成，结果应该一模一样（页面反复存盘不会漂）"""
    first = dump_bot_yaml(full_values())
    second = dump_bot_yaml(yaml.safe_load(first))
    assert first == second


def test_regex_pattern_survives_round_trip():
    """正则里的反斜杠最容易在 YAML 转义上出问题"""
    for pattern in (r"^[LM]\d{6}$", r"^\w+@\w+\.\w+$", r"^(?:A|B)\d{4}$"):
        raw = yaml.safe_load(
            dump_bot_yaml({"name": "b", "identity": {"pattern": pattern}})
        )
        assert raw["identity"]["pattern"] == pattern


@pytest.mark.parametrize(
    "text",
    [
        "带 # 井号的文案",
        "带'单引号'的文案",
        '带"双引号"的文案',
        "带：全角冒号",
        "yes",
        "no",
        "123",
        "1.5",
        "true",
        "null",
        "正在处理，请稍候...",
        "多行\n第二行\n第三行",
        "结尾有空格 ",
        "@开头的文案",
        "- 减号开头",
        "{花括号}",
        "[方括号]",
    ],
)
def test_tricky_message_text_survives(text):
    """
    这些值直接 dump 会破坏 YAML 语义（被解析成布尔、数字、流式集合等），
    必须靠 PyYAML 判断引号。
    """
    raw = yaml.safe_load(dump_bot_yaml({"name": "b", "messages": {"welcome": text}}))
    assert raw["messages"]["welcome"] == text


def test_placeholder_syntax_survives():
    raw = yaml.safe_load(
        dump_bot_yaml({"name": "b", "wecom": {"bot_id": "${WECHAT_BOT_ID}"}})
    )
    assert raw["wecom"]["bot_id"] == "${WECHAT_BOT_ID}"


def test_empty_lists_survive():
    raw = yaml.safe_load(
        dump_bot_yaml(
            {
                "name": "b",
                "mcp": {"servers": [{"name": "m", "url": "u", "allow_tools": []}]},
                "identity": {"inject": []},
            }
        )
    )
    assert raw["mcp"]["servers"][0]["allow_tools"] == []
    assert raw["identity"]["inject"] == []


def test_float_timeout_written_as_int_when_whole():
    """60.0 写成 60，配置文件里更干净"""
    text = dump_bot_yaml({"name": "b", "llm": {"timeout_seconds": 60.0}})
    assert "timeout_seconds: 60" in text
    assert "60.0" not in text


def test_non_whole_float_preserved():
    raw = yaml.safe_load(dump_bot_yaml({"name": "b", "llm": {"timeout_seconds": 2.5}}))
    assert raw["llm"]["timeout_seconds"] == 2.5


# --------------------------------------------------------------------------
# 默认值填充
# --------------------------------------------------------------------------


def test_minimal_input_gets_sensible_defaults():
    raw = yaml.safe_load(dump_bot_yaml({"name": "bare-bot"}))

    assert raw["name"] == "bare-bot"
    assert raw["wecom"]["bot_id"] == "${WECHAT_BOT_ID}"
    assert raw["llm"]["client"] == "openai"
    assert raw["llm"]["max_tool_rounds"] == 6
    assert raw["prompt"]["file"] == "prompt.md"
    assert raw["mcp"]["servers"][0]["url"] == "${MCP_URL}"
    assert raw["session"]["scope"] == "group_user"
    assert raw["messages"]["welcome"] == MessagesConfig().welcome
    assert raw["logging"]["level"] == "INFO"


def test_empty_dict_still_produces_loadable_file():
    raw = yaml.safe_load(dump_bot_yaml({}))
    assert raw["name"] == "my-bot"


def test_no_pattern_leaves_commented_example():
    """没配 pattern 时留个注释示例，方便手改的人知道怎么写"""
    text = dump_bot_yaml({"name": "b", "identity": {}})
    assert "# pattern:" in text
    raw = yaml.safe_load(text)
    assert "pattern" not in raw["identity"]


def test_tool_progress_default_is_added():
    raw = yaml.safe_load(
        dump_bot_yaml(
            {"name": "b", "messages": {"tool_progress": {"my_tool": "查询中..."}}}
        )
    )
    assert raw["messages"]["tool_progress"]["my_tool"] == "查询中..."
    assert "default" in raw["messages"]["tool_progress"]


def test_inline_prompt_supported():
    raw = yaml.safe_load(
        dump_bot_yaml({"name": "b", "prompt": {"inline": "你是测试机器人。"}})
    )
    assert raw["prompt"]["inline"] == "你是测试机器人。"
    assert "file" not in raw["prompt"]


def test_invalid_log_level_falls_back():
    raw = yaml.safe_load(dump_bot_yaml({"name": "b", "logging": {"level": "VERBOSE"}}))
    assert raw["logging"]["level"] == "INFO"


def test_lowercase_log_level_normalized():
    raw = yaml.safe_load(dump_bot_yaml({"name": "b", "logging": {"level": "debug"}}))
    assert raw["logging"]["level"] == "DEBUG"


def test_none_values_treated_as_missing():
    raw = yaml.safe_load(
        dump_bot_yaml(
            {"name": "b", "llm": {"client": None, "model": None}, "session": None}
        )
    )
    assert raw["llm"]["client"] == "openai"
    assert raw["session"]["scope"] == "group_user"


# --------------------------------------------------------------------------
# 生成的文件必须能被框架加载
# --------------------------------------------------------------------------


def test_generated_file_loads_through_config_layer(tmp_path, monkeypatch):
    """
    最重要的一条：页面存出来的文件，botkit 自己要能读。
    生成器和配置层一旦对不上（比如多写了个不存在的字段），这里就会红。
    """
    monkeypatch.setenv("WECHAT_BOT_ID", "id-1")
    monkeypatch.setenv("WECHAT_BOT_SECRET", "secret-1")
    monkeypatch.setenv("LLM_API_URL", "https://x.invalid/v1")
    monkeypatch.setenv("LLM_API_KEY", "sk-1")
    monkeypatch.setenv("LLM_MODEL", "qwen")
    monkeypatch.setenv("MCP_URL", "https://x.invalid/mcp")
    monkeypatch.setenv("CRM_MCP_URL", "https://crm.invalid/mcp")

    bot_dir = write_bot(tmp_path / "hr", full_values())
    cfg = load_bot_config(bot_dir)

    assert cfg.name == "hr-assistant"
    assert cfg.llm.model == "qwen"
    assert cfg.llm.timeout_seconds == 90
    assert cfg.llm.max_tool_rounds == 8
    assert [s.name for s in cfg.mcp.servers] == ["oa", "crm"]
    assert cfg.mcp.servers[0].allow_tools == ["get_system_config", "create_it_ticket"]
    assert cfg.mcp.servers[0].deny_tools == ["delete_everything"]
    assert cfg.identity.pattern == r"^[LM]\d{6}$"
    assert cfg.inject_for("create_it_ticket") == ["work_code"]
    assert cfg.inject_for("submit_expense") == ["applicant"]
    assert cfg.session.scope == "group"
    assert cfg.session.max_turns == 5
    assert cfg.messages.welcome == "你好，我是 HR 助手：请假、报销都能办。"
    assert cfg.messages.progress_for("submit_leave") == "正在提交请假..."
    assert cfg.messages.progress_for("unknown") == "正在处理..."
    assert cfg.log_level == "DEBUG"


def test_minimal_generated_file_loads(tmp_path, monkeypatch):
    for name, value in (
        ("WECHAT_BOT_ID", "id"),
        ("WECHAT_BOT_SECRET", "secret"),
        ("LLM_API_URL", "https://x.invalid/v1"),
        ("LLM_API_KEY", "sk"),
        ("LLM_MODEL", "m"),
        ("MCP_URL", "https://x.invalid/mcp"),
    ):
        monkeypatch.setenv(name, value)

    bot_dir = write_bot(tmp_path / "bare", {"name": "bare-bot"})
    cfg = load_bot_config(bot_dir)
    assert cfg.name == "bare-bot"
    assert cfg.identity.pattern is None
    assert cfg.mcp.servers[0].allow_tools == []


def test_generated_file_has_no_unknown_keys(tmp_path, monkeypatch):
    """
    配置层对未知字段是直接报错的。生成器要是写了个拼错的字段名，
    这个测试会立刻发现。
    """
    for name, value in (
        ("WECHAT_BOT_ID", "id"),
        ("WECHAT_BOT_SECRET", "secret"),
        ("LLM_API_URL", "https://x.invalid/v1"),
        ("LLM_API_KEY", "sk"),
        ("LLM_MODEL", "m"),
        ("MCP_URL", "https://x.invalid/mcp"),
        ("CRM_MCP_URL", "https://crm.invalid/mcp"),
    ):
        monkeypatch.setenv(name, value)

    # 不抛 ConfigError 就说明字段名全对
    write_bot(tmp_path / "a", full_values())
    load_bot_config(tmp_path / "a")
    write_bot(tmp_path / "b", {})
    load_bot_config(tmp_path / "b")


def test_scaffolded_bot_survives_regeneration(tmp_path, monkeypatch):
    """
    脚手架生成的 bot 过一遍生成器，加载结果应该和原来等价。

    这模拟同事用页面打开一个现有 bot、随手点了下保存的情况。
    脚手架模板和生成器是两份独立的 YAML 来源，一旦对不上（比如模板加了
    新字段而生成器不认），这个测试会红。
    """
    from botkit.scaffold import create_bot

    for name, value in (
        ("WECHAT_BOT_ID", "id"),
        ("WECHAT_BOT_SECRET", "secret"),
        ("LLM_API_URL", "https://x.invalid/v1"),
        ("LLM_API_KEY", "sk"),
        ("LLM_MODEL", "m"),
        ("MCP_URL", "https://x.invalid/mcp"),
    ):
        monkeypatch.setenv(name, value)

    monkeypatch.chdir(tmp_path)
    source = create_bot("scaffolded", parent=tmp_path / "bots")
    original = load_bot_config(source)

    raw = yaml.safe_load((source / "bot.yaml").read_text(encoding="utf-8"))
    regenerated = write_bot(tmp_path / "regen", raw, prompt=original.prompt_text)
    after = load_bot_config(regenerated)

    assert after.name == original.name
    assert after.llm.client == original.llm.client
    assert after.llm.model == original.llm.model
    assert after.llm.timeout_seconds == original.llm.timeout_seconds
    assert after.llm.max_tool_rounds == original.llm.max_tool_rounds
    assert after.identity.source == original.identity.source
    assert after.identity.pattern == original.identity.pattern
    assert after.identity.inject == original.identity.inject
    assert after.session.scope == original.session.scope
    assert after.session.max_turns == original.session.max_turns
    assert after.session.ttl_seconds == original.session.ttl_seconds
    assert after.messages.welcome == original.messages.welcome
    assert after.messages.reset_keywords == original.messages.reset_keywords
    assert after.messages.tool_progress == original.messages.tool_progress
    assert after.mcp.servers[0].name == original.mcp.servers[0].name
    assert after.mcp.servers[0].url == original.mcp.servers[0].url
    assert (
        after.mcp.servers[0].identity_header
        == original.mcp.servers[0].identity_header
    )
    assert after.mcp.servers[0].allow_tools == original.mcp.servers[0].allow_tools
    assert after.log_level == original.log_level
    assert after.prompt_text == original.prompt_text


def test_realistic_config_survives_regeneration(tmp_path, monkeypatch):
    """
    一份填满了业务配置的 bot（白名单、身份绑定、多条进度文案）
    过一遍生成器不掉东西。这是页面反复编辑时最要紧的保证。
    """
    for name, value in (
        ("WECHAT_BOT_ID", "id"),
        ("WECHAT_BOT_SECRET", "secret"),
        ("LLM_API_URL", "https://x.invalid/v1"),
        ("LLM_API_KEY", "sk"),
        ("LLM_MODEL", "m"),
        ("MCP_URL", "https://x.invalid/mcp"),
        ("CRM_MCP_URL", "https://crm.invalid/mcp"),
    ):
        monkeypatch.setenv(name, value)

    first = write_bot(tmp_path / "first", full_values())
    original = load_bot_config(first)

    # 再过一遍（模拟第二次保存）
    raw = yaml.safe_load((first / "bot.yaml").read_text(encoding="utf-8"))
    second = write_bot(tmp_path / "second", raw, prompt=original.prompt_text)
    after = load_bot_config(second)

    assert after.mcp.servers[0].allow_tools == original.mcp.servers[0].allow_tools
    assert after.mcp.servers[0].deny_tools == original.mcp.servers[0].deny_tools
    assert after.identity.inject == original.identity.inject
    assert after.messages.tool_progress == original.messages.tool_progress
    assert after.messages.reset_keywords == original.messages.reset_keywords
    # 文件内容也应该逐字节一致
    assert (first / "bot.yaml").read_text(encoding="utf-8") == (
        second / "bot.yaml"
    ).read_text(encoding="utf-8")
