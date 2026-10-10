"""
MCP 连通性探测

读指定 bot 的配置连它的 MCP 服务端，打印能拿到哪些工具、参数长什么样、
白名单过滤后实际暴露给 LLM 的是哪几个。也可以直接调一个工具试试。

用法（把 hr-assistant 换成你自己的 bot 名字）：
    python tools/mcp_probe.py bots/hr-assistant
    python tools/mcp_probe.py bots/hr-assistant --identity L220104
    python tools/mcp_probe.py bots/hr-assistant --call get_leave_types
    python tools/mcp_probe.py bots/hr-assistant --call submit_leave --args '{"days":3}'

配置页面的「工具」页做的就是这件事，效果一样。这个脚本适合在
命令行里快速看一眼，或者拿返回的原文排查问题。

排查建议：
- 连不上          先看 url 和网络/证书，报错信息里有原始异常
- 工具列表是空的   看 allow_tools 是不是写错了工具名（会有 WARNING 提示）
- 调用返回"需要登录" 中台的免认证策略还没对这个身份放行
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

# 让脚本能 import botkit（不需要先安装成包）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from botkit.config import ConfigError, load_bot_config  # noqa: E402
from botkit.mcp_client import McpError, McpToolClient  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="探测某个 bot 的 MCP 服务端连通性和工具列表",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("bot_dir", help="bot 目录，如 bots/hr-assistant")
    parser.add_argument(
        "--identity",
        default="L220104",
        help="用哪个身份去连（会作为 identity_header 发出），默认 L220104",
    )
    parser.add_argument("--call", help="顺便调用这个工具试试")
    parser.add_argument(
        "--args", default="{}", help="调用参数，JSON 字符串，默认 {}"
    )
    parser.add_argument(
        "--verbose", action="store_true", help="打开 DEBUG 日志"
    )
    return parser.parse_args()


async def main() -> int:
    args = parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)-7s [%(name)s] %(message)s",
    )
    if not args.verbose:
        for name in ("httpx2", "httpx", "httpcore", "mcp"):
            logging.getLogger(name).setLevel(logging.WARNING)

    try:
        cfg = load_bot_config(args.bot_dir)
    except ConfigError as e:
        print(f"配置读取失败：\n{e}", file=sys.stderr)
        return 1

    print(f"bot          : {cfg.name}")
    print(f"身份         : {args.identity}")
    for s in cfg.mcp.servers:
        print(f"MCP 服务端   : {s.name}  {s.url}")
        print(f"  身份头     : {s.identity_header or '(不发送)'}")
        print(f"  白名单     : {'、'.join(s.allow_tools) or '(全部放开)'}")
        if s.deny_tools:
            print(f"  黑名单     : {'、'.join(s.deny_tools)}")
    print()

    mcp = McpToolClient(cfg.mcp.servers)

    try:
        async with mcp.session(identity=args.identity) as sess:
            try:
                tools = await sess.list_tools_openai()
            except McpError as e:
                print(f"拉取工具失败：\n{e}", file=sys.stderr)
                return 1

            print(f"过滤后暴露给 LLM 的工具（{len(tools)} 个）")
            print("=" * 70)
            for t in tools:
                fn = t["function"]
                print(f"\n■ {fn['name']}   [来自 {sess.routes[fn['name']]}]")
                desc = (fn.get("description") or "").strip().replace("\n", " ")
                print(f"  描述: {desc[:150]}")
                schema = fn.get("parameters") or {}
                props = schema.get("properties") or {}
                required = set(schema.get("required") or [])
                if not props:
                    print("  参数: (无)")
                for pname, pinfo in props.items():
                    mark = " (必填)" if pname in required else ""
                    ptype = pinfo.get("type", "")
                    pdesc = (pinfo.get("description") or "").replace("\n", " ")
                    print(f"    - {pname}: {ptype}{mark} {pdesc[:80]}")

            if not tools:
                print("\n（一个工具都没有。检查 allow_tools 里的工具名是不是拼错了，")
                print("  上面的 WARNING 日志会列出服务端实际不存在的名字）")

            if args.call:
                try:
                    call_args = json.loads(args.args)
                except json.JSONDecodeError as e:
                    print(f"\n--args 不是合法 JSON：{e}", file=sys.stderr)
                    return 1

                print("\n" + "=" * 70)
                print(f"调用 {args.call}  参数={json.dumps(call_args, ensure_ascii=False)}")
                injected = cfg.inject_for(args.call)
                if injected:
                    print(
                        f"注意：正式运行时 {'、'.join(injected)} 会被强制改成当前用户身份"
                        f"（identity.inject），这个探测脚本原样发送你给的参数。"
                    )
                print("=" * 70)
                result = await sess.call_tool(args.call, call_args)
                print(result[:2000])
                if len(result) > 2000:
                    print(f"\n（还有 {len(result) - 2000} 字未显示）")

            print(f"\n建连次数（应为每个服务端 1 次）: {sess.connect_counts}")
    except McpError as e:
        print(f"\nMCP 出错：\n{e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"\n意外错误：{type(e).__name__}: {e}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
