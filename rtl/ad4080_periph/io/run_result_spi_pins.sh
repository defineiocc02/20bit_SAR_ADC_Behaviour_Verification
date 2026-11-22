#!/bin/sh
# =============================================================================
# 文件名     : run_result_spi_pins.sh
# 模块名     : 结果 SPI DCLK 引脚引擎验证入口
# 版本       : v1.0
# 功能       : 分别构建 ADI 固件前缀和显式约定的外部 24 位工程尾。
# 设计说明   : 正向/负向断言均基于真实引脚 DCLK。保留独立的源散列和构建
#              目录，不依赖当前目录既有文件或将工具安装进源仓。
# -----------------------------------------------------------------------------
# 变更记录
# -----------------------------------------------------------------------------
# 初始版本，暂无变更记录。
# =============================================================================
set -eu

spi_source=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
spi_build=$(mktemp -d "${RESULT_SPI_BUILD_ROOT:-/tmp}/ad4080-result-spi.XXXXXX")
printf 'Result SPI build %s\n' "$spi_build"
cp "$spi_source/ad4080_fifo_record_framer.sv" "$spi_source/ad4080_result_spi_pins.sv" \
   "$spi_source/ad4080_result_spi_pins_tb.sv" "$spi_build/"
cd "$spi_build"
shasum -a 256 ./*.sv > sources.sha256

# 同一组 DCS/DCLK 断言在两个参数中分别运行。禁止直接用内部 valid
# 的条数验收尾，只有实际下降沿上的物理 SDO 和该组回执都通过才结束。
for spi_tail in 0 1; do
    "${VERILATOR:-verilator}" --binary --timing --compiler clang -CFLAGS '-std=c++20' \
        -Wall --output-split 0 -MAKEFLAGS VM_PARALLEL_BUILDS=0 --Mdir "obj-$spi_tail" \
        --top-module ad4080_result_spi_pins_tb "-GEXTERNAL_TAIL=1'b$spi_tail" \
        ad4080_fifo_record_framer.sv ad4080_result_spi_pins.sv ad4080_result_spi_pins_tb.sv \
        > "build-$spi_tail.log" 2>&1 || { cat "build-$spi_tail.log" >&2; exit 1; }
    "./obj-$spi_tail/Vad4080_result_spi_pins_tb" > "run-$spi_tail.log" 2>&1 || {
        cat "run-$spi_tail.log" >&2; exit 1;
    }
    cat "run-$spi_tail.log"
    rg -F "AD4080_RESULT_SPI_PIN_ENGINE PASS tail=$spi_tail" "run-$spi_tail.log"
done
