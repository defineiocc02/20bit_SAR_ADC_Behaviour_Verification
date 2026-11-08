# =============================================================================
# 文件名     : pre_route.tcl
# 模块名     : task2_pre_route_sta
# 版本       : v1.0
# 功能       : task2 综合后早期 STA
#
# 设计说明   :
#   * 早期结果只作趋势判断；最终结论以提取后 STA 为准。
#   * 本文件只描述约束/验证意图，不是验收结论；实际状态以运行日志与归档收据为准。
# -----------------------------------------------------------------------------
# 变更记录
# -----------------------------------------------------------------------------
# 初始版本，暂无变更记录。
# =============================================================================

read_liberty $::env(TASK1_PROXY_LIBERTY)
read_db $::env(TASK1_FINAL_DIR)/1_synth.odb
read_sdc $::env(TASK1_FINAL_DIR)/1_synth.sdc
check_setup -verbose
foreach sense {max min} {
  report_worst_slack -$sense -digits 6
  report_tns -$sense -digits 6
  report_checks -path_delay $sense -group_path_count 5 -format full_clock_expanded
}
write_verilog /work/out/delivery/mapped_netlist.v
puts "TASK2_SYNTH_STA_COMPLETE"
