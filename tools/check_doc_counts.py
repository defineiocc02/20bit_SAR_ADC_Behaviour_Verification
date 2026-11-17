#!/usr/bin/env python3
"""核对索引文档里声称的文件数是否与实测一致。

为什么需要这个脚本
------------------
本仓库多次出现"文档声称 N 份、实测 M 份"的不一致。
人工核对容易漏，所以做成可执行检查。

用法:
    python3 tools/check_doc_counts.py     # 退出码 0=全对, 1=有不一致
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def count(cmd: str) -> int:
    out = subprocess.run(cmd, shell=True, cwd=ROOT, capture_output=True, text=True)
    return int(out.stdout.strip() or 0)


def main() -> int:
    facts = {
        "现行文档(不含_history)": count("find docs -name '*.md' | grep -v '/_history/' | wc -l"),
        "docs根级md": count("ls docs/*.md | wc -l"),
        "根目录md": count("ls *.md | wc -l"),
        "_history文档": count("find docs/_history -name '*.md' | wc -l"),
        "git跟踪文件": count("git ls-files | wc -l"),
    }
    print("=== 实测 ===")
    for k, v in facts.items():
        print(f"  {k}: {v}")

    # 检查 INDEX.md 里的声称
    index = (ROOT / "docs" / "INDEX.md").read_text()
    problems = []

    m = re.search(r"现行文档（(\d+) 份", index)
    if m and int(m.group(1)) != facts["现行文档(不含_history)"]:
        problems.append(f"INDEX 声称现行文档 {m.group(1)} 份，实测 {facts['现行文档(不含_history)']}")

    m = re.search(r"`_history/`，(\d+) 份", index)
    if m and int(m.group(1)) != facts["_history文档"]:
        problems.append(f"INDEX 声称 _history {m.group(1)} 份，实测 {facts['_history文档']}")

    m = re.search(r"根级 (\d+) 份", index)
    if m and int(m.group(1)) != facts["docs根级md"]:
        problems.append(f"INDEX 声称 docs 根级 {m.group(1)} 份，实测 {facts['docs根级md']}")

    print("\n=== 核对 ===")
    if problems:
        for p in problems:
            print(f"  MISMATCH: {p}")
        return 1
    print("  OK: 索引计数与实测一致")
    return 0


if __name__ == "__main__":
    sys.exit(main())
