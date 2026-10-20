#!/usr/bin/env bash
#=============================================================================
# run_top.sh -- 编译并运行 ad4080_top 自检测试平台
#-----------------------------------------------------------------------------
# 用法:  bash run_top.sh          （在任意目录均可）
#        bash run_top.sh --clean  清理 build 目录后退出
#
# 退出码:
#   0  = 全部 PASS
#   1  = 仿真跑通但有 [FAIL]
#   2  = 本机没有任何可用的 Verilog 仿真器（不假装成功）
#   3  = 编译失败（RTL/TB 端口或语法不一致）
#   4  = 仿真器崩溃 / 无 SUMMARY 输出
#=============================================================================
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RTL_DIR="$(cd "$SCRIPT_DIR/../rtl" && pwd)"
TB_DIR="$SCRIPT_DIR/tb"
TB_FILE="$TB_DIR/ad4080_top_tb.sv"
BUILD_DIR="$SCRIPT_DIR/build"
LOG_FILE="$BUILD_DIR/sim_top.log"

if [ "${1:-}" = "--clean" ]; then
  rm -rf "$BUILD_DIR"
  echo "cleaned: $BUILD_DIR"
  exit 0
fi

if [ ! -f "$TB_FILE" ]; then
  echo "ERROR: testbench not found: $TB_FILE" >&2
  exit 3
fi

RTL_SRCS=$(ls "$RTL_DIR"/*.sv 2>/dev/null)
if [ -z "$RTL_SRCS" ]; then
  echo "ERROR: no RTL sources under $RTL_DIR" >&2
  exit 3
fi

mkdir -p "$BUILD_DIR"

echo "=============================================================="
echo " AD4080 top TB"
echo "   RTL : $RTL_DIR"
echo "   TB  : $TB_FILE"
echo "   OUT : $BUILD_DIR"
echo "=============================================================="

#-----------------------------------------------------------------------------
# 1) Icarus Verilog（首选，SV 子集 + 时序支持最省事）
#-----------------------------------------------------------------------------
if command -v iverilog >/dev/null 2>&1; then
  SIM="iverilog"
  echo "simulator: iverilog ($(iverilog -V 2>/dev/null | head -1))"

  # shellcheck disable=SC2086
  iverilog -g2012 -Wall \
      -o "$BUILD_DIR/ad4080_top.vvp" \
      -s ad4080_top_tb \
      $RTL_SRCS "$TB_FILE" > "$BUILD_DIR/compile.log" 2>&1
  IV_RC=$?
  cat "$BUILD_DIR/compile.log"

  if [ "$IV_RC" -ne 0 ] || [ ! -f "$BUILD_DIR/ad4080_top.vvp" ]; then
    echo ""
    echo "COMPILE FAILED -- 见 $BUILD_DIR/compile.log"
    echo "常见原因: rtl/ad4080_top.sv 的端口与冻结清单不一致（本 TB 按冻结清单写）。"
    echo "请对照 sim/TB_NOTES.md 的「待修 RTL 问题」清单。"
    exit 3
  fi

  vvp -n "$BUILD_DIR/ad4080_top.vvp" 2>&1 | tee "$LOG_FILE"
  SIM_RC=${PIPESTATUS[0]}

#-----------------------------------------------------------------------------
# 2) Verilator（--binary --timing 可直接跑带延时的 TB）
#-----------------------------------------------------------------------------
elif command -v verilator >/dev/null 2>&1; then
  SIM="verilator"
  echo "simulator: verilator ($(verilator --version 2>/dev/null))"

  # shellcheck disable=SC2086
  verilator --binary --timing -j 0 \
      -Wno-fatal -Wno-WIDTH -Wno-UNOPTFLAT -Wno-CASEINCOMPLETE \
      --top-module ad4080_top_tb \
      -Mdir "$BUILD_DIR/obj" \
      $RTL_SRCS "$TB_FILE" 2>&1 | tee "$BUILD_DIR/compile.log"

  BIN="$BUILD_DIR/obj/Vad4080_top_tb"
  if [ ! -x "$BIN" ]; then
    echo ""
    echo "COMPILE FAILED -- 见 $BUILD_DIR/compile.log"
    echo "请对照 sim/TB_NOTES.md 的「待修 RTL 问题」清单。"
    exit 3
  fi

  "$BIN" 2>&1 | tee "$LOG_FILE"
  SIM_RC=${PIPESTATUS[0]}

#-----------------------------------------------------------------------------
# 3) 没有仿真器：明确失败，不伪造结果
#-----------------------------------------------------------------------------
else
  echo ""
  echo "=============================================================="
  echo " 没有找到任何可用的 Verilog 仿真器。"
  echo ""
  echo " 已查找: iverilog / verilator / vvp / vcs / xrun / vsim / questa"
  echo ""
  echo " 本脚本不会伪造仿真结果。可选用以下任一方式安装后重跑："
  echo "   macOS : brew install icarus-verilog"
  echo "   macOS : brew install verilator"
  echo "   Ubuntu: sudo apt install iverilog"
  echo ""
  echo " 当前只做了**静态自检**，结论见 sim/TB_NOTES.md"
  echo " （其中明确标注：未运行仿真）"
  echo "=============================================================="
  exit 2
fi

echo ""
if [ "$SIM_RC" -ne 0 ]; then
  echo "RESULT: simulation FAILED (exit=$SIM_RC)"
  echo "----------------------------------------"
  grep -E '^\[FAIL\]|^==== SUMMARY' "$LOG_FILE" | tail -40
  exit 1
fi

if ! grep -q '^==== SUMMARY' "$LOG_FILE"; then
  echo "RESULT: no SUMMARY in log -- simulator may have crashed"
  exit 4
fi

if grep -q '^\[FAIL\]' "$LOG_FILE"; then
  echo "RESULT: FAIL"
  grep -E '^\[FAIL\]|^==== SUMMARY' "$LOG_FILE"
  exit 1
fi

echo "RESULT: PASS"
grep -E '^==== SUMMARY|^ALL TESTS PASSED' "$LOG_FILE"
exit 0
