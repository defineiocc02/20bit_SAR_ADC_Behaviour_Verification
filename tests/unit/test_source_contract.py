"""检查原文披露的目标常量和显式输入拒绝规则，不运行 ADC。"""

import importlib.util
from dataclasses import fields, replace

import numpy as np
import pytest

from adi_model.simulation.config import Config, ConfigError
from adi_model.behavioral.sadc import SADC, build_first_stage_quantizer


def test_empty_config_is_inspectable_but_not_ready_for_execution():
    cfg = Config()
    before = cfg.to_dict()
    assert (cfg.b1, cfg.n_slices, cfg.n_active) == (9, 18, 8)
    assert cfg.sadc_thresholds_v is None and cfg.adc2_n_bits is None
    assert cfg.ra_out_noise_rms is None and cfg.seed is None
    with pytest.raises(ConfigError, match="missing"):
        cfg.check_legal()
    assert cfg.to_dict() == before


@pytest.mark.parametrize("name", ["paper_consistent", "paper_literal", "legacy_v61"])
def test_retired_factories_are_absent(name):
    assert not hasattr(Config, name)


@pytest.mark.parametrize(
    "field,value", [("b1", 6), ("b1", 7), ("n_slices", 16), ("n_slices", 19), ("n_active", 1)]
)
def test_target_architecture_cannot_be_relabeled(configuration_inputs, field, value):
    with pytest.raises(ConfigError, match=field):
        replace(configuration_inputs, **{field: value}).check_legal()


@pytest.mark.parametrize(
    "field,value",
    [
        ("dac_arch", "unary"),
        ("stage1_reading", "paper_consistent"),
        ("ktc_enable", True),
        ("dither_transfer_model", "dual_port"),
        ("dither_enhancement_bits", 2),
        ("dither_mode", "quantizer"),
        ("sadc_cap_ratio", 0.05),
        ("target_dr_db", 94.6),
        ("ra_autozero_cost_db", 1.6),
        ("dyn_t_sample_frac", 0.45),
    ],
)
def test_retired_fields_are_not_accepted_as_inactive_compatibility(field, value):
    assert field not in {f.name for f in fields(Config)}
    with pytest.raises(TypeError):
        Config.from_dict({field: value})


@pytest.mark.parametrize(
    "field",
    [
        "dac_n_main",
        "dac_n_sub",
        "sadc_thresholds_v",
        "sadc_endpoint_policy",
        "adc2_n_bits",
        "adc2_v_min",
        "adc2_v_max",
        "adc2_codebook",
        "adc2_endpoint_policy",
        "ra_v_clip",
    ],
)
def test_missing_unpublished_inputs_are_not_derived(configuration_inputs, field):
    cfg = replace(configuration_inputs, **{field: None})
    with pytest.raises(ConfigError, match=field):
        cfg.check_legal()
    assert getattr(cfg, field) is None


def test_input_readiness_is_not_a_circuit_or_source_conformance_result(configuration_inputs):
    configuration_inputs.check_legal()
    assert configuration_inputs.dac_n_main == 2
    assert configuration_inputs.ra_out_noise_rms is None
    report = configuration_inputs.validate(False)
    assert set(report) == {"SADC decision intervals", "target output step", "ADC2 code step"}
    assert report["SADC decision intervals"][:3] == (512.0, 512.0, True)


def test_rtl_interface_does_not_fill_analog_inputs():
    cfg = Config.rtl_structural()
    assert cfg.execution_profile == "rtl16"
    assert cfg.conversion is None and cfg.adc2_v_min is None
    with pytest.raises(ConfigError, match="missing"):
        cfg.check_legal()


def test_dither_request_requires_an_actual_runtime_program(explicit_pipeline_factory):
    from adi_model.simulation.pipeline import run_pipeline
    from adi_model.behavioral.sampler import dc_input

    cfg, controls = explicit_pipeline_factory(n_samples=4)
    requested = replace(cfg, dither_enable=True)
    requested.check_legal(geometry=controls["pool"].geometry)
    with pytest.raises(ConfigError, match="dither_program"):
        run_pipeline(requested, dc_input(0.2), 4, **controls)


@pytest.mark.parametrize("amplitude", [0.25, 4.0])
def test_old_amplitude_request_cannot_be_silently_ignored(amplitude):
    with pytest.raises(TypeError):
        Config.from_dict({"dither_amplitude_lsb1": amplitude})


@pytest.mark.parametrize(
    "name",
    [
        "chip",
        "rdac",
        "ktc",
        "noise_phase",
        "ref_track",
        "interleave_tracking",
        "aux_input",
        "digital_core",
        "experiments",
        "closure_experiments",
        "acceptance",
        "reporting",
        "sim_split",
    ],
)
def test_retired_runtime_modules_have_no_compatibility_alias(name):
    assert importlib.util.find_spec(f"adi_model.{name}") is None


def test_target_sadc_requires_all_513_actual_thresholds_and_an_endpoint(synthetic_sadc_thresholds):
    cfg = Config(sadc_thresholds_v=synthetic_sadc_thresholds, sadc_endpoint_policy="half_open")
    adc = build_first_stage_quantizer(cfg)
    assert adc.n_code == 512
    np.testing.assert_array_equal(adc.thresholds, synthetic_sadc_thresholds)
    for broken in (replace(cfg, sadc_thresholds_v=None), replace(cfg, sadc_endpoint_policy=None)):
        with pytest.raises(ConfigError, match="SADC"):
            build_first_stage_quantizer(broken)


@pytest.mark.parametrize("endpoint,upper_over", [("closed", False), ("half_open", True)])
def test_small_sadc_is_a_generic_math_leaf(endpoint, upper_over):
    # 三个区间只检查区间算术，不重新定义目标 ADC。
    adc = SADC(np.array([-1.0, -0.5, 0.2, 1.0]), endpoint_policy=endpoint)
    code, over = adc.convert_codes(np.array([-2, -1, -0.5, 0.2, 1, 2]))
    np.testing.assert_array_equal(code, [0, 0, 1, 2, 2, 2])
    np.testing.assert_array_equal(over, [True, False, False, False, upper_over, True])


@pytest.mark.parametrize("value", [np.nan, np.inf, -np.inf])
def test_sadc_nonfinite_input_is_never_a_valid_conversion(value):
    adc = SADC(np.array([-1.0, 0.0, 1.0]), endpoint_policy="half_open")
    with pytest.raises(ValueError, match="finite"):
        adc.convert_codes([value])


@pytest.mark.parametrize("thresholds", [[0, 0], [1, 0], [0, np.nan], [[0, 1]], [0]])
def test_invalid_sadc_thresholds_are_refused(thresholds):
    with pytest.raises(ConfigError, match="thresholds"):
        SADC(np.asarray(thresholds), endpoint_policy="closed")


def test_active_input_network_requires_its_bus_capacitance_at_config_boundary(configuration_inputs):
    from adi_model.behavioral.input_network import InputNetworkParameters

    cfg = replace(
        configuration_inputs,
        dyn_input_settling=True,
        c_sadc=1e-12,
        dyn_r_source=30.0,
        dyn_r_on=20.0,
        dyn_ron_code_coeff=0.0,
        input_network=InputNetworkParameters(),
    )
    with pytest.raises(ConfigError, match="filter_cap_f"):
        cfg.check_legal()
    assert cfg.input_network.filter_cap_f is None


def test_selected_conversion_requires_actual_timing_and_models_at_config_boundary(
    configuration_inputs,
):
    from adi_model.behavioral.conversion import ConversionParameters

    cfg = replace(configuration_inputs, conversion=ConversionParameters())
    with pytest.raises(ConfigError, match="quantizer_time_s|ra_duration_s|response_model"):
        cfg.check_legal()


def test_active_reference_requires_its_explicit_events_and_switch_time(configuration_inputs):
    from adi_model.behavioral.conversion import ConversionParameters

    timing = ConversionParameters(
        quantizer_time_s=3e-9,
        ra_duration_s=10e-9,
        ra_response_model="ideal",
        adc2_response_model="ideal",
        ra_dc_gain="ideal",
        ra_slew_v_s="unlimited",
    )
    cfg = replace(
        configuration_inputs,
        dyn_ref_settling=True,
        dyn_c_decouple=100e-9,
        dyn_tau_ref=5e-9,
        conversion=timing,
    )
    with pytest.raises(
        ConfigError, match="rdac_event_times_s|reference_accurate_start_s|fine_reference_tau_s"
    ):
        cfg.check_legal()
