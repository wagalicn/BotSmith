"""
配置页面启动器（被 start.bat / start-ui.sh 调用，也可以直接 python start_ui.py）

放在 Python 里而不是 .bat / .sh 里做这些检查，是因为 Python 处理 UTF-8
中文没问题，而批处理脚本受终端代码页影响，中文很容易乱码。

做的事：
1. 检查 Python 版本（需要 3.11+）
2. 检查依赖，缺了就装
3. 起配置页面
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
LINE = "=" * 60

# 这几个包缺一个页面就起不来。名字是 import 名，不是 pip 包名。
REQUIRED = ("yaml", "aiohttp", "openai", "mcp", "dotenv")


def _print_header() -> None:
    print()
    print(LINE)
    print("  botkit 配置页面")
    print(LINE)
    print()


def _check_python() -> bool:
    if sys.version_info < (3, 11):
        v = ".".join(str(x) for x in sys.version_info[:3])
        print(f"[x] 需要 Python 3.11 或更高版本，当前是 {v}。")
        print("    框架用到了 BaseExceptionGroup，低版本会报语法错误。")
        return False
    print(f"[1/3] Python {'.'.join(str(x) for x in sys.version_info[:3])}")
    return True


def _missing_packages() -> list[str]:
    import importlib.util

    return [name for name in REQUIRED if importlib.util.find_spec(name) is None]


def _ensure_deps() -> bool:
    missing = _missing_packages()
    if not missing:
        print("[2/3] 依赖已就绪")
        return True

    print(f"[2/3] 缺少依赖：{'、'.join(missing)}，正在安装（第一次会慢一点）……")
    print()
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "-r", str(HERE / "requirements.txt")]
    )
    print()
    if result.returncode != 0:
        print("[x] 依赖安装失败。")
        print("    如果是公司网络问题，找同事要内网 pip 源，然后手动执行：")
        print("      python -m pip install -i 内网源地址 -r requirements.txt")
        return False

    # 装完再确认一遍，避免装了但仍缺某个
    still = _missing_packages()
    if still:
        print(f"[x] 装完之后仍然缺：{'、'.join(still)}")
        return False
    return True


def main() -> int:
    _print_header()

    if not _check_python():
        return 1
    if not _ensure_deps():
        return 1

    print("[3/3] 正在启动，浏览器会自动打开……")
    print()

    # 交给 botkit 自己的 ui 命令。用 subprocess 而不是直接 import，
    # 是为了让它以正常的模块入口运行，行为和命令行 python -m botkit ui 一致。
    # 多余的命令行参数（如 --port 8888）原样透传过去。
    extra = sys.argv[1:]
    try:
        return subprocess.call(
            [sys.executable, "-m", "botkit", "ui", *extra], cwd=str(HERE)
        )
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
