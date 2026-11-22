#!/usr/bin/env bash
# =============================================================================
# 文件名     : check_rtl_mutation.sh
# 版本       : v1.0
# 功能       : 对 check_rtl_rnd96.py 做"变异测试"——证明它真的能发现错误。
#
# 设计说明   :
#   * 审查指出：早前 prove_rnd96.py 自己重写公式，把生产 RTL 改错它仍打印
#     PROVEN。一个"改错了也不报"的检查等于没有检查。
#   * 本脚本依次注入三类真实错误，要求 check_rtl_rnd96.py **必须失败**，
#     然后恢复并**必须通过**。任何一步不符合预期即退出非零。
#   * 只改临时副本，不动工作树里的生产文件。
#
# 用法: bash tools/check_rtl_mutation.sh
# 变更记录   :
#   2026-11-16  初版。
# =============================================================================
set -uo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
if [ "${1:-}" = "--core-only" ]; then
  exec bash "$ROOT/tools/check_core_counterexamples.sh" "${2:?missing output directory}" --mutate
fi
RUNNER=${D3D4_RUNNER:-}
WORK=${D3D4_WORKDIR:-}
RTL_REL=rtl/v21/recon_v21.sv

run() { if [ -n "$RUNNER" ]; then ssh "$RUNNER" "$1"; else bash -c "$1"; fi; }

if [ -z "$WORK" ]; then
  WORK=$(run 'mktemp -d /tmp/rtlmut.XXXXXXXX') || exit 1
else
  [[ "$WORK" =~ ^/[A-Za-z0-9_./-]+$ ]] || exit 2
  run "mkdir -- '$WORK'" || exit 1
fi
echo "=== 准备 $WORK ==="
run "mkdir -p -- '$WORK/rtl/v21' '$WORK/tools'" || exit 1
if [ -n "$RUNNER" ]; then
  scp -q "$ROOT/$RTL_REL"    "$RUNNER:$WORK/$RTL_REL"
  scp -q "$ROOT/tools/check_rtl_rnd96.py" "$RUNNER:$WORK/tools/"
else
  cp "$ROOT/$RTL_REL" "$WORK/$RTL_REL"
  cp "$ROOT/tools/check_rtl_rnd96.py" "$WORK/tools/"
fi

SETUP='export PATH=/opt/synopsys/vcs/W-2024.09-SP1/bin:$PATH SNPSLMD_LICENSE_FILE=27080@localhost'

chk() {  # $1=期望(pass|fail)  $2=描述
  out=$(run "$SETUP; cd $WORK && python3 tools/check_rtl_rnd96.py 2>&1")
  if echo "$out" | grep -q "RESULT: RTL_CHECK_PASS"; then got=pass; else got=fail; fi
  if [ "$got" = "$1" ]; then
    echo "  OK   $2  (期望 $1，实得 $got)"
  else
    echo "  BAD  $2  (期望 $1，实得 $got)"
    echo "$out" | tail -6
    return 1
  fi
}

fail=0
echo
echo "=== 基线 ==="
chk pass "未改动的生产 RTL" || fail=1

echo
echo "=== 注入 1：常数漏最高位（历史真实事故）==="
run "cd $WORK && sed -i \"s/35'h1_5555_5556/34'h5555_5556/\" $RTL_REL"
chk fail "常数 0x1_5555_5556 -> 0x5555_5556" || fail=1
if [ -n "$RUNNER" ]; then scp -q "$ROOT/$RTL_REL" "$RUNNER:$WORK/$RTL_REL"; else cp "$ROOT/$RTL_REL" "$WORK/$RTL_REL"; fi

echo
echo "=== 注入 2：移位量 34 -> 33 ==="
run "cd $WORK && sed -i 's/prod >> 34/prod >> 33/' $RTL_REL"
chk fail "除以 3 的移位量改错" || fail=1
if [ -n "$RUNNER" ]; then scp -q "$ROOT/$RTL_REL" "$RUNNER:$WORK/$RTL_REL"; else cp "$ROOT/$RTL_REL" "$WORK/$RTL_REL"; fi

echo
echo "=== 注入 3：舍入阈值 96 -> 95 ==="
run "cd $WORK && sed -i \"s/>= 32'd96/>= 32'd95/\" $RTL_REL"
chk fail "恰半离零的判据改错" || fail=1
if [ -n "$RUNNER" ]; then scp -q "$ROOT/$RTL_REL" "$RUNNER:$WORK/$RTL_REL"; else cp "$ROOT/$RTL_REL" "$WORK/$RTL_REL"; fi

echo
echo "=== 恢复后必须通过 ==="
chk pass "恢复原始 RTL" || fail=1

echo
if [ "$fail" -eq 0 ]; then
  echo "全部符合预期：检查能发现常数/移位/舍入三类漂移"
else
  echo "存在不符合预期的项"
fi
exit "$fail"
