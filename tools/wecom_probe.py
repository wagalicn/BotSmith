"""
企微消息探测

用指定 bot 的凭证连上企微，把收到的原始消息帧完整打出来，
并回显解析结果。主要用来确认两件事：

1. 发送者 userid 是明文工号（L220104 这种）还是加密串（wo1234xxx== 这种）。
   加密串的话业务系统认不了，需要在 hooks.py 里实现 resolve_identity 做映射，
   并把 bot.yaml 的 identity.source 改成 hook。
2. 这个 bot 配的 identity.pattern 能不能匹配上真实的 userid。

用法（把 hr-assistant 换成你自己的 bot 名字）：
    python tools/wecom_probe.py bots/hr-assistant
然后在企微里 @ 机器人发一句话，看控制台输出。Ctrl+C 退出。

配置页面「密钥」页的「等我在企微 @ 一句话」做的就是这件事，
结果排版更好读。这个脚本适合不想开页面的时候用。

注意：这个脚本只探测身份和消息结构，不会调 LLM、不会调 MCP、不会建工单。
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from botkit.config import ConfigError, load_bot_config  # noqa: E402
from botkit.engine import identity_matches  # noqa: E402
from botkit.runtime import MessagePipeline, strip_mention  # noqa: E402
from botkit.session import build_session_key  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="探测企微消息里的用户身份和消息结构")
    parser.add_argument("bot_dir", help="bot 目录，如 bots/hr-assistant")
    parser.add_argument(
        "--raw", action="store_true", help="连原始消息帧的完整 JSON 一起打印"
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")

    try:
        cfg = load_bot_config(args.bot_dir)
    except ConfigError as e:
        print(f"配置读取失败：\n{e}", file=sys.stderr)
        return 1

    from aibot import WSClient, WSClientOptions, generate_req_id

    print(f"bot            : {cfg.name}")
    print(f"identity.source: {cfg.identity.source}")
    print(f"identity 正则  : {cfg.identity.pattern or '(不校验)'}")
    print(f"会话隔离       : {cfg.session.scope}")
    print("\n连接中……连上后请在企微里 @ 机器人发一句话。Ctrl+C 退出。\n")

    ws_client = WSClient(
        WSClientOptions(bot_id=cfg.wecom.bot_id, secret=cfg.wecom.bot_secret)
    )

    def on_authenticated() -> None:
        print("认证成功，等待消息……\n")

    def on_error(err) -> None:
        print(f"SDK 错误：{err}")

    async def on_text(frame: dict) -> None:
        body = frame.get("body", {})
        userid = MessagePipeline.wecom_userid(frame)
        content = (body.get("text") or {}).get("content", "")

        print("=" * 70)
        if args.raw:
            print("原始消息帧：")
            print(json.dumps(frame, ensure_ascii=False, indent=2))
            print("-" * 70)

        looks_encrypted = (
            "=" in userid or len(userid) > 32 or not userid.isascii()
        )

        print(f"发送者 userid   : {userid!r}")
        print(f"  长度          : {len(userid)}")
        print(
            f"  像明文工号吗  : {'不像，可能是加密串' if looks_encrypted else '像'}"
        )
        print(f"会话类型        : {body.get('chattype', '')}  (single=单聊 / group=群聊)")
        print(f"chatid          : {body.get('chatid', '')}")
        print(f"原始内容        : {content!r}")
        print(f"剥离 @ 后       : {strip_mention(content)!r}")
        print(f"会话隔离 key    : {build_session_key(frame, cfg.session.scope)}")

        ok = identity_matches(userid, cfg.identity.pattern)
        print(f"\n身份校验结果    : {'通过' if ok else '不通过'}")
        if not ok:
            print(f"  这个 userid 匹配不上 identity.pattern（{cfg.identity.pattern}）。")
            print("  机器人收到这条消息会直接回 messages.identity_invalid。")
            print("  两种解法：")
            print("    a) userid 就是业务工号，只是正则写严了 → 改 identity.pattern")
            print("    b) userid 是加密串 → 在 hooks.py 实现 resolve_identity 做映射，")
            print("       并把 bot.yaml 的 identity.source 改成 hook")
        print("=" * 70 + "\n")

        reply = (
            f"探测成功\n"
            f"识别到的 userid：`{userid}`\n"
            f"身份校验：{'通过' if ok else '不通过'}\n"
            f"会话类型：{body.get('chattype', '')}"
        )
        await ws_client.reply_stream(frame, generate_req_id("stream"), reply, True)

    ws_client.on("authenticated", on_authenticated)
    ws_client.on("error", on_error)
    ws_client.on("message.text", on_text)

    try:
        ws_client.run()
    except KeyboardInterrupt:
        print("\n已退出")
    return 0


if __name__ == "__main__":
    sys.exit(main())
