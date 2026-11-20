#!/bin/sh

# =============================================================================
# 文件名     : verify.sh
# 模块名     : transport 本地回归入口
# 版本       : v1.0
# 功能       : 构建并运行独立的结果搬运定向仿真，可选执行通用综合检查。
# 设计说明   :
#   * 只复制本包源和既有边沿映射到新建 ASCII 目录。WASI Yosys 需要可
#     直接读取的文件，含中文原路径或越过预打开目录的链接会阻断读取。
#   * 先报临时目录，失败时可以定位各阶段的原始输出，便于保留复跑证据。
# -----------------------------------------------------------------------------
# 变更记录
# -----------------------------------------------------------------------------
# 初始版本，暂无变更记录。
# =============================================================================
set -eu

# =========================================================================
# 1. 离线依赖与独立构建目录
# =========================================================================
transport_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
transport_build=$(mktemp -d /tmp/ad4080-transport-verify-XXXXXXXX)
printf 'transport_build=%s\n' "$transport_build"
cp "$transport_dir/ad4080_result_transport.sv" "$transport_build/"
cp "$transport_dir/tb_ad4080_result_transport.sv" "$transport_build/"
cp "$transport_dir/../io/ad4080_ddr_edge_words.sv" "$transport_build/"
cd "$transport_build"
transport_verilator=${TRANSPORT_VERILATOR:-verilator}

# =========================================================================
# 2. 警告敏感的 lint、定向仿真和可选综合
# =========================================================================
# [证据] lint 与编译共用顶层和源位置，保证综合控制器和独立接收参考一起检查。
shasum -a 256 ad4080_result_transport.sv tb_ad4080_result_transport.sv ad4080_ddr_edge_words.sv \
    > sources.sha256
"$transport_verilator" --lint-only --timing -Wall --top-module tb_ad4080_result_transport \
    --Mdir "$transport_build/lint" ad4080_result_transport.sv ad4080_ddr_edge_words.sv \
    tb_ad4080_result_transport.sv > lint.log 2>&1
# [工具] 单一 C++ 编译单元绕开部分 Verilator 安装的预编译头规则缺项。
"$transport_verilator" --binary --timing --compiler clang -CFLAGS '-std=c++20' -Wall --output-split 0 \
    --top-module tb_ad4080_result_transport --Mdir "$transport_build/obj" \
    ad4080_result_transport.sv ad4080_ddr_edge_words.sv tb_ad4080_result_transport.sv > build.log 2>&1
"$transport_build/obj/Vtb_ad4080_result_transport" > regression.log 2>&1
cat sources.sha256 regression.log

# [综合] 关闭 ABC，保留通用逻辑和层级计数。验收需要实际产出 JSON，
# 同时避免工具提前退出时仅凭返回码把尚未生成的网表报为成功。
if [ -n "${TRANSPORT_YOSYS:-}" ]; then
    "$TRANSPORT_YOSYS" -Q -p 'read_verilog -sv ad4080_result_transport.sv ad4080_ddr_edge_words.sv; hierarchy -check -top ad4080_result_transport; synth -noabc -top ad4080_result_transport; check -assert; stat; write_json generic.json' \
        > synth.log 2>&1
    test -s generic.json
    printf 'Yosys generic synthesis and check PASS\n'
fi
