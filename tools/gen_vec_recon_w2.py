#!/usr/bin/env python3
"""生成 tb/vec_recon_w2.txt：recon_d5_w2（预计算倒数版 D_eff）的定向 + 随机向量。

与 vec_recon_w.txt 的区别在于 **期望值的算法**：
  · vec_recon_w.txt  —— 期望值是 recon_d5_w 的精确除法（round_half_away((6Wm-3G)*2^24/G)）
  · vec_recon_w2.txt —— 期望值走硬件真实路径 ((6Wm-3G)*inv_g_q60) >> 36
最后一级（r̂ / TΔ / ÷96 / 饱和）两边逐字相同。

wm/g 不是硬编码的：每笔事务先跑 run_transaction，取它实际分配的
slice_ids 与 masks 再累加，避免「向量和 RTL 用的片不同」这类假失配。

字段：wm_q30 g_q30 inv_g_q60 adc2 dither code20_expected
"""

from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from adi_model.digital_chain import (  # noqa: E402
    N_COLUMNS,
    N_TOTAL,
    Q30,
    Transaction,
    forward_code,
    run_transaction,
)

Q24 = 1 << 24
INV_SHIFT = 36


def inv_of(g_q30: int) -> int:
    """inv_g_q60 = round(2^60 / G)，整数实现（四舍五入到最近）。"""
    return (2 * (1 << 60) + g_q30) // (2 * g_q30)


def w2_datapath(wm_q30: int, g_q30: int, inv_g_q60: int, adc2: int, dither: int) -> tuple[int, int, int]:
    """recon_d5_w2 的逐位模型。返回 (code20, overflow, dac_q24)。"""
    num = 6 * wm_q30 - 3 * g_q30
    dac_q24 = (num * inv_g_q60) >> INV_SHIFT          # 算术右移 = 向下取整，与 SV >>> 一致

    # r̂ = ((2a+1)·144 + 2) / 5，倒数乘 838861 >> 22
    r_q24 = (((adc2 * 2 + 1) * 144 + 2) * 838861) >> 22

    b = [(dither >> (5 - i)) & 1 for i in range(6)]
    upper = 4 * b[0] + b[2]
    t_num = upper * 8 + b[1] * 16 + b[3] * 4 + b[4] * 2 + b[5]
    x_q24 = dac_q24 + r_q24 + t_num * 24576

    a = abs(x_q24)
    q = (a * 44739243 + (1 << 31)) >> 32              # round(x/96)
    if x_q24 < 0:
        q = -q
    overflow = (q + 524288) > 1048575 or (q + 524288) < 0
    if q > 524287:
        code = 0xFFFFF
    elif q < -524288:
        code = 0
    else:
        code = q + 524288
    return code, int(overflow), dac_q24


def exact_dac(wm_q30: int, g_q30: int) -> int:
    """recon_d5_w 的精确除法（半数远离零），仅用于统计偏差。"""
    num = (6 * wm_q30 - 3 * g_q30) * Q24
    n = abs(num)
    qq = (n + (g_q30 >> 1)) // g_q30
    return qq if num >= 0 else -qq


def make_weights(rng: random.Random, spread: float) -> dict:
    """名义单 2C 逻辑单元 w = 0.25（Q30 即 2^28），逐单元 ±spread。

    与 vec_recon_w.txt 同口径（那份 g≈3.4e10 ≈ 128×2^28）。
    """
    w = {}
    for s in range(18):
        for j in range(N_TOTAL):
            w[(s, j)] = int(round(Q30 / 4.0 * (1.0 + rng.uniform(-spread, spread))))
    return w


def wm_g_of(result: dict, weights: dict) -> tuple[int, int, int]:
    """从 run_transaction 的结果反推 RTL 端口上的 wm_q30 / g_q30。

    G    = Σ w（仅 16 个采样主列，与 mask 无关）
    Wm   = Σ w·m（主 + 子；子阵列只有 L 的 4 位里低两位有效）
    """
    ids = result["slice_ids"]
    masks = result["masks"]
    k = result["k"]
    g = 0
    wm = 0
    for slot, sid in enumerate(ids):
        for j in range(N_COLUMNS):
            wv = weights[(int(sid), j)]
            g += wv
            wm += wv * masks[slot][j]
    low = k & 3
    sub = [(low >> (3 - i)) & 1 for i in range(4)]
    for slot, sid in enumerate(ids):
        for i, j in enumerate(range(N_COLUMNS, N_TOTAL)):
            wm += weights[(int(sid), j)] * sub[i]
    return wm, g, k


def build(n_vec: int, seed: int) -> list[tuple]:
    rng = random.Random(seed)
    rows: list[tuple] = []
    must: list[tuple] = []
    weight_sets = [make_weights(rng, 0.03), make_weights(rng, 0.01), make_weights(rng, 0.0)]

    def push(ws, sid, code, word, adc2, *, keep=False):
        res = run_transaction(Transaction(sid, code, word, adc2, ws))
        if res.get("code20") is None:      # 非法 k（被拒收）
            return
        wm, g, _ = wm_g_of(res, ws)
        if g <= 0 or wm >= (1 << 44) or g >= (1 << 44):
            return
        inv = inv_of(g)
        if not (0 < inv < (1 << 27)):
            return
        exp, _, _ = w2_datapath(wm, g, inv, adc2, word)
        row = (wm, g, inv, adc2, word, exp)
        (must if keep else rows).append(row)

    # 1) 定向端点：两端饱和 + 溢出，扰动字取 0/小值保证 k 不被拒收
    for code in (0, 1, 2, 3, 4, 6, 8, 12, 16, 24, 32, 48, 64,
                 448, 480, 496, 504, 508, 509, 510, 511):
        for word in (0, 1, 2):
            ws = weight_sets[(code + word) % len(weight_sets)]
            for adc2 in (0, 4095):
                push(ws, code, code, word, adc2, keep=True)

    # 2) 粗码全扫：512 档代码全过一遍（扰动字错开，避免只有 upper=0 一路）
    for i in range(512):
        ws = weight_sets[i % len(weight_sets)]
        push(ws, i, i, (i * 5) % 64, rng.randrange(4096))

    # 3) 随机：代码 / 扰动字 / ADC2 全随机
    while len(rows) < n_vec * 4:
        ws = weight_sets[rng.randrange(len(weight_sets))]
        push(ws, rng.randrange(0, 4096), rng.randrange(512), rng.randrange(64), rng.randrange(4096))

    # 4) 定向：forward_code 生成相容的 (c, adc2)，覆盖中间轨
    for x_mv in (-2900, -2400, -1500, -600, -100, 0, 100, 600, 1500, 2400, 2900):
        for word in (0, 7, 21, 42, 63):
            f = forward_code(x_mv / 1000.0, word)
            ws = weight_sets[rng.randrange(len(weight_sets))]
            push(ws, rng.randrange(0, 4096), f["c"], word, f["adc2"])

    # 端点用例全部保留，其余随机抽，避免 shuffle 截断把饱和样本丢掉
    rng.shuffle(rows)
    keep_n = max(0, n_vec - len(must))
    out = must + rows[:keep_n]
    rng.shuffle(out)
    return out[:n_vec]

    rng.shuffle(rows)
    return rows[:n_vec]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=400)
    ap.add_argument("--seed", type=int, default=20261114)
    ap.add_argument("--out", type=Path, default=ROOT / "tb" / "vec_recon_w2.txt")
    args = ap.parse_args()

    rows = build(args.n, args.seed)

    # 统计：与精确除法金标的偏差、覆盖情况
    dmax = 0
    ndiff = 0
    invariants = {"num_bits": 0, "prod_bits": 0}
    codes = {"sat_lo": 0, "sat_hi": 0}
    for wm, g, inv, adc2, word, code in rows:
        num = 6 * wm - 3 * g
        prod = num * inv
        invariants["num_bits"] = max(invariants["num_bits"], abs(num).bit_length())
        invariants["prod_bits"] = max(invariants["prod_bits"], abs(prod).bit_length())
        d = abs(((num * inv) >> INV_SHIFT) - exact_dac(wm, g))
        dmax = max(dmax, d)
        ndiff += 1 if d else 0
        codes["sat_hi"] += 1 if code == 0xFFFFF else 0
        codes["sat_lo"] += 1 if code == 0 else 0

    args.out.write_text("".join(f"{a} {b} {c} {d} {e} {f}\n" for a, b, c, d, e, f in rows))

    print(f"vectors      : {len(rows)}  -> {args.out}")
    print(f"|num| bits   : {invariants['num_bits']:2d}   (RTL 端口 42 bit)")
    print(f"|prod| bits  : {invariants['prod_bits']:2d}   (必须 <= 63)")
    print(f"inv range    : {min(r[2] for r in rows)} .. {max(r[2] for r in rows)}")
    print(f"g range      : {min(r[1] for r in rows)} .. {max(r[1] for r in rows)}")
    print(f"D_eff 差     : max {dmax} Q24 units, {ndiff}/{len(rows)} 组非零")
    print(f"溢出用例     : {sum(w2_datapath(*r[:5])[1] for r in rows)}")
    print(f"饱和到 0     : {codes['sat_lo']}, 饱和到 1048575: {codes['sat_hi']}")
    if invariants["prod_bits"] > 63:
        print("ERROR: 乘积超过 64 位有符号范围")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
