"""
生成带注释的 bot.yaml

可视化配置页面存盘时用这个，而不是 yaml.safe_dump —— 后者会把注释全丢掉。
那些注释是给手改配置的人看的文档，不能因为用过一次页面就没了。

输入是一个和 bot.yaml 结构一致的 dict（值里可以有 ${VAR} 占位符），
输出是排版和注释都固定的 YAML 文本。round-trip 保证：
    yaml.safe_load(dump_bot_yaml(d)) 的内容等价于 d
"""

from __future__ import annotations

from typing import Any

import yaml

from botkit.config import (
    MessagesConfig,
    SessionConfig,
    VALID_LLM_CLIENTS,
    VALID_LOG_LEVELS,
    VALID_SESSION_SCOPES,
)

# messages 里各文案字段的顺序和说明
_MESSAGE_FIELDS: tuple[tuple[str, str], ...] = (
    ("welcome", "用户进入会话时"),
    ("empty_input", "只 @ 了Agent没说内容"),
    ("thinking", "开始处理时先回一句"),
    ("reset_done", "命中重置关键词"),
    ("error", "出错兜底"),
    ("identity_invalid", "身份识别不了"),
    ("tool_rounds_exceeded", "工具调用轮次用尽"),
)


# --------------------------------------------------------------------------
# 标量渲染
# --------------------------------------------------------------------------


def _scalar(value: Any) -> str:
    """
    把一个值渲染成单行 YAML 标量。

    引号交给 PyYAML 判断（该加的会加，比如 'yes'、'123'、含 # 的字符串），
    只把它附带的文档结束符去掉。
    """
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        # 60.0 写成 60，配置文件里更干净
        return str(int(value)) if value.is_integer() else repr(value)

    dumped = yaml.safe_dump(
        str(value), allow_unicode=True, default_flow_style=True, width=10**6
    )
    dumped = dumped.rstrip("\n")
    if dumped.endswith("\n..."):
        dumped = dumped[: -len("\n...")]
    return dumped.strip()


def _kv(key: str, value: Any, indent: int = 0) -> list[str]:
    """渲染 `key: value`。多行字符串用块标量，避免缩进错乱"""
    pad = " " * indent
    if isinstance(value, str) and "\n" in value:
        lines = [f"{pad}{key}: |-"]
        for line in value.split("\n"):
            lines.append(f"{pad}  {line}" if line else "")
        return lines
    return [f"{pad}{key}: {_scalar(value)}"]


def _str_list(key: str, values: list[str], indent: int = 0) -> list[str]:
    """渲染字符串列表。空列表写成 []，非空则每项一行（比 flow 风格好改）"""
    pad = " " * indent
    if not values:
        return [f"{pad}{key}: []"]
    lines = [f"{pad}{key}:"]
    lines.extend(f"{pad}  - {_scalar(v)}" for v in values)
    return lines


def _comment(text: str, indent: int = 0) -> list[str]:
    """把多行说明渲染成注释块"""
    pad = " " * indent
    return [f"{pad}# {line}".rstrip() for line in text.strip("\n").split("\n")]


def _rule(indent: int = 0) -> str:
    return " " * indent + "# " + "-" * 73


def _section(title: str, body: str = "") -> list[str]:
    """带分隔线的段落标题注释"""
    lines = ["", _rule()]
    lines.extend(_comment(title))
    if body:
        lines.extend(_comment(body))
    lines.append(_rule())
    return lines


# --------------------------------------------------------------------------
# 取值辅助
# --------------------------------------------------------------------------


def _get(data: Any, key: str, default: Any = None) -> Any:
    if isinstance(data, dict):
        value = data.get(key, default)
        return default if value is None else value
    return default


def _section_dict(values: dict[str, Any], key: str) -> dict[str, Any]:
    raw = values.get(key)
    return raw if isinstance(raw, dict) else {}


# --------------------------------------------------------------------------
# 各段
# --------------------------------------------------------------------------


def _dump_wecom(values: dict[str, Any]) -> list[str]:
    wecom = _section_dict(values, "wecom")
    lines = _section(
        "企业微信凭证",
        "企微管理后台 → 智能机器人 → API模式 → 长连接\n"
        "用 ${VAR} 占位，真实值放同目录 .env（不提交 git）",
    )
    lines.append("wecom:")
    lines.extend(_kv("bot_id", _get(wecom, "bot_id", "${WECHAT_BOT_ID}"), 2))
    lines.extend(_kv("bot_secret", _get(wecom, "bot_secret", "${WECHAT_BOT_SECRET}"), 2))
    return lines


def _dump_api(values: dict[str, Any]) -> list[str]:
    """对外 chat 接口段。整段可省略 = 不启用，只有配了才写。"""
    api = values.get("api")
    if not isinstance(api, dict) or not api:
        return []
    enabled = bool(api.get("enabled", False))
    has_port = api.get("port") not in (None, "")
    has_token = bool(str(api.get("token") or "").strip())
    if not enabled and not has_port and not has_token:
        return []
    lines = _section(
        "对外 chat 接口",
        "开启后，运行时会同时起一个 HTTP 服务，暴露 POST /chat 供 Dify 或\n"
        "自研门户调用。token 用 ${VAR} 占位，真实值放 .env（不提交 git）。",
    )
    lines.append("api:")
    lines.extend(_kv("enabled", enabled, 2))
    lines.extend(_kv("port", _get(api, "port", 9000), 2))
    lines.extend(_kv("token", _get(api, "token", "${CHAT_API_TOKEN}"), 2))
    return lines


def _dump_llm(values: dict[str, Any]) -> list[str]:
    llm = _section_dict(values, "llm")
    client = _get(llm, "client", "openai")

    lines = _section(
        "大模型",
        "client 可选 " + " / ".join(VALID_LLM_CLIENTS) + "\n"
        "  openai  官方 SDK，自带重试。api_url 填到 /v1 为止\n"
        "  aiohttp 裸 HTTP，用于不规范的兼容端点。api_url 填完整端点\n"
        "max_tool_rounds 是一轮对话最多让模型调几轮工具，防止打转",
    )
    lines.append("llm:")
    lines.extend(_kv("client", client, 2))
    lines.extend(_kv("api_url", _get(llm, "api_url", "${LLM_API_URL}"), 2))
    lines.extend(_kv("api_key", _get(llm, "api_key", "${LLM_API_KEY}"), 2))
    lines.extend(_kv("model", _get(llm, "model", "${LLM_MODEL}"), 2))
    lines.extend(_kv("timeout_seconds", _get(llm, "timeout_seconds", 60), 2))
    lines.extend(_kv("max_tool_rounds", _get(llm, "max_tool_rounds", 6), 2))
    return lines


def _dump_prompt(values: dict[str, Any]) -> list[str]:
    prompt = _section_dict(values, "prompt")
    lines = _section("提示词", "角色、任务、流程约束都写在这个文件里")
    lines.append("prompt:")
    inline = prompt.get("inline")
    if inline:
        lines.extend(_kv("inline", inline, 2))
    else:
        lines.extend(_kv("file", _get(prompt, "file", "prompt.md"), 2))
    return lines


def _dump_skills(values: dict[str, Any]) -> list[str]:
    """技能段。整段可省略 = 不启用。只有配了才写，别给旧 bot 平添空段。"""
    skills = values.get("skills")
    if not isinstance(skills, dict) or not skills:
        return []
    enabled = bool(skills.get("enabled", False))
    files = [str(f).strip() for f in (skills.get("files") or []) if str(f).strip()]
    if not enabled and not files:
        return []
    lines = _section(
        "技能",
        "启用后把 _skills/ 下选中的补充说明拼进系统提示词，帮模型理解业务。\n"
        "文件放在 bots/_skills/，多个 bot 可引用同一份。",
    )
    lines.append("skills:")
    lines.extend(_kv("enabled", enabled, 2))
    lines.extend(_str_list("files", files, 2))
    return lines


def _dump_mcp(values: dict[str, Any]) -> list[str]:
    mcp = _section_dict(values, "mcp")
    servers = mcp.get("servers")
    if not isinstance(servers, list) or not servers:
        servers = [{"name": "main", "url": "${MCP_URL}"}]

    lines = _section(
        "MCP 工具来源",
        "allow_tools 留空 = 该服务端所有工具都暴露给模型。\n"
        "工具多的时候建议配白名单：模型不容易选错，也省 token。\n"
        "identity_header 是把当前用户身份透传给服务端的请求头，留空则不发。",
    )
    lines.append("mcp:")
    lines.append("  servers:")
    for server in servers:
        lines.extend(_kv("- name", _get(server, "name", "main"), 4))
        lines.extend(_kv("url", _get(server, "url", "${MCP_URL}"), 6))
        header = server.get("identity_header")
        if header:
            lines.extend(_kv("identity_header", header, 6))
        headers = server.get("headers")
        if isinstance(headers, dict) and headers:
            lines.append("      headers:")
            for hk, hv in headers.items():
                lines.extend(_kv(str(hk), hv, 8))
        lines.extend(_str_list("allow_tools", list(_get(server, "allow_tools", [])), 6))
        deny = list(_get(server, "deny_tools", []))
        if deny:
            lines.extend(_str_list("deny_tools", deny, 6))
    return lines


def _dump_identity(values: dict[str, Any]) -> list[str]:
    identity = _section_dict(values, "identity")
    lines = _section(
        "身份",
        "pattern 是安全阀：不匹配就直接拒绝，不进模型、不碰工具。留空则不校验。\n"
        "inject 是安全边界：把可信身份强制写进指定工具的指定参数，\n"
        "        覆盖模型生成的值。凡是「代表某人操作」的工具都该配上，\n"
        "        否则模型可能替别人提交。",
    )
    lines.append("identity:")
    lines.extend(_kv("source", _get(identity, "source", "wecom_userid"), 2))

    pattern = identity.get("pattern")
    if pattern:
        lines.extend(_kv("pattern", pattern, 2))
    else:
        lines.extend(_comment("pattern: \"^[LM]\\\\d{6}$\"   # 不校验身份格式", 2))

    inject = identity.get("inject") or []
    if inject:
        lines.append("  inject:")
        for item in inject:
            lines.extend(_kv("- tool", _get(item, "tool", ""), 4))
            lines.extend(_kv("arg", _get(item, "arg", ""), 6))
    else:
        lines.append("  inject: []")
    return lines


def _dump_session(values: dict[str, Any]) -> list[str]:
    session = _section_dict(values, "session")
    defaults = SessionConfig()
    lines = _section(
        "会话上下文",
        "scope 决定谁跟谁共享对话历史，可选 " + " / ".join(VALID_SESSION_SCOPES) + "\n"
        "  group_user 群内按人隔离（多数场景）\n"
        "  group      整群共享一份\n"
        "  user       同一个人跨所有会话共享",
    )
    lines.append("session:")
    lines.extend(_kv("scope", _get(session, "scope", defaults.scope), 2))
    lines.extend(_kv("max_turns", _get(session, "max_turns", defaults.max_turns), 2))
    lines.extend(_kv("ttl_seconds", _get(session, "ttl_seconds", defaults.ttl_seconds), 2))
    return lines


def _dump_messages(values: dict[str, Any]) -> list[str]:
    messages = _section_dict(values, "messages")
    defaults = MessagesConfig()

    width = max(len(key) for key, _ in _MESSAGE_FIELDS)
    what_is_what = "\n".join(
        f"{key.ljust(width)}  {note}" for key, note in _MESSAGE_FIELDS
    )

    lines = _section(
        "文案",
        "没写的用框架默认值。各条什么时候发：\n" + what_is_what,
    )
    lines.append("messages:")
    for key, _note in _MESSAGE_FIELDS:
        lines.extend(_kv(key, _get(messages, key, getattr(defaults, key)), 2))

    # reply_suffix 只在配了非空值时写，避免给每个 bot 平添一行空配置
    suffix = messages.get("reply_suffix")
    if isinstance(suffix, str) and suffix:
        lines.append("")
        lines.extend(_comment("自动追加到每条 LLM 回复末尾（署名等）", 2))
        lines.extend(_kv("reply_suffix", suffix, 2))

    keywords = list(_get(messages, "reset_keywords", defaults.reset_keywords))
    lines.append("")
    lines.extend(_comment("整条消息完全等于其中一个词才算命中重置", 2))
    lines.extend(_str_list("reset_keywords", keywords, 2))

    progress = _get(messages, "tool_progress", {})
    if not isinstance(progress, dict) or not progress:
        progress = dict(defaults.tool_progress)
    progress.setdefault("default", defaults.tool_progress["default"])

    lines.append("")
    lines.append("  tool_progress:")
    # default 放最后，前面按工具名排，读起来顺
    for tool in sorted(k for k in progress if k != "default"):
        lines.extend(_kv(tool, progress[tool], 4))
    lines.extend(_kv("default", progress["default"], 4))
    return lines


def _dump_intercepts(values: dict[str, Any]) -> list[str]:
    """前置拦截段。整段可省略 = 不启用，只有配了规则才写。"""
    rules = values.get("intercepts")
    if not isinstance(rules, list) or not rules:
        return []
    lines = _section(
        "前置拦截",
        "命中就直接回 reply，不进 LLM —— 快、省 token、答案稳定。\n"
        "match 精确匹配关键词列表；match_regex 正则；两者至少配一个。\n"
        "优先级：重置关键词 > intercepts > on_message hook > LLM。",
    )
    lines.append("intercepts:")
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        match = [str(m).strip() for m in (rule.get("match") or []) if str(m).strip()]
        regex = str(rule.get("match_regex") or "").strip()
        reply = str(rule.get("reply") or "")
        # 列表项用单独的 "-" 起头，项内的键统一 6 缩进，
        # 这样多行 reply 的块标量缩进不会和列表项对不上。
        lines.append("  -")
        lines.extend(_kv("reply", reply, 6))
        if match:
            lines.extend(_str_list("match", match, 6))
        if regex:
            lines.extend(_kv("match_regex", regex, 6))
    return lines


def _dump_hooks(values: dict[str, Any]) -> list[str]:
    hooks = _section_dict(values, "hooks")
    lines = _section(
        "代码扩展点（可选）",
        "纯配置搞不定的需求才用。文件里全注释掉也没问题。",
    )
    lines.append("hooks:")
    lines.extend(_kv("file", _get(hooks, "file", "hooks.py"), 2))
    return lines


def _dump_logging(values: dict[str, Any]) -> list[str]:
    log = _section_dict(values, "logging")
    level = str(_get(log, "level", "INFO")).upper()
    if level not in VALID_LOG_LEVELS:
        level = "INFO"
    lines = _section("日志", "排查问题时改成 DEBUG，能看到每次模型请求和工具调用")
    lines.append("logging:")
    lines.extend(_kv("level", level, 2))
    return lines


# --------------------------------------------------------------------------
# 入口
# --------------------------------------------------------------------------


def dump_bot_yaml(values: dict[str, Any], *, generated_note: bool = True) -> str:
    """
    把配置 dict 渲染成带注释的 bot.yaml 文本。

    generated_note=True 会在开头加一句说明，告诉后来的人这个文件
    既可以用页面改也可以手改。
    """
    name = values.get("name") or "my-bot"

    lines: list[str] = [f"# {name}"]
    if generated_note:
        lines.extend(
            _comment(
                "\n"
                "可以用可视化页面改（python -m botkit ui），也可以直接手改这个文件。\n"
                "两种方式都行，注释会被保留。\n"
                "\n"
                "改完用 python -m botkit validate <本目录> 检查。"
            )
        )
    lines.append("")
    lines.extend(_kv("name", name))

    for dump in (
        _dump_wecom,
        _dump_api,
        _dump_llm,
        _dump_prompt,
        _dump_skills,
        _dump_mcp,
        _dump_identity,
        _dump_session,
        _dump_messages,
        _dump_intercepts,
        _dump_hooks,
        _dump_logging,
    ):
        lines.extend(dump(values))

    text = "\n".join(lines).rstrip() + "\n"
    return text
