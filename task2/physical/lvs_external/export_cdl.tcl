# Generate one reference from the frozen routed database for both rule decks.
read_liberty $::env(LVS_LIBERTY)
read_db /work/input/sar20_digital_top.odb
set top [[ord::get_db_block] getName]
if {$top ne "sar20_digital_top"} {
  error "Unexpected OpenDB block: $top"
}
read_sdc /work/input/sar20_digital_top.sdc
write_cdl -masters /work/out/compatible.cdl /work/out/sar20_digital_top.cdl
puts "LVS_ODB_TOP $top"
