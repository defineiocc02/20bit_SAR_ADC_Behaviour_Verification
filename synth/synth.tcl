# SMIC 180 slow corner 综合：fc_shell -no_init -f synth.tcl
set LIB /home/yian/PDK/smic180/standard_cells/synopsys/slow.db
set_app_var target_library $LIB
set_app_var link_library  "* $LIB"
set_app_var search_path ". /home/yian/sar20/rtl"
define_design_lib WORK -path ./work

set srcs {recon_d5.sv cdac_decode.sv dem_shuffle.sv dither_split.sv \
          follower.sv slice_pool.sv digital_top.sv}
analyze -format sverilog -library WORK $srcs
elaborate digital_top -library WORK
link
create_clock -name clk -period 25 [get_ports clk] 
compile_ultra
report_area   > area.rpt
report_timing -max_paths 5 > timing.rpt
report_qor    > qor.rpt
exit
