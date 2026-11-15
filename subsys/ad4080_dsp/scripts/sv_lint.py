#!/usr/bin/env python3
"""组合 pyslang 语法/elaboration 诊断与工程文本风格检查。

pyslang 负责完整语法解析和 elaboration，诊断带文件与行号；文本规则
对应 docs/coding_standard.md。此门禁不代替仿真、CDC 或综合/STA。

既有工具选型记录指出，Verible v0.0-4296 的 macOS 资产实际为 Linux
ARM64 ELF（``ELF 64-bit ... ARM aarch64``），不能在 macOS 执行，
当时本机也无 Docker/Homebrew，因此采用 slang 的 Python 绑定 pyslang。
Linux CI 仍应按上述规范运行 verible-verilog-lint。该记录不作为本次环境探测。

用法：``python3 scripts/sv_lint.py rtl/*.sv``；``--strict`` 将警告计为失败。
退出码 0 只表示所选语法/文本检查通过，1 表示诊断或文本问题，
2 表示命令、输入或工具不可用；不选择默认套件。
"""

from __future__ import annotations

import argparse
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
        缺少 pyslang 时抛出 RuntimeError，不视为完成检查。
    """
    try:
        from pyslang import ast, syntax
    except ImportError as error:
        raise RuntimeError("pyslang 不可用；请通过 PYTHON 指定已安装该工具的解释器") from error

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
    text = path.read_text(encoding="utf-8")
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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strict", action="store_true", help="将解析器警告计为失败")
    parser.add_argument("files", type=Path, nargs="+", help="显式 RTL 文件与所需依赖")
    args = parser.parse_args(argv)
    files = args.files
    style = []
    try:
        for path in files:
            if path.suffix != ".sv" or not path.is_file():
                raise ValueError(f"不是可读的 .sv 文件：{path}")
            if path.stat().st_size > 300_000_000:
                raise ValueError(f"文件超过本机 300 MB 审计限制：{path}")
            style.extend(style_check(path))
        errors, warnings = parse_diagnostics(files)
    except (OSError, UnicodeError, ValueError, RuntimeError) as error:
        print(f"RESULT: BLOCKED: {error}", file=sys.stderr)
        return 2

    print(f"== pyslang: {len(files)} files, {len(errors)} errors, {len(warnings)} warnings ==")
    for e in errors:
        print(f"[ERROR] {e}")
    for w in warnings:
        print(f"[WARN ] {w}")

    print(f"== style: {len(style)} violations ==")
    for s in style:
        print(f"[STYLE] {s}")

    if errors or style or (args.strict and warnings):
        print("\nRESULT: FAIL")
        return 1
    print("\nRESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
