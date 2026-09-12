"""专利 [12] US 10,707,889 B1 -- 跟踪相位机制的机制级测试。

覆盖四层契约：
1. **信息流**：track_other 的保持值必须来自**另一颗** sub-ADC 的最近
   转换结果（专利核心主张），不是自己的上一拍；
2. **专利背景的算术**：近 Nyquist 下"保持自己上一拍"比"复位到中点"
   更差（[12] 背景段 Q = 2·A·C_IN 论证），而 track_other 两头都好；
3. **加权跟踪**：保持值 = 最近 N 次转换的加权组合（披露例 10/30/60%）；
4. **标度律**：kickback 电荷 -> 滤波器带宽上限 ∝ q、驱动噪声 ∝ sqrt(q)。
"""

import numpy as np
import pytest

from adi_model.config import Config
from adi_model.interleave_tracking import (
    InterleavedSAR,
    TrackPolicy,
    kickback_filter_bw,
)


def _slow_sine(cfg: Config, cycles_per_period: float = 64.0, amp: float = 0.5):
    def fn(t):
        return amp * np.sin(2 * np.pi * cfg.fs * np.asarray(t, dtype=float) / cycles_per_period)

    return fn


def _fast_sine(cfg: Config, frac_nyquist: float = 0.45, amp: float = 0.5):
    def fn(t):
        return amp * np.sin(2 * np.pi * frac_nyquist * cfg.fs * np.asarray(t, dtype=float))

    return fn


def _run(cfg, policy, input_fn, n=192, n_adcs=3, seed=0):
    sim = InterleavedSAR(
        cfg, n_adcs=n_adcs, policy=policy, input_fn=input_fn, n_cycles=n, seed=seed
    )
    return sim.run()


class TestTrackPolicyValidation:
    def test_illegal_mode_is_refused(self):
        with pytest.raises(ValueError, match="不是已实现取值"):
            TrackPolicy(mode="track_mid").validated()

    def test_weighted_requires_matching_weights(self):
        with pytest.raises(ValueError, match="至少要有"):
            TrackPolicy(mode="track_other_weighted", n_weighted=3, weights=(0.6,)).validated()

    def test_weighted_rejects_negative_weights(self):
        with pytest.raises(ValueError, match="非负"):
            TrackPolicy(mode="track_other_weighted", n_weighted=2, weights=(0.8, -0.1)).validated()


class TestInformationFlow:
    """专利核心主张：跟踪值来自另一颗 ADC 的最近转换结果。"""

    def test_track_other_source_is_a_different_adc(self):
        cfg = Config()
        res = _run(
            cfg,
            TrackPolicy(mode="track_other", randomize=False),
            _slow_sine(cfg),
        )
        # 确定性轮转：第 1 拍起，上一拍的采集者（≠本拍采集者）必有结果
        has_source = res.source_adc >= 0
        assert has_source[1:].all()
        # 有来源的拍：来源 sub-ADC 一定不是本拍采集者
        valid = has_source
        assert np.all(res.source_adc[valid] != res.acquired_adc[valid])
        # 且信息来源至少是 1 拍之前（"most recent" 的时序下界）
        assert np.all(res.source_age[valid] >= 1)

    def test_track_other_value_equals_most_recent_other_result(self):
        """确定性轮转下逐拍重构期望值：v_hold = 最近一次**别人**的转换。"""
        cfg = Config()
        n_adcs, n = 3, 96
        fn = _slow_sine(cfg)
        res = _run(
            cfg,
            TrackPolicy(mode="track_other", randomize=False),
            fn,
            n=n,
            n_adcs=n_adcs,
        )
        t = np.arange(n) / cfg.fs
        x = np.broadcast_to(np.asarray(fn(t), dtype=float), (n,))
        # 确定性轮转：cycle i 的转换者 = order[(i+1)%n] = (i+1) mod 3 的 sub-ADC，
        # 采集者 = order[i] = i mod 3 -> 转换者 != 采集者恒成立，
        # 因此"最近的别人结果"就是上一拍的转换结果 x[i-1]。
        for i in range(1, n):
            assert res.v_hold[i] == pytest.approx(x[i - 1], abs=1e-15)

    def test_track_own_uses_own_last_result(self):
        cfg = Config()
        n_adcs, n = 3, 96
        fn = _slow_sine(cfg)
        res = _run(cfg, TrackPolicy(mode="track_own", randomize=False), fn, n=n, n_adcs=n_adcs)
        t = np.arange(n) / cfg.fs
        x = np.broadcast_to(np.asarray(fn(t), dtype=float), (n,))
        # 自己上一拍 = 3 周期之前（n_adcs=3 轮转）的采样
        for i in range(n_adcs, n):
            assert res.v_hold[i] == pytest.approx(x[i - n_adcs], abs=1e-15)
            assert res.source_adc[i] == res.acquired_adc[i]

    def test_weighted_mode_is_the_weighted_combination(self):
        cfg = Config()
        n_adcs, n = 3, 96
        fn = _slow_sine(cfg)
        w1, w2 = 0.7, 0.3
        res = _run(
            cfg,
            TrackPolicy(
                mode="track_other_weighted",
                n_weighted=2,
                weights=(w1, w2),
                randomize=False,
            ),
            fn,
            n=n,
            n_adcs=n_adcs,
        )
        t = np.arange(n) / cfg.fs
        x = np.broadcast_to(np.asarray(fn(t), dtype=float), (n,))
        for i in range(2, n):
            expected = w1 * x[i - 1] + w2 * x[i - 2]
            assert res.v_hold[i] == pytest.approx(expected, abs=1e-12)

    def test_predistortion_adds_exactly_the_drawn_offset(self):
        """随机预失真（低-K 介电吸收对策）：只平移跟踪值，不改变来源结构。"""
        cfg = Config()
        pol = TrackPolicy(mode="track_other", predistortion_sigma_v=0.01, randomize=False)
        res = _run(cfg, pol, _slow_sine(cfg), n=96, seed=7)
        res_clean = _run(
            cfg, TrackPolicy(mode="track_other", randomize=False), _slow_sine(cfg), n=96
        )
        delta = res.v_hold[3:] - res_clean.v_hold[3:]
        # 偏移量分布与 sigma=0.01 一致（粗判：量级 + 标准差）
        assert 0.002 < np.std(delta) < 0.03
        assert np.all(res.source_adc[3:] == res_clean.source_adc[3:])


class TestPatentBackgroundArithmetic:
    """[12] 背景段：快信号下 track_own 比 reset 更差；track_other 两头都好。"""

    def test_slow_signal_tracking_beats_reset(self):
        cfg = Config()
        res = _run(cfg, TrackPolicy(mode="track_other"), _slow_sine(cfg, cycles_per_period=64))
        assert res.summary["charge_ratio_vs_reset"] < 0.2

    def test_nyquist_track_own_is_worse_than_reset(self):
        """专利自己的论证：Nyquist 输入下保持前拍需 Q = 2·A·C_IN > 复位的 A·C_IN。"""
        cfg = Config()
        fast = _fast_sine(cfg, frac_nyquist=0.45)
        res_own = _run(cfg, TrackPolicy(mode="track_own", randomize=False), fast, n=192, n_adcs=3)
        # reset 基线（解析）：mean(c_dac·|x|)
        t = np.arange(192) / cfg.fs
        x = np.asarray(fast(t), dtype=float)
        c_dac = cfg.c_active_nominal() / cfg.n_active
        q_reset = float(np.mean(c_dac * np.abs(x)))
        # track_own 从第 4 拍起有历史；只统计有历史的拍
        q_own = float(np.mean(res_own.charge[3:]))
        assert q_own > q_reset, (
            f"track_own ({q_own:.3e}) 应劣于 reset ({q_reset:.3e})："
            "这正是 [12] 背景段说'复位对快信号更优'的算术"
        )

    def test_midband_track_other_still_beats_reset(self):
        """专利的贡献：另一颗的最近一拍只差 1 个复合周期 -> 中频信号下仍优于复位。

        注：接近 Nyquist 时 mean|x[i]-x[i-1]| 可达 2·sin(πf/fs)·mean|x|
        （[12] 背景段 Q=2·A·C_IN 算术的均值版），复位反而更优——模型
        口径不掩盖这一点，见 test_nyquist_track_own_is_worse_than_reset。
        """
        cfg = Config()
        fast = _fast_sine(cfg, frac_nyquist=0.15)
        res = _run(cfg, TrackPolicy(mode="track_other", randomize=False), fast, n=192)
        assert res.summary["charge_ratio_vs_reset"] < 1.0


class TestScalingLaws:
    def test_filter_bw_is_proportional_to_charge(self):
        """kickback_filter_bw 的推导：f_3dB = ln(1/eps)·q / (2π·T·a_tol)。"""
        q, a_tol, t_cyc, eps = 1e-12, 0.01, 25e-9, 0.01
        f1 = kickback_filter_bw(q, a_tol, t_cyc, eps)
        assert f1 == pytest.approx(np.log(100.0) * q / (2 * np.pi * t_cyc * a_tol))
        # 电荷减半 -> 带宽上限减半（同比例）
        assert kickback_filter_bw(q / 2, a_tol, t_cyc, eps) == pytest.approx(f1 / 2)

    def test_illegal_inputs_refused(self):
        with pytest.raises(ValueError):
            kickback_filter_bw(0.0, 0.01, 25e-9)
        with pytest.raises(ValueError):
            kickback_filter_bw(1e-12, 0.01, 25e-9, eps=1.5)

    def test_summary_ratios_are_consistent(self):
        """带宽比 = 电荷比；驱动噪声比 = sqrt(电荷比)（同一推导的两面）。"""
        cfg = Config()
        res = _run(cfg, TrackPolicy(mode="track_other"), _slow_sine(cfg))
        s = res.summary
        assert s["filter_bw_ratio_vs_reset"] == pytest.approx(s["charge_ratio_vs_reset"])
        assert s["driver_noise_ratio_vs_reset"] == pytest.approx(
            np.sqrt(s["charge_ratio_vs_reset"])
        )


class TestDeterminism:
    def test_same_seed_same_result(self):
        cfg = Config()
        pol = TrackPolicy(mode="track_other", randomize=True, predistortion_sigma_v=0.005)
        a = _run(cfg, pol, _slow_sine(cfg), n=64, seed=3)
        b = _run(cfg, pol, _slow_sine(cfg), n=64, seed=3)
        assert np.array_equal(a.charge, b.charge)
        assert np.array_equal(a.v_hold, b.v_hold)
        assert np.array_equal(a.acquired_adc, b.acquired_adc)

    def test_n_adcs_below_two_refused(self):
        cfg = Config()
        with pytest.raises(ValueError, match=">= 2"):
            InterleavedSAR(
                cfg, n_adcs=1, policy=TrackPolicy(), input_fn=_slow_sine(cfg), n_cycles=8
            )
