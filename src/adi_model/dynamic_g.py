"""random18 的动态 G 与倒数单元（Python 模型，已修正两处定点错误）。

!!! 2026-11-14 修正说明 !!!
--------------------------
本文件 2026-11-14 16:49 之前的版本有两处错误，**此前"已验证 D_eff 误差
0.119 µV"的结论不成立**，实测该版本的倒数本身就算错了：

1. ``FRAC = 40`` 太小。G ≈ 2^35，``2^40/G ≈ 32``，整数粒度 1/32 = 3.1%。
   实测 ``reciprocal(34e9)`` 返回 1009，而精确值 32（相对误差 30.5 倍）；
   原因是 x0 = 2^(frac-b+1) = 2^5 使 ``g·x0/2^40 < 1``，Newton 迭代把
   归一化点推到了错误的分支上。
   → 正确做法：倒数必须是 Q60（``2^60/G ≈ 2^25``，相对分辨率 2^-25）。

2. ``deff()`` 的移位写成 ``>> (frac + 6)``，应为 ``>> (frac − 24)``。
   相差 2^30 倍，所以"0.119 µV"实际是 1e-9 量级的伪值。

修正后的实现见 ``adi_model/recip_q60.py``（归一化 + 64 项 LUT + 2 次 Newton，
与 ``rtl/recip_q60.sv`` 逐位对应）。本文件保留原来的函数名，改为调用该实现，
并提供 :func:`selftest` 给出真实误差。

结论（V0 系数，全 C(18,8)=43,758 片组 × 64 个 num 档，见 tools/check_p0_4.py）：
    倒数与 floor(2^60/G) 最大差 1 个最低位（相对 3.0e-8）；
    D_eff 与精确值最大差 2 个 Q24 单位 = 0.021 LSB（判据 ≤ 0.5 LSB）。
"""

from __future__ import annotations

from .recip_q60 import (  # noqa: F401  (reciprocal_exact 保留旧名)
    recip_exact_floor as reciprocal_exact,
    recip_q60 as reciprocal,
    deff_q24,
    deff_exact_q24,
)

FRAC = 60            # 倒数定点位宽（必须是 60，理由见文件头）
Q = 1 << 30          # 权重定点


def compute_g(w, ids, n_col: int = 16) -> int:
    """G = Σ_{选中的 8 片} Σ_{16 主列} w[s][j]（Q30 整数）。"""
    return sum(w[(s, j)] for s in ids for j in range(n_col))


def deff(wm: int, g: int, inv: int, frac: int = FRAC) -> int:
    """D_eff = (6·Wm − 3·G)·inv >> 36（Q24）。inv 必须是 Q60。"""
    if frac != FRAC:
        raise ValueError("只有 Q60 倒数与 recon_d5_w2 的 >>36 配套")
    return deff_q24(6 * wm - 3 * g, inv)


def selftest() -> dict:
    """典型量级下的真实误差（用于替换此前失效的 0.119 µV 结论）。"""
    g = 34_000_000_000
    inv_n = reciprocal(g)
    inv_e = reciprocal_exact(g)
    worst_q24 = 0
    for i in range(64):
        wm = int((0.5 + 0.5 * (i / 63 - 0.5)) * g)
        worst_q24 = max(worst_q24, abs(deff(wm, g, inv_n) - deff_exact_q24(6 * wm - 3 * g, g)))
    return dict(g=g, inv_newton=inv_n, inv_exact=inv_e,
                rel_err=abs(inv_n - inv_e) / inv_e,
                worst_deff_q24=worst_q24, worst_deff_lsb=worst_q24 / 96.0,
                worst_deff_uv=worst_q24 / 96.0 * 6e6 / (1 << 20))


if __name__ == "__main__":
    print(selftest())
