# 完整流程：.tf 层定义 + 生成 tech LEF（含 DIRECTION）
set PDK  /home/yian/PDK/tsmc28/digital_technology
set TF   $PDK/installed/tech/synopsys_pr/N28_PRTF_Syn_v1d5a/PR_tech/Synopsys/TechFile/HVH/tsmcn28_9lm4X2Y2RUTRDL.tf
set NDM  $PDK/ndm/tcbn28hpcplusbwp7t40p140.ndm
set TLU  $PDK/installed/tech/rc_1p9m_4x2y2r
set WORK /tmp/fc_final
file mkdir $WORK/ref
create_lib -technology $TF -ref_libs $NDM $WORK/ref/lib
puts "STEP_CREATE_LIB_OK"
analyze -format sverilog /tmp/ip/rtl/sched18.sv
elaborate sched18
set_top_module sched18
# 关键：读自带 DIRECTION 的 tech LEF（.tf 只给几何、不含方向）
set _rt [catch {read_tech_lef /tmp/tsmc28_tech_gen.lef} _et]
puts "CHK_TECH rc=$_rt"
puts "STEP_ELAB_OK"
read_parasitic_tech -tlup $TLU/cln28hpc+_1p09m+ut-alrdl_4x2y2r_cworst.tluplus -name maxTLU
read_parasitic_tech -tlup $TLU/cln28hpc+_1p09m+ut-alrdl_4x2y2r_cbest.tluplus -name minTLU
set_parasitics_parameters -early_spec minTLU -late_spec maxTLU
puts "STEP_TLU_OK"
create_clock -name clk -period 25 [get_ports clk]
set_input_delay 1.0 -clock clk [remove_from_collection [all_inputs] [get_ports clk]]
set_output_delay 1.0 -clock clk [all_outputs]
puts "STEP_CONSTRAINT_OK"
compile_fusion
puts "STEP_SYNTH_OK"
initialize_floorplan -core_utilization 0.6 -use_site_row
# **必须手工建 site row**：initialize_floorplan 建的是 siteArray，
# 而 place_opt 需要 siteRow。实测 initialize_floorplan 后
#   get_site_rows -quiet *  -> 空
#   LGL-397 "No site rows found in floorplan" x49
# 用 create_site_row 按 core7T（0.140 x 0.700）铺满 core。
# 注意 -orientation 只接受 R0/R90/R180/R270/MX/MXR90/MY/MYR90，
# 用 "N" 会报 CMD-031。
set _bb [get_attribute [current_design] boundary]
set _ll [lindex $_bb 0]
set _ur [lindex $_bb 2]
set _x1 [lindex $_ll 0]; set _y1 [lindex $_ll 1]
set _x2 [lindex $_ur 0]; set _y2 [lindex $_ur 1]
set _sw 0.140
set _sh 0.700
set _ncol [expr {int(($_x2-$_x1)/$_sw)}]
set _nrow [expr {int(($_y2-$_y1)/$_sh)}]
# **必须先清掉已有的 site row 再建**，否则报
#   Error: Incompatible site rows overlap. (LGL-010)
# 因为 initialize_floorplan -use_site_row 已经建了一部分（实测 39 个），
# 再手工建就重叠。
set _old [get_site_rows -quiet *]
if {[sizeof_collection $_old] > 0} {
    catch {remove_site_rows $_old}
    puts "CHK_SITE_ROWS cleared=[sizeof_collection [get_site_rows -quiet *]]"
}
set _mk 0
for {set _r 0} {$_r < $_nrow} {incr _r} {
    set _y [expr {$_y1 + $_r*$_sh}]
    if {[catch {create_site_row -name "R$_r" -site core7T -origin [list $_x1 $_y] \
                -site_count $_ncol -orientation R0} _er] == 0} { incr _mk }
}
puts "CHK_SITE_ROWS made=$_mk total=[sizeof_collection [get_site_rows -quiet *]]"
puts "STEP_FLOORPLAN_OK"
set rc1 [catch {place_opt} e1]; puts "STEP_PLACE rc=$rc1"
set rc2 [catch {clock_opt} e2]; puts "STEP_CTS rc=$rc2"
set rc3 [catch {route_auto} e3]; puts "STEP_ROUTE rc=$rc3"
puts "===REPORT==="
report_area
report_timing -max_paths 1 -delay_type max
puts "===END==="
exit
