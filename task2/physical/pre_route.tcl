# Capture early STA with the actual synthesized cells and original I/O budget.
read_liberty $::env(TASK1_PROXY_LIBERTY)
read_db $::env(TASK1_FINAL_DIR)/1_synth.odb
read_sdc $::env(TASK1_FINAL_DIR)/1_synth.sdc
check_setup -verbose
foreach sense {max min} {
  report_worst_slack -$sense -digits 6
  report_tns -$sense -digits 6
  report_checks -path_delay $sense -group_path_count 5 -format full_clock_expanded
}
write_verilog /work/out/delivery/mapped_netlist.v
puts "TASK2_SYNTH_STA_COMPLETE"
