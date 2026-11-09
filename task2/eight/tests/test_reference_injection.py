# =============================================================================
# 文件名     : test_reference_injection.py
# 模块名     : task2.eight.tests.test_reference_injection
# 版本       : v1.0
# 功能       : M4b 判据——参考动态建立接进前端，且默认关闭时逐位不变。
#
# 设计说明   :
#   * 核心断言是「参考误差随转换码变化」。这一点无法从 SNDR 看出来：常数失调
#     被 FFT 去直流吃掉，而码相关的误差才表现为 INL。把 error 写成一个常数
#     是最容易犯的错，本文件用「码 0 与码顶的误差之比」把这条路堵死。
#   * RA 的 ppm 非线性不在这里验：它是输出满幅口径，与残差的输入域差一个增益，
#     见 task2/eight/experiment.py 的 _capture docstring 与 milestones.json。
# -----------------------------------------------------------------------------
# 变更记录
# -----------------------------------------------------------------------------
# 初始版本，M4b。
# =============================================================================

"""M4b 判据：参考动态建立接进前端，且默认关闭时逐位不变。"""

from __future__ import annotations

from itertools import pairwise

import numpy as np
import pytest

from adi_model.reference import (
    PRECHARGE_FRACTION_FROM_PAPER,
    ReferenceConfig,
    dynamic_reference,
)

# 论文 ISSCC2024 9.8 是 40 MS/s，一个时钟周期 25 ns，RA 相位约占半个周期。
# 本模型没有显式时序，这里取这个值只为给 from_paper_anchor 一个具体量级，
# M5 引入真实时序后应改为从配置读。
T_RA_S = 12.5e-9


def _swing(rig, state):
    """按翻动的电容单位数折算本次转换的参考摆幅，单位 V。"""
    return np.abs(state.features).sum(axis=(1, 2)) * rig["step_v"]


def test_disabled_reference_reproduces_the_no_reference_capture(rig):
    """默认配置（enabled=False）必须与完全不传 reference 逐位相同。"""
    base_state, base_coarse, base_residue, base_code = rig["capture"]()
    state, coarse, residue, code = rig["capture"](reference=ReferenceConfig())
    np.testing.assert_array_equal(residue, base_residue)
    np.testing.assert_array_equal(code, base_code)
    np.testing.assert_array_equal(coarse, base_coarse)
    np.testing.assert_array_equal(state.switches.codes, base_state.switches.codes)


def test_reference_error_is_code_dependent_not_a_constant_offset(rig):
    """参考误差必须随转换码变化——而且要看 _capture 的**输出**，不是它的内部量。

    常数失调在 FFT 去直流后整个消失，看上去「接了」实际毫无作用。这里把残差位移
    按增益折回输入域，再卡它的相对离散度：若实现里把摆幅写成满幅常数，离散度是 0。
    直接检查 state.features 的摆幅是不够的——那只能证明特征本身码相关，证明不了
    _capture 用的是它。
    """
    base_state, _, base_residue, _ = rig["capture"]()
    ref = ReferenceConfig.from_paper_anchor(T_RA_S)
    state, _, residue, _ = rig["capture"](reference=ref)
    same = state.switches.codes == base_state.switches.codes
    gain, _ = rig["physical"].affine(rig["plan"], state.features)
    bump = (residue - base_residue)[same] / gain[same]
    assert np.all(bump > 0.0)
    relative_spread = float(np.std(bump) / np.mean(np.abs(bump)))
    assert relative_spread > 0.05
    assert np.unique(np.round(bump, 12)).size > 100


def test_reference_residue_shift_matches_gain_times_settling_error(rig):
    """未跳档样本的残差变化必须逐点等于 g × 参考剩余误差。"""
    base_state, _, base_residue, _ = rig["capture"]()
    ref = ReferenceConfig.from_paper_anchor(T_RA_S)
    state, _, residue, _ = rig["capture"](reference=ref)
    same = state.switches.codes == base_state.switches.codes
    gain, _ = rig["physical"].affine(rig["plan"], state.features)
    expected = gain * dynamic_reference(
        ref.precharge_frac, ref.tau_s, ref.t_precharge_s, ref.t_ra_s, _swing(rig, state)
    )
    np.testing.assert_allclose(
        residue[same] - base_residue[same], expected[same], rtol=0, atol=1e-15
    )


def test_smaller_time_constant_leaves_less_error(rig):
    """τ 越小建立越快、剩余误差越小；方向写反是最常见的理解错误。"""
    base_state, _, base_residue, _ = rig["capture"]()
    scale = []
    for tau in (0.05, 0.18755, 1.0, 5.0):
        ref = ReferenceConfig(
            enabled=True,
            t_ra_s=T_RA_S,
            tau_s=tau * T_RA_S,
            precharge_frac=PRECHARGE_FRACTION_FROM_PAPER,
        )
        state, _, residue, _ = rig["capture"](reference=ref)
        same = state.switches.codes == base_state.switches.codes
        scale.append(float(np.std((residue - base_residue)[same])))
    assert all(later > earlier for earlier, later in pairwise(scale))


def test_paper_anchor_uses_the_reversed_time_constant():
    """from_paper_anchor 的 τ 必须等于 0.65·T_RA/ln(32)。"""
    ref = ReferenceConfig.from_paper_anchor(T_RA_S)
    assert ref.enabled
    assert ref.tau_s == pytest.approx(0.187550 * T_RA_S, rel=1e-5)
    assert ref.precharge_frac == PRECHARGE_FRACTION_FROM_PAPER


def test_enabled_reference_rejects_an_unset_precharge_fraction():
    """开了参考却没给预充比例时必须报错，不能默认从零摆幅开始。"""
    with pytest.raises(ValueError, match="precharge fraction 0"):
        ReferenceConfig(enabled=True, t_ra_s=T_RA_S, tau_s=1e-9)
