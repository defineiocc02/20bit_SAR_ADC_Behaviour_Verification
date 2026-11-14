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

from adi_model.digital_chain import (  # noqa: E402
    N_SLICES,
    Transaction,
    dem_phases,
    run_transaction,
)

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


_grng = random.Random(20261114)
GAIN = [[_grng.gauss(0.0, 0.01) for _ in range(18)] for _ in range(4)]


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
            # 片增益偏差：使各片行和不同（不再强制行和相等）
            gain = 1.0 + GAIN[ver][sid]
            rot = (sid * 5 + ver * 7) % N_COL
            for j in range(N_COL):
                tables[ver][sid][j] = int(round(pat[(j + rot) % N_COL] * gain))
            # 子阵列 4 位：片间也不同，专测 Wm 的子阵列寻址（不进 G）
            for k in range(4):
                tables[ver][sid][N_COL + k] = int(round((UNIT // 8 + ((sid * 3 + k * 7 + ver) % 5) * (UNIT // 128)) * gain))
    return tables


def row_sum(tab, sid):
    return sum(tab[sid][j] for j in range(N_COL))


def main():
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    tb = os.path.join(root, "tb")
    rng = random.Random(20261114)

    W = build_weights()

    # ---- 自检 1：行和**必须**片间不同（非均匀实例） ----
    for ver in range(3):
        sums = [row_sum(W[ver], s) for s in range(N_SLICES)]
        assert len(set(sums)) > 1, f"version {ver} row sums identical — 非均匀实例未生效"
        print(f"V{ver}: row sums {min(sums)}..{max(sums)} (spread {max(sums)-min(sums)})")

    # ---- 每组倒数：按活动片组各自的真实 G ----
    inv = {}
    for ver in range(3):
        inv[ver] = []
        Gs = []
        for n in range(9):
            ids = [(8 * (n + 1) + s) % N_SLICES for s in range(N_ACTIVE)]
            G = sum(row_sum(W[ver], sid) for sid in ids)
            Gs.append(G)
            iv = (2 * (1 << 60) + G) // (2 * G)
            assert iv < (1 << 27), f"V{ver} g{n} inv overflows 27b: {iv}"
            inv[ver].append(iv)
        assert len(set(Gs)) > 1, f"V{ver} 9 组 G 仍相同，非均匀实例未生效"
        print(f"V{ver}: 9 组 G 极差 {max(Gs)-min(Gs)}，inv {min(inv[ver])}..{max(inv[ver])}")
    inv[3] = [0] * 9

    # ---- 系数文件 ----
    coef_path = os.path.join(tb, "vec_topw_coef.txt")
    with open(coef_path, "w") as f:
        # 统一 5 列：TYPE VER SID UNIT VALUE（TYPE=0 权重，TYPE=1 版本 inv）
        for ver in range(N_VER):
            for sid in range(N_SLICES):
                for u in range(N_UNIT):
                    f.write(f"0 {ver} {sid} {u} {W[ver][sid][u]}\n")
            for g in range(9):
                f.write(f"1 {ver} {g} 0 {inv[ver][g]}\n")
    print(f"wrote {coef_path}")

    # ---- DEM 相位表（1920 行，v3 满周期置换）：TB 用同一序列驱动端口 ----
    phase_path = os.path.join(tb, "vec_phase_v3.txt")
    with open(phase_path, "w") as f:
        for i in range(1920):
            r_, h_, v_ = dem_phases(i)
            f.write(f"{i} {r_} {h_} {v_}\n")
    print(f"wrote {phase_path} (1920 phases)")

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
            # G 随活动片组变化；此处校验 inv 与 G 自洽，不再要求 9 组 G 相同
            assert iv == (2 * (1 << 60) + g) // (2 * g), (i, g, iv)
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
            # 相位列：v3 是满周期置换表，TB 无法用公式复算，必须随向量导出。
            # 缺这三列时 TB 只能退回 v2 的老公式，与当前生产模型不同步
            # （2026-11-14 修：此前正好踩到这个坑，第 4 笔起 Wm 就对不上）。
            ph_r, ph_h, ph_v = dem_phases(i)
            f.write(f"{c} {adc2} {dither} {wm} {g} {iv} {code} {ovf} "
                    f"{ph_r} {ph_h} {ph_v}\n")

    print(f"wrote {gold_path} ({N_GOLD} tx)")
    print(f"bitwidth: |6Wm-3G| max = {max_num} ({max_num.bit_length()} bit), "
          f"|num*inv| max = {max_prod} ({max_prod.bit_length()} bit)")
    assert max_num < (1 << 38), "num exceeds recon_d5_w2 contract"
    assert max_prod < (1 << 63), "product exceeds recon_d5_w2 contract"
    print(f"code20 range {min(codes)}..{max(codes)}, "
          f"saturated low={codes.count(0)}, high={codes.count((1 << 20) - 1)}")


if __name__ == "__main__":
    main()
