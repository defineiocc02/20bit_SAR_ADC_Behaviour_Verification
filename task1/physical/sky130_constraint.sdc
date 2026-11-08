# =============================================================================
# 文件名     : sky130_constraint.sdc
# 模块名     : task1_constraint_sky130hd
# 版本       : v1.0
# 功能       : 模块一 SKY130HD 代理时序约束
#
# 设计说明   :
#   * 与 Nangate45 版仅 set_load 单位不同（pF vs fF）；0.80/0.20/-0.20 的推导依据待核实。
#   * 本文件只描述约束/验证意图，不是验收结论；实际状态以运行日志与归档收据为准。
# -----------------------------------------------------------------------------
# 变更记录
# -----------------------------------------------------------------------------
# 初始版本，暂无变更记录。
# =============================================================================

create_clock -name task1_clk -period 6.25 [get_ports clk_i]

# The digital block has one clock domain. Reset is synchronous and timed.
set input_ports [all_inputs -no_clocks]
set_input_delay -clock task1_clk -max 0.80 $input_ports
set_input_delay -clock task1_clk -min 0.20 $input_ports
set_input_transition 0.10 $input_ports
set_output_delay -clock task1_clk -max 0.80 [all_outputs]
set_output_delay -clock task1_clk -min -0.20 [all_outputs]
# SKY130HD Liberty capacitance is in pF. This is 5 fF per output.
set_load 0.005 [all_outputs]
set_clock_uncertainty -setup 0.10 [get_clocks task1_clk]
set_clock_uncertainty -hold 0.05 [get_clocks task1_clk]
