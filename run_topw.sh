#!/usr/bin/env bash
# 当前数字顶层的验证入口：编译 + 跑记分板 TB（tb/tb_digital_top_w.sv）
# 依赖 tb/vec_topw_coef.txt 与 tb/vec_topw_gold.txt（已在库）
set -e
ROOT=$(cd "$(dirname "$0")" && pwd)
WORK=${WORK:-/tmp/topw_run}
rm -rf "$WORK"; mkdir -p "$WORK"
cp "$ROOT"/tb/vec_topw_coef.txt "$ROOT"/tb/vec_topw_gold.txt "$WORK"/

if [ -n "${VCS_HOME:-}" ] || command -v vcs >/dev/null 2>&1; then
    VCS=${VCS_HOME:+$VCS_HOME/bin/}vcs
    cd "$ROOT"
    "$VCS" -full64 -sverilog -timescale=1ns/1ps -f files_topw.f \
           -o "$WORK/simv" -Mdir="$WORK/csrc" > "$WORK/compile.log" 2>&1
    cd "$WORK" && ./simv | tee run.log | tail -12
elif command -v iverilog >/dev/null 2>&1; then
    cd "$ROOT"
    iverilog -g2012 -o "$WORK/simv" -f files_topw.f
    cd "$WORK" && ./simv | tail -12
else
    echo "需要 VCS 或 iverilog（见 README §怎么跑）" >&2; exit 127
fi
