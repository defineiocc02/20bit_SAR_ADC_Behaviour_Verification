open_checkpoint out/post_route.dcp
set launch [all_registers -clock_pins]
set capture [all_registers -data_pins]
if {[llength $launch] == 0 || [llength $capture] == 0} {error "Missing sequential timing pins"}
set fh [open out/regreg_summary.txt w]
puts $fh "CLOCK_PINS=[llength $launch]"
puts $fh "DATA_PINS=[llength $capture]"
foreach {label mode} {SETUP max HOLD min} {
    set paths [get_timing_paths -from $launch -to $capture -delay_type $mode -max_paths 1]
    if {[llength $paths] != 1} {error "Missing $label register-to-register path"}
    set worst [lindex $paths 0]
    puts $fh "REGREG_${label}_WORST_NS=[get_property SLACK $worst]"
    puts $fh "REGREG_${label}_STARTPOINT=[get_property NAME [get_property STARTPOINT_PIN $worst]]"
    puts $fh "REGREG_${label}_ENDPOINT=[get_property NAME [get_property ENDPOINT_PIN $worst]]"
    report_timing -from $launch -to $capture -delay_type $mode -max_paths 20 -path_type full_clock_expanded -file out/regreg_${mode}_paths.rpt
}
close $fh
