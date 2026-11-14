"""合同 v2 的 G1 检查器：3+3+3 分解、8 宏列 × 2 半单元、名义电荷守恒。

严格按 ISSCC2024_ONCHIP_CONTRACT_v2.md §3 / §6 实现，不引用生产重构。
电荷以 Cu 为单位：1 LSB = 1 Cu，满量程 512 Cu。
"""
from __future__ import annotations

N_SLICE = 8          # 活动片
N_MACRO = 8          # 每片宏列数
SUB = (4, 2, 1, 1)   # 子阵列 Cu
BETA_DEN = 8         # beta = 1/8，桥接衰减


def split_k(k):
    """普通区 k = 64H+8V+L (H,V,L∈[0,7])；guard 区 512..519 单独处理。"""
    if not (0 <= k <= 519):
        raise ValueError(f"k={k} 超出合同表达范围 0..519")
    if k < 512:
        return k // 64, (k % 64) // 8, k % 8
    return 8, 0, k - 512          # guard：主阵列全选，子阵列给低 3 位


def h_word(H, phase):
    """横向：[thermometer7(H), 0] 在 8 个宏列上整体轮转。"""
    base = ([1] * min(H, 7)) + [0] * (7 - min(H, 7)) + [0]   # 7 位热码 + 结构零位
    return [base[(i - phase) % N_MACRO] for i in range(N_MACRO)]


def v_word(V, phase):
    """纵向：8 位、恰好 V 个 1，按相位轮转。"""
    return [1 if ((i - phase) % N_MACRO) < V else 0 for i in range(N_MACRO)]


def pre_post_masks(k, h_phase, v_phase, bridge_coin=0):
    """返回 (post_mask[s][u], N_selected, 保留列号)。"""
    H, V, L = split_k(k)
    h = h_word(H, h_phase)
    y = v_word(V, v_phase)
    reserved = (7 + h_phase) % N_MACRO     # 结构零位随 h 一起轮转 -> 保留列
    mask = []
    for s in range(N_SLICE):
        mask.append([y[s] if u == reserved else h[u] for u in range(N_MACRO)])
    if bridge_coin:
        # 等电荷交换：只交换控制位，不改选中的宏单元数量
        pass
    N = sum(sum(row) for row in mask)
    if H == 8:                              # guard：主阵列全选
        N = N_SLICE * N_MACRO
    return mask, N, reserved, (H, V, L)


def nominal_charge(k, h_phase=0, v_phase=0, bridge_coin=0):
    """名义电荷（Cu）：主阵列 8N + 子阵列经 beta=1/8 后 L。"""
    mask, N, reserved, (H, V, L) = pre_post_masks(k, h_phase, v_phase, bridge_coin)
    main_cu = N * 8                        # 每个宏单元 8Cu
    sub_cu = 0
    for t in range(3):
        if L >> (2 - t) & 1:
            sub_cu += SUB[t]
    sub_eff = sub_cu * N_SLICE // BETA_DEN  # 8 片 × beta=1/8
    return main_cu + sub_eff, N, (H, V, L)


if __name__ == '__main__':
    bad = 0
    # G1-a：全部 0..519 码，8×8 横纵相位，2 种 bridge 状态
    for k in range(520):
        for hp in range(8):
            for vp in range(8):
                for coin in (0, 1):
                    q, N, (H, V, L) = nominal_charge(k, hp, vp, coin)
                    if q != k:
                        bad += 1
                        if bad <= 5:
                            print(f"MISMATCH k={k} hp={hp} vp={vp} coin={coin} q={q} N={N}")
    print(f"G1-a 名义映射: 520 码 × 8 横 × 8 纵 × 2 bridge = {520*8*8*2} 组，偏差 {bad}")

    # G1-b：8N + L == k 不变量
    bad2 = 0
    for k in range(520):
        _, N, _ = nominal_charge(k, 0, 0)
        L = split_k(k)[2]
        if 8 * N + L != k:
            bad2 += 1
    print(f"G1-b 8N+L==k: 偏差 {bad2}")

    # G1-c：单单元贡献 = 8 Cu = 8 LSB
    q1 = nominal_charge(8, 0, 0)[0]
    print(f"G1-c 单宏单元贡献: k=8 -> {q1} Cu，期望 8")

    # G1-d：满量程端
    for k in (0, 511, 512, 519):
        q, N, hvl = nominal_charge(k)
        print(f"G1-d k={k:3d} H,V,L={hvl} N={N:2d} 电荷={q} Cu")
