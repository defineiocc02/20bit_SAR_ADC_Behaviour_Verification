"""random18 的动态 G 与倒数单元（Python 原型）。

背景：D5 原式 D_eff = 6·Wm/G − 3 用的是 9 组预计算倒数。
动态选片后每笔的 8 片组合不同，G 随之变化（跨度 1.668%），
9 组表失效（错用导致 0.83% FS 误差 ≈ 8700 LSB）。

本模块给出：
  1. compute_g()    按真实选中的 8 片累加 G（128 次加法）
  2. reciprocal()   定长 Newton 迭代求 1/G，精度可配置
  3. deff()         用动态 G 与倒数算 D_eff，与精确值比对
"""

from __future__ import annotations

Q = 1 << 30          # 权重定点
FRAC = 40            # 倒数的小数位宽


def compute_g(w, ids, n_col=16) -> int:
    """G = Σ_{选中的 8 片} Σ_{16 主列} w[s][j]（Q30 整数）。"""
    return sum(w[(s, j)] for s in ids for j in range(n_col))


def reciprocal(g: int, frac: int = FRAC, iters: int = 4) -> int:
    """求 round(2^frac / g)，用 Newton 迭代，全程整数。

    初值取 2^frac 的移位近似，4 次迭代即可把相对误差压到 < 2^-60。
    """
    if g <= 0:
        raise ValueError("g must be positive")
    # 初值：g 落在 [2^(b-1), 2^b)，取 x0 = 2^(frac-b+1)，相对误差 < 2
    b = g.bit_length()
    k = frac - b + 1
    x = (1 << k) if k >= 0 else 1
    if x <= 0:
        x = 1
    target = 1 << frac
    for _ in range(iters):
        # x <- x * (2 - g*x/2^frac)
        x = (x * (2 * target - (g * x) // target)) // target
        if x <= 0:
            x = 1
    return x


def reciprocal_exact(g: int, frac: int = FRAC) -> int:
    """精确的 round(2^frac / g)，用于校验 Newton 结果。"""
    return (1 << frac) // g


def deff(wm: int, g: int, inv: int, frac: int = FRAC) -> int:
    """D_eff = 6·Wm/G − 3，返回 Q24。wm 为 Q30，inv 为 Q(frac)。"""
    # 6*Wm/G = 6*Wm*inv / 2^frac ；再折成 Q24
    num = 6 * wm * inv                       # Q30 * Q40 = Q70
    val = num >> (frac + 6)                  # -> Q24
    return val - (3 << 24)


def check(w, ids, wm_by_code, n_codes=64) -> dict:
    """验证：动态 G + Newton 倒数 与 精确 1/G 的 D_eff 差异。"""
    g = compute_g(w, ids)
    inv_newton = reciprocal(g)
    inv_exact = reciprocal_exact(g)
    err_inv = abs(inv_newton - inv_exact) / max(inv_exact, 1)

    worst = 0
    for k in range(n_codes):
        wm = wm_by_code(k)
        d_a = deff(wm, g, inv_newton)
        d_b = deff(wm, g, inv_exact)
        worst = max(worst, abs(d_a - d_b))
    return dict(g=g, rel_err=err_inv, worst_deff_lsb_q24=worst,
                worst_deff_uv=worst / (1 << 24) * 1e6)
