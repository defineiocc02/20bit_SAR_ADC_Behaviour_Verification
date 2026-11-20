#!/usr/bin/env bash
# =============================================================================
# 文件名     : run_product.sh
# 模块名     : 产品级数字联调入口
# 版本       : v1.0
# 功能       : 在新目录绑定固定 DSP，运行有终态判据的仿真和结构检查。
# 设计说明   : 测试档位复用唯一 DUT。只有校准更换工程增益移位和主机输入。
#              每次构建同时保留配置参数、读回序列、源摘要和工具回执。
# -----------------------------------------------------------------------------
# 变更记录   : 真实配置/SPI数据口、滤波边沿和完整记录各有独立运行档。
# =============================================================================
set -euo pipefail

# 原仓只读；编译的前置条件是每次从固定 Git 对象建新临时绑定。
here=$(cd -- "$(dirname -- "$0")" && pwd)
project=$(cd -- "$here/../.." && pwd)
upstream=${DSP_UPSTREAM_REPO:?set DSP_UPSTREAM_REPO to the protected source repository}
gain_shift=14
pass_marker='AD4080_PRODUCT_BINDING PASS'
calibration=0
all_pins=0
run_group=''
if [[ $# -eq 1 && $1 == --calibration ]]; then
    calibration=1
    gain_shift=15
    pass_marker='AD4080_PRODUCT_CALIBRATION PASS'
elif [[ $# -eq 1 && $1 == --pin-edges ]]; then
    pass_marker='AD4080_PRODUCT_PIN_EDGES PASS'
elif [[ $# -eq 1 && $1 == --all-pins ]]; then
    all_pins=1
    pass_marker='AD4080_PRODUCT_PIN_EDGES PASS'
elif [[ $# -eq 1 && $1 == --result-spi ]]; then
    run_group=result_spi
    pass_marker='AD4080_PRODUCT_RESULT_SPI PASS'
elif [[ $# -eq 1 && $1 == --control-pins ]]; then
    run_group=control
    pass_marker='AD4080_PRODUCT_CONTROL_PINS PASS'
elif [[ $# -eq 1 && $1 == --full-record ]]; then
    run_group=full_record
    pass_marker='AD4080_PRODUCT_FULL_RECORD PASS'
elif [[ $# -eq 1 && $1 == --filters ]]; then
    run_group=filters
    pass_marker='AD4080_PRODUCT_FILTERS PASS'
elif [[ $# -ne 0 ]]; then
    printf '%s\n' 'Usage: run_product.sh [--all-pins|--calibration|--pin-edges|--result-spi|--control-pins|--full-record|--filters]' >&2
    exit 2
fi
# ============================================================================
# 1. 生成只读输入清单
# ============================================================================
# 每轮使用新目录，避免旧二进制或网表误绿。外部 runner 可以指定持久工作
# 目录，让中断后的实际日志和构建输入仍可用于核对。
work_parent=${AD4080_PRODUCT_WORK_PARENT:-/tmp}
mkdir -p -- "$work_parent"
work_parent=$(cd -- "$work_parent" && pwd)
build=$(mktemp -d "$work_parent/ad4080-product.XXXXXX")
printf 'Build directory %s\n' "$build"
cd -- "$build"
mkdir -p integration/product
python3 "$here/bind_dsp.py" "$upstream" "$build/bound_dsp" | tee upstream.log

lists=(integration/files.f integration/readback_files.f integration/product/rtl.f)
for file in "${lists[@]}"; do cat "$project/$file"; done > all.f
tb=integration/product/tb_ad4080_peripheral_product.sv
pin_tasks=(integration/product/{continuous_pin_tasks,result_spi_tasks,control_pin_tasks,full_record_tasks}.svh)
while IFS= read -r file; do
    if [[ $file != bound_dsp/* ]]; then
        mkdir -p "$(dirname -- "$file")"
        ln -s "$project/$file" "$file"
    fi
done < all.f
for file in "$tb" "${pin_tasks[@]}" "${lists[@]}" integration/lint.vlt integration/check_tool_run.py \
    integration/product/bind_dsp.py integration/product/run_product.sh integration/product/check_product_netlist.py; do
    ln -s "$project/$file" "$file"
done
{
    while IFS= read -r file; do shasum -a 256 "$file"; done < all.f
    shasum -a 256 bound_dsp/sinc5_comp_coeff_lut.svh
    shasum -a 256 "$tb" "${pin_tasks[@]}" "${lists[@]}" integration/lint.vlt integration/check_tool_run.py \
        integration/product/{bind_dsp.py,run_product.sh,check_product_netlist.py}
} > sources.sha256

# ============================================================================
# 2. 联调激励和实际工具完成标记
# ============================================================================
# 校准仅更换已知工程移位和输入方案，复用同一个生产顶层与物理银行。
sim_args=()
if [[ $calibration -eq 1 ]]; then
    mkdir -p calibration
    for host_source in system_cal.py verify_rtl.py requirements.txt; do
        ln -s "$project/calibration/$host_source" "calibration/$host_source"
    done
    ln -s "$here/prepare_calibration.py" integration/product/prepare_calibration.py
    mpl_dir="$build/calibration-mpl"
    mkdir -p "$mpl_dir"
    MPLBACKEND=Agg MPLCONFIGDIR="$mpl_dir" "${CAL_PYTHON:-python3}" -B \
        integration/product/prepare_calibration.py calibration "$build" | tee calibration_cli.log
    python3 integration/check_tool_run.py simulation calibration_cli.log 'PRODUCT_ADCTOOLBOX_PLAN PASS'
    read -r offset_word gain_word < calibration_words.txt
    sim_args=("+offset_word=$offset_word" "+gain_word=$gain_word")
    shasum -a 256 integration/product/prepare_calibration.py calibration/{system_cal.py,verify_rtl.py,requirements.txt} \
        training_sine.csv calibration_plan.json calibration_words.txt >> sources.sha256
fi

verilator_cmd=${VERILATOR:-verilator}
"$verilator_cmd" --lint-only --top-module ad4080_peripheral_product \
    --Mdir lint -Ibound_dsp integration/lint.vlt -f all.f 2>&1 | tee lint.log
# 单独编译拆分后的 C++，避免六组时序任务在低内存机器上产生一个极大
# 翻译单元。明确指定 Clang 的 PCH 扩展和传参，兼容本机打包工具。
# 保留 8 位 SPI 激励的循环与逐位断言，减少重复的协程状态；四路 GPIO
# 的组合循环仍可展开。关闭 C++ 优化以控制时序模型的构建时长。
"$verilator_cmd" --binary --timing --assert --compiler clang -CFLAGS '-std=c++20' -j 2 \
    --unroll-count 4 --output-split 12000 \
    -MAKEFLAGS 'VM_PARALLEL_BUILDS=1 OPT_FAST=-O0 OPT_SLOW=-O0 CXX=clang++ CFG_CXXFLAGS_PCH_I=-include-pch CFG_GCH_IF_CLANG=.gch' \
    "-GCALIBRATION_RUN=$calibration" \
    --Mdir obj_dir --top-module tb_ad4080_peripheral_product -Ibound_dsp -Iintegration/product -f all.f "$tb" \
    > compile.log 2>&1 || { cat compile.log; exit 1; }
if [[ $calibration -eq 1 ]]; then
    ./obj_dir/Vtb_ad4080_peripheral_product "${sim_args[@]}" 2>&1 | tee simulation.log
elif [[ -n $run_group ]]; then
    ./obj_dir/Vtb_ad4080_peripheral_product "+product_group=$run_group" 2>&1 | tee simulation.log
else
    ./obj_dir/Vtb_ad4080_peripheral_product 2>&1 | tee simulation.log
fi
python3 integration/check_tool_run.py simulation simulation.log "$pass_marker"
# 成功标记之后才提取本轮实际采纳的边沿。其它定向档不应拼入图示收据。
if [[ $calibration -eq 0 && -z $run_group ]]; then
    rg '^PIN_EDGE,' simulation.log > pin_edges.csv
fi
if [[ $run_group == filters ]]; then
    rg '^FILTER_EDGE,' simulation.log > filter_edges.csv
fi
if [[ $all_pins -eq 1 ]]; then
    # 各自冷启动同一二进制，避免前一组的寄存器或记录元数据泄漏进下一组。
    # 所有组走自己的实际终止行检查，随后才进行一次共同结构验证。
    groups=(filters result_spi control full_record)
    markers=('AD4080_PRODUCT_FILTERS PASS' 'AD4080_PRODUCT_RESULT_SPI PASS' \
        'AD4080_PRODUCT_CONTROL_PINS PASS' 'AD4080_PRODUCT_FULL_RECORD PASS')
    for i in "${!groups[@]}"; do
        group=${groups[$i]}
        group_log="simulation_${group}.log"
        ./obj_dir/Vtb_ad4080_peripheral_product "+product_group=$group" 2>&1 | tee "$group_log"
        python3 integration/check_tool_run.py simulation "$group_log" "${markers[$i]}"
    done
    rg '^FILTER_EDGE,' simulation_filters.log > filter_edges.csv
fi

# ============================================================================
# 3. 结构与输入完整性
# ============================================================================
# 保持 4K×20 银行及 8×22 输送队列为抽象存储。核对 CDC 两槽
# 实际触发器，同时验证与仿真采用同一工程时基和配置参数。
if [[ -n ${YOSYS:-} ]]; then
    while IFS= read -r file; do
        cat "$file"
        printf '\n'
    done < all.f > synth_sources.sv
    cat > synth.ys <<YS
read_verilog -sv -Ibound_dsp -D SYNTHESIS synth_sources.sv
chparam -set OTP_WORD_WIDTH 8 -set GAIN_BOUND 1 -set GAIN_K 1 -set GAIN_SHIFT $gain_shift -set SPI_MULTIBYTE_ORDER 0 -set OFFSET_LSB_ADDRESS 37 -set SPI_WRITE_SDO_DRIVE 1 ad4080_peripheral_product
chparam -set CORE_CLOCK_HZ 100000000 -set P_CNV_TIMING_BOUND 1 ad4080_peripheral_product
hierarchy -check -top ad4080_peripheral_product
proc
opt
memory -nomap
check -assert
write_json synthesized.json
YS
    "$YOSYS" --version > yosys_version.log
    "$YOSYS" -l synth.log -Q -T -s synth.ys > synth_console.log 2>&1 \
        || { cat synth_console.log; exit 1; }
    python3 integration/check_tool_run.py yosys synthesized.json synth.log ad4080_peripheral_product
    python3 integration/product/check_product_netlist.py synthesized.json "$gain_shift" | tee structure.log
fi
# 共享源改动可能恰好发生在编译运行之间，退出前再核对本轮输入。
shasum -a 256 -c sources.sha256 > source_check.log
if [[ $all_pins -eq 1 ]]; then
    printf '%s\n' 'AD4080_PRODUCT_ALL_PINS PASS'
fi
