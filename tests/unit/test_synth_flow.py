"""Execute synthesis flow decisions with Tcl/Bash stubs, without claiming EDA signoff."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
FLOW = REPO / "synth/run_dc.tcl"


def block(start: str, end: str) -> str:
    source = FLOW.read_text(encoding="utf-8")
    return source[source.index(start) : source.index(end)]


def tcl(script: str):
    exe = shutil.which("tclsh")
    if exe is None:
        pytest.skip("tclsh is required; the RTL CI job installs it explicitly")
    return subprocess.run([exe], input=script, text=True, capture_output=True, timeout=10)


def compile_probe(mode: str, drf: int, fail: bool = False):
    code = block('set DRF "1"', "# ---- 6) 报告")
    body = 'error "injected tool failure"' if fail else 'puts "CALLED $name $args"'
    script = f"""set COMPILE_MODE {mode}
set ::env(DESIGN_RULE_FIX) {drf}
proc dc_phase {{args}} {{}}
proc invoke {{name args}} {{{body}}}
proc compile {{args}} {{invoke compile {{*}}$args}}
proc compile_ultra {{args}} {{invoke compile_ultra {{*}}$args}}
{code}
"""
    return tcl(script)


@pytest.mark.parametrize(
    "mode,drf,expected",
    [
        ("ultra", 1, "CALLED compile_ultra -no_autoungroup"),
        ("compile", 1, "CALLED compile "),
        ("compile", 0, "CALLED compile -no_design_rule"),
    ],
)
def test_actual_compile_command_matches_mode(mode, drf, expected):
    cp = compile_probe(mode, drf)
    assert cp.returncode == 0, cp.stdout + cp.stderr
    assert expected in cp.stdout
    assert cp.stdout.count("CALLED") == 1


def test_ultra_cannot_disable_compile_only_design_rule_fix():
    cp = compile_probe("ultra", 0)
    assert cp.returncode == 2 and "CALLED" not in cp.stdout


@pytest.mark.parametrize("mode", ["compile", "ultra"])
def test_compile_error_is_fatal(mode):
    cp = compile_probe(mode, 1, fail=True)
    assert cp.returncode == 1
    assert "injected tool failure" in cp.stdout


@pytest.mark.parametrize("delay", ["0.0", "0", "1.5"])
def test_zero_io_delay_is_still_an_explicit_constraint(delay):
    code = block("# ---- 4) 约束", "# ---- 5) compile")
    script = f"""proc dc_phase {{args}} {{}}
proc unknown {{args}} {{return {{}}}}
proc get_ports {{args}} {{return clk}}
proc all_inputs {{}} {{return {{clk data}}}}
proc all_outputs {{}} {{return result}}
proc remove_from_collection {{args}} {{return data}}
proc sizeof_collection {{value}} {{return [llength $value]}}
proc get_clocks {{args}} {{return clk}}
proc set_input_delay {{args}} {{puts "INPUT $args"}}
proc set_output_delay {{args}} {{puts "OUTPUT $args"}}
set CLK_NAME clk
set CLK_PERIOD 10
set DRIVE_CELL buffer
set LIB_CAP_UNIT_PF 1.0
set LOAD_PF 0
set MAX_TRANS 1
set IN_DELAY {delay}
set OUT_DELAY {delay}
{code}
"""
    cp = tcl(script)
    assert cp.returncode == 0, cp.stdout + cp.stderr
    assert f"INPUT {delay} -clock clk data" in cp.stdout
    assert f"OUTPUT {delay} -clock clk result" in cp.stdout


def wrapper_probe(tmp_path, status, tool_rc, wns="1.0"):
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash is required; installed in the RTL CI environment")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    # The fake tool emits a success marker and then either succeeds or crashes.
    tool = bindir / "fake_dc"
    tool.write_text(
        "#!/usr/bin/env bash\n"
        'printf "DC_STATUS=%s\\nWNS=%s\\n" "$TEST_DC_STATUS" "$TEST_DC_WNS" > "$OUT_DIR/status.txt"\n'
        'exit "$TEST_DC_RC"\n',
        encoding="utf-8",
    )
    timeout = bindir / "timeout"
    timeout.write_text('#!/usr/bin/env bash\nshift\nexec "$@"\n', encoding="utf-8")
    tool.chmod(0o755)
    timeout.chmod(0o755)
    env = {
        **os.environ,
        "PATH": str(bindir) + os.pathsep + os.environ["PATH"],
        "DC_SHELL": str(tool),
        "TSMC_ENV": str(tmp_path / "no_pdk"),
        "TEST_DC_STATUS": status,
        "TEST_DC_WNS": wns,
        "TEST_DC_RC": str(tool_rc),
        "TOP": "dummy",
        "FILES": "dummy.sv",
        "CLK_PERIOD": "10",
        "PREFLIGHT": "0",
        "OUT_DIR": str(tmp_path / "out"),
        "COMPILE_MODE": "compile",
        "NO_DESIGN_RULE": "0",
        "CHECK_ONLY": "0",
    }
    cp = subprocess.run(
        [bash, str(REPO / "synth/run_synth.sh")],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return cp


@pytest.mark.parametrize("status", ["OK", "PREFLIGHT_OK", "CHECK_ONLY_OK"])
@pytest.mark.parametrize("tool_rc", [0, 9])
def test_success_marker_cannot_hide_process_failure(tmp_path, status, tool_rc):
    cp = wrapper_probe(tmp_path, status, tool_rc)
    assert cp.returncode == (0 if tool_rc == 0 else 1), cp.stdout + cp.stderr
    if tool_rc:
        assert "success marker conflicts" in cp.stderr


def test_unknown_compile_mode_is_rejected():
    cp = compile_probe("bogus", 1)
    assert cp.returncode == 2 and "CALLED" not in cp.stdout


@pytest.mark.parametrize(
    "slacks,wns,tns",
    [
        ("-1 -2 3", "-2", "-3.0"),
        ("1 2 3", "1", "0.0"),
        ("", "NA", "NA"),
    ],
)
def test_wns_and_sampled_negative_slack_sum_are_distinct(slacks, wns, tns):
    code = block(
        "proc dc_slack",
        "# ---------------------------------------------------------------------------\n# RTL 参数",
    )
    script = f"""proc get_timing_paths {{args}} {{return {{{slacks}}}}}
proc sizeof_collection {{value}} {{return [llength $value]}}
proc foreach_in_collection {{var values body}} {{
    uplevel 1 [list foreach $var $values $body]
}}
proc get_attribute {{value attr}} {{return $value}}
{code}
puts "WNS=[dc_slack max 1]"
puts "TNS=[dc_slack max 2000 sum_negative]"
"""
    cp = tcl(script)
    assert cp.returncode == 0, cp.stdout + cp.stderr
    assert f"WNS={wns}" in cp.stdout
    assert f"TNS={tns}" in cp.stdout
    assert "set tns [dc_slack max $::TNS_NWORST sum_negative]" in FLOW.read_text()


@pytest.mark.parametrize(
    "wns,expected",
    [
        ("-0.1", 3),
        ("-1e-3", 3),
        ("-.001", 3),
        ("-0.0", 0),
        ("1e-3", 0),
        ("NA", 1),
        ("", 1),
        ("NaN", 1),
    ],
)
def test_ok_marker_requires_numeric_nonnegative_wns(tmp_path, wns, expected):
    cp = wrapper_probe(tmp_path, "OK", 0, wns)
    assert cp.returncode == expected, cp.stdout + cp.stderr


@pytest.mark.parametrize("unit,expected", [("1.0", "0.02"), ("0.001", "20.0")])
def test_load_is_converted_from_pf_to_library_units(unit, expected):
    code = block("# ---- 4) 约束", "# ---- 5) compile")
    cp = tcl(f"""proc unknown {{args}} {{return {{}}}}
proc all_outputs {{}} {{return result}}
proc sizeof_collection {{value}} {{return [llength $value]}}
proc set_load {{args}} {{puts "LOAD $args"}}
set CLK_NAME clk
set CLK_PERIOD 10
set DRIVE_CELL buffer
set LOAD_PF 0.02
set LIB_CAP_UNIT_PF {unit}
set MAX_TRANS 1
set IN_DELAY 0
set OUT_DELAY 0
{code}
""")
    assert cp.returncode == 0, cp.stdout + cp.stderr
    assert f"LOAD {expected} result" in cp.stdout
