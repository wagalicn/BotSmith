"""
日志配置

格式里带上 bot 名字。虽然是一进程一 bot 的部署方式，但同一台机器上
往往跑着好几个 bot，日志汇到一起时能一眼看出是哪个在说话。
"""

from __future__ import annotations

import logging

# 这些第三方库在 INFO 级别很吵（每个 HTTP 请求都打一行），
# 除非我们自己开了 DEBUG，否则压到 WARNING。
NOISY_LOGGERS = (
    "httpx2",
    "httpx",
    "httpcore",
    "openai",
    "aiohttp.access",
    "mcp",
    "asyncio",
)


def setup_logging(bot_name: str, level: str = "INFO") -> None:
    """
    配置根日志。多次调用是安全的（会替换已有的 handler）。

    level 是 botkit 自己和 bot 代码的级别；第三方库的噪音单独压制。
    """
    numeric = getattr(logging, level.upper(), logging.INFO)

    root = logging.getLogger()
    # 清掉已有 handler，避免重复配置时同一条日志打多遍
    for handler in list(root.handlers):
        root.removeHandler(handler)

    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter(
            fmt=f"%(asctime)s [{bot_name}] %(levelname)-7s [%(name)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    root.addHandler(handler)
    root.setLevel(numeric)

    if numeric > logging.DEBUG:
        for name in NOISY_LOGGERS:
            logging.getLogger(name).setLevel(logging.WARNING)
