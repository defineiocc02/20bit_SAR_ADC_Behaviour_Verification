"""Mock/static checks only: these tests do not run Vivado or validate FPGA behavior."""

from __future__ import annotations

import csv
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "full_mapped_flow", ROOT / "synth/run_vivado_full_mapped.py"
)
FLOW = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FLOW)


def origin(tmp_path, stages=7):
    dcp = tmp_path / "source.dcp"
    dcp.write_bytes(b"MOCK DCP ONLY")
    manifest = {
        "status": "SYNTH_COMPLETE_TIMING_NOT_MET",
        "arguments": {"stages": stages, "part": "fixture_part"},
        "vivado_status": {
            "STATUS": "SYNTH_COMPLETE",
            "P_RECON_STAGES": str(stages),
            "PART": "fixture_part",
            "WNS_NS": "-1",
            "TIMING_MET": "0",
            "SCOPE": "FPGA_POST_SYNTH_OOC_VECTORLESS_NOT_ASIC",
        },
        "remote_dcp_identity": {"sha256": FLOW.sha256(dcp)},
    }
    return manifest, dcp


def trace_fixture(tmp_path, stages=7):
    ids = [i for i in range(2, 159) if (i - 1) % 37 not in (11, 12) and i % 41 != 13]
    rows = [
        [m, i, (m + 1) * 10000 + i * 16, i, i * 19, i * 19, 0, 0, 0] for m in range(3) for i in ids
    ]
    path = tmp_path / "trace.csv"
    with path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(FLOW.COLUMNS)
        writer.writerows(rows)
    lines = [
        f"MAPPED_SCOPE functional_only public_ports_only recon_stages={stages} latency_checked=0 reset_release_ns=220"
    ]
    for mode in range(3):
        lines.extend("MAPPED_ROW," + ",".join(map(str, row)) for row in rows if row[0] == mode)
        lines.append(f"MAPPED_CANCEL,mode={mode},sample_id=159,frame=159")
    lines.append(
        f"STRUCTURAL_MAPPED_COMPLETE modes=3 outputs=438 checks=7680 physical_slices=18 recon_stages={stages} config_reads=3900 cancelled=3 expected=441 latency_checked=0"
    )
    log = tmp_path / "xsim.log"
    log.write_text("\n".join(lines) + "\n")
    return path, log


@pytest.mark.parametrize("stages", [5, 7])
def test_origin_and_trace_positive_fixture(tmp_path, stages):
    manifest, dcp = origin(tmp_path, stages)
    assert FLOW.validate_origin(manifest, dcp, stages, None)["dcp_sha256"] == FLOW.sha256(dcp)
    trace, log = trace_fixture(tmp_path, stages)
    assert FLOW.audit_trace(trace, log, stages)["rows"] == 438


@pytest.mark.parametrize(
    "mutation", ["stages", "status", "undriven", "part", "hash", "nan", "timing", "summary_status"]
)
def test_rejects_invalid_origin(tmp_path, mutation):
    manifest, dcp = origin(tmp_path)
    if mutation == "stages":
        manifest["arguments"]["stages"] = 5
    elif mutation == "status":
        manifest["status"] = "FAILED"
    elif mutation == "undriven":
        manifest["undriven_diagnostics"] = ["Synth 8-3848"]
    elif mutation == "part":
        manifest["vivado_status"]["PART"] = "other_part"
    elif mutation == "hash":
        dcp.write_bytes(b"wrong checkpoint")
    elif mutation == "nan":
        manifest["vivado_status"]["WNS_NS"] = "nan"
    elif mutation == "timing":
        manifest["vivado_status"]["TIMING_MET"] = "1"
    elif mutation == "summary_status":
        manifest["status"] = "SYNTH_COMPLETE_TIMING_MET"
    with pytest.raises(ValueError):
        FLOW.validate_origin(manifest, dcp, 7, None)


def test_explicit_dcp_identity_is_required_when_legacy_manifest_has_no_hash(tmp_path):
    manifest, dcp = origin(tmp_path)
    del manifest["remote_dcp_identity"]
    with pytest.raises(ValueError, match="identity"):
        FLOW.validate_origin(manifest, dcp, 7, None)
    assert (
        FLOW.validate_origin(manifest, dcp, 7, FLOW.sha256(dcp))["binding"]
        == "explicit_caller_expected_sha256"
    )


def test_explicit_hash_cannot_contradict_recorded_hash(tmp_path):
    manifest, dcp = origin(tmp_path)
    with pytest.raises(ValueError, match="disagrees"):
        FLOW.validate_origin(manifest, dcp, 7, "0" * 64)


@pytest.mark.parametrize(
    "mutation",
    [
        "truncated",
        "schema",
        "x_value",
        "code",
        "id",
        "row_log",
        "fatal",
        "missing_marker",
        "duplicate_marker",
        "wrong_stage",
        "cancel",
    ],
)
def test_trace_rejects_failures_even_with_a_pass_marker(tmp_path, mutation):
    trace, log = trace_fixture(tmp_path)
    lines = trace.read_text().splitlines()
    text = log.read_text()
    if mutation == "truncated":
        lines.pop()
    elif mutation == "schema":
        lines[0] = lines[0].replace("mode", "unexpected")
    elif mutation in {"x_value", "code", "id"}:
        columns = lines[1].split(",")
        columns[{"x_value": 4, "code": 4, "id": 3}[mutation]] = (
            "x" if mutation == "x_value" else "999"
        )
        lines[1] = ",".join(columns)
    elif mutation == "row_log":
        text = text.replace("MAPPED_ROW,0,2,", "MAPPED_ROW,0,3,", 1)
    elif mutation == "fatal":
        text += "Fatal: mocked failure after marker\n"
    elif mutation == "missing_marker":
        text = text.replace("STRUCTURAL_MAPPED_COMPLETE", "INCOMPLETE")
    elif mutation == "duplicate_marker":
        text += text.splitlines()[-1] + "\n"
    elif mutation == "wrong_stage":
        text = text.replace("recon_stages=7", "recon_stages=5")
    elif mutation == "cancel":
        text = text.replace("sample_id=159", "sample_id=3", 1)
    trace.write_text("\n".join(lines) + "\n")
    log.write_text(text)
    with pytest.raises(ValueError):
        FLOW.audit_trace(trace, log, 7)


def test_windows_literal_batch_preserves_spaces_and_disables_delayed_expansion():
    batch = FLOW.windows_batch(
        [r"D:\Vivado 2018\bin\xsim.bat", "snapshot", "mapped_trace=trace.csv"]
    )
    assert 'call "D:\\Vivado 2018\\bin\\xsim.bat" "snapshot"' in batch
    assert "DisableDelayedExpansion" in batch
    assert "exit /b %ERRORLEVEL%" in batch


@pytest.mark.parametrize("arg", ['a"b', "a%b", "a\nb", "a\x00b"])
def test_windows_batch_rejects_ambiguous_arguments(arg):
    with pytest.raises(ValueError):
        FLOW.windows_batch(["vivado.bat", arg])


def test_prepare_only_never_starts_tools_and_preserves_input(tmp_path, monkeypatch):
    manifest, dcp = origin(tmp_path, 5)
    path = tmp_path / "synth.json"
    path.write_text(json.dumps(manifest))
    out = tmp_path / "new run"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "flow",
            "--dcp",
            str(dcp),
            "--synthesis-manifest",
            str(path),
            "--stages",
            "5",
            "--vivado",
            str(tmp_path / "bin/vivado"),
            "--out",
            str(out),
        ],
    )
    monkeypatch.setattr(FLOW, "run_step", lambda *args: pytest.fail("prepare-only started EDA"))
    assert FLOW.main() == 0
    result = json.loads((out / "manifest.json").read_text())
    assert result["status"] == "PREPARED"
    assert result["steps"] == []
    assert result["commands"][2][1][-2:] == ["-generic_top", "P_RECON_STAGES=5"]
    assert result["input_sha256"]["post_synth.dcp"] == FLOW.sha256(dcp)
    with pytest.raises(FileExistsError):
        FLOW.main()


HARNESS = r"""
proc set_param {args} {}
proc open_checkpoint {args} {if {$::env(FAIL) eq "open"} {error "injected open failure"}}
proc current_design {} {return sar20_digital_core}
proc get_property {property object} {
  if {$property eq "PART"} {return [expr {$::env(FAIL) eq "part" ? "wrong_part" : "fixture_part"}]}
  return [expr {$::env(FAIL) eq "top" ? "other_top" : "sar20_digital_core"}]
}
proc get_cells {args} {if {$::env(FAIL) eq "blackbox"} {return cell}; return {}}
proc get_ports {args} {if {$::env(FAIL) eq "port"} {return {}}; return [lindex $args end]}
proc write_verilog {args} {
  if {$::env(FAIL) eq "export"} {error "injected export failure"}
  if {[lrange $args 0 1] ne {-mode funcsim}} {error "wrong export mode"}
  set fh [open [lindex $args end] w]; puts $fh "MOCK NETLIST ONLY"; close $fh
}
proc version {args} {return mock-2018.3}
source $::env(FLOW_TCL)
"""


@pytest.mark.parametrize("failure", ["", "open", "part", "top", "blackbox", "port", "export"])
def test_tcl_export_decisions_only(tmp_path, failure):
    tclsh = shutil.which("tclsh")
    if not tclsh:
        pytest.skip("Tcl interpreter is required for mocked control-flow checks")
    dcp = tmp_path / "source.dcp"
    dcp.write_bytes(b"MOCK DCP")
    harness = tmp_path / "harness.tcl"
    harness.write_text(HARNESS)
    out = tmp_path / "new export"
    result = subprocess.run(
        [tclsh, str(harness), str(dcp), str(out), "fixture_part", "7"],
        env={
            **os.environ,
            "FAIL": failure,
            "FLOW_TCL": str(ROOT / "synth/run_vivado_full_mapped.tcl"),
        },
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert (result.returncode == 0) == (not failure), result.stdout + result.stderr
    assert (out / "status.txt").exists() == (not failure)
    if not failure:
        assert (
            "SCOPE=FULL_TOP_POST_SYNTH_FUNCTIONAL_NO_SDF_NOT_TIMING"
            in (out / "status.txt").read_text()
        )
    assert dcp.read_bytes() == b"MOCK DCP"


@pytest.mark.parametrize("failure", ["", "export", "xvlog", "xelab", "xsim", "error_with_zero"])
def test_mocked_pipeline_never_promotes_failed_tool(tmp_path, monkeypatch, failure):
    manifest, dcp = origin(tmp_path)
    path = tmp_path / "synth.json"
    path.write_text(json.dumps(manifest))
    out = tmp_path / "new run"
    install = tmp_path / "install"
    (install / "bin").mkdir(parents=True)
    (install / "data/verilog/src").mkdir(parents=True)
    for name in ("vivado", "xvlog", "xelab", "xsim"):
        (install / "bin" / name).write_text("MOCK TOOL; not executed")
    (install / "data/verilog/src/glbl.v").write_text("MOCK GLBL")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "flow",
            "--dcp",
            str(dcp),
            "--synthesis-manifest",
            str(path),
            "--stages",
            "7",
            "--vivado",
            str(install / "bin/vivado"),
            "--out",
            str(out),
            "--execute",
        ],
    )

    def fake_step(name, argv, directory, timeout):
        (directory / f"{name}.console.log").write_text(
            "ERROR: injected wrapper-zero failure" if failure == "error_with_zero" else "MOCK ONLY"
        )
        if name == failure:
            return {"step": name, "raw_returncode": 9}
        if name == "export":
            export = directory / "export"
            export.mkdir()
            (export / "full_top_funcsim.v").write_text("MOCK NETLIST")
            (export / "status.txt").write_text(
                "STATUS=FULL_MAPPED_EXPORT_COMPLETE\nTOP=sar20_digital_core\nPART=fixture_part\nP_RECON_STAGES=7\nSCOPE=FULL_TOP_POST_SYNTH_FUNCTIONAL_NO_SDF_NOT_TIMING\nVIVADO_VERSION=mock-only\n"
            )
        if name == "xsim":
            trace_fixture(directory)
        return {"step": name, "raw_returncode": 0}

    monkeypatch.setattr(FLOW, "run_step", fake_step)
    result = FLOW.main()
    record = json.loads((out / "manifest.json").read_text())
    assert (result == 0) == (not failure)
    assert record["status"] == ("FAILED" if failure else "FULL_MAPPED_FUNCTIONAL_PASS")
    if failure in {"export", "xvlog", "xelab", "xsim"}:
        assert record["steps"][-1]["raw_returncode"] == 9
    assert manifest == json.loads(path.read_text())
