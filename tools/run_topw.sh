#!/usr/bin/env bash
# 当前数字顶层的验证入口：编译 + 跑记分板 TB（tb/tb_digital_top_w.sv）
# 依赖 tb/ 下三个向量：vec_topw_coef.txt、vec_topw_gold.txt、vec_phase_v3.txt
# 第三个是 DEM 相位表（1920 行）。早期版本漏拷它，
# TB 会在 time 0 报 "cannot open vec_phase_v3.txt" 直接 $finish，
# 表现为"仿真跑完但什么都没测"——极容易被误读成通过。
set -e
# 本脚本已从仓库根移到 tools/，所以 ROOT 要上退一级。
# 踩过的坑：移动后若仍用 dirname "$0"，$ROOT 会变成 tools/，
# 导致下面的 cp "$ROOT"/tb/... 与 -f flow/build/... 全部找不到。
ROOT=$(cd "$(dirname "$0")/.." && pwd)
WORK=${WORK:-/tmp/topw_run}
rm -rf "$WORK"; mkdir -p "$WORK"
cp "$ROOT"/tb/vec_topw_coef.txt "$ROOT"/tb/vec_topw_gold.txt \
   "$ROOT"/tb/vec_phase_v3.txt "$WORK"/

if [ -n "${VCS_HOME:-}" ] || command -v vcs >/dev/null 2>&1; then
    VCS=${VCS_HOME:+$VCS_HOME/bin/}vcs
    cd "$ROOT"
    "$VCS" -full64 -sverilog -timescale=1ns/1ps -f flow/build/filelists/files_topw.f \
           -o "$WORK/simv" -Mdir="$WORK/csrc" > "$WORK/compile.log" 2>&1
    cd "$WORK" && ./simv | tee run.log | tail -12
elif command -v iverilog >/dev/null 2>&1; then
    cd "$ROOT"
    iverilog -g2012 -o "$WORK/simv" -f flow/build/filelists/files_topw.f
    cd "$WORK" && ./simv | tail -12
else
    echo "需要 VCS 或 iverilog（见 README §怎么跑）" >&2; exit 127
fi
