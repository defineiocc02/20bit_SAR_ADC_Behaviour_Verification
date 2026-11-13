"""D5 收口：相容观测下，真系数恢复输入、名义系数留下失配误差。

关键点（此前几次实验都不成立的原因）：
1. 观测必须由**真权重**生成（G_true, K_true），不能拿名义 forward_code 的码配合真系数解码；
2. t 必须逐单元：主阵列 m=1/-3、m=0/+3；子阵列 a=0，m=1/-6、m=0/0；
3. 子阵列权重按 8 片归一（总和 0.5/0.25/0.125/0.0625），否则 K 被算 8 次。
"""

from __future__ import annotations

import numpy as np

from adi_model.digital_chain import (
    N_COLUMNS,
    Transaction,
    _horizontal_columns,
    run_transaction,
    vertical_bits,
)

Q = 1 << 30
IDS = [14, 15, 16, 17, 0, 1, 2, 3]
RES, K_CODE, X = 3, 277, 0.3


def _weights(scale=None):
    w = {(s, j): 0.25 for s in range(18) for j in range(16)}
    for i, wt in enumerate((0.5, 0.25, 0.125, 0.0625)):
        for s in range(18):
            w[(s, 16 + i)] = wt / 8
    if scale:
        w = {k: v * scale[k] for k, v in w.items()}
    return {k: int(round(v * Q)) for k, v in w.items()}


def _gk(w_q30):
    h = _horizontal_columns(K_CODE, RES, 3)
    yb = vertical_bits(K_CODE, 8, 3)
    masks = [[yb[s] if j == RES else h[j] for j in range(16)] for s in range(8)]
    sub = [(K_CODE & 3) >> (3 - i) & 1 for i in range(4)]
    G = K = 0.0
    for sl, sid in enumerate(IDS):
        for j in range(16):
            w = w_q30[(sid, j)] / Q
            G += w
            K += w * (-3.0 if masks[sl][j] else 3.0)
        for i in range(4):
            K += (w_q30[(sid, 16 + i)] / Q) * (-6.0 if sub[i] else 0.0)
    return G, K


def test_true_coefficients_recover_input_under_consistent_observation():
    rng = np.random.default_rng(7)
    base = _weights()
    true = _weights({k: 1 + rng.uniform(-0.03, 0.03) for k in base})
    G, K = _gk(true)
    adc = int(round((X + K / G) * 32 / (0.45 / 4096)))

    out = run_transaction(Transaction(3, 281, 0, adc, true))["x_weight_v"]
    assert abs(out - X) < 5e-6, f"真系数误差 {abs(out - X) * 1e6:.1f} uV"


def test_nominal_coefficients_leave_a_measurable_mismatch_error():
    rng = np.random.default_rng(7)
    base = _weights()
    true = _weights({k: 1 + rng.uniform(-0.03, 0.03) for k in base})
    G, K = _gk(true)
    adc = int(round((X + K / G) * 32 / (0.45 / 4096)))

    out = run_transaction(Transaction(3, 281, 0, adc, base))["x_weight_v"]
    assert abs(out - X) > 1e-3, f"名义系数误差仅 {abs(out - X) * 1e6:.1f} uV，无判别力"
