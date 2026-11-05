#!/usr/bin/env python3
"""检查 RTL 文件头必需字段、文件名和模块名的一致性。

前 60 行须包含 @file、@brief、@details、@author、@date、@version，
以及手册依据、时钟域、复位、可综合性；同时检查 POSIX 末尾换行。
这只是文本合规检查，不进行 HDL 解析、综合或接口验收。

用法：``python3 scripts/check_headers.py rtl/*.sv``。全部合规返回 0，
任一文件存在问题返回 1；未提供文件返回 2。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REQUIRED_TAGS = ("@file", "@brief", "@details", "@author", "@date", "@version")
REQUIRED_CN = ("手册依据", "时钟域", "复位", "可综合性")


def check(path: Path) -> list[str]:
    """返回单个文件的文本合规问题；空列表表示本函数未发现问题。

    Args:
        path: 待检查的 UTF-8 RTL 文件路径；解码错误用替代字符保留。

    Returns:
        文件头字段、文件/模块名或末尾换行问题的中文列表。
    """
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
    """逐个检查 argv[1:] 指定的文件，打印问题并返回进程退出码。"""
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
