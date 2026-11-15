#!/usr/bin/env bash
# D3/D4 唯一验收入口：编译 -> 仿真 -> 逐位比对 -> 摘要
#
# 本地：  bash tools/verify_d3d4.sh
# 远端：  D3D4_WORKDIR=/root/pg D3D4_RUNNER="ssh vm-meow" bash tools/verify_d3d4.sh
#
# 环境变量：
#   D3D4_SIM     vcs | iverilog（默认 vcs）
#   D3D4_RUNNER  远端前缀，如 "ssh vm-meow"
#   D3D4_WORKDIR 远端工作目录
#   D3D4_SETUP   覆盖默认的 EDA 环境（VCS_HOME + license）
#   D3D4_LOG     本地日志路径
#
# 远端非交互 shell 不带 EDA 环境，必须显式 export VCS_HOME 与
# SNPSLMD_LICENSE_FILE，否则报 "Cannot find license file"。
set -uo pipefail
cd "$(dirname "$0")/.."
SIM="${D3D4_SIM:-vcs}"
WORKDIR="${D3D4_WORKDIR:-.}"
RUNNER="${D3D4_RUNNER:-}"
HOST="${RUNNER#ssh }"                 # scp 只认主机名
LOG="${D3D4_LOG:-/tmp/d3d4_mout.txt}"
SHA=$(git rev-parse --short HEAD 2>/dev/null || echo unknown)
SETUP="${D3D4_SETUP:-export VCS_HOME=/opt/synopsys/vcs/W-2024.09-SP1; export SNPSLMD_LICENSE_FILE=27080@localhost; export PATH=\$VCS_HOME/bin:\$PATH}"

run() { if [ -n "$RUNNER" ]; then $RUNNER "$1"; else bash -c "$1"; fi; }

echo "== D3/D4 验证  SHA=$SHA  SIM=$SIM =="

if [ -n "$RUNNER" ]; then
  run "mkdir -p $WORKDIR/rtl $WORKDIR/tb" || { echo "FAIL: 建目录"; exit 1; }
  scp -q rtl/dem_mask_v3.sv rtl/dither_pre.sv "$HOST:$WORKDIR/rtl/" \
      || { echo "FAIL: 拷贝 rtl"; exit 1; }
  scp -q tb/tb_dem_dither.sv "$HOST:$WORKDIR/tb/" || { echo "FAIL: 拷贝 tb"; exit 1; }
  scp -q flow/build/filelists/files_d3d4.f "$HOST:$WORKDIR/" || { echo "FAIL: 拷贝清单"; exit 1; }
fi

case "$SIM" in
  vcs)
    # 远端：清单已拷到 $WORKDIR 根，所以用文件名而不是本地那个带目录的路径。
    # 本地：清单在 flow/build/filelists/ 下，用完整相对路径。
    if [ -n "$RUNNER" ]; then FL="files_d3d4.f"; else FL="flow/build/filelists/files_d3d4.f"; fi
    run "$SETUP; cd $WORKDIR && vcs -full64 -sverilog -timescale=1ns/1ps \
         -f $FL -o simv_d3d4 > vcs.log 2>&1" \
      || { echo "FAIL: 编译失败"; exit 1; }
    run "$SETUP; cd $WORKDIR && ./simv_d3d4 > d3d4_mout.txt 2>&1" \
      || { echo "FAIL: 仿真失败"; exit 1; } ;;
  iverilog)
    if [ -n "$RUNNER" ]; then FL="files_d3d4.f"; else FL="flow/build/filelists/files_d3d4.f"; fi
    run "$SETUP; cd $WORKDIR && iverilog -g2012 -o simv_d3d4 -f $FL > iv.log 2>&1" \
      || { echo "FAIL: 编译失败"; exit 1; }
    run "cd $WORKDIR && vvp simv_d3d4 > d3d4_mout.txt 2>&1" \
      || { echo "FAIL: 仿真失败"; exit 1; } ;;
  *) echo "FAIL: 未知 D3D4_SIM=$SIM"; exit 1 ;;
esac

if [ -n "$RUNNER" ]; then
  scp -q "$HOST:$WORKDIR/d3d4_mout.txt" "$LOG" || { echo "FAIL: 取回日志"; exit 1; }
else
  cp "$WORKDIR/d3d4_mout.txt" "$LOG"
fi

echo "日志行数: $(grep -cE '^(M|P) ' "$LOG" || echo 0)"
# 比对只依赖 dem_dither.py 的纯函数（脚本内按路径直载，不经过 adi_model/__init__），
# 因此不要求 scipy，也不强制 Python>=3.10。这里只做最低可用性守卫：
# 用 set -u 下 python3 缺失或被 alias 成空串时，避免把 "command not found"
# 当成比对失败（rc=127 会被误读为失配）。
if ! command -v python3 >/dev/null 2>&1; then
  echo "FAIL: 找不到 python3（比对需要 Python3.8+ 标准库）"
  exit 1
fi
if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)'; then
  echo "FAIL: python3 版本过低（需要 >=3.8）"
  exit 1
fi
python3 tools/cmp_dem_dither.py "$LOG"
RC=$?
echo "== 结束 SHA=$SHA SIM=$SIM rc=$RC =="
exit $RC
