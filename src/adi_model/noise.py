# =============================================================================
# 文件名     : noise.py
# 模块名     : adi_model.noise
# 版本       : v1.0
# 功能       : 采样网络的热噪声、参考/比较器/ADC2 的分配噪声，以及 1/f 整形内核
#
# 设计说明   :
#   * 所有噪声源**默认关闭**：NoiseConfig() 的每个 sigma 都是 0，enabled 为 False。
#     这样 M3 把 NoiseConfig 挂进 Capacitors 时，既有实验的数值一位都不会变。
#   * 每个源用 SeedSequence([noise_seed, tag]) 派生独立流，不共用 Generator。
#     这是为了满足「正交性」：同时开三个源时，第一个源的抽样必须与只开它时
#     逐位相同。若共用一条流，加一个源就会让后面的源全部错位，
#     敏感度曲线上的差异就分不清是「新源的影响」还是「抽样错位」。
#   * 归一化用**解析**增益，不用实测 RMS。理由：按实测 RMS 归一会把每次实现的
#     幅度涨落抹平，而 DR/SNR 的测量恰恰依赖这个涨落；那样做出来的
#     「σ 回收误差」永远是 0，等于自证。
#   * 这里的 sigma 是**输入等效分配值**，不是电路设计值。M7 的噪声预算按
#     DR 94.2 dB 反推得到，每个数都标 S5_假设，可被证伪。
#   * 白噪 ≠ 电路噪声谱。本模块只给「若噪声谱是白的会怎样」，不对任何
#     具体电路的噪声谱做断言。
# -----------------------------------------------------------------------------
# 变更记录
# -----------------------------------------------------------------------------
# 初始版本。
# =============================================================================

"""热噪声、分配噪声与 1/f 整形内核。"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# 玻尔兹曼常数，单位 J/K。2019 年 SI 修订后为精确值。
BOLTZMANN_J_PER_K = 1.380649e-23

# 各噪声源的分流派生标签。必须是互不相同的正整数：
# 两个源共用标签就会得到完全相同的抽样序列，看上去像「共模噪声」，
# 实际是把两处独立噪声错误地相关起来。
TAG_REFERENCE = 1
TAG_COMPARATOR = 2
TAG_ADC2 = 3
TAG_RA_OFFSET = 4
TAG_FLICKER = 5
TAG_SAMPLING = 6

_RELATIVE_SIGMA_TOLERANCE = 0.01


@dataclass(frozen=True)
class NoiseConfig:
    """噪声源配置。全字段默认关闭，关闭时任何采样函数都不消耗随机数。

    Attributes:
        enabled: 总开关。False 时所有采样函数返回全零。
        include_thermal: 是否计入 kT/C。单独关掉它才能隔离其它源做敏感度，
            否则「只开比较器」这种实验根本做不出来——kT/C 会一直在场。
        temperature_k: 绝对温度，单位 K。
        sampling_cap_f: 单侧采样电容，单位 F。ISSCC2024 讲稿第 23 页给 20.5 pF。
        comparator_sigma_v: 比较器输入等效噪声标准差，单位 V。
        reference_sigma_v: 参考路径（RA + REF_IN + 预充残差）输入等效噪声标准差，单位 V。
        adc2_sigma_v: 二级量化与额外抖动的输入等效噪声标准差，单位 V。
        ra_offset_v: 残差放大器常数失调，单位 V，**残差域**（ADC2 输入）。
            常数项在 FFT 去直流后不进 DR/SNR，只吃 ADC2 余量，故不是噪声源。
        flicker_corner_hz: 1/f 拐点，单位 Hz。0 表示不加 1/f 分量。
        noise_seed: 派生种子。必须非零，否则 SeedSequence 退化。
    """

    enabled: bool = False
    include_thermal: bool = True
    temperature_k: float = 300.0
    sampling_cap_f: float = 20.5e-12
    comparator_sigma_v: float = 0.0
    reference_sigma_v: float = 0.0
    adc2_sigma_v: float = 0.0
    ra_offset_v: float = 0.0
    flicker_corner_hz: float = 0.0
    noise_seed: int = 0x5EED_0A17

    def __post_init__(self) -> None:
        """拒绝物理上无意义或会让派生流退化的取值。"""
        if self.temperature_k <= 0:
            raise ValueError("temperature must be positive")
        if self.sampling_cap_f <= 0:
            raise ValueError("sampling capacitance must be positive")
        if self.noise_seed == 0:
            raise ValueError("noise seed must be nonzero")
        for name in ("comparator_sigma_v", "reference_sigma_v", "adc2_sigma_v"):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must not be negative")
        if self.flicker_corner_hz < 0:
            raise ValueError("flicker corner must not be negative")

    @property
    def thermal_sigma_v(self) -> float:
        """单侧采样电容上的 kT/C 噪声标准差，单位 V。

        差分输入取两侧独立噪声之差，标准差要再乘 √2；本属性只给单侧。
        ``include_thermal=False`` 时返回 0，用于隔离其它噪声源。
        """
        if not self.include_thermal:
            return 0.0
        return thermal_sigma(self.sampling_cap_f, self.temperature_k)


def thermal_sigma(cap_f: float, temperature_k: float) -> float:
    """给出 kT/C 噪声的标准差，单位 V。

    Args:
        cap_f: 单个采样电容，单位 F。
        temperature_k: 绝对温度，单位 K。

    Returns:
        单侧噪声标准差 ``sqrt(kT/C)``，单位 V。差分使用时乘 √2。
    """
    if cap_f <= 0:
        raise ValueError("capacitance must be positive")
    if temperature_k <= 0:
        raise ValueError("temperature must be positive")
    return float(np.sqrt(BOLTZMANN_J_PER_K * temperature_k / cap_f))


def _generator(cfg: NoiseConfig, tag: int, stream: int = 0) -> np.random.Generator:
    """按 (noise_seed, tag, stream) 派生一条独立随机流。

    不共用 Generator 是正交性的前提：SeedSequence 的派生只依赖
    (seed, tag, stream)，与已经取过几个源无关，因此新增一个源不会挪动
    其它源的抽样位置。

    ``stream`` 用来区分同一份配置下的不同激励序列（训练段、评估段、
    静态边缘）。同一 stream 重复调用必须拿到同一份噪声——off/on 与
    shared/old_off 这几组对照就是把同一批样本重跑一遍，若每次调用都
    重新抽噪声，对照差异里就混进了噪声本身的差异。``stream=0`` 时
    entropy 仍写作两元，保证加这个参数对既有抽样逐位无影响。
    """
    if tag <= 0:
        raise ValueError("noise tag must be a positive integer")
    if stream < 0:
        raise ValueError("noise stream must not be negative")
    entropy = [cfg.noise_seed, tag] if stream == 0 else [cfg.noise_seed, tag, stream]
    return np.random.Generator(np.random.PCG64(np.random.SeedSequence(entropy)))


def white(
    cfg: NoiseConfig, tag: int, sigma_v: float, count: int, stream: int = 0
) -> np.ndarray:
    """给出 ``count`` 个零均值高斯样本。

    Args:
        cfg: 噪声配置，只用到 enabled 与 noise_seed。
        tag: 流派生标签，取本模块的 TAG_* 常量。
        sigma_v: 标准差，单位 V。0 表示不加这个源。
        count: 样本数。
        stream: 序列编号，见 ``_generator``。

    Returns:
        形状为 ``(count,)`` 的 float64 数组；关闭或 sigma 为 0 时全零。
    """
    if count < 0:
        raise ValueError("count must not be negative")
    if sigma_v < 0:
        raise ValueError("sigma must not be negative")
    if not cfg.enabled or sigma_v == 0.0 or count == 0:
        return np.zeros(count, dtype=np.float64)
    return _generator(cfg, tag, stream).standard_normal(count) * sigma_v


def flicker(
    cfg: NoiseConfig, tag: int, sigma_v: float, count: int, fs_hz: float, stream: int = 0
) -> np.ndarray:
    """给出功率谱按 1/f 整形、总标准差为 ``sigma_v`` 的样本。

    整形在频域做：把白噪的频谱乘 ``1/sqrt(f)``，再按**解析**增益归一化。
    解析归一化的推导（numpy 的 FFT 不归一化，正逆变换互为逆运算）：

    ``x' = irfft(rfft(x) * w)``，由 Parseval，
    ``E[sum(x'^2)] = sigma^2 * (|w_0|^2 + |w_{N/2}|^2 + 2*sum_{k=1}^{N/2-1}|w_k|^2)``。
    于是把滤波器整体乘一个常数 ``1/sqrt(该括号值 / N)`` 即可让方差回到 ``sigma^2``。

    不去按实测 RMS 归一：那样会把每次实现的幅度涨落抹平，DR/SNR 测量依赖
    这个涨落，抹平后「σ 回收误差」恒为 0，等于自证。

    **单次实现的方差涨落很大，这是 1/f 的性质而不是本实现的缺陷。**
    功率几乎全落在最低几个频点，有效自由度只有
    ``(sum 1/k)^2 / sum 1/k^2`` 量级（2^20 样本约 115），故单次实现的
    标准差本身就有约 6.6% 的涨落。要验归一化是否正确，必须用集成平均，
    或者直接用 sigma 的线性标度做确定性检查。

    Args:
        cfg: 噪声配置。
        tag: 流派生标签。
        sigma_v: 目标总标准差，单位 V。
        count: 样本数，至少 4。
        fs_hz: 采样率，单位 Hz，用于确定频率栅格。
        stream: 序列编号，见 ``_generator``。

    Returns:
        形状为 ``(count,)`` 的 float64 数组；关闭、sigma 为 0、
        ``flicker_corner_hz`` 为 0 或样本过少时全零。
    """
    if count < 0:
        raise ValueError("count must not be negative")
    if sigma_v < 0:
        raise ValueError("sigma must not be negative")
    if cfg.flicker_corner_hz <= 0 or fs_hz <= 0:
        return np.zeros(count, dtype=np.float64)
    if not cfg.enabled or sigma_v == 0.0 or count < 4:
        return np.zeros(count, dtype=np.float64)

    raw = _generator(cfg, tag, stream).standard_normal(count)
    spectrum = np.fft.rfft(raw)
    freqs = np.fft.rfftfreq(count, d=1.0 / fs_hz)
    weight = np.zeros_like(freqs)
    # 直流不参与 1/f：f→0 时 1/sqrt(f) 发散，且直流分量会被 FFT 去直流抹掉。
    nonzero = freqs > 0
    weight[nonzero] = 1.0 / np.sqrt(freqs[nonzero])

    # 解析增益：直流与 Nyquist 各计一次，其余 bin 共轭成对计两次。
    gain_sq = float(weight[0] ** 2 + weight[-1] ** 2 + 2.0 * np.sum(weight[1:-1] ** 2)) / count
    if gain_sq <= 0.0:
        return np.zeros(count, dtype=np.float64)
    shaped = np.fft.irfft(spectrum * weight, n=count) / np.sqrt(gain_sq)
    shaped -= shaped.mean()
    return shaped * sigma_v


def combine(*parts: np.ndarray) -> np.ndarray:
    """把若干同长度的噪声分量相加。

    用求和而不是逐点重抽样，是为了让各分量的独立性在调用方可见：
    相加后的总方差约等于各分量方差之和（独立时严格相等）。
    """
    if not parts:
        raise ValueError("need at least one component")
    total = np.array(parts[0], dtype=np.float64, copy=True)
    for part in parts[1:]:
        if part.shape != total.shape:
            raise ValueError("all components must share one shape")
        total += part
    return total


@dataclass(frozen=True)
class FrontEndNoise:
    """一笔采样的四个噪声分量。

    **口径**：``sampling_v`` / ``comparator_v`` / ``conversion_v`` 都是
    **输入等效**电压（单位 V）；``offset_v`` 是**残差域**常数（单位 V）。

    分开三个数组而不是给一个总噪声，是因为它们注在三个不同节点上：

    * ``sampling_v`` 加在采样值上，粗码判决与残差都要看到它——采样网络
      （kT/C + 参考路径）的噪声就是这样进两处的；
    * ``comparator_v`` 只加到 SADC 的判决输入上，**不进残差**。若把它也加进
      残差，就会被后级重构吸收掉，等于凭空消掉一个噪声源；
    * ``conversion_v`` 只加到残差上，不参与判决。它是二级量化与额外抖动，
      在判决时还不存在。
    """

    sampling_v: np.ndarray
    comparator_v: np.ndarray
    conversion_v: np.ndarray
    offset_v: float

    @property
    def is_quiet(self) -> bool:
        """四个分量是否全为零。全零时调用方可完全跳过噪声路径。"""
        return (
            not np.any(self.sampling_v)
            and not np.any(self.comparator_v)
            and not np.any(self.conversion_v)
            and self.offset_v == 0.0
        )


def front_end_noise(cfg: NoiseConfig, count: int, stream: int = 0) -> FrontEndNoise:
    """按节点顺序给出一笔采样的四个噪声分量。

    kT/C 的差分口径：每侧电容各自有 ``sqrt(kT/C)`` 的噪声，差分取两侧之差，
    标准差为 ``sqrt(2) * sqrt(kT/C)``。20.5 pF / 300 K 时差分为 20.10 µV。

    Args:
        cfg: 噪声配置。
        count: 样本数。
        stream: 序列编号。同一批样本重跑不同阵列配置时必须传同一个
            stream，否则 off/on 对照会把噪声差异算成 dither 差异。

    Returns:
        四个分量；``cfg.enabled`` 为 False 时全部为零。
    """
    differential_thermal = float(np.sqrt(2.0)) * cfg.thermal_sigma_v
    return FrontEndNoise(
        sampling_v=combine(
            white(cfg, TAG_SAMPLING, differential_thermal, count, stream),
            white(cfg, TAG_REFERENCE, cfg.reference_sigma_v, count, stream),
        ),
        comparator_v=white(cfg, TAG_COMPARATOR, cfg.comparator_sigma_v, count, stream),
        conversion_v=white(cfg, TAG_ADC2, cfg.adc2_sigma_v, count, stream),
        offset_v=cfg.ra_offset_v if cfg.enabled else 0.0,
    )
