# =============================================================================
# 文件名     : recip_q60.py
# 模块名     : adi_model.recip_q60
# 版本       : v1.0
# 功能       : 复算 RTL 的 Q60 近似倒数，以及独立整数除法的参考值。
# 设计说明   : 正增益整数先归一化为 Q30 尾数，再以 64 项表取初值。
#              Newton 步中的有符号移位按向下取整，保留与定点乘积一致的行为。
#              v21 整链的最终最近舍入由 recip_rnd_v21 的余数修正实现。
# 变更记录   : 清除退役整链及旧重构变体的交叉引用。
# =============================================================================
"""以整数步进复算 recip_q60.sv 的近似倒数。

1. 归一化 G，得到 [2^30, 2^31) 内的 Q30 尾数 m。
2. 取 m 的六位小数索引，取区间中点的倒数截断值作为 Newton 初值。
3. 以 e = 2^30 − ((m·u)>>30) 和 u ← u + ((u·e)>>30) 更新。
4. 反归一化，得到接近 2^60/G 的向下截断整数。

精确参考按整数除法计算。近似倒数的误差与最终 v21 余数修正分别验收。
"""

from __future__ import annotations

# =============================================================================
# 1. 尾数归一化与 Newton 迭代
# =============================================================================
LUT_BITS = 6
LUT_N = 1 << LUT_BITS          # 64
Q = 30                         # 归一化尾数的定点位数
ONE = 1 << Q                   # 2^30 = 尾数 1.0


def _build_lut() -> list[int]:
    """u0[i] = floor(2^60 / m_mid)，m_mid 为第 i 个区间的尾数中点（Q30 整数）。"""
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
    """返回整数 g 的最小无符号幅值位宽；零返回 0，符号位不计。"""
    return g.bit_length()


def recip_q60(g: int, iters: int = 2) -> int:
    """使用定点 Newton 整数步近似 2^60/g。非正输入返回零。"""
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


# =============================================================================
# 2. 精确参考、重构单位与误差检查
# =============================================================================
def recip_exact_round(g: int) -> int:
    """将正整数比 2^60/g 舍入到最近整数，正半点向上。"""
    if g <= 0:
        return 0
    return ((1 << 61) + g) // (2 * g)


def recip_exact_floor(g: int) -> int:
    """按整数除法返回 floor(2^60 / g)，供正增益码 g 的定点参考比较。

    该参考不使用查表或迭代。非正增益用与 RTL 近似实现一致的零倒数，
    交易是否错误仍由调用方依照增益合同报告。
    """
    if g <= 0:
        return 0
    return (1 << 60) // g


def deff_q24(num: int, inv: int) -> int:
    """将 Q30 分子和整数倒数的乘积截为 Q24，保留有符号移位。"""
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
