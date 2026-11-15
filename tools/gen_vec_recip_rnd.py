#!/usr/bin/env python3
"""生成 recip_rnd_v21 的金标向量：G -> I = RND(2^60, G)。

向量格式（每行）：G_q30  I_q60  err
用精确整数运算生成，与方案 §9.3 的定义逐字一致：
  RND(n,d)：符号对称、恰半离零。此处 n = 2^60 恒正。
"""
import random
import sys

G_MIN = 1 << 34
G_MAX = 1 << 36
TWO60 = 1 << 60


def rnd_pos(n, d):
    q, r = divmod(n, d)
    return q + (1 if 2 * r >= d else 0)


def main(path):
    Gs = set()
    # 合同域边界（含域外，用于验证 err）
    for g in (0, 1, G_MIN - 1, G_MIN, G_MIN + 1, G_MAX - 1, G_MAX, G_MAX + 1):
        if g >= 0:
            Gs.add(g)
    # 每个 b 区间端点
    for b in range(35, 37):
        lo = max(G_MIN, 1 << (b - 1))
        hi = min(G_MAX, (1 << b) - 1)
        for g in (lo, lo + 1, lo + 2, hi - 2, hi - 1, hi):
            Gs.add(g)
    # 名义值附近（G 跨度约 1.67%）
    for base in (32 << 30,):
        for d in range(-400, 401):
            g = base + d * (1 << 20)
            if G_MIN <= g <= G_MAX:
                Gs.add(g)
    # 随机
    rnd = random.Random(20261116)
    for _ in range(20000):
        Gs.add(rnd.randrange(G_MIN, G_MAX + 1))
    Gs = sorted(Gs)

    with open(path, 'w') as f:
        for g in Gs:
            in_dom = G_MIN <= g <= G_MAX
            if in_dom:
                f.write(f"{g} {rnd_pos(TWO60, g)} 0\n")
            else:
                f.write(f"{g} 0 1\n")
    print(f"写出 {len(Gs)} 行到 {path}")


if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else 'tb/v21/vec_recip_rnd.txt')
