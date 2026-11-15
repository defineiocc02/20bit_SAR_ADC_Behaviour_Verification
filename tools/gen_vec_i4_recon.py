#!/usr/bin/env python3
"""生成 I4 重构的逐位金标（方案 v1.4 §9.3 整数 oracle）。

**必须逐字采用 §9.3 的规则**，不得"改进"舍入方式：
    RND(n,d)：符号对称的最近舍入，恰半离零
    Y  = -3145728 + 1536*raw + 768
    Z  = Y - b                     （alpha == 2^30 旁路）
    Z  = RND((Y-b)*2^30, alpha)    （其余合法 alpha）
    N  = Z*64 + 6*Wm - 3*G - 6*Wp
    I  = RND(2^60, G)
    X  = floor((N*I)/2^36)
    Q  = RND(X, 96)
    unclipped = Q + 524288
    code = min(1048575, max(0, unclipped))
    out_sat = (code != unclipped)

输出格式（每行）：raw Wm_q30 G_q30 Wp_q30 alpha_q30 b_q24 code sat err
用法：python3 tools/gen_vec_i4_recon.py > tb/v21/vec_i4_recon.txt
"""
from __future__ import annotations

import sys

Q30 = 1 << 30


def RND(n: int, d: int) -> int:
    """符号对称的最近舍入，恰半时离零（§9.3）。"""
    s = -1 if n < 0 else 1
    q, r = divmod(abs(n), d)
    return s * (q + (1 if 2 * r >= d else 0))


def recon(raw: int, Wm: int, G: int, Wp: int,
          alpha: int = Q30, b: int = 0) -> tuple[int, int, int]:
    """返回 (code, sat, err)。"""
    # 方案 §9.2 的合法域：0.5 <= alpha <= 2，即 alpha_q30 in [2^29, 2^31]。
    # alpha 越小，Z = RND((Y-b)*2^30, alpha) 越大，会突破 |X| < 2^31 的界，
    # 使 32 位 RND 溢出。所以这里把域外值判为 err（与 G=0 同等处理），
    # 而不是生成一个硬件无法表达的向量。
    bad = (G == 0) or (alpha < (Q30 // 2)) or (alpha > (2 * Q30))
    if bad:
        return 0, 0, 1

    Y = -3145728 + 1536 * raw + 768
    if alpha == Q30:
        Z = Y - b
    else:
        Z = RND((Y - b) * Q30, alpha)

    N = Z * 64 + 6 * Wm - 3 * G - 6 * Wp
    I = RND(1 << 60, G)
    # floor 除法（Python // 对负数即 floor，与算术右移一致）
    X = (N * I) // (1 << 36)
    Q = RND(X, 96)
    un = Q + 524288
    code = min(1048575, max(0, un))
    return code, int(code != un), 0


def main() -> int:
    rows = []

    # ---- 名义权重（合同 §3.2）----
    # 主 4Cu = 1/4，子 j=16..18 = 1/32,1/64,1/128；j=19 平衡位不进 Wm
    def wm_of(post_full_slices: int, post_sub_mask: int) -> int:
        per = post_full_slices * 16 * 0.25
        per += post_sub_mask * (1 / 32 + 1 / 64 + 1 / 128)
        return int(round(per * 8 * Q30))

    G_nom = int(32.0 * Q30)
    # A: 每片 0x7FFFF -> 16 主位全 1 + 子 j=16,17,18 全 1
    Wm_A = int(round(8 * (16 * 0.25 + 1 / 32 + 1 / 64 + 1 / 128) * Q30))
    Wp_A = int(round(0.4921875 * Q30))
    # B: c=258 -> 16.125
    Wm_B = int(round(16.125 * Q30))
    Wp_B = int(round(0.1328125 * Q30))

    # ---- 定向：方案 §11.3 的两个已知点 ----
    rows.append(("A_nominal", 5120, Wm_A, G_nom, Wp_A, Q30, 0))
    rows.append(("B_nominal", 2560, Wm_B, G_nom, Wp_B, Q30, 0))

    # ---- 扫 ADC2 raw 全域（4 个代表性权重配置）----
    for raw in range(8192):
        rows.append((f"raw{raw}", raw, Wm_A, G_nom, Wp_A, Q30, 0))

    # ---- 边界：raw 端点 ----
    for raw in (0, 1, 8190, 8191):
        rows.append((f"edge{raw}", raw, Wm_B, G_nom, Wp_B, Q30, 0))

    # ---- alpha 与 b 的边界 ----
    # 合法域边界 + 域外（1 应被拒）
    for alpha in (Q30, Q30 // 2, Q30 * 2, 1):
        rows.append((f"alpha{alpha}", 4096, Wm_A, G_nom, Wp_A, alpha, 0))
    for b in (0, 1, -1, 1 << 20, -(1 << 20)):
        rows.append((f"b{b}", 4096, Wm_A, G_nom, Wp_A, Q30, b))

    # ---- 合同违例 ----
    rows.append(("G0", 4096, Wm_A, 0, Wp_A, Q30, 0))
    rows.append(("alpha0", 4096, Wm_A, G_nom, Wp_A, 0, 0))

    out = []
    for name, raw, Wm, G, Wp, alpha, b in rows:
        code, sat, err = recon(raw, Wm, G, Wp, alpha, b)
        out.append(f"{raw} {Wm} {G} {Wp} {alpha} {b} {code} {sat} {err} {name}")

    sys.stdout.write("\n".join(out) + "\n")
    print(f"# generated {len(out)} vectors", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
