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
    """一笔数字事务的输入。"""

    sample_id: int
    final_code: int          # 已决 9 位粗码（follower 用它推前缀）
    dither_word: int         # 6 位
    adc2_code: int           # ADC2 原始码
    ones_per_code: float     # 冻结的每码权重（C/Cf），工程标定值


def _dither_terms(word: int) -> tuple[int, int, int]:
    """Fig.19：U = 4·D6 + D4，L = 2·D5 + (4D3+2D2+D1)/7，T = U + L（单位：粗 LSB）。"""
    bits = [(word >> (5 - i)) & 1 for i in range(6)]  # D6..D1
    upper = 4 * bits[0] + bits[2]
    lower = 2 * bits[1] + (4 * bits[3] + 2 * bits[4] + bits[5]) / 7.0
    return upper, lower, upper + lower


def run_transaction(tx: Transaction) -> dict:
    """执行一笔事务，返回逐事件记录。"""
    plan = round_robin_plan(tx.sample_id + 1, n_slices=N_SLICES, n_active=N_ACTIVE)
    slice_ids = np.asarray(plan.acq)[-1]
    prefixes = ResolvedPrefixes.from_final(np.array([tx.final_code], dtype=np.int64))

    upper, lower, total = _dither_terms(tx.dither_word)
    k = int(tx.final_code) - upper                      # RDAC 控制码
    b4, b5, b6 = (k >> 4) & 1, (k >> 3) & 1, (k >> 2) & 1
    vertical = fig21_vertical_selection(bool(b4), bool(b5), bool(b6))
    columns = fig21_column_assignment(
        reserved_position=tx.sample_id % 16,
        vertical_value=vertical["count"],
        horizontal_value=max(vertical["count"], 1) + 1,
    )

    events = []
    applied = prefixes.codes[0]
    for i in range(FOLLOWER_EVENTS):
        events.append({"event_index": i, "committed_prefix": int(applied[i])})

    active = vertical["count"]
    est = sum(
        1 for s in range(N_ACTIVE) if vertical["selected"][s % N_ACTIVE]
    ) * tx.ones_per_code
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
        "events": events,
        "adc2_code": tx.adc2_code,
        "active_slices": active,
        "estimate_lsb": round(est + tx.adc2_code * 1e-3, 6),
    }
