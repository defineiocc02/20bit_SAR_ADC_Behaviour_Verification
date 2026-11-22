#!/usr/bin/env bash
# =============================================================================
# 文件名     : run.sh
# 模块名     : AD4080 DSP 定向回归
# 版本       : v1.0
# 功能       : 临时导出固定版本，用 Verilator 执行原生旁路测试及独立滤波通路台架。
# 设计说明   : 仅以 git archive 读取原仓，台架、二进制、日志都在独立 ASCII 临时目录
#              构建。三个仿真各有独立 Mdir，一个构建或断言失败立即退出并可审查日志。
# 变更记录   : 2026-11-20 核对重定时后启动码、起录及独立数值回归的完成标记。
# =============================================================================
set -euo pipefail

# =========================================================================
# 1. 固定源与运行目录
# =========================================================================
repo=${1:?用法: run.sh 含冻结提交的工程目录}
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
IFS= read -r commit < "$script_dir/../../evidence/dsp-revision.txt"
if [[ ! $commit =~ ^[0-9a-f]{40}$ ]]; then
    printf '%s\n' 'dsp-revision.txt must contain a full commit SHA' >&2
    exit 2
fi
work=$(mktemp -d /tmp/ad4080-dsp-review.XXXXXXXX)
printf 'build directory: %s\n' "$work"
printf 'DSP source revision: %s\n' "$commit"
git -C "$repo" archive "$commit" rtl/ad4080_dsp | tar -xf - -C "$work"
cp -- "$script_dir/tb_dsp_path.sv" "$work/tb_dsp_path.sv"
cp -- "$script_dir/ad4080_record_guard.sv" "$work/ad4080_record_guard.sv"
cp -- "$script_dir/../../io/ad4080_fifo_record_framer.sv" "$work/ad4080_fifo_record_framer.sv"
cd -- "$work"

# Make 可能直接调用 python。使用执行包装避免 macOS 的 python3 shim 改名后仍按 python 查找。
mkdir bin
printf '%s\n' '#!/usr/bin/env sh' 'exec python3 "$@"' > bin/python
chmod +x bin/python
export PATH="$work/bin:$PATH"

# 用固定 Git 对象的完整系数和独立闭式阶跃生成期望，不复用 RTL 的内部状态。
python3 - <<'PY'
import json
from math import comb
from pathlib import Path

source = Path("rtl/ad4080_dsp/design/sinc5_comp_coefficients.json")
table = json.loads(source.read_text())
modes = sorted(table["modes"], key=lambda item: item["rate"])
if ([item["rate"] for item in modes] != list(range(8)) or table["fir_taps"] != 35 or
        table["frac_bits"] != 22 or any(len(item["coefficients_q"]) != 35 for item in modes)):
    raise ValueError("invalid PF1 full integer coefficient table")

def sinc5_step(step, n, group):
    # (1-z^-N)^5/(1-z^-1)^6 的系数和固定整除，不继承 DUT 的流水或梳状实现。
    if group <= 0:
        return 0
    count = group * n
    if count >= 5*(n-1)+1:
        return step
    mass = sum((-1)**k * comb(5,k) * comb(count-k*n+4,5)
               for k in range(6) if count-k*n+4 >= 5)
    return (step*mass+n**5//2)//(n**5)

def pf1_step(step, n, group, coeffs):
    # 直接对实际偶相应取的 35 个前级结果做完整整数点积。
    acc = sum(coef*sinc5_step(step,n,2*group-i) for i,coef in enumerate(coeffs))
    word = (acc+(1<<21))>>22
    return min(524287,max(-524288,word))

# 基本阶跃 −262144 先加 −64 再乘 65/64，末端得 −266305。
fields = ["35 22"]
for i in range(8):
    fields.append(" ".join(str(sinc5_step(-266305,2<<i,k)) for k in range(1,41)))
for i, mode in enumerate(modes):
    fields.append(" ".join(str(pf1_step(-266305,2<<i,k,mode["coefficients_q"])) for k in range(1,41)))
fields.append(" ".join(str(pf1_step(-1105,4,k,modes[1]["coefficients_q"])) for k in range(1,26)))
Path("filter_step_reference.txt").write_text("\n".join(fields) + "\n")
PY

# 系统安装通常直接调用 verilator。另支持发行包内置运行库，使用 ASCII 别名保留
# 临时 Make 路径。Clang 的显式 include 选项与本工程组件运行器保持一致。
simulator=${VERILATOR:-verilator}
if [[ -n ${VERILATOR_ROOT:-} && -x "$VERILATOR_ROOT/bin/verilator_bin" ]]; then
    ln -s "$VERILATOR_ROOT" "$work/vlt"
    export VERILATOR_ROOT="$work/vlt"
    simulator="$work/vlt/bin/verilator_bin"
fi
compiler=$(command -v clang++)

# =========================================================================
# 2. 两种绑定与原生对照
# =========================================================================
# 先运行专项并验证正向路径，最后跑固定版本已有的复位、状态、旁路 FIFO 对照。
# -Wno-fatal 允许继承的告警，报告中的 PASS 只针对自动断言。
# 原生测试含 fork/join。保持组件运行器的默认文件拆分与展开配置，避免
# Verilator 5.30 使用小 cfunc 拆分时在生成的协程 join 处触发运行库断言。
run_top() {
    local top=$1 case_name=$2 bench=$3
    shift 3
    local rtl=rtl/ad4080_dsp
    local -a layout=(--output-split 10000 --output-split-cfuncs 500 -j 2)
    local -a sources=("$rtl"/*.sv ad4080_record_guard.sv ad4080_fifo_record_framer.sv)
    if [[ $case_name == native ]]; then
        layout=(--unroll-count 8192 -j 1 -Werror-PINMISSING -Werror-SELRANGE)
        sources=("$rtl"/{ad4080_top,spi_slave,ad4080_regs,ad4080_dsp_chain,sys_error_corr,sinc1_decim,sinc5_decim,sinc5_comp,event_detect,fifo_capture,result_fifo,gpio_ctrl}.sv)
    fi
    "$simulator" --binary --timing --assert "${layout[@]}" --compiler clang \
        -MAKEFLAGS "CFG_CXXFLAGS_PCH_I=-include CXX=$compiler OPT_FAST=-O0 OPT_SLOW=-O0" \
        -CFLAGS '-std=c++20' -Wno-fatal -I"$rtl" \
        --top-module "$top" --Mdir "$case_name" \
        "$@" "${sources[@]}" "$bench" > "$case_name-build.log" 2>&1
    "./$case_name/V$top" 2>&1 | tee "$case_name-run.log"
}
run_top tb_dsp_path bound tb_dsp_path.sv "-GBOUND=1'b1"
run_top tb_dsp_path unbound tb_dsp_path.sv "-GBOUND=1'b0"
run_top ad4080_top_tb native rtl/ad4080_dsp/sim/tb/ad4080_top_tb.sv

# 同时核对独立组帧、保护和固定原生用例，避免仿真提前结束仍报告回归完成。
grep -Fq 'PASS result SPI SDOB: beats=80 wire=aa00075d0009e7fff30d' bound-run.log
grep -Fq 'immediate W=1 captures partial-window -60' bound-run.log
grep -Fq 'GUARD accepted history: SINC5 N=4 observed CNV=27 outputs=6 latest=-1105' bound-run.log
grep -Fq 'PASS guarded SINC5 W=1:' bound-run.log
grep -Fq 'PASS guarded mode change/bypass/PF1 profile and write/reset interlock' bound-run.log
grep -Fq 'AD4080_DSP_STEP_ORACLE_COMPLETE words=443' bound-run.log
grep -Fq 'AD4080_DSP_RATE_SCAN_COMPLETE legal=27 clamp=3 pause_switch=1' bound-run.log
grep -Fq 'PASS unbound numerical features:' unbound-run.log
grep -Fq 'AD4080_TOP_CONTRACT_COMPLETE' native-run.log
printf '%s\n' 'AD4080_DSP_REVIEW_COMPLETE'
