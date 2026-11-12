# =============================================================================
# 文件名     : timing.py
# 模块名     : adi_model.control.timing
# 版本       : v1.0
# 功能       : 采样孔径抖动、DAC 极点带宽与片间时偏的纯函数与配置。
#
# 设计说明   :
#   * 三个量各有出处，不要混用：
#     - 采样孔径抖动：专利 US10505561B2 给了完整数值链（5 V 动态范围、
#       10 MHz、幅度 2.5 V、斜率 2.5·2π·10^7 = 157e6 V/s、1 ps 折 157 µV、
#       18b 的 LSB 是 19 µV，故 1 ps 折 8.3 LSB）。这条链可以逐位复算。
#     - DAC 带宽：讲稿 ISSCC2024 第 19-22 页反复写 "DAC BW set to 15 × f_Nyq"，
#       15× 是设计取值，不是拟合出来的。
#     - 残余时偏 0.6 ps：论文正文 "The estimated residual RMS timing mismatch
#       is ~0.6ps"。注意它是**残余**值（已经有匹配时钟方案之后），讲稿里的
#       10 ps 才是失配仿真的条件，两者不能互相替代。
#   * 本模块只做纯计算，不持有随机状态；需要逐片时偏时由调用方给种子和片数。
# -----------------------------------------------------------------------------
# 变更记录
# -----------------------------------------------------------------------------
# 初始版本，M5a。
# =============================================================================

"""采样孔径抖动、DAC 极点带宽与片间时偏的纯函数与配置。"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# 讲稿给的 DAC 带宽设计值，相对 Nyquist 的倍数。
DAC_BANDWIDTH_MULTIPLE_OF_NYQUIST = 15.0

# 论文给的残余 RMS 时偏，单位 s。是校准/匹配时钟之后的残值。
RESIDUAL_RMS_TIMING_MISMATCH_S = 0.6e-12

# 专利数值链里用到的条件，单位分别是 V、Hz。留作常量是为了让复算可追溯。
PATENT_CHAIN_PEAK_V = 2.5
PATENT_CHAIN_FREQ_HZ = 10e6
PATENT_CHAIN_JITTER_S = 1e-12
PATENT_CHAIN_DR_V = 5.0
PATENT_CHAIN_BITS = 18


def sine_slope(peak_v: float, freq_hz: float) -> float:
    """给出正弦在过零点处的最大斜率，单位 V/s。

    过零点是斜率最大的地方，所以孔径抖动造成的误差上限就在这里。

    Args:
        peak_v: 正弦幅度，单位 V。
        freq_hz: 频率，单位 Hz。

    Returns:
        最大斜率 ``2*pi*f*peak``，单位 V/s。
    """
    if peak_v < 0:
        raise ValueError("amplitude must not be negative")
    if freq_hz < 0:
        raise ValueError("frequency must not be negative")
    return float(2.0 * np.pi * freq_hz * peak_v)


def aperture_jitter(slope_v_per_s: float, rms_jitter_s: float) -> float:
    """给出孔径抖动折成的采样电压误差标准差，单位 V。

    一阶模型：误差 = 斜率 × 时间偏差。斜率取过零点的最大值，所以这是上限估计；
    对满幅正弦，全周期的 RMS 是它的 ``1/sqrt(2)``，本函数不替调用方做这个折算。

    Args:
        slope_v_per_s: 信号斜率，单位 V/s。
        rms_jitter_s: 抖动的 RMS 值，单位 s。

    Returns:
        电压误差的标准差，单位 V。
    """
    if rms_jitter_s < 0:
        raise ValueError("jitter must not be negative")
    return float(abs(slope_v_per_s) * rms_jitter_s)


def jitter_limited_snr(peak_v: float, freq_hz: float, rms_jitter_s: float) -> float:
    """给出仅由孔径抖动决定的 SNR 上限，单位 dB。

    满幅正弦的 RMS 是 ``peak/sqrt(2)``，抖动误差的 RMS 是
    ``2*pi*f*peak*jitter/sqrt(2)``，两者相除后幅度抵消，得
    ``1/(2*pi*f*jitter)``。幅度不出现是正确结果，不是笔误。

    Args:
        peak_v: 正弦幅度，单位 V。只用于校验，不进入结果。
        freq_hz: 输入频率，单位 Hz。
        rms_jitter_s: 抖动的 RMS 值，单位 s。

    Returns:
        SNR 上限，单位 dB；``freq_hz`` 或 ``rms_jitter_s`` 为 0 时返回 ``inf``。
    """
    if peak_v <= 0:
        raise ValueError("amplitude must be positive")
    if freq_hz < 0:
        raise ValueError("frequency must not be negative")
    if rms_jitter_s < 0:
        raise ValueError("jitter must not be negative")
    if freq_hz == 0 or rms_jitter_s == 0:
        return float("inf")
    return float(-20.0 * np.log10(2.0 * np.pi * freq_hz * rms_jitter_s))


def dac_pole_attenuation(freq_hz: float, pole_hz: float) -> float:
    """给出一阶极点对给定频率的幅度衰减（线性，不是 dB）。

    Args:
        freq_hz: 信号频率，单位 Hz。
        pole_hz: 极点频率，单位 Hz。0 表示无限带宽，返回 1。

    Returns:
        ``1/sqrt(1 + (f/pole)^2)``，取值 (0, 1]。
    """
    if freq_hz < 0:
        raise ValueError("frequency must not be negative")
    if pole_hz < 0:
        raise ValueError("pole frequency must not be negative")
    if pole_hz == 0:
        return 1.0
    return float(1.0 / np.sqrt(1.0 + (freq_hz / pole_hz) ** 2))


def pole_from_bandwidth_multiple(multiple: float, nyquist_hz: float) -> float:
    """把「带宽是 Nyquist 的多少倍」换成极点频率，单位 Hz。

    Args:
        multiple: 倍数，必须为正。
        nyquist_hz: Nyquist 频率，单位 Hz。

    Returns:
        极点频率，单位 Hz。
    """
    if multiple <= 0:
        raise ValueError("bandwidth multiple must be positive")
    if nyquist_hz <= 0:
        raise ValueError("Nyquist frequency must be positive")
    return float(multiple * nyquist_hz)


@dataclass(frozen=True)
class TimingConfig:
    """片间时偏的配置。默认取值与 M5 之前的确定性三值路径逐位一致。

    Attributes:
        mode: ``deterministic`` 保持旧的三值对称时偏（+skew/−skew/0），
            ``rms`` 按高斯抽样给出逐片时偏。默认 deterministic，因为
            task1/evidence/effects 的归档结果就是在这条路径上生成的，
            换默认值会让它不可复现。
        skew_s: deterministic 模式的时偏幅值，单位 s。
        rms_jitter_s: rms 模式的时偏标准差，单位 s。
        seed: rms 模式的抽样种子。
    """

    mode: str = "deterministic"
    skew_s: float = 5e-12
    rms_jitter_s: float = RESIDUAL_RMS_TIMING_MISMATCH_S
    seed: int = 0x7157_1A6E

    def __post_init__(self) -> None:
        """拒绝不认识的模式与负值，避免扫描时静默走错分支。"""
        if self.mode not in ("deterministic", "rms"):
            raise ValueError(f"unknown timing mode: {self.mode}")
        if self.skew_s < 0:
            raise ValueError("skew must not be negative")
        if self.rms_jitter_s < 0:
            raise ValueError("jitter must not be negative")

    def delays(self, slices: int) -> np.ndarray:
        """给出每片的时偏，单位 s，形状 ``(slices,)``。

        deterministic 模式精确复现旧写法 ``repeat((+skew, -skew, 0), (8, 8, 2))``：
        前 8 片正偏、中 8 片负偏、末 2 片为零。片数不是 18 时按比例切分，
        保证总片数对得上而不是靠 assert 挡住。

        Args:
            slices: 片数。

        Returns:
            时偏数组，单位 s。
        """
        if slices <= 0:
            raise ValueError("slice count must be positive")
        if self.mode == "deterministic":
            if slices < 3:
                raise ValueError("deterministic mode needs at least three slices")
            # 旧写法在 18 片上固定是 8 正 / 8 负 / 2 零，按 8:8:2 的比例缩放到任意片数。
            # 用 slices//2 会得到 9/9/0，与归档结果差一位，所以这里按原比例算。
            half = max(1, slices * 8 // 18)
            remainder = slices - 2 * half
            if remainder < 0:
                raise ValueError("slice count is too small for the deterministic pattern")
            return np.repeat((self.skew_s, -self.skew_s, 0.0), (half, half, remainder))
        generator = np.random.Generator(np.random.PCG64(self.seed))
        return generator.normal(0.0, self.rms_jitter_s, size=slices)
