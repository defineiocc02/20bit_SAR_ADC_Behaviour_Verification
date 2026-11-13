set LIB /home/yian/PDK/smic180/standard_cells/synopsys/slow.db
set_app_var target_library $LIB
set_app_var link_library "* $LIB"
set_app_var search_path ". /root/sar20/rtl"
define_design_lib WORK -path ./work_net
set sv {dither_split.sv cdac_decode.sv dem_shuffle.sv recon_d5.sv follower.sv slice_pool.sv digital_top.sv}
analyze -format sverilog -library WORK $sv
elaborate digital_top -library WORK
link
create_clock -name clk -period 25 [get_ports clk]
compile
write -format verilog -hierarchy -output netlist.v
write_sdc sdc.sdc
exit
