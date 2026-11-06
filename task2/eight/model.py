"""八片原生子阵列底板扰动的行为参考。"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from task1.experiments.adctoolbox_calibration import SwitchState
from task1.experiments.bridge_lms_comparison import Q32
from task1.experiments.residue_control_study import MapperTable
from task1.experiments.run_effects import (
    MAIN_WEIGHTS,
    SUB_WEIGHTS,
    Capacitors,
    ExperimentConfig,
    _unpack,
)
from task1.model.scheduler import GROUP_SIZE, SLICE_COUNT
from task2.model.dither_word_gen import DitherWordGenerator

COARSE_CODES = 512
MAIN_BITS = len(MAIN_WEIGHTS)
SUB_BITS = len(SUB_WEIGHTS)
FEATURES_PER_SLICE = MAIN_BITS + SUB_BITS - 1
SUB_DITHER_WEIGHTS = np.array(SUB_WEIGHTS[:-1], dtype=np.uint8)


@dataclass(frozen=True)
class Dither:
    """同笔八个有序 slot 的采样底板掩码与整数扰动标签。"""

    word: np.ndarray
    upper: np.ndarray
    lower_eighth: np.ndarray
    total_eighth: np.ndarray
    setting: np.ndarray
    sample_sub_high: np.ndarray


def decode(word: np.ndarray) -> Dither:
    """按六位字设定子阵列底板，字 0 对应全低轨。"""
    word = np.asarray(word)
    if word.dtype.kind not in "iu" or np.any((word < 0) | (word >= 64)):
        raise ValueError("each dither word must be an integer in [0, 63]")
    word = word.astype(np.uint8, copy=False)
    upper = 4 * (word >> 5) + ((word >> 3) & 1)
    lower_integer = 2 * ((word >> 4) & 1)
    count = word & 7
    setting = (
        upper[..., None] + lower_integer[..., None] + (np.arange(GROUP_SIZE) < count[..., None])
    )
    # Task1 的物理位序为 8C、4C、2C、1C、dummy；p 的二进制位序相反。
    mask = ((setting[..., None] & SUB_DITHER_WEIGHTS) != 0) @ (1 << np.arange(4))
    lower_eighth = GROUP_SIZE * lower_integer + count
    return Dither(
        word,
        upper,
        lower_eighth,
        GROUP_SIZE * upper + lower_eighth,
        setting,
        mask.astype(np.uint8),
    )


def words(enabled: np.ndarray, seed: int) -> np.ndarray:
    """生成与 RTL 一致的采样序列，关闭时保持 PRBS 状态。"""
    generator = DitherWordGenerator()
    if generator.step(seed_load=True, seed=seed).seed_error:
        raise ValueError("a nonzero dither seed is required")
    return np.fromiter(
        (
            generator.step(sample_accept=True, sample_id=i, dither_enable=bool(en)).word
            for i, en in enumerate(enabled)
        ),
        dtype=np.uint8,
        count=len(enabled),
    )


def native_step_v(experiment: ExperimentConfig) -> float:
    """计算单位子电容切轨造成的名义输入折算步长。"""
    return 2 * experiment.full_scale_peak_v / (sum(MAIN_WEIGHTS) * (1 + sum(SUB_WEIGHTS)))


def coarse_decision(
    input_v: np.ndarray, dither: Dither, thresholds: np.ndarray, step_v: float
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """独立 SADC 辅助 DAC 加低路后判决，返回原码、最终命令和前级有效。"""
    sadc_v = input_v + dither.lower_eighth * (step_v / GROUP_SIZE)
    coarse = np.searchsorted(thresholds, sadc_v, side="right") - 1
    command = coarse + dither.upper
    peak = thresholds[-1]
    valid = (
        np.isfinite(input_v)
        & (input_v >= -peak)
        & (input_v < peak)
        & (coarse >= 0)
        & (coarse < COARSE_CODES)
        & (command < COARSE_CODES)
    )
    return coarse, command, valid


def active_features(state: SwitchState, dither: Dither) -> np.ndarray:
    """给出每片主阵列转换位与子阵列转换位减采样位的有符号特征。"""
    main = _unpack(state.main, MAIN_BITS).astype(np.int8)
    sub = _unpack(state.sub, SUB_BITS)[..., :-1].astype(np.int8)
    sampled = _unpack(dither.sample_sub_high, SUB_BITS)[..., :-1]
    return np.concatenate((main, sub - sampled), axis=-1)


def fit_features(state: SwitchState, feature: np.ndarray) -> np.ndarray:
    """按物理片号散布同笔有序特征，供片外 LMS 使用。"""
    dense = np.zeros((len(state.plan), SLICE_COUNT, FEATURES_PER_SLICE), dtype=np.int8)
    dense[np.arange(len(state.plan))[:, None], state.plan] = feature
    return dense.reshape(len(state.plan), -1)


def weighted_sum(plan: np.ndarray, feature: np.ndarray, weight: np.ndarray) -> np.ndarray:
    """用物理片号读取每片权重并累加。"""
    return np.einsum("rsi,rsi->r", feature, weight[plan])


@dataclass(frozen=True)
class ChargePool:
    """由固定制造电容生成的两节点闭式增益和底板权重。"""

    gain: np.ndarray
    weight: np.ndarray

    @classmethod
    def from_caps(cls, cap: Capacitors, experiment: ExperimentConfig) -> ChargePool:
        """将实际电容映射到输入等效 V，保持每颗物理切片的桥衰减。"""
        if any(np.any(~np.isfinite(c) | (c <= 0)) for c in (cap.main, cap.sub, cap.bridge)):
            raise ValueError("the two-node model requires finite positive capacitors")
        nominal_c = GROUP_SIZE * sum(MAIN_WEIGHTS) * experiment.unit_cap_f
        span_over_c = 2 * experiment.full_scale_peak_v / nominal_c
        beta = cap.bridge / (cap.bridge + cap.sub.sum(axis=1))
        weight = np.column_stack((cap.main, cap.sub[:, :-1] * beta[:, None])) * span_over_c
        return cls(cap.main.sum(axis=1) / nominal_c, weight)

    def affine(self, plan: np.ndarray, feature: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """给出 r = gain·(x−Vlow)−base 的增益和物理底板截距。"""
        return self.gain[plan].sum(axis=1), weighted_sum(plan, feature, self.weight)


@dataclass(frozen=True)
class ConversionState:
    """前级命令、物理掩码、名义截距与同笔可观测特征。"""

    switches: SwitchState
    dither: Dither
    features: np.ndarray
    correction_q32: np.ndarray
    nominal_base_v: np.ndarray


def conversion_state(
    command: np.ndarray,
    plan: np.ndarray,
    phases: tuple[np.ndarray, np.ndarray],
    dither: Dither,
    thresholds: np.ndarray,
    step_v: float,
    table: MapperTable,
) -> ConversionState:
    """按最终合法命令生成 Fig.11 或 Fig.21 开关及补偿。"""
    if np.any((command < 0) | (command >= COARSE_CODES)):
        raise ValueError("RDAC command is outside the 9-bit domain")
    switches, correction = table.lookup(command, plan, phases)
    peak = thresholds[-1]
    # 使用旧 Fig.11 码本，显式加 Fig.21 转换回放差值一次并减总采样扰动一次。
    nominal_base = (
        thresholds[command]
        + peak
        + correction * (peak / Q32)
        - dither.total_eighth * (step_v / GROUP_SIZE)
    )
    return ConversionState(
        switches, dither, active_features(switches, dither), correction, nominal_base
    )
