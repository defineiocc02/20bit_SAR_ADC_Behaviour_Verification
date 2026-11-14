#!/usr/bin/env bash
# D3/D4 唯一验收入口：编译 -> 仿真 -> 逐位比对 -> 摘要
#
# 用法：
#   本地（需有 iverilog 或 vcs）：
#     bash tools/verify_d3d4.sh
#   Mac（无仿真器）走远端：
#     D3D4_WORKDIR=/root/pg D3D4_RUNNER="ssh <host>" bash tools/verify_d3d4.sh
#
# 环境变量：
#   D3D4_SIM     vcs | iverilog        （默认 vcs）
#   D3D4_RUNNER  远端执行前缀，如 "ssh vm-meow"
#   D3D4_WORKDIR 远端工作目录
#   D3D4_LOG     本地日志路径
#
# 注意：iverilog 分支**未在本机验证**（本机无任何仿真器），
#       只用过 vcs 分支。
set -uo pipefail
cd "$(dirname "$0")/.."
SIM="${D3D4_SIM:-vcs}"
WORKDIR="${D3D4_WORKDIR:-.}"
RUNNER="${D3D4_RUNNER:-}"
LOG="${D3D4_LOG:-/tmp/d3d4_mout.txt}"
SHA=$(git rev-parse --short HEAD 2>/dev/null || echo unknown)

run() { if [ -n "$RUNNER" ]; then $RUNNER "$*"; else bash -c "$*"; fi; }

echo "== D3/D4 验证  SHA=$SHA  SIM=$SIM  清单=files_d3d4.f =="

if [ -n "$RUNNER" ]; then
  run "mkdir -p $WORKDIR" || { echo "FAIL: 无法创建 $WORKDIR"; exit 1; }
  scp -q rtl/dem_mask_v3.sv rtl/dither_pre.sv tb/tb_dem_dither.sv files_d3d4.f \
      "$RUNNER:$WORKDIR/" || { echo "FAIL: 拷贝源文件"; exit 1; }
fi

case "$SIM" in
  vcs)
    run "cd $WORKDIR && vcs -full64 -sverilog -timescale=1ns/1ps -f files_d3d4.f \
         -o simv_d3d4 > vcs.log 2>&1" || { echo "FAIL: 编译失败（见 vcs.log）"; exit 1; }
    run "cd $WORKDIR && ./simv_d3d4 > d3d4_mout.txt 2>&1" \
         || { echo "FAIL: 仿真失败"; exit 1; } ;;
  iverilog)
    run "cd $WORKDIR && iverilog -g2012 -o simv_d3d4 -f files_d3d4.f > iv.log 2>&1" \
         || { echo "FAIL: 编译失败（见 iv.log）"; exit 1; }
    run "cd $WORKDIR && vvp simv_d3d4 > d3d4_mout.txt 2>&1" \
         || { echo "FAIL: 仿真失败"; exit 1; } ;;
  *) echo "FAIL: 未知 D3D4_SIM=$SIM"; exit 1 ;;
esac

if [ -n "$RUNNER" ]; then
  scp -q "$RUNNER:$WORKDIR/d3d4_mout.txt" "$LOG" || { echo "FAIL: 取回日志"; exit 1; }
else
  cp "$WORKDIR/d3d4_mout.txt" "$LOG"
fi

echo "日志行数: $(grep -cE '^(M|P) ' "$LOG" || echo 0)"
PYTHONPATH=src python3 tools/cmp_dem_dither.py "$LOG"
RC=$?
echo "== 结束 SHA=$SHA SIM=$SIM 契约=v2.1 覆盖=133376+512 rc=$RC =="
exit $RC
