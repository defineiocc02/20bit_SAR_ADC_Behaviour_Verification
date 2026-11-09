# =============================================================================
# 文件名     : export_cdl.tcl
# 模块名     : lvs_cdl_external
# 版本       : v1.0
# 功能       : LVS 参考 CDL 导出（外部规则）
#
# 设计说明   :
#   * top 名不符立即报错，防止把别的顶层 CDL 当成 sar20_digital_top 参与比对。
#   * 本文件只描述约束/验证意图，不是验收结论；实际状态以运行日志与归档收据为准。
# -----------------------------------------------------------------------------
# 变更记录
# -----------------------------------------------------------------------------
# 初始版本，暂无变更记录。
# =============================================================================

read_liberty $::env(LVS_LIBERTY)
read_db /work/input/sar20_digital_top.odb
set top [[ord::get_db_block] getName]
if {$top ne "sar20_digital_top"} {
  error "Unexpected OpenDB block: $top"
}
read_sdc /work/input/sar20_digital_top.sdc
write_cdl -masters /work/out/compatible.cdl /work/out/sar20_digital_top.cdl
puts "LVS_ODB_TOP $top"
