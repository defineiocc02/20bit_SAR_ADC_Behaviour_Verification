#!/usr/bin/env bash
# D3/D4 唯一验收入口：编译 -> 仿真 -> 逐位比对 -> 摘要
# 用法：  bash tools/verify_d3d4.sh
#   远端：D3D4_WORKDIR=/root/pg D3D4_RUNNER="ssh your-host" bash tools/verify_d3d4.sh
set -uo pipefail
cd "$(dirname "$0")/.."
WORKDIR="${D3D4_WORKDIR:-.}"
RUNNER="${D3D4_RUNNER:-}"
LOG="${D3D4_LOG:-/tmp/d3d4_mout.txt}"
SHA=$(git rev-parse --short HEAD 2>/dev/null || echo unknown)

run() { if [ -n "$RUNNER" ]; then $RUNNER "$@"; else bash -c "$*"; fi; }

echo "== D3/D4 验证  SHA=$SHA =="
[ -n "$RUNNER" ] && run "mkdir -p $WORKDIR/rtl $WORKDIR/tb"
if [ -n "$RUNNER" ]; then
  scp -q rtl/dem_mask_v3.sv rtl/dither_pre.sv "$RUNNER:$WORKDIR/rtl/" || { echo "FAIL: 拷贝 rtl"; exit 1; }
  scp -q tb/tb_dem_dither.sv "$RUNNER:$WORKDIR/tb/" || { echo "FAIL: 拷贝 tb"; exit 1; }
fi

run "cd $WORKDIR && vcs -full64 -sverilog -timescale=1ns/1ps -o simv_d3d4 \
    rtl/dem_mask_v3.sv rtl/dither_pre.sv tb/tb_dem_dither.sv > vcs.log 2>&1" \
  || { echo "FAIL: 编译失败"; exit 1; }

run "cd $WORKDIR && ./simv_d3d4 > d3d4_mout.txt 2>&1" || { echo "FAIL: 仿真失败"; exit 1; }

if [ -n "$RUNNER" ]; then
  scp -q "$RUNNER:$WORKDIR/d3d4_mout.txt" "$LOG" || { echo "FAIL: 取回日志"; exit 1; }
else
  cp "$WORKDIR/d3d4_mout.txt" "$LOG"
fi

LINES=$(grep -cE '^(M|P) ' "$LOG" || true)
echo "日志行数: ${LINES:-0}"
PYTHONPATH=src python3 tools/cmp_dem_dither.py "$LOG"
RC=$?
echo "== 结束 SHA=$SHA 契约=v2.1 覆盖=133376+512 rc=$RC =="
exit $RC
