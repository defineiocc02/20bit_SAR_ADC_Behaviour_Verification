"""验证正常用户默认入口及工程假设的可见性，不声明原芯片复现。

波形与 N 可以直接运行；低层未知项仍不自动补全。名义数字系数与制造随机量
保持分离，全部样本及节点标志保留，20 位仅表示输出码宽。
"""

from dataclasses import replace

import numpy as np
import pytest

from adi_model.config import Config, ConfigError
from adi_model.defaults import DefaultParameters, default_model, simulate
from adi_model.sampler import dc_input, sine_input


@pytest.mark.parametrize("count", [256, 1024])
def test_waveform_and_count_produce_all_raw_and_integer_records(count):
    run = simulate(sine_input(2.0, 1e6, 0.2), count)
    assert run.codes.code.shape == (count,)
    assert np.issubdtype(run.codes.code.dtype, np.integer)
    assert np.all((run.codes.code >= 0) & (run.codes.code < 2**20))
    np.testing.assert_array_equal(run.raw.sample_id, np.arange(count))
    np.testing.assert_array_equal(run.codes.analog_overflow, run.raw.analog_overflow)
    assert run.metadata["n_samples"] == count
    assert run.metadata["output_bits"] == 20
    assert run.metadata["source_reproduction"] is False
    assert run.metadata["digital_coefficients"].startswith("NOMINAL_NOT_FITTED")
    assert not run.raw.calibration_applied
    assert run.metadata["raw_invalid"] == int(np.count_nonzero(run.codes.analog_overflow))
    assert run.metadata["final_clip_low"] == int(np.count_nonzero(run.codes.clipped_low))
    assert run.metadata["final_clip_high"] == int(np.count_nonzero(run.codes.clipped_high))


def test_defaults_do_not_make_low_level_config_implicitly_ready():
    assert Config().adc2_n_bits is None
    with pytest.raises(ConfigError, match="missing"):
        Config().check_legal()
    model = default_model()
    assert model.cfg.adc2_n_bits == 12
    assert model.cfg.b1 == 9
    assert (model.cfg.n_slices, model.cfg.n_active) == (18, 8)
    assert model.nominal_gain == pytest.approx(32.0)
    assert model.nominal_active_cap_f == pytest.approx(20.5e-12)


def test_inference_table_separates_source_anchors_from_geometry_and_noise_assumptions():
    model = default_model()
    rows = model.inference_table()
    assert rows["stage1_bits"]["grade"] == "DISCLOSED"
    assert rows["n_main"]["grade"] == "ASSUMED_GEOMETRY"
    assert rows["adc2_window_v"]["grade"] == "INFERRED_HEADROOM"
    assert rows["ra_noise_rms_v"]["grade"] == "ASSUMED_NOISE_BUDGET"
    assert rows["fixed_point"]["grade"] == "ENGINEERING_ARITHMETIC_CHOICE"
    assert rows["sadc_thresholds"]["value"] == {"edge_count": 513, "first_v": -3.0, "last_v": 3.0}
    assert rows["sadc_static_mismatch"]["value"] == 0.0
    assert all(isinstance(row["reason"], str) and row["reason"] for row in rows.values())
    result = model.simulate(dc_input(0.2), 8)
    assert result.metadata["enabled"]["unit_dem"] is False
    assert result.metadata["enabled"]["source_dither"] is False
    assert any("lower/upper" in text for text in result.metadata["unmodeled"])


def test_same_seed_and_run_id_replay_and_different_run_ids_do_not_refabricate_chip():
    model = default_model(seed=42)
    wave = sine_input(1.5, 1e6, 0.1)
    first = model.simulate(wave, 32, run_id=5)
    replay = model.simulate(wave, 32, run_id=5)
    changed = model.simulate(wave, 32, run_id=6)
    np.testing.assert_array_equal(first.codes.code, replay.codes.code)
    np.testing.assert_array_equal(first.raw.conv_slice_ids, replay.raw.conv_slice_ids)
    np.testing.assert_array_equal(first.raw.vra, replay.raw.vra)
    assert first.raw.pool.chip is replay.raw.pool.chip is changed.raw.pool.chip is model.chip
    assert np.any(first.raw.conv_slice_ids != changed.raw.conv_slice_ids)
    assert np.any(first.raw.sample.rdac_noise_v != changed.raw.sample.rdac_noise_v)
    assert first.metadata["seed_partition"]["fabrication_spawn_key"] == [0]
    assert first.metadata["seed_partition"]["noise_spawn_key"] == [2, 5]
    assert changed.metadata["seed_partition"]["noise_spawn_key"] == [2, 6]


def test_nominal_integer_coefficients_do_not_depend_on_manufactured_capacitances():
    first, second = default_model(seed=1), default_model(seed=2)
    assert np.any(first.chip.C_main != second.chip.C_main)
    np.testing.assert_array_equal(
        first.nominal_decoder.to_dict()["weights_q"], second.nominal_decoder.to_dict()["weights_q"]
    )
    assert first.nominal_decoder.spec == second.nominal_decoder.spec
    assert not hasattr(first.nominal_decoder, "training_samples")


def test_explicit_nominal_override_is_not_hidden_retuning_or_source_authority():
    base = DefaultParameters()
    model = default_model(parameters=replace(base, feedback_cap_f=2 * base.feedback_cap_f))
    assert model.parameters.unit_cap_f == base.unit_cap_f
    assert model.parameters.bridge_cap_f == base.bridge_cap_f
    assert model.nominal_active_cap_f == pytest.approx(20.5e-12)
    assert model.nominal_gain == pytest.approx(16.0)
    rows = model.inference_table()
    assert rows["feedback_cap_f"]["grade"] == "USER_OVERRIDE"
    assert rows["feedback_cap_f"]["default_grade"] == "DERIVED_WITH_ASSUMPTION"


def test_custom_window_and_threshold_metadata_report_the_actual_override():
    thresholds = tuple(np.linspace(-2.0, 2.0, 513) + 0.0003)
    model = default_model(
        config_overrides={
            "v_fs": 2.0,
            "fs": 20e6,
            "sadc_thresholds_v": thresholds,
            "adc2_n_bits": 10,
            "adc2_v_min": -0.1,
            "adc2_v_max": 0.8,
        }
    )
    result = model.simulate(dc_input(0.2), 8)
    rows = result.metadata["inferences"]
    assert rows["fs_hz"]["grade"] == "USER_OVERRIDE"
    assert rows["adc2_window_v"]["value"] == [-0.1, 0.8]
    assert rows["adc2_window_v"]["grade"] == "USER_OVERRIDE"
    assert rows["sadc_thresholds"]["grade"] == "USER_OVERRIDE"
    assert rows["sadc_static_mismatch"]["value"] == pytest.approx(0.0003)
    np.testing.assert_array_equal(result.raw.sample.aperture_s, np.arange(8) / 20e6)
    assert result.metadata["config_overrides"]["adc2_n_bits"] == 10


@pytest.mark.parametrize("value", [-3.2, 3.2])
def test_out_of_range_inputs_and_each_node_flag_are_retained(value):
    run = simulate(dc_input(value), 16)
    assert len(run.codes.code) == len(run.raw.adc2_code) == 16
    assert run.raw.sadc_over.all()
    assert run.codes.analog_overflow.all()
    np.testing.assert_array_equal(run.raw.sample.input_clean_v, np.full(16, value))
    expected_union = (
        run.raw.sadc_over
        | run.raw.rdac_over
        | run.raw.ra_sat
        | run.raw.adc2_over
        | ~run.raw.plan_valid
    )
    np.testing.assert_array_equal(run.codes.analog_overflow, expected_union)
    assert run.metadata["raw_invalid"] == 16


def test_declared_ideal_override_matches_independent_quantization_bound():
    parameters = replace(DefaultParameters(), unit_mismatch_std=0.0, bridge_mismatch_std=0.0)
    model = default_model(
        parameters=parameters,
        config_overrides={"enable_sampling_noise": False, "ra_enable_noise": False},
    )
    run = model.simulate(sine_input(2.0, 1e6, 0.1), 64)
    input_lsb = 2 * model.cfg.v_fs / 2**20
    bound = model.cfg.delta2 / (2 * model.nominal_gain) + input_lsb / 2
    assert np.max(abs(run.codes.voltage - run.raw.sample.input_clean_v)) <= bound + 1e-9
    assert not run.codes.analog_overflow.any()
    assert not run.metadata["enabled"]["sampling_noise"]
    assert not run.metadata["enabled"]["ra_output_noise"]


@pytest.mark.parametrize("count", [0, -1, True, 1.5])
def test_invalid_record_count_is_not_coerced_or_allocated(count):
    with pytest.raises(ConfigError):
        simulate(dc_input(0.0), count)


@pytest.mark.parametrize(
    "overrides",
    [
        {"b1": 7},
        {"dac_arch": "unary"},
        {"dither_mode": "quantizer"},
        {"dem_enable": True},
        {"dyn_input_settling": True},
        {"adc2_v_min": None},
        {"c_sadc": None},
        {"input_network": {}},
    ],
)
def test_unsupported_or_incomplete_explicit_requests_do_not_silently_fall_back(overrides):
    with pytest.raises((ConfigError, TypeError)):
        default_model(config_overrides=overrides)


@pytest.mark.parametrize(
    "field,value", [("unit_cap_f", 0), ("n_main", 64), ("unit_mismatch_std", -0.1)]
)
def test_invalid_nominal_geometry_or_physical_scale_is_rejected(field, value):
    with pytest.raises(ConfigError):
        replace(DefaultParameters(), **{field: value})
