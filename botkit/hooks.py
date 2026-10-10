"""
可选的代码扩展点

大部分 bot 靠 bot.yaml + prompt.md 就够了，不需要写一行 Python。
需要特殊逻辑时，在 bot 目录放一个 hooks.py，实现下面任意几个函数即可：

    resolve_identity(frame)             -> str | None    自定义身份解析
    on_message(text, ctx)               -> str | None    前置拦截，返回文本则不进 LLM
    before_tool_call(tool_name, args, ctx) -> dict       工具参数改写
    after_tool_call(tool_name, result, ctx) -> str       工具结果加工
    before_reply(text, ctx)             -> str           最终回复加工

另有一个不同类的扩展点，用于给对外 HTTP 服务挂 bot 特有的自定义路由：

    register_routes(app, config, pipeline) -> None       注册自定义 HTTP 路由
    register_routes(app, config, pipeline, base_path="") -> None   带前缀注册（可选）

它在对外接口启动时调用一次，直接操作 aiohttp app，和上面 5 个逐消息
调用的 hook 处理方式不同（出错直接抛，不静默降级）。想让端点在多 Agent
网关下自动带前缀的 bot，可多声明一个 base_path 形参，用 base_path + "/xxx"
注册；不声明则按裸路径注册，行为与以前完全一致。

约定：
- 没定义的函数走框架默认行为，不需要写空实现
- 同步和 async def 都支持
- hook 抛异常时记日志并降级到默认行为，不让整条消息挂掉
  （扩展点出问题不应该让Agent变哑巴）
"""

from __future__ import annotations

import importlib.util
import inspect
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger("botkit.hooks")

# 框架认识的 hook 名字。写错名字会被提示，不会静默失效。
HOOK_NAMES = (
    "resolve_identity",
    "on_message",
    "before_tool_call",
    "after_tool_call",
    "before_reply",
)

# register_routes 是另一类扩展点：不是消息处理 hook，而是让 bot 往对外
# HTTP 服务上挂自己的自定义路由（比如某个 bot 特有的结构化分析端点）。
# 签名 register_routes(app, config, pipeline)，在 create_api_app 组装完
# /chat、/health 之后调用一次。和 5 个消息 hook 分开处理：它只在启动时
# 跑一次、直接操作 aiohttp app，不需要逐次调用的异常降级/返回值校验。
ROUTES_HOOK_NAME = "register_routes"


def _accepts_base_path(func: Callable[..., Any]) -> bool:
    """
    判断 bot 的 register_routes 是否愿意接收 base_path 前缀。

    只有显式声明了名为 base_path 的参数，或带 **kwargs 的，才按新式调用
    （传 base_path=...）；否则按老的三参签名调用，保证现有 bot 行为不变。
    签名无法内省时（极少数情况）保守按老签名处理。
    """
    try:
        params = inspect.signature(func).parameters
    except (TypeError, ValueError):
        return False
    for p in params.values():
        if p.name == "base_path" or p.kind is inspect.Parameter.VAR_KEYWORD:
            return True
    return False


class HookError(Exception):
    """hooks.py 加载失败（语法错误、import 不到依赖等）"""


@dataclass
class HookContext:
    """传给 hook 的上下文"""

    identity: str | None = None
    session_key: str | None = None
    config: Any = None
    logger: logging.Logger = logger
    frame: dict[str, Any] | None = None


async def _maybe_await(func: Callable[..., Any], *args: Any) -> Any:
    """同步/异步 hook 都能调"""
    result = func(*args)
    if inspect.isawaitable(result):
        return await result
    return result


class Hooks:
    """
    一组 hook 的持有者。没实现的 hook 走默认行为。

    每个 hook 都包了异常兜底：出错记 ERROR 日志（带完整堆栈方便定位），
    然后按「没实现这个 hook」处理。
    """

    def __init__(
        self,
        impls: dict[str, Callable[..., Any]] | None = None,
        source: str = "(无)",
        register_routes: Callable[..., Any] | None = None,
    ):
        self._impls = impls or {}
        self.source = source
        self._register_routes = register_routes

    def has(self, name: str) -> bool:
        return name in self._impls

    @property
    def has_routes(self) -> bool:
        """bot 是否实现了 register_routes（要往对外接口挂自定义路由）"""
        return self._register_routes is not None

    def register_routes(
        self, app: Any, config: Any, pipeline: Any, base_path: str = ""
    ) -> None:
        """
        调用 bot 的 register_routes，让它往 aiohttp app 挂自定义路由。

        只在对外服务启动时跑一次。出错抛出去（而不是像消息 hook 那样静默
        降级）—— 路由挂载失败意味着 bot 的核心能力起不来，应该让启动直接
        失败并暴露原因，而不是悄悄少一个端点。

        base_path 是路由前缀（默认 ""）：单 Agent 模式传空（裸路径，行为不变），
        多 Agent 网关模式传 "/{agent}"，bot 用 base_path + "/xxx" 注册即可两种
        模式复用同一份代码。为保持向后兼容，只有当 bot 的 register_routes
        声明了 base_path 形参时才传入；老 bot（三参签名）仍按原样调用，行为不变。
        """
        if self._register_routes is None:
            return
        if _accepts_base_path(self._register_routes):
            self._register_routes(app, config, pipeline, base_path=base_path)
        else:
            self._register_routes(app, config, pipeline)

    @property
    def implemented(self) -> list[str]:
        return [n for n in HOOK_NAMES if n in self._impls]

    # -- 各 hook -----------------------------------------------------------

    async def resolve_identity(self, frame: dict[str, Any]) -> str | None:
        """
        自定义身份解析。没实现时返回 None，调用方回退到默认行为
        （取企微 userid）。
        """
        fn = self._impls.get("resolve_identity")
        if fn is None:
            return None
        try:
            result = await _maybe_await(fn, frame)
        except Exception:
            logger.exception("hook resolve_identity 出错，回退到默认身份解析")
            return None
        if result is not None and not isinstance(result, str):
            logger.error(
                "hook resolve_identity 应该返回字符串或 None，实际返回 %s，已忽略",
                type(result).__name__,
            )
            return None
        return result

    async def on_message(self, text: str, ctx: HookContext) -> str | None:
        """前置拦截。返回字符串则直接用它回复，返回 None 表示继续正常流程"""
        fn = self._impls.get("on_message")
        if fn is None:
            return None
        try:
            result = await _maybe_await(fn, text, ctx)
        except Exception:
            logger.exception("hook on_message 出错，按未拦截处理，继续正常流程")
            return None
        if result is None:
            return None
        if not isinstance(result, str):
            logger.error(
                "hook on_message 应该返回字符串或 None，实际返回 %s，已忽略",
                type(result).__name__,
            )
            return None
        return result

    async def before_tool_call(
        self, tool_name: str, args: dict[str, Any], ctx: HookContext
    ) -> dict[str, Any]:
        """工具参数改写。出错或返回值不是 dict 时用原参数"""
        fn = self._impls.get("before_tool_call")
        if fn is None:
            return args
        try:
            result = await _maybe_await(fn, tool_name, args, ctx)
        except Exception:
            logger.exception("hook before_tool_call 出错，使用未改写的原参数")
            return args
        if not isinstance(result, dict):
            logger.error(
                "hook before_tool_call 应该返回 dict，实际返回 %s，使用原参数",
                type(result).__name__,
            )
            return args
        return result

    async def after_tool_call(
        self, tool_name: str, result: str, ctx: HookContext
    ) -> str:
        """工具结果加工。出错或返回值不是字符串时用原结果"""
        fn = self._impls.get("after_tool_call")
        if fn is None:
            return result
        try:
            out = await _maybe_await(fn, tool_name, result, ctx)
        except Exception:
            logger.exception("hook after_tool_call 出错，使用未加工的原结果")
            return result
        if not isinstance(out, str):
            logger.error(
                "hook after_tool_call 应该返回字符串，实际返回 %s，使用原结果",
                type(out).__name__,
            )
            return result
        return out

    async def before_reply(self, text: str, ctx: HookContext) -> str:
        """最终回复加工。出错或返回值不是字符串时用原文本"""
        fn = self._impls.get("before_reply")
        if fn is None:
            return text
        try:
            out = await _maybe_await(fn, text, ctx)
        except Exception:
            logger.exception("hook before_reply 出错，使用未加工的原回复")
            return text
        if not isinstance(out, str):
            logger.error(
                "hook before_reply 应该返回字符串，实际返回 %s，使用原回复",
                type(out).__name__,
            )
            return text
        return out


def load_hooks(path: Path | None) -> Hooks:
    """
    从文件加载 hooks。path 为 None（没配 hook）时返回全默认的空实现。

    加载失败会抛 HookError —— 文件明明存在却 import 不了，
    通常是语法错误或少装依赖，静默跳过只会让人更难查。
    """
    if path is None:
        logger.debug("没有 hooks 文件，全部使用框架默认行为")
        return Hooks()

    path = Path(path)
    if not path.is_file():
        logger.warning("hooks 文件不存在，全部使用框架默认行为：%s", path)
        return Hooks()

    module_name = f"botkit_hooks_{path.stem}_{abs(hash(str(path.resolve())))}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise HookError(f"无法加载 hooks 文件：{path}")

    module = importlib.util.module_from_spec(spec)
    # 先塞进 sys.modules，这样 hooks.py 里用 dataclass 等依赖模块自身的写法也能工作
    sys.modules[module_name] = module

    # 不生成 __pycache__。bot 目录是给同事看的，应该干干净净只有那几个
    # 配置文件，多一个看不懂的缓存目录只会让人犯疑。
    # hooks.py 就一个小文件，省掉字节码缓存没有可感知的代价。
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    except Exception as e:
        del sys.modules[module_name]
        raise HookError(
            f"加载 hooks 文件 {path} 出错：{type(e).__name__}: {e}"
        ) from e
    finally:
        sys.dont_write_bytecode = previous

    impls: dict[str, Callable[..., Any]] = {}
    for name in HOOK_NAMES:
        fn = getattr(module, name, None)
        if fn is None:
            continue
        if not callable(fn):
            logger.error("%s 里的 %s 不是函数，已忽略", path, name)
            continue
        impls[name] = fn

    # register_routes：可选的自定义路由挂载扩展点（单独收集，不进 impls）
    register_routes = getattr(module, ROUTES_HOOK_NAME, None)
    if register_routes is not None and not callable(register_routes):
        logger.error("%s 里的 %s 不是函数，已忽略", path, ROUTES_HOOK_NAME)
        register_routes = None

    _warn_on_typos(module, impls, path, register_routes)

    loaded = list(impls)
    if register_routes is not None:
        loaded.append(ROUTES_HOOK_NAME)
    if loaded:
        logger.info("已从 %s 加载 hook：%s", path, "、".join(loaded))
    else:
        logger.info("%s 里没有可用的 hook，全部使用框架默认行为", path)

    return Hooks(impls, source=str(path), register_routes=register_routes)


def _warn_on_typos(
    module: Any,
    impls: dict[str, Callable[..., Any]],
    path: Path,
    register_routes: Callable[..., Any] | None = None,
) -> None:
    """
    hooks.py 里定义了公开函数但名字不在约定列表里，很可能是拼错了。
    提示一下，免得同事以为写了 hook 其实没生效。
    """
    known = set(impls)
    if register_routes is not None:
        known.add(ROUTES_HOOK_NAME)

    suspicious: list[str] = []
    for attr in dir(module):
        if attr.startswith("_") or attr in known:
            continue
        value = getattr(module, attr)
        if not inspect.isfunction(value):
            continue
        # 只关心定义在这个文件里的函数，import 进来的不算
        if getattr(value, "__module__", None) != module.__name__:
            continue
        suspicious.append(attr)

    if suspicious:
        logger.warning(
            "%s 里这些函数不是框架认识的 hook，不会被调用：%s。"
            "框架支持的 hook 是：%s",
            path,
            "、".join(sorted(suspicious)),
            "、".join(HOOK_NAMES + (ROUTES_HOOK_NAME,)),
        )
