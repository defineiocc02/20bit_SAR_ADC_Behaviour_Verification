"""Independent boundary, clock-budget and measurement-contract regressions."""

from dataclasses import replace

import numpy as np
import pytest

from adi_model import Config, cli, dc_input, run_sim, sine_input
from adi_model import __main__ as module_cli
from adi_model.closure_experiments import _error_statistics
from adi_model.conversion import ConversionParameters
from adi_model.fixed_point import FixedPointReconstructor
from adi_model.pipeline import run_pipeline
from adi_model.ref_track import RefTrackRun, reference_precision_bits
from adi_model.weight_calibration import CalibrationSpec


@pytest.mark.parametrize("command", ["run-all", "make-report"])
@pytest.mark.parametrize("implicit_argv", [False, True])
def test_module_dispatch_forwards_the_existing_cli_options(
    command, implicit_argv, tmp_path, monkeypatch
):
    observed = []
    destination = str(tmp_path / "output with spaces")
    monkeypatch.setattr(cli, "_run_script", lambda name, out: observed.append((name, out)) or 0)
    argv = [command, "--results-dir", destination]
    if implicit_argv:
        monkeypatch.setattr("sys.argv", ["python -m adi_model", *argv])
    assert module_cli.main(None if implicit_argv else argv) == 0
    expected = "run_all.py" if command == "run-all" else "make_report.py"
    assert observed == [(expected, destination)]


def test_module_command_help_includes_the_real_command_options(capsys):
    with pytest.raises(SystemExit) as exc:
        module_cli.main(["run-all", "--help"])
    assert exc.value.code == 0
    assert "--results-dir" in capsys.readouterr().out


@pytest.mark.parametrize("timing", ["scaled", "rtl16"])
@pytest.mark.parametrize("fine_tau", [None, 3e-9])
def test_reference_pulse_convolution_covers_the_complete_clock_budget(timing, fine_tau):
    cfg = Config.rtl_structural(
        fs=10e6,
        mismatch_enable=False,
        enable_sampling_noise=False,
        ra_enable_noise=False,
        sadc_rdac_gain_mismatch=0,
        dyn_ref_settling=True,
    )
    if timing == "scaled":
        cfg = Config.paper_literal(
            fs=10e6,
            mismatch_enable=False,
            enable_sampling_noise=False,
            ra_enable_noise=False,
            sadc_rdac_gain_mismatch=0,
            dyn_ref_settling=True,
            rdac_bitwise_loading=True,
        )
    cfg = replace(
        cfg,
        conversion=replace(
            cfg.conversion,
            fine_reference_tau_s=fine_tau,
            ra_bandwidth_hz=120e6,
            adc2_wide_bandwidth_hz=80e6,
        ),
    )
    result = run_pipeline(cfg, sine_input(1.2, cfg.fs / 16), 5)
    trace = result.conversion_trace
    entry = cfg.conversion.quantizer_time_s
    amplification = cfg.dyn_t_conv_frac / cfg.fs
    aperture = entry + amplification
    coarse_tau = cfg.dyn_tau_ref
    final_decay = np.exp(-amplification / (fine_tau or coarse_tau))
    previous_end = np.zeros(2)
    for sample in range(5):
        # Closed-form impulse sum at RA entry, including any pre-RA gap.
        inherited = previous_end * np.exp(-(1 / cfg.fs - aperture + entry) / coarse_tau)
        if sample == 0:
            inherited[:] = 0
        pulse_sum = np.sum(
            -trace.reference_charge_c[sample]
            / cfg.dyn_c_decouple
            * np.exp(-(entry - trace.event_times_s[:, None]) / coarse_tau),
            axis=0,
        )
        expected_entry = inherited + pulse_sum
        expected_end = expected_entry * final_decay
        np.testing.assert_allclose(trace.phase_reference_v[sample, 0], expected_entry, atol=1e-18)
        np.testing.assert_allclose(trace.reference_error_v[sample], expected_end, atol=1e-18)
        previous_end = expected_end
    # No RA or ADC2 integration occurs before the declared RA entry.
    assert trace.phase_ra_v[0, 0] == 0
    assert trace.phase_adc2_v[0, 0] == 0
    np.testing.assert_array_equal(trace.phase_ra_v[1:, 0], trace.ra_v[:-1])
    np.testing.assert_array_equal(trace.phase_adc2_v[1:, 0], trace.adc2_v[:-1])


@pytest.mark.parametrize(
    "override",
    [
        {"dyn_input_settling": True},
        {"dyn_ref_settling": True},
        {"dyn_crosstalk": True},
        {"slice_bw_spread": 0.1},
        {"slice_timing_skew_s": 1e-12},
        {"slice_offset_sigma_v": 1e-3},
        {"conversion": ConversionParameters(ra_bandwidth_hz=120e6)},
    ],
)
def test_unary_runner_rejects_split_physics_requests_at_the_entry(override):
    with pytest.raises(ValueError, match="static unary runner.*use run_pipeline"):
        run_sim(replace(Config(), **override), dc_input(0), 4)


@pytest.mark.parametrize("field", ["dyn_input_settling", "dyn_ref_settling", "dyn_crosstalk"])
def test_physical_split_runner_still_accepts_its_supported_requests(field):
    cfg = Config.paper_literal(
        mismatch_enable=False, enable_sampling_noise=False, ra_enable_noise=False, **{field: True}
    )
    assert len(run_pipeline(cfg, dc_input(0.1), 4).out) == 4


@pytest.mark.parametrize("scale", [-3.0, 0.0, np.nan, np.inf])
def test_invalid_full_scale_in_a_serialized_register_image_is_rejected(scale):
    spec = CalibrationSpec.from_config(Config.paper_literal())
    image = FixedPointReconstructor.from_weights(spec, np.full(spec.shape, 0.0625)).to_dict()
    image["spec"]["v_fs"] = scale
    with pytest.raises(ValueError, match="positive finite full-scale"):
        FixedPointReconstructor.from_dict(image)


@pytest.mark.parametrize("lower, upper", [(1.0, 1.0), (2.0, 1.0), (np.nan, 2.0)])
def test_invalid_adc2_voltage_bounds_in_register_images_are_rejected(lower, upper):
    spec = CalibrationSpec.from_config(Config.paper_literal())
    image = FixedPointReconstructor.from_weights(spec, np.full(spec.shape, 0.0625)).to_dict()
    image["spec"]["adc2_v_min"], image["spec"]["adc2_v_max"] = lower, upper
    with pytest.raises(ValueError, match="increasing ADC2"):
        FixedPointReconstructor.from_dict(image)


def test_rms_keeps_a_constant_error_that_standard_deviation_removes():
    statistics = _error_statistics(np.full(8, 1e-3))
    assert statistics["rms_v"] == pytest.approx(1e-3)
    assert statistics["std_v"] == 0
    assert statistics["mean_v"] == pytest.approx(1e-3)
    run = RefTrackRun(np.full(8, 1e-3), np.zeros((8, 2)), np.zeros(8), np.zeros(8), None)
    assert run.err_final_bits(3) == pytest.approx(reference_precision_bits(1e-3, 3))
    assert np.isfinite(run.err_final_bits(3))


def test_stage12_reports_charge_units_and_preserves_the_deprecated_value():
    from adi_model.experiments import stage12_split_arch

    result = stage12_split_arch(Config(), n=4096)["charge_closure"]
    charge = result["closedform_vs_nodalmatrix_max_C"]
    assert charge == result["closedform_vs_nodalmatrix_max_V"]
    declaration = result["deprecated_fields"]["closedform_vs_nodalmatrix_max_V"]
    assert declaration["actual_unit"] == "C"
    assert declaration["replacement"] == "closedform_vs_nodalmatrix_max_C"
    assert "fV" not in result["判据"]


@pytest.mark.parametrize("field", ["driver_noise_rms", "ra_out_noise_rms", "mismatch_sigma0"])
@pytest.mark.parametrize("value", [-1e-3, np.nan, np.inf])
def test_invalid_noise_and_mismatch_amplitudes_do_not_silently_disable_a_source(field, value):
    with pytest.raises(ValueError, match=field + ".*finite and nonnegative"):
        run_pipeline(replace(Config.paper_literal(), **{field: value}), dc_input(0.1), 4)


def test_static_dem_record_keeps_a_complete_state_cycle_after_warmup():
    from adi_model.experiments import _dem_rotation_period
    from adi_model.mapper import N_DEM_STATES, dem_state_sequence

    cfg = Config(dem_enable=True)
    period = _dem_rotation_period(cfg)
    assert period == N_DEM_STATES == 512
    banks = np.arange(2 * period + 1) % 2
    states = dem_state_sequence(len(banks), cfg, banks)
    kept_banks, kept_states = banks[1:], states[1:]
    for bank in (0, 1):
        selected = kept_states[kept_banks == bank]
        assert len(selected) == period
        np.testing.assert_array_equal(np.sort(selected), np.arange(N_DEM_STATES))


@pytest.mark.parametrize("gradient", [(0.01, 0), (0, 0.01)])
def test_physical_pool_refuses_an_undefined_layout_gradient(gradient):
    cfg = Config.paper_literal(mismatch_gradient=gradient)
    with pytest.raises(ValueError, match="mismatch_gradient requires an explicit aggregate chip"):
        run_pipeline(cfg, dc_input(0.1), 4)


def test_explicit_aggregate_gradient_is_supported_without_inventing_pool_coordinates():
    from adi_model.dac_arch import build_split_chip

    cfg = Config.paper_literal(mismatch_gradient=(0.001, 0))
    chip = build_split_chip(cfg)
    result = run_pipeline(cfg, dc_input(0.1), 4, chip=chip)
    expected = np.concatenate((chip.C_main, chip.C_sub)) / cfg.n_active
    np.testing.assert_array_equal(result.pool.unit_caps[0], expected)


def test_runtime_gradient_override_cannot_silently_reuse_a_coordinate_free_pool():
    cfg = Config.paper_literal()
    baseline = run_pipeline(cfg, dc_input(0.1), 4)
    assert baseline.pool._mismatch_gradient == (0.0, 0.0)
    with pytest.raises(
        ValueError, match="mismatch_gradient cannot change an existing physical pool"
    ):
        run_pipeline(
            replace(cfg, mismatch_gradient=(0.001, 0)), dc_input(0.1), 4, pool=baseline.pool
        )
