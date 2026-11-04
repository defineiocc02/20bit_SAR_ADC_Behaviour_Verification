# Non-project, out-of-context FPGA screening. Not an ASIC PPA/signoff flow.
# vivado -mode batch -source synth/run_vivado_ooc.tcl -tclargs PART OUT PERIOD STAGES
if {$argc != 4} {
    puts stderr "Usage: PART OUT PERIOD_NS P_RECON_STAGES"
    exit 2
}
lassign $argv part out period stages
if {![string is double -strict $period] || $period <= 0 ||
    ![string is integer -strict $stages] || $stages < 5 || $stages > 63} {
    puts stderr "Invalid clock period or divider parameter"
    exit 2
}
set root [file normalize [file join [file dirname [info script]] ..]]
set out [file normalize $out]
if {[file exists $out]} {
    puts stderr "Refusing to reuse output directory: $out"
    exit 2
}
file mkdir $out
set rc [catch {
    if {[llength [get_parts -quiet $part]] != 1} {error "FPGA part unavailable: $part"}
    create_project -in_memory -part $part
    set_param general.maxThreads 2
    set_property include_dirs [list [file join $root rtl params]] [current_fileset]
    set fh [open [file join $root rtl rtl_sources.f] r]
    set sources {}
    foreach line [split [read $fh] \n] {
        set line [string trim $line]
        if {$line ne ""} {lappend sources [file join $root $line]}
    }
    close $fh
    read_verilog -sv $sources
    # Explicit constraints are read before synthesis so optimization sees them.
    # Zero I/O delays are a screening assumption, not a board timing budget.
    set fh [open [file join $out screening.xdc] w]
    puts $fh [format {create_clock -name core_clk -period %.9g [get_ports clk]} $period]
    puts $fh {set_clock_uncertainty 0.05 [get_clocks core_clk]}
    puts $fh {set_input_delay 0.0 -clock core_clk [get_ports -filter {DIRECTION == IN && NAME != clk}]}
    puts $fh {set_output_delay 0.0 -clock core_clk [all_outputs]}
    close $fh
    read_xdc [file join $out screening.xdc]
    synth_design -top sar20_digital_core -part $part -mode out_of_context \
        -flatten_hierarchy rebuilt -generic P_RECON_STAGES=$stages
    if {[llength [get_cells -hierarchical -filter {IS_BLACKBOX == 1}]] != 0} {
        error "Unresolved black boxes in synthesized design"
    }
    report_utilization -file [file join $out utilization.rpt]
    report_utilization -hierarchical -file [file join $out utilization_hier.rpt]
    report_timing_summary -report_unconstrained -file [file join $out timing_summary.rpt]
    report_timing -max_paths 20 -file [file join $out timing_paths.rpt]
    check_timing -verbose -file [file join $out check_timing.rpt]
    report_drc -file [file join $out drc.rpt]
    # No measured activity is supplied: this estimate is explicitly vectorless.
    report_power -file [file join $out power_vectorless.rpt]
    write_checkpoint [file join $out post_synth.dcp]
    set paths [get_timing_paths -delay_type max -max_paths 1]
    if {[llength $paths] == 0} {error "No constrained timing path"}
    set wns [get_property SLACK [lindex $paths 0]]
    set fh [open [file join $out status.txt] w]
    puts $fh "STATUS=SYNTH_COMPLETE"
    puts $fh "TIMING_MET=[expr {$wns >= 0}]"
    puts $fh "WNS_NS=$wns"
    puts $fh "PART=$part"
    puts $fh "PERIOD_NS=$period"
    puts $fh "P_RECON_STAGES=$stages"
    puts $fh "VIVADO_VERSION=[version -short]"
    puts $fh "SCOPE=FPGA_POST_SYNTH_OOC_VECTORLESS_NOT_ASIC"
    close $fh
} err opts]
if {$rc} {
    set fh [open [file join $out failure.txt] w]
    puts $fh $err
    puts $fh [dict get $opts -errorinfo]
    close $fh
    puts stderr $err
    exit 1
}
if {$wns < 0} {exit 3}
exit 0
