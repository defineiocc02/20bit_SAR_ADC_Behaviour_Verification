#!/usr/bin/env bash
#=============================================================================
# run_fv.sh -- 形式验证（BMC），绕过 SBY 的 Python 封装
#-----------------------------------------------------------------------------
# 为什么不用 `sby`：本机 WorkBuddy 的 mkdir 兼容层对已存在目录抛 EEXIST，
# SBY 的 sby_core.py 无法创建 model 目录。这里直接用 yosys + yosys-smtbmc。
#
# 用法：bash fv/run_fv.sh [spi|fifo|all]
# 结果：PASSED / FAILED；SPI 约 6 分钟（自由 SCLK 状态空间较大）。
#=============================================================================
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="$ROOT/sim/build/fv"
mkdir -p "$OUT"

export PATH="${HOME}/.workbuddy/tools/oss-cad-suite/bin:${PATH}"
command -v yosys-smtbmc >/dev/null 2>&1 || { echo "[SKIP] yosys-smtbmc not found"; exit 2; }

run_spi () {
  echo "== SPI: we -> (addr == addr_ptr) =="
  yosys -q -p "read_verilog -sv -DFORMAL $ROOT/rtl/spi_slave.sv; \
              prep -top spi_slave; async2sync; dffunmap; \
              write_smt2 -wires $OUT/spi.smt2" || return 3
  yosys-smtbmc -s boolector -t 60 "$OUT/spi.smt2" | tail -3
}

run_fifo () {
  echo "== FIFO: count <= DEPTH, full 不写, empty 不读 =="
  yosys -q -p "read_verilog -sv -DFORMAL $ROOT/rtl/result_fifo.sv $ROOT/fv/result_fifo_fv.sv; \
              prep -top result_fifo_fv; async2sync; memory_map; opt; dffunmap; \
              write_smt2 -wires $OUT/fifo.smt2" || return 3
  yosys-smtbmc -s boolector -t 40 "$OUT/fifo.smt2" | tail -3
}

case "${1:-all}" in
  spi)  run_spi ;;
  fifo) run_fifo ;;
  all)  run_fifo; run_spi ;;
  *)    echo "usage: $0 [spi|fifo|all]"; exit 2 ;;
esac
