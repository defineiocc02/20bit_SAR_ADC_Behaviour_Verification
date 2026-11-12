"""单孔径采样记录、显式样本域噪声与解析输入函数。

本模块没有KTC第二孔径或隐藏dither生成器。显式Fig19 program分别形成SADC的x-L与RDAC的x-T。
波形为外部输入V，SADC噪声为其决策节点V，
RDAC噪声为按Cnorm归一的残差输入等效V；时间为s。采样噪声的RDAC等效电容必须由调用者显式给出；
不再将某一未知相位网络的Csignal、Cload或旧噪声公式当作默认。该噪声是声明的样本域过程，
不是开关谱求解。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from adi_model._arrays import readonly
from adi_model.control.dem import _integer_array
from adi_model.control.dither import Fig19DitherProgram
from adi_model.simulation.config import K_B, Config, ConfigError


def finite_record(value, shape: tuple[int, ...], name: str, *, scalar: bool = True) -> np.ndarray:
    """复制显式实电压/状态数组；只在合同允许时广播标量。"""
    try:
        raw = np.asarray(value)
        if value is None or np.iscomplexobj(raw) or raw.dtype.kind not in "iuf":
            raise ValueError("expected explicit real numeric data")
        array = np.asarray(raw, dtype=float)
    except (TypeError, ValueError, OverflowError) as error:
        raise ConfigError(f"{name} must explicitly provide finite real values") from error
    if scalar and array.ndim == 0:
        array = np.full(shape, float(array))
    if array.shape != shape or np.any(~np.isfinite(array)):
        raise ConfigError(f"{name} must have finite shape {shape}")
    return array.copy()


@dataclass(frozen=True)
class SampleCapture:
    """单个raw记录的孔径与样本域源；数组均为(N,)。

    aperture_s/acquisition_start_s的周期坐标从本记录0开始，sample_ids另保实际数字身份，
    不从ID回绕推物理时间。input_clean_v是驱动噪声前的评分真值，input_v含公共驱动噪声；
    x_sadc另含独立SADC采样噪声和显式一阶tau误差；Fig19开启时再含-L。
    rdac_input_v是实际送给RDAC采样底板的输入，Fig19开启时为input_v-T，默认等于input_v。
    rdac_noise_v在主引擎作为Cnorm*noise的保存电荷误差加入；不是实际逐节点开关噪声协方差，
    也不自动除Signal连接衰减，不进入数字观测特征。
    """

    aperture_s: np.ndarray
    acquisition_start_s: np.ndarray
    sample_ids: np.ndarray
    input_clean_v: np.ndarray
    input_v: np.ndarray
    x_sadc: np.ndarray
    rdac_input_v: np.ndarray
    driver_noise_v: np.ndarray
    sadc_noise_v: np.ndarray
    rdac_noise_v: np.ndarray

    def __post_init__(self):
        """冻结快照，不使下游模拟重标定改变已记录源。"""
        for name in self.__dataclass_fields__:
            value = np.asarray(getattr(self, name))
            object.__setattr__(self, name, readonly(value, value.dtype))


def capture(
    cfg: Config,
    input_fn,
    n_samples: int,
    *,
    sample_ids,
    rng: np.random.Generator | None = None,
    rdac_noise_cap_f=None,
    dither_program: Fig19DitherProgram | None = None,
    dither_sample_v=None,
) -> SampleCapture:
    """在单孔径生成可复放输入与显式噪声，保留每笔raw。

    Args:
        cfg: 消费fs及实际启用的噪声/一阶tau字段，不检查ADC2或物理几何。
        input_fn: t[s]到输入[V]的有限波形，需支持(N,)时间向量。
        n_samples: 正整数raw笔数。
        sample_ids: (N,)显式uint32域整数身份；实际控制流独立于时间原点。
        rng: 启用随机过程时的Generator，或使用显式cfg.seed构造。
        rdac_noise_cap_f: 启用热噪声时必填(N,)正等效电容[F]；
            定义所选残差输入域sigma=sqrt(chi*kB*T/Ceq)，不从物理拓扑补相位假设。
        dither_program: 可选同笔Fig19静态等效控制；lower/total从同一Q32字生成，
            必须与raw长度和Vfs一致，不用RA误差代替采样注入。
        dither_sample_v: 可选(N,)注入**采样节点**的 dither 电压[V]。独立电荷口
            拓扑里 lower 段由 dither 电容在采样节点注入，因此 SADC 与 RDAC
            两个视图同时平移；upper 段由残差节点的独立电荷口注入（不在本函数内）。
            与 dither_program 互斥。

    Returns:
        含真实SADC/RDAC两个采样输入视图的SampleCapture；采集起点严格aperture−Ts。

    Raises:
        ConfigError: 缺失活跃输入、非法ID/数组或未声明噪声分布。

    Side effects: 只消费活跃随机源；不改Config、输入或实际电容。
    """
    if isinstance(n_samples, bool) or not isinstance(n_samples, int | np.integer) or n_samples < 1:
        raise ConfigError("sample count must be a positive integer")
    fs = float(finite_record(cfg.fs, (), "sampling fs [Hz]"))
    if fs <= 0:
        raise ConfigError("sampling fs must be finite and positive [Hz]")
    ids = _integer_array(sample_ids, "sample IDs", ndim=1)
    if ids.shape != (n_samples,) or np.any((ids < 0) | (ids > 0xFFFF_FFFF)):
        raise ConfigError("sample IDs must explicitly identify each raw within uint32")
    if len(ids) > 1:
        steps = np.diff(ids) & 0xFFFF_FFFF
        if np.any(steps == 0) or np.any(steps > 0x7FFF_FFFF):
            raise ConfigError("sample IDs must preserve forward order within one epoch")
    aperture = np.arange(n_samples, dtype=float) / fs
    clean = finite_record(input_fn(aperture), (n_samples,), "input waveform [V]")
    driver = np.zeros(n_samples)
    n_sadc = np.zeros(n_samples)
    n_rdac = np.zeros(n_samples)
    if type(cfg.enable_sampling_noise) is not bool:
        raise ConfigError("enable_sampling_noise must be bool")
    driver_active = cfg.driver_noise_rms is not None or cfg.driver_noise_distribution is not None
    random_active = driver_active or cfg.enable_sampling_noise
    if random_active:
        if rng is None:
            if (
                isinstance(cfg.seed, bool)
                or not isinstance(cfg.seed, int | np.integer)
                or cfg.seed < 0
            ):
                raise ConfigError(
                    "active sampling random sources require explicit nonnegative seed"
                )
            rng = np.random.default_rng(cfg.seed)
        elif not isinstance(rng, np.random.Generator):
            raise ConfigError("sampling RNG must be a Generator")
    if driver_active:
        if cfg.driver_noise_distribution != "gaussian_white":
            raise ConfigError("driver noise requires explicit gaussian_white law")
        rms = float(finite_record(cfg.driver_noise_rms, (), "driver noise RMS[V]"))
        if rms < 0:
            raise ConfigError("driver noise RMS must be nonnegative [V]")
        assert rng is not None
        if rms > 0:
            driver = rng.normal(0.0, rms, n_samples)
    if cfg.enable_sampling_noise:
        thermal_parameters = {
            name: float(finite_record(getattr(cfg, name), (), name))
            for name in ("temperature_k", "chi", "c_sadc")
        }
        if any(value <= 0 for value in thermal_parameters.values()):
            raise ConfigError(
                "temperature_k[K], chi[1], c_sadc[F] must be positive for sampling noise"
            )
        if cfg.sampling_noise_distribution != "gaussian_white":
            raise ConfigError("sampling noise requires explicit gaussian_white law")
        capacitance = finite_record(
            rdac_noise_cap_f, (n_samples,), "rdac_noise_cap_f [F]", scalar=False
        )
        if np.any(capacitance <= 0):
            raise ConfigError("RDAC noise-equivalent capacitance must be positive [F]")
        assert rng is not None
        thermal = thermal_parameters["chi"] * K_B * thermal_parameters["temperature_k"]
        n_rdac = rng.normal(size=n_samples) * np.sqrt(thermal / capacitance)
        n_sadc = rng.normal(size=n_samples) * np.sqrt(thermal / thermal_parameters["c_sadc"])
    elif rdac_noise_cap_f is not None:
        raise ConfigError("rdac_noise_cap_f is inactive while sampling noise is disabled")
    driven = clean + driver
    lower = np.zeros(n_samples)
    total = np.zeros(n_samples)
    if dither_program is not None and dither_sample_v is not None:
        raise ConfigError(
            "dither_program (code-shift topology) and dither_sample_v (charge-port "
            "topology) are mutually exclusive; pick one and state it"
        )
    if dither_sample_v is not None:
        # 独立电荷口：lower 段注入在采样节点，两个视图一起平移。
        lower = finite_record(dither_sample_v, (n_samples,), "dither sample [V]", scalar=False)
        total = lower
    if dither_program is not None:
        if (
            not isinstance(dither_program, Fig19DitherProgram)
            or dither_program.words.shape != (n_samples,)
            or dither_program.spec.v_fs != cfg.v_fs
        ):
            raise ConfigError("Fig19 program length/Vfs must match the actual sampling record")
        lower, total = dither_program.lower_v, dither_program.total_v
    tau_error = np.zeros(n_samples)
    if cfg.sampling_tau_mismatch is not None:
        tau = float(finite_record(cfg.sampling_tau_mismatch, (), "sampling_tau_mismatch [s]"))
        if tau != 0:
            tau_error = -tau * input_derivative(input_fn, aperture)
    return SampleCapture(
        aperture,
        aperture - 1.0 / fs,
        ids,
        clean,
        driven,
        driven + n_sadc + tau_error - lower,
        driven - total,
        driver,
        n_sadc,
        n_rdac,
    )


class AnalyticInput:
    """显式波形与匹配解析导数；harmonics只给已声明的实相量[Hz,V]。"""

    def __init__(self, fn, derivative, *, harmonics=None):
        """保存可调用输入与解析导数，不从短记录拟造连续频谱。"""
        if not callable(fn) or not callable(derivative):
            raise ValueError("input and analytic derivative must be callable")
        self._fn = fn
        self.derivative = derivative
        self.harmonics = harmonics

    def __call__(self, t):
        """在显式时间[s]返回电压[V]。"""
        return self._fn(t)


def dc_input(level: float) -> AnalyticInput:
    """构造显式直流电压[V]与零导数[V/s]的数学输入。"""
    level_v = float(finite_record(level, (), "DC level [V]"))
    return AnalyticInput(
        lambda t: np.full_like(np.asarray(t, dtype=float), level_v),
        lambda t: np.zeros_like(np.asarray(t, dtype=float)),
        harmonics=((0.0, complex(level_v)),),
    )


def sine_input(amp: float, fin: float, phase: float = 0.0) -> AnalyticInput:
    """构造峰值amp[V]、fin[Hz]、phase[rad]的解析正弦输入。"""
    amplitude_v = float(finite_record(amp, (), "sine amplitude [V]"))
    frequency_hz = float(finite_record(fin, (), "sine frequency [Hz]"))
    phase_rad = float(finite_record(phase, (), "sine phase [rad]"))
    return AnalyticInput(
        lambda t: amplitude_v * np.sin(2 * np.pi * frequency_hz * np.asarray(t) + phase_rad),
        lambda t: 2
        * np.pi
        * frequency_hz
        * amplitude_v
        * np.cos(2 * np.pi * frequency_hz * np.asarray(t) + phase_rad),
        harmonics=((frequency_hz, -1j * amplitude_v * np.exp(1j * phase_rad)),),
    )


def spectral_derivative(x: np.ndarray, dt: float) -> np.ndarray:
    """显式周期且均匀网格的数学频域导数，不作生产波形的隐式假设。"""
    values = np.asarray(x, dtype=float)
    if values.ndim != 1 or np.any(~np.isfinite(values)) or not np.isfinite(dt) or dt <= 0:
        raise ValueError("spectral derivative needs a finite periodic vector and positive dt[s]")
    if values.size < 2:
        return np.zeros_like(values)
    omega = 2 * np.pi * np.fft.fftfreq(values.size, d=dt)
    return np.fft.ifft(1j * omega * np.fft.fft(values)).real


def input_derivative(input_fn, t: np.ndarray) -> np.ndarray:
    """读取明确解析导数[V/s]，缺失时拒绝而不猜周期性或有限差分步长。"""
    times = np.asarray(t, dtype=float)
    derivative = getattr(input_fn, "derivative", None)
    if not callable(derivative):
        raise ConfigError("the active response approximation requires an explicit input derivative")
    return finite_record(derivative(times), times.shape, "input derivative [V/s]")
