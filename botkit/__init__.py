"""
botkit —— 企业微信 Agent 通用骨架

用配置驱动的方式快速搭一个企微 Agent：
- 业务定制集中在 bot.yaml + prompt.md，不需要改框架代码
- LLM function calling 循环 + MCP 工具调用已内置
- 需要特殊逻辑时，用可选的 hooks.py 扩展

命令行：
    python -m botkit new <name>        生成一个新 Agent 骨架
    python -m botkit validate <dir>    校验配置并打印摘要
    python -m botkit run <dir>         启动Agent
"""

__version__ = "1.0.0"

__all__ = ["__version__"]
