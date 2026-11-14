"""四级回归：理想权重 → 非均匀权重 → ADC2 量化 → DEM/Dither 实际开关。

每级都用独立电荷前向（forward_model），数字侧只拿 k / adc2 / dither / 冻结系数。
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(__file__))
import numpy as np
from forward_model import dac_from_connection, quantize_adc2, ADC2_LSB
from adi_model.digital_chain import (run_transaction, Transaction, dem_phases,
    _horizontal_columns, vertical_bits, _dither_terms, N_COLUMNS)

Q = 1 << 30
SUB = [18, 19]


def ideal_w():
    w = {}
    for s in range(18):
        for j in range(N_COLUMNS):
            w[(s, j)] = int(0.25 * Q)
        for i, wt in enumerate((0.5, 0.25, 0.125, 0.0625)):
            w[(s, 16 + i)] = int(wt * Q / 8)
    return w


def real_w(seed=2026):
    r = np.random.default_rng(seed)
    w = {}
    for sid in range(18):
        g = 1.0 + r.normal(0, 0.01)
        for j in range(N_COLUMNS):
            w[(sid, j)] = 0.25 * g * (1 + r.normal(0, 0.015))
        for i, wt in enumerate((0.5, 0.25, 0.125, 0.0625)):
            w[(sid, 16 + i)] = (wt / 8) * g * (1 + r.normal(0, 0.025))
    return {k: int(round(v * Q)) for k, v in w.items()}


def masks_for(sid, k):
    res, hp, vp = dem_phases(sid)
    h = _horizontal_columns(k, res, hp)
    yb = vertical_bits(k, 8, vp)
    m = []
    for s in range(8):
        row = [yb[s] if j == res else h[j] for j in range(N_COLUMNS)]
        row += [0, 0, (k >> 1) & 1, k & 1]
        m.append(row)
    return m


def run_level(name, coef_w, fwd_w, words, n=40, seed=7, resid_lo=0.001, resid_hi=0.013):
    rng = np.random.default_rng(seed)
    err = []
    tries = 0
    while len(err) < n and tries < 4000:
        tries += 1
        sid = int(rng.integers(0, 300)); k = int(rng.integers(0, 512))
        word = int(rng.choice(words))
        u, l, t = _dither_terms(word)
        if k + u > 511:
            continue
        p = run_transaction(Transaction(sid, k + u, word, 0, fwd_w))
        if p.get('dac_hw_q24') is None:
            continue
        ids = p['slice_ids']
        m = masks_for(sid, k)                      # 内部码 k
        x0 = -dac_from_connection(fwd_w, m, ids, N_COLUMNS, SUB)
        resid = float(rng.uniform(resid_lo, resid_hi))
        x = x0 + t * 3.0 / 256.0 + resid
        a2 = quantize_adc2(resid)
        if a2 is None:
            continue
        r = run_transaction(Transaction(sid, k + u, word, a2, coef_w))
        if r.get('x_weight_v') is None:
            continue
        err.append(abs(r['x_weight_v'] - x))
    if not err:
        print(f"{name:28s}  n=0  (无有效样本)")
        return
    print(f"{name:28s}  n={len(err):3d}  max={max(err)*1e6:8.2f} uV  rms={np.sqrt(np.mean(np.square(err)))*1e6:7.2f} uV")


if __name__ == '__main__':
    wi = ideal_w(); wr = real_w()
    print("=== L1 理想权重（理想系数解码） ===")
    run_level("L1 理想/理想", wi, wi, [0])
    print("=== L2 非均匀权重 ===")
    run_level("L2 真实例/真系数", wr, wr, [0])
    run_level("L2 真实例/名义系数", wi, wr, [0])
    print("=== L3 ADC2 量化边界 ===")
    run_level("L3 余差 0~1LSB", wr, wr, [0], resid_lo=0.0, resid_hi=ADC2_LSB/32)
    run_level("L3 余差近满量程", wr, wr, [0], resid_lo=0.0135, resid_hi=0.01406)
    print("=== L4 DEM / Dither 实际开关 ===")
    run_level("L4 dither=0", wr, wr, [0])
    run_level("L4 Lower 仅变 (D3..D0)", wr, wr, [1, 2, 4, 8, 15])
    run_level("L4 Upper 仅变 (D6/D5)", wr, wr, [0, 32, 16, 48])
    run_level("L4 全 64 字", wr, wr, list(range(64)))
