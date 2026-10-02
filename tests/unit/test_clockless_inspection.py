"""Bounded read-only query and conservative constant classification; Tcl mocks only."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

FLOW = Path(__file__).resolve().parents[2] / "synth/inspect_clockless_paths.tcl"

HARNESS = r"""
set ::scenario $::env(SCENARIO)
set ::bulk_calls 0
proc current_design {} {return design}
proc version {args} {return mock-only}
proc report_stub {args} {
    set i [lsearch -exact $args -file]
    set f [open [lindex $args [expr {$i+1}]] w]; puts $f MOCK_ONLY; close $f
}
foreach command {report_timing_summary check_timing report_exceptions report_clocks report_timing} {
    proc $command {args} {report_stub {*}$args}
}
foreach command {set_false_path set_case_analysis set_clock_groups create_clock set_property opt_design place_design route_design write_checkpoint} {
    proc $command {args} {error "FORBIDDEN_MUTATION"}
}
proc all_registers {args} {return {reg0/D reg1/D}}
proc all_outputs {} {return result}
proc all_fanin {args} {
    incr ::bulk_calls
    if {$::scenario eq "query_error"} {error "injected fanin command failure"}
    if {[lindex $args end] eq "result"} {return {dsp1/ACOUT[29]}}
    return {reg0/C dsp0/ACOUT[29] dsp1/ACOUT[29]}
}
proc get_timing_paths {args} {
    puts "TIMING_QUERY $args"
    if {$::scenario eq "no_paths"} {return {}}
    return {p0 p1 p_clocked}
}
proc get_cells {args} {
    if {[lsearch -exact $args -hierarchical] >= 0} {return {dsp0 dsp1}}
    return [lindex [split [lindex [lindex $args end] 0] /] 0]
}
proc get_property {key obj} {
    if {[llength $obj] == 1} {set obj [lindex $obj 0]}
    switch -- $key {
        NAME {return $obj}
        GROUP {if {$obj eq "p_clocked"} {return core_clk}; return {(none)}}
        STARTPOINT_PIN {
            if {$obj eq "p_clocked"} {return reg0/C}
            if {$obj eq "p0"} {return {dsp0/ACOUT[29]}}
            return {dsp1/ACOUT[29]}
        }
        ENDPOINT_PIN {if {$obj eq "p0"} {return reg0/D}; return reg1/D}
        STARTPOINT_CLOCK {if {$obj eq "p_clocked"} {return core_clk}; return {}}
        ENDPOINT_CLOCK {return core_clk}
        SLACK {if {$obj eq "p_clocked"} {return -0.1}; return inf}
        DATAPATH_DELAY {return 1.25}
        REF_NAME {
            if {$obj in {dsp0 dsp1}} {return DSP48E1}
            if {$obj eq "tie"} {return GND}
            if {$obj eq "tie1"} {return VCC}
            return LUT1
        }
        DIRECTION {
            if {[regexp {/(A|ACIN)\[[0-9]+\]$} $obj] || [string match */CLK $obj]} {return IN}
            return OUT
        }
        AREG {if {$::scenario eq "registered" && $obj eq "dsp0"} {return 1}; return 0}
        ACASCREG {return 0}
        A_INPUT {if {$::scenario eq "cascade" && $obj eq "dsp1"} {return CASCADE}; return DIRECT}
        default {error "unexpected property $key $obj"}
    }
}
proc list_property {object} {return {NAME REF_NAME A_INPUT AREG ACASCREG}}
proc get_pins {args} {
    if {[lsearch -exact $args -hierarchical] >= 0} {
        regexp {NAME == "(.*)"} [lindex $args end] -> name
        return [list $name]
    }
    if {[string first "ACOUT*" [lindex $args end]] >= 0} {return {dsp0/ACOUT[29] dsp1/ACOUT[29]}}
    set nets [lindex $args [expr {[lsearch -exact $args -of_objects]+1}]]
    if {$nets eq "n_cascade"} {return {dsp0/ACOUT[29]}}
    if {$::scenario eq "multiple_drivers"} {return {tie/G tie1/P}}
    if {$::scenario eq "dynamic"} {return lut/O}
    if {$::scenario eq "vcc"} {return tie1/P}
    return tie/G
}
proc get_nets {args} {
    if {$::scenario eq "empty_net"} {return {}}
    if {[lindex [lindex $args end] 0] eq {dsp1/ACIN[29]}} {return n_cascade}
    return n_gnd
}
proc get_ports {args} {return {}}
proc get_clocks {args} {return {}}
source $::env(FLOW)
if {$::scenario eq "limited"} {set clockless_audit::source_limit 1}
clockless_audit::run $::env(OUT)
puts "BULK_CALLS=$::bulk_calls"
"""


def run_probe(tmp_path, scenario):
    tcl = shutil.which("tclsh")
    if not tcl:
        pytest.skip("tclsh required for query-control mock; not EDA validation")
    harness = tmp_path / "probe.tcl"
    harness.write_text(HARNESS)
    out = tmp_path / "out"
    cp = subprocess.run(
        [tcl, str(harness)],
        env={**os.environ, "FLOW": str(FLOW), "OUT": str(out), "SCENARIO": scenario},
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    return cp, out


@pytest.mark.parametrize(
    "scenario,kind",
    [("direct", "CONSTANT_ZERO"), ("cascade", "CONSTANT_ZERO"), ("vcc", "CONSTANT_ONE")],
)
def test_selected_constant_chain_is_classified_without_claiming_full_coverage(
    tmp_path, scenario, kind
):
    cp, out = run_probe(tmp_path, scenario)
    assert cp.returncode == 0, cp.stdout + cp.stderr
    assert "BULK_CALLS=2" in cp.stdout
    assert cp.stdout.count("TIMING_QUERY") == 2
    assert "-sort_by group -max_paths 256 -nworst 1" in cp.stdout
    table = (out / "source_classification.tsv").read_text()
    assert table.count(kind) == 2
    status = (out / "status.txt").read_text()
    assert "STATUS=QUERY_COMPLETE" in status
    assert "COMPLETENESS=PARTIAL" in status
    assert "OBSERVED_MISSING_CLOCK_STARTPOINTS=2" in status
    assert "OBSERVED_MODE_ENDPOINT_PAIRS=4" in status
    assert "CONSTANT_SOURCES=2" in status
    assert "AREG=0 ACASCREG=0" in (out / "constant_connections.txt").read_text()
    assert "PASS" not in status


@pytest.mark.parametrize("scenario", ["registered", "multiple_drivers", "dynamic", "empty_net"])
def test_incomplete_constant_proof_stays_unresolved(tmp_path, scenario):
    cp, out = run_probe(tmp_path, scenario)
    assert cp.returncode == 0, cp.stdout + cp.stderr
    assert "UNRESOLVED" in (out / "source_classification.tsv").read_text()
    assert "COMPLETENESS=PARTIAL" in (out / "status.txt").read_text()


def test_actual_query_error_is_failure_not_empty_graph_or_constant(tmp_path):
    cp, out = run_probe(tmp_path, "query_error")
    assert cp.returncode != 0
    assert "STATUS=FAILED" in (out / "status.txt").read_text()
    assert "injected fanin command failure" in (out / "failure.txt").read_text()


def test_limit_and_empty_path_sample_do_not_silently_certify_completeness(tmp_path):
    cp, out = run_probe(tmp_path, "limited")
    assert cp.returncode == 0, cp.stderr
    assert "NOT_INSPECTED" in (out / "source_classification.tsv").read_text()
    assert "NOT_INSPECTED_SOURCES=1" in (out / "status.txt").read_text()
    second = tmp_path / "no_paths"
    second.mkdir()
    cp, out = run_probe(second, "no_paths")
    assert cp.returncode == 0, cp.stderr
    assert "OBSERVED_MISSING_CLOCK_STARTPOINTS=0" in (out / "status.txt").read_text()
    assert "COMPLETENESS=PARTIAL" in (out / "status.txt").read_text()
    assert "bulk_DSP_ACOUT" in (out / "source_classification.tsv").read_text()
