# =============================================================================
# 文件名     : fig19_seven.py
# 模块名     : fig19_seven
# 版本       : v1.0
# 功能       : US10505561B2 Fig. 19 七片电荷域工程等效模型。
#
# 设计说明   :
#   * 六位拆字和七片相邻档分配来自专利。负等效端口、均匀粗码本、理想
#   * ADC2 窗口、二十位输出网格和第 8 档是本实验的约定。逐前缀守卫按旧
#   * 工程事件次序另算，直接设定最终码用于 Fig. 19 机制实验。
# -----------------------------------------------------------------------------
# 变更记录
# -----------------------------------------------------------------------------
# 初始版本，暂无变更记录。
# =============================================================================

"""US10505561B2 Fig. 19 七片电荷域工程等效模型。

六位拆字和七片相邻档分配来自专利。负等效端口、均匀粗码本、理想
ADC2 窗口、二十位输出网格和第 8 档是本实验的约定。逐前缀守卫按旧
工程事件次序另算，直接设定最终码用于 Fig. 19 机制实验。
"""

from dataclasses import dataclass
from fractions import Fraction
from math import lcm

import numpy as np
from numpy.typing import ArrayLike, NDArray

from adi_model.codes import COARSE_CODES, OUTPUT_CODES

INPUT_MIN_V = -5.0
INPUT_MAX_V = 5.0
FULL_SCALE_V = INPUT_MAX_V - INPUT_MIN_V
ADC2_CODES = 1 << 12
FIG19_SLICES = 7
COARSE_LSB_V = FULL_SCALE_V / COARSE_CODES
OUTPUT_LSB_V = FULL_SCALE_V / OUTPUT_CODES
OUTPUT_PER_COARSE = OUTPUT_CODES // COARSE_CODES


# =========================================================================
# 1. 函数组：ModelConfig / DitherTerms / Conversion
# =========================================================================
# -------------------------------------------------------------------------
# 工程等效参数。
# 拆字结果，末轴为七片，分子字段的单位为粗码 LSB 的七分之一。
# 每笔原始观测与有效性，所有电压折算到 ADC 输入端。
# -------------------------------------------------------------------------
@dataclass(frozen=True)
class ModelConfig:
    """工程等效参数。

    Attributes:
        adc2_half_range_v: ADC2 输入折算到前端的半量程，单位 V。
        max_slice_setting_lsb: 单片支持的最大整数档位，单位粗码 LSB。
            设为 7 可测试第 8 档所需的额外能力。
    """

    adc2_half_range_v: float = 0.100
    max_slice_setting_lsb: int = 8

    def __post_init__(self) -> None:
        """校验此实验所支持的窗口和档位选项。"""
        if not 0.001 <= self.adc2_half_range_v <= 5.0:
            raise ValueError("ADC2 half range must be 1 mV to 5 V, input referred")
        if self.max_slice_setting_lsb not in (7, 8):
            raise ValueError("maximum slice setting must be 7 or 8")
        # 导出码使用 int64 有理算术，要求窗口的十进制精度能在该域精确表达。
        _export_coefficients(self.adc2_half_range_v)

    @property
    def adc2_lsb_v(self) -> float:
        """给出折算到前端的 ADC2 量化步长，单位 V。"""
        return 2 * self.adc2_half_range_v / ADC2_CODES


@dataclass(frozen=True)
class DitherTerms:
    """拆字结果，末轴为七片，分子字段的单位为粗码 LSB 的七分之一。"""

    upper_lsb: NDArray
    lower_numerator: NDArray
    total_numerator: NDArray
    slice_setting_lsb: NDArray

    @property
    def lower_v(self) -> NDArray:
        """给出 sADC 采样所用的低字，单位 V。"""
        return self.lower_numerator * (COARSE_LSB_V / FIG19_SLICES)

    @property
    def total_v(self) -> NDArray:
        """给出 RDAC 七片的等效总扰动，单位 V。"""
        return self.total_numerator * (COARSE_LSB_V / FIG19_SLICES)


@dataclass(frozen=True)
class Conversion:
    """每笔原始观测与有效性，所有电压折算到 ADC 输入端。

    slice_sample_v 和 prefix_command_code 的末轴分别为物理片序和事件序。
    adc2_code 在 ADC2 超窗时为 -1。output_code 和 output_v 在直接路径
    拒绝时分别为 -1 和 NaN。各 validity 字段记录各自节点，整笔有效
    性以 accepted_direct 或 accepted_prefix_guard 为准。
    """

    sadc_sample_v: NDArray
    slice_sample_v: NDArray
    coarse_code: NDArray
    command_code: NDArray
    prefix_command_code: NDArray
    residue_v: NDArray
    adc2_code: NDArray
    residue_quantized_v: NDArray
    reconstructed_v: NDArray
    output_code: NDArray
    output_v: NDArray
    input_valid: NDArray
    setting_valid: NDArray
    sadc_valid: NDArray
    rdac_valid: NDArray
    command_valid: NDArray
    prefix_valid: NDArray
    adc2_valid: NDArray
    output_valid: NDArray
    accepted_direct: NDArray
    accepted_prefix_guard: NDArray


# =========================================================================
# 2. 函数组：CodeDensity / decode_words / _export_coefficients
# =========================================================================
# -------------------------------------------------------------------------
# 输出各码的概率等效输入宽度与分阶段拒绝宽度，单位 V。
# 拆解 D6 至 D1，D6 对应 bit 5。
# -------------------------------------------------------------------------
@dataclass(frozen=True)
class CodeDensity:
    """输出各码的概率等效输入宽度与分阶段拒绝宽度，单位 V。"""

    width_v: NDArray
    pre_adc2_rejected_v: float
    adc2_rejected_v: float
    output_rejected_v: float


def decode_words(word: ArrayLike) -> DitherTerms:
    """拆解 D6 至 D1，D6 对应 bit 5。

    Args:
        word: 整数六位字，可以是单笔或任意形状的数组。

    Returns:
        每笔的高字、低字分子、总扰动分子和七片的整数采样档位。
        m=7 采用七片全部升档的工程约定。

    Raises:
        ValueError: 字不是 0 至 63 之间的整数。
    """
    word = np.asarray(word)
    if word.dtype.kind not in "iu" or np.any((word < 0) | (word >= 64)):
        raise ValueError("dither words must be integers in [0, 63]")
    word = word.astype(np.int64, copy=False)
    upper = ((word >> 5) & 1) * 4 + ((word >> 3) & 1)
    lower_integer = ((word >> 4) & 1) * 2
    fractional_count = word & 7
    base_setting = upper + lower_integer
    slice_setting = base_setting[..., None] + (
        np.arange(FIG19_SLICES) < fractional_count[..., None]
    )
    lower_numerator = lower_integer * FIG19_SLICES + fractional_count
    return DitherTerms(
        upper,
        lower_numerator,
        upper * FIG19_SLICES + lower_numerator,
        slice_setting,
    )


def _export_coefficients(half_range_v: float) -> tuple[int, int, int, int]:
    # 取精确十进制窗口和有理 LSB，消除 ADC2 中点恰落输出码边界时的浮点歧义。
    half_in_lsb20 = Fraction(str(half_range_v)) / Fraction(FULL_SCALE_V) * OUTPUT_CODES
    fine_step = 2 * half_in_lsb20 / ADC2_CODES
    fine_offset = -half_in_lsb20 + fine_step / 2
    total_step = Fraction(OUTPUT_PER_COARSE, FIG19_SLICES)
    denominator = lcm(fine_step.denominator, fine_offset.denominator, total_step.denominator)
    fine_num = int(fine_step * denominator)
    offset_num = int(fine_offset * denominator)
    total_num = int(total_step * denominator)
    max_numerator = (ADC2_CODES - 1) * fine_num + abs(offset_num) + 56 * total_num
    if max_numerator > np.iinfo(np.int64).max:
        raise ValueError("ADC2 half range has too many decimal places for exact code export")
    return fine_num, offset_num, total_num, denominator


DEFAULT_CONFIG = ModelConfig()


# =========================================================================
# 3. 函数组：_output_codes / _inside / _prefix_commands
# =========================================================================
# -------------------------------------------------------------------------
def _output_codes(
    command_code: NDArray, fine_code: NDArray, terms: DitherTerms, config: ModelConfig
) -> NDArray:
    fine_num, offset_num, total_num, denominator = _export_coefficients(config.adc2_half_range_v)
    within = fine_code * fine_num + offset_num + terms.total_numerator * total_num
    return command_code * OUTPUT_PER_COARSE + within // denominator


def _inside(value: NDArray, low: float, high: float) -> NDArray:
    return (value >= low) & (value < high)


def _prefix_commands(coarse_code: NDArray, upper_lsb: NDArray) -> NDArray:
    # 3 位 Flash 先发布，接着依次发布 6 位 SAR。未确定低位按零填充。
    shifts = np.arange(6, -1, -1)
    prefix = (coarse_code[..., None] >> shifts) << shifts
    return prefix - upper_lsb[..., None]


# =========================================================================
# 4. 函数组：convert / code_density
# =========================================================================
# -------------------------------------------------------------------------
# 按七片等电容的电荷守恒计算一笔或一组转换。
# 对给定输入区间和等权扰动字解析积分，输出全二十位逐码宽度。
# -------------------------------------------------------------------------
def convert(
    input_v: ArrayLike, word: ArrayLike, config: ModelConfig = DEFAULT_CONFIG
) -> Conversion:
    """按七片等电容的电荷守恒计算一笔或一组转换。

    Args:
        input_v: 差分输入，单位 V，合法范围 [-5, 5)。
        word: 六位整数扰动字，与 input_v 形状可广播。0 表示关闭。
        config: ADC2 窗口和切片采样档位的工程配置。

    Returns:
        每笔路径节点、量程标志及两类有效性判据。输出码为 unsigned，
        等效电压对应该二十位输出码的中点。
    """
    input_v, word = np.broadcast_arrays(np.asarray(input_v, dtype=float), np.asarray(word))
    terms = decode_words(word)
    input_valid = _inside(input_v, INPUT_MIN_V, INPUT_MAX_V)
    setting_valid = np.all(terms.slice_setting_lsb <= config.max_slice_setting_lsb, axis=-1)

    sadc = input_v - terms.lower_v
    sampled = input_v[..., None] - terms.slice_setting_lsb * COARSE_LSB_V
    sadc_valid = _inside(sadc, INPUT_MIN_V, INPUT_MAX_V)
    rdac_valid = np.all(_inside(sampled, INPUT_MIN_V, INPUT_MAX_V), axis=-1)
    coarse_position = np.where(sadc_valid, (sadc - INPUT_MIN_V) / COARSE_LSB_V, 0.0)
    # 最接近合法上沿的浮点数可在归一化时舍入到 512。
    coarse_code = np.where(sadc_valid, np.minimum(coarse_position, COARSE_CODES - 1), -1)
    coarse_code = np.floor(coarse_code).astype(np.int64)
    command = coarse_code - terms.upper_lsb
    command_valid = _inside(command, 0, COARSE_CODES)
    prefix = _prefix_commands(coarse_code, terms.upper_lsb)
    prefix_valid = np.all(_inside(prefix, 0, COARSE_CODES), axis=-1)

    dac_v = INPUT_MIN_V + command * COARSE_LSB_V
    # 每片减去同一个名义回放量。短接七片等值电容后，残差等于电荷的平均。
    residue = np.mean(sampled - dac_v[..., None], axis=-1)
    half = config.adc2_half_range_v
    adc2_valid = _inside(residue, -half, half)
    fine_position = np.where(adc2_valid, (residue + half) / config.adc2_lsb_v, 0.0)
    fine_code = np.floor(np.minimum(fine_position, ADC2_CODES - 1)).astype(np.int64)
    fine_v = np.where(adc2_valid, -half + (fine_code + 0.5) * config.adc2_lsb_v, np.nan)

    # 该式使用最终 RDAC 码，数字端对总扰动只补偿一次。
    reconstructed = dac_v + fine_v + terms.total_v
    raw_output = _output_codes(command, fine_code, terms, config)
    output_valid = adc2_valid & _inside(raw_output, 0, OUTPUT_CODES)
    accepted = input_valid & setting_valid & sadc_valid & rdac_valid & command_valid & output_valid
    output_code = np.where(accepted, raw_output, -1)
    output_v = np.where(accepted, INPUT_MIN_V + (output_code + 0.5) * OUTPUT_LSB_V, np.nan)
    return Conversion(
        sadc,
        sampled,
        coarse_code,
        command,
        prefix,
        residue,
        np.where(adc2_valid, fine_code, -1),
        fine_v,
        reconstructed,
        output_code,
        output_v,
        input_valid,
        setting_valid,
        sadc_valid,
        rdac_valid,
        command_valid,
        prefix_valid,
        adc2_valid,
        output_valid,
        accepted,
        accepted & prefix_valid,
    )


def code_density(
    input_low_v: float,
    input_high_v: float,
    words: ArrayLike,
    config: ModelConfig = DEFAULT_CONFIG,
    *,
    require_prefix_valid: bool = False,
) -> CodeDensity:
    """对给定输入区间和等权扰动字解析积分，输出全二十位逐码宽度。

    Args:
        input_low_v: 均匀输入区间的下沿，单位 V。
        input_high_v: 输入区间的上沿，单位 V。
        words: 非空的一维六位字序列，出现多次的字有相应权重。
        config: ADC2 和采样档位的工程配置。
        require_prefix_valid: 同时拒绝逐前缀越界，供独立守卫对比。

    Returns:
        所有二十位码的宽度和各阶段拒绝宽度，单位 V。

    Raises:
        ValueError: 输入超出标称量程或扰动字序列为空、形状错误。
    """
    if not INPUT_MIN_V <= input_low_v < input_high_v <= INPUT_MAX_V:
        raise ValueError("density interval must be inside [-5, 5] V and have positive width")
    words = np.asarray(words)
    if words.ndim != 1 or not words.size:
        raise ValueError("density requires a nonempty one-dimensional word sequence")
    decode_words(words)

    # sADC 与 RDAC 的理想名义量相消后，合法粗码区间内的残差始终在 [0, Δ)。
    half = config.adc2_half_range_v
    adc2_edges = -half + np.arange(ADC2_CODES + 1) * config.adc2_lsb_v
    fine_codes = np.flatnonzero((adc2_edges[:-1] < COARSE_LSB_V) & (adc2_edges[1:] > 0))
    fine_low = adc2_edges[fine_codes]
    fine_high = adc2_edges[fine_codes + 1]
    coarse = np.arange(COARSE_CODES)
    coarse_low = INPUT_MIN_V + coarse * COARSE_LSB_V
    total_width = input_high_v - input_low_v
    width = np.zeros(OUTPUT_CODES)
    rejected = np.zeros(3)
    for word in words:
        terms = decode_words(word)
        k = coarse - terms.upper_lsb
        valid = k >= 0
        if require_prefix_valid:
            valid &= np.all(_prefix_commands(coarse, terms.upper_lsb) >= 0, axis=-1)

        valid_sample_low = INPUT_MIN_V + max(
            float(terms.lower_v), np.max(terms.slice_setting_lsb) * COARSE_LSB_V
        )
        coarse_sadc_low = coarse_low + terms.lower_v
        left = np.maximum(coarse_sadc_low, max(input_low_v, valid_sample_low))
        right = np.minimum(coarse_sadc_low + COARSE_LSB_V, input_high_v)
        use = (right > left) & valid
        if np.max(terms.slice_setting_lsb) > config.max_slice_setting_lsb:
            use[:] = False

        valid_width = np.sum(right[use] - left[use])
        rejected[0] += total_width - valid_width
        if not np.any(use):
            continue

        origin = coarse_sadc_low[use, None]
        a = np.maximum(left[use, None], origin + fine_low)
        b = np.minimum(right[use, None], origin + fine_high)
        mass = np.maximum(b - a, 0)
        codes = _output_codes(k[use, None], fine_codes, terms, config)
        accepted = _inside(codes, 0, OUTPUT_CODES) & (mass > 0)
        accepted_mass = mass[accepted]
        width += np.bincount(codes[accepted], weights=accepted_mass, minlength=OUTPUT_CODES)
        fine_width = mass.sum()
        rejected[1] += max(0.0, valid_width - fine_width)
        rejected[2] += max(0.0, fine_width - accepted_mass.sum())
    width /= words.size
    rejected /= words.size
    return CodeDensity(width, *rejected)
