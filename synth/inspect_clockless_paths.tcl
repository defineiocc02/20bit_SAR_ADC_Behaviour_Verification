# Read-only, bounded inspection of an ALREADY OPEN, caller-validated DCP.
# source synth/inspect_clockless_paths.tcl
# clockless_audit::run NEW_OUT_DIR
# No clocks, exceptions, case analysis, netlist edits or implementation commands
# are applied. PARTIAL is deliberate: bounded timing samples are not all paths.
namespace eval clockless_audit {
    variable group_path_limit 256
    variable source_limit 256
    variable dumped_cells
}

proc clockless_audit::names {objects} {
    if {[llength $objects] == 0} {return {}}
    return [get_property NAME $objects]
}

proc clockless_audit::pin_named {name} {
    set escaped [string map [list "\\" "\\\\" "\"" "\\\""] $name]
    return [get_pins -hierarchical -filter [format {NAME == "%s"} $escaped]]
}

proc clockless_audit::property_or_na {key object} {
    if {[lsearch -exact [list_property $object] $key] < 0} {return "UNAVAILABLE"}
    return [get_property $key $object]
}

proc clockless_audit::dump_cell {fh cell} {
    variable dumped_cells
    set name [get_property NAME $cell]
    if {[dict exists $dumped_cells $name]} {return}
    dict set dumped_cells $name 1
    puts $fh "CELL $name"
    foreach key [lsort [list_property $cell]] {
        puts $fh "  $key=[get_property $key $cell]"
    }
    set clk [pin_named "$name/CLK"]
    if {[llength $clk] == 1} {
        puts $fh "  CLK_NETS=[names [get_nets -segments -of_objects $clk]]"
        puts $fh "  CLK_CLOCKS=[names [get_clocks -of_objects $clk]]"
    }
}

# This recognizes only literal GND/VCC drivers and the transparent DSP48E1
# A/ACIN -> ACOUT path (A_INPUT plus BOTH AREG and ACASCREG equal zero).
# No LUT evaluation, empty-fanin inference, or arbitrary macro equivalence.
proc clockless_audit::constant_trace {pin fh visited} {
    set name [get_property NAME $pin]
    if {[lsearch -exact $visited $name] >= 0 || [llength $visited] >= 64} {
        return [list UNRESOLVED cycle_or_depth_limit]
    }
    lappend visited $name
    set cell [get_cells -of_objects $pin]
    if {[llength $cell] != 1} {return [list UNRESOLVED no_unique_owning_cell]}
    set ref [get_property REF_NAME $cell]
    puts $fh "TRACE $name REF=$ref DIRECTION=[get_property DIRECTION $pin]"
    if {$ref eq "GND"} {return [list CONSTANT_ZERO literal_GND_driver]}
    if {$ref eq "VCC"} {return [list CONSTANT_ONE literal_VCC_driver]}
    if {[get_property DIRECTION $pin] eq "OUT"} {
        if {$ref ne "DSP48E1" || ![regexp {^(.*)/ACOUT\[([0-9]+)\]$} $name -> inst bit]} {
            return [list UNRESOLVED unsupported_output_primitive]
        }
        dump_cell $fh $cell
        set areg [property_or_na AREG $cell]
        set acascreg [property_or_na ACASCREG $cell]
        set select [property_or_na A_INPUT $cell]
        puts $fh "  SELECT AREG=$areg ACASCREG=$acascreg A_INPUT=$select"
        if {$areg ne "0" || $acascreg ne "0"} {
            return [list UNRESOLVED registered_or_unknown_DSP_A_path]
        }
        if {$select eq "DIRECT"} {
            set selected [format {%s/A[%d]} $inst $bit]
        } elseif {$select eq "CASCADE"} {
            set selected [format {%s/ACIN[%d]} $inst $bit]
        } else {
            return [list UNRESOLVED unknown_DSP_A_INPUT]
        }
        set input [pin_named $selected]
        puts $fh "  SELECTED_PIN=$selected MATCHES=[llength $input]"
        if {[llength $input] != 1} {return [list UNRESOLVED selected_pin_not_unique]}
        return [constant_trace $input $fh $visited]
    }
    set nets [get_nets -segments -of_objects $pin]
    puts $fh "  NETS=[names $nets]"
    if {[llength $nets] == 0} {return [list UNRESOLVED no_connected_net]}
    set drivers [get_pins -of_objects $nets -filter {DIRECTION == OUT && IS_LEAF == 1}]
    set ports [get_ports -of_objects $nets -filter {DIRECTION == IN}]
    puts $fh "  LEAF_DRIVERS=[names $drivers] INPUT_PORT_DRIVERS=[names $ports]"
    if {[llength $ports] != 0 || [llength $drivers] != 1} {
        return [list UNRESOLVED nonunique_or_external_driver]
    }
    return [constant_trace [lindex $drivers 0] $fh $visited]
}

proc clockless_audit::run {out} {
    variable group_path_limit
    variable source_limit
    variable dumped_cells
    set dumped_cells {}
    set out [file normalize $out]
    if {[file exists $out]} {error "Refusing to reuse output directory: $out"}
    file mkdir $out
    set rc [catch {
        # The raw reports retain total unconstrained endpoint counts and any
        # other category; nothing is reclassified or waived by this script.
        report_timing_summary -delay_type min_max -report_unconstrained \
            -file [file join $out timing_summary.rpt]
        check_timing -verbose -file [file join $out check_timing.rpt]
        report_exceptions -file [file join $out exceptions.rpt]
        report_clocks -file [file join $out clocks.rpt]

        # Two bulk graph traversals, no per-register timing query. Keep typed
        # pin/port collections separate, then merge only their NAME strings.
        set roots {}
        set registers [all_registers -data_pins]
        if {[llength $registers]} {
            set roots [names [all_fanin -flat -startpoints_only -trace_arcs timing $registers]]
        }
        set outputs [all_outputs]
        if {[llength $outputs]} {
            set roots [concat $roots [names [all_fanin -flat -startpoints_only -trace_arcs timing $outputs]]]
        }
        set roots [lsort -unique $roots]
        set root_set {}
        set fh [open [file join $out bulk_startpoints.txt] w]
        foreach root $roots {puts $fh $root; dict set root_set $root 1}
        close $fh
        set register_count [llength $registers]
        unset registers outputs

        set candidates {}
        # ACOUT matching is a candidate selector, never a constant proof.
        set dsps [get_cells -hierarchical -filter {REF_NAME == DSP48E1}]
        if {[llength $dsps]} {
            set acout_pins [get_pins -of_objects $dsps -filter {DIRECTION == OUT && REF_PIN_NAME =~ ACOUT*}]
            foreach name [names $acout_pins] {
                if {[dict exists $root_set $name]} {dict lappend candidates $name bulk_DSP_ACOUT}
            }
        }
        set observed_starts {}
        set observed_endpoints {}
        set group_counts {}
        set fh [open [file join $out sampled_paths.tsv] w]
        puts $fh "delay_type\tgroup\tstartpoint\tendpoint\tstart_clock\tend_clock\tslack\tdata_delay\tmissing_clock"
        foreach mode {max min} {
            # UG835: -sort_by group applies max_paths PER GROUP; -nworst 1
            # still does not enumerate all alternative sources to an endpoint.
            set paths [get_timing_paths -delay_type $mode -sort_by group \
                -max_paths $group_path_limit -nworst 1]
            set witnesses {}
            foreach path $paths {
                set group [get_property GROUP $path]
                dict incr group_counts [list $mode $group]
                set start [get_property STARTPOINT_PIN $path]
                set end [get_property ENDPOINT_PIN $path]
                set sc [get_property STARTPOINT_CLOCK $path]
                set ec [get_property ENDPOINT_CLOCK $path]
                set missing [expr {$sc eq "" || $ec eq ""}]
                puts $fh [join [list $mode $group $start $end $sc $ec \
                    [get_property SLACK $path] [get_property DATAPATH_DELAY $path] $missing] "\t"]
                if {$missing} {
                    dict lappend candidates $start observed_$mode
                    dict set observed_starts $start 1
                    dict set observed_endpoints [list $mode $end] 1
                    lappend witnesses $path
                }
            }
            if {[llength $witnesses]} {
                report_timing -of_objects $witnesses -file [file join $out clockless_$mode.rpt]
            }
            unset paths witnesses
        }
        close $fh
        set fh [open [file join $out group_counts.tsv] w]
        puts $fh "delay_type\tgroup\tsampled_paths\tgroup_limit_hit"
        dict for {key count} $group_counts {
            puts $fh [join [concat $key [list $count [expr {$count >= $group_path_limit}]]] "\t"]
        }
        close $fh

        set candidate_names [lsort [dict keys $candidates]]
        set detail [open [file join $out constant_connections.txt] w]
        set fh [open [file join $out source_classification.tsv] w]
        puts $fh "source\tprovenance\tclassification\treason"
        set inspected 0
        set constants 0
        set unresolved 0
        foreach name $candidate_names {
            set provenance [lsort -unique [dict get $candidates $name]]
            if {$inspected >= $source_limit} {
                puts $fh [join [list $name $provenance NOT_INSPECTED source_limit] "\t"]
                continue
            }
            incr inspected
            puts $detail "\nSOURCE $name PROVENANCE=$provenance"
            set pin [pin_named $name]
            if {[llength $pin] != 1} {
                set result [list UNRESOLVED source_not_unique_leaf_pin]
            } else {
                set result [constant_trace $pin $detail {}]
            }
            if {[lindex $result 0] eq "UNRESOLVED"} {incr unresolved} else {incr constants}
            puts $fh [join [concat [list $name $provenance] $result] "\t"]
        }
        close $fh
        close $detail
        set fh [open [file join $out status.txt] w]
        puts $fh "STATUS=QUERY_COMPLETE"
        puts $fh "COMPLETENESS=PARTIAL_BOUNDED_PATH_SAMPLING"
        puts $fh "REGISTER_DATA_PINS=$register_count"
        puts $fh "BULK_GRAPH_STARTPOINTS=[llength $roots]"
        puts $fh "OBSERVED_MISSING_CLOCK_STARTPOINTS=[dict size $observed_starts]"
        puts $fh "OBSERVED_MODE_ENDPOINT_PAIRS=[dict size $observed_endpoints]"
        puts $fh "SOURCE_CANDIDATES=[llength $candidate_names]"
        puts $fh "INSPECTED_SOURCES=$inspected"
        puts $fh "CONSTANT_SOURCES=$constants"
        puts $fh "UNRESOLVED_SOURCES=$unresolved"
        puts $fh "NOT_INSPECTED_SOURCES=[expr {[llength $candidate_names]-$inspected}]"
        puts $fh "PATHS_PER_GROUP_LIMIT=$group_path_limit"
        puts $fh "NWORST_PER_ENDPOINT=1"
        puts $fh "SOURCE_INSPECTION_LIMIT=$source_limit"
        puts $fh "DESIGN=[get_property NAME [current_design]]"
        puts $fh "VIVADO_VERSION=[version -short]"
        puts $fh "SCOPE=READ_ONLY_DIAGNOSTIC_NOT_TIMING_CLOSURE_OR_GENERAL_CONSTANT_PROOF"
        close $fh
    } message options]
    if {$rc} {
        # An unsupported command/property or query failure must remain an error;
        # never interpret it as an empty graph or evidence of a constant.
        set fh [open [file join $out failure.txt] w]
        puts $fh $message
        puts $fh [dict get $options -errorinfo]
        close $fh
        set fh [open [file join $out status.txt] w]
        puts $fh "STATUS=FAILED"
        close $fh
        return -options $options $message
    }
    return $out
}
