# recon_d5_w2 · SMIC180 slow @ 25 ns
#
# 约束写法照 docs/TIMING_PIPE2.md 第 4 节：
#   set_input_delay 的 -max / -min 必须分行，写在同一行会报 CMD-012 并被整条丢弃
#   （dc_shell 不退出、继续跑，只留下一个假的 slack）。
#   报完必须 grep 报告确认 `input external delay` / `output external delay` 真的出现。
set LIB /home/yian/PDK/smic180/standard_cells/synopsys/slow.db
set_app_var target_library $LIB
set_app_var link_library   "* $LIB"
define_design_lib WORK -path /root/timing/w2/dc/work

analyze -format sverilog /root/timing/w2/rtl/recon_d5_w2.sv
elaborate recon_d5_w2
current_design recon_d5_w2
link

create_clock -name clk -period 25 [get_ports clk]
set_clock_uncertainty -setup 0.3 [get_clocks clk]
set_clock_uncertainty -hold  0.1 [get_clocks clk]

set ins [remove_from_collection [all_inputs] [get_ports clk]]
set_input_delay  -max 2.0 -clock clk $ins
set_input_delay  -min 0.5 -clock clk -add_delay $ins
set_output_delay -max 2.0 -clock clk [all_outputs]
set_output_delay -min 0.5 -clock clk -add_delay [all_outputs]

check_timing > check_timing.rpt
report_clocks > clocks.rpt

compile

report_area   > area.rpt
report_timing -delay max -max_paths 10 -nworst 1 > setup.rpt
report_timing -delay min -max_paths 5  -nworst 1 > hold.rpt

# 分级：S1 输入 → d_q24_r，S2 d_q24_r/adc2_s1/dither_s1 → x_q24，S3 x_q24 → 输出
report_timing -delay max -from [get_ports {wm_q30* g_q30* inv_g_q60* adc2_i* dither_i*}] \
              -to [get_pins {d_q24_r_reg*/D}] -max_paths 1 > stage1.rpt
report_timing -delay max -from [get_pins {d_q24_r_reg*/CK adc2_s1_reg*/CK dither_s1_reg*/CK}] \
              -to [get_pins {x_q24_reg*/D}] -max_paths 1 > stage2.rpt
report_timing -delay max -from [get_pins {x_q24_reg*/CK}] \
              -to [all_outputs] -max_paths 1 > stage3.rpt

set sp [get_timing_paths -delay max -max_paths 1 -nworst 1]
set hp [get_timing_paths -delay min -max_paths 1 -nworst 1]
puts "SETUP_SLACK=[get_attribute $sp slack]"
puts "HOLD_SLACK=[get_attribute $hp slack]"
puts "SETUP_START=[get_attribute [get_attribute $sp startpoint] full_name]"
puts "SETUP_END=[get_attribute [get_attribute $sp endpoint] full_name]"
exit
