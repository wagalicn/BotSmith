"""
可视化配置页面的服务层

这里只有纯逻辑（读写配置、探测、校验），HTTP 的部分在 server.py。
分开是为了这些逻辑能被单测覆盖 —— 配置读写一旦出错会毁掉同事的文件。

几个关键约定：
- 所有 bot 目录都必须在 bots/ 下，路径穿越会被拒绝
- 校验时加载 .env 会污染进程环境变量，所以在隔离的环境副本里做，
  否则切换 bot 时上一个的密钥会残留下来盖住下一个的
- 存盘走 yaml_writer，保留注释；.env 也保留原有注释和顺序
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

import yaml

from botkit.config import (
    SKILLS_DIRNAME,
    ConfigError,
    VALID_IDENTITY_SOURCES,
    VALID_LLM_CLIENTS,
    VALID_LOG_LEVELS,
    VALID_SESSION_SCOPES,
    config_summary,
    load_bot_config,
)
from botkit.mcp_client import McpError, McpToolClient, describe_exception
from botkit.scaffold import ScaffoldError, create_bot, validate_name
from botkit.yaml_writer import dump_bot_yaml

logger = logging.getLogger("botkit.ui")

_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
_ENV_LINE = re.compile(r"^(\s*)([A-Za-z_][A-Za-z0-9_]*)(\s*=)(.*)$")

# 等企微消息的默认超时。够一个人切到企微再发一句话
WECOM_WAIT_SECONDS = 90.0

# 删除的Agent挪到 bots/.trash/ 而不是真删 —— 没有 git 兜底，误删能捞回来
TRASH_DIRNAME = ".trash"

# 只匹配「整个字符串就是一个 ${VAR}」的地址，用来判断某个 url 是不是纯占位符
_SOLE_PLACEHOLDER = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$")


def mcp_url_var_for(server_name: str) -> str:
    """
    按 MCP 服务端标识名生成存地址的环境变量名。

    oa -> MCP_URL_OA，crm -> MCP_URL_CRM。这样 .env 里一眼能对上是哪个服务端。
    标识名里的非法字符转成下划线，空名兜底成 MAIN。
    """
    cleaned = re.sub(r"[^A-Za-z0-9]", "_", (server_name or "").strip()).upper()
    cleaned = cleaned.strip("_") or "MAIN"
    return f"MCP_URL_{cleaned}"


def is_mcp_url_var(var_name: str) -> bool:
    """判断一个环境变量名是不是由 mcp_url_var_for 生成的 MCP 地址变量"""
    return bool(re.fullmatch(r"MCP_URL_[A-Za-z0-9_]+", var_name or ""))


def mcp_header_var_for(server_name: str, header_name: str) -> str:
    """
    按服务端标识名 + 请求头名生成存该头值的环境变量名。

    (oa, Authorization) -> MCP_HEADER_OA_AUTHORIZATION。
    头值常是 token 一类的密钥，所以和地址一样落 .env、不进 git、不进密钥页。
    """
    s = re.sub(r"[^A-Za-z0-9]", "_", (server_name or "").strip()).upper().strip("_") or "MAIN"
    h = re.sub(r"[^A-Za-z0-9]", "_", (header_name or "").strip()).upper().strip("_") or "HEADER"
    return f"MCP_HEADER_{s}_{h}"


def is_mcp_header_var(var_name: str) -> bool:
    """判断一个环境变量名是不是由 mcp_header_var_for 生成的 MCP 请求头变量"""
    return bool(re.fullmatch(r"MCP_HEADER_[A-Za-z0-9_]+", var_name or ""))


class UiError(Exception):
    """页面操作失败，消息会直接显示给用户"""


# --------------------------------------------------------------------------
# 路径
# --------------------------------------------------------------------------


def bots_root(root: Path | str = ".") -> Path:
    """bots/ 目录，不存在就建"""
    path = (Path(root) / "bots").resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def bot_dir_for(root: Path | str, name: str) -> Path:
    """
    取某个 bot 的目录，并确认它真的在 bots/ 下面。

    name 是从网页传进来的，必须挡住 ../ 这类路径穿越。
    """
    if not name or not isinstance(name, str):
        raise UiError("没有指定 Agent 名字")

    parent = bots_root(root)
    candidate = (parent / name).resolve()

    if candidate.parent != parent:
        raise UiError(f"Agent 名字不合法：{name}")
    if candidate.name == TRASH_DIRNAME:
        raise UiError("这不是一个Agent")
    if not candidate.is_dir():
        raise UiError(f"找不到这个 Agent：{name}")
    return candidate


def delete_bot(root: Path | str, name: str) -> dict[str, Any]:
    """
    删除一个Agent：不真删，挪到 bots/.trash/ 下。

    没有 git 兜底，误删的配置和提示词很难重写，所以做成可找回。
    同名重复删除时给回收站里的目录加时间戳后缀，不覆盖上一次删的。
    """
    import time

    bot_dir = bot_dir_for(root, name)
    trash = bots_root(root) / TRASH_DIRNAME
    trash.mkdir(exist_ok=True)

    target = trash / name
    if target.exists():
        target = trash / f"{name}.{time.strftime('%Y%m%d-%H%M%S')}"

    try:
        bot_dir.rename(target)
    except OSError as e:
        # 跨盘或占用时 rename 会失败，退回到复制+删除
        import shutil

        try:
            shutil.move(str(bot_dir), str(target))
        except Exception as inner:
            raise UiError(
                f"删除失败：{type(inner).__name__}: {inner}"
            ) from inner
        _ = e

    logger.info("已把Agent %s 移到回收站：%s", name, target)
    return {"name": name, "trashed_to": str(target)}


# --------------------------------------------------------------------------
# 环境变量隔离
# --------------------------------------------------------------------------


@contextmanager
def isolated_env() -> Iterator[None]:
    """
    在环境变量的副本里做事，退出时还原。

    load_bot_config 会把 bot 的 .env 灌进 os.environ 且不覆盖已有值。
    页面上来回切 bot 时，如果不隔离，第一个 bot 的密钥会一直留着，
    并且盖住后面 bot 的同名变量 —— 校验结果就全错了。
    """
    saved = dict(os.environ)
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(saved)


# --------------------------------------------------------------------------
# .env 读写
# --------------------------------------------------------------------------


def read_env_file(path: Path) -> dict[str, str]:
    """读 .env，返回键值对。读不到就返回空"""
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = _ENV_LINE.match(line)
        if match:
            values[match.group(2)] = match.group(4).strip()
    return values


def write_env_file(path: Path, updates: dict[str, str]) -> None:
    """
    更新 .env，保留原有的注释、顺序和未涉及的变量。

    没有 .env 时会拿 .env.example 当底子（连注释一起），
    这样同事看到的还是带说明的文件，而不是一堆裸的 KEY=VALUE。
    """
    if path.is_file():
        original = path.read_text(encoding="utf-8").splitlines()
    else:
        example = path.parent / ".env.example"
        original = (
            example.read_text(encoding="utf-8").splitlines()
            if example.is_file()
            else []
        )

    remaining = dict(updates)
    out: list[str] = []
    for line in original:
        match = _ENV_LINE.match(line)
        if match and match.group(2) in remaining:
            indent, key, eq, _old = match.groups()
            out.append(f"{indent}{key}{eq}{remaining.pop(key)}")
        else:
            out.append(line)

    if remaining:
        if out and out[-1].strip():
            out.append("")
        for key, value in remaining.items():
            out.append(f"{key}={value}")

    path.write_text("\n".join(out).rstrip("\n") + "\n", encoding="utf-8")


def _remove_env_vars(path: Path, names: set[str]) -> None:
    """从 .env 里删掉指定变量（连同它那行）。用于清理改名后遗留的旧地址变量"""
    if not names or not path.is_file():
        return
    kept: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        match = _ENV_LINE.match(line)
        if match and match.group(2) in names:
            continue
        kept.append(line)
    path.write_text("\n".join(kept).rstrip("\n") + "\n", encoding="utf-8")


def placeholders_in(node: Any) -> list[str]:
    """把配置树里用到的 ${VAR} 变量名收集出来，顺序去重"""
    found: list[str] = []

    def walk(value: Any) -> None:
        if isinstance(value, str):
            for name in _ENV_PATTERN.findall(value):
                if name not in found:
                    found.append(name)
        elif isinstance(value, dict):
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(node)
    return found


# --------------------------------------------------------------------------
# 校验
# --------------------------------------------------------------------------


def validate_bot_dir(bot_dir: Path) -> dict[str, Any]:
    """校验一个 bot 目录，返回给前端的结构。不抛异常"""
    with isolated_env():
        try:
            cfg = load_bot_config(bot_dir)
        except ConfigError as e:
            return {"ok": False, "error": str(e), "summary": ""}
        except Exception as e:
            logger.exception("校验 %s 时出现意外错误", bot_dir)
            return {
                "ok": False,
                "error": f"意外错误（{type(e).__name__}）：{e}",
                "summary": "",
            }
        return {"ok": True, "error": "", "summary": config_summary(cfg)}


# --------------------------------------------------------------------------
# 列表 / 读 / 写
# --------------------------------------------------------------------------


def list_bots(root: Path | str = ".") -> list[dict[str, Any]]:
    """列出所有 bot 及其校验状态"""
    parent = bots_root(root)
    result: list[dict[str, Any]] = []
    for child in sorted(parent.iterdir()):
        # .trash 是删除的Agent的回收站，不算现役 bot
        if child.name == TRASH_DIRNAME:
            continue
        if not child.is_dir() or not (child / "bot.yaml").is_file():
            continue
        state = validate_bot_dir(child)
        result.append(
            {
                "name": child.name,
                "ok": state["ok"],
                "error": state["error"],
                "has_env": (child / ".env").is_file(),
            }
        )
    return result


def read_bot(root: Path | str, name: str) -> dict[str, Any]:
    """读一个 bot 的全部可编辑内容"""
    bot_dir = bot_dir_for(root, name)

    try:
        raw = yaml.safe_load((bot_dir / "bot.yaml").read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise UiError(
            f"bot.yaml 不是合法的 YAML，页面没法打开。请先手动修好：\n{e}"
        ) from e
    if not isinstance(raw, dict):
        raise UiError("bot.yaml 的顶层不是键值对，页面没法打开")

    prompt_text = ""
    prompt_file = ((raw.get("prompt") or {}) if isinstance(raw.get("prompt"), dict) else {}).get("file")
    if prompt_file:
        candidate = (bot_dir / str(prompt_file)).resolve()
        if candidate.is_file():
            prompt_text = candidate.read_text(encoding="utf-8")

    env_values = read_env_file(bot_dir / ".env")
    example_values = read_env_file(bot_dir / ".env.example")

    # 每个 MCP 服务端的地址在「工具」页明文维护（实际值存 .env、不进 git）。
    # 给每个 server 附一个解析好的明文 address，并算出哪些环境变量是 MCP 地址变量，
    # 这些变量不再出现在「密钥」页，避免两个地方都能改同一个值。
    servers = _servers_of(raw)
    mcp_url_vars: set[str] = set()
    mcp_hidden_vars: set[str] = set()
    for server in servers:
        var = _server_url_var(server)
        if var:
            mcp_url_vars.add(var)
            mcp_hidden_vars.add(var)
            server["address"] = env_values.get(var, "")
        # 请求头：把 bot.yaml 里的占位符解析成明文供页面编辑。
        # header 值多是 token，跟地址一样在工具页维护、不进密钥页。
        raw_headers = server.get("headers")
        plain: dict[str, str] = {}
        if isinstance(raw_headers, dict):
            for hk, hv in raw_headers.items():
                hvar = _sole_placeholder_var(hv)
                if hvar:
                    mcp_hidden_vars.add(hvar)
                    plain[str(hk)] = env_values.get(hvar, "")
                else:
                    plain[str(hk)] = str(hv)
        server["headers"] = plain

    # LLM 的 api_url / api_key / model 改在「大模型」页明文维护（存 .env、不进 git）。
    # bot.yaml 里这三个字段是 ${LLM_API_URL} 这类固定占位符；read 时替换成明文供编辑，
    # 并把这三个固定变量从「密钥」页排除。
    llm_hidden_vars = _resolve_llm_plain(raw, env_values)
    # 对外接口的 token 也在「对外接口」页明文维护，同样从密钥页排除。
    api_hidden_vars = _resolve_api_plain(raw, env_values)
    hidden_vars = mcp_hidden_vars | llm_hidden_vars | api_hidden_vars

    needed = placeholders_in(raw)
    env: list[dict[str, Any]] = []
    for key in needed:
        if key in hidden_vars:
            continue  # MCP 地址/请求头、LLM 三项在各自页面维护，不进密钥页
        value = env_values.get(key, "")
        env.append(
            {
                "key": key,
                "value": value,
                "filled": bool(value.strip()),
                "hint": example_values.get(key, ""),
            }
        )

    return {
        "name": bot_dir.name,
        "values": raw,
        "prompt": prompt_text,
        "prompt_file": prompt_file or "prompt.md",
        "env": env,
        "hooks": _hooks_state(bot_dir, raw),
        "validation": validate_bot_dir(bot_dir),
    }


def _servers_of(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """从配置里取出 mcp.servers 列表（只保留 dict 项）"""
    mcp = raw.get("mcp")
    if not isinstance(mcp, dict):
        return []
    servers = mcp.get("servers")
    if not isinstance(servers, list):
        return []
    return [s for s in servers if isinstance(s, dict)]


def _sole_placeholder_var(value: Any) -> str | None:
    """一个值如果是「纯 ${VAR} 占位符」，返回变量名，否则 None。"""
    match = _SOLE_PLACEHOLDER.match(str(value or "").strip())
    return match.group(1) if match else None


# LLM 在「大模型」页明文维护的三个字段 -> .env 里的固定变量名
LLM_ENV_FIELDS = {
    "api_url": "LLM_API_URL",
    "api_key": "LLM_API_KEY",
    "model": "LLM_MODEL",
}


def _resolve_llm_plain(raw: dict[str, Any], env_values: dict[str, str]) -> set[str]:
    """
    把 raw["llm"] 里 api_url/api_key/model 的占位符替换成 .env 明文（就地改 raw），
    返回被这样处理掉的固定变量名集合（供密钥页排除）。

    只处理「值恰好是 ${LLM_XXX} 占位符」的情况；有人手写了明文就原样留着、不隐藏。
    """
    llm = raw.get("llm")
    if not isinstance(llm, dict):
        return set()
    hidden: set[str] = set()
    for field_name, var in LLM_ENV_FIELDS.items():
        cur = llm.get(field_name)
        cur_var = _sole_placeholder_var(cur)
        # 只有当占位符正好是约定的固定变量时才接管，避免误伤自定义占位符
        if cur_var == var:
            llm[field_name] = env_values.get(var, "")
            hidden.add(var)
    return hidden


# 对外 chat 接口的鉴权 token -> .env 固定变量
API_TOKEN_VAR = "CHAT_API_TOKEN"


def _resolve_api_plain(raw: dict[str, Any], env_values: dict[str, str]) -> set[str]:
    """
    把 raw["api"]["token"] 的占位符替换成 .env 明文（就地改 raw），
    返回被处理的固定变量名集合（供密钥页排除）。
    """
    api = raw.get("api")
    if not isinstance(api, dict):
        return set()
    cur_var = _sole_placeholder_var(api.get("token"))
    if cur_var == API_TOKEN_VAR:
        api["token"] = env_values.get(API_TOKEN_VAR, "")
        return {API_TOKEN_VAR}
    return set()


def _server_url_var(server: dict[str, Any]) -> str | None:
    """
    如果 server 的 url 是「纯 ${VAR} 占位符」，返回变量名，否则 None。

    地址走占位符 + .env（不进 git）时 url 就是这个形态；
    要是有人手写了明文 url，这里返回 None，页面照原样展示、不当作可明文编辑的地址。
    """
    return _sole_placeholder_var(server.get("url"))


def _hooks_state(bot_dir: Path, raw: dict[str, Any]) -> dict[str, Any]:
    """hooks.py 的状态，只做展示用，页面不编辑代码"""
    hooks_cfg = raw.get("hooks") if isinstance(raw.get("hooks"), dict) else {}
    rel = (hooks_cfg or {}).get("file") or "hooks.py"
    path = (bot_dir / str(rel)).resolve()
    if not path.is_file():
        return {"file": str(rel), "exists": False, "implemented": []}

    from botkit.hooks import HookError, load_hooks

    try:
        hooks = load_hooks(path)
        implemented = hooks.implemented
        error = ""
    except HookError as e:
        implemented = []
        error = str(e)
    return {
        "file": str(rel),
        "exists": True,
        "implemented": implemented,
        "error": error,
    }


def save_bot(root: Path | str, name: str, payload: dict[str, Any]) -> dict[str, Any]:
    """
    保存一个 bot 的配置。

    payload: {"values": {...}, "prompt": "...", "env": {"KEY": "VALUE"}}
    先写文件再校验，把校验结果一起返回给前端。
    """
    bot_dir = bot_dir_for(root, name)

    values = payload.get("values")
    if not isinstance(values, dict):
        raise UiError("提交的配置格式不对（values 应该是对象）")

    # 名字以目录为准，避免页面改了名字导致目录和配置不一致
    values = dict(values)
    values["name"] = bot_dir.name

    _check_values(values)

    # MCP 地址：前端在工具页填明文，这里把明文落到 .env 的 MCP_URL_<NAME>，
    # bot.yaml 里 url 存成 ${MCP_URL_<NAME>} 占位符（不进 git）。
    env_from_addresses = _extract_server_addresses(bot_dir, values)

    # LLM 三项：大模型页填明文，落到 .env 的 LLM_API_URL/KEY/MODEL，
    # bot.yaml 的 llm 段改回 ${...} 占位符。
    env_from_llm = _extract_llm_env(bot_dir, values)

    # 对外接口 token：对外接口页填明文，落到 .env 的 CHAT_API_TOKEN。
    env_from_api = _extract_api_env(bot_dir, values)

    prompt_cfg = values.get("prompt") if isinstance(values.get("prompt"), dict) else {}
    prompt_file = (prompt_cfg or {}).get("file")
    prompt_text = payload.get("prompt")

    # 写提示词。用 inline 的 bot 不写文件
    if prompt_file and isinstance(prompt_text, str):
        target = (bot_dir / str(prompt_file)).resolve()
        if not str(target).startswith(str(bot_dir)):
            raise UiError(f"提示词文件路径不合法：{prompt_file}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(prompt_text.rstrip("\n") + "\n", encoding="utf-8")

    (bot_dir / "bot.yaml").write_text(dump_bot_yaml(values), encoding="utf-8")

    # 合并要写进 .env 的变量：密钥页提交的 + MCP 地址
    clean: dict[str, str] = {}
    env_updates = payload.get("env")
    if isinstance(env_updates, dict):
        for k, v in env_updates.items():
            if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", str(k)):
                clean[str(k)] = str(v)
    clean.update(env_from_addresses)
    clean.update(env_from_llm)
    clean.update(env_from_api)

    if clean:
        write_env_file(bot_dir / ".env", clean)

    return {"name": bot_dir.name, "validation": validate_bot_dir(bot_dir)}


def _extract_server_addresses(
    bot_dir: Path, values: dict[str, Any]
) -> dict[str, str]:
    """
    把每个 server 前端填的明文 address / headers 转成 .env 变量，并把 server 的
    url、headers 值改成对应占位符（就地改 values，供 dump_bot_yaml 用）。

    返回要写进 .env 的 {变量名: 明文值}。

    还会清理改名/删除后遗留的旧 MCP 地址/请求头变量，避免 .env 里越攒越多。
    """
    servers = _servers_of(values)

    # 先记下改动前 .env 里已有的 MCP 地址/请求头变量，用于清理孤儿
    existing = read_env_file(bot_dir / ".env")
    stale = {k for k in existing if is_mcp_url_var(k) or is_mcp_header_var(k)}

    updates: dict[str, str] = {}
    for server in servers:
        sname = str(server.get("name") or "").strip()
        # address 是前端传的明文；没传就回退到「解析当前 url 占位符拿到的旧值」
        address = server.pop("address", None)

        # 请求头：前端传的是明文 {头名: 值}。把非空的落 .env、值换成占位符。
        _extract_server_headers(server, sname, updates, stale)

        # 空行（没标识名、也没填地址）：不生成任何 MCP_URL 变量，url 留空
        if not sname and not (address or "").strip():
            server["url"] = ""
            continue

        if sname:
            var = mcp_url_var_for(sname)
        else:
            # 没标识名时保持 url 原样，不动（_check_values 会拦空名的多 server 情况）
            continue

        if address is None:
            # 前端没带 address（比如老客户端）：保留原 url，不改动
            # 但如果 url 已经是我们的占位符，别把它算成 stale
            current_var = _server_url_var(server)
            if current_var:
                stale.discard(current_var)
            continue

        if not str(address).strip():
            # 填了标识名但地址留空：视为未配好的空行，url 清空、不落 .env 变量。
            # 加载时 url 为空的 server 会被跳过（纯对话）。
            server["url"] = ""
            continue

        server["url"] = "${" + var + "}"
        updates[var] = str(address).strip()
        stale.discard(var)

    # 清理：改名/删头后旧的 MCP_URL_XXX / MCP_HEADER_XXX 变量从 .env 删掉
    if stale:
        _remove_env_vars(bot_dir / ".env", stale)

    return updates


def _extract_server_headers(
    server: dict[str, Any],
    sname: str,
    updates: dict[str, str],
    stale: set[str],
) -> None:
    """
    把一个 server 前端填的明文 headers 转成 .env 变量并改成占位符（就地改 server）。

    - 空头名或空值的条目直接丢弃（前端留了空行不算）。
    - 有标识名时，值落到 MCP_HEADER_<SERVER>_<HEADER>，headers 里存占位符。
    - 没标识名时保持原样不动。
    updates / stale 会被就地更新。
    """
    raw_headers = server.get("headers")
    if not isinstance(raw_headers, dict):
        server.pop("headers", None)
        return

    out: dict[str, str] = {}
    for hk, hv in raw_headers.items():
        hname = str(hk or "").strip()
        hval = str(hv if hv is not None else "").strip()
        if not hname or not hval:
            continue
        if not sname:
            # 没标识名：原样保留（多半是手写配置，_check_values 会拦空名多 server）
            out[hname] = hval
            continue
        var = mcp_header_var_for(sname, hname)
        out[hname] = "${" + var + "}"
        updates[var] = hval
        stale.discard(var)

    if out:
        server["headers"] = out
    else:
        # 没有有效头就别在 yaml 里留个空 headers 段
        server.pop("headers", None)


def _extract_llm_env(bot_dir: Path, values: dict[str, Any]) -> dict[str, str]:
    """
    把 llm 段 api_url/api_key/model 的明文落到 .env 固定变量，
    并把 llm 段这三个字段就地改回 ${LLM_XXX} 占位符（供 dump_bot_yaml）。

    返回要写进 .env 的 {变量名: 明文值}。
    - 填了明文 → 写回 .env、yaml 存占位符。
    - 留空 → 不覆盖 .env 里已有的值（避免误清密钥），yaml 仍存占位符。
    """
    llm = values.get("llm")
    if not isinstance(llm, dict):
        return {}
    existing = read_env_file(bot_dir / ".env")
    updates: dict[str, str] = {}
    for field_name, var in LLM_ENV_FIELDS.items():
        raw_val = llm.get(field_name)
        # 已经是占位符就别动（比如前端没改、或用户手写了别的占位符）
        cur_var = _sole_placeholder_var(raw_val)
        if cur_var:
            # 是我们的固定占位符：保持；是别的自定义占位符：也原样留着
            continue
        text = str(raw_val if raw_val is not None else "").strip()
        # 统一把 yaml 里的值改成固定占位符
        llm[field_name] = "${" + var + "}"
        if text:
            updates[var] = text
        elif var not in existing:
            # 空且 .env 里也没有 → 写个空占位，validate 会提示去填
            updates[var] = ""
        # 空但 .env 已有值 → 不动，保留旧值
    return updates


def _extract_api_env(bot_dir: Path, values: dict[str, Any]) -> dict[str, str]:
    """
    把 api.token 的明文落到 .env 的 CHAT_API_TOKEN，token 字段就地改回占位符。

    token 可以为空（= 不鉴权），是合法的。
    - 填了明文 → 写回 .env、yaml 存占位符。
    - 留空但 .env 已有 → 保留旧值（避免误清）。
    - 留空且 .env 也没有 → 写空值（表示不鉴权），yaml 仍存占位符。
    """
    api = values.get("api")
    if not isinstance(api, dict):
        return {}
    existing = read_env_file(bot_dir / ".env")
    raw_val = api.get("token")
    cur_var = _sole_placeholder_var(raw_val)
    if cur_var:
        return {}  # 已是占位符，不动
    text = str(raw_val if raw_val is not None else "").strip()
    api["token"] = "${" + API_TOKEN_VAR + "}"
    if text:
        return {API_TOKEN_VAR: text}
    if API_TOKEN_VAR not in existing:
        return {API_TOKEN_VAR: ""}
    return {}  # 空但已有值 → 保留


def _check_values(values: dict[str, Any]) -> None:
    """
    存盘前挡掉明显的非法值。

    这里不做完整校验（那是 load_bot_config 的事），只拦住会让 YAML
    生成器产出坏文件、或者让人一头雾水的输入。
    """
    def enum(section: str, key: str, allowed: tuple[str, ...]) -> None:
        node = values.get(section)
        if not isinstance(node, dict):
            return
        value = node.get(key)
        if value in (None, ""):
            return
        if str(value) not in allowed:
            raise UiError(
                f"{section}.{key} 只能是 {'、'.join(allowed)} 之一，收到的是 {value!r}"
            )

    enum("llm", "client", VALID_LLM_CLIENTS)
    enum("session", "scope", VALID_SESSION_SCOPES)
    enum("identity", "source", VALID_IDENTITY_SOURCES)

    log = values.get("logging")
    if isinstance(log, dict) and log.get("level"):
        if str(log["level"]).upper() not in VALID_LOG_LEVELS:
            raise UiError(
                f"logging.level 只能是 {'、'.join(VALID_LOG_LEVELS)} 之一"
            )

    identity = values.get("identity")
    if isinstance(identity, dict) and identity.get("pattern"):
        try:
            re.compile(str(identity["pattern"]))
        except re.error as e:
            raise UiError(f"身份校验正则写得不对：{e}") from e

    # MCP 服务端校验：只校验「真的要用」的 server（填了地址的）。
    # 没填地址的行视为还没配好的空行，直接跳过 —— 纯对话 bot 用不到工具，
    # 页面预置的空 server 行不应该拦住保存。
    # 填了地址的 server 才要求标识名唯一非空，否则工具路由会打架。
    mcp = values.get("mcp")
    if isinstance(mcp, dict) and isinstance(mcp.get("servers"), list):
        seen: set[str] = set()
        for i, server in enumerate(mcp["servers"]):
            if not isinstance(server, dict):
                continue
            # address 是页面填的明文地址；url 是落盘后的占位符。两者都空 = 空行
            addr = str(server.get("address") or "").strip()
            url = str(server.get("url") or "").strip()
            if not addr and not url:
                continue
            name = str(server.get("name") or "").strip()
            if not name:
                raise UiError(f"第 {i + 1} 个 MCP 服务端填了地址但还没填标识名")
            if name in seen:
                raise UiError(f"有两个 MCP 服务端都叫「{name}」，标识名要唯一")
            seen.add(name)

    # 前置拦截校验：完全空的规则（页面加了没填）直接丢弃，不拦住保存；
    # 填了一半的（有匹配条件但没 reply，或有 reply 但没匹配条件）给友好提示。
    intercepts = values.get("intercepts")
    if isinstance(intercepts, list):
        kept: list[dict[str, Any]] = []
        for i, rule in enumerate(intercepts):
            if not isinstance(rule, dict):
                continue
            match = [str(m).strip() for m in (rule.get("match") or []) if str(m).strip()]
            regex = str(rule.get("match_regex") or "").strip()
            reply = str(rule.get("reply") or "").strip()
            # 全空 = 空行，跳过
            if not match and not regex and not reply:
                continue
            if not reply:
                raise UiError(f"第 {i + 1} 条拦截规则还没填「命中后的回复」")
            if not match and not regex:
                raise UiError(
                    f"第 {i + 1} 条拦截规则要至少填「关键词」或「正则」中的一个"
                )
            if regex:
                try:
                    re.compile(regex)
                except re.error as e:
                    raise UiError(f"第 {i + 1} 条拦截规则的正则写得不对：{e}") from e
            # 规范化：写回清洗后的值
            rule["match"] = match
            if regex:
                rule["match_regex"] = regex
            else:
                rule.pop("match_regex", None)
            rule["reply"] = str(rule.get("reply") or "")
            kept.append(rule)
        values["intercepts"] = kept


def create_new_bot(root: Path | str, name: str) -> dict[str, Any]:
    """新建一个 bot（复用命令行那套脚手架，保证两条路产出一致）"""
    try:
        validate_name(name)
        target = create_bot(name, parent=bots_root(root))
    except ScaffoldError as e:
        raise UiError(str(e)) from e
    return {"name": target.name}


# --------------------------------------------------------------------------
# 导入 / 导出
# --------------------------------------------------------------------------

# 导出格式的版本号。以后结构变了靠它兼容旧文件。
EXPORT_FORMAT = "botkit-bot/v1"


def export_bot(root: Path | str, name: str) -> dict[str, Any]:
    """
    把一个 bot 导成可搬运的 JSON。

    默认不含密钥值 —— env 只保留变量名和 .env.example 里的提示，
    实际 token/密钥不会被带出去。导入方在目标环境自己填。
    """
    data = read_bot(root, name)
    # read_bot 为了让页面能编辑，把 llm 三项、MCP 地址/请求头都解析成了明文，
    # 直接导出会泄漏密钥。这里脱敏：把这些明文还原成占位符结构再导出。
    values = _sanitize_values_for_export(data["values"])
    # env 只留结构（key + 提示），清空实际值，避免密钥泄漏
    env_struct = [
        {"key": e["key"], "hint": e.get("hint", "")}
        for e in data.get("env", [])
    ]
    return {
        "format": EXPORT_FORMAT,
        "name": data["name"],
        "values": values,
        "prompt": data.get("prompt", ""),
        "env": env_struct,
    }


def _sanitize_values_for_export(values: dict[str, Any]) -> dict[str, Any]:
    """
    导出脱敏：read_bot 塞进 values 的明文（llm 三项、server.address、headers 值）
    都还原成占位符/清空，确保导出的 JSON 不含任何真实密钥或地址。
    """
    import copy as _copy

    out = _copy.deepcopy(values)

    # LLM 三项还原成固定占位符
    llm = out.get("llm")
    if isinstance(llm, dict):
        for field_name, var in LLM_ENV_FIELDS.items():
            if field_name in llm:
                llm[field_name] = "${" + var + "}"

    # 对外接口 token 还原成占位符（enabled/port 不是密钥，保留）
    api = out.get("api")
    if isinstance(api, dict) and "token" in api:
        api["token"] = "${" + API_TOKEN_VAR + "}"

    # MCP 服务端：address 是明文地址，移除；headers 值还原成占位符
    servers = out.get("mcp", {}).get("servers") if isinstance(out.get("mcp"), dict) else None
    if isinstance(servers, list):
        for s in servers:
            if not isinstance(s, dict):
                continue
            s.pop("address", None)
            sname = str(s.get("name") or "").strip()
            headers = s.get("headers")
            if isinstance(headers, dict) and headers:
                s["headers"] = {
                    hk: "${" + mcp_header_var_for(sname, hk) + "}"
                    for hk in headers
                }
    return out


def import_bot(root: Path | str, payload: dict[str, Any]) -> dict[str, Any]:
    """
    从 export_bot 产出的 JSON 建一个新 bot。

    payload: {name?, values, prompt?, env?}
    - name 缺省时用 values.name；两者都没有则报错。
    - 只建结构，不写密钥值（导出本来也不含）。导入后去密钥页填。
    - 名字已存在会报错，不覆盖。
    """
    if not isinstance(payload, dict):
        raise UiError("导入内容应该是一个 JSON 对象")

    values = payload.get("values")
    if not isinstance(values, dict):
        raise UiError("导入内容缺少 values（bot.yaml 的结构），或格式不对")

    name = str(payload.get("name") or values.get("name") or "").strip()
    if not name:
        raise UiError("导入内容里没有 Agent 名字（name），补一个再导入")

    # 名字合法性 + 是否已存在，都在 create_new_bot 里用脚手架统一校验
    parent = bots_root(root)
    if (parent / name).exists():
        raise UiError(f"已经有一个叫「{name}」的 Agent 了，换个名字或先删掉旧的")

    create_new_bot(root, name)

    # 名字以导入的为准落进 values，交给 save_bot 走正常保存 + 校验流程
    values = dict(values)
    values["name"] = name
    prompt = payload.get("prompt")
    save_payload: dict[str, Any] = {"values": values}
    if isinstance(prompt, str):
        save_payload["prompt"] = prompt
    # 导入不带密钥值：env 留空，让用户去密钥页填
    try:
        result = save_bot(root, name, save_payload)
    except UiError:
        # 保存失败就把刚建的空目录清掉，别留半成品
        try:
            delete_bot(root, name)
        except Exception:
            logger.debug("回滚导入失败的 bot 目录时出错（忽略）", exc_info=True)
        raise
    return {"name": name, "validation": result.get("validation")}


# --------------------------------------------------------------------------
# 技能（共享 skill 文档）
# --------------------------------------------------------------------------

_SKILL_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-]*\.md$")


def skills_root(root: Path | str = ".") -> Path:
    """共享 skill 目录 bots/_skills/，不存在就建"""
    path = (bots_root(root) / SKILLS_DIRNAME).resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _check_skill_name(filename: str) -> str:
    name = (filename or "").strip()
    if not _SKILL_NAME.match(name):
        raise UiError(
            f"技能文件名不合法：{filename}（用字母/数字开头，只能含字母数字下划线连字符，且以 .md 结尾）"
        )
    return name


def _iter_bot_dirs(root: Path | str) -> Iterator[Path]:
    """遍历现役 Agent 目录（跳过回收站、共享 skill 目录等没有 bot.yaml 的目录）"""
    parent = bots_root(root)
    for child in sorted(parent.iterdir()):
        if child.name == TRASH_DIRNAME:
            continue
        if child.is_dir() and (child / "bot.yaml").is_file():
            yield child


def _load_raw_yaml(bot_dir: Path) -> dict[str, Any] | None:
    """读一个 Agent 的 bot.yaml 原始内容。不是合法 YAML 对象就返回 None"""
    try:
        raw = yaml.safe_load((bot_dir / "bot.yaml").read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return None
    return raw if isinstance(raw, dict) else None


def _skill_files_of(raw: dict[str, Any]) -> list[str]:
    skills = raw.get("skills")
    if not isinstance(skills, dict):
        return []
    files = skills.get("files")
    if not isinstance(files, list):
        return []
    return [str(f).strip() for f in files if str(f).strip()]


def _skill_usage(root: Path | str) -> dict[str, list[str]]:
    """每个 skill 文件名被哪些 Agent 引用：{文件名: [Agent 名, ...]}"""
    usage: dict[str, list[str]] = {}
    for bot_dir in _iter_bot_dirs(root):
        raw = _load_raw_yaml(bot_dir)
        if raw is None:
            continue
        for fname in _skill_files_of(raw):
            usage.setdefault(fname, []).append(bot_dir.name)
    return usage


def list_skills(root: Path | str = ".") -> list[dict[str, Any]]:
    """列出共享目录里的 skill 文件（名字 + 首行摘要 + 大小 + 被哪些 Agent 引用）"""
    sdir = skills_root(root)
    usage = _skill_usage(root)
    out: list[dict[str, Any]] = []
    for p in sorted(sdir.glob("*.md")):
        # 和上传同一套文件名规则：点开头的文件（如发布方的临时文件）不当成技能
        if not p.is_file() or not _SKILL_NAME.match(p.name):
            continue
        text = ""
        try:
            text = p.read_text(encoding="utf-8")
        except OSError:
            pass
        first = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
        out.append(
            {
                "name": p.name,
                "summary": first[:80],
                "chars": len(text),
                "used_by": usage.get(p.name, []),
            }
        )
    return out


def upload_skill(root: Path | str, filename: str, content: str) -> dict[str, Any]:
    """写入/覆盖一个共享 skill 文件"""
    name = _check_skill_name(filename)
    if not isinstance(content, str) or not content.strip():
        raise UiError("技能内容是空的")
    sdir = skills_root(root)
    target = (sdir / name).resolve()
    if target.parent != sdir:
        raise UiError(f"技能文件名不合法：{filename}")
    target.write_text(content.rstrip("\n") + "\n", encoding="utf-8")
    return {"name": name, "skills": list_skills(root)}


def _remove_skill_ref(bot_dir: Path, name: str) -> bool:
    """
    从一个 Agent 的 bot.yaml 里去掉对某个 skill 的引用。

    返回 True = 引用了且已移除；False = 本来就没引用。
    重写后如果除这条引用外还有别的配置变了（比如手改过、带了生成器不认识的写法），
    就不落盘、抛 UiError，交给调用方提示用户去页面手动取消勾选 —— 宁可不改，也不能误改。
    """
    raw = _load_raw_yaml(bot_dir)
    if raw is None or name not in _skill_files_of(raw):
        return False

    updated = dict(raw)
    skills = dict(raw["skills"])
    skills["files"] = [f for f in _skill_files_of(raw) if f != name]
    updated["skills"] = skills
    # 生成器在「未启用且没有文件」时会省略整个 skills 段，这里保持同样的形状，下面才好比对
    if not skills.get("enabled") and not skills["files"]:
        updated.pop("skills")

    text = dump_bot_yaml(updated)
    if yaml.safe_load(text) != updated:
        raise UiError(f"{bot_dir.name} 的 bot.yaml 无法安全地自动改写")

    (bot_dir / "bot.yaml").write_text(text, encoding="utf-8")
    return True


def delete_skill(root: Path | str, filename: str) -> dict[str, Any]:
    """
    删除一个共享 skill 文件，并把所有 Agent 里对它的引用一起去掉。

    只删文件不清引用的话，引用了它的 Agent 会一直校验失败，
    而页面上又找不到那个勾选框去取消。

    返回 cleaned（已自动移除引用的 Agent）和 skipped（引用了但没法安全自动改写、
    需要到页面手动取消勾选的 Agent）。
    """
    name = _check_skill_name(filename)
    sdir = skills_root(root)
    target = (sdir / name).resolve()
    if target.parent != sdir:
        raise UiError(f"技能文件名不合法：{filename}")

    cleaned: list[str] = []
    skipped: list[str] = []
    for bot_dir in _iter_bot_dirs(root):
        try:
            if _remove_skill_ref(bot_dir, name):
                cleaned.append(bot_dir.name)
        except (UiError, OSError) as e:
            logger.warning("删除技能 %s 时没能清掉 %s 里的引用：%s", name, bot_dir.name, e)
            skipped.append(bot_dir.name)

    if target.is_file():
        target.unlink()
    if cleaned:
        logger.info("删除技能 %s，已从这些 Agent 移除引用：%s", name, "、".join(cleaned))
    return {"skills": list_skills(root), "cleaned": cleaned, "skipped": skipped}


def check_prompt(text: str) -> dict[str, Any]:
    """分析提示词结构，给页面提示缺了哪几段。纯逻辑，不碰磁盘"""
    from botkit.prompt_check import analyze_prompt, report_to_dict

    return report_to_dict(analyze_prompt(text or ""))


# --------------------------------------------------------------------------
# 预览测试
# --------------------------------------------------------------------------


def _build_preview_engine(cfg: Any) -> Any:
    """
    从配置组装预览用的引擎（和正式运行同一套）。

    单独抽出来是为了测试能替换掉它 —— 否则得连真的 LLM 和 MCP。
    """
    from botkit.engine import BotEngine
    from botkit.hooks import load_hooks
    from botkit.llm import create_backend

    hooks = load_hooks(cfg.hooks_file)
    mcp = McpToolClient(cfg.mcp.servers)
    backend = create_backend(cfg.llm)
    return BotEngine(cfg, mcp, backend, hooks=hooks)


async def preview_chat(
    root: Path | str,
    name: str,
    message: str,
    history: list[dict[str, str]] | None = None,
    identity: str | None = None,
    engine_factory: Any = None,
) -> dict[str, Any]:
    """
    在页面里跟Agent聊一句，看真实效果。

    走的是和正式运行完全一样的引擎（真调 LLM、真调 MCP 工具），
    只是不连企微。所以：
    - 必须配置能通过校验、密钥填对、MCP 能连上，否则这里会失败 ——
      这不是 bug，预览就是要真连才有意义。
    - 会真的执行工具。如果提示词触发了 create_it_ticket 之类，
      预览时会真建一张单。前端对此有明显警示。

    engine_factory 只在测试里传，用来注入假引擎，避免真的联网。

    返回 {reply, tool_calls: [{tool, args, result}], identity_used, history}。
    出问题时抛 UiError，消息直接给用户看。
    """
    from botkit.engine import IdentityRejected
    from botkit.llm import LlmError
    from botkit.mcp_client import McpError

    message = (message or "").strip()
    if not message:
        raise UiError("先输入一句话再发送")

    bot_dir = bot_dir_for(root, name)

    with isolated_env():
        try:
            cfg = load_bot_config(bot_dir)
        except ConfigError as e:
            raise UiError(
                "配置还没通过校验，没法预览。先把下面的问题解决：\n\n" + str(e)
            ) from e

        used_identity = identity or _sample_identity(cfg)

        build = engine_factory or _build_preview_engine
        engine = build(cfg)

        # 走和正式运行完全一样的 MessagePipeline，这样 intercepts（前置拦截）、
        # 重置关键词、空消息文案、reply_suffix、on_message hook 在预览里都能测到，
        # 而不是只测 LLM 那一段 —— 否则预览命中拦截规则却仍进 LLM，和线上不一致。
        from botkit.runtime import KIND_ERROR, KIND_IDENTITY_INVALID, MessagePipeline
        from botkit.session import SessionStore

        sessions = SessionStore.from_config(cfg.session)
        pipeline = MessagePipeline(
            cfg, engine, sessions, hooks=getattr(engine, "hooks", None)
        )

        # 预览身份走单聊帧：session key = single:<identity>
        frame = {
            "body": {
                "from": {"userid": used_identity},
                "chattype": "single",
                "text": {"content": message},
            }
        }
        session_key = sessions.key_for(frame)

        # 把前几轮历史成对预置进 session，让多轮预览仍有上下文。
        # history 是 user/assistant 交替的列表，按对喂给 append_turn。
        prior = list(history or [])
        for j in range(0, len(prior) - 1, 2):
            u = prior[j]
            a = prior[j + 1]
            if u.get("role") == "user" and a.get("role") == "assistant":
                sessions.append_turn(
                    session_key, u.get("content", ""), a.get("content", "")
                )

        trace: list[dict[str, Any]] = []

        async def on_tool_call(tool_name: str, args: dict[str, Any], result: str) -> None:
            trace.append(
                {
                    "tool": tool_name,
                    "args": args,
                    "result": result[:2000],
                    "truncated": len(result) > 2000,
                }
            )

        try:
            result = await pipeline.handle(frame, on_tool_call=on_tool_call)
            reply = result.text
            # 身份不符 pattern：pipeline 会返回 identity_invalid 文案而不抛异常。
            # 预览里把它翻成可操作的提示（和之前 engine.chat 抛 IdentityRejected 时一致）。
            if result.kind == KIND_IDENTITY_INVALID:
                raise UiError(
                    f"预览用的身份 {used_identity!r} 不符合 identity.pattern。"
                    "去「身份与安全」页调整校验规则，或改用别的身份预览。"
                )
            # handle 不抛异常：LLM / MCP 连不上会被吞成 messages.error。
            # 预览要让人看清是不是连接问题，所以命中 error 分支时给明确提示。
            if result.kind == KIND_ERROR:
                raise UiError(
                    "进入大模型 / 工具阶段时出错（预览需要真的连上 LLM 和 MCP）。"
                    "请查看Agent日志确认是密钥、地址还是网络问题。"
                )
        except IdentityRejected:
            raise UiError(
                f"预览用的身份 {used_identity!r} 不符合 identity.pattern。"
                "去「身份与安全」页调整校验规则，或改用别的身份预览。"
            ) from None
        except McpError as e:
            raise UiError(
                "调用 MCP 工具失败（预览需要真的连上工具服务端）：\n\n" + str(e)
            ) from e
        except LlmError as e:
            raise UiError(
                "调用大模型失败（预览需要真的连上 LLM）：\n\n" + str(e)
            ) from e
        except UiError:
            raise
        except Exception as e:
            logger.exception("预览 %s 时出错", name)
            raise UiError(f"预览时出错（{type(e).__name__}）：{e}") from e
        finally:
            await engine.aclose()

    new_history = list(history or [])
    new_history.append({"role": "user", "content": message})
    new_history.append({"role": "assistant", "content": reply})

    return {
        "reply": reply,
        "tool_calls": trace,
        "identity_used": used_identity,
        "history": new_history,
    }


# --------------------------------------------------------------------------
# 探测
# --------------------------------------------------------------------------


async def probe_mcp(
    root: Path | str, name: str, server_name: str | None = None
) -> dict[str, Any]:
    """
    连 MCP 服务端把工具列出来，**忽略白名单** —— 页面要靠这份完整列表
    让用户勾选，如果先过滤就只能看到已经勾上的那些了。

    返回每个工具的参数明细，供页面做 identity.inject 的下拉。
    """
    bot_dir = bot_dir_for(root, name)

    with isolated_env():
        try:
            cfg = load_bot_config(bot_dir)
        except ConfigError as e:
            raise UiError(
                "配置还没通过校验，没法探测。先把下面的问题解决：\n\n" + str(e)
            ) from e

        servers = cfg.mcp.servers
        if server_name:
            servers = [s for s in servers if s.name == server_name]
            if not servers:
                raise UiError(f"配置里没有名叫 {server_name} 的 MCP 服务端")

        identity = _sample_identity(cfg)
        results: list[dict[str, Any]] = []

        for server in servers:
            # 复制一份并清空白名单，拿到服务端的全部工具。
            # headers 要保留 —— 探测也得带上鉴权头，否则连不上。
            unfiltered = type(server)(
                name=server.name,
                url=server.url,
                identity_header=server.identity_header,
                allow_tools=[],
                deny_tools=[],
                headers=server.headers,
            )
            entry: dict[str, Any] = {
                "server": server.name,
                "url": server.url,
                "ok": False,
                "error": "",
                "tools": [],
            }
            try:
                client = McpToolClient([unfiltered])
                async with client.session(identity=identity) as sess:
                    tools = await sess.list_tools_openai()
                entry["ok"] = True
                entry["tools"] = [_tool_brief(t) for t in tools]
            except McpError as e:
                entry["error"] = str(e)
            except Exception as e:
                entry["error"] = describe_exception(e)
            results.append(entry)

    return {"identity_used": identity, "servers": results}


def _sample_identity(cfg: Any) -> str:
    """
    探测时用什么身份。配了 pattern 就造一个符合格式的样例，
    否则用一个明显是占位的值。
    """
    pattern = cfg.identity.pattern
    if not pattern:
        return "probe-user"
    for candidate in ("L220104", "M220104", "probe-user"):
        if re.match(pattern, candidate):
            return candidate
    return "probe-user"


def _tool_brief(openai_tool: dict[str, Any]) -> dict[str, Any]:
    """把 OpenAI 格式的工具定义压成页面要显示的样子"""
    fn = openai_tool.get("function") or {}
    schema = fn.get("parameters") or {}
    props = schema.get("properties") or {}
    required = set(schema.get("required") or [])

    params = []
    for pname, pinfo in props.items():
        info = pinfo if isinstance(pinfo, dict) else {}
        params.append(
            {
                "name": pname,
                "type": info.get("type", ""),
                "required": pname in required,
                "description": (info.get("description") or "").strip(),
            }
        )

    return {
        "name": fn.get("name", ""),
        "description": (fn.get("description") or "").strip(),
        "params": params,
    }


async def probe_wecom(
    root: Path | str,
    name: str,
    wait_for_message: bool = False,
    timeout: float = WECOM_WAIT_SECONDS,
) -> dict[str, Any]:
    """
    连企微验证凭证。wait_for_message=True 时还会等一条消息，
    用来看清企微给的 userid 长什么样、能不能过 identity.pattern。

    这是"新建 bot 最容易卡住"的那一步，值得在页面上一键完成。
    """
    from aibot import WSClient, WSClientOptions

    bot_dir = bot_dir_for(root, name)

    with isolated_env():
        try:
            cfg = load_bot_config(bot_dir)
        except ConfigError as e:
            raise UiError(
                "配置还没通过校验，没法连企微。先把下面的问题解决：\n\n" + str(e)
            ) from e
        bot_id = cfg.wecom.bot_id
        secret = cfg.wecom.bot_secret
        pattern = cfg.identity.pattern
        scope = cfg.session.scope

    loop = asyncio.get_running_loop()
    authed: asyncio.Future[bool] = loop.create_future()
    message: asyncio.Future[dict] = loop.create_future()

    def on_authenticated() -> None:
        if not authed.done():
            authed.set_result(True)

    def on_error(err: Any) -> None:
        text = str(err)
        for future in (authed, message):
            if not future.done():
                future.set_exception(UiError(text))

    async def on_text(frame: dict) -> None:
        if not message.done():
            message.set_result(frame)

    client = WSClient(WSClientOptions(bot_id=bot_id, secret=secret))
    client.on("authenticated", on_authenticated)
    client.on("error", on_error)
    client.on("message.text", on_text)

    result: dict[str, Any] = {"authenticated": False, "message": None}

    try:
        await client.connect()
        try:
            await asyncio.wait_for(authed, timeout=20)
        except asyncio.TimeoutError:
            raise UiError("连上了企微但一直没通过认证，请检查 bot_id 和 secret") from None
        result["authenticated"] = True

        if wait_for_message:
            try:
                frame = await asyncio.wait_for(message, timeout=timeout)
            except asyncio.TimeoutError:
                result["timed_out"] = True
            else:
                result["message"] = _describe_frame(frame, pattern, scope)
    except UiError:
        raise
    except Exception as e:
        raise UiError(f"连接企微失败（{type(e).__name__}）：{e}") from e
    finally:
        try:
            client.disconnect()
        except Exception:
            logger.debug("断开企微连接时出错（忽略）", exc_info=True)

    return result


def _describe_frame(
    frame: dict[str, Any], pattern: str | None, scope: str
) -> dict[str, Any]:
    """把收到的企微消息拆成页面要显示的诊断信息"""
    from botkit.engine import identity_matches
    from botkit.runtime import strip_mention
    from botkit.session import build_session_key

    body = frame.get("body") or {}
    userid = ((body.get("from") or {}).get("userid")) or ""
    content = (body.get("text") or {}).get("content", "")

    looks_encrypted = bool(userid) and (
        "=" in userid or len(userid) > 32 or not userid.isascii()
    )

    return {
        "userid": userid,
        "looks_encrypted": looks_encrypted,
        "chattype": body.get("chattype", ""),
        "chatid": body.get("chatid", ""),
        "content": content,
        "stripped": strip_mention(content),
        "session_key": build_session_key(frame, scope),
        "identity_ok": identity_matches(userid, pattern),
        "pattern": pattern or "",
    }


# --------------------------------------------------------------------------
# 给前端的选项表
# --------------------------------------------------------------------------


def form_options() -> dict[str, Any]:
    """
    枚举字段的可选值和一句话说明，前端拿它渲染下拉框。
    从配置层的常量来，加了新选项不会漏。
    """
    return {
        "llm_clients": [
            {
                "value": "openai",
                "label": "openai（推荐）",
                "note": "官方 SDK，自带重试。地址填到 /v1 为止",
            },
            {
                "value": "aiohttp",
                "label": "aiohttp（兼容模式）",
                "note": "裸 HTTP，用于不规范的兼容端点。地址填完整端点",
            },
        ],
        "session_scopes": [
            {
                "value": "group_user",
                "label": "群内按人隔离（推荐）",
                "note": "同一个群里两个人互不干扰",
            },
            {
                "value": "group",
                "label": "整群共享",
                "note": "几个人一起跟Agent讨论同一件事",
            },
            {
                "value": "user",
                "label": "按人跨群共享",
                "note": "同一个人在所有会话里共享上下文",
            },
        ],
        "identity_sources": [
            {
                "value": "wecom_userid",
                "label": "企微 userid（推荐）",
                "note": "直接用企微给的用户 ID",
            },
            {
                "value": "hook",
                "label": "自定义（hooks.py）",
                "note": "userid 不是业务工号时，在 hooks.py 里查表映射",
            },
        ],
        "log_levels": list(VALID_LOG_LEVELS),
        "identity_presets": [
            {"label": "不校验", "value": ""},
            {"label": "L/M + 6位数字（如 L220104）", "value": r"^[LM]\d{6}$"},
            {"label": "字母 + 6位数字", "value": r"^[A-Za-z]\d{6}$"},
            {"label": "纯数字工号", "value": r"^\d+$"},
        ],
        "message_fields": [
            {"key": "welcome", "label": "欢迎语", "note": "用户进入会话时"},
            {"key": "empty_input", "label": "只 @ 没说话", "note": "提醒用户描述需求"},
            {"key": "thinking", "label": "处理中提示", "note": "开始处理时先回一句"},
            {"key": "reset_done", "label": "已重置", "note": "命中重置关键词"},
            {"key": "error", "label": "出错提示", "note": "模型或工具出错时的兜底"},
            {
                "key": "identity_invalid",
                "label": "认不出身份",
                "note": "工号不符合格式或解析失败",
            },
            {
                "key": "tool_rounds_exceeded",
                "label": "工具轮次用尽",
                "note": "模型反复调工具打转",
            },
            {
                "key": "reply_suffix",
                "label": "回复后缀",
                "note": "自动追加到每条 LLM 回复末尾（署名等），留空则不加",
            },
        ],
        "valid": {
            "llm_clients": list(VALID_LLM_CLIENTS),
            "session_scopes": list(VALID_SESSION_SCOPES),
            "identity_sources": list(VALID_IDENTITY_SOURCES),
            "log_levels": list(VALID_LOG_LEVELS),
        },
    }
