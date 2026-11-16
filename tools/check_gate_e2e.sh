#!/usr/bin/env bash
# =============================================================================
# 文件名     : check_gate_e2e.sh
# 版本       : v2.0
# 功能       : 端到端门级等价的一键入口 —— 四项网表自证 → RTL 轨迹 →
#              门级轨迹 → 逐拍 diff → 交易数检查 → 三态退出码。
# 设计说明   :
#   * 为什么需要这个脚本：旧的 check_gate_equiv.sh 用的是 tb_trace（单笔烟测），
#     1464 行日志里实际只有 61 个观察沿，根本没走完
#     "逐事件 → 余差捕获 → ADC2 返回 → 20 位输出"，不能作为整链证据。
#     本脚本改跑 tb_i4b_e2e（两笔 A/B 完整事务），并对交易数做硬门禁：
#     accepted / captured / output 三项各 >= 2，否则即使两边轨迹一致也判 FAIL
#     —— "两侧都没输出却判通过"是这个项目历史上真实发生过的假结论。
#
#   * 三态退出码（供上游流水线区分"真失败"与"环境/编译问题"）：
#       0  PASS      ：四项自证过 + 两侧交易数 >=2 + 轨迹零差异 + 两侧断言零错
#       1  FAIL      ：跑起来了，但存在差异/断言失败/交易数不足（**证据性失败**）
#       2  ERROR     ：环境或编译问题（找不到网表、VCS 编译失败等），结论无效
#
#   * 两侧激励为什么能保证逐字一致：
#     激励与断言全在 tb/v21/tb_e2e_decl.svh + tb_e2e_body.svh，两侧 TB 只做
#     端口绑定（RTL 数组端口 / 网表 11520+2880 位扁平向量）。
#     门级 TB 由 tools/gen_gate_tb.py 从网表端口生成，端口自检不过就直接退出。
#
#   * **已知缺陷（本脚本会照实暴露，不会掩盖）**：
#     rtl/recip_q60.sv 的 64 项种子 LUT 只用 `initial begin` 初始化，
#     DC 综合时忽略该块（dc.log 里 VER-281），于是网表里 recip_q60 的
#     inv_q60[26:0] 全部被赋成悬空自引用网、无单元驱动，倒数恒为 0。
#     实测后果：A 笔 out_code = 0x80000（RTL 为 0xFFF00），B 笔不受影响。
#     该缺陷不是本脚本引入的，且修它必须改 rtl/（本轮授权范围外）。
#     脚本会把这条作为 FAIL 的定位信息打印出来，便于复审时直接对应。
#
# 用法:
#   D3D4_RUNNER=vm-yian bash tools/check_gate_e2e.sh
#   不给 RUNNER 则在本地跑（本机需有 vcs + 网表 + 单元库）。
#   可选环境变量：
#     D3D4_WORKDIR  : 远端/本地工作目录（默认 /tmp/gate2）
#     D3D4_NETLIST  : 综合网表路径（默认 /tmp/dcwork/adc_digital_ip_v21_syn.v）
#     D3D4_DCLOG    : DC 日志路径（默认 /tmp/dcwork/dc.log）
#     D3D4_SVF      : SVF 路径（默认 /tmp/dcwork/adc_digital_ip_v21.svf）
#
# 变更记录   :
#   2026-11-16  v1.0 初版（tb_trace 单笔烟测 + 两项目证，证据不足）。
#   2026-11-16  v2.0 改用 tb_i4b_e2e 两笔完整事务 + 共享激励层；
#                    四项目证齐全；新增交易数门禁与三态退出码；
#                    新增 recip_q60 LUT 缺陷的定向取证。
# =============================================================================
set -uo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
RUNNER=${D3D4_RUNNER:-}
WORK=${D3D4_WORKDIR:-/tmp/gate2}
NETLIST=${D3D4_NETLIST:-/tmp/dcwork/adc_digital_ip_v21_syn.v}
DCLOG=${D3D4_DCLOG:-/tmp/dcwork/dc.log}
SVF=${D3D4_SVF:-/tmp/dcwork/adc_digital_ip_v21.svf}
LIB=/home/yian/PDK/tsmc28/digital_technology/TSMCHOME/digital/Front_End/verilog/tcbn28hpcplusbwp7t40p140_110a/tcbn28hpcplusbwp7t40p140.v

# 生产 RTL（不含节点宏：节点宏属于仿真环境，两侧都要编译）
RTL_FILES="rtl/sched18.sv rtl/dem_phase_rr.sv rtl/dem_phase_gen.sv rtl/dither_pre.sv \
rtl/dem_mask_v3.sv rtl/follower_rt.sv rtl/recip_q60.sv \
rtl/v21/acq_conv_ctx.sv rtl/v21/sar_evt_v21.sv rtl/v21/adc2_capture.sv \
rtl/v21/wsum_v21.sv rtl/v21/recip_rnd_v21.sv rtl/v21/recon_v21.sv \
rtl/v21/adc_digital_ip_v21.sv rtl/v21/adc2_node_macro.sv"

VCS_SETUP='export PATH=/opt/synopsys/vcs/W-2024.09-SP1/bin:$PATH \
SNPSLMD_LICENSE_FILE=27080@localhost VCS_HOME=/opt/synopsys/vcs/W-2024.09-SP1'

run() { if [ -n "$RUNNER" ]; then ssh "$RUNNER" "$1"; else bash -c "$1"; fi; }
say() { echo; echo "=== $* ==="; }

# ---------------------------------------------------------------- 0. 传文件
say "0. 准备远端工作目录 $WORK"
run "rm -rf $WORK && mkdir -p $WORK/rtl/v21 $WORK/tb/v21 $WORK/tools" || exit 2
for f in $RTL_FILES tb/v21/tb_i4b_e2e.sv tb/v21/tb_e2e_decl.svh \
         tb/v21/tb_e2e_body.svh tools/gen_gate_tb.py; do
  if [ -n "$RUNNER" ]; then
    scp -q "$ROOT/$f" "$RUNNER:$WORK/$f" || { echo "ERROR: 传 $f 失败"; exit 2; }
  else
    mkdir -p "$WORK/$(dirname "$f")" && cp "$ROOT/$f" "$WORK/$f" || exit 2
  fi
done
echo "  已传 $(echo $RTL_FILES | wc -w) 个 RTL + 3 个 TB/工具文件"

# ---------------------------------------------------------------- 1. 四项目证
say "1. 网表四项自证"
# 注意：这四项是历史上两次"报了假面积/假等价"的直接教训，缺一不可。
#   ① recon_v21 存在   ② recip_rnd_v21 存在   ③ 无 VER-954/LINK-3/LINK-5
#   ④ DW_div 存在（recon 的除法器确实被综合进来了，不是被当黑盒）
# 每项单独取，避免多层引号嵌套被 shell 吃掉（踩过：整段 printf 传过去后
# 远端只执行了最后一条 grep，前三项都是空串，误判成"缺 DW_div"）。
n_recon=$(run "grep -c '^module recon_v21 ' '$NETLIST' 2>/dev/null; true" | tr -d ' \n')
n_recip=$(run "grep -c '^module recip_rnd_v21 ' '$NETLIST' 2>/dev/null; true" | tr -d ' \n')
n_ver=$(run   "grep -cE 'VER-954|LINK-3|LINK-5' '$DCLOG' 2>/dev/null; true" | tr -d ' \n')
n_div=$(run   "grep -c 'DW_div' '$NETLIST' 2>/dev/null; true" | tr -d ' \n')
n_recon=${n_recon:-0}; n_recip=${n_recip:-0}; n_ver=${n_ver:-0}; n_div=${n_div:-0}
echo "  ① recon_v21 模块数      = $n_recon   (须 > 0)"
echo "  ② recip_rnd_v21 模块数  = $n_recip   (须 > 0)"
echo "  ③ VER-954|LINK-3|LINK-5 = $n_ver     (须 = 0)"
echo "  ④ DW_div 出现次数       = $n_div     (须 > 0)"
case "${n_recon:-0}" in ''|0|*[!0-9]*) echo "  ERROR: 网表不可用或缺 recon_v21"; exit 2;; esac
if [ "${n_recip:-0}" = "0" ]; then echo "FAIL: 网表缺 recip_rnd_v21"; exit 1; fi
if [ "${n_ver:-1}" != "0" ]; then echo "FAIL: DC 日志含 VER-954/LINK-3/LINK-5"; exit 1; fi
if [ "${n_div:-0}" = "0" ]; then echo "FAIL: 网表缺 DW_div"; exit 1; fi
echo "  四项自证全部通过"

say "1b. 网表与 SVF 指纹"
run "sha256sum $NETLIST $SVF 2>/dev/null" | sed 's/^/  /'

# ---------------------------------------------------------------- 2. 生成门级 TB
say "2. 从网表端口生成门级 TB（端口自检不过即退出）"
gen=$(run "cd $WORK && python3 tools/gen_gate_tb.py --netlist $NETLIST \
        --out tb/v21/tb_gate_auto.sv --common tb_e2e_decl.svh 2>&1")
echo "$gen" | sed 's/^/  /'
echo "$gen" | grep -q "端口自检 PASS" || { echo "ERROR: 端口自检未通过"; exit 2; }

# ---------------------------------------------------------------- 3. RTL 轨迹
say "3. RTL 侧：编译 + 跑两笔完整事务"
run "$VCS_SETUP; cd $WORK && vcs -full64 -sverilog -timescale=1ns/1ps +incdir+tb/v21 \
     $RTL_FILES tb/v21/tb_i4b_e2e.sv -o srtl > srtl.log 2>&1"
rc=$?
if [ $rc -ne 0 ]; then
  echo "ERROR: RTL 编译失败（rc=$rc）"; run "grep -E '^Error' $WORK/srtl.log | head -10"
  exit 2
fi
run "cd $WORK && ./srtl +TRACE=$WORK/tr_rtl.txt > run_rtl.log 2>&1"
rtl_rc=$?
rtl_tx=$(run "grep -m1 '^TRANSACTIONS' $WORK/run_rtl.log")
rtl_res=$(run "grep -m1 '^RESULT:' $WORK/run_rtl.log")
echo "  退出码 $rtl_rc"
echo "  $rtl_tx"
echo "  $rtl_res"

# ---------------------------------------------------------------- 4. 门级轨迹
say "4. 门级侧：编译（单元库 + 网表）+ 跑同一份激励"
# 节点模型 adc2_node_macro 是**仿真环境**的一部分，不属于 DUT，门级侧同样编译 RTL 版。
run "$VCS_SETUP; cd $WORK && vcs -full64 -sverilog -timescale=1ns/1ps +incdir+tb/v21 \
     $LIB $NETLIST rtl/v21/adc2_node_macro.sv tb/v21/tb_gate_auto.sv -o sgate > sgate.log 2>&1"
rc=$?
if [ $rc -ne 0 ]; then
  echo "ERROR: 门级编译失败（rc=$rc）"; run "grep -E '^Error' $WORK/sgate.log | head -10"
  exit 2
fi
run "cd $WORK && ./sgate +TRACE=$WORK/tr_gate.txt > run_gate.log 2>&1"
gate_rc=$?
gate_tx=$(run "grep -m1 '^TRANSACTIONS' $WORK/run_gate.log")
gate_res=$(run "grep -m1 '^RESULT:' $WORK/run_gate.log")
echo "  退出码 $gate_rc"
echo "  $gate_tx"
echo "  $gate_res"

# ---------------------------------------------------------------- 5. 交易数门禁
say "5. 交易数门禁（三项各须 >= 2）"
# 解析 "TRANSACTIONS accepted=N captured=N output=N"
parse_tx() { echo "$1" | sed -n 's/.*accepted=\([0-9]*\) captured=\([0-9]*\) output=\([0-9]*\).*/\1 \2 \3/p'; }
read -r ra rc_x ro <<<"$(parse_tx "$rtl_tx")"
read -r ga gc go <<<"$(parse_tx "$gate_tx")"
echo "  RTL : accepted=${ra:-?} captured=${rc_x:-?} output=${ro:-?}"
echo "  门级: accepted=${ga:-?} captured=${gc:-?} output=${go:-?}"
tx_ok=1
for v in "${ra:-0}" "${rc_x:-0}" "${ro:-0}" "${ga:-0}" "${gc:-0}" "${go:-0}"; do
  [ "${v:-0}" -ge 2 ] 2>/dev/null || tx_ok=0
done
if [ $tx_ok -ne 1 ]; then
  echo "  FAIL: 交易数不足 —— 激励没走完整链，此时"轨迹一致"不算通过"
  exit 1
fi
echo "  交易数全部 >= 2，激励有效"

# ---------------------------------------------------------------- 6. 轨迹 diff
say "6. 逐拍轨迹比对"
lines_rtl=$(run "wc -l < $WORK/tr_rtl.txt 2>/dev/null" | tr -d ' ')
lines_gate=$(run "wc -l < $WORK/tr_gate.txt 2>/dev/null" | tr -d ' ')
n_diff=$(run "diff $WORK/tr_rtl.txt $WORK/tr_gate.txt 2>/dev/null | grep -c '^[<>]'" | tr -d ' ')
echo "  RTL 轨迹 ${lines_rtl:-0} 行 / 门级 ${lines_gate:-0} 行"
echo "  差异 ${n_diff:-0} 处"
# 字段级统计：直接告诉人"是哪个字段不一样"，不必逐行读 diff
if [ "${n_diff:-0}" != "0" ]; then
  echo "  差异字段分布："
  run "diff $WORK/tr_rtl.txt $WORK/tr_gate.txt | grep '^[<>]' | \
       awk '{print \$1, \$2}' | sort | uniq -c | sort -rn" | sed 's/^/    /'
  echo "  前 12 条差异："
  run "diff $WORK/tr_rtl.txt $WORK/tr_gate.txt | head -24" | sed 's/^/    /'
fi

# ---------------------------------------------------------------- 7. 判定
say "7. 判定"
rtl_ok=0; gate_ok=0
echo "$rtl_res"  | grep -q "RESULT: PASS" && rtl_ok=1
echo "$gate_res" | grep -q "RESULT: PASS" && gate_ok=1

if [ "${n_diff:-1}" = "0" ] && [ $rtl_ok -eq 1 ] && [ $gate_ok -eq 1 ]; then
  echo "RESULT: GATE_E2E_PASS"
  echo "  RTL 与 28HPC+ 网表在 ${lines_rtl} 拍轨迹上逐字段一致，两笔交易（A/B）均走完整链。"
  echo "  注意：这是**门级仿真等价**，覆盖施加的两笔激励，不是全输入的形式化证明。"
  exit 0
fi

echo "RESULT: GATE_E2E_FAIL"
[ $rtl_ok  -eq 1 ] || echo "  - RTL 侧断言未通过（见 $WORK/run_rtl.log）"
[ $gate_ok -eq 1 ] || echo "  - 门级侧断言未通过（见 $WORK/run_gate.log）"
[ "${n_diff:-1}" = "0" ] || echo "  - 轨迹存在 ${n_diff} 处差异"

# 定向取证：如果差异只在 out_code 且只在 A 笔，指向 recip_q60 的 LUT 缺陷
if [ "${n_diff:-0}" != "0" ] && run "diff $WORK/tr_rtl.txt $WORK/tr_gate.txt | grep '^[<>]' | grep -qv '^[<>] out_code'"; then
  echo "  - 差异不限于 out_code 字段，需要逐条定位"
else
  echo
  echo "  定位提示：差异集中在 out_code，且最可能是 recip_q60 的 LUT 缺陷。"
  echo "  rtl/recip_q60.sv 的 64 项种子表只用 initial begin 初始化，"
  echo "  DC 综合时忽略该块（dc.log 里 VER-281），网表里 inv_q60 全是悬空网。"
  echo "  复现命令（隔离模块直接对比）："
  echo "    awk '/^module recip_q60 /,/^endmodule/' $NETLIST > /tmp/recip_only.v"
  echo "    # RTL  : inv=33554431（G=2^35，正确）"
  echo "    # 网表 : inv=0        （LUT 未初始化，倒数恒 0）"
fi
echo
echo "日志：$WORK/run_rtl.log  $WORK/run_gate.log  $WORK/tr_rtl.txt  $WORK/tr_gate.txt"
exit 1
