"""DEM 完整周期对照：N=17280（q 走完 0..1919，h_phase 覆盖 15 个值）。

为跑得动，先把每个 (片组,相位) 的 512 个 D_real(k) 预算成表，之后 O(1) 查表。
指标：去增益/偏置后的残差谱（SFDR 用输出谱单独算）。
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(__file__))
import numpy as np
from adi_model.digital_chain import (run_transaction, Transaction, dem_phases,
    _horizontal_columns, vertical_bits, N_COLUMNS)

Q = 1 << 30
VFS = 3.0
N = 17280
FIN = 691            # 与 N 互质，相干采样
AMP = 1.5


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


def nominal_w():
    w = {}
    for s_ in range(18):
        for j in range(N_COLUMNS):
            w[(s_, j)] = int(0.25 * Q)
        for i, wt in enumerate((0.5, 0.25, 0.125, 0.0625)):
            w[(s_, 16 + i)] = int(wt * Q / 8)
    return w


def mask_of(k, res, hp, vp):
    h = _horizontal_columns(k, res, hp)
    yb = vertical_bits(k, 8, vp)
    return [[yb[s] if j == res else h[j] for j in range(N_COLUMNS)]
            + [0, 0, (k >> 1) & 1, k & 1] for s in range(8)]


def d_of(w, m, ids):
    num = 0.0; den = 0.0
    for slot, sid in enumerate(ids):
        for j in range(N_COLUMNS):
            wj = w[(sid, j)]; den += wj
            num += (-VFS if m[slot][j] else VFS) * wj
        for j in (18, 19):
            if m[slot][j]:
                num += -2.0 * VFS * w[(sid, j)]
    return -num / den


def build_cache(w, groups, phases):
    cache = {}
    for g in groups:
        p = run_transaction(Transaction(0, 256, 0, 0, w), slice_group=g)
        ids = p['slice_ids']
        for ph in phases:
            cache[(g, ph)] = np.array([d_of(w, mask_of(k, *ph), ids) for k in range(512)])
    return cache


def main():
    wr = real_w(); nom = nominal_w()
    groups = list(range(9))
    phase_list = sorted({dem_phases(s) for s in range(0, N, 9)})
    print(f"组数 {len(groups)}  相位数 {len(phase_list)}  N={N}")
    cw = build_cache(wr, groups, phase_list)
    cn = build_cache(nom, groups, phase_list)
    d_ideal = np.array([-VFS + 2 * VFS * k / 512 for k in range(512)])

    def sadc(x):
        return min(511, max(0, int(np.floor((x + VFS) / (2 * VFS) * 512))))

    for name, rg, rp, g0 in (("A", False, False, 3), ("B", False, True, 3),
                             ("C", True, False, 3), ("D", True, True, 3)):
        errs = []
        for n in range(N):
            x = AMP * np.sin(2 * np.pi * FIN * n / N)
            g = (n % 9) if rg else g0
            ph = dem_phases(n) if rp else dem_phases(g0 * 9)
            k = sadc(x)
            dr = cw[(g, ph)][k]
            dn = cn[(g, ph)][k]
            errs.append((dn + (x - dr)) - x)
        e = np.array(errs)
        A = np.vstack([np.arange(N)]).T
        (a, b), *_ = np.linalg.lstsq(np.vstack([np.sin(2 * np.pi * FIN * np.arange(N) / N),
                                                np.ones(N)]).T, e, rcond=None)
        resid = e - (a * np.sin(2 * np.pi * FIN * np.arange(N) / N) + b)
        sp = np.abs(np.fft.rfft(resid * np.hanning(N))) ** 2
        sp[0:3] = 0
        pk = int(np.argmax(sp))
        print(f"  {name}: 原始rms {np.sqrt(np.mean(e**2))*1e6:8.1f} uV  "
              f"去增益后rms {np.sqrt(np.mean(resid**2))*1e6:8.1f} uV  "
              f"峰 bin {pk:5d}  峰/其余 {10*np.log10(sp[pk]/(sp.sum()-sp[pk])):6.2f} dB")


if __name__ == '__main__':
    main()
