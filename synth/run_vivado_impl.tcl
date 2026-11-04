# FPGA OOC implementation screening of a caller-validated synthesized DCP.
# Usage: vivado -mode batch -source synth/run_vivado_impl.tcl \
#        -tclargs INPUT_DCP NEW_OUT_DIR PERIOD_NS BUFGCTRL_XxYy
# The caller must freeze/hash and validate the source/DCP before this step.
# This script never resynthesizes RTL or overwrites the input checkpoint.
# UG905 v2018.3 pp.8,16,21: HD.CLK_SRC improves clock delay/skew estimates;
# an external OOC clock is NOT physically routed by this flow. Zero I/O delays
# are screening assumptions, not a board budget. No ASIC/board signoff claim.
# https://docs.amd.com/api/khub/documents/dUZkF9u9qQ4x4MtRgpH9aQ/content

proc finite_number {value} {
    if {![regexp {^[+-]?([0-9]+(\.[0-9]*)?|\.[0-9]+)([eE][+-]?[0-9]+)?$} $value]} {
        return 0
    }
    if {[catch {expr {abs(double($value)) < Inf}} finite]} {return 0}
    return $finite
}

if {$argc != 4} {
    puts stderr "Usage: INPUT_DCP NEW_OUT_DIR PERIOD_NS BUFGCTRL_XxYy"
    exit 1
}
lassign $argv input_dcp out period clock_site
set input_dcp [file normalize $input_dcp]
set out [file normalize $out]
if {![finite_number $period] || $period <= 0} {
    puts stderr "Clock period must be finite and positive"
    exit 1
}
if {![file isfile $input_dcp] || ![file readable $input_dcp] ||
    [file size $input_dcp] == 0 || [string tolower [file extension $input_dcp]] ne ".dcp"} {
    puts stderr "A nonempty, readable, caller-validated .dcp is required: $input_dcp"
    exit 1
}
if {![regexp {^BUFGCTRL_X[0-9]+Y[0-9]+$} $clock_site]} {
    puts stderr "CLOCK_SITE must name one actual BUFGCTRL site"
    exit 1
}
if {[file exists $out]} {
    puts stderr "Refusing to reuse output directory: $out"
    exit 1
}
if {[catch {file mkdir $out} error_text]} {
    puts stderr $error_text
    exit 1
}
set scope "FPGA_POST_ROUTE_OOC_ESTIMATED_EXTERNAL_CLOCK_ZERO_IO_VECTORLESS_NOT_ASIC_OR_BOARD"
set rc [catch {
    set_param general.maxThreads 2
    open_checkpoint $input_dcp
    if {[llength [get_cells -hierarchical -filter {IS_BLACKBOX == 1}]] != 0} {
        error "Unresolved black boxes in input checkpoint"
    }
    set clock_port [get_ports -quiet clk]
    if {[llength $clock_port] != 1} {error "Input checkpoint must have one clk port"}
    set site [get_sites -quiet $clock_site]
    if {[llength $site] != 1 || [get_property SITE_TYPE $site] ne "BUFGCTRL" ||
        [get_property NAME $site] ne $clock_site} {
        error "CLOCK_SITE is not a unique BUFGCTRL site on this device: $clock_site"
    }
    # create_clock without -add replaces the existing core_clk definition.
    # HD.CLK_SRC must be applied AFTER that clock exists on the port (UG905).
    set fh [open [file join $out screening.xdc] w]
    puts $fh [format {create_clock -name core_clk -period %.9g [get_ports clk]} $period]
    puts $fh {set_clock_uncertainty 0.05 [get_clocks core_clk]}
    puts $fh [format {set_property HD.CLK_SRC %s [get_ports clk]} $clock_site]
    puts $fh {set_input_delay 0.0 -clock core_clk [get_ports -filter {DIRECTION == IN && NAME != clk}]}
    puts $fh {set_output_delay 0.0 -clock core_clk [all_outputs]}
    close $fh
    read_xdc -mode out_of_context [file join $out screening.xdc]
    if {[llength [get_clocks -quiet *]] != 1 || [llength [get_clocks -quiet core_clk]] != 1} {
        error "Expected exactly one core_clk after screening constraints"
    }
    opt_design
    place_design
    phys_opt_design
    route_design

    report_utilization -file [file join $out utilization.rpt]
    report_utilization -hierarchical -file [file join $out utilization_hier.rpt]
    report_timing_summary -delay_type min_max -report_unconstrained \
        -file [file join $out timing_summary.rpt]
    report_timing -delay_type max -max_paths 20 -file [file join $out timing_setup_paths.rpt]
    report_timing -delay_type min -max_paths 20 -file [file join $out timing_hold_paths.rpt]
    report_route_status -file [file join $out route_status.rpt]
    check_timing -verbose -file [file join $out check_timing.rpt]
    report_drc -file [file join $out drc.rpt]
    report_power -file [file join $out power_vectorless.rpt]
    write_checkpoint [file join $out post_route.dcp]

    set fully_routed [report_route_status -boolean_check ROUTED_FULLY]
    set route_errors [report_route_status -boolean_check ERRORS_IN_ROUTES]
    set drc_errors [llength [get_drc_violations -quiet -filter {SEVERITY == Error}]]
    if {$fully_routed ne "1" || $route_errors ne "0" || $drc_errors != 0} {
        error "Invalid implementation: routed=$fully_routed route_errors=$route_errors drc_errors=$drc_errors"
    }
    foreach {delay_type name} {max wns min whs} {
        set paths [get_timing_paths -delay_type $delay_type -max_paths 1]
        if {[llength $paths] != 1} {error "Missing constrained $delay_type timing path"}
        set slack [get_property SLACK [lindex $paths 0]]
        if {![finite_number $slack]} {error "Nonfinite or missing $delay_type slack: $slack"}
        set $name $slack
    }
    set timing_met [expr {$wns >= 0 && $whs >= 0}]
    set fh [open [file join $out status.txt] w]
    puts $fh "STATUS=IMPL_COMPLETE"
    puts $fh "TIMING_MET=$timing_met"
    puts $fh "WNS_NS=$wns"
    puts $fh "WHS_NS=$whs"
    puts $fh "ROUTED_FULLY=$fully_routed"
    puts $fh "ROUTE_ERRORS=$route_errors"
    puts $fh "DRC_ERRORS=$drc_errors"
    puts $fh "INPUT_DCP=$input_dcp"
    puts $fh "INPUT_DCP_BYTES=[file size $input_dcp]"
    puts $fh "PART=[get_property PART [current_design]]"
    puts $fh "PERIOD_NS=$period"
    puts $fh "CLOCK_SITE=$clock_site"
    puts $fh "CLOCK_UNCERTAINTY_NS=0.05"
    puts $fh "EXTERNAL_CLOCK_ROUTED=0"
    puts $fh "VIVADO_VERSION=[version -short]"
    puts $fh "SCOPE=$scope"
    close $fh
} error_text error_options]
if {$rc} {
    set fh [open [file join $out failure.txt] w]
    puts $fh $error_text
    puts $fh [dict get $error_options -errorinfo]
    close $fh
    set fh [open [file join $out status.txt] w]
    puts $fh "STATUS=FAILED"
    puts $fh "SCOPE=$scope"
    close $fh
    puts stderr $error_text
    exit 1
}
if {!$timing_met} {exit 3}
exit 0
