"""
可视化配置页面

`python -m botkit ui` 起一个只监听本机的小网站，用来配置 bot：
填密钥、勾选工具、写提示词、实时校验、一键探测企微身份和 MCP 工具。

只绑 127.0.0.1，不做认证 —— 它能读写真实密钥和磁盘文件，
不应该暴露到网络上。
"""

from botkit.ui.server import DEFAULT_HOST, DEFAULT_PORT, serve

__all__ = ["serve", "DEFAULT_HOST", "DEFAULT_PORT"]
