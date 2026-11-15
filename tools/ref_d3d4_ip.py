#!/usr/bin/env python3
"""生成 d3d4_ip 顶层对拍向量 tb/vec_d3d4_ip.txt。

每行：seq k dither mask_hi mask_lo pre8hex U f res hp vp

mask 拆成两段 20 位（各 5 个 hex 字符）输出：SystemVerilog 的 $fscanf("%h")
读进 longint 时会按 32 位截断，一次读 160 位会静默丢高位，因此分两段。

参考口径
--------
* mask / pre / U / f 直接调用 `src/adi_model/dem_dither.py` 的合同实现，
  与叶子级 `tools/cmp_dem_dither.py` 用的是同一份参考（不另写一套公式）。
* 相位序列按 `rtl/dem_phase_rr.sv`（round-robin）重算：索引 0..1919 线性递增，
  混合进制分解 (idx/120, (idx/8)%15, idx%8)。这是 d3d4_ip 的**默认**相位源，
  依据见 docs/PHASE_AB.md（与 PPT 原文 "Round robin shuffler" 一致，
  且统计/面积全面更优）。
* coin 取 `^idx[10:7]`（见 rtl/d3d4_ip.sv 的说明）。注意这里**踩过坑**：
  原实现取 rng[31]，切到 round-robin 后索引只占 11 位，bit31 恒 0，
  bridge 永不触发且不报错。
* acq_seq = (res[0] << 3) | hp[0]，对应 d3d4_ip 里的 half 选择。

为什么要单独生成
----------------
顶层 TB 要同时覆盖"相位来自 RTL 内部"和"mask/pre 与合同一致"两件事，
叶子级向量（先给相位再给 k）无法表达这个组合，因此单独出一份。

用法：
    python3 tools/ref_d3d4_ip.py [--n 512]
"""
from __future__ import annotations

import argparse
import importlib.util
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)

_spec = importlib.util.spec_from_file_location(
    '_dem_dither_standalone', os.path.join(_ROOT, 'src', 'adi_model', 'dem_dither.py'))
_dd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_dd)

MASK32 = 0xFFFFFFFF
PG_SEED = 0x1234_5678      # 仅 xorshift 对照模式使用
PHASE_TOTAL = 1920
RES_SPAN = 120
H_STEP = 8
H_MOD = 15


def phase_of_idx(idx: int) -> tuple[int, int, int]:
    """相位索引 -> (reserved, h_phase, v_phase)。与 dem_phase_rr.sv 一致。"""
    i = idx % PHASE_TOTAL
    return (i // RES_SPAN, (i // H_STEP) % H_MOD, i % 8)


def coin_of_idx(idx: int) -> int:
    """coin = ^idx[10:7]。与 rtl/d3d4_ip.sv 的 `^rng_use[10:7]` 一致。

    注意不能用 `int.bit_count()` —— 它是 Python 3.10+ 才有。
    仓库 requires-python >=3.10，但本机 /usr/bin/python3 是 3.9，
    而这些工具脚本应当能在任意 3.9+ 环境跑（与 cmp_dem_dither.py 同一原则）。
    """
    i = idx % PHASE_TOTAL
    nib = (i >> 7) & 0xF
    return (bin(nib).count('1')) & 1


def xs32(x: int) -> int:
    x &= MASK32
    x ^= (x << 13) & MASK32
    x ^= x >> 17
    x ^= (x << 5) & MASK32
    return x & MASK32


def phase_of(rng: int) -> tuple[int, int, int]:
    i = rng % 1920
    return (i // 120, (i // 8) % 15, i % 8)


def pack_mask(m) -> int:
    """8×20 -> 160 位，bit = 20*s + j（与 rtl/dem_mask_v3.sv 一致）。"""
    v = 0
    for s in range(8):
        for j in range(20):
            if m[s][j]:
                v |= 1 << (20 * s + j)
    return v


def pack_pre(bits) -> int:
    """8×4 -> 32 位，bit = 4*s + b（与 rtl/dither_pre.sv 一致）。

    bits[s] 是 dem_dither.pre() 返回的 [b2,b1,b0,bal]（MSB-first），
    落到 4 位向量上是 pre_v[s][3]=b2 ... pre_v[s][0]=bal，
    因此 list 下标 i 对应向量位 (3-i)。
    """
    v = 0
    for s in range(8):
        for i in range(4):
            if bits[s][i]:
                v |= 1 << (4 * s + (3 - i))
    return v


def dither_fields(word: int) -> tuple[int, int]:
    """与 dither_pre.sv 同口径：U = 4*D6 + D4，f = 4*D3 + 2*D2 + D1。"""
    U = 4 * ((word >> 5) & 1) + ((word >> 3) & 1)
    f = 4 * ((word >> 2) & 1) + 2 * ((word >> 1) & 1) + (word & 1)
    return U, f


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--n', type=int, default=512)
    ap.add_argument('--out', default=os.path.join(_ROOT, 'tb', 'vec_d3d4_ip.txt'))
    args = ap.parse_args()

    lines = []
    for i in range(args.n):
        # round-robin：第 i 拍用的索引 = i+1（复位态是 0，首个握手推进到 1）
        idx = (i + 1) % PHASE_TOTAL
        # 关键：d3d4_ip 在握手沿**先推进相位、再寄存输出**，所以第 i 拍的
        # 输出对应"推进 i+1 次后"的相位。忘了这一步会让整条序列偏移一拍，
        # 症状是每一拍的 mask/pre 都对不上，但结构本身没错。
        res, hp, vp = phase_of_idx(idx)
        coin = coin_of_idx(idx)
        k = i % 521                      # 覆盖 0..520（含 guard）
        dith = (i * 37) & 0x3F           # 64 个字都走到
        acq_seq = ((res & 1) << 3) | (hp & 1)

        # dem_mask_v3 的 h_phase_i 是 3 位端口，取 hp 低 3 位；
        # res 不参与 DEM 映射（它只用于 bridge 的 acq_seq 与观测）。
        m = _dd.post_mask(k, hp & 7, vp)
        m = _dd.bridge_swap(m, hp & 7, vp, acq_seq, coin)
        mask = pack_mask(m)

        bits, T, Ur, Ld = _dd.pre(dith, vp)
        pre = pack_pre(bits)
        U, f = dither_fields(dith)
        assert U == Ur, (U, Ur)

        m_lo = mask & 0xFFFFF                 # bit 0..19   -> slice 0
        m_hi = (mask >> 20) & 0xFFFFF         # bit 20..39  -> slice 1..7 的前 20 位
        m_hi2 = (mask >> 40) & 0xFFFFF
        # 160 位需要 8 段才能无损，这里直接按 20 位一段输出 8 段
        segs = [(mask >> (20*t)) & 0xFFFFF for t in range(8)]
        seg_s = " ".join(f"{v:05x}" for v in segs)
        lines.append(
            f"{i} {k} {dith} {seg_s} {pre:08x} {U} {f} {res} {hp} {vp}")

    with open(args.out, 'w') as f:
        f.write("\n".join(lines) + "\n")
    print(f"wrote {args.out} ({len(lines)} vectors)")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
