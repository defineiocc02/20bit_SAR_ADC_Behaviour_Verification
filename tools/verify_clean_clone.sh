#!/usr/bin/env bash
# =============================================================================
# 文件名     : verify_clean_clone.sh
# 版本       : v1.1
# 功能       : 从**全新克隆**验证仓库可用性 —— 证明结果不依赖本地未提交残留。
#
# 设计说明   :
#   * 为什么需要它：开发时工作树里有临时文件、缓存、未提交改动。
#     "在我这儿能跑"不等于"别人克隆下来能跑"。本脚本把两者对齐。
#   * 检查项（全部要求 exit 0）：
#       1. 全量单元/验证测试
#       2. 工程自检 7 项
#       3. python -m adi_model simulate（最小运行；--output 必须是新目录）
#       4. tools/check_audit_findings.py（4/4）
#       5. tools/behavior_fingerprint.py --check（指纹与基线一致）
#       6. tools/run_p4_ablation.py（矩阵重跑，与入库数据逐项一致）
#       7. tools/run_review_figures.py（图重生成）
#   * 用法：
#       bash tools/verify_clean_clone.sh              # 用当前仓库做克隆源
#       bash tools/verify_clean_clone.sh <repo-path>  # 指定克隆源
#       SKIP_HEAVY=1 bash tools/verify_clean_clone.sh # 跳过第 6/7 项（快）
#   * 环境变量：
#       PYTHON     解释器路径（默认 python3）
#       SKIP_HEAVY 非空则跳过矩阵与图
# =============================================================================
set -u

PYTHON="${PYTHON:-python3}"
SRC="${1:-$(cd "$(dirname "$0")/.." && pwd)}"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/clean-clone-XXXXXX")"
CLONE="$WORK/repo"
FAILED=0

note() { printf '\n=== %s ===\n' "$1"; }
ok()   { printf 'PASS  %s\n' "$1"; }
bad()  { printf 'FAIL  %s\n' "$1"; FAILED=$((FAILED + 1)); }

note "clone"
if ! git clone -q "$SRC" "$CLONE"; then
  echo "FAIL  git clone of $SRC"; exit 1
fi
cd "$CLONE" || exit 1
printf 'source: %s\nat commit: %s\n' "$SRC" "$(git rev-parse --short HEAD)"

note "1. tests"
# 覆盖**全部**测试目录，不再按目录/文件排除。
# 历史教训（2026-11-13）：
#   ① 曾排除 2 个文件（138 个测试），理由是早期 tclsh 环境问题；环境修好后
#      清单没更新，这 138 个测试长期无人检查。
#   ② 曾只跑 tests/unit + tests/verification，漏掉 tests/audit、tests/regression、
#      tests/integration 共 17 个文件；首次运行时暴露出 9 failed。
# 唯一保留的例外是 test_source_bound_replay.py：它需要 VERILATOR，
# 本机没有，只能整体跳过——这是**环境限制**而非排除清单。
if PYTHONPATH=src "$PYTHON" -m pytest -q -p no:cacheprovider \
     tests/unit tests/verification tests/audit tests/regression \
     --ignore=tests/integration >/dev/null 2>&1; then
  ok "tests (unit/verification/audit/regression)"
else
  bad "tests (unit/verification/audit/regression)"
fi

# integration 单独跑：本机缺 VERILATOR，test_source_bound_replay 会整体报错。
INTEGRATION_IGNORE="--ignore=tests/integration/test_source_bound_replay.py"
if PYTHONPATH=src "$PYTHON" -m pytest -q -p no:cacheprovider \
     tests/integration $INTEGRATION_IGNORE >/dev/null 2>&1; then
  ok "tests (integration, minus VERILATOR-dependent)"
else
  bad "tests (integration)"
fi

note "2. check_engineering"
if "$PYTHON" tools/check_engineering.py >/dev/null 2>&1; then
  ok "check_engineering (7/7)"
else
  bad "check_engineering"
fi

note "3. minimal simulate"
# --output 必须是**不存在的**目录；这里用全新的名字。
OUT="$WORK/sim_output"
if PYTHONPATH=src "$PYTHON" -m adi_model simulate --samples 32 --output "$OUT" >/dev/null 2>&1; then
  ok "python -m adi_model simulate"
else
  bad "simulate"
fi

note "4. audit findings"
if PYTHONPATH=src "$PYTHON" tools/check_audit_findings.py 2>/dev/null | grep -q "4/4"; then
  ok "check_audit_findings (4/4)"
else
  bad "check_audit_findings"
fi

note "5. behaviour fingerprint"
if PYTHONPATH=src "$PYTHON" tools/behavior_fingerprint.py \
     --check docs/restructure/behavior_fingerprint.json >/dev/null 2>&1; then
  ok "behaviour fingerprint matches baseline"
else
  bad "behaviour fingerprint"
fi

if [ -n "${SKIP_HEAVY:-}" ]; then
  echo
  echo "SKIP  heavy checks 6-7 (SKIP_HEAVY set)"
else
  note "6. ablation matrix"
  MAT="$WORK/mat"
  if PYTHONPATH=src:. "$PYTHON" tools/run_p4_ablation.py \
       --dynamic-n 1024 --static-points 512 --out "$MAT" >/dev/null 2>&1; then
    cat > "$WORK/cmp.py" <<'PY'
import json, sys
fresh = {c["id"]: c for c in json.load(open(sys.argv[1]))["configs"]}
stored = {c["id"]: c for c in json.load(open(sys.argv[2]))["configs"]}
assert set(fresh) == set(stored), "config set differs"
for key in fresh:
    a = (fresh[key].get("static") or {}).get("inl_max_lsb20")
    b = (stored[key].get("static") or {}).get("inl_max_lsb20")
    if a is None or b is None:
        continue
    assert abs(a - b) < 1e-9, f"{key}: {a} != {b}"
PY
    if "$PYTHON" "$WORK/cmp.py" "$MAT/p4_ablation.json" \
         docs/reviews/20261113_p3p4/data/p4_ablation.json >/dev/null 2>&1; then
      ok "fresh matrix matches stored data"
    else
      bad "fresh matrix differs from stored data"
    fi
  else
    bad "run_p4_ablation"
  fi

  note "7. figures"
  if PYTHONPATH=src:. "$PYTHON" tools/run_review_figures.py \
       --out "$WORK/fig" >/dev/null 2>&1; then
    count=$(find "$WORK/fig" -name '*.png' 2>/dev/null | wc -l | tr -d ' ')
    if [ "$count" -ge 12 ]; then
      ok "run_review_figures ($count png)"
    else
      bad "run_review_figures produced only $count png"
    fi
  else
    bad "run_review_figures"
  fi
fi

rm -rf "$WORK"
note "result"
if [ "$FAILED" -eq 0 ]; then
  echo "ALL CLEAN-CLONE CHECKS PASSED"
  exit 0
fi
echo "$FAILED check(s) failed"
exit 1
