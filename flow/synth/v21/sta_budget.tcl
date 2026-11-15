# =============================================================================
# 文件名     : sta_budget.tcl
# 版本       : v3.0
# 功能       : 按方案要求的三类时间预算分组报告 28HPC+ 综合网表时序。
#
# 设计说明   :
#   * 方案 I5 要求逐项报告：
#       ① 旧组释放 -> 下一组完成采集
#       ② 每次已决结果 -> 物理 RDAC 开关生效
#       ③ 末次生效 -> 经参考、RA、安静窗 -> ADC2 捕获
#
#   * v3.0 的关键做法：**用 -through 按中间组合节点切分**。
#     v2.0 用 `-from/-to` 直接取，因 DC ungroup 后层次名丢失、
#     且起终点共享寄存器族，全部返回 N/A。
#     -through 的好处是：只要给定一个必经的中间节点（例如
#     `conv_q_reg` 的输出），就能把跨多级寄存器的逻辑阶段切出来。
#
#   * 网表实测可用的寄存器族（ungroup 后不带层次前缀）：
#       acq_*_q_reg / conv_*_q_reg / exp_evt_q_reg / final_post_q_reg
#       i_q60_reg / inv_q60_reg / code_o_reg / quiet_cnt_q_reg ...
#
#   * 换算：到达时间 = 周期 - slack，
#     所以 `25 - slack` 是该阶段从发起到采样的最大允许延迟。
# 变更记录   :
#   2026-11-16  v1.0 按层次名分组（ungroup 后全 N/A）。
#   2026-11-16  v2.0 改按功能信号名（①② 仍 N/A，起终点共享寄存器）。
#   2026-11-16  v3.0 改 -through 切分。
# =============================================================================

set DESIGN adc_digital_ip_v21
set PDK    /home/yian/PDK/tsmc28/digital_technology
set DB     $PDK/TSMCHOME/digital/Front_End/timing_power_noise/CCS/tcbn28hpcplusbwp7t40p140_180a/tcbn28hpcplusbwp7t40p140ssg0p81vm40c_ccs.db
set PERIOD 25.0

set_app_var search_path [list . /tmp/ip /tmp/ip/rtl /tmp/ip/rtl/v21]
set_app_var target_library [list $DB]
set_app_var link_library   [list "*" $DB]

read_verilog -netlist /tmp/dcwork/${DESIGN}_syn.v
current_design $DESIGN
link

create_clock -name clk -period $PERIOD [get_ports clk]
set_input_delay 1.0 -clock clk [remove_from_collection [all_inputs] [get_ports clk]]
set_output_delay 1.0 -clock clk [all_outputs]
set_driving_cell -lib_cell BUF_X2 -pin Z [remove_from_collection [all_inputs] [get_ports clk]]
set_load 0.05 [all_outputs]

proc rpt {tag args} {
  set paths [eval get_timing_paths $args]
  if {[sizeof_collection $paths] == 0} {
    puts "$tag N/A"
    return
  }
  set p [index_collection $paths 0]
  puts "$tag slack=[format %.2f [get_attribute $p slack]] start=[get_attribute $p startpoint] end=[get_attribute $p endpoint]"
}

# 取某寄存器族的输出引脚集合（供 -through）
proc outpins {pat} {
  return [get_pins -hier -filter "full_name =~ $pat"]
}

puts "===BUDGET_START==="
puts "PERIOD $PERIOD"

# ---- ① 旧组释放 -> 下一组采集 ----
# 逻辑阶段：CONV 侧判断可释放（captured/held/quiet）-> 释放 -> ACQ 装入新描述符
# 用 -through 穿过 conv 释放判定相关的组合节点：
#   conv_q_reg 的输出 = 该笔仍占用 CONV
#   acq_ids_q_reg 的输入 = 新一组片号被锁存
set thru1 [outpins "*conv_*_q_reg*/Q"]
set thru2 [outpins "*quiet_cnt_q_reg*/Q"]
rpt "B1A_CONV_TO_QUIET"  -through $thru1 -to [get_pins -hier -filter "full_name =~ *quiet_cnt_q_reg*/D"] -max_paths 1 -nworst 1 -delay_type max
rpt "B1B_QUIET_TO_ACQ"   -from  $thru2 -to [get_pins -hier -filter "full_name =~ *acq_ids_q_reg*/D"] -max_paths 1 -nworst 1 -delay_type max
rpt "B1C_FULL"           -through $thru1 -to [get_pins -hier -filter "full_name =~ *acq_ids_q_reg*/D"] -max_paths 1 -nworst 1 -delay_type max

# ---- ② 已决结果 -> 物理 RDAC 开关 ----
# 逻辑阶段：事件层产生应用命令 -> RDAC 输出
# rdac_post_state_o / rdac_pre_state_o 是**输出端口**（不是寄存器）
set ev [outpins "*exp_evt_q_reg*/Q"]
# 实测：寄存器 Q -> 输出端口 这种跨边界查询，用 -through 返回 0，
# 直接用 -to [get_ports ...] 才能取到（B2A/B2B 早前 N/A 就是这个原因）。
rpt "B2A_TO_POST" -to [get_ports rdac_post_state_o] -max_paths 1 -nworst 1 -delay_type max
rpt "B2B_TO_PRE"  -to [get_ports rdac_pre_state_o]  -max_paths 1 -nworst 1 -delay_type max
# 也用 -through 看经过事件层内部最长的一段
rpt "B2C_EVT_INTERNAL" -through $ev -max_paths 1 -nworst 1 -delay_type max

# ---- ③ 末次生效 -> ADC2 捕获 ----
# 逻辑阶段：最后 post 锁存 -> arm -> capture -> hold -> 返回
set fp [outpins "*final_post_q_reg*/Q"]
rpt "B3A_TO_ARM" -to [get_ports adc2_capture_arm_o] -max_paths 1 -nworst 1 -delay_type max
rpt "B3B_HOLD_INTERNAL"  -to [get_pins -hier -filter "full_name =~ *hold_done_q_reg*/D"] -max_paths 1 -nworst 1 -delay_type max
rpt "B3C_QUIET_INTERNAL" -from $thru2 -max_paths 1 -nworst 1 -delay_type max

# ---- 全局最差 ----
rpt "GLOBAL_WORST" -max_paths 1 -nworst 1 -delay_type max

puts "===BUDGET_END==="
exit
