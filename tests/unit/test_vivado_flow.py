"""Exercise Vivado flow control with subprocess/Tcl stubs, never claim EDA validation."""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import shlex
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
FLOW = REPO / "synth/run_vivado_ooc.tcl"


@pytest.fixture
def wrapper(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "vivado_test_flow", REPO / "synth/run_vivado_ssh.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    root = tmp_path / "snapshot"
    (root / "rtl/params").mkdir(parents=True)
    (root / "synth").mkdir()
    (root / "rtl/top.sv").write_text('`include "rtl_error_codes.vh"\nmodule top; endmodule\n')
    (root / "rtl/rtl_sources.f").write_text("rtl/top.sv\n")
    (root / "rtl/params/rtl_params.vh").write_text("// parameter fixture\n")
    (root / "rtl/params/rtl_error_codes.vh").write_text("// error fixture\n")
    shutil.copyfile(FLOW, root / "synth/run_vivado_ooc.tcl")
    monkeypatch.setattr(module, "ROOT", root)
    monkeypatch.setattr(sys, "argv", ["flow", "--part", "fixture_part", "--stages", "5"])
    return module, root


def artifacts(root):
    (manifest_path,) = (root / "synth/artifacts/vivado").glob("*/manifest.json")
    return manifest_path.parent, json.loads(manifest_path.read_text())


def status_text(**changes):
    fields = {
        "STATUS": "SYNTH_COMPLETE",
        "TIMING_MET": "1",
        "WNS_NS": "0.125",
        "PART": "fixture_part",
        "PERIOD_NS": "1.5625",
        "P_RECON_STAGES": "5",
        "VIVADO_VERSION": "stub-only",
        "SCOPE": "FPGA_POST_SYNTH_OOC_VECTORLESS_NOT_ASIC",
    }
    fields.update(changes)
    return "".join(f"{key}={value}\n" for key, value in fields.items())


def mock_transport(monkeypatch, module, status, rc=0, fail_mkdir=False):
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        script = ""
        if argv[0] == "ssh" and "-EncodedCommand " in argv[-1]:
            script = decode_powershell(argv[-1])
        if argv[0] == "ssh" and ("mktemp" in argv[-1] or "[Guid]::NewGuid" in script):
            if fail_mkdir:
                raise subprocess.TimeoutExpired(argv, 20)
            remote = (
                "C:/Users/Test User/adc_rtl_vivado/run." + "a" * 32
                if script
                else "adc_rtl_vivado/run.TEST1234"
            )
            return subprocess.CompletedProcess(argv, 0, remote + "\n", "")
        if argv[0] == "ssh":
            kwargs["stdout"].write("stubbed transport; no EDA execution\n")
            return subprocess.CompletedProcess(argv, rc)
        if "-r" in argv and argv[-2].endswith("/out"):
            target = Path(argv[-1]) / "out"
            target.mkdir()
            if status is not None:
                (target / "status.txt").write_text(status)
        elif "-r" in argv and argv[-2].endswith("/vivado.log"):
            (Path(argv[-1]) / "vivado.log").write_text("stub-only\n")
        elif "-r" in argv and argv[-2].endswith("/vivado_exit_code.txt"):
            (Path(argv[-1]) / "vivado_exit_code.txt").write_text(str(rc))
        elif "-r" in argv and argv[-2].endswith(("/console.log", "/console.stderr.log")):
            (Path(argv[-1]) / argv[-2].rsplit("/", 1)[1]).write_text("stub-only\n")
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(module.subprocess, "run", run)
    return calls


def decode_powershell(command):
    return base64.b64decode(command.split("-EncodedCommand ", 1)[1]).decode("utf-16le")


def test_prepare_is_offline_and_archives_headers_with_exact_hashes(wrapper, monkeypatch):
    module, root = wrapper
    monkeypatch.setattr(sys, "argv", [*sys.argv, "--prepare-only"])
    calls = mock_transport(monkeypatch, module, None)
    assert module.main() == 0
    assert not calls
    out, manifest = artifacts(root)
    assert manifest["status"] == "PREPARED"
    with tarfile.open(out / "source.tar.gz") as archive:
        assert "rtl/params/rtl_error_codes.vh" in archive.getnames()
        for name, digest in manifest["sources_sha256"].items():
            assert hashlib.sha256(archive.extractfile(name).read()).hexdigest() == digest


@pytest.mark.parametrize("wns,rc,timing", [("0.125", 0, "MET"), ("-0.125", 3, "NOT_MET")])
def test_consistent_result_preserves_timing_outcome(wrapper, monkeypatch, wns, rc, timing):
    module, root = wrapper
    mock_transport(monkeypatch, module, status_text(WNS_NS=wns, TIMING_MET=str(int(rc == 0))), rc)
    assert module.main() == rc
    _, manifest = artifacts(root)
    assert manifest["status"] == f"SYNTH_COMPLETE_TIMING_{timing}"
    assert manifest["vivado_status"]["P_RECON_STAGES"] == "5"


@pytest.mark.parametrize(
    "status,rc",
    [
        (None, 0),
        ("STATUS=SYNTH_COMPLETE\n", 0),
        (status_text(WNS_NS="-0.1", TIMING_MET="0"), 0),
        (status_text(), 9),
        (status_text(WNS_NS="NaN"), 0),
        (status_text(PART="other_part"), 0),
        (status_text(P_RECON_STAGES="7"), 0),
        (status_text(PERIOD_NS="10"), 0),
        (status_text() + "STATUS=SYNTH_COMPLETE\n", 0),
    ],
)
def test_missing_or_conflicting_status_never_becomes_success(wrapper, monkeypatch, status, rc):
    module, root = wrapper
    mock_transport(monkeypatch, module, status, rc)
    assert module.main() == 1
    _, manifest = artifacts(root)
    assert manifest["status"] == "FAILED"


def test_connection_timeout_is_recorded_without_launching_tool(wrapper, monkeypatch):
    module, root = wrapper
    calls = mock_transport(monkeypatch, module, None, fail_mkdir=True)
    assert module.main() == 1
    assert len(calls) == 1
    _, manifest = artifacts(root)
    assert manifest["status"] == "FAILED"
    assert "timed out" in manifest["error"]


@pytest.mark.parametrize("host", ["-oProxyCommand=bad", "user@host extra"])
def test_host_cannot_be_an_ssh_option_or_multiple_arguments(wrapper, monkeypatch, host):
    module, _ = wrapper
    monkeypatch.setattr(sys, "argv", [*sys.argv, f"--host={host}"])
    calls = mock_transport(monkeypatch, module, None)
    with pytest.raises(SystemExit) as exc:
        module.main()
    assert exc.value.code == 2
    assert not calls


def test_remote_shell_roundtrip_preserves_literal_paths_and_tool_arguments(wrapper, monkeypatch):
    module, _ = wrapper
    settings = "/EDA path/settings;$(touch UNEXPECTED).sh"
    executable = "/EDA path/vivado'$(touch UNEXPECTED)"
    monkeypatch.setattr(sys, "argv", [*sys.argv, "--settings", settings, "--vivado", executable])
    calls = mock_transport(monkeypatch, module, status_text())
    assert module.main() == 0
    (launch,) = (call for call in calls if call[0] == "ssh" and "bash -lc" in call[-1])
    outer = shlex.split(launch[-1])
    assert outer[:2] == ["bash", "-lc"]
    tokens = shlex.split(outer[2])
    assert tokens[tokens.index("source") + 1] == settings
    assert executable in tokens
    assert tokens[-5:] == ["-tclargs", "fixture_part", "out", "1.5625", "5"]
    assert "--kill-after=30s" in tokens


@pytest.mark.parametrize("rc,wns", [(0, "0.125"), (3, "-0.125")])
def test_windows_preserves_parameters_logs_and_timing_outcome(wrapper, monkeypatch, rc, wns):
    module, root = wrapper
    executable = r"D:\EDA path\O'Brien $(literal)\Vivado\bin\vivado.bat"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            *sys.argv,
            "--remote-os",
            "windows",
            "--host",
            "windows-codex",
            "--vivado",
            executable,
            "--timeout",
            "45",
        ],
    )
    calls = mock_transport(
        monkeypatch, module, status_text(WNS_NS=wns, TIMING_MET=str(int(rc == 0))), rc
    )
    assert module.main() == rc
    out, manifest = artifacts(root)
    assert manifest["status"] == (
        "SYNTH_COMPLETE_TIMING_MET" if rc == 0 else "SYNTH_COMPLETE_TIMING_NOT_MET"
    )
    (mkdir, launch) = (decode_powershell(call[-1]) for call in calls if call[0] == "ssh")
    assert "$env:USERPROFILE" in mkdir and "[Guid]::NewGuid().ToString('N')" in mkdir
    assert "& tar.exe -xzf source.tar.gz" in launch
    assert "WaitForExit(45000)" in launch and "WaitForExit(30000)" in launch
    assert "taskkill.exe /PID $process.Id /T /F" in launch
    assert "exit 124" in launch
    assert "'/d /v:off /c run_vivado.cmd'" in launch
    assert "exit $code" in launch
    assert (out / "remote_run.ps1").read_text() == launch
    batch = (out / "run_vivado.cmd").read_text()
    assert f'call "{executable}"' in batch
    assert '-tclargs "fixture_part" out 1.5625 5' in batch
    assert "DisableDelayedExpansion" in batch and "exit /b %ERRORLEVEL%" in batch
    assert all("-s" in call for call in calls if call[0] == "scp")
    assert manifest["remote_directory"].startswith("C:/Users/Test User/")
    assert (out / "vivado_exit_code.txt").read_text() == str(rc)


@pytest.mark.parametrize(
    "rc,status", [(124, None), (1, status_text()), (0, None), (0, status_text(P_RECON_STAGES="7"))]
)
def test_windows_failure_or_conflicting_status_cannot_pass(wrapper, monkeypatch, rc, status):
    module, root = wrapper
    monkeypatch.setattr(sys, "argv", [*sys.argv, "--remote-os", "windows"])
    mock_transport(monkeypatch, module, status, rc)
    assert module.main() == 1
    _, manifest = artifacts(root)
    assert manifest["status"] == "FAILED"
    assert manifest["exit_code"] == rc


@pytest.mark.parametrize(
    "extra",
    [
        ["--settings", "settings64.sh"],
        ["--vivado", r"D:\%BAD%\vivado.bat"],
        ["--vivado", 'vivado.bat" & echo injected'],
        ["--part", "part & echo injected"],
    ],
)
def test_windows_rejects_batch_expansion_and_linux_setup(wrapper, monkeypatch, extra):
    module, _ = wrapper
    monkeypatch.setattr(sys, "argv", [*sys.argv, "--remote-os", "windows", *extra])
    calls = mock_transport(monkeypatch, module, None)
    with pytest.raises(SystemExit) as exc:
        module.main()
    assert exc.value.code == 2
    assert not calls


def test_windows_saved_exit_code_must_match_ssh(wrapper, monkeypatch):
    module, root = wrapper
    monkeypatch.setattr(sys, "argv", [*sys.argv, "--remote-os", "windows"])
    mock_transport(monkeypatch, module, status_text())
    original_run = module.subprocess.run

    def inconsistent_rc(argv, **kwargs):
        result = original_run(argv, **kwargs)
        if argv[0] == "scp" and argv[-2].endswith("/vivado_exit_code.txt"):
            (Path(argv[-1]) / "vivado_exit_code.txt").write_text("9")
        return result

    monkeypatch.setattr(module.subprocess, "run", inconsistent_rc)
    assert module.main() == 1
    _, manifest = artifacts(root)
    assert "exit code differs" in manifest["error"]


def test_windows_powershell_sets_utf8_and_suppresses_progress(wrapper):
    module, _ = wrapper
    script = decode_powershell(module.powershell_command("Write-Output 'test'"))
    assert script.startswith("$ProgressPreference = 'SilentlyContinue'")
    assert "[Console]::OutputEncoding = $utf8" in script
    assert "$OutputEncoding = $utf8" in script
    assert "[System.Text.UTF8Encoding]::new($false)" in script


def test_windows_mkdir_preserves_non_utf8_diagnostics(wrapper, monkeypatch):
    module, root = wrapper
    monkeypatch.setattr(sys, "argv", [*sys.argv, "--remote-os", "windows"])
    mock_transport(monkeypatch, module, status_text())
    original_run = module.subprocess.run

    def cp936_stderr(argv, **kwargs):
        result = original_run(argv, **kwargs)
        if argv[0] == "ssh" and "[Guid]::NewGuid" in decode_powershell(argv[-1]):
            assert kwargs["encoding"] == "utf-8"
            assert kwargs["errors"] == "backslashreplace"
            result.stderr = b"progress: \xd5\xfd\xd4\xda".decode(
                kwargs["encoding"], kwargs["errors"]
            )
        return result

    monkeypatch.setattr(module.subprocess, "run", cp936_stderr)
    assert module.main() == 0
    out, manifest = artifacts(root)
    assert manifest["status"] == "SYNTH_COMPLETE_TIMING_MET"
    assert r"\xd5\xfd\xd4\xda" in (out / "mkdir.stderr.log").read_text()


def test_unicode_decode_error_is_recorded_as_failed(wrapper, monkeypatch):
    module, root = wrapper
    monkeypatch.setattr(sys, "argv", [*sys.argv, "--remote-os", "windows"])

    def bad_decode(*args, **kwargs):
        raise UnicodeDecodeError("utf-8", b"\xd5", 0, 1, "injected diagnostic decoding failure")

    monkeypatch.setattr(module.subprocess, "run", bad_decode)
    assert module.main() == 1
    _, manifest = artifacts(root)
    assert manifest["status"] == "FAILED"
    assert "injected diagnostic decoding failure" in manifest["error"]


def tcl_probe(tmp_path, stages=5, slack="0.125", failure=""):
    tclsh = shutil.which("tclsh")
    if tclsh is None:
        pytest.skip("tclsh is needed to exercise flow control, not Vivado semantics")
    out = tmp_path / "out"
    harness = tmp_path / "probe.tcl"
    # Only documented Vivado entry points are stubbed. In particular, do not
    # invent Synopsys remove_from_collection to hide a broken Vivado XDC.
    harness.write_text(
        f"""
set argc 4
set argv [list fixture_part {{{out}}} 1.5625 {stages}]
set ::test_slack {{{slack}}}
set ::failure {{{failure}}}
proc get_parts {{args}} {{return fixture_part}}
proc create_project {{args}} {{}}
proc set_param {{args}} {{}}
proc set_property {{args}} {{}}
proc current_fileset {{}} {{return sources}}
proc read_verilog {{args}} {{}}
proc get_ports {{args}} {{
    if {{[lindex $args 0] eq "-filter"}} {{return {{rst_n data}}}}
    return clk
}}
proc get_clocks {{args}} {{return core_clk}}
proc all_inputs {{}} {{return {{clk rst_n data}}}}
proc all_outputs {{}} {{return result}}
proc create_clock {{args}} {{puts "CLOCK $args"}}
proc set_clock_uncertainty {{args}} {{}}
proc set_input_delay {{args}} {{puts "INPUT $args"}}
proc set_output_delay {{args}} {{puts "OUTPUT $args"}}
proc read_xdc {{path}} {{source $path; set ::constraints_seen 1}}
proc synth_design {{args}} {{
    if {{![info exists ::constraints_seen]}} {{error "constraints missing before synthesis"}}
    if {{$::failure eq "synthesis"}} {{error "injected synthesis failure"}}
    puts "SYNTH $args"
}}
proc get_cells {{args}} {{
    if {{$::failure eq "blackbox"}} {{return unresolved}}
    return {{}}
}}
foreach command {{report_utilization report_timing_summary report_timing check_timing report_drc report_power write_checkpoint}} {{
    proc $command {{args}} {{}}
}}
proc get_timing_paths {{args}} {{
    if {{$::failure eq "no_path"}} {{return {{}}}}
    return worst_path
}}
proc get_property {{args}} {{return $::test_slack}}
proc version {{args}} {{return stub-only}}
source {{{FLOW}}}
""",
        encoding="utf-8",
    )
    cp = subprocess.run([tclsh, str(harness)], text=True, capture_output=True, timeout=10)
    return cp, out


@pytest.mark.parametrize("stages", [5, 6, 7])
def test_tcl_applies_requested_parameter_and_explicit_io_constraints(tmp_path, stages):
    cp, out = tcl_probe(tmp_path, stages)
    assert cp.returncode == 0, cp.stdout + cp.stderr
    assert f"-generic P_RECON_STAGES={stages}" in cp.stdout
    assert "CLOCK -name core_clk -period 1.5625 clk" in cp.stdout
    assert "INPUT 0.0 -clock core_clk {rst_n data}" in cp.stdout
    assert "OUTPUT 0.0 -clock core_clk result" in cp.stdout
    assert "STATUS=SYNTH_COMPLETE" in (out / "status.txt").read_text()


@pytest.mark.parametrize("failure", ["synthesis", "blackbox", "no_path"])
def test_tcl_failure_cannot_emit_completion(tmp_path, failure):
    cp, out = tcl_probe(tmp_path, failure=failure)
    assert cp.returncode == 1
    assert not (out / "status.txt").exists()
    assert (out / "failure.txt").is_file()


def test_tcl_negative_slack_is_distinct_from_flow_failure(tmp_path):
    cp, out = tcl_probe(tmp_path, slack="-0.125")
    assert cp.returncode == 3, cp.stdout + cp.stderr
    assert "TIMING_MET=0" in (out / "status.txt").read_text()
