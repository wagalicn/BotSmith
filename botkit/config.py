"""
botkit 配置加载层

职责：
- 读取 bot 目录下的 bot.yaml
- 加载同目录 .env，并把 YAML 里的 ${VAR} 占位符替换成环境变量
- 校验字段合法性，转成带类型的 dataclass
- 提示词从独立文件（prompt.md）或 inline 读入

设计约定：
- bot.yaml 里只写非敏感的结构化配置，密钥统一用 ${VAR} 占位，实际值放 .env
- 所有相对路径都相对 bot.yaml 所在目录解析
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

logger = logging.getLogger("botkit.config")

# ${VAR} 占位符
_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")

VALID_LLM_CLIENTS = ("openai", "aiohttp")
VALID_SESSION_SCOPES = ("group_user", "group", "user")
VALID_IDENTITY_SOURCES = ("wecom_userid", "hook")
VALID_LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")

# 顶层允许的 key，写错会直接报错而不是被静默忽略
_TOP_LEVEL_KEYS = {
    "name",
    "wecom",
    "api",
    "llm",
    "prompt",
    "skills",
    "mcp",
    "identity",
    "session",
    "messages",
    "intercepts",
    "hooks",
    "logging",
}


class ConfigError(Exception):
    """配置文件有问题时抛出，消息里必须能定位到具体字段"""


# --------------------------------------------------------------------------
# 数据结构
# --------------------------------------------------------------------------


@dataclass
class WecomConfig:
    # 只做 chat API、不连企微时，这两项可以留空
    bot_id: str = ""
    bot_secret: str = ""


@dataclass
class ApiConfig:
    """对外 HTTP chat 接口（/chat）。用于对接 Dify 或自研门户。"""

    enabled: bool = False
    port: int = 9000
    # 请求头 Authorization: Bearer <token> 的校验值。留空 = 不鉴权。
    token: str = ""


@dataclass
class LlmConfig:
    api_url: str
    api_key: str
    model: str
    client: str = "openai"
    timeout_seconds: float = 60.0
    max_tool_rounds: int = 6


@dataclass
class McpServerConfig:
    name: str
    url: str
    identity_header: str | None = "X-MCP-Identity"
    allow_tools: list[str] = field(default_factory=list)
    deny_tools: list[str] = field(default_factory=list)
    # 额外固定请求头，探测和运行时都会带上。用于服务端要求的鉴权，
    # 比如 {"Authorization": "Bearer xxx"}。值可用 ${VAR} 占位存 .env。
    headers: dict[str, str] = field(default_factory=dict)


@dataclass
class McpConfig:
    servers: list[McpServerConfig] = field(default_factory=list)


@dataclass
class IdentityInject:
    """把可信身份强制写进某个工具的某个参数，覆盖 LLM 生成的值"""

    tool: str
    arg: str


@dataclass
class IdentityConfig:
    source: str = "wecom_userid"
    pattern: str | None = None
    inject: list[IdentityInject] = field(default_factory=list)


@dataclass
class SessionConfig:
    scope: str = "group_user"
    max_turns: int = 10
    ttl_seconds: int = 1800


@dataclass
class MessagesConfig:
    welcome: str = "你好，@我描述一下你的问题，我来帮你处理。"
    empty_input: str = "请描述一下你的问题。"
    thinking: str = "正在处理，请稍候..."
    reset_done: str = "已清空对话，我们重新开始吧。"
    error: str = "抱歉，处理过程中发生异常，请查看Agent日志。"
    identity_invalid: str = "无法识别你的工号，请联系管理员确认企微账号配置。"
    tool_rounds_exceeded: str = "处理超时（工具调用轮次过多），请把问题描述得更具体一些再试。"
    # 自动追加到每条 LLM 正常回复末尾的固定后缀（署名、帮助入口等）。
    # 只作用于模型产生的回复，不影响报错、拦截等固定文案。留空 = 不追加。
    reply_suffix: str = ""
    reset_keywords: list[str] = field(
        default_factory=lambda: ["重新开始", "重来", "清空对话", "重置", "reset"]
    )
    tool_progress: dict[str, str] = field(
        default_factory=lambda: {"default": "正在调用工具..."}
    )

    def progress_for(self, tool_name: str) -> str:
        """取某工具的进度文案，没配就回退到 default"""
        return self.tool_progress.get(
            tool_name, self.tool_progress.get("default", "正在调用工具...")
        )


@dataclass
class Intercept:
    """
    前置拦截规则：命中就直接用 reply 回复，不进 LLM。

    match       精确匹配（去空格后完全相等）的关键词列表
    match_regex 正则匹配（用 re.search，即匹配到子串就算命中）
    reply       命中时的回复文本

    match 和 match_regex 至少配一个；两个都配时任一命中即可。
    """

    reply: str
    match: list[str] = field(default_factory=list)
    match_regex: str | None = None

    def hit(self, text: str) -> bool:
        """text 是否命中本规则。text 已经过 strip_mention 处理"""
        stripped = text.strip()
        if stripped in self.match:
            return True
        if self.match_regex is not None and re.search(self.match_regex, stripped):
            return True
        return False


@dataclass
class BotConfig:
    name: str
    bot_dir: Path
    wecom: WecomConfig
    llm: LlmConfig
    prompt_text: str
    mcp: McpConfig
    identity: IdentityConfig
    session: SessionConfig
    messages: MessagesConfig
    hooks_file: Path | None
    log_level: str
    prompt_source: str
    # 前置拦截规则（可选，默认无）。命中就直接回固定文案，不进 LLM。
    intercepts: list[Intercept] = field(default_factory=list)
    # 技能：启用且选了文件时，把共享 skill 文档拼进系统提示词。
    skills_enabled: bool = False
    skills_files: list[str] = field(default_factory=list)
    skills_text: str = ""
    # 对外 chat HTTP 接口
    api: ApiConfig = field(default_factory=ApiConfig)

    def inject_for(self, tool_name: str) -> list[str]:
        """该工具需要被强制注入身份的参数名列表"""
        return [i.arg for i in self.identity.inject if i.tool == tool_name]

    @property
    def has_wecom(self) -> bool:
        """是否配了企微凭证（配了才连企微长连接）"""
        return bool(self.wecom.bot_id and self.wecom.bot_secret)


# --------------------------------------------------------------------------
# ${VAR} 替换
# --------------------------------------------------------------------------


def _substitute_env(node: Any, path: str, missing: list[str]) -> Any:
    """
    递归替换配置树里的 ${VAR}。
    缺失的变量收集到 missing（而不是第一个就抛错），一次性把问题全报出来。
    """
    if isinstance(node, str):
        found: list[str] = []

        def _replace(m: re.Match[str]) -> str:
            var = m.group(1)
            val = os.environ.get(var)
            if val is None:
                found.append(var)
                return ""
            return val

        result = _ENV_PATTERN.sub(_replace, node)
        for var in found:
            missing.append(f"{path} 引用了未定义的环境变量 ${{{var}}}")
        return result

    if isinstance(node, dict):
        return {
            k: _substitute_env(v, f"{path}.{k}" if path else str(k), missing)
            for k, v in node.items()
        }

    if isinstance(node, list):
        return [
            _substitute_env(v, f"{path}[{i}]", missing) for i, v in enumerate(node)
        ]

    return node


# --------------------------------------------------------------------------
# 取值辅助
# --------------------------------------------------------------------------


def _as_dict(raw: Any, path: str) -> dict[str, Any]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ConfigError(f"{path} 应该是一个对象（键值对），当前是 {type(raw).__name__}")
    return raw


def _require_str(data: dict[str, Any], key: str, path: str) -> str:
    val = data.get(key)
    if val is None or (isinstance(val, str) and not val.strip()):
        raise ConfigError(f"缺少必填配置 {path}.{key}（若是密钥，请检查 .env 里对应的变量是否已填）")
    if not isinstance(val, str):
        raise ConfigError(f"{path}.{key} 应该是字符串，当前是 {type(val).__name__}")
    return val.strip()


def _opt_str(data: dict[str, Any], key: str, path: str, default: str | None) -> str | None:
    if key not in data or data[key] is None:
        return default
    val = data[key]
    if not isinstance(val, str):
        raise ConfigError(f"{path}.{key} 应该是字符串，当前是 {type(val).__name__}")
    val = val.strip()
    return val or None


def _opt_number(data: dict[str, Any], key: str, path: str, default: float) -> float:
    if key not in data or data[key] is None:
        return default
    val = data[key]
    if isinstance(val, bool) or not isinstance(val, (int, float)):
        raise ConfigError(f"{path}.{key} 应该是数字，当前是 {type(val).__name__}")
    if val <= 0:
        raise ConfigError(f"{path}.{key} 必须大于 0，当前是 {val}")
    return float(val)


def _opt_int(data: dict[str, Any], key: str, path: str, default: int) -> int:
    if key not in data or data[key] is None:
        return default
    val = data[key]
    if isinstance(val, bool) or not isinstance(val, int):
        raise ConfigError(f"{path}.{key} 应该是整数，当前是 {type(val).__name__}")
    if val <= 0:
        raise ConfigError(f"{path}.{key} 必须大于 0，当前是 {val}")
    return val


def _opt_str_list(data: dict[str, Any], key: str, path: str) -> list[str]:
    if key not in data or data[key] is None:
        return []
    val = data[key]
    if not isinstance(val, list):
        raise ConfigError(f"{path}.{key} 应该是字符串列表，当前是 {type(val).__name__}")
    out: list[str] = []
    for i, item in enumerate(val):
        if not isinstance(item, str):
            raise ConfigError(f"{path}.{key}[{i}] 应该是字符串，当前是 {type(item).__name__}")
        item = item.strip()
        if item:
            out.append(item)
    return out


def _opt_str_dict(data: dict[str, Any], key: str, path: str) -> dict[str, str]:
    """解析一个 {字符串: 字符串} 的可选映射，比如 MCP 服务端的额外请求头。"""
    if key not in data or data[key] is None:
        return {}
    val = data[key]
    if not isinstance(val, dict):
        raise ConfigError(f"{path}.{key} 应该是键值对（字符串到字符串），当前是 {type(val).__name__}")
    out: dict[str, str] = {}
    for hk, hv in val.items():
        if not isinstance(hk, str) or not hk.strip():
            raise ConfigError(f"{path}.{key} 里有个请求头名是空的或不是字符串")
        if not isinstance(hv, str):
            raise ConfigError(f"{path}.{key}.{hk} 的值应该是字符串，当前是 {type(hv).__name__}")
        out[hk.strip()] = hv
    return out


def _reject_unknown(data: dict[str, Any], allowed: set[str], path: str) -> None:
    unknown = sorted(set(data) - allowed)
    if unknown:
        hint = "、".join(sorted(allowed))
        raise ConfigError(
            f"{path} 下有无法识别的配置项：{'、'.join(unknown)}。"
            f"可用的配置项是：{hint}"
        )


def _one_of(value: str, choices: tuple[str, ...], path: str) -> str:
    if value not in choices:
        raise ConfigError(
            f"{path} 只能是 {'、'.join(choices)} 之一，当前是 {value!r}"
        )
    return value


# --------------------------------------------------------------------------
# 各段解析
# --------------------------------------------------------------------------


def _parse_wecom(raw: Any) -> WecomConfig:
    # wecom 段整段可省略：只做 chat API、不连企微时不需要它。
    data = _as_dict(raw, "wecom")
    _reject_unknown(data, {"bot_id", "bot_secret"}, "wecom")
    return WecomConfig(
        bot_id=_opt_str(data, "bot_id", "wecom", "") or "",
        bot_secret=_opt_str(data, "bot_secret", "wecom", "") or "",
    )


def _parse_api(raw: Any) -> ApiConfig:
    """对外 chat 接口配置。整段可省略 = 不启用。"""
    data = _as_dict(raw, "api")
    if not data:
        return ApiConfig()
    _reject_unknown(data, {"enabled", "port", "token"}, "api")
    port = ApiConfig().port
    if data.get("port") is not None:
        port = _opt_int(data, "port", "api", ApiConfig().port)
    return ApiConfig(
        enabled=bool(data.get("enabled", False)),
        port=port,
        token=_opt_str(data, "token", "api", "") or "",
    )


def _parse_llm(raw: Any) -> LlmConfig:
    data = _as_dict(raw, "llm")
    if not data:
        raise ConfigError("缺少必填配置段 llm（需要 api_url、api_key、model）")
    _reject_unknown(
        data,
        {"client", "api_url", "api_key", "model", "timeout_seconds", "max_tool_rounds"},
        "llm",
    )
    client = _one_of((_opt_str(data, "client", "llm", "openai") or "openai"), VALID_LLM_CLIENTS, "llm.client")
    api_url = _require_str(data, "api_url", "llm")

    # openai 后端要的是 base_url（到 /v1 为止），填成 /chat/completions 会 404
    if client == "openai" and api_url.rstrip("/").endswith("/chat/completions"):
        raise ConfigError(
            "llm.client=openai 时，llm.api_url 应该填到 /v1 为止（不含 /chat/completions），"
            "官方 SDK 会自己拼接路径。当前值：" + api_url + "\n"
            "  改成： " + api_url.rstrip("/")[: -len("/chat/completions")] + "\n"
            "  或者把 llm.client 改成 aiohttp（裸 HTTP 后端接受完整 URL）"
        )

    return LlmConfig(
        client=client,
        api_url=api_url,
        api_key=_require_str(data, "api_key", "llm"),
        model=_require_str(data, "model", "llm"),
        timeout_seconds=_opt_number(data, "timeout_seconds", "llm", 60.0),
        max_tool_rounds=_opt_int(data, "max_tool_rounds", "llm", 6),
    )


def _parse_prompt(raw: Any, bot_dir: Path) -> tuple[str, str]:
    """返回 (提示词正文, 来源描述)"""
    data = _as_dict(raw, "prompt")
    if not data:
        raise ConfigError("缺少必填配置段 prompt（用 file 指向提示词文件，或用 inline 直接写）")
    _reject_unknown(data, {"file", "inline"}, "prompt")

    has_file = bool(data.get("file"))
    has_inline = bool(data.get("inline"))
    if has_file and has_inline:
        raise ConfigError("prompt.file 和 prompt.inline 只能配一个")
    if not has_file and not has_inline:
        raise ConfigError("prompt 段需要 file 或 inline 其中之一")

    if has_inline:
        text = str(data["inline"]).strip()
        if not text:
            raise ConfigError("prompt.inline 不能是空内容")
        return text, "inline"

    rel = str(data["file"]).strip()
    prompt_path = (bot_dir / rel).resolve()
    return _read_prompt_file(prompt_path), str(prompt_path)


def _read_prompt_file(prompt_path: Path) -> str:
    """读提示词文件。启动加载和运行中热加载共用，读完即关闭文件。"""
    if not prompt_path.is_file():
        raise ConfigError(f"prompt.file 指向的文件不存在：{prompt_path}")
    text = prompt_path.read_text(encoding="utf-8").strip()
    if not text:
        raise ConfigError(f"提示词文件是空的：{prompt_path}")
    return text


# 共享 skill 目录名（在 bots/ 下，多个 bot 共用）
SKILLS_DIRNAME = "_skills"


def skills_dir_for(bot_dir: Path) -> Path:
    """某个 bot 对应的共享 skill 目录：bots/_skills/"""
    return bot_dir.parent / SKILLS_DIRNAME


def _parse_skills(raw: Any, bot_dir: Path) -> tuple[bool, list[str], str]:
    """
    解析 skills 段，返回 (enabled, files, 拼好的正文)。

    skills:
      enabled: true
      files: [it-faq.md, glossary.md]

    整段可省略 = 不启用。enabled=false 时也不读文件。
    文件放在共享目录 bots/_skills/，多个 bot 可引用同一份。
    """
    data = _as_dict(raw, "skills")
    if not data:
        return False, [], ""
    _reject_unknown(data, {"enabled", "files"}, "skills")

    enabled = bool(data.get("enabled", False))
    files = _opt_str_list(data, "files", "skills")

    if not enabled or not files:
        return enabled, files, ""

    return enabled, files, _read_skill_files(bot_dir, files)


def _skill_path(bot_dir: Path, fname: str) -> Path:
    """技能文件名 → 共享目录下的绝对路径。只允许纯文件名，防止 ../ 逃出共享目录"""
    sdir = skills_dir_for(bot_dir)
    if "/" in fname or "\\" in fname or fname.startswith("."):
        raise ConfigError(f"skills.files 里的名字不合法：{fname}（只能是 _skills/ 下的文件名）")
    fpath = (sdir / fname).resolve()
    if not str(fpath).startswith(str(sdir.resolve())):
        raise ConfigError(f"skills.files 里的名字越界了：{fname}")
    return fpath


def _read_skill_files(bot_dir: Path, files: list[str]) -> str:
    """
    按顺序读技能文件并拼成正文。启动加载和运行中热加载共用。

    每个文件都是打开、读完、立即关闭，不长期占着文件句柄 —— Windows 下被占着的
    文件无法被原子替换，会让发布方（如 Demand 系统）的替换失败。
    """
    sdir = skills_dir_for(bot_dir)
    parts: list[str] = []
    for fname in files:
        fpath = _skill_path(bot_dir, fname)
        if not fpath.is_file():
            raise ConfigError(f"技能文件不存在：{fpath}（放到 {sdir} 下，或在页面上取消勾选）")
        content = fpath.read_text(encoding="utf-8").strip()
        if content:
            parts.append(f"## {fname}\n\n{content}")
    return "\n\n".join(parts)


def _parse_mcp(raw: Any) -> McpConfig:
    # mcp 段可选：不配 servers 时Agent就是纯 LLM 对话，不接任何工具。
    data = _as_dict(raw, "mcp")
    if not data:
        return McpConfig(servers=[])
    _reject_unknown(data, {"servers"}, "mcp")

    servers_raw = data.get("servers")
    if not servers_raw:
        return McpConfig(servers=[])
    if not isinstance(servers_raw, list):
        raise ConfigError(f"mcp.servers 应该是列表，当前是 {type(servers_raw).__name__}")

    servers: list[McpServerConfig] = []
    seen: set[str] = set()
    for i, item in enumerate(servers_raw):
        path = f"mcp.servers[{i}]"
        sd = _as_dict(item, path)
        _reject_unknown(
            sd,
            {"name", "url", "identity_header", "allow_tools", "deny_tools", "headers"},
            path,
        )

        # 没填 url 的条目视为「还没配好的空行」，跳过而不是报错。
        # 页面新建 bot 时会预置一个空的 server 行，纯对话 bot 用不到工具、
        # 不会去填地址，这种情况不应该拦住保存。
        # 注意：直接读原始值判空，不走 _opt_str —— 后者对空串会返回 None。
        url_raw = sd.get("url")
        if url_raw is not None and not isinstance(url_raw, str):
            raise ConfigError(f"{path}.url 应该是字符串，当前是 {type(url_raw).__name__}")
        url = (url_raw or "").strip()
        name_val = sd.get("name")
        name_raw = (name_val or "").strip() if isinstance(name_val, str) else ""
        if not url:
            if name_raw:
                logger.info(
                    "MCP 服务端 %s 没填 url，视为未启用已跳过", name_raw
                )
            continue

        # 填了 url 就必须有 name（要用于工具路由和日志）
        name = _require_str(sd, "name", path)
        if name in seen:
            raise ConfigError(f"{path}.name 重复：{name}（每个 MCP 服务端的 name 要唯一）")
        seen.add(name)
        servers.append(
            McpServerConfig(
                name=name,
                url=url,
                identity_header=_opt_str(sd, "identity_header", path, "X-MCP-Identity"),
                allow_tools=_opt_str_list(sd, "allow_tools", path),
                deny_tools=_opt_str_list(sd, "deny_tools", path),
                headers=_opt_str_dict(sd, "headers", path),
            )
        )
    return McpConfig(servers=servers)


def _parse_identity(raw: Any) -> IdentityConfig:
    data = _as_dict(raw, "identity")
    if not data:
        return IdentityConfig()
    _reject_unknown(data, {"source", "pattern", "inject"}, "identity")

    source = _one_of(
        (_opt_str(data, "source", "identity", "wecom_userid") or "wecom_userid"),
        VALID_IDENTITY_SOURCES,
        "identity.source",
    )

    pattern = _opt_str(data, "pattern", "identity", None)
    if pattern is not None:
        try:
            re.compile(pattern)
        except re.error as e:
            raise ConfigError(f"identity.pattern 不是合法的正则表达式：{e}") from e

    inject: list[IdentityInject] = []
    inject_raw = data.get("inject") or []
    if not isinstance(inject_raw, list):
        raise ConfigError(f"identity.inject 应该是列表，当前是 {type(inject_raw).__name__}")
    for i, item in enumerate(inject_raw):
        path = f"identity.inject[{i}]"
        idata = _as_dict(item, path)
        _reject_unknown(idata, {"tool", "arg"}, path)
        inject.append(
            IdentityInject(
                tool=_require_str(idata, "tool", path),
                arg=_require_str(idata, "arg", path),
            )
        )

    return IdentityConfig(source=source, pattern=pattern, inject=inject)


def _parse_session(raw: Any) -> SessionConfig:
    data = _as_dict(raw, "session")
    if not data:
        return SessionConfig()
    _reject_unknown(data, {"scope", "max_turns", "ttl_seconds"}, "session")
    return SessionConfig(
        scope=_one_of(
            (_opt_str(data, "scope", "session", "group_user") or "group_user"),
            VALID_SESSION_SCOPES,
            "session.scope",
        ),
        max_turns=_opt_int(data, "max_turns", "session", 10),
        ttl_seconds=_opt_int(data, "ttl_seconds", "session", 1800),
    )


def _parse_messages(raw: Any) -> MessagesConfig:
    data = _as_dict(raw, "messages")
    defaults = MessagesConfig()
    if not data:
        return defaults

    text_keys = {
        "welcome",
        "empty_input",
        "thinking",
        "reset_done",
        "error",
        "identity_invalid",
        "tool_rounds_exceeded",
    }
    _reject_unknown(
        data,
        text_keys | {"reply_suffix", "reset_keywords", "tool_progress"},
        "messages",
    )

    kwargs: dict[str, Any] = {}
    for key in text_keys:
        val = _opt_str(data, key, "messages", getattr(defaults, key))
        kwargs[key] = val if val else getattr(defaults, key)

    # reply_suffix 不 strip：署名常带前导换行（如 "\n\n—— 名字"），strip 会吃掉。
    suffix_raw = data.get("reply_suffix")
    if suffix_raw is None:
        kwargs["reply_suffix"] = defaults.reply_suffix
    elif isinstance(suffix_raw, str):
        kwargs["reply_suffix"] = suffix_raw
    else:
        raise ConfigError(
            f"messages.reply_suffix 应该是字符串，当前是 {type(suffix_raw).__name__}"
        )

    if "reset_keywords" in data and data["reset_keywords"] is not None:
        kwargs["reset_keywords"] = _opt_str_list(data, "reset_keywords", "messages")
    else:
        kwargs["reset_keywords"] = defaults.reset_keywords

    tp_raw = data.get("tool_progress")
    if tp_raw is None:
        kwargs["tool_progress"] = defaults.tool_progress
    else:
        tp = _as_dict(tp_raw, "messages.tool_progress")
        progress: dict[str, str] = {}
        for k, v in tp.items():
            if not isinstance(v, str):
                raise ConfigError(
                    f"messages.tool_progress.{k} 应该是字符串，当前是 {type(v).__name__}"
                )
            progress[str(k)] = v
        progress.setdefault("default", defaults.tool_progress["default"])
        kwargs["tool_progress"] = progress

    return MessagesConfig(**kwargs)


def _parse_intercepts(raw: Any) -> list[Intercept]:
    """
    前置拦截规则（可选）。命中就直接回固定文案，不进 LLM。

    每项：
      match       精确匹配的关键词列表（可选）
      match_regex 正则（可选）
      reply       命中时的回复（必填）
    match / match_regex 至少要有一个。
    """
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ConfigError(f"intercepts 应该是列表，当前是 {type(raw).__name__}")

    out: list[Intercept] = []
    for i, item in enumerate(raw):
        path = f"intercepts[{i}]"
        idata = _as_dict(item, path)
        _reject_unknown(idata, {"match", "match_regex", "reply"}, path)

        reply = _require_str(idata, "reply", path)
        match = _opt_str_list(idata, "match", path)
        match_regex = _opt_str(idata, "match_regex", path, None)

        if not match and not match_regex:
            raise ConfigError(
                f"{path} 至少要配 match（关键词列表）或 match_regex（正则）中的一个"
            )
        if match_regex is not None:
            try:
                re.compile(match_regex)
            except re.error as e:
                raise ConfigError(f"{path}.match_regex 不是合法的正则表达式：{e}") from e

        out.append(Intercept(reply=reply, match=match, match_regex=match_regex))
    return out


def _parse_hooks(raw: Any, bot_dir: Path) -> Path | None:
    """
    hooks 段省略时，自动在 bot 目录找 hooks.py（找不到就是没有 hook）。
    显式配了但文件不存在时不报错（按约定跳过），但会打 WARNING 提醒，避免静默失效。
    """
    data = _as_dict(raw, "hooks")
    if not data:
        implicit = bot_dir / "hooks.py"
        return implicit if implicit.is_file() else None

    _reject_unknown(data, {"file"}, "hooks")
    rel = _opt_str(data, "file", "hooks", None)
    if not rel:
        return None

    path = (bot_dir / rel).resolve()
    if not path.is_file():
        logger.warning("hooks.file 指向的文件不存在，将不加载任何 hook：%s", path)
        return None
    return path


def _parse_logging(raw: Any) -> str:
    data = _as_dict(raw, "logging")
    if not data:
        return "INFO"
    _reject_unknown(data, {"level"}, "logging")
    level = (_opt_str(data, "level", "logging", "INFO") or "INFO").upper()
    return _one_of(level, VALID_LOG_LEVELS, "logging.level")


# --------------------------------------------------------------------------
# 入口
# --------------------------------------------------------------------------


def resolve_bot_dir(bot_dir: str | Path) -> Path:
    path = Path(bot_dir).expanduser()
    if not path.is_absolute():
        path = (Path.cwd() / path).resolve()
    else:
        path = path.resolve()
    if not path.is_dir():
        raise ConfigError(f"bot 目录不存在：{path}")
    if not (path / "bot.yaml").is_file():
        raise ConfigError(f"目录里没有 bot.yaml：{path}")
    return path


def _load_env_file(env_file: Path) -> None:
    """
    加载 bot 目录下的 .env。

    已经存在的进程环境变量优先（override=False），这样容器/CI 可以直接注入
    密钥而不用改文件。但这有个坑：shell 里残留一个同名变量，就会静默盖掉
    .env 里的值，人还以为改生效了。所以被盖住的变量要明确打出来。
    """
    from dotenv import dotenv_values

    try:
        file_vars = dotenv_values(env_file)
    except Exception:
        file_vars = {}

    shadowed = [
        name
        for name, value in file_vars.items()
        if value is not None and name in os.environ and os.environ[name] != value
    ]

    load_dotenv(env_file, override=False)
    logger.debug("已加载环境变量文件 %s", env_file)

    if shadowed:
        logger.warning(
            "%s 里这些变量被已存在的进程环境变量盖住了，用的是环境里的值："
            "%s。如果不是有意的（比如容器注入），检查一下 shell 里是不是残留了同名变量",
            env_file,
            "、".join(sorted(shadowed)),
        )


def load_bot_config(bot_dir: str | Path, *, load_env: bool = True) -> BotConfig:
    """
    加载一个 bot 目录的配置。

    bot_dir 里应该有 bot.yaml，通常还有 prompt.md / .env / hooks.py。
    load_env=False 用于测试，跳过 .env 文件加载（只用进程已有的环境变量）。
    """
    path = resolve_bot_dir(bot_dir)
    config_path = path / "bot.yaml"

    if load_env:
        env_file = path / ".env"
        if env_file.is_file():
            _load_env_file(env_file)
        else:
            logger.debug("bot 目录下没有 .env，只使用进程环境变量：%s", path)

    try:
        raw_text = config_path.read_text(encoding="utf-8")
    except OSError as e:
        raise ConfigError(f"读不到配置文件 {config_path}：{e}") from e

    try:
        raw = yaml.safe_load(raw_text)
    except yaml.YAMLError as e:
        raise ConfigError(f"{config_path} 不是合法的 YAML：{e}") from e

    if raw is None:
        raise ConfigError(f"配置文件是空的：{config_path}")
    if not isinstance(raw, dict):
        raise ConfigError(f"{config_path} 顶层应该是键值对，当前是 {type(raw).__name__}")

    # 占位符替换：把所有缺失的环境变量一次性报出来
    missing: list[str] = []
    raw = _substitute_env(raw, "", missing)
    if missing:
        detail = "\n".join(f"  - {m}" for m in missing)
        raise ConfigError(
            f"{config_path} 里有占位符没有对应的环境变量：\n{detail}\n"
            f"请在 {path / '.env'} 里补上这些变量（可参考同目录的 .env.example）"
        )

    _reject_unknown(raw, _TOP_LEVEL_KEYS, "配置文件顶层")

    name = _require_str(raw, "name", "配置文件顶层").replace(" ", "-")
    prompt_text, prompt_source = _parse_prompt(raw.get("prompt"), path)
    skills_enabled, skills_files, skills_text = _parse_skills(raw.get("skills"), path)
    wecom = _parse_wecom(raw.get("wecom"))
    api = _parse_api(raw.get("api"))

    # 一个 bot 至少得有一种对外方式：连企微，或开 chat 接口。
    has_wecom = bool(wecom.bot_id and wecom.bot_secret)
    if not has_wecom and not api.enabled:
        raise ConfigError(
            "这个 bot 既没填企微凭证、也没开对外接口 —— 至少要有一种：\n"
            "  - 在「密钥」页填企微 bot_id / bot_secret（连企微群使用），或\n"
            "  - 在「对外接口」页开启 chat 接口（给 Dify / 门户调用）"
        )

    return BotConfig(
        name=name,
        bot_dir=path,
        wecom=wecom,
        llm=_parse_llm(raw.get("llm")),
        prompt_text=prompt_text,
        prompt_source=prompt_source,
        mcp=_parse_mcp(raw.get("mcp")),
        identity=_parse_identity(raw.get("identity")),
        session=_parse_session(raw.get("session")),
        messages=_parse_messages(raw.get("messages")),
        intercepts=_parse_intercepts(raw.get("intercepts")),
        hooks_file=_parse_hooks(raw.get("hooks"), path),
        log_level=_parse_logging(raw.get("logging")),
        skills_enabled=skills_enabled,
        skills_files=skills_files,
        skills_text=skills_text,
        api=api,
    )


# --------------------------------------------------------------------------
# 运行中热加载提示词 / 技能文件
# --------------------------------------------------------------------------

class ContentWatcher:
    """
    盯着提示词文件和技能文件，有变化就把新内容写回配置对象。

    背景：技能文件（如 Demand 系统发布的锚点集）会在 Agent 运行期间被更新。
    只在启动时读一次的话，要重启 Agent 才能看到新内容，而且配置页的预览
    （每次都重读）会和线上结果对不上。

    做法：每次拼 system prompt 前调用 refresh()，只取一下各文件的修改时间和大小，
    和上次比较；没变就什么都不读，变了才重新读。

    约定：
    - 只读 bot.yaml 里明确列出的文件，不扫描目录，发布方的临时文件
      （如 .xxx.md.tmp）不会被读到。
    - 读文件都是打开、读完、立即关闭，不长期占着文件。
    - 读失败（文件缺失、内容为空、编码错误等）时保留上一版内容，不让请求失败；
      同一个失败状态只记一次 WARNING，下次请求还会重试，文件恢复后自动生效。
    - 只热加载提示词正文和技能正文。bot.yaml 本身的改动（比如新勾选一个技能）
      仍需重启。
    - 第一次 refresh() 一定会重读一遍，避免启动加载与建立基线之间文件被替换造成的遗漏。
    """

    def __init__(self, config: BotConfig):
        self.config = config
        self._sigs: dict[str, Any] | None = None
        self._failed_sigs: dict[str, Any] | None = None

    def _prompt_path(self) -> Path | None:
        if self.config.prompt_source == "inline":
            return None
        return Path(self.config.prompt_source)

    def _skill_files(self) -> list[str]:
        cfg = self.config
        if not (cfg.skills_enabled and cfg.skills_files):
            return []
        return list(cfg.skills_files)

    def _signature(self) -> dict[str, Any]:
        paths: list[Path] = []
        prompt_path = self._prompt_path()
        if prompt_path is not None:
            paths.append(prompt_path)
        for fname in self._skill_files():
            try:
                paths.append(_skill_path(self.config.bot_dir, fname))
            except ConfigError:
                continue
        sigs: dict[str, Any] = {}
        for p in paths:
            try:
                st = p.stat()
                sigs[str(p)] = (st.st_mtime_ns, st.st_size)
            except OSError:
                sigs[str(p)] = None
        return sigs

    def refresh(self) -> bool:
        """
        文件有变化就重新读。返回 True 表示本次确实更新了配置里的内容。
        """
        sigs = self._signature()
        if sigs == self._sigs:
            return False

        cfg = self.config
        try:
            prompt_path = self._prompt_path()
            new_prompt = _read_prompt_file(prompt_path) if prompt_path is not None else None
            files = self._skill_files()
            new_skills = _read_skill_files(cfg.bot_dir, files) if files else None
        except (ConfigError, OSError, UnicodeDecodeError) as e:
            if sigs != self._failed_sigs:
                logger.warning("提示词/技能文件重新加载失败，继续使用上一版内容：%s", e)
                self._failed_sigs = sigs
            return False

        first_time = self._sigs is None
        self._sigs = sigs
        self._failed_sigs = None

        changed = False
        if new_prompt is not None and new_prompt != cfg.prompt_text:
            cfg.prompt_text = new_prompt
            changed = True
        if new_skills is not None and new_skills != cfg.skills_text:
            cfg.skills_text = new_skills
            changed = True
        if changed and not first_time:
            logger.info("检测到提示词/技能文件有更新，已重新加载（%s）", "、".join(sigs))
        elif changed:
            logger.info("提示词/技能文件在启动后有更新，已按最新内容加载")
        return changed


# --------------------------------------------------------------------------
# 摘要输出（供 validate 命令用）
# --------------------------------------------------------------------------


def mask_secret(value: str, keep: int = 4) -> str:
    """密钥打码，只留前几位便于确认是不是填对了那一个"""
    if not value:
        return "(空)"
    if len(value) <= keep:
        return "*" * len(value)
    return value[:keep] + "*" * (len(value) - keep)


def config_summary(cfg: BotConfig) -> str:
    """人可读的配置摘要，密钥打码。给 `botkit validate` 用"""
    lines: list[str] = []
    lines.append(f"bot 名称         : {cfg.name}")
    lines.append(f"bot 目录         : {cfg.bot_dir}")
    lines.append(f"日志级别         : {cfg.log_level}")
    lines.append("")
    lines.append("[企微]")
    if cfg.has_wecom:
        lines.append(f"  bot_id         : {mask_secret(cfg.wecom.bot_id)}")
        lines.append(f"  bot_secret     : {mask_secret(cfg.wecom.bot_secret)}")
    else:
        lines.append("  (未配企微凭证，不连企微长连接)")
    lines.append("")
    lines.append("[对外接口]")
    if cfg.api.enabled:
        lines.append(f"  chat 接口      : 已开启，端口 {cfg.api.port}")
        lines.append(f"  鉴权 token     : {mask_secret(cfg.api.token) if cfg.api.token else '(不鉴权)'}")
    else:
        lines.append("  chat 接口      : 未开启")
    lines.append("")
    lines.append("[LLM]")
    lines.append(f"  后端           : {cfg.llm.client}")
    lines.append(f"  api_url        : {cfg.llm.api_url}")
    lines.append(f"  api_key        : {mask_secret(cfg.llm.api_key)}")
    lines.append(f"  模型           : {cfg.llm.model}")
    lines.append(f"  超时(秒)       : {cfg.llm.timeout_seconds:g}")
    lines.append(f"  最大工具轮次   : {cfg.llm.max_tool_rounds}")
    lines.append("")
    lines.append("[提示词]")
    lines.append(f"  来源           : {cfg.prompt_source}")
    lines.append(f"  长度           : {len(cfg.prompt_text)} 字")
    first_line = cfg.prompt_text.splitlines()[0] if cfg.prompt_text else ""
    lines.append(f"  首行           : {first_line[:60]}")
    lines.append("")
    lines.append("[技能]")
    if cfg.skills_enabled and cfg.skills_files:
        lines.append(f"  启用           : 是（{len(cfg.skills_files)} 个，{len(cfg.skills_text)} 字）")
        lines.append(f"  文件           : {'、'.join(cfg.skills_files)}")
    else:
        lines.append("  启用           : 否")
    lines.append("")
    lines.append("[MCP 服务端]")
    if not cfg.mcp.servers:
        lines.append("  （未配置，纯 LLM 对话，不接任何工具）")
    for s in cfg.mcp.servers:
        lines.append(f"  - {s.name}")
        lines.append(f"      url        : {s.url}")
        lines.append(f"      身份头     : {s.identity_header or '(不发送)'}")
        if s.headers:
            masked = "、".join(f"{k}: {mask_secret(v)}" for k, v in s.headers.items())
            lines.append(f"      额外请求头 : {masked}")
        allow = "、".join(s.allow_tools) if s.allow_tools else "(全部放开)"
        lines.append(f"      白名单     : {allow}")
        if s.deny_tools:
            lines.append(f"      黑名单     : {'、'.join(s.deny_tools)}")
    lines.append("")
    lines.append("[身份]")
    lines.append(f"  来源           : {cfg.identity.source}")
    lines.append(f"  校验正则       : {cfg.identity.pattern or '(不校验)'}")
    if cfg.identity.inject:
        for inj in cfg.identity.inject:
            lines.append(f"  强制注入       : {inj.tool}.{inj.arg}")
    else:
        lines.append("  强制注入       : (无)")
    lines.append("")
    lines.append("[会话]")
    lines.append(f"  隔离粒度       : {cfg.session.scope}")
    lines.append(f"  保留轮数       : {cfg.session.max_turns}")
    lines.append(f"  空闲过期(秒)   : {cfg.session.ttl_seconds}")
    lines.append("")
    lines.append("[文案]")
    lines.append(f"  重置关键词     : {'、'.join(cfg.messages.reset_keywords) or '(无)'}")
    lines.append(f"  工具进度文案   : {len(cfg.messages.tool_progress)} 条")
    suffix_preview = cfg.messages.reply_suffix.strip().replace("\n", " ")
    lines.append(f"  回复后缀       : {suffix_preview[:40] if suffix_preview else '(无)'}")
    lines.append("")
    lines.append("[前置拦截]")
    if cfg.intercepts:
        for idx, rule in enumerate(cfg.intercepts, 1):
            conds = list(rule.match)
            if rule.match_regex:
                conds.append(f"正则:{rule.match_regex}")
            lines.append(f"  规则 {idx}        : {'、'.join(conds)}")
    else:
        lines.append("  (无，所有消息都进 LLM)")
    lines.append("")
    lines.append("[Hook]")
    lines.append(f"  文件           : {cfg.hooks_file or '(无)'}")
    return "\n".join(lines)
