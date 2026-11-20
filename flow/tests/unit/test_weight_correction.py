# =============================================================================
# 文件名     : test_weight_correction.py
# 模块名     : test_weight_correction
# 版本       : v1.0
# 功能       : 用显式连接矩阵检查非均匀系数对反解误差的影响。
# 设计说明   : 观测值由真系数与已知电平逐项相乘，反解只读取总信号增益。
#              未连接单元仍有正权重，可暴露把整个矩阵加进分母的错误。
#              真系数用例检查复原等式，名义系数用例用相同观测形成负对照。
# 变更记录   : 使用小型显式矩阵代替退役整链生成的掩码。
# =============================================================================
"""隔离信号连接和权重估计对线性反解的作用。"""

from __future__ import annotations

import numpy as np


# =============================================================================
# 1. 观测夹具
# =============================================================================
LEVEL_V = 0.3
CONNECTED = np.array([[True, False, False], [True, True, False]])
NOMINAL = np.full(CONNECTED.shape, 0.25)


def _case():
    """返回真权重和信号电荷观测；失配固定为相对幅度 ±3% 的均匀分布。"""
    rng = np.random.default_rng(7)
    actual = NOMINAL * (1.0 + rng.uniform(-0.03, 0.03, NOMINAL.shape))
    # 电压先与逐单元权重相乘，只有采样时接到信号的支路贡献观测。
    observation_v = ((actual * LEVEL_V) * CONNECTED).sum()
    return actual, observation_v


def _reconstruct(observation_v, weights):
    """按被选信号项的权重和换回输入电平。"""
    return observation_v / weights[CONNECTED].sum()


# =============================================================================
# 2. 同一观测的正负对照
# =============================================================================
def test_true_coefficients_recover_the_input_exactly():
    actual, observation_v = _case()
    assert abs(_reconstruct(observation_v, actual) - LEVEL_V) < 1e-12


def test_nominal_coefficients_leave_a_measurable_error():
    """名义权重在同一笔观测上留下可分辨的偏差。"""
    _, observation_v = _case()
    error_v = abs(_reconstruct(observation_v, NOMINAL) - LEVEL_V)
    lsb20_v = 6.0 / (1 << 20)
    assert error_v > 10 * lsb20_v, f"残差 {error_v:.3e} V 太小，无法区分系数"
