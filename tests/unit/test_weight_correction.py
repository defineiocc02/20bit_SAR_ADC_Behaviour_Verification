"""D5 关键实验：非均匀权重下，正确系数必须能**恢复输入**。

只证明"改系数输出会变"是不够的——那可能只是把错误换个方向。
这里用独立前向模型生成观测，再分别用真系数与名义系数反解。
"""

from __future__ import annotations

import numpy as np

from adi_model.digital_chain import _horizontal_columns, vertical_bits

Q = 1 << 30
K, RES, X = 277, 3, 0.3
IDS = [14, 15, 16, 17, 0, 1, 2, 3]


def _masks():
    h = _horizontal_columns(K, RES, h_phase=3)
    y = vertical_bits(K, 8, v_phase=3)
    return [[y[s] if j == RES else h[j] for j in range(16)] for s in range(8)]


def _nominals():
    return {(s, j): (2.0 if j < 15 else 1.0) / 32.0 for s in range(18) for j in range(16)}


def _observe(masks, w):
    """独立前向：y = Σ w·(a·x)，a=1。"""
    return sum(w[(IDS[s], j)] for s in range(8) for j in range(16) if masks[s][j]) * X


def _spec_reconstruct(y, masks, w):
    """SPEC 方程：x̂ = y / Σw·a。"""
    den = sum(w[(IDS[s], j)] for s in range(8) for j in range(16) if masks[s][j])
    return y / den


def test_true_coefficients_recover_the_input_exactly():
    rng = np.random.default_rng(7)
    nom = _nominals()
    w_true = {k: v * (1.0 + rng.uniform(-0.03, 0.03)) for k, v in nom.items()}
    masks = _masks()
    y = _observe(masks, w_true)
    assert abs(_spec_reconstruct(y, masks, w_true) - X) < 1e-12


def test_nominal_coefficients_leave_a_measurable_error():
    """3% 失配下名义系数应留下明显残差——否则这个实验没有判别力。"""
    rng = np.random.default_rng(7)
    nom = _nominals()
    w_true = {k: v * (1.0 + rng.uniform(-0.03, 0.03)) for k, v in nom.items()}
    masks = _masks()
    y = _observe(masks, w_true)
    err = abs(_spec_reconstruct(y, masks, nom) - X)
    lsb20 = 6.0 / (1 << 20)
    assert err > 10 * lsb20, f"残差 {err:.3e} V 太小，实验不具判别力"
