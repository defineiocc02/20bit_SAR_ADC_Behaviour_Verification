"""SAR行为模型的配置、节点单位与合法运行域。

Config()保留7bit先级/unary对照；paper_literal()是9bit判决、63+8 split及理想dual-port注入候选；
rtl_structural()保持同一9bit几何，另约束16相位、range转移、D=2序列和由dem_enable联动的实际分配器。
工厂不会复原未公开电路。

fs单位Hz；输入峰值v_fs单位输入节点V；ADC2/ra_v_clip单位RA输出节点V；电容单位F，失配为相对量。Δ1、
RDAC细栅格ΔDAC、目标LSB分别定义，不可混用。合法性检查拒绝无定义分支/几何；validate只给名义预算，
不能替代逐样本余量实验。来源分级在provenance；噪声和工艺假设不等于硅预测。
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field

from .conversion import ConversionParameters
from .input_network import InputNetworkParameters

K_B = 1.380_649e-23  # J/K
TEMP_K = 300.0  # K


class ConfigError(ValueError):
    """配置不满足已实现分支、几何或数值域时的入口异常。

    与validate返回的余量不足不同：合法配置仍可用于研究有限余量，
    无定义枚举或不兼容profile应在运行前拒绝。
    """


# 枚举只约束已声明分支，不保证每字段在每runner活跃。
# generic调度由dem_mode选择；rtl16由profile约束dem_mode与dem_enable联动。
# 新增分支须同步此表；入口合法性、运行时有效配置和波形余量是不同检查。
LEGAL_VALUES: dict[str, tuple[str, ...]] = {
    "stage1_reading": ("paper_consistent", "paper_literal", "legacy_codeword"),
    "dither_transfer_model": ("range", "granularity", "dual_port"),
    "dither_mode": ("off", "analog", "quantizer", "sampling"),
    "dither_split_bank": ("sub", "main"),
    "dither_quant_transfer": ("quantizer", "rdac"),
    "ra_gain_model": ("charge", "fixed"),
    "calibration": ("none", "gain", "gain_beta", "weights"),
    # dem_mode 现在**真的决定调度器**：三个仿真入口都经 scheduler.make_scheduler
    # 取调度器（rotate -> Scheduler，permute -> ShuffledScheduler）。
    # 显式调度器与配置冲突时拒绝运行；所有权由当前因果slice计划检查。
    "dem_mode": ("rotate", "permute"),
    "dac_arch": ("unary", "split"),
}


# 架构标签与实际决策位数必须匹配；已知dither不增加未知输入信息。
# 7bit/6bit是保留对照，9bit工厂另声明几何与端口假设。
READING_B1: dict[str, int] = {
    "paper_consistent": 7,
    "paper_literal": 9,
    "legacy_codeword": 6,
}


@dataclass
class Config:
    """模型配置；顶层字段的来源分级见provenance，嵌套控制见对应参数类。

    裸构造器不自动随b1重推后端；需要成组切换架构时使用工厂方法。字段被接受不代表在所有runner活跃，
    实际应用与保留项由effective_config说明。
    """

    # ---------------- 系统与目标 ----------------
    fs: float = 40e6  # raw采样率[Hz]
    n_bits_target: int = 20  # 定义目标LSB；最终整数码宽由FixedPointFormat另行指定
    v_fs: float = 3.0  # 差分满幅峰值（V），输入范围 = ±v_fs
    target_dr_db: float = 94.6  # 目标预算[dB]；ra_out_noise_rms=None时用于反推RA预算

    # ---------------- 第一级 SADC ----------------
    b1: int = 7  # 第一级 SADC 判决位数（含义见 stage1_reading）
    # 架构读数标签；工厂同步设置b1/几何/后端，标签本身不是电路证据。
    stage1_reading: str = "paper_consistent"
    # range: d_R=0，按ΔDAC转移-rint(d_Q/ΔDAC)；granularity保留可选取整粒度。
    # dual_port: d_R=2**enh*d_Q，RDAC命令加(d_R-d_Q)/ΔDAC，重构扣d_R。
    # 两者是不同端口假设，不由dither码宽增加未知输入判决位数。
    dither_transfer_model: str = "range"
    dither_enhancement_bits: int = 2  # [披露] 论文："enhanced by 2b"
    c_sadc: float = 1.0e-12  # SADC 采样电容（小、快）
    sadc_offset: float = 0.0  # SADC 失调（V，折输入）
    sadc_rdac_gain_mismatch: float = 1e-4  # SADC 与 RDAC 的增益失配（>12b matching 的相关量）
    # [09] 1.5：**匹配的是采样响应，不只是电容值**。
    # 两条通路 τ = (R_s+R_on)C 不同 → 对快速变化的输入产生不同的跟踪误差：
    #     x_S - x_R ≈ -Δτ · dx/dt
    # 这是**信号相关**的误差（与频率、幅度成正比），会直接进残差并吃掉余量，
    # 与静态增益失配（sadc_rdac_gain_mismatch）是两类不同的东西。
    # 专利的对策是在输入开关支路串一个稳定的电阻 280，降低 τ 对 R_on 变化的敏感度。
    sampling_tau_mismatch: float = 0.0  # Δτ=τ_SADC−τ_RDAC [s]；误差近似−Δτ·dx/dt
    sadc_mismatch_enable: bool = False
    sadc_mismatch_sigma: float = 0.0  # SADC 阈值失配 sigma（V，折输入）

    # ---------------- 级间增益 / 残差放大器 ----------------
    g0: float = 32.0  # 名义级间增益
    gain_error: float = 0.0  # fixed 模式下 G_actual = g0*(1+gain_error)
    # charge: G[n]=C_signal[selected]/Cf，选中电容集合进入增益。
    # fixed: G=g0*(1+gain_error)，仅用于隔离电容耦合的对照。
    ra_gain_model: str = "charge"
    ra_out_noise_rms: float | None = None  # None -> 自动标定到 target_dr_db（在 s=1 冻结一次）
    ra_v_clip: float = 1.98  # RA 输出摆幅（先检查自身饱和，再送 ADC2）
    # paper_consistent 读数：g0*delta1 = 1.5 V，
    # 1.2×adc2_v_max = 1.98 V。
    ra_enable_noise: bool = True

    # ---------------- RDAC ----------------
    n_slices: int = 18  # 电容slice池：8转换/8采集/2备用，并非18颗独立ADC
    n_active: int = 8  # 每次参与残差输出的 slice 数
    n_unit_per_slice: int = 64  # unary每slice单位数/预算锚点；split数组按n_main+n_sub构造
    n_units_headroom: int = 0  # unary名义信号范围外保留的单位码数
    c_total0: float = 20.5e-12  # cap_scale=1 时 RDAC 总采样电容（PPT: 20.5 pF）
    cap_scale: float = 1.0  # 面积缩放 s
    chi: float = 2.0  # kT/C 系数：1 = 单端；2 = 差分两侧独立
    mismatch_enable: bool = True
    # 行为预算锚点的相对σ，不是PDK数据；实际unit尺度由面积律函数换算。
    mismatch_sigma0: float = 1e-4
    # 失配的空间分解：字段为方差份额，归一化后求和为1；标准差权重取平方根。
    #   global : 全阵列共模（C_fb 失配 + 整体工艺偏差）。DEM 完全无效。
    #   slice  : slice 级共模（版图梯度 / 刻蚀负载）。DEM 跨 slice 有效。
    #   unit   : unit级独立随机（Pelgrom A/sqrt(WL)）；DEM可改变误差谱，不保证总误差降低。
    # (0,0,1)仅作为独立unit随机失配对照，不代表布局或工艺分布。
    mismatch_split: tuple = (0.15, 0.35, 0.50)  # (global, slice, unit) 的**方差**占比
    # aggregate版图的相对线性梯度(gx,gy)，坐标跨度归一到1，不乘随机σ。
    # 默认物理pool没有布局坐标，非零梯度须显式提供aggregate chip。
    mismatch_gradient: tuple = (0.0, 0.0)
    # 反馈电容名义值 = 活跃采样电容 / 名义增益 = 20.5 pF / 32 ≈ 0.6406 pF。
    # **电荷一致模型**：G = C_active[n]/C_F。注意这里 C_active 是每次采样
    # 实际接到的 8 个 slice 的总电容（= c_unit0 * n_active * 64 = 20.5 pF），
    # 不是整个 18-slice 资源池（46.1 pF）。
    c_feedback0: float = 20.5e-12 / 32.0
    split_feedback_cap_f: float | None = None  # None: C_sig_nom/g0；显式值为当前面积的 F
    dac_complete_range: bool = False  # True: n_main 电容允许 0..n_main 个选通

    # ---------------- 后端 ADC2 ----------------
    # 位预算（paper_consistent 读数，b1=7）：后端需分辨 G0*Delta1 到 LSB20 以下。
    #   Delta1 = 2*3/2**7 = 46.875 mV；G0*Delta1 = 32*46.875m = 1.5 V；
    #   取 14 bit -> Delta2 = 1.8/16384 = 109.9 uV -> 折输入 3.43 uV
    #   = 0.6 LSB20步长；其RMS为步长/sqrt(12)，是否可忽略取决于总噪声。
    # 这些值由 Config.paper_consistent() 从 Delta1 推导，勿手改：
    #   adc2_v_min = −0.10·G0·Δ1   adc2_v_max = +1.10·G0·Δ1
    #   adc2_n_bits = ceil(log2((v_max−v_min)/(LSB20·G0)))
    adc2_n_bits: int = 14
    adc2_v_min: float = -0.15  # -0.1 * g0 * delta1
    adc2_v_max: float = 1.65  #  1.1 * g0 * delta1

    # ---------------- 数字功能开关 ----------------
    dem_enable: bool = False
    dem_bridge_enable: bool = False  # 可实现的跨 slice 零和码交换；分段拓扑假设
    # analog将已知d加到共同输入，直接消耗孔径电压余量。
    # sampling把固定2D个物理单位从信号切到参考轨，形成alpha*x+d；
    # quantizer区分SADC端d_Q与可选RDAC端d_R。真实失配仍须逐节点检查。
    dither_mode: str = "off"  # off | analog | quantizer | sampling
    dither_amplitude_lsb1: float = (
        2.0  # analog及generic quantizer的d_Q幅度[Δ1]；rtl16由固定D2序列覆盖
    )
    dither_units_range: float = 2.0  # sampling的bank码幅度D；rtl16合同固定D=2
    # 采样 dither 掩码落在哪个阵列（split 拓扑）：sub（默认，桥接衰减后权重小）
    # 或 main。α 必须由**同一份掩码**推导：被移出输入连接的 2D 个单位，
    # 其信号电荷贡献按所在阵列的权重扣除。
    dither_split_bank: str = "sub"  # sub | main（仅 dac_arch="split" 时生效）
    # 连续插值仅作数学对照；物理开关只能
    # 实现整数单位选择。True 时 dither_code 取整数 d_u ∈ [−D, D]：
    # 掩码固定为 bank 阵列**末尾** 2D 个单位，其中 D+d_u 个接 +V_FS、
    # D−d_u 个接 −V_FS；注入量由**实际掩码电容**逐 d_u 精确生成（stage18）。
    dither_discrete: bool = False
    # KTC 观测网络对子阵列噪声的覆盖比例 γ ∈ [0,1]（stage16 逐相位噪声传递）。
    # γ=1：观测与残差通路看到同一份组合（a≈b，可完全相消）；
    # γ=0：观测只接主阵列 —— 子阵列噪声 β·q_S 不可观消，成为噪声底永久项。
    # [假设] 当前电路未定，默认 1（乐观界），stage16 给出 γ 的灵敏度。
    ktc_sub_obs_fraction: float = 1.0
    dither_changes_in_window: bool = False  # dither 在 KTC 提取窗口内是否变化（默认不变）
    ktc_enable: bool = False
    ktc_gain_n: float = 8.0  # 噪声观测通路增益 G_N
    ktc_kappa: float | None = None  # None -> g0/gain_n，即 beta = 1
    ktc_beta_error: float = 0.0  # beta 失配：beta = (1+ktc_beta_error) * kappa*G_N/G_R
    ktc_noise_n: float = 0.0  # 噪声观测通路自身输出噪声 sigma（V）
    # 观测通路一阶带宽。
    # 从提取开始建立的一阶模型，窗口末端采样时的残余建立误差：
    #     eps = exp(-2*pi*f_bw*dt)
    # 实际观测增益 = G_N*(1-eps) —— 数字端仍按名义 kappa 扣除，差额就是 beta 误差。
    # None = 理想（eps=0，只用于算法验证）。validate() 会报告给定 eps 预算所需带宽。
    ktc_observe_bw_hz: float | None = None
    # 提取窗口dt=ktc_dt_fraction*Ts。观测节点幅度为G_N*(n_R-dx)，
    # 数字扣除不直接占ADC2模拟窗口；缩短dt同时提高观测建立带宽压力。
    ktc_dt_fraction: float = 1.0 / 256.0
    calibration: str = "none"  # none | gain | gain_beta | weights
    dem_mode: str = "rotate"  # rotate=固定A/B分组；permute=因果slice洗牌；单位DEM另由dem_enable控制

    # ================================================================
    # 工艺压力假设；与架构读数、公开指标分开记录。
    # ================================================================
    # stage1_reading / dither_transfer_model / dither_enhancement_bits
    # 已定义在第一级 SADC 段（上文），此处不再重复。
    # "PDK 等效失配"对照用的面积律估算，仅用于校准压力测试：
    # 论文 [00] 没有披露 PDK 失配数值，本字段属于 [假设]。
    pdk_sigma_est_ppm: float = 1117.0

    # ================================================================
    # DAC拓扑与桥接寄生
    # ================================================================
    # "unary": 纯等权阵列对照。
    # "split" : 主阵列 n_main 个单位 + 子阵列 n_sub 个单位，经桥接电容 C_C 耦合
    #           总电平数 = n_main * n_sub，总单位数 = n_main + n_sub
    dac_arch: str = "unary"
    dac_n_main: int = 64  # 主阵列单位数
    dac_n_sub: int = 8  # 子阵列单位数（= 主 LSB 细分倍数）
    # 顶极板寄生：相对于子阵列总电容的比例（含比较器输入电容、走线、开关漏端）
    dac_parasitic_ratio: float = 0.02
    # 顶极板寄生的**芯片间散布**（相对值）。注意：桥接节点寄生散布对子 DAC
    # 权重的敏感度被 D = C_C + C_sub + c_p 放大：
    #   d(beta)/beta ≈ -d(c_p)/D，D ≈ C_sub -> 5% 散布即 ~1000 ppm 子权重误差。
    # 这是桥接 DAC 的真实设计约束（寄生必须小且可控，或子增益必须校准）。
    dac_parasitic_spread: float = 0.05
    # 桥接电容失配：C_C 的**相对**失配 sigma（直接量，单位 = 相对值）。
    # C_C 是单个器件：若用单位电容拼成 n 个并联，则 sigma_C_C ≈ sigma_eps/sqrt(n)；
    # 单个大电容则接近工艺的器件级失配。默认 1e-4 = 100 ppm，属 [假设]。
    dac_bridge_mismatch_sigma: float = 1e-4

    # ================================================================
    # 输入、参考和数字耦合的动态误差控制
    # ================================================================
    # --- (a) 输入建立：采样开关导通电阻 + 源阻抗，一阶 RC ---
    # 硬设计约束：20.5 pF @ 40MS/s 要建立到 0.1%，需 R_total ≈ 60 Ω
    # （Ts*0.45/(6.9*C)=1.6 ns）-> 采样开关必须自举。这是模型反推的规格。
    dyn_input_settling: bool = False
    input_network: InputNetworkParameters = field(default_factory=InputNetworkParameters)
    dyn_r_source: float = 30.0  # 驱动器 + 走线串联电阻 [ohm]
    dyn_r_on: float = 20.0  # 采样开关导通电阻 [ohm]（自举开关量级）
    dyn_t_sample_frac: float = 0.45  # 采样相时长 / Ts
    # 开关导通电阻随信号电平的调制（V_GS 随输入变）-> tau 与码相关 -> 真 INL
    dyn_ron_code_coeff: float = (
        0.1  # 生产split：Ron=Ron0·[1+rho·(Vin/Vfs)^2]；旧aggregate代理另有定义
    )

    # --- (b) 参考建立：每次切换从参考抽取电荷，有限去耦 + 有限恢复带宽 ---
    dyn_ref_settling: bool = False
    conversion: ConversionParameters = field(default_factory=ConversionParameters)
    dyn_c_decouple: float = 4.7e-6  # 参考去耦电容 [F]（片外典型量级）
    dyn_tau_ref: float = 20e-9  # 参考缓冲恢复时间常数 [s]
    dyn_t_conv_frac: float = 0.40  # 转换相时长 / Ts
    dyn_ref_dynamic_ratio: float = 1.0  # 旧aggregate参考代理；物理参考由实际有符号电荷驱动

    # --- (c) 数字串扰：数字走线到顶极板的耦合 ---
    dyn_crosstalk: bool = False
    dyn_c_xtalk_common: float = 1e-18  # 共模耦合电容 [F]（所有切换单位共享，DEM 无效）
    dyn_c_xtalk_unit: float = 5e-19  # 单位耦合电容 [F]（每个切换单位一份，DEM 有效）
    dyn_v_digital: float = 1.0  # 数字节点摆幅 [V]
    # 单调切换下每次转换的单位翻转数 A(k) = 2*min(k, N-k)（三角波，中码最大）

    # --- (d) 物理slice属性，制造后跨样本保持 ---
    # slice 间时间常数相对失配：tau_i = tau0*(1+delta_i)，delta_i ~ N(0, spread)。
    # 物理来源：18 个物理 slice 的开关 R_on / 走线寄生各自不同。
    # 后果：采集组 8 个 slice 的 eps_i 不同 -> 逐样本建立误差随 bank 交替出现
    # 周期性 -> f_S/2 与 f_S/2±f_IN 交织杂散（论文以 DAC BW=15×f_NYQ + spare
    # 轮换打散）。0 = 全部 slice 时间常数相同。
    slice_bw_spread: float = 0.0
    # slice 间采样时刻偏移（timing skew）：第 i 个 slice 的采样时钟沿在
    # t_n + delta_i，delta_i ~ N(0, sigma_t)，每颗芯片一次、跨样本持久。
    # held value ≈ x(t_n) + delta_i * dx/dt(t_n) —— 信号相关误差，与带宽失配
    # （tau 失配）正交：tau 失配误差 ∝ (x − v_top)，skew 误差 ∝ dx/dt。
    # 论文原文（交织误差四件套）："offset, gain, timing and bandwidth
    # mismatch artefacts of the DACs"；timing 由同扩散条 + dummy 压到
    # "estimated residual RMS timing mismatch is ~0.6ps"。
    # PPT p.21-22 校验锚点：10ps 失配仿真 -> f_S/2−fIN 杂散高出量化底 13dB
    # （绝对值依赖其仿真用的 fin/幅度，模型验收用标度律 + 杂散位置 + 洗牌抑制）。
    slice_timing_skew_s: float = 0.0
    # 逐slice输入参考offset，制造后跨样本保持。
    # V_os,i ~ N(0, sigma)，每颗芯片一次、跨样本持久；采集组保持值
    # x_hold = x + mean(V_os[acq]) -> 样本级误差 = mean(V_os[acq])（常数型）。
    # 固定 A/B 两组 -> (−1)^n 调制的**常数** -> 杂散在 f_S/2（不随 f_IN 平移），
    # 与 skew/带宽/增益杂散（f_S/2±f_IN，信号相关）构成判别性指纹。
    slice_offset_sigma_v: float = 0.0
    # c_sadc定义独立SADC的采样电容[F]；此旧比例仅保留序列化/API兼容。
    sadc_cap_ratio: float = 0.05  # 保留且不活跃；SADC噪声/物理采样负载使用c_sadc

    # ================================================================
    # 可选噪声预算与参考装载模型
    # ================================================================
    # 仅granularity转移模型使用此取整粒度；range与dual_port按各自公式。
    # Δ1是SADC名义决策步，ΔDAC是RDAC细栅格，不能混加两种码单位。
    dither_quant_transfer: str = "rdac"  # quantizer | rdac

    # 旧现象预算：白噪声乘10**(cost/20)，闪烁源被跳过。
    # 不含CBIAS采样/保持状态，不等于chopping_az的成对误差机理。
    ra_autozero: bool = False
    ra_autozero_cost_db: float = 1.6  # [披露]（PPT p.34-35）
    # ADC2 动态采样带宽（PPT +1.3 dB）：信号以宽带宽建立、噪声在采样瞬间以
    # 窄带宽收取。行为建模 = ADC2 输入噪声幅度 × ratio（<1）。
    # 适用域：本模型中 ADC2 输入节点 = RA 输出、无独立 ADC2 输入噪声源，
    # 故该开关作用于 RA 输出噪声（唯一存在的噪声），口径声明见 ra.py。
    adc2_dyn_bw_ratio: float | None = None  # None = off；10**(-1.3/20) -> +1.3 dB

    # --- (3) 1/f 噪声（PPT：转角 ~40 Hz）---
    # RA 折输入的闪烁噪声，PSD = S_f·(f_c/f)（f <= f_c），f_c 处 PSD = 白底。
    # 白底幅度 = flicker_white_ratio × sigma_sampling（默认 1 -> 转角处
    # 闪烁 PSD = kT/C 白底，与 PPT "噪声底 8.8 nV/√Hz、转角 40 Hz" 自洽）。
    # 生成 = 白噪声 FFT 塑形（确定性、可复现）；ra_autozero=True 时整体移除。
    flicker_corner_hz: float = 0.0
    flicker_white_ratio: float = 1.0

    # --- (4) RDAC 逐位装载（论文："conversion results are loaded as they
    #     develop"，跟随器逐位装载）---
    # 影响**参考建立误差的时序结构**：单次终态装载 = 全部电荷在转换开始
    # 抽取、只恢复 T_conv；逐位装载 = 电荷分 B 位步抽取、每步在剩余时间恢复
    # -> 动态分量的残余压降按指数权重下降。终态码相同 -> 静态分量不变
    # （DC 负载贯穿整个转换相），只改动态分量 —— 口径声明见 dynamics.py。
    rdac_bitwise_loading: bool = False
    rdac_bitwise_bits: int = 6  # 旧aggregate参考代理；物理事件序列用实际b1

    # ---------------- 其它非理想与随机 ----------------
    enable_sampling_noise: bool = True
    driver_noise_rms: float = 0.0  # 驱动器噪声（折输入），t1/t2 上同一实现
    seed: int = 1234

    # ================= 派生量 =================
    @property
    def lsb_target(self) -> float:
        """20 bit 目标步长（V）。

        Returns:
            目标 20 bit 步长（V），= 2·V_FS / 2^n_bits_target。
            [派生] 只由量程与目标位数决定，不含工艺或拟合参数。
        """
        return 2.0 * self.v_fs / (2.0**self.n_bits_target)

    @property
    def delta1(self) -> float:
        """第一级量化步长（V）。

        Returns:
            第一级量化步长 Δ1（V），= 2·V_FS / 2^b1。
            [派生] 直接决定残差摆幅、RA 输出范围与后端位预算。
        """
        return 2.0 * self.v_fs / (2.0**self.b1)

    @property
    def stage1_levels(self) -> int:
        """第一级对未知输入拥有的名义判决区间数。

        Returns:
            2**b1个判决区间。已知dither码不增加未知输入的信息位数。
        """
        return int(2**self.b1)

    @property
    def stage1_resolution_bits(self) -> float:
        """有效第一级分辨率（bit）= log2(判决电平数)。

        Returns:
            第一级有效分辨率（bit），= log2(stage1_levels)。[派生]
        """
        return float(self.b1)

    @property
    def backend_bits_required(self) -> int:
        """后端为达到 n_bits_target 所需的最少位数。

        Returns:
            后端为达到 n_bits_target 所需的最少位数（bit），
            = max(n_bits_target − b1, 1)。[派生]
        """
        return max(int(self.n_bits_target) - int(self.b1), 1)

    @classmethod
    def legacy_v61(cls) -> Config:
        """生成保留的6bit判决/旧码字读数对照配置。

        Returns:
            b1=6、15bit后端和旧granularity转移模型；仅用于既有实验对照，
            不把6bit判决加已知dither称为9bit未知输入量化。
        """
        return cls(
            b1=6,
            stage1_reading="legacy_codeword",
            # 此旧对照的栅格比为512/64=8，因此保留3bit增强参数。
            dither_enhancement_bits=3,
            dither_transfer_model="granularity",
            adc2_n_bits=15,
            adc2_v_min=-0.30,
            adc2_v_max=3.30,
            ra_v_clip=3.6,
        )

    @classmethod
    def paper_consistent(cls) -> Config:
        """Return the historical seven-decision-bit hypothesis.

        This preserves the legacy grid-ratio interpretation for comparison. It does not establish
        that 7 decisions plus 2 dither bits reproduce the paper's nine quantization bits. See
        `paper_literal` for the independent range/decision candidate and ADR 0003 for the
        distinction.

        Returns:
            Seven SADC bits, 14 backend bits and a [-0.15, 1.65] V backend.
        """
        base = cls()
        span = base.g_actual * (2.0 * base.v_fs / (2.0**7))
        v_lo, v_hi = -0.10 * span, 1.10 * span
        # 后端分辨能力：Δ2 / G0 <= LSB20  =>  n_bits >= log2(span_total/(LSB20*G0))
        total = v_hi - v_lo
        n_bits = int(math.ceil(math.log2(total / (base.lsb_target * base.g_actual))))
        return cls(
            b1=7,
            stage1_reading="paper_consistent",
            dither_transfer_model="range",
            adc2_n_bits=n_bits,
            adc2_v_min=v_lo,
            adc2_v_max=v_hi,
            ra_v_clip=1.2 * v_hi,
        )

    @classmethod
    def paper_literal(cls, **overrides) -> Config:
        """Build a nine-decision-bit candidate with independent dither range.

        A 63-main/8-sub complete-count topology is an explicit modeling choice, not a claim that the
        paper discloses these physical capacitor counts. With beta=1/8 it spans 512 uniform codes
        over the full 6 V input range. Dual-port dither is a separately declared ideal
        charge-injection model.

        Args:
            **overrides: Explicit configuration overrides; backend settings are rederived unless
                explicitly supplied.

        Returns:
            A legal 9b decision candidate with a 12b backend at nominal settings.
        """
        from dataclasses import replace

        base = cls(
            b1=9,
            stage1_reading="paper_literal",
            dac_arch="split",
            dac_n_main=63,
            dac_n_sub=8,
            dac_complete_range=True,
            dither_transfer_model="dual_port",
            dither_enhancement_bits=2,
            dither_discrete=True,
            dem_bridge_enable=True,
        )
        base = replace(base, **overrides)
        span = base.g0 * base.delta1
        lo, hi = -0.1 * span, 1.1 * span
        return replace(
            base,
            adc2_v_min=base.adc2_v_min if "adc2_v_min" in overrides else lo,
            adc2_v_max=base.adc2_v_max if "adc2_v_max" in overrides else hi,
            adc2_n_bits=(
                base.adc2_n_bits
                if "adc2_n_bits" in overrides
                else math.ceil(math.log2((hi - lo) / (base.g0 * base.lsb_target)))
            ),
            ra_v_clip=base.ra_v_clip if "ra_v_clip" in overrides else 1.2 * hi,
        )

    @classmethod
    def rtl_structural(cls, **overrides) -> Config:
        """Build the explicit 16-phase timing profile used by the RTL.

        This is an engineering timing contract, not a statement about the unpublished silicon clock.
        Callers may override other model parameters; incompatible timing or geometry is rejected by
        ``check_legal`` .
        """
        fs = overrides.get("fs", cls().fs)
        if not math.isfinite(fs) or fs <= 0:
            raise ConfigError("rtl_structural requires a positive finite sample rate")
        overrides.setdefault(
            "conversion",
            ConversionParameters(quantizer_time_s=10 / (16 * fs), rdac_event_timing="rtl16"),
        )
        overrides.setdefault("dyn_t_conv_frac", 6 / 16)
        overrides.setdefault("rdac_bitwise_loading", True)
        overrides.setdefault("dither_transfer_model", "range")
        overrides.setdefault(
            "dem_mode", "permute" if overrides.get("dem_enable", False) else "rotate"
        )
        cfg = cls.paper_literal(**overrides)
        cfg.check_legal()
        return cfg

    @property
    def nominal_rdac_step(self) -> float:
        """Nominal RDAC input-referred step [V], independent of true capacitors.

        Returns:
            Fine step from the declared signal-charge topology.
        """
        if self.dac_arch == "unary":
            return self.rdac_step
        beta = 1.0 / self.dac_n_sub
        return 2.0 * self.v_fs * beta / (self.dac_n_main + beta * self.dac_n_sub)

    @property
    def dither_rdac_ratio(self) -> float:
        """Nominal dither amplitude ratio for the explicit dual-port model.

        Returns:
            RDAC injected voltage divided by quantizer injected voltage;
            zero for legacy models without an RDAC injection port.
        """
        return (
            2.0**self.dither_enhancement_bits if self.dither_transfer_model == "dual_port" else 0.0
        )

    @property
    def n_units_sig(self) -> int:
        """参与信号量程的单位数。

        Returns:
            参与信号量程的单位数（个），
            = n_active·n_unit_per_slice − n_units_headroom。[派生]
        """
        return self.n_active * self.n_unit_per_slice - self.n_units_headroom

    @property
    def rdac_step(self) -> float:
        """RDAC 单位步长（V）。

        Returns:
            RDAC 单位步长（V），= 2·V_FS / n_units_sig。[派生]
        """
        return 2.0 * self.v_fs / self.n_units_sig

    @property
    def units_per_lsb1(self) -> int:
        """一个 Delta1 对应多少个 RDAC 单位。

        Returns:
            一个 Δ1 对应的 RDAC 单位数（个），= n_units_sig // 2^b1。
            [派生] 向下取整，除不尽的余数构成量化死区。
        """
        return self.n_units_sig // (2**self.b1)

    @property
    def delta2(self) -> float:
        """ADC2 步长（V）。

        Returns:
            ADC2 步长 Δ2（V），
            = (adc2_v_max − adc2_v_min) / 2^adc2_n_bits。[派生]
        """
        return (self.adc2_v_max - self.adc2_v_min) / (2.0**self.adc2_n_bits)

    @property
    def c_unit0(self) -> float:
        """cap_scale=1 时的单位电容（F）。

        Returns:
            cap_scale=1 时的单位电容（F）。= c_total0 / (n_active·n_unit_per_slice)；
            [派生] 自 c_total0（[披露] 20.5 pF）。
        """
        return self.c_total0 / (self.n_active * self.n_unit_per_slice)

    def c_active_nominal(self, cap_scale: float | None = None) -> float:
        """等权预算中一次转换实际活跃组的名义采样电容。

        Args:
            cap_scale: 面积倍率；None使用当前cap_scale。

        Returns:
            活跃单位数乘单位电容[F]；不含未参与本次采样的备用slice。
            split信号/负载/噪声等效电容由物理pool分别计算。
        """
        s = self.cap_scale if cap_scale is None else cap_scale
        return (
            self.c_unit0 * self.n_active * self.n_unit_per_slice
            - self.n_units_headroom * self.c_unit0
        ) * s

    @property
    def g_actual(self) -> float:
        """固定增益模型及预算使用的标量G=g0*(1+gain_error)。

        Returns:
            无量纲参考增益。charge生产路径的逐样本真实G由选中物理电容/Cf给出，
            不能用此属性代替该数组。
        """
        return self.g0 * (1.0 + self.gain_error)

    # ---------------- 分段 DAC 派生量 ----------------
    @property
    def dac_n_units(self) -> int:
        """返回当前拓扑用于权重几何的单位数。

        Returns:
            unary为一组n_active*n_unit_per_slice；split为每物理slice的
            n_main+n_sub。split权重矩阵形状为(n_slices,dac_n_units)。
        """
        if self.dac_arch == "unary":
            return self.n_active * self.n_unit_per_slice
        return int(self.dac_n_main) + int(self.dac_n_sub)

    @property
    def dac_levels(self) -> int:
        """当前拓扑可寻址的名义RDAC码数。

        Returns:
            split为(n_main+int(dac_complete_range))*n_sub；
            unary为n_active*n_unit_per_slice。合法码为0..dac_levels-1。
        """
        if self.dac_arch == "unary":
            return self.n_active * self.n_unit_per_slice
        return (int(self.dac_n_main) + int(self.dac_complete_range)) * max(int(self.dac_n_sub), 1)

    @property
    def dac_bits(self) -> float:
        """DAC 电平数对应的位数 log2(dac_levels)。

        Returns:
            DAC 电平数对应位数（bit，无量纲）。= log2(dac_levels)；[派生]。
        """
        import math as _m

        return _m.log2(self.dac_levels)

    def dac_unit_cap(self, cap_scale: float | None = None) -> float:
        """按活跃组总电容预算分配split名义单位电容。

        Args:
            cap_scale: 面积倍率；None使用当前cap_scale。

        Returns:
            活跃组等效单位电容[F]。分母包含main、sub和桥接电容的等效单位数，
            并非一律加1。物理pool进一步除以n_active得到每slice的单位电容。
        """
        s = self.cap_scale if cap_scale is None else cap_scale
        if self.dac_arch == "unary":
            return self.c_unit0 * s
        n_tot = int(self.dac_n_main) + int(self.dac_n_sub)
        bridge_units = (
            self.dac_n_sub * (1 + self.dac_parasitic_ratio) / (self.dac_n_sub - 1)
            if self.dac_n_sub > 1
            else 1.0
        )
        n_alloc = n_tot + bridge_units
        return self.c_total0 * s / n_alloc

    def dac_step_main(self) -> float:
        """主阵列一个单位的名义输入等效电压步长。

        Returns:
            split为dac_n_sub*nominal_rdac_step[V]；unary为rdac_step。
            complete-range几何已包含在nominal_rdac_step的归一化中。
        """
        if self.dac_arch == "unary":
            return self.rdac_step
        return self.nominal_rdac_step * self.dac_n_sub

    def dac_step_sub(self) -> float:
        """子阵列单位步长；unary 或无子阵列时为 0。

        Returns:
            子阵列单位步长（V），= dac_step_main() / n_sub；
            unary 或 n_sub = 0 时为 0 V。[派生]
        """
        if self.dac_arch == "unary" or self.dac_n_sub == 0:
            return 0.0
        return self.dac_step_main() / float(self.dac_n_sub)

    # ---------------- 采样态 dither（专利 [10]）----------------
    @property
    def dither_units_total(self) -> int:
        """参与 dither 的物理单位数 = 2D（半接 +V_FS，半接 −V_FS）。

        Returns:
            参与 dither 的物理单位数（个），= 2·dither_units_range；
            dither 未启用（dither_mode 不等于 sampling）时为 0。[派生]
        """
        if self.dither_mode != "sampling":
            return 0
        return int(round(2.0 * self.dither_units_range))

    @property
    def dither_alpha(self) -> float:
        """名义采样mask移走的信号电荷占比之补α。

        unary为1-2D/N；split为1-2D*w_bank/C_signal_nom，main权重为Cu，sub权重为beta*Cu。
        固定物理末2D个单位不接信号，不能用统一单位数比替代。

        Returns:
            无量纲名义α；非sampling返回1。物理pool按选中slice重新计算实际α，
            数字端只能用名义值或独立估得的有效权重，不读取该物理真值。
        """
        if self.dither_mode != "sampling":
            return 1.0
        d = self.dither_units_total
        if self.dac_arch == "unary":
            n_tot = self.n_active * self.n_unit_per_slice
            return max(1.0 - d / n_tot, 1e-6)
        # split：按名义参数（与数字端可见栅格同一套）推导
        c_u = self.dac_unit_cap()
        bn = self.dac_n_sub * c_u
        cp = self.dac_parasitic_ratio * bn
        cc = (bn + cp) / (self.dac_n_sub - 1.0) if self.dac_n_sub > 1 else c_u
        beta = cc / (cc + bn + cp) if (cc + bn + cp) > 0 else 1.0
        c_sig = self.dac_n_main * c_u + beta * bn
        w_unit = c_u if self.dither_split_bank == "main" else beta * c_u
        return max(1.0 - d * w_unit / c_sig, 1e-6)

    def sigma_sampling(self, cap_scale: float | None = None) -> float:
        """RDAC 采样热噪声 sigma（**标称口径**，用于预算，逐样本噪声见 sigma_sampling_c）。

        Args:
            cap_scale: 电容缩放因子（无量纲，> 0）。[派生]

        Returns:
            标称口径的 kT/C 采样热噪声 rms（Vrms），按缩放后总电容计算。
            [派生] 仅用于预算；逐样本噪声请用 sigma_sampling_c。
        """
        s = self.cap_scale if cap_scale is None else cap_scale
        c_eff = self.c_total0 * s
        return math.sqrt(self.chi * K_B * TEMP_K / c_eff)

    @staticmethod
    def sigma_sampling_c(c_active: float) -> float:
        """按本次活跃噪声等效电容给出差分热噪声参考。

        Args:
            c_active: 当前样本噪声等效电容[F]；备用slice不贡献该电容。

        Returns:
            sqrt(2*kT/c_active)[输入节点Vrms]，非正输入返回0。
            本辅助函数固定χ=2；生产capture使用cfg.chi。
        """
        return math.sqrt(2.0 * K_B * TEMP_K / c_active) if c_active > 0 else 0.0

    def sigma_sadc_sampling(self) -> float:
        """SADC 采样电容上的 kT/C 噪声 rms（V）。

        Returns:
            SADC 采样电容上的 kT/C 噪声 rms（Vrms）。[派生]
        """
        return math.sqrt(self.chi * K_B * TEMP_K / self.c_sadc)

    def sigma_mismatch(self, cap_scale: float | None = None) -> float:
        """探索性失配假设 sigma_eps(s) = sigma0 / sqrt(s)。

        Args:
            cap_scale: 电容缩放因子（无量纲，> 0）。[派生]

        Returns:
            探索性失配假设 sigma_eps（无量纲相对误差），= sigma0 / sqrt(s)。
            [假设] 未与单位电容面积挂钩，仅供扫描趋势，不得用于良率结论。
        """
        s = self.cap_scale if cap_scale is None else cap_scale
        return 0.0 if not self.mismatch_enable else self.mismatch_sigma0 / math.sqrt(s)

    def sigma_mismatch_unit(self, c_unit: float) -> float:
        """单位电容面积律假设σ=σ0*sqrt(Cunit_ref/Cunit)。

        Args:
            c_unit: 调用方给定的单位电容[F]；物理pool使用每slice单位，aggregate chip使用其等效单位，
                二者不是同一个统计构造。

        Returns:
            无量纲相对σ；关闭失配返回0。参考c_unit0不随cap_scale同步缩放，
            因而缩小电容不会把面积律因子约掉。本函数不是工艺实测或良率预测。
        """
        if not self.mismatch_enable:
            return 0.0
        # ref固定在未缩放的名义单位电容，避免分子分母同步缩放后约掉面积律。
        ref = self.c_unit0
        return self.mismatch_sigma0 / math.sqrt(max(c_unit / ref, 1e-9))

    def mismatch_weights(self) -> tuple:
        """返回 (a_global, a_slice, a_unit) 的**标准差**权重，平方和为 1。

        注意：字段名 mismatch_split 存的是**方差**占比，这里开方转成标准差权重。

        Returns:
            (a_global, a_slice, a_unit) 三元组，标准差权重（无量纲），
            三者平方和为 1。[派生] 注意字段 mismatch_split 存的是方差占比。
        """
        g, sl, u = self.mismatch_split
        tot = g + sl + u
        if tot <= 0:
            return (0.0, 0.0, 1.0)
        return (math.sqrt(g / tot), math.sqrt(sl / tot), math.sqrt(u / tot))

    def ktc_dt(self) -> float:
        """KTC 观测相时长 = ktc_dt_fraction / fs。

        Returns:
            KTC 观测相时长 T_w（s），= ktc_dt_fraction / fs。[派生]
        """
        return self.ktc_dt_fraction / self.fs

    def ktc_tau_a(self) -> float:
        """观测通路一阶时间常数 tau_a = 1/(2*pi*f_bw)。None 带宽 = 理想 -> 0。

        Returns:
            观测通路一阶时间常数 τ_a（s），= 1 / (2π·f_bw)；
            带宽为 None（理想）时返回 0 s。[派生]
        """
        if self.ktc_observe_bw_hz is None:
            return 0.0
        return 1.0 / (2.0 * math.pi * float(self.ktc_observe_bw_hz))

    def ktc_settle_residual(self) -> float:
        """窗口末端对**阶跃**（n_R）的建立残差 eps = exp(-2*pi*f_bw*dt) = 1 - eta_n。

        None（未设带宽）= 理想，返回 0。

        Returns:
            建立残差 eps（无量纲）。= exp(-2π·f_bw·dt)；带宽 None 时返回 0；
            [研究扩展]。
        """
        if self.ktc_observe_bw_hz is None:
            return 0.0
        return math.exp(-2.0 * math.pi * float(self.ktc_observe_bw_hz) * self.ktc_dt())

    def ktc_eta_n(self) -> float:
        """n_R 的建立系数：阶跃在窗口开始就存在，eta_n = 1 - exp(-T_w/tau_a)。

        Returns:
            n_R 的建立系数 η_n（无量纲），= 1 − exp(−T_w / τ_a)。[派生]
        """
        return 1.0 - self.ktc_settle_residual()

    def ktc_eta_x(self) -> float:
        """线性斜坡Δx在一阶观测窗口内的建立系数。

        eta_x=1-(tau/Tw)*(1-exp(-Tw/tau))，不同于窗口开始已存在的阶跃噪声eta_n。
        低带宽极限eta_x约为eta_n/2；理想时间常数/零窗口按现有约定返回1。

        Returns:
            无量纲斜坡建立系数；仅用于KTC研究扩展。
        """
        t_w = self.ktc_dt()
        tau = self.ktc_tau_a()
        if tau == 0.0 or t_w == 0.0:
            return 1.0
        return 1.0 - (tau / t_w) * (1.0 - math.exp(-t_w / tau))

    def kappa_eff(self) -> float:
        """生效的 KTC 观测系数 kappa；KTC 未启用时为 0。

        Returns:
            生效的 KTC 观测系数 κ（无量纲）；KTC 未启用时为 0。[派生]
        """
        if not self.ktc_enable:
            return 0.0
        if self.ktc_kappa is not None:
            return float(self.ktc_kappa)
        return self.g0 / self.ktc_gain_n

    def beta_eff(self) -> float:
        """KTC预算的名义beta=(1+beta_error)*kappa*G_N/g0。

        Returns:
            无量纲参考值；KTC关闭返回0。电荷模型的逐样本G及观测响应由实际
            观测路径处理，此标量不证明增益/噪声相消已经在电路实现。
        """
        if not self.ktc_enable:
            return 0.0
        return (1.0 + self.ktc_beta_error) * self.kappa_eff() * self.ktc_gain_n / self.g0

    # ================= 合法性检查（在仿真入口调用）=================
    def legality_violations(self) -> list[str]:
        """列出不能进入已实现仿真分支的配置原因。

        检查枚举、数值域、拓扑与profile时序的一致性；不把有限余量不足当成非法。
        validate另行给名义预算，采样输入是否越过各节点窗口仍需运行时标志。

        Returns:
            违规消息列表；空列表表示通过此配置合同，不表示满足性能目标。
        """
        bad: list[str] = []
        if not isinstance(self.input_network, InputNetworkParameters):
            bad.append(
                "input_network requires InputNetworkParameters; use Config.from_dict for JSON"
            )
        if not isinstance(self.conversion, ConversionParameters):
            bad.append("conversion requires ConversionParameters; use Config.from_dict for JSON")
        if bad:
            return bad
        bad.extend(self.input_network.violations())
        bad.extend(self.conversion.violations())
        if self.conversion.rdac_event_timing == "rtl16" and (
            not math.isfinite(self.fs)
            or self.fs <= 0
            or self.b1 != 9
            or self.n_slices != 18
            or self.n_active != 8
            or self.dac_n_main != 63
            or self.dac_n_sub != 8
            or self.dac_arch != "split"
            or not self.dac_complete_range
            or self.adc2_n_bits != 12
            or self.n_bits_target != 20
            or self.dem_mode != ("permute" if self.dem_enable else "rotate")
            or self.dither_mode not in ("off", "sampling", "quantizer")
            or not self.dither_discrete
            or self.dither_units_range != 2
            or self.dither_split_bank != "sub"
            or self.dither_transfer_model != "range"
            or not self.rdac_bitwise_loading
            or not math.isclose(self.dyn_t_conv_frac, 6 / 16, rel_tol=0, abs_tol=1e-15)
            or not math.isclose(
                self.conversion.quantizer_time_s,
                10 / (16 * self.fs),
                rel_tol=1e-12,
                abs_tol=0,
            )
        ):
            bad.append("rtl16 timing requires the 9-bit, 18/8, 63+8 structural schedule")
        if self.dyn_ref_settling or self.conversion.dynamic:
            if not math.isfinite(self.dyn_tau_ref) or self.dyn_tau_ref <= 0:
                bad.append("dyn_tau_ref must be finite and positive [s]")
            if not math.isfinite(self.dyn_c_decouple) or self.dyn_c_decouple <= 0:
                bad.append("dyn_c_decouple must be finite and positive [F]")
            if not 0 < self.dyn_t_conv_frac <= 1:
                bad.append("dyn_t_conv_frac must be in (0, 1]")
            if (
                self.fs > 0
                and self.conversion.quantizer_time_s + self.dyn_t_conv_frac / self.fs
                > (1 + 1e-12) / self.fs
            ):
                bad.append("quantizer and RA/ADC2 phases must fit within one sample period")
        if self.dyn_input_settling:
            if (
                self.fs > 0
                and self.input_network.pretrack_mode != "residue"
                and self.input_network.pretrack_time_s + self.dyn_t_sample_frac / self.fs
                > 1 / self.fs
            ):
                bad.append("pretracking and acquisition must fit within one sample period")
            if not math.isfinite(self.dyn_r_source) or self.dyn_r_source < 0:
                bad.append("dyn_r_source must be finite and nonnegative [ohm]")
            if not math.isfinite(self.dyn_r_on) or self.dyn_r_on <= 0:
                bad.append("dyn_r_on must be finite and positive [ohm]")
            if not 0 < self.dyn_t_sample_frac <= 1:
                bad.append("dyn_t_sample_frac must be in (0, 1]")
            if not math.isfinite(self.dyn_ron_code_coeff) or not 0 <= self.dyn_ron_code_coeff <= 1:
                bad.append("dyn_ron_code_coeff must be in [0, 1] under the declared Config domain")
        for field_name, allowed in LEGAL_VALUES.items():
            actual = getattr(self, field_name)
            if actual not in allowed:
                bad.append(
                    f"{field_name}={actual!r} 不是已实现取值（可选：{', '.join(allowed)}）；"
                    f"此前会静默退化为默认分支"
                )

        # stage1_reading与b1的一致性由READING_B1单点维护。
        expected_b1 = READING_B1.get(self.stage1_reading)
        if expected_b1 is not None and self.b1 != expected_b1:
            bad.append(
                f"stage1_reading={self.stage1_reading!r} 对应的 b1 应为 {expected_b1}，"
                f"但 b1={self.b1} —— 标签说一种读法、参数是另一种。用 "
                f"Config.paper_consistent() / Config.legacy_v61() 切换读数，"
                f"或显式把 b1 设成与该标签相符的值"
            )

        # 拒绝非有限值，避免其进入后续电荷或数值计算。
        if not math.isfinite(self.fs) or self.fs <= 0.0:
            bad.append(f"fs={self.fs!r} 必须为正有限值（采样率 [Hz]）")
        if not math.isfinite(self.g0) or self.g0 <= 0:
            bad.append(f"g0={self.g0!r} 必须为有限正值（名义级间增益）")
        for name in ("cap_scale", "chi", "c_sadc", "ra_v_clip"):
            value = getattr(self, name)
            if not math.isfinite(value) or value <= 0:
                bad.append(f"{name} must be finite and positive")
        for name in (
            "driver_noise_rms",
            "ra_out_noise_rms",
            "ktc_noise_n",
            "mismatch_sigma0",
            "sadc_mismatch_sigma",
            "dac_bridge_mismatch_sigma",
            "dac_parasitic_spread",
            "slice_bw_spread",
            "slice_timing_skew_s",
            "slice_offset_sigma_v",
        ):
            value = getattr(self, name)
            if value is None and name == "ra_out_noise_rms":
                continue
            if value is None or not math.isfinite(value) or value < 0:
                bad.append(f"{name} must be finite and nonnegative")
        if not math.isfinite(self.gain_error) or (
            self.ra_gain_model == "fixed" and self.gain_error <= -1
        ):
            bad.append("gain_error must be finite and preserve positive fixed-mode gain")
        if self.n_slices < 1:
            bad.append(f"n_slices={self.n_slices!r} 必须 >= 1（slice 池大小）")
        if self.n_unit_per_slice < 1:
            bad.append(f"n_unit_per_slice={self.n_unit_per_slice!r} 必须 >= 1")
        if not math.isfinite(self.c_total0) or self.c_total0 <= 0:
            bad.append(f"c_total0={self.c_total0!r} 必须为有限正值（RDAC 总采样电容 [F]）")
        if not math.isfinite(self.c_feedback0) or self.c_feedback0 <= 0:
            bad.append(f"c_feedback0={self.c_feedback0!r} 必须为有限正值（RA 反馈电容 [F]）")
        # 拒绝非有限值，避免其进入后续电荷或数值计算。
        if not math.isfinite(self.v_fs) or self.v_fs <= 0.0:
            bad.append(f"v_fs={self.v_fs!r} 必须为正有限值（满幅峰值 [V]）")
        if self.n_bits_target < 1:
            bad.append(f"n_bits_target={self.n_bits_target!r} 必须 >= 1")
        if (
            isinstance(self.adc2_n_bits, bool)
            or not isinstance(self.adc2_n_bits, int)
            or not 1 <= self.adc2_n_bits <= 30
        ):
            bad.append("adc2_n_bits must be an integer in [1, 30]")
        if (
            not math.isfinite(self.adc2_v_min)
            or not math.isfinite(self.adc2_v_max)
            or self.adc2_v_min >= self.adc2_v_max
        ):
            bad.append("ADC2 voltage bounds must be finite and strictly increasing")
        if self.split_feedback_cap_f is not None and (
            not math.isfinite(self.split_feedback_cap_f) or self.split_feedback_cap_f <= 0
        ):
            bad.append("split_feedback_cap_f must be finite and positive [F]")
        if self.dither_mode == "sampling":
            D = self.dither_units_range
            available = (
                (self.dac_n_main if self.dither_split_bank == "main" else self.dac_n_sub)
                if self.dac_arch == "split"
                else self.n_units_sig
            )
            if not math.isfinite(D) or D < 0 or int(D) != D or available < 2 * D:
                bad.append(
                    "sampling dither requires integer D >= 0 and 2D available bank capacitors"
                )
        if self.dac_arch == "split":
            if self.dac_n_main < 1:
                bad.append(f"dac_n_main={self.dac_n_main!r} 必须 >= 1（主阵列单位数）")
            if self.dac_n_sub < 2:
                bad.append(
                    f"dac_n_sub={self.dac_n_sub!r} 必须 >= 2：桥接电容名义值 "
                    f"C_C=(n_sub*c_u+c_p)/(n_sub-1) 在 n_sub=1 处发散"
                )
            # 第一级读数必须能被 DAC 表达，否则残差会被静默推出 ADC2 窗口
            if self.dac_levels < 2**self.b1:
                bad.append(
                    f"2**b1 = {2**self.b1} 超过 DAC 电平数 {self.dac_levels}："
                    f"该第一级读数无法被这个 DAC 表达"
                )
            if self.dac_levels % (2**self.b1):
                bad.append("DAC levels must be divisible by the number of SADC decisions")
        if self.dither_transfer_model == "dual_port" and not self.dither_discrete:
            bad.append("dual_port dither requires discrete nominal RDAC-grid injection")
        if not 0 <= self.dither_enhancement_bits <= 16:
            bad.append("dither_enhancement_bits must be between 0 and 16")
        return bad

    def check_legal(self) -> None:
        """Refuse to simulate an illegal configuration.

        Raises:
            ConfigError: 若 :meth:`legality_violations` 非空；异常消息逐条列出违规项，
                便于直接定位拼写错误。
        """
        bad = self.legality_violations()
        if bad:
            raise ConfigError(
                "配置不合法，已被仿真入口拒绝（不是「检查表里写着 FAIL」，是直接拒绝）：\n  - "
                + "\n  - ".join(bad)
            )

    # ================= 边界检查 =================
    def validate(self, verbose: bool = True) -> dict:
        """报告名义栅格、噪声及摆幅预算，不进行波形仿真。

        Args:
            verbose: 是否打印预算表；不改变计算值。

        Returns:
            每项为(值,门限或None,通过标志,单位)。None门限仅记录。
            这些条件不扫描输入幅度或逐样本dither，不能代替SADC/RA/ADC2余量统计，
            也不能由此推出目标工艺、动态建立或完整噪声性能通过。
        """
        lsb = self.lsb_target
        d1 = self.delta1
        d2 = self.delta2
        g = self.g_actual

        # (实测值, 阈值|None, 是否通过, 单位)：阈值为 None 表示"只记录不判定"。
        checks: dict[str, tuple[float, float | None, bool, str]] = {}

        # 名义RDAC码数须覆盖2**b1个决策，且与每个判决的RDAC步数整除。
        dac_levels = self.dac_levels if self.dac_arch == "split" else self.n_units_sig
        levels_needed = 2**self.b1
        checks["第一级读数与 DAC 拓扑自洽（2**b1 <= DAC 电平数）"] = (
            float(levels_needed),
            float(dac_levels),
            levels_needed <= dac_levels,
            "级",
        )
        # 同一读数下 units_per_lsb1 必须能整除，否则栅格对不齐
        if dac_levels % levels_needed != 0:
            checks["第一级栅格可整除（DAC 电平数 % 2**b1 == 0）"] = (
                float(dac_levels % levels_needed),
                0.0,
                False,
                "级",
            )

        # 历史栅格假设比较增强位数与log2(DAC码数/决策数)；dual_port另核对独立端口电压比。
        enh_expected = math.log2(dac_levels / levels_needed) if levels_needed else float("nan")
        checks["dither 增强位数 == log2(DAC 电平数 / 2**b1)"] = (
            float(self.dither_enhancement_bits),
            float(enh_expected),
            (
                levels_needed > 0
                and dac_levels % levels_needed == 0
                and (
                    self.dither_transfer_model == "dual_port"
                    or int(self.dither_enhancement_bits) == int(round(enh_expected))
                )
            ),
            "bit",
        )
        if self.dither_transfer_model == "dual_port":
            # A separate port ratio is not a statement about decision bits.
            checks.pop("dither 增强位数 == log2(DAC 电平数 / 2**b1)")
            checks["dither 双端口名义幅度比"] = (
                self.dither_rdac_ratio,
                4.0,
                self.dither_rdac_ratio == 4.0,
                "V/V",
            )

        # 判据 1：后端分辨能力  Delta2 / G0 <= LSB_target
        step_referred = d2 / g
        checks["后端分辨能力 Delta2/G0 <= LSB20"] = (
            step_referred,
            lsb,
            step_referred <= lsb,
            "V 折输入",
        )

        # 判据 2：残差是否溢出  G0 * r_max < V_ADC2,pk
        r_max = d1  # 单极性残差上限（含 DAC 误差前的名义值）
        vra_max = g * r_max
        checks["残差峰值 G0*r_max < ADC2 上限"] = (
            vra_max,
            self.adc2_v_max,
            vra_max < self.adc2_v_max,
            "V @ RA 输出",
        )

        # 名义残差中心是否落在 ADC2 范围中点附近
        centre = g * d1 / 2.0
        mid = (self.adc2_v_min + self.adc2_v_max) / 2.0
        checks["残差中心落在 ADC2 范围中点"] = (
            centre,
            mid,
            abs(centre - mid) < 0.05 * (self.adc2_v_max - self.adc2_v_min),
            "V",
        )

        # 噪声预算
        sn = self.sigma_sampling()
        checks["RDAC kT/C 噪声 sigma"] = (sn, None, True, "V")
        checks["kT/C 单独可达 DR"] = (
            20 * math.log10((self.v_fs / math.sqrt(2)) / sn),
            None,
            True,
            "dB",
        )

        # 失配导致的残偏移：SADC/RDAC 增益失配被 G 放大后必须仍在 ADC2 内
        off_amp = g * self.sadc_rdac_gain_mismatch * self.v_fs
        margin = (self.adc2_v_max - g * d1) + (-self.adc2_v_min)
        checks["SADC/RDAC 失配占用余量"] = (off_amp, margin, off_amp < margin, "V @ RA 输出")
        checks["SADC/RDAC 失配 => 等效匹配位数"] = (
            -math.log2(abs(self.sadc_rdac_gain_mismatch)) if self.sadc_rdac_gain_mismatch else 99.0,
            12.0,
            (abs(self.sadc_rdac_gain_mismatch) <= 2**-12) if self.sadc_rdac_gain_mismatch else True,
            "bit",
        )

        # 量化器侧 dither 的余项占用 ADC2 窗口（stage21 的判据来源）
        if self.dither_mode == "quantizer":
            gran = self.rdac_step if self.dither_quant_transfer == "rdac" else self.delta1
            margin_d = min(self.adc2_v_max - self.g0 * self.delta1, -self.adc2_v_min)
            checks["量化器 dither 余项占用 ADC2 窗口"] = (
                self.g0 * gran / 2.0,
                margin_d,
                self.g0 * gran / 2.0 < margin_d,
                "V（余项峰值 ×G0）",
            )

        # KTC校正在数字域扣除，不直接占ADC2模拟窗口。
        # 实际观测节点受|G_N*dx|与ra_v_clip约束；摆幅通过不代表建立带宽或噪声满足。
        if self.ktc_enable:
            f_bw = (self.ra_v_clip / self.ktc_gain_n) / (2 * math.pi * self.v_fs * self.ktc_dt())
            checks["KTC 观测通路摆幅上限 f_max (满幅)"] = (f_bw, 5e6, f_bw >= 5e6, "Hz")
            dx_ny = 2 * math.pi * (self.fs / 2) * self.ktc_dt() * self.v_fs
            checks["KTC 提取通路 Nyquist 摆幅 < clip"] = (
                self.ktc_gain_n * dx_ny,
                self.ra_v_clip,
                self.ktc_gain_n * dx_ny < self.ra_v_clip,
                "V",
            )
            # 给出此提取窗口的一阶建立带宽预算，不作为器件实测规格。
            for eps_budget in (0.10, 0.01):
                need = -math.log(eps_budget) / (2 * math.pi * self.ktc_dt())
                checks[f"KTC 观测带宽需求 (eps={eps_budget:.0%})"] = (
                    need,
                    None,
                    True,
                    "Hz（一阶模型，非最终规格）",
                )
            if self.ktc_observe_bw_hz is not None:
                eps = self.ktc_settle_residual()
                checks["KTC 观测建立残差 eps（当前带宽）"] = (
                    eps,
                    0.01,
                    eps <= 0.01,
                    "ratio -> beta 误差 = eps",
                )

        # 电荷一致模型的自洽性：C_F 名义值应等于活跃采样电容 / 名义增益
        if self.ra_gain_model == "charge":
            c_active_nom = (
                self.c_unit0 * self.n_active * self.n_unit_per_slice
                - self.n_units_headroom * self.c_unit0
            )
            cf_expect = c_active_nom / self.g0
            checks["C_F 名义 = C_active/G0（电荷一致）"] = (
                self.c_feedback0,
                cf_expect,
                abs(self.c_feedback0 / cf_expect - 1.0) < 0.02,
                "F",
            )

        # ---- 分段 DAC 结构自洽性 ----
        if self.dac_arch == "split":
            # 非dual_port栅格假设按2**b1*2**enh检查；独立dual_port不增加未知输入判决数。
            need = 2**self.b1 * (
                1
                if self.dither_transfer_model == "dual_port"
                else 2 ** int(self.dither_enhancement_bits)
            )
            checks["分段 DAC 电平数 >= 2**b1 * 2**增强位数"] = (
                float(self.dac_levels),
                float(need),
                self.dac_levels >= need,
                "levels",
            )
            # 子阵列必须恰好覆盖主阵列一个 LSB（这是 C_C 名义值的设计条件）
            c_u = self.dac_unit_cap()
            # beta = C_C/(C_C + n_sub*c_u + c_p) = 1/n_sub  ->  C_C = (n_sub*c_u + c_p)/(n_sub-1)
            c_p = self.dac_parasitic_ratio * c_u * self.dac_n_sub
            cc_nom = (self.dac_n_sub * c_u + c_p) / (self.dac_n_sub - 1.0)
            checks["桥接电容 C_C / 单位电容"] = (
                cc_nom / c_u,
                1.0,
                abs(cc_nom / c_u - 1.0) < 0.5,
                "ratio (经典值 ≈ 1)",
            )
            # 子 LSB 折回输入要足够细（不然后端 ADC2 补不回来）
            checks["子 LSB <= 主 LSB/4"] = (
                self.dac_step_sub(),
                self.dac_step_main() / 4.0,
                self.dac_step_sub() <= self.dac_step_main() / 4.0,
                "V",
            )
            # 面积对比：达到同样电平数，等权阵列需要多少个单位
            checks["等权阵列达到同样电平数所需单位数"] = (
                float(self.dac_levels),
                None,
                True,
                "units（分段只需 n_main+n_sub）",
            )

        # 报告中的增益枚举与入口check_legal共用LEGAL_VALUES，避免配置/报告分支不同。
        _gain_models = LEGAL_VALUES["ra_gain_model"]
        checks[f"RA 增益口径 ra_gain_model ∈ {{{', '.join(_gain_models)}}}"] = (
            1.0 if self.ra_gain_model in _gain_models else 0.0,
            1.0,
            self.ra_gain_model in _gain_models,
            "bool",
        )

        if verbose:
            print(f"{'检查项':<40s}{'实际':>14s}{'门限':>14s}  结果")
            print("-" * 78)
            for k, (val, thr, ok, unit) in checks.items():
                vs = f"{val:,.4g}"
                ts = "-" if thr is None else f"{thr:,.4g}"
                print(f"{k:<40s}{vs:>14s}{ts:>14s}  {'PASS' if ok else 'FAIL'}   [{unit}]")
        return checks

    def to_dict(self) -> dict:
        """导出为普通字典（等价于 dataclasses.asdict）。

        Returns:
            dict: 键为字段名（str），值为字段值，单位同各字段定义。
            Nested parameter dataclasses are recursively converted to dictionaries.
        """
        return asdict(self)

    @classmethod
    def from_dict(cls, values: dict) -> Config:
        """Load a configuration exported by to_dict, including a JSON round trip.

        Args:
            values: Field names and SI values; unknown fields are rejected.

        Returns:
            A validated independent configuration with typed nested controls.

        Raises:
            TypeError: Unknown fields or malformed nested parameters.
            ConfigError: Invalid physical settings.
        """
        params = dict(values)
        if isinstance(params.get("input_network"), dict):
            network = dict(params["input_network"])
            if "pretrack_weights" in network:
                network["pretrack_weights"] = tuple(network["pretrack_weights"])
            params["input_network"] = InputNetworkParameters(**network)
        if isinstance(params.get("conversion"), dict):
            params["conversion"] = ConversionParameters(**params["conversion"])
        for name in ("mismatch_split", "mismatch_gradient"):
            if name in params:
                params[name] = tuple(params[name])
        cfg = cls(**params)
        cfg.check_legal()
        return cfg


def noise_budget(cfg: Config) -> dict:
    """分配名义输入等效噪声预算，不生成随机样本。

    预算与功能开关解耦；RA在cap_scale=1的预算锚点推算，随后不因采样电容缩小而被重新扣到0。
    实际RA/采样随机过程分别由对应生产模块生成。

    Args:
        cfg: Config，包含目标DR、标称电容、ADC2栅格和可选RA输出RMS[V]。

    Returns:
        各源的输入等效RMS[V]字典；不是实际测得的总噪声或器件噪声传递。
    """
    sn = cfg.sigma_sampling()
    sn_ref = cfg.sigma_sampling(cap_scale=1.0)
    q2 = cfg.delta2 / math.sqrt(12.0) / cfg.g0
    if cfg.ra_out_noise_rms is None:
        # 由 target_dr_db 反推：total 已知，扣掉 s=1 时的 kT/C 与量化 -> 剩余给 RA
        vfs_rms = cfg.v_fs / math.sqrt(2.0)
        total = vfs_rms / (10 ** (cfg.target_dr_db / 20.0))
        ra_in = math.sqrt(max(total**2 - sn_ref**2 - q2**2, 0.0))
    else:
        ra_in = cfg.ra_out_noise_rms / cfg.g0
    ktc_in = (cfg.kappa_eff() * cfg.ktc_noise_n / cfg.g0) if cfg.ktc_enable else 0.0
    return {
        "kT/C (RDAC)": sn,
        "RA (折输入)": ra_in,
        "ADC2 量化 (折输入)": q2,
        "KTC 观测通路 (折输入)": ktc_in,
    }


def mismatch_from_process_assumption(
    cfg: Config, a_sigma_pct_um: float = 0.5, density_ff_per_um2: float = 2.0
) -> float:
    """根据显式工艺假设估算单位电容相对σ。

    A_unit=C_unit/density，σ=(A_sigma/100)/sqrt(A_unit)。

    Args:
        cfg: Config；取未缩放单位电容和面积倍率。
        a_sigma_pct_um: 假设的Pelgrom系数[%·um]。
        density_ff_per_um2: 假设的电容密度[fF/um²]。

    Returns:
        无量纲相对σ。未输入工艺测量数据，不能据此签核面积、功耗或良率。
    """
    c_u_fF = cfg.c_unit0 * 1e15 * cfg.cap_scale
    a_u = c_u_fF / density_ff_per_um2
    return (a_sigma_pct_um / 100.0) / math.sqrt(a_u)


# 旧名保留为 alias（外部脚本可能引用），语义以 mismatch_from_process_assumption 为准
mismatch_from_pdk = mismatch_from_process_assumption


def dac_error_scaling_note(cfg: Config) -> str:
    """一个有用的结论：等权阵列的 DAC 误差只取决于**总面积**，与单位数无关。

    sigma_e ∝ sigma_eps / sqrt(N) ∝ 1/sqrt(N * A_u) = 1/sqrt(A_total)

    Args:
        cfg: Config 实例（未使用，保留接口以便未来扩展）；[披露]/[假设] 混合。

    Returns:
        结论字符串（str）：等权阵列 DAC 误差 sigma_e ∝ 1/sqrt(A_total)，
        只由阵列总面积决定，与切分单位数无关；[派生]。
    """
    return (
        "等权阵列的 DAC 误差 sigma_e ∝ sigma_eps/sqrt(N) ∝ 1/sqrt(A_total)："
        "只由阵列总面积决定，与切分成多少个单位无关。"
    )


def resolve_ra_noise(cfg: Config) -> float:
    """返回 RA 输出端的噪声 sigma（V）。输出端噪声是电路属性，不随逐样本增益变。

    Args:
        cfg: 配置对象（Config）。

    Returns:
        RA 输出端噪声 sigma（Vrms）。[拟合] 在 cap_scale = 1 标定一次后锁定，
        不随采样电容缩放而变。
    """
    if cfg.ra_out_noise_rms is not None:
        return float(cfg.ra_out_noise_rms)
    return noise_budget(cfg)["RA (折输入)"] * cfg.g0
