"""导出真实求解的合成线性拟合，不将其称为器件校准。"""

from __future__ import annotations

import json

import numpy as np
import pytest
from _rtl_test_support import copy_inputs, load_tool, read_json, sha, write_frozen, write_json

from adi_model.fixed_point import FixedPointReconstructor


@pytest.fixture
def inputs(tmp_path):
    return copy_inputs(tmp_path)


@pytest.fixture
def exporter(monkeypatch):
    return load_tool("export_fitted_rtl", monkeypatch)


def test_fit_export_is_deterministic_and_binds_the_actual_frozen_bytes(exporter, inputs):
    frozen_path = write_frozen(inputs)
    first = exporter.build_fitted_registers(
        inputs.config, inputs.format, inputs.binding, frozen_path
    )
    second = exporter.build_fitted_registers(
        inputs.config, inputs.format, inputs.binding, frozen_path
    )
    assert first == second
    register_text, manifest_text = first
    image, manifest, frozen = (
        json.loads(register_text),
        json.loads(manifest_text),
        read_json(frozen_path),
    )
    assert image["spec"] == frozen["spec"]
    assert image["format"] == read_json(inputs.format)
    assert np.asarray(image["weights_q"]).shape == (18, 3)
    np.testing.assert_array_equal(
        image["weights_q"], np.rint(np.asarray(frozen["weights"]) * (1 << 30)).astype(np.int64)
    )
    assert image["offset_q"] == int(np.rint(frozen["offset_v"] / 3.0 * (1 << 32)))
    assert manifest["register_sha256"] == sha(register_text)
    assert manifest["input_sha256"] == {
        "config_json": sha(inputs.config.read_bytes()),
        "format_json": sha(inputs.format.read_bytes()),
        "binding_json": sha(inputs.binding.read_bytes()),
        "source_sv": sha(inputs.source.read_bytes()),
        "acquisition_sv": sha(inputs.acquisition.read_bytes()),
        "frozen_fit": sha(frozen_path.read_bytes()),
    }
    assert manifest["training_statistics_are_supplied_metadata"] is True
    assert manifest["training_digest"] == frozen["training_digest"]
    assert manifest["rank"] == 55
    assert manifest["training_samples"] == 256
    assert manifest["weight_unit"] == "effective C/Cf"
    assert manifest["voltage_register_unit"] == "normalized V/Vfs"
    assert manifest["scope"] == "FROZEN_FIT_REGISTER_EXPORT_NOT_TRAINING_OR_ANALOG_VALIDATION"
    restored = FixedPointReconstructor.from_dict(image)
    assert restored.to_dict() == image


def test_exporter_does_not_retrain_or_create_nominal_coefficients(exporter, inputs, monkeypatch):
    import adi_model.weight_calibration as calibration

    frozen_path = write_frozen(inputs)

    def forbidden_training(*args, **kwargs):
        raise AssertionError("register export must not estimate coefficients")

    monkeypatch.setattr(calibration, "fit_unit_weights", forbidden_training)
    register_text, _ = exporter.build_fitted_registers(
        inputs.config, inputs.format, inputs.binding, frozen_path
    )
    assert len(json.loads(register_text)["weights_q"]) == 18


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("v_fs", 2.5),
        ("adc2_n_bits", 5),
        ("adc2_v_min", -0.125),
        ("adc2_v_max", 0.5),
        ("adc2_endpoint_policy", "half_open"),
    ],
)
def test_same_shape_but_changed_full_spec_is_rejected(exporter, inputs, name, value):
    frozen_path = write_frozen(inputs)
    frozen = read_json(frozen_path)
    frozen["spec"][name] = value
    write_json(frozen_path, frozen)
    with pytest.raises(ValueError):
        exporter.build_fitted_registers(inputs.config, inputs.format, inputs.binding, frozen_path)


@pytest.mark.parametrize(
    "change", ["schema1", "legacy_vfs", "missing_spec_field", "missing_domain", "unknown_domain"]
)
def test_partial_or_legacy_frozen_identity_is_rejected(exporter, inputs, change):
    frozen_path = write_frozen(inputs)
    frozen = read_json(frozen_path)
    if change == "schema1":
        frozen["schema_version"] = 1
    elif change == "legacy_vfs":
        frozen["v_fs"] = 3.0
    elif change == "missing_spec_field":
        del frozen["spec"]["adc2_endpoint_policy"]
    elif change == "missing_domain":
        del frozen["observation_domain"]
    else:
        frozen["observation_domain"] = "unbound_analog_truth"
    write_json(frozen_path, frozen)
    with pytest.raises(ValueError):
        exporter.build_fitted_registers(inputs.config, inputs.format, inputs.binding, frozen_path)


def test_nonidentifying_fit_metadata_is_not_accepted(exporter, inputs):
    frozen_path = write_frozen(inputs)
    frozen = read_json(frozen_path)
    frozen["rank"] = 54
    write_json(frozen_path, frozen)
    with pytest.raises(ValueError, match="identify"):
        exporter.build_fitted_registers(inputs.config, inputs.format, inputs.binding, frozen_path)
