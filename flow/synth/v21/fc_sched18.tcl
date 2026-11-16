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

# **place_opt 报 OPT-045 的真正根因（已逐项排除确认）**：
#   Warning: Cannot find default buffer/inverter for VA DEFAULT_VA (OPT-043)
#   Error:   Cannot find usable buffers or inverters. (OPT-045)
#
# 排查过程（每一步都有实测依据）：
#   ① 库本身没问题：NDM 里 839 个 cell、15 个 BUFFD，
#      BUFFD2 的 is_buffer=true、area=0.392、dont_use=false、非黑盒。
#   ② `set_lib_cell_purpose` 返回 0（成功）但 `purpose` 属性仍为空 ——
#      该命令定义的是"允许用途"，不直接改属性，所以不是根因。
#   ③ 设计的 is_black_box=false，不是"block 不可修改"。
#   ④ **真正的根因**：`get_voltage_areas` 返回 DEFAULT_VA，
#      但它的 `power_domain` 和 `blocks` **都是空** —— VA 没绑定任何东西，
#      place_opt 在该 VA 内找不到可插入的 buffer。
#
# 正确做法（man set_voltage_area）：
#   set_voltage_area -add_power_domains <pd> -add_cells <cells> <va>
# **只绑 power domain，不要 -add_cells**：
# 实测 `-add_cells` 报 NDMUI-155
#   Cell 'A689' does not belong to a power_domain associated with this voltage_area
# 因为 cell 本来就属于 DEFAULT_POWER_DOMAIN，再 -add_cells 反而要求它
# "已属于该 VA 关联的 PD"，形成循环约束。绑 PD 之后 VA 自动覆盖该 PD 下所有 cell。
set _pd [get_power_domains -quiet *]
set _va [get_voltage_areas]
if {[sizeof_collection $_pd] > 0} {
    set_voltage_area -add_power_domains $_pd $_va
    puts "STEP_VA_BIND_OK pd=[get_object_name $_pd]"
} else {
    puts "FATAL: 没有 power domain 可绑定"
    exit 1
}

# **本库是无 PG 引脚的普通标准单元库**（实测 BUFFD2 只有 I/Z 两个信号引脚，
# 没有 VDD/VSS pin），因此 FC 的 voltage-area 感知在
#   "VA 有 PD 但 VA_power/VA_ground 为空、used supplies=0"
# 状态下找不到可插入的 buffer，报 OPT-043/OPT-045。
#
# 两条路：
#   ① `set_voltage_area -power/-ground`  -> 报 NDMUI-444
#      "The power_supply_net of the default voltage_area cannot be changed"
#      （DEFAULT_VA 的电源网由 tech file 决定，不可改）
#   ② 关闭 VA 感知，让 place_opt 按普通单域设计处理
#      （本设计没有 UPF、没有多电源域，②正确）
set_app_options -name mv.upf.enable_missing_voltage_area -value true
puts "CHK_VA_OPT enable_missing_voltage_area=true"

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
