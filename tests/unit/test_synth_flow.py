"""以Tcl命令替身验证显式DC控制流，不形成综合或STA证据。"""

from __future__ import annotations

import pytest
from _synth_test_support import DRIVER, ROOT, inputs, tcl_probe

HARNESS = r"""
set ::compiled 0
set ::scenario $::env(SCENARIO)
proc step {name args} {
    puts "CALL $name $args"
    if {$::scenario eq $name} {error "injected $name failure"}
}
proc set_app_var {args} {step set_app_var {*}$args}
proc define_design_lib {args} {step define_design_lib {*}$args}
proc analyze {args} {step analyze {*}$args}
proc elaborate {args} {step elaborate {*}$args}
proc current_design {args} {step current_design {*}$args}
proc link {} {step link; return [expr {$::scenario ne "unresolved"}]}
proc check_design {} {step check_design; return [expr {$::scenario ne "bad_design"}]}
proc read_sdc {path} {step read_sdc $path; source $path}
proc get_clocks {args} {
    if {$::scenario eq "no_clock"} {return {}}
    if {$::scenario eq "extra_clock" && [lindex $args end] eq "*"} {return {sample_clock foreign}}
    return $::user_clock_name
}
proc all_registers {args} {
    if {$::scenario eq "no_registers"} {return {}}
    if {[llength $args] && ($::scenario eq "foreign_domain" ||
        ($::scenario eq "post_domain" && $::compiled))} {return r0}
    return {r0 r1}
}
proc sizeof_collection {items} {return [llength $items]}
proc do_compile {name} {
    step $name
    set ::compiled 1
    return $::env(COMPILE_RESULT)
}
proc compile {} {return [do_compile compile]}
proc compile_ultra {} {return [do_compile compile_ultra]}
proc redirect {path body} {
    set fh [open $path w]; puts $fh "MOCK REPORT ONLY"; close $fh
    uplevel 1 $body
}
foreach name {report_area report_qor report_clock check_timing report_timing} {
    proc $name {args} [format {step %s {*}$args} $name]
}
proc get_timing_paths {args} {
    set mode [lindex $args 1]
    if {$::scenario eq "no_$mode"} {return {}}
    if {$::scenario eq "many_$mode"} {return {p0 p1}}
    return $mode
}
proc get_attribute {path attr} {
    if {$path eq "max"} {return $::env(WNS)}
    return $::env(WHS)
}
foreach name {create_clock set_input_delay set_output_delay set_load set_clock_uncertainty} {
    proc $name {args} {error "UNDISCLOSED_DEFAULT_CONSTRAINT"}
}
source $::env(FLOW)
"""


def probe(
    tmp_path,
    *,
    scenario="",
    compile_command="compile",
    compile_result="1",
    wns="0.125",
    whs="0.0625",
    parameters="",
    existing=False,
):
    data = inputs(tmp_path / "inputs")
    data["sdc"].write_text("set ::user_clock_name sample_clock\nset ::user_clock_period 2.75\n")
    reports = tmp_path / "reports"
    if existing:
        reports.mkdir()
        (reports / "sentinel").write_text("preserve")
    config = {
        "top": "sample_counter",
        "sources": [data["source"], data["leaf"]],
        "include_dirs": [data["include"]],
        "defines": ["TEXT_FIXTURE=1"],
        "libraries": [data["library"]],
        "sdc": data["sdc"],
        "clock": "sample_clock",
        "compile_command": compile_command,
        "parameters": parameters,
        "out": reports,
    }
    contract = tmp_path / "inputs.tcl"
    contract.write_text(DRIVER.render_contract(config))
    cp = tcl_probe(
        tmp_path,
        HARNESS,
        env={
            "SYNTH_INPUT_TCL": str(contract),
            "FLOW": str(ROOT / "synth/run_dc.tcl"),
            "SCENARIO": scenario,
            "COMPILE_RESULT": compile_result,
            "WNS": wns,
            "WHS": whs,
        },
    )
    return cp, reports, data


@pytest.mark.parametrize("compiler", ["compile", "compile_ultra"])
@pytest.mark.parametrize("parameters", ["", "P_WIDTH=7,P_DELAY=2"])
def test_explicit_sources_library_sdc_and_compile_are_forwarded(tmp_path, compiler, parameters):
    cp, out, data = probe(tmp_path, compile_command=compiler, parameters=parameters)
    assert cp.returncode == 0, cp.stdout + cp.stderr
    assert cp.stdout.count("CALL " + compiler + " ") == 1
    assert (
        "GENERIC_SYNTH_COMPLETE sources=2 registers=2 clocks=1 reported_paths=2 closure=NOT_CERTIFIED"
        in cp.stdout
    )
    assert "CALL analyze -format sverilog -define TEXT_FIXTURE=1" in cp.stdout
    assert str(data["source"]) in cp.stdout and str(data["leaf"]) in cp.stdout
    assert "CALL read_sdc " + str(data["sdc"]) in cp.stdout
    assert cp.stdout.index("CALL read_sdc") < cp.stdout.index("CALL " + compiler + " ")
    assert ("-parameters " + parameters in cp.stdout) == bool(parameters)
    status = (out / "status.txt").read_text()
    assert "TIMING_CLOSURE=NOT_CERTIFIED" in status
    assert "MAX_PATH_SLACK=0.125\nMIN_PATH_SLACK=0.0625" in status
    assert "USER_SDC=" + str(data["sdc"]) in status
    assert "UNDISCLOSED_DEFAULT_CONSTRAINT" not in cp.stderr


@pytest.mark.parametrize("compiler", ["compile", "compile_ultra"])
def test_compile_exception_is_fatal(tmp_path, compiler):
    cp, out, _ = probe(tmp_path, scenario=compiler, compile_command=compiler)
    assert cp.returncode == 1 and f"injected {compiler} failure" in cp.stderr
    assert "GENERIC_SYNTH_COMPLETE" not in cp.stdout and not (out / "status.txt").exists()


@pytest.mark.parametrize("value", ["0", "false", "not_a_boolean"])
def test_non_success_compile_result_is_not_accepted(tmp_path, value):
    cp, _, _ = probe(tmp_path, compile_result=value)
    assert cp.returncode == 1 and "Compile command did not report success" in cp.stderr
    assert "GENERIC_SYNTH_COMPLETE" not in cp.stdout


@pytest.mark.parametrize(
    "scenario,reason",
    [
        ("analyze", "injected analyze failure"),
        ("elaborate", "injected elaborate failure"),
        ("unresolved", "Unresolved design/library references"),
        ("bad_design", "check_design rejected"),
        ("read_sdc", "injected read_sdc failure"),
        ("no_clock", "Exactly one explicit clock"),
        ("extra_clock", "Exactly one explicit clock"),
        ("no_registers", "Unclocked or foreign-domain"),
        ("foreign_domain", "Unclocked or foreign-domain"),
        ("post_domain", "Post-compile sequential"),
        ("no_max", "Missing constrained max"),
        ("no_min", "Missing constrained min"),
        ("many_max", "Missing constrained max"),
        ("many_min", "Missing constrained min"),
        ("report_timing", "injected report_timing failure"),
    ],
)
def test_failed_elaboration_domain_or_timing_query_cannot_complete(tmp_path, scenario, reason):
    cp, out, _ = probe(tmp_path, scenario=scenario)
    assert cp.returncode == 1 and reason in cp.stderr
    assert "GENERIC_SYNTH_COMPLETE" not in cp.stdout and not (out / "status.txt").exists()


@pytest.mark.parametrize("wns,whs", [("-0.125", "0.1"), ("0.2", "-0.01"), ("-0.1", "-0.1")])
def test_negative_setup_or_hold_is_distinct_from_incomplete_command(tmp_path, wns, whs):
    cp, out, _ = probe(tmp_path, wns=wns, whs=whs)
    assert cp.returncode == 3, cp.stdout + cp.stderr
    assert "GENERIC_SYNTH_COMPLETE" in cp.stdout
    assert f"MAX_PATH_SLACK={wns}\nMIN_PATH_SLACK={whs}" in (out / "status.txt").read_text()


@pytest.mark.parametrize(
    "wns,whs", [("NaN", "0"), ("0", "Inf"), ("-Inf", "0"), ("", "0"), ("bad", "0")]
)
def test_missing_or_nonfinite_slack_is_fatal(tmp_path, wns, whs):
    cp, _, _ = probe(tmp_path, wns=wns, whs=whs)
    assert cp.returncode == 1 and "Nonfinite" in cp.stderr
    assert "GENERIC_SYNTH_COMPLETE" not in cp.stdout


def test_existing_report_directory_is_preserved(tmp_path):
    cp, out, _ = probe(tmp_path, existing=True)
    assert cp.returncode == 1 and "Refusing existing" in cp.stderr
    assert (out / "sentinel").read_text() == "preserve"
    assert "CALL analyze" not in cp.stdout


def test_unknown_compile_command_cannot_call_a_tool(tmp_path):
    cp, _, _ = probe(tmp_path, compile_command="unexpected")
    assert cp.returncode == 1 and "Unsupported compile command" in cp.stderr
    assert "CALL compile " not in cp.stdout
