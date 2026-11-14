"""P0-1：DEM 相位三轴的独立性与白噪声性检验。

检验项：
  1. 每轴自相关：lag 1..20 是否落在 95% 置信带 ±1.96/sqrt(N)
  2. 轴间互信息：与置换零分布的 95 分位比较
  3. 联合覆盖：1920 组合的覆盖数与各组合样本数偏差
  4. 与旧版本的关键关联式 v=(r+2)%8 的违反率
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
import numpy as np
from adi_model.digital_chain import dem_phases


def axes(n):
    a = np.array([dem_phases(s) for s in range(n)])
    return a[:, 0], a[:, 1], a[:, 2]


def autocorr(x, lags=20):
    x = x.astype(float); x = x - x.mean()
    d = np.dot(x, x)
    return np.array([np.dot(x[:-L], x[L:]) / d for L in range(1, lags + 1)])


def mutual_info(a, b):
    ax = np.unique(a); bx = np.unique(b)
    n = len(a)
    pab = np.zeros((len(ax), len(bx)))
    ia = np.searchsorted(ax, a); ib = np.searchsorted(bx, b)
    np.add.at(pab, (ia, ib), 1)
    pab /= n
    pa = pab.sum(1, keepdims=True); pb = pab.sum(0, keepdims=True)
    with np.errstate(divide='ignore', invalid='ignore'):
        m = pab * np.log2(pab / (pa * pb))
    return float(np.nansum(m))


def main(n=17280, seed_nulls=200):
    r, h, v = axes(n)
    band = 1.96 / np.sqrt(n)
    print(f"N={n}  95% 白噪声自相关带 = ±{band:.5f}")
    ok = True
    for name, ser in (("reserved", r), ("h_phase", h), ("v_phase", v)):
        ac = autocorr(ser)
        bad = int((np.abs(ac) > band).sum())
        print(f"  {name:9s}: |ac|max={np.abs(ac).max():.5f}  超带点数 {bad}/20  "
              f"{'OK' if bad == 0 else 'FAIL'}")
        ok &= (bad == 0)

    # 互信息 vs 置换零分布
    rng = np.random.default_rng(0)
    for n1, s1, n2, s2 in (("reserved", r, "h_phase", h),
                           ("reserved", r, "v_phase", v),
                           ("h_phase", h, "v_phase", v)):
        mi = mutual_info(s1, s2)
        null = [mutual_info(s1, rng.permutation(s2)) for _ in range(seed_nulls)]
        q95 = np.percentile(null, 95)
        good = mi <= q95
        print(f"  MI({n1},{n2}) = {mi:.5f}  置换95分位 {q95:.5f}  "
              f"{'OK' if good else 'FAIL（存在依赖）'}")
        ok &= good

    # 联合覆盖
    triples = list(zip(r.tolist(), h.tolist(), v.tolist()))
    uniq = len(set(triples))
    from collections import Counter
    c = Counter(triples)
    vals = np.array(list(c.values()))
    print(f"  联合覆盖: {uniq}/1920 = {uniq/1920*100:.1f}%  "
          f"每组合 {vals.min()}~{vals.max()}  "
          f"{'OK' if uniq/1920 >= 0.9 else 'FAIL(<90%)'}")

    # 旧关联式
    bad = sum(1 for rr, vv in zip(r, v) if vv == (rr + 2) % 8)
    print(f"  v=(r+2)%8 违反率: {(1-bad/n)*100:.1f}% 实际满足 "
          f"{bad}/{n} = {bad/n*100:.2f}%（独立应 ≈12.5%）")

    print("P0-1:", "PASS" if ok else "FAIL（红线项）")
    return ok


if __name__ == '__main__':
    main()
