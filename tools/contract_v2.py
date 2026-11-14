"""合同 v2.1 的 G1 检查器（修订版）。

v2.0 检查器的缺陷（评审指出，属实）：
  对 guard 码只把统计量 N 改成 64，**没有把实际 mask 改成全选**，
  于是 nominal_charge() 打印的值正确，但返回的开关命令是错的。
  实测 k=512 报 512Cu，从返回 mask 重算只有 448Cu。

本版原则：**电荷一律从返回的 post-mask 重算**，不从中间统计量推。
"""
from __future__ import annotations
from fractions import Fraction

N_SLICE = 8
N_MACRO = 8
SUB = (4, 2, 1, 1)       # 子阵列 Cu
BETA_DEN = 8             # beta = 1/8
J_MAX = 520


def split_k(k):
    """普通区 k=64H+8V+L (H,V,L∈[0,7])；guard 区走独立分支。"""
    if not (0 <= k <= J_MAX):
        raise ValueError(f"k={k} 超出 0..{J_MAX}")
    if k < 512:
        return k // 64, (k % 64) // 8, k % 8, False
    return 0, 0, 0, True          # guard：H/V 无意义，单独走


def h_word(H, phase):
    base = ([1] * H) + [0] * (8 - H)      # 7 位热码 + 结构零位（共 8 位）
    return [base[(i - phase) % N_MACRO] for i in range(N_MACRO)]


def v_word(V, phase):
    return [1 if ((i - phase) % N_MACRO) < V else 0 for i in range(N_MACRO)]


def base_post(k, h_phase, v_phase):
    """生成 8×20 的普通/guard post-mask（bridge 交换之前）。"""
    H, V, L, guard = split_k(k)
    post = [[0] * 20 for _ in range(N_SLICE)]
    if guard:
        # 128 个主半单元全部为 1
        for s in range(N_SLICE):
            for j in range(16):
                post[s][j] = 1
        r = k - 512
        if r < 8:
            sub = [(r >> 2) & 1, (r >> 1) & 1, r & 1, 0]
        else:                       # k == 520
            sub = [1, 1, 1, 1]
        for s in range(N_SLICE):
            for t in range(4):
                post[s][16 + t] = sub[t]
        return post, (H, V, L, True)

    h = h_word(H, h_phase)
    y = v_word(V, v_phase)
    reserved = (7 + h_phase) % N_MACRO     # v2.1 确认
    for s in range(N_SLICE):
        for u in range(N_MACRO):
            bit = y[s] if u == reserved else h[u]
            post[s][2 * u] = bit
            post[s][2 * u + 1] = bit        # 宏单元展开成两个半单元
        for t in range(3):
            post[s][16 + t] = (L >> (2 - t)) & 1
        post[s][19] = 0
    return post, (H, V, L, False)


def apply_bridge(post, h_phase, v_phase, acq_seq, coin):
    """v2.1 §3：等名义电荷交换。返回新的 post 与是否真的发生交换。"""
    reserved = (7 + h_phase) % N_MACRO
    slot = v_phase % N_SLICE
    macro = reserved
    half = (acq_seq >> 3) & 1
    j = 2 * macro + half
    a = post[slot][j]
    b4 = post[0][16]
    out = [row[:] for row in post]
    changed = 0
    if coin:
        out[slot][j] = b4
        for s in range(N_SLICE):
            out[s][16] = a
        for s in range(N_SLICE):
            for jj in range(20):
                if out[s][jj] != post[s][jj]:
                    changed += 1
    return out, changed, (slot, macro, half, j, a, b4)


def charge_q8(post):
    """从实际 mask 重算电荷，单位 1/8 Cu（整数，无浮点）。"""
    q = 0
    for s in range(N_SLICE):
        for j in range(16):
            q += post[s][j] * 32          # 4Cu = 32/8 Cu
        for t in range(4):
            q += post[s][16 + t] * SUB[t]  # 已含 beta=1/8
    return q


def G_q8(post):
    """G：采样 Vin 的全部主单元权重之和（与 mask 无关，= 32 Cu）。"""
    return N_SLICE * 16 * 32


if __name__ == '__main__':
    # G1 锚点（v2.1 明示）
    anchors = {0: 0, 8: 8, 511: 511, 512: 512, 516: 516, 519: 519, 520: 520}
    bad = 0
    for k, want in anchors.items():
        post, _ = base_post(k, 0, 0)
        got = charge_q8(post) / 8
        ok = (got == want)
        bad += 0 if ok else 1
        print(f"  锚点 k={k:3d}  电荷={got:7.2f} Cu  期望 {want:3d}  {'OK' if ok else 'FAIL'}")

    # 全组合：521 码 × 8 横 × 8 纵 × 2 coin × 2 half
    total = 0; dev = 0
    for k in range(J_MAX + 1):
        for hp in range(8):
            for vp in range(8):
                for acq in range(2):          # 提供 half=0/1
                    for coin in (0, 1):
                        base, _ = base_post(k, hp, vp)
                        post, changed, _ = apply_bridge(base, hp, vp, acq * 8, coin)
                        total += 1
                        if charge_q8(post) != k * 8:
                            dev += 1
                            if dev <= 3:
                                print(f"  DEV k={k} hp={hp} vp={vp} half={acq} coin={coin} "
                                      f"q={charge_q8(post)/8}")
    print(f"G1 全组合: {total} 组，实际 mask 电荷偏差 {dev}")

    # v2.1 指定的最小验收例：k=8,h=0,v=0,half=0
    base, _ = base_post(8, 0, 0)
    post, changed, info = apply_bridge(base, 0, 0, 0, 1)
    print(f"最小例 k=8 coin=1: 改变 {changed} 个物理 bit（期望 9），"
          f"电荷 {charge_q8(post)/8} Cu（期望 8），G={G_q8(post)/8} Cu（期望 32）")
    print(f"  交换细节 slot/macro/half/j/a/b4 = {info}")
