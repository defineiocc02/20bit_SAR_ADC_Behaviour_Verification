# =============================================================================
# 文件名     : sta_budget.tcl
# 版本       : v5.0
# 功能       : 对 28HPC+ 综合网表做三类时间预算的 STA 取证。逐条输出
#              **完整体路径信息**（起点/终点/路径组/类型/到达/要求/slack/
#              时钟关系/端口外部延迟），而不是只输出一个 slack。
#
# 设计说明   :
#   * 先读这里，否则会误读数字：
#       1) slack 是**这一条 STA 路径**相对时钟周期的余量。一条路径常常只
#          覆盖某个功能阶段的一小段组合逻辑，所以 `周期 − slack` **不能**
#          当作该功能阶段的可用时间或延迟。本脚本因此从不输出这种换算，
#          而是把 data arrival / data required 原样列出来。
#       2) 时序路径以寄存器为起点与终点，**不会穿过寄存器**。
#          `-through [get_pins *x_q_reg*/Q]` 只是"路径必经点"约束，取到的
#          仍然是"某寄存器 Q → 另一寄存器 D"的单段组合路径。脚本第 3 节
#          专门做一次 -through 实证，把这个语义钉死。
#       3) `rdac_pre_state_o` 是**采样预置（pre）通路**，由 dither_pre 在
#          prepare 时驱动；`rdac_post_state_o` 才是"已决事件 → 物理开关"。
#          两者必须分开报，不能拿 pre 的 slack 去代表 post 的预算。
#       4) 复位（rst_n）起点的最差路径不是功能路径。脚本对每条查询同时给出
#          "含端口起点的最差"与"纯寄存器起点（reg→reg）的最差"，避免把
#          复位分发路径当成功能瓶颈。
#
#   * 输出通道：dc_shell 的 stdout 在本机 locale 下无法编码中文（实测输出
#     全是 `?`），因此**中文叙述一律写入 UTF-8 报告文件**，stdout 只保留
#     纯 ASCII 的 TAG 与数值，便于 grep。
#
#   * 网表是 ungroup 过的：寄存器名不带层次前缀、形如 `\conv_q_reg[0]/Q`，
#     所以 get_pins 的 full_name 模式**必须带前导 `*`**（`*conv_q_reg*`）；
#     写成 `conv_q_reg*` 返回 0 —— 之前踩过的坑。
#
#   * 驱动单元：本库没有 `BUF_X2`，只有 `BUFFD2BWP7T40P140`（I/Z 两脚）。
#     旧脚本写 `-lib_cell BUF_X2` 会报 UID-993 且**整条命令不生效**，
#     于是所有输入端口实际没有任何 driving cell。本脚本显式记录该错误，
#     并做一次"换成库内合法 BUFFD2"的敏感性对照，两次数字都如实给出。
#
#   * 取数口径：本脚本的约束集（25 ns / setup 0.30 / hold 0.15 /
#     clock transition 0.10 / IO delay 各 1.0 ns / 负载 0.05 pF）与综合时
#     `report_timing` 不同 —— dc_synth_v21.tcl 里**没有** set_input_delay
#     与 set_output_delay。两套数字不可混用。
# 变更记录   :
#   2026-11-16  v1.0 按层次名分组（ungroup 后全 N/A）。
#   2026-11-16  v2.0 改按功能信号名（起终点共享寄存器族，仍 N/A）。
#   2026-11-16  v3.0 改 -through 切分（解释错误，已在 v5.0 纠正）。
#   2026-11-16  v4.0 逐条输出完整体；区分单段组合路径与功能阶段。
#   2026-11-16  v5.0 中文改写 UTF-8 文件（stdout 只留 ASCII）；
#                    reg→reg 与含端口起点分开报，剔除复位路径干扰；
#                    -through 语义实证；driving cell 双口径对照。
# =============================================================================

set DESIGN  adc_digital_ip_v21
set PDK     /home/yian/PDK/tsmc28/digital_technology
set LIBNAME tcbn28hpcplusbwp7t40p140ssg0p81vm40c_ccs
set DB      $PDK/TSMCHOME/digital/Front_End/timing_power_noise/CCS/tcbn28hpcplusbwp7t40p140_180a/${LIBNAME}.db
set NETLIST /tmp/dcwork/${DESIGN}_syn.v
set OUTDIR  /tmp/sta_budget_out
set PERIOD  25.0

file mkdir $OUTDIR
file mkdir $OUTDIR/paths

set_app_var search_path [list . /tmp]
set_app_var target_library [list $DB]
set_app_var link_library   [list "*" $DB]

read_verilog -netlist $NETLIST
current_design $DESIGN
link

# ---------------------------------------------------------------- 约束集
create_clock -name clk -period $PERIOD [get_ports clk]
set_clock_uncertainty -setup 0.30 [get_clocks clk]
set_clock_uncertainty -hold  0.15 [get_clocks clk]
set_clock_transition 0.10 [get_clocks clk]
set_input_delay  1.0 -clock clk [remove_from_collection [all_inputs] [get_ports clk]]
set_output_delay 1.0 -clock clk [all_outputs]
set_load 0.05 [all_outputs]

# ---------------------------------------------------------------- 输出通道
# 中文叙述写 UTF-8 文件；stdout 只走 ASCII，避免 locale 把中文打成 `?`。
set RPT [open $OUTDIR/timing_budget_audit.md w]
fconfigure $RPT -encoding utf-8
proc zh {s} {
  global RPT
  puts $RPT $s
}
proc ascii {s} { puts $s }

# ---------------------------------------------------------------- 工具函数
proc attr {obj name} {
  if {[catch {get_attribute $obj $name} v]} { return "<n/a>" }
  if {$v eq ""} { return "<empty>" }
  return $v
}
proc oclass {obj} {
  if {[catch {get_attribute $obj object_class} v]} { return "<n/a>" }
  return $v
}
proc tf {v} { return [format %.3f $v] }

# 在报告文件里写一条路径的完整信息；同时把 report_timing 全文落盘。
#
# optlist 是 get_timing_paths / report_timing 的公共选项（-from/-to/-through）。
# 为什么 arrival 与 required 都要写：
#   slack 只是差值；只有并列 arrival/required 才能看出这条路径用了多少
#   组合延迟、相对什么要求时刻，也才不会被误当成"阶段可用时间"。
proc dump_path {tag title optlist} {
  global RPT OUTDIR
  set paths [eval get_timing_paths -max_paths 1 -nworst 1 -delay_type max $optlist]
  zh ""
  zh "#### $tag — $title"
  zh ""
  if {[sizeof_collection $paths] == 0} {
    zh "| 项 | 值 |"
    zh "|:---|:---|"
    zh "| 结果 | N/A（该查询没有匹配路径） |"
    ascii "TAG $tag N/A"
    return
  }
  set p   [index_collection $paths 0]
  set sp  [get_attribute $p startpoint]
  set ep  [get_attribute $p endpoint]
  set spn [get_object_name $sp]
  set epn [get_object_name $ep]
  set sl  [get_attribute $p slack]
  set arr [get_attribute $p arrival]
  set req [get_attribute $p required]
  set pg  [get_object_name [get_attribute $p path_group]]
  set sc  [get_object_name [get_attribute $p startpoint_clock]]
  set ec  [get_object_name [get_attribute $p endpoint_clock]]
  set pty [attr $p path_type]
  set ide [attr $p startpoint_input_delay_value]
  set oed [attr $p endpoint_output_delay_value]
  set st  [expr {$sl >= 0 ? "MET" : "VIOLATED"}]
  set sls [expr {$sl >= 0 ? [format "+%.3f" $sl] : [format "%.3f" $sl]}]

  zh "| 项 | 值 |"
  zh "|:---|:---|"
  zh "| Startpoint | `$spn` \[[oclass $sp]\] |"
  zh "| Endpoint | `$epn` \[[oclass $ep]\] |"
  zh "| Path Group | $pg |"
  zh "| Path Type | $pty |"
  zh "| Launch clock → Capture clock | $sc → $ec（[expr {$sc eq $ec ? "同一时钟" : "跨时钟"}]） |"
  zh "| data arrival time | **[tf $arr] ns** |"
  zh "| data required time | **[tf $req] ns** |"
  zh "| slack | **$sls ns（$st）** |"
  zh "| 起点 input external delay | $ide |"
  zh "| 终点 output external delay | $oed |"
  zh "| path_length / logic_depth | [attr $p path_length] / [attr $p logic_depth] |"
  zh "| is_multicycle / path_infeasible | [attr $p is_multicycle] / [attr $p path_infeasible] |"

  ascii [format "TAG %-26s slack=%8.3f arrival=%7.3f required=%7.3f start=%s end=%s" \
             $tag $sl $arr $req $spn $epn]

  # 完整体全文落盘：审阅时不必重跑 DC。
  # 用全局变量传选项，因为 redirect 的 body 是延迟求值的脚本块，
  # 直接内插 $optlist 会把其中的方括号当成命令替换。
  set ::rt_opts $optlist
  redirect $OUTDIR/paths/${tag}.rpt {
    eval report_timing -max_paths 1 -nworst 1 -delay_type max \
         -path_type full_clock_expanded $::rt_opts
  }
}

proc section {n title} {
  zh ""
  zh "## $n"
  zh ""
  zh "<!-- $title -->"
  ascii "SECTION $n"
}

# ---------------------------------------------------------------- 0. 环境与口径
section "0. 环境与口径" "ENV"

set md5 "unavailable"
catch {set md5 [lindex [split [exec md5sum $NETLIST]] 0]}
zh "| 项 | 值 |"
zh "|:---|:---|"
zh "| design | `[get_object_name [current_design]]` |"
zh "| netlist | `$NETLIST`（md5 `$md5`） |"
zh "| library | `$LIBNAME`（SS 0.81 V −40 °C） |"
zh "| 约束 | 周期 $PERIOD ns；setup uncertainty 0.30 ns；hold 0.15 ns；clock transition 0.10 ns；IO delay 各 1.0 ns；输出负载 0.05 pF |"
zh "| 端口 | 输入 [sizeof_collection [all_inputs]]，输出 [sizeof_collection [all_outputs]] |"
zh "| 寄存器 | [sizeof_collection [all_registers]]，受 clk 驱动 [sizeof_collection [all_registers -clock clk]]，**未时钟 [sizeof_collection [remove_from_collection [all_registers] [all_registers -clock clk]]]** |"

# wire load：ZeroWireload 的实际参数要写清楚，否则容易被当成"有互连线模型"。
catch {report_wire_load -nosplit} wl
zh ""
zh "<details><summary>report_wire_load 原文</summary>"
zh ""
zh '```'
zh $wl
zh '```'
zh ""
zh "</details>"
ascii "WIRELOAD captured"

# ---------------------------------------------------------------- 1. 驱动单元
section "1. 驱动单元核实" "DRIVING_CELL"

set NO_CLK_IN [remove_from_collection [all_inputs] [get_ports clk]]

zh "旧脚本写的是："
zh ""
zh '```tcl'
zh "set_driving_cell -lib_cell BUF_X2 -pin Z [remove_from_collection [all_inputs] [get_ports clk]]"
zh '```'
zh ""
if {[catch {set_driving_cell -lib_cell BUF_X2 -pin Z $NO_CLK_IN} e1]} {
  zh "实测结果：**失败**。"
  zh ""
  zh '```'
  zh $e1
  zh '```'
  zh ""
  zh "后果：整条命令不生效，**全部输入端口没有任何 driving cell**。"
  ascii "DRV BUF_X2 FAILED (UID-993)"
} else {
  zh "实测结果：成功（本库确实有 BUF_X2）。"
  ascii "DRV BUF_X2 OK"
}
zh ""
zh "本库真实存在的缓冲器："
zh ""
zh '```'
zh [get_object_name [get_lib_cells */BUFF*]]
zh '```'
zh ""
zh "BUFFD2 的引脚：`[get_object_name [get_lib_pins */BUFFD2BWP7T40P140/*]]`"
zh ""

# 口径 A：不加驱动（= 旧脚本实际生效的状态）
redirect $OUTDIR/port_nodrv.rpt {report_port -verbose [get_ports {prepare_valid rst_n ev_bit_valid_i sample_event}]}
zh "**口径 A（无 driving cell）**端口属性见 `$OUTDIR/port_nodrv.rpt`。"
zh ""

# 口径 B：补上库内合法 BUFFD2
set drvB 0
if {[catch {set_driving_cell -lib_cell BUFFD2BWP7T40P140 -pin Z $NO_CLK_IN} e2]} {
  zh "**口径 B（BUFFD2BWP7T40P140）**建立失败：$e2"
  ascii "DRV BUFFD2 FAILED"
} else {
  set drvB 1
  redirect $OUTDIR/port_drv.rpt {report_port -verbose [get_ports {prepare_valid rst_n ev_bit_valid_i sample_event}]}
  zh "**口径 B（BUFFD2BWP7T40P140）**建立成功；端口属性见 `$OUTDIR/port_drv.rpt`。"
  zh ""
  zh "对照两个文件里同一端口（如 `ev_bit_valid_i`）的 Max Tran 一栏，"
  zh "即可看出驱动单元到底有没有绑上。"
  ascii "DRV BUFFD2 OK"
}

# ---------------------------------------------------------------- 2. 全局最差
section "2. 全局最差 setup" "GLOBAL"

dump_path GLOBAL_REG2REG \
  "全局最差 setup（**纯寄存器起点**，reg→reg；排除端口起点）" \
  [list -from [get_pins -hier -filter {full_name =~ */CP}]] 

dump_path GLOBAL_ANY \
  "全局最差 setup（不限起终点，含端口路径）" \
  [list]

# 纯端口起点的最差，单独列出，用于驱动敏感性对照。
dump_path GLOBAL_FROM_INPUT \
  "起点为输入端口的最差 setup" \
  [list -from [all_inputs]]

# ---------------------------------------------------------------- 3. -through 语义实证
section "3. `-through` 语义实证（为什么它不是\"跨多级寄存器\"）" "THROUGH"

set thru_conv [get_pins -hier -filter {full_name =~ *conv_q_reg*/Q}]
set to_acqids [get_pins -hier -filter {full_name =~ *acq_ids_q_reg*/D}]

zh "旧报告写："
zh ""
zh "> 用 \`-through [conv_*_q_reg/Q]\` 再 \`-to [acq_ids_q_reg/D]\`，把跨多级寄存器的阶段切出来"
zh ""
zh "下面的实测表明：这样取到的路径 **startpoint 就是 conv_q_reg 这个寄存器本身**，"
zh "即它是\"从该寄存器 Q 出发、到 acq_ids_q 的 D 为止\"的**单段组合路径**，"
zh "中间没有（也不可能）穿过任何寄存器。"
zh ""

dump_path THROUGH_PROOF \
  "v3.0 的写法：-through [conv_q_reg/Q] -to [acq_ids_q_reg/D]" \
  [list -through $thru_conv -to $to_acqids]

zh ""
zh "结论：时序路径以寄存器为界。要把\"多级功能阶段\"的总延迟算清楚，"
zh "必须做**事务级/多拍**分析（数清中间跨了几拍、每拍各自的组合段多长），"
zh "STA 逐条路径的 slack 不能相加，也不能当作阶段预算。"
ascii "THROUGH proof recorded"

# ---------------------------------------------------------------- 4. 三类预算
section "4. 三类预算逐段实测" "BUDGETS"

zh "下面每一段都是**一条独立的单段组合路径**。段与段之间隔着寄存器，"
zh "因此**不能相加**，也不能当成某个功能阶段的总延迟。"
zh ""
zh "每条查询同时给两行："
zh ""
zh "- **含端口起点**：可能被复位（rst_n）分发路径占据，不代表功能路径；"
zh "- **reg→reg**：起点限定为寄存器时钟脚，才是功能逻辑的组合段。"
zh ""

# ---- 4.1 ① 释放 → 采集 ----
zh ""
zh "### 4.1 ① 旧组释放 → 下一组采集"
zh ""

dump_path B1A_QUIET_TO_CONV \
  "安静窗计数寄存器 Q → CONV 有效寄存器 D（释放判定的单段组合）" \
  [list -from [get_pins -hier -filter {full_name =~ *quiet_cnt_q_reg*/Q}] \
        -to   [get_pins -hier -filter {full_name =~ *conv_valid_q_reg*/D}]]

dump_path B1B_ACQ_IDS_PORTSTART \
  "→ ACQ 描述符片号寄存器 D（含端口起点的最差；注意起点可能是 rst_n）" \
  [list -to $to_acqids]

dump_path B1C_ACQ_IDS_REG2REG \
  "→ ACQ 描述符片号寄存器 D（**reg→reg**，剔除复位路径）" \
  [list -from [get_pins -hier -filter {full_name =~ */CP}] -to $to_acqids]

dump_path B1D_ACQ_SEL_OUT \
  "ACQ 片组选通输出端口 acq_sel_o（单段）" \
  [list -to [get_ports acq_sel_o]]

# ---- 4.2 ② 已决事件 → RDAC 开关（post）----
zh ""
zh "### 4.2 ② 已决事件 → 物理 RDAC post 开关"
zh ""
zh "post 通路在寄存器处断开，天然是两段："
zh ""
zh "1. 事件译码 / DEM 展开 → `post_state_q` 的 D（寄存器前）；"
zh "2. `post_state_q` 的 Q → `rdac_post_state_o`（寄存器后，纯输出适配）。"
zh ""

dump_path B2A_POST_D_PORTSTART \
  "→ post_state_q 的 D（含端口起点的最差）" \
  [list -to [get_pins -hier -filter {full_name =~ *post_state_q_reg*/D}]]

dump_path B2B_POST_D_REG2REG \
  "→ post_state_q 的 D（**reg→reg**，这才是事件译码的组合段）" \
  [list -from [get_pins -hier -filter {full_name =~ */CP}] \
        -to   [get_pins -hier -filter {full_name =~ *post_state_q_reg*/D}]]

dump_path B2C_FROM_EXP_EVT_REG2REG \
  "exp_evt_q 的 Q → 下游寄存器 D（事件层内部单段，reg→reg）" \
  [list -from [get_pins -hier -filter {full_name =~ *exp_evt_q_reg*/Q}] \
        -to   [get_pins -hier -filter {full_name =~ */D}]]

dump_path B2D_POST_ADAPTER \
  "post_state_q 的 Q → rdac_post_state_o 端口（寄存器后适配段）" \
  [list -to [get_ports rdac_post_state_o]]

dump_path B2E_POST_WR_OUT \
  "post_wr_q 的 Q → post_wr_o 端口（写门适配段）" \
  [list -to [get_ports post_wr_o]]

# pre 通路必须单列：它是采样预置，不是已决事件。
zh ""
zh "#### 采样预置（pre）通路 —— **不要**和上面的 post 预算混为一谈"
zh ""
zh "`rdac_pre_state_o` 由 `dither_pre` 在 **prepare** 时给出（RTL："
zh "`acq_conv_ctx` 的 `pre_state_c` 直接驱动，见 `rtl/v21/acq_conv_ctx.sv`"
zh "「物理路由」一节），与\"已决事件 → post 开关\"是两条不同的通路。"
zh ""

dump_path B2F_PRE_PORTSTART \
  "→ rdac_pre_state_o 端口（含端口起点的最差）" \
  [list -to [get_ports rdac_pre_state_o]]

dump_path B2G_PRE_REG2REG \
  "→ rdac_pre_state_o 端口（起点限定寄存器 Q，看真正的 pre 组合深度）" \
  [list -from [get_pins -hier -filter {full_name =~ */CP}] -to [get_ports rdac_pre_state_o]]

# ---- 4.3 ③ arm / hold / quiet ----
zh ""
zh "### 4.3 ③ arm / hold / quiet（末次生效 → ADC2 捕获）"
zh ""

dump_path B3A_ARM_OUT \
  "→ adc2_capture_arm_o 端口（沿前授权）" \
  [list -to [get_ports adc2_capture_arm_o]]

dump_path B3B_ARM_OUT_REG2REG \
  "→ adc2_capture_arm_o 端口（起点限定寄存器 Q）" \
  [list -from [get_pins -hier -filter {full_name =~ */CP}] -to [get_ports adc2_capture_arm_o]]

dump_path B3C_HOLD_DONE \
  "→ hold_done_q 的 D（hold 记录单段）" \
  [list -to [get_pins -hier -filter {full_name =~ *hold_done_q_reg*/D}]]

dump_path B3D_QUIET_CNT \
  "→ quiet_cnt_q 的 D（安静窗计数单段）" \
  [list -to [get_pins -hier -filter {full_name =~ *quiet_cnt_q_reg*/D}]]

dump_path B3E_QUIET_CNT_REG2REG \
  "→ quiet_cnt_q 的 D（reg→reg）" \
  [list -from [get_pins -hier -filter {full_name =~ */CP}] \
        -to   [get_pins -hier -filter {full_name =~ *quiet_cnt_q_reg*/D}]]

dump_path B3F_FINAL_POST_REG2REG \
  "final_post_q 的 Q → 下游寄存器 D（reg→reg）" \
  [list -from [get_pins -hier -filter {full_name =~ *final_post_q_reg*/Q}] \
        -to   [get_pins -hier -filter {full_name =~ */D}]]

# ---------------------------------------------------------------- 5. 驱动敏感性
section "5. 驱动单元敏感性对照" "DRV_SENS"

if {$drvB} {
  dump_path DRV_B_WITH_BUFFD2 \
    "口径 B：已绑 BUFFD2 驱动，起点为输入端口的最差 setup" \
    [list -from [all_inputs]]
  remove_driving_cell $NO_CLK_IN
  dump_path DRV_A_NO_DRIVING \
    "口径 A：已解除驱动，起点为输入端口的最差 setup" \
    [list -from [all_inputs]]
  zh ""
  zh "两条数字的差额就是\"驱动单元绑定错误\"对端口起点路径的影响量级。"
  ascii "DRV_SENS done"
} else {
  dump_path DRV_A_NO_DRIVING \
    "口径 A：无 driving cell，起点为输入端口的最差 setup" \
    [list -from [all_inputs]]
  ascii "DRV_SENS skipped-B"
}

# ---------------------------------------------------------------- 6. 约束汇总
section "6. 约束与违例汇总" "CONSTRAINTS"

redirect $OUTDIR/constraint_max.rpt {report_constraint -all_violators -max_delay}
redirect $OUTDIR/constraint_min.rpt {report_constraint -all_violators -min_delay}
set rptmax [open $OUTDIR/constraint_max.rpt r]
set maxbody [read $rptmax]
close $rptmax
set rptmin [open $OUTDIR/constraint_min.rpt r]
set minbody [read $rptmin]
close $rptmin
set nmax [regexp -all {VIOLATED} $maxbody]
set nmin [regexp -all {VIOLATED} $minbody]

zh "| 检查 | 违例条数 | 说明 |"
zh "|:---|---:|:---|"
zh "| setup（max_delay） | $nmax | [expr {$nmax == 0 ? "无 setup 违例" : "存在 setup 违例"}] |"
zh "| hold（min_delay） | $nmin | 综合网表、理想时钟树下的 hold 检查结果（见下） |"

zh ""
zh "**hold 必须单独说清楚**：本网表是综合产物、时钟为理想时钟（零 skew），"
zh "hold 违例在 CTS 之前是常见现象，不能据此判定设计失败；但也不能像旧报告那样"
zh "只写一句\"本报告只做 setup\"就算交代过去 —— 违例条数与最差值要如实列出。"
zh ""

dump_path HOLD_WORST \
  "全局最差 hold（delay_type min）" \
  [list -delay_type min]
# 上面 dump_path 固定用 -delay_type max，这里单独补一条 hold 查询。
set hp [get_timing_paths -max_paths 1 -nworst 1 -delay_type min]
if {[sizeof_collection $hp]} {
  set ph [index_collection $hp 0]
  zh ""
  zh "**hold 最差一条**："
  zh ""
  zh "| 项 | 值 |"
  zh "|:---|:---|"
  zh "| Startpoint | `[get_object_name [get_attribute $ph startpoint]]` |"
  zh "| Endpoint | `[get_object_name [get_attribute $ph endpoint]]` |"
  zh "| data arrival | [tf [get_attribute $ph arrival]] ns |"
  zh "| data required | [tf [get_attribute $ph required]] ns |"
  zh "| slack | [tf [get_attribute $ph slack]] ns |"
  ascii [format "TAG HOLD_WORST  slack=%8.3f arrival=%7.3f required=%7.3f start=%s end=%s" \
           [get_attribute $ph slack] [get_attribute $ph arrival] [get_attribute $ph required] \
           [get_object_name [get_attribute $ph startpoint]] [get_object_name [get_attribute $ph endpoint]]]
}
ascii "VIOL setup=$nmax hold=$nmin"

zh ""
zh "<details><summary>report_constraint -max_delay 原文</summary>"
zh ""
zh '```'
zh $maxbody
zh '```'
zh "</details>"
zh ""
zh "<details><summary>report_constraint -min_delay 原文（节选：前 60 行）</summary>"
zh ""
zh '```'
zh [join [lrange [split $minbody "\n"] 0 60] "\n"]
zh '```'
zh "</details>"

# ---------------------------------------------------------------- 收尾
zh ""
zh "---"
zh ""
zh "完整体全文目录：\`$OUTDIR/paths/\`（每条查询一个 \`.rpt\`，"
zh "由 \`report_timing -path_type full_clock_expanded\` 生成）。"
close $RPT
ascii "===BUDGET_END==="
exit
