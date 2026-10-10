"""
botkit 命令行入口

    python -m botkit ui                    打开可视化配置页面
    python -m botkit new <name>            生成一个新 Agent 骨架
    python -m botkit validate <bot_dir>    校验配置并打印摘要（密钥打码）
    python -m botkit run <bot_dir>         启动Agent
"""

from __future__ import annotations

import argparse
import sys

from botkit.config import ConfigError, config_summary, load_bot_config


def _cmd_validate(args: argparse.Namespace) -> int:
    try:
        cfg = load_bot_config(args.bot_dir)
    except ConfigError as e:
        print("配置校验失败：\n", file=sys.stderr)
        print(str(e), file=sys.stderr)
        return 1

    print("配置校验通过\n")
    print(config_summary(cfg))
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    from botkit.runtime import run_bot

    try:
        cfg = load_bot_config(args.bot_dir)
    except ConfigError as e:
        print("配置校验失败：\n", file=sys.stderr)
        print(str(e), file=sys.stderr)
        return 1

    run_bot(cfg)
    return 0


def _cmd_serve_api(args: argparse.Namespace) -> int:
    from botkit.runtime import serve_chat_api

    try:
        cfg = load_bot_config(args.bot_dir)
    except ConfigError as e:
        print("配置校验失败：\n", file=sys.stderr)
        print(str(e), file=sys.stderr)
        return 1

    if not cfg.api.enabled:
        print(
            "这个 bot 没开对外接口。请在配置页「对外接口」把它打开，"
            "或在 bot.yaml 里设 api.enabled: true 再启动。",
            file=sys.stderr,
        )
        return 1

    serve_chat_api(cfg)
    return 0


def _cmd_ui(args: argparse.Namespace) -> int:
    from botkit.ui import serve

    serve(
        project_root=args.root,
        host=args.host,
        port=args.port,
        open_browser=not args.no_browser,
    )
    return 0


def _cmd_new(args: argparse.Namespace) -> int:
    import os
    from pathlib import Path

    from botkit.scaffold import ScaffoldError, create_bot

    try:
        target = create_bot(args.name, parent=args.dir, force=args.force)
    except ScaffoldError as e:
        print(f"生成失败：{e}", file=sys.stderr)
        return 1

    # 相对路径更短、更好复制粘贴
    try:
        shown = Path(target).relative_to(Path.cwd()).as_posix()
    except ValueError:
        shown = str(target)

    copy_cmd = "copy" if os.name == "nt" else "cp"

    print(f"已生成 bot 骨架：{target}\n")
    print("接下来：")
    print(f"  1. {copy_cmd} {shown}/.env.example {shown}/.env")
    print("     然后在 .env 里填入企微凭证、LLM key、MCP 地址")
    print(f"  2. 编辑 {shown}/bot.yaml    工具白名单、身份规则、文案")
    print(f"  3. 编辑 {shown}/prompt.md   这个Agent是谁、要做什么")
    print(f"  4. python -m botkit validate {shown}")
    print(f"  5. python -m botkit run {shown}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m botkit",
        description="企业微信 Agent 通用骨架",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例：\n"
            "  python -m botkit ui                          打开可视化配置页面\n"
            "  python -m botkit new hr-assistant            生成骨架\n"
            "  python -m botkit validate bots/hr-assistant  校验配置\n"
            "  python -m botkit run bots/hr-assistant       启动Agent\n"
            "  python -m botkit serve-api bots/hr-assistant 只起 chat 接口（对接 Dify/门户）\n"
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True, metavar="<命令>")

    p_ui = sub.add_parser("ui", help="打开可视化配置页面（推荐新手用）")
    p_ui.add_argument(
        "--root",
        default=".",
        help="项目根目录，里面应该有 bots/ 子目录。默认当前目录",
    )
    p_ui.add_argument(
        "--host",
        default="127.0.0.1",
        help="监听地址。默认只监听本机，不建议改（页面能读写密钥）",
    )
    p_ui.add_argument("--port", type=int, default=8771, help="监听端口，默认 8771")
    p_ui.add_argument(
        "--no-browser", action="store_true", help="不要自动打开浏览器"
    )
    p_ui.set_defaults(func=_cmd_ui)

    p_validate = sub.add_parser("validate", help="校验 Agent 配置并打印摘要")
    p_validate.add_argument("bot_dir", help="Agent 目录（里面有 bot.yaml）")
    p_validate.set_defaults(func=_cmd_validate)

    p_run = sub.add_parser(
        "run",
        help="启动Agent（连企微；若配置里开了对外接口，会一并起 /chat）",
    )
    p_run.add_argument("bot_dir", help="Agent 目录（里面有 bot.yaml）")
    p_run.set_defaults(func=_cmd_run)

    p_api = sub.add_parser(
        "serve-api",
        help="只启动对外 chat 接口（不连企微，用于对接 Dify / 门户）",
    )
    p_api.add_argument("bot_dir", help="Agent 目录（里面有 bot.yaml）")
    p_api.set_defaults(func=_cmd_serve_api)

    p_new = sub.add_parser("new", help="生成一个新 Agent 骨架")
    p_new.add_argument("name", help="Agent 名称，会作为目录名，如 hr-assistant")
    p_new.add_argument(
        "--dir",
        default="bots",
        help="骨架生成到哪个父目录下，默认 bots",
    )
    p_new.add_argument(
        "--force",
        action="store_true",
        help="目标目录已存在时也继续（会覆盖同名文件）",
    )
    p_new.set_defaults(func=_cmd_new)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))
