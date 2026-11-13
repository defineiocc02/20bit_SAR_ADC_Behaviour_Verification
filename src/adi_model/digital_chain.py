"""片上数字主链（独立于模拟 plant）。

输入一笔事务的规定字段，输出事件记录与 20 位码。**不读 plant 的增益、电荷或归一化**：
重构只用「ADC2 原码 + 实际 mask + 物理片 ID + 冻结系数」。

D1 调度 → D2 Follower → D3 DEM(Fig.19–21) → D4 Dither → D5 定点重构
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from adi_model.control.cross_slice_mux import (
    fig21_column_assignment,
    fig21_vertical_selection,
)
from adi_model.control.dem3d import round_robin_plan
from adi_model.control.follower import FOLLOWER_EVENTS, ResolvedPrefixes

N_SLICES = 18
N_ACTIVE = 8


@dataclass(frozen=True)
class Transaction:
    """一笔数字事务的输入。系数由外部冻结提供，不从 plant 读取。"""

    sample_id: int
    final_code: int          # 已决 9 位粗码
    dither_word: int         # 6 位
    adc2_code: int           # ADC2 原始码（12 位无符号）
    weights_q30: dict        # {(slice_id, "main"|"sub", unit): Q30 权重}


Q30 = 1 << 30
MAIN_UNITS = 17
SUB_UNITS = 5
ADC2_FRACTION_BITS = 12


def _dither_terms(word: int) -> tuple[int, int, float]:
    """Fig.19：U = 4·D6 + D4，L = 2·D5 + (4D3+2D2+D1)/7（粗 LSB 单位）。"""
    bits = [(word >> (5 - i)) & 1 for i in range(6)]
    upper = 4 * bits[0] + bits[2]
    lower = 2 * bits[1] + (4 * bits[3] + 2 * bits[4] + bits[5]) / 7.0
    return upper, lower, upper + lower


def _mask_bits(k: int) -> tuple[int, int]:
    """粗码 -> 主阵列 17 位温度计 + 子阵列 4 位二进制（第 5 位哑元恒 0）。"""
    main_code = (k >> 4) & 0x1F
    therm = min(main_code // 2, 15)
    ones = min(main_code - 2 * therm, 2)
    main = ((1 << therm) - 1) | (((1 << ones) - 1) << 15)
    sub = (k & 0xF) & 0b1111
    return main, sub


def run_transaction(tx: Transaction) -> dict:
    """执行一笔事务：调度 -> follower 事件 -> Fig.21 MUX -> 定点重构 -> 20 bit。

    MUX 语义（US10511316B2 Fig.20–21）：
        m[s][j] = y[s]  if j == reserved  else  h[j]
    即**保留列取该片纵向数据，其余列取共享横向数据**；
    y[s] == 0 只是该列数据为 0，**不表示该片退出转换**。
    """
    plan = round_robin_plan(tx.sample_id + 1, n_slices=N_SLICES, n_active=N_ACTIVE)
    slice_ids = np.asarray(plan.acq)[-1]
    prefixes = ResolvedPrefixes.from_final(np.array([tx.final_code], dtype=np.int64))

    upper, lower, total = _dither_terms(tx.dither_word)
    k = int(tx.final_code) - upper

    h_main, h_sub = _mask_bits(k)                 # 共享横向数据（位向量）
    b4, b5, b6 = (k >> 4) & 1, (k >> 3) & 1, (k >> 2) & 1
    y = fig21_vertical_selection(bool(b4), bool(b5), bool(b6))["selected"]  # 每片纵向数据

    reserved = tx.sample_id % MAIN_UNITS          # controller353 定保留列位置

    events = []
    for i in range(FOLLOWER_EVENTS):
        code_i = int(prefixes.codes[0][i])
        hm, hs = _mask_bits(code_i)
        events.append({"event_index": i, "committed_code": code_i,
                       "horizontal_main": hm, "horizontal_sub": hs,
                       "slice_ids": slice_ids.tolist()})

    # 每片最终 mask：普通列取 h，保留列取 y[s]；8 个活动片全部参与
    masks = []
    for slot in range(N_ACTIVE):
        bit = (int(y[slot]) << reserved) if reserved < MAIN_UNITS else 0
        m = (h_main & ~(1 << reserved)) | bit
        masks.append(m)

    acc = 0
    # 保留列用 y[s] 顶掉横向 h[reserved]，丢失/多出的权重必须**恰好补一次**
    h_bit = (h_main >> reserved) & 1
    for slot, sid in enumerate(slice_ids.tolist()):
        diff = h_bit - int(y[slot])
        if diff:
            acc += diff * int(tx.weights_q30.get((int(sid), "main", reserved), 0))
    for slot, sid in enumerate(slice_ids.tolist()):
        for unit in range(MAIN_UNITS):
            if (masks[slot] >> unit) & 1:
                acc += int(tx.weights_q30.get((int(sid), "main", unit), 0))
        for unit in range(4):
            if (h_sub >> unit) & 1:
                acc += int(tx.weights_q30.get((int(sid), "sub", unit), 0))

    H, M, L = k >> 5, (k >> 2) & 7, k & 3
    ident_ok = (32 * H + 4 * M + L) == k

    coarse_q = (acc * (1 << 20)) // (Q30 * 32)
    fine_q = (int(tx.adc2_code) << (20 - ADC2_FRACTION_BITS)) // 32
    code20 = coarse_q + fine_q
    sat = not (0 <= code20 < (1 << 20))

    return {
        "sample_id": tx.sample_id, "slice_ids": slice_ids.tolist(), "k": k,
        "k_decomp": {"H": H, "M": M, "L": L}, "k_identity_ok": ident_ok,
        "dither_u": upper, "dither_l": round(lower, 6), "dither_t": round(total, 6),
        "vertical_count": int(np.count_nonzero(y)), "reserved_column": reserved,
        "masks": masks, "n_active_slices_used": len(slice_ids),
        "adc2_code": tx.adc2_code, "code20": int(min(max(code20, 0), (1 << 20) - 1)),
        "valid": not sat, "overflow": bool(sat),
    }
