#!/usr/bin/env python3
"""把 LLM / Agent 输出的原始文本清洗成可入库的 SystemVerilog 文件。

清洗规则来自 RTLCoder（HKUST-Zhiyao）的推理后处理实践：
  https://github.com/hkust-zhiyao/RTL-Coder  benchmark_inference/test_on_rtllm.py
  1. 若输出被 Markdown 代码围栏包裹，取围栏内的内容。
  2. 去掉第一个 `module` 之前的解释性文字。
  3. 在 `endmodulemodule`（RTLCoder-Deepseek 的重复结束标记）处截断。
  4. 截到最后一个 `endmodule`，并保证文件以 `endmodule` 结束。
  5. 可选补 `default_nettype none/wire`。

随后可用 `--check` 调 pyslang 做语法解析，避免把不可编译的代码写进仓库。

用法：
    python3 scripts/rtl_postprocess.py raw.txt -o rtl/foo.sv --check
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


def extract_code(text: str) -> str:
    """Apply the RTLCoder-style extraction rules."""
    # 1) Markdown fence
    fence = re.search(r"```(?:systemverilog|verilog|sv)?\s*\n(.*?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1)

    # 2) drop prose before the first module
    m = re.search(r"^\s*module\s+\w+", text, re.MULTILINE)
    if m:
        text = text[m.start():]

    # 3) RTLCoder-Deepseek duplicated terminator
    if "endmodulemodule" in text:
        text = text.split("endmodulemodule", 1)[0] + "endmodule\n"

    # 4) cut at the last endmodule
    idx = text.rfind("endmodule")
    if idx >= 0:
        text = text[: idx + len("endmodule")] + "\n"
    else:
        text = text.rstrip() + "\nendmodule\n"

    return text.rstrip() + "\n"


def syntax_check(path: Path) -> list[str]:
    try:
        from pyslang import syntax
    except ImportError:
        return ["pyslang 未安装，跳过语法检查"]
    tree = syntax.SyntaxTree.fromFile(str(path))
    return [f"{d.code}" for d in tree.diagnostics]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("input", type=Path)
    ap.add_argument("-o", "--output", type=Path)
    ap.add_argument("--check", action="store_true", help="pyslang 语法检查")
    ap.add_argument("--add-nettype", action="store_true")
    args = ap.parse_args()

    code = extract_code(args.input.read_text(encoding="utf-8", errors="replace"))
    if args.add_nettype:
        if "`default_nettype none" not in code:
            code = code.replace("module ", "`default_nettype none\n\nmodule ", 1)
            code += "\n`default_nettype wire\n"

    out = args.output or args.input.with_suffix(".sv")
    out.write_text(code, encoding="utf-8")
    print(f"wrote {out} ({len(code.splitlines())} lines)")

    if args.check:
        diags = syntax_check(out)
        if diags:
            print("syntax diagnostics:")
            for d in diags:
                print("  -", d)
            return 1
        print("syntax: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
