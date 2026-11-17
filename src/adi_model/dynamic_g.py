# =============================================================================
# 文件名     : dynamic_g.py
# 模块名     : adi_model.dynamic_g
# 版本       : v1.0
# 功能       : 用实际活动片的列权重构造增益并检查 Q24 码值。
# 设计说明   : 权重采用 Q30。片组、权重和列数由独立调用方提供。
#              用同一组 G 和 Wm 对比近似倒数与精确除法，定位算术移位误差。
# 变更记录   : 将早期全片组实测的入口固定到原 Git 提交。
# =============================================================================
"""活动片增益与 Q24 反解的独立参考。

早期以 Q40 倒数作精度估算会放大整数粒度，当前复用 recip_q60 的截断算法。
对同一组系数的历史片组遍历见 `git show 2224d30:tools/check_p0_4.py`。
本文件的自检使用 34,000,000,000 的增益整数覆盖一组逐点分子。
"""

from __future__ import annotations

from .recip_q60 import (  # noqa: F401  (reciprocal_exact 保留旧名)
    recip_exact_floor as reciprocal_exact,
    recip_q60 as reciprocal,
    deff_q24,
    deff_exact_q24,
)

# =============================================================================
# 1. 活动片权重和倒数尺度
# =============================================================================
FRAC = 60            # 倒数的二进制分度
Q = 1 << 30          # 权重定点


def compute_g(w, ids, n_col: int = 16) -> int:
    """将指定片组内前 n_col 个列权重相加，返回 Q30 整数。"""
    return sum(w[(s, j)] for s in ids for j in range(n_col))


def deff(wm: int, g: int, inv: int, frac: int = FRAC) -> int:
    """D_eff = (6·Wm − 3·G)·inv >> 36（Q24）。inv 必须是 Q60。"""
    if frac != FRAC:
        raise ValueError("只有 Q60 倒数与 recon_d5_w2 的 >>36 配套")
    return deff_q24(6 * wm - 3 * g, inv)


# =============================================================================
# 2. 典型增益的误差自检
# =============================================================================
def selftest() -> dict:
    """校验典型增益下近似倒数和精确整数除法的最大重构差。"""
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
