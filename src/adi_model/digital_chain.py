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


N_COLUMNS = 16          # 15 个有效 2C 逻辑列 + 1 个固定零（保留列）


def _horizontal_columns(k: int, reserved: int) -> list[int]:
    """横向数据 h：15 个有效列按 H 做温度计，保留列为**固定零**（不是从有效位里挖）。

    US10511316B2 Fig.20：shuffler352 的一个输入固定为 0，该零位随打乱移动形成保留列。
    因此 h[reserved] == 0 且 sum(h) == H 必须同时成立。
    """
    H = (k >> 5) & 0xF
    cols = [0] * N_COLUMNS
    data = [c for c in range(N_COLUMNS) if c != reserved]
    for c in data[:H]:
        cols[c] = 1
    return cols


def vertical_bits(k: int, n_active: int = 8) -> list[int]:
    """纵向数据 y：恰好 M 个 1，M = (k>>2)&7。"""
    M = (k >> 2) & 7
    y = [0] * n_active
    for i in range(M):
        y[i] = 1
    return y


def _mask_bits(k: int) -> tuple[int, int]:
    """粗码 -> 主阵列 17 个物理开关位（15×2C + B4 + S4）+ 子阵列 4 位。"""
    main_code = (k >> 4) & 0x1F
    therm = min(main_code // 2, 15)
    ones = min(main_code - 2 * therm, 2)
    return ((1 << therm) - 1) | (((1 << ones) - 1) << 15), (k & 0xF) & 0b1111


def run_transaction(tx: Transaction) -> dict:
    """调度 -> follower 事件 -> Fig.20–21 MUX -> 位权自检 -> 定点重构。

    MUX：m[s][j] = y[s] if j == reserved else h[j]，且 h[reserved] == 0。
    因此 Σ_{s,j} m = 8H + M，且 4·(8H+M) + L == k。
    """
    plan = round_robin_plan(tx.sample_id + 1, n_slices=N_SLICES, n_active=N_ACTIVE)
    slice_ids = np.asarray(plan.acq)[-1]
    prefixes = ResolvedPrefixes.from_final(np.array([tx.final_code], dtype=np.int64))

    upper, lower, total = _dither_terms(tx.dither_word)
    k = int(tx.final_code) - upper
    H, M, L = k >> 5, (k >> 2) & 7, k & 3
    reserved = tx.sample_id % N_COLUMNS
    h = _horizontal_columns(k, reserved)
    y = vertical_bits(k, N_ACTIVE)

    masks = [[y[s] if j == reserved else h[j] for j in range(N_COLUMNS)] for s in range(N_ACTIVE)]
    total_selected = sum(sum(row) for row in masks)

    events = []
    for i in range(FOLLOWER_EVENTS):
        events.append({"event_index": i, "committed_code": int(prefixes.codes[0][i]),
                       "slice_ids": slice_ids.tolist()})

    # 重构：Σ w·m / Σ w_full（w 为 C_eff/Cf 的 Q30），8 片一起求和，不额外除 8
    acc = 0
    for slot, sid in enumerate(slice_ids.tolist()):
        for j in range(N_COLUMNS):
            if masks[slot][j]:
                acc += int(tx.weights_q30.get((int(sid), "col2c", j), 0))
    code20 = None
    if tx.weights_q30:
        wf = {(sid, j): tx.weights_q30[(sid, j)] / float(1 << 30) for (sid, j) in tx.weights_q30}
        a_all = [[1] * N_COLUMNS for _ in range(N_ACTIVE)]
        recon = reconstruct(0.0, [[0] * N_COLUMNS for _ in range(N_ACTIVE)], a_all, wf,
                            slice_ids.tolist())
        code20 = recon["code20"]
    return {
        "sample_id": tx.sample_id, "slice_ids": slice_ids.tolist(), "k": k,
        "code20": code20,
        "H": H, "M": M, "L": L,
        "mask_selected": total_selected,
        "expect_8H_plus_M": 8 * H + M,
        "weight_identity_ok": total_selected == 8 * H + M,
        "code_identity_ok": 4 * total_selected + L == k,
        "h_reserved_is_zero": h[reserved] == 0,
        "sum_h_is_H": sum(h) == H,
        "sum_y_is_M": sum(y) == M,
        "dither_u": upper, "dither_l": round(lower, 6), "dither_t": round(total, 6),
        "reserved_column": reserved,
        "masks": masks, "n_active_slices_used": len(slice_ids),
        "weight_acc_q30": acc,
        "adc2_code": tx.adc2_code,
    }

# ---------------------------------------------------------------- 重构（数学参考）
V_FS = 3.0
CODE_BITS = 20


def _known_term(m: int, t_low_v: float, t_high_v: float) -> float:
    """m 只选择参考连接：0 -> t_low，1 -> t_high。**不因 m=0 删除整项。**"""
    return t_high_v if m else t_low_v


def forward_observation(
    x_v: float,
    masks: list[list[int]],
    a: list[list[int]],
    weights: dict,
    slice_ids: list[int],
    *,
    t_low_v: float = 0.0,
    t_high_v: float = 0.0,
) -> float:
    """y = Σ_{所有单元} w·(a·x + t(m))。循环**不跳过 m=0**。

    weights 以真实物理片 ID 为键：{(physical_slice_id, column): w}。
    """
    y = 0.0
    for slot, sid in enumerate(slice_ids):
        for j in range(len(masks[slot])):
            key = (int(sid), j)
            if key not in weights:
                raise KeyError(f"missing weight for {key}")
            w = weights[key]
            y += w * (a[slot][j] * x_v + _known_term(masks[slot][j], t_low_v, t_high_v))
    return y


def reconstruct(
    y_v: float,
    masks: list[list[int]],
    a: list[list[int]],
    weights: dict,
    slice_ids: list[int],
    *,
    t_low_v: float = 0.0,
    t_high_v: float = 0.0,
    bias_v: float = 0.0,
) -> dict:
    """x̂ = (y − b − Σ w·t(m)) / (Σ w·a)，遍历所有单元。"""
    num = y_v - bias_v
    den = 0.0
    for slot, sid in enumerate(slice_ids):
        for j in range(len(masks[slot])):
            key = (int(sid), j)
            if key not in weights:
                raise KeyError(f"missing weight for {key}")
            w = weights[key]
            num -= w * _known_term(masks[slot][j], t_low_v, t_high_v)
            den += w * a[slot][j]
    if den == 0.0:
        raise ValueError("Σw·a == 0: no signal-carrying unit selected")
    x_hat = num / den
    code = int(round((x_hat / V_FS + 1.0) * (1 << (CODE_BITS - 1))))
    return {"x_hat_v": x_hat, "code20": min(max(code, 0), (1 << CODE_BITS) - 1), "sum_wa": den}
