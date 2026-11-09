# =============================================================================
# 文件名     : post_route.tcl
# 模块名     : task1_post_route_sta
# 版本       : v1.0
# 功能       : 模块一布线段提取后 STA
#
# 设计说明   :
#   * 必须与 stream-out 用同一份 6_final.odb / .spef，否则报告与交付 GDS 不是同一个实现。
#   * 本文件只描述约束/验证意图，不是验收结论；实际状态以运行日志与归档收据为准。
# -----------------------------------------------------------------------------
# 变更记录
# -----------------------------------------------------------------------------
# 初始版本，暂无变更记录。
# =============================================================================

set base $::env(TASK1_FINAL_DIR)
read_liberty $::env(TASK1_PROXY_LIBERTY)
read_db $base/6_final.odb
read_sdc $base/6_final.sdc
set_propagated_clock [all_clocks]
read_spef $base/6_final.spef

puts "\n=== Constraint coverage ==="
check_setup -verbose
puts "\n=== Setup, extracted RC ==="
report_worst_slack -max -digits 4
report_tns -max -digits 4
report_checks -path_delay max -group_path_count 10 -format full_clock_expanded
puts "\n=== Hold, extracted RC ==="
report_worst_slack -min -digits 4
report_tns -min -digits 4
report_checks -path_delay min -group_path_count 10 -format full_clock_expanded
puts "\n=== Final proxy power-grid connectivity ==="
check_power_grid -net VDD
check_power_grid -net VSS
puts "TASK1_POST_ROUTE_STA_COMPLETE"
