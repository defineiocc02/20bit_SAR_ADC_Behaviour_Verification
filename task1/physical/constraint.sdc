# =============================================================================
# 文件名     : constraint.sdc
# 模块名     : task1_constraint_nangate45
# 版本       : v1.0
# 功能       : 模块一 Nangate45 代理时序约束
#
# 设计说明   :
#   * 一刀切 input/output delay 是代理假设，不是签核预算；跨模拟边界的分组 delay 与 max_delay 尚未落地。
#   * 本文件只描述约束/验证意图，不是验收结论；实际状态以运行日志与归档收据为准。
# -----------------------------------------------------------------------------
# 变更记录
# -----------------------------------------------------------------------------
# 初始版本，暂无变更记录。
# =============================================================================

create_clock -name task1_clk -period 6.25 [get_ports clk_i]

# The digital proxy has one clock domain. Reset is synchronous and timed.
set input_ports [all_inputs -no_clocks]
set_input_delay -clock task1_clk -max 0.80 $input_ports
set_input_delay -clock task1_clk -min 0.20 $input_ports
set_input_transition 0.10 $input_ports
set_output_delay -clock task1_clk -max 0.80 [all_outputs]
set_output_delay -clock task1_clk -min -0.20 [all_outputs]
# Nangate45's Liberty capacitance unit is 1 fF.
set_load 5.0 [all_outputs]
set_clock_uncertainty -setup 0.10 [get_clocks task1_clk]
set_clock_uncertainty -hold 0.05 [get_clocks task1_clk]
