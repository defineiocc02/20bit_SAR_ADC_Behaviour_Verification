"""检查显式导出的原子写入及输入、输出身份保护。"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from _rtl_test_support import (
    BUNDLE_FILES,
    blob,
    copy_inputs,
    input_args,
    invoke,
    load_tool,
    snapshot_inputs,
    write_frozen,
)


@pytest.fixture
def inputs(tmp_path):
    return copy_inputs(tmp_path)


@pytest.fixture
def exporter(monkeypatch):
    return load_tool("export_rtl_params", monkeypatch)


def test_parameter_export_writes_only_the_complete_bundle(exporter, inputs, tmp_path, monkeypatch):
    out = tmp_path / "new_bundle"
    expected = exporter.build(inputs.config, inputs.format, inputs.binding)
    before = snapshot_inputs(inputs)
    assert invoke(exporter, [*input_args(inputs), "--out", out], monkeypatch) == 0
    assert {p.name for p in out.iterdir()} == BUNDLE_FILES
    assert {p.name: p.read_bytes() for p in out.iterdir()} == {
        name: blob(content) for name, content in expected.items()
    }
    assert snapshot_inputs(inputs) == before


def test_parameter_export_never_overwrites_an_existing_directory(
    exporter, inputs, tmp_path, monkeypatch
):
    out = tmp_path / "existing"
    out.mkdir()
    marker = out / "user_result.txt"
    marker.write_bytes(b"existing result")
    before = snapshot_inputs(inputs)
    with pytest.raises((OSError, ValueError, SystemExit)):
        invoke(exporter, [*input_args(inputs), "--out", out], monkeypatch)
    assert {p.name: p.read_bytes() for p in out.iterdir()} == {marker.name: b"existing result"}
    assert snapshot_inputs(inputs) == before


@pytest.mark.parametrize("alias_kind", ["same_directory", "dotdot", "symlink"])
def test_parameter_export_rejects_input_directory_alias_before_writing(
    exporter, inputs, tmp_path, monkeypatch, alias_kind
):
    if alias_kind == "same_directory":
        out = inputs.folder
    elif alias_kind == "dotdot":
        (tmp_path / "directory").mkdir()
        out = tmp_path / "directory" / ".." / inputs.folder.name
    else:
        out = tmp_path / "alias"
        out.symlink_to(inputs.folder, target_is_directory=True)
    before = snapshot_inputs(inputs)
    with pytest.raises((OSError, ValueError, SystemExit)):
        invoke(exporter, [*input_args(inputs), "--out", out], monkeypatch)
    assert snapshot_inputs(inputs) == before
    assert {p.name for p in inputs.folder.iterdir()} == set(before)


@pytest.mark.parametrize(
    "failed_name",
    [
        "packet_schema.json",
        "binding.json",
        "source_acquisition_binding.sv",
        "rtl_input_manifest.json",
    ],
)
def test_partial_parameter_write_is_rolled_back_without_touching_inputs(
    exporter, inputs, tmp_path, monkeypatch, failed_name
):
    out = tmp_path / "failed_bundle"
    before = snapshot_inputs(inputs)
    original = Path.write_bytes

    def write_failure(path, data):
        if path.parent == out and path.name == failed_name:
            raise OSError("synthetic output failure")
        return original(path, data)

    monkeypatch.setattr(Path, "write_bytes", write_failure)
    with pytest.raises(OSError, match="synthetic output failure"):
        invoke(exporter, [*input_args(inputs), "--out", out], monkeypatch)
    assert not out.exists()
    assert snapshot_inputs(inputs) == before


@pytest.mark.parametrize("changed_name", sorted(BUNDLE_FILES))
def test_check_detects_every_changed_artifact_and_has_no_write_side_effect(
    exporter, inputs, tmp_path, monkeypatch, changed_name
):
    out = tmp_path / "bundle"
    args = [*input_args(inputs), "--out", out]
    assert invoke(exporter, args, monkeypatch) == 0
    assert invoke(exporter, [*args, "--check"], monkeypatch) == 0
    target = out / changed_name
    target.write_bytes(target.read_bytes() + b"\n// synthetic drift\n")
    before = {p.name: p.read_bytes() for p in out.iterdir()}
    with pytest.raises(SystemExit) as error:
        invoke(exporter, [*args, "--check"], monkeypatch)
    assert error.value.code not in (0, None)
    assert {p.name: p.read_bytes() for p in out.iterdir()} == before


def test_check_does_not_create_a_missing_output(exporter, inputs, tmp_path, monkeypatch):
    out = tmp_path / "never_created"
    with pytest.raises(SystemExit):
        invoke(exporter, [*input_args(inputs), "--out", out, "--check"], monkeypatch)
    assert not out.exists()


def test_exact_byte_check_does_not_hide_line_ending_drift(exporter, inputs, tmp_path, monkeypatch):
    out = tmp_path / "bundle"
    args = [*input_args(inputs), "--out", out]
    assert invoke(exporter, args, monkeypatch) == 0
    target = out / "rtl_params.vh"
    original = target.read_bytes()
    changed = original.replace(b"\n", b"\r\n")
    assert changed != original
    target.write_bytes(changed)
    with pytest.raises(SystemExit):
        invoke(exporter, [*args, "--check"], monkeypatch)
    assert target.read_bytes() == changed


@pytest.mark.parametrize(
    "input_name", ["config", "format", "binding", "source", "acquisition", "frozen"]
)
@pytest.mark.parametrize("alias_kind", ["dotdot", "symlink", "hardlink"])
def test_fitted_output_rejects_alias_of_any_input_before_writing(
    inputs, tmp_path, monkeypatch, input_name, alias_kind
):
    fitted = load_tool("export_fitted_rtl", monkeypatch)
    frozen = write_frozen(inputs)
    target = frozen if input_name == "frozen" else getattr(inputs, input_name)
    if alias_kind == "dotdot":
        (inputs.folder / "directory").mkdir()
        alias = inputs.folder / "directory" / ".." / target.name
    else:
        alias = tmp_path / "input_alias"
        if alias_kind == "symlink":
            alias.symlink_to(target)
        else:
            os.link(target, alias)
    before = snapshot_inputs(inputs)
    with pytest.raises((OSError, ValueError, SystemExit)):
        invoke(fitted, [*input_args(inputs), "--frozen", frozen, "--out", alias], monkeypatch)
    assert snapshot_inputs(inputs) == before
    assert alias.read_bytes() == target.read_bytes()


def test_fitted_export_rolls_back_a_partial_new_bundle(inputs, tmp_path, monkeypatch):
    fitted = load_tool("export_fitted_rtl", monkeypatch)
    frozen = write_frozen(inputs)
    before = snapshot_inputs(inputs)
    out = tmp_path / "failed_fitted"
    original = Path.write_text

    def fail_manifest(path, data, *args, **kwargs):
        if path.parent == out and path.name == "fitted_register_manifest.json":
            raise OSError("synthetic fitted output failure")
        return original(path, data, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", fail_manifest)
    with pytest.raises(OSError, match="synthetic fitted output failure"):
        invoke(fitted, [*input_args(inputs), "--frozen", frozen, "--out", out], monkeypatch)
    assert not out.exists()
    assert snapshot_inputs(inputs) == before


@pytest.mark.parametrize("name", ["registers.json", "fitted_register_manifest.json"])
def test_fitted_check_detects_drift_without_overwriting(inputs, tmp_path, monkeypatch, name):
    fitted = load_tool("export_fitted_rtl", monkeypatch)
    frozen = write_frozen(inputs)
    out = tmp_path / "fitted"
    args = [*input_args(inputs), "--frozen", frozen, "--out", out]
    assert invoke(fitted, args, monkeypatch) == 0
    target = out / name
    target.write_bytes(target.read_bytes() + b"\n// changed\n")
    before = {p.name: p.read_bytes() for p in out.iterdir()}
    with pytest.raises(SystemExit):
        invoke(fitted, [*args, "--check"], monkeypatch)
    assert {p.name: p.read_bytes() for p in out.iterdir()} == before
    with pytest.raises((OSError, ValueError, SystemExit)):
        invoke(fitted, args, monkeypatch)
    assert {p.name: p.read_bytes() for p in out.iterdir()} == before
