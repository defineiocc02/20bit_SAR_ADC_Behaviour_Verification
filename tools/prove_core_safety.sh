#!/usr/bin/env bash
# =============================================================================
# 文件名     : prove_core_safety.sh
# 模块名     : 核心轻量形式验证入口
# 版本       : 随 Git 源版本
# 功能       : 读取生产 DEM 与调度器，运行两个 SymbiYosys 任务。
# 设计说明   : DEM 使用 ABC PDR。调度属性通过 SBY 的准备任务运行 SAT 归纳。
#              后者核对真实归纳结论，单纯准备成功不能作为证明。
# 变更记录   : 增加两个独立形式属性的统一入口。
# =============================================================================
set -euo pipefail
root=$(cd "$(dirname "$0")/.." && pwd -P)
out=${1:?Usage: bash tools/prove_core_safety.sh OUTPUT}
out=$(python3 -c 'import pathlib,sys;print(pathlib.Path(sys.argv[1]).resolve())' "$out")
case "$out/" in "$root/"*) printf '%s\n' 'output directory must be outside checkout' >&2; exit 2 ;; esac
mkdir -- "$out"
cd "$out"
cp "$root/flow/formal/refactor/core_safety.sby" "$root/flow/formal/refactor/core_safety.sv" .
sv2v "$root/rtl/sar_core/dem_mask_v3.sv" "$root/rtl/sar_core/sched18.sv" > core-leaves.v
timeout 180 "${SBY:-sby}" -d dem core_safety.sby dem
timeout 300 "${SBY:-sby}" -d sched core_safety.sby sched
grep -F 'Induction step proven: SUCCESS!' sched/model/design.log
printf '%s\n' 'CORE_SAFETY PASS dem_saturation sched_disjoint sched_count8'
