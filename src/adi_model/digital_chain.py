"""片上数字主链（独立于模拟 plant）。

输入一笔事务的规定字段，输出事件记录与 20 位码。**不读 plant 的增益、电荷或归一化**：
重构只用「ADC2 原码 + 实际 mask + 物理片 ID + 冻结系数」。

D1 调度 → D2 Follower → D3 DEM(Fig.19–21) → D4 Dither → D5 定点重构
"""

from __future__ import annotations

from dataclasses import dataclass, replace

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


DITHER_DENOMINATOR = 8     # 工程模式（八片）。专利七片示例用 7，单独标识。


def _dither_terms(word: int, denominator: int = None) -> tuple[int, int, float]:
    """Fig.19：U = 4·D6 + D4，L = 2·D5 + (4D3+2D2+D1)/D。

    D = DITHER_DENOMINATOR(8) 为主模式 [工程]；D = 7 属专利七片示例 [原文]。
    """
    d = DITHER_DENOMINATOR if denominator is None else denominator
    bits = [(word >> (5 - i)) & 1 for i in range(6)]
    upper = 4 * bits[0] + bits[2]
    lower = 2 * bits[1] + (4 * bits[3] + 2 * bits[4] + bits[5]) / float(d)
    return upper, lower, upper + lower


N_COLUMNS = 16          # 15 个有效 2C 逻辑列 + 1 个固定零（保留列）
N_SUB_COLUMNS = 4       # 子阵列 8/4/2/1，由剩余低位 L 控制
N_TOTAL = N_COLUMNS + N_SUB_COLUMNS


def _horizontal_columns(k: int, reserved: int, h_phase: int = 0) -> list[int]:
    """横向数据 h：15 个有效列按 H 做温度计，保留列为**固定零**（不是从有效位里挖）。

    US10511316B2 Fig.20：shuffler352 的一个输入固定为 0，该零位随打乱移动形成保留列。
    因此 h[reserved] == 0 且 sum(h) == H 必须同时成立。
    """
    H = (k >> 5) & 0xF
    cols = [0] * N_COLUMNS
    data = [c for c in range(N_COLUMNS) if c != reserved]
    n = len(data)
    start = h_phase % n if n else 0                      # 横向置换：按相位轮转起点
    for i in range(H):
        cols[data[(start + i) % n]] = 1
    return cols


def vertical_bits(k: int, n_active: int = 8, v_phase: int = 0) -> list[int]:
    """纵向数据 y：恰好 M 个 1，M = (k>>2)&7，按 v_phase 轮转选片。"""
    M = (k >> 2) & 7
    y = [0] * n_active
    for i in range(M):
        y[(v_phase + i) % n_active] = 1      # 纵向置换：不是总选前 M 片
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
    h_phase = tx.sample_id % 15
    v_phase = tx.sample_id % N_ACTIVE
    h = _horizontal_columns(k, reserved, h_phase)
    y = vertical_bits(k, N_ACTIVE, v_phase)

    sub_bits = [(L >> (3 - i)) & 1 for i in range(N_SUB_COLUMNS)]   # L 的 4 位进子阵列
    masks = [[y[s] if j == reserved else h[j] for j in range(N_COLUMNS)] for s in range(N_ACTIVE)]
    total_selected = sum(sum(row) for row in masks)
    masks_full = [row + list(sub_bits) for row in masks]

    # Follower：每个已决事件生成该事件自己的 mask（不是只记前缀）
    events = []
    for i in range(FOLLOWER_EVENTS):
        code_i = int(prefixes.codes[0][i])
        k_i = code_i - upper
        h_i = _horizontal_columns(k_i, reserved, h_phase)     # 用本事件前缀
        y_i = vertical_bits(k_i, N_ACTIVE, v_phase)
        ev_masks = [
            [y_i[s] if j == reserved else h_i[j] for j in range(N_COLUMNS)]
            for s in range(N_ACTIVE)
        ]
        events.append({"event_index": i, "committed_code": code_i, "k_event": k_i,
                       "low_bits": k_i & 3,
                       "rdac_mask_units": int(sum(sum(r) for r in ev_masks)),
                       "ev_masks": ev_masks,
                       "slice_ids": slice_ids.tolist()})

    # 重构：Σ w·m / Σ w_full（w 为 C_eff/Cf 的 Q30），8 片一起求和，不额外除 8
    acc = 0
    for slot, sid in enumerate(slice_ids.tolist()):
        for j in range(N_COLUMNS):
            if masks[slot][j]:
                acc += int(tx.weights_q30.get((int(sid), "col2c", j), 0))
    code20 = None
    overflow = False
    if tx.weights_q30:
        a_all_full = [[1] * N_TOTAL for _ in range(N_ACTIVE)]
        # ADC2 原码 -> Q24 观测（12 位码、0.45 V 窗口）
        # 后级观测只用 ADC2 原码（不再混入输入折算量）
        y_q24 = int(tx.adc2_code) * int(round(ADC2_LSB_V * (1 << V_FRAC)))
        recon = reconstruct_int(
            y_q24, masks_full, a_all_full, tx.weights_q30, slice_ids.tolist(),
            t_low_q24=0, t_high_q24=int(round(V_FS * (1 << V_FRAC))),
        )
        # 完整重构结果就是输入估计，不截位、不拼粗码。
        # 主链以实际 mask（由 k 生成）重构，故其坐标是 x − TΔ；补 TΔ 一次回到输入坐标。
        T_q24 = int(round(total * COARSE_LSB_V * (1 << V_FRAC)))
        x_est_q24 = int(recon["x_q24"]) + T_q24
        # 只在最后做一次 20 位量化与饱和
        signed = _div_round(x_est_q24 << (CODE_BITS - 1), int(round(V_FS * (1 << V_FRAC))))
        code20 = signed + (1 << (CODE_BITS - 1))
        code20 = min(max(code20, 0), (1 << CODE_BITS) - 1)
        overflow = recon["overflow"]
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
        "masks": masks, "events": events, "n_active_slices_used": len(slice_ids),
        "weight_acc_q30": acc,
        "adc2_code": tx.adc2_code,
    }

# ---------------------------------------------------------------- 重构（数学参考）
V_FS = 3.0
ADC2_LSB_V = 0.45 / 4096      # ADC2 12 位、0.45 V 窗口 [工程]
COARSE_LSB_V = 6.0 / 512      # 粗 LSB = 11.71875 mV [工程]
CODE_BITS = 20
W_FRAC = 30        # 权重 Q30
V_FRAC = 24        # 电压量 Q24
ACC_BITS = 96


def _div_round(n: int, d: int) -> int:
    """就近舍入（半数远离零），负数对称。RTL 需实现同一规则。"""
    if d <= 0:
        raise ValueError("divisor must be positive")
    if n >= 0:
        return (n + d // 2) // d
    return -((-n + d // 2) // d)


def reconstruct_int(
    y_q24: int,
    masks: list[list[int]],
    a: list[list[int]],
    w_q30: dict,
    slice_ids: list[int],
    *,
    t_low_q24: int = 0,
    t_high_q24: int = 0,
    bias_q24: int = 0,
) -> dict:
    """整数定点反解：x̂_q24 = ((y − Σw·t) << 30) / (Σ w·a)。

    w 为 Q30、y/t/b 为 Q24；乘积自然落在 Q54，右移 30 回到 Q24。
    累加器 96 位。最终 20 位用就近舍入（.5 向上）并饱和。
    """
    num = int(y_q24) - int(bias_q24)
    den = 0
    for slot, sid in enumerate(slice_ids):
        for j in range(len(masks[slot])):
            key = (int(sid), j)
            if key not in w_q30:
                raise KeyError(f"missing weight for {key}")
            w = int(w_q30[key])
            t = t_high_q24 if masks[slot][j] else t_low_q24
            num -= (w * t) >> W_FRAC
            den += w * a[slot][j]
    if den == 0:
        raise ValueError("Σw·a == 0")
    # 96 位上界（显式）：|项| <= 2^31(w) * 2^25(t) = 2^56；最多 8*20=160 项
    # => |Σ| <= 160 * 2^56 < 2^64 << 2^96。超出即视为实现错误。
    if abs(num) >= (1 << 96) or abs(den) >= (1 << 96):
        raise OverflowError("accumulator exceeds the 96-bit contract")
    x_q24 = _div_round(num << W_FRAC, den)
    # code = round((x/V_FS + 1) * 2^19)  =>  ((x_q24 << 19) / (V_FS * 2^24)) + 2^19
    lsb = int(round(V_FS * (1 << V_FRAC)))
    signed = _div_round(x_q24 << (CODE_BITS - 1), lsb)   # 先在有符号量上舍入
    code = signed + (1 << (CODE_BITS - 1))               # 再加中点
    sat = not (0 <= code < (1 << CODE_BITS))
    return {"x_q24": x_q24, "code20": min(max(code, 0), (1 << CODE_BITS) - 1),
            "overflow": sat, "den_q30": den}


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
    # 先在**有符号量**上舍入（半数远离零），再加 offset-binary 中点
    signed = (x_hat / V_FS) * (1 << (CODE_BITS - 1))
    signed = int(signed + 0.5) if signed >= 0 else -int(-signed + 0.5)
    code = signed + (1 << (CODE_BITS - 1))
    return {"x_hat_v": x_hat, "code20": min(max(code, 0), (1 << CODE_BITS) - 1), "sum_wa": den}


def run_sequence(
    txs: list[Transaction], *, reset_at: int | None = None, stall_window: tuple[int, int] | None = None
) -> dict:
    """事务序列：正常接收 / stall（不消耗 sample_id）/ reset（清空在途、归零）/ 排空。

    返回逐笔事件与计数守恒：accepted + stalled + resets == len(txs)。
    """
    log = []
    held: list[Transaction] = []
    sid = 0
    accepted = stalled = resets = 0
    retired = 0
    for i, tx in enumerate(txs):
        if reset_at is not None and i == reset_at:
            sid = 0
            resets += 1
            log.append({"at": i, "event": "reset", "sample_id_after": sid})
            continue
        if stall_window is not None and stall_window[0] <= i < stall_window[1]:
            # 保序：stall 时**不越过**当前事务，把本项及其后全部押后
            stalled += 1
            held.extend(replace(t, sample_id=sid + n) for n, t in enumerate(txs[i:]))
            log.append({"at": i, "event": "stall", "held": True, "sample_id_held": sid})
            break
        r = run_transaction(replace(tx, sample_id=sid))
        accepted += 1
        retired_now = 1
        retired += 1
        log.append({"at": i, "event": "accepted", "sample_id": r["sample_id"],
                    "code20": r["code20"], "slice_ids": r["slice_ids"]})
        sid += 1
    # 排空：被押后的事务按原顺序逐笔处理
    for tx in held:
        r = run_transaction(tx)
        retired += 1
        log.append({"at": -1, "event": "retried", "sample_id": r["sample_id"],
                    "code20": r["code20"], "src": tx.sample_id})
        sid += 1
    accepted_total = accepted + len(held)
    cancelled = resets
    pending = accepted_total - retired - cancelled
    return {"log": log, "accepted": accepted_total, "stalled": stalled, "resets": resets,
            "retired": retired, "cancelled": cancelled, "pending": pending,
            "conserved": accepted_total == retired + cancelled + pending and pending == 0,
            "next_sample_id": sid}
