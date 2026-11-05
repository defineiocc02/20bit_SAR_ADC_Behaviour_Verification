#!/usr/bin/env python3
"""将模型输出中的 RTL 文本抽取为独立 SystemVerilog 文件。

抽取规则参考 RTLCoder（HKUST-Zhiyao）的
https://github.com/hkust-zhiyao/RTL-Coder 中
``benchmark_inference/test_on_rtllm.py``：优先取 Markdown 围栏内容，
去掉首个 module 之前的文字，在重复标记 endmodulemodule 处截断，
最后保留到末个 endmodule；缺失结束标记时补齐。可选补 default_nettype。

``--check`` 在写出文件后调用 pyslang 进行语法解析；它不证明 elaboration、
协议或数值正确性。用法：
``python3 scripts/rtl_postprocess.py raw.txt -o rtl/foo.sv --check``。
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


def extract_code(text: str) -> str:
    """按既有 RTLCoder 式规则抽取代码，返回以换行结束的文本。

    Args:
        text: 可含 Markdown 围栏和解释文字的原始模型输出。

    Returns:
        截取到最后一个 endmodule 的文本；缺失时补 endmodule。
    """
    # 优先使用围栏内容，后续规则只处理选中的代码文本。
    fence = re.search(r"```(?:systemverilog|verilog|sv)?\s*\n(.*?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1)

    # 以首个 module 声明为正文起点，去除前置说明。
    m = re.search(r"^\s*module\s+\w+", text, re.MULTILINE)
    if m:
        text = text[m.start() :]

    # 重复结束标记按 RTLCoder-Deepseek 既有规则截断。
    if "endmodulemodule" in text:
        text = text.split("endmodulemodule", 1)[0] + "endmodule\n"

    # 保留到最后一个结束标记；缺失时显式补齐，不据此声称语法有效。
    idx = text.rfind("endmodule")
    if idx >= 0:
        text = text[: idx + len("endmodule")] + "\n"
    else:
        text = text.rstrip() + "\nendmodule\n"

    return text.rstrip() + "\n"


def syntax_check(path: Path) -> list[str]:
    """用 pyslang 解析文件并返回语法诊断码；未安装时返回提示。

    返回空列表只表示语法树未报告诊断，不表示完成 elaboration 或仿真。
    """
    try:
        from pyslang import syntax
    except ImportError:
        return ["pyslang 未安装，跳过语法检查"]
    tree = syntax.SyntaxTree.fromFile(str(path))
    return [f"{d.code}" for d in tree.diagnostics]


def main() -> int:
    """抽取文本、按选项补 default_nettype 并写文件，可选执行语法检查。

    Returns:
        写出成功且无请求的语法诊断时为 0；有诊断或缺少 pyslang 时为 1。
    """
    ap = argparse.ArgumentParser()
    ap.add_argument("input", type=Path)
    ap.add_argument("-o", "--output", type=Path)
    ap.add_argument("--check", action="store_true", help="pyslang 语法检查")
    ap.add_argument("--add-nettype", action="store_true")
    args = ap.parse_args()

    code = extract_code(args.input.read_text(encoding="utf-8", errors="replace"))
    if args.add_nettype and "`default_nettype none" not in code:
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
