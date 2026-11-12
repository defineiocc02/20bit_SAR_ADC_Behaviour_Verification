"""检查真实命令分发、显式时钟预算及持久化数值边界。"""

from dataclasses import replace
from types import SimpleNamespace

import numpy as np
import pytest

from adi_model import __main__ as module_cli
from adi_model.behavioral.conversion import ConversionParameters
from adi_model.behavioral.sampler import dc_input
from adi_model.contracts.fixed_point import FixedPointReconstructor
from adi_model.metrics.metrics import summarize
from adi_model.simulation.config import Config
from adi_model.simulation.pipeline import run_pipeline


@pytest.mark.parametrize("command", ["validate-config", "simulate"])
@pytest.mark.parametrize("implicit", [False, True])
def test_module_dispatch_forwards_arguments_without_an_old_script_alias(
    command, implicit, monkeypatch
):
    seen = []
    selected = "main_validate_config" if command == "validate-config" else "main_simulate"
    monkeypatch.setattr(module_cli, selected, lambda argv: seen.append(list(argv)) or 0)
    options = (
        ["--config", "input with spaces.json"]
        if command == "validate-config"
        else ["--samples", "16"]
    )
    argv = [command, *options]
    if implicit:
        monkeypatch.setattr("sys.argv", ["python -m adi_model", *argv])
    assert module_cli.main(None if implicit else argv) == 0
    assert seen == [options]


def _ideal_conversion():
    return ConversionParameters(
        quantizer_time_s=5e-9,
        ra_duration_s=10e-9,
        ra_response_model="ideal",
        ra_dc_gain="ideal",
        ra_slew_v_s="unlimited",
        adc2_response_model="ideal",
    )


def test_conversion_timing_uses_explicit_intervals_not_an_inferred_fraction():
    parameters = _ideal_conversion()
    parameters.require_runtime(fs=40e6, reference_active=False)
    with pytest.raises(ValueError, match="exceed one sample"):
        replace(parameters, ra_duration_s=21e-9).require_runtime(fs=40e6, reference_active=False)
    replace(parameters, ra_duration_s=21e-9).require_runtime(fs=20e6, reference_active=False)


@pytest.mark.parametrize(
    "field",
    [
        "quantizer_time_s",
        "ra_duration_s",
        "ra_response_model",
        "ra_dc_gain",
        "ra_slew_v_s",
        "adc2_response_model",
    ],
)
def test_selected_conversion_never_interprets_missing_models_as_ideal(field):
    with pytest.raises(ValueError, match=field):
        replace(_ideal_conversion(), **{field: None}).require_runtime(
            fs=40e6, reference_active=False
        )


@pytest.mark.parametrize(
    "field", ["rdac_event_times_s", "reference_accurate_start_s", "fine_reference_tau_s"]
)
def test_active_reference_requires_its_own_event_and_switch_contract(field):
    parameters = replace(
        _ideal_conversion(),
        rdac_event_times_s=(0.0, 1e-9, 4e-9),
        reference_accurate_start_s=6e-9,
        fine_reference_tau_s=1e-9,
    )
    with pytest.raises(ValueError, match=field):
        replace(parameters, **{field: None}).require_runtime(fs=40e6, reference_active=True)


@pytest.mark.parametrize("scale", [-3.0, 0.0, np.nan, np.inf])
def test_invalid_full_scale_in_a_serialized_register_image_is_rejected(
    identity_record_factory, explicit_format, scale
):
    data = identity_record_factory(bits=3)
    image = FixedPointReconstructor.from_weights(
        data.spec, np.array([[0.25, 0.35, 0.4]]), 0.0, format=explicit_format
    ).to_dict()
    image["spec"]["v_fs"] = scale
    with pytest.raises(ValueError):
        FixedPointReconstructor.from_dict(image)


@pytest.mark.parametrize("lower,upper", [(1.0, 1.0), (2.0, 1.0), (np.nan, 2.0)])
def test_invalid_adc2_voltage_bounds_in_register_images_are_rejected(
    identity_record_factory, explicit_format, lower, upper
):
    data = identity_record_factory(bits=3)
    image = FixedPointReconstructor.from_weights(
        data.spec, np.array([[0.25, 0.35, 0.4]]), 0.0, format=explicit_format
    ).to_dict()
    image["spec"]["adc2_v_min"], image["spec"]["adc2_v_max"] = lower, upper
    with pytest.raises(ValueError):
        FixedPointReconstructor.from_dict(image)


def test_summary_keeps_constant_error_and_invalid_samples_without_renaming_rms_to_sigma():
    n, fs, k = 256, 40e6, 17
    error = np.full(n, 1e-3)
    bad = np.zeros(n, dtype=bool)
    bad[[0, -1]] = True
    record = SimpleNamespace(
        out=np.sin(2 * np.pi * k * np.arange(n) / n) + error,
        err=error,
        residue=np.zeros(n),
        adc2_over=bad,
        ra_sat=np.zeros(n, dtype=bool),
        analog_overflow=bad,
        effective_config={"output_kind": "nominal_scalar_diagnostic"},
    )
    result = summarize(record, fs, fs * k / n, 3.0, 6 / 2**20)
    assert result["err_mean_V"] == pytest.approx(1e-3)
    assert result["err_rms_V"] == pytest.approx(1e-3)
    assert np.std(error) <= 4 * np.finfo(float).eps * abs(error[0])
    assert result["raw_samples"] == n and result["invalid_raw_count"] == 2
    assert result["overflow_rate_adc2"] == 2 / n
    assert result["integer_output_status"] == "NOT_RUN"


@pytest.mark.parametrize("field", ["driver_noise_rms", "ra_out_noise_rms"])
@pytest.mark.parametrize("value", [-1e-3, np.nan, np.inf])
def test_invalid_active_noise_amplitude_is_rejected_not_silently_disabled(
    explicit_pipeline_factory, field, value
):
    cfg, controls = explicit_pipeline_factory(n_samples=4)
    changes = {field: value, "seed": 3}
    if field == "driver_noise_rms":
        changes["driver_noise_distribution"] = "gaussian_white"
    else:
        changes.update(ra_enable_noise=True, ra_noise_distribution="gaussian_white")
    with pytest.raises(ValueError):
        run_pipeline(replace(cfg, **changes), dc_input(0.1), 4, **controls)


def test_undefined_layout_gradient_is_not_invented_from_a_field_or_default_coordinates():
    with pytest.raises(TypeError):
        Config.from_dict({"mismatch_gradient": [0.001, 0.0]})
