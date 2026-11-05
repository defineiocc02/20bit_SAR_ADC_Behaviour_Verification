#!/usr/bin/env python3
"""组合 pyslang 语法/elaboration 诊断与工程文本风格检查。

pyslang 负责完整语法解析和 elaboration，诊断带文件与行号；文本规则
对应 docs/coding_standard.md。此门禁不代替仿真、CDC 或综合/STA。

既有工具选型记录指出，Verible v0.0-4296 的 macOS 资产实际为 Linux
ARM64 ELF（``ELF 64-bit ... ARM aarch64``），不能在 macOS 执行，
当时本机也无 Docker/Homebrew，因此采用 slang 的 Python 绑定 pyslang。
Linux CI 仍应按上述规范运行 verible-verilog-lint。该记录不作为本次环境探测。

用法：``python3 scripts/sv_lint.py rtl/*.sv``；``--strict`` 将警告计为失败。
退出码 0 表示本门禁通过，1 表示错误、风格问题或 strict 模式警告，
2 表示没有提供文件。
"""

from __future__ import annotations

import sys
from pathlib import Path

MAX_LINE = 120  # 文本检查按字符计数；中英混排上限 120，纯英文项目建议 100。
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
    """解析并 elaboration 所选文件，分开返回错误与警告。

    Args:
        files: 非空路径列表；调用方负责提供可解析的源码依赖闭包。

    Returns:
        ``(errors, warnings)``；各项采用 ``file:line: code`` 字符串。
        缺少 pyslang 时错误列表包含安装提示，不视为完成检查。
    """
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
    """按工程编码规范返回文本问题列表，不进行 HDL 语义验证。"""
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
    """检查 argv 指定的 RTL 文件，打印诊断并返回门禁退出码。

    ``--strict`` 使警告也导致失败；argv 不包含脚本文件名。
    """
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
