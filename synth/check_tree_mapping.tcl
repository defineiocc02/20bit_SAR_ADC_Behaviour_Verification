# Standalone small-cell synthesis witness; no full ADC or timing signoff.
# Usage: vivado -mode batch -source synth/check_tree_mapping.tcl -tclargs RTL_ROOT OUT
# Then simulate OUT/mapped.v with sim/tb/tree_mapping_tb.sv and vendor glbl/unisim.
if {$argc != 2} {error "Usage: RTL_ROOT OUT"}
lassign $argv rtl_root out
set rtl_root [file normalize $rtl_root]
set out [file normalize $out]
if {[file exists $out]} {error "Refusing to overwrite $out"}
file mkdir $out
create_project -in_memory -part xc7vx690tffg1761-2
set_param general.maxThreads 2
set_property include_dirs [list [file join $rtl_root rtl params]] [current_fileset]
read_verilog -sv [list [file join $rtl_root rtl top sadc_enc.sv] \
    [file join $rtl_root rtl core cal_weight_reduce.sv] \
    [file join $rtl_root sim tb tree_mapping_dut.sv]]
synth_design -top tree_mapping_dut -part xc7vx690tffg1761-2 \
    -mode out_of_context -flatten_hierarchy rebuilt
report_utilization -file [file join $out utilization.rpt]
write_verilog -mode funcsim -force [file join $out mapped.v]
write_checkpoint [file join $out mapped.dcp]
# A disconnected comparator input invalidates the PPA/functional baseline.
# Save the check without aborting: the intentionally broken negative control
# still needs its mapped netlist to reproduce the independent TB failure.
set fh [open [file join $out input_fanout.csv] w]
puts $fh "input,endpoint_count"
set checked 0
set disconnected 0
foreach p [get_ports -filter {DIRECTION == IN}] {
    set loads [all_fanout -flat -endpoints_only -from $p]
    puts $fh "[get_property NAME $p],[llength $loads]"
    incr checked
    if {[llength $loads] == 0} {incr disconnected}
}
close $fh
set fh [open [file join $out synth_complete.txt] w]
puts $fh "TREE_MAPPING_SYNTH_COMPLETE inputs=$checked disconnected=$disconnected"
close $fh
exit 0
