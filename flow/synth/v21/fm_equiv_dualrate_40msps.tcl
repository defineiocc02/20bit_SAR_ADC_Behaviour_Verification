# =============================================================================
# 文件名     : fm_equiv_dualrate_40msps.tcl
# 模块名     : adc_digital_ip_v21_40msps 形式等价验证脚本（Synopsys Formality）
# 版本       : v1.0
# 功能       : 核验 TSMC 28HPC+ 综合网表 adc_digital_ip_v21_40msps_syn.v 与双域 RTL
#              的数学逻辑完全等价（含 SHA-256 源码/网表/SVF 三方绑定校验）。
# 设计说明   :
#   * [多版本防串] 启动前强制设置 v21::manifest = files_adc_v21_40msps.f 并调用
#     v21::check_dc，拒绝任何旧版或单时钟网表混入等价证明。
#   * [读入约束] 沿用 Formality W-2024.09 逐文件 read_sverilog 与显式 -container r/i
#     约定，先加载 DC 生成的 SVF 再读参考与实现端。
# 变更记录   :
#   2026-11-24  v1.0 初版。配套双域 40 MSPS 综合网表与 SVF。
# =============================================================================

source [file join [file dirname [file normalize [info script]]] design_inputs.tcl]
set v21::manifest rtl/filelists/files_adc_v21_40msps.f
set DESIGN adc_digital_ip_v21_40msps
set PDK    [v21::pdk]
set DB     $PDK/TSMCHOME/digital/Front_End/timing_power_noise/CCS/tcbn28hpcplusbwp7t40p140_180a/tcbn28hpcplusbwp7t40p140ssg0p81vm40c_ccs.db
set RTLDIR $v21::root
set DC_DIR [v21::dc_dir]

# =============================================================================
# 1. 核验 SHA-256 收据、读入工艺库与 SVF 指引
# =============================================================================
v21::check_dc $DC_DIR $DESIGN
puts "STEP_FM_INPUTS_OK"

set_app_var search_path [list . $RTLDIR $RTLDIR/rtl $RTLDIR/rtl/sar_core]
read_db $DB
puts "STEP_FM_DB_OK"

if {[catch {set_svf [file join $DC_DIR ${DESIGN}.svf]} e]} {
    error "STEP_FM_SVF_FAIL: $e"
}
puts "STEP_FM_SVF_OK"

# 抑制已确认的良性 SystemVerilog 解释提示，不影响最终等价判决
suppress_message FMR_VLOG-091
suppress_message FMR_VLOG-057
suppress_message FMR_VLOG-063
suppress_message FMR_ELAB-147

# =============================================================================
# 2. 逐文件读入双域 RTL 参考端与门级实现网表
# =============================================================================
set RTL_FILES [v21::sources]
foreach f $RTL_FILES {
    read_sverilog -container r $f
}
set_top r:/WORK/$DESIGN
puts "STEP_FM_REF_TOP_OK"

read_verilog -container i -netlist [file join $DC_DIR ${DESIGN}_syn.v]
set_top i:/WORK/$DESIGN
puts "STEP_FM_IMPL_TOP_OK"

# =============================================================================
# 3. 匹配与形式等价验证
# =============================================================================
match
puts "STEP_FM_MATCH_OK"

set _ok [verify]
report_status
report_failing_points
report_aborted_points
report_unverified_points

if {!$_ok} {
    puts "FM_DUALRATE_FAIL"
    exit 1
}
puts "FM_DUALRATE_SUCCEEDED"
exit 0
