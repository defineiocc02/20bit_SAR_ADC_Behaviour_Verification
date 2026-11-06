# Inspect the actual OpenDB PG wiring before a physical checkpoint is promoted.

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

if {[info exists ::env(TASK2_PG_DATABASE)]} {
  read_liberty $::env(TASK1_PROXY_LIBERTY)
  read_db $::env(TASK2_PG_DATABASE)
}
set pg_block [ord::get_db_block]
set pg_findings [pg_inventory $pg_block]
if {[dict size $pg_findings]} { error "PG inventory findings $pg_findings" }
foreach pg_net {VDD VSS} {
  if {$::env(TASK2_PG_STAGE) eq "floorplan"} {
    check_power_grid -net $pg_net -floorplanning
  } else {
    check_power_grid -net $pg_net
  }
}
puts "TASK2_PG_INVENTORY_COMPLETE $::env(TASK2_PG_STAGE)"
