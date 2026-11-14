"""ADC2 余差裕量审计（修正版）。

关键修正：
  δ(k) = D_real(k) − D_ideal(k)        是**失配偏移**，不是余差
  r_ideal ∈ [0, 2Δ)                     9 位粗量化的理想余差
  r_real  = r_ideal − δ(k)              真实余差
因此余差包络 = [0 − δ_max, 2Δ − δ_min]，不是 [δ_min, δ_max]。

逐笔计算 17280 笔实际转换，不预计算全状态 512 码。
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(__file__))
import numpy as np
from adi_model.digital_chain import (run_transaction, Transaction, dem_phases,
    _horizontal_columns, vertical_bits, N_COLUMNS)

Q = 1 << 30
VFS = 3.0
STEP = 2 * VFS / 512          # 11.71875 mV
ADC2_MAX = 0.45 / 32          # 14.0625 mV
N = 17280
FIN = 691
AMP = 1.5


def make_w(seed, gain_s, main_s, sub_s):
    r = np.random.default_rng(seed)
    w = {}
    for sid in range(18):
        g = 1.0 + r.normal(0, gain_s)
        for j in range(N_COLUMNS):
            w[(sid, j)] = 0.25 * g * (1 + r.normal(0, main_s))
        for i, wt in enumerate((0.5, 0.25, 0.125, 0.0625)):
            w[(sid, 16 + i)] = (wt / 8) * g * (1 + r.normal(0, sub_s))
    return {k: int(round(v * Q)) for k, v in w.items()}


def nominal_w():
    w = {}
    for s_ in range(18):
        for j in range(N_COLUMNS):
            w[(s_, j)] = int(0.25 * Q)
        for i, wt in enumerate((0.5, 0.25, 0.125, 0.0625)):
            w[(s_, 16 + i)] = int(wt * Q / 8)
    return w


def prepare(w):
    """预计算 9 个片组的 (ids, 每组 16 列权重, 子阵列权重)。"""
    tabs = []
    for g in range(9):
        p = run_transaction(Transaction(0, 256, 0, 0, w), slice_group=g)
        ids = p['slice_ids']
        main = [[w[(sid, j)] for j in range(N_COLUMNS)] for sid in ids]
        sub = [[w[(sid, 16 + i)] for i in range(4)] for sid in ids]
        tabs.append((ids, main, sub))
    return tabs


def d_of(main, sub, mask_main, mask_sub):
    den = 0.0
    for s in range(8):
        den += sum(main[s])
    num = 0.0
    for s in range(8):
        for j in range(N_COLUMNS):
            num += (-VFS if mask_main[s][j] else VFS) * main[s][j]
        for i in (2, 3):
            if mask_sub[s][i]:
                num += -2.0 * VFS * sub[s][i]
    return -num / den


def mask_of(k, res, hp, vp):
    h = _horizontal_columns(k, res, hp)
    yb = vertical_bits(k, 8, vp)
    mm = [[yb[s] if j == res else h[j] for j in range(N_COLUMNS)] for s in range(8)]
    ms = [[0, 0, (k >> 1) & 1, k & 1] for s in range(8)]
    return mm, ms


def audit(tabs, label, dem_on=True):
    r_real, deltas = [], []
    for n in range(N):
        x = AMP * np.sin(2 * np.pi * FIN * n / N)
        g = (n % 9) if dem_on else 3
        ph = dem_phases(n) if dem_on else dem_phases(27)
        ids, main, sub = tabs[g]
        k = min(511, max(0, int(np.floor((x + VFS) / (2 * VFS) * 512))))
        mm, ms = mask_of(k, *ph)
        d_r = d_of(main, sub, mm, ms)
        deltas.append(d_r - (-VFS + 2 * VFS * k / 512))
        r_real.append(x - d_r)
    r = np.array(r_real); d = np.array(deltas)
    neg = (r < 0).sum(); pos = (r > ADC2_MAX).sum(); ok = len(r) - neg - pos
    print(f"\n[{label}]  N={len(r)}")
    print(f"  失配偏移 δ:  min {d.min()*1e3:+.3f}  max {d.max()*1e3:+.3f}  "
          f"跨度 {(d.max()-d.min())*1e3:.3f} mV")
    print(f"  真实余差 r:  min {r.min()*1e3:+.3f}  max {r.max()*1e3:+.3f}  "
          f"跨度 {(r.max()-r.min())*1e3:.3f} mV")
    print(f"  理论包络:    [0−δmax, 2Δ−δmin] = [{-d.max()*1e3:+.3f}, "
          f"{(STEP-d.min())*1e3:+.3f}] mV  跨度 {(STEP-d.min()+d.max())*1e3:.3f} mV")
    print(f"  单边窗口 0~{ADC2_MAX*1e3:.2f} mV：负溢 {neg} ({neg/len(r)*100:.2f}%)  "
          f"正溢 {pos} ({pos/len(r)*100:.2f}%)  有效 {ok} ({ok/len(r)*100:.2f}%)")
    return d, r


if __name__ == '__main__':
    print(f"理想台阶 2Δ = {STEP*1e3:.3f} mV；单边 ADC2 窗口 0~{ADC2_MAX*1e3:.3f} mV")
    for tag, seed, gm, mn, sb in (
            ("压力失配 1%/1.5%/2.5%", 2026, 0.01, 0.015, 0.025),
            ("目标失配 0.1%/0.15%/0.25%（假设）", 2026, 0.001, 0.0015, 0.0025)):
        w = make_w(seed, gm, mn, sb)
        tabs = prepare(w)
        audit(tabs, tag, dem_on=True)
