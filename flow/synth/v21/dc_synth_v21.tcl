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
set_app_var link_library   [list "*" $DB]

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
  /tmp/ip/rtl/recip_q60.sv
  /tmp/ip/rtl/v21/wsum_v21.sv
  /tmp/ip/rtl/v21/recip_rnd_v21.sv
  /tmp/ip/rtl/v21/recon_v21.sv
  /tmp/ip/rtl/v21/adc_digital_ip_v21.sv
}
# **必须生成 SVF**：DC 会做极性变换（用 DFCNQD1 反相输出触发器）、
# 寄存器合并、常量传播等优化，Formality 需要 SVF 才能复现这些操作。
# 没有 SVF 时静默失配：本轮实测 20 个 failing compare point，
# 全部集中在用了 DFCNQD1 的寄存器上（quiet_cnt_q / conv_valid_q 等）。
set_svf $OUT/${DESIGN}.svf

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
# ---------------------------------------------------------------- 驱动与负载
#
# **不能用 `BUF_X2`：本库没有这个单元。**
#
# 实测：库里的缓冲器是 `BUFFD1BWP7T40P140` / `BUFFD2BWP7T40P140` 这类
# 全名，`BUF_X1/X2/X4` 一个都不存在（`get_lib_cells */BUF_X2` 返回 0）。
#
# 更麻烦的是：`set_driving_cell -lib_cell BUF_X2` 失败时**只打一条
# `Error: Cannot find the specified driving cell in memory. (UID-993)`，
# 不抛 Tcl 异常** —— 所以 `catch` 抓不到、脚本照常往下跑，
# 所有输入端口最后是「无驱动单元」状态，时序偏乐观。
# 这意味着本轮此前所有综合/STA 的输入驱动都是缺失的。
#
# 现在改成真实单元名，并在设置前**先确认库单元存在**。
set _drv_cell BUFFD2BWP7T40P140
set _n_cell 0
foreach_in_collection _c [get_lib_cells -quiet */$_drv_cell] {
    incr _n_cell
}
puts "CHK driving_cell_exists=$_n_cell ($_drv_cell)"
if {$_n_cell == 0} {
    puts "FATAL: 库里没有 $_drv_cell —— 检查单元名"
    exit 1
}

set_driving_cell -lib_cell $_drv_cell -pin Z \
    [remove_from_collection [all_inputs] [get_ports clk]]
set_load 0.05 [all_outputs]

# 回读校验：UID-993 是静默的（只打 Error 不抛异常），必须主动确认。
#
# **不能用 `get_attribute <port> driving_cell`** —— 该属性名在 DC 的
# 端口属性集里不存在（`UID-101: Attribute 'driving_cell' does not exist
# on port`）。本脚本 v2 就是错在这里，导致 `_drv_n` 恒为 0、
# 把一次本来成功的综合误判成 FATAL 中断。
#
# 正确的校验：**先确认库单元存在**（这是根因），再设。
# `set_driving_cell` 成功时 DC 会对每个受影响端口打印
# `UID-401: Design rule attributes from the driving cell will be set
# on the port '<name>'` —— 本轮实测 14654 条，这就是成功证据；
# 找不到单元时打 `UID-993` 且**不抛异常**。
# **不要给异步复位加 false_path**。
#
# 踩过的坑：原来写的是
#     set_false_path -from [get_ports rst_n] -to [all_registers]
#     set_false_path -from [all_registers]  -to [get_ports rst_n]
# 意图是"复位不走时钟"，但实际效果是 DC 认为复位路径不关心时序，
# 于是对异步复位寄存器做了激进重构（复位网络重连、部分寄存器改同步复位），
# 导致 Formality 报 20 个 failing compare point，全部集中在 u_ctx
# （quiet_cnt_q / conv_valid_q / acq_tag_q 等）。
#
# 正确做法：异步复位的时序由库单元自身保证，不需要 false_path；
# 若要豁免跨时钟检查，用 set_untimed_path 或只对复位网络设 ideal。
# 这里直接删掉这两条，让复位网络保持原样。

# ---- 综合 ----
# ---------------------------------------------------------------- 层次对照实验
#
# **假设（已做过一轮对照，结果：不成立）**：
#   recon 单独综合约 3 分钟完成（22522 µm², slack +24.83），
#   完整顶层跑 19 分钟以上仍停在同一端点。曾怀疑是跨层 ungroup 把
#   除法器输入锥拉到整个数据通路导致优化空间爆炸。
#
# **实测对照（2026-11-16）**：
#   默认设置        : 22 min 超时（rc=124），停在 u_recon/code_o_reg[*]
#   set_ungroup false: 19 min 仍停在同一端点 u_recon/code_o_reg[9]/D
#   => 关掉 ungroup **没有解决**，跨层优化不是主因。
#
# 所以本脚本默认用**默认 ungroup**（与块级结果口径一致），
# 把"为什么顶层比块级慢这么多"留作待查项，而不是假装已定位。
# 若要复跑保留层次的对照，把下面两行取消注释：
# set_ungroup false
# set_boundary_optimization false

# 用 medium effort：compile_ultra 在本设计上耗时过长，暂不作常规入口。
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
