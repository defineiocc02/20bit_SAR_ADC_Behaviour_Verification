# =============================================================================
# 文件名     : dc_synth_dualrate_40msps.tcl
# 模块名     : adc_digital_ip_v21_40msps 双域综合脚本（TSMC 28HPC+）
# 版本       : v1.1
# 功能       : 在 TSMC 28HPC+ 工艺库下综合双时钟域 40 MSPS 转换核，并输出分域时序、
#              面积、功耗、QoR、SVF 与完整 SHA-256 绑定收据。
# 设计说明   :
#   * [工艺基线] 本脚本使用 TSMC 28HPC+ 7-track 工艺库（tcbn28hpcplusbwp7t40p140_180a，
#     SS 0.81V -40C 作为 setup 综合角）；若迁移至 40 nm 工艺，请按 docs/synopsys_signoff/
#     的 28 nm -> 40 nm 迁移清单重估 ev_clk 步长、Level Shifter 延迟与面积倍率。
#   * [多版本收据] 显式将 v21::manifest 切换至 rtl/filelists/files_adc_v21_40msps.f，
#     与单时钟基线 v21 共享同一套 SHA-256 绑定与 Formality 校验机制，杜绝跨版本串网表。
#   * [分域分组] 通过 group_path 将 clk_fs（25 ns）、ev_clk（1 ns）及两条跨边界
#     路径分开优化，使 DC 仅对 ~1k 门快速跟随器施加高速映射，避免过度放大 wsum/recon。
# 变更记录   :
#   2026-11-24  v1.0 初版。配套双域 40 MSPS 生产 RTL 与 SDC。
#   2026-11-24  v1.1 绑定 files_adc_v21_40msps.f SHA-256 收据、启用 8 核并增补 QoR/功耗报告。
# =============================================================================

source [file join [file dirname [file normalize [info script]]] design_inputs.tcl]
set v21::manifest rtl/filelists/files_adc_v21_40msps.f
set DESIGN adc_digital_ip_v21_40msps
set PDK    [v21::pdk]
set DB     $PDK/TSMCHOME/digital/Front_End/timing_power_noise/CCS/tcbn28hpcplusbwp7t40p140_180a/tcbn28hpcplusbwp7t40p140ssg0p81vm40c_ccs.db
set OUT    [v21::dc_dir]
set SDC    [file join [file dirname [file normalize [info script]]] sdc_dualrate_40msps.sdc]

# =============================================================================
# 1. 多核配置、读入双域生产清单并生成 SVF
# =============================================================================
set_host_options -max_cores 8
v21::begin_dc $OUT $DESIGN
set_app_var search_path    [list . $v21::root $OUT]
set_app_var target_library [list $DB]
set_app_var link_library   [list "*" $DB]
set_svf $OUT/${DESIGN}.svf

# 经由 v21::sources 统一读入，确保分析顺序与 SHA-256 收据完全一致
set _rtl [v21::sources]
foreach f $_rtl { analyze -format sverilog $f }
elaborate $DESIGN
current_design $DESIGN
link
puts "STEP_DUALRATE_ELAB_OK"

# =============================================================================
# 2. 施加双域约束与真实驱动单元核验
# =============================================================================
source $SDC

set _drv_cell BUFFD2BWP7T40P140
set _n_cell 0
foreach_in_collection _c [get_lib_cells -quiet */$_drv_cell] { incr _n_cell }
if {$_n_cell == 0} {
    puts "FATAL: 工艺库缺少驱动单元 $_drv_cell"
    exit 1
}
set_driving_cell -lib_cell $_drv_cell -pin Z \
    [remove_from_collection [all_inputs] [get_ports {clk_fs ev_clk_i}]]

# 分域路径分组，防止 ev_clk 1 ns 紧路径把 wsum/recon 误推向高功耗单元
group_path -name GRP_CLK_FS  -from [get_clocks clk_fs] -to [get_clocks clk_fs] -weight 2
group_path -name GRP_EV_CLK  -from [get_clocks ev_clk] -to [get_clocks ev_clk] -weight 5
group_path -name GRP_FS2EV   -from [get_clocks clk_fs] -to [get_clocks ev_clk] -weight 3
group_path -name GRP_EV2FS   -from [get_clocks ev_clk] -to [get_clocks clk_fs] -weight 2

# =============================================================================
# 3. 综合与分域时序/面积/功耗/未时钟寄存器检查
# =============================================================================
compile -map_effort medium
puts "STEP_DUALRATE_COMPILE_OK"

write -format verilog -hierarchy -output $OUT/${DESIGN}_syn.v
write_sdc $OUT/${DESIGN}_syn.sdc
report_qor                                     > $OUT/qor.rpt
report_area -hierarchy                         > $OUT/area_hier.rpt
report_power -hierarchy                        > $OUT/power_hier.rpt
report_timing -group GRP_CLK_FS -max_paths 15  > $OUT/sta_clk_fs.rpt
report_timing -group GRP_EV_CLK -max_paths 15  > $OUT/sta_ev_clk.rpt
report_timing -group GRP_FS2EV  -max_paths 10  > $OUT/sta_fs2ev.rpt
report_timing -group GRP_EV2FS  -max_paths 10  > $OUT/sta_ev2fs.rpt
report_constraint -all_violators               > $OUT/viol.rpt

set _all_reg  [all_registers]
set _clk_reg  [add_to_collection [all_registers -clock clk_fs] [all_registers -clock ev_clk]]
set _unclk    [remove_from_collection $_all_reg $_clk_reg]
puts "CHK total=[sizeof_collection $_all_reg] clocked=[sizeof_collection $_clk_reg] unclocked=[sizeof_collection $_unclk]"

set_svf -off
v21::finish_dc $OUT $DESIGN
puts "STEP_DUALRATE_DONE"
exit
