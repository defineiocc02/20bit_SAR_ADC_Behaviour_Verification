"""sampler.py -- SADC / RDAC 两条采样路径，以及它们各自的噪声。

    x_S[n] = x[n] + n_S[n]        -> 供小 SADC 判断
    x_R[n] = x[n] + n_R[n]        -> 大 RDAC 保存的样本

**不默认两者含有同一份采样热噪声**：开关热噪声各自独立；
驱动器噪声若开启，则作为 t1/t2 上的同一实现加在共享输入上。

单位契约：全部时间量 [s]，电压量 [V]；dither_code [RDAC 单位当量]；
c_active [F]。噪声全部为 RMS 高斯（事件级实现，非谱密度）。

参数来源分级：
    sigma_sampling（kT/C）   [披露-推导]（20.5 pF -> 20.1 µV，χ=2 差分口径）
    sigma_sadc_sampling      [假设]（c_sadc=1 pF，PPT 未给量化器电容）
    driver_noise_rms         [假设]（默认 0；论文 SNR<100kHz 含驱动 ~0.3dB）
    dither_amplitude/units   [假设]（量程取值，机制见 [10]）

契约与不变量：
    * 同一 n_R 实现贯穿该样本全部后续处理（三条底线③的"事件"语义在这里）；
    * dither 三要素（注入/码修改/数字扣除）的配对责任在 SampleBatch 字段，
      缺一不可（DitherState docstring）；
    * dither_changes_in_window=True 为显式 NotImplemented——开放问题不许
      静默走错口径。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from .config import K_B, TEMP_K, Config


@dataclass
class SampleBatch:
    """一次采样的全部事件级实现（不可变语义：下游只读，改写仅限 sampling_dither_injection 的注入重标定——它必须同步改 x_rdac 与 dither）。

    字段单位契约：全部 ``(N,)`` float64 [V]，除 t1/t2 [s]、dither_code
    [RDAC 单位当量]；x1_clean/x2_clean 为 ``(N,)`` float64 [V]（None=未保留）。

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
        x1_clean: 驱动器噪声加入**之前**的 x1 [V]；None=未保留
            （driver_noise_rms=0 时与 x1 等同）。度量"相对干净输入的误差"
            必须用此项，否则 1 mV 驱动器噪声会伪装成 1 µV 内部误差（A07.6）。
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
    # 驱动器噪声**加入之前**的输入值（外部审计 A07.6）。
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
) -> SampleBatch:
    """按采样事件生成噪声；同一个 n_R 实现贯穿该样本的全部后续处理。

    **kT/C 的电容口径（v3 审计修正）**：
    采样噪声必须绑定**本次样本实际使用的 slice 集合**：

        C_active[n] = sum_{j in S[n]} sum_k C_{j,k}

    没有参与本次采样的备用 slice 不能替这个样本降低 kT/C。
    旧版用整池 chip.C_total_true（18 slice = 46.1 pF），导致：
      - 噪声被低估 20.10 -> 13.39 uV（约 3.5 dB）；
      - 增加**未使用**的备用 slice，噪声竟然继续下降（16 slice: 14.26 uV
        -> 32 slice: 10.08 uV）—— 物理上不成立。
    c_active 传入时逐样本使用；缺省退回名义活跃电容（算法开发用）。

    Args:
        cfg: 仿真配置（Config）；相关字段来源分级见 config.PARAM_GRADES
            （kT/C 电容口径见 sigma_sampling；sigma_sadc_sampling 为 [假设]）。
        input_fn: 可调用 ``t -> x``（t [s]，返回 [V]）。
        n_samples: 样本数 [无量纲]。
        rng: 随机源（采样热噪声 + dither 抽取）。
        dither_rng: 可选独立随机源；给定时 dither 与其余噪声解耦抽取。
        chip: 虚拟芯片（用于 dither 掩码失配与兼容路径的 c_active 取值）。
        c_active: 可选 ``(N,)`` float64 逐样本活跃电容 [F]
            （kT/C 绑定真实采样集合；缺省退回名义活跃电容）。

    Returns:
        SampleBatch（字段单位见 SampleBatch 的 docstring）。

    Side effects:
        消耗 rng（及 dither_rng，若给定）的随机抽取；不改传入的 cfg/chip。
    """
    n = np.arange(n_samples)
    t1 = n / cfg.fs
    dt = cfg.ktc_dt()
    t2 = t1 + dt

    x1 = np.asarray(input_fn(t1), dtype=float)
    x2 = np.asarray(input_fn(t2), dtype=float)
    dx = x2 - x1

    # 驱动器噪声加入**之前**的干净值 —— 只在有驱动器噪声时才需要保留副本
    # （A07.6：没有它，"相对干净输入的误差"无法与 "ADC 内部误差" 区分）。
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

    # dither：两种物理实现，代价形式完全不同
    dither = np.zeros(n_samples)  # 加到采样值上的电压（数字端已知）
    dither_code = np.zeros(n_samples)  # 码域配对量（单位 = RDAC 单位）
    if cfg.dither_mode == "analog":
        # 输入注入：直接从 ±V_FS 里扣量程
        drng = dither_rng if dither_rng is not None else rng
        amp = cfg.dither_amplitude_lsb1 * cfg.delta1
        dither = drng.uniform(-amp, amp, n_samples)
    elif cfg.dither_mode == "quantizer":
        # 量化器侧 dither（论文："dither range is enhanced by 2b when
        # transferred from the quantizer to the RDAC"，stage21）：
        # d_Q 加在 **SADC 决策通路**，RDAC 保存通路不含 dither。
        # 粗码在 x+d_Q 上决策 -> 数字侧把 d_Q 转移给 RDAC 码（d_u 在
        # sim_split/pipeline 里按 cfg.dither_quant_transfer 的粒度取整），
        # 余项 d_Q − round(d_Q) 落进残差、由 ADC2 在其窗口内吸收。
        # dither_code 语义（本模式）= d_Q/Δ1 [Δ1 当量]（量化器粒度的转移值，
        # 仅供诊断；真正写进开关码的取整在主循环做，因为粒度依赖拓扑 step0）。
        drng = dither_rng if dither_rng is not None else rng
        amp = cfg.dither_amplitude_lsb1 * cfg.delta1
        dither = drng.uniform(-amp, amp, n_samples)
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
        dcode = drng.uniform(-D, D, n_samples)
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
    # α：采样态 dither 带来的**恒定**信号衰减（analog 模式为 1）。
    #
    # 两条采样通路的分工（[10] 2.6 整数 dither）：
    #   SADC 侧  : 只看 α·x（不含 dither）→ 粗码 c1 由信号决定
    #   RDAC 侧  : 保存 α·x + d，DAC 码 = c1 + d  → 两项在残差里相消
    # 若把 dither 也加进 SADC，粗码就已经被扰动，DAC 再加一次 d 会导致
    # dither 被计入两次（实测残差里残留 −d·step，SNDR 崩到 50 dB）。
    #
    # 另外 α 必须**同时**作用于两条通路：若粗码基于 x 而残差保存 αx，
    # 二者之差 (1−α)x 会直接吃掉残差余量（α=0.969 时约 90 mV ≈ Δ1），
    # 这正是专利 [09] 1.5 强调"两条采样通路响应必须匹配"的原因。
    if cfg.dither_mode == "analog":
        # 输入注入：dither 是真实加到输入端的电压，两条采样通路**都会**看到它
        # —— 已经含在 u1 里，sadc_dither 必须为 0（重入即 x_sadc = x+2d，
        # 首版教训：粗码被双倍扰动 -> analog 校准联调 G_hat 减半）。
        u1 = x1 + dither
        rdac_dither: float | np.ndarray = 0.0
        sadc_dither: float | np.ndarray = 0.0
    elif cfg.dither_mode == "quantizer":
        # 量化器侧 dither：只进 SADC 决策通路，RDAC 保存通路保持干净
        # （残差 = x − vD(x+d_Q 取整)，余项由 ADC2 窗口吸收，见 stage21）。
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

    Timing skew is proportional to ``dx/dt``, so an input that can supply an
    analytic slope removes the last numerical approximation from that path.
    A plain callable is still accepted everywhere; :func:`input_derivative`
    falls back to the spectral estimate when ``.derivative`` is absent.
    """

    def __init__(self, fn, derivative, *, harmonics=None):
        """绑定波形与其解析导数。

        Args:
            fn:         ``t -> x(t)`` 波形，[V]。
            derivative: ``t -> dx/dt``，[V/s]，须与 ``fn`` 精确对应。
            harmonics: Optional (Hz, complex V) real-phasor terms for exact
                continuous RC integration; not inferred from sampled data.
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
        amp:   峰值 [V]（惯用 0.7·v_fs / 0.9·v_fs，留 dither/失配余量）。
        fin:   频率 [Hz]；正式动态指标必须用 _coherent_fin 的相干频率
               （非相干 + 窗函数会把泄漏拟合成假杂散，SFDR 陷阱见工作记忆）。
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
    """Uniform-grid derivative via the FFT (exact for band-limited records).

    Why not ``np.gradient``
    -----------------------
    A central difference has amplitude response
    ``sin(2*pi*f/fs) / (2*pi*f/fs)``. At f = fs/2 that factor is **0.0524**
    -- the derivative is underestimated by ~25.6 dB, so any effect that is
    proportional to ``dx/dt`` (interleaving timing skew is exactly that) is
    silently suppressed near Nyquist. An external audit measured this on v6.1
    (finding A07.3) and warned that low-frequency skew results must not be
    extrapolated.

    The spectral derivative has unit response across [0, fs/2] and is exact
    for a band-limited, periodically-sampled record. It is the fallback when
    the input function cannot supply an analytic derivative.

    Args:
        x:  ``(N,)`` samples on a uniform grid.
        dt: Sample spacing [s].

    Returns:
        ``(N,)`` derivative estimate, same units as ``x``/``dt``.
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

    1. **Analytic.** ``sine_input`` / ``dc_input`` attach a ``.derivative``
       attribute; a general callable may do the same. Exact at any ``fin``,
       including near Nyquist.
    2. **Spectral.** Otherwise :func:`spectral_derivative` on the uniform
       grid -- unit response up to ``fs/2``.

    ``np.gradient`` is deliberately **not** used: see
    :func:`spectral_derivative` for the 25.6 dB near-Nyquist bias that made
    it unacceptable (audit A07.3).

    Args:
        input_fn: any callable ``t -> x``; may carry ``.derivative``.
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
