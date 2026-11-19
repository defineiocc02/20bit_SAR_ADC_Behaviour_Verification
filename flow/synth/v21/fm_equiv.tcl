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

source [file join [file dirname [file normalize [info script]]] design_inputs.tcl]
set DESIGN adc_digital_ip_v21
set PDK    [v21::pdk]
set DB     $PDK/TSMCHOME/digital/Front_End/timing_power_noise/CCS/tcbn28hpcplusbwp7t40p140_180a/tcbn28hpcplusbwp7t40p140ssg0p81vm40c_ccs.db
set RTLDIR $v21::root
set DC_DIR [v21::dc_dir]
# 加载数据库前先核对整个参考清单、网表与 SVF 的实际字节。
v21::check_dc $DC_DIR $DESIGN
puts "STEP_INPUTS_OK"

set_app_var search_path [list . $RTLDIR $RTLDIR/rtl $RTLDIR/rtl/v21]

read_db $DB
puts "STEP_DB_OK"

# **加载 DC 写出的 SVF**（必须在读设计之前）。
#
# SVF 记录 DC 的常量传播、寄存器合并或极性变换。网表及 SVF 都已按本次
# 检出的收据核验。历史失败点绑定当时网表，见 docs/v21/FORMAL_STATUS.md。
if {[catch {set_svf [file join $DC_DIR ${DESIGN}.svf]} e]} {
  error "STEP_SVF_FAIL: $e"
} else {
  puts "STEP_SVF_OK"
}

# ---- 抑制已知的良性 RTL 解释消息 ----
#
# 历史导入流程在函数读取模块级变量、有符号/无符号转换等情形使用下列消息设置。
# 这些设置仅控制工具导入；结果仍取自本次 match/verify 与退出状态。
suppress_message FMR_VLOG-091
suppress_message FMR_VLOG-057
suppress_message FMR_VLOG-063
suppress_message FMR_ELAB-147
puts "STEP_SUPPRESS_OK"

# ---- 参考端：逐个文件读（Formality 不接受一次多个文件）----
# 参考端采用与综合相同的数字顶层和子模块，ADC2 节点模型由仿真环境使用。
set RTL_FILES [v21::sources]
set nfail 0
foreach f $RTL_FILES {
  if {[catch {read_sverilog -container r $f} e]} {
    puts "  RTL_FAIL $f : [string range $e 0 120]"
    incr nfail
  }
}
if {$nfail} { error "reference files failed to load: $nfail" }
puts "STEP_REF_READ_OK"

set_top r:/WORK/$DESIGN
puts "STEP_REF_TOP_OK"

# ---- 实现端：网表 ----
# **必须显式指定 `-container i`**：
# 不带时 Formality 把网表读进当前容器（此时是 r），
# 之后 set_top i:/... 会报 "Unknown name"（FM-036）。
read_verilog -container i -netlist [file join $DC_DIR ${DESIGN}_syn.v]
set_top i:/WORK/$DESIGN
puts "STEP_IMPL_TOP_OK"

match
puts "STEP_MATCH_OK"
verify
puts "STEP_VERIFY_DONE"
report_status
exit
