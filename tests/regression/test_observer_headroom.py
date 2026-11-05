"""保留 v6.2 重构时的观测校正余量及旧架构回归。

旧 KTC 校正曾在 ADC2 量化前相减，使频率相关项占用后级窗口。历史 5 MHz
单音记录为 10.3% 溢出和 1.19 mV RMS，并随频率接近 Nyquist 继续增大，
处理口径见 docs/adr/0006；TestObserverCorrectionIsDigital 检查校正位于数字域。

TestFirstStageObservableRank 检查旧假设下 rank(U)=n_units_sig/n_active，
扫掠码数由 2**b1 决定，不固定为 64。TestSubWeightInducedINL 比较历史 7 位
粗码加 2 位扰动假设中的子节点寄生：子 DAC 被码调制后产生周期 2 的确定性
误差，无法靠当时 DEM 平均消除。上述假设与接口仅作历史回归，不作为当前架构需求。
"""

from __future__ import annotations

import numpy as np
import pytest

from adi_model import Config
from adi_model.adc2 import ADC2
from adi_model.chip import build_chip
from adi_model.sampler import sine_input
from adi_model.sim import initialize_state, run_sim


class TestObserverCorrectionIsDigital:
    """检查旧观测校正发生在量化后，不占用 ADC2 模拟窗口。"""

    def test_zero_correction_is_identity(self, cfg):
        adc2 = ADC2(cfg)
        v = np.linspace(cfg.adc2_v_min, cfg.adc2_v_max, 257)
        q_plain, over_plain = adc2.quantize(v)
        q_corr, over_corr = adc2.quantize_with_correction(v, np.zeros_like(v))
        assert np.array_equal(q_plain, q_corr)
        assert np.array_equal(over_plain, over_corr)

    def test_huge_correction_does_not_overflow(self, cfg):
        """检查远大于量化窗口的数字校正项不会引起 ADC2 输入溢出。"""
        adc2 = ADC2(cfg)
        v = np.linspace(cfg.adc2_v_min, cfg.adc2_v_max, 257)
        huge = np.full_like(v, 100.0 * (cfg.adc2_v_max - cfg.adc2_v_min))
        _, over = adc2.quantize_with_correction(v, -huge)
        assert not over.any(), "correction must be applied after quantisation"

    def test_plain_quantize_would_have_overflowed(self, cfg):
        """反向检查：同一夹具按旧的量化前相减方式确实越出窗口。"""
        adc2 = ADC2(cfg)
        v = np.full(16, cfg.adc2_v_max)
        corr = np.full(16, -0.5)  # 原历史观察夹具的 |kappa*v_N| 约为 0.3 V。
        _, over_old = adc2.quantize(v - corr)  # v6.1 的量化前相减形式。
        assert over_old.all()
        _, over_new = adc2.quantize_with_correction(v, corr)
        assert not over_new.any()

    @pytest.mark.parametrize("fin_div", [2.0, 8.0])
    def test_no_overflow_with_ktc_at_worst_case(self, cfg, fin_div):
        """检查所列满幅及接近 Nyquist 夹具中启用旧 KTC 后的节点有效性。"""
        from adi_model.experiments import _clone

        c = _clone(
            cfg,
            ktc_enable=True,
            ktc_beta_error=0.2,
            mismatch_enable=False,
            dem_enable=False,
            calibration="none",
            ra_enable_noise=True,
            enable_sampling_noise=True,
        )
        n = 2**13
        r = run_sim(
            c,
            sine_input(1.0 * c.v_fs, c.fs / fin_div),
            n,
            chip=build_chip(c),
            state=initialize_state(c),
            rng=np.random.default_rng(3),
        )
        assert float(np.mean(r.adc2_over)) == 0.0
        assert float(np.mean(r.ra_sat)) == 0.0

    def test_beta_calibration_recovers_the_target(self, cfg):
        """检查旧 beta 标定相对目标误差小于 1%；历史缺陷记录为 10.7%。"""
        from adi_model.experiments import _clone
        from adi_model.sim import run_with_calibration

        n = 2**13
        c = _clone(
            cfg,
            ktc_enable=True,
            ktc_beta_error=0.2,
            mismatch_enable=False,
            dem_enable=False,
            calibration="gain_beta",
            enable_sampling_noise=False,
            ra_enable_noise=False,
        )
        rb = run_with_calibration(
            c, sine_input(0.9 * c.v_fs, c.fs * 1024 / n), n, rng=np.random.default_rng(21)
        )
        target = (cfg.g0 / cfg.ktc_gain_n) / 1.2
        assert abs(rb.state.kappa / target - 1.0) < 0.01


class TestFirstStageObservableRank:
    """检查旧可观测秩由所用口径推导，不固定到某一个 b1。"""

    @pytest.mark.parametrize("constructor", ["default", "legacy_v61"])
    def test_rank_equals_intra_slice_positions(self, constructor):
        from adi_model.mapper import N_DEM_STATES as NS
        from adi_model.mapper import unit_rank_arrays

        cfg = Config() if constructor == "default" else Config.legacy_v61()
        slice_rank, unit_idx = unit_rank_arrays(cfg)
        n_u = cfg.n_units_sig
        codes = np.arange(0, 2**cfg.b1)  # 码数由 b1 推导，不固定为 64。
        U = np.zeros((codes.size * NS, n_u), dtype=np.float32)
        row = 0
        for k_code in codes:
            ku = int(k_code * cfg.units_per_lsb1)
            for sid in range(NS):
                sr, ui = slice_rank[sid], unit_idx[sid]
                U[row, sr[:ku] * cfg.n_unit_per_slice + ui[:ku]] = 1.0
                row += 1
        assert int(np.linalg.matrix_rank(U)) == n_u // cfg.n_active

    def test_code_sweep_must_span_the_reading(self):
        """保留历史 b1=7 时仅扫 64 个码会得到错误秩的反例。"""
        from adi_model.mapper import N_DEM_STATES as NS
        from adi_model.mapper import unit_rank_arrays

        cfg = Config()
        slice_rank, unit_idx = unit_rank_arrays(cfg)
        n_u = cfg.n_units_sig

        def rank_for(n_codes):
            U = np.zeros((n_codes * NS, n_u), dtype=np.float32)
            row = 0
            for k_code in range(n_codes):
                ku = int(k_code * cfg.units_per_lsb1)
                for sid in range(NS):
                    sr, ui = slice_rank[sid], unit_idx[sid]
                    U[row, sr[:ku] * cfg.n_unit_per_slice + ui[:ku]] = 1.0
                    row += 1
            return int(np.linalg.matrix_rank(U))

        assert rank_for(2**cfg.b1) == n_u // cfg.n_active
        assert rank_for(64) != n_u // cfg.n_active  # 对照旧的固定码数。


class TestSubWeightInducedINL:
    """比较旧子节点寄生误差的解析值与所选探针结果。"""

    def test_prediction_matches_measurement(self, cfg):
        from adi_model.experiments import sub_weight_inl_probe

        out = sub_weight_inl_probe(cfg)
        assert out["dem_states_averaged"] == 512
        assert out["sub_code_modulated"] is True
        assert out["ratio"] == pytest.approx(1.0, abs=0.25)

    def test_legacy_reading_does_not_excite_the_sub_dac(self):
        from adi_model.experiments import sub_weight_inl_probe

        out = sub_weight_inl_probe(Config.legacy_v61())
        assert out["dem_states_averaged"] == 512
        assert out["sub_code_modulated"] is False
        assert out["measured_dac_only_INL_max_LSB20"] == pytest.approx(0.0, abs=1e-9)


@pytest.mark.parametrize("constructor", [Config, Config.legacy_v61])
def test_sub_weight_probe_has_no_unrelated_pipeline_or_rho_work(constructor, monkeypatch):
    """检查聚焦 DAC 的探针不会触发无关的完整流水线或 rho 扫掠。"""
    from adi_model import experiments, sim_split

    def reject_unrelated_work(*args, **kwargs):
        raise AssertionError("sub-weight probe must not start an ADC/rho scan")

    monkeypatch.setattr(experiments, "stage13_dynamics", reject_unrelated_work)
    monkeypatch.setattr(sim_split, "run_sim_split", reject_unrelated_work)
    result = experiments.sub_weight_inl_probe(constructor())
    assert result["dem_states_averaged"] == 512
    assert "measured_chain_base_row_LSB20" not in result
