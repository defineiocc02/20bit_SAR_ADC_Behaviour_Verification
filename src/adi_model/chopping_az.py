"""源图支持的样本域斩波协助AZ与完整码配对，默认不启用。

PKU VLSI2025 C32.1 Fig.5/6披露整输入换向、偶周期AZ刷新、奇周期保存同一输入等效状态及两个完整结果的半差。
本模块不抽取噪声，不从原型4.3dB或整机指标分配部件RMS。启用AZ时，
调用方须给出输入等效状态记录和每笔传递到RA输入的系数；奇次保持损失也须明确给出。
有效噪声传递为物理G乘该系数。

状态按放大器输入等效V定义，经RA增益后进入原有限幅与后级量化。每个raw先完整重构为整数码，
再按样本归属配对；全部raw码和异常保留。只实现样本域，
不实现连续斩波、CBIAS/FGA/CP-FIA电路或真实噪声传递。显式零输入仅为控制，不能证明噪声校准有效。
单次速率与两点输出响应分别报告。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np

from ._arrays import readonly
from .config import ConfigError

if TYPE_CHECKING:
    from .config import Config
    from .fixed_point import CodeStream, FixedPointFormat
    from .sim import SimResult
    from .weight_calibration import FrozenCalibration


@dataclass(frozen=True)
class ChoppingAZParameters:
    """只定义AZ更新/配对选择；不隐藏任何物理噪声幅度或传递默认值。

    mode为off、conventional_az或chopping_assisted。off直接运行普通主链；
    conventional_az每笔刷新并作普通两点平均；chopping_assisted偶刷新/奇保持，
    整输入反相后作整码偶减奇/2。后者必须显式给保持比例rho[0,1]。保持比例只表示给定两周期预算，
    不推断泄漏时间常数或器件统计。
    """

    mode: str = "off"
    odd_hold_retention: float | None = None

    def validate(self) -> None:
        """拒绝未知模式、缺少有效保持预算和不消费的非空保持参数。"""
        if self.mode not in ("off", "conventional_az", "chopping_assisted"):
            raise ConfigError("unknown sample-domain AZ mode")
        if self.mode == "chopping_assisted":
            value = self.odd_hold_retention
            if (
                value is None
                or isinstance(value, bool | np.bool_)
                or not isinstance(value, int | float | np.integer | np.floating)
                or np.ndim(value) != 0
                or np.iscomplexobj(value)
                or not np.isfinite(value)
                or not 0 <= value <= 1
            ):
                raise ConfigError("chopping_assisted requires explicit odd_hold_retention in [0,1]")
        elif self.odd_hold_retention is not None:
            raise ConfigError("odd_hold_retention is inactive outside chopping_assisted")


@dataclass(frozen=True)
class ChoppingAZResult:
    """保留原始结果、实际状态和输出视图；电压字段直接标明参考节点。

    az_sample_input_v是每笔使用的AZ状态[RA输入等效V]，已含奇次保持损失；
    az_transfer_to_input是调用方明确给出的每笔传递；injected_ra_input_error_v是它们的乘积，
    也是实际传入RA输入的误差。az_input_evidence区分未施加、显式零控制和非零显式记录，
    任何一种均不自动成为物理噪声验证结论。output_v为最终码中心[外部输入等效V]，
    prequantized_output_v只是raw码中心的精确平均/半差，不是模拟真值。
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
    az_sample_input_v: np.ndarray
    az_transfer_to_input: np.ndarray
    injected_ra_input_error_v: np.ndarray
    az_input_evidence: str
    mode: str


def pair_readout(
    words: CodeStream, *, difference: bool
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """用整数码的精确 bin 中心执行配对，再量化为同位宽输出。

    Args:
        words: 偶数长度 (N,) CodeStream；B=output_bits 在 [1,30]，
            码为[0,2**B) 的 offset-binary 整数。v_fs 是有限正输入峰值 [V]，三类 raw 标志须为同形布尔数组。
        difference: True 为偶减奇再除二；False 为普通配对平均。

    Returns:
        四个只读 (N/2,) 数组：(offset-binary code, bin 中心输入等效 V,精确配对电压 V, invalid)。
        设偶/奇码 ke/ko、M=2**B：平均输出为floor((ke+ko+1)/2)，差分输出为 floor((ke-ko+M)/2)。
        常数项来自midrise bin 中心及 offset-binary 零点，不是随意增加的舍入偏置。
        无符号码先扩为 int64 再减，避免无符号回绕；
        invalid 为 pair 内所有 raw 模拟溢出及数字裁剪标志的 OR，不过滤异常样本。

    Raises:
        ValueError: 长度、位宽、码制/范围、满幅或标志形状/类型非法。

    Notes:
        本函数只做码算术，不推断样本 ID、epoch 或 AZ 存储归属；
        fresh runner 在调用前检查连续偶数起始 ID。来源为 C32.1 Fig.6 的偶减奇机制，
        midrise 再量化是本工程合同。
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

    按模 2**32 验证逐次加一，允许数值回绕；不把缺样、错序或奇数起始自动重排成新 pair。
    此检查不提供跨调用的 epoch/存储代状态。
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
        raise ConfigError("fresh AZ capture requires continuous IDs and an even first phase")
    return ids


class _SampleIndexedChop:
    """仅在各raw样本的唯一孔径处按所属样本施加极性。

    依靠样本所有权索引，不按任意时间的 floor/ceil 猜测相位；
    因此不能用作连续时间开关波形或传入动态斩波电路求解器。
    """

    def __init__(self, source, signs):
        self.source, self.signs = source, signs

    def __call__(self, time_s):
        values = np.asarray(time_s)
        if values.shape != self.signs.shape:
            raise ConfigError("sample-domain chopper requires an owned raw-sample time array")
        return self.signs * self.source(values)


def _require_sample_domain(cfg: Config, pool) -> None:
    """只接受现有样本域操作，不将孔径换向当作连续开关求解。"""
    unsupported = [
        name
        for name in (
            "dyn_input_settling",
            "dyn_ref_settling",
            "dyn_crosstalk",
        )
        if getattr(cfg, name, None)
    ]
    conversion = getattr(cfg, "conversion", None)
    if conversion is not None and conversion.dynamic:
        unsupported.append("conversion.dynamic")
    if pool is None:
        raise ConfigError("active sample-domain AZ requires its explicit physical pool")
    for name in ("t_skew", "v_os"):
        value = getattr(pool, name, None)
        if value is not None and np.any(np.asarray(value) != 0):
            unsupported.append(f"pool.{name}")
    if unsupported:
        raise ConfigError(
            "sample-domain AZ does not implement or combine: " + ", ".join(unsupported)
        )


def _input_record(value, length: int, name: str) -> np.ndarray:
    """复制调用边界的显式实数记录，不广播、填补或截断样本。"""
    if value is None or np.iscomplexobj(value):
        raise ConfigError(f"{name} is required as a finite real record")
    try:
        record = np.asarray(value, dtype=float)
    except (TypeError, ValueError) as error:
        raise ConfigError(f"{name} must be a finite real record") from error
    if record.shape != (length,) or np.any(~np.isfinite(record)):
        raise ConfigError(f"{name} must have shape ({length},) with finite values")
    return record.copy()


def _az_input_state(
    az: ChoppingAZParameters,
    n_samples: int,
    az_input_samples_v,
    az_transfer_to_input,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, str]:
    """构建显式AZ记录的样本归属，不产生随机幅度或噪声分布。

    conventional_az需要N份采集记录；chopping_assisted需要N/2份，奇次仅按明确rho保持。
    az_transfer_to_input始终是(N,)的每笔无量纲传递，不能缺省为1。
    原文理想输入等效关系也要由调用方显式给全1/保持1，而非内部补值。
    """
    if not isinstance(az, ChoppingAZParameters):
        raise ConfigError("az must be an explicit ChoppingAZParameters value")
    az.validate()
    if type(n_samples) is not int or n_samples < 1:
        raise ConfigError("raw sample count must be a positive integer")
    signs = np.ones(n_samples, dtype=np.int64)
    refresh = np.zeros(n_samples, dtype=bool)
    sampled = np.zeros(n_samples)
    transfer = np.zeros(n_samples)
    injected = np.zeros(n_samples)
    if az.mode == "off":
        if az_input_samples_v is not None or az_transfer_to_input is not None:
            raise ConfigError("off mode cannot silently ignore supplied AZ records")
        return signs, refresh, sampled, transfer, injected, "NOT_APPLIED"
    if n_samples % 2:
        raise ConfigError("sample-domain AZ pairing requires an even raw sample count")
    source_length = n_samples // 2 if az.mode == "chopping_assisted" else n_samples
    samples = _input_record(az_input_samples_v, source_length, "az_input_samples_v [RA-input V]")
    transfer = _input_record(az_transfer_to_input, n_samples, "az_transfer_to_input [1]")
    if az.mode == "chopping_assisted":
        retention = az.odd_hold_retention
        if retention is None:
            raise ConfigError("odd_hold_retention is required by the active AZ hold")
        refresh[::2] = True
        sampled[::2] = samples
        sampled[1::2] = retention * samples
        signs[1::2] = -1
    else:
        refresh[:] = True
        sampled[:] = samples
    with np.errstate(over="raise", invalid="raise"):
        try:
            injected = transfer * sampled
        except FloatingPointError as error:
            raise ConfigError("transferred AZ input record is not representable") from error
    evidence = "ZERO_INPUT_CONTROL" if not np.any(injected) else "EXPLICIT_INPUT_RECORD"
    return signs, refresh, sampled, transfer, injected, evidence


def run_caaz_pipeline(
    cfg: Config,
    input_fn,
    n_samples: int,
    *,
    format: FixedPointFormat,
    calibration: FrozenCalibration | None = None,
    az: ChoppingAZParameters | None = None,
    az_input_samples_v=None,
    az_transfer_to_input=None,
    **pipeline_kwargs,
) -> ChoppingAZResult:
    """先逐笔执行 ADC 和整数重构，再按真实样本 ID 配对读出。

    激活 AZ 时，az_input_samples_v 是 FGA/RA 输入等效采集状态 [V]：conventional_az 长度为 N，
    chopping_assisted 为 N/2。az_transfer_to_input 是长度 N 的显式传递；
    实际 RA 输入误差为 transfer*held_sample，再乘物理 G。
    本入口没有额外 RMS、分布、RNG、输出误差或偏置的隐含物理默认值。

    format 必须明确提供；calibration 直接透传整数重构器。未显式给模型时，
    只允许使用结果中已有的冻结模型，不生成名义系数。启用配对时输出速率为 cfg.fs/2，
    时间取两个孔径中点，信号幅频响应为 abs(cos(pi*fin/cfg.fs))；off 模式保留原速率与孔径。

    两点响应与码中心 floor 是明确计算规则。无噪控制、数字同代或运行成功，
    不证明实际 CBIAS 保持或噪声收益。
    """
    from .pipeline import run_pipeline

    az = ChoppingAZParameters() if az is None else az
    state = _az_input_state(az, n_samples, az_input_samples_v, az_transfer_to_input)
    signs, refresh, sampled, transfer, injected, evidence = state
    if "ra_input_error_v" in pipeline_kwargs:
        raise ConfigError("use the explicit AZ records, not two RA-input error streams")
    if az.mode == "off":
        raw = run_pipeline(cfg, input_fn, n_samples, **pipeline_kwargs)
    else:
        _require_sample_domain(cfg, pipeline_kwargs.get("pool"))
        waveform = (
            _SampleIndexedChop(input_fn, signs) if az.mode == "chopping_assisted" else input_fn
        )
        raw = run_pipeline(cfg, waveform, n_samples, ra_input_error_v=injected, **pipeline_kwargs)
    words = raw.to_codes(format=format, calibration=calibration)
    if az.mode == "off":
        ids = np.asarray(raw.sample_id)
        if (
            ids.shape != (n_samples,)
            or not np.issubdtype(ids.dtype, np.integer)
            or np.any(ids < 0)
            or np.any(ids >= 2**32)
        ):
            raise ConfigError("raw pipeline must provide explicit sample ownership")
        output, voltage, exact = words.code, words.voltage, words.voltage
        invalid = words.analog_overflow | words.clipped_low | words.clipped_high
        times, source_ids, output_fs = raw.sample.aperture_s, ids[:, None], cfg.fs
    else:
        ids = _fresh_pair_ids(raw, n_samples)
        output, voltage, exact, invalid = pair_readout(
            words, difference=az.mode == "chopping_assisted"
        )
        times = (raw.sample.aperture_s[::2] + raw.sample.aperture_s[1::2]) / 2
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
        readonly(transfer),
        readonly(injected),
        evidence,
        az.mode,
    )
