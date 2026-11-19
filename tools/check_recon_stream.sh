#!/usr/bin/env bash
# =============================================================================
# 文件名     : check_recon_stream.sh
# 版本       : v1.0
# 功能       : 重构流水"连续输入"回归 —— 相邻拍不同笔，逐笔逐级对拍。
#
# 设计说明   :
#   * 为什么需要独立入口：
#     原 tb_i4_recon 对每组输入**保持相同总线 7 拍**再检查一次，
#     这恰好让流水里的"实时总线读取"看起来正确，掩盖了取数点错误。
#     独立复核用连续受理激励，一次暴露 10 笔中 8 笔码错。
#     fe7535f 修正了这组测试的取数点。当前激励另含负数接中点和夹空拍的情况。
#
#   * 判定（任一不满足即 FAIL）：
#       ① RTL 编译成功、仿真退出码 0
#       ② 采集到的轨迹行数 > 0（防空轨迹）
#       ③ oracle 输出必须出现 "RESULT: RECON_STREAM_MATCH"
#       ④ 每个场景 accepted 必须等于 retired（无缺笔/重复退休）
#       ⑤ 所有场景 mismatches 必须为 0
#
# 用法: D3D4_RUNNER=vm-yian bash tools/check_recon_stream.sh
# 变更记录   :
#   2026-11-16  初版。把独立复核的连续输入测试纳入正式入口。
# =============================================================================
set -uo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
RUNNER=${V21_RUNNER:-${D3D4_RUNNER:-}}
WORK=${V21_WORKDIR:-${D3D4_WORKDIR:-}}

run() { if [ -n "$RUNNER" ]; then ssh "$RUNNER" "$1"; else bash -c "$1"; fi; }
SETUP='export PATH=/opt/synopsys/vcs/W-2024.09-SP1/bin:$PATH SNPSLMD_LICENSE_FILE=27080@localhost VCS_HOME=/opt/synopsys/vcs/W-2024.09-SP1'
fail() { echo; echo "RESULT: RECON_STREAM_FAIL -- $1"; exit 1; }

# 运行日志与向量都应来自本次。保留已存在的证据，防止重试时覆盖旧 RTL 的反例。
if [[ -z "$WORK" ]]; then
  WORK=$(run 'mktemp -d /tmp/reconstream.XXXXXXXX') || fail "创建运行目录失败"
elif [[ "$WORK" =~ ^/[a-zA-Z0-9_./-]+$ ]]; then
  run "mkdir -- $WORK" || fail "请提供尚未存在的运行目录"
else
  fail "运行目录须为没有空格的绝对路径"
fi
echo "=== 准备 $WORK ==="
run "mkdir -p $WORK/rtl/v21 $WORK/tb/v21 $WORK/tools" || fail "创建目录失败"
for f in rtl/recip_q60.sv rtl/v21/recip_rnd_v21.sv rtl/v21/recon_v21.sv \
         tb/v21/tb_recon_stream.sv tools/recon_oracle.py; do
  if [ -n "$RUNNER" ]; then scp -q "$ROOT/$f" "$RUNNER:$WORK/$f" || fail "传 $f 失败"
  else cp "$ROOT/$f" "$WORK/$f"; fi
done
echo "  源文件就位"

echo
echo "=== 1. 生成逐拍激励（含负数跨零与空拍）==="
run "cd $WORK && python3 tools/recon_oracle.py generate $WORK/stream.vec" || fail "生成 vec 失败"
n=$(run "wc -l < $WORK/stream.vec" | tr -d ' ')
echo "  vec $n 行"
[ "${n:-0}" -gt 0 ] || fail "vec 为空"

echo
echo "=== 2. 编译 RTL + 连续输入 TB ==="
run "$SETUP; cd $WORK && vcs -full64 -sverilog -timescale=1ns/1ps rtl/recip_q60.sv rtl/v21/recip_rnd_v21.sv rtl/v21/recon_v21.sv tb/v21/tb_recon_stream.sv -o sstr > c.log 2>&1" \
  || fail "编译失败"

echo
echo "=== 3. 仿真 ==="
run "$SETUP; cd $WORK && ./sstr +VEC=$WORK/stream.vec +TRACE=$WORK/trace.csv > run.log 2>&1" \
  || fail "仿真退出码非 0"
run "grep -Fxq 'RECON_S3_LIMITS_PASS checks=6' $WORK/run.log" || fail "截断或错误传播检查未完成"
t=$(run "wc -l < $WORK/trace.csv" | tr -d ' ')
echo "  轨迹 $t 行"
[ "${t:-0}" -gt 1 ] || fail "轨迹为空或只有表头"

echo
echo "=== 4. 独立 oracle 逐笔逐级比对 ==="
res=$(run "cd $WORK && python3 tools/recon_oracle.py examine $WORK/trace.csv $WORK/report.json 2>&1") \
  || { echo "$res"; fail "逐笔对拍失败"; }
echo "$res"

echo
if ! echo "$res" | grep -q "RESULT: RECON_STREAM_MATCH"; then
  fail "oracle 判定不匹配"
fi
if echo "$res" | grep -qE "accepted=\[[^]]*\] retired=\[[^]]*\]" ; then
  # accepted 与 retired 列表必须逐字相同
  bad=$(echo "$res" | python3 -c '
import sys, re
bad = 0
for line in sys.stdin:
    m = re.search(r"accepted=(\[[^]]*\]) retired=(\[[^]]*\])", line)
    if m and m.group(1) != m.group(2):
        bad += 1
print(bad)')
  [ "$bad" = "0" ] || fail "存在 accepted != retired（缺笔或重复退休）"
fi
if echo "$res" | grep -qE "mismatches=[1-9]"; then
  fail "存在 mismatches > 0"
fi

echo
echo "RESULT: RECON_STREAM_PASS（逐笔逐级一致，无缺笔或重复退休）"
exit 0
