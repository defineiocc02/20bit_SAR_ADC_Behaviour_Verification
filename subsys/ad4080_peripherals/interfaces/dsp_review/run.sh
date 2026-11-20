#!/usr/bin/env bash
# =============================================================================
# 文件名     : run.sh
# 模块名     : AD4080 DSP 定向回归
# 版本       : v1.0
# 功能       : 临时导出固定版本，用 Verilator 执行原生旁路测试及独立滤波通路台架。
# 设计说明   : 仅以 git archive 读取原仓，台架、二进制、日志都在独立 ASCII 临时目录
#              构建。三个仿真各有独立 Mdir，一个构建或断言失败立即退出并可审查日志。
# 变更记录   : 初始版本，暂无变更记录。
# =============================================================================
set -euo pipefail

# =========================================================================
# 1. 固定源与运行目录
# =========================================================================
repo=${1:?用法: run.sh 含冻结提交的工程目录}
commit=7408edf8db9a41eebdd756378b69d2775f107dec
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
work=$(mktemp -d /tmp/ad4080-dsp-review.XXXXXXXX)
printf 'build directory: %s\n' "$work"
git -C "$repo" archive "$commit" subsys/ad4080_dsp | tar -xf - -C "$work"
cp -- "$script_dir/tb_dsp_path.sv" "$work/tb_dsp_path.sv"
cp -- "$script_dir/ad4080_record_guard.sv" "$work/ad4080_record_guard.sv"
cp -- "$script_dir/../../io/ad4080_fifo_record_framer.sv" "$work/ad4080_fifo_record_framer.sv"
cd -- "$work"

# Make 可能直接调用 python。使用执行包装避免 macOS 的 python3 shim 改名后仍按 python 查找。
mkdir bin
printf '%s\n' '#!/usr/bin/env sh' 'exec python3 "$@"' > bin/python
chmod +x bin/python
export PATH="$work/bin:$PATH"

# 系统安装通常直接调用 verilator。另支持发行包内置运行库，使用 ASCII 别名保留
# 临时 Make 路径。在宿主旧构建包中，跳过拆分 PCH 以避开 clang/gcc 参数差异。
simulator=${VERILATOR:-verilator}
if [[ -n ${VERILATOR_ROOT:-} && -x "$VERILATOR_ROOT/bin/verilator_bin" ]]; then
    ln -s "$VERILATOR_ROOT" "$work/vlt"
    export VERILATOR_ROOT="$work/vlt"
    simulator="$work/vlt/bin/verilator_bin"
fi

# =========================================================================
# 2. 两种绑定与原生对照
# =========================================================================
# 先运行专项并验证正向路径，最后跑固定版本已有的复位、状态、旁路 FIFO 对照。
# -Wno-fatal 允许继承的告警，报告中的 PASS 只针对自动断言。
run_top() {
    local top=$1 case_name=$2 bench=$3
    shift 3
    "$simulator" --binary --timing --assert --output-split 0 --compiler clang \
        -MAKEFLAGS VM_PARALLEL_BUILDS=0 \
        -CFLAGS '-std=c++20 -O0' -j 2 -Wno-fatal --top-module "$top" --Mdir "$case_name" \
        "$@" subsys/ad4080_dsp/rtl/*.sv ad4080_record_guard.sv ad4080_fifo_record_framer.sv "$bench" \
        > "$case_name-build.log" 2>&1
    "./$case_name/V$top" 2>&1 | tee "$case_name-run.log"
}
run_top tb_dsp_path bound tb_dsp_path.sv "-GBOUND=1'b1"
run_top tb_dsp_path unbound tb_dsp_path.sv "-GBOUND=1'b0"
run_top ad4080_top_tb native subsys/ad4080_dsp/sim/tb/ad4080_top_tb.sv

# 同时核对独立组帧、保护和固定原生用例，避免仿真提前结束仍报告回归完成。
grep -Fq 'PASS result SPI SDOB: beats=80 wire=aa00075e0009e8fff30e' bound-run.log
grep -Fq 'PASS guarded mode change/bypass/unbound COMP and write/reset interlock' bound-run.log
grep -Fq 'AD4080_DSP_RATE_SCAN_COMPLETE legal=27 clamp=3 pause_switch=1' bound-run.log
grep -Fq 'PASS unbound numerical features:' unbound-run.log
grep -Fq 'AD4080_TOP_CONTRACT_COMPLETE' native-run.log
printf '%s\n' 'AD4080_DSP_REVIEW_COMPLETE'
