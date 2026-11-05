"""样本域斩波辅助 AZ（CAAZ）研究入口，默认关闭。

信号链顺序为：整个 ADC 输入按 raw 样本交替极性 -> RA 输出误差注入 ->
RA 钳位/ADC2 量化 -> 每个 raw 完整位权重构 -> 按归属配对读出。不可先
平均模拟真值再量化，否则会隐藏逐 raw 的失配、溢出和量化影响。

C32.1 Figs.5-6 披露偶数更新 AZ、奇数保持、偶减奇的机制。若同一份 AZ
误差 a_k [RA 输出 V] 被两次等增益传递，pair 差分中的共同项相消；保持
rho 和奇次传递 r 不匹配时，线性 RA 输出等效残留为 (1-rho*r)*a_k/2。
转换增益、非线性、裁剪和码量化仍可破坏输入等效抵消。

raw 40 MS/s -> 非重叠 pair 20 MS/s 是本项目明确选择；文献 6 MS/s
标题未唯一说明 raw/effective 口径。AZ 幅度、保持和传递差异均为假设，
没有用测得 4.3 dB 反推标量。本模型与 ra.py 的旧 AZ 噪声倍率分开，配对
模式拒绝将两者叠加。没有 FGA/CP-FIA 晶体管、连续斩波或 1/f 电路传递。
来源：Wang 等，VLSI 2025 C32.1，DOI 10.23919/VLSITechnologyandCir65189.2025.11075015。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ._arrays import readonly
from .config import Config, ConfigError
from .fixed_point import CodeStream
from .pipeline import run_pipeline
from .sim import SimResult


@dataclass(frozen=True)
class ChoppingAZParameters:
    """显式研究控制；所有误差幅度均在 RA 输出节点定义。

    Args:
        mode: off 保留普通 raw 输出；baseline 无新增 AZ 噪声、只做配对平均；
            conventional_az 每次 raw 独立采 AZ 噪声并做配对平均；
            chopping_assisted 对完整 ADC 输入交替极性，偶数刷新、奇数保持
            同一份 AZ 误差，再对重构码偶减奇。均不从模拟输入真值估计误差。
        az_noise_rms_v: 新增 AZ 采样误差的 RA 输出 RMS [V]；独立假设源，
            不是外部输入等效噪声，也不是从论文 4.3 dB 测量拟合的系数。
        post_az_offset_v: AZ 后的常量残余 [RA 输出 V]，不是失调消除电路模型。
        odd_hold_retention: 奇次保持偶次误差的比例 rho，范围 [0,1]；仅 CAAZ。
        odd_transfer_ratio: 奇/偶误差传递比 r，无量纲；仅 CAAZ，校验仅要求有限。

    Side effects:
        参数不可变；显式 validate 在生成数据前拒绝非法或无效非默认控制。
    """

    mode: str = "off"
    az_noise_rms_v: float = 0.0
    post_az_offset_v: float = 0.0
    odd_hold_retention: float = 1.0
    odd_transfer_ratio: float = 1.0

    def validate(self) -> None:
        """在生成样本前拒绝未知模式、非有限值和不生效的非默认参数。

        Returns:
            None；成功不代表物理参数已由器件测量确认。

        Raises:
            ConfigError: 模式未知，RMS 为负，保持比例越界，或关闭/不适用模式
                下仍请求新增 AZ 噪声、保持/传递控制或残余误差。

        Side effects:
            不修改参数、不消费任何随机流。
        """
        if self.mode not in ("off", "baseline", "conventional_az", "chopping_assisted"):
            raise ConfigError("unknown sampled-AZ research mode")
        values = [
            self.az_noise_rms_v,
            self.post_az_offset_v,
            self.odd_hold_retention,
            self.odd_transfer_ratio,
        ]
        if not np.all(np.isfinite(values)) or self.az_noise_rms_v < 0:
            raise ConfigError("AZ amplitudes must be finite and noise RMS nonnegative")
        if not 0 <= self.odd_hold_retention <= 1:
            raise ConfigError("AZ hold retention must lie in [0, 1]")
        if self.mode in ("off", "baseline") and self.az_noise_rms_v != 0:
            raise ConfigError("off/baseline modes require zero added AZ sample noise")
        if self.mode != "chopping_assisted" and self.odd_hold_retention != 1:
            raise ConfigError("hold retention only applies to chopping-assisted AZ")
        if self.mode != "chopping_assisted" and self.odd_transfer_ratio != 1:
            raise ConfigError("odd transfer ratio only applies to chopping-assisted AZ")
        if self.mode == "off" and self.post_az_offset_v != 0:
            raise ConfigError("off mode cannot silently ignore requested AZ perturbations")


@dataclass(frozen=True)
class ChoppingAZResult:
    """同时保留 N 个原始结果和独立的 raw/pair 输出视图。

    Attributes:
        raw: N 次完整 ADC 的 SimResult，保留 RA/ADC2 模拟溢出标志。
        raw_words: 每次 raw 冻结位权重构后的 CodeStream，配对算术之前。
        output_code: offset-binary 整数码；off 长 N，其余长 M=N/2。
        output_v: 对应输出码的 bin 中心 [外部输入等效 V]。
        prequantized_output_v: raw bin 中心的精确平均/半差 [输入等效 V]，
            用于分离最终配对再量化误差，不是未量化模拟真值。
        output_time_s: off 为 raw 孔径时刻，pair 为两次孔径中点 [s]。
        source_sample_ids: off 为 (N,1)，pair 为 (M,2) 的实际 raw 样本 ID。
        invalid: 输出内任一 raw 模拟溢出/数字裁剪的 OR，保留全部输出位置。
        raw_sample_rate_hz: 原始转换速率 [Hz]。
        output_sample_rate_hz: off 为 raw 速率，其余为 raw/2 [Hz]。
        chop_sign: (N,) 每次 raw 的实际输入极性 +1/-1。
        az_refresh: (N,) 显式 AZ 源是否刷新。
        az_sample_error_v: (N,) 更新/保持并应用奇次传递后的误差 [RA 输出 V]。
        injected_ra_error_v: (N,) 进入 RA 钳位前的总显式误差 [RA 输出 V]。
        mode: 实际研究模式。

    Side effects:
        输出诊断数组只读；raw 保留 SimResult 原有语义，不丢弃失败样本。
    """

    raw: SimResult
    raw_words: CodeStream
    output_code: np.ndarray
    output_v: np.ndarray
    prequantized_output_v: np.ndarray
    output_time_s: np.ndarray
    source_sample_ids: np.ndarray
    invalid: np.ndarray
    raw_sample_rate_hz: float
    output_sample_rate_hz: float
    chop_sign: np.ndarray
    az_refresh: np.ndarray
    az_sample_error_v: np.ndarray
    injected_ra_error_v: np.ndarray
    mode: str


def pair_readout(
    words: CodeStream, *, difference: bool
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """用整数码的精确 bin 中心执行配对，再量化为同位宽输出。

    Args:
        words: 偶数长度 (N,) CodeStream；B=output_bits 在 [1,30]，码为
            [0,2**B) 的 offset-binary 整数。v_fs 是有限正输入峰值 [V]，
            三类 raw 标志须为同形布尔数组。
        difference: True 为偶减奇再除二；False 为普通配对平均。

    Returns:
        四个只读 (N/2,) 数组：(offset-binary code, bin 中心输入等效 V,
        精确配对电压 V, invalid)。设偶/奇码 ke/ko、M=2**B：平均输出为
        floor((ke+ko+1)/2)，差分输出为 floor((ke-ko+M)/2)。常数项来自
        midrise bin 中心及 offset-binary 零点，不是随意增加的舍入偏置。
        无符号码先扩为 int64 再减，避免无符号回绕；invalid 为 pair 内
        所有 raw 模拟溢出及数字裁剪标志的 OR，不过滤异常样本。

    Raises:
        ValueError: 长度、位宽、码制/范围、满幅或标志形状/类型非法。

    Notes:
        本函数只做码算术，不推断样本 ID、epoch 或 AZ 存储归属；fresh
        runner 在调用前检查连续偶数起始 ID。来源为 C32.1 Fig.6 的偶减奇
        机制，midrise 再量化是本工程合同。
    """
    code = np.asarray(words.code)
    if code.ndim != 1 or len(code) < 2 or len(code) % 2:
        raise ValueError("paired readout requires a positive even raw record")
    if type(words.output_bits) is not int or not 1 <= words.output_bits <= 30:
        raise ValueError("paired readout output_bits must be an integer in [1, 30]")
    count = 2**words.output_bits
    if not np.issubdtype(code.dtype, np.integer) or np.any(code < 0) or np.any(code >= count):
        raise ValueError("paired readout words must be in-range offset-binary integers")
    if not np.isfinite(words.v_fs) or words.v_fs <= 0:
        raise ValueError("paired readout v_fs must be finite and positive [V]")
    flags = [np.asarray(x) for x in (words.analog_overflow, words.clipped_low, words.clipped_high)]
    if any(x.shape != code.shape or x.dtype != np.dtype(bool) for x in flags):
        raise ValueError("paired readout requires matching Boolean raw flags")
    code = code.astype(np.int64)
    even, odd = code[::2], code[1::2]
    numerator = even - odd + count if difference else even + odd + 1
    output = numerator // 2
    raw_voltage = words.v_fs * (2 * (code + 0.5) / count - 1)
    exact = (
        (raw_voltage[::2] - raw_voltage[1::2]) / 2
        if difference
        else (raw_voltage[::2] + raw_voltage[1::2]) / 2
    )
    voltage = words.v_fs * (2 * (output + 0.5) / count - 1)
    invalid_raw = flags[0] | flags[1] | flags[2]
    invalid = invalid_raw[::2] | invalid_raw[1::2]
    return readonly(output, np.int64), readonly(voltage), readonly(exact), readonly(invalid, bool)


def _fresh_pair_ids(raw: SimResult, n_samples: int) -> np.ndarray:
    """检查 fresh 记录属于连续偶数起始的 32 位 raw ID 序列。

    按模 2**32 验证逐次加一，允许数值回绕；不把缺样、错序或奇数起始
    自动重排成新 pair。此检查不提供跨调用的 epoch/存储代状态。
    """
    ids = np.asarray(raw.sample_id)
    if (
        ids.shape != (n_samples,)
        or not np.issubdtype(ids.dtype, np.integer)
        or np.any(ids < 0)
        or np.any(ids >= 2**32)
    ):
        raise ConfigError("fresh AZ capture requires explicit unsigned 32-bit raw sample IDs")
    ids = ids.astype(np.int64)
    if ids[0] % 2 or np.any(((ids[1:] - ids[:-1]) & 0xFFFF_FFFF) != 1):
        raise ConfigError(
            "fresh AZ capture requires continuous IDs and an even positive first phase"
        )
    return ids


class _SampleIndexedChop:
    """仅在各 raw 样本孔径处施加极性，两次孔径 t1/t2 使用同一符号。

    依靠样本所有权索引，不按任意时间的 floor/ceil 猜测相位；因此不能
    用作连续时间开关波形或传入动态斩波电路求解器。
    """

    def __init__(self, source, signs):
        self.source, self.signs = source, signs

    def __call__(self, time_s):
        values = np.asarray(time_s)
        if values.shape != self.signs.shape:
            raise ConfigError("sample-domain chopper requires an owned raw-sample time array")
        return self.signs * self.source(values)


def _require_sample_domain(cfg: Config) -> None:
    unsupported = []
    for name in (
        "ra_autozero",
        "dyn_input_settling",
        "dyn_ref_settling",
        "dyn_crosstalk",
        "slice_timing_skew_s",
        "slice_offset_sigma_v",
        "slice_bw_spread",
        "sampling_tau_mismatch",
        "driver_noise_rms",
        "flicker_corner_hz",
        "ktc_enable",
    ):
        if getattr(cfg, name):
            unsupported.append(name)
    if cfg.conversion.dynamic:
        unsupported.append("conversion.dynamic")
    if cfg.adc2_dyn_bw_ratio is not None:
        unsupported.append("adc2_dyn_bw_ratio")
    if unsupported:
        raise ConfigError(
            "sample-domain AZ does not implement or combine: " + ", ".join(unsupported)
        )


def run_caaz_pipeline(
    cfg: Config,
    input_fn,
    n_samples: int,
    *,
    az: ChoppingAZParameters | None = None,
    post_az_error_v=None,
    az_rng: np.random.Generator | None = None,
    **pipeline_kwargs,
) -> ChoppingAZResult:
    """先运行完整 raw ADC 和逐码重构，再做有归属的成对读出。

    Args:
        cfg: 现有 ADC 配置；fs 是 raw 转换率 [Hz]，不是 pair 输出率。
        input_fn: 外部输入电压函数，按归属明确的 raw 孔径时间 [s] 求值。
        n_samples: 正整数 raw 数 N；非 off 模式要求偶数。
        az: 显式模式及假设的 RA 输出误差参数；None 等价默认 off。
        post_az_error_v: 可选 (N,) 有限的 AZ 后误差 [RA 输出 V]；
            是调用方定义的扰动，不自动成为器件 1/f 或失调消除模型。
        az_rng: 独立的 AZ 随机流；off/baseline 不消费，另两模式均抽 N 个
            创新样本以便同流比较，CAAZ 只使用偶数创新，奇数创新未被采入。
        **pipeline_kwargs: run_pipeline 的 chip/state/rng/scheduler/pool 参数；
            state 可包含独立训练并冻结的位权，不能在留出数据上重拟合。

    Returns:
        ChoppingAZResult，保留每个 raw 码和标志。CAAZ 的每个 raw 同时在
        SADC/RDAC 孔径按样本 ID 取 +/− 极性，误差在 RA 钳位前注入。
        非 off 输出 N/2 个码、pair 中点时间及 fs/2 速率。两点读出的信号
        幅频响应为 abs(cos(pi*fin/fs))；高于 pair Nyquist 的信号会混叠。
        基线也做同样两点平均，需分开评价平均降噪和相关 AZ 误差相消。

    Side effects:
        使用启用噪声源的 RNG；run_pipeline 重置 pool 电气状态而保留制造
        电容。不更改 Config、ADC2 窗口、旧噪声预算或普通默认输出。
        off 直接委托普通主链。此 fresh runner 不连续继承跨调用的配对状态。

    Raises:
        ConfigError: 非法参数/长度/显式误差流，重复 RA 误差入口，或非 off
            记录的 ID 归属不合法。非 off 还拒绝未实现的动态斩波、旧 AZ、
            flicker、KTC 及若干切片/驱动动态组合；独立孔径采样白噪声可保留。

    Notes:
        [C32.1 Figs.5-6] 支持输入斩波、偶更新/奇保持及偶减奇。raw40/pair20
        是项目选择，噪声幅度、rho/r 为假设；这些条件不是原芯片同条件复现。
    """
    az = ChoppingAZParameters() if az is None else az
    az.validate()
    if type(n_samples) is not int or n_samples < 1:
        raise ConfigError("raw sample count must be a positive integer")
    if "ra_output_error_v" in pipeline_kwargs:
        raise ConfigError("use the named AZ perturbation inputs, not two RA-error streams")
    external = (
        np.zeros(n_samples) if post_az_error_v is None else np.asarray(post_az_error_v, float)
    )
    if external.shape != (n_samples,) or np.any(~np.isfinite(external)):
        raise ConfigError("post-AZ error must be a finite RA-output-V stream of raw length")
    signs = np.ones(n_samples, dtype=np.int64)
    refresh = np.zeros(n_samples, dtype=bool)
    sampled = np.zeros(n_samples)
    injected = np.zeros(n_samples)
    if az.mode == "off":
        if post_az_error_v is not None:
            raise ConfigError("off mode cannot silently ignore a post-AZ error stream")
        raw = run_pipeline(cfg, input_fn, n_samples, **pipeline_kwargs)
    else:
        _require_sample_domain(cfg)
        if n_samples % 2:
            raise ConfigError("sampled-AZ research modes require an even raw sample count")
        if az.mode in ("conventional_az", "chopping_assisted"):
            generator = np.random.default_rng(cfg.seed + 120001) if az_rng is None else az_rng
            # 共享同一创新序列可比较相同物理假设；CAAZ 只采偶次，奇次重用保持值。
            # 仍抽 N 个数，保持 conventional/CAAZ 的随机流消耗一致。
            innovation = generator.normal(0, az.az_noise_rms_v, n_samples)
            if az.mode == "conventional_az":
                refresh[:] = True
                sampled[:] = innovation
            else:
                refresh[::2] = True
                sampled[::2] = innovation[::2]
                sampled[1::2] = az.odd_hold_retention * innovation[::2]
                signs[1::2] = -1
            sampled[1::2] *= az.odd_transfer_ratio
        injected = sampled + external + az.post_az_offset_v
        waveform = (
            _SampleIndexedChop(input_fn, signs) if az.mode == "chopping_assisted" else input_fn
        )
        raw = run_pipeline(cfg, waveform, n_samples, ra_output_error_v=injected, **pipeline_kwargs)
    # 每次 raw 先独立完成位权重构/最终码量化，再配对；保留逐 raw 的异常标志。
    words = raw.to_codes()
    invalid_raw = words.analog_overflow | words.clipped_low | words.clipped_high
    ids = np.arange(n_samples) if raw.sample_id is None else raw.sample_id
    if az.mode != "off":
        ids = _fresh_pair_ids(raw, n_samples)
    if az.mode == "off":
        output, voltage, exact, invalid = words.code, words.voltage, words.voltage, invalid_raw
        times, source_ids, output_fs = raw.sample.t1, ids[:, None], cfg.fs
    else:
        output, voltage, exact, invalid = pair_readout(
            words, difference=az.mode == "chopping_assisted"
        )
        times = (raw.sample.t1[::2] + raw.sample.t1[1::2]) / 2
        source_ids, output_fs = ids.reshape(-1, 2), cfg.fs / 2
    return ChoppingAZResult(
        raw,
        words,
        readonly(output, np.int64),
        readonly(voltage),
        readonly(exact),
        readonly(times),
        readonly(source_ids, np.int64),
        readonly(invalid, bool),
        cfg.fs,
        output_fs,
        readonly(signs, np.int64),
        readonly(refresh, bool),
        readonly(sampled),
        readonly(injected),
        az.mode,
    )
