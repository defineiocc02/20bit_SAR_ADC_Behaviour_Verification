"""D3 DEM / D4 Split Dither 的参考算法（唯一实现）。

合同：docs/ISSCC2024_ONCHIP_CONTRACT_v2_1_ADDENDUM.md
本模块只放**纯函数**，不含整链转换、失配、频谱实验。
"""
CF = 16.0
SUB = (4, 2, 1, 1)


def post_mask(k, h, v):
    """D3：k -> 8×20 物理 post-mask（bridge 交换之前）。

    k<512：k = 64H+8V+L；k>=512 走 guard（主阵列全选 + 子阵列）。
    """
    m = [[0] * 20 for _ in range(8)]
    if k >= 512:
        r = k - 512
        sub = [1, 1, 1, 1] if r >= 8 else [(r >> 2) & 1, (r >> 1) & 1, r & 1, 0]
        for s in range(8):
            m[s][:16] = [1] * 16
            m[s][16:] = sub
        return m
    H, V, L = k // 64, (k % 64) // 8, k % 8
    hw = [(1 if ((i - h) % 8) < H else 0) for i in range(8)]
    yw = [(1 if ((i - v) % 8) < V else 0) for i in range(8)]
    res = (7 + h) % 8
    for s in range(8):
        for u in range(8):
            b = yw[s] if u == res else hw[u]
            m[s][2 * u] = m[s][2 * u + 1] = b
        for t in range(3):
            m[s][16 + t] = (L >> (2 - t)) & 1
    return m


def bridge_swap(m, h, v, acq_seq, coin):
    """D3：合同 §6.1 等名义电荷交换。"""
    if not coin:
        return m
    res = (7 + h) % 8
    slot = v % 8
    j = 2 * res + ((acq_seq >> 3) & 1)
    a, b4 = m[slot][j], m[0][16]
    o = [r[:] for r in m]
    o[slot][j] = b4
    for s in range(8):
        o[s][16] = a
    return o


def dither_split(word):
    """D4：6 位扰动字 -> (U, Ld, T, f)。"""
    d1, d2, d3 = word & 1, (word >> 1) & 1, (word >> 2) & 1
    d4, d5, d6 = (word >> 3) & 1, (word >> 4) & 1, (word >> 5) & 1
    U = 4 * d6 + d4
    Ld = 2 * d5 + (4 * d3 + 2 * d2 + d1) / 8.0
    return U, Ld, U + Ld, 4 * d3 + 2 * d2 + d1


def pre(word, v):
    """D4：6 位字 -> 8 片 × 4 位物理采样预置开关位。

    pre_value[s] = J + ((s - v) % 8 < f)，J = U + 2*D5
    """
    U, Ld, T, f = dither_split(word)
    J = U + 2 * ((word >> 4) & 1)
    bits = []
    for s in range(8):
        val = J + (1 if ((s - v) % 8) < f else 0)
        bits.append([1, 1, 1, 1] if val >= 8 else
                    [(val >> 2) & 1, (val >> 1) & 1, val & 1, 0])
    return bits, T, U, Ld
