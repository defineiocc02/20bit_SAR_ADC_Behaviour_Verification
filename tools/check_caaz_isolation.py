#!/usr/bin/env python3
"""CAAZ 隔离检查：主包/主 RTL/主测试不得出现 CAAZ 实现符号。

允许注释/文档提及 CAAZ（说明隔离边界），但禁止 import、实例化、filelist 引用。
退出码 0 = 隔离通过；1 = 发现残留。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATTERNS = (
    r"\bchopping_az\b",
    r"\bChoppingAZ",
    r"\brun_caaz_pipeline\b",
    r"\bpair_readout\b",
    r"\bcaaz_cycle_ctrl\b",
    r"\bcaaz_pair_processor\b",
)
SCAN = ("src/adi_model", "rtl")


def main() -> int:
    hits = []
    for top in SCAN:
        for p in (ROOT / top).rglob("*"):
            if not p.is_file() or p.suffix not in {".py", ".sv", ".svh", ".v", ".f", ".vh"}:
                continue
            if top == "extensions":
                continue  # 扩展本身允许
            text = p.read_text(errors="ignore")
            for pat in PATTERNS:
                for m in re.finditer(pat, text):
                    line = text.count("\n", 0, m.start()) + 1
                    hits.append(f"{p.relative_to(ROOT)}:{line}: {m.group(0)}")
    if hits:
        print("CAAZ_ISOLATION_FAIL")
        for h in hits:
            print(" ", h)
        return 1
    print("CAAZ_ISOLATION_PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
