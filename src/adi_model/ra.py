"""ra.py -- 残差放大器（RA）：电荷一致增益、输出噪声、饱和。

============================================================================
物理模型与文献出处
============================================================================
论文 [00]：RA 是共享的（PPT p.11 "Shared residue amplifier, 40% of ADC
power consumption"），GMR + OTA 架构、带 auto-zero（PPT p.34-35：消
offset/漂移、噪声代价 -1.6 dB、ADC2 动态采样带宽 +1.3 dB —— 这两个 dB
结构**未建模**，见"适用域"）。行为模型取电荷守恒口径：

    G[n]   = C_active[n] / C_F_true      （v3 审计修正：逐样本，由同一组
                                            物理电容决定，失配由此走增益通路）
    v_R[n] = G[n]·r[n] + n_RA,out        （输出端噪声是电路属性，不随 G[n] 变）

* n_RA,out 的大小在 cap_scale=1 时由 target_dr_db 反推标定一次并**锁定**
  （config.noise_budget 的两条原则），不随电容缩放漂移——缩电容扫描的
  关键口径，防止"总预算 - kT/C 逐点反推"把 RA 噪声算成 0。
* **先饱和再送 ADC2**：ra_v_clip 检查在 ADC2 之前。不能先把大信号在
  数学上被 ADC2 余量吸收、掩盖真实放大器已饱和的事实。KTC 支路里的
  两条放大通路各自独立检查饱和（ktc.py）。
* ra_gain_model="fixed"：理想固定增益 G = g0·(1+gain_error)，仅作对照，
  用于隔离"电荷一致"与"固定增益"两种口径的差异（v3 审计实验）。

单位契约：residue / 输出均为 [V]；noise_out 为输出端 RMS [V]
（config.ra_out_noise_rms 语义 = 输出端，折输入时除以 G）。

参数来源分级：
    g0 = 32               [披露]（PPT 图标注）
    ra_v_clip = 3.6 V     [假设]（3.3V 器件 + 余量的工程取值）
    noise_out             [拟合]（由 target_dr_db=94.6 反推，逐字审计：
                          论文正文写 94.2，取 PPT 94.6 因与 NSD 8.8nV 自洽）

适用域声明（勿夸大）：
    * auto-zero：v6.1 起结构化建模 —— ra_autozero=True 时白噪声按折叠因子
      10^(1.6/20) 放大（PPT 披露代价）、1/f 整体移除（auto-zero 的收益面）；
      RA 自身的静态 offset 仍不单独建模（它被 auto-zero 消除，且残差链路
      对恒定偏移不敏感 —— 数字增益校准吸收）；
    * 不建模有限 GBW 建立误差（RA 在本模型内瞬时建立；建立类误差由
      dynamics.py 的三项承担——RA 自身的建立是开放项）；
    * 不建模 RA 复用/功耗循环（PPT ~50% idle time）——对输出行为无影响，
      只影响功耗，而功耗不在行为模型预测范围内。
"""

from __future__ import annotations

import math

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
    """生成 1/f 闪烁噪声**注入源**（FFT 塑形，确定性可复现）。

    PSD（单边）= S0·(f_corner/f)，f <= f_corner；f > f_corner 处为 0。
    **口径**：这是"闪烁分量"而非完整 1/f 角过程 —— 白底由系统既有热噪声源
    （kT/C、RA 白噪声）承担，本源只补 1/f 尾巴；总噪声谱的转角（闪烁 PSD
    = 白底处）由 sigma_white 与系统白底的匹配决定（stage23）。
    S0 = 2·sigma_white²/fs（转角处闪烁 PSD 的定义值）。

    Args:
        n:            样本数。
        fs:           采样率 [Hz]。
        f_corner:     转角频率 [Hz]（PPT：~40 Hz）。
        sigma_white:  闪烁白底等效 RMS [V]：S0 = 2·sigma_white²/fs。
        rng:          噪声源（与主循环共用，保证可复现）。
        t_obs:        观测时长 [s]。f_low = 1/t_obs 是补回的亚 f_min
                      功率的下积分限（T 越长，可积的不可分辨功率越多）。
        include_drift: 是否把 f < f_min 那部分**不可分辨**的 1/f 功率
                      以"常数偏移 + 线性趋势"显式补回；False 则只返回
                      可分辨的闪烁分量（短记录 SNDR 会系统性偏乐观）。
    Returns:
        (n,) 逐样本电压 [V]。DC 分量为 0（rfft 置零，避免伪漂移基线）。
    适用域：① 周期边界伪影在 f < fs/n 处（不进带）；② 单实现周期图每 bin
    是 χ²₂（±5.6 dB），**谱形验收必须用多实现平均或带功率比**
    （stage23 的发生器检验用 K=24 种子平均，系统内用带功率比）。
    """
    w = rng.normal(0.0, sigma_white, n)
    W = np.fft.rfft(w)
    f = np.fft.rfftfreq(n, d=1.0 / fs)
    f[0] = f[1] if n > 1 else 1.0
    # 只生成 1/f 段（f<=fc），转角以上置零：白底由系统热噪声源承担（口径见
    # docstring）。历史教训：若把转角以上置 1（完整角过程），系统注入会在
    # 全带多出一份白底（σ_f = 系统总噪声）-> SNDR −3 dB（stage23 实测教训）。
    shape = np.where(f <= f_corner, np.sqrt(f_corner / np.maximum(f, 1e-12)), 0.0)
    shape[0] = 0.0
    x = np.fft.irfft(W * shape, n)

    # ------------------------------------------------------------------
    # 审计 F9：主系统记录里 40 Hz 闪烁噪声**恒为零**。
    #
    # 长度 n、采样率 fs 的记录，最低可分辨频率是 f_min = fs/n。默认
    # n=32768、fs=40 MHz 时 f_min = 1220.7 Hz >> 40 Hz，于是 shape 全零，
    # flicker_series 返回全 0 序列（独立复算实测 nonzero_samples = 0）。
    # "40 Hz 转角已验证"的说法因此不成立——发生器单独通过不等于系统短记录
    # 里真的存在闪烁功率。
    #
    # 物理上，低于 f_min 的 1/f 功率并没有消失，它只是**不可分辨**：在一段
    # 有限记录里它表现为缓慢漂移（近似直流偏移 + 线性趋势），而不是某个
    # 可指认的谱峰。这里把它显式补回来，否则短记录 SNDR 会系统性偏乐观。
    # ------------------------------------------------------------------
    if not include_drift:
        return x
    f_min = fs / n
    f_low = 1.0 / max(t_obs, 1e-12)
    if f_corner <= f_min or f_min <= f_low:
        # 转角低于可分辨下限，且观测时长内也没有可积功率 -> 无可补分量。
        return x
    s0 = 2.0 * sigma_white**2 / fs
    var_band = s0 * f_corner * math.log(f_min / f_low)
    if var_band <= 0.0:
        return x
    # 一半给常数偏移，一半给线性趋势。趋势系数 a 满足
    #   mean_t[(a·t)^2] = a^2·T^2/12 = var_band/2  ->  a = sqrt(6·var_band)/T
    offset = rng.normal(0.0, math.sqrt(0.5 * var_band))
    t_rec = n / fs
    a = math.sqrt(6.0 * var_band) / t_rec * rng.normal(0.0, 1.0)
    trend = a * np.linspace(-0.5 * t_rec, 0.5 * t_rec, n)
    return x + offset + trend


class ResidueAmplifier:
    """残差放大器：增益（电荷一致或固定）、输出高斯噪声、独立饱和检查。

    Attributes:
        noise_out: 输出端噪声 RMS [V]；ra_enable_noise=False 时为 0。
    """

    def __init__(self, cfg: Config):
        """构造残差放大器：噪声、auto-zero 与 1/f 分支。

        Args:
            cfg: 模型配置（Config）。决定 noise_out（[拟合]，由
                 target_dr_db 反推）、auto-zero 倍率（[披露] -1.6 dB）、
                 ADC2 动态带宽倍率（[推导] +1.3 dB）与闪烁白底；
                 全部系数在此固化。g0=32 [披露]，ra_v_clip=3.6V [假设]。
        Side effects: 计算并固化 self.noise_out 与 self.flicker_white。
        """
        self.cfg = cfg
        self.noise_out = resolve_ra_noise(cfg) if cfg.ra_enable_noise else 0.0
        # RA auto-zero（PPT p.34-35，−1.6 dB）：消 offset/低频噪声（含 1/f），
        # 代价 = 存储电容 kT/C + 噪声折叠 -> 白噪声 ×10^(cost/20)。
        # 口径注记：PPT 的 −1.6 dB 参照系（RA 自身还是整机）未披露；
        # 折到本模型整机预算（RA 占噪声功率 ~74%）后整机 ΔSNDR ≈ −1.2 dB，
        # stage22 做"预算推导 vs 实测"一致性检验，不硬凑披露值。
        if cfg.ra_autozero and self.noise_out > 0:
            self.noise_out *= 10.0 ** (cfg.ra_autozero_cost_db / 20.0)
        # ADC2 动态采样带宽（PPT +1.3 dB，宽建立/窄噪声）：适用域声明 ——
        # 本模型中 ADC2 输入节点 = RA 输出、无独立 ADC2 输入噪声源，
        # 故"ADC2 以窄带宽收取噪声"只能作用于本类生成的输出噪声（唯一噪声）。
        if cfg.adc2_dyn_bw_ratio is not None and self.noise_out > 0:
            self.noise_out *= float(cfg.adc2_dyn_bw_ratio)
        # 1/f 闪烁噪声（折输入）白底幅度 = ratio × kT/C 白底；auto-zero 开启时
        # 整体移除（auto-zero 消的就是 offset/漂移/低频噪声；相关双采样把 1/f
        # 转移到 fs 附近，行为级取"带内消除"口径）。
        self.flicker_white = (
            cfg.flicker_white_ratio * float(np.sqrt(cfg.chi * 1.380649e-23 * 300.0 / cfg.c_total0))
            if cfg.flicker_corner_hz > 0
            else 0.0
        )

    def gain_vector(self, c_active: np.ndarray, c_feedback_true: float) -> np.ndarray:
        """电荷一致模型的逐样本增益 G[n] = C_active[n]/C_F_true。

        Args:
            c_active: 逐样本实际参与的采样电容 [F]（噪声绑活跃口径，
                      见 config.sigma_sampling_c 的注释——备用 slice 不降噪声）。
            c_feedback_true: 该芯片的实际反馈电容 [F]（chip 只生成一次）。
        Returns:
            G[n] [无量纲]，形状同 c_active。fixed 模式退化为常数 g_actual。
        """
        if self.cfg.ra_gain_model == "fixed":
            return np.full(np.shape(c_active), self.cfg.g_actual)
        return np.asarray(c_active, dtype=float) / c_feedback_true

    def evaluate(
        self, residue: np.ndarray, rng: np.random.Generator, g: np.ndarray | float | None = None
    ) -> tuple[np.ndarray, np.ndarray]:
        """放大 + 加噪 + 饱和。

        Args:
            residue: 残差电压 r[n] = (x_R + e_input) - v_D,true [V]。
            rng:     噪声生成器（与主循环共用，保证可复现）。
            g:       逐样本增益 [无量纲]；charge 模式**必传**
                     （gain_vector 的结果），缺省仅适用于 fixed 模式。
        Returns:
            (vra, sat)
            vra: RA 输出电压 [V]，已按 ra_v_clip 钳位。
            sat: 逐样本布尔，|vra| ≥ ra_v_clip（饱和统计口径见 metrics）。
        Side effects: 无（噪声按事件生成，但状态在 rng 里，不在本类）。
        """
        if g is None:
            g = self.cfg.g_actual
        g = np.asarray(g, dtype=float)
        v = g * residue
        if self.flicker_white > 0 and not self.cfg.ra_autozero:
            # 折输入的闪烁噪声 × 逐样本增益（电荷一致口径下闪烁噪声与信号
            # 经同一个 G -> 折输入幅度不随 G 波动）。
            nf = flicker_series(
                residue.shape[0], self.cfg.fs, self.cfg.flicker_corner_hz, self.flicker_white, rng
            )
            v = v + g * nf
        if self.noise_out > 0:
            v = v + rng.normal(0.0, self.noise_out, residue.shape[0])
        sat = np.abs(v) >= self.cfg.ra_v_clip
        v = np.clip(v, -self.cfg.ra_v_clip, self.cfg.ra_v_clip)
        return v, sat
