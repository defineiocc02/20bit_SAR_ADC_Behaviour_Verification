"""把 F1/F2/F3/F4/F8/F9/F10 的审计意图接到当前显式接口。

旧7+2/6bit、自动DR反推噪声、unary、KTC与旧Monte-Carlo绘图入口已按授权删除，
不把旧实现细节当作当前要求。这里检查真实转换、因果/统计及证据分级；
Fig19完整64字/整数/逐事件验收另由integration测试承担。
"""

from __future__ import annotations

from dataclasses import replace
from fractions import Fraction

import numpy as np
import pytest

from adi_model.behavioral.adc2 import ADC2
from adi_model.behavioral.low_frequency_noise import BandLimitedFlicker
from adi_model.behavioral.ra import ResidueAmplifier
from adi_model.behavioral.sadc import build_first_stage_quantizer
from adi_model.behavioral.sampler import dc_input, sine_input
from adi_model.control.dither import Fig19DitherProgram, Fig19StaticSpec
from adi_model.defaults import DefaultParameters, default_model, simulate
from adi_model.metrics.metrics import inl_from_mean_error, sine_fit_metrics, static_test, summarize
from adi_model.provenance import GradingError, SourceGrade, annotate_config
from adi_model.simulation.config import Config, ConfigError
from adi_model.simulation.serialization import result_document


@pytest.fixture
def quiet_nominal_model():
    """显式关闭噪声/制造压力的工程夹具，不作为论文测量值。"""
    return default_model(
        parameters=DefaultParameters(unit_mismatch_std=0, bridge_mismatch_std=0),
        config_overrides={"enable_sampling_noise": False, "ra_enable_noise": False},
    )


class TestF1FirstStageReading:
    """F1：九位未知输入判决独立于已知dither字宽，后端必须显式绑定。"""

    def test_actual_quantizer_resolves_512_unknown_input_bins(self, configuration_inputs):
        adc = build_first_stage_quantizer(configuration_inputs)
        centres = -3 + (np.arange(512) + 0.5) * (6 / 512)
        code, over = adc.convert_codes(centres)
        assert np.array_equal(code, np.arange(512))
        assert not over.any()
        assert configuration_inputs.delta1 == pytest.approx(6 / 512)

    @pytest.mark.parametrize("bits", [6, 7, 8, 10])
    def test_old_decision_counts_are_rejected(self, configuration_inputs, bits):
        cfg = replace(configuration_inputs, b1=bits)
        with pytest.raises(ConfigError, match="b1 must be 9"):
            cfg.check_legal()

    @pytest.mark.parametrize("field", ["stage1_reading", "dac_arch", "dither_enhancement_bits"])
    def test_removed_architecture_knobs_cannot_be_silently_accepted(self, field):
        with pytest.raises(TypeError, match=field):
            Config.from_dict({field: "legacy"})

    def test_empty_config_does_not_invent_backend_inputs(self):
        cfg = Config()
        assert cfg.adc2_n_bits is None and cfg.adc2_v_min is None
        with pytest.raises(ConfigError, match="missing adc2_n_bits"):
            cfg.check_legal()

    def test_explicit_backend_window_is_not_rederived_from_gain(self, configuration_inputs):
        cfg = replace(configuration_inputs, adc2_v_min=-0.2, adc2_v_max=0.7)
        cfg.check_legal()
        altered_gain = replace(cfg, g0=17)
        assert ADC2(cfg).step == ADC2(altered_gain).step == pytest.approx(0.9 / 4096)
        assert (ADC2(altered_gain).vmin, ADC2(altered_gain).vmax) == (-0.2, 0.7)


class TestF2SliceCausality:
    """F2：组间不相交、跨周期归属、实际持有sample ID必须分别成立。"""

    def test_actual_raw_charge_is_owned_by_its_sample(self, quiet_nominal_model):
        raw = quiet_nominal_model.simulate(dc_input(0.31), 128).raw
        assert np.array_equal(
            np.sort(raw.next_acq_slice_ids[:-1], axis=1),
            np.sort(raw.conv_slice_ids[1:], axis=1),
        )
        assert all(
            not set(conv) & set(acq)
            for conv, acq in zip(raw.conv_slice_ids, raw.next_acq_slice_ids, strict=True)
        )
        assert np.all(raw.held_sample_ids == raw.sample_id[:, None])
        assert raw.plan_valid.all()

    def test_causal_plan_is_not_a_fixed_group_placeholder(self, quiet_nominal_model):
        raw = quiet_nominal_model.simulate(dc_input(0.31), 128).raw
        assert len({tuple(sorted(row)) for row in raw.conv_slice_ids}) > 20
        assert len(np.unique(raw.conv_slice_ids)) == 18

    def test_actual_selected_capacitance_sets_the_physical_gain(self):
        run = simulate(
            dc_input(0.31),
            128,
            config_overrides={"enable_sampling_noise": False, "ra_enable_noise": False},
        )
        raw = run.raw
        assert np.ptp(raw.normalization_capacitance_f) > 0
        assert np.allclose(
            raw.g_vec,
            raw.normalization_capacitance_f / raw.pool.chip.require_feedback(),
            rtol=0,
            atol=1e-14,
        )
        assert raw.state.weight_calibration is None


class TestF3DitherUnits:
    """F3：固定点单位、原coarse与实际mask视图不可混用；不恢复旧range/4x分支。"""

    def test_program_uses_one_q32_source_for_both_ports(self):
        step_q = 2**24
        weights = tuple(round(Fraction(n * step_q, 7)) for n in (4, 2, 1))
        spec = Fig19StaticSpec(3.0, step_q, 32, weights, "subtract", "synthetic explicit binding")
        program = Fig19DitherProgram.from_words(np.array([0, 1, 8, 16, 32, 63]), spec=spec)
        assert program.lower_q[0] == program.total_q[0] == program.upper_code[0] == 0
        assert np.array_equal(program.total_q - program.lower_q, program.upper_code * step_q)
        assert np.array_equal(program.sadc_input_shift_q, -program.lower_q)
        assert np.array_equal(program.rdac_input_shift_q, -program.total_q)
        assert np.array_equal(program.known_common_q, program.rdac_input_shift_q)
        assert np.array_equal(program.lower_v, 3.0 * program.lower_q / 2**32)
        assert np.array_equal(program.total_v, 3.0 * program.total_q / 2**32)
        assert program.lower_q[-1] == 3 * step_q
        assert program.total_q[-1] == 8 * step_q
        assert not np.array_equal(program.total_q, 4 * program.lower_q)
        for bad_step in (0, -step_q):
            with pytest.raises(ConfigError, match="full-scale step"):
                replace(spec, step_q=bad_step)

    @pytest.mark.parametrize(
        "field", ["dither_mode", "dither_transfer_model", "dither_amplitude_lsb1"]
    )
    def test_old_mutually_exclusive_dither_controls_are_not_aliases(self, field):
        with pytest.raises(TypeError, match=field):
            Config.from_dict({field: "range"})

    def test_request_without_actual_program_is_rejected_at_runtime(self, quiet_nominal_model):
        model = default_model(config_overrides={"dither_enable": True})
        with pytest.raises(ConfigError, match="dither_words"):
            model.simulate(dc_input(0), 4)
        assert quiet_nominal_model.simulate(dc_input(0), 4).raw.dither_program is None


class TestF4NoiseBudgetIsAnAnchor:
    """F4：不允许通过目标DR反解来制造独立性能预测。"""

    def test_missing_active_noise_is_not_fitted_from_a_target(self, configuration_inputs):
        cfg = replace(configuration_inputs, ra_enable_noise=True)
        with pytest.raises(ConfigError, match="ra_out_noise_rms"):
            cfg.check_legal()
        with pytest.raises(ValueError, match="ra_out_noise_rms"):
            ResidueAmplifier(cfg)

    def test_default_noise_and_process_pressure_are_assumptions(self):
        model = default_model()
        ann = annotate_config(model.cfg)
        assert ann["ra_out_noise_rms"].grade is SourceGrade.ASSUMED
        with pytest.raises(GradingError):
            ann["ra_out_noise_rms"].require(SourceGrade.DISCLOSED)
        rows = model.inference_table()
        assert rows["ra_noise_rms_v"]["grade"] == "ASSUMED_NOISE_BUDGET"
        assert rows["unit_mismatch_std"]["grade"] == "ASSUMED_SENSITIVITY"

    def test_explicit_output_noise_is_not_reallocated_when_gain_changes(self):
        cfg = Config(
            ra_v_clip=1.0,
            ra_enable_noise=True,
            ra_out_noise_rms=1e-3,
            ra_noise_distribution="gaussian_white",
        )
        ra = ResidueAmplifier(cfg)
        a, sat_a = ra.evaluate(np.zeros(1024), np.random.default_rng(7), 32.0)
        b, sat_b = ra.evaluate(np.zeros(1024), np.random.default_rng(7), 16.0)
        expected = np.random.default_rng(7).normal(0, 1e-3, 1024)
        assert np.array_equal(a, expected) and np.array_equal(b, expected)
        assert not sat_a.any() and not sat_b.any()

    @pytest.mark.parametrize("field", ["target_dr_db", "pdk_sigma_est_ppm", "ra_autozero_cost_db"])
    def test_retired_headlines_are_not_forward_model_dials(self, field):
        with pytest.raises(TypeError, match=field):
            Config.from_dict({field: 94.6})


class TestF8EvidenceScope:
    """F8：均值/条件标准差、浮点诊断、正式整数输出使用不同证据口径。"""

    def test_static_statistics_keep_bias_separate_from_standard_deviation(self):
        levels = np.array([-1.0, 0, 1.0])
        errors = np.array([0.1, 0.3, 0.1, 0.3])
        result = static_test(lambda level, repeats: level + errors[:repeats], levels, 4)
        assert np.allclose(result["mu_e"], 0.2)
        assert np.allclose(result["sigma_e"], np.std(errors, ddof=1))
        assert np.sqrt(np.mean(errors**2)) > result["sigma_e"][0]
        assert set(result) == {"levels", "mu_e", "sigma_e"}

    def test_mean_curve_is_detrended_without_claiming_code_density(self):
        levels = np.linspace(-1, 1, 5)
        result = inl_from_mean_error(levels, 0.2 + 0.03 * levels + 0.01 * levels**2, 1e-3)
        assert np.max(np.abs(result["inl_v"])) > 0
        assert np.allclose(result["inl_lsb"], result["inl_v"] / 1e-3)
        assert not any("DNL" in name.upper() for name in result)

    def test_integer_output_is_real_but_not_a_20bit_accuracy_claim(
        self, quiet_nominal_model, explicit_format
    ):
        run = quiet_nominal_model.simulate(sine_input(1.0, 1e6), 128)
        assert np.issubdtype(run.codes.code.dtype, np.integer)
        assert np.all((run.codes.code >= 0) & (run.codes.code < 2**20))
        assert run.raw.state.weight_calibration is None
        with pytest.raises(ValueError, match="calibration"):
            run.raw.to_codes(format=explicit_format)
        summary = summarize(run.raw, run.raw.cfg.fs, 1e6, 3.0, 6 / 2**20)
        assert summary["integer_output_status"] == "NOT_RUN"
        assert summary["output_domain"] == "nominal_scalar_diagnostic"
        assert run.metadata["source_reproduction"] is False

    def test_reconstruction_cannot_rewrite_analog_or_change_the_output_contract(
        self, quiet_nominal_model
    ):
        run = quiet_nominal_model.simulate(sine_input(1.0, 1e6), 32)
        data = run.raw.digital_observation
        recorded = {
            name: getattr(run.raw, name).copy()
            for name in ("sampled_node_charge_c", "residue", "vra", "adc2_code", "fine")
        }
        decoder = quiet_nominal_model.nominal_decoder
        with pytest.raises(ValueError, match="unsupported fixed-point format"):
            replace(decoder.format, output_bits=16)
        # 必须与分析链同口径：2026-11-13 起 `reconstruct` 接受 `physical_gain`，
        # 用于把 fine 从 G_phys 口径换算到权重和口径（否则差约 3.1%）。
        # 本测试要比对的是"重构不改变输出契约"，因此要和 `simulate` 走同一条路径。
        full_words = decoder.reconstruct(
            data, physical_gain=quiet_nominal_model.state_estimated_gain(run.raw)
        )
        assert full_words.output_bits == 20
        assert np.array_equal(full_words.code, run.codes.code)
        assert np.array_equal(full_words.analog_overflow, run.codes.analog_overflow)
        for name, expected in recorded.items():
            assert np.array_equal(getattr(run.raw, name), expected)


class TestF9FlickerBandLimit:
    """F9：有限物理频带与观察时长分开；不恢复FFT清零/隐式漂移补丁。"""

    @staticmethod
    def _state():
        return BandLimitedFlicker.create(
            1e-12,
            40.0,
            0.1,
            np.random.default_rng(0),
            high_hz=40.0,
            modes=32,
            reference_node="explicit_test_node",
            distribution="gaussian",
        )

    def test_explicit_band_power_is_an_ensemble_quantity(self):
        state = self._state()
        assert np.all((state.frequency_hz >= 0.1) & (state.frequency_hz <= 40))
        assert state.expected_variance_v2 == pytest.approx(1e-12 * 40 * np.log(400))
        assert np.any(state.at_times(np.arange(1024) / 4000) != 0)

    def test_unresolved_short_record_does_not_erase_the_low_frequency_state(self):
        state = self._state()
        indices = np.arange(1024, dtype=np.int64)
        short = state.at_adc_indices(indices, 40e6)
        assert np.array_equal(short, state.at_times(indices / 40e6))
        assert np.any(short != 0)
        assert 40e6 / len(indices) > state.high_hz
        assert np.array_equal(short, state.at_adc_indices(indices, 40e6))

    @pytest.mark.parametrize(
        "change", [{"low_hz": None}, {"reference_node": ""}, {"distribution": None}]
    )
    def test_missing_physical_psd_contract_is_rejected(self, change):
        values = {
            "white_density_v2_hz": 1e-12,
            "corner_hz": 40,
            "low_hz": 0.1,
            "rng": np.random.default_rng(0),
            "high_hz": 40,
            "modes": 8,
            "reference_node": "test",
            "distribution": "gaussian",
        }
        with pytest.raises(ValueError):
            BandLimitedFlicker.create(**(values | change))


class TestF10MonteCarloProvenance:
    """F10：保留实际逐芯片记录；不复活已删除的历史MC绘图器或宣称PDK良率。"""

    def test_serialized_sample_summary_uses_the_actual_virtual_chips(self):
        model_results = [
            simulate(
                dc_input(0.31),
                16,
                seed=seed,
                config_overrides={"enable_sampling_noise": False, "ra_enable_noise": False},
            )
            for seed in range(6)
        ]
        measured = np.array(
            [np.sqrt(np.mean((run.codes.voltage - 0.31) ** 2)) for run in model_results]
        )
        document = result_document(
            {
                "scope": "six actual engineering virtual-chip runs",
                "rms_per_chip_v": measured,
                "mean_v": measured.mean(),
                "std_v": measured.std(),
                "yield_claim": False,
            }
        )
        assert np.array_equal(np.asarray(document["rms_per_chip_v"]), measured)
        assert len(document["rms_per_chip_v"]) == 6
        assert document["mean_v"] == pytest.approx(measured.mean())
        assert document["std_v"] == pytest.approx(measured.std())
        assert document["yield_claim"] is False
        assert len({tuple(run.codes.code) for run in model_results}) > 1


class TestExtraLowFrequencyMetrics:
    """保留低频/频带边缘不抛异常及谐波不可分辨标记的回归。"""

    @pytest.mark.parametrize("frequency", [1e3, 5e3, 1e5, 1e6, 5e6, 19e6])
    def test_known_sine_fit_handles_low_and_nyquist_adjacent_inputs(self, frequency):
        fs, n = 40e6, 2**14
        values = sine_input(1.0, frequency)(np.arange(n) / fs)
        result = sine_fit_metrics(values, fs, frequency)
        assert np.isfinite(result["SNDR_dB"])
        assert result["SNDR_dB"] > 60
        if frequency == 1e3:
            assert result["harmonics_reliable"] is False

    def test_band_search_clamps_both_record_edges(self):
        from adi_model.metrics.metrics import _band_max

        spectrum = np.arange(32, dtype=float)
        for centre, expected in ((0, 4.0), (1, 5.0), (31, 31.0)):
            assert _band_max(spectrum, centre, 4) == expected
        spectrum[0] = 1000.0
        assert _band_max(spectrum, 31, 4) == 31.0
