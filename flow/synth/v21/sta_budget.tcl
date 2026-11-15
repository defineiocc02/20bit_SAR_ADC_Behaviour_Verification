# =============================================================================
# 文件名     : sta_budget.tcl
# 版本       : v2.0
# 功能       : 按方案要求的三类时间预算分组报告 28HPC+ 综合网表时序。
#
# 设计说明   :
#   * 方案 I5 要求逐项报告：
#       ① 旧组释放 -> 下一组完成采集
#       ② 每次已决结果 -> 物理 RDAC 开关生效
#       ③ 末次生效 -> 经参考、RA、安静窗 -> ADC2 捕获
#   * **v2.0 的关键修正**：网表里层次被 ungroup 展开，
#     寄存器名不带层次前缀（`\acq_ids_q_reg[0]` 而不是
#     `u_ctx/acq_ids_q_reg[0]`），所以 v1.0 用 `*acq_conv_ctx*` 过滤
#     全部返回 N/A。改为按**功能信号名**分组。
#   * slack 与阶段耗时的换算：到达时间 = 周期 - slack，
#     所以 `25 - slack` 就是该阶段从发起到采样的最大允许延迟
#     （已含 clock uncertainty 与 output delay）。
# 变更记录   :
#   2026-11-16  v1.0 按层次名分组，网表 ungroup 后全部 N/A。
#   2026-11-16  v2.0 改按功能信号名分组。
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

# 按功能信号名取集合（名字里的方括号要用 get_pins 的 -filter 匹配）
proc pins_of {pat} {
  return [get_pins -hier -filter "full_name =~ $pat"]
}

proc report_group {tag from_pat to_pat} {
  set fp [pins_of $from_pat]
  set tp [pins_of $to_pat]
  set nf [sizeof_collection $fp]
  set nt [sizeof_collection $tp]
  if {$nf == 0 || $nt == 0} {
    puts "$tag N/A  (from=$nf to=$nt)"
    return
  }
  set paths [get_timing_paths -from $fp -to $tp -max_paths 1 -nworst 1 -delay_type max]
  if {[sizeof_collection $paths] == 0} {
    puts "$tag NO_PATH  (from=$nf to=$nt)"
    return
  }
  set p [index_collection $paths 0]
  puts "$tag slack=[get_attribute $p slack] start=[get_attribute $p startpoint] end=[get_attribute $p endpoint]"
}

puts "===BUDGET_START==="
puts "PERIOD $PERIOD"

# ① 旧组释放 -> 下一组采集
#    起点：CONV 侧状态（conv_q / quiet_cnt / conv_held）
#    终点：ACQ 描述符锁存（acq_ids_q 等）
report_group "B1_REL_TO_ACQ"  "conv_*_q_reg*\\\\\[0\\\\\]/CP"   "acq_*_q_reg*\\\\\[0\\\\\]/D"

# ② 已决结果 -> 物理 RDAC 开关
#    起点：事件层状态（exp_evt_q / bit_cmd_q / prefix_q / guard_q）
#    终点：RDAC 输出寄存器
report_group "B2_EVT_TO_POST" "exp_evt_q_reg*\\\\\[*\\\\\]/CP"  "rdac_post_state*_reg*\\\\\[*\\\\\]/D"
report_group "B2B_EVT_TO_PRE" "exp_evt_q_reg*\\\\\[*\\\\\]/CP"  "rdac_pre_state*_reg*\\\\\[*\\\\\]/D"

# ③ 末次生效 -> ADC2 捕获
#    起点：owner/hold/quiet 状态
#    终点：捕获与返回寄存器
report_group "B3_ARM_TO_CAP"  "owner_valid_q_reg*/CP"           "hold_done_q_reg*/D"
report_group "B3B_CAP_INT"    "*hold*_q_reg*/CP"                "*hold*_q_reg*/D"
report_group "B3C_QUIET"      "quiet_cnt_q_reg*/CP"             "quiet_cnt_q_reg*/D"

# 全局最差内部路径（供参考）
set allp [get_timing_paths -max_paths 1 -nworst 1 -delay_type max]
if {[sizeof_collection $allp] > 0} {
  set p [index_collection $allp 0]
  puts "GLOBAL_WORST slack=[get_attribute $p slack] start=[get_attribute $p startpoint] end=[get_attribute $p endpoint]"
}
puts "===BUDGET_END==="
exit
