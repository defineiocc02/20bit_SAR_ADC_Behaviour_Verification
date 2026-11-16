#!/usr/bin/env bash
# =============================================================================
# 文件名     : check_formal.sh
# 版本       : v1.0
# 功能       : 用 Formality 做综合网表与 RTL 的形式化等价检查。
#
# 设计说明   :
#   * 前置：dc_synth_v21.tcl 已生成网表与 SVF
#       /tmp/dcwork/adc_digital_ip_v21_syn.v
#       /tmp/dcwork/adc_digital_ip_v21.svf
#   * **耗时**：本轮实测 verify 阶段 >850 s 仍未完成（加载 SVF 后
#     计算量显著增加），所以本脚本默认预留 45 min，且建议后台跑。
#   * 结果解读：
#       - Verification SUCCEEDED -> 形式化等价成立
#       - Verification FAILED    -> 看 failing point 名单；
#                                  若集中在 DFCNQD1 寄存器上，
#                                  先确认 SVF 是否加载成功
#       - 超时                   -> 如实报告"未取得结论"，不当作通过
#   * 本脚本**不**与 verify_v21.sh 合并：形式化耗时长，日常回归不该被它拖慢。
#
# 用法: D3D4_RUNNER=vm-yian bash tools/check_formal.sh [timeout_sec]
# 变更记录   :
#   2026-11-16  初版。
# =============================================================================
set -uo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
RUNNER=${D3D4_RUNNER:-}
WORK=${D3D4_WORKDIR:-/tmp/fml}
TMO=${1:-2700}

run() { if [ -n "$RUNNER" ]; then ssh "$RUNNER" "$1"; else bash -c "$1"; fi; }
SETUP='export PATH=/opt/synopsys/fm/W-2024.09-SP3/bin:$PATH SNPSLMD_LICENSE_FILE=27080@localhost LM_LICENSE_FILE=27080@localhost'

echo "=== 前置检查 ==="
pre=$(run "ls -la /tmp/dcwork/adc_digital_ip_v21_syn.v /tmp/dcwork/adc_digital_ip_v21.svf 2>&1")
echo "$pre"
if ! echo "$pre" | grep -q "\.svf"; then
  echo
  echo "FAIL: 缺 SVF。没有 SVF 时 DC 的极性变换无法复现，"
  echo "      verify 会报大量假失败（本轮实测 20 个，全在 DFCNQD1 寄存器上）。"
  echo "      先跑 dc_synth_v21.tcl 生成。"
  exit 1
fi

echo
echo "=== 准备 $WORK ==="
run "rm -rf $WORK && mkdir -p $WORK/rtl/v21" || exit 1
if [ -n "$RUNNER" ]; then
  scp -q "$ROOT/flow/synth/v21/fm_equiv.tcl" "$RUNNER:$WORK/" || exit 1
else
  cp "$ROOT/flow/synth/v21/fm_equiv.tcl" "$WORK/"
fi

echo
echo "=== 运行 Formality（预算 ${TMO}s）==="
run "$SETUP; cd $WORK && timeout $TMO fm_shell -f $WORK/fm_equiv.tcl > $WORK/fm.log 2>&1"
rc=$?
echo "  exit=$rc"

echo
echo "=== 阶段 ==="
run "grep -E '^STEP_' $WORK/fm.log" | tail -8

echo
echo "=== 结果 ==="
res=$(run "grep -E 'Verification (SUCCEEDED|FAILED|INCONCLUSIVE)|Passing compare|Failing compare|Unverified compare' $WORK/fm.log | head -5")
echo "$res"

echo
if echo "$res" | grep -q "Verification SUCCEEDED"; then
  echo "RESULT: FORMAL_EQUIV_PASS"
  exit 0
elif echo "$res" | grep -q "Verification FAILED"; then
  echo "RESULT: FORMAL_EQUIV_FAIL"
  echo "failed points:"
  run "grep 'Compare point.*failed' $WORK/fm.log | head -10"
  exit 1
else
  echo "RESULT: FORMAL_INCONCLUSIVE（超时或未出结论，不当作通过）"
  exit 2
fi
