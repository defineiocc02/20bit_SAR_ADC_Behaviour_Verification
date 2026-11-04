# Export only: caller verifies synthesis manifest + DCP SHA256 before invocation.
# No RTL synthesis, implementation, SDF, or timing signoff occurs in this script.
# Usage: INPUT_DCP NEW_OUT_DIR EXPECTED_PART MANIFEST_P_RECON_STAGES
if {$argc != 4} {puts stderr "Usage: INPUT_DCP NEW_OUT_DIR PART STAGES"; exit 1}
lassign $argv checkpoint out part stages
set checkpoint [file normalize $checkpoint]
set out [file normalize $out]
if {$stages ni {5 7} || ![file isfile $checkpoint] || [file size $checkpoint] == 0 ||
    [string tolower [file extension $checkpoint]] ne ".dcp"} {
    puts stderr "A nonempty DCP and manifest-verified P5/P7 profile are required"
    exit 1
}
if {[file exists $out]} {puts stderr "Refusing to reuse $out"; exit 1}
file mkdir $out
set rc [catch {
    set_param general.maxThreads 2
    open_checkpoint $checkpoint
    if {[get_property NAME [current_design]] ne "sar20_digital_core"} {
        error "Checkpoint is not the full sar20_digital_core top"
    }
    if {[get_property PART [current_design]] ne $part} {error "DCP part disagrees with manifest"}
    if {[llength [get_cells -hierarchical -filter {IS_BLACKBOX == 1}]] != 0} {
        error "Unresolved black boxes in checkpoint"
    }
    foreach port {clk rst_n cfg_wr cfg_addr cfg_wdata cfg_rdata cfg_ready dout dout_valid dout_sample_id dout_flags} {
        # get_ports bus-name queries can return each bit; existence is sufficient.
        if {[llength [get_ports -quiet $port]] == 0 &&
            [llength [get_ports -quiet ${port}\[*\]]] == 0} {
            error "Missing full-top public port: $port"
        }
    }
    write_verilog -mode funcsim [file join $out full_top_funcsim.v]
    set fh [open [file join $out status.txt] w]
    puts $fh "STATUS=FULL_MAPPED_EXPORT_COMPLETE"
    puts $fh "TOP=sar20_digital_core"
    puts $fh "PART=$part"
    puts $fh "P_RECON_STAGES=$stages"
    puts $fh "STAGE_BINDING=CALLER_SYNTHESIS_MANIFEST_AND_DCP_SHA256"
    puts $fh "VIVADO_VERSION=[version -short]"
    puts $fh "SCOPE=FULL_TOP_POST_SYNTH_FUNCTIONAL_NO_SDF_NOT_TIMING"
    close $fh
} message options]
if {$rc} {
    set fh [open [file join $out failure.txt] w]
    puts $fh $message
    puts $fh [dict get $options -errorinfo]
    close $fh
    puts stderr $message
    exit 1
}
exit 0
