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
    """执行一笔事务：调度 -> follower 事件 -> DEM 映射 -> 定点重构 -> 20 bit。"""
    plan = round_robin_plan(tx.sample_id + 1, n_slices=N_SLICES, n_active=N_ACTIVE)
    slice_ids = np.asarray(plan.acq)[-1]
    prefixes = ResolvedPrefixes.from_final(np.array([tx.final_code], dtype=np.int64))

    upper, lower, total = _dither_terms(tx.dither_word)
    k = int(tx.final_code) - upper

    # D3：Fig.21 纵向分配 + 保留列，作用于物理片；横向由 k 的高位决定
    b4, b5, b6 = (k >> 4) & 1, (k >> 3) & 1, (k >> 2) & 1
    vertical = fig21_vertical_selection(bool(b4), bool(b5), bool(b6))
    reserved = tx.sample_id % N_ACTIVE
    columns = fig21_column_assignment(
        reserved_position=reserved,
        vertical_value=vertical["count"],
        horizontal_value=(k & 0xF) + 1,          # 实际横向数据 = 低 4 位
    )

    # D5：逐事件都产出一组真实 mask，最后一个事件与完整粗码一致
    events = []
    for i in range(FOLLOWER_EVENTS):
        code_i = int(prefixes.codes[0][i])
        m_i, s_i = _mask_bits(code_i)
        events.append(
            {
                "event_index": i,
                "committed_code": code_i,
                "main_mask": m_i,
                "sub_mask": s_i,
                "slice_ids": slice_ids.tolist(),
            }
        )

    # 定点重构：只用最后一组真实 mask + 冻结系数 + ADC2 原码
    main_mask, sub_mask = _mask_bits(k)
    acc = 0
    used = []
    for slot, sid in enumerate(slice_ids.tolist()):
        # 纵向选中的片子才承担高位权重；否则取横向数据
        take = bool(vertical["selected"][slot]) if slot < N_ACTIVE else False
        if not take:
            continue
        used.append(int(sid))
        for unit in range(MAIN_UNITS):
            if (main_mask >> unit) & 1:
                acc += int(tx.weights_q30.get((int(sid), "main", unit), 0))
        for unit in range(4):
            if (sub_mask >> unit) & 1:
                acc += int(tx.weights_q30.get((int(sid), "sub", unit), 0))

    coarse_q = (acc * (1 << 20)) // (Q30 * 32)          # 主阵列折算到 20 bit
    fine_q = (int(tx.adc2_code) << (20 - ADC2_FRACTION_BITS)) // 32
    code20 = coarse_q + fine_q
    saturated = not (0 <= code20 < (1 << 20))
    code20 = min(max(code20, 0), (1 << 20) - 1)

    return {
        "sample_id": tx.sample_id,
        "slice_ids": slice_ids.tolist(),
        "k": k,
        "dither_u": upper,
        "dither_l": round(lower, 6),
        "dither_t": round(total, 6),
        "vertical_count": vertical["count"],
        "vertical_weight_c": vertical["weight_c"],
        "reserved_column": columns["reserved_column"],
        "n_from_350": columns["n_from_350"],
        "horizontal_data": columns["columns"][0],
        "events": events,
        "used_slices": used,
        "adc2_code": tx.adc2_code,
        "code20": int(code20),
        "valid": not saturated,
        "overflow": bool(saturated),
    }
