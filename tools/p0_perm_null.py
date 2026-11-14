"""P0-1 判据修正：用「排列零分布」代替 iid 白噪声带。

理由：v3 是有周期内的无放回排列（17280 = 9 × 1920），
不是 iid 抽样，±1.96/√N 不适用。

做法：生成 M 个与 v3 同长度、同映射的随机排列，统计每个排列在
lag 1..20 上 |自相关| 的最大值，取其 95 分位作为判据带。
v3 的实测值若落在该带内，判 PASS。
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
import numpy as np
from adi_model.digital_chain import dem_phases, _PHASE_TOTAL

N = 17280
LAGS = 20
M = 200


def ac_max(series, lags=LAGS):
    x = series.astype(float); x -= x.mean()
    d = np.dot(x, x)
    return max(abs(np.dot(x[:-L], x[L:]) / d) for L in range(1, lags + 1))


def axes_from(perm, n=N):
    idx = np.asarray(perm)[np.arange(n) % _PHASE_TOTAL]
    return idx // 120, (idx // 8) % 15, idx % 8


def null_band(m=M, seed=12345):
    rng = np.random.default_rng(seed)
    out = []
    for _ in range(m):
        p = rng.permutation(_PHASE_TOTAL)
        r, h, v = axes_from(p)
        out.append([ac_max(r), ac_max(h), ac_max(v)])
    return np.array(out)


if __name__ == '__main__':
    null = null_band()
    q95 = np.percentile(null, 95, axis=0)
    r, h, v = axes_from(list(range(_PHASE_TOTAL)))          # 恒等映射占位
    rr, hh, vv = (np.array([dem_phases(s)[i] for s in range(N)]) for i in range(3))
    real = np.array([ac_max(rr), ac_max(hh), ac_max(vv)])

    print(f"排列零分布（{M} 次随机排列，lag 1..20 的 |ac|max）:")
    for i, nm in enumerate(("reserved", "h_phase", "v_phase")):
        print(f"  {nm:9s}: 零分布 50%={np.percentile(null[:,i],50):.4f}  "
              f"95%={q95[i]:.4f}  最大={null[:,i].max():.4f} | v3 实测={real[i]:.4f} "
              f"-> {'PASS' if real[i] <= q95[i] else 'FAIL'}")
    print("P0-1（排列判据）:", "PASS" if (real <= q95).all() else "FAIL")
