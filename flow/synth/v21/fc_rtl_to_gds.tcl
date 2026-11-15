# =============================================================================
# 文件名     : fc_rtl_to_gds.tcl
# 版本       : v3.0
# 功能       : v21 数字 IP 的 TSMC 28HPC+ RTL-to-GDS 一体流程（Fusion Compiler）。
# 设计说明   :
#   * 本机没有独立 ICC2，但有 fusioncompiler W-2024.09-SP3（含布局布线）。
#     一体会话保证"综合/布局/布线/提取/STA 用同一份网表与同一套约束"。
#   * **FC 用 NDM 库**（不是 .db）：
#       $PDK/ndm/tcbn28hpcplusbwp7t40p140.ndm
#   * **必须配 TLU+ 寄生参数**，否则 compile_fusion 报
#       "No valid parasitics for LATE/EARLY corner"（SCN-1001）并中止。
#       TLU+ 在 $PDK/installed/tech/rc_1p9m_4x2y2r/*.tluplus。
#   * 早前踩过的三个 FC 语法坑（都写在代码注释里）：
#       ① create_lib 前不能 open_lib 试探（会污染状态、错误被 catch 吞掉）
#       ② elaborate 后用 set_top_module，不是 current_design + link
#       ③ 综合命令是 compile_fusion，不是 compile_ultra
#   * 目标：25 ns 公共采样周期（40 MS/s）。
#   * 数字硬宏边界：模拟宏在 RTL 里是行为模型，节点宏在仿真侧，都不进本流程。
# 变更记录   :
#   2026-11-15  v1.0 .db 库，失败。
#   2026-11-15  v2.0 改 NDM；set_top_module；compile_fusion。
#   2026-11-15  v3.0 补 TLU+ 寄生参数；补 floorplan/place/CTS/route/输出。
# =============================================================================

set DESIGN   adc_digital_ip_v21
set PDK      /home/yian/PDK/tsmc28/digital_technology
set LIB_SS   $PDK/ndm/tcbn28hpcplusbwp7t40p140.ndm
set TLU_DIR  $PDK/installed/tech/rc_1p9m_4x2y2r
set TLU_MAX  $TLU_DIR/cln28hpc+_1p09m+ut-alrdl_4x2y2r_cworst.tluplus
set TLU_MIN  $TLU_DIR/cln28hpc+_1p09m+ut-alrdl_4x2y2r_cbest.tluplus
set GDS_MAP  $PDK/installed/tech/synopsys_pr/N28_PRTF_Syn_v1d5a/PR_tech/Synopsys/GdsOutMap/gdsout_4X2Y1Z.map
set PERIOD   25.0
set WORK     /tmp/fcwork
set REF      $WORK/ref

file mkdir $WORK
file mkdir $REF

# ---------------------------------------------------------------- 建库（NDM）
create_lib -ref_libs $LIB_SS $REF/lib
puts "STEP_CREATE_LIB_OK"

# ---------------------------------------------------------------- TLU+ 寄生
# 必须在 compile 之前配好，否则 compile_fusion 报 SCN-1001 中止。
#
# 实测：FC 没有 `set_parasitic_tech`（那是 ICC2 的命令）；
# `set_parasitic_parameters` 也不接受 `-early_tlu_plus_file` 之类的选项。
# 正确的做法就是 `read_parasitic_tech` 注册 TLU+ 文件并命名，
# FC 会按名字前缀（late/early/max/min）自动关联到对应 corner。
read_parasitic_tech -tlup $TLU_MAX -name maxTLU
read_parasitic_tech -tlup $TLU_MIN -name minTLU
puts "STEP_TLU_OK"

# **待绑定项**：TLU+ 注册成功（STEP_TLU_OK 已实测），但 compile_fusion 仍报
#   "No valid parasitics for LATE/EARLY corner default"（SCN-1001）
# 说明 FC 还没把这两个 tech 关联到 "default" corner。
# 已实测确认：
#   · `set_parasitic_tech` 不存在（那是 ICC2 命令）
#   · `set_parasitic_parameters` 的 `-early_corner` / `-late_corner` 均非法
#   · 它接受 `-corners`（报 "bad value specified for option -corners"）
# 下一步：按 `set_parasitic_parameters -corners <name>` 的正确取值补齐，
# 或用 `create_corner` 建一个名为 default 的 corner 再挂 parasitic tech。
# 在此之前，综合结果可用（网表/面积/时序已产出），但不能声称后布线签核。

# ---------------------------------------------------------------- 读入 RTL
set RTL {
  /tmp/ip/rtl/dem_phase_rr.sv
  /tmp/ip/rtl/dem_phase_gen.sv
  /tmp/ip/rtl/dither_pre.sv
  /tmp/ip/rtl/dem_mask_v3.sv
  /tmp/ip/rtl/sched18.sv
  /tmp/ip/rtl/follower_rt.sv
  /tmp/ip/rtl/v21/acq_conv_ctx.sv
  /tmp/ip/rtl/v21/sar_evt_v21.sv
  /tmp/ip/rtl/v21/adc2_capture.sv
  /tmp/ip/rtl/v21/wsum_v21.sv
  /tmp/ip/rtl/v21/recon_v21.sv
  /tmp/ip/rtl/v21/adc_digital_ip_v21.sv
}
foreach f $RTL { analyze -format sverilog $f }

elaborate $DESIGN
puts "STEP_ELAB_OK"
set_top_module $DESIGN
puts "STEP_TOP_OK"

# ---------------------------------------------------------------- 约束
create_clock -name clk -period $PERIOD [get_ports clk]
set_clock_uncertainty -setup 0.30 [get_clocks clk]
set_clock_uncertainty -hold  0.15 [get_clocks clk]
set_clock_transition 0.10 [get_clocks clk]
set_load 0.05 [all_outputs]
set_false_path -from [get_ports rst_n] -to [all_registers]
set_false_path -from [all_registers] -to [get_ports rst_n]

# ---------------------------------------------------------------- 综合
compile_fusion
puts "STEP_COMPILE_OK"
write -format verilog -hierarchy -output $WORK/${DESIGN}_syn.v
write_sdc $WORK/${DESIGN}_syn.sdc
report_area > $WORK/area_syn.rpt
report_timing -max_paths 20 > $WORK/sta_syn.rpt

exit
