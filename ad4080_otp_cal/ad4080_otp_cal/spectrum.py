# ==============================================================================
# 文件名     : spectrum.py
# 模块名     : ad4080_otp_cal.spectrum
# 版本       : v1.0
# 功能       : 提供 ADCToolbox 优先联动、纯 NumPy 自动回退的频谱与动态指标分析
#              （ENOB / SNDR / SNR / SFDR / THD / NSD / 相干频率计算）。
# 设计说明   :
#   * [双引擎策略] 若环境中安装了 `adctoolbox`，优先调用 `adctoolbox.analyze_spectrum`
#     与 `adctoolbox.find_coherent_frequency`；未安装时无缝回退至同口径纯 NumPy
#     实现（Hann 窗 + 基波主瓣 ±3 bin + 2..10 次谐波折叠统计），确保开箱即用。
#   * [单位约定] SNR/SNDR/SFDR 单位 dB 或 dBFS，THD 单位 dBc，NSD 单位 dBFS/Hz。
# 变更记录   :
#   2026-11-26  v1.0  初版实现。
# ==============================================================================
"""ADCToolbox 联动与纯 NumPy 回退频谱性能评估模块。"""

from __future__ import annotations

from dataclasses import dataclass
import math
import numpy as np

try:
    import adctoolbox as _adctb  # type: ignore
except ImportError:
    _adctb = None


@dataclass(frozen=True)
class SpectrumMetrics:
    """ADC 动态频谱核心指标汇总。"""

    enob: float
    sndr_db: float
    snr_db: float
    sfdr_db: float
    thd_db: float
    nsd_dbfs_hz: float
    fund_amp_dbfs: float
    backend: str


def has_adctoolbox() -> bool:
    """检测当前 Python 环境是否已安装 `adctoolbox` 包。"""
    return _adctb is not None


# ==============================================================================
# 1. 相干频率选择（与 ADCToolbox 同口径）
# ==============================================================================
def find_coherent_frequency(
    fs_hz: float,
    fin_target_hz: float,
    n_fft: int,
) -> tuple[float, int]:
    """计算与采样率 fs_hz、点数 n_fft 严格相干（互质奇数 bin）的测试频率。

    Args:
        fs_hz: 采样率（Hz，例如 40e6）。
        fin_target_hz: 目标输入频率（Hz，例如 1e6）。
        n_fft: FFT 点数（通常为 2 的幂次）。

    Returns:
        (fin_coherent_hz, bin_index): 精确相干频率（Hz）与对应奇数互质频点号。
    """
    # [相干必要性] 奇数且与 N_FFT 互质保证正弦波在 N_FFT 采样点内遍历全部相位，
    # 既消除 FFT 频谱泄漏，又确保 360 个物理电容在标定阶段获得最均匀的码字激励。
    if _adctb is not None and hasattr(_adctb, "find_coherent_frequency"):
        fin, k_bin = _adctb.find_coherent_frequency(fs=fs_hz, fin_target=fin_target_hz, n_fft=n_fft)
        return float(fin), int(k_bin)

    k_est = max(3, int(round(fin_target_hz * n_fft / fs_hz)))
    if k_est % 2 == 0:
        k_est += 1
    while math.gcd(k_est, n_fft) != 1:
        k_est += 2
    # [越界回退] 若目标频点超过奈奎斯特边界，向低频回退至最近合法互质奇数 bin
    if k_est >= n_fft // 2:
        k_est = max(3, (n_fft // 2) - 1)
        while math.gcd(k_est, n_fft) != 1:
            k_est -= 2
    return float(k_est) * fs_hz / float(n_fft), int(k_est)


# ==============================================================================
# 2. 谐波折叠与动态频谱指标计算（ENOB / SNDR / SNR / SFDR / THD / NSD）
# ==============================================================================
def _folded_bin(k_harm: int, n_fft: int) -> int:
    """将任意整数倍谐波频点折叠至第一奈奎斯特区 [0, n_fft // 2]。"""
    # [混叠折叠] 高次谐波超过 fs/2 后会镜像折返至基带，必须精确折叠以免漏计 THD
    rem = k_harm % n_fft
    return n_fft - rem if rem > n_fft // 2 else rem


def analyze_spectrum(
    signal: np.ndarray,
    fs_hz: float = 40e6,
    full_scale_peak: float = 1.0,
    max_harmonics: int = 10,
    use_window: bool = True,
) -> SpectrumMetrics:
    """计算 ADC 输出码流或归一化信号的 ENOB / SNDR / SNR / SFDR / THD / NSD。

    Args:
        signal: 1D 实数序列（可为归一化浮点 [-1, +1] 或 20-bit 整数码；若为整数码，
                请将 full_scale_peak 设为 2^19 = 524288.0）。
        fs_hz: 采样率（Hz）。
        full_scale_peak: 满量程峰值幅度（用于换算 dBFS 与 NSD）。
        max_harmonics: 计入 THD 的最高谐波次数（2..max_harmonics）。
        use_window: 是否加 Hann 窗（相干整周期采样时可关闭以获最锐利谱线）。

    Returns:
        SpectrumMetrics 数据结构。
    """
    sig = np.asarray(signal, dtype=np.float64).ravel()
    n_fft = len(sig)
    if n_fft < 64:
        raise ValueError("FFT 采样点数至少为 64。")

    # 若环境装有 adctoolbox，优先走官方 analyze_spectrum 保持口径一致
    if _adctb is not None and hasattr(_adctb, "analyze_spectrum"):
        norm_sig = sig / float(full_scale_peak)
        res = _adctb.analyze_spectrum(norm_sig, fs=fs_hz, max_scale_range=(-1.0, 1.0))
        sndr = float(res.get("sndr_dbc", res.get("sndr", 0.0)))
        snr = float(res.get("snr_dbc", res.get("snr", sndr)))
        sfdr = float(res.get("sfdr_dbc", res.get("sfdr", 0.0)))
        thd = float(res.get("thd_dbc", res.get("thd", -120.0)))
        enob = float(res.get("enob", (sndr - 1.76) / 6.02))
        nsd = -snr - 10.0 * math.log10(fs_hz / 2.0)
        return SpectrumMetrics(
            enob=enob,
            sndr_db=sndr,
            snr_db=snr,
            sfdr_db=sfdr,
            thd_db=thd,
            nsd_dbfs_hz=nsd,
            fund_amp_dbfs=float(res.get("fund_dbfs", 0.0)),
            backend="adctoolbox",
        )

    # 纯 NumPy 同口径频谱引擎（去直流 + 可选 Hann 窗）
    x = (sig - np.mean(sig)) / float(full_scale_peak)
    if use_window:
        win = np.hanning(n_fft)
        cg = float(np.sum(win))
        span = 3  # Hann 窗主瓣半宽
    else:
        win = np.ones(n_fft, dtype=np.float64)
        cg = float(n_fft)
        span = 1

    spec = np.fft.rfft(x * win)
    # 单边功率谱（满量程正弦波峰值=1.0 对应功率 0.5）
    pwr = (2.0 * (np.abs(spec) ** 2)) / (cg ** 2)
    pwr[0] = 0.0
    if n_fft % 2 == 0:
        pwr[-1] *= 0.5

    # 定位基波峰值（避开 DC 附近）
    k_dc = max(2, span + 1)
    k_fund = k_dc + int(np.argmax(pwr[k_dc:]))

    fund_lo = max(1, k_fund - span)
    fund_hi = min(len(pwr), k_fund + span + 1)
    p_sig = float(np.sum(pwr[fund_lo:fund_hi]))

    # 标记并累加 2..max_harmonics 次谐波功率
    harm_mask = np.zeros(len(pwr), dtype=bool)
    harm_mask[:k_dc] = True
    harm_mask[fund_lo:fund_hi] = True
    p_harm = 0.0
    for h in range(2, max_harmonics + 1):
        kh = _folded_bin(h * k_fund, n_fft)
        lo = max(1, kh - 1)
        hi = min(len(pwr), kh + 2)
        for idx in range(lo, hi):
            if not harm_mask[idx]:
                p_harm += float(pwr[idx])
                harm_mask[idx] = True

    p_noise = float(np.sum(pwr[~harm_mask]))
    eps = 1e-30

    # SFDR：基波与带内最大杂散（不含 DC 和基波主瓣）之比
    spur_pwr = pwr.copy()
    spur_pwr[:k_dc] = 0.0
    spur_pwr[fund_lo:fund_hi] = 0.0
    p_max_spur = float(np.max(spur_pwr))

    sndr_db = 10.0 * math.log10(max(p_sig, eps) / max(p_noise + p_harm, eps))
    snr_db = 10.0 * math.log10(max(p_sig, eps) / max(p_noise, eps))
    thd_db = 10.0 * math.log10(max(p_harm, eps) / max(p_sig, eps))
    sfdr_db = 10.0 * math.log10(max(p_sig, eps) / max(p_max_spur, eps))
    enob = (sndr_db - 1.76) / 6.02
    fund_dbfs = 10.0 * math.log10(max(p_sig / 0.5, eps))
    nsd_dbfs_hz = fund_dbfs - snr_db - 10.0 * math.log10(fs_hz / 2.0)

    return SpectrumMetrics(
        enob=enob,
        sndr_db=sndr_db,
        snr_db=snr_db,
        sfdr_db=sfdr_db,
        thd_db=thd_db,
        nsd_dbfs_hz=nsd_dbfs_hz,
        fund_amp_dbfs=fund_dbfs,
        backend="numpy-builtin",
    )
