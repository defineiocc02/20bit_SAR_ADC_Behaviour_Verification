"""验证当前输入准备和Tcl文本检查；旧固定模式mapped流程不再是运行入口。"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest
from _synth_test_support import DRIVER, ROOT, arguments, digest, inputs, invoke, manifest, tool


@pytest.mark.parametrize(
    "option",
    [
        "--tool",
        "--top",
        "--filelist",
        "--library",
        "--sdc",
        "--clock",
        "--compile-command",
        "--out",
    ],
)
def test_every_external_input_is_required_instead_of_using_old_profiles(tmp_path, option):
    data = inputs(tmp_path / "inputs")
    out = tmp_path / "out"
    argv = arguments(data, out, "not_started", "--prepare-only")
    index = argv.index(option)
    del argv[index : index + 2]
    cp = subprocess.run(
        [sys.executable, str(ROOT / "synth/run_synth.py"), *argv],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert cp.returncode == 2 and option in cp.stderr
    assert not out.exists()


@pytest.mark.parametrize(
    "extra",
    [
        ["--host", "unused_host"],
        ["--remote-os", "windows"],
        ["--part", "old_part"],
        ["--stages", "7"],
        ["--execute"],
        ["--in-delay", "0"],
    ],
)
def test_retired_remote_and_device_shortcuts_are_not_silently_accepted(tmp_path, extra):
    data = inputs(tmp_path / "inputs")
    out = tmp_path / "out"
    cp = invoke(data, out, "not_started", "--prepare-only", *extra)
    assert cp.returncode == 2 and "unrecognized arguments" in cp.stderr
    assert not out.exists()


def test_generic_parameter_probe_uses_declared_source_and_parameter_hashes(tmp_path):
    data = inputs(tmp_path / "inputs")
    source = ROOT / "synth/tests/param_probe.sv"
    data["filelist"].write_text(
        json.dumps({"sources": [str(source)], "include_dirs": [], "headers": [], "defines": []})
    )
    out = tmp_path / "prepared"
    cp = invoke(data, out, "not_started", "--prepare-only", "--parameters", "P_WIDTH=13")
    assert cp.returncode == 0, cp.stdout + cp.stderr
    record = manifest(out)
    assert record["config"]["sources"] == [str(source)]
    assert record["config"]["parameters"] == "P_WIDTH=13"
    assert record["config"]["include_dirs"] == record["config"]["defines"] == []
    assert record["declared_input_sha256"][str(source)] == digest(source)
    assert record["synthesis_evidence"] == record["sta_evidence"] == "NOT_RUN"


def syntax(paths, *, tclsh=None):
    return subprocess.run(
        [tool("bash"), str(ROOT / "synth/check_syntax.sh"), *map(str, paths)],
        env={**os.environ, "TCLSH": tclsh or tool("tclsh")},
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )


def test_current_tcl_scripts_are_compiled_as_text_without_vendor_execution():
    paths = [
        ROOT / "synth" / name
        for name in ["run_dc.tcl", "inspect_clockless_paths.tcl", "run_vivado_buffered_impl.tcl"]
    ]
    cp = syntax(paths)
    assert cp.returncode == 0, cp.stdout + cp.stderr
    assert (
        "TCL_SCRIPT_PARSE_COMPLETE files=3 vendor_commands_executed=0 semantic_API_validation=NOT_ASSESSED"
        in cp.stdout
    )


def test_syntax_checker_does_not_execute_valid_but_dangerous_tcl(tmp_path):
    text = "error {this command must never execute}\nset x [missing_vendor_command]\n"
    path = tmp_path / "nonexecuted.tcl"
    path.write_text(text)
    cp = syntax([path])
    assert cp.returncode == 0 and "files=1" in cp.stdout
    assert path.read_text() == text


@pytest.mark.parametrize("kind", ["missing", "incomplete", "no_inputs", "no_interpreter"])
def test_tcl_text_check_failure_is_not_a_successful_eda_record(tmp_path, kind):
    path = tmp_path / "input.tcl"
    if kind != "missing":
        path.write_text("if {1} {\n" if kind == "incomplete" else "set x 1\n")
    cp = syntax(
        [] if kind == "no_inputs" else [path],
        tclsh=str(tmp_path / "missing_tclsh") if kind == "no_interpreter" else None,
    )
    assert cp.returncode != 0
    assert "TCL_SCRIPT_PARSE_COMPLETE" not in cp.stdout


def test_output_aliases_cannot_change_or_create_an_existing_named_path(tmp_path):
    for kind in ["file", "directory", "dangling_symlink", "valid_symlink", "dangling_chain"]:
        root = tmp_path / kind
        root.mkdir()
        output = root / "existing output"
        target = root / "not_created"
        if kind == "file":
            output.write_bytes(b"preserve file")
        elif kind == "directory":
            output.mkdir()
        elif kind == "valid_symlink":
            target.mkdir()
            (target / "sentinel").write_text("preserve")
            output.symlink_to(target, target_is_directory=True)
        elif kind == "dangling_chain":
            link = root / "second_alias"
            link.symlink_to(target, target_is_directory=True)
            output.symlink_to(link, target_is_directory=True)
        else:
            output.symlink_to(target, target_is_directory=True)
        with pytest.raises(ValueError, match="output|Output"):
            DRIVER.new_output(output)
        if kind == "valid_symlink":
            assert (target / "sentinel").read_text() == "preserve"
        else:
            assert not target.exists()
        if kind == "file":
            assert output.read_bytes() == b"preserve file"
        if "symlink" in kind or kind == "dangling_chain":
            assert output.is_symlink()


def test_fresh_output_directory_is_created_once(tmp_path):
    output = tmp_path / "new output 中文"
    assert DRIVER.new_output(output) == output.resolve()
    assert output.is_dir() and list(output.iterdir()) == []
    with pytest.raises(ValueError, match="new directory"):
        DRIVER.new_output(output)


def test_unlisted_sources_are_neither_discovered_nor_represented_as_hashed_inputs(tmp_path):
    data = inputs(tmp_path / "inputs")
    extra = data["source"].parent / "unlisted.sv"
    extra.write_text("not a valid source; caller did not declare it\n")
    out = tmp_path / "out"
    cp = invoke(data, out, "not_started", "--prepare-only")
    assert cp.returncode == 0, cp.stdout + cp.stderr
    record = manifest(out)
    assert str(extra) not in record["declared_input_sha256"]
    assert str(extra) not in record["config"]["sources"]
    assert len(record["config"]["sources"]) == 2
    assert record["tool_execution_performed"] is False
