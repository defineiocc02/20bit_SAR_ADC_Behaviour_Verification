"""专利 [14] US 10,826,519 B1 -- 低功耗参考方案的机制级测试（A06 缺口）。

覆盖：
1. **整定行为**：阈值逼零积分使周期末误差几何收敛；整定周期数与
   [14] 披露的"5 或 6 个转换周期"量级一致（g=0.8 为 [假设]，量级
   一致是结构结果，不是拟合主张）；
2. **电荷核算**：S1 闭合时外部参考只需补 C·E，且随整定衰减
   （专利的低功耗论据）；
3. **A06 结构**：粗判决位试承载大参考误差（容忍度 Δ1 量级），
   低位试的参考误差被压到 20b 级——"参考 ~15b 时 RA 起动、
   尾段才到 20b"的结构解释；
4. **精度换算**与入口拒绝。
"""

import numpy as np
import pytest

from adi_model.ref_track import (
    RefTrackConfig,
    RefTrackSim,
    reference_precision_bits,
)


@pytest.fixture(scope="module")
def run():
    return RefTrackSim(RefTrackConfig()).run()


class TestSettling:
    def test_settles_within_disclosed_order(self, run):
        """[14]：'usually able to settle within 5 or 6 conversion cycles'。"""
        assert run.settle_cycle is not None
        assert run.settle_cycle <= 8

    def test_error_converges_and_stays(self, run):
        cfg = RefTrackConfig()
        tail = run.err_end_of_cycle[-10:]
        assert np.all(np.abs(tail) < cfg.settle_eps_v)

    def test_threshold_is_driven_towards_zero(self, run):
        """[14] 的阈值逼零描述：末段 |θ| 远小于首段周期误差（结构主张）。

        注：稳态阈值收敛到 −r（充电器跟随滞后，θ* = −r 的不动点），
        不恒等于 0——口径不夸大，只主张"逼零"的相对量级。
        """
        e_first_cycle = float(np.mean(np.abs(run.err_per_trial[0])))
        assert abs(run.threshold[-1]) < 0.01 * e_first_cycle

    def test_external_charge_shrinks_after_settling(self, run):
        """低功耗论据：S1 闭合时外部参考只补 C·E，整定后趋近于零。"""
        m = len(run.charge_from_ext)
        q_head = float(np.mean(run.charge_from_ext[: m // 4]))
        q_tail = float(np.mean(run.charge_from_ext[-m // 4 :]))
        assert q_tail < 0.01 * q_head


class TestA06Structure:
    @staticmethod
    def _steady(run):
        """稳态窗口：剔除起动瞬态（与 a06_analysis 同口径，后 3/4 周期）。"""
        w = run.err_per_trial
        return w[len(w) // 4 :]

    def test_early_trials_carry_the_reference_error(self, run):
        """粗判决位试的参考误差 >> 低位试（自愈窗口容忍前者、后者需要精度）。"""
        w = self._steady(run)
        e_first = float(np.mean(np.abs(w[:, 0])))
        e_last = float(np.mean(np.abs(w[:, -1])))
        assert e_first > 10.0 * e_last

    def test_window_margin_ratio_positive(self, run):
        """首试实际误差必须落在 Δ1/2 容忍窗内（否则粗判决会被参考误差打翻）。"""
        assert run.a06["window_margin_ratio"] > 1.0

    def test_precision_grows_from_coarse_to_fine(self, run):
        cfg = RefTrackConfig()
        w = self._steady(run)
        e_first = float(np.mean(np.abs(w[:, 0])))
        e_last = float(np.mean(np.abs(w[:, -1])))
        b_first = reference_precision_bits(e_first, cfg.v_ext)
        b_last = reference_precision_bits(e_last, cfg.v_ext)
        # 结构主张：参考精度需求从粗级量级爬升到 20b 量级
        assert b_first < 12.0
        assert b_last > 19.0

    def test_end_to_end_precision_bits(self, run):
        assert run.err_final_bits(3.0) > 19.0


class TestPrecisionConversion:
    def test_reference_precision_bits_identity(self):
        # σ = span/(2√12·2^b) 的反解：span=3, 20b -> σ ≈ 21.6 µV
        sigma_20b = 3.0 / (2.0 * np.sqrt(12.0) * 2.0**20)
        assert reference_precision_bits(sigma_20b, 3.0) == pytest.approx(20.0)
        # 更大误差 -> 更少位数
        assert reference_precision_bits(sigma_20b * 8, 3.0) == pytest.approx(17.0)

    def test_nonpositive_refused(self):
        with pytest.raises(ValueError):
            reference_precision_bits(0.0, 3.0)
        with pytest.raises(ValueError):
            reference_precision_bits(1e-6, 0.0)


class TestValidation:
    def test_nonpositive_config_refused(self):
        with pytest.raises(ValueError, match="必须为正"):
            RefTrackConfig(i_charge_max=0.0).validated()

    def test_sar_load_profile_shape(self):
        sim = RefTrackSim(RefTrackConfig(n_bits=12))
        dq = sim._sar_load_profile()
        assert dq.shape == (12,)
        # SAR 二进制结构：MSB 位试抽取最大，逐位减半
        assert dq[0] == pytest.approx(2.0 * dq[1])
        assert np.all(np.diff(dq) < 0)
