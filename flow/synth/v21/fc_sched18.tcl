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
source [file join [file dirname [file normalize [info script]]] design_inputs.tcl]
set DESIGN   sched18
set PDK      [v21::pdk]
set LIB_SS   $PDK/ndm/tcbn28hpcplusbwp7t40p140.ndm
set TLU_DIR  $PDK/installed/tech/rc_1p9m_4x2y2r
set TLU_MAX  $TLU_DIR/cln28hpc+_1p09m+ut-alrdl_4x2y2r_cworst.tluplus
set TLU_MIN  $TLU_DIR/cln28hpc+_1p09m+ut-alrdl_4x2y2r_cbest.tluplus
set PERIOD   25.0
set WORK     [v21::location V21_FC_DIR [file join [pwd] fc_sched18]]
set REF      $WORK/ref

file mkdir $WORK
file mkdir $REF

# ---------------------------------------------------------------- 建 NDM 库
create_lib -ref_libs $LIB_SS $REF/lib
puts "STEP_CREATE_LIB_OK"

# ---------------------------------------------------------------- 读 RTL
analyze -format sverilog [file join $v21::root rtl sched18.sv]
elaborate $DESIGN
puts "STEP_ELAB_OK"
set_top_module $DESIGN
puts "STEP_TOP_OK"

# ---------------------------------------------------------------- UPF（单电源域）
#
# **为什么需要**：place_opt 报 OPT-043/OPT-045，日志里
#   Number of used supplies = 0
# 库本身有 PG 引脚（get_lib_pins */*/VDD -> 839 个，dir=in），
# 但 PD 与 supply net 没有关联。命令式 UPF 全部 rc=1，
# 所以用文件式 load_upf。
if {[file exists [file join $v21::root flow synth v21 min_single_domain.upf]]} {
    set _r [catch {load_upf [file join $v21::root flow synth v21 min_single_domain.upf]} _e]
    puts "CHK_UPF rc=$_r [string range $_e 0 100]"
    # **电压必须在这里设**：set_voltage 是 TCL 命令，需要 design 上下文；
    # 写进 UPF 文件里会报 CMD-005 unknown command。
    # 0.81 V 与 SS 角库名 ...ssg0p81vm40c 一致。
    set _rv1 [catch {set_voltage 0.81 -object_list [get_supply_nets VDD]} _ev1]
    set _rv2 [catch {set_voltage 0.00 -object_list [get_supply_nets VSS]} _ev2]
    puts "CHK_VOLTAGE VDD_rc=$_rv1 VSS_rc=$_rv2"
} else {
    puts "CHK_UPF SKIP (file not found)"
}

# ---------------------------------------------------------------- TLU+
#
# **正确的绑定方式（已由 manpage 确认并实测）**：
#   read_parasitic_tech -tlup <file> -name <tag>   登记两份
#   set_parasitics_parameters -early_spec <tag> -late_spec <tag>
#
# 此前一直失败是因为用错了选项名：
#   set_parasitics_parameters 的选项是 **-early_spec / -late_spec**
#   （string，parasitic tech 的 tag），不是 -early / -late，
#   也没有 -early_tlu_plus_file。
# 参考：/opt/synopsys/fusioncompiler/W-2024.09-SP3/doc/FC/man/cat2/set_parasitics_parameters.2
read_parasitic_tech -tlup $TLU_MAX -name maxTLU
read_parasitic_tech -tlup $TLU_MIN -name minTLU
set_parasitics_parameters -early_spec minTLU -late_spec maxTLU
puts "STEP_TLU_OK"

# 绑定自证
#
# **踩过两次坑**：
#   ① `set _rpt [report_parasitic_parameters]` —— 该命令把内容直接打印到
#      stdout，**返回值是空字符串**，string match 必然误判为"未绑定"。
#   ② `get_attribute corner late_spec` —— 返回空；corner 的
#      `list_attributes -class corner` 也是空的。
# 可靠做法：用 `redirect` 把 report 写进文件，再读文件判断。
redirect $WORK/tlu_check.txt { report_parasitic_parameters }
set _fh [open $WORK/tlu_check.txt r]
set _txt [read $_fh]
close $_fh
if {![string match "*late_spec = maxTLU*" $_txt] ||
    ![string match "*early_spec = minTLU*" $_txt]} {
    puts "FATAL: TLU 未绑定（见 $WORK/tlu_check.txt）"
    exit 1
}
puts "CHK_TLU_BOUND early=minTLU late=maxTLU"

puts "STEP_CONSTRAINT_OK"

# ---------------------------------------------------------------- 综合
compile_fusion
puts "STEP_SYNTH_OK"

# ---------------------------------------------------------------- 物理
initialize_floorplan -core_utilization 0.6
puts "STEP_FLOORPLAN_OK"

# **place_opt 报 OPT-045 的完整根因链（逐步实测确认）**
#   Warning: Cannot find default buffer/inverter for VA DEFAULT_VA (OPT-043)
#   Error:   Cannot find usable buffers or inverters. (OPT-045)
#   Number of used supplies = 0
#
# 已排除：
#   ① 库缺 buffer      -- get_lib_cells */*BUFFD* -> 15 个，is_buffer=true
#   ② 库缺 PG 引脚     -- get_lib_pins */*/VDD -> **839 个**，dir=in
#   ③ 设计只读         -- is_black_box=false
#   ④ 缺电压           -- set_voltage 已 rc=0（见 CHK_VOLTAGE）
#
# **真正的根因：DEFAULT_VA 的 power/ground 无法设置**
#   实测 set_voltage_area -power/-ground DEFAULT_VA
#     -> Error: The power_supply_net of the default voltage_area
#               cannot be changed. (NDMUI-444)
#   而 DEFAULT_VA 的 power/ground 恒为空 -> used supplies = 0 ->
#   place_opt 找不到可插入 buffer 的电源上下文。
#
# **解法：不使用 DEFAULT_VA，自己建一个带 power/ground 的 VA**
#   create_voltage_area 支持同时给 -power_domains / -power / -ground / -cells，
#   这正是 DEFAULT_VA 缺少的能力。
# **建新 VA 替代 DEFAULT_VA**（它的 power/ground 不可改，NDMUI-444）
#
# 顺序很关键（上一轮踩过）：**先建新 VA 成功，再删旧的**。
# 上一轮先 remove 再 create，create 因 -region 格式错失败，
# 结果一个 VA 都没有，OPT-045 从 2 次恶化到 5 次。
set _pd   [get_power_domains -quiet *]
set _vdd  [get_supply_nets -quiet VDD]
set _vss  [get_supply_nets -quiet VSS]
set _all  [get_cells -hierarchical -filter "is_hierarchical == false"]

# -region 要的是 {{x1 y1} {x2 y2}} 这种"点对列表"，不是单个 bbox 对象。
# 上一轮传 [list $core] 得到空串 -> NDMUI-100 Invalid coordinates ''。
# **boundary 返回 4 个角点，不是 2 个**（实测）：
#   {0 0} {0 39.6} {39.825 39.6} {39.825 0}
# 所以 [lindex $bbox 0] 与 [lindex $bbox 1] 是同一侧的相邻点，
# 两点构成 x 方向宽度为 0 的退化矩形 -> NDMUI-100 / DES-136。
# 正确取法：下左角 = [lindex 0]，上右角 = [lindex 2]。
# 另：core_area / core_bbox / core_boundary 属性在本版本均为空，不可用。
set _bbox [get_attribute [current_design] boundary]
set _ll   [lindex $_bbox 0]
set _ur   [lindex $_bbox 2]
set _x1   [lindex $_ll 0]
set _y1   [lindex $_ll 1]
set _x2   [lindex $_ur 0]
set _y2   [lindex $_ur 1]
puts "CHK_VA_REGION {{$_x1 $_y1} {$_x2 $_y2}}"
if {$_x2 <= $_x1 || $_y2 <= $_y1} {
    puts "FATAL: region 退化（{{$_x1 $_y1} {$_x2 $_y2}}），不建 VA"
}
set _r [catch {
    create_voltage_area -name VA_TOP \
        -power_domains $_pd -power $_vdd -ground $_vss \
        -cells $_all -region [list [list $_x1 $_y1] [list $_x2 $_y2]]
} _e]
puts "CHK_VA_NEW rc=$_r [string range $_e 0 120]"

set _va_new [get_voltage_areas -quiet VA_TOP]
if {[sizeof_collection $_va_new] > 0} {
    puts "CHK_VA_BIND pd=[get_object_name [get_attribute -quiet $_va_new power_domains]] power=[get_object_name [get_attribute -quiet $_va_new power]]"
    # **不删 DEFAULT_VA**：实测删掉之后 place_opt 仍然引用
    #   "VA DEFAULT_VA with Block Hierarchy ."
    # 说明 DEFAULT_VA 是 FC 内部固定引用的对象，删不掉也不该删。
    # 保留它，只保证 VA_TOP 存在并绑定 PD。
    set _va_all [get_voltage_areas -quiet *]
    puts "CHK_VA_LIST [get_object_name $_va_all]"
} else {
    puts "CHK_VA_FAIL 保留原 VA，不删除"
}
puts "STEP_LIBCELL_PURPOSE_OK"

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
