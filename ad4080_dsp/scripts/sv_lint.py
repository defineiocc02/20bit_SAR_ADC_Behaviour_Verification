#!/usr/bin/env python3
"""AD4080 RTL 静态检查门禁。

做两件事：
  1. 用 pyslang 做**完整语法解析 + elaboration**，报告 file:line 级诊断。
  2. 按 docs/coding_standard.md 做文本级风格检查。

为什么不用 Verible：
  Verible 官方 v0.0-4296 的 macOS 资产实际打包的是 Linux ARM64 ELF
  （`file` 显示 "ELF 64-bit ... ARM aarch64"），在 macOS 上无法执行；
  本机也没有 Docker/Homebrew。因此这里用 pyslang（slang 的 Python 绑定，
  工业级 SystemVerilog 前端）承担语法/位宽/符号检查，风格规则用文本检查补齐。
  在 Linux CI 上仍应加跑 `verible-verilog-lint`（命令见 docs/coding_standard.md）。

用法：
    python3 scripts/sv_lint.py rtl/*.sv
    python3 scripts/sv_lint.py --strict rtl/*.sv   # 警告也判失败
退出码：
    0 通过；1 有错误（或 --strict 下有警告）；2 参数错误
"""

from __future__ import annotations

import sys
from pathlib import Path

MAX_LINE = 120  # 中英混排，取 120；纯英文项目建议 100
REQUIRED_HEADER = (
    "@file",
    "@brief",
    "@details",
    "@author",
    "@date",
    "@version",
    "手册依据",
    "时钟域",
    "复位",
    "可综合性",
)


def parse_diagnostics(files: list[Path]) -> tuple[list[str], list[str]]:
    """Return (errors, warnings) as 'file:line: code' strings."""
    try:
        from pyslang import ast, syntax
    except ImportError:
        return (["pyslang 未安装：pip install pyslang"], [])

    trees = [syntax.SyntaxTree.fromFile(str(f)) for f in files]
    sm = trees[0].sourceManager
    comp = ast.Compilation()
    for t in trees:
        comp.addSyntaxTree(t)

    errors: list[str] = []
    warnings: list[str] = []
    diags = comp.getAllDiagnostics()
    for i in range(len(diags)):
        d = diags[i]
        loc = d.location
        where = f"{sm.getFileName(loc)}:{sm.getLineNumber(loc)}"
        msg = f"{where}: {d.code}"
        (errors if d.isError() else warnings).append(msg)
    return errors, warnings


def style_check(path: Path) -> list[str]:
    """Text-level rules from the project coding standard."""
    problems: list[str] = []
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()

    head = "\n".join(lines[:80])
    for tag in REQUIRED_HEADER:
        if tag not in head:
            problems.append(f"{path}: 文件头缺少 {tag}")

    if "`default_nettype none" not in text:
        problems.append(f"{path}: 缺少 `default_nettype none")
    if "`timescale" not in text:
        problems.append(f"{path}: 缺少 `timescale")

    for no, line in enumerate(lines, 1):
        if "\t" in line:
            problems.append(f"{path}:{no}: 含制表符")
        if line.rstrip() != line:
            problems.append(f"{path}:{no}: 行尾空格")
        if len(line) > MAX_LINE:
            problems.append(f"{path}:{no}: 行宽 {len(line)} > {MAX_LINE}")
        stripped = line.strip()
        if stripped.startswith("reg ") or stripped.startswith("reg["):
            problems.append(f"{path}:{no}: 使用 reg，请改 logic")
        if "casex" in line:
            problems.append(f"{path}:{no}: 禁止 casex")
        if (
            stripped.startswith("always @")
            and "always_ff" not in line
            and "always_comb" not in line
        ):
            problems.append(f"{path}:{no}: 使用 always @，请改 always_ff/always_comb")

    if not text.endswith("\n"):
        problems.append(f"{path}: 文件末尾缺少换行")
    return problems


def main(argv: list[str]) -> int:
    """命令行入口：解析 .sv 诊断，--strict 时把警告也计为错误。"""
    strict = "--strict" in argv
    files = [Path(a) for a in argv if not a.startswith("--")]
    if not files:
        print(__doc__)
        return 2

    errors, warnings = parse_diagnostics(files)
    style = []
    for f in files:
        style.extend(style_check(f))

    print(f"== pyslang: {len(files)} files, {len(errors)} errors, {len(warnings)} warnings ==")
    for e in errors:
        print(f"[ERROR] {e}")
    for w in warnings:
        print(f"[WARN ] {w}")

    print(f"== style: {len(style)} violations ==")
    for s in style:
        print(f"[STYLE] {s}")

    if errors or style or (strict and warnings):
        print("\nRESULT: FAIL")
        return 1
    print("\nRESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
