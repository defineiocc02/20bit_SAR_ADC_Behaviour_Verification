#!/usr/bin/env python3
"""验证：recip_q60 的近似倒数经余数修正后可精确实现 RND(2^60, G)。

背景
----
`recon_v21` 里的 `I = RND(2^60, G)` 用 64 位除法器实现，而 DC 在该除法器上
时序驱动优化不收敛（见 docs/v21/CHECKPOINT_I5_SYNTH_v4.md 的对照实验：
除数换常数后秒级收敛，4578 cells / 3468 µm²）。

`rtl/recip_q60.sv` 用「归一化 + 64 项 LUT + 两次 Newton」求倒数，输出是
**近似值**。本脚本回答两个问题：
  1. 近似误差有多大？（必须是可修正的有界误差）
  2. 能不能用「真实 G 算余数 + 一步修正」把它抬成精确的 RND？

结论（实测，见输出）：
  · 误差只在 {-1, 0, +1}
  · 用 (q+1)*G <= 2^60 双向夹逼定位 q = floor(2^60/G)，再按 2r>=G 做
    最近舍入，结果与精确 RND 完全一致

**本脚本不证明 RTL 实现正确** —— 它是算法层验证。RTL 侧的等价性
需要另外的门级/形式化证据；在那一层证据到位前，不能声称可以替换。

用法: python3 tools/check_recip_correction.py
"""
import random
import re
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
RTL = ROOT / 'rtl' / 'recip_q60.sv'

ONE30 = 1 << 30
TWO60 = 1 << 60
G_MIN = 1 << 34
G_MAX = 1 << 36


def load_lut():
    """从生产 RTL 原地抽取 LUT，避免手抄副本漂移。"""
    src = RTL.read_text()
    lut = [int(x) for x in re.findall(r"lut\[\s*\d+\]\s*=\s*31'd(\d+);", src)]
    if len(lut) != 64:
        print(f"FAIL: 从 {RTL.name} 抽取到 {len(lut)} 个 LUT 项，期望 64")
        sys.exit(1)
    return lut


def recip(g, lut):
    """逐字复现 rtl/recip_q60.sv 的算法（33 位有符号误差项）。"""
    b = g.bit_length()
    if b <= 36:
        m36 = (g << (36 - b)) & ((1 << 36) - 1)
    else:
        m36 = g >> (b - 36)
    m = m36 >> 5
    u = lut[(m >> 24) & 0x3F]
    for _ in range(2):
        e = ONE30 - ((m * u) >> 30)     # 有符号，可能是负数
        u = u + ((u * e) >> 30)
    return (u >> (b - 31)) if b >= 31 else (u << (31 - b))


def ref_rnd(n, d):
    """方案 §9.3 的 RND：符号对称、恰半离零。"""
    s = -1 if n < 0 else 1
    q, r = divmod(abs(n), d)
    return s * (q + (1 if 2 * r >= d else 0))


def corrected(g, lut):
    """近似倒数 + 双向夹逼 + 最近舍入。"""
    q = recip(g, lut)
    while q * g > TWO60:
        q -= 1
    while (q + 1) * g <= TWO60:
        q += 1
    r = TWO60 - q * g
    return q + (1 if 2 * r >= g else 0)


def main():
    lut = load_lut()
    print("=" * 68)
    print(f"LUT 来源: {RTL.relative_to(ROOT)}（原地抽取 64 项）")
    print("=" * 68)

    # 样本：边界 + 每个 b 区间的端点 + 大量随机
    Gs = set()
    for b in range(35, 37):
        lo = max(G_MIN, 1 << (b - 1))
        hi = min(G_MAX, (1 << b) - 1)
        Gs.update([lo, lo + 1, lo + 2, hi - 2, hi - 1, hi])
    Gs.update([G_MIN, G_MIN + 1, G_MAX - 1, G_MAX])
    rnd = random.Random(20261116)
    for _ in range(50000):
        Gs.add(rnd.randrange(G_MIN, G_MAX + 1))
    Gs = sorted(Gs)

    diffs = {}
    bad_raw = 0
    bad_corr = 0
    for g in Gs:
        u = recip(g, lut)
        d = u - (TWO60 // g)
        diffs[d] = diffs.get(d, 0) + 1
        if abs(d) > 1:
            bad_raw += 1
        if corrected(g, lut) != ref_rnd(TWO60, g):
            bad_corr += 1
            if bad_corr <= 3:
                print(f"  MISMATCH G={g}")

    print(f"样本数: {len(Gs)}（合同域 [{G_MIN}, {G_MAX}]）")
    print()
    print("1. 原始近似倒数误差（u - floor(2^60/G)）:")
    for k in sorted(diffs):
        print(f"     {k:+d}: {diffs[k]}")
    print(f"   超出 ±1 的样本: {bad_raw}")
    print()
    print(f"2. 余数修正后与精确 RND(2^60,G) 的失配: {bad_corr}")
    print()

    if bad_raw == 0 and bad_corr == 0:
        print("RESULT: RECIP_CORRECTION_EXACT")
        print("  近似倒数误差在 ±1 内，且经余数修正可精确实现 RND(2^60,G)。")
        print("  注意：这是**算法层**结论。RTL 实现与替换资格需单独的门级/")
        print("        形式化证据，以及同条件综合对比。")
        return 0
    print("RESULT: RECIP_CORRECTION_FAILS")
    return 1


if __name__ == '__main__':
    sys.exit(main())
