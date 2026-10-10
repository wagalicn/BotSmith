"""
提示词 / 技能文件热加载测试（ContentWatcher）

Agent 运行期间技能文件会被外部系统更新（比如 Demand 发布新锚点集）。
这里验证：改了就生效、没改不重读、读失败保留上一版且能自动恢复、
只读 bot.yaml 列出的文件（发布方的临时文件不会被读到）。
"""

from __future__ import annotations

import logging

import pytest

import botkit.config as config_module
from botkit.config import ContentWatcher, load_bot_config
from botkit.engine import BotEngine

# 和 conftest.MINIMAL_YAML 相同，再加一个技能。这里不 `from conftest import`：
# 仓库里有多个 conftest.py（骨架 + 各 Agent），按名字导入可能拿到别的那个。
YAML_WITH_SKILL = """\
name: test-bot

wecom:
  bot_id: ${TEST_BOT_ID}
  bot_secret: ${TEST_BOT_SECRET}

llm:
  api_url: https://example.invalid/compatible-mode/v1
  api_key: ${TEST_LLM_KEY}
  model: test-model

prompt:
  file: prompt.md

mcp:
  servers:
    - name: main
      url: https://example.invalid/mcp

skills:
  enabled: true
  files:
    - faq.md
"""


@pytest.fixture
def loaded(bot_dir_factory, base_env):
    """一个带 prompt.md + 一个技能文件的 Agent，已按启动流程加载好配置"""
    bot_dir = bot_dir_factory(
        yaml_text=YAML_WITH_SKILL,
        prompt="你是测试 Agent。",
        files={"../_skills/faq.md": "# FAQ\n旧答案"},
    )
    cfg = load_bot_config(bot_dir, load_env=False)
    skill = bot_dir.parent / "_skills" / "faq.md"
    return cfg, bot_dir / "prompt.md", skill


def test_first_refresh_without_changes_is_noop(loaded):
    cfg, _, _ = loaded
    before = (cfg.prompt_text, cfg.skills_text)
    assert ContentWatcher(cfg).refresh() is False
    assert (cfg.prompt_text, cfg.skills_text) == before


def test_prompt_file_update_takes_effect(loaded):
    cfg, prompt, _ = loaded
    watcher = ContentWatcher(cfg)
    watcher.refresh()

    prompt.write_text("你是更新后的测试 Agent，多了几句约束。", encoding="utf-8")

    assert watcher.refresh() is True
    assert cfg.prompt_text == "你是更新后的测试 Agent，多了几句约束。"


def test_skill_file_atomic_replace_takes_effect(loaded):
    """按发布方的做法：先写点开头的临时文件，再原子替换同名文件"""
    cfg, _, skill = loaded
    watcher = ContentWatcher(cfg)
    watcher.refresh()
    assert "旧答案" in cfg.skills_text

    tmp = skill.with_name(".faq.md.tmp")
    tmp.write_text("# FAQ\n新答案，内容更长一些", encoding="utf-8")
    # 替换之前，临时文件已经存在，但不在 bot.yaml 列表里，不会被读到
    assert watcher.refresh() is False
    assert "新答案" not in cfg.skills_text

    tmp.replace(skill)
    assert watcher.refresh() is True
    assert "新答案，内容更长一些" in cfg.skills_text
    assert "旧答案" not in cfg.skills_text
    assert cfg.skills_text.startswith("## faq.md\n\n")  # 和启动加载的拼接格式一致


def test_unchanged_files_are_not_reread(loaded, monkeypatch):
    cfg, _, _ = loaded
    watcher = ContentWatcher(cfg)
    watcher.refresh()

    calls = []
    real = config_module._read_skill_files
    monkeypatch.setattr(
        config_module,
        "_read_skill_files",
        lambda *a, **kw: calls.append(1) or real(*a, **kw),
    )
    for _ in range(5):
        assert watcher.refresh() is False
    assert calls == []  # 只看了修改时间和大小，没有重新读文件


def test_missing_skill_keeps_last_good_and_recovers(loaded, caplog):
    cfg, _, skill = loaded
    watcher = ContentWatcher(cfg)
    watcher.refresh()
    good = cfg.skills_text

    caplog.set_level(logging.WARNING, logger="botkit.config")
    skill.unlink()
    assert watcher.refresh() is False
    assert watcher.refresh() is False
    assert cfg.skills_text == good  # 读不到就继续用上一版，不让请求失败
    warnings = [r for r in caplog.records if "重新加载失败" in r.getMessage()]
    assert len(warnings) == 1  # 同一个失败状态只记一次

    skill.write_text("# FAQ\n恢复后的答案", encoding="utf-8")
    assert watcher.refresh() is True
    assert "恢复后的答案" in cfg.skills_text


def test_empty_prompt_keeps_last_good(loaded):
    cfg, prompt, _ = loaded
    watcher = ContentWatcher(cfg)
    watcher.refresh()
    good = cfg.prompt_text

    prompt.write_text("   \n", encoding="utf-8")
    assert watcher.refresh() is False
    assert cfg.prompt_text == good


def test_inline_prompt_and_disabled_skills_left_alone(base_env, bot_dir_factory):
    """inline 提示词没有文件可盯；技能没启用时，已有的 skills_text 不被清空"""
    bot_dir = bot_dir_factory()
    cfg = load_bot_config(bot_dir, load_env=False)
    cfg.prompt_source = "inline"
    cfg.prompt_text = "内联提示词"
    cfg.skills_text = "外部直接给的技能正文"

    watcher = ContentWatcher(cfg)
    assert watcher.refresh() is False
    assert cfg.prompt_text == "内联提示词"
    assert cfg.skills_text == "外部直接给的技能正文"


def test_engine_system_prompt_uses_latest_skill(loaded):
    cfg, _, skill = loaded
    engine = BotEngine(cfg, mcp=None, backend=None)
    assert "旧答案" in engine.build_system_prompt(None)

    skill.write_text("# FAQ\n引擎应该看到这个新答案", encoding="utf-8")
    prompt = engine.build_system_prompt(None)
    assert "引擎应该看到这个新答案" in prompt
    assert "旧答案" not in prompt
