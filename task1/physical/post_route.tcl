# Reopen the same final database and extracted SPEF used for stream-out.
set base $::env(TASK1_FINAL_DIR)
read_liberty $::env(TASK1_PROXY_LIBERTY)
read_db $base/6_final.odb
read_sdc $base/6_final.sdc
set_propagated_clock [all_clocks]
read_spef $base/6_final.spef

puts "\n=== Constraint coverage ==="
check_setup -verbose
puts "\n=== Setup, extracted RC ==="
report_worst_slack -max -digits 4
report_tns -max -digits 4
report_checks -path_delay max -group_path_count 10 -format full_clock_expanded
puts "\n=== Hold, extracted RC ==="
report_worst_slack -min -digits 4
report_tns -min -digits 4
report_checks -path_delay min -group_path_count 10 -format full_clock_expanded
puts "\n=== Final proxy power-grid connectivity ==="
check_power_grid -net VDD
check_power_grid -net VSS
puts "TASK1_POST_ROUTE_STA_COMPLETE"
