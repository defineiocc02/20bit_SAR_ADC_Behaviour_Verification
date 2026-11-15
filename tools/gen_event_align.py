#!/usr/bin/env python3
"""生成逐事件对齐测试的金标 tb/vec_event_align.txt。

背景
----
`tb/tb_d3d4_ip.sv` 原有的 follower 判据只是 smoke 级（k 严格增长 8 次、
mask 改变 8 次）。它抓不到"mask 落后 k 一个事件"这类错位——实测确实存在：
EV0 时 k_ev=1 而 mask 仍是复位值，EV1 时 k_ev=3 而 mask 是 k=1 的。

本脚本产出**逐事件**金标：对每个事件 i，给出该事件的 k 与该 k 对应的
160 位物理 mask。TB 逐事件比对，k 与 mask 必须来自同一事件。

口径
----
* k：前缀累加 | 位权，再减 upper。本用例 bit 全 1、upper=0，
  所以事件 i 的 k = bitwise_or of (1 << (8-j)) for j=0..i。
* mask：直接调用 `src/adi_model/dem_dither.py` 的合同实现，
  与叶子级对拍（tools/cmp_dem_dither.py）同一份参考，不另写公式。
* 相位：第一笔事务复位后相位索引为 0，`acq_prepare_fire` 未发生，
  因此本用例用 (res,h,v) = (0,0,0) 的复位相位 —— 与 DUT 复位行为一致。
  若将来相位推进规则变化，这里要同步。

用法：
    python3 tools/gen_event_align.py
"""
from __future__ import annotations

import importlib.util
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
_spec = importlib.util.spec_from_file_location(
    '_dem_dither_ea', os.path.join(_ROOT, 'src', 'adi_model', 'dem_dither.py'))
_dd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_dd)

N_EVENTS = 9
RES, HP, VP = 0, 0, 0     # 复位相位（DUT 复位后 idx=0 -> (0,0,0)）
COIN = 0                  # acq_prepare 未发生，coin 由 idx[10:7] 奇偶给出 = 0
ACQ_SEQ = 0


def pack_mask(m) -> int:
    """8x20 -> 160 位，bit = 20*s + j（与 rtl/dem_mask_v3.sv 一致）。"""
    v = 0
    for s in range(8):
        for j in range(20):
            if m[s][j]:
                v |= 1 << (20 * s + j)
    return v


def main() -> int:
    lines = []
    prefix = 0
    for i in range(N_EVENTS):
        # 第 i 个事件：索引 8-i，bit=1 -> 置位 (1 << (8-(8-i)))... 直接按位权
        prefix |= 1 << i
        k = prefix
        m = _dd.post_mask(k, HP, VP)
        m = _dd.bridge_swap(m, HP, VP, ACQ_SEQ, COIN)
        mk = pack_mask(m)
        segs = [(mk >> (20 * t)) & 0xFFFFF for t in range(8)]
        seg_s = " ".join(f"{v:05x}" for v in segs)
        lines.append(f"{i} {k} {seg_s} {RES} {HP} {VP}")
    out = os.path.join(_ROOT, 'tb', 'vec_event_align.txt')
    with open(out, 'w') as f:
        f.write("\n".join(lines) + "\n")
    print(f"wrote {out} ({len(lines)} events)")
    return 0


if __name__ == '__main__':
    sys.exit(main())
