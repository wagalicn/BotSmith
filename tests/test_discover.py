"""多 Agent 发现（门户网关用）测试：config.discover_api_bots"""

from __future__ import annotations

from pathlib import Path

import pytest

from botkit.config import ConfigError, discover_api_bots
from conftest import MINIMAL_YAML


# MINIMAL_YAML 默认没有 api 段 = 不开对外接口。给需要的 Agent 追加 api 段。
API_SNIPPET = """
api:
  enabled: true
  token: ${TEST_LLM_KEY}
"""


def _write_bot(parent: Path, name: str, yaml_text: str, prompt: str = "你是测试。") -> Path:
    bot_dir = parent / name
    bot_dir.mkdir(parents=True, exist_ok=True)
    (bot_dir / "bot.yaml").write_text(yaml_text, encoding="utf-8")
    (bot_dir / "prompt.md").write_text(prompt, encoding="utf-8")
    return bot_dir


def _yaml_with_api(name: str) -> str:
    # 换掉 name，并追加 api 段开启对外接口
    return MINIMAL_YAML.replace("name: test-bot", f"name: {name}") + API_SNIPPET


# --------------------------------------------------------------------------
# 基本发现
# --------------------------------------------------------------------------


def test_empty_directory_returns_nothing(tmp_path, base_env):
    assert discover_api_bots(tmp_path) == []


def test_discovers_only_api_enabled_bots(tmp_path, base_env):
    # demand、rom 开了 api；plain 没开（默认 enabled=false）
    _write_bot(tmp_path, "demand", _yaml_with_api("demand"))
    _write_bot(tmp_path, "rom", _yaml_with_api("rom"))
    _write_bot(tmp_path, "plain", MINIMAL_YAML)  # 无 api 段

    found = discover_api_bots(tmp_path)
    names = [b.name for b in found]

    assert names == ["demand", "rom"]  # 按名字排序，plain 被忽略
    assert all(b.ok for b in found)
    assert all(b.config is not None and b.config.api.enabled for b in found)


def test_results_sorted_by_name(tmp_path, base_env):
    for n in ["zeta", "alpha", "mike"]:
        _write_bot(tmp_path, n, _yaml_with_api(n))
    assert [b.name for b in discover_api_bots(tmp_path)] == ["alpha", "mike", "zeta"]


# --------------------------------------------------------------------------
# 坏配置不拖垮整体
# --------------------------------------------------------------------------


def test_broken_bot_does_not_break_discovery(tmp_path, base_env):
    _write_bot(tmp_path, "good", _yaml_with_api("good"))
    # 坏的：bot.yaml 不是合法 YAML
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "bot.yaml").write_text("name: [这不是合法: yaml", encoding="utf-8")

    found = {b.name: b for b in discover_api_bots(tmp_path)}

    assert set(found) == {"good", "bad"}
    assert found["good"].ok is True
    assert found["bad"].ok is False
    assert found["bad"].config is None
    assert found["bad"].error  # 有错误信息


def test_missing_env_placeholder_marks_bot_unavailable(tmp_path, monkeypatch):
    # 不提供 TEST_* 环境变量，占位符替换失败 → 该 Agent 不可用，但不抛
    _write_bot(tmp_path, "needs_env", _yaml_with_api("needs_env"))
    found = discover_api_bots(tmp_path, load_env=False)
    assert len(found) == 1
    assert found[0].ok is False
    assert found[0].error


# --------------------------------------------------------------------------
# 跳过特殊目录
# --------------------------------------------------------------------------


def test_skips_dot_and_underscore_dirs(tmp_path, base_env):
    _write_bot(tmp_path, "real", _yaml_with_api("real"))
    # .trash 回收站里即使有 bot.yaml 也不算
    _write_bot(tmp_path, ".trash", _yaml_with_api("trashed"))
    # _skills 共享技能目录
    (tmp_path / "_skills").mkdir()
    (tmp_path / "_skills" / "bot.yaml").write_text("name: x", encoding="utf-8")

    assert [b.name for b in discover_api_bots(tmp_path)] == ["real"]


def test_ignores_non_bot_dirs_and_files(tmp_path, base_env):
    _write_bot(tmp_path, "real", _yaml_with_api("real"))
    (tmp_path / "notabot").mkdir()  # 目录但没有 bot.yaml
    (tmp_path / "loose.txt").write_text("x", encoding="utf-8")  # 散文件

    assert [b.name for b in discover_api_bots(tmp_path)] == ["real"]


# --------------------------------------------------------------------------
# 错误入参
# --------------------------------------------------------------------------


def test_nonexistent_directory_raises(tmp_path):
    with pytest.raises(ConfigError):
        discover_api_bots(tmp_path / "does-not-exist")
