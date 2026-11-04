"""Check implementation-flow Tcl decisions with mocks, not FPGA timing or EDA semantics."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from pathlib import Path

import pytest

FLOW = Path(__file__).resolve().parents[2] / "synth/run_vivado_impl.tcl"

HARNESS = r"""
set ::failure $::env(TEST_FAILURE)
proc set_param {args} {puts "PARAM $args"}
proc open_checkpoint {path} {
    puts "OPEN $path"
    if {$::failure eq "open"} {error "injected open failure"}
}
proc get_cells {args} {
    if {$::failure eq "blackbox"} {return unresolved}
    return {}
}
proc get_ports {args} {
    if {[lsearch -exact $args -filter] >= 0} {return {rst_n data}}
    if {$::failure eq "clock_port"} {return {}}
    return clk
}
proc get_sites {args} {
    if {$::failure eq "clock_site"} {return {}}
    return [lindex $args end]
}
proc get_property {property object} {
    switch -- $property {
        SITE_TYPE {
            if {$::failure eq "site_type"} {return SLICE}
            return BUFGCTRL
        }
        NAME {return $object}
        PART {return fixture_part}
        SLACK {
            if {$object eq "worst_max"} {return $::env(TEST_WNS)}
            return $::env(TEST_WHS)
        }
        default {error "unexpected property $property"}
    }
}
proc create_clock {args} {
    puts "CLOCK $args"
    set ::clock_seen 1
}
proc set_clock_uncertainty {args} {puts "UNCERTAINTY $args"}
proc get_clocks {args} {
    if {![info exists ::clock_seen]} {error "clock queried before create_clock"}
    if {$::failure eq "extra_clock"} {return {core_clk unexpected}}
    return core_clk
}
proc set_property {property value object} {
    if {$property ne "HD.CLK_SRC"} {error "unexpected property $property"}
    if {![info exists ::clock_seen]} {error "HD.CLK_SRC before create_clock"}
    puts "CLOCK_SITE $value $object"
}
proc all_outputs {} {return result}
proc set_input_delay {args} {puts "INPUT $args"}
proc set_output_delay {args} {puts "OUTPUT $args"}
proc read_xdc {args} {
    puts "XDC $args"
    source [lindex $args end]
}
proc implementation_step {name args} {
    if {![info exists ::clock_seen]} {error "unconstrained implementation"}
    puts "STEP $name $args"
    if {$::failure eq $name} {error "injected $name failure"}
}
foreach command {opt_design place_design phys_opt_design route_design} {
    proc $command {args} [format {implementation_step %s {*}$args} $command]
}
proc output_report {name args} {
    puts "REPORT $name $args"
    if {$::failure eq $name} {error "injected report failure"}
    set index [lsearch -exact $args -file]
    if {$index < 0} {error "missing report path"}
    set fh [open [lindex $args [expr {$index+1}]] w]
    puts $fh "MOCK ONLY: $name"
    close $fh
}
foreach command {report_utilization report_timing_summary report_timing check_timing report_drc report_power} {
    proc $command {args} [format {output_report %s {*}$args} $command]
}
proc report_route_status {args} {
    if {[lindex $args 0] eq "-boolean_check"} {
        if {[lindex $args 1] eq "ROUTED_FULLY"} {
            return [expr {$::failure ne "unrouted"}]
        }
        return [expr {$::failure eq "route_errors"}]
    }
    output_report report_route_status {*}$args
}
proc get_drc_violations {args} {
    if {$::failure eq "drc"} {return error_violation}
    return {}
}
proc write_checkpoint {path} {
    puts "WRITE $path"
    set fh [open $path w]
    puts $fh "MOCK CHECKPOINT ONLY"
    close $fh
}
proc get_timing_paths {args} {
    set mode [lindex $args 1]
    if {$::failure eq "no_$mode"} {return {}}
    return worst_$mode
}
proc current_design {} {return fixture_design}
proc version {args} {return stub-only}
source $::env(TEST_FLOW)
"""


def probe(
    tmp_path,
    *,
    period="20",
    site="BUFGCTRL_X0Y0",
    failure="",
    wns="0.25",
    whs="0.1",
    input_exists=True,
    existing_out=False,
):
    tclsh = shutil.which("tclsh")
    if tclsh is None:
        pytest.skip("tclsh is required for flow-control checks, not EDA validation")
    checkpoint = tmp_path / "original source.dcp"
    original = b"MOCK SYNTHESIZED CHECKPOINT; immutable input\n"
    if input_exists:
        checkpoint.write_bytes(original)
    out = tmp_path / "new results"
    if existing_out:
        out.mkdir()
        (out / "status.txt").write_text("DO NOT OVERWRITE")
    harness = tmp_path / "probe.tcl"
    harness.write_text(HARNESS)
    result = subprocess.run(
        [tclsh, str(harness), str(checkpoint), str(out), period, site],
        env={
            **os.environ,
            "TEST_FLOW": str(FLOW),
            "TEST_FAILURE": failure,
            "TEST_WNS": wns,
            "TEST_WHS": whs,
        },
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if input_exists:
        assert hashlib.sha256(checkpoint.read_bytes()).digest() == hashlib.sha256(original).digest()
    return result, out


def test_routed_flow_preserves_dcp_and_constraints_before_implementation(tmp_path):
    result, out = probe(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PARAM general.maxThreads 2" in result.stdout
    assert "CLOCK -name core_clk -period 20 clk" in result.stdout
    assert "UNCERTAINTY 0.05 core_clk" in result.stdout
    assert "INPUT 0.0 -clock core_clk {rst_n data}" in result.stdout
    assert "OUTPUT 0.0 -clock core_clk result" in result.stdout
    order = [
        "OPEN ",
        "CLOCK ",
        "CLOCK_SITE ",
        "STEP opt_design",
        "STEP place_design",
        "STEP phys_opt_design",
        "STEP route_design",
        "WRITE ",
    ]
    assert [result.stdout.index(marker) for marker in order] == sorted(
        result.stdout.index(marker) for marker in order
    )
    assert "XDC -mode out_of_context" in result.stdout
    assert "-delay_type min_max -report_unconstrained" in result.stdout
    assert "report_timing -delay_type max" in result.stdout
    assert "report_timing -delay_type min" in result.stdout
    assert "EXTERNAL_CLOCK_ROUTED=0" in (out / "status.txt").read_text()
    assert "WNS_NS=0.25" in (out / "status.txt").read_text()
    assert "WHS_NS=0.1" in (out / "status.txt").read_text()
    assert "NOT_ASIC_OR_BOARD" in (out / "status.txt").read_text()
    assert {p.name for p in out.iterdir()} == {
        "screening.xdc",
        "utilization.rpt",
        "utilization_hier.rpt",
        "timing_summary.rpt",
        "timing_setup_paths.rpt",
        "timing_hold_paths.rpt",
        "route_status.rpt",
        "check_timing.rpt",
        "drc.rpt",
        "power_vectorless.rpt",
        "post_route.dcp",
        "status.txt",
    }
    assert "false_path" not in (out / "screening.xdc").read_text()
    assert "multicycle" not in (out / "screening.xdc").read_text()


@pytest.mark.parametrize("wns,whs", [("-0.1", "0.2"), ("0.2", "-0.1"), ("-0.1", "-0.2")])
def test_setup_or_hold_violation_is_completed_but_not_passed(tmp_path, wns, whs):
    result, out = probe(tmp_path, wns=wns, whs=whs)
    assert result.returncode == 3, result.stdout + result.stderr
    status = (out / "status.txt").read_text()
    assert "STATUS=IMPL_COMPLETE" in status and "TIMING_MET=0" in status
    assert (out / "post_route.dcp").is_file()


@pytest.mark.parametrize(
    "failure",
    [
        "open",
        "blackbox",
        "clock_port",
        "clock_site",
        "site_type",
        "extra_clock",
        "opt_design",
        "place_design",
        "phys_opt_design",
        "route_design",
        "report_power",
        "unrouted",
        "route_errors",
        "drc",
        "no_max",
        "no_min",
    ],
)
def test_flow_or_integrity_failure_cannot_report_completion(tmp_path, failure):
    result, out = probe(tmp_path, failure=failure)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "STATUS=FAILED" in (out / "status.txt").read_text()
    assert "IMPL_COMPLETE" not in (out / "status.txt").read_text()
    assert (out / "failure.txt").is_file()


@pytest.mark.parametrize("wns,whs", [("NaN", "0"), ("0", "Inf"), ("-Inf", "0"), ("0", "")])
def test_missing_or_nonfinite_slack_is_failure(tmp_path, wns, whs):
    result, out = probe(tmp_path, wns=wns, whs=whs)
    assert result.returncode == 1
    assert "STATUS=FAILED" in (out / "status.txt").read_text()


@pytest.mark.parametrize("period", ["0", "-1", "NaN", "Inf", "1e999", "not-a-period"])
def test_invalid_period_never_opens_dcp(tmp_path, period):
    result, out = probe(tmp_path, period=period)
    assert result.returncode == 1
    assert "OPEN " not in result.stdout and not out.exists()


def test_existing_output_and_missing_input_are_not_overwritten_or_accepted(tmp_path):
    a = tmp_path / "existing"
    a.mkdir()
    result, out = probe(a, existing_out=True)
    assert result.returncode == 1
    assert (out / "status.txt").read_text() == "DO NOT OVERWRITE"
    b = tmp_path / "missing"
    b.mkdir()
    result, out = probe(b, input_exists=False)
    assert result.returncode == 1 and not out.exists()


def test_clock_location_must_be_exact_site_name_not_pattern(tmp_path):
    result, out = probe(tmp_path, site="BUFGCTRL_*")
    assert result.returncode == 1 and not out.exists()
