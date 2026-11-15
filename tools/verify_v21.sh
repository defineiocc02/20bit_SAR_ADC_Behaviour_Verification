#!/usr/bin/env bash
# =============================================================================
# 文件名     : verify_v21.sh
# 版本       : v1.0
# 功能       : v21 数字 IP 的正式验收入口（一条命令跑完全部回归）。
# 设计说明   :
#   * 四项回归 + 三项审计反例，全部在本机编译、远端执行。
#   * 任一项 FAIL 即整体退出码非零。
#   * 用法：
#       D3D4_RUNNER=vm-yian bash tools/verify_v21.sh
#     不给 RUNNER 则在本地跑（需要本机有 vcs）。
# 变更记录   :
#   2026-11-15  初版。对应三项审计修复后的正式回归入口。
# =============================================================================
set -uo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
RUNNER=${D3D4_RUNNER:-}
WORK=${D3D4_WORKDIR:-/tmp/v21_verify}
SIM=${D3D4_SIM:-vcs}

SETUP='export SNPSLMD_LICENSE_FILE=27080@localhost PATH=/opt/synopsys/vcs/W-2024.09-SP1/bin:$PATH VCS_HOME=/opt/synopsys/vcs/W-2024.09-SP1'

# 生产 + 仿真源（节点宏只在仿真侧）
# 源文件（仓库内相对路径）。叶子在 rtl/，v21 层在 rtl/v21/。
SRC="rtl/sched18.sv rtl/dem_phase_rr.sv rtl/dem_phase_gen.sv rtl/dither_pre.sv \
rtl/dem_mask_v3.sv rtl/follower_rt.sv \
rtl/v21/acq_conv_ctx.sv rtl/v21/sar_evt_v21.sv rtl/v21/adc2_capture.sv \
rtl/v21/wsum_v21.sv rtl/v21/recon_v21.sv rtl/v21/adc2_node_macro.sv \
rtl/v21/adc_digital_ip_v21.sv"
# 编译时在 $WORK 下的相对名（保持 rtl/ 前缀，与仓库一致）
SRC_REL="$SRC"

# 测试项：<源文件> <二进制名> <期望字符串>
TESTS=(
  "tb/v21/tb_acq_ctx_fixed.sv|sax|PASS (两反例已修复)"
  "tb/v21/tb_audit3.sv|sa3|PASS (10 checks)"
  "tb/v21/tb_i12_full.sv|s12|PASS (105 checks)"
  "tb/v21/tb_i3_full.sv|s3f|PASS (20 checks)"
  "tb/v21/tb_i4b_e2e.sv|s4b|PASS (25 checks)"
  "tb/tb_recip_q60.sv|srecip|PASS (43766 inverses bit-exact"
)

fail=0
run() { if [ -n "$RUNNER" ]; then ssh "$RUNNER" "$1"; else bash -c "$1"; fi; }

echo "=== 准备 $WORK ==="
run "rm -rf $WORK && mkdir -p $WORK/rtl/v21 $WORK/tb/v21 $WORK/tools" || { echo "FAIL: 建目录"; exit 1; }
# 逐个文件传：tar 在 macOS->Linux 上会带 xattr 头，且扁平化路径，
# 实测导致叶子源文件没落到预期位置（编译报 "Source file cannot be opened"）。
# 用 scp/cp 逐个复制最可靠。
FILES="$SRC rtl/recip_q60.sv tb/tb_recip_q60.sv tb/vec_r18_recip.txt \
tb/v21/tb_acq_ctx_fixed.sv tb/v21/tb_audit3.sv tb/v21/tb_i12_full.sv \
tb/v21/tb_i3_full.sv tb/v21/tb_i4b_e2e.sv tools/check_rtl_rnd96.py"
for f in $FILES; do
  if [ -n "$RUNNER" ]; then
    scp -q "$ROOT/$f" "$RUNNER:$WORK/$f" || { echo "FAIL: 传 $f"; exit 1; }
  else
    cp "$ROOT/$f" "$WORK/$f"
  fi
done

echo
echo "=== 回归 ==="
for t in "${TESTS[@]}"; do
  f=${t%%|*}; rest=${t#*|}; bin=${rest%%|*}; want=${rest##*|}
  bf=$(basename "$f")
  # tb_acq_ctx_fixed 只实例化 acq_conv_ctx + dither_pre（对照审查的最小反例）
  case "$bf" in
    tb_acq_ctx_fixed.sv)
      SRC_USE="rtl/dither_pre.sv rtl/v21/acq_conv_ctx.sv" ;;
    tb_recip_q60.sv)
      SRC_USE="rtl/recip_q60.sv" ;;
    *) SRC_USE="$SRC_REL" ;;
  esac
  run "$SETUP; cd $WORK && vcs -full64 -sverilog -timescale=1ns/1ps $SRC_USE $f -o $bin > ${bin}_c.log 2>&1" \
      || { echo "  $bin 编译 FAIL"; fail=1; continue; }
  # tb_recip_q60 需要显式向量路径（它的默认是相对路径）
  VARGS=""
  case "$bf" in
    tb_recip_q60.sv) VARGS="+VEC=$WORK/tb/vec_r18_recip.txt" ;;
  esac
  out=$(run "$SETUP; cd $WORK && ./$bin $VARGS 2>&1")
  if echo "$out" | grep -q "$want"; then
    echo "  $bf  PASS"
  else
    echo "  $bf  FAIL（期望 '$want'）"
    echo "$out" | grep -E "ERROR|RESULT" | head -5
    fail=1
  fi
done

echo
echo "=== 算术证据（三类分开，不互相替代）==="
# [A] 数学恒等式（与 RTL 无关）
if python3 "$ROOT/tools/prove_rnd96.py" 2>&1 | grep -q "RESULT: PROVEN"; then
  echo "  [A] 数学恒等式 RND(n,96)              PROVEN"
else
  echo "  [A] 数学恒等式 RND(n,96)              FAILED"; fail=1
fi
# [A] X 位宽全域证明
if python3 "$ROOT/tools/prove_xwidth.py" 2>&1 | grep -q "RESULT: XWIDTH_PROVEN"; then
  echo "  [A] X 的 32 位截断 |X|<2^31           PROVEN（前提 A1-A5）"
else
  echo "  [A] X 的 32 位截断                    FAILED"; fail=1
fi
# [B] 生产 RTL 检查（从源码抽取 + VCS）
if run "$SETUP; cd $WORK && python3 tools/check_rtl_rnd96.py 2>&1" | grep -q "RESULT: RTL_CHECK_PASS"; then
  echo "  [B] 生产 RTL 的 rnd96_const            PASS（从源码抽取）"
else
  echo "  [B] 生产 RTL 的 rnd96_const            FAILED"; fail=1
fi
# [C] 工具形式化：未完成，如实标注
echo "  [C] VC Formal 属性证明                 未完成（不计入通过）"

echo
if [ "$fail" -eq 0 ]; then
  echo "全部通过：6 项 RTL 回归 + [A] 数学证明 + [B] 生产 RTL 检查"
  echo "注意：[C] 工具形式化未完成，本入口不声称已形式化验证。"
else
  echo "存在失败项，见上"
fi
exit "$fail"
