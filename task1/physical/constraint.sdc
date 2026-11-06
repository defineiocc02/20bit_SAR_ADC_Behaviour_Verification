# 16 synchronous ticks per 100 ns sample slot. Times are in ns.
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
