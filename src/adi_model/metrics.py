"""ADC 输出的频谱、误差统计和条件平均转移曲线。

输入通常为某一明确节点的实数电压记录 (N,)。调用方负责选择内部浮点重构或最终整数码解码后的电压；
本模块不把两者混用，也不自动筛掉溢出样本。采样率 fs [Hz]、记录时长 N/fs [s]、
FFT 间隔 df=fs/N [Hz/bin] 相互独立。

口径：SNDR 含基波拟合后的全部残余；SNR 再扣除可分辨的指定谐波；SFDR搜索基波扣除后的最大非直流谱线；
THD 只累加指定、可分辨的谐波。幅度谱以满幅正弦峰值 v_fs [V] 为 0 dBFS，
显示的是每个 FFT bin 的相干幅度（常标 dBFS/bin），不是噪声密度。PSD 返回 [V²/Hz]，积分时乘 df；
dBc 是相对实际基波的幅度比，不能把 dBFS/bin、dBFS/Hz 和 dBc 直接互换。

静态电平测试分别报告均值误差 mu_e=E[x_hat|x]-x 与条件标准差 sigma_e；总误差 RMS 还包含均值项。
DEM 可能减小均值曲线的弯曲，同时增大 sigma_e。稀疏电平的平均曲线及去直线残差属于 INL 类指标，
不是完整码密度 INL/DNL。有限记录、谐波混叠及窗泄漏的限制通过返回标志或 NaN 暴露，详见 ADR 0015。
"""

from __future__ import annotations

import numpy as np

# 基波低于 16 bin 时，窗主瓣可能污染 FFT 谐波估计，显式标记不可靠。
# 对应阈值为 16*fs/N；fs=40 MHz、N=16384 时约 39.06 kHz。
# 阈值只保护 FFT THD/harm_SFDR，不替代时域拟合的秩与自由度检查。
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
    """计算实数电压记录的单边功率谱密度，不返回幅度谱。

    Args:
        x: (N,) 有限实数电压 [V]，N>=4；节点与是否经过码量化由调用方注明。
        fs: 正且有限的采样率 [Hz]。
        window: boxcar、hann、blackman 或 blackmanharris。
        remove_mean: 是否在加窗前去除算术均值；默认移除直流。

    Returns:
        frequency_hz、density_v2_hz 为 (N//2+1,)；分别为 bin 中心 [Hz] 与PSD [V²/Hz]。df_hz=fs/N；
        enbw_hz=fs*sum(w²)/sum(w)² 是窗等效噪声带宽；coherent_gain=mean(w) 是幅度相干增益。
        PSD=|FFT(w*x)|²/(fs*sum(w²))，内部正频率加倍，DC 和偶数 N 的Nyquist 不加倍。
        sum(PSD*df) 等于去均值后信号的窗平方加权均方值，不是任意窗下未经加权的 mean(x²)。
        不要再乘/除一次 ENBW。

    Raises:
        ValueError: 复数、非一维/过短/非有限记录，非法 fs 或未知窗。
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
    """按 bin 中心范围积分 PSD，得到指定频带的均方值和 RMS。

    Args:
        spectrum: power_spectral_density 的结果；密度 [V²/Hz]、df [Hz/bin]。
        low_hz: 包含的最低中心频率 [Hz]，须非负。
        high_hz: 包含的最高中心频率 [Hz]，须不超过返回频率轴上界。

    Returns:
        power_v2=sum(PSD*df) [V²]、rms_v=sqrt(power_v2) [V]，以及实际纳入的 bin 数和首末中心频率。
        边界不插值，不补造小于 df 的分辨率。

    Raises:
        ValueError: 非有限/越界/反向频带，或频带不含任何已分辨 bin。

    Notes:
        函数不会自动排除基波和谐波。欲报告纯噪声，须传入已按明确规则扣除信号的残余 PSD；
        全 Nyquist 与输出带内噪声应分别积分。
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
    """对已知频率做联合时域最小二乘，返回各成分峰值幅度。

    Args:
        x: (N,) 实数电压记录 [V]。
        freqs: 已知频率列表 [Hz]；每项构成一对 sin/cos 列，另有直流列。
        fs: 采样率 [Hz]。

    Returns:
        与 freqs 等长的正弦峰值幅度 [V]。

    Notes:
        频率必须来自独立已知激励或明确估计；有限记录内过近频率、DC、Nyquist 和混叠重复可能使列相关。
        本 helper 不做秩检查或频率去重，不能据此保证窗主瓣内的任意未知杂散均可分离。
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
    """生成四项 Blackman-Harris 窗，用于幅度谱和残余搜峰。

    Args:
        n: 窗长，即样本数；调用方应保证 n>=2。

    Returns:
        (n,) 无量纲窗系数。幅度相干增益是 sum(w)/n，长记录约 0.35875；
        内部正频率的正弦峰值幅度用 |FFT|/(sum(w)/2) 归一化。
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
    """先以窗 FFT 定位基波，再在相邻 bin 内细化 DTFT 峰值。

    Args:
        xc: (N,) 已去均值的实数电压 [V]；应由单一基波主导。
        fs: 采样率 [Hz]，df=fs/N 为频率网格间隔。

    Returns:
        基波频率估计 [Hz]。搜索区间宽度收敛至约 1e-3 bin；这只说明数值搜索精度，
        不是所有噪声/失真条件下的频率估计准确度保证。

    Notes:
        后续 SFDR 会在时域扣除基波。频率误差会留下扣除残差，故已知输入频率时应直接传入，
        不能把搜索精度当成近端杂散的可分辨率。
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
    """先扣除直流和基波，再对残余加窗全谱搜峰。

    Args:
        x: (N,) 已含噪声和杂散的实数电压 [V]。
        fs: 采样率 [Hz]。
        guard_bins: 残余谱中屏蔽基波 bin 两侧的半宽 [bin]。None 时已知fin 取 0，仅屏蔽基波 bin；
            未知 fin 取 3，吸收频率估计残差。
        fin: 独立已知基波频率 [Hz]；None 时内部估计。

    Returns:
        (SFDR_dB, spur_Hz)：基波与最大保留谱线的幅度比 [dB]、对应频率[Hz]。
        内部正频率按正弦峰值比较，Nyquist 换成相同 RMS 功率的正弦等效峰值。
        没有可用残余谱线时返回 (inf, 0.0)。

    Notes:
        窗泄漏来自有限记录内的基波本身，先在时域扣除可避免把主瓣滚降当作杂散。
        频率估计误差及保护带仍限制近端检测；
        保护带内谱线、同频成分、非相干杂散的栅栏损失均不能据此宣称被精确分离。纯函数，不修改输入；
        本 helper 由已检查记录的上层入口调用。
    """
    x0 = np.asarray(x, dtype=float)
    n = x0.size
    xc = x0 - x0.mean()
    t = np.arange(n) / fs

    # 已知 fin 时只屏蔽基波 bin，避免连带删除相邻真实谱线；
    # 未知 fin 时 ±3 bin 用于吸收估计残差，代价是该范围无法检出杂散。
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
        # Nyquist 只有一个实分量；换成同 RMS 功率的正弦峰值后再比较。
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
    """在谱线附近的有限 bin 范围取峰值。

    Args:
        X: (N//2+1,) 幅度谱，单位沿用调用方。
        bin_idx: 中心 bin 的整数索引。
        guard: 两侧半宽 [bin]。

    Returns:
        裁至 [0, len(X)) 后的最大幅度；空谱或空区间返回 0。

    Notes:
        下边界必须裁至零；Python 负索引会从谱尾计数，低频主瓣附近直接切片可能意外得到空数组。
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
    """用 FFT 主瓣峰值提取指定谐波，并折叠、去除不可分辨项。

    Args:
        x: (N,) 实数电压 [V]；定量幅度比较以整数 bin 相干输入为前提。
        fs: 采样率 [Hz]。
        fin: 已知基波频率 [Hz]；本 helper 本身不拒绝非相干输入。
        nharm: 考察到的最高谐波次数，无量纲。
        guard_bins: 基波/谐波主瓣保护半宽 [bin]。

    Returns:
        A1 为基波峰值 [V]；harmonics 为 (次数, 幅度 V) 列表；dropped 记录不可分辨的项。
        THD_dB 为保留谐波 RMS 合成后相对基波 RMS 的比值；harm_SFDR_dB 仅比较最大保留谐波，
        不是全谱 SFDR。Nyquist 分量仅有一个实列，其 amplitude 按实际 RMS 处理。

    Notes:
        谐波先折回 [0, fs/2]；DC/边界主瓣、与基波或已计谐波重合的项被丢弃，不能重复计功率，
        也不能据丢弃项推断器件没有该失真。非相干输入会有栅栏和泄漏误差；
        入口另用基波 bin 数给出低频可靠性标志。
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
    """对同一电压记录分别计算 SNDR、SNR、谐波失真和全谱 SFDR。

    Args:
        x: (N,) 有限实数电压 [V]，N>=4；调用方注明是内部浮点输出还是最终整数码解码值。
            函数不会剔除模拟溢出或数字裁剪样本。
        fs: 正且有限的采样率 [Hz]。
        fin: 独立已知输入基波频率 [Hz]，严格位于 (0, fs/2)。
        nharm: 正整数，时域去谐波和 FFT 谐波提取的最高次数。

    Returns:
        amp 为基波峰值 [V]。nd_rms 为仅扣直流/基波后的 RMS [V]，
        noise_rms 为再扣指定、可辨识谐波后的 RMS [V]；
        未拟合失真、杂散和估计误差仍可能留在 noise_rms 中。SNDR_dB=20*log10((amp/sqrt(2))/nd_rms)，
        SNR_dB 同理使用 noise_rms；THD_dB 使用 FFT 谐波 RMS，SFDR_dB 搜索完整残余谱，
        harm_SFDR_dB只搜谐波。ENOB=(SNDR-1.76)/6.02 是正弦等效位数，不是输出码宽。
        NSD_V_rtHz=noise_rms/sqrt(fs/2) 是全 Nyquist 白噪声等效密度[V/sqrt(Hz)]，不是逐频 PSD，
        也不证明噪声平坦。返回混叠丢弃项、拟合秩/条件数/自由度、N/fs 时长、fs/N 分辨率和相干标记。
        基波 bin<16 时 FFT THD/harm_SFDR 置 NaN；SNR 的noise_fit_reliable 则只说明列秩和自由度，
        不保证信号/噪声模型正确。

    Raises:
        ValueError: 非法实数记录、采样率、基波频率或谐波次数。

    Notes:
        谐波与基波同频或彼此混叠时无法分离；相干标记仅报告，不拒绝非相干输入。
        FFT 谐波幅值仍受窗泄漏与栅栏损失限制。
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
    # 混叠后重复的谐波只拟合一次；Nyquist 只使用 cos 实列，避免虚假秩亏。
    Dh, fit_dropped = _harmonic_design(n, fs, fin, nharm)
    ch, _, fit_rank, singular = np.linalg.lstsq(Dh, x, rcond=1e-10)
    resid_n = x - Dh @ ch  # 扣除指定可辨识谐波后的残余，可能仍含其他失真/杂散。
    r_n = float(np.sqrt(np.mean(resid_n**2)))

    a_rms = A / np.sqrt(2.0)
    sndr = 20 * np.log10(a_rms / r_nd) if r_nd > 0 else np.inf
    snr = 20 * np.log10(a_rms / r_n) if r_n > 0 else np.inf

    fft_h = _fft_harmonics(x, fs, fin, nharm)
    thd = fft_h["THD_dB"]
    sfdr_h = fft_h["harm_SFDR_dB"]
    sfdr, spur_hz = _fft_sfdr(x, fs, fin=fin)

    # 有限记录不能分辨低频主瓣中的谐波时，明确置 NaN，避免把泄漏当失真。
    # 此标志不保证高于阈值后的所有非相干或混叠谐波均可准确提取。
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
    """返回按满幅正弦峰值归一化的单边幅度谱，供绘图使用。

    Args:
        x: (N,) 有限实数电压 [V]，N>=4；不自动从整数码换算电压。
        fs: 正且有限的采样率 [Hz]。
        v_fs: 差分满幅正弦峰值 [V]；其 RMS=v_fs/sqrt(2)，峰值 v_fs的相干正弦为 0 dBFS。
            这里不是 2*v_fs 的峰峰值。
        window: boxcar、hann、blackman 或 blackmanharris。

    Returns:
        两个 (N//2+1,) 数组：(bin 中心频率 [Hz], 幅度 [dBFS])。内部正频率 mag=2*|FFT|/sum(w)，
        DC/偶数 N 的 Nyquist 不乘二；dBFS=20*log10(mag/v_fs)。
        图可标 dBFS/bin 以表明是离散 bin 幅度。此函数始终先去均值，所以直流线不用于测失调。

    Raises:
        ValueError: 非法记录、fs、v_fs 或未知窗。

    Notes:
        这是相干增益归一的幅度谱；噪声底随 N 和窗 ENBW 变化，不能当作 dBFS/Hz。
        需密度时用 power_spectral_density；需 dBc 时还应以实际基波幅度为参考。
        非相干谱线峰值存在栅栏损失。
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
    """按逐次原始输出统计各电平的均值误差和条件标准差。

    Args:
        run_fn: run_fn(level, repeats)->out，返回该外部输入电平下的repeats 个 ADC 输出电压 [V]；
            调用方决定配置和是否用最终整数码。
        levels: (L,) 外部输入电平 [V]，应在被测配置的有效范围内。
        repeats: 每电平重复次数，须由噪声预算和 DEM 状态覆盖选择。

    Returns:
        levels、mu_e、sigma_e 均为 (L,)；mu_e=mean(out-level) [V]，
        sigma_e 是 ddof=1 的条件标准差 [V]，repeats=1 时为 0。
        均值的估计噪声量级为 sigma_e/sqrt(repeats)，不是确定性 INL。

    Side effects:
        调用 run_fn，可能推进该函数持有的物理池、估计器或 RNG 状态；本函数自身不保存状态，
        也不从均值/方差重新合成输出分布。

    Notes:
        均值、标准差与 RMS 不可互换。稀疏电平统计没有覆盖全部码宽，
        不能作为严格码密度 DNL/INL 或完整芯片线性度证明。
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
    """从平均误差曲线去除最佳拟合直线，返回 INL 类残差。

    Args:
        levels: (L,) 输入电平 [V]，应有足够不同电平供直线拟合。
        mu_e: (L,) 对应平均误差 [V]，通常来自 static_test。
        lsb: 正的归一化步长 [V/LSB]。目标 20 位时 LSB20=2*v_fs/2**20；
            不是一级量化步长 delta1 或 RDAC 单位步长 nominal_rdac_step。

    Returns:
        inl_v 为去掉 offset+gain*levels 后的残差 [V]；inl_lsb 和inl_max_lsb 以传入 lsb 归一；
        offset_v [V]、gain_err 无量纲。该名称不代表已完成逐码转换边界或码密度测试。
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
    """汇总指定电压输出的频谱、误差和模拟支路饱和统计。

    Args:
        res: SimResult；out、err 为 (N,) 输入等效电压 [V]；residue 为RDAC 顶板残差 [V]。
            adc2_over/ra_sat/ktc_sat 为各模拟支路标志。当前使用 res.out 浮点重构，
            不会隐式调用 to_codes()。
        fs: 此记录实际采样率 [Hz]，不在函数内固定为 40 MS/s。
        fin: 已知基波频率 [Hz]，供 sine_fit_metrics 使用。
        v_fs: 满幅正弦峰值 [V]，用于白噪声等效 NSD 的 dBFS/Hz 参考。
        lsb: 误差归一化步长 [V/LSB]，通常为目标 LSB20。

    Returns:
        包含 sine_fit_metrics 字段；另含 err_mean_V、err_rms_V [V]，
        err_rms_LSB、err_max_LSB [LSB]。RMS 保留直流误差，不能写作 sigma。
        overflow_rate_adc2/ra/ktc 为各支路置标志的样本比例；它们与最终
        整数输出 clipped_low/clipped_high 不同，后者须由 CodeStream 单列。
        residue_min/max_V 是 RA 放大前残差，NSD_dBFS_Hz 以 v_fs/sqrt(2)
        归一白噪声等效密度，不是从某个 bin 幅度直接转换的结果。

    Side effects:
        不修改 res，也不剔除异常样本；调用方负责分开报告码域和有效带宽。
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
