# =============================================================================
# 文件名     : pg_audit.tcl
# 模块名     : task2_pg_audit
# 版本       : v1.0
# 功能       : 电源网 PG 连通性审计
#
# 设计说明   :
#   * 只修复 PG-PIN-MISSING / PG-INSTANCE-NO-PG 两类可安全重连的问题；其他类型一律报错停止，避免掩盖真实 PDN 缺陷。
#   * 本文件只描述约束/验证意图，不是验收结论；实际状态以运行日志与归档收据为准。
# -----------------------------------------------------------------------------
# 变更记录
# -----------------------------------------------------------------------------
# 初始版本，暂无变更记录。
# =============================================================================

# =========================================================================
# 1. 实例级 PG 引脚盘点
# =========================================================================
# -------------------------------------------------------------------------
# 遍历每个实例的每个 ITerm，只统计 POWER/GROUND 类型的 master term；
# 其余信号引脚不影响 PDN 判定，跳过以免把普通网络误报成电源网缺失。
# -------------------------------------------------------------------------
proc pg_inventory {block} {
  set findings [dict create]
  set pg_pin_count 0
  foreach inst [$block getInsts] {
    set required {}
    set connected 0
    foreach iterm [$inst getITerms] {
      set mterm [$iterm getMTerm]
      set kind [$mterm getSigType]
      if {$kind ne "POWER" && $kind ne "GROUND"} { continue }
      incr pg_pin_count
      lappend required [$mterm getName]
      set expected [expr {$kind eq "POWER" ? "VDD" : "VSS"}]
      set net [$iterm getNet]
      if {$net eq "NULL" || $net eq ""} {
        dict incr findings PG-PIN-MISSING
      } elseif {[$net getName] ne $expected} {
        dict incr findings PG-WRONG-NET
      } else {
        incr connected
      }
    }
    if {[llength $required] && $connected == 0} {
      dict incr findings PG-INSTANCE-NO-PG
      if {[string match "pg_audit_eco_*" [$inst getName]]} {
        dict incr findings PG-ECO-NO-PG
      }
    }
  }
  if {$pg_pin_count == 0} { dict incr findings PG-PINS-ABSENT }
  puts "PG_PIN_COUNT $pg_pin_count"

# =========================================================================
# 2. 全局 PG 网络与几何检查
# =========================================================================
# -------------------------------------------------------------------------
# 除引脚连通外还要验网本身存在、sigType 正确、有 BTerm、有实际金属几何；
# 多层金属却零 via 是典型的悬空网格，单看 net 存在会漏掉。
# -------------------------------------------------------------------------
  foreach {name kind} {VDD POWER VSS GROUND} {
    set net [$block findNet $name]
    if {$net eq "NULL" || $net eq "" || [$net getSigType] ne $kind} {
      dict incr findings PG-NET-MISSING
      continue
    }
    if {[llength [$net getBTerms]] == 0} { dict incr findings PG-PORT-MISSING }
    set area [dict create]
    set vias 0
    set segments 0
    foreach wire [$net getSWires] {
      foreach shape [$wire getWires] {
        if {[$shape isVia]} {
          incr vias
        } else {
          set layer [[$shape getTechLayer] getName]
          set rectangle [expr {([$shape xMax] - [$shape xMin]) * ([$shape yMax] - [$shape yMin])}]
          dict incr area $layer $rectangle
          incr segments
        }
      }
    }
    if {$segments == 0} { dict incr findings PG-GEOMETRY-MISSING }
    if {[dict size $area] > 1 && $vias == 0} { dict incr findings PG-VIA-ABSENT }
    puts "PG_GEOMETRY $name segments=$segments placed_vias=$vias layer_area_dbu2=$area"
  }
  return $findings
}

# =========================================================================
# 3. 数据库载入与可选修复
# =========================================================================
# -------------------------------------------------------------------------
# CI 里若已传入现成数据库就直接复用，避免重新读 Liberty/ODB 造成阶段不一致；
# 脚本不主动创建数据库，调用方负责保证路径与顶层名一致。
# -------------------------------------------------------------------------
if {[info exists ::env(TASK2_PG_DATABASE)]} {
  read_liberty $::env(TASK1_PROXY_LIBERTY)
  read_db $::env(TASK2_PG_DATABASE)
}
set pg_block [ord::get_db_block]
set pg_findings [pg_inventory $pg_block]
set pg_repaired 0
# -------------------------------------------------------------------------
# 只允许自动修复两类「新增标准单元未接 PG」的缺脚问题；
# 出现错网、缺网、缺几何等任何其它类型时保持 missing_only=0，不重连，
# 直接报错停止——自动 global_connect 会掩盖真实 PDN 缺陷。
# -------------------------------------------------------------------------
if {[dict size $pg_findings] && [info exists ::env(TASK2_PG_REPAIRED_DB)]} {
  set missing_only 1
  foreach finding [dict keys $pg_findings] {
    if {$finding ne "PG-PIN-MISSING" && $finding ne "PG-INSTANCE-NO-PG"} {
      set missing_only 0
    }
  }
  if {$missing_only} {
    puts "PG_RECONNECT_BEFORE $pg_findings"
    # Reapply the SKY130HD PDN's supply aliases to newly inserted standard cells.
    add_global_connection -net VDD -inst_pattern {.*} -pin_pattern {^(VDD|VDDPE|VDDCE|VPWR|VPB)$} -power
    add_global_connection -net VSS -inst_pattern {.*} -pin_pattern {^(VSS|VSSE|VGND|VNB)$} -ground
    global_connect
    set pg_findings [pg_inventory $pg_block]
    set pg_repaired 1
  }
}
# -------------------------------------------------------------------------
# 任何残留 finding 都以非零状态退出，物理检查点不得被升级为 PASS。
# -------------------------------------------------------------------------
if {[dict size $pg_findings]} { error "PG inventory findings $pg_findings" }
# =========================================================================
# 4. 最终电源网检查与落盘
# =========================================================================
# -------------------------------------------------------------------------
# floorplan 阶段与布线后阶段调用不同的 check_power_grid 模式；
# 只有执行过修复才写出 REPAIRED_DB，避免把未改动数据库当成新证据。
# -------------------------------------------------------------------------
foreach pg_net {VDD VSS} {
  if {$::env(TASK2_PG_STAGE) eq "floorplan"} {
    check_power_grid -net $pg_net -floorplanning
  } else {
    check_power_grid -net $pg_net
  }
}
if {$pg_repaired} { write_db $::env(TASK2_PG_REPAIRED_DB) }
puts "TASK2_PG_INVENTORY_COMPLETE $::env(TASK2_PG_STAGE)"
