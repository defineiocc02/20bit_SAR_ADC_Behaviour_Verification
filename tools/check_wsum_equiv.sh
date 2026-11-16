#!/usr/bin/env bash
# =============================================================================
# 文件名     : check_wsum_equiv.sh
# 版本       : v2.0
# 功能       : wsum_v21 在任意权重下，RTL 与综合网表的逐笔一致性检查。
#
# 设计说明   :
#   * v1.0 的判定缺陷（审查指出）：
#       - 目录创建不全（缺 rtl/v21、tb/v21）
#       - 剔除 X 后可能剩 0 条记录却输出 TOTAL_BAD 0
#       - 缺笔/重复/空交集/编译失败不判失败
#     全部修掉。
#
#   * v2.0 的硬门禁（任一不满足即 FAIL）：
#       ① RTL 与门级都必须编译成功、仿真退出码为 0
#       ② 两侧都必须打出 WSUM_SCAN_DONE 且 errors=0
#       ③ 双方交易号集合必须**完全相同**（缺笔/重复都 FAIL）
#       ④ 记录数必须等于期望笔数（EXPECT_TXN）
#       ⑤ 输出不得含 X/Z
#       ⑥ 逐笔逐字段（G/Wp/Wm）与对侧一致，且与各自参考值一致
#
#   * 位序映射由 gen_wsum_gate_tb.py v2.0 从**实际网表端口名**生成并自检，
#     不再靠猜（v1.0 曾把 [0][0] 放在最低位，行列接反，误判成"综合折叠"）。
#
# 用法: D3D4_RUNNER=vm-yian bash tools/check_wsum_equiv.sh [netlist]
# 变更记录   :
#   2026-11-16  v1.0 初版。
#   2026-11-16  v2.0 修判定逻辑：补目录、加硬门禁、禁止剔除后为空的假通过。
# =============================================================================
set -uo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
RUNNER=${D3D4_RUNNER:-}
WORK=${D3D4_WORKDIR:-/tmp/wsumchk}
NET=${1:-${D3D4_NETLIST:-/tmp/dcwork4/adc_digital_ip_v21_syn.v}}
LIB=/home/yian/PDK/tsmc28/digital_technology/TSMCHOME/digital/Front_End/verilog/tcbn28hpcplusbwp7t40p140_110a/tcbn28hpcplusbwp7t40p140.v
EXPECT_TXN=364

run() { if [ -n "$RUNNER" ]; then ssh "$RUNNER" "$1"; else bash -c "$1"; fi; }
SETUP='export PATH=/opt/synopsys/vcs/W-2024.09-SP1/bin:$PATH SNPSLMD_LICENSE_FILE=27080@localhost VCS_HOME=/opt/synopsys/vcs/W-2024.09-SP1'
fail() { echo; echo "RESULT: WSUM_EQUIV_FAIL -- $1"; exit 1; }

echo "=== 准备 $WORK ==="
# 目录必须齐全（v1.0 缺 rtl/v21、tb/v21 导致 scp 失败）
run "rm -rf $WORK && mkdir -p $WORK/rtl/v21 $WORK/tb/v21 $WORK/tools" || fail "创建目录失败"
for f in rtl/v21/wsum_v21.sv tb/v21/tb_wsum_scan.sv tools/gen_wsum_gate_tb.py; do
  if [ -n "$RUNNER" ]; then scp -q "$ROOT/$f" "$RUNNER:$WORK/$f" || fail "传 $f 失败"
  else cp "$ROOT/$f" "$WORK/$f"; fi
done
echo "  RTL/TB/生成器 已就位"

echo
echo "=== 1. 从网表抽出 wsum_v21 及其子模块 ==="
run "cd $WORK && python3 - <<'PYEOF'
import re
src = open('$NET').read()
keep = [m.group(0) for m in re.finditer(r'^module (wsum_v21\w*) \(.*?^endmodule', src, re.S|re.M)]
if not keep:
    raise SystemExit('FAIL: 网表里找不到 wsum_v21')
open('$WORK/wsum_only.v','w').write('\n\n'.join(keep) + '\n')
print(f'抽出 {len(keep)} 个模块')
PYEOF" || fail "抽模块失败"

echo
echo "=== 2. 生成门级 TB（位序从网表端口名生成并自检）==="
gen=$(run "cd $WORK && python3 tools/gen_wsum_gate_tb.py wsum_only.v tb/v21/tb_wsum_gate.sv 2>&1")
echo "$gen" | tail -4
if ! echo "$gen" | grep -q "OK  w_main"; then fail "位序自检未通过（见上）"; fi

echo
echo "=== 3. RTL 侧 ==="
run "$SETUP; cd $WORK && vcs -full64 -sverilog -timescale=1ns/1ps rtl/v21/wsum_v21.sv tb/v21/tb_wsum_scan.sv -o srtl > c_rtl.log 2>&1" \
  || fail "RTL 编译失败"
run "$SETUP; cd $WORK && ./srtl +OUT=$WORK/ws_rtl.txt > run_rtl.log 2>&1" || fail "RTL 仿真退出码非 0"
grep -q "WSUM_SCAN_DONE" <(run "cat $WORK/run_rtl.log") || fail "RTL 未打出 WSUM_SCAN_DONE"
run "grep 'WSUM_SCAN_DONE' $WORK/run_rtl.log"
if ! run "grep -q 'errors=0' $WORK/run_rtl.log"; then
  run "grep 'ERROR' $WORK/run_rtl.log | head -5"; fail "RTL 侧自检报错"
fi

echo
echo "=== 4. 网表侧 ==="
run "$SETUP; cd $WORK && vcs -full64 -sverilog -timescale=1ns/1ps $LIB wsum_only.v tb/v21/tb_wsum_gate.sv -o sgate > c_gate.log 2>&1" \
  || fail "门级编译失败"
run "$SETUP; cd $WORK && ./sgate +OUT=$WORK/ws_gate.txt > run_gate.log 2>&1" || fail "门级仿真退出码非 0"
grep -q "WSUM_SCAN_DONE" <(run "cat $WORK/run_gate.log") || fail "门级未打出 WSUM_SCAN_DONE"
run "grep 'WSUM_SCAN_DONE' $WORK/run_gate.log"
if ! run "grep -q 'errors=0' $WORK/run_gate.log"; then
  run "grep 'ERROR' $WORK/run_gate.log | head -5"; fail "门级侧自检报错"
fi

echo
echo "=== 5. 逐笔逐字段完整比对（硬门禁）==="
res=$(run "cd $WORK && python3 - $EXPECT_TXN <<'PYEOF'
import sys
EXPECT=int(sys.argv[1])
import sys
def load(fn):
    d={}
    for l in open(fn):
        p=l.split()
        if not p or not p[0].startswith('#'): continue
        idx=int(p[0][1:])
        if idx in d:
            print(f'FAIL: 交易号 {idx} 重复'); sys.exit(1)
        f=dict(x.split('=') for x in p if '=' in x); f['tag']=p[1]
        d[idx]=f
    return d
r=load('ws_rtl.txt'); g=load('ws_gate.txt')
print(f'RTL {len(r)} 笔 / 门级 {len(g)} 笔（期望 {EXPECT}）')
if len(r)!=EXPECT or len(g)!=EXPECT:
    print(f'FAIL: 笔数不等于 {EXPECT}'); sys.exit(1)
if set(r)!=set(g):
    print(f'FAIL: 交易号集合不同，RTL 缺 {sorted(set(g)-set(r))[:5]}，门级缺 {sorted(set(r)-set(g))[:5]}')
    sys.exit(1)
badX=badRef=badCross=0
for k in sorted(r):
    for f in ('G','Wp','Wm'):
        if 'X' in g[k][f] or 'x' in g[k][f] or 'Z' in g[k][f]:
            badX+=1
        if g[k][f]!=g[k]['r'+f] or r[k][f]!=r[k]['r'+f]:
            badRef+=1
        if r[k][f]!=g[k][f]:
            badCross+=1
print(f'含 X/Z 字段 {badX}')
print(f'与参考值不符 {badRef}')
print(f'RTL vs 门级 不符 {badCross}')
if badX or badRef or badCross:
    print('FAIL: 存在不符'); sys.exit(1)
print(f'OK: {len(r)} 笔 x 3 字段全部一致且与参考值一致')
PYEOF")
echo "$res"
# **严格判定**：出现 FAIL、或没出现 OK 行，都算失败。
# v2.0 早期这里只 grep "^FAIL"，脚本自身报错时仍输出 PASS —— 已修。
if echo "$res" | grep -q "FAIL"; then fail "比对未通过"; fi
if ! echo "$res" | grep -q "OK: "; then fail "未取得比对结论（脚本异常）"; fi

echo
echo "RESULT: WSUM_EQUIV_PASS（$EXPECT_TXN 笔，任意权重下 RTL == 网表，且双方与参考值一致）"
exit 0
