# =============================================================================
# 文件名     : dc_synth_v21.tcl
# 版本       : v1.0
# 功能       : v21 数字 IP 的 28HPC+ 综合（Design Compiler）。
# 设计说明   :
#   * 为什么另起 DC 流程：FC 的 mcmmStart 需要 corner/scenario 绑定
#     （TLU+ 关联），该配置在 `create_lib` 之后、block 未定义时无法建
#     （实测 create_corner/create_scenario 均报 "Current block is not defined"）。
#     DC 不需要那套 NDM/corner 机制，能直接把综合网表交出来。
#     后布线寄生与签核 STA 交给 PrimeTime（它读 SDC + SPEF，无需 NDM）。
#   * 库：28HPC+ CCS，SS 0.81V -40C 作 setup 主用角。
#   * 约束：25 ns 公共采样周期（40 MS/s）。**注意**：源码在一个时钟周期内
#     接收多个逐次事件（Flash + 6 SAR），所以这个周期不是"全部内部事件预算"，
#     详见 docs/v21/I5_TIMING_BUDGET.md。
# 变更记录   :
#   2026-11-16  初版。
# =============================================================================

set DESIGN adc_digital_ip_v21
set PDK    /home/yian/PDK/tsmc28/digital_technology
set DB     $PDK/TSMCHOME/digital/Front_End/timing_power_noise/CCS/tcbn28hpcplusbwp7t40p140_180a/tcbn28hpcplusbwp7t40p140ssg0p81vm40c_ccs.db
set PERIOD 25.0
set OUT    /tmp/dcwork

file mkdir $OUT
set_app_var search_path [list . /tmp/ip $OUT]
set_app_var target_library [list $DB]
set_app_var link_library   [list "* $DB"]

# ---- 读入生产 RTL（不含节点宏）----
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
current_design $DESIGN
link
puts "STEP_ELAB_OK"

# ---- 约束 ----
create_clock -name clk -period $PERIOD [get_ports clk]
set_clock_uncertainty -setup 0.30 [get_clocks clk]
set_clock_uncertainty -hold  0.15 [get_clocks clk]
set_clock_transition 0.10 [get_clocks clk]
set_driving_cell -lib_cell BUF_X2 -pin Z \
    [remove_from_collection [all_inputs] [get_ports clk]]
set_load 0.05 [all_outputs]
set_false_path -from [get_ports rst_n] -to [all_registers]
set_false_path -from [all_registers] -to [get_ports rst_n]

# ---- 综合 ----
# 用 medium effort：实测 compile_ultra 在 recon 的 96 位乘除上会跑很久；
# medium 能在合理时间内给出可用的 PPA 起点，作为后续优化的基线。
compile -map_effort medium
puts "STEP_COMPILE_OK"

# ---- 产物 ----
write -format verilog -hierarchy -output $OUT/${DESIGN}_syn.v
write_sdc $OUT/${DESIGN}_syn.sdc
report_area  > $OUT/area.rpt
report_timing -max_paths 20 > $OUT/sta.rpt
report_constraint -all_violators > $OUT/viol.rpt

set r [all_registers]
set c [all_registers -clock clk]
puts "CHK total=[sizeof_collection $r] clocked=[sizeof_collection $c]"
set u [remove_from_collection $r $c]
puts "CHK unclocked=[sizeof_collection $u]"
puts "STEP_DONE"
exit
