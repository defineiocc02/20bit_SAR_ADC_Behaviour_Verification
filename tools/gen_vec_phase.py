#!/usr/bin/env python3
"""生成 dem_phase_gen 的金标向量 tb/vec_phase_rtl.txt。

格式：每行 "seq res hp vp"，seq 是推进次数（0 = 复位态，即种子对应的相位）。

为什么不复用 existing 函数
--------------------------
* `tools/gen_vec_topw.py` 产出的 `tb/vec_phase_v3.txt` 是 **v3 置换表**
  （`digital_chain.dem_phases()`，1920 行满周期排列），服务 legacy9 顶层；
  它与 `dem_phase_gen.sv` 的 xorshift 实时序列**不是同一条**。早期把两者
  混用，导致 TB 整体偏移一拍、并误判 RTL 有 bug。
* `digital_chain.dem_phases_rtl()` 是本 RTL 的镜像，但该模块有包内依赖
  （`adi_model.control.*`），按路径直载会 ImportError。

因此这里直接内联 RTL 的算法（xorshift32 13/17/5 + %1920 + 混合进制分解），
并在 assert 里锁定 SEED 与分解口径。RTL 侧由 tb_dem_phase_gen 逐拍对拍兜底，
两边任何一处改动都会立刻暴露。

用法：
    python3 tools/gen_vec_phase.py [--cycles 17280]
"""
from __future__ import annotations

import argparse
import os

MASK32 = 0xFFFFFFFF
PG_SEED = 0x1234_5678          # 必须与 rtl/dem_phase_gen.sv 的 SEED 一致
PHASE_TOTAL = 1920
RES_SPAN = 120                  # 1920 / 16
H_STEP = 8                      # 1920 / 240
H_MOD = 15


def xs32(x: int) -> int:
    """一步 xorshift32，与 rtl/dem_phase_gen.sv 的 xs32() 逐位一致。"""
    x &= MASK32
    x ^= (x << 13) & MASK32
    x ^= x >> 17
    x ^= (x << 5) & MASK32
    return x & MASK32


def phase_of(rng: int) -> tuple[int, int, int]:
    """32 位随机态 -> (reserved, h_phase, v_phase)。"""
    i = rng % PHASE_TOTAL
    return (i // RES_SPAN, (i // H_STEP) % H_MOD, i % 8)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--cycles', type=int, default=17280)
    ap.add_argument('--out', default=os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        'tb', 'vec_phase_rtl.txt'))
    args = ap.parse_args()

    # 自检：复位态必须是 SEED 的相位，且第一次推进后与 RTL 实测一致。
    # 断言值来自 rtl/dem_phase_gen.sv 的 VCS 实测（tb_rng 探针，寄存器输出版）：
    #   复位态   rng=0x12345678 -> (13,12,0)
    #   推进 1 次 rng=0x87985aa5 -> (5,9,5)
    #   推进 2 次 rng=0x155b24a3 -> (13,1,3)
    assert phase_of(PG_SEED) == (13, 12, 0), phase_of(PG_SEED)
    assert phase_of(xs32(PG_SEED)) == (5, 9, 5), phase_of(xs32(PG_SEED))
    assert phase_of(xs32(xs32(PG_SEED))) == (13, 1, 3)

    with open(args.out, 'w') as f:
        rng = PG_SEED
        for n in range(args.cycles):
            r, h, v = phase_of(rng)
            f.write(f"{n} {r} {h} {v}\n")
            rng = xs32(rng)
    print(f"wrote {args.out} ({args.cycles} phases, seed={PG_SEED:#x})")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
