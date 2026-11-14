"""多 seed × 多频的余差/溢出统计。

每个 (档位, seed) 建一次 (group, phase, k) → D 的缓存，三个频率共用。
逐笔只算自己的实际码，不预计算全状态 512 码。
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(__file__))
import numpy as np
from adi_model.digital_chain import (run_transaction, Transaction, dem_phases,
    _horizontal_columns, vertical_bits, N_COLUMNS)

Q = 1 << 30
VFS = 3.0
STEP = 2 * VFS / 512
ADC2_MAX = 0.45 / 32
N = 17280
FREQS = [691, 1093, 2417]
AMP = 1.5
LEVELS = {"压力 1%/1.5%/2.5%": (0.01, 0.015, 0.025),
          "目标 0.1%/0.15%/0.25%": (0.001, 0.0015, 0.0025)}
SEEDS = [2026, 7, 99, 1234, 555]


def make_w(seed, gm, mn, sb):
    r = np.random.default_rng(seed)
    w = {}
    for sid in range(18):
        g = 1.0 + r.normal(0, gm)
        for j in range(N_COLUMNS):
            w[(sid, j)] = 0.25 * g * (1 + r.normal(0, mn))
        for i, wt in enumerate((0.5, 0.25, 0.125, 0.0625)):
            w[(sid, 16 + i)] = (wt / 8) * g * (1 + r.normal(0, sb))
    return {k: int(round(v * Q)) for k, v in w.items()}


def prepare(w):
    tabs = []
    for g in range(9):
        p = run_transaction(Transaction(0, 256, 0, 0, w), slice_group=g)
        ids = p['slice_ids']
        main = [[w[(sid, j)] for j in range(N_COLUMNS)] for sid in ids]
        sub = [[w[(sid, 16 + i)] for i in range(4)] for sid in ids]
        tabs.append((main, sub))
    return tabs


def d_of(main, sub, res, hp, vp, k):
    h = _horizontal_columns(k, res, hp)
    yb = vertical_bits(k, 8, vp)
    den = 0.0
    for s in range(8):
        den += sum(main[s])
    num = 0.0
    for s in range(8):
        for j in range(N_COLUMNS):
            m = yb[s] if j == res else h[j]
            num += (-VFS if m else VFS) * main[s][j]
        if (k >> 1) & 1:
            num += -2.0 * VFS * sub[s][2]
        if k & 1:
            num += -2.0 * VFS * sub[s][3]
    return -num / den


def run_one(tabs, freq, cache):
    rs, ds = [], []
    for n in range(N):
        x = AMP * np.sin(2 * np.pi * freq * n / N)
        g = n % 9
        res, hp, vp = dem_phases(n)
        k = min(511, max(0, int(np.floor((x + VFS) / (2 * VFS) * 512))))
        key = (g, res, hp, vp, k)
        d = cache.get(key)
        if d is None:
            main, sub = tabs[g]
            d = d_of(main, sub, res, hp, vp, k)
            cache[key] = d
        rs.append(x - d)
        ds.append(d - (-VFS + 2 * VFS * k / 512))
    return np.array(rs), np.array(ds)


if __name__ == '__main__':
    print(f"N={N} amp={AMP}V  freqs={FREQS}  seeds={SEEDS}")
    print(f"{'档位':22s} {'seed':>5s} {'freq':>5s} {'负溢%':>7s} {'正溢%':>7s} "
          f"{'有效%':>7s} {'r_min':>8s} {'r_max':>8s}")
    agg = {}
    for lname, (gm, mn, sb) in LEVELS.items():
        for seed in SEEDS:
            w = make_w(seed, gm, mn, sb)
            tabs = prepare(w)
            cache = {}
            for f in FREQS:
                r, d = run_one(tabs, f, cache)
                neg = (r < 0).mean() * 100; pos = (r > ADC2_MAX).mean() * 100
                ok = 100 - neg - pos
                agg.setdefault(lname, []).append((neg, pos, ok))
                print(f"{lname:22s} {seed:5d} {f:5d} {neg:7.2f} {pos:7.2f} {ok:7.2f} "
                      f"{r.min()*1e3:8.2f} {r.max()*1e3:8.2f}")
    print("\n=== 汇总（15 组/档）===")
    for lname, rows in agg.items():
        a = np.array(rows)
        print(f"{lname}: 负溢 {a[:,0].mean():.2f}±{a[:,0].std():.2f}%  "
              f"正溢 {a[:,1].mean():.2f}±{a[:,1].std():.2f}%  "
              f"有效 {a[:,2].mean():.2f}±{a[:,2].std():.2f}%")
