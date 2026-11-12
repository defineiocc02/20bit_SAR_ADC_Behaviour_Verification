# =============================================================================
# 文件名     : test_transient_kernels.py
# 模块名     : test_transient_kernels
# 版本       : v1.0
# 功能       : 用手算和独立ODE核对三个动态核的单位、方向、极限与因果性。
# 设计说明   :
#   [独立性] RA用solve_ivp重积分，不复刻被测解析表达式作为答案。
#   [边界] 相等时间常数、零τ、负电荷和倒序事件均需覆盖。
# 变更记录   : 新增状态核定向验证。
# =============================================================================

"""动态核的独立数学验证。"""

import numpy as np
import pytest
from scipy.integrate import solve_ivp

from adi_model.behavioral.transients import CoupledNode, DynamicConfig, RampDrive, SineDrive, ra_endpoint


def test_q_over_c_and_signed_recovery():
    """2 C注入2 F得到1 V，1 s后按τ=1 s衰减；反向注入不能取绝对值。"""
    node = CoupledNode(2, 1)
    assert node.inject(0, 2) == 1
    np.testing.assert_allclose(node.advance(1), np.exp(-1), rtol=0, atol=1e-15)
    assert node.inject(1, -2) < 0
    with pytest.raises(ValueError, match="backwards"):
        node.advance(0)


def test_late_edge_cannot_change_an_earlier_observation():
    """先取孔径值再注入下一事件，已保存的孔径值不可反向变化。"""
    node = CoupledNode(1, 1)
    observed = node.advance(0)
    node.inject(0.5, 1)
    assert observed == 0
    assert node.advance(1) > 0


@pytest.mark.parametrize(
    "ref_tau,ra_tau", [(3e-9, 1e-9), (1e-9, 1e-9), (1.00000001e-9, 1e-9), (0, 1e-9)]
)
def test_ra_matches_independent_differential_equation(ref_tau, ra_tau):
    """归一时间避免求解器绝对时间尺度影响，单独积分仍在建立的参考驱动。"""
    duration = 10e-9
    ideal, initial, amplitude = 0.03, -0.02, 0.004

    def derivative(u, y):
        ref = amplitude * np.exp(-u * duration / ref_tau) if ref_tau else 0
        return duration * (ideal + ref - y) / ra_tau

    result = solve_ivp(derivative, (0, 1), [initial], rtol=2e-12, atol=1e-14)
    np.testing.assert_allclose(
        ra_endpoint(ideal, initial, amplitude, duration, ref_tau, ra_tau),
        result.y[0, -1],
        rtol=0,
        atol=3e-13,
    )


def test_zero_ra_tau_tracks_reference_endpoint():
    """瞬时RA取参考终点，不能误用RA起点的误差。"""
    assert ra_endpoint(1, 0, 2, 0, 1, 1) == 0
    np.testing.assert_allclose(ra_endpoint(1, 0, 2, 1, 1, 0), 1 + 2 / np.e)


@pytest.mark.parametrize("drive", [RampDrive(-1, 0.2), SineDrive(1, 0.4)])
def test_continuous_acquisition_matches_independent_ode(drive):
    """比较整个跟踪窗，不能把连续正弦的孔径值当成窗内常数。"""
    start, end, tau, initial = 0.2, 0.7, 0.13, -0.4
    result = solve_ivp(
        lambda t, y: (drive.value(t) - y) / tau, (start, end), [initial], rtol=1e-12, atol=1e-13
    )
    np.testing.assert_allclose(
        drive.track(initial, end, end - start, tau), result.y[0, -1], atol=2e-12
    )
    assert drive.track(initial, end, end - start, 0) == drive.value(end)


def test_longer_dc_acquisition_reduces_initial_memory():
    """只对DC初始误差断言单调，连续AC的相位滞后不会靠延长窗口消失。"""
    drive = RampDrive(1, 0)
    assert abs(drive.track(0, 2, 2, 0.3) - 1) < abs(drive.track(0, 1, 1, 0.3) - 1)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"period_s": 0},
        {"dac_bandwidth_multiple": 0},
        {"driver_tau_s": -1},
        {"ra_start_fraction": 1},
        {"quiet_guard_s": float("nan")},
    ],
)
def test_bad_parameters_are_rejected(kwargs):
    """非法周期、带宽与相位直接失败，不补成理想结果。"""
    with pytest.raises(ValueError):
        DynamicConfig(**kwargs)
