"""生成SADC/RDAC孔径事件、dither端口量和采样噪声。

x1/x2是dither前输入节点[V]；x_sadc是SADC输入，x_rdac是RDAC保存电荷的输入等效V。
两条采样热噪声n_S/n_R独立；开启的驱动噪声在t1/t2使用同一实现。量化dither分d_Q与d_R：
dual_port取d_R=ratio*d_Q，range/granularity的d_R=0；采样mask模式另生成alpha*x+d。
后续物理pool会按实际选中电容重算采样注入。

时间单位s；coarse决策不在此模块，dither_bank_code是采样mask码，dither_code的模式含义见字段。
这里是样本域事件模型，不是连续开关注入或噪声谱电路求解器。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from .config import K_B, TEMP_K, Config


@dataclass
class SampleBatch:
    """一次采样的全部事件级实现（不可变语义：下游只读，

    改写仅限 sampling_dither_injection 的注入重标定——它必须同步改 x_rdac 与 dither）。

    字段单位契约：全部 ``(N,)`` float64 [V]，除 t1/t2 [s]、dither_code [RDAC 单位当量]；
    x1_clean/x2_clean 为 ``(N,)`` float64 [V]（None=未保留）。

    Attributes:
        t1, t2: 采样时刻 [s]（t2 = t1 + ktc_dt）。
        x1, x2: 输入采样值 [V]（含驱动器噪声 nd，若开启）。
        dx: x2 − x1 [V]。
        u1: dither 注入后的输入节点 [V]（两条采样通路共享）。
        x_sadc: SADC 决策通路取样 [V] = u1 + n_S + tau_err + sadc_dither。
        x_rdac: RDAC 保存通路取样 [V] = u1 + rdac_dither + n_R。
        n_R: RDAC 采样热噪声 [V]（同一实现进残差通路与 KTC 观测通路）。
        n_S: SADC 采样热噪声 [V]。
        dither: 模拟注入量 [V]（数字端已知，用于成对扣除）。
        dither_code: 码域配对量 [RDAC 单位当量]（sampling 模式用）。
        x1_clean: 驱动器噪声加入**之前**的 x1 [V]；None=未保留（driver_noise_rms=0 时与 x1 等同）。
            度量"相对干净输入的误差"必须用此项，否则 1 mV 驱动器噪声会伪装成 1 µV 内部误差。
        x2_clean: 同 x1_clean，对应 x2 [V]；None=未保留。
    """

    t1: np.ndarray
    t2: np.ndarray
    x1: np.ndarray  # x(t1)
    x2: np.ndarray  # x(t2)
    dx: np.ndarray  # x2 - x1
    u1: np.ndarray  # 注入 dither 后的输入（供 SADC/RDAC 采）
    x_sadc: np.ndarray  # x_S = u1 + n_S
    x_rdac: np.ndarray  # x_R = u1 + n_R
    n_R: np.ndarray  # RDAC 采样噪声（同一实现要进残差通路与 KTC 观测通路）
    n_S: np.ndarray
    dither: np.ndarray  # 模拟注入量（数字端已知，用于成对扣除）
    dither_code: np.ndarray  # 码域配对量（单位 = RDAC 单位；sampling 模式用）
    # 驱动器噪声加入之前的输入，用于整链评分，不与含噪x1混用。
    # `x1`/`x2` 是"驱动器看到的节点口径"；它们含有 nd，所以
    # `out - x1` 会把 nd 消掉，测不出整链误差。要度量"相对干净输入的误差"
    # 必须用这两个字段 —— 否则 1 mV 的驱动器噪声会伪装成 1 µV 的内部误差。
    x1_clean: np.ndarray | None = None
    x2_clean: np.ndarray | None = None
    dither_bank_code: np.ndarray | None = None  # 原始掩码码；split 重标定后保留
    signal_alpha: float | np.ndarray = 1.0  # 物理信号电荷系数，仅供模拟通路
    rdac_dither: np.ndarray | None = None  # 双端口模型的名义 RDAC 注入 [V]


def capture(
    cfg: Config,
    input_fn,
    n_samples: int,
    rng: np.random.Generator,
    dither_rng: np.random.Generator | None = None,
    chip=None,
    c_active: np.ndarray | None = None,
    dither_codes: np.ndarray | None = None,
) -> SampleBatch:
    """在两个归属明确的孔径时刻生成一份可重放的采样记录。

    Args:
        cfg: 配置；单位和dither profile合同见Config。
        input_fn: t[s]到dither前输入[V]的波形。
        n_samples: raw记录长度N。
        rng: 采样与驱动噪声的随机流。
        dither_rng: 可选独立dither流；给定时不消费采样噪声流。
        chip: 可选aggregate实现，兼容路径用其电容；不作为数字估计器输入。
        c_active: 可选(N,)噪声等效电容[F]。split由C_signal²/Σ(A+beta²B)给出，
            不等于整个18-slice池的物理电容总和。
        dither_codes: 可选(N,)整数码，用于指定实际序列；rtl16传固定D=2 LFSR。
            quantizer码单位为名义RDAC细栅格；sampling码为bank mask单位。

    Returns:
        SampleBatch：所有N笔事件均保留。quantizer的d_Q来自幅度旋钮或显式码，
        可再取整到RDAC栅格；d_R由独立端口比决定，不能一律写RDAC不含dither。
        sampling先用名义mask，生产pool随后同步更新x_rdac、dither和实际alpha。

    Side effects:
        消费rng/dither_rng；不修改cfg/chip。dither跨KTC提取窗口变化未定义时拒绝。
    """
    n = np.arange(n_samples)
    t1 = n / cfg.fs
    dt = cfg.ktc_dt()
    t2 = t1 + dt

    x1 = np.asarray(input_fn(t1), dtype=float)
    x2 = np.asarray(input_fn(t2), dtype=float)
    dx = x2 - x1

    # 驱动器噪声加入**之前**的干净值 —— 只在有驱动器噪声时才需要保留副本
    # 。
    if cfg.driver_noise_rms > 0:
        x1_clean = x1.copy()
        x2_clean = x2.copy()
    else:
        x1_clean = x1
        x2_clean = x2

    # 驱动器噪声：t1 与 t2 上的同一实现（共享输入上的噪声）
    if cfg.driver_noise_rms > 0:
        nd = rng.normal(0.0, cfg.driver_noise_rms, n_samples)
        x1 = x1 + nd
        x2 = x2 + nd
        dx = x2 - x1

    if dither_codes is not None:
        dither_codes = np.asarray(dither_codes)
        if (
            dither_codes.shape != (n_samples,)
            or cfg.dither_mode not in ("sampling", "quantizer")
            or np.any(~np.isfinite(dither_codes))
            or np.any(dither_codes != np.rint(dither_codes))
            or np.any(np.abs(dither_codes) > cfg.dither_units_range)
        ):
            raise ValueError("explicit dither codes must be legal integer samples")

    # dither：两种物理实现，代价形式完全不同
    dither = np.zeros(n_samples)  # 加到采样值上的电压（数字端已知）
    dither_code = np.zeros(n_samples)  # 码域配对量（单位 = RDAC 单位）
    if cfg.dither_mode == "analog":
        # 输入注入：直接从 ±V_FS 里扣量程
        drng = dither_rng if dither_rng is not None else rng
        amp = cfg.dither_amplitude_lsb1 * cfg.delta1
        dither = drng.uniform(-amp, amp, n_samples)
    elif cfg.dither_mode == "quantizer":
        # 生成SADC端d_Q。显式rtl16整数序列覆盖amplitude旋钮；
        # generic按幅度抽样，再可选取整到ΔDAC，故小于半栅格时可全为0。
        # RDAC端d_R稍后由dither_rdac_ratio决定，不可一律称RDAC未含dither。
        drng = dither_rng if dither_rng is not None else rng
        amp = cfg.dither_amplitude_lsb1 * cfg.delta1
        dither = (
            drng.uniform(-amp, amp, n_samples)
            if dither_codes is None
            else dither_codes * cfg.nominal_rdac_step
        )
        dither_code = dither / cfg.delta1
        if cfg.dither_discrete:
            dither = np.round(dither / cfg.nominal_rdac_step) * cfg.nominal_rdac_step
            dither_code = dither / cfg.delta1
    elif cfg.dither_mode == "sampling":
        # 采样态电荷注入（专利 [10]）：2D 个单位不接输入而接 ±V_FS
        #   D+d_u 个接 +V_FS，D−d_u 个接 −V_FS  →  注入量 = d_u * step
        #   信号衰减 α = (N−2D)/N 恒定，与 d_u 无关
        drng = dither_rng if dither_rng is not None else rng
        D = cfg.dither_units_range
        dcode = drng.uniform(-D, D, n_samples) if dither_codes is None else dither_codes.copy()
        if getattr(cfg, "dither_discrete", False):
            # 离散掩码口径（stage18）：物理开关只能整数单位选择。
            # 连续均匀分布保留为数学基准；离散化后 d_u ∈ {−D..D} 共 2D+1 个码。
            dcode = np.round(dcode)
        dither_code = dcode
        # 这 2D 个物理单元自身也有失配 → 实际注入量偏离名义值
        eps_d = 0.0
        if chip is not None and cfg.mismatch_enable:
            n_dith = cfg.dither_units_total
            if n_dith > 0:
                eps_d = float(chip.eps.reshape(-1)[-n_dith:].mean())
        dither = dither_code * cfg.rdac_step * (1.0 + eps_d)
    # sampling的两条信号通路先用同一名义alpha；mask注入仅进保存电荷，
    # 不再叠加到SADC。物理pool随后按相同mask更新实际alpha和注入。
    # analog共同输入已经包含d，不能在SADC或RDAC再注入一次。
    if cfg.dither_mode == "analog":
        # 输入注入：dither 是真实加到输入端的电压，两条采样通路**都会**看到它
        # —— 已经含在 u1 里，sadc_dither 必须为 0（重入即 x_sadc = x+2d，
        # 首版教训：粗码被双倍扰动 -> analog 校准联调 G_hat 减半）。
        u1 = x1 + dither
        rdac_dither: float | np.ndarray = 0.0
        sadc_dither: float | np.ndarray = 0.0
    elif cfg.dither_mode == "quantizer":
        # quantizer：SADC看x+d_Q；RDAC保存x+d_R，dual_port的d_R=ratio*d_Q
        # range/granularity的ratio=0；两端口的码修改与数字扣除由下游按同笔上下文配对。
        u1 = x1
        rdac_dither = cfg.dither_rdac_ratio * dither
        sadc_dither = dither
    else:
        u1 = cfg.dither_alpha * x1
        rdac_dither = dither
        sadc_dither = 0.0
    if cfg.dither_changes_in_window:
        # 明确标注的开放问题：若 dither 在窗口内跳变，dx 需要额外扣除
        raise NotImplementedError("dither 在提取窗口内变化的行为尚未定义")

    # [09] 1.5 采样响应失配：Δτ 造成的一阶跟踪误差 −Δτ·dx/dt（信号相关，非静态增益）
    tau_err = np.zeros(n_samples)
    if cfg.sampling_tau_mismatch != 0.0:
        h = 1.0 / (cfg.fs * 64.0)
        deriv = (
            np.asarray(input_fn(t1 + h), dtype=float) - np.asarray(input_fn(t1 - h), dtype=float)
        ) / (2.0 * h)
        tau_err = -cfg.sampling_tau_mismatch * deriv

    if cfg.enable_sampling_noise:
        if c_active is not None:
            # 逐样本口径：噪声绑定本次实际采样的电容（可含失配后的真实值）
            ca = np.maximum(np.asarray(c_active, dtype=float), 1e-30)
            sig_r = np.sqrt(cfg.chi * K_B * TEMP_K / ca)
            n_R = rng.normal(0.0, 1.0, n_samples) * sig_r
        elif chip is not None:
            # 兼容路径：按名义活跃电容（8 slice）而非整池
            sig_r = math.sqrt(cfg.chi * K_B * TEMP_K / cfg.c_active_nominal())
            n_R = rng.normal(0.0, sig_r, n_samples)
        else:
            n_R = rng.normal(0.0, cfg.sigma_sampling(), n_samples)
        n_S = rng.normal(0.0, cfg.sigma_sadc_sampling(), n_samples)
    else:
        n_R = np.zeros(n_samples)
        n_S = np.zeros(n_samples)

    # RDAC 保存 = 衰减后的信号 + 注入电荷；SADC 决策通路按模式决定是否含 dither
    # （整数 dither 不进粗码，见 [10] 2.6 口径注释）
    return SampleBatch(
        t1=t1,
        t2=t2,
        x1=x1,
        x2=x2,
        dx=dx,
        u1=u1,
        x_sadc=u1 + n_S + tau_err + sadc_dither,
        x_rdac=(u1 + rdac_dither) + n_R,
        n_R=n_R,
        n_S=n_S,
        dither=dither,
        dither_code=dither_code,
        rdac_dither=(
            cfg.dither_rdac_ratio * dither
            if cfg.dither_mode == "quantizer"
            else np.zeros(n_samples)
        ),
        x1_clean=x1_clean,
        x2_clean=x2_clean,
    )


class AnalyticInput:
    """A ``t -> x`` input generator that also knows its own exact derivative.

    Timing skew is proportional to ``dx/dt`` , so an input that can supply an analytic slope removes
    the last numerical approximation from that path. A plain callable is still accepted everywhere;
    :func:`input_derivative` falls back to the spectral estimate when ``.derivative`` is absent.
    """

    def __init__(self, fn, derivative, *, harmonics=None):
        """绑定波形与其解析导数。

        Args:
            fn: ``t -> x(t)`` 波形，[V]。
            derivative: ``t -> dx/dt``，[V/s]，须与 ``fn`` 精确对应。
            harmonics: Optional (Hz, complex V) real-phasor terms for exact continuous RC
                integration; not inferred from sampled data.
        """
        self._fn = fn
        self.derivative = derivative
        self.harmonics = harmonics

    def __call__(self, t):
        """在时刻 ``t`` 求波形值（标量或数组均可）。

        Args:
            t: 时刻 [s]，标量或 ndarray。

        Returns:
            ndarray 或标量：波形值 [V]，与 ``t`` 同形状。
        """
        return self._fn(t)


def dc_input(level: float) -> AnalyticInput:
    """直流输入（静态测试用）。

    Args:
        level: 直流电平 [V]。

    Returns:
        AnalyticInput：恒定输出 ``level`` [V]，解析导数 0 [V/s]
        （供 timing skew 路径使用，斜率为 0 故无 skew 误差）。
    """
    return AnalyticInput(
        lambda t: np.full_like(np.asarray(t, dtype=float), level),
        lambda t: np.zeros_like(np.asarray(t, dtype=float)),
        harmonics=((0.0, complex(level)),),
    )


def sine_input(amp: float, fin: float, phase: float = 0.0) -> AnalyticInput:
    """正弦输入发生器。

    Args:
        amp: 峰值 [V]（惯用 0.7·v_fs / 0.9·v_fs，留 dither/失配余量）。
        fin: 频率 [Hz]；
            正式动态指标必须用 _coherent_fin 的相干频率（非相干 + 窗函数会把泄漏拟合成假杂散，
            SFDR 陷阱见工作记忆）。
        phase: 初相 [rad]。

    Returns:
        f(t) -> amp·sin(2π·fin·t + phase)，t 标量或 ndarray [s]。

        返回的可调用对象**带一个精确的 ``.derivative`` 属性**，供 timing
        skew 使用（见 :func:`input_derivative`）。
    """
    return AnalyticInput(
        lambda t: amp * np.sin(2 * np.pi * fin * np.asarray(t) + phase),
        lambda t: 2 * np.pi * fin * amp * np.cos(2 * np.pi * fin * np.asarray(t) + phase),
        harmonics=((float(fin), -1j * amp * np.exp(1j * phase)),),
    )


def spectral_derivative(x: np.ndarray, dt: float) -> np.ndarray:
    """周期、均匀孔径序列的频域导数。

    乘j*omega对已解析且严格低于Nyquist的周期分量求导；实Nyquist采样不能唯一确定连续导数，
    本函数给出其实频域导数0。普通中心差分会按sin(w)/w压低高频斜率。

    Args:
        x: (N,)周期样本，单位可为输入V。
        dt: 均匀孔径间隔[s]。

    Returns:
        (N,)导数，单位为输入单位/s。不把非周期短记录变成准确的连续波形导数。
    """
    x = np.asarray(x, dtype=float)
    n = x.size
    if n < 2:
        return np.zeros_like(x)
    omega = 2 * np.pi * np.fft.fftfreq(n, d=dt)
    return np.fft.ifft(1j * omega * np.fft.fft(x)).real


def input_derivative(input_fn, t: np.ndarray) -> np.ndarray:
    """``d/dt`` of an input waveform at times ``t`` -- skew's driving quantity.

    Preference order:

    1. **Analytic.** ``sine_input`` / ``dc_input`` attach a ``.derivative`` attribute; a general
       callable may do the same. Exact at any ``fin`` , including near Nyquist.
    2. **Spectral.** Otherwise :func:`spectral_derivative` on the uniform periodic grid, for
       resolved content strictly below ``fs/2`` . At Nyquist the continuous derivative is not
       identifiable without extra information.

    ``np.gradient`` is deliberately **not** used: see :func:`spectral_derivative` for the 25.6 dB
    near-Nyquist bias that made it unacceptable (audit A07.3) .

    Args:
        input_fn: any callable ``t -> x`` ; may carry ``.derivative`` .
        t: ``(N,)`` uniform time grid [s].

    Returns:
        ``(N,)`` slope [V/s].
    """
    t = np.asarray(t, dtype=float)
    analytic = getattr(input_fn, "derivative", None)
    if callable(analytic):
        return np.asarray(analytic(t), dtype=float)
    if t.size < 2:
        return np.zeros_like(t)
    return spectral_derivative(input_fn(t), float(t[1] - t[0]))
