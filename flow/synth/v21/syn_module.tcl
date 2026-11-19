# =============================================================================
# 文件名     : syn_module.tcl
# 版本       : v1.0
# 功能       : 单模块综合（用于逐模块摸底，不依赖完整顶层收敛）。
#
# 设计说明   :
#   * 为什么需要：完整顶层综合长时间不收敛（卡 wsum -> 改寄存器堆后卡 recip_q60）。
#     逐模块综合能给出每个模块的真实面积/时序/耗时，用于：
#       ① 判断瓶颈是某个模块本身异常，还是顶层组合爆炸
#       ② 后端工作可以分模块推进
#   * 用法：dc_shell -f syn_module.tcl -x "set MOD <name>; set RTL <files>"
# 变更记录   :
#   2026-11-16  v1.0 初版。
# =============================================================================
source [file join [file dirname [file normalize [info script]]] design_inputs.tcl]
set PDK [v21::pdk]
set DB  $PDK/TSMCHOME/digital/Front_End/timing_power_noise/CCS/tcbn28hpcplusbwp7t40p140_180a/tcbn28hpcplusbwp7t40p140ssg0p81vm40c_ccs.db
set_app_var search_path [list . $v21::root [file join $v21::root rtl] [file join $v21::root rtl v21]]
set_app_var target_library [list $DB]
set_app_var link_library   [list "*" $DB]

foreach f $RTL { analyze -format sverilog $f }
elaborate $MOD
current_design $MOD
link
create_clock -name clk -period 25 [get_ports clk]
set_input_delay  1.0 -clock clk [remove_from_collection [all_inputs] [get_ports clk]]
set_output_delay 1.0 -clock clk [all_outputs]
set_driving_cell -lib_cell BUFFD2BWP7T40P140 -pin Z \
    [remove_from_collection [all_inputs] [get_ports clk]]
set_load 0.05 [all_outputs]

set t0 [clock seconds]
compile -map_effort medium
set t1 [clock seconds]
puts "CHK_MODULE $MOD elapsed=[expr {$t1-$t0}]s"
puts "CHK_CELLS [sizeof_collection [get_cells -hier -filter "is_hierarchical==false"]]"
report_area
report_timing -max_paths 1 -nworst 1 -delay_type max
exit
