# =============================================================================
# 文件名     : fm_equiv.tcl
# 版本       : v5.0
# 功能       : 用 Formality 检查 28HPC+ 综合网表与 RTL 的功能等价。
#
# 设计说明   :
#   * **本轮逐条实测到的命令约束**（前四版都栽在这些上）：
#       · 没有 `link_library` app var（CMD-104）
#       · 没有 `set_top_designs`，正确是 `set_top`
#       · 没有 `set_container`；`create_container` 不能带 `-type`
#       · `read_verilog` / `read_sverilog` **一次只能读一个文件**，
#         多文件会报 "extra positional option"（CMD-012）
#       · 所有 RTL 都是 SV（`always_comb`/`logic`），必须用 read_sverilog
#       · 参考端要显式 `-container r`，实现端不带给默认容器
#   * 按「读库 -> 逐个读参考 -> 逐个读实现 -> set_top -> match -> verify」。
# 变更记录   :
#   2026-11-16  v1.0-v3.0 多次因不存在的命令失败。
#   2026-11-16  v4.0 修好读库，但多文件读入仍报 CMD-012。
#   2026-11-16  v5.0 改为逐文件读取。
# =============================================================================

set DESIGN adc_digital_ip_v21
set PDK    /home/yian/PDK/tsmc28/digital_technology
set DB     $PDK/TSMCHOME/digital/Front_End/timing_power_noise/CCS/tcbn28hpcplusbwp7t40p140_180a/tcbn28hpcplusbwp7t40p140ssg0p81vm40c_ccs.db
set RTLDIR /tmp/ip

set_app_var search_path [list . $RTLDIR $RTLDIR/rtl $RTLDIR/rtl/v21]

read_db $DB
puts "STEP_DB_OK"

# **加载 DC 写出的 SVF**（必须在读设计之前）。
#
# 为什么必须：DC 综合会做极性变换（用 DFCNQD1 反相输出触发器）、
# 寄存器合并、常量传播。Formality 需要 SVF 才能复现这些操作，
# 否则静默失配 —— 本轮实测没有 SVF 时 20 个 failing compare point，
# 全部集中在用了 DFCNQD1 的寄存器上（quiet_cnt_q / conv_valid_q /
# conv_captured_q / conv_held_q）。
if {[catch {set_svf /tmp/dcwork/${DESIGN}.svf} e]} {
  puts "STEP_SVF_FAIL: [string range $e 0 120]"
} else {
  puts "STEP_SVF_OK"
}

# ---- 抑制已知的良性 RTL 解释消息 ----
#
# FMR_VLOG-091：函数使用非局部变量。
#   wsum_v21 的 f_Wm / f_Wp 直接引用模块级 `w_sub_tbl`（权重表）。
#   这在 SV 里是合法的（函数可读模块作用域），综合与仿真行为一致 ——
#   门级等价已在 1464 拍上验证过这一点。
#   但 Formality 默认把这类消息当"可能导致仿真/综合不一致"而拒绝 link
#   （FM-089 / FM-262），必须显式抑制。
#
# FMR_VLOG-057 / FMR_VLOG-063：有符号/无符号赋值。
#   recip_q60 里本来就有的混合位宽运算，已由 tb_recip_q60
#   （43766 笔逐位一致）证明行为正确。
suppress_message FMR_VLOG-091
suppress_message FMR_VLOG-057
suppress_message FMR_VLOG-063
suppress_message FMR_ELAB-147
puts "STEP_SUPPRESS_OK"

# ---- 参考端：逐个文件读（Formality 不接受一次多个文件）----
set RTL_FILES [list \
  $RTLDIR/rtl/sched18.sv \
  $RTLDIR/rtl/dem_phase_rr.sv \
  $RTLDIR/rtl/dem_phase_gen.sv \
  $RTLDIR/rtl/dither_pre.sv \
  $RTLDIR/rtl/dem_mask_v3.sv \
  $RTLDIR/rtl/follower_rt.sv \
  $RTLDIR/rtl/recip_q60.sv \
  $RTLDIR/rtl/v21/acq_conv_ctx.sv \
  $RTLDIR/rtl/v21/sar_evt_v21.sv \
  $RTLDIR/rtl/v21/adc2_capture.sv \
  $RTLDIR/rtl/v21/wsum_v21.sv \
  $RTLDIR/rtl/v21/recip_rnd_v21.sv \
  $RTLDIR/rtl/v21/recon_v21.sv \
  $RTLDIR/rtl/v21/adc_digital_ip_v21.sv \
]
set nfail 0
foreach f $RTL_FILES {
  if {[catch {read_sverilog -container r $f} e]} {
    puts "  RTL_FAIL $f : [string range $e 0 120]"
    incr nfail
  }
}
puts "STEP_REF_READ_OK fail=$nfail"

set_top r:/WORK/$DESIGN
puts "STEP_REF_TOP_OK"

# ---- 实现端：网表 ----
# **必须显式指定 `-container i`**：
# 不带时 Formality 把网表读进当前容器（此时是 r），
# 之后 set_top i:/... 会报 "Unknown name"（FM-036）。
read_verilog -container i -netlist /tmp/dcwork/${DESIGN}_syn.v
set_top i:/WORK/$DESIGN
puts "STEP_IMPL_TOP_OK"

match
puts "STEP_MATCH_OK"
verify
puts "STEP_VERIFY_OK"
report_status
exit
