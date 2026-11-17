#!/usr/bin/env bash
# =============================================================================
# 文件名     : verify_clean_clone.sh
# 版本       : v1.1
# 功能       : 从全新克隆验证当前提交的测试、工程自检和 Python 入口。
#
# 设计说明   :
#   * 在独立目录克隆，测试只接触 Git 中已有的文件和本次运行产物。
#   * 工程自检包含现行图件哈希和 v21 ADC2 的跨语言接口核对。
#   * CLI 示例沿用 README 的 64 笔样本。输出使用专用的新目录。
#   * 源机制反例核对需通过 4 项，失败时输出脚本原始结果。
#   * SKIP_HEAVY 非空时跳过失配扫描和单笔轨迹，保留上述检查。
#   * 通过 PYTHON 指定已安装项目依赖的解释器，默认使用 python3。
#   * bash tools/verify_clean_clone.sh <repo-path> 可指定待克隆仓库。
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

# 1. 当前测试目录
# 用模块入口保留仓库根导入路径，同时隔离基础测试和集成失败的诊断。
note "1. tests"
if PYTHONPATH=src "$PYTHON" -m pytest -q -p no:cacheprovider \
     flow/tests/unit flow/tests/verification flow/tests/audit flow/tests/regression \
     >"$WORK/tests.log" 2>&1; then
  ok "tests (unit/verification/audit/regression)"
else
  cat "$WORK/tests.log"
  bad "tests (unit/verification/audit/regression)"
fi

# 单列集成结果便于定位模型输入与普通单元测试的不同失败原因。
if PYTHONPATH=src "$PYTHON" -m pytest -q -p no:cacheprovider \
     flow/tests/integration >"$WORK/integration.log" 2>&1; then
  ok "tests (integration)"
else
  cat "$WORK/integration.log"
  bad "tests (integration)"
fi

# 2. 工程自检
# 图件身份和 ADC2 合同由同一自检入口核对，失败详情随其余检查一起输出。
note "2. check_engineering"
if "$PYTHON" tools/check_engineering.py >"$WORK/engineering.log" 2>&1; then
  ok "check_engineering (7/7)"
else
  cat "$WORK/engineering.log"
  bad "check_engineering"
fi

note "3. minimal simulate"
# CLI 要求输出目录此前不存在，避免写入另一次运行。
OUT="$WORK/sim_output"
if PYTHONPATH=src "$PYTHON" -m adi_model simulate --samples 64 --output "$OUT"; then
  ok "python -m adi_model simulate"
else
  bad "simulate"
fi

note "4. audit findings"
# 同时检查脚本退出状态和通过项计数，以便识别回归中途退出。
if PYTHONPATH=src "$PYTHON" tools/check_audit_findings.py >"$WORK/audit-findings.log" 2>&1 \
    && grep -Fq "4/4 audit findings resolved" "$WORK/audit-findings.log"; then
  ok "check_audit_findings (4/4)"
else
  cat "$WORK/audit-findings.log"
  bad "check_audit_findings"
fi

# 5–6. 可选模型诊断
# 两项各写入运行目录，独立失败仍会计入最终退出状态。
if [ -n "${SKIP_HEAVY:-}" ]; then
  echo
  echo "SKIP  mismatch sweep and sample trace (SKIP_HEAVY set)"
else
  note "5. run_mismatch_sweep"
  if PYTHONPATH=src "$PYTHON" tools/run_mismatch_sweep.py \
       --out "$WORK/sweep" --points 64 --repeats 2 >"$WORK/sweep.log" 2>&1; then
    ok "run_mismatch_sweep"
  else
    cat "$WORK/sweep.log"
    bad "run_mismatch_sweep"
  fi

  note "6. trace_one_sample"
  if PYTHONPATH=src "$PYTHON" tools/trace_one_sample.py \
       --out "$WORK/trace" >"$WORK/trace.log" 2>&1; then
    ok "trace_one_sample"
  else
    cat "$WORK/trace.log"
    bad "trace_one_sample"
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
