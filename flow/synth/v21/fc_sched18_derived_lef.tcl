# =============================================================================
# 文件名     : fc_sched18_derived_lef.tcl
# 功能       : 仅在 sched18 叶子模块上检查派生 tech LEF、site row 与供电配置。
# 设计说明   : 选择目标的依据是下文的 elaborate sched18。各阶段的 STEP 表示
#              调用记录，模块级物理结果另看原始工具报告。
# =============================================================================
source [file join [file dirname [file normalize [info script]]] design_inputs.tcl]
set PDK [v21::pdk]
set TF   $PDK/installed/tech/synopsys_pr/N28_PRTF_Syn_v1d5a/PR_tech/Synopsys/TechFile/HVH/tsmcn28_9lm4X2Y2RUTRDL.tf
set NDM  $PDK/ndm/tcbn28hpcplusbwp7t40p140.ndm
set TLU  $PDK/installed/tech/rc_1p9m_4x2y2r
set WORK [v21::location V21_FC_DIR [file join [pwd] fc_sched18_derived]]
file mkdir $WORK/ref
create_lib -technology $TF -ref_libs $NDM $WORK/ref/lib
puts "STEP_CREATE_LIB_OK"
analyze -format sverilog [file join $v21::root rtl sched18.sv]
elaborate sched18
set_top_module sched18
# 关键：读自带 DIRECTION 的 tech LEF（.tf 只给几何、不含方向）
set _rt [catch {read_tech_lef [file join $v21::root flow synth v21 tsmc28_tech_derived.lef]} _et]
puts "CHK_TECH rc=$_rt"
# **必须加载 UPF 并设电压**：否则日志出现
#   Number of used supplies = 0
# 导致 place_opt 插入的 buffer（A689 这类）没有电源上下文、
# 报了 168 个 "is not placed"。
# UPF 本身已由 check_mv_design 验证通过（UPF-072 committed）。
set _ru [catch {load_upf [file join $v21::root flow synth v21 min_single_domain.upf]} _eu]
puts "CHK_UPF rc=$_ru"
set _rv1 [catch {set_voltage 0.81 -object_list [get_supply_nets VDD]} _e1]
set _rv2 [catch {set_voltage 0.00 -object_list [get_supply_nets VSS]} _e2]
puts "CHK_VOLTAGE vdd=$_rv1 vss=$_rv2"
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
set _ncol [expr {int(($_x2-$_x1)/$_sw) - 1}]
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
