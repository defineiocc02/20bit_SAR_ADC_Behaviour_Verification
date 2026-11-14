#!/usr/bin/env python3
"""生成 digital_top_w 的系数表 + 金标事务向量。

期望的 wm_q30 / g_q30 / inv_g_q60 / code20 **直接来自生产模型**
`adi_model.digital_chain.run_transaction`，不是在本脚本里另写一套公式。
本脚本只负责：

  1. 造 3 套"每片行和相同、片间列权旋转不同"的冻结系数
     （行和相同 ⇒ 任意 8 片活动组的 G 都相同，单个 inv 寄存器才成立；
       片间旋转不同 ⇒ 片 ID 查错一定会改 Wm，记分板能抓到）
  2. 调生产模型算每笔事务的期望值
  3. 自检：权重恒等式、G 对 9 个片组一致、inv=round(2^60/G)、位宽
  4. 写 tb/vec_topw_coef.txt 与 tb/vec_topw_gold.txt

用法（仓库根目录）：
    PYTHONPATH=src python3 tools/gen_vec_topw.py
"""

from __future__ import annotations

import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from adi_model.digital_chain import N_SLICES, Transaction, run_transaction  # noqa: E402

Q30 = 1 << 30
UNIT = Q30 // 4                 # 0.25 in Q30，名义单 2C 单元
N_UNIT = 20
N_COL = 16
N_ACTIVE = 8
N_VER = 4                       # 0/1/2 正常，3 = 全零（err 路径）
N_GOLD = 1536

# 零和扰动模板（单位：UNIT/64），保证 16 列行和 = 16·UNIT
_D0 = [3, -2, 1, -1, 2, -3, 0, 4, -4, 1, -1, 2, -2, 0, 3, -3]
_D1 = [-4, 2, 3, -1, -3, 1, 4, -2, 2, -2, 0, 1, -1, 0, -3, 3]
_D2 = [1, 2, -3, 4, -2, -4, 3, -1, -1, 2, -2, 0, 1, 3, -4, 1]
assert sum(_D0) == 0 and sum(_D1) == 0 and sum(_D2) == 0


def build_weights():
    """返回 W[ver][sid][unit]，W[3] 全零。"""
    tables = [[[0] * N_UNIT for _ in range(N_SLICES)] for _ in range(N_VER)]

    for ver, (templ, scale_num, scale_den) in enumerate(
        ((_D0, 1, 1), (_D1, 101, 100), (_D2, 49, 50))
    ):
        base = (UNIT * scale_num) // scale_den
        step = max(1, UNIT // 64)
        pat = [base + d * step for d in templ]
        # 缩放后仍保证精确等行和（把余数补到 pattern 上再旋转）
        for sid in range(N_SLICES):
            rot = (sid * 5 + ver * 7) % N_COL
            for j in range(N_COL):
                tables[ver][sid][j] = pat[(j + rot) % N_COL]
            # 子阵列 4 位：片间也不同，专测 Wm 的子阵列寻址（不进 G）
            for k in range(4):
                tables[ver][sid][N_COL + k] = UNIT // 8 + ((sid * 3 + k * 7 + ver) % 5) * (UNIT // 128)
    return tables


def row_sum(tab, sid):
    return sum(tab[sid][j] for j in range(N_COL))


def main():
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    tb = os.path.join(root, "tb")
    rng = random.Random(20261114)

    W = build_weights()

    # ---- 自检 1：行和片间一致 / 版本内一致 ----
    for ver in range(3):
        sums = {row_sum(W[ver], s) for s in range(N_SLICES)}
        assert len(sums) == 1, f"version {ver} row sums differ: {sorted(sums)}"
        print(f"V{ver}: per-slice row sum = {sums.pop()}  G(8 slices) = {8 * row_sum(W[ver], 0)}")

    # ---- inv 与 G 对 9 个片组检查 ----
    inv = {}
    for ver in range(3):
        G = 8 * row_sum(W[ver], 0)
        inv[ver] = (2 * (1 << 60) + G) // (2 * G)
        assert inv[ver] < (1 << 27), f"V{ver} inv overflows 27b: {inv[ver]}"
        groups = {(8 * (n + 1)) % N_SLICES for n in range(9)}
        vals = {sum(row_sum(W[ver], (g + s) % N_SLICES) for s in range(N_ACTIVE)) for g in groups}
        assert vals == {G}, f"V{ver} G varies by group: {vals}"
        print(f"V{ver}: inv_g_q60 = {inv[ver]}  (9 groups all G={G})")
    inv[3] = 0

    # ---- 系数文件 ----
    coef_path = os.path.join(tb, "vec_topw_coef.txt")
    with open(coef_path, "w") as f:
        # 统一 5 列：TYPE VER SID UNIT VALUE（TYPE=0 权重，TYPE=1 版本 inv）
        for ver in range(N_VER):
            for sid in range(N_SLICES):
                for u in range(N_UNIT):
                    f.write(f"0 {ver} {sid} {u} {W[ver][sid][u]}\n")
            f.write(f"1 {ver} 0 0 {inv[ver]}\n")
    print(f"wrote {coef_path}")

    # ---- 金标事务（V0，sample_id = 行号，连续无气泡） ----
    gold_path = os.path.join(tb, "vec_topw_gold.txt")
    max_num = 0
    max_prod = 0
    codes = []
    with open(gold_path, "w") as f:
        for i in range(N_GOLD):
            if i < 512:
                # 512 档粗码全扫（dither=0 ⇒ upper=0 ⇒ k=c）
                dither, c, adc2 = 0, i, (i * 2654435761) & 0xFFF
            elif i < 640:
                dither = rng.randrange(0, 64)
                c = rng.randrange(0, 512)
                adc2 = rng.randrange(0, 4096)
            elif i < 660:
                # 低端饱和：k=0、adc2=0、T=0 ⇒ code 应钳到 0
                dither, c, adc2 = 0, 0, 0
            elif i < 724:
                # 64 个 dither 字全覆盖（k 固定）
                dither = i - 660
                c = 300
                adc2 = rng.randrange(0, 4096)
            else:
                dither = rng.randrange(0, 64)
                c = rng.randrange(0, 512)
                adc2 = rng.randrange(0, 4096)
            upper = 4 * ((dither >> 5) & 1) + ((dither >> 3) & 1)
            if c < upper:                       # k = c - upper 必须非负
                c = upper

            wd = {(sid, u): W[0][sid][u] for sid in range(N_SLICES) for u in range(N_UNIT)}
            tx = Transaction(
                sample_id=i, final_code=c, dither_word=dither,
                adc2_code=adc2, weights_q30=wd,
            )
            res = run_transaction(tx)
            assert res["code20"] is not None, res
            assert res["weight_identity_ok"], (i, res["mask_selected"], res["expect_8H_plus_M"])
            assert res["code_identity_ok"], (i, res["k"])
            wm, g, iv = res["wm_q30"], res["g_q30"], res["inv_g_q60"]
            assert g == 8 * row_sum(W[0], 0), (i, g)
            assert iv == inv[0], (i, iv)
            # overflow 口径与 RTL 相同：q+2^19 超出 [0,2^20)
            dac_q24 = res["dbg"][2]
            r_hat = res["dbg"][3]
            t_num = (upper * 8 + ((dither >> 4) & 1) * 16 + ((dither >> 2) & 1) * 4
                     + ((dither >> 1) & 1) * 2 + (dither & 1))
            x_q24 = dac_q24 + r_hat + t_num * 24576
            a = abs(x_q24)
            q = (a * 44739243 + (1 << 31)) >> 32
            if x_q24 < 0:
                q = -q
            cc = q + (1 << 19)
            ovf = 1 if (cc > (1 << 20) - 1 or cc < 0) else 0
            code = min(max(cc, 0), (1 << 20) - 1)
            assert code == res["code20"], (i, code, res["code20"])

            num = 6 * wm - 3 * g
            max_num = max(max_num, abs(num))
            max_prod = max(max_prod, abs(num * iv))
            codes.append(code)
            f.write(f"{c} {adc2} {dither} {wm} {g} {iv} {code} {ovf}\n")

    print(f"wrote {gold_path} ({N_GOLD} tx)")
    print(f"bitwidth: |6Wm-3G| max = {max_num} ({max_num.bit_length()} bit), "
          f"|num*inv| max = {max_prod} ({max_prod.bit_length()} bit)")
    assert max_num < (1 << 38), "num exceeds recon_d5_w2 contract"
    assert max_prod < (1 << 63), "product exceeds recon_d5_w2 contract"
    print(f"code20 range {min(codes)}..{max(codes)}, "
          f"saturated low={codes.count(0)}, high={codes.count((1 << 20) - 1)}")


if __name__ == "__main__":
    main()
