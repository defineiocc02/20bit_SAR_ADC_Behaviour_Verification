"""metrics.py -- 频谱、均值误差、条件方差、溢出统计。

静态测试拆成两项，**必须一起看**：

    mu_e(x)    = E[x_hat | x] - x        平均误差（DEM 能把这条曲线拉直）
    sigma_e(x) = sqrt(Var(x_hat | x))    输出离散程度（DEM 可能让它变大）

平均误差曲线不能直接冒充严格码密度法得到的 DNL；v1 不跑完整 20 bit 码密度测试。
单位契约：频谱 dBFS（相对 2·v_fs 满幅正弦 RMS）；INL [LSB@20b]；误差 [V/µV]。
适用域：SFDR 必须**扣除基波后全谱搜索**（保护带 LSQ 会把窗泄漏拟合成
-25 dBc 假杂散）；静态 INL 有估计噪声底 σ_e/√rep，rep≥384 才可信。

"""

from __future__ import annotations

import numpy as np

# Minimum bin index of the fundamental below which FFT harmonic extraction
# is considered unreliable: the 2nd harmonic then sits inside the window main
# lobe, so the reported THD is window leakage rather than a real tone.
# 16 bins is conservative; corresponds to ~40 MHz / 16 / N, i.e. ~24 kHz at
# N = 16384. Below this frequency the function returns NaN for THD and harm-
# SFDR with a "harmonics_unreliable" marker instead of a plausible-looking
# number. Finding B11 from the v6.1 audit.
_MIN_FUND_BIN_FOR_HARMONICS = 16


def _record(x, fs):
    if np.iscomplexobj(x):
        raise ValueError("one-sided ADC spectrum requires real samples")
    data = np.asarray(x, dtype=float)
    if data.ndim != 1 or data.size < 4 or np.any(~np.isfinite(data)):
        raise ValueError("spectral analysis needs at least four finite real samples")
    if not np.isfinite(fs) or fs <= 0:
        raise ValueError("sample rate must be finite and positive")
    return data


def _window(n, name):
    if name == "boxcar":
        return np.ones(n)
    if name == "hann":
        return np.hanning(n)
    if name == "blackman":
        return np.blackman(n)
    if name == "blackmanharris":
        return _bh4(n)
    raise ValueError("window must be boxcar, hann, blackman or blackmanharris")


def power_spectral_density(x, fs: float, *, window="blackmanharris", remove_mean=True) -> dict:
    """Return a one-sided density [V²/Hz], distinct from tone amplitude [V].

    Density is |FFT(w*x)|²/(fs*sum(w²)); interior positive-frequency bins
    receive a factor of two. DC and an even-record Nyquist bin do not.
    Summing PSD*df equals sum(w²*x²)/sum(w²), including endpoints. ENBW
    is fs*sum(w²)/sum(w)² and does not get applied a second time.
    """
    data = _record(x, fs)
    n = len(data)
    w = _window(n, window)
    centered = data - data.mean() if remove_mean else data
    density = np.abs(np.fft.rfft(w * centered)) ** 2 / (fs * np.dot(w, w))
    density[1 : -1 if n % 2 == 0 else None] *= 2
    return {
        "frequency_hz": np.fft.rfftfreq(n, 1 / fs),
        "density_v2_hz": density,
        "df_hz": fs / n,
        "enbw_hz": float(fs * np.dot(w, w) / w.sum() ** 2),
        "coherent_gain": float(w.mean()),
        "window": window,
        "mean_removed": bool(remove_mean),
        "integrated_power_v2": float(density.sum() * fs / n),
    }


def integrate_noise_band(spectrum: dict, low_hz: float, high_hz: float) -> dict:
    """Sum PSD times bin width over bins whose centers lie in [low,high].

    Reports actual included bin centers. Bands finer than the record resolution
    are not silently interpolated. Input should be a residual/noise PSD if a
    noise-only result is intended; signal and harmonics are not auto-excluded.
    """
    f = np.asarray(spectrum["frequency_hz"])
    p = np.asarray(spectrum["density_v2_hz"])
    df = spectrum["df_hz"]
    if not (np.isfinite(low_hz) and np.isfinite(high_hz) and 0 <= low_hz <= high_hz <= f[-1]):
        raise ValueError("noise band must lie inside the sampled frequency grid")
    mask = (f >= low_hz) & (f <= high_hz)
    if not np.any(mask):
        raise ValueError("requested band contains no resolved FFT bin")
    power = float(p[mask].sum() * df)
    return {
        "power_v2": power,
        "rms_v": float(np.sqrt(power)),
        "bins": int(mask.sum()),
        "first_center_hz": float(f[mask][0]),
        "last_center_hz": float(f[mask][-1]),
        "df_hz": float(df),
    }


def _harmonic_design(n: int, fs: float, fin: float, nharm: int):
    t = np.arange(n) / fs
    cols = [np.ones(n)]
    used: list[float] = []
    dropped = []
    tolerance = (fs / n) * 1e-7
    for h in range(1, nharm + 1):
        alias = abs((h * fin + fs / 2) % fs - fs / 2)
        if alias <= tolerance:
            dropped.append((h, "DC"))
            continue
        if any(abs(alias - old) <= tolerance for old in used):
            dropped.append((h, "aliased duplicate"))
            continue
        used.append(alias)
        if abs(alias - fs / 2) <= tolerance:
            cols.append(np.cos(np.pi * np.arange(n)))
        else:
            cols += [np.sin(2 * np.pi * alias * t), np.cos(2 * np.pi * alias * t)]
    return np.stack(cols, axis=1), dropped


# ------------------------------------------------------------------ 频谱
def _lsq_amp(x: np.ndarray, freqs: list[float], fs: float) -> list[float]:
    """对**已知频率**集合做联合最小二乘，返回各成分幅度。

    用于近端杂散：当 spur 落在窗主瓣/保护带内时，频域幅值被窗泄漏污染，
    无法从 FFT 读数。但基波与 spur 的频率都精确已知，可以在时域做
    [1, sin f1, cos f1, sin f2, cos f2, ...] 联合拟合直接分离幅度 ——
    相干整数 bin 下各列离散正交（完美分离）；非相干时仍可解，
    只是两频率过近时条件数变差（记录长度内相位差 <~2 rad 时不可分）。

    Args:
        x: 实数信号数组（时域样本） [V]。
        freqs: 已知频率列表 [Hz]；每个频率拆成 sin/cos 两列参与拟合。
        fs: 采样率 [Hz]。

    Returns:
        各频率成分 0-peak 幅度列表 [V]，顺序与 freqs 一一对应。
    """
    n = x.size
    t = np.arange(n) / fs
    cols = [np.ones(n)]
    for f in freqs:
        cols += [np.sin(2 * np.pi * f * t), np.cos(2 * np.pi * f * t)]
    A = np.stack(cols, axis=1)
    c, *_ = np.linalg.lstsq(A, x, rcond=None)
    return [float(np.hypot(c[1 + 2 * i], c[2 + 2 * i])) for i in range(len(freqs))]


def _bh4(n: int) -> np.ndarray:
    """4 项 Blackman-Harris 窗（numpy 2.x 已移除 np.blackmanharris）。

    Args:
        n: 窗长（样本数） [无量纲]。

    Returns:
        (n,) 窗系数数组 [无量纲]；峰值约 1，相干增益约 0.5（用于 FFT
        幅值归一化时除以 sum(w)/2）。
    """
    a = (0.35875, 0.48829, 0.14128, 0.01168)
    k = np.arange(n)
    return (
        a[0]
        - a[1] * np.cos(2 * np.pi * k / (n - 1))
        + a[2] * np.cos(4 * np.pi * k / (n - 1))
        - a[3] * np.cos(6 * np.pi * k / (n - 1))
    )


def _estimate_fund_freq(xc: np.ndarray, fs: float) -> float:
    """基波频率估计：窗 FFT 粗定位 + DTFT 幅度黄金分割细化。

    必须精确到 ~0.01 bin。后续要用 LSQ 从时域扣除基波，频率误差 0.5 bin
    会在残余里留下约 −40 dBc 的扣除残差，直接被当成杂散误报。

    Args:
        xc: 去均值后的实数信号 [V]。
        fs: 采样率 [Hz]。

    Returns:
        基波频率估计值 [Hz]，精度约 1e-3 bin（记录长度内相位差足够区分）。
    """
    n = xc.size
    w = _bh4(n)
    X = np.abs(np.fft.rfft(xc * w))
    X[0] = 0.0
    k0 = int(np.argmax(X))
    f0 = k0 * fs / n
    df = fs / n
    t = np.arange(n) / fs

    def amp_neg(f):
        """DTFT 幅度取负：黄金分割搜索的极小化目标（未归一化，只需相对大小）。"""
        return -abs(np.sum(xc * np.exp(-2j * np.pi * f * t)))

    # 黄金分割搜索 f0 ± 1 bin，迭代到 1e-3 bin
    lo, hi = f0 - df, f0 + df
    gr = (np.sqrt(5.0) - 1.0) / 2.0
    c, d = hi - gr * (hi - lo), lo + gr * (hi - lo)
    for _ in range(60):
        if amp_neg(c) < amp_neg(d):
            hi = d
        else:
            lo = c
        c, d = hi - gr * (hi - lo), lo + gr * (hi - lo)
        if hi - lo < 1e-3 * df:
            break
    return 0.5 * (lo + hi)


def _fft_sfdr(
    x: np.ndarray, fs: float, guard_bins: int | None = None, fin: float | None = None
) -> tuple[float, float]:
    """全谱 SFDR：**先扣除基波，再对残余做加窗全谱搜峰**。

    ==========================================================================
    v5 审计修正 —— 这里原来有一个会把 SFDR 钳死在 25 dB 的静默 bug
    ==========================================================================
    旧实现用「保护区内逐 bin 的 [基波, 怀疑杂散] 联合 LSQ」来分离主瓣内的
    近端杂散。它只在**相干采样**（fin 恰为整数 bin）下成立 —— 此时设计矩阵
    离散正交。非相干采样下两列频率间隔仅 3~8 bin，记录长度内相位差很小，
    设计矩阵接近奇异，拟合出的"杂散幅度"实际是在拟合窗函数的主瓣滚降。

    实证（v5 自查）：**零噪声、零杂散的纯正弦被报出 SFDR = 25.05 dB**，
    且拟合幅度随离基波距离单调衰减（-8bin:34.4 -> +3bin:25.1 -> +8bin:33.8 dBc），
    形状与 BH4 主瓣完全吻合 —— 分离根本没有发生。
    旧自检（stage10.4 / stage11.5）全部用相干 fin，所以从未触发；
    正式流程也全程用 _coherent_fin()，故已发布报告未受污染。
    但这是**静默错误**：任何非相干输入（真实 ADC 测试的常见做法）都会跌到
    25 dB 且不报错。

    修正原理：窗泄漏来自**信号本身**（有限长截断 + 加窗），不是杂散。
    用精确频率的 LSQ 把基波从时域扣掉后，残余里就不再有主瓣泄漏，
    近端 bin 与远端 bin 因此变得一视同仁，直接全谱搜峰即可。
    LSQ 扣除精度与频率是否整数 bin 无关，故本方法对非相干输入同样成立。

    fin 已知时应直接传入（sine_fit_metrics 就是这么做的）；
    fin=None 时内部用 DTFT 峰值细化估计到 ~1e-3 bin。

    Args:
        x: 实数信号数组（已含噪声/杂散） [V]。
        fs: 采样率 [Hz]。
        guard_bins: 基波附近置零的半宽 [bin]；None 时 fin 已知取 0、
            未知取 3（吸收频率估计残差，并标注近端适用限制）。
        fin: 已知基波频率 [Hz]；None 则内部用 DTFT 峰值细化估计。

    Returns:
        (SFDR_dB, spur_Hz)：扣除基波后全谱搜峰得到的杂散抑制比 [dB] 与
        杂散频率 [Hz]。无杂散（残余被基波主导）时返回 (inf, 0.0)。
    Side effects: 无（纯函数，不改 x 也不存内部状态）。
    """
    x0 = np.asarray(x, dtype=float)
    n = x0.size
    xc = x0 - x0.mean()
    t = np.arange(n) / fs

    # ---- 1) 基波频率：已知优先，否则估计 ----
    # v5 第二轮审计 §6：fin 精确已知时，LSQ 扣除在任意 bin 距离上都近完美，
    # 保护区只保留基波 bin 本身 —— ±3 bin 的旧屏蔽会把 +1/+2/+3 bin 的
    # -80 dBc 真杂散一起抹掉（实测漏报成 115.8/94.1/83.3 dB）。
    # fin 未知（内部估计）时保留 ±3 bin 吸收估计误差产生的扣除残差，
    # 并在报告里注明该口径的近端适用限制。
    fin_known = fin is not None
    f_fund = float(fin) if fin is not None else _estimate_fund_freq(xc, fs)
    if guard_bins is None:
        guard_bins = 0 if fin_known else 3

    # ---- 2) LSQ 扣除直流 + 基波，得到基波 0-peak 幅度与残余 ----
    D = np.stack(
        [np.ones(n), np.sin(2 * np.pi * f_fund * t), np.cos(2 * np.pi * f_fund * t)], axis=1
    )
    c, *_ = np.linalg.lstsq(D, x0, rcond=None)
    A_fund = float(np.hypot(c[1], c[2]))
    if A_fund <= 0:
        return np.inf, 0.0
    resid = x0 - D @ c

    # ---- 3) 残余加窗 FFT，全谱搜峰（近端与远端同口径）----
    w = _bh4(n)
    mag = np.abs(np.fft.rfft(resid * w)) / (np.sum(w) / 2.0)
    if n % 2 == 0:
        # Equivalent sine peak for an RMS-power spur comparison.
        mag[-1] /= np.sqrt(2.0)
    mag[0] = 0.0
    k_fund = int(round(f_fund * n / fs))
    k_fund = max(1, min(k_fund, mag.size - 1))
    lo = max(1, k_fund - guard_bins)
    hi = min(mag.size, k_fund + guard_bins + 1)
    mag[lo:hi] = 0.0
    if not mag.any():
        return np.inf, 0.0

    k_spur = int(np.argmax(mag))
    if mag[k_spur] <= 0:
        return np.inf, 0.0
    return float(20 * np.log10(A_fund / mag[k_spur])), float(k_spur * fs / n)


def _band_max(X: np.ndarray, bin_idx: int, guard: int) -> float:
    """Peak magnitude in a +/- guard-bin window, with the window clamped.

    Clamping matters: at low input frequencies ``fund_bin`` can be 1, and the
    unclamped slice ``X[1-4 : 1+4+1]`` wraps to an empty array, which raised
    ``ValueError: zero-size array to reduction operation maximum`` for
    ``f_in <= 5 kHz``. Reported by independent review (finding B11).

    Args:
        X: Magnitude spectrum, ``(n // 2 + 1,)``.
        bin_idx: Centre bin.
        guard: Half-width of the window, in bins.

    Returns:
        Maximum magnitude in the clamped window, or 0.0 if the spectrum is
        empty.
    """
    if X.size == 0:
        return 0.0
    lo = max(int(bin_idx) - int(guard), 0)
    hi = min(int(bin_idx) + int(guard) + 1, X.size)
    if hi <= lo:
        return 0.0
    return float(X[lo:hi].max())


def _fft_harmonics(
    x: np.ndarray, fs: float, fin: float, nharm: int = 12, guard_bins: int = 4
) -> dict:
    """FFT 口径的谐波提取（相干采样），**折叠频率 + 去重**。

    v3 审计修正：高频输入时部分谐波混叠后与基波或其他谐波重合
    （fs=40 MHz、fin=5 MHz 时 7/9 次谐波折回 5 MHz 与基波重合）。
    时域最小二乘在这类频点上设计矩阵共线，会把基波能量重复计入，
    实测纯 5 MHz 正弦被报出 THD=-6.5 dB 的假谐波。
    规则：混叠后落在基波 guard 带内的谐波丢弃；与已计谐波重合的丢弃。

    Args:
        x: 实数信号数组（相干采样前提） [V]。
        fs: 采样率 [Hz]。
        fin: 基波频率 [Hz]，必须相干（整数 bin）否则混叠去重失效。
        nharm: 考察的最高谐波次数 [无量纲]，默认 12。
        guard_bins: 谐波/基波保护的半宽 [bin]，默认 4。

    Returns:
        字典，字段与单位：
            A1 [V]           基波 0-peak 幅度；
            harmonics        谐波列表 [(次数, 幅度 V)]；
            dropped          被丢弃谐波列表 [(次数, 原因)]；
            THD_dB [dB]      谐波口径总失真（不含噪声）；
            harm_SFDR_dB [dB] 谐波口径杂散抑制比。
    Side effects: 无（纯函数）。
    """
    n = x.size
    a = (0.35875, 0.48829, 0.14128, 0.01168)
    k = np.arange(n)
    w = (
        a[0]
        - a[1] * np.cos(2 * np.pi * k / (n - 1))
        + a[2] * np.cos(4 * np.pi * k / (n - 1))
        - a[3] * np.cos(6 * np.pi * k / (n - 1))
    )
    X = np.abs(np.fft.rfft((x - x.mean()) * w)) / (np.sum(w) / 2.0)
    fund_bin = int(round(fin * n / fs))
    fund_bin = max(1, min(fund_bin, X.size - 1))
    A1 = _band_max(X, fund_bin, guard_bins)

    used_bins = [(fund_bin, "fund")]
    harms, dropped, rms_harmonics = [], [], []
    for h in range(2, nharm + 1):
        f_alias = h * fin
        # 折叠到 [0, fs/2]
        f_alias = f_alias % fs
        if f_alias > fs / 2:
            f_alias = fs - f_alias
        b = int(round(f_alias * n / fs))
        nyquist = n % 2 == 0 and abs(f_alias - fs / 2) < fs / n * 1e-7
        if b <= guard_bins or (b >= X.size - guard_bins and not nyquist):
            dropped.append((h, "DC/Nyquist"))
            continue
        # 与基波重合（±guard） -> 丢弃
        if abs(b - fund_bin) <= guard_bins:
            dropped.append((h, "与基波重合"))
            continue
        # 与已计谐波重合 -> 丢弃（能量只计一次）
        if any(abs(b - ub) <= guard_bins for ub, _ in used_bins):
            dropped.append((h, "与低次谐波重合"))
            continue
        used_bins.append((b, f"h{h}"))
        amplitude = float(X[-1] / 2) if nyquist else _band_max(X, b, guard_bins)
        harms.append((h, amplitude))
        rms_harmonics.append(amplitude if nyquist else amplitude / np.sqrt(2.0))
    thd = (
        20 * np.log10(np.linalg.norm(rms_harmonics) / (A1 / np.sqrt(2.0)))
        if harms and A1 > 0
        else -np.inf
    )
    sfdr_h = (
        20 * np.log10((A1 / np.sqrt(2.0)) / max(rms_harmonics))
        if harms and max(a2 for _, a2 in harms) > 0
        else np.inf
    )
    return {
        "A1": float(A1),
        "harmonics": harms,
        "dropped": dropped,
        "THD_dB": thd,
        "harm_SFDR_dB": sfdr_h,
    }


def sine_fit_metrics(x: np.ndarray, fs: float, fin: float, nharm: int = 12) -> dict:
    """正弦指标：SNDR/SNR 用时域最小二乘拟合；THD/SFDR 用 FFT（含混叠去重）。

    SFDR = 全谱（保护带按窗主瓣）；THD = FFT 谐波口径，混叠重合的谐波
    折叠去重（见 _fft_harmonics）。

    Args:
        x: 实数信号数组 [V]。
        fs: 采样率 [Hz]。
        fin: 基波频率 [Hz]（已知，供 _fft_sfdr 精确扣除基波）。
        nharm: 考察的最高谐波次数 [无量纲]，默认 12。

    Returns:
        指标字典，字段与单位：
            amp [V] / noise_rms [V] / nd_rms [V]   基波、噪声、噪声+失真 RMS；
            SNDR_dB / SNR_dB / THD_dB / SFDR_dB / harm_SFDR_dB [dB]；
            spur_Hz [Hz]；ENOB [bit]；
            NSD_V_rtHz [V/√Hz]   噪声谱密度；
            harmonics [(次数, 幅度 V) 数组]；harmonics_dropped [list]；
            harmonics_reliable [bool]   基波 bin < 16 时为 False（THD 置 nan）。
    Side effects: 无（纯函数）。
    """
    x = _record(x, fs)
    if not np.isfinite(fin) or not 0 < fin < fs / 2:
        raise ValueError("sine frequency must be strictly between DC and Nyquist")
    if type(nharm) is not int or nharm < 1:
        raise ValueError("nharm must be a positive integer")
    n = x.size
    # 只拟合基波
    D1, _ = _harmonic_design(n, fs, fin, 1)
    c1, *_ = np.linalg.lstsq(D1, x, rcond=None)
    A = float(np.hypot(c1[1], c1[2]))
    resid_nd = x - D1 @ c1  # 含谐波 + 噪声
    r_nd = float(np.sqrt(np.mean(resid_nd**2)))

    # 含谐波（低频输入、无混叠时仍最稳）
    Dh, fit_dropped = _harmonic_design(n, fs, fin, nharm)
    ch, _, fit_rank, singular = np.linalg.lstsq(Dh, x, rcond=1e-10)
    resid_n = x - Dh @ ch  # 仅噪声
    r_n = float(np.sqrt(np.mean(resid_n**2)))

    a_rms = A / np.sqrt(2.0)
    sndr = 20 * np.log10(a_rms / r_nd) if r_nd > 0 else np.inf
    snr = 20 * np.log10(a_rms / r_n) if r_n > 0 else np.inf

    fft_h = _fft_harmonics(x, fs, fin, nharm)
    thd = fft_h["THD_dB"]
    sfdr_h = fft_h["harm_SFDR_dB"]
    sfdr, spur_hz = _fft_sfdr(x, fs, fin=fin)

    # ---- 可靠性标记 ----------------------------------------------------
    # 记录长度给不出足够低的频率分辨率时，二次谐波落在基波窗主瓣内，
    # 谐波口径的 THD/harm_SFDR 会把基波泄漏当成谐波（fin=5 kHz、N=16384
    # 时实测报出 THD=-13 dB 的假谐波）。这里不静默返回一个好看的数，
    # 而是显式置 nan 并标记不可靠 —— 与审计"有数字不等于有证据"一致。
    fund_bin = int(round(fin * n / fs))
    harmonics_reliable = fund_bin >= _MIN_FUND_BIN_FOR_HARMONICS
    if not harmonics_reliable:
        thd = float("nan")
        sfdr_h = float("nan")

    return {
        "amp": A,
        "noise_rms": r_n,
        "nd_rms": r_nd,
        "SNDR_dB": sndr,
        "SNR_dB": snr,
        "THD_dB": thd,
        "SFDR_dB": sfdr,  # 全谱（默认口径）
        "harm_SFDR_dB": sfdr_h,  # 仅谐波口径（对照）
        "spur_Hz": spur_hz,
        "ENOB": (sndr - 1.76) / 6.02,
        "NSD_V_rtHz": r_n / np.sqrt(fs / 2.0),
        "harmonics": np.array([a2 for _, a2 in fft_h["harmonics"]]),
        "harmonics_dropped": fft_h["dropped"],
        "harmonics_reliable": harmonics_reliable,
        "harmonic_fit_dropped": fit_dropped,
        "harmonic_fit_rank": int(fit_rank),
        "harmonic_fit_columns": Dh.shape[1],
        "harmonic_fit_condition": float(singular[0] / max(singular[-1], np.finfo(float).tiny)),
        "noise_fit_reliable": bool(fit_rank == Dh.shape[1] and n > fit_rank),
        "noise_degrees_of_freedom": int(n - fit_rank),
        "record_duration_s": n / fs,
        "resolution_hz": fs / n,
        "coherent": bool(abs(fin * n / fs - round(fin * n / fs)) < 1e-8),
    }


def spectrum_dbfs(
    x: np.ndarray, fs: float, v_fs: float, window: str = "blackmanharris"
) -> tuple[np.ndarray, np.ndarray]:
    """返回 (freq, dBFS) 用于画图。

    Args:
        x: 实数信号数组 [V]。
        fs: 采样率 [Hz]。
        v_fs: 差分满幅峰值 [V]，作为 0 dBFS 参考（峰值幅度 = v_fs 的正弦
            为 0 dBFS）；v_fs 来源 [推导]（2*v_fs/√2 对齐 NSD 8.8 nV/√Hz）。
        window: 窗类型，"blackman" 或 "blackmanharris"（默认 4 项 BH）。

    Returns:
        (freq, dBFS)：频率轴 [Hz] 与对应谱线 [dBFS]（按 0-peak 幅度归一到
        v_fs）。dBFS = 20·log10(mag/v_fs)，mag 为各谱线峰值幅度。
    Side effects: 无（纯函数）。
    """
    x = _record(x, fs)
    if not np.isfinite(v_fs) or v_fs <= 0:
        raise ValueError("full-scale sine peak must be finite and positive")
    n = x.size
    w = _window(n, window)
    X = np.fft.rfft((x - x.mean()) * w)
    f = np.fft.rfftfreq(n, 1 / fs)
    # 按相干增益归一到正弦峰值幅度
    mag = np.abs(X) / (np.sum(w) / 2.0)
    mag[0] *= 0.5
    if n % 2 == 0:
        mag[-1] *= 0.5
    dbfs = 20 * np.log10(np.maximum(mag / v_fs, 1e-20))
    return f, dbfs


# ------------------------------------------------------------------ 静态
def static_test(run_fn, levels: np.ndarray, repeats: int) -> dict:
    """run_fn(level, repeats) -> out 数组。返回每个电平的均值误差与条件标准差。

    Args:
        run_fn: 可调用 ``run_fn(level, repeats) -> out``；给定输入电平与重复
            次数，返回该电平下 ADC 输出数组 [V]。
        levels: 待测输入电平数组 [V]，范围应在 ±v_fs 内。
        repeats: 每个电平的重复采样数 [无量纲]；需 ≥ 384 才可信（σ_e/√rep
            估计噪声底，见模块头适用域）。

    Returns:
        字典，字段与单位：
            levels [V]    输入电平；
            mu_e [V]      各电平的平均误差 E[x_hat|x] - x（DEM 可拉直）；
            sigma_e [V]   条件标准差 √Var(x_hat|x)（repeats=1 时为 0）。
    Side effects: 无（纯函数；只调用 run_fn，不修改其状态）。

    Notes:
        审计教训（勿回退）：mu_e / sigma_e 必须由**逐次原始输出**直接统计，
        严禁用 mean/sigma 重采样伪造分布（历史缺陷 A07.4）——那样会抹平真实
        直方图的多峰/偏态，使静态 INL/DNL 失真。本函数不做任何重采样。
    """
    mu = np.zeros(levels.size)
    sd = np.zeros(levels.size)
    for i, lv in enumerate(levels):
        out = np.asarray(run_fn(lv, repeats), dtype=float)
        e = out - lv
        mu[i] = e.mean()
        sd[i] = e.std(ddof=1) if repeats > 1 else 0.0
    return {"levels": np.asarray(levels), "mu_e": mu, "sigma_e": sd}


def inl_from_mean_error(levels: np.ndarray, mu_e: np.ndarray, lsb: float) -> dict:
    """去掉最佳拟合直线（失调 + 增益）后的残差 = INL 类指标。

    注意：这是**平均转移曲线的非线性**，不是严格码密度法 DNL/INL。

    Args:
        levels: 输入电平数组 [V]（与 mu_e 等长）。
        mu_e: 各电平的平均误差数组 [V]（通常来自 static_test）。
        lsb: LSB 电压步长 [V]，LSB = 2·v_fs / 2**20（[推导]）。

    Returns:
        字典，字段与单位：
            inl_v [V]          去最佳拟合直线后的残差；
            inl_lsb [LSB]      以 lsb 归一化的 INL；
            inl_max_lsb [LSB]  峰值 |INL|；
            offset_v [V]       拟合失调；
            gain_err [无量纲]  拟合增益误差（直线斜率）。
    Side effects: 无（纯函数）。
    """
    p = np.polyfit(levels, mu_e, 1)
    inl = mu_e - np.polyval(p, levels)
    return {
        "inl_v": inl,
        "inl_lsb": inl / lsb,
        "inl_max_lsb": float(np.max(np.abs(inl)) / lsb),
        "offset_v": float(p[1]),
        "gain_err": float(p[0]),
    }


# ------------------------------------------------------------------ 汇总
def summarize(res, fs: float, fin: float, v_fs: float, lsb: float) -> dict:
    """把一次仿真的结果汇总成指标字典（SNDR/THD 与误差统计）。

    Args:
        res: 一次仿真的 SimResult，需含属性 out [V]、err [V]、adc2_over /
            ra_sat / ktc_sat [bool 数组]、residue [V]。
        fs: 采样率 [Hz]（[披露] 40 MS/s）。
        fin: 基波频率 [Hz]（供 sine_fit_metrics）。
        v_fs: 差分满幅峰值 [V]（[推导]，用于 NSD dBFS 归一口径）。
        lsb: LSB 电压步长 [V]（[推导]）。

    Returns:
        聚合字典，含 sine_fit_metrics 全部字段，另加：
            err_mean_V / err_rms_V [V]   残差均值/均方根；
            err_rms_LSB / err_max_LSB [LSB]   残差以 lsb 归一；
            overflow_rate_adc2 / ra / ktc [无量纲]   各支路饱和率（0..1）；
            residue_min_V / residue_max_V [V]   残差摆幅；
            NSD_dBFS_Hz [dBFS/Hz]   噪声谱密度（以 v_fs/√2 为参考）。
    Side effects: 无（纯函数，不改 res）。
    """
    m = sine_fit_metrics(res.out, fs, fin)
    m.update(
        {
            "err_mean_V": float(res.err.mean()),
            "err_rms_V": float(np.sqrt(np.mean(res.err**2))),
            "err_rms_LSB": float(np.sqrt(np.mean(res.err**2)) / lsb),
            "err_max_LSB": float(np.max(np.abs(res.err)) / lsb),
            "overflow_rate_adc2": float(res.adc2_over.mean()),
            "overflow_rate_ra": float(res.ra_sat.mean()),
            "overflow_rate_ktc": float(res.ktc_sat.mean()),
            "residue_min_V": float(res.residue.min()),
            "residue_max_V": float(res.residue.max()),
            "NSD_dBFS_Hz": float(20 * np.log10(m["NSD_V_rtHz"] / (v_fs / np.sqrt(2)))),
        }
    )
    return m
