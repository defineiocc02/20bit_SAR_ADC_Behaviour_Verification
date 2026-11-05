"""共享残差放大器：残差电压增益、孔径输出噪声和独立摆幅检查。

输入 residue 是 RDAC 顶板残差 [V]，不是外部输入或 ADC2 码；
输出 vra 是RA 输出/ADC2 输入节点电压 [V]。
电荷一致增益 G[n]=C_signal[n]/C_feedback_true由相同活动物理电容决定。
采样掩码使存储信号变为 alpha*x 时，外部输入信号传递为 G*alpha，不能把它与残差放大增益 G 混为一项。

白噪声 noise_out 是 RA 输出端 RMS [V]；折到已存残差输入需除 G，
折到被 alpha 衰减的外部输入还需除 alpha。默认由所选 DR 锚点在 cap_scale=1拟合一次并锁定，
是噪声预算校准，不是器件噪声预测；电容扫描不得逐点用剩余预算重新拟合。
fixed 增益只作为隔离物理耦合的对照。

ra_autozero 是旧的现象预算：白噪声乘 10**(cost_db/20)，并停用本类 1/f注入；没有实际 AZ 存储电容、
保持噪声或静态失调源的电路消除。它与chopping_az.py 的逐 raw 更新/保持研究模型不同，
后者拒绝叠加此预算。adc2_dyn_bw_ratio 同样只是输出噪声倍率，不是开关噪声传递函数。

[00]/[00_1] 披露共享 RA 和名义增益 32；摆幅限值为配置假设。
讲稿的 AZ噪声代价及动态采样收益未唯一披露器件/整机参照，不能由倍率声称复原。有限建立、
压摆与参考耦合由 ConversionEngine 的信号路径处理；本类噪声在孔径处叠加，不实现周期平稳噪声、
晶体管 AZ、功耗循环或功耗预测。
"""

from __future__ import annotations

import numpy as np

from .config import Config, resolve_ra_noise


def flicker_series(
    n: int,
    fs: float,
    f_corner: float,
    sigma_white: float,
    rng: np.random.Generator,
    *,
    t_obs: float = 1.0,
    include_drift: bool = True,
) -> np.ndarray:
    """在实际 ADC 时钟上生成带限 1/f 电压分量，不重复加入白底。

    Args:
        n: 正整数样本数，返回记录时长为 n/fs [s]。
        fs: 实际 ADC 采样率 [Hz]，有限且正。
        f_corner: 1/f 转角 [Hz]，满足 0<=f_corner<fs/2。
        sigma_white: 用于定义白底 S0=2*sigma_white**2/fs 的电压 RMS [V]；
            本函数仅生成 S(f)=S0*f_corner/f 的 1/f 分量，白底由调用方另加。
        rng: 1/f 实现的随机流。
        t_obs: 假设的最低频率截止 f_low=1/t_obs [Hz]，有限且正；不是本次记录时长，
            也不是已测得的漂移规律。
        include_drift: 是否加入低于 fs/n、有限记录未分辨的平稳慢状态。

    Returns:
        (n,) 电压 [V]，单位/参考节点沿用 sigma_white。已分辨频率用 FFT
        整形，未分辨频率用对数频率高斯状态。短记录不会因此分辨 40 Hz 转角。

    Side effects:
        消费 rng；每次调用生成独立实现。跨记录连续性或长时间查询应显式
        重用 BandLimitedFlicker 状态，而不是反复调用本包装函数。

    Raises:
        ValueError: 非法长度、时钟、转角、幅度或低频截止参数。
    """
    if (
        type(n) is not int
        or n < 1
        or not np.all(np.isfinite([fs, f_corner, sigma_white, t_obs]))
        or fs <= 0
        or f_corner < 0
        or f_corner >= fs / 2
        or sigma_white < 0
        or t_obs <= 0
    ):
        raise ValueError(
            "flicker needs valid record length, clock, corner, amplitude and low cutoff"
        )
    w = rng.normal(0.0, sigma_white, n)
    W = np.fft.rfft(w)
    f = np.fft.rfftfreq(n, d=1.0 / fs)
    f[0] = f[1] if n > 1 else 1.0
    # 转角以上置零：此处只补 1/f 分量；若置为 1，会重复加入系统已有白底。
    shape = np.where(f <= f_corner, np.sqrt(f_corner / np.maximum(f, 1e-12)), 0.0)
    shape[f < 1 / t_obs] = 0.0
    shape[0] = 0.0
    x = np.fft.irfft(W * shape, n)

    if not include_drift:
        return x
    f_low = 1 / t_obs
    upper = min(f_corner, fs / n)
    if upper <= f_low or sigma_white == 0:
        return x
    from .low_frequency_noise import BandLimitedFlicker

    state = BandLimitedFlicker.create(
        2 * sigma_white**2 / fs,
        f_corner,
        f_low,
        rng,
        high_hz=upper,
    )
    return x + state.at_adc_indices(np.arange(n), fs)


class ResidueAmplifier:
    """把已存残差放大到 ADC2 输入节点，叠加噪声并检查 RA 摆幅。

    Attributes:
        noise_out: 输出端白噪声 RMS [V]，已含启用的旧 AZ/动态带宽倍率；
            ra_enable_noise=False 时为零，不随逐样本 G 改变。
        flicker_white: 1/f 过程的输入等效白底 RMS 锚点 [V]；仅在旧 AZ预算关闭时注入，
            经逐样本 G 转为 RA 输出误差。
    """

    def __init__(self, cfg: Config):
        """固化输出噪声预算及输入等效 1/f 锚点，不生成运行时噪声。

        Args:
            cfg: 模型配置；resolve_ra_noise 给出 AZ/动态带宽关闭的输出噪声锚点，
                再依次应用 AZ 代价和 adc2_dyn_bw_ratio，不二次反推预算。
                ra_v_clip 是当前配置的 RA 输出节点摆幅 [V]。

        Side effects:
            绑定 cfg，计算 noise_out 与 flicker_white；不消费 RNG。

        Notes:
            旧 AZ 仅停用 1/f 注入并放大白噪声，不意味着仿真已消除独立器件
            失调。动态带宽倍率作用于孔径输出噪声，不能替代完整噪声传递分析。
        """
        self.cfg = cfg
        self.noise_out = resolve_ra_noise(cfg) if cfg.ra_enable_noise else 0.0
        # 固定基线先应用 AZ 白噪声代价；它不是整机 SNDR 的直接 dB 增减。
        # 本倍率没有实际 AZ 采样/保持状态，参照范围不明时不得当作器件复现。
        if cfg.ra_autozero and self.noise_out > 0:
            self.noise_out *= 10.0 ** (cfg.ra_autozero_cost_db / 20.0)
        # ADC2 输入节点等于 RA 输出；这里仅缩放输出等效噪声，
        # 不声称由信号建立带宽推出开关噪声等效带宽。
        if cfg.adc2_dyn_bw_ratio is not None and self.noise_out > 0:
            self.noise_out *= float(cfg.adc2_dyn_bw_ratio)
        # 输入等效 1/f 白底锚点；旧 AZ 开启时在 evaluate 跳过该源，
        # 不模拟斩波搬频、采样折叠或连续低频噪声消除电路。
        self.flicker_white = (
            cfg.flicker_white_ratio * float(np.sqrt(cfg.chi * 1.380649e-23 * 300.0 / cfg.c_total0))
            if cfg.flicker_corner_hz > 0
            else 0.0
        )

    def gain_vector(self, c_active: np.ndarray, c_feedback_true: float) -> np.ndarray:
        """返回残差电压增益 G=C_signal/C_feedback_true。

        Args:
            c_active: (N,) 活动组等效 C_signal [F]；split 中不是所有物理电容的直接和，
                也不是含 18 片的总池电容，见 split_coefficients。
            c_feedback_true: 本虚拟芯片固定的实际反馈电容 [F]。

        Returns:
            与 c_active 同形的无量纲增益。charge 模式使用物理比值，fixed
            模式返回 cfg.g_actual。采样信号系数 alpha 不在此函数内相乘。
        """
        if self.cfg.ra_gain_model == "fixed":
            return np.full(np.shape(c_active), self.cfg.g_actual)
        return np.asarray(c_active, dtype=float) / c_feedback_true

    def evaluate(
        self,
        residue: np.ndarray,
        rng: np.random.Generator,
        g: np.ndarray | float | None = None,
        *,
        error_v: np.ndarray | None = None,
    ) -> tuple[np.ndarray, np.ndarray]:
        """对同一原始记录执行残差放大、输出误差叠加和 RA 钳位。

        Args:
            residue: (N,) RDAC 顶板已存残差 r=stored_input-v_D_true [V]。
            rng: 运行时噪声随机流；不是制造电容随机流。
            g: 标量或 (N,) 残差电压增益，无量纲。生产 charge 路径应显式传入gain_vector 的物理结果；
                None 实际回落 cfg.g_actual，函数不会因 charge 模式缺参而拒绝，
                也不会自动从物理池重算。
            error_v: 可选 (N,) 有限 RA 输出等效误差 [V]，在已有噪声之后、
                RA 钳位与 ADC2 量化之前注入。来源由调用方定义，不自动解释为器件噪声；
                None 不改变原 RNG 消费顺序。

        Returns:
            (vra, sat)，均为 (N,)；vra 为钳位后的 RA 输出电压 [V]；sat 为
            钳位前 abs(v)>=ra_v_clip 的布尔标志，含噪声和显式误差造成的饱和。
            ADC2 窗口溢出由后级另判，不能用它覆盖 RA 已经饱和的事实。

        Side effects:
            先抽输出白噪声，再按启用条件抽输入等效 1/f 并乘 G；状态在 rng
            中，不存连续的跨记录 1/f 状态。error_v 不消费 RNG，不修改输入。

        Raises:
            ValueError: 显式 error_v 形状不匹配或含非有限值。
        """
        if g is None:
            g = self.cfg.g_actual
        g = np.asarray(g, dtype=float)
        v = g * residue
        # 先抽白噪声，使仅切换 1/f 开关时可共享白噪声实现；记录间不保留电气状态。
        if self.noise_out > 0:
            v = v + rng.normal(0.0, self.noise_out, residue.shape[0])
        if self.flicker_white > 0 and not self.cfg.ra_autozero:
            # 折输入的闪烁噪声 × 逐样本增益（电荷一致口径下闪烁噪声与信号
            # 经同一个 G -> 折输入幅度不随 G 波动）。
            nf = flicker_series(
                residue.shape[0], self.cfg.fs, self.cfg.flicker_corner_hz, self.flicker_white, rng
            )
            v = v + g * nf
        if error_v is not None:
            error = np.asarray(error_v, dtype=float)
            if error.shape != residue.shape or np.any(~np.isfinite(error)):
                raise ValueError("explicit RA output error must be finite and match the record")
            v = v + error
        sat = np.abs(v) >= self.cfg.ra_v_clip
        v = np.clip(v, -self.cfg.ra_v_clip, self.cfg.ra_v_clip)
        return v, sat
