#!/usr/bin/env python3
"""本机 VCS 严格组件入口；沿用显式 --tops、--out 和绑定要求。

调用者负责配置现有 VCS/许可证环境。本入口不连接远程主机、不修改环境、
不生成默认参数或兼容 golden；完成依据由 run_open_rtl 的真实执行记录给出。
"""

from __future__ import annotations

import sys

sys.dont_write_bytecode = True  # 在导入同目录模块前关闭字节码写入，避免在工作树生成缓存。

from run_open_rtl import main  # noqa: E402

if __name__ == "__main__":
    if any(arg == "--simulator" or arg.startswith("--simulator=") for arg in sys.argv[1:]):
        raise SystemExit("run_rtl_sim.py selects VCS; use run_open_rtl.py for other simulators")
    raise SystemExit(main(["--simulator", "vcs", *sys.argv[1:]]))
