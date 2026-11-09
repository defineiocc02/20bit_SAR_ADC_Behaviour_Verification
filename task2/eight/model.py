# =============================================================================
# 文件名     : model.py
# 模块名     : model
# 版本       : v1.0
# 功能       : 八片负注入数字合同的行为参考。
#
# 设计说明   :
#   * 承担 八片原生子阵列模型与对照实验 中的一环；输入输出的物理口径与单位以同目录 SPEC/README 为准。
# -----------------------------------------------------------------------------
# 变更记录
# -----------------------------------------------------------------------------
# 初始版本，暂无变更记录。
# =============================================================================

"""八片负注入数字合同的行为参考。

SADC 判 x−L，命令 k=c−U（下溢拒绝）；残差为 x−VD(k)−T，重构加回 T。
逻辑注入位图对应理想受控电荷源，幅度假定与子阵列转换权重相同；这属于工程假设。
低轨→高轨底板采样的两节点网络给出相反符号，不能把该网络直接称作负注入实现。
独立节点参考在子节点 KCL 中显式加入辅助电荷，检验该假设下的数学一致性。
原文的 upper 扣码关系与本工程电容连接、位图及单位电荷映射须分别核对。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from adi_model.capacitors import MAIN_WEIGHTS, SUB_WEIGHTS, Capacitors, ExperimentConfig, unpack
from adi_model.codes import COARSE_CODES
from task1.experiments.adctoolbox_calibration import SwitchState
from task1.experiments.bridge_lms_comparison import Q32
from task1.experiments.residue_control_study import MapperTable
from task1.model.scheduler import GROUP_SLICES, POOL_SLICES
from task2.model.dither_word_gen import DitherWordGenerator

MAIN_BITS = len(MAIN_WEIGHTS)
SUB_BITS = len(SUB_WEIGHTS)
FEATURES_PER_SLICE = MAIN_BITS + SUB_BITS - 1
SUB_DITHER_WEIGHTS = np.array(SUB_WEIGHTS[:-1], dtype=np.uint8)


# =========================================================================
# 1. 函数组：Dither / decode / words
# =========================================================================
# -------------------------------------------------------------------------
# 同笔八个有序槽的逻辑注入掩码与整数扰动标签。
# 按六位字生成逻辑注入位图，字 0 对应零注入。
# 生成与 RTL 一致的采样序列，关闭时保持 PRBS 状态。
# -------------------------------------------------------------------------
@dataclass(frozen=True)
class Dither:
    """同笔八个有序槽的逻辑注入掩码与整数扰动标签。"""

    word: np.ndarray
    upper: np.ndarray
    lower_eighth: np.ndarray
    total_eighth: np.ndarray
    setting: np.ndarray
    sample_sub_high: np.ndarray


def decode(word: np.ndarray) -> Dither:
    """按六位字生成逻辑注入位图，字 0 对应零注入。"""
    word = np.asarray(word)
    if word.dtype.kind not in "iu" or np.any((word < 0) | (word >= 64)):
        raise ValueError("each dither word must be an integer in [0, 63]")
    word = word.astype(np.uint8, copy=False)
    upper = 4 * (word >> 5) + ((word >> 3) & 1)
    lower_integer = 2 * ((word >> 4) & 1)
    count = word & 7
    setting = (
        upper[..., None] + lower_integer[..., None] + (np.arange(GROUP_SLICES) < count[..., None])
    )
    # Task1 的物理位序为 8C、4C、2C、1C、dummy；p 的二进制位序相反。
    mask = ((setting[..., None] & SUB_DITHER_WEIGHTS) != 0) @ (1 << np.arange(4))
    lower_eighth = GROUP_SLICES * lower_integer + count
    return Dither(
        word,
        upper,
        lower_eighth,
        GROUP_SLICES * upper + lower_eighth,
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


# =========================================================================
# 2. 函数组：native_step_v / coarse_decision / active_features
# =========================================================================
# -------------------------------------------------------------------------
# 计算单位子电容切轨造成的名义输入折算步长。
# 独立 SADC 辅助 DAC 扣低路后判决，返回原码、最终命令和前级有效。
# 给出每片主阵列转换位与子阵列「转换位＋采样位」的有符号特征。
# -------------------------------------------------------------------------
def native_step_v(experiment: ExperimentConfig) -> float:
    """计算单位子电容切轨造成的名义输入折算步长。"""
    return 2 * experiment.full_scale_peak_v / (sum(MAIN_WEIGHTS) * (1 + sum(SUB_WEIGHTS)))


def coarse_decision(
    input_v: np.ndarray,
    dither: Dither,
    thresholds: np.ndarray,
    step_v: float,
    *,
    decision_v: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """独立 SADC 辅助 DAC 扣低路后判决，返回原码、最终命令和前级有效。

    ``decision_v`` 只替换比较器看到的电压，量程有效性仍按 ``input_v`` 判定。
    这个分离是必要的：比较器噪声加在 ``sadc_v`` 上会让粗码偶发跳档（这是
    物理效应，要保留），但同一个噪声不该让样本被误判成「输入超量程」
    （那是激励配置错误，要报出来）。两者混在一根线上就分不开了。
    """
    # [工程合同] SADC 的辅助注入为 -L，RDAC 命令扣除 U；由调用方提供模拟宏。
    # 当前位图映射及步长不是专利给出的工艺参数；有效性独立检查，不能靠 clip 隐藏下溢。
    sensed_v = input_v if decision_v is None else decision_v
    sadc_v = sensed_v - dither.lower_eighth * (step_v / GROUP_SLICES)
    coarse = np.searchsorted(thresholds, sadc_v, side="right") - 1
    command = coarse - dither.upper
    peak = thresholds[-1]
    valid = (
        np.isfinite(input_v)
        & (input_v >= -peak)
        & (input_v < peak)
        & (coarse >= 0)
        & (coarse < COARSE_CODES)
        & (command >= 0)
        & (command < COARSE_CODES)
    )
    return coarse, command, valid


def active_features(state: SwitchState, dither: Dither) -> np.ndarray:
    """给出每片主阵列转换位与子阵列「转换位＋采样位」的有符号特征。"""
    # [模型边界] 受控电荷源的残差贡献为 -T，重构的子特征因此为 conv+sample。
    # 两位同时为一时取 2，不能并成 1 或抵消为零；模拟实现须满足相同符号和幅度合同。
    main = unpack(state.main, MAIN_BITS).astype(np.int8)
    sub = unpack(state.sub, SUB_BITS)[..., :-1].astype(np.int8)
    sampled = unpack(dither.sample_sub_high, SUB_BITS)[..., :-1].astype(np.int8)
    return np.concatenate((main, sub + sampled), axis=-1)


# =========================================================================
# 3. 函数组：fit_features / weighted_sum / ChargePool
# =========================================================================
# -------------------------------------------------------------------------
# 按物理片号散布同笔有序特征，供片外 LMS 使用。
# 用物理片号读取每片权重并累加。
# 由固定制造电容生成的两节点闭式增益和底板权重。
# -------------------------------------------------------------------------
def fit_features(state: SwitchState, feature: np.ndarray) -> np.ndarray:
    """按物理片号散布同笔有序特征，供片外 LMS 使用。"""
    dense = np.zeros((len(state.plan), POOL_SLICES, FEATURES_PER_SLICE), dtype=np.int8)
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
        nominal_c = GROUP_SLICES * sum(MAIN_WEIGHTS) * experiment.unit_cap_f
        span_over_c = 2 * experiment.full_scale_peak_v / nominal_c
        beta = cap.bridge / (cap.bridge + cap.sub.sum(axis=1))
        weight = np.column_stack((cap.main, cap.sub[:, :-1] * beta[:, None])) * span_over_c
        return cls(cap.main.sum(axis=1) / nominal_c, weight)

    def affine(self, plan: np.ndarray, feature: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """给出 r = gain·(x−Vlow)−base 的增益和物理底板截距。"""
        return self.gain[plan].sum(axis=1), weighted_sum(plan, feature, self.weight)


# =========================================================================
# 4. 函数组：ConversionState / conversion_state
# =========================================================================
# -------------------------------------------------------------------------
# 前级命令、物理掩码、名义截距与同笔可观测特征。
# 按最终合法命令生成 Fig.11 或 Fig.21 开关及补偿。
# -------------------------------------------------------------------------
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
    # 使用旧 Fig.11 码本，显式加 Fig.21 转换回放差值一次，并把这笔注入量加回去一次。
    # 当前工程合同是负注入：RDAC 存 x−T（T = U·Δ + L），所以残差里含着 −T，
    # 数字侧必须加回 T 才能还原输入；写成减 T 就成了把注入算两遍。
    nominal_base = (
        thresholds[command]
        + peak
        + correction * (peak / Q32)
        + dither.total_eighth * (step_v / GROUP_SLICES)
    )
    return ConversionState(
        switches, dither, active_features(switches, dither), correction, nominal_base
    )
