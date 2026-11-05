"""用本机Python假工具验证返回码及完整标记，不执行FPGA实现。"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest
from _synth_test_support import MARKER, ROOT, arguments, fake_tool, inputs, invoke, manifest, tool


@pytest.mark.parametrize(
    "returncode,status",
    [(0, "EXTERNAL_COMMAND_COMPLETE_NOT_SIGNOFF"), (3, "TIMING_VIOLATION_OBSERVED")],
)
def test_complete_command_is_recorded_without_certifying_timing(tmp_path, returncode, status):
    data = inputs(tmp_path / "inputs")
    executable = fake_tool(tmp_path, returncode=returncode)
    out = tmp_path / "out"
    cp = invoke(data, out, str(executable))
    assert cp.returncode == returncode, cp.stdout + cp.stderr
    record = manifest(out)
    assert record["status"] == status and record["raw_returncode"] == returncode
    assert record["tool_execution_performed"] is True
    assert record["tool_command"] == [str(executable), "-no_gui", "-f", str(out / "run_dc.tcl")]
    assert record["synthesis_evidence"] == "EXTERNAL_COMMAND_RECORD_REQUIRES_TOOL_AUTHENTICATION"
    assert record["sta_evidence"] == "REPORTED_PATHS_ONLY_NOT_TIMING_CLOSURE"
    assert "NOT_AUTHENTICATED" in record["tool_authenticity"]
    assert (out / "tool.log").read_text().strip() == MARKER


@pytest.mark.parametrize("returncode", [1, 9, 124])
def test_completion_marker_cannot_hide_actual_process_failure(tmp_path, returncode):
    data = inputs(tmp_path / "inputs")
    out = tmp_path / "out"
    cp = invoke(data, out, str(fake_tool(tmp_path, returncode=returncode)))
    assert cp.returncode == 1
    record = manifest(out)
    assert record["status"] == "FAILED" and record["raw_returncode"] == returncode
    assert record["synthesis_evidence"] == "NO_COMPLETE_EXTERNAL_COMMAND_RESULT"
    assert "GENERIC_SYNTH_COMPLETE" in (out / "tool.log").read_text()


@pytest.mark.parametrize(
    "output",
    [
        "",
        "tool finished without a marker",
        MARKER + "\n" + MARKER,
        MARKER.replace("sources=2", "sources=0"),
        MARKER.replace("sources=2", "sources=7"),
        MARKER.replace("registers=2", "registers=0"),
        MARKER.replace("clocks=1", "clocks=0"),
        MARKER.replace("reported_paths=2", "reported_paths=0"),
        MARKER + " trailing text",
    ],
)
def test_missing_partial_zero_or_duplicate_completion_is_rejected(tmp_path, output):
    data = inputs(tmp_path / "inputs")
    out = tmp_path / "out"
    cp = invoke(data, out, str(fake_tool(tmp_path, output=output)))
    assert cp.returncode == 1 and manifest(out)["status"] == "FAILED"
    assert manifest(out)["raw_returncode"] == 0


@pytest.mark.parametrize(
    "diagnostic",
    ["Error: bad input", "Error-[SYNTH] bad input", "Fatal: bad tool", "DC_FATAL: bad constraints"],
)
def test_fatal_diagnostic_with_zero_exit_and_marker_is_rejected(tmp_path, diagnostic):
    data = inputs(tmp_path / "inputs")
    out = tmp_path / "out"
    cp = invoke(data, out, str(fake_tool(tmp_path, output=MARKER + "\n" + diagnostic)))
    assert cp.returncode == 1 and "emitted an error" in manifest(out)["error"]


@pytest.mark.parametrize("key", ["source", "header", "library", "sdc", "filelist"])
def test_changed_declared_input_during_execution_cannot_be_promoted(tmp_path, key):
    data = inputs(tmp_path / "inputs")
    before = (
        "from pathlib import Path\nPath("
        + repr(str(data[key]))
        + ").write_bytes(b'changed during fake command')\n"
    )
    executable = fake_tool(tmp_path, before=before)
    out = tmp_path / "out"
    cp = invoke(data, out, str(executable))
    assert cp.returncode == 1 and manifest(out)["status"] == "FAILED"
    assert "declared input changed" in manifest(out)["error"]


def test_missing_explicit_tool_has_no_installation_or_fallback(tmp_path):
    data = inputs(tmp_path / "inputs")
    out = tmp_path / "out"
    cp = invoke(data, out, str(tmp_path / "absent executable"))
    assert cp.returncode == 1
    record = manifest(out)
    assert record["status"] == "FAILED" and record["tool_execution_performed"] is False
    assert "no installation or fallback" in record["error"]
    assert not (out / "tool.log").exists()


def test_timeout_retains_failure_and_never_emits_complete_result(tmp_path):
    data = inputs(tmp_path / "inputs")
    out = tmp_path / "out"
    executable = fake_tool(tmp_path, before="import time\ntime.sleep(3)\n")
    cp = invoke(data, out, str(executable), "--timeout", "1")
    assert cp.returncode == 1
    record = manifest(out)
    assert record["status"] == "FAILED" and record["raw_returncode"] is None
    assert "timed out" in record["error"]
    assert record["sta_evidence"] == "NOT_ESTABLISHED"


@pytest.mark.parametrize("rc", [0, 3, 9])
def test_shell_entry_is_a_local_thin_driver_and_propagates_result(tmp_path, rc):
    data = inputs(tmp_path / "inputs")
    executable = fake_tool(tmp_path, returncode=rc)
    out = tmp_path / "results"
    cp = subprocess.run(
        [tool("bash"), str(ROOT / "synth/run_synth.sh"), *arguments(data, out, str(executable))],
        cwd=tmp_path,
        env={**os.environ, "PYTHON": sys.executable, "PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert cp.returncode == (rc if rc in (0, 3) else 1), cp.stdout + cp.stderr
    assert manifest(out)["raw_returncode"] == rc
    assert manifest(out)["config"]["sources"] == [str(data["source"]), str(data["leaf"])]
