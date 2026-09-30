create_clock -name core_clk -period 25 [get_ports clk]
set_clock_uncertainty 0.05 [get_clocks core_clk]
set_input_delay 0.0 -clock core_clk [get_ports -filter {DIRECTION == IN && NAME != clk}]
set_output_delay 0.0 -clock core_clk [all_outputs]
