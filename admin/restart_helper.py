#!/usr/bin/env python3
"""重启辅助脚本（占位文件）

说明：
- 该脚本通常由 admin/restart_api.py 在运行时动态生成并覆盖，用于执行主程序重启。
- 仓库中若残留 Windows 编码内容会导致 Python 语法解析失败，因此提供一个可解析的占位版本。

用法（可选）：
  python3 admin/restart_helper.py <python_executable> <main_py> <cwd>
"""

from __future__ import annotations

import os
import subprocess
import sys
import time


def main() -> int:
    time.sleep(2)

    if len(sys.argv) < 3:
        print("Usage: restart_helper.py <python_executable> <main_py> [cwd]", file=sys.stderr)
        return 2

    python_executable = sys.argv[1]
    main_py = sys.argv[2]
    cwd = sys.argv[3] if len(sys.argv) > 3 else os.getcwd()

    subprocess.Popen([python_executable, main_py], cwd=cwd, shell=False)

    try:
        os.remove(__file__)
    except Exception:
        pass

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
