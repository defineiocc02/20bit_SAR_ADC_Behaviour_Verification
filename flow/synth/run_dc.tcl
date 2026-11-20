# Generic explicit-input DC flow. No device, technology or timing-budget defaults.
proc synth_require {condition message} {
    if {![uplevel 1 [list expr $condition]]} {error $message}
}
proc synth_finite {value} {
    if {![string is double -strict $value]} {return 0}
    if {[catch {expr {abs(double($value)) < Inf}} result]} {return 0}
    return $result
}
proc synth_main {} {
    synth_require {[info exists ::env(SYNTH_INPUT_TCL)] && $::env(SYNTH_INPUT_TCL) ne ""} "Explicit SYNTH_INPUT_TCL required"
    synth_require {[file isfile $::env(SYNTH_INPUT_TCL)]} "Input contract not found"
    source $::env(SYNTH_INPUT_TCL)
    synth_require {[info exists ::synth_inputs]} "Missing synth_inputs dictionary"
    set cfg $::synth_inputs
    foreach key {top sources include_dirs defines libraries sdc clock compile_command parameters out} {
        synth_require {[dict exists $cfg $key]} "Missing explicit input: $key"
    }
    foreach key {sources libraries} {
        synth_require {[llength [dict get $cfg $key]] > 0} "Empty explicit $key"
        foreach item [dict get $cfg $key] {synth_require {[file isfile $item]} "Missing input: $item"}
    }
    foreach directory [dict get $cfg include_dirs] {
        synth_require {[file isdirectory $directory]} "Missing include directory"
    }
    synth_require {[file isfile [dict get $cfg sdc]]} "Missing explicit SDC"
    set out [file normalize [dict get $cfg out]]
    synth_require {![file exists $out]} "Refusing existing report directory"
    file mkdir $out
    set_app_var sh_continue_on_error false
    define_design_lib WORK -path [file join $out work]
    set_app_var search_path [dict get $cfg include_dirs]
    set_app_var target_library [dict get $cfg libraries]
    set_app_var link_library [concat [list *] [dict get $cfg libraries]]
    set options [list -format sverilog]
    if {[llength [dict get $cfg defines]]} {lappend options -define [dict get $cfg defines]}
    analyze {*}$options [dict get $cfg sources]
    if {[dict get $cfg parameters] eq ""} {
        elaborate [dict get $cfg top]
    } else {
        elaborate [dict get $cfg top] -parameters [dict get $cfg parameters]
    }
    current_design [dict get $cfg top]
    synth_require {[link]} "Unresolved design/library references"
    synth_require {[check_design]} "check_design rejected the elaborated design"
    read_sdc [dict get $cfg sdc]
    set selected [get_clocks -quiet [dict get $cfg clock]]
    synth_require {[sizeof_collection $selected] == 1 && [sizeof_collection [get_clocks -quiet *]] == 1} "Exactly one explicit clock domain required"
    set registers [all_registers]
    set clocked [all_registers -clock [dict get $cfg clock]]
    synth_require {[sizeof_collection $registers] > 0 && [sizeof_collection $registers] == [sizeof_collection $clocked]} "Unclocked or foreign-domain sequential cells"
    set compiler [dict get $cfg compile_command]
    synth_require {$compiler in {compile compile_ultra}} "Unsupported compile command"
    set compiled [$compiler]
    synth_require {[string is boolean -strict $compiled] && $compiled} "Compile command did not report success"
    set registers [all_registers]
    set clocked [all_registers -clock [dict get $cfg clock]]
    synth_require {[sizeof_collection $registers] > 0 && [sizeof_collection $registers] == [sizeof_collection $clocked]} "Post-compile sequential clock-domain check failed"
    redirect [file join $out area.rpt] {report_area -hierarchy}
    redirect [file join $out qor.rpt] {report_qor}
    redirect [file join $out clocks.rpt] {report_clock}
    redirect [file join $out check_design.rpt] {check_design}
    redirect [file join $out check_timing.rpt] {check_timing -verbose}
    set violation 0
    foreach mode {max min} {
        redirect [file join $out timing_$mode.rpt] {report_timing -delay $mode -max_paths 10 -nworst 1}
        set paths [get_timing_paths -delay $mode -max_paths 1 -nworst 1]
        synth_require {[sizeof_collection $paths] == 1} "Missing constrained $mode timing path"
        set slack [get_attribute $paths slack]
        synth_require {[synth_finite $slack]} "Nonfinite $mode slack"
        if {$slack < 0} {set violation 1}
        set ${mode}_slack $slack
    }
    set fh [open [file join $out status.txt] w]
    puts $fh "STATUS=COMPILE_COMPLETE"
    puts $fh "TIMING_CLOSURE=NOT_CERTIFIED"
    puts $fh "MAX_PATH_SLACK=$max_slack"
    puts $fh "MIN_PATH_SLACK=$min_slack"
    puts $fh "USER_SDC=[dict get $cfg sdc]"
    puts $fh "CLOCK=[dict get $cfg clock]"
    puts $fh "COMPILE_COMMAND=$compiler"
    puts $fh "SCOPE=GENERIC_SINGLE_DOMAIN_DECLARED_CONSTRAINTS_NOT_SIGNOFF"
    close $fh
    puts "GENERIC_SYNTH_COMPLETE sources=[llength [dict get $cfg sources]] registers=[sizeof_collection $registers] clocks=1 reported_paths=2 closure=NOT_CERTIFIED"
    return [expr {$violation ? 3 : 0}]
}
if {[catch {synth_main} result options]} {
    puts stderr "DC_FATAL: $result"
    if {[dict exists $options -errorinfo]} {puts stderr [dict get $options -errorinfo]}
    exit 1
}
exit $result
