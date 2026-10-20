#!/usr/bin/env python3
"""RTL 文件头合规检查（工业级交付门禁的一部分）。

检查每个 .sv 文件的前 60 行是否包含规范要求的字段：
    @file @brief @details @author @date @version
    手册依据 时钟域 复位 可综合性
并校验 @file 字段与真实文件名一致。

用法:
    python3 scripts/check_headers.py rtl/*.sv
退出码:
    0 = 全部合规；1 = 存在缺失（逐条打印）
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REQUIRED_TAGS = ("@file", "@brief", "@details", "@author", "@date", "@version")
REQUIRED_CN = ("手册依据", "时钟域", "复位", "可综合性")


def check(path: Path) -> list[str]:
    """Return a list of human-readable problems for one file."""
    text = path.read_text(encoding="utf-8", errors="replace")
    head = "\n".join(text.splitlines()[:60])
    problems: list[str] = []

    for tag in REQUIRED_TAGS:
        if tag not in head:
            problems.append(f"缺少文件头字段 {tag}")
    for field in REQUIRED_CN:
        if field not in head:
            problems.append(f"缺少文件头字段 {field}")

    m = re.search(r"@file\s+(\S+)", head)
    if m and m.group(1) != path.name:
        problems.append(f"@file={m.group(1)} 与文件名 {path.name} 不一致")

    # 模块名与文件名一致性（Verible 也会查，这里给出更早的提示）
    mod = re.search(r"^\s*module\s+([A-Za-z_]\w*)", text, re.MULTILINE)
    if mod and mod.group(1) != path.stem:
        problems.append(f"module {mod.group(1)} 与文件名 {path.stem} 不一致")

    if not text.endswith("\n"):
        problems.append("文件末尾缺少换行（POSIX）")
    return problems


def main(argv: list[str]) -> int:
    """命令行入口：逐个检查 .sv 文件头是否符合规范，返回失败文件数。"""
    files = [Path(a) for a in argv[1:]]
    if not files:
        print("usage: check_headers.py <file.sv> [...]", file=sys.stderr)
        return 2

    bad = 0
    for path in files:
        problems = check(path)
        if problems:
            bad += 1
            print(f"[FAIL] {path}")
            for p in problems:
                print(f"       - {p}")
        else:
            print(f"[ OK ] {path}")

    print(f"\n{len(files) - bad}/{len(files)} files compliant")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
