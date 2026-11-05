#!/usr/bin/env python3
"""独立核对完整顶层测试台的 CSV／日志一致性，不运行 EDA。

DCP 身份来自完整运行清单，码值参考来自测试台；本工具不另外证明网表与源码
同源，也不形成第二套独立算术参考。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from run_vivado_full_mapped import audit_trace


def main() -> int:
    """写入新的审计记录，同时保留成功与失败诊断。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trace", type=Path, required=True)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--stages", type=int, choices=(5, 7), required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.out.exists():
        parser.error("refusing to overwrite an existing audit record")
    try:
        record = audit_trace(args.trace, args.log, args.stages)
        code = 0
    except (OSError, ValueError) as error:
        record = {"status": "FAILED", "error": str(error)}
        code = 1
    args.out.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print(record["status"])
    return code


if __name__ == "__main__":
    raise SystemExit(main())
