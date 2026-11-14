#!/usr/bin/env python3
"""P0-4：动态 G + 实时倒数的 D_eff 闭合检查（全 C(18,8) = 43,758 片组）。

判据（docs/ALGORITHM_BASELINE_AUDIT.md §三 P0-4）：
    ``D_eff`` 用动态 G + 实时倒数，与精确值差 ≤ 0.5 LSB（20 bit）
    在全 43,758 组合上验证。

1 LSB(20 bit) = 96 个 Q24 单位（code20 = round(x_q24/96)，满量程 6 单位 = 2^20 LSB），
故判据等价于 |ΔD_eff| ≤ 48 Q24 单位。

三层证据：
  A. 倒数本身：recip_q60 与 floor(2^60/G) 逐位比（最大差 1）
  B. D_eff：每片组 × 64 个 num 档（覆盖 ±3 满量程），与精确 num·2^24/G 比
  C. 与"9 组表错用"对照：量化 random18 下沿用 legacy9 倒数会造成多大误差

用法：``PYTHONPATH=src:tools python3 tools/check_p0_4.py``
"""

from __future__ import annotations

import itertools
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(__file__))

from adi_model.recip_q60 import (  # noqa: E402
    deff_exact_q24,
    deff_q24,
    recip_exact_floor,
    recip_q60,
)
from gen_vec_topw import N_COL, N_SLICES, build_weights, row_sum  # noqa: E402

N_ACTIVE = 8
LSB_Q24 = 96            # 20 bit 输出 1 LSB 对应的 Q24 单位
NUM_STEPS = 64


def main(ver: int = 0) -> int:
    W = build_weights()
    rs = [row_sum(W[ver], s) for s in range(N_SLICES)]
    print(f"V{ver}: 各片行和 {min(rs)} .. {max(rs)}（极差 {max(rs)-min(rs)}）")

    combos = list(itertools.combinations(range(N_SLICES), N_ACTIVE))
    print(f"片组总数 C(18,8) = {len(combos)}")

    # ---- A. 倒数逐位 ----
    inv_off_by_1 = 0
    gs = []
    worst_inv_rel = 0.0
    for ids in combos:
        g = sum(rs[s] for s in ids)
        gs.append(g)
        a = recip_q60(g)
        e = recip_exact_floor(g)
        d = abs(a - e)
        if d:
            inv_off_by_1 += 1
        worst_inv_rel = max(worst_inv_rel, d / e)
    print(f"A 倒数：{len(gs)} 组，其中 {inv_off_by_1} 组差 1 个最低位，"
          f"最大相对误差 {worst_inv_rel:.3e}")

    # ---- B. D_eff 闭合 ----
    worst_q24 = 0
    worst_case = None
    n_eval = 0
    for ids, g in zip(combos, gs):
        inv = recip_q60(g)
        for i in range(NUM_STEPS):
            frac = (i + 0.5) / NUM_STEPS - 0.5           # -0.5 .. +0.5
            num = int(frac * 6 * g)                      # ±3 满量程
            a = deff_q24(num, inv)
            b = deff_exact_q24(num, g)
            d = abs(a - b)
            n_eval += 1
            if d > worst_q24:
                worst_q24 = d
                worst_case = (ids, g, num)
    print(f"B D_eff：{n_eval} 个 (片组, num) 点，最大偏差 {worst_q24} Q24 = "
          f"{worst_q24/LSB_Q24:.4f} LSB（判据 ≤ 0.5 LSB）")

    # ---- C. 对照：random18 下错用 legacy9 的固定倒数 ----
    # 取 legacy9 第 0 组的倒数（G0 = 前 8 片连续片组），套到所有片组上
    ids0 = [(8 * 1 + s) % N_SLICES for s in range(N_ACTIVE)]
    g0 = sum(rs[s] for s in ids0)
    inv0 = recip_exact_floor(g0)
    worst_c = 0
    for g in gs:
        for i in range(NUM_STEPS):
            frac = (i + 0.5) / NUM_STEPS - 0.5
            num = int(frac * 6 * g)
            worst_c = max(worst_c, abs(deff_q24(num, inv0) - deff_exact_q24(num, g)))
    print(f"C 对照：沿用单一固定倒数（G0={g0}）最大偏差 {worst_c} Q24 = "
          f"{worst_c/LSB_Q24:.1f} LSB = {worst_c/(96*1048576)*100:.3f}% FS")

    ok = worst_q24 <= 48
    print("P0-4:", "PASS" if ok else "FAIL", f"（A/B 口径；C 仅作对照）")
    if worst_case:
        ids, g, num = worst_case
        print("  最差用例：ids=", ids, "G=", g, "num=", num)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
