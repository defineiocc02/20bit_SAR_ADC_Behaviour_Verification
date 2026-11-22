#!/bin/sh
# =============================================================================
# 文件名     : run_verify.sh
# 模块名     : ADI AD4080 收端互操作门禁
# 版本       : v1.0
# 功能       : 核验原版 ADI 源码，隔离构建，逐模式及相位运行接收回归。
# 设计说明   : 官方文件和生成物都进入交付树外的独立 scratch。只读解析
#              Git 对象，保持原工程和工具发行包。冻结 hash 防止变更字序。
# -----------------------------------------------------------------------------
# 变更记录
# -----------------------------------------------------------------------------
# 初始版本，暂无变更记录。
# =============================================================================
set -eu

# =============================================================================
# 1. 隔离目录和官方源输入
# =============================================================================
rx_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
project_dir=$(CDPATH= cd -- "$rx_dir/../.." && pwd -P)
official_commit=f518dd124fc005c6f9cd80b7947b10a638c37b37
scratch_parent=$(CDPATH= cd -- "${ADI_RX_SCRATCH:-${TMPDIR:-/tmp}}" && pwd -P)
case "$scratch_parent/" in
    "$project_dir/"*) printf '%s\n' 'ADI_RX_SCRATCH must be outside the delivery tree' >&2; exit 2 ;;
esac

if [ -z "${ADI_HDL_CHECKOUT:-}${ADI_HDL_SOURCE_DIR:-}" ] && [ "${ADI_HDL_FETCH:-0}" != 1 ]; then
    printf '%s\n' 'Set ADI_HDL_CHECKOUT, ADI_HDL_SOURCE_DIR, or ADI_HDL_FETCH=1' >&2
    exit 2
fi

rx_stage=$(mktemp -d "$scratch_parent/ad4080-adi-rx.XXXXXX")
printf 'ADI receiver scratch %s\n' "$rx_stage" >&2

# 只取本次用到的三份 RTL 和对应许可证。checkout 模式由对象内容而非
# 工作树决定，SOURCE_DIR 模式适合已经冻结的原样导出和离线复跑。
while read -r locked_sum locked_path; do
    mkdir -p "$rx_stage/$(dirname -- "$locked_path")"
    if [ -n "${ADI_HDL_CHECKOUT:-}" ]; then
        git -C "$ADI_HDL_CHECKOUT" show "$official_commit:$locked_path" > "$rx_stage/$locked_path"
    elif [ -n "${ADI_HDL_SOURCE_DIR:-}" ]; then
        cp "$ADI_HDL_SOURCE_DIR/$locked_path" "$rx_stage/$locked_path"
    else
        curl --fail --silent --show-error --location --proto '=https' --retry 2 \
            --connect-timeout 8 --max-time 30 \
            "https://raw.githubusercontent.com/analogdevicesinc/hdl/$official_commit/$locked_path" \
            --output "$rx_stage/$locked_path"
    fi
    printf '%s  %s\n' "$locked_sum" "$locked_path" >> "$rx_stage/upstream.sha256"
done < "$rx_dir/adi_upstream.sha256"

cp "$rx_dir/ideal_7series_io.sv" "$rx_dir/adi_receiver_tb.sv" \
    "$project_dir/io/ad4080_ddr_edge_words.sv" "$project_dir/transport/ad4080_result_transport.sv" "$rx_stage/"
cd "$rx_stage"
shasum -a 256 -c upstream.sha256

# =============================================================================
# 2. 原版接收逻辑的联测
# =============================================================================
# Verilator 发行版的 Clang PCH 路径和本机组合不匹配，故固定单体 C++
# 构建。保留厂商和模拟原语的诊断日志，仿真断言及 PASS 行才构成验收。
for lane_mode in 0 1; do
    for hold_pairs in 8 9; do
        rx_label="lane-$((lane_mode + 1))-phase-$hold_pairs"
        obj_dir="$rx_stage/obj-$rx_label"
        if ! "${VERILATOR:-verilator}" --compiler clang -CFLAGS '-std=c++20' \
            --binary --timing -Wall -Wno-fatal --output-split 0 -MAKEFLAGS VM_PARALLEL_BUILDS=0 \
            --top-module adi_receiver_tb "-GDUAL=1'b$lane_mode" "-GHOLD_PAIRS=$hold_pairs" \
            --Mdir "$obj_dir" ideal_7series_io.sv library/common/ad_pack.v \
            library/xilinx/common/ad_serdes_in.v library/axi_ad408x/ad408x_phy.v \
            ad4080_ddr_edge_words.sv ad4080_result_transport.sv adi_receiver_tb.sv \
            > "$rx_label.build.log" 2>&1; then
            tail -n 50 "$rx_label.build.log" >&2
            exit 1
        fi
        if ! "$obj_dir/Vadi_receiver_tb" > "$rx_label.run.log" 2>&1; then
            cat "$rx_label.run.log" >&2
            exit 1
        fi
        rg -F "PASS ADI receiver lane=$((lane_mode + 1)) hold_pairs=$hold_pairs " "$rx_label.run.log"
    done
done
printf 'PASS ADI receiver interop: 4 runs, commit %s\n' "$official_commit"
