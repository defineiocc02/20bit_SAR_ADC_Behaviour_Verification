"""Q60 动态倒数单元模型（位精确，与 rtl/recip_q60.sv 一一对应）。

背景
----
D5 原式 ``D_eff = 6·Wm/G − 3`` 用的是 9 组预计算倒数。random18 模式下每笔
事务的 8 片组合不同，G 随之变化，9 组表失效。本模块给出**运行时**倒数：

    inv_q60 = 2^60 / G   （整数，向下取整；与 recon_d5_w2 的 ``>> 36`` 配套）

为什么必须是 Q60 而不是 Q40
---------------------------
G ≈ 2^35（8 片 × 16 列 × 0.25 Q30）。若用 Q40，``2^40/G ≈ 2^5 = 32``，
整数粒度为 1/32 = 3.1%——换算到 20 bit 输出是 16000+ LSB 的误差。
Q60 下 ``2^60/G ≈ 2^25``，相对分辨率 2^-25，D_eff 误差 ≈ 0.02 LSB。
（此前 ``dynamic_g.py`` 的 FRAC=40 与 ``>>(frac+6)`` 两处错误见该文件头注。）

算法（RTL 可实现的定长形式）
---------------------------
1. 归一化：b = bit_length(G)，M = G·2^(36−b) → M ∈ [2^35, 2^36)，m = M>>5 ∈ [2^30, 2^31)（Q30）。
2. 查表种子：idx = m[29:24]（6 bit，64 项），u0 = LUT[idx] ≈ 2^60/m 的中点值。
   区间宽度 1/64 ⇒ 初值相对误差 ≤ 0.79%。
3. 两次 Newton：``e = 2^30 − ((m·u)>>30)``；``u ← u + ((u·e)>>30)``。
   0.79% → 6.2e-5 → 3.9e-9。
4. 反归一化：``inv_q60 = u >> (b−31)``。

全部运算为正整数加、乘、算术右移（= floor），因此 Python 与 RTL 可逐位一致。
"""

from __future__ import annotations

LUT_BITS = 6
LUT_N = 1 << LUT_BITS          # 64
Q = 30                         # 归一化尾数的定点位数
ONE = 1 << Q                   # 2^30 = 尾数 1.0


def _build_lut() -> list[int]:
    """u0[i] = round(2^60 / m_mid)，m_mid 为第 i 个区间的尾数中点（Q30 整数）。"""
    out = []
    for i in range(LUT_N):
        # 区间 [1 + i/64, 1 + (i+1)/64)，中点 (i + 0.5)/64
        m_mid = ONE + ((2 * i + 1) << (Q - LUT_BITS - 1))   # 2^30 + (i+0.5)·2^24
        out.append((1 << (2 * Q)) // m_mid)                 # 2^60 // m_mid
    return out


LUT = _build_lut()

# 反向依赖检查：LUT 项必须落在 (2^29, 2^30] 内，否则说明归一化假设被破坏
assert all((1 << (Q - 1)) < v <= (1 << Q) for v in LUT), "LUT seed out of range"


def num_bits(g: int) -> int:
    return g.bit_length()


def recip_q60(g: int, iters: int = 2) -> int:
    """RTL 等价实现：返回 floor(2^60 / g)。g 必须 > 0。"""
    if g <= 0:
        return 0                       # RTL 侧同一合同：G=0 输出 0，由调用方报 err
    b = g.bit_length()
    # 归一化到 36 bit 尾数
    if b <= 36:
        m36 = g << (36 - b)
    else:
        m36 = g >> (b - 36)
    m = m36 >> 5                       # 31 bit，Q30，∈ [2^30, 2^31)
    u = LUT[(m >> (Q - LUT_BITS)) & (LUT_N - 1)]   # idx = m[29:24]（bit30 是隐含整数位）
    for _ in range(iters):
        e = ONE - ((m * u) >> Q)       # 残差（Q30）
        u = u + ((u * e) >> Q)         # Newton（u·e 可能为负 -> floor 与 RTL 算术右移一致）
    sh = b - 31
    inv = (u >> sh) if sh >= 0 else (u << (-sh))
    return inv


def recip_exact_round(g: int) -> int:
    """round(2^60/g)（就近）。与 digital_chain 的 (2·2^60 + G)//(2G) 同式。"""
    if g <= 0:
        return 0
    return ((1 << 61) + g) // (2 * g)


def recip_exact_floor(g: int) -> int:
    return (1 << 60) // g


def deff_q24(num: int, inv: int) -> int:
    """D_eff = num·inv >> 36（Q24），num = 6·Wm − 3·G。与 recon_d5_w2 S1 逐位一致。"""
    return (num * inv) >> 36


def deff_exact_q24(num: int, g: int) -> int:
    """精确参考：num·2^24/G，向负无穷取整（与 RTL 算术右移同口径）。"""
    if g <= 0:
        return 0
    return (num << 24) // g


def check_range(g_values, codes=64, max_err_q24: float = 48.0) -> dict:
    """对一组 G 做 D_eff 误差统计。

    ``max_err_q24`` 缺省 48：20 bit 输出 1 LSB = 96 个 Q24 单位，
    因此 48 对应 ±0.5 LSB。
    """
    worst = 0
    worst_g = 0
    n = 0
    for g in g_values:
        inv = recip_q60(g)
        for c in range(codes):
            num = int((c / (codes - 1) - 0.5) * 6 * g)      # 覆盖 ±3 满量程
            a = deff_q24(num, inv)
            b = deff_exact_q24(num, g)
            d = abs(a - b)
            n += 1
            if d > worst:
                worst, worst_g = d, g
    return dict(n=n, worst_q24=worst, worst_lsb=worst / 96.0, worst_g=worst_g,
                ok=worst <= max_err_q24)
