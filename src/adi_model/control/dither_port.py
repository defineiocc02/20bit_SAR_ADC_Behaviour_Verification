"""dither 的显式电荷口：六位字 -> 真实电荷 -> 数字解扰，以及范围/残余预算。

旧的 Fig19 静态路径把 dither 实现成"RDAC 码整体减 U"（`k = c - U`）。这在
**等间距、无失配**的码本里成立，在两种真实情形下会破：

1. **码域越界**：c - U 对低码会落到 0 以下。这不是数值 bug，是"用码字空间换 dither 幅度"
   的必然结果。真实电路里 dither 有自己的电容，不占用 RDAC 码域，所以正确做法是把 dither
   表示为**独立电荷口**，RDAC 码保持 c。
2. **非等间距码本**：分段阵列（Fig.11 的 15×2C+2×1C + 桥接二进制子阵列）里
   `VD(c) - VD(c-U)` 不等于 `U*Δ`。差额就是已知问题里的"上段残余"，必须显式记账，
   不能靠"最终码没 clip"掩盖。

本模块给出三件事，且都能被反例推翻：

* `charge_c`：同一六位字在电荷口的实际注入电荷；
* `code_shift_residual`：把同一字实现成码偏移时的残差 [V]/[LSB20]，等间距阵列应为 0；
* `range_ledger`：所有 64 个字下的 SADC 可用输入区间与 RDAC 合法码域，给出下界缺口。

关于任务树里那个 −4.855238970588 V：本模块用 `attenuated_dither_bound` 复原它的来源——
v_fs=5 V 时 `5*(1 - 63/(128*17)) = 4.855238970588235`，即"子阵列 β=1/17 衰减后
仍达 63/128 满幅的 dither"。这是**从数值反推的复原**，不是原文披露；本模型自己的缺口由
`range_ledger` 直接算出，不移植那个数。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from adi_model.simulation.config import ConfigError
from adi_model.control.dither import Fig19DitherProgram, Fig19StaticSpec, complete_flash3_sar6_prefix_table


@dataclass(frozen=True)
class DitherPort:
    """把 Fig19 六位字接到实际电容上的独立电荷口。

    Attributes:
        spec: 冻结的 Q32 规格（步长、低位权、极性）。
        c_lsb_f: 一个粗码步长对应的等效电容 [F]（= C_signal/512）。
        reference_swing_v: dither 电容看到的轨间摆幅 [V]（V_hi - V_lo）。
    """

    spec: Fig19StaticSpec
    c_lsb_f: float
    reference_swing_v: float

    def __post_init__(self):
        """校验规格与两个正实数量，不推断电容。"""
        if not isinstance(self.spec, Fig19StaticSpec):
            raise ConfigError("DitherPort requires an explicit Fig19StaticSpec")
        for name in ("c_lsb_f", "reference_swing_v"):
            value = getattr(self, name)
            if not np.isfinite(value) or value <= 0:
                raise ConfigError(f"DitherPort.{name} must be finite positive")
            object.__setattr__(self, name, float(value))

    @classmethod
    def from_program(cls, program: Fig19DitherProgram, *, c_lsb_f: float, reference_swing_v: float):
        """从已有 program 取规格，保证字解码只有一处定义。"""
        if not isinstance(program, Fig19DitherProgram):
            raise ConfigError("DitherPort.from_program needs a Fig19DitherProgram")
        return cls(program.spec, c_lsb_f, reference_swing_v)

    def lsb_equivalent(self, words) -> np.ndarray:
        """六位字折合的粗码 LSB 数（可为分数：低位权是 4:2:1/7）。"""
        program = Fig19DitherProgram.from_words(words, spec=self.spec)
        return program.total_q.astype(float) / self.spec.step_q

    def charge_c(self, words) -> np.ndarray:
        """电荷口实际注入的电荷 [C]；与 RDAC 码无关。"""
        return self.lsb_equivalent(words) * self.c_lsb_f * self.reference_swing_v

    def input_referred_v(self, words) -> np.ndarray:
        """折合到输入端的等效电压 [V]（= LSB 数 x 粗码步长）。"""
        return self.lsb_equivalent(words) * self.spec.coarse_step_v


def code_shift_residual(
    pool,
    codes,
    words,
    *,
    spec: Fig19StaticSpec,
    reference_high_v: float,
    reference_low_v: float,
    c_signal_f: float,
) -> dict:
    """比较"码偏移实现"与"独立电荷口实现"的实际电荷差。

    只有**上段 U** 能被码偏移表示（RDAC 码减 U）；下段 L 走的是 SADC 采样路径，
    码偏移根本无法表达它，因此本函数只比较 U 部分，并把 `lower_lsb` 单独列出。
    这一点必须写清楚，否则会把"下段没实现"误读成"上段有残余"。

    码偏移实现：`Q(c) - Q(c-U)`。
    电荷口实现：`U * C_lsb * (Vhi-Vlo)`。
    两者在等间距无失配阵列上相等；在分段/失配阵列上留下残余，这就是"上段残余"。

    Args:
        pool: 实际（可含失配）池，几何需 512 码。
        codes: (N,) 最终码。
        words: (N,) 六位字。
        spec: 冻结的 Fig19 规格（v_fs/步长/低位权/极性）。
        reference_high_v/reference_low_v: 实际轨电压。
        c_signal_f: 全 Signal 连接的归一化电容 [F]，用于把电荷换成输入等效 V。

    Returns:
        dict：最大残余 [C]/[V]/[LSB20]、对应码、码偏移非法码计数（不夹码），
        以及下段折合 LSB（明确不可由码偏移表达）。
    """
    if not isinstance(spec, Fig19StaticSpec):
        raise ConfigError("residual computation needs an explicit Fig19StaticSpec")
    values = np.asarray(codes, dtype=np.int64)
    program = Fig19DitherProgram.from_words(words, spec=spec)
    if not np.isfinite(c_signal_f) or c_signal_f <= 0:
        raise ConfigError("residual computation needs a positive signal capacitance")
    ids = np.broadcast_to(np.arange(pool.geometry.n_active, dtype=np.int64), (values.size, pool.geometry.n_active))
    shifted = values - program.upper_code
    legal = (shifted >= 0) & (shifted <= 511)
    illegal = int(np.count_nonzero(~legal))
    if illegal == values.size:
        raise ConfigError(
            "every code-shifted candidate is outside the code domain; "
            "no legal sample is left to measure a residual"
        )
    # 只对**合法**的 shifted 码求残差；非法样本单独计数，绝不夹紧后混进统计。
    states = np.zeros(values.size, dtype=np.int64)

    def q_of(code_vector):
        command = pool.select(code_vector, states, slice_ids=ids)
        return pool.charge_from_masks(
            ids,
            command,
            reference_high_v=float(reference_high_v),
            reference_low_v=float(reference_low_v),
        ).charge_c

    q_shift = q_of(values) - q_of(np.where(legal, shifted, values))
    c_lsb = c_signal_f / 512
    port_charge = program.upper_code.astype(float) * c_lsb * (reference_high_v - reference_low_v)
    residual_c = q_shift - port_charge
    residual_v = residual_c / c_signal_f
    residual_v = np.where(legal, residual_v, np.nan)
    lsb20 = abs(reference_high_v - reference_low_v) / 2**20
    worst = int(np.nanargmax(np.abs(residual_v)))
    return {
        "samples": int(values.size),
        "illegal_shifted_codes": illegal,
        "legal_samples": int(np.count_nonzero(legal)),
        "max_abs_residual_c": float(np.nanmax(np.abs(residual_c))),
        "max_abs_residual_v": float(np.nanmax(np.abs(residual_v))),
        "max_abs_residual_lsb20": float(np.nanmax(np.abs(residual_v)) / lsb20),
        "worst_index": worst,
        "worst_code": int(values[worst]),
        "worst_upper_code": int(program.upper_code[worst]),
        "lower_lsb": (program.lower_q.astype(float) / spec.step_q).tolist(),
        "code_shift_exact": bool(np.nanmax(np.abs(residual_v)) <= 1e-12),
        "_note": "only the upper part U is code-representable; the lower part L is a sampling-path injection",
    }


def range_ledger(
    spec: Fig19StaticSpec,
    *,
    v_high: float,
    v_low: float,
    prefix_table: np.ndarray | None = None,
) -> dict:
    """给出 64 个字下的输入区间缺口与早期 RDAC 非法码。

    SADC 看到 `x - L`，其自身输入域为 `[v_low, v_high]`。逐字求交后：
    可用输入下界 = v_low + max(L)，上界 = v_high + min(L)。
    RDAC 若实现成码偏移，则 `c - U` 必须仍在码域内，早期候选码会越界——这里如实计数。

    Args:
        spec: 冻结规格（提供 v_fs 与步长）。
        v_high/v_low: SADC 实际输入域端点 [V]。
        prefix_table: (512,E) 已决前缀表；None 用当前 core 的完整表。

    Returns:
        dict：`lower_bound_v`/`upper_bound_v`、相对 ±v_fs 的缺口、非法码统计。
    """
    if not np.isfinite(v_high) or not np.isfinite(v_low) or v_high <= v_low:
        raise ConfigError("range ledger needs a finite increasing SADC input domain")
    table = complete_flash3_sar6_prefix_table() if prefix_table is None else np.asarray(prefix_table)
    if table.ndim != 2 or table.shape[0] != 512:
        raise ConfigError("range ledger needs a (512,E) resolved prefix table")
    words = np.arange(64, dtype=np.int64)
    program = Fig19DitherProgram.from_words(words, spec=spec)
    lower_v = program.lower_v
    total_v = program.total_v
    upper_code = program.upper_code
    # SADC 输入为 x - L：L 越大，可用的输入下界越高。
    lower_bound = float(v_low) + float(lower_v.max())
    upper_bound = float(v_high) + float(lower_v.min())
    shifted = table[None, :, :] - upper_code[:, None, None]
    illegal_events = int(np.count_nonzero((shifted < 0) | (shifted > 511)))
    illegal_words = int(np.count_nonzero(np.any((shifted < 0) | (shifted > 511), axis=(1, 2))))
    return {
        "words": 64,
        "v_high_v": float(v_high),
        "v_low_v": float(v_low),
        "max_lower_dither_v": float(lower_v.max()),
        "max_total_dither_v": float(total_v.max()),
        "max_upper_code": int(upper_code.max()),
        "sadc_lower_bound_v": lower_bound,
        "sadc_upper_bound_v": upper_bound,
        "lower_deficit_v": float(lower_bound - v_low),
        "upper_deficit_v": float(v_high - upper_bound),
        "lower_deficit_lsb20": float((lower_bound - v_low) / ((v_high - v_low) / 2**20)),
        "illegal_early_events": illegal_events,
        "illegal_words": illegal_words,
        "code_shift_legal": bool(illegal_events == 0),
        "_note": "deficits are relative to the SADC input domain, not to the digital output range",
    }


def attenuated_dither_bound(v_fs: float, *, fraction: float = 63 / 128, beta: float = 1 / 17) -> dict:
    """复原"子阵列 β 衰减后的 dither"造成的输入下界缺口。

    任务树的 −4.855238970588 V 在 v_fs=5 V 下等于 `5*(1 - 63/(128*17))`。
    本函数把这个复原写成可复算的形式，并明确它是**从数值反推**的解释：
    `fraction` 与 `beta` 都要由调用方给出，不能当作原文披露。

    Args:
        v_fs: 输入峰值 [V]。
        fraction: dither 幅度占满幅的比例（默认 63/128，复原值）。
        beta: 子阵列到主节点的耦合系数（Fig.11 几何下为 1/17）。

    Returns:
        dict：缺口 [V]、下界 [V]、以及与 4.855238970588 的一致性检查。
    """
    if not np.isfinite(v_fs) or v_fs <= 0:
        raise ConfigError("attenuated dither bound needs a positive full scale")
    for name, value in (("fraction", fraction), ("beta", beta)):
        if not np.isfinite(value) or not 0 < value <= 1:
            raise ConfigError(f"{name} must be in (0,1]")
    deficit = v_fs * fraction * beta
    reference = 4.855238970588
    return {
        "v_fs_v": float(v_fs),
        "fraction": float(fraction),
        "beta": float(beta),
        "deficit_v": float(deficit),
        "lower_bound_v": float(-v_fs + deficit),
        "matches_task_tree_number": bool(
            abs(v_fs) == 5.0 and abs((-v_fs + deficit) + reference) < 1e-9
        ),
        "_grade": "RECONSTRUCTED_FROM_NUMBER; fraction/beta are inputs, not disclosures",
    }


def digital_descramble(total_v, *, reference_high_v: float, reference_low_v: float) -> np.ndarray:
    """数字侧已知公共项：减去同一 total_v（不重复纠正 U）。

    旧路径的坑是"SADC 侧减 L、RDAC 侧减 T、数字侧再减一次 U"。本函数只做一次减法，
    并用 `total_v` 单一口径，避免把 L 与 U 混用。

    Args:
        total_v: (N,) 与程序同源的 T = L + U*Δ [V]。
        reference_high_v/reference_low_v: 仅用于校验量级，不参与符号推断。

    Returns:
        (N,) 已解扰的输入等效电压修正项（负号表示注入极性为减）。
    """
    values = np.asarray(total_v, dtype=float)
    if np.any(~np.isfinite(values)):
        raise ConfigError("digital descramble needs finite dither amplitudes")
    span = abs(reference_high_v - reference_low_v)
    if np.any(np.abs(values) > span):
        raise ConfigError("dither amplitude exceeds the reference span")
    return -values


__all__ = [
    "DitherPort",
    "attenuated_dither_bound",
    "code_shift_residual",
    "digital_descramble",
    "range_ledger",
]
