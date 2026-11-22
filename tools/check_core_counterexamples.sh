#!/usr/bin/env bash
# =============================================================================
# 文件名     : check_core_counterexamples.sh
# 模块名     : 核心独立四态反例和变异门禁
# 版本       : 随 Git 源版本
# 功能       : 使用 sv2v 与 Icarus 核对保护码和 seed/adv 同拍，再实测反例。
# 设计说明   : DUT 与测试台分开转换。变异必须成功编译且被预期断言抓住。
# 变更记录   : 增加 DEM 回绕和调度器丢种子的负控制。
# =============================================================================
set -euo pipefail
root=$(cd "$(dirname "$0")/.." && pwd -P)
out=${1:?Usage: bash tools/check_core_counterexamples.sh OUTPUT_DIR [--mutate]}
mode=${2:-}
case "$mode" in ''|--mutate) ;; *) exit 2 ;; esac
mkdir -- "$out"
out=$(cd "$out" && pwd -P)
case "$out/" in "$root/"*) printf '%s\n' 'output directory must be outside checkout' >&2; exit 2 ;; esac
export LC_ALL=C

verify() {
    local label=$1
    local variant=$out/$label
    # 三个候选都从同一份原源码开始，在各自工件目录独立操作。
    mkdir "$variant"
    cp "$root/rtl/dem_mask_v3.sv" "$root/rtl/sched18.sv" "$variant/"
    python3 - "$label" "$variant" <<'PY'
from pathlib import Path
import sys
name, root = sys.argv[1], Path(sys.argv[2])
edits = {
    "dem_wrap": ("dem_mask_v3.sv", "k_i >= 10'd520", "k_i[3:0] >= 4'd8"),
    "seed_override": ("sched18.sv","else if (adv) rng_q <= x2;", "if (adv) rng_q <= x2;"),
}
if name in edits:
    filename, old, new = edits[name]
    path = root / filename
    source = path.read_text()
    if source.count(old) != 1:
        raise RuntimeError(f"mutation anchor stale: {filename} {old}")
    path.write_text(source.replace(old, new))
PY
    # 只转换 DUT 源。Icarus 用原始四态台架独立检查返回位图和事件。
    sv2v "$variant/dem_mask_v3.sv" "$variant/sched18.sv" > "$variant/core.v"
    iverilog -g2012 -Wall -s tb_core_p0 -o "$variant/sim.vvp" "$variant/core.v" \
        "$root/flow/formal/refactor/tb_core_p0.sv" > "$variant/compile.log" 2>&1
    local rc=0
    vvp -N "$variant/sim.vvp" > "$variant/simulation.log" 2>&1 || rc=$?
    if [ "$label" = baseline ]; then
        test "$rc" -eq 0
        grep -Fx 'CORE_P0 PASS dem_vectors=32 seed_adv=1' "$variant/simulation.log"
    else
        test "$rc" -ne 0
        if [ "$label" = dem_wrap ]; then
            grep -m1 -F 'DEM_RANGE FAIL k=528' "$variant/simulation.log"
        else
            grep -m1 -F 'SEED_ADV FAIL' "$variant/simulation.log"
        fi
        printf 'MUTATION_CAUGHT %s\n' "$label"
    fi
}
verify baseline
if [ "$mode" = "--mutate" ]; then
    verify dem_wrap
    verify seed_override
fi
