"""检查本机显式输入准备；旧SSH/Vivado封装已退役，不恢复其隐式参数。"""

from __future__ import annotations

import json

import pytest
from _synth_test_support import DRIVER, ROOT, digest, inputs, invoke, manifest, tcl_probe


def test_prepare_records_every_input_and_never_launches_a_tool(tmp_path):
    data = inputs(tmp_path / "input files")
    before = {k: digest(p) for k, p in data.items() if p.is_file()}
    out = tmp_path / "new prepared run"
    cp = invoke(
        data,
        out,
        "missing_tool_that_must_not_be_started",
        "--prepare-only",
        "--parameters",
        "P_WIDTH=7",
    )
    assert cp.returncode == 0, cp.stdout + cp.stderr
    record = manifest(out)
    assert record["status"] == "INPUTS_PREPARED"
    assert record["tool_execution_performed"] is False
    assert record["synthesis_evidence"] == record["sta_evidence"] == "NOT_RUN"
    assert record["config"]["parameters"] == "P_WIDTH=7"
    assert record["config"]["clock"] == "sample_clock"
    assert set(record["declared_input_sha256"]) == {str(p) for p in data.values() if p.is_file()}
    assert all(record["declared_input_sha256"][str(data[k])] == h for k, h in before.items())
    assert len(list((out / "input_snapshot").iterdir())) == len(before)
    assert sorted(p.read_bytes() for p in (out / "input_snapshot").iterdir()) == sorted(
        p.read_bytes() for p in data.values() if p.is_file()
    )
    assert record["script_sha256"] == digest(ROOT / "synth/run_dc.tcl")
    assert record["contract_sha256"] == digest(out / "inputs.tcl")
    assert before == {k: digest(data[k]) for k in before}


@pytest.mark.parametrize("key", ["sources", "include_dirs", "headers", "defines"])
def test_all_filelist_lists_must_be_explicit(tmp_path, key):
    data = inputs(tmp_path)
    value = json.loads(data["filelist"].read_text())
    del value[key]
    data["filelist"].write_text(json.dumps(value))
    with pytest.raises(ValueError, match="exactly"):
        DRIVER.load_filelist(data["filelist"])


@pytest.mark.parametrize(
    "mutation",
    [
        "empty_sources",
        "missing_source",
        "non_sv_source",
        "duplicate_source",
        "duplicate_header",
        "missing_include",
        "header_suffix",
        "unknown_key",
        "array_root",
        "string_sources",
        "empty_item",
        "newline_item",
        "unsafe_define",
        "nonfinite",
        "duplicate_key",
    ],
)
def test_invalid_filelist_is_rejected_before_output_creation(tmp_path, mutation):
    data = inputs(tmp_path / "input")
    value = json.loads(data["filelist"].read_text())
    if mutation == "empty_sources":
        value["sources"] = []
    elif mutation == "missing_source":
        value["sources"] = ["missing.sv"]
    elif mutation == "non_sv_source":
        value["sources"] = ["filelist.json"]
    elif mutation == "duplicate_source":
        value["sources"] *= 2
    elif mutation == "duplicate_header":
        value["headers"] *= 2
    elif mutation == "missing_include":
        value["include_dirs"] = ["missing"]
    elif mutation == "header_suffix":
        value["headers"] = ["leaf.sv"]
    elif mutation == "unknown_key":
        value["old_profile"] = "paper_literal"
    elif mutation == "array_root":
        value = []
    elif mutation == "string_sources":
        value["sources"] = "sample_counter.sv"
    elif mutation == "empty_item":
        value["sources"] = [""]
    elif mutation == "newline_item":
        value["sources"] = ["a\nb.sv"]
    elif mutation == "unsafe_define":
        value["defines"] = ["A=[exec bad]"]
    elif mutation == "nonfinite":
        value["defines"] = [float("nan")]
    data["filelist"].write_text(json.dumps(value))
    if mutation == "duplicate_key":
        text = data["filelist"].read_text()
        data["filelist"].write_text('{"sources": [],' + text[1:])
    out = tmp_path / "out"
    cp = invoke(data, out, "must_not_launch", "--prepare-only")
    assert cp.returncode == 1 and not out.exists(), cp.stdout + cp.stderr
    assert "FAIL:" in cp.stderr


@pytest.mark.parametrize(
    "key,kind", [("library", "missing"), ("library", "empty"), ("sdc", "missing"), ("sdc", "empty")]
)
def test_library_and_sdc_cannot_be_missing_or_empty(tmp_path, key, kind):
    data = inputs(tmp_path / "inputs")
    if kind == "missing":
        data[key].unlink()
    else:
        data[key].write_bytes(b"")
    out = tmp_path / "out"
    cp = invoke(data, out, "must_not_launch", "--prepare-only")
    assert cp.returncode == 1 and "nonempty library and SDC" in cp.stderr and not out.exists()


@pytest.mark.parametrize("value", ["$bad", "name with space", "-top", "a;exec"])
def test_top_and_clock_are_simple_explicit_identifiers(tmp_path, value):
    data = inputs(tmp_path / "inputs")
    cp = invoke(data, tmp_path / "out", "must_not_launch", "--prepare-only", "--clock=" + value)
    assert cp.returncode == 1 and "top/clock" in cp.stderr
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("value", ["WIDTH=1.5", "WIDTH=[exec bad]", "", "WIDTH", "WIDTH=1;bad"])
def test_parameters_do_not_accept_tcl_or_implicit_values(tmp_path, value):
    data = inputs(tmp_path / "inputs")
    cp = invoke(data, tmp_path / "out", "must_not_launch", "--prepare-only", "--parameters", value)
    assert cp.returncode == 1 and "NAME=integer" in cp.stderr and not (tmp_path / "out").exists()


@pytest.mark.parametrize(
    "value",
    [
        'literal $v [exec dangerous] "quote" \\ backslash',
        "unicode 中文\nnext\rline",
        "{brace}; semi",
    ],
)
def test_tcl_literals_roundtrip_without_command_or_variable_substitution(tmp_path, value):
    script = (
        "set captured "
        + DRIVER.tcl_word(value)
        + "\nbinary scan [encoding convertto utf-8 $captured] H* bytes\nputs $bytes\n"
    )
    cp = tcl_probe(tmp_path, script)
    assert cp.returncode == 0, cp.stdout + cp.stderr
    assert cp.stdout.strip() == value.encode("utf-8").hex()


def test_existing_output_is_preserved_and_git_output_refused(tmp_path):
    data = inputs(tmp_path / "inputs")
    out = tmp_path / "existing"
    out.mkdir()
    sentinel = out / "sentinel"
    sentinel.write_bytes(b"original\x00bytes")
    cp = invoke(data, out, "must_not_launch", "--prepare-only")
    assert cp.returncode == 1 and sentinel.read_bytes() == b"original\x00bytes"
    repository = tmp_path / "unrelated git tree"
    repository.mkdir()
    (repository / ".git").write_text("gitdir: external\n")
    cp = invoke(data, repository / "new", "must_not_launch", "--prepare-only")
    assert cp.returncode == 1 and not (repository / "new").exists()


def test_relative_filelist_paths_resolve_from_the_manifest_not_cwd(tmp_path, monkeypatch):
    data = inputs(tmp_path / "different cwd")
    monkeypatch.chdir(tmp_path)
    loaded = DRIVER.load_filelist(data["filelist"])
    assert loaded["sources"] == [data["source"], data["leaf"]]
    assert loaded["headers"] == [data["header"]]
    assert loaded["defines"] == ["TEXT_FIXTURE=1"]
