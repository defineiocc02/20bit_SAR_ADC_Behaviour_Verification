"""Tcl ECO/flow guards against a small stateful mock, never real FPGA routing."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

FLOW = Path(__file__).resolve().parents[2] / "synth/run_vivado_buffered_impl.tcl"
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures/vivado_2018_3_buffered"
HARNESS = r"""
set ::failure $::env(TEST_FAILURE)
if {$::env(TEST_HASH_STYLE) ne "unix"} {
    set ::tcl_platform(platform) windows
    rename exec original_exec
    proc exec {args} {
        if {[lrange $args 0 1] ne {certutil -hashfile} || [lindex $args end] ne "SHA256"} {
            error "Unexpected external command"
        }
        if {$::env(TEST_HASH_STYLE) eq "missing"} {error "certutil missing"}
        if {$::env(TEST_HASH_STYLE) eq "malformed"} {return "No digest"}
        set digest [lindex [original_exec shasum -a 256 -- [lindex $args 2]] 0]
        if {$::env(TEST_HASH_STYLE) eq "duplicate"} {return "$digest\n$digest"}
        return "Localized certutil header\r\n[join [regexp -all -inline .. [string toupper $digest]] { }]\r\nLocalized success"
    }
}
set ::created 0
set ::routed 0
set ::hd BUFGCTRL_X0Y1
set ::ep {u0/C u1/C}
set ::cells {u0 u1}
set ::ports {clk rst_n data result}
set ::nets [dict create clk clk_net u0/C clk_net u1/C clk_net]
proc arg {args key} {
    set i [lsearch -exact $args $key]
    if {$i < 0} {return {}}
    return [lindex $args [expr {$i+1}]]
}
proc set_param {args} {puts "PARAM $args"}
proc open_checkpoint {path} {
    puts "OPEN $path"
    if {$::failure eq "open"} {error "open failure"}
}
proc version {args} {
    if {$::failure eq "version"} {return 2024.1}
    return 2018.3
}
proc current_design {} {return top}
proc get_cells {args} {
    set filter [arg $args -filter]
    set of [arg $args -of_objects]
    if {$of ne ""} {
        if {[string match BUFGCTRL_* $of]} {
            if {$::failure eq "occupied"} {return occupied_cell}
            return {}
        }
        return [file dirname $of]
    }
    if {$filter ne ""} {
        if {[string match *IS_BLACKBOX* $filter]} {
            if {$::failure eq "blackbox"} {return blackbox}
            return {}
        }
        if {$::failure eq "buffered"} {return existing_bufg}
        if {$::created} {
            if {$::failure eq "extra_buffer" && $::routed} {return {__ooc_internal_bufg extra_bufg}}
            return __ooc_internal_bufg
        }
        return {}
    }
    set name [lindex $args end]
    if {$name eq "*"} {return $::cells}
    if {$name eq "__ooc_internal_bufg" && $::failure eq "name_collision"} {return $name}
    if {[lsearch -exact $::cells $name] >= 0} {return $name}
    return {}
}
proc get_ports {args} {
    set of [arg $args -of_objects]
    if {$of ne ""} {
        set out {}
        foreach p $::ports {
            if {[dict exists $::nets $p] && [dict get $::nets $p] eq $of} {lappend out $p}
        }
        if {$::failure eq "other_port" && $of eq "clk_net"} {lappend out another_clock}
        return $out
    }
    if {[arg $args -filter] ne ""} {return {rst_n data}}
    if {[lindex $args end] eq "*"} {return $::ports}
    if {$::failure eq "clock_port"} {return {}}
    return clk
}
proc get_sites {name} {
    if {$::failure eq "clock_site"} {return {}}
    return $name
}
proc get_lib_cells {args} {
    if {$::failure eq "library"} {return {}}
    return UNISIMS/BUFG
}
proc get_clocks {args} {
    if {$::failure eq "extra_clock"} {return {core_clk wrong_clk}}
    if {$::failure eq "clock_propagation" && $::created && [arg $args -of_objects] ne ""} {return {}}
    return core_clk
}
proc get_nets {args} {
    set of [arg $args -of_objects]
    if {$of ne ""} {
        if {![dict exists $::nets $of]} {return {}}
        return [dict get $::nets $of]
    }
    set name [lindex $args end]
    if {$name eq "__ooc_clk_input" && !$::created} {return {}}
    if {$name in {clk_net __ooc_clk_input}} {return $name}
    error "Unexpected get_nets $args"
}
proc get_pins {args} {
    set of [arg $args -of_objects]
    if {$of ne ""} {
        set filter [arg $args -filter]
        set outputs {}
        if {$::failure eq "driver" && $of eq "clk_net" && !$::created} {return driver/O}
        if {$::failure eq "extra_driver" && $of eq "clk_net" && $::created} {return {__ooc_internal_bufg/O bad/O}}
        dict for {pin net} $::nets {
            if {$net ne $of || [string first / $pin] < 0} {continue}
            set dir [expr {[string match */O $pin] ? "OUT" : "IN"}]
            if {$filter eq "DIRECTION == $dir"} {lappend outputs $pin}
        }
        if {$::failure eq "drop_endpoint" && $::created && $of eq "clk_net" && $filter eq "DIRECTION == IN"} {return u0/C}
        return $outputs
    }
    return [lindex $args end]
}
proc all_registers {args} {
    if {$::failure eq "no_endpoints"} {return {}}
    if {$::failure eq "other_domain"} {return {u0/C u1/C other/C}}
    if {$::failure eq "wrong_domain" && [arg $args -clock] ne ""} {return u0/C}
    if {[lsearch -exact $args -data_pins] >= 0} {return {u0/D u1/D}}
    return $::ep
}
proc get_property {key object} {
    switch -- $key {
        NAME {return $object}
        PART {
            if {$::failure eq "part"} {return xcvu9p-flga2104-2L-e}
            return xc7k325tffg900-2
        }
        DIRECTION {
            if {$::failure eq "clock_direction"} {return OUT}
            return IN
        }
        SITE_TYPE {
            if {$::failure eq "site_type"} {return SLICE}
            return BUFGCTRL
        }
        HD.CLK_SRC {return $::hd}
        LOC {
            if {$::failure eq "loc_changed" && $::routed} {return BUFGCTRL_X0Y3}
            return $::loc
        }
        REF_NAME {
            if {$::failure eq "wrong_buffer"} {return LUT1}
            return BUFG
        }
        PERIOD {
            if {$::failure eq "period_readback"} {return 21}
            return $::period
        }
        ROUTE_STATUS {
            if {$::failure eq "clock_partial"} {return PARTIAL}
            if {$::failure eq "clock_hierport"} {return HIERPORT}
            return ROUTED
        }
        SLACK {
            if {[string match *max $object]} {return $::env(TEST_WNS)}
            return $::env(TEST_WHS)
        }
        default {error "Unknown property $key $object"}
    }
}
proc list_property {object} {return HD.CLK_SRC}
proc reset_property {key object} {
    puts "RESET $key $object"
    if {$::failure ne "hd_stuck"} {set ::hd {}}
}
proc create_cell {args} {
    puts "CREATE_CELL $args"
    set ::created 1
    lappend ::cells [lindex $args end]
    if {$::failure eq "cell_set"} {lappend ::cells unexpected}
}
proc create_net {args} {puts "CREATE_NET $args"}
proc disconnect_net {args} {
    puts "DISCONNECT $args"
    if {[lsearch -exact $args -prune] >= 0} {error "Must never prune"}
    set object [arg $args -objects]
    if {$object ne "clk"} {error "Must only detach top-level clock port"}
    dict unset ::nets $object
}
proc connect_net {args} {
    puts "CONNECT $args"
    if {$::failure eq "connect"} {error "connect failure"}
    dict set ::nets [arg $args -objects] [arg $args -net]
}
proc set_property {key value object} {
    puts "PROPERTY $key $value $object"
    if {$key eq "LOC"} {set ::loc $value;return}
    if {$key eq "DONT_TOUCH"} {return}
    error "Unexpected property mutation $key"
}
proc create_clock {args} {set ::period [arg $args -period];puts "CLOCK $args"}
proc set_clock_uncertainty {args} {puts "UNCERTAINTY $args"}
proc set_propagated_clock {args} {puts "PROPAGATED $args"}
proc set_input_delay {args} {puts "INPUT_DELAY $args"}
proc set_output_delay {args} {puts "OUTPUT_DELAY $args"}
proc all_outputs {} {return result}
proc read_xdc {args} {puts "XDC $args";source [lindex $args end]}
proc write_xdc {path} {set f [open $path w];puts $f "MOCK constraints";close $f}
proc step {name} {
    puts "STEP $name"
    if {$::failure eq $name} {error "$name failed"}
    if {$name eq "route_design"} {
        set ::routed 1
        if {$::failure eq "route_replication"} {
            lappend ::ep u2/C
            dict set ::nets u2/C clk_net
        }
    }
}
foreach command {opt_design place_design phys_opt_design route_design} {
    proc $command {} [format {step %s} $command]
}
proc report {name args} {
    puts "REPORT $name $args"
    if {$::failure eq $name} {error "$name failed"}
    if {$name eq "check_timing"} {
        file copy $::env(TEST_CHECK_REPORT) [arg $args -file];return
    }
    if {$name eq "report_timing_summary"} {
        file copy $::env(TEST_SUMMARY_REPORT) [arg $args -file];return
    }
    set f [open [arg $args -file] w];puts $f "MOCK ONLY $name";close $f
}
foreach command {report_clock_utilization report_clocks report_exceptions report_utilization report_timing_summary report_timing check_timing report_drc report_power} {
    proc $command {args} [format {report %s {*}$args} $command]
}
proc report_route_status {args} {
    if {[lsearch -exact $args -has_routing] >= 0} {return [expr {$::failure eq "already_routed"}]}
    set flag [arg $args -boolean_check]
    if {$flag eq "ROUTED_FULLY"} {return [expr {$::failure ne "unrouted"}]}
    if {$flag eq "ERRORS_IN_ROUTES"} {return [expr {$::failure eq "route_errors"}]}
    report report_route_status {*}$args
}
proc get_nodes {args} {
    if {$::failure eq "no_nodes"} {return {}}
    return {mock_global_node0 mock_global_node1}
}
proc get_pips {args} {
    if {$::failure eq "no_pips"} {return {}}
    return {mock_global_pip0 mock_global_pip1}
}
proc get_drc_violations {args} {
    if {$::failure eq "drc"} {return violation}
    return {}
}
proc get_timing_paths {args} {
    set mode [arg $args -delay_type]
    set from [arg $args -from]
    set kind [expr {$from ne "" ? "internal" : "all"}]
    if {$kind eq "internal" && ($from ne $::ep || [arg $args -to] ne {u0/D u1/D})} {
        error "Internal timing query must use sequential clock and data pins, not a clock group"
    }
    if {$::failure eq "no_${kind}_${mode}"} {return {}}
    return ${kind}_${mode}
}
proc write_checkpoint {path} {
    puts "WRITE $path"
    set f [open $path w];puts $f "MOCK DCP ONLY";close $f
}
source $::env(TEST_FLOW)
"""


def probe(
    tmp_path,
    *,
    failure="",
    period="20",
    site="BUFGCTRL_X0Y0",
    wns="0.2",
    whs="0.1",
    wpws="0.025",
    tpws="0.000",
    failing=(0, 0, 0),
    check_report=None,
    summary_report=None,
    hash_style="unix",
    existing_out=False,
    input_exists=True,
):
    tclsh = shutil.which("tclsh")
    if not tclsh or not shutil.which("shasum"):
        pytest.skip("Local mock requires tclsh and shasum, not Vivado")
    checkpoint = tmp_path / "input source.dcp"
    original = b"MOCK INPUT DCP; must remain byte-identical\n"
    if input_exists:
        checkpoint.write_bytes(original)
    out = tmp_path / "new output"
    if existing_out:
        out.mkdir()
        (out / "status.txt").write_text("DO NOT OVERWRITE")
    harness = tmp_path / "probe.tcl"
    harness.write_text(HARNESS)
    coverage = tmp_path / "fixture_check.rpt"
    coverage.write_text(
        (FIXTURES / "check_timing.rpt").read_text() if check_report is None else check_report
    )
    timing = tmp_path / "fixture_timing.rpt"
    if summary_report is None:
        summary_report = (FIXTURES / "timing_summary.rpt").read_text()
        # Replace only the real report's first 12-column summary row. All
        # retained path details remain historical fixture text, not mock STA.
        summary_report = re.sub(
            r"(?m)^\s*-17\.426\s+-170862\.625\s+71067\s+201263\s+-0\.264\s+-15260\.233\s+61733\s+201263\s+0\.025\s+0\.000\s+0\s+66823\s*$",
            f"{wns} 0.000 {failing[0]} 201263 {whs} 0.000 {failing[1]} 201263 {wpws} {tpws} {failing[2]} 66823",
            summary_report,
            count=1,
        )
    timing.write_text(summary_report)
    result = subprocess.run(
        [tclsh, str(harness), str(checkpoint), str(out), period, site],
        env={
            **os.environ,
            "TEST_FLOW": str(FLOW),
            "TEST_HASH_STYLE": hash_style,
            "TEST_FAILURE": failure,
            "TEST_WNS": wns,
            "TEST_WHS": whs,
            "TEST_CHECK_REPORT": str(coverage),
            "TEST_SUMMARY_REPORT": str(timing),
        },
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    if input_exists:
        assert checkpoint.read_bytes() == original
    return result, out


def test_single_bufg_eco_preserves_complete_endpoint_set_and_records_real_hashes(tmp_path):
    result, out = probe(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    status = (out / "status.txt").read_text()
    assert "STATUS=BUFFERED_IMPL_COMPLETE" in status
    assert "INTERNAL_CLOCK_ROUTED=1\nEXTERNAL_CLOCK_ROUTED=0" in status
    assert "CONSTRAINT_COVERAGE=CHECKED_UNDER_RECORDED_OOC_CONSTRAINTS" in status
    assert "BOARD_TIMING_CERTIFIED=0" in status
    assert "WPWS_NS=0.025\nTPWS_NS=0.000" in status
    assert "CHECK_TIMING_CATEGORIES=16\nCHECK_TIMING_ISSUES=0" in status
    assert "UNCONSTRAINED_PATH_GROUPS=0\nIGNORED_PATH_GROUPS=0" in status
    before = (out / "clock_endpoints.before.txt").read_bytes()
    assert before == (out / "clock_endpoints.after_eco.txt").read_bytes() == b"u0/C\nu1/C\n"
    digest = hashlib.sha256(before).hexdigest()
    assert f"ENDPOINTS_BEFORE_SHA256={digest}" in status
    assert f"ENDPOINTS_AFTER_ECO_SHA256={digest}" in status
    assert "ENDPOINTS_BEFORE=2\nENDPOINTS_AFTER_ECO=2\nENDPOINTS_POST_ROUTE=2" in status
    assert result.stdout.count("DISCONNECT ") == 1
    assert "DISCONNECT -net clk_net -objects clk" in result.stdout
    assert "CONNECT -net clk_net -objects __ooc_internal_bufg/O" in result.stdout
    assert "RESET HD.CLK_SRC clk" in result.stdout
    assert "PROPERTY LOC BUFGCTRL_X0Y0 __ooc_internal_bufg" in result.stdout
    assert "PROPAGATED core_clk" in result.stdout
    assert result.stdout.index("DISCONNECT ") < result.stdout.index("STEP opt_design")
    assert result.stdout.index("clock_eco.dcp") < result.stdout.index("STEP opt_design")
    assert "-path_type full_clock_expanded" in result.stdout
    assert "report_route_status -of_objects clk_net" in result.stdout
    assert "INTERNAL_CLOCK_ROUTE_STATUS=ROUTED" in status
    assert "INTERNAL_CLOCK_NODES=2\nINTERNAL_CLOCK_PIPS=2" in status
    xdc = (out / "screening.xdc").read_text()
    assert "create_clock -name core_clk -period 20 [get_ports clk]" in xdc
    for forbidden in [
        "HD.CLK_SRC",
        "create_generated_clock",
        "false_path",
        "multicycle",
        "CLOCK_DEDICATED_ROUTE",
    ]:
        assert forbidden not in xdc


@pytest.mark.parametrize(
    "failure,reason",
    {
        "open": "open failure",
        "version": "qualified only for Vivado 2018.3",
        "part": "limited to 7-series",
        "blackbox": "Black box",
        "already_routed": "Expected unrouted",
        "clock_port": "Missing/non-input clk",
        "clock_direction": "Missing/non-input clk",
        "extra_clock": "Expected sole core_clk",
        "buffered": "Already buffered",
        "clock_site": "Invalid BUFGCTRL site",
        "site_type": "Invalid BUFGCTRL site",
        "occupied": "site is occupied",
        "library": "BUFG library reference",
        "name_collision": "ECO names already exist",
        "driver": "Clock net has a primitive driver",
        "other_port": "additional external port",
        "no_endpoints": "No sequential clock endpoints",
        "other_domain": "Clock loads differ",
        "wrong_domain": "outside core_clk",
        "hd_stuck": "HD.CLK_SRC was not cleared",
        "connect": "connect failure",
        "cell_set": "ECO changed original cell set",
        "wrong_buffer": "not a global BUFG",
        "drop_endpoint": "Clock loads differ",
        "extra_driver": "Global clock has missing/multiple drivers",
        "clock_propagation": "core_clk did not propagate",
        "opt_design": "opt_design failed",
        "place_design": "place_design failed",
        "phys_opt_design": "phys_opt_design failed",
        "route_design": "route_design failed",
        "extra_buffer": "Unexpected additional global",
        "loc_changed": "BUFG location changed",
        "clock_partial": "lacks complete physical routing",
        "clock_hierport": "lacks complete physical routing",
        "no_nodes": "lacks complete physical routing",
        "no_pips": "lacks complete physical routing",
        "report_power": "report_power failed",
        "unrouted": "Incomplete route or DRC errors",
        "route_errors": "Incomplete route or DRC errors",
        "drc": "Incomplete route or DRC errors",
        "no_all_max": "Missing all max constrained path",
        "no_all_min": "Missing all min constrained path",
        "no_internal_max": "Missing internal max constrained path",
        "no_internal_min": "Missing internal min constrained path",
        "period_readback": "Effective period differs",
    }.items(),
)
def test_invalid_network_or_failed_routing_cannot_report_completion(tmp_path, failure, reason):
    result, out = probe(tmp_path, failure=failure)
    assert result.returncode == 1, result.stdout + result.stderr
    assert reason in result.stderr
    assert reason in (out / "failure.txt").read_text()
    assert "STATUS=FAILED" in (out / "status.txt").read_text()
    assert "BUFFERED_IMPL_COMPLETE" not in (out / "status.txt").read_text()


@pytest.mark.parametrize("wns,whs", [("-0.1", "0.1"), ("0.2", "-0.1"), ("-0.1", "-0.1")])
def test_setup_or_hold_violation_is_complete_but_not_timing_pass(tmp_path, wns, whs):
    result, out = probe(tmp_path, wns=wns, whs=whs)
    assert result.returncode == 3, result.stdout + result.stderr
    assert "STATUS=BUFFERED_IMPL_COMPLETE\nTIMING_MET=0" in (out / "status.txt").read_text()


@pytest.mark.parametrize("wns,whs", [("NaN", "0.1"), ("0", "Inf"), ("-Inf", "0"), ("0", "")])
def test_nonfinite_slack_cannot_pass(tmp_path, wns, whs):
    result, out = probe(tmp_path, wns=wns, whs=whs)
    assert result.returncode == 1
    assert "STATUS=FAILED" in (out / "status.txt").read_text()


def test_unmodified_real_2018_3_reports_parse_without_promoting_baseline(tmp_path):
    provenance = json.loads((FIXTURES / "provenance.json").read_text())
    for name, identity in provenance["files"].items():
        assert hashlib.sha256((FIXTURES / name).read_bytes()).hexdigest() == identity["sha256"]
    result, out = probe(
        tmp_path,
        wns="-17.426",
        whs="-0.264",
        summary_report=(FIXTURES / "timing_summary.rpt").read_text(),
    )
    assert result.returncode == 3, result.stdout + result.stderr
    fields = dict(line.split("=", 1) for line in (out / "status.txt").read_text().splitlines())
    assert fields["TIMING_MET"] == "0"
    assert fields["WPWS_NS"] == "0.025"
    assert fields["SETUP_FAILING_ENDPOINTS"] == "71067"
    assert fields["HOLD_FAILING_ENDPOINTS"] == "61733"
    assert fields["PULSE_WIDTH_TOTAL_ENDPOINTS"] == "66823"
    assert fields["CHECK_TIMING_CATEGORIES"] == "16"


@pytest.mark.parametrize("category", range(16))
def test_each_nonzero_check_timing_category_requires_diagnosis(tmp_path, category):
    text = (FIXTURES / "check_timing.rpt").read_text()
    rows = list(re.finditer(r"There are 0 [^\n]+", text))
    assert len(rows) == 16
    row = rows[category]
    text = (
        text[: row.start()]
        + row.group().replace("There are 0 ", "There are 1 ", 1)
        + text[row.end() :]
    )
    result, out = probe(tmp_path, check_report=text)
    assert result.returncode == 1
    assert "check_timing requires independent diagnosis" in result.stderr
    assert "STATUS=FAILED" in (out / "status.txt").read_text()
    assert (out / "check_timing.rpt").read_text() == text


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "unknown", "nonnumeric", "empty"])
def test_check_timing_schema_cannot_silently_default_to_zero(tmp_path, mutation):
    text = (FIXTURES / "check_timing.rpt").read_text()
    row = re.search(r"There are 0 [^\n]+", text).group()
    if mutation == "missing":
        text = text.replace(row, "", 1)
    elif mutation == "duplicate":
        text += "\n" + row + "\n"
    elif mutation == "unknown":
        text += "\nThere are 0 unrecognized future category.\n"
    elif mutation == "nonnumeric":
        text = text.replace("There are 0 ", "There are unavailable ", 1)
    else:
        text = ""
    result, out = probe(tmp_path, check_report=text)
    assert result.returncode == 1
    assert "STATUS=FAILED" in (out / "status.txt").read_text()


@pytest.mark.parametrize("title", ["Unconstrained Path Table", "User Ignored Path Table"])
def test_nonempty_path_groups_cannot_report_constraint_coverage(tmp_path, title):
    text = (FIXTURES / "timing_summary.rpt").read_text()
    start = text.index("| " + title + "\n")
    line_end = text.index("\n", text.index("Path Group", start))
    text = text[:line_end] + "\n(none) (none) core_clk" + text[line_end:]
    result, out = probe(tmp_path, summary_report=text)
    assert result.returncode == 1
    assert title + " requires independent diagnosis" in result.stderr
    assert "STATUS=FAILED" in (out / "status.txt").read_text()


@pytest.mark.parametrize(
    "wpws,tpws,failing",
    [
        ("-0.001", "-0.001", (0, 0, 1)),  # Minimum period/high/low pulse check failure.
        ("-0.001", "0", (0, 0, 0)),
        ("0.025", "-0.001", (0, 0, 0)),
        ("0.025", "0", (0, 0, 1)),
        ("0.025", "0", (1, 0, 0)),
        ("0.025", "0", (0, 1, 0)),
    ],
)
def test_pulse_or_failing_endpoints_prevent_timing_pass(tmp_path, wpws, tpws, failing):
    result, out = probe(tmp_path, wpws=wpws, tpws=tpws, failing=failing)
    assert result.returncode == 3, result.stdout + result.stderr
    status = (out / "status.txt").read_text()
    assert "STATUS=BUFFERED_IMPL_COMPLETE\nTIMING_MET=0" in status
    assert f"WPWS_NS={wpws}\nTPWS_NS={tpws}" in status


@pytest.mark.parametrize(
    "kwargs",
    [
        {"wpws": "NaN"},
        {"tpws": "Inf"},
        {"wpws": "n/a"},
        {"failing": (-1, 0, 0)},
        {"failing": (201264, 0, 0)},
        {"failing": (0, 0, "unknown")},
    ],
)
def test_unparseable_or_invalid_summary_values_fail_closed(tmp_path, kwargs):
    result, out = probe(tmp_path, **kwargs)
    assert result.returncode == 1
    assert "STATUS=FAILED" in (out / "status.txt").read_text()


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_header",
        "duplicate_header",
        "missing_row",
        "short_row",
        "missing_groups",
        "query_mismatch",
    ],
)
def test_summary_completeness_and_crosscheck(tmp_path, mutation):
    text = (FIXTURES / "timing_summary.rpt").read_text()
    header = next(line for line in text.splitlines() if line.lstrip().startswith("WNS(ns)"))
    row = next(line for line in text.splitlines() if line.lstrip().startswith("-17.426"))
    if mutation == "missing_header":
        text = text.replace(header, "", 1)
    elif mutation == "duplicate_header":
        text = text.replace(header, header + "\n" + header, 1)
    elif mutation == "missing_row":
        text = text.replace(row, "", 1)
    elif mutation == "short_row":
        text = text.replace(row, " ".join(row.split()[:-1]), 1)
    elif mutation == "missing_groups":
        text = text.replace("| Unconstrained Path Table", "| Missing section", 1)
    # Unmodified fixture in query_mismatch has negative WNS/WHS, while the
    # mocked timing-object query is positive; neither result may be promoted.
    result, out = probe(tmp_path, summary_report=text)
    assert result.returncode == 1
    assert "STATUS=FAILED" in (out / "status.txt").read_text()


@pytest.mark.parametrize("period", ["0", "-1", "NaN", "Inf", "1e999", "bad"])
def test_bad_period_is_rejected_before_dcp_open(tmp_path, period):
    result, out = probe(tmp_path, period=period)
    assert result.returncode == 1 and "OPEN " not in result.stdout and not out.exists()


def test_site_wildcard_is_rejected_before_open(tmp_path):
    result, out = probe(tmp_path, site="BUFGCTRL_*")
    assert result.returncode == 1 and "OPEN " not in result.stdout and not out.exists()


def test_postroute_optimization_endpoint_change_is_recorded_separately_from_eco(tmp_path):
    result, out = probe(tmp_path, failure="route_replication")
    assert result.returncode == 0, result.stdout + result.stderr
    status = (out / "status.txt").read_text()
    assert "ENDPOINTS_AFTER_ECO=2\nENDPOINTS_POST_ROUTE=3" in status
    assert "POST_ROUTE_ENDPOINT_SET_CHANGED=1" in status


def test_certutil_localized_and_space_separated_digest_parser(tmp_path):
    result, out = probe(tmp_path, hash_style="windows")
    assert result.returncode == 0, result.stdout + result.stderr
    digest = hashlib.sha256((out / "clock_endpoints.before.txt").read_bytes()).hexdigest()
    assert f"ENDPOINTS_BEFORE_SHA256={digest}" in (out / "status.txt").read_text()


@pytest.mark.parametrize("hash_style", ["missing", "malformed", "duplicate"])
def test_hash_tool_failure_never_opens_dcp(tmp_path, hash_style):
    result, out = probe(tmp_path, hash_style=hash_style)
    assert result.returncode == 1 and "OPEN " not in result.stdout
    assert "STATUS=FAILED" in (out / "status.txt").read_text()


def test_existing_directory_is_not_overwritten(tmp_path):
    result, out = probe(tmp_path, existing_out=True)
    assert result.returncode == 1 and "OPEN " not in result.stdout
    assert (out / "status.txt").read_text() == "DO NOT OVERWRITE"


def test_missing_input_is_not_accepted(tmp_path):
    result, out = probe(tmp_path, input_exists=False)
    assert result.returncode == 1 and "OPEN " not in result.stdout and not out.exists()
