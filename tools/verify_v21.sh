#!/usr/bin/env bash
# =============================================================================
# 文件名     : verify_v21.sh
# 版本       : v1.0
# 功能       : v21 数字 IP 的正式验收入口（一条命令跑完全部回归）。
# 设计说明   :
#   * 生产依赖从 v21 文件清单读取。ADC2 行为宏仅用于仿真。
#   * RTL 用例在新建的任务目录中运行；重构还要对实际轨迹逐笔核对。
#   * V21_RUNNER 可指定 SSH 主机。未设置时使用本机 VCS。
#   * --check-sources 只核对并打印仿真源，不启动任何工具。
# =============================================================================
set -uo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
RUNNER=${V21_RUNNER:-${D3D4_RUNNER:-}}
REQUESTED_WORK=${V21_WORKDIR:-${D3D4_WORKDIR:-}}
# 模拟器可由执行主机的环境选择。仅在 PATH 无 vcs 时使用现有环境默认位置。
SETUP='export SNPSLMD_LICENSE_FILE="${SNPSLMD_LICENSE_FILE:-27080@localhost}"; if ! command -v vcs >/dev/null; then export VCS_HOME="${VCS_HOME:-/opt/synopsys/vcs/W-2024.09-SP1}"; export PATH="$VCS_HOME/bin:$PATH"; fi'

# 生产清单的每行均为仓库相对路径。在远端保持原有目录结构，
# 因为端到端测试的 include 与向量以这些路径为坐标。
# ---- 1. 读取生产清单及测试项 ---------------------------------------------------
SOURCES=()
while IFS= read -r source || [ -n "$source" ]; do
    case "$source" in
        ""|//*) continue ;;
    esac
    if [[ ! "$source" =~ ^rtl/[[:alnum:]_./-]+\.sv$ || "$source" == *../* || ! -f "$ROOT/$source" ]]; then
        printf 'Invalid v21 source: %s\n' "$source" >&2
        exit 1
    fi
    SOURCES+=("$source")
done < "$ROOT/flow/build/filelists/files_adc_v21.f"
if [ "${#SOURCES[@]}" -eq 0 ]; then
    printf 'The v21 production list is empty.\n' >&2
    exit 1
fi
SOURCES+=(rtl/v21/adc2_node_macro.sv)
if [ ! -f "$ROOT/${SOURCES[${#SOURCES[@]}-1]}" ]; then
    printf 'The v21 ADC2 test macro is missing.\n' >&2
    exit 1
fi
case "${1:-}" in
    --check-sources) printf '%s\n' "${SOURCES[@]}"; exit 0 ;;
    "") ;;
    *) printf 'Usage: %s [--check-sources]\n' "$0" >&2; exit 2 ;;
esac
SRC_REL="${SOURCES[*]}"

# 测试项：<源文件> <二进制名> <期望字符串>。
# 有些 testbench 在打印 FAIL 后也会正常退出，所以每项还须核对完成标志。
TESTS=(
  "tb/v21/tb_acq_ctx_fixed.sv|sax|PASS (两反例已修复)"
  "tb/v21/tb_audit3.sv|sa3|PASS (10 checks)"
  "tb/v21/tb_i12_full.sv|s12|PASS (105 checks)"
  "tb/v21/tb_i3_full.sv|s3f|PASS (20 checks)"
  "tb/v21/tb_i4b_e2e.sv|s4b|PASS (25 checks)"
  "tb/tb_recip_q60.sv|srecip|PASS (43766 inverses bit-exact"
  "tb/v21/tb_recip_rnd.sv|srnd|PASS (20817 RND(2^60,G) bit-exact)"
  "tb/v21/tb_analog_if.sv|saif|SUMMARY checks=124 failures=0"
  "tb/v21/tb_cfg_lifecycle.sv|scl|PASS (20 checks)"
  "tb/v21/tb_i4_recon.sv|si4|PASS (8213 checks, 8209 retired)"
  "tb/v21/tb_recon_stream.sv|sstr|RECON_STREAM_CAPTURE_COMPLETE"
)

# ---- 2. 执行端目录与源码暂存 ---------------------------------------------------
# 远端工具命令经标准输入交给 Bash，SSH 目标始终作为主机参数传入。
fail=0
run() {
    if [ -n "$RUNNER" ]; then
        printf '%s\n' "$1" | ssh -- "$RUNNER" bash -s
    else
        bash -c "$1"
    fi
}

# 在执行端解析实际父目录，避免符号链接把产物指向任务目录以外。
# mkdir 独占创建，若目标已存在立即失败并保留其中的文件。
if [[ -n "$REQUESTED_WORK" && ! "$REQUESTED_WORK" =~ ^/[[:alnum:]_./-]+$ ]]; then
    printf 'V21_WORKDIR must be an absolute path without spaces.\n' >&2
    exit 2
fi
WORK=$(run "$(printf 'requested=%q\n' "$REQUESTED_WORK")"'
    set -eu
    umask 077
    if [ -z "$requested" ]; then
        mktemp -d /tmp/v21_verify.XXXXXX
        exit 0
    fi
    parent=$(cd -P -- "$(dirname "$requested")" && pwd -P)
    leaf=$(basename "$requested")
    temporary=$(cd -P /tmp && pwd -P)
    home=$(cd -P "$HOME" && pwd -P)
    [ "$parent/$leaf" != "$home" ] || exit 2
    retained=""
    if [ -d "$HOME/eda_runs" ]; then
        retained=$(cd -P "$HOME/eda_runs" && pwd -P)
    fi
    case "$leaf" in .|..) exit 2 ;; esac
    if [[ "$parent" != "$temporary" && "$parent" != "$temporary/"* &&
          ( -z "$retained" || ( "$parent" != "$retained" && "$parent" != "$retained/"* ) ) ]]; then
        printf "V21_WORKDIR must be under /tmp or \$HOME/eda_runs.\n" >&2
        exit 2
    fi
    mkdir -- "$parent/$leaf"
    printf "%s\n" "$parent/$leaf"
') || { printf 'Unable to create fresh v21 work directory.\n' >&2; exit 1; }
if [[ ! "$WORK" =~ ^/[[:alnum:]_./-]+$ ]]; then
    printf 'Invalid v21 work directory returned by execution host.\n' >&2
    exit 1
fi

echo "=== 准备 $WORK ==="
run "mkdir -p -- $WORK/rtl/v21 $WORK/tb/v21 $WORK/tools" || exit 1
# scp 逐文件传输让 RTL 与测试台的相对路径在两端严格一致。
FILES=("${SOURCES[@]}" tb/v21/tb_e2e_decl.svh tb/v21/tb_e2e_body.svh \
       tb/vec_r18_recip.txt tb/v21/vec_recip_rnd.txt tb/v21/vec_i4_recon.txt \
       tools/check_rtl_rnd96.py tools/recon_oracle.py)
for test_case in "${TESTS[@]}"; do
    FILES+=("${test_case%%|*}")
done
for f in "${FILES[@]}"; do
    if [ -n "$RUNNER" ]; then
        scp -q -- "$ROOT/$f" "$RUNNER:$WORK/$f" || exit 1
    else
        cp -- "$ROOT/$f" "$WORK/$f" || exit 1
    fi
done

echo
# ---- 3. 回归与证据边界 ---------------------------------------------------------
# 最小算术用例使用自己的叶子源，其余测试用同一份整核输入。
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
    tb_recip_rnd.sv)
      SRC_USE="rtl/recip_q60.sv rtl/v21/recip_rnd_v21.sv" ;;
    tb_analog_if.sv)
      SRC_USE="$SRC_REL" ;;
    tb_cfg_lifecycle.sv)
      SRC_USE="rtl/v21/wsum_v21.sv" ;;
    # 两个测试台共用发布的倒数链和重构器；一个按冻结码验收，
    # 一个跟踪跨拍的 N、I、X 和标签，均不需要 ADC2 行为宏。
    tb_i4_recon.sv|tb_recon_stream.sv)
      SRC_USE="rtl/recip_q60.sv rtl/v21/recip_rnd_v21.sv rtl/v21/recon_v21.sv" ;;
    *) SRC_USE="$SRC_REL" ;;
  esac
  run "$SETUP; cd $WORK && vcs -full64 -sverilog -timescale=1ns/1ps $SRC_USE $f -o $bin > ${bin}_c.log 2>&1" \
      || { echo "  $bin 编译 FAIL"; fail=1; continue; }
  # tb_recip_q60 需要显式向量路径（它的默认是相对路径）
  VARGS=""
  case "$bf" in
    tb_recip_q60.sv) VARGS="+VEC=$WORK/tb/vec_r18_recip.txt" ;;
    tb_recip_rnd.sv) VARGS="+VEC=$WORK/tb/v21/vec_recip_rnd.txt" ;;
    tb_i4_recon.sv) VARGS="+VEC=$WORK/tb/v21/vec_i4_recon.txt" ;;
    tb_recon_stream.sv)
      # 生成器与检查器来自同一份源码快照，旧运行目录里的 trace 不可复用。
      # 模拟器和检查器都在本次 WORK 内按相同的相对路径读写。
      run "cd $WORK && python3 -B tools/recon_oracle.py generate stream.vec" \
        || { echo "  $bf 激励生成失败"; fail=1; continue; }
      VARGS="+VEC=stream.vec +TRACE=stream.csv" ;;
  esac
  # 退出码、完成标志均需通过，避免中途失败把先前的字样当成本项结果。
  if out=$(run "$SETUP; cd $WORK && ./$bin $VARGS 2>&1") && grep -Fq "$want" <<< "$out"; then
    if [[ "$bf" == tb_recon_stream.sv ]]; then
      # 仅捕获完 CSV 无法判定出码正确，边界探针与逐级逐标签对拍须各自通过。
      if ! grep -Fxq 'RECON_S3_LIMITS_PASS checks=6' <<< "$out" ||
         ! run "cd $WORK && python3 -B tools/recon_oracle.py examine stream.csv stream.json"; then
        echo "  $bf  FAIL（S3 边界或逐笔输出不符）"; fail=1; continue
      fi
    fi
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
if run "$SETUP; cd $WORK && python3 tools/check_rtl_rnd96.py --work-dir $WORK 2>&1" | grep -q "RESULT: RTL_CHECK_PASS"; then
  echo "  [B] 生产 RTL 的 rnd96_const            PASS（从源码抽取）"
else
  echo "  [B] 生产 RTL 的 rnd96_const            FAILED"; fail=1
fi
# [C] 属性证明要由专门的形式工具执行，独立于定向回归。
echo "  [C] VC Formal 属性证明                 未完成（不计入通过）"

echo
if [ "$fail" -eq 0 ]; then
  echo "全部通过：${#TESTS[@]} 项 RTL 回归 + [A] 数学证明 + [B] 生产 RTL 检查"
  echo "注意：[C] 工具形式化未完成，本入口不声称已形式化验证。"
else
  echo "存在失败项，见上"
fi
exit "$fail"
