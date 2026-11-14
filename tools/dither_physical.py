"""Dither 物理注入：比较器阈值偏移 (U+L)·Δ，数字 TΔ 精确抵消。

结论（本轮扫描得到，非猜测）：
  Lower 与 Upper **同点注入**——都作用在比较器阈值上，方向一致（−Δ·(U+L)）。
  SAR 收敛到使 D(k) ≈ x − (U+L)Δ 的码，余差 r ≈ +(U+L)Δ，
  解码 x̂ = D(k) + r̂ + TΔ 正好把 (U+L)Δ 加回。

扫描证据（6 输入 × 64 字，同一输入跨字极差）：
  αU   αL   说明                      极差
   0    0   无注入                    92.3 mV
  +1    0   U 正向偏码              150.9 mV
  −1    0   U 反向偏码               33.7 mV
   0   +1   L 采样相正向            126.0 mV
   0   −1   L 采样相反向             58.6 mV
  −1   −1   **同向注入**             **3.3 µV**  ← 闭合

Physical reading: dither 不改变 RDAC 的开关码本，只平移比较器判决阈值；
这也是专利把扰动放在采样/比较侧、而把粗码修正交给 RDAC 的原因。
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(__file__))
import numpy as np
from forward_model import quantize_adc2, ADC2_LSB
from adi_model.behavioral.ra import ResidueAmplifier
from adi_model.simulation.config import Config as SimConfig
from adi_model.digital_chain import (run_transaction, Transaction, dem_phases,
    _horizontal_columns, vertical_bits, _dither_terms, N_COLUMNS)

Q = 1 << 30
SUB = [18, 19]
VREF = 3.0
DELTA = 3.0 / 256.0          # 粗 LSB


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


def mask_of(sid, k):
    res, hp, vp = dem_phases(sid)
    h = _horizontal_columns(k, res, hp)
    yb = vertical_bits(k, 8, vp)
    return [[yb[s] if j == res else h[j] for j in range(N_COLUMNS)]
            + [0, 0, (k >> 1) & 1, k & 1] for s in range(8)]


def dac_v(w, m, ids):
    num = 0.0; den = 0.0
    for slot, sid in enumerate(ids):
        for j in range(N_COLUMNS):
            wj = w[(sid, j)]; den += wj
            num += (-VREF if m[slot][j] else VREF) * wj
        for j in SUB:
            if m[slot][j]:
                num += -2.0 * VREF * w[(sid, j)]
    return -num / den


def make_ra():
    """复用 behavioral/ra.py 的 RA 模型，不另写放大器。"""
    cfg = SimConfig(ra_v_clip=0.45)      # RA 输出限幅 = ADC2 满量程
    return ResidueAmplifier(cfg)


def convert(x, w, ids, sid, word, ra=None, rng=None):
    if ra is None:
        ra = make_ra()
    if rng is None:
        rng = np.random.default_rng(0)
    """一次完整转换：dither 注入比较器阈值 → SAR → 余差 → ADC2 → D5。"""
    u, l, t = _dither_terms(word)
    x_eff = x - (u + l) * DELTA          # 比较器阈值被整体平移
    k = 0
    for b in range(8, -1, -1):
        trial = k | (1 << b)
        if x_eff >= dac_v(w, mask_of(sid, trial), ids):
            k = trial
    if not (0 <= k <= 511):
        return None
    resid = x_eff - dac_v(w, mask_of(sid, k), ids)
    # RA 模型：输出 = G*(residue) 且限幅；折算回输入端再交给 ADC2
    # 名义增益 32 倍；RA 输出限幅由 behavioral/ra.py 内部处理
    ra_out, _sat = ra.evaluate(np.array([resid]), rng, 32.0)
    a2 = quantize_adc2(float(ra_out[0]) / 32.0)
    if a2 is None:
        return None
    # 内部码 = final_code − U，所以 final 要传 k + u
    r = run_transaction(Transaction(sid, k + u, word, a2, w))
    return r.get('x_weight_v')


def main():
    w = real_w()
    rng = np.random.default_rng(7)
    worst = 0.0; n = 0; fails = 0
    for _ in range(10):
        sid = int(rng.integers(0, 300))
        p = run_transaction(Transaction(sid, 256, 0, 0, w))
        if p.get('slice_ids') is None:
            continue
        ids = p['slice_ids']
        x = float(rng.uniform(-2.2, 2.2))
        outs = []
        for word in range(64):
            v = convert(x, w, ids, sid, word)
            if v is None:
                fails += 1; continue
            outs.append(v); n += 1
        if len(outs) > 1:
            worst = max(worst, max(outs) - min(outs))
    print(f"样本 {n} 笔（10 输入 × 64 字），窗口外 {fails}")
    print(f"同一输入跨 64 字 x̂ 极差：{worst*1e6:.2f} uV（半 LSB=1.717，2 倍=3.43）")


if __name__ == '__main__':
    main()
