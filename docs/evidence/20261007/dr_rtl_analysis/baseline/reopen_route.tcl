# Re-read a frozen routed DCP only. No implementation or timing constraint edit.
if {$argc != 2} { puts stderr "Usage: DCP NEW_REPORT_DIR"; exit 2 }
lassign $argv dcp out
if {[file exists $out]} { puts stderr "Refusing existing output"; exit 2 }
file mkdir $out
set rc [catch {
    open_checkpoint $dcp
    if {[version -short] ne "2018.3"} { error "Unexpected Vivado version" }
    set_param general.maxThreads 2
    report_timing_summary -report_unconstrained -file [file join $out timing_summary.rpt]
    report_timing -delay_type max -max_paths 10 -file [file join $out timing_max_paths.rpt]
    report_timing -delay_type min -max_paths 10 -file [file join $out timing_min_paths.rpt]
    set launch [all_registers -clock_pins]
    set capture [all_registers -data_pins]
    if {![llength $launch] || ![llength $capture]} {error "Empty register timing endpoint sets"}
    report_timing -delay_type max -from $launch -to $capture -max_paths 10 -file [file join $out timing_core_max.rpt]
    report_timing -delay_type min -from $launch -to $capture -max_paths 10 -file [file join $out timing_core_min.rpt]
    check_timing -verbose -file [file join $out check_timing.rpt]
    report_clocks -file [file join $out clocks.rpt]
    report_exceptions -summary -file [file join $out exceptions.rpt]
    report_utilization -file [file join $out utilization.rpt]
    write_xdc [file join $out loaded_constraints.xdc]
    # Keep actual-version help and optional diagnostics; failure is explicit.
    set fh [open [file join $out high_fanout_status.txt] w]
    puts $fh [help report_high_fanout_nets]
    if {[catch {report_high_fanout_nets -max_nets 25 -file [file join $out high_fanout.rpt]} msg]} {
        puts $fh "HIGH_FANOUT_REPORT_STATUS=ERROR"
        puts $fh $msg
    } else {puts $fh "HIGH_FANOUT_REPORT_STATUS=COMPLETE"}
    close $fh
    set fh [open [file join $out scope.txt] w]
    puts $fh "STATUS=FROZEN_DCP_REPORT_REEXPORT_COMPLETE"
    puts $fh "VIVADO_VERSION=[version -short]"
    puts $fh "PART=[get_property PART [current_design]]"
    puts $fh "INPUT_DCP=$dcp"
    puts $fh "REGISTER_CLOCK_PINS=[llength $launch]"
    puts $fh "REGISTER_DATA_PINS=[llength $capture]"
    puts $fh "NO_SYNTHESIS_OR_PLACE_ROUTE=1"
    puts $fh "NO_CONSTRAINT_EDIT=1"
    puts $fh "SCOPE=HISTORICAL_P5_ROUTED_FPGA_REEXPORT_NOT_CURRENT_P7_OR_ASIC"
    close $fh
} msg opts]
if {$rc} {
    set fh [open [file join $out failure.txt] w]
    puts $fh $msg
    puts $fh [dict get $opts -errorinfo]
    close $fh
    puts stderr $msg
    exit 1
}
exit 0
