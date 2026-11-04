#!/usr/bin/env bash
#=============================================================================
# run_all.sh -- 编译并运行全部自检 TB（需要 Icarus Verilog）
#-----------------------------------------------------------------------------
# 用法:
#   bash sim/run_all.sh
#   SIM=/path/to/iverilog bash sim/run_all.sh   # 指定仿真器
#
# 自动探测（按顺序）：
#   1. PATH 里的 iverilog
#   2. $HOME/.workbuddy/tools/oss-cad-suite/bin/iverilog
#   3. /opt/oss-cad-suite/bin/iverilog
#
# 退出码：0 全过；1 有 FAIL；2 没找到仿真器；3 编译失败
#=============================================================================
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD="$ROOT/sim/build"
mkdir -p "$BUILD"

if [ -n "${SIM:-}" ]; then
  IVERILOG="$SIM"
elif command -v iverilog >/dev/null 2>&1; then
  IVERILOG="$(command -v iverilog)"
else
  IVERILOG=""
  for p in "$HOME/.workbuddy/tools/oss-cad-suite/bin" "/opt/oss-cad-suite/bin"; do
    if [ -x "$p/iverilog" ]; then IVERILOG="$p/iverilog"; export PATH="$p:$PATH"; break; fi
  done
fi

if [ -z "$IVERILOG" ]; then
  echo "[SKIP] 未找到 iverilog。安装 OSS CAD Suite 或设置 SIM=/path/to/iverilog"
  exit 2
fi
echo "iverilog: $IVERILOG"
"$IVERILOG" -V 2>&1 | head -1

fail=0
run_tb () {
  local name="$1"; local tb="$2"; shift 2
  local vvp="$BUILD/${name}.vvp"
  echo
  echo "==================== $name ===================="
  if ! "$IVERILOG" -g2012 -o "$vvp" "$tb" "$@" 2>"$BUILD/${name}.compile.log"; then
    echo "[COMPILE FAIL] 见 $BUILD/${name}.compile.log"
    sed -n '1,12p' "$BUILD/${name}.compile.log"
    fail=1
    return
  fi
  local out
  out="$(vvp "$vvp" 2>&1)"
  echo "$out" | tail -6
  if echo "$out" | grep -qE "ALL TESTS PASSED|TB PASS|RESULT_FIFO_TB PASS"; then
    echo "[PASS] $name"
  else
    echo "[FAIL] $name"
    fail=1
  fi
}

run_tb cal     "$ROOT/sim/tb/ad4080_cal_tb.sv"   "$ROOT/rtl/sys_error_corr.sv" "$ROOT/rtl/cal_two_point.sv"
run_tb sinc5   "$ROOT/sim/tb/sinc5_decim_tb.sv"  "$ROOT/rtl/sinc5_decim.sv"
run_tb fifo    "$ROOT/sim/tb/result_fifo_tb.sv"  "$ROOT/rtl/result_fifo.sv"
run_tb spi     "$ROOT/sim/tb/spi_slave_tb.sv"    "$ROOT/rtl/spi_slave.sv" "$ROOT/rtl/ad4080_regs.sv"
run_tb top     "$ROOT/sim/tb/ad4080_top_tb.sv"   "$ROOT"/rtl/*.sv

echo
if [ "$fail" -eq 0 ]; then
  echo "==== ALL TBS PASSED ===="
  exit 0
else
  echo "==== SOME TBS FAILED ===="
  exit 1
fi
