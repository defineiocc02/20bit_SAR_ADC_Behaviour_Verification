# =============================================================================
# 文件名     : sta_budget.tcl
# 版本       : v6.0
# 功能       : 对 28HPC+ 综合网表做三类时间预算的 STA 取证。逐条输出
#              **完整体路径信息**（起点/终点/路径组/类型/到达/要求/slack/
#              时钟关系/端口外部延迟），而不是只输出一个 slack。
#
# 设计说明   :
#   * 先读这里，否则会误读数字：
#       1) slack 是**这一条 STA 路径**相对时钟周期的余量。一条路径常常只
#          覆盖某个功能阶段的一小段组合逻辑，所以 `周期 − slack` **不能**
#          当作该功能阶段的可用时间或延迟。本脚本因此从不输出这种换算，
#          只把 data arrival / data required 原样列出。
#       2) 时序路径以寄存器为起点与终点，**不会穿过寄存器**。
#          `-through [get_pins *x_q_reg*/Q]` 只是"路径必经点"约束；它取到的
#          仍然是"某寄存器 Q → 另一寄存器 D / 端口"的单段组合路径。
#          脚本第 3 节用实测把这条语义钉死（对比 -through 与 -from 的结果）。
#       3) `rdac_pre_state_o` 是**采样预置（pre）通路**，由 dither_pre 在
#          prepare 时驱动；`rdac_post_state_o` 才是"已决事件 → 物理开关"。
#          两者必须分开报，不能拿 pre 的 slack 去代表 post 的预算。
#       4) 以复位 `rst_n` 为起点的路径不是功能路径。脚本对每条查询同时给出
#          "含端口起点的最差"与"纯寄存器起点（reg→reg）的最差"，避免把
#          复位分发路径当成功能瓶颈。
#
#   * 输出通道：dc_shell 的 stdout 在本机 locale 下无法编码中文（实测输出
#     全是 `?`），所以**中文叙述一律写入 UTF-8 报告文件**，stdout 只保留
#     纯 ASCII 的 TAG/SECTION/数值，便于 grep。
#
#   * 网表是 ungroup 过的：寄存器名不带层次前缀、形如 `\conv_q_reg[0]/Q`，
#     所以 get_pins 的 full_name 模式**必须带前导 `*`**（`*conv_q_reg*`）；
#     写成 `conv_q_reg*` 返回 0 —— 之前踩过的坑。
#
#   * 驱动单元：本库没有 `BUF_X2`，只有 `BUFFD2BWP7T40P140`（I/Z 两脚）。
#     旧脚本写 `-lib_cell BUF_X2` 会报 UID-993 且**整条命令不生效**，
#     于是所有输入端口实际没有任何 driving cell。
#     **注意**：该失败不会让 Tcl 抛异常，`catch` 抓不到；必须回读
#     `report_port -verbose` 里 "Driving Cell" 一栏才算核实。
#     本脚本用这个回读做判定，并给出启用/未启用驱动的敏感性对照。
#
#   * 取数口径：本脚本的约束集（25 ns / setup 0.30 / hold 0.15 /
#     clock transition 0.10 / IO delay 各 1.0 ns / 负载 0.05 pF）与综合时
#     `report_timing` 不同 —— dc_synth_v21.tcl 里**没有** set_input_delay
#     与 set_output_delay。两套数字不可混用。
# 变更记录   :
#   2026-11-16  v1.0 按层次名分组（ungroup 后全 N/A）。
#   2026-11-16  v2.0 改按功能信号名（起终点共享寄存器族，仍 N/A）。
#   2026-11-16  v3.0 改 -through 切分（解释错误，已在 v6.0 纠正）。
#   2026-11-16  v4.0 逐条输出完整体；区分单段组合路径与功能阶段。
#   2026-11-16  v5.0 中文改写 UTF-8 文件；reg→reg 与端口起点分开报。
#   2026-11-16  v6.0 driving cell 改用 report_port 回读判定（catch 无效）；
#                   -through 对照 -from 做语义实证；hold 不再重复查询。
# =============================================================================

source [file join [file dirname [file normalize [info script]]] design_inputs.tcl]
set DESIGN  adc_digital_ip_v21
set PDK     [v21::pdk]
set LIBNAME tcbn28hpcplusbwp7t40p140ssg0p81vm40c_ccs
set DB      $PDK/TSMCHOME/digital/Front_End/timing_power_noise/CCS/tcbn28hpcplusbwp7t40p140_180a/${LIBNAME}.db
set NETLIST [file join [v21::dc_dir] ${DESIGN}_syn.v]
set OUTDIR  [file join [pwd] sta_budget]
set PERIOD  25.0

# 允许用环境变量切换网表/输出目录：同一份脚本要能对"基线网表"和"最新网表"
# 各跑一次做对照，固定本次输入，避免沿用其他目录的结果（实际踩过）。
if {[info exists ::env(STA_NETLIST)]} {
  # 历史取数仍允许显式选网表，报告保留实际输入身份。
  set NETLIST [file normalize $::env(STA_NETLIST)]
} else {
  v21::check_dc [v21::dc_dir] $DESIGN
}
if {[info exists ::env(STA_OUTDIR)]}  { set OUTDIR  $::env(STA_OUTDIR) }

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
proc tf {v} { return [format %.3f $v] }

# 读回 report_port -verbose 全文，判据：端口若绑了驱动单元，该栏会出现
# "<lib_cell>/Z"。返回该文本，供调用者用 regexp 判定。
proc port_body {portname} {
  global OUTDIR
  set f "$OUTDIR/_port_${portname}.rpt"
  redirect $f "report_port -verbose \[get_ports {$portname}\]"
  set fh [open $f r]
  set body [read $fh]
  close $fh
  return $body
}

# 在报告文件里写一条路径的完整信息；同时把 report_timing 全文落盘。
#
# optlist 是 get_timing_paths 的选项列表（-from/-to/-through/-delay_type）。
# 为什么 arrival 与 required 都要写：
#   slack 只是差值；只有并列 arrival/required 才能看出这条路径用了多少
#   组合延迟、相对什么要求时刻，也才不会被误当成"阶段可用时间"。
proc dump_path {tag title optlist} {
  global RPT OUTDIR
  set paths [eval get_timing_paths -max_paths 1 -nworst 1 $optlist]
  zh ""
  zh "#### $tag — $title"
  zh ""
  zh "| 项 | 值 |"
  zh "|:---|:---|"
  if {[sizeof_collection $paths] == 0} {
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
  set scc [get_attribute $sp object_class]
  set ecc [get_attribute $ep object_class]
  set st  [expr {$sl >= 0 ? "MET" : "VIOLATED"}]
  set sls [expr {$sl >= 0 ? [format "+%.3f" $sl] : [format "%.3f" $sl]}]
  # 起点/终点是否端口，决定 external delay 该不该有值。
  set sp_is_port [expr {$scc eq "port"}]
  set ep_is_port [expr {$ecc eq "port"}]

  zh "| Startpoint | `$spn` \[$scc\] |"
  zh "| Endpoint | `$epn` \[$ecc\] |"
  zh "| Path Group | $pg |"
  zh "| Path Type | $pty |"
  zh "| Launch → Capture clock | $sc → $ec（[expr {$sc eq $ec ? "同一时钟" : "跨时钟"}]） |"
  zh "| **data arrival time** | **$arr ns**（[tf $arr] ns） |"
  zh "| **data required time** | **$req ns**（[tf $req] ns） |"
  zh "| **slack** | **$sls ns（$st）** |"
  zh "| 起点 input external delay | [expr {$sp_is_port ? "$ide（起点是输入端口，故有外部延迟）" : "N/A（起点是寄存器，无外部延迟）"}] |"
  zh "| 终点 output external delay | [expr {$ep_is_port ? "$oed（终点是输出端口，故有外部延迟）" : "N/A（终点是寄存器，无外部延迟）"}] |"
  zh "| path_length / logic_depth | [attr $p path_length] / [attr $p logic_depth] |"
  zh "| is_multicycle / path_infeasible | [attr $p is_multicycle] / [attr $p path_infeasible] |"

  ascii [format "TAG %-28s slack=%8.3f arrival=%7.3f required=%7.3f start=%s end=%s" \
             $tag $sl $arr $req $spn $epn]

  # 完整体全文落盘。选项经全局变量传入：redirect 的 body 是延迟求值的
  # 脚本块，直接内插 $optlist 会把其中的方括号当成命令替换（v5.0 踩过）。
  set ::rt_opts $optlist
  redirect $OUTDIR/paths/${tag}.rpt {
    eval report_timing -max_paths 1 -nworst 1 -path_type full_clock_expanded $::rt_opts
  }
}

proc section {n title} {
  zh ""
  zh "## $n"
  zh ""
  zh "<!-- $title -->"
  ascii "SECTION $n"
}

set REG_Q [get_pins -hier -filter {full_name =~ */CP}]
# 端点的落脚点是触发器的 D 脚（不是 CP）；-to 用 CP 会返回 N/A。
set REG_D [get_pins -hier -filter {full_name =~ */D}]

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

catch {report_wire_load -nosplit} wl
zh ""
zh "<details><summary>report_wire_load 原文</summary>"
zh ""
zh "```"
zh $wl
zh "```"
zh "</details>"
ascii "WIRELOAD captured"

# ---------------------------------------------------------------- 1. 驱动单元
section "1. 驱动单元核实" "DRIVING_CELL"

set NO_CLK_IN [remove_from_collection [all_inputs] [get_ports clk]]
set PROBE_PORT ev_bit_valid_i

# 先把驱动清掉，确保下面测的是"脚本自己设的"，不是上一轮残留。
catch {remove_driving_cell $NO_CLK_IN}
set body0 [port_body $PROBE_PORT]
set has0 [regexp {BWP7T40P140/Z} $body0]

zh "### 1.1 旧脚本的写法"
zh ""
zh "```tcl"
zh "set_driving_cell -lib_cell BUF_X2 -pin Z \[remove_from_collection \[all_inputs\] \[get_ports clk\]\]"
zh "```"
zh ""
zh "本库的缓冲器清单（`get_lib_cells */BUFF*`）："
zh ""
zh "```"
zh [get_object_name [get_lib_cells */BUFF*]]
zh "```"
zh ""
zh "没有 `BUF_X2`。执行该命令的实际后果（stdout 侧日志）："
zh ""
zh "```"
zh "Error: Cannot find the specified driving cell in memory.   (UID-993)"
zh "```"
zh ""
zh "**关键**：这条 Error 不会让 Tcl 抛异常，`catch` 抓不到；"
zh "`if \{catch ...\}` 会走成功分支，误以为驱动设上了。"
zh "唯一可靠的核实办法是回读 `report_port -verbose` 的 \"Driving Cell\" 一栏。"
zh ""

# 实测：不带驱动时端口上确实没有驱动
zh "### 1.2 实测（探针端口 \`$PROBE_PORT\`）"
zh ""
zh "| 状态 | `report_port` 里是否出现驱动单元 |"
zh "|:---|:---|"
zh "| 未设驱动 | [expr {$has0 ? "**出现**（异常）" : "**不出现**（符合预期：无驱动）"}] |"
ascii "DRV probe_before_clear cell_present=$has0"
catch {remove_driving_cell $NO_CLK_IN}

# A/B 必须量**同一条**路径，否则比的是两条不同的逻辑，说明不了驱动的贡献。
# 所以固定探针端口，只查从它出发的最差路径。
set PORTS_PROBE [get_ports $PROBE_PORT]

# 口径 A：不设驱动（= 旧脚本实际生效的状态）
dump_path DRV_A_NO_DRIVING \
  "口径 A（不设 driving cell）：从输入端口 $PROBE_PORT 出发的最差 setup" \
  [list -delay_type max -from $PORTS_PROBE]

# 口径 B：补上库内合法 BUFFD2
set drvB 0
set_driving_cell -lib_cell BUFFD2BWP7T40P140 -pin Z $NO_CLK_IN
set bodyB [port_body $PROBE_PORT]
if {[regexp {BWP7T40P140/Z} $bodyB]} { set drvB 1 }
zh ""
zh "| 状态 | `report_port` 里是否出现驱动单元 |"
zh "|:---|:---|"
zh "| 设 \`BUFFD2BWP7T40P140\` | [expr {$drvB ? "**出现**（驱动已生效）" : "**不出现**（仍未生效）"}] |"
ascii "DRV probe_after_set cell_present=$drvB"

if {$drvB} {
  dump_path DRV_B_WITH_BUFFD2 \
    "口径 B（已绑 BUFFD2 驱动）：从输入端口 $PROBE_PORT 出发的最差 setup" \
    [list -delay_type max -from $PORTS_PROBE]
} else {
  ascii "TAG DRV_B_WITH_BUFFD2 unavailable"
}

zh ""
zh "两条查询的起点/终点应当**完全相同**，slack 之差才是驱动单元的贡献。"
zh ""
zh "> 说明：旧报告里所有端口起点路径的 slack 都是在**无驱动**"
zh "> （即最乐观的零输入转换）下算出来的。真实上游驱动更弱时，"
zh "> 这些端口路径只会更差；因此端口路径的 slack 应视为**乐观上界**。"
zh ""
zh "端口属性全文：`$OUTDIR/_port_${PROBE_PORT}.rpt`（口径 B 覆盖写）。"
ascii "DRV_SENS done"

# ---------------------------------------------------------------- 2. 全局最差
section "2. 全局最差 setup" "GLOBAL"

dump_path GLOBAL_REG2REG \
  "全局最差 setup（**纯寄存器起点**，reg→reg）" \
  [list -delay_type max -from $REG_Q]

dump_path GLOBAL_ANY \
  "全局最差 setup（不限起终点，含端口路径）" \
  [list -delay_type max]

dump_path GLOBAL_FROM_INPUT \
  "起点为输入端口的最差 setup" \
  [list -delay_type max -from [all_inputs]]

# ---------------------------------------------------------------- 3. -through 语义实证
section "3. \`-through\` 语义实证（为什么它不是\"跨多级寄存器\"）" "THROUGH"

# **注意**：这里必须用 *conv_*_q_reg*（带前导 *），否则匹配到 0 个引脚。
set thru_conv [get_pins -hier -filter {full_name =~ *conv_*_q_reg*/Q}]
set to_acqids [get_pins -hier -filter {full_name =~ *acq_ids_q_reg*/D}]
set from_conv [get_pins -hier -filter {full_name =~ *conv_*_q_reg*/Q}]

zh "旧报告写："
zh ""
zh "> 用 \`-through \[conv_*_q_reg/Q\]\` 再 \`-to \[acq_ids_q_reg/D\]\`，把跨多级寄存器的阶段切出来"
zh ""
zh "下面做两组对照（引脚集合完全相同，共 [sizeof_collection $thru_conv] 个 conv 系寄存器 Q）："
zh ""
zh "| 查询 | 起点 | 终点 | slack |"
zh "|:---|:---|:---|---:|"
set t_thru [get_timing_paths -through $thru_conv -to $to_acqids -max_paths 1 -nworst 1 -delay_type max]
set t_from [get_timing_paths -from $from_conv -to $to_acqids -max_paths 1 -nworst 1 -delay_type max]
foreach {nm ps} [list "-through conv_q/Q -to acq_ids/D" $t_thru "-from conv_q/Q -to acq_ids/D" $t_from] {
  if {[sizeof_collection $ps]} {
    set p [index_collection $ps 0]
    zh "| `$nm` | `[get_object_name [get_attribute $p startpoint]]` | `[get_object_name [get_attribute $p endpoint]]` | [tf [get_attribute $p slack]] |"
  } else {
    zh "| `$nm` | N/A | N/A | N/A |"
  }
}
zh ""
zh "两组结果**起点完全相同**：`-through <寄存器>/Q` 与 `-from <同一寄存器>/Q` "
zh "得到同一条路径，起点都是那个寄存器的时钟脚 CP。这直接证明："
zh ""
zh "- 路径从该寄存器**出发**，不会\"穿过\"它；"
zh "- `-through` 只是要求路径经过该引脚，而该引脚正是起点侧的 Q，"
zh "  所以它退化成了 `-from`，语义上没有多跨一级寄存器。"
zh ""
zh "要把\"多级功能阶段\"的总延迟算清楚，必须做**事务级/多拍**分析"
zh "（数清中间跨了几拍、每拍各自的组合段多长）。逐条路径的 slack 不能相加，"
zh "更不能当作阶段预算。"
zh ""
dump_path THROUGH_PROOF \
  "同一组引脚，完整体留档（-through conv 系 Q → acq_ids_q 的 D）" \
  [list -delay_type max -through $thru_conv -to $to_acqids]
ascii "THROUGH proof recorded"

# ---------------------------------------------------------------- 4. 三类预算
section "4. 三类预算逐段实测" "BUDGETS"

zh "下面每一段都是**一条独立的单段组合路径**。段与段之间隔着寄存器，"
zh "因此**不能相加**，也不能当成某个功能阶段的总延迟。"
zh ""
zh "每条查询给两类结果："
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
  [list -delay_type max -from [get_pins -hier -filter {full_name =~ *quiet_cnt_q_reg*/Q}] -to [get_pins -hier -filter {full_name =~ *conv_valid_q_reg*/D}]]

dump_path B1B_ACQ_IDS_PORTSTART \
  "→ ACQ 描述符片号寄存器 D（含端口起点的最差）" \
  [list -delay_type max -to $to_acqids]

dump_path B1C_ACQ_IDS_REG2REG \
  "→ ACQ 描述符片号寄存器 D（**reg→reg**，剔除复位路径）" \
  [list -delay_type max -from $REG_Q -to $to_acqids]

dump_path B1D_ACQ_SEL_OUT \
  "ACQ 片组选通输出端口 acq_sel_o（单段）" \
  [list -delay_type max -to [get_ports acq_sel_o]]

dump_path B1E_IDLE_SEL_OUT \
  "idle 掩码输出端口 idle_sel_o（释放后片组回池的旁证）" \
  [list -delay_type max -to [get_ports idle_sel_o]]

# ---- 4.2 ② 已决事件 → RDAC 开关（post）----
zh ""
zh "### 4.2 ② 已决事件 → 物理 RDAC post 开关"
zh ""
zh "post 通路在寄存器处断开，天然是两段："
zh ""
zh "1. 事件译码 / DEM 展开 → \`post_state_q\` 的 D（寄存器前）；"
zh "2. \`post_state_q\` 的 Q → \`rdac_post_state_o\`（寄存器后，纯输出适配）。"
zh ""
dump_path B2A_POST_D_PORTSTART \
  "→ post_state_q 的 D（含端口起点的最差）" \
  [list -delay_type max -to [get_pins -hier -filter {full_name =~ *post_state_q_reg*/D}]]

dump_path B2B_POST_D_REG2REG \
  "→ post_state_q 的 D（**reg→reg**，事件译码的真实组合段）" \
  [list -delay_type max -from $REG_Q -to [get_pins -hier -filter {full_name =~ *post_state_q_reg*/D}]]

dump_path B2C_FROM_EXP_EVT_REG2REG \
  "exp_evt_q 的 Q → 下游寄存器 D（事件层内部单段，reg→reg）" \
  [list -delay_type max -from [get_pins -hier -filter {full_name =~ *exp_evt_q_reg*/Q}] -to $REG_D]

dump_path B2D_POST_ADAPTER \
  "post_state_q 的 Q → rdac_post_state_o 端口（寄存器后适配段）" \
  [list -delay_type max -to [get_ports rdac_post_state_o]]

dump_path B2E_POST_WR_OUT \
  "post_wr_q 的 Q → post_wr_o 端口（写门适配段）" \
  [list -delay_type max -to [get_ports post_wr_o]]

zh ""
zh "#### 采样预置（pre）通路 —— **不要**和上面的 post 预算混为一谈"
zh ""
zh "\`rdac_pre_state_o\` 由 \`dither_pre\` 在 **prepare** 时给出"
zh "（RTL：\`acq_conv_ctx\` 的 \`pre_state_c\` 直接驱动，"
zh "见 \`rtl/v21/acq_conv_ctx.sv\`「物理路由」一节），"
zh "与\"已决事件 → post 开关\"是两条不同的通路。"
zh ""
dump_path B2F_PRE_PORTSTART \
  "→ rdac_pre_state_o 端口（含端口起点的最差）" \
  [list -delay_type max -to [get_ports rdac_pre_state_o]]

dump_path B2G_PRE_REG2REG \
  "→ rdac_pre_state_o 端口（起点限定寄存器，看真正的 pre 组合深度）" \
  [list -delay_type max -from $REG_Q -to [get_ports rdac_pre_state_o]]

dump_path B2H_PRE_WR_OUT \
  "→ pre_wr_o 端口（pre 写门）" \
  [list -delay_type max -to [get_ports pre_wr_o]]

# ---- 4.3 ③ arm / hold / quiet ----
zh ""
zh "### 4.3 ③ arm / hold / quiet（末次生效 → ADC2 捕获）"
zh ""
dump_path B3A_ARM_OUT \
  "→ adc2_capture_arm_o 端口（沿前授权，含端口起点）" \
  [list -delay_type max -to [get_ports adc2_capture_arm_o]]

dump_path B3B_ARM_OUT_REG2REG \
  "→ adc2_capture_arm_o 端口（起点限定寄存器）" \
  [list -delay_type max -from $REG_Q -to [get_ports adc2_capture_arm_o]]

dump_path B3C_HOLD_DONE \
  "→ hold_done_q 的 D（hold 记录单段）" \
  [list -delay_type max -to [get_pins -hier -filter {full_name =~ *hold_done_q_reg*/D}]]

dump_path B3D_QUIET_CNT \
  "→ quiet_cnt_q 的 D（安静窗计数单段，含端口起点）" \
  [list -delay_type max -to [get_pins -hier -filter {full_name =~ *quiet_cnt_q_reg*/D}]]

dump_path B3E_QUIET_CNT_REG2REG \
  "→ quiet_cnt_q 的 D（reg→reg）" \
  [list -delay_type max -from $REG_Q -to [get_pins -hier -filter {full_name =~ *quiet_cnt_q_reg*/D}]]

dump_path B3F_FINAL_POST_REG2REG \
  "final_post_q 的 Q → 下游寄存器 D（reg→reg）" \
  [list -delay_type max -from [get_pins -hier -filter {full_name =~ *final_post_q_reg*/Q}] -to $REG_D]

# ---------------------------------------------------------------- 5. 约束汇总
section "5. 约束与违例汇总" "CONSTRAINTS"

redirect $OUTDIR/constraint_max.rpt {report_constraint -all_violators -max_delay}
redirect $OUTDIR/constraint_min.rpt {report_constraint -all_violators -min_delay}
set fh1 [open $OUTDIR/constraint_max.rpt r]; set maxbody [read $fh1]; close $fh1
set fh2 [open $OUTDIR/constraint_min.rpt r]; set minbody [read $fh2]; close $fh2
set nmax [regexp -all {VIOLATED} $maxbody]
set nmin [regexp -all {VIOLATED} $minbody]

dump_path HOLD_WORST \
  "全局最差 hold（delay_type min）" \
  [list -delay_type min]

zh ""
zh "| 检查 | 违例条数 | 说明 |"
zh "|:---|---:|:---|"
zh "| setup（max_delay） | **$nmax** | [expr {$nmax == 0 ? "无 setup 违例" : "存在 setup 违例"}] |"
zh "| hold（min_delay） | **$nmin** | 综合网表 + 理想时钟树下的 hold 结果，见下 |"

zh ""
zh "**hold 必须单独说清楚**：本网表是综合产物、时钟为理想时钟（零 skew），"
zh "hold 违例在 CTS 之前是常见现象，不能据此判定设计失败；但也不能像旧报告"
zh "那样只写一句\"本报告只做 setup\"就算交代过去 —— 违例条数与最差值要如实列出。"
zh "典型量级：最差约 −0.07 ns，主要是同沿直通/短路径（如 recon 的 valid/tag 链）。"
zh ""
zh "<details><summary>report_constraint -max_delay 原文</summary>"
zh ""
zh "```"
zh $maxbody
zh "```"
zh "</details>"
zh ""
zh "<details><summary>report_constraint -min_delay 原文（前 60 行）</summary>"
zh ""
zh "```"
zh [join [lrange [split $minbody "\n"] 0 60] "\n"]
zh "```"
zh "</details>"
ascii "VIOL setup=$nmax hold=$nmin"

# ---------------------------------------------------------------- 收尾
zh ""
zh "---"
zh ""
zh "完整体全文目录：\`$OUTDIR/paths/\`（每条查询一个 \`.rpt\`，"
zh "由 \`report_timing -path_type full_clock_expanded\` 生成）。"
close $RPT
ascii "===BUDGET_END==="
exit
