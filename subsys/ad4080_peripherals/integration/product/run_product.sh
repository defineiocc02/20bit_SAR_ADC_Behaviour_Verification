#!/usr/bin/env bash
set -euo pipefail

# 原仓只读；编译的前置条件是每次从固定 Git 对象建新临时绑定。
here=$(cd -- "$(dirname -- "$0")" && pwd)
project=$(cd -- "$here/../.." && pwd)
upstream=${DSP_UPSTREAM_REPO:?set DSP_UPSTREAM_REPO to the protected source repository}
gain_shift=14
pass_marker='AD4080_PRODUCT_BINDING PASS'
calibration=0
if [[ $# -eq 1 && $1 == --calibration ]]; then
    calibration=1
    gain_shift=15
    pass_marker='AD4080_PRODUCT_CALIBRATION PASS'
elif [[ $# -ne 0 ]]; then
    printf '%s\n' 'Usage: run_product.sh [--calibration]' >&2
    exit 2
fi
build=$(mktemp -d /tmp/ad4080-product.XXXXXX)
printf 'Build directory %s\n' "$build"
cd -- "$build"
mkdir -p integration/product
python3 "$here/bind_dsp.py" "$upstream" "$build/bound_dsp" | tee upstream.log

lists=(integration/files.f integration/readback_files.f integration/product/rtl.f)
for file in "${lists[@]}"; do cat "$project/$file"; done > all.f
tb=integration/product/tb_ad4080_peripheral_product.sv
while IFS= read -r file; do
    if [[ $file != bound_dsp/* ]]; then
        mkdir -p "$(dirname -- "$file")"
        ln -s "$project/$file" "$file"
    fi
done < all.f
for file in "$tb" "${lists[@]}" integration/lint.vlt integration/check_tool_run.py \
    integration/product/bind_dsp.py integration/product/run_product.sh integration/product/check_product_netlist.py; do
    ln -s "$project/$file" "$file"
done
{
    while IFS= read -r file; do shasum -a 256 "$file"; done < all.f
    shasum -a 256 "$tb" "${lists[@]}" integration/lint.vlt integration/check_tool_run.py \
        integration/product/{bind_dsp.py,run_product.sh,check_product_netlist.py}
} > sources.sha256

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
    --Mdir lint integration/lint.vlt -f all.f 2>&1 | tee lint.log
# 混合模块会触发预编译头分支。用合并编译规则兼容当前工具自带的 PCH 参数。
"$verilator_cmd" --binary --timing --assert --compiler clang -CFLAGS '-std=c++20' -j 2 \
    -MAKEFLAGS VM_PARALLEL_BUILDS=0 "-GCALIBRATION_RUN=$calibration" \
    --Mdir obj_dir --top-module tb_ad4080_peripheral_product -f all.f "$tb" \
    > compile.log 2>&1 || { cat compile.log; exit 1; }
if [[ $calibration -eq 1 ]]; then
    ./obj_dir/Vtb_ad4080_peripheral_product "${sim_args[@]}" 2>&1 | tee simulation.log
else
    ./obj_dir/Vtb_ad4080_peripheral_product 2>&1 | tee simulation.log
fi
python3 integration/check_tool_run.py simulation simulation.log "$pass_marker"

# 保持 4K×20 银行及 8×22 输送队列为抽象存储，验证和仿真相同的工程绑定。
if [[ -n ${YOSYS:-} ]]; then
    while IFS= read -r file; do
        cat "$file"
        printf '\n'
    done < all.f > synth_sources.sv
    cat > synth.ys <<YS
read_verilog -sv -D SYNTHESIS synth_sources.sv
chparam -set OTP_WORD_WIDTH 8 -set GAIN_BOUND 1 -set GAIN_K 1 -set GAIN_SHIFT $gain_shift -set SPI_MULTIBYTE_ORDER 0 -set OFFSET_LSB_ADDRESS 37 -set SPI_WRITE_SDO_DRIVE 0 ad4080_peripheral_product
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
shasum -a 256 -c sources.sha256 > source_check.log
