"""
wheel 冒烟测试

在干净的虚拟环境里安装构建好的 wheel，确认安装后的包真的能用。
单元测试跑的是源码目录，测不出"打包时漏了文件"或"入口点装不上"，这个脚本补的就是这块。

用法（在仓库根目录）：
    python -m pip wheel . --no-deps --wheel-dir dist-check
    python tools/smoke_wheel.py dist-check          # 目录里只能有一个 .whl
    python tools/smoke_wheel.py path/to/x.whl       # 或直接给 wheel 文件

检查项（全部在仓库之外的空目录里、用新虚拟环境的 Python 执行）：
    1. botkit 能从 site-packages 导入，不是从源码目录导入
    2. `botkit --help` 和 `python -m botkit --help` 都能运行
    3. `botkit new` 能用打进 wheel 的模板生成完整的 Agent 骨架
    4. 配置页的首页和静态文件，通过真实路由请求能拿到 200

需要联网安装依赖。临时目录只清理脚本自己创建的那一个，不碰其它位置。
本脚本只依赖标准库。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import venv
from pathlib import Path

# botkit new 应该生成的文件（对应 botkit/scaffold.py 的 TEMPLATE_FILES）
EXPECTED_BOT_FILES = ["bot.yaml", "prompt.md", "hooks.py", ".env.example", "README.md"]

# 应该随 wheel 安装的 5 个模板
EXPECTED_TEMPLATES = [
    "README.md.tmpl",
    "bot.yaml.tmpl",
    "env.example.tmpl",
    "hooks.py.tmpl",
    "prompt.md.tmpl",
]

# 在虚拟环境里执行：包的位置和打进 wheel 的资源。
INSPECT_CODE = r"""
import json
from pathlib import Path

import botkit
from botkit.scaffold import TEMPLATE_DIR
from botkit.ui.server import STATIC_DIR

print(json.dumps({
    "botkit_file": str(Path(botkit.__file__).resolve()),
    "version": botkit.__version__,
    "templates": sorted(p.name for p in TEMPLATE_DIR.glob("*.tmpl")),
    "static": sorted(p.name for p in Path(STATIC_DIR).iterdir() if p.is_file()),
}, ensure_ascii=False))
"""

# 在虚拟环境里执行：用真实的 aiohttp 路由请求配置页，不占用固定端口。
UI_CODE = r"""
import asyncio
import json
import tempfile

from aiohttp.test_utils import TestClient, TestServer

from botkit.ui.server import create_app

CASES = [
    ("/", "<!DOCTYPE html>"),
    ("/static/app.js", "function"),
    ("/static/i18n.js", "const I18N"),
    ("/static/style.css", "{"),
]


async def main():
    results = []
    with tempfile.TemporaryDirectory() as root:
        async with TestClient(TestServer(create_app(root))) as client:
            for path, needle in CASES:
                resp = await client.get(path)
                body = await resp.text()
                results.append({"path": path, "status": resp.status, "found": needle in body})
    print(json.dumps(results))


asyncio.run(main())
"""


class Smoke:
    """收集检查结果：每项打印一行，最后统一给出退出码。"""

    def __init__(self) -> None:
        self.failed: list[str] = []

    def check(self, name: str, ok: bool, detail: str = "") -> bool:
        mark = "ok  " if ok else "FAIL"
        print(f"  [{mark}] {name}" + (f"  ({detail})" if detail else ""))
        if not ok:
            self.failed.append(name)
        return ok


def find_wheel(target: Path) -> Path:
    if target.is_file():
        return target.resolve()
    wheels = sorted(target.glob("*.whl"))
    if len(wheels) != 1:
        raise SystemExit(
            f"需要在 {target} 里找到恰好一个 .whl 文件，实际找到 {len(wheels)} 个"
        )
    return wheels[0].resolve()


def clean_env() -> dict[str, str]:
    """子进程环境：去掉会让 Python 回到源码目录的变量，并统一用 UTF-8 输出。"""
    env = dict(os.environ)
    for name in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"):
        env.pop(name, None)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def run(cmd: list[str], cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=600,
    )


def venv_paths(venv_dir: Path) -> tuple[Path, Path]:
    """返回（虚拟环境的 python，botkit 命令）。"""
    if os.name == "nt":
        return venv_dir / "Scripts" / "python.exe", venv_dir / "Scripts" / "botkit.exe"
    return venv_dir / "bin" / "python", venv_dir / "bin" / "botkit"


def last_json(text: str) -> object:
    """取输出的最后一行当 JSON 解析（前面可能有 pip 或库打印的杂项）。"""
    return json.loads(text.strip().splitlines()[-1])


def smoke(wheel: Path) -> int:
    print(f"wheel: {wheel.name}")
    result = Smoke()

    # ignore_cleanup_errors：Windows 上偶尔有文件被占用，清理失败不应让测试失败
    with tempfile.TemporaryDirectory(prefix="botsmith-smoke-", ignore_cleanup_errors=True) as tmp_name:
        tmp = Path(tmp_name)
        venv_dir = tmp / "venv"
        # 与仓库无关的空目录。cwd 不能在仓库里，否则 `python -m botkit` 会导入源码目录
        work = tmp / "work"
        work.mkdir()
        env = clean_env()

        print("创建干净的虚拟环境并安装 wheel（会联网安装依赖）...")
        venv.EnvBuilder(with_pip=True).create(venv_dir)
        python, botkit_cmd = venv_paths(venv_dir)

        install = run(
            [str(python), "-m", "pip", "install", "--disable-pip-version-check", str(wheel)],
            work,
            env,
        )
        if not result.check("wheel 安装成功", install.returncode == 0):
            print(install.stdout[-2000:], install.stderr[-2000:], sep="\n")
            return 1

        print("检查：")

        # 1. 包来自 site-packages，资源随 wheel 安装
        info_run = run([str(python), "-c", INSPECT_CODE], work, env)
        if not result.check("能导入已安装的 botkit", info_run.returncode == 0, info_run.stderr.strip()[-300:]):
            return 1
        info = last_json(info_run.stdout)
        assert isinstance(info, dict)
        from_site = "site-packages" in Path(str(info["botkit_file"])).parts
        result.check("botkit 来自 site-packages，不是源码目录", from_site, str(info["botkit_file"]))
        result.check("5 个模板文件已随 wheel 安装", info["templates"] == EXPECTED_TEMPLATES, str(info["templates"]))
        result.check("UI 静态文件已随 wheel 安装", len(info["static"]) == 4, str(info["static"]))

        # 2. 命令行入口
        entry = run([str(botkit_cmd), "--help"], work, env)
        result.check("`botkit --help` 可运行", entry.returncode == 0 and "validate" in entry.stdout, f"退出码 {entry.returncode}")
        module = run([str(python), "-m", "botkit", "--help"], work, env)
        result.check("`python -m botkit --help` 可运行", module.returncode == 0 and "validate" in module.stdout, f"退出码 {module.returncode}")

        # 3. 用打进 wheel 的模板生成骨架
        new = run([str(botkit_cmd), "new", "smoke-bot", "--dir", "bots"], work, env)
        bot_dir = work / "bots" / "smoke-bot"
        missing = [f for f in EXPECTED_BOT_FILES if not (bot_dir / f).is_file()]
        result.check(
            "`botkit new` 生成完整骨架",
            new.returncode == 0 and not missing,
            f"缺少 {missing}" if missing else f"生成 {len(EXPECTED_BOT_FILES)} 个文件",
        )

        # 4. 配置页：首页与静态文件走真实路由
        ui = run([str(python), "-c", UI_CODE], work, env)
        if result.check("配置页应用能启动", ui.returncode == 0, ui.stderr.strip()[-300:]):
            for item in last_json(ui.stdout):  # type: ignore[union-attr]
                ok = item["status"] == 200 and item["found"]
                result.check(f"GET {item['path']}", ok, f"状态 {item['status']}，内容{'符合' if item['found'] else '不符'}")

    print()
    if result.failed:
        print(f"失败 {len(result.failed)} 项：{'；'.join(result.failed)}")
        return 1
    print("冒烟测试全部通过")
    return 0


def use_utf8_output() -> None:
    """
    把本进程的 stdout / stderr 改成 UTF-8。

    输出被重定向时（比如 CI 的日志），Windows 上的 Python 用系统代码页编码（美区是 cp1252），
    脚本里的中文会触发 UnicodeEncodeError。子进程已经在 clean_env() 里统一成 UTF-8，这里补上本进程。
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


def main() -> int:
    use_utf8_output()
    parser = argparse.ArgumentParser(description="在干净虚拟环境里安装 wheel 并做冒烟测试")
    parser.add_argument("wheel", type=Path, help=".whl 文件，或只含一个 .whl 的目录")
    args = parser.parse_args()
    return smoke(find_wheel(args.wheel))


if __name__ == "__main__":
    sys.exit(main())
