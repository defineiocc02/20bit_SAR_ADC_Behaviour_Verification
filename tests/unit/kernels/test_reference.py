# =============================================================================
# 文件名     : test_reference.py
# 模块名     : src.tests.test_reference
# 版本       : v1.0
# 功能       : M4a 判据——参考动态建立的解析锚点、指数线性与 RA 非线性量级。
#
# 设计说明   :
#   * 这里验的是「公式与论文锚点是否自洽」，不是「τ 是否等于某个硅实测值」。
#     τ 本身是 S5_假设，唯一可验的是它由论文两句话反解出来的那一步。
#   * 指数建立的 ln(error) 对 t/τ 必须严格是一条斜率为 −1 的直线。这条检验
#     看着平凡，但能抓到「把 exp 写成 1/(1+x)」「把 τ 放到分子」这类错——
#     那两种错在单点上看都像是对的。
# -----------------------------------------------------------------------------
# 变更记录
# -----------------------------------------------------------------------------
# 初始版本，M4a。
# =============================================================================

"""M4a 判据：参考建立的解析锚点、指数线性与 RA 非线性量级。"""

from __future__ import annotations

import numpy as np
import pytest

from adi_model.codes import OUTPUT_CODES
from adi_model.reference import (
    RESIDUAL_ACCURACY_RATIO,
    SETTLE_FRACTION_OF_RA_PHASE,
    dynamic_reference,
    precharge_fraction,
    ra_transfer,
    required_tau,
    settle_error,
)

# 差分满幅 10 V 对应 20 位输出的 1 LSB，单位 V。与 task2 的输出码口径一致。
LSB20_V = 10.0 / OUTPUT_CODES


def test_settle_error_hits_the_physical_boundaries():
    """t=0 时误差等于整个摆幅，t 很大时趋零，τ=0 时恒为零。"""
    assert settle_error(1e-9, 0.0, 1.0) == pytest.approx(1.0, rel=0, abs=0)
    assert settle_error(1e-9, 100e-9, 1.0) < 1e-40
    assert settle_error(0.0, 1e-9, 1.0) == 0.0
    assert settle_error(1e-9, -1e-12, 1.0) == pytest.approx(1.0, rel=0, abs=0)


def test_precharge_fraction_and_dynamic_reference_share_one_tau():
    """预充 + RA 两段若是同一个 τ，合起来必须等于一次性建立同样的总时长。"""
    tau, t_pre, t_ra, swing = 0.4e-9, 0.6e-9, 1.2e-9, 5.0
    frac = precharge_fraction(tau, t_pre)
    two_stage = dynamic_reference(frac, tau, t_pre, t_ra, swing)
    one_stage = settle_error(tau, t_pre + t_ra, swing)
    assert two_stage == pytest.approx(one_stage, rel=1e-12, abs=0)


def test_dynamic_reference_rejects_a_precharge_time_from_another_tau():
    """预充比例与预充时长不属于同一个 τ 时必须报错，不能默默取一个。"""
    with pytest.raises(ValueError, match="disagree under one tau"):
        dynamic_reference(0.5, 0.4e-9, 10e-9, 1.2e-9, 5.0)


def test_required_tau_matches_the_closed_form():
    """反解式 tau = f*T/ln(ratio) 与手算一致。"""
    t_ra = 12.5e-9
    tau = required_tau(t_ra, SETTLE_FRACTION_OF_RA_PHASE, RESIDUAL_ACCURACY_RATIO)
    assert tau == pytest.approx(
        SETTLE_FRACTION_OF_RA_PHASE * t_ra / np.log(RESIDUAL_ACCURACY_RATIO), rel=1e-15
    )
    # 15b 到 20b 是 2^5，τ 约为 0.18755 倍的 RA 相位。
    assert tau / t_ra == pytest.approx(0.187550, rel=1e-5)


def test_paper_anchor_settles_to_one_thirty_second_at_65_percent_of_the_ra_phase():
    """论文锚点：在 0.65·T_RA 处，初始误差恰好被压到 1/32。"""
    t_ra, swing = 12.5e-9, 5.0
    tau = required_tau(t_ra, SETTLE_FRACTION_OF_RA_PHASE, RESIDUAL_ACCURACY_RATIO)
    residual = dynamic_reference(0.0, tau, 0.0, SETTLE_FRACTION_OF_RA_PHASE * t_ra, swing)
    assert residual == pytest.approx(swing / RESIDUAL_ACCURACY_RATIO, rel=1e-12, abs=0)


def test_log_error_is_a_straight_line_with_slope_minus_one():
    """ln(error) 对 T_RA/τ 必须是斜率为 −1 的直线。

    这条能抓到「把 exp 写成 1/(1+x)」或「τ 放到分子」——两者在单点上都像对的。
    """
    t_ra, swing = 12.5e-9, 5.0
    taus = np.array([0.05, 0.1, 0.1876, 0.4, 1.0]) * t_ra
    errors = np.array([dynamic_reference(0.0, tau, 0.0, t_ra, swing) for tau in taus])
    # τ 越小建立越快、误差越小，所以误差随 τ 单调**增**；方向写反是最常见的理解错误。
    assert np.all(np.diff(errors) > 0)
    x = t_ra / taus
    y = np.log(errors)
    slope, intercept = np.polyfit(x, y, 1)
    residual = y - (slope * x + intercept)
    r_squared = 1.0 - residual.var() / y.var()
    assert slope == pytest.approx(-1.0, rel=1e-12, abs=0)
    assert intercept == pytest.approx(np.log(swing), rel=1e-12, abs=0)
    assert r_squared > 0.999999


def test_zero_coefficients_leave_the_transfer_untouched():
    """两个系数都为 0 时 ra_transfer 必须是恒等映射。"""
    values = np.array([-5.0, -1.0, 0.0, 1.0, 5.0])
    np.testing.assert_array_equal(ra_transfer(values, 0.0, 0.0, 5.0), values)


def test_ten_ppm_second_order_gives_about_10_5_lsb20_at_full_scale():
    """10 ppm 二阶系数在差分满幅 10 V 上产生 100 µV，即 10.49 LSB20。"""
    deviation = ra_transfer(10.0, ppm2=10.0, full_scale_v=10.0) - 10.0
    assert deviation == pytest.approx(100e-6, rel=1e-9, abs=0)
    assert deviation / LSB20_V == pytest.approx(10.49, rel=1e-3)


def test_second_order_is_even_and_third_order_is_odd():
    """二阶项是偶函数（差分不抵消，抛物线 INL），三阶项是奇函数（S 形 INL）。"""
    x = 3.0
    second = ra_transfer(x, ppm2=40.0, full_scale_v=5.0) - x
    second_mirror = ra_transfer(-x, ppm2=40.0, full_scale_v=5.0) + x
    assert second == pytest.approx(second_mirror, rel=1e-15, abs=0)

    third = ra_transfer(x, ppm3=40.0, full_scale_v=5.0) - x
    third_mirror = ra_transfer(-x, ppm3=40.0, full_scale_v=5.0) + x
    assert third == pytest.approx(-third_mirror, rel=1e-15, abs=0)
