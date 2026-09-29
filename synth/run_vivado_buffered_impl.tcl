# Vivado 2018.3 / 7-series OOC implementation with one real internal BUFG.
# Usage: vivado -mode batch -source synth/run_vivado_buffered_impl.tcl \
#        -tclargs INPUT_DCP NEW_OUT_DIR PERIOD_NS BUFGCTRL_XxYy
# Caller must validate/freeze the synthesized DCP. No synthesis, source edit,
# false path, multicycle, dedicated-route waiver or input-DCP overwrite occurs.
# BUFG/O -> registers is routed; external clk -> BUFG/I remains an OOC boundary.
# SHA-256 needs Windows certutil or Unix shasum. Missing tools fail closed.
# APIs: UG835 v2018.3 pp.93,198,235,238,479,809,814,838,843,1265,1376,1624.
# Scope/routing distinction: UG905 v2018.3 pp.6,8,11.

namespace eval buffered_impl {}
proc buffered_impl::require {condition message} {
    if {![uplevel 1 [list expr $condition]]} {error $message}
}
proc buffered_impl::finite {value} {
    if {![regexp {^[+-]?([0-9]+(\.[0-9]*)?|\.[0-9]+)([eE][+-]?[0-9]+)?$} $value]} {return 0}
    if {[catch {expr {abs(double($value)) < Inf}} result]} {return 0}
    return $result
}
proc buffered_impl::names {objects} {
    if {![llength $objects]} {return {}}
    return [lsort -ascii -unique [get_property NAME $objects]]
}
proc buffered_impl::sha256 {path} {
    if {$::tcl_platform(platform) eq "windows"} {
        set raw [exec certutil -hashfile $path SHA256]
        set hashes {}
        foreach line [split $raw \n] {
            set line [string map {" " "" "\r" "" "\t" ""} $line]
            if {[regexp {^[0-9a-fA-F]{64}$} $line]} {lappend hashes [string tolower $line]}
        }
        require {[llength $hashes] == 1} "certutil did not return exactly one SHA-256"
        return [lindex $hashes 0]
    }
    set raw [exec shasum -a 256 -- $path]
    require {[regexp {^([0-9a-fA-F]{64})[ \t]} $raw -> value]} "shasum did not return SHA-256"
    return [string tolower $value]
}
proc buffered_impl::save_names {path names} {
    set fh [open $path w]
    fconfigure $fh -encoding utf-8 -translation lf
    foreach name $names {
        require {![regexp {[\r\n]} $name]} "Newline in object name cannot be serialized"
        puts $fh $name
    }
    close $fh
    return [sha256 $path]
}
proc buffered_impl::top_net {object} {
    set nets [get_nets -segments -top_net_of_hierarchical_group -of_objects $object]
    require {[llength $nets] == 1} "Expected one connected top-level clock net"
    return $nets
}
proc buffered_impl::segments {net} {return [get_nets -segments $net]}
proc buffered_impl::loads {net} {
    return [get_pins -leaf -of_objects [segments $net] -filter {DIRECTION == IN}]
}
proc buffered_impl::drivers {net} {
    return [get_pins -leaf -of_objects [segments $net] -filter {DIRECTION == OUT}]
}
proc buffered_impl::property_or_empty {key object} {
    if {[lsearch -exact [list_property $object] $key] < 0} {return ""}
    return [get_property $key $object]
}
# Clock-pin set is all sequential primitives (including DSP/BRAM), not a sample.
# Reject other clock domains, undriven clock pins and use of clk as ordinary data.
proc buffered_impl::check_clock_loads {net} {
    set endpoints [names [all_registers -clock_pins]]
    require {[llength $endpoints] > 0} "No sequential clock endpoints"
    require {[names [loads $net]] eq $endpoints} "Clock loads differ from all register clock pins"
    require {[names [all_registers -clock core_clk -clock_pins]] eq $endpoints} \
        "A sequential endpoint is outside core_clk"
    return $endpoints
}
proc buffered_impl::check_buffer {cell_name input_net clock_port clock_site} {
    set cell [get_cells $cell_name]
    require {[llength $cell] == 1} "Inserted BUFG is missing or nonunique"
    require {[get_property REF_NAME $cell] in {BUFG BUFGCTRL}} "Inserted cell is not a global BUFG"
    require {[names [get_cells -hierarchical -filter {REF_NAME =~ BUFG*}]] eq [list $cell_name]} \
        "Unexpected additional global clock buffer"
    require {[get_property LOC $cell] eq $clock_site} "Inserted BUFG location changed"
    set pin_o [get_pins $cell_name/O]
    require {[llength $pin_o] == 1} "Missing BUFG output pin"
    set global_net [top_net $pin_o]
    require {[names [drivers $global_net]] eq [list "$cell_name/O"]} "Global clock has missing/multiple drivers"
    require {![llength [get_ports -of_objects [segments $global_net]]]} "Global clock still touches an external port"
    # opt_design may map BUFG I to BUFGCTRL I0. The port must still feed only
    # the inserted buffer, never a register or another hierarchy.
    set input_loads [loads $input_net]
    require {[llength $input_loads] == 1} "External clock must feed exactly one BUFG input"
    set input_owner [get_cells -of_objects $input_loads]
    require {[names $input_owner] eq [list $cell_name]} "External clock bypasses BUFG"
    require {![llength [drivers $input_net]]} "External clock net has a primitive driver"
    require {[names [get_ports -of_objects [segments $input_net]]] eq [names $clock_port]} \
        "External clock has another port driver"
    require {[names [get_clocks -of_objects $pin_o]] eq {core_clk}} "core_clk did not propagate through BUFG"
    return $global_net
}

if {$argc != 4} {
    puts stderr "Usage: INPUT_DCP NEW_OUT_DIR PERIOD_NS BUFGCTRL_XxYy"
    exit 1
}
lassign $argv input_dcp out period clock_site
set input_dcp [file normalize $input_dcp]
set out [file normalize $out]
if {![buffered_impl::finite $period] || $period <= 0 ||
    ![regexp {^BUFGCTRL_X[0-9]+Y[0-9]+$} $clock_site] ||
    ![file isfile $input_dcp] || ![file readable $input_dcp] || [file size $input_dcp] == 0 ||
    [string tolower [file extension $input_dcp]] ne ".dcp" || [file exists $out]} {
    puts stderr "Require a new output directory, nonempty DCP, positive period and exact BUFGCTRL site"
    exit 1
}
if {[catch {file mkdir $out} error_text]} {puts stderr $error_text; exit 1}
set scope "FPGA_POST_ROUTE_OOC_INTERNAL_BUFG_EXTERNAL_BOUNDARY_ZERO_IO_VECTORLESS_NOT_ASIC_OR_BOARD"
set rc [catch {
    set input_sha [buffered_impl::sha256 $input_dcp]
    set_param general.maxThreads 2
    open_checkpoint $input_dcp
    buffered_impl::require {[string match 2018.3* [version -short]]} "This flow is qualified only for Vivado 2018.3"
    buffered_impl::require {[string match xc7* [get_property PART [current_design]]]} "This BUFG flow is limited to 7-series"
    buffered_impl::require {![llength [get_cells -hierarchical -filter {IS_BLACKBOX == 1}]]} "Black box in checkpoint"
    buffered_impl::require {![report_route_status -has_routing]} "Expected unrouted post-synthesis DCP"
    set clock_port [get_ports clk]
    buffered_impl::require {[llength $clock_port] == 1 && [get_property DIRECTION $clock_port] eq "IN"} "Missing/non-input clk port"
    buffered_impl::require {[buffered_impl::names [get_clocks *]] eq {core_clk} &&
        [buffered_impl::names [get_clocks -of_objects $clock_port]] eq {core_clk}} "Expected sole core_clk sourced at clk"
    buffered_impl::require {![llength [get_cells -hierarchical -filter {REF_NAME =~ BUFG* || REF_NAME =~ BUFR* || REF_NAME =~ BUFH* || REF_NAME =~ BUFIO* || REF_NAME =~ MMCME* || REF_NAME =~ PLLE*}]]} \
        "Already buffered or noncanonical clock network"
    set site [get_sites $clock_site]
    buffered_impl::require {[llength $site] == 1 && [get_property SITE_TYPE $site] eq "BUFGCTRL" &&
        [get_property NAME $site] eq $clock_site} "Invalid BUFGCTRL site"
    buffered_impl::require {![llength [get_cells -of_objects $site]]} "Selected BUFGCTRL site is occupied"
    buffered_impl::require {[llength [get_lib_cells */BUFG]] == 1} "BUFG library reference is missing/nonunique"
    set cell_name __ooc_internal_bufg
    set input_name __ooc_clk_input
    buffered_impl::require {![llength [get_cells -quiet $cell_name]] && ![llength [get_nets -quiet $input_name]]} "ECO names already exist"
    set original_net [buffered_impl::top_net $clock_port]
    buffered_impl::require {![llength [buffered_impl::drivers $original_net]]} "Clock net has a primitive driver"
    buffered_impl::require {[buffered_impl::names [get_ports -of_objects [buffered_impl::segments $original_net]]] eq {clk}} \
        "Clock net has additional external port connections"
    set before [buffered_impl::check_clock_loads $original_net]
    set before_sha [buffered_impl::save_names [file join $out clock_endpoints.before.txt] $before]
    set cells_before [buffered_impl::names [get_cells -hierarchical *]]
    set ports_before [buffered_impl::names [get_ports *]]
    write_xdc [file join $out constraints.before.xdc]
    set old_hd [buffered_impl::property_or_empty HD.CLK_SRC $clock_port]
    if {$old_hd ne ""} {reset_property HD.CLK_SRC $clock_port}
    buffered_impl::require {[buffered_impl::property_or_empty HD.CLK_SRC $clock_port] eq ""} "HD.CLK_SRC was not cleared"

    # Only the top-level port is detached. Never prune or rebuild fanout chains.
    create_cell -reference BUFG $cell_name
    create_net $input_name
    set bufg [get_cells $cell_name]
    set input_net [get_nets $input_name]
    disconnect_net -net $original_net -objects $clock_port
    connect_net -net $input_net -objects $clock_port
    connect_net -net $input_net -objects [get_pins $cell_name/I]
    connect_net -net $original_net -objects [get_pins $cell_name/O]
    set_property LOC $clock_site $bufg
    set_property DONT_TOUCH true $bufg
    buffered_impl::require {[buffered_impl::names [get_cells -hierarchical *]] eq [lsort -ascii [concat $cells_before [list $cell_name]]]} "ECO changed original cell set"
    buffered_impl::require {[buffered_impl::names [get_ports *]] eq $ports_before} "ECO changed port set"
    set global_net [buffered_impl::check_buffer $cell_name $input_net $clock_port $clock_site]
    set after [buffered_impl::check_clock_loads $global_net]
    set after_sha [buffered_impl::save_names [file join $out clock_endpoints.after_eco.txt] $after]
    buffered_impl::require {$before eq $after && $before_sha eq $after_sha} "ECO changed the complete clock endpoint set"

    set fh [open [file join $out screening.xdc] w]
    puts $fh [format {create_clock -name core_clk -period %.9g [get_ports clk]} $period]
    puts $fh {set_clock_uncertainty 0.05 [get_clocks core_clk]}
    puts $fh {set_propagated_clock [get_clocks core_clk]}
    puts $fh {set_input_delay 0.0 -clock core_clk [get_ports -filter {DIRECTION == IN && NAME != clk}]}
    puts $fh {set_output_delay 0.0 -clock core_clk [all_outputs]}
    close $fh
    read_xdc -mode out_of_context [file join $out screening.xdc]
    buffered_impl::require {[buffered_impl::names [get_clocks *]] eq {core_clk}} "Extra clock after constraints"
    set actual_period [get_property PERIOD [get_clocks core_clk]]
    buffered_impl::require {[buffered_impl::finite $actual_period] && abs($actual_period-$period) <= 0.00051} "Effective period differs beyond 1 ps rounding"
    write_checkpoint [file join $out post_clock_eco.dcp]
    opt_design
    place_design
    phys_opt_design
    route_design

    set global_net [buffered_impl::check_buffer $cell_name $input_net $clock_port $clock_site]
    set routed [buffered_impl::check_clock_loads $global_net]
    set routed_sha [buffered_impl::save_names [file join $out clock_endpoints.post_route.txt] $routed]
    report_route_status -of_objects $global_net -show_all -file [file join $out internal_clock_route.rpt]
    set clock_route_status [get_property ROUTE_STATUS $global_net]
    set clock_nodes [get_nodes -of_objects $global_net]
    set clock_pips [get_pips -of_objects $global_net]
    set nodes_sha [buffered_impl::save_names [file join $out internal_clock_nodes.txt] [buffered_impl::names $clock_nodes]]
    set pips_sha [buffered_impl::save_names [file join $out internal_clock_pips.txt] [buffered_impl::names $clock_pips]]
    buffered_impl::require {$clock_route_status eq "ROUTED" && [llength $clock_nodes] > 0 && [llength $clock_pips] > 0} \
        "Internal BUFG clock lacks complete physical routing"
    report_clock_utilization -file [file join $out clock_utilization.rpt]
    report_clocks -file [file join $out clocks.rpt]
    report_exceptions -file [file join $out exceptions.rpt]
    report_utilization -file [file join $out utilization.rpt]
    report_utilization -hierarchical -file [file join $out utilization_hier.rpt]
    report_timing_summary -delay_type min_max -report_unconstrained -file [file join $out timing_summary.rpt]
    foreach mode {max min} {
        report_timing -delay_type $mode -max_paths 20 -path_type full_clock_expanded -input_pins \
            -file [file join $out timing_${mode}_paths.rpt]
        report_timing -from [get_clocks core_clk] -to [get_clocks core_clk] -delay_type $mode \
            -max_paths 20 -path_type full_clock_expanded -input_pins -file [file join $out internal_${mode}_paths.rpt]
    }
    report_route_status -file [file join $out route_status.rpt]
    check_timing -verbose -file [file join $out check_timing.rpt]
    report_drc -file [file join $out drc.rpt]
    report_power -file [file join $out power_vectorless.rpt]
    write_checkpoint [file join $out post_route.dcp]
    set fully_routed [report_route_status -boolean_check ROUTED_FULLY]
    set route_errors [report_route_status -boolean_check ERRORS_IN_ROUTES]
    set drc_errors [llength [get_drc_violations -quiet -filter {SEVERITY == Error}]]
    buffered_impl::require {$fully_routed eq "1" && $route_errors eq "0" && $drc_errors == 0} "Incomplete route or DRC errors"
    set timing_met 1
    foreach mode {max min} {
        foreach group {all internal} {
            set args [list -delay_type $mode -max_paths 1]
            if {$group eq "internal"} {lappend args -from [get_clocks core_clk] -to [get_clocks core_clk]}
            set paths [get_timing_paths {*}$args]
            buffered_impl::require {[llength $paths] == 1} "Missing $group $mode constrained path"
            set slack [get_property SLACK [lindex $paths 0]]
            buffered_impl::require {[buffered_impl::finite $slack]} "Nonfinite $group $mode slack"
            dict set slacks ${group}_${mode} $slack
            if {$slack < 0} {set timing_met 0}
        }
    }
    buffered_impl::require {[buffered_impl::sha256 $input_dcp] eq $input_sha} "Input checkpoint changed on disk"
    set fh [open [file join $out status.txt] w]
    foreach {key value} [list STATUS BUFFERED_IMPL_COMPLETE TIMING_MET $timing_met \
        WNS_NS [dict get $slacks all_max] WHS_NS [dict get $slacks all_min] \
        INTERNAL_WNS_NS [dict get $slacks internal_max] INTERNAL_WHS_NS [dict get $slacks internal_min] \
        ROUTED_FULLY $fully_routed ROUTE_ERRORS $route_errors DRC_ERRORS $drc_errors \
        INPUT_DCP $input_dcp INPUT_DCP_SHA256 $input_sha PART [get_property PART [current_design]] \
        REQUESTED_PERIOD_NS $period EFFECTIVE_PERIOD_NS $actual_period CLOCK_SITE $clock_site \
        CLOCK_UNCERTAINTY_NS 0.05 REMOVED_HD_CLK_SRC $old_hd \
        ENDPOINTS_BEFORE [llength $before] ENDPOINTS_AFTER_ECO [llength $after] ENDPOINTS_POST_ROUTE [llength $routed] \
        ENDPOINTS_BEFORE_SHA256 $before_sha ENDPOINTS_AFTER_ECO_SHA256 $after_sha ENDPOINTS_POST_ROUTE_SHA256 $routed_sha \
        POST_ROUTE_ENDPOINT_SET_CHANGED [expr {$before ne $routed}] \
        INTERNAL_CLOCK_NET [get_property NAME $global_net] INTERNAL_CLOCK_ROUTE_STATUS $clock_route_status \
        INTERNAL_CLOCK_NODES [llength $clock_nodes] INTERNAL_CLOCK_PIPS [llength $clock_pips] \
        INTERNAL_CLOCK_NODES_SHA256 $nodes_sha INTERNAL_CLOCK_PIPS_SHA256 $pips_sha \
        INTERNAL_CLOCK_ROUTED 1 EXTERNAL_CLOCK_ROUTED 0 CONSTRAINT_COVERAGE NOT_CERTIFIED \
        SCRIPT_SHA256 [buffered_impl::sha256 [info script]] VIVADO_VERSION [version -short] SCOPE $scope] {puts $fh "$key=$value"}
    close $fh
} error_text error_options]
if {$rc} {
    set fh [open [file join $out failure.txt] w]
    puts $fh $error_text
    puts $fh [dict get $error_options -errorinfo]
    close $fh
    set fh [open [file join $out status.txt] w]
    puts $fh "STATUS=FAILED\nSCOPE=$scope"
    close $fh
    puts stderr $error_text
    exit 1
}
if {!$timing_met} {exit 3}
exit 0
