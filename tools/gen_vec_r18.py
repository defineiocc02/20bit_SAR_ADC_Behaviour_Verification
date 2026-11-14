#!/usr/bin/env python3
"""生成 random18 模式的三组对拍向量（全部来自生产模型，不另写公式）。

产物（写入 tb/）：
  vec_r18_sched.txt  调度器逐拍：cyc id0..id7 acq_mask conv_mask idle_mask
  vec_r18_recip.txt  倒数单元：g inv（含全部 43,758 个片组 G + 边界）
  vec_r18_gold.txt   端到端：sid c adc2 dither id0..id7 wm g inv code_hw code_exact ovf err

口径分工：
  * sched 文件由 ``adi_model.sched18_rtl``（与 rtl/sched18.sv 同一套整数运算）生成
  * gold 文件的 wm/g/code 由 ``adi_model.digital_chain.run_transaction`` 生成，
    inv 由 ``adi_model.recip_q60``（与 rtl/recip_q60.sv 同一算法）生成
  * code_hw    = 用 recip_q60 倒数算出的码（RTL 期望，逐位比）
  * code_exact = 用精确 round(2^60/G) 算出的码（算法参考，允许 ±1 LSB）

用法：``PYTHONPATH=src:tools python3 tools/gen_vec_r18.py``
"""

from __future__ import annotations

import itertools
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(__file__))

from adi_model.digital_chain import (  # noqa: E402
    N_ACTIVE,
    N_COLUMNS,
    N_SLICES,
    Transaction,
    dem_phases,
    run_transaction,
)
from adi_model.recip_q60 import recip_exact_floor, recip_q60  # noqa: E402
from adi_model.sched18_rtl import SEED_DEFAULT, Scheduler18Rtl, run as sched_run  # noqa: E402
from gen_vec_topw import build_weights, row_sum  # noqa: E402

N_SCHED = 4096
N_GOLD = 2048
N_UNIT = 20


def write_sched(path: str, cycles: int, seed: int) -> int:
    sch = Scheduler18Rtl(seed)
    with open(path, "w") as f:
        for c in range(cycles):
            st = sch.step()
            ids = st["acq"]
            assert len(ids) == 8 and len(st["conv"]) == 8 and len(st["idle"]) == 2
            am = sum(1 << s for s in st["acq"])
            cm = sum(1 << s for s in st["conv"])
            im = sum(1 << s for s in st["idle"])
            assert (am & cm) == 0 and (am | cm | im) == (1 << N_SLICES) - 1
            f.write(f"{c} " + " ".join(str(s) for s in ids) + f" {am} {cm} {im}\n")
    return cycles


def write_recip(path: str, ver: int = 0) -> dict:
    W = build_weights()
    rs = [row_sum(W[ver], s) for s in range(N_SLICES)]
    gs = sorted({sum(rs[s] for s in ids)
                 for ids in itertools.combinations(range(N_SLICES), N_ACTIVE)})
    # 合同域 G ≥ 2^34（更小的 G 由 RTL 的 ovf 报错，不参与逐位比对）
    edge = [2**34, 2**34 + 1, 2**35 - 1, 2**35, 2**36, 2**37, 2**40, 2**44 - 1]
    rows = []
    worst_rel = 0.0
    for g in gs + edge:
        inv = recip_q60(g)
        e = recip_exact_floor(g)
        if e:
            worst_rel = max(worst_rel, abs(inv - e) / e)
        assert 0 < inv < (1 << 27), (g, inv)
        rows.append((g, inv))
    with open(path, "w") as f:
        for g, inv in rows:
            f.write(f"{g} {inv}\n")
    return dict(n=len(rows), n_subset=len(gs), worst_rel=worst_rel,
                g_min=gs[0], g_max=gs[-1])


def write_gold(path: str, n: int, seed: int, ver: int = 0) -> dict:
    W = build_weights()
    wd = {(sid, u): W[ver][sid][u] for sid in range(N_SLICES) for u in range(N_UNIT)}
    sch = Scheduler18Rtl(seed)
    rng = random.Random(seed ^ 0x5EED)
    stats = dict(n=0, code_diff1=0, code_diff_gt1=0, max_abs_d=-1, saturated=0)
    with open(path, "w") as f:
        for i in range(n):
            st = sch.step()
            ids = st["acq"]
            dither = rng.randrange(0, 64)
            upper = 4 * ((dither >> 5) & 1) + ((dither >> 3) & 1)
            if i < 512:
                c = i            # 粗码全扫
            elif i < 640:
                c = rng.randrange(0, 512)
            else:
                c = rng.randrange(0, 512)
            c = max(c, upper)
            tx = Transaction(sample_id=i, final_code=c, dither_word=dither,
                             adc2_code=rng.randrange(0, 4096), weights_q30=wd)
            phases = dem_phases(i)
            hw = run_transaction(tx, phases=phases, slice_ids=ids, inv_fn=recip_q60)
            ex = run_transaction(tx, phases=phases, slice_ids=ids)
            assert hw["code20"] is not None and ex["code20"] is not None
            d = hw["code20"] - ex["code20"]
            if abs(d) == 1:
                stats["code_diff1"] += 1
            elif abs(d) > 1:
                stats["code_diff_gt1"] += 1
            stats["max_abs_d"] = max(stats["max_abs_d"], abs(d))
            if hw["code20"] in (0, (1 << 20) - 1):
                stats["saturated"] += 1
            stats["n"] += 1
            f.write(f"{i} {c} {tx.adc2_code} {dither} "
                    + " ".join(str(s) for s in ids)
                    + f" {hw['wm_q30']} {hw['g_q30']} {hw['inv_g_q60']} "
                      f"{hw['code20']} {ex['code20']} 0 0\n")
    return stats


def main() -> int:
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    tb = os.path.join(root, "tb")

    p = os.path.join(tb, "vec_r18_sched.txt")
    n = write_sched(p, N_SCHED, SEED_DEFAULT)
    print(f"wrote {p} ({n} cycles)")

    p = os.path.join(tb, "vec_r18_recip.txt")
    s = write_recip(p)
    print(f"wrote {p} ({s['n']} rows, {s['n_subset']} 个真实片组 G ∈ "
          f"[{s['g_min']}, {s['g_max']}], 最大相对误差 {s['worst_rel']:.2e})")

    p = os.path.join(tb, "vec_r18_gold.txt")
    s = write_gold(p, N_GOLD, SEED_DEFAULT)
    print(f"wrote {p} ({s['n']} tx)：与精确倒数比 code 差 1 LSB 的 {s['code_diff1']} 笔，"
          f"差 >1 的 {s['code_diff_gt1']} 笔，最大 |Δcode| = {s['max_abs_d']}，"
          f"饱和 {s['saturated']} 笔")

    # 调度器统计（片使用不平衡度）
    r = sched_run(SEED_DEFAULT, 20000, 100)
    print(f"sched 统计：20000 拍 违例 {len(r['violations'])}，"
          f"片使用不平衡度 {r['imbalance']*100:.2f}%")
    assert not r["violations"]
    assert s["code_diff_gt1"] == 0, "倒数误差超过 1 LSB，不能作为算法正确性证据"
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
