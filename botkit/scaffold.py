"""
脚手架：生成一个新 bot 的目录骨架

`python -m botkit new <name>` 会在 bots/<name>/ 下生成 bot.yaml、prompt.md、
hooks.py、.env.example 和一份 README，模板里的注释写清了每个字段的作用。

比"复制一份现有 bot 再改"可靠：不会漏改上一个 bot 的名字、密钥、
工具白名单，也不会带上不需要的 hook 代码。
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

logger = logging.getLogger("botkit.scaffold")

TEMPLATE_DIR = Path(__file__).parent / "templates"

# 模板文件 -> 生成的文件名
TEMPLATE_FILES = {
    "bot.yaml.tmpl": "bot.yaml",
    "prompt.md.tmpl": "prompt.md",
    "hooks.py.tmpl": "hooks.py",
    "env.example.tmpl": ".env.example",
    "README.md.tmpl": "README.md",
}

# bot 名字用作目录名和日志前缀，限制成安全字符
_VALID_NAME = re.compile(r"^[a-zA-Z][a-zA-Z0-9_-]*$")


class ScaffoldError(Exception):
    """生成骨架失败"""


def validate_name(name: str) -> str:
    """校验 bot 名字。它会成为目录名，不能是奇怪的字符"""
    name = (name or "").strip()
    if not name:
        raise ScaffoldError("Agent 名字不能为空")
    if not _VALID_NAME.match(name):
        raise ScaffoldError(
            f"Agent 名字 {name!r} 不合法。要求：字母开头，只能包含字母、数字、下划线和连字符。"
            "例如 hr-assistant、crm_bot"
        )
    return name


def render(template: str, bot_name: str, bot_dir: str) -> str:
    """填模板占位符"""
    return template.replace("{{BOT_NAME}}", bot_name).replace("{{BOT_DIR}}", bot_dir)


def create_bot(
    name: str,
    parent: str | Path = "bots",
    force: bool = False,
) -> Path:
    """
    生成 bots/<name>/ 骨架，返回生成的目录。

    force=False 时，目录已存在且非空就报错 —— 免得覆盖掉别人已经配好的 bot。
    """
    name = validate_name(name)

    parent_path = Path(parent)
    if not parent_path.is_absolute():
        parent_path = Path.cwd() / parent_path
    target = (parent_path / name).resolve()

    if target.exists() and any(target.iterdir()) and not force:
        raise ScaffoldError(
            f"目录已存在且不是空的：{target}\n"
            f"换个名字，或者加 --force 覆盖同名文件（会保留目录里的其他文件）"
        )

    if not TEMPLATE_DIR.is_dir():
        raise ScaffoldError(f"找不到模板目录：{TEMPLATE_DIR}")

    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise ScaffoldError(f"创建目录 {target} 失败：{e}") from e

    # README 和 bot.yaml 里要写「怎么跑」，用相对路径更好抄
    try:
        display_dir = target.relative_to(Path.cwd()).as_posix()
    except ValueError:
        display_dir = target.as_posix()

    for template_name, out_name in TEMPLATE_FILES.items():
        template_path = TEMPLATE_DIR / template_name
        if not template_path.is_file():
            raise ScaffoldError(f"模板文件缺失：{template_path}")

        content = render(
            template_path.read_text(encoding="utf-8"), name, display_dir
        )
        out_path = target / out_name
        out_path.write_text(content, encoding="utf-8")
        logger.debug("已生成 %s", out_path)

    return target
