"""Follower 逐事件驱动：把 SADC 的 9 次判决变成 RDAC 的 7 个逐事件 mask。

要点（任务书 D2）：
  - 第 i 个事件只能用已决前缀，不得提前用后续判决位
  - 7 个事件的 mask 序列应逐步逼近最终 mask
  - 首个事件与最终 mask 必须不同（否则等于一次性加载，不是 Follower）
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(__file__))
import numpy as np
from adi_model.digital_chain import dem_phases, _horizontal_columns, vertical_bits, \
    run_transaction, Transaction, N_COLUMNS

Q = 1 << 30


def mask_of(sid, k):
    res, hp, vp = dem_phases(sid)
    h = _horizontal_columns(k, res, hp)
    yb = vertical_bits(k, 8, vp)
    m = []
    for s in range(8):
        row = [yb[s] if j == res else h[j] for j in range(N_COLUMNS)]
        row += [0, 0, (k >> 1) & 1, k & 1]
        m.append(row)
    return m


def prefixes(c):
    """Follower 的 7 个事件：第 i 个只用高 i 位（i=0 时全 0）。

    与 digital_chain.ResolvedPrefixes.from_final 同构：
    prefix_i = (c >> (6-i)) << (6-i)，i=0..6
    """
    return [(c >> (6 - i)) << (6 - i) for i in range(7)]


def main():
    r = np.random.default_rng(3)
    w = {(s, j): int(0.25 * Q) for s in range(18) for j in range(20)}
    bad_final = 0; same_first = 0; n = 0; maxdiff = 0
    for _ in range(60):
        sid = int(r.integers(0, 300)); c = int(r.integers(0, 512))
        ps = prefixes(c)
        ms = [mask_of(sid, p) for p in ps]
        final = mask_of(sid, c)
        n += 1
        # 1) 最后一个事件必须与最终 mask 完全一致
        if ms[-1] != final:
            bad_final += 1
        # 2) 第一个事件不应等于最终 mask（否则不是逐事件加载）
        if ms[0] == final:
            same_first += 1
        # 3) 事件间应单调逼近：与前缀的汉明距离不增
        d = [sum(abs(a - b) for ra, rb in zip(m, final) for a, b in zip(ra, rb)) for m in ms]
        if d != sorted(d, reverse=True):
            maxdiff += 1
    print(f"{n} 笔：末事件≠最终mask {bad_final}；首事件=最终mask {same_first}；汉明距离非单调 {maxdiff}")
    # 抽样展示
    sid, c = 42, 281
    ps = prefixes(c)
    print(f"示例 sid={sid} c={c}  前缀 {ps}")
    for i, p in enumerate(ps):
        m = mask_of(sid, p)
        on = sum(sum(row) for row in m)
        print(f"  event {i}: prefix={p:3d}  选通单元数={on}")


if __name__ == '__main__':
    main()
