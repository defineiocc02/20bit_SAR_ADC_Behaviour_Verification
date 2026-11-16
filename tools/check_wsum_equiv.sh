#!/usr/bin/env bash
# =============================================================================
# 文件名     : check_wsum_equiv.sh
# 版本       : v1.0
# 功能       : wsum_v21 在**任意权重取值**下，RTL 与综合网表的逐笔一致性检查。
#
# 设计说明   :
#   * 为什么需要独立入口：
#     tb_i4b_e2e / check_gate_e2e 都用**名义固定权重**（主列 268435456 等）。
#     那样恰好不暴露"综合把权重行折叠掉"这类缺陷 —— 实测网表里
#     只有 w_main_tbl[17][*] 参与求和，[0..16] 全被折掉。
#     本脚本用 256 组随机权重（低 4 位强制非零）逐笔比对 G/Wp/Wm。
#
#   * 两侧激励同源：tb_wsum_scan.sv 的激励段被 gen_wsum_gate_tb.py
#     原样复用，只替换端口连接层（数组 -> 网表的扁平向量）。
#     所以"两侧激励逐字一致"是结构保证。
#
#   * 判定：按 (ids, pre, post) 精确配对，剔除含 X 的记录后，
#     G / Wp / Wm 三者都必须 0 处不符。任一不符即 FAIL。
#
# 用法: D3D4_RUNNER=vm-yian bash tools/check_wsum_equiv.sh [netlist]
# 变更记录   :
#   2026-11-16  初版。为验证 wsum 片号钳位修复而写。
# =============================================================================
set -uo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
RUNNER=${D3D4_RUNNER:-}
WORK=${D3D4_WORKDIR:-/tmp/wsumchk}
NET=${1:-${D3D4_NETLIST:-/tmp/dcwork5/adc_digital_ip_v21_syn.v}}
LIB=/home/yian/PDK/tsmc28/digital_technology/TSMCHOME/digital/Front_End/verilog/tcbn28hpcplusbwp7t40p140_110a/tcbn28hpcplusbwp7t40p140.v

run() { if [ -n "$RUNNER" ]; then ssh "$RUNNER" "$1"; else bash -c "$1"; fi; }
SETUP='export PATH=/opt/synopsys/vcs/W-2024.09-SP1/bin:$PATH SNPSLMD_LICENSE_FILE=27080@localhost VCS_HOME=/opt/synopsys/vcs/W-2024.09-SP1'

echo "=== 准备 $WORK ==="
run "rm -rf $WORK && mkdir -p $WORK/rtl $WORK/tb $WORK/tools" || exit 1
for f in rtl/v21/wsum_v21.sv tb/v21/tb_wsum_scan.sv tools/gen_wsum_gate_tb.py; do
  if [ -n "$RUNNER" ]; then scp -q "$ROOT/$f" "$RUNNER:$WORK/$f" || exit 1
  else cp "$ROOT/$f" "$WORK/$f"; fi
done

echo
echo "=== 1. 从网表抽出 wsum_v21 及其子模块 ==="
run "cd $WORK && python3 - <<'PYEOF'
import re
src = open('$NET').read()
keep = [m.group(0) for m in re.finditer(r'^module (wsum_v21\w*) \(.*?^endmodule', src, re.S|re.M)]
open('$WORK/wsum_only.v','w').write('\n\n'.join(keep) + '\n')
print(f'抽出 {len(keep)} 个模块')
PYEOF"

echo
echo "=== 2. 生成门级 TB（扁平权重表端口适配）==="
run "cd $WORK && python3 tools/gen_wsum_gate_tb.py wsum_only.v tb/tb_wsum_gate.sv" | tail -2

echo
echo "=== 3. RTL 侧 ==="
run "$SETUP; cd $WORK && vcs -full64 -sverilog -timescale=1ns/1ps rtl/v21/wsum_v21.sv tb/tb_wsum_scan.sv -o srtl > c_rtl.log 2>&1" \
  || { echo "RTL 编译 FAIL"; run "tail -5 $WORK/c_rtl.log"; exit 1; }
run "$SETUP; cd $WORK && ./srtl +OUT=$WORK/wsum_rtl.txt 2>&1 | grep WSUM_SCAN_DONE"

echo
echo "=== 4. 网表侧 ==="
run "$SETUP; cd $WORK && vcs -full64 -sverilog -timescale=1ns/1ps $LIB wsum_only.v tb/tb_wsum_gate.sv -o sgate > c_gate.log 2>&1" \
  || { echo "门级编译 FAIL"; run "tail -5 $WORK/c_gate.log"; exit 1; }
run "$SETUP; cd $WORK && ./sgate +OUT=$WORK/wsum_gate.txt 2>&1 | grep WSUM_SCAN_DONE"

echo
echo "=== 5. 按输入精确配对比对 ==="
res=$(run "cd $WORK && python3 - <<'PYEOF'
rtl={}
for l in open('wsum_rtl.txt'):
    f=dict(x.split('=') for x in l.split() if '=' in x)
    rtl[(f['ids'],f['pre'],f['post'])]=f
gate={}
for l in open('wsum_gate.txt'):
    f=dict(x.split('=') for x in l.split() if '=' in x)
    gate[(f['ids'],f['pre'],f['post'])]=f
print(f'RTL {len(rtl)} 条 / 门级 {len(gate)} 条')
common=set(rtl)&set(gate)
clean=[k for k in common if 'X' not in gate[k]['G']+gate[k]['Wp']+gate[k]['Wm']]
print(f'共同 {len(common)}，无 X {len(clean)}')
tot=0
for fld in ('G','Wp','Wm'):
    bad=sum(1 for k in clean if rtl[k][fld]!=gate[k][fld])
    tot+=bad
    print(f'  {fld} 不符 {bad}')
for k in [k for k in clean if rtl[k]['G']!=gate[k]['G']][:3]:
    print(f'  ids={k[0]} RTL G={rtl[k][\"G\"]} GATE G={gate[k][\"G\"]}')
print(f'TOTAL_BAD {tot}')
PYEOF")
echo "$res"

if echo "$res" | grep -q "TOTAL_BAD 0"; then
  echo
  echo "RESULT: WSUM_EQUIV_PASS（任意权重下 RTL == 网表）"
  exit 0
else
  echo
  echo "RESULT: WSUM_EQUIV_FAIL"
  exit 1
fi
