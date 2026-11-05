"""保留外部审计针对 v6.1 的反例及历史回归意图。

每项检查对应一条审计发现，并独立于 experiments.stage* 的自评估。
下列 A 编号来自审计报告，F 编号是 CHANGELOG.md 引用的稳定测试标识；
此索引说明历史对应关系，实际通过状态以测试执行记录为准。

A01 前级分辨率口径 → F1 / TestF1FirstStageReading。
A02 跨周期采样状态 → F2 / TestF2SliceCausality。
A03 扰动码单位换算 → F3 / TestF3DitherUnits。
A04 动态范围作为设定值 → F4 / TestF4NoiseBudgetIsAnAnchor。
A07.1 静态测试与 20 位码域 → F8 / TestF8EvidenceScope。
A07.2 主记录缺少闪烁分量 → F9 / TestF9FlickerBandLimit。
A07.4 蒙特卡洛直方图来源 → F10 / TestF10MonteCarloProvenance。
A07.5 低频指标异常 → TestExtraLowFrequencyMetrics。

A05/A06 的观测校正余量回归在 tests/regression/test_observer_headroom.py，
A07.3 的采样时偏斜率回归在 tests/regression/test_skew_derivative.py。
逐项审计记录见 docs/audit_response.md。
"""

from __future__ import annotations

import numpy as np
import pytest

from adi_model import Config, sine_input
from adi_model.mapper import dither_transfer_code
from adi_model.sadc import (
    units_per_first_stage_step,
)
from adi_model.slice_pool import PhysicalSlicePool, check_causality


# ===========================================================================
# F1：前级分辨率口径及网格一致性。
# ===========================================================================
class TestF1FirstStageReading:
    """F1：区分未知输入的判决分辨率与扰动码字位数。

    v6.1 将 9 位前级解释为 6 位粗判决加 3 位扰动，只有 64 个输入判决电平；
    对应步长及最大残差约为 93.75 mV，不能视为 512 个输入电平。
    本组保留显式口径及 DAC 可表达电平数的历史一致性检查。
    """

    def test_readings_are_explicit_and_distinct(self):
        """检查历史接口中的三种口径可按名称显式区分。"""
        cfg = Config()
        assert cfg.stage1_reading in {
            "paper_consistent",
            "paper_literal",
            "legacy_codeword",
        }
        legacy = Config.legacy_v61()
        assert legacy.stage1_reading == "legacy_codeword"
        consistent = Config.paper_consistent()
        assert consistent.stage1_reading == "paper_consistent"

    def test_decision_levels_equal_two_to_the_b1(self):
        """检查输入判决电平数为 2**b1，区分审计中的 64 与 512 个电平。"""
        for cfg in (Config(), Config.paper_consistent(), Config.legacy_v61()):
            assert cfg.stage1_levels == 2**cfg.b1

    def test_step_is_two_vfs_over_levels(self):
        """检查 Delta1=2*v_fs/2**b1；历史 9 位为 11.71875 mV，6 位为 93.75 mV。"""
        for cfg in (Config(), Config.paper_consistent(), Config.legacy_v61()):
            assert cfg.delta1 == pytest.approx(2.0 * cfg.v_fs / 2**cfg.b1)

    def test_reading_and_topology_must_be_consistent(self):
        """检查 1024 电平口径与 512 电平 DAC 的不一致会被报告。"""
        bad = Config(b1=10)
        checks = bad.validate(verbose=False)
        key = "第一级读数与 DAC 拓扑自洽（2**b1 <= DAC 电平数）"
        assert key in checks, "validate() must carry the consistency judgement"
        assert (
            checks[key][2] is False
        ), "b1=10 needs 1024 levels but the DAC has 512; validate() must FAIL"

    def test_historical_seven_bit_grid_ratio_is_preserved(self):
        """保留历史 7+2 网格假设的算术对照。

        该比例不能建立 9 位未知输入判决能力；独立参数化候选由其他测试描述。
        """
        cfg = Config.paper_consistent()
        enh = float(np.log2(units_per_first_stage_step(cfg, None)))
        assert enh == pytest.approx(float(cfg.dither_enhancement_bits))
        assert cfg.b1 + enh == pytest.approx(9.0)

    def test_legacy_reading_does_not_match_the_enhancement_disclosure(self):
        """检查历史 6 位口径的 3 位网格余量与披露的 2 位增强不同。

        旧配置可在自身 3 位声明下自洽，但自身一致不代表符合论文的 2 位披露。
        """
        disclosed_enh = 2  # [00] 披露“增强 2 位”。
        legacy = Config.legacy_v61()
        grid_enh = float(np.log2(units_per_first_stage_step(legacy, None)))
        assert grid_enh == pytest.approx(3.0)
        assert int(legacy.dither_enhancement_bits) == 3  # 仅检查旧声明自身一致。
        assert int(legacy.dither_enhancement_bits) != disclosed_enh

    def test_enhancement_bits_must_match_the_grid(self):
        """检查旧声明等于 log2(DAC 电平数 / 2**b1)。

        这是 ADR 0003 的历史机器检查，防止仅修改 b1 而遗漏相应网格声明。
        """
        key = "dither 增强位数 == log2(DAC 电平数 / 2**b1)"
        for cfg in (Config(), Config.paper_consistent(), Config.legacy_v61()):
            checks = cfg.validate(verbose=False)
            assert key in checks
            assert checks[key][2] is True, f"{cfg.stage1_reading} must be self-consistent"

        # 单独修改 b1 而遗漏网格声明时应被检查发现。
        drifted = Config(
            b1=7,
            dither_enhancement_bits=3,  # 在 2 位网格余量上声明 3 位。
            adc2_n_bits=14,
            adc2_v_min=-0.15,
            adc2_v_max=1.65,
            ra_v_clip=1.98,
        )
        assert drifted.units_per_lsb1 == 4
        assert drifted.validate(verbose=False)[key][2] is False

    def test_backend_range_is_derived_from_delta1(self):
        """保留旧接口对 ADC2 范围及位数随 Delta1 变化的检查，避免遗留旧量化窗口。"""
        for cfg in (Config(), Config.paper_consistent(), Config.legacy_v61()):
            span = cfg.g_actual * cfg.delta1
            assert cfg.adc2_v_max == pytest.approx(1.10 * span, rel=1e-9)
            assert cfg.adc2_v_min == pytest.approx(-0.10 * span, rel=1e-9)
            assert cfg.ra_v_clip >= cfg.adc2_v_max


# ===========================================================================
# F2：物理切片采样状态跨周期保存。
# ===========================================================================
class TestF2SliceCausality:
    """F2：检查转换电容确实持有所属样本的采样电荷。

    历史 ShuffledScheduler.reserve_dual() 每周期独立抽样，8191 次转移仅 1 次
    满足转换组等于前周期采集组，平均交集为 3.56/8。本组目标是检查物理池跨周期
    保存状态，而不只检查单周期两个组互斥。
    """

    @pytest.mark.parametrize("strategy", ["pingpong", "shuffle_causal"])
    def test_no_cross_cycle_violations(self, cfg, strategy):
        pool = PhysicalSlicePool(cfg, np.random.default_rng(0))
        plan = pool.plan(4096, np.random.default_rng(20260910), strategy=strategy)
        chk = check_causality(plan)
        assert chk["violations"] == 0, (
            f"{strategy}: {chk['violations']} cycles converted a group that "
            f"was not acquired in the previous cycle"
        )

    @pytest.mark.parametrize("strategy", ["pingpong", "shuffle_causal"])
    def test_full_coverage(self, cfg, strategy):
        """检查每个转换切片都来自前周期采集组，覆盖应为 8/8；历史值为 3.56/8。"""
        pool = PhysicalSlicePool(cfg, np.random.default_rng(0))
        plan = pool.plan(4096, np.random.default_rng(20260910), strategy=strategy)
        chk = check_causality(plan)
        assert chk["mean_coverage"] == pytest.approx(8.0, abs=1e-9)

    def test_shuffle_keeps_randomness(self, cfg):
        """检查因果调度仍访问多个不同转换组，不通过固定调度满足归属条件。"""
        pool = PhysicalSlicePool(cfg, np.random.default_rng(5))
        plan = pool.plan(2048, np.random.default_rng(1), strategy="shuffle_causal")
        distinct = len({tuple(g) for g in plan.conv.tolist()})
        assert distinct > 100, (
            f"only {distinct} distinct converting groups — the schedule is " f"effectively fixed"
        )

    def test_mismatch_binds_to_the_physical_selection(self, cfg):
        """检查 DAC 误差随实际转换切片选择变化。

        历史 evaluate_physical 未接收 8/18 选择，固定与随机调度误差逐位相同，
        max|delta| 为 0.0 µV；本断言意在约束该物理选择的作用。
        """
        pool = PhysicalSlicePool(cfg, np.random.default_rng(7))
        n = 1024
        k = (np.arange(n) % 512).astype(float)
        sid = np.arange(n) % 512
        conv_a = np.tile(np.arange(0, 8), (n, 1))
        conv_b = np.tile(np.arange(10, 18), (n, 1))
        e_a = pool.dac_error(conv_a, k, sid)
        e_b = pool.dac_error(conv_b, k, sid)
        delta = np.max(np.abs(e_a - e_b))
        assert delta > 0.0, (
            "DAC error is independent of the 8/18 physical selection — the " "audit's exact finding"
        )

    def test_signal_capacitance_depends_on_selection(self, cfg):
        """检查采样增益随实际转换电容组变化。

        100 ppm 单元失配下，这一匹配效应约 10 ppm，可能小于 NumPy 默认 allclose
        的 rtol=1e-5。此处用显式容差区分两个不同电容组，不以默认近似吞掉差异。
        """
        pool = PhysicalSlicePool(cfg, np.random.default_rng(11))
        n = 512
        conv_a = np.tile(np.arange(0, 8), (n, 1))
        conv_b = np.tile(np.arange(8, 16), (n, 1))
        c_a = pool.signal_capacitance(conv_a)
        c_b = pool.signal_capacitance(conv_b)
        rel = abs(c_a.mean() / c_b.mean() - 1.0)
        assert rel > 1e-7, (
            "the selected 8-of-18 group does not change the sampling "
            "capacitance — this is the audit's exact finding"
        )
        assert rel < 1000e-6, (
            "the selection is changing the gain by more than a matching " "artefact would"
        )

        # 因果随机调度应使该值逐样本变化，
        # 变化量应处于匹配误差量级。
        plan = pool.plan(n, np.random.default_rng(3), strategy="shuffle_causal")
        c = pool.signal_capacitance(plan.conv[plan.valid])
        assert np.unique(c).size > 1, "shuffling does not modulate the gain"
        ppm = (c.max() - c.min()) / c.mean() * 1e6
        assert 0.0 < ppm < 1000.0, f"gain modulation {ppm:.1f} ppm is not matching-scale"


# ===========================================================================
# F3：扰动码单位换算。
# ===========================================================================
class TestF3DitherUnits:
    """F3：检查粗码步长与 RDAC 单元步长之间的换算。

    历史粗粒度分支将 -round(d/Delta_Q) 得到的粗步数直接加到 RDAC 单元码上，
    遗漏 Delta_Q/Delta_D；v6.1 该比例为 8。审计记录中修正前后溢出率为
    32.7%→16.0%，RMS 为 16308→5699 µV，并由此使原检查从通过变为失败。
    这些数值是历史诊断记录，不表示当前模型已重跑。
    """

    def test_range_model_is_an_exact_unit_conversion(self, cfg):
        d = np.array([-0.25, 0.5, -1.0, 0.0, 0.75]) * cfg.delta1
        cc = Config(
            **{**cfg.__dict__, "dither_mode": "quantizer", "dither_transfer_model": "range"}
        )
        code = dither_transfer_code(cc, d, step_rdac=cfg.rdac_step, step_coarse=cfg.delta1)
        assert np.array_equal(code, -np.round(d / cfg.rdac_step))

    def test_output_is_in_rdac_unit_steps(self, cfg):
        """检查返回码乘 RDAC 步长可在半个 RDAC 步长内还原扰动电压。"""
        d = np.linspace(-0.5, 0.5, 21) * cfg.delta1
        cc = Config(
            **{**cfg.__dict__, "dither_mode": "quantizer", "dither_transfer_model": "range"}
        )
        code = dither_transfer_code(cc, d, step_rdac=cfg.rdac_step, step_coarse=cfg.delta1)
        resid = np.abs(code * cfg.rdac_step + d)
        assert np.all(resid <= cfg.rdac_step / 2 + 1e-18), (
            "the converted code does not reconstruct the dither voltage in "
            "RDAC units — a unit-system mix-up"
        )

    def test_missing_conversion_factor_is_reproduced(self, cfg):
        """构造缺少换算因子的反例，其比例误差为 step_coarse/step_rdac。"""
        d = np.array([1.0, -1.0]) * cfg.delta1
        correct = -np.round(d / cfg.rdac_step)
        wrong = -np.round(d / cfg.delta1)
        ratio = cfg.delta1 / cfg.rdac_step
        assert ratio > 1.0
        assert np.array_equal(correct, wrong * ratio)

    def test_zero_dither_returns_zero(self, cfg):
        d = np.zeros(4)
        cc = Config(
            **{**cfg.__dict__, "dither_mode": "quantizer", "dither_transfer_model": "range"}
        )
        code = dither_transfer_code(cc, d, step_rdac=cfg.rdac_step, step_coarse=cfg.delta1)
        assert np.array_equal(code, np.zeros(4))

    def test_nonpositive_step_raises(self, cfg):
        cc = Config(**{**cfg.__dict__, "dither_mode": "quantizer"})
        with pytest.raises(ValueError):
            dither_transfer_code(cc, np.zeros(2), step_rdac=0.0, step_coarse=cfg.delta1)


# ===========================================================================
# F4：噪声预算的目标反解属性。
# ===========================================================================
class TestF4NoiseBudgetIsAnAnchor:
    """F4：区分目标动态范围反解的噪声预算与独立预测。

    历史 ra_out_noise_rms=None 按目标 DR 反解 RA 噪声，因此复现 94.6 dB 是
    预算恒等式。本组意图是将该值标为 FITTED，并与披露值或独立推导分开。
    """

    def test_auto_resolved_ra_noise_is_graded_as_an_anchor(self, cfg):
        from adi_model.provenance import SourceGrade, annotate_config

        ann = annotate_config(cfg)
        g = ann["ra_out_noise_rms"]
        assert g.grade is SourceGrade.FITTED, (
            "ra_out_noise_rms is reverse-solved from target_dr_db; it must be "
            "FITTED (an anchor), not quotable as a prediction"
        )
        assert "ANCHOR" in g.source.upper() or "anchor" in g.source

    def test_noise_budget_sums_to_the_target(self, cfg):
        """检查预算分量相加满足目标动态范围，仅作为预算连接的一致性检查。

        审计历史记录为目标 90/94.6/98 dB 对应 90.015/94.611/98.001 dB；
        这种一致性不能证明电路独立达到所设定性能。
        """
        from adi_model.config import noise_budget

        nb = noise_budget(cfg)
        total = np.sqrt(
            sum(
                v**2
                for k, v in nb.items()
                if "折输入" in k or "RA" in k or "kT/C" in k or "ADC2" in k
            )
        )
        dr = 20 * np.log10((cfg.v_fs / np.sqrt(2)) / total)
        assert dr == pytest.approx(cfg.target_dr_db, abs=0.5)

    def test_pdk_sigma_is_assumed_not_disclosed(self, cfg):
        """检查 1117 ppm 的假设面积定律估计不会被标为 PDK 测量或原文披露。"""
        from adi_model.provenance import SourceGrade, annotate_config

        ann = annotate_config(cfg)
        assert ann["pdk_sigma_est_ppm"].grade is SourceGrade.ASSUMED


# ===========================================================================
# F8：静态测试与输出码域的证据边界。
# ===========================================================================
class TestF8EvidenceScope:
    """F8：区分平均误差曲线、全码密度 DNL 与真正整数输出。

    审计时静态曲线来自平均误差，n_bits_target 只设 LSB 参考，不对输出量化；
    本组保留该历史证据边界，不能以标签代替对应测试域。
    """

    def test_static_test_reports_its_own_scope(self):
        import inspect

        from adi_model.metrics import inl_from_mean_error, static_test

        src = inspect.getsource(static_test) + inspect.getsource(inl_from_mean_error)
        assert "code-density" in src or "码密度" in src, (
            "the static test must declare that it is not a code-density DNL " "measurement"
        )

    def test_n_bits_target_does_not_change_the_analog_output(self, cfg):
        """检查历史 n_bits_target 仅改变 LSB 参考的行为，不据此声称已建立 20 位码域。"""
        from adi_model.sim import run_sim

        a = Config(**{**cfg.__dict__, "n_bits_target": 20, "dac_arch": "unary"})
        b = Config(**{**cfg.__dict__, "n_bits_target": 18, "dac_arch": "unary"})
        n = 2**11
        inp = sine_input(0.5 * cfg.v_fs, cfg.fs * 331 / n)
        ra = run_sim(a, inp, n, rng=np.random.default_rng(1))
        rb = run_sim(b, inp, n, rng=np.random.default_rng(1))
        assert np.array_equal(ra.out, rb.out), (
            "output should not depend on n_bits_target — the model is an "
            "analog-voltage-equivalent model"
        )


# ===========================================================================
# F9：闪烁噪声生成器与主记录频带。
# ===========================================================================
class TestF9FlickerBandLimit:
    """F9：检查有限记录可分辨的闪烁功率及其声明范围。

    历史 fs=40 MHz、N=32768 时频率分辨率约 1220 Hz，远高于 40 Hz 拐角；
    旧生成器又清零拐角以上频点，导致主记录的闪烁序列全零。本组保留可分辨性及
    独立低采样率生成器检查，避免把不可分辨功率描述成已在主记录验证。
    """

    def test_generator_has_power_below_the_record_band(self):
        """检查足够长、能分辨 40 Hz 的记录具有非零闪烁分量。"""
        from adi_model.ra import flicker_series

        n, fs = 2**16, 4000.0  # 频率分辨率 0.061 Hz，可以分辨 40 Hz。
        x = flicker_series(n, fs, f_corner=40.0, sigma_white=1e-6, rng=np.random.default_rng(0))
        assert np.any(x != 0.0)
        assert np.std(x) > 0.0

    def test_generator_power_is_zero_above_the_corner(self):
        """检查历史约定的 1/f 分支在拐角以上终止，白噪声由系统热噪声源单独提供。

        该约定避免重复注入白底；旧 stage23 记录中重复白底造成过 3 dB 差异。
        """
        from adi_model.ra import flicker_series

        n, fs = 2**15, 4000.0
        x = flicker_series(n, fs, f_corner=40.0, sigma_white=1e-6, rng=np.random.default_rng(0))
        P = np.abs(np.fft.rfft(x)) ** 2
        f = np.fft.rfftfreq(n, d=1.0 / fs)
        hi = f > 10.0 * 40.0
        assert P[hi].sum() < 1e-12 * max(
            P.sum(), 1e-30
        ), "the generator is still emitting power well above the corner"

    def test_main_record_claim_is_not_silently_made(self, cfg):
        """检查默认 fs/N 无法分辨拐角的算术条件，不把该主记录称为已验证低频功率。"""
        n = 2**15
        df = cfg.fs / n
        assert df > 40.0, (
            "this test documents that the main record cannot resolve 40 Hz; "
            "if fs/N changes, revisit docs/model_scope.md"
        )


# ===========================================================================
# F10：蒙特卡洛直方图样本来源。
# ===========================================================================
class TestF10MonteCarloProvenance:
    """F10：检查直方图使用实际逐芯片样本。

    历史“60 芯片 SNDR 分布”图使用均值和标准差重新抽取 400 个高斯数值，
    不能支持尾部或良率判断。本组约束保留并直接使用真实逐芯片结果。
    """

    def test_summary_keeps_per_chip_samples(self, cfg):
        """检查结果保存逐芯片 SNDR 数组，统计矩由该数组计算。"""
        from adi_model.experiments import mc_pdk_sigma

        out = mc_pdk_sigma(cfg, n_chips=6, n=2**11)
        assert "sndr_per_chip" in out, (
            "Monte-Carlo must expose the per-chip samples so that any "
            "histogram is drawn from real data"
        )
        s = np.asarray(out["sndr_per_chip"])
        assert s.shape == (6,)
        # 摘要必须由逐芯片样本确定，
        # 不能作为独立于样本的另一组描述。
        assert out["SNDR_mean"] == pytest.approx(float(s.mean()), rel=1e-12)
        assert out["SNDR_std"] == pytest.approx(float(s.std()), rel=1e-12)
        assert out["SNDR_min"] == pytest.approx(float(s.min()), rel=1e-12)
        assert out["sigma_ppm"] == pytest.approx(float(cfg.pdk_sigma_est_ppm))

    def test_histogram_is_not_resampled(self):
        """静态检查 src/ 与 tools/ 不从均值、标准差重造直方图样本。

        只扫描 adi_model.experiments 曾遗漏 tools/run_all.py 中的绘图重抽样，
        因此本断言覆盖两个目录。
        """
        import pathlib
        import re

        roots = [
            pathlib.Path(__file__).resolve().parents[2] / "src",
            pathlib.Path(__file__).resolve().parents[2] / "tools",
        ]
        # rng.normal(均值, 标准差, 数量) 形式表示按统计矩重新抽样。
        pattern = re.compile(
            r"(rng|np\.random\.[a-z_]*|default_rng\([^)]*\))\.normal\(\s*"
            r"[^)]*mean[^)]*,\s*[^)]*std[^)]*,"
        )
        offenders = []
        for root in roots:
            for path in sorted(root.rglob("*.py")):
                for i, line in enumerate(path.read_text().splitlines(), 1):
                    code = line.split("#", 1)[0]
                    if pattern.search(code):
                        offenders.append(f"{path.relative_to(root.parent)}:{i}: {line.strip()}")
        assert not offenders, (
            "found a histogram drawn by resampling a (mean, sigma) pair: " f"{offenders}"
        )

    def test_monte_carlo_figure_uses_per_chip_samples(self):
        """检查绘图入口读取 sndr_per_chip，而非只读取统计矩。"""
        import pathlib

        run_all = pathlib.Path(__file__).resolve().parents[2] / "tools" / "run_all.py"
        src = run_all.read_text()
        assert "sndr_per_chip" in src, (
            "tools/run_all.py must draw the SNDR histogram from the real "
            "per-chip samples (audit A07.4)"
        )

    def test_the_guard_would_catch_the_historical_line(self):
        """用 v6.1 的历史问题行反向检查正则守卫，避免模式退化为无效检查。"""
        import re

        historical = '    samp = rng.normal(m["SNDR_mean"], m["SNDR_std"], 400)'
        pattern = re.compile(
            r"(rng|np\.random\.[a-z_]*|default_rng\([^)]*\))\.normal\(\s*"
            r"[^)]*mean[^)]*,\s*[^)]*std[^)]*,"
        )
        assert pattern.search(historical), (
            "the resampling guard no longer matches the v6.1 line — it has " "become vacuous"
        )


# ===========================================================================
# 附加回归：低频指标窗口异常。
# ===========================================================================
class TestExtraLowFrequencyMetrics:
    """检查审计复现时发现的低频谐波窗口边界。

    旧 sine_fit_metrics 在 fin 约低于 5 kHz 时，将 fund_bin 限到 1 后仍使用
    fund_bin±guard 切片；负索引导致空窗口并抛出 ValueError。
    """

    @pytest.mark.parametrize("fin", [1e3, 5e3, 1e5, 1e6, 5e6, 19e6])
    def test_metrics_do_not_raise_across_bin_clamping(self, cfg, fin):
        from adi_model.metrics import sine_fit_metrics

        n = 2**14
        x = sine_input(1.0, fin)(np.arange(n) / cfg.fs)
        m = sine_fit_metrics(x, cfg.fs, fin)
        assert np.isfinite(m["SNDR_dB"])
        assert m["SNDR_dB"] > 60.0

    def test_low_frequency_declares_harmonics_unreliable(self, cfg):
        """基波过低而无法分离谐波时，应显式报告不可靠，不返回未说明限制的 THD。"""
        from adi_model.metrics import sine_fit_metrics

        n = 2**14
        x = sine_input(1.0, 1e3)(np.arange(n) / cfg.fs)
        m = sine_fit_metrics(x, cfg.fs, 1e3)
        assert "harmonics_reliable" in m
        assert m["harmonics_reliable"] is False

    def test_spectral_band_helper_clamps_at_the_edges(self):
        """检查 _band_max 显式裁切边界，不依赖 Python 负索引切片。"""
        from adi_model.metrics import _band_max

        X = np.arange(32, dtype=float)
        for b in (0, 1, 31):
            assert np.isfinite(_band_max(X, b, 4))
