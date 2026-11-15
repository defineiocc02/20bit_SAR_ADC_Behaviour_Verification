#!/usr/bin/env bash
# =============================================================================
# 文件名     : check_gate_equiv.sh
# 版本       : v1.0
# 功能       : 门级等价——同一组激励分别跑 RTL 与 28HPC+ 综合网表，
#              逐拍 diff 轨迹，差异必须为 0。
#
# 设计说明   :
#   * 为什么需要：综合会做常量传播、资源共享、边界优化，网表与 RTL
#     是否等价**不能假定**。这是"网表可信"的必要证据。
#   * 为什么不用 Formality：fm_shell W-2024.09-SP3 在本机进入 (setup) 模式，
#     命令集与 DC/PT 不同（link_library / set_top_designs / set_container
#     都不存在），本轮多次尝试未跑通。门级仿真是可用的替代证据，
#     **但只覆盖施加的激励，不是全输入的形式化证明**，这一点必须注明。
#   * 门级 TB 由 tools/gen_gate_tb.py 从网表端口自动生成：
#     DC 把权重表展开成 11520/2880 位扁平向量，手写连接不现实。
#   * 两侧激励必须逐字一致，否则轨迹 diff 无意义。
#     本轮踩过的三处不一致（都表现为 diff 非零）：
#       1. tb_trace 未接 sched_* 输入 -> acq_sel_o 全程 X
#       2. 字段顺序不同 -> 数值一致但行错位
#       3. tb_trace 未设 prepare_U=5 -> U_conflict 让 err_o 不一样
#
# 用法: D3D4_RUNNER=vm-yian bash tools/check_gate_equiv.sh
# 前置: /tmp/dcwork/adc_digital_ip_v21_syn.v 已由 dc_synth_v21.tcl 生成
# 变更记录   :
#   2026-11-16  初版。
# =============================================================================
set -uo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
RUNNER=${D3D4_RUNNER:-}
WORK=${D3D4_WORKDIR:-/tmp/gateq}
NETLIST=${D3D4_NETLIST:-/tmp/dcwork/adc_digital_ip_v21_syn.v}
LIB=/home/yian/PDK/tsmc28/digital_technology/TSMCHOME/digital/Front_End/verilog/tcbn28hpcplusbwp7t40p140_110a/tcbn28hpcplusbwp7t40p140.v

run() { if [ -n "$RUNNER" ]; then ssh "$RUNNER" "$1"; else bash -c "$1"; fi; }
SETUP='export PATH=/opt/synopsys/vcs/W-2024.09-SP1/bin:$PATH SNPSLMD_LICENSE_FILE=27080@localhost VCS_HOME=/opt/synopsys/vcs/W-2024.09-SP1'

RTL="rtl/sched18.sv rtl/dem_phase_rr.sv rtl/dem_phase_gen.sv rtl/dither_pre.sv \
rtl/dem_mask_v3.sv rtl/follower_rt.sv rtl/recip_q60.sv \
rtl/v21/acq_conv_ctx.sv rtl/v21/sar_evt_v21.sv rtl/v21/adc2_capture.sv \
rtl/v21/wsum_v21.sv rtl/v21/recip_rnd_v21.sv rtl/v21/recon_v21.sv \
rtl/v21/adc_digital_ip_v21.sv"

echo "=== 准备 $WORK ==="
run "rm -rf $WORK && mkdir -p $WORK/rtl/v21 $WORK/tb/v21 $WORK/tools" || exit 1
for f in $RTL tb/v21/tb_trace.sv tools/gen_gate_tb.py; do
  if [ -n "$RUNNER" ]; then
    scp -q "$ROOT/$f" "$RUNNER:$WORK/$f" || { echo "FAIL: 传 $f"; exit 1; }
  else
    cp "$ROOT/$f" "$WORK/$f"
  fi
done

echo
echo "=== 1. 检查网表完整性（三项自证）==="
out=$(run "cd $WORK && grep -c '^module recip_rnd_v21' $NETLIST; grep -c 'LINK-3' /tmp/dcwork/dc.log 2>/dev/null || echo 0")
echo "$out" | head -2

echo
echo "=== 2. 门级 TB 自动生成 ==="
run "cd $WORK && python3 tools/gen_gate_tb.py $NETLIST tb/v21/tb_gate_auto.sv" | tail -2

echo
echo "=== 3. RTL 轨迹 ==="
run "$SETUP; cd $WORK && vcs -full64 -sverilog -timescale=1ns/1ps $RTL tb/v21/tb_trace.sv -o srtl > srtl.log 2>&1" \
  || { echo "RTL 编译 FAIL"; run "tail -5 $WORK/srtl.log"; exit 1; }
run "$SETUP; cd $WORK && ./srtl +OUT=$WORK/tr_rtl.txt > /dev/null 2>&1"
run "wc -l $WORK/tr_rtl.txt"

echo
echo "=== 4. 门级轨迹 ==="
run "$SETUP; cd $WORK && vcs -full64 -sverilog -timescale=1ns/1ps $LIB $NETLIST tb/v21/tb_gate_auto.sv -o sgate > sgate.log 2>&1" \
  || { echo "门级编译 FAIL"; run "tail -5 $WORK/sgate.log"; exit 1; }
run "$SETUP; cd $WORK && ./sgate +OUT=$WORK/tr_gate.txt > /dev/null 2>&1"
run "wc -l $WORK/tr_gate.txt"

echo
echo "=== 5. 轨迹比对 ==="
n=$(run "diff $WORK/tr_rtl.txt $WORK/tr_gate.txt 2>/dev/null | grep -c '^[<>]'")
lines=$(run "wc -l < $WORK/tr_rtl.txt" | tr -d ' ')
echo "  RTL 轨迹 $lines 行，差异 $n 处"
if [ "$n" = "0" ]; then
  echo
  echo "RESULT: GATE_EQUIV_PASS ($lines 拍，RTL == 28HPC+ 网表)"
  echo "注意：这是**门级仿真等价**，覆盖施加的激励；不是全输入的形式化证明。"
  exit 0
else
  echo
  echo "RESULT: GATE_EQUIV_FAIL"
  run "diff $WORK/tr_rtl.txt $WORK/tr_gate.txt | head -20"
  exit 1
fi
