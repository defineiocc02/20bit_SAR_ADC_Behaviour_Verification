"""以显式合成观测检查拟合及固定系数的数字推理。

小型线性记录只验证数值合同，不代表 ADC 仿真、实际电容提取、目标芯片校准
或 RTL 性能证据。
"""

import json
from dataclasses import FrozenInstanceError, asdict, fields, replace
from types import SimpleNamespace

import numpy as np
import pytest

from adi_model.calibration.reconstruction import DigitalState
from adi_model.calibration.weight_calibration import (
    CalibrationUnidentifiableError,
    DigitalObservation,
    FrozenCalibration,
    apply_frozen_calibration,
    fit_unit_weights,
)
from adi_model.contracts.fixed_point import FixedPointReconstructor


@pytest.fixture
def noisy_fit(synthetic_linear_data):
    case = synthetic_linear_data
    data = case.data
    voltage = (
        case.weights[data.slice_ids]
        * (data.signal_mask * case.known[:, None, None] + data.known_voltage_term_v)
    ).sum((1, 2)) + case.offset
    # 误差在合成响应节点显式声明，不通过目标动态范围反解。
    error = np.random.default_rng(101).normal(0, 5e-5, len(voltage))
    code = np.floor((voltage + error - data.spec.adc2_v_min) / data.spec.adc2_step_v).astype(
        np.int64
    )
    observed = replace(data, adc2_code=code)
    return case, observed, fit_unit_weights(observed, case.known), error


def test_training_contains_explicit_measurement_noise_and_reports_identifiability(noisy_fit):
    case, data, model, error = noisy_fit
    assert 3e-5 < np.std(error) < 7e-5
    assert model.rank == case.weights.size + 1 == 7
    assert model.training_samples == len(data.adc2_code) == 128
    assert 2e-5 < model.residual_rms_v < 8e-5
    assert np.all(model.standard_error > 0)
    assert model.condition >= 1 and np.isfinite(model.condition)
    np.testing.assert_allclose(model.weights, case.weights, atol=5e-4, rtol=0)


def _held_out(case):
    """构造单独声明的线性激励，不使用模拟模型或隐藏芯片状态。"""
    data = case.data
    generator = np.random.default_rng(893)
    n = 64
    ids = generator.integers(0, 2, (n, 1))
    signal = generator.integers(0, 2, (n, 1, 3))
    signal[:, :, 0] = 1
    term = generator.uniform(-0.2, 0.2, (n, 1, 3))
    known = generator.uniform(-0.35, 0.35, n)
    voltage = (case.weights[ids] * (signal * known[:, None, None] + term)).sum((1, 2)) + case.offset
    code = np.floor((voltage - data.spec.adc2_v_min) / data.spec.adc2_step_v).astype(np.int64)
    observed = DigitalObservation(
        data.spec,
        ids,
        code,
        generator.integers(0, 2, (n, 1, 2)),
        generator.integers(0, 2, (n, 1, 1)),
        signal,
        term,
        np.zeros(n, bool),
        np.arange(1000, 1000 + n),
        True,
    )
    return observed, known


def test_independent_holdout_uses_unchanged_frozen_coefficients(noisy_fit):
    case, training, model, _ = noisy_fit
    held, known = _held_out(case)
    frozen_bytes = model.weights.tobytes()
    nominal = np.full_like(case.weights, 0.1)
    weights = nominal[held.slice_ids]
    before = (held.fine_v - np.sum(weights * held.known_voltage_term_v, axis=(1, 2))) / np.sum(
        weights * held.signal_mask, axis=(1, 2)
    )
    after = model.reconstruct(held)
    assert model.weights.tobytes() == frozen_bytes
    assert not np.array_equal(held.sample_ids, training.sample_ids)
    assert np.std(after - known) < 0.1 * np.std(before - known)
    assert np.max(abs(after - known)) < 0.002


def test_estimator_record_does_not_include_physical_truth(synthetic_linear_data):
    data = synthetic_linear_data.data
    assert {f.name for f in fields(data)}.isdisjoint(
        {"pool", "x_known", "gain", "C_true", "held_voltage"}
    )

    class Result:
        digital_observation = data
        adc2_code = data.adc2_code
        sample_id = data.sample_ids

        @property
        def pool(self):
            raise AssertionError("digital extraction read physical truth")

        @property
        def g_vec(self):
            raise AssertionError("digital extraction read per-sample gain")

        @property
        def e_dac(self):
            raise AssertionError("digital extraction read physical DAC error")

    assert DigitalObservation.from_result(Result()) is data
    assert not hasattr(data, "pool") and not hasattr(data, "x_known")


def test_frozen_coefficients_remain_immutable_after_standard_json_round_trip(noisy_fit):
    _, data, model, _ = noisy_fit
    restored = FrozenCalibration.from_dict(json.loads(json.dumps(model.to_dict(), allow_nan=False)))
    np.testing.assert_array_equal(restored.reconstruct(data), model.reconstruct(data))
    with pytest.raises(ValueError):
        restored.weights[0, 0] = 0
    with pytest.raises(ValueError):
        restored.weights.setflags(write=True)
    with pytest.raises(FrozenInstanceError):
        restored.offset_v = 0


def test_missing_excitation_is_rejected_instead_of_hidden_by_nominal_priors(synthetic_linear_data):
    data = synthetic_linear_data.data
    deficient = replace(data, slice_ids=np.zeros_like(data.slice_ids))
    with pytest.raises(CalibrationUnidentifiableError, match="rank"):
        fit_unit_weights(deficient, synthetic_linear_data.known)


def test_dynamic_or_clipped_training_cannot_be_mislabeled_as_static_weights(synthetic_linear_data):
    data, known = synthetic_linear_data.data, synthetic_linear_data.known
    with pytest.raises(ValueError, match="static charge-domain"):
        fit_unit_weights(replace(data, static_charge_domain=False), known)
    flags = np.zeros(len(known), bool)
    flags[3] = True
    with pytest.raises(ValueError, match="clipped conversions"):
        fit_unit_weights(replace(data, overflow=flags), known)


def test_digital_application_preserves_raw_observations_and_separate_scores(noisy_fit):
    case, _, model, _ = noisy_fit
    data, known = _held_out(case)
    raw = data.adc2_code.copy()
    result = SimpleNamespace(
        digital_observation=data,
        adc2_code=data.adc2_code,
        sample_id=data.sample_ids,
        x_ref=known,
        out=np.zeros(len(known)),
        uncalibrated_out=None,
        state=DigitalState(32.0),
        calibration_applied=(),
    )
    apply_frozen_calibration(result, model)
    np.testing.assert_array_equal(result.adc2_code, raw)
    np.testing.assert_array_equal(result.out, model.reconstruct(data))
    np.testing.assert_array_equal(result.err, result.out - known)
    np.testing.assert_array_equal(result.uncalibrated_out, np.zeros(len(known)))
    assert result.calibration_applied == ("unit_weights",)
    assert result.state.weight_calibration is model
    # 评分参考不应改变已经固定系数的未知输入推理。
    result.x_ref = np.full(len(known), np.nan)
    apply_frozen_calibration(result, model)
    np.testing.assert_array_equal(result.out, model.reconstruct(data))
    assert np.isnan(result.err).all()


def test_quantized_frozen_weights_preserve_holdout_benefit(noisy_fit, explicit_format):
    case, _, model, _ = noisy_fit
    data, known = _held_out(case)
    decoder = FixedPointReconstructor.from_frozen_calibration(
        data.spec, model, format=explicit_format
    )
    stream = decoder.reconstruct(data)
    assert np.max(abs(stream.voltage - model.reconstruct(data))) < 0.51 * 6 / 2**20
    assert np.max(abs(stream.voltage - known)) < 0.002
    assert not np.any(stream.clipped_low | stream.clipped_high | stream.analog_overflow)


def test_fitted_register_image_roundtrip_and_scale_mismatch(noisy_fit, explicit_format):
    _, data, model, _ = noisy_fit
    decoder = FixedPointReconstructor.from_frozen_calibration(
        data.spec, model, format=explicit_format
    )
    encoded = json.loads(json.dumps(decoder.to_dict(), allow_nan=False))
    restored = FixedPointReconstructor.from_dict(encoded)
    np.testing.assert_array_equal(restored.reconstruct(data).code, decoder.reconstruct(data).code)
    with pytest.raises(ValueError, match="specification differs"):
        FixedPointReconstructor.from_frozen_calibration(
            replace(data.spec, v_fs=3.01), model, format=explicit_format
        )


@pytest.mark.parametrize("value", [np.nan, np.inf, -np.inf])
def test_training_reference_must_be_finite(synthetic_linear_data, value):
    known = synthetic_linear_data.known.copy()
    known[0] = value
    with pytest.raises(ValueError, match="finite"):
        fit_unit_weights(synthetic_linear_data.data, known)


def test_fit_freezes_the_complete_observation_specification(noisy_fit):
    _, data, model, _ = noisy_fit
    payload = model.to_dict()
    assert model.spec == data.spec
    assert model.observation_domain == "static_charge_connections_v2"
    assert payload["schema_version"] == 2
    assert payload["spec"] == asdict(data.spec)
    assert payload["observation_domain"] == "static_charge_connections_v2"
    assert "v_fs" not in payload
    assert {f.name for f in fields(model)}.isdisjoint({"pool", "chip_id", "physical_weights"})


def test_schema_one_is_rejected_instead_of_assumed_current(noisy_fit):
    _, _, model, _ = noisy_fit
    old = model.to_dict()
    old["schema_version"] = 1
    old.pop("spec")
    old.pop("observation_domain")
    old["v_fs"] = 3.0
    with pytest.raises(ValueError, match="unknown calibrated-weight schema"):
        FrozenCalibration.from_dict(old)


@pytest.mark.parametrize(
    "change",
    [
        {"adc2_n_bits": 25},
        {"adc2_v_min": -1.5},
        {"adc2_v_max": 1.5},
        {"adc2_endpoint_policy": "closed"},
    ],
)
def test_same_shape_but_different_backend_contract_cannot_reuse_coefficients(
    noisy_fit, explicit_format, change
):
    _, data, model, _ = noisy_fit
    different = replace(data.spec, **change)
    assert different.shape == data.spec.shape
    with pytest.raises(ValueError, match="specification or observation domain"):
        model.reconstruct(replace(data, spec=different))
    with pytest.raises(ValueError, match="specification differs"):
        FixedPointReconstructor.from_frozen_calibration(different, model, format=explicit_format)
