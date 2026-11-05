"""实际默认模拟 CLI 的完整输出、覆盖追溯与失败边界。"""

import hashlib
import json

import numpy as np
import pytest

from adi_model import __main__ as module_cli
from adi_model import cli

KEYS = {
    "code",
    "sample_id",
    "clipped_low",
    "clipped_high",
    "analog_overflow",
    "sadc_over",
    "ra_sat",
    "adc2_over",
    "rdac_over",
    "input_clean_v",
    "input_v",
}


def _invoke(arguments):
    try:
        return cli.main_simulate(arguments)
    except SystemExit as exc:
        return exc.code


def test_cli_runs_the_real_model_and_keeps_all_output_arrays(tmp_path, capsys):
    output = tmp_path / "run"
    assert _invoke(["--samples", "32", "--output", str(output)]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["status"] == "SIMULATION_COMPLETE"
    assert summary["model_run"] is True
    assert summary["result_role"] == "UNCALIBRATED_NOMINAL_20BIT"
    assert summary["source_conformance"] == "NOT_ASSESSED"
    assert summary["source_reproduction"] is False
    assert summary["records_filtered"] == 0
    assert summary["n_codes"] == summary["n_samples"] == 32
    assert summary["frequency_adjusted"] is False
    assert {p.name for p in output.iterdir()} == {"codes.npz", "metadata.json", "assumptions.json"}
    with np.load(output / "codes.npz") as data:
        assert set(data.files) == KEYS
        assert all(data[name].shape == (32,) for name in KEYS)
        np.testing.assert_array_equal(data["sample_id"], np.arange(32))
        assert np.all(data["code"] < 2**20)
    assert json.loads((output / "assumptions.json").read_text()) == summary["inferences"]
    metadata = json.loads((output / "metadata.json").read_text())
    assert metadata["model_run"] is True and metadata["records_filtered"] == 0


def test_cli_preserves_out_of_range_samples_and_the_node_flag_union(tmp_path, capsys):
    output = tmp_path / "overrange"
    assert _invoke(["--amplitude", "3.2", "--samples", "32", "--output", str(output)]) == 0
    summary = json.loads(capsys.readouterr().out)
    with np.load(output / "codes.npz") as data:
        assert len(data["code"]) == 32
        assert np.any(data["sadc_over"])
        assert np.any(abs(data["input_clean_v"]) > 3)
        union = data["sadc_over"] | data["ra_sat"] | data["adc2_over"] | data["rdac_over"]
        np.testing.assert_array_equal(data["analog_overflow"], union)
        assert summary["raw_invalid"] == int(np.count_nonzero(union))


def test_exact_equal_form_flags_keep_user_origin_and_do_not_retune_frequency(capsys):
    assert _invoke(["--amplitude=1.2", "--frequency=1234567", "--samples=16"]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["stimulus"]["frequency"]["value"] == 1234567
    assert summary["frequency_adjusted"] is False
    for name in ("amplitude", "frequency", "samples"):
        assert summary["stimulus"][name]["origin"] == "USER_OVERRIDE"


def test_explicit_parameter_files_have_hashes_and_actual_override_values(tmp_path, capsys):
    parameters = tmp_path / "parameters.json"
    overrides = tmp_path / "overrides.json"
    parameters.write_text(json.dumps({"unit_mismatch_std": 0.0, "bridge_mismatch_std": 0.0}))
    overrides.write_text(
        json.dumps({"fs": 20e6, "enable_sampling_noise": False, "ra_enable_noise": False})
    )
    assert (
        _invoke(
            [
                "--samples",
                "16",
                "--parameters",
                str(parameters),
                "--config-overrides",
                str(overrides),
            ]
        )
        == 0
    )
    summary = json.loads(capsys.readouterr().out)
    by_role = {row["role"]: row for row in summary["input_files"]}
    for role, path in (("parameters", parameters), ("config_overrides", overrides)):
        assert by_role[role]["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
        assert by_role[role]["override_fields"] == sorted(json.loads(path.read_text()))
    assert summary["inferences"]["fs_hz"]["value"] == 20e6
    assert summary["inferences"]["fs_hz"]["grade"] == "USER_OVERRIDE"
    assert summary["enabled"]["sampling_noise"] is False
    assert summary["enabled"]["ra_output_noise"] is False


@pytest.mark.parametrize(
    "arguments",
    [
        ["--amp", "1"],
        ["--fre", "1e6"],
        ["--samples", "0"],
        ["--samples", "4294967296"],
        ["--seed", "-1"],
        ["--frequency", "nan"],
        ["--amplitude", "inf"],
    ],
)
def test_invalid_cli_requests_emit_failure_json_without_a_model_success_claim(arguments, capsys):
    assert _invoke(arguments) == 2
    output = capsys.readouterr()
    assert not output.out
    error = json.loads(output.err)
    assert error["status"] == "SIMULATION_REJECTED" and error["model_run"] is False


@pytest.mark.parametrize("kind", ["unknown", "active_none", "dither_without_words", "duplicate"])
def test_bad_override_files_do_not_fall_back_to_defaults(tmp_path, capsys, kind):
    path = tmp_path / "bad.json"
    payload = {
        "unknown": '{"legacy_field": 1}',
        "active_none": '{"c_sadc": null}',
        "dither_without_words": '{"dither_enable": true}',
        "duplicate": '{"v_fs": 3, "v_fs": 2}',
    }[kind]
    path.write_text(payload)
    assert _invoke(["--samples", "16", "--config-overrides", str(path)]) == 2
    result = capsys.readouterr()
    assert not result.out
    assert json.loads(result.err)["model_run"] is False


def test_existing_and_dangling_output_paths_are_rejected_without_modification(tmp_path, capsys):
    existing = tmp_path / "old"
    existing.mkdir()
    marker = existing / "user.txt"
    marker.write_bytes(b"prior evidence")
    dangling = tmp_path / "dangling"
    dangling.symlink_to(tmp_path / "absent")
    for target in (existing, dangling):
        assert _invoke(["--samples", "8", "--output", str(target)]) == 2
        result = capsys.readouterr()
        assert json.loads(result.err)["model_run"] is False
    assert marker.read_bytes() == b"prior evidence" and dangling.is_symlink()


def test_python_module_uses_the_same_actual_simulate_entry(capsys):
    assert module_cli.main(["simulate", "--samples", "8"]) == 0
    assert json.loads(capsys.readouterr().out)["model_run"] is True
