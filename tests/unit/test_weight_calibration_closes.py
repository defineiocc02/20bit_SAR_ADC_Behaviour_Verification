"""D5 收口：相容观测下，真系数恢复输入、名义系数留下失配误差。

要点：
1. 观测由**真权重**生成（G_true, K_true），不能拿名义码配合真系数解码；
2. t 逐单元：主 m=1/−3、m=0/+3；子 a=0，m=1/−6、m=0/0；
3. 子阵列权重按 8 片归一，否则 K 被算 8 次；
4. 相位与 DUT 共用 dem_phases()，避免两份推导漂移；
5. 输入由 D_eff 反推 + 10 mV 余差，保证落在 ADC2 窗口内。
"""

from __future__ import annotations

import numpy as np

from adi_model.digital_chain import (
    Transaction,
    _horizontal_columns,
    dem_phases,
    run_transaction,
    vertical_bits,
)

Q = 1 << 30
IDS = [14, 15, 16, 17, 0, 1, 2, 3]
K_CODE, SID = 282, 3
R_OFFSET = 0.01


def _weights(scale=None):
    w = {(s, j): 0.25 for s in range(18) for j in range(16)}
    for i, wt in enumerate((0.5, 0.25, 0.125, 0.0625)):
        for s in range(18):
            w[(s, 16 + i)] = wt / 8
    if scale:
        w = {k: v * scale[k] for k, v in w.items()}
    return {k: int(round(v * Q)) for k, v in w.items()}


def _gk(w_q30, sid=SID):
    res, hp, vp = dem_phases(sid)
    h = _horizontal_columns(K_CODE, res, hp)
    yb = vertical_bits(K_CODE, 8, vp)
    masks = [[yb[s] if j == res else h[j] for j in range(16)] for s in range(8)]
    sub = [(K_CODE & 3) >> (3 - i) & 1 for i in range(4)]
    G = K = 0.0
    for sl, sid_s in enumerate(IDS):
        for j in range(16):
            w = w_q30[(sid_s, j)] / Q
            G += w
            K += w * (-3.0 if masks[sl][j] else 3.0)
        for i in range(4):
            K += (w_q30[(sid_s, 16 + i)] / Q) * (-6.0 if sub[i] else 0.0)
    return G, K


def _case():
    rng = np.random.default_rng(7)
    base = _weights()
    true = _weights({k: 1 + rng.uniform(-0.03, 0.03) for k in base})
    G, K = _gk(true)
    x_in = -K / G + R_OFFSET
    adc = int(round(R_OFFSET * 32 / (0.45 / 4096)))
    return base, true, x_in, adc


def test_true_coefficients_recover_input_under_consistent_observation():
    _, true, x_in, adc = _case()
    out = run_transaction(Transaction(SID, K_CODE, 0, adc, true))["x_weight_v"]
    assert abs(out - x_in) < 5e-6, f"真系数误差 {abs(out - x_in) * 1e6:.1f} uV"


def test_nominal_coefficients_leave_a_measurable_mismatch_error():
    base, _, x_in, adc = _case()
    out = run_transaction(Transaction(SID, K_CODE, 0, adc, base))["x_weight_v"]
    assert abs(out - x_in) > 5e-5, f"名义系数误差仅 {abs(out - x_in) * 1e6:.1f} uV，无判别力"
