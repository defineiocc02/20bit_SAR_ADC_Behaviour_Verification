#!/usr/bin/env python3
# =============================================================================
# 文件名     : verify_rtl_quality.py
# 模块名     : 生产 RTL 严格门禁
# 版本       : 随 Git 源版本
# 功能       : 按现行生产清单实跑 Lint、通用结构检查和独立四态回归。
# 设计说明   : 真实外围清单由冻结 DSP 构建器生成。各种工件只落在独立目录。
#              文本规则只作用于设计列表，保留测试台检查未知态的能力。
# 变更记录   : 新增三套顶层入口与语法检查。
# =============================================================================
"""Verilator、sv2v、Yosys 与 Icarus 的生产 RTL 统一入口。"""
import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PERIPH = ROOT / "subsys/ad4080_peripherals"
DSP = ROOT / "subsys/ad4080_dsp/rtl"

# 字符串和注释使用相同长度替换以保持行号。注释里的横幅不参与运算符检查。
NONCODE = re.compile(r'//[^\n]*|/\*.*?\*/|"(?:\\.|[^"\\])*"', re.S)
TERM = r"!?\s*[A-Za-z_]\w*(?:\[\s*[\w:]+\s*\])?"
HOLD_LIST = re.compile(rf"(?:{TERM}|\{{\s*{TERM}(?:\s*,\s*{TERM})*\s*\}})\Z")
EDGE = re.compile(rf"{TERM}\Z")


def checked(command, folder, logfile):
    """每条命令同时保存退出码的上下文和实际工具输出。"""
    print("+", " ".join(map(str, command)), flush=True)
    with logfile.open("w", encoding="utf-8") as out:
        result = subprocess.run(command, cwd=folder, stdout=out, stderr=subprocess.STDOUT)
    if result.returncode:
        raise RuntimeError(f"{logfile} exit={result.returncode}\n" + "\n".join(
            logfile.read_text(errors="replace").splitlines()[-35:]))


def filelist(base, path):
    """现行清单中的已存在文件沿用原相对寻址。"""
    lines = (base / path).read_text().splitlines()
    return [row.strip() for row in lines if row.strip() and not row.lstrip().startswith("//")]


def check_code(sources):
    """拒绝设计清单的四态比较、非直接复位事件和复杂保持输入。"""
    findings = []
    for path in sorted(set(sources)):
        source = NONCODE.sub(lambda m: re.sub(r"[^\n]", " ", m[0]), path.read_text())
        patterns = (r"===|!==", r"\bnegedge\s+([^;\n]+?)\s*(?=\bor\b|\))",
                    r"\.hold_ni\s*\(([^()]*(?:\([^()]*\)[^()]*)*)\)")
        for group, pattern in enumerate(patterns):
            for match in re.finditer(pattern, source):
                if group == 1 and EDGE.fullmatch(match[1].strip()):
                    continue
                if group == 2 and HOLD_LIST.fullmatch(match[1].strip()):
                    continue
                line = source.count("\n", 0, match.start()) + 1
                findings.append(f"{path}:{line}: {match[0]}")

        # 数据寄存器允许无复位。明确写成复位分支的控制寄存器需用同域异步低有效。
        for match in re.finditer(r"always_ff\s*@\(([^)]+)\)\s*(?:begin\s*)?if\s*\(\s*!\s*(\w+reset\w*|rst_\w+)\s*\)", source):
            if "negedge" not in match[1]:
                findings.append(f"{path}:{source.count(chr(10), 0, match.start())+1}: mixed reset style")
    if findings:
        raise ValueError("\n".join(findings))
    print(f"RTL_SOURCE_RULES PASS files={len(set(sources))}")


def source_sets(out):
    """精确提取产品工程使用的单存储绑定，再解析两个原始生产层次。"""
    binding = out / "bound_dsp"
    checked([sys.executable, PERIPH / "integration/product/bind_dsp.py", ROOT, binding],
            out, out / "bind.log")
    core = [ROOT / rel for rel in filelist(ROOT, "flow/build/filelists/files_adc_v21.f")]
    dsp = sorted(DSP.glob("*.sv"))
    product = []
    for name in ("integration/files.f", "integration/readback_files.f", "integration/product/rtl.f"):
        for rel in filelist(PERIPH, name):
            product.append(out / rel if rel.startswith("bound_dsp/") else PERIPH / rel)
    return core, dsp, product


def run_lint(cases, out, verilator):
    """警告全量保留，检查 lint.vlt 内既有的单个文件名例外。"""
    waiver = PERIPH / "integration/lint.vlt"
    rows = [row.strip() for row in waiver.read_text().splitlines() if row.lstrip().startswith("lint_off")]
    if rows != ['lint_off -rule DECLFILENAME -file "*rtl/result_store.sv" -lines 55']:
        raise ValueError("lint.vlt differs from the one permitted filename entry")
    for top, sources, inc in cases:
        flags = [waiver] if top == "ad4080_peripheral_product" else []
        checked([verilator, "--lint-only", "-Wall", "-Werror-LATCH", "-Werror-WIDTH",
                 "-Werror-PINMISSING", "-Werror-SELRANGE", "--top-module", top,
                 *flags, *inc, *sources], out, out / f"{top}.lint.log")
        print(f"RTL_STRICT_LINT PASS top={top} warnings=0 errors=0")


def run_synthesis(cases, out, converter, yosys):
    """sv2v 仅解析前端兼容性，Yosys 独立展开并检查无未驱动或锁存结构。"""
    for top, sources, inc in cases:
        folder = out / top
        folder.mkdir()
        # 转换器的标准输出为网表，诊断单独落盘，避免告警进入 Verilog。
        with (folder / "sources.v").open("w") as netlist, (folder / "convert.log").open("w") as diagnostics:
            subprocess.run([converter, "-D", "SYNTHESIS", *inc, *sources], cwd=folder,
                           stdout=netlist, stderr=diagnostics, check=True)
        script = (f"read_verilog -sv sources.v; hierarchy -check -top {top}; proc; opt; "
                  "check -assert; select -assert-none t:$dlatch t:$adlatch")
        checked([yosys, "-Q", "-T", "-p", script], folder, folder / "check.log")
        print(f"RTL_GENERIC_STRUCTURE PASS top={top} latches=0")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--synth", action="store_true", help="另执行开源通用结构检查")
    args = parser.parse_args()
    out = args.out.resolve()
    if out == ROOT or ROOT in out.parents:
        parser.error("output path must be outside the repository")
    out.mkdir(parents=True, exist_ok=False)
    core, dsp, product = source_sets(out)
    check_code(core + dsp + [path for path in product if PERIPH in path.parents])
    cases = [
        ("adc_digital_ip_v21", core, []),
        ("sar20_ad4080_top", dsp, [f"-I{DSP}"]),
        ("ad4080_peripheral_product", product, [f"-I{out / 'bound_dsp'}"]),
    ]
    run_lint(cases, out, os.getenv("VERILATOR", "verilator"))
    if args.synth:
        run_synthesis([cases[0], cases[2]], out, os.getenv("SV2V", "sv2v"), os.getenv("YOSYS", "yosys"))


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError, ValueError, subprocess.CalledProcessError) as error:
        print(error, file=sys.stderr)
        raise SystemExit(1)
