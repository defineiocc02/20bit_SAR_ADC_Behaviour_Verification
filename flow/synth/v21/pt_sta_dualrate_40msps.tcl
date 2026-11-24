# =============================================================================
# 文件名     : pt_sta_dualrate_40msps.tcl
# 模块名     : adc_digital_ip_v21_40msps 多角静态时序签核脚本（Synopsys PrimeTime）
# 版本       : v1.0
# 功能       : 在 TSMC 28HPC+ 四角点（SS -40C / SS 125C Setup 与 FF -40C / FF 125C Hold）
#              下对双域 40 MSPS 门级网表执行独立静态时序分析，分域汇总 WNS/TNS 与违规。
# 设计说明   :
#   * [独立复核] 严格加载与 DC 一致的 sdc_dualrate_40msps.sdc，在 SS 角核验 Setup 与
#     跨时钟 set_max_delay -datapath_only，在 FF 角核验 Hold 裕量。
#   * [工艺提示] 本脚本评估 TSMC 28HPC+ 网表；40 nm 迁移需重新综合并替换为 40 nm .db。
# 变更记录   :
#   2026-11-24  v1.0 初版。双域 40 MSPS 四角点 PrimeTime 静态时序检查。
# =============================================================================

source [file join [file dirname [file normalize [info script]]] design_inputs.tcl]
set v21::manifest rtl/filelists/files_adc_v21_40msps.f
set DESIGN adc_digital_ip_v21_40msps
set PDK    [v21::pdk]
set LIBDIR $PDK/TSMCHOME/digital/Front_End/timing_power_noise/CCS/tcbn28hpcplusbwp7t40p140_180a
set DC_DIR [v21::dc_dir]
set SDC    [file join [file dirname [file normalize [info script]]] sdc_dualrate_40msps.sdc]
set CORNER [expr {[info exists ::env(PT_CORNER)] ? $::env(PT_CORNER) : "ssg0p81vm40c"}]
set OUTDIR [expr {[info exists ::env(PT_OUT_DIR)] ? $::env(PT_OUT_DIR) : $DC_DIR}]
file mkdir $OUTDIR

# =============================================================================
# 1. 按指定角点加载 TSMC 28HPC+ CCS 库与门级网表
# =============================================================================
set DB $LIBDIR/tcbn28hpcplusbwp7t40p140${CORNER}_ccs.db
set_app_var search_path  [list . $DC_DIR $LIBDIR]
set_app_var link_path    [list "*" $DB]

read_verilog [file join $DC_DIR ${DESIGN}_syn.v]
current_design $DESIGN
link_design

# =============================================================================
# 2. 施加双域 SDC 约束与驱动单元，并执行独立时序更新
# =============================================================================
source $SDC
set_driving_cell -lib_cell BUFFD2BWP7T40P140 -pin Z \
    [remove_from_collection [all_inputs] [get_ports {clk_fs ev_clk_i}]]

update_timing -full

# =============================================================================
# 3. 分别输出 Setup（max）、Hold（min）与跨时钟窗报告（显式打印正裕量）
# =============================================================================
report_clock -skew -attributes                                                                                > $OUTDIR/pt_${CORNER}_clocks.rpt
report_analysis_coverage                                                                                      > $OUTDIR/pt_${CORNER}_coverage.rpt
report_timing -delay_type max -slack_lesser_than 1000.0 -from [get_clocks clk_fs] -to [get_clocks clk_fs] -max_paths 10 -significant_digits 4 > $OUTDIR/pt_${CORNER}_setup_clk_fs.rpt
report_timing -delay_type max -slack_lesser_than 1000.0 -from [get_clocks ev_clk] -to [get_clocks ev_clk] -max_paths 10 -significant_digits 4 > $OUTDIR/pt_${CORNER}_setup_ev_clk.rpt
report_timing -delay_type max -slack_lesser_than 1000.0 -from [get_clocks clk_fs] -to [get_clocks ev_clk] -max_paths 10 -significant_digits 4 > $OUTDIR/pt_${CORNER}_setup_fs2ev.rpt
report_timing -delay_type max -slack_lesser_than 1000.0 -from [get_clocks ev_clk] -to [get_clocks clk_fs] -max_paths 10 -significant_digits 4 > $OUTDIR/pt_${CORNER}_setup_ev2fs.rpt
report_timing -delay_type min -slack_lesser_than 1000.0 -from [get_clocks clk_fs] -to [get_clocks clk_fs] -max_paths 10 -significant_digits 4 > $OUTDIR/pt_${CORNER}_hold_clk_fs.rpt
report_timing -delay_type min -slack_lesser_than 1000.0 -from [get_clocks ev_clk] -to [get_clocks ev_clk] -max_paths 10 -significant_digits 4 > $OUTDIR/pt_${CORNER}_hold_ev_clk.rpt
report_constraint -all_violators -significant_digits 4                                                        > $OUTDIR/pt_${CORNER}_violators.rpt

puts "PT_DUALRATE_DONE corner=$CORNER"
exit 0
