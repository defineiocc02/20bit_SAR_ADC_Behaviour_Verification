# =============================================================================
# 文件名     : fc_sched18.tcl
# 版本       : v2.0
# 功能       : sched18 单模块 RTL-to-GDS 最小验证（Fusion Compiler）。
#
# 设计说明   :
#   * 为什么从 sched18：13 个可综合模块里它有时钟、规模适中
#     （2338 cells / 1265 µm²）、端口少（12 个），适合先跑通 stage-gate。
#
#   * **FC 的库体系与 DC 完全不同**（v1.0 踩过）：
#       · 没有 `read_db` / `target_library` / `link_library`
#         （实测：read_db 报 CMD-005，get_app_var target_library 报 CMD-104）
#       · 正确做法是 `create_lib -ref_libs <NDM> <新库路径>`，
#         用 NDM（Milkyway 后继格式）而不是 .db
#       · 综合命令是 `compile_fusion`，不是 compile_ultra
#       · `read_parasitic_tech` 要在 `set_top_module` 之后
#
#   * stage-gate：每个阶段打 STEP_* 标记，失败即停，不静默继续。
#
# 变更记录   :
#   2026-11-16  v1.0 照 DC 的 read_db 写，FC 不认识。
#   2026-11-16  v2.0 改 create_lib -ref_libs + NDM + set_top_module。
# =============================================================================
set DESIGN   sched18
set PDK      /home/yian/PDK/tsmc28/digital_technology
set LIB_SS   $PDK/ndm/tcbn28hpcplusbwp7t40p140.ndm
set TLU_DIR  $PDK/installed/tech/rc_1p9m_4x2y2r
set TLU_MAX  $TLU_DIR/cln28hpc+_1p09m+ut-alrdl_4x2y2r_cworst.tluplus
set TLU_MIN  $TLU_DIR/cln28hpc+_1p09m+ut-alrdl_4x2y2r_cbest.tluplus
set PERIOD   25.0
set WORK     /tmp/fc_sched18
set REF      $WORK/ref

file mkdir $WORK
file mkdir $REF

# ---------------------------------------------------------------- 建 NDM 库
create_lib -ref_libs $LIB_SS $REF/lib
puts "STEP_CREATE_LIB_OK"

# ---------------------------------------------------------------- 读 RTL
analyze -format sverilog /tmp/ip/rtl/sched18.sv
elaborate $DESIGN
puts "STEP_ELAB_OK"
set_top_module $DESIGN
puts "STEP_TOP_OK"

# ---------------------------------------------------------------- TLU+
read_parasitic_tech -tlup $TLU_MAX -name default
puts "STEP_TLU_OK"

# ---------------------------------------------------------------- 约束
create_clock -name clk -period $PERIOD [get_ports clk]
set_clock_uncertainty -setup 0.30 [get_clocks clk]
set_input_delay  1.0 -clock clk [remove_from_collection [all_inputs] [get_ports clk]]
set_output_delay 1.0 -clock clk [all_outputs]
puts "STEP_CONSTRAINT_OK"

# ---------------------------------------------------------------- 综合
compile_fusion
puts "STEP_SYNTH_OK"

# ---------------------------------------------------------------- 物理
initialize_floorplan -core_utilization 0.6
puts "STEP_FLOORPLAN_OK"
place_opt
puts "STEP_PLACE_OK"
clock_opt
puts "STEP_CTS_OK"
route_auto
puts "STEP_ROUTE_OK"

# ---------------------------------------------------------------- 报告
puts "===REPORT==="
report_area
report_timing -max_paths 1 -delay_type max
puts "===END==="
exit
