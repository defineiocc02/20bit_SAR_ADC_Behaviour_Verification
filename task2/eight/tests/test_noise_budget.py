# =============================================================================
# 文件名     : test_noise_budget.py
# 模块名     : task2.eight.tests.test_noise_budget
# 版本       : v1.0
# 功能       : M7 判据（解析部分）——预算合计、DR 反推、功率闭合与三条斜率。
#
# 设计说明   :
#   * 这里只验**算术自洽**，不验「芯片里真是这个比例」。分配值是 S5_假设，
#     唯一受论文约束的是合计与它折出的 DR。
#   * 三条斜率的口径必须统一成「按功率占比」，不能像计划里那样混用独占口径
#     与占比口径——混用时同一组数字会同时支持两种互相矛盾的解读。
#     两种口径都算出来放着，实测时看哪个对得上。
# -----------------------------------------------------------------------------
# 变更记录
# -----------------------------------------------------------------------------
# 初始版本，M7。
# =============================================================================

"""M7 判据（解析部分）：预算合计、DR 反推、功率闭合与三条斜率。"""

from __future__ import annotations

import numpy as np
import pytest

from adi_model.capacitors import ExperimentConfig
from task2.eight.noise_budget import (
    DEFAULT_BUDGET,
    PAPER_BANDWIDTH_HZ,
    PAPER_DIFFERENTIAL_HALF_RANGE_V,
    PAPER_DYNAMIC_RANGE_DB,
    PAPER_NSD_NV_PER_SQRT_HZ,
    budget_table,
    combine,
    differential_full_scale_rms,
    exclusive_dr,
    expected_dr_change,
    noise_config,
    required_total_noise,
)

# 模型默认满幅是 full_scale_peak_v=5.0（差分 10 Vpp），比论文的 ±3 V 大 1.667 倍。
MODEL_HALF_RANGE_V = 5.0


def test_differential_full_scale_rms_matches_the_six_vpp_reading():
    """±3 V 差分就是 6 Vpp，RMS 是 2.1213 V。"""
    assert differential_full_scale_rms(3.0) == pytest.approx(2.12132, rel=1e-5)


def test_budget_total_reproduces_the_paper_dynamic_range():
    """四源合计 41.39 µV，在 ±3 V 差分下折 94.19 dB，与论文 94.2 差 0.01 dB。"""
    total = combine(source.sigma_v for source in DEFAULT_BUDGET)
    assert total == pytest.approx(41.39e-6, rel=1e-3)
    dr = exclusive_dr(PAPER_DIFFERENTIAL_HALF_RANGE_V, total)
    assert dr == pytest.approx(94.19, abs=0.02)
    assert abs(dr - PAPER_DYNAMIC_RANGE_DB) < PAPER_DYNAMIC_RANGE_DB * 0.001 + 0.02


def test_power_domain_closes_within_half_a_percent():
    """逐源功率之和必须与合计 DR 闭合；闭合不上说明分配表抄错了。"""
    table = budget_table(PAPER_DIFFERENTIAL_HALF_RANGE_V)
    total = table[-1]
    summed = sum(10.0 ** (-row["exclusive_dr_db"] / 10.0) for row in table[:-1])
    direct = 10.0 ** (-total["exclusive_dr_db"] / 10.0)
    assert abs(summed - direct) / direct < 0.005
    assert sum(row["share"] for row in table[:-1]) == pytest.approx(1.0, rel=1e-12)


def test_required_total_noise_inverts_the_dynamic_range():
    """反推式与正算式互为逆：41.36 µV 对应 94.2 dB。"""
    required = required_total_noise(PAPER_DIFFERENTIAL_HALF_RANGE_V, PAPER_DYNAMIC_RANGE_DB)
    assert required == pytest.approx(41.36e-6, rel=1e-3)
    assert exclusive_dr(PAPER_DIFFERENTIAL_HALF_RANGE_V, required) == pytest.approx(
        PAPER_DYNAMIC_RANGE_DB, rel=1e-12
    )


def test_noise_density_is_self_consistent_with_the_dynamic_range():
    """9.3 nV/√Hz × √20 MHz = 41.59 µV，折 94.15 dB，与 94.2 差 0.05 dB。

    这条是整套反推的锚点：三个公开数互不矛盾，所以「按 DR 反推总噪声」站得住。
    论文自己 DR 94.2 与 SNR 93.7 差 0.5 dB，窗口就是从这里来的。
    """
    density_v = PAPER_NSD_NV_PER_SQRT_HZ * 1e-9 * np.sqrt(PAPER_BANDWIDTH_HZ)
    assert density_v == pytest.approx(41.59e-6, rel=1e-3)
    assert exclusive_dr(PAPER_DIFFERENTIAL_HALF_RANGE_V, density_v) == pytest.approx(
        94.15, abs=0.02
    )


def test_the_model_and_the_paper_use_different_full_scale():
    """模型满幅比论文大 1.667 倍，同一个 σ 会给出高 4.44 dB 的 DR。

    这个差必须显式存在，不能靠「反正都在 94 dB 附近」糊过去：它决定了
    paper_alignment 必须用 ±3 V 预设跑，而不是用模型默认的 ±5 V。
    """
    total = combine(source.sigma_v for source in DEFAULT_BUDGET)
    paper_dr = exclusive_dr(PAPER_DIFFERENTIAL_HALF_RANGE_V, total)
    model_dr = exclusive_dr(MODEL_HALF_RANGE_V, total)
    assert model_dr - paper_dr == pytest.approx(20 * np.log10(MODEL_HALF_RANGE_V / 3.0), rel=1e-9)
    assert model_dr - paper_dr == pytest.approx(4.44, abs=0.01)


def test_sensitivity_slopes_under_both_conventions():
    """三条斜率在「独占」与「按占比」两种口径下的解析值。

    计划里三条数字用了混用口径（kT/C 与比较器按独占、参考按占比），
    这里把两种都算出来：实测时独占口径要单独开一个源才看得到。
    """
    shares = {row["name"]: row["share"] for row in budget_table(3.0)}
    # 采样电容减半 ⇒ σ × √2。
    assert expected_dr_change(np.sqrt(2.0), 1.0) == pytest.approx(-3.01, abs=0.01)
    assert expected_dr_change(np.sqrt(2.0), shares["sampling_ktc"]) == pytest.approx(
        -0.92, abs=0.02
    )
    # 参考噪声功率翻倍 ⇒ σ × √2。
    assert expected_dr_change(np.sqrt(2.0), 1.0) == pytest.approx(-3.01, abs=0.01)
    assert expected_dr_change(np.sqrt(2.0), shares["reference"]) == pytest.approx(-1.83, abs=0.02)
    # 比较器 σ 20 → 200 µV ⇒ ×10。
    assert expected_dr_change(10.0, 1.0) == pytest.approx(-20.0, abs=0.01)
    assert expected_dr_change(10.0, shares["comparator"]) == pytest.approx(-13.8, abs=0.1)


def test_noise_config_leaves_ktc_to_the_capacitance():
    """kT/C 的 σ 不写进预算表，由采样电容算；否则改电容时噪声不会跟着动。"""
    cfg = noise_config()
    assert cfg.enabled
    assert cfg.sampling_cap_f == pytest.approx(20.5e-12, rel=0)
    assert cfg.thermal_sigma_v * np.sqrt(2.0) == pytest.approx(20.102e-6, rel=1e-4)
    assert cfg.reference_sigma_v == pytest.approx(30e-6, rel=0)
    assert cfg.comparator_sigma_v == pytest.approx(20e-6, rel=0)
    assert cfg.adc2_sigma_v == pytest.approx(3e-6, rel=0)


def test_noise_density_scales_as_the_square_root_of_bandwidth():
    """噪声密度是 σ/√BW：带宽 ×4 只让密度减半，即 6.02 dB，不是 12.04 dB。

    写成 20log10(BW 比) 是这里最容易犯的错——它会让 A4 从 9.25 变成 4.6 nV/√Hz
    还看不出量级问题，因为两者都在「个位数 nV」范围内。
    """
    sigma = 41.39e-6
    narrow = sigma / np.sqrt(5e6)
    wide = sigma / np.sqrt(20e6)
    assert narrow / wide == pytest.approx(2.0, rel=1e-9)
    assert 20 * np.log10(narrow / wide) == pytest.approx(6.02, abs=0.01)


def test_paper_scale_is_four_point_four_db_for_the_default_full_scale():
    """模型 ±5 V 折到论文 ±3 V 是 −4.44 dB；这个量必须显式出现，不能靠「都在 94 附近」糊过去。"""
    from task2.eight.paper_alignment import paper_scale_db, tone_backoff_db

    assert paper_scale_db(5.0) == pytest.approx(-4.437, abs=0.002)
    assert paper_scale_db(3.0) == pytest.approx(0.0, abs=1e-12)
    assert tone_backoff_db(ExperimentConfig()) == pytest.approx(3.522, abs=0.002)
