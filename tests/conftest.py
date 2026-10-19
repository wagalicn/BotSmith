"""
pytest 公共配置与工具

把 bot-demo 根目录加进 sys.path，这样测试里可以直接 `import botkit`
而不需要先安装成包。
"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

BOT_DEMO_ROOT = Path(__file__).resolve().parent.parent
if str(BOT_DEMO_ROOT) not in sys.path:
    sys.path.insert(0, str(BOT_DEMO_ROOT))


MINIMAL_YAML = """\
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
"""


# 这些变量名在 bot.yaml / .env.example 模板里用到。开发机的 shell 里
# 很可能残留着同名变量（调试时 set 过），会让测试结果取决于运行环境。
# 每个测试跑之前统一清掉，需要的测试自己显式设置。
MANAGED_ENV_VARS = (
    "WECHAT_BOT_ID",
    "WECHAT_BOT_SECRET",
    "LLM_API_URL",
    "LLM_API_KEY",
    "LLM_MODEL",
    "MCP_URL",
    "CRM_MCP_URL",
    "TEST_BOT_ID",
    "TEST_BOT_SECRET",
    "TEST_LLM_KEY",
    "MCP_HOST",
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch):
    """让测试不受运行环境里残留的环境变量影响"""
    for name in MANAGED_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def bot_dir_factory(tmp_path: Path):
    """
    造一个临时 bot 目录。

    用法：
        d = bot_dir_factory(yaml_text=..., prompt="...", files={"hooks.py": "..."})
    """

    def _make(
        yaml_text: str = MINIMAL_YAML,
        prompt: str | None = "你是一个测试机器人。",
        files: dict[str, str] | None = None,
        dir_name: str = "mybot",
    ) -> Path:
        bot_dir = tmp_path / dir_name
        bot_dir.mkdir(parents=True, exist_ok=True)
        (bot_dir / "bot.yaml").write_text(textwrap.dedent(yaml_text), encoding="utf-8")
        if prompt is not None:
            (bot_dir / "prompt.md").write_text(prompt, encoding="utf-8")
        for rel, content in (files or {}).items():
            target = bot_dir / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(textwrap.dedent(content), encoding="utf-8")
        return bot_dir

    return _make


@pytest.fixture
def base_env(monkeypatch: pytest.MonkeyPatch):
    """MINIMAL_YAML 需要的环境变量"""
    monkeypatch.setenv("TEST_BOT_ID", "bot-id-1234")
    monkeypatch.setenv("TEST_BOT_SECRET", "bot-secret-5678")
    monkeypatch.setenv("TEST_LLM_KEY", "sk-test-key")
    return monkeypatch


@pytest.fixture(autouse=True)
def _usage_to_tmp(tmp_path, monkeypatch):
    """
    用量落盘默认写 config.bot_dir。测试里的 BotConfig 常用 Path(".")（即仓库根），
    真实跑一条消息就会在仓库里留下 usage/usage-YYYYMM.jsonl。

    全局把 UsageStore.from_config 重定向到每个测试独立的临时目录，杜绝任何测试
    （runtime / sse / api_server / ui 预览等走真实 pipeline 的）污染工作区。
    """
    from botkit import usage as usage_mod

    monkeypatch.setattr(
        usage_mod.UsageStore,
        "from_config",
        classmethod(lambda cls, bot_dir: usage_mod.JsonlUsageStore(tmp_path)),
    )
