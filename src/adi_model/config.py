"""Configuration, units and legal operating domains for the SAR models.

The 40 MS/s rate, 6 Vpp input, 18/8 slice counts and G=32 are disclosed anchors.
`paper_consistent()` retains the historical seven-decision-bit hypothesis for
comparison; its name does not establish a unique reading of the paper.
`paper_literal()` implements nine decisions with independent dither port range
and a complete-count split topology. Its 63+8 counts and ideal dual-port coupling
are explicit assumptions. Dither amplitude enhancement never creates extra
information about the unknown input.

ADC2 range/precision are derived under a stated 10% redundancy assumption, not
transcribed circuit details. Capacitance, gain, sampling load and noise-equivalent
capacitance remain distinct. Fitted noise/mismatch targets are not predictions.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass

K_B = 1.380_649e-23  # J/K
TEMP_K = 300.0  # K


class ConfigError(ValueError):
    """A configuration that cannot model anything physical.

    Raised by :meth:`Config.check_legal` and therefore by every simulation
    entry point. Distinct from a `validate()` record being ``False``: a FAIL
    record says "this (legal) design has little margin here", which is worth
    studying; a ``ConfigError`` says "these numbers are not a model of
    anything" — an unrecognised enum value silently falling through to a
    default branch, or a size that makes the algebra undefined.
    """


# --------------------------------------------------------------------------
# 枚举型字段的**已实现**取值表。
#
# 用途：仿真入口在开始计算前查这张表。此前一个拼错的取值（如
# ra_gain_model="Fixd"）会静默退化到默认分支，仿真照跑、结果照出，只是跑的
# 不是你以为的那条路径 —— 外部复核 2026-09-11 把这称为"字段名贴标签"。
# 契约：配置在某条链路里要么按声明生效，要么被显式拒绝，不能静默忽略。
#
# 维护：新增取值时同步这里，否则入口会拒绝这个新取值（宁可拒绝也不要静默）。
# 本表是"入口拒绝非法取值"的唯一来源；`tests/audit/test_review_contracts.py`
# 逐字段核对未声明取值确实被拒绝
# （TestR4ConfigTakesEffectAtTheRunner::test_an_illegal_enum_is_refused_for_every_declared_field）。
#
# 范围声明：本表**只声明取值集合**，不代表每个字段都已被下游读取。据实登记：
#   * 有比较分支：dac_arch / calibration / dither_mode / dither_split_bank /
#     dither_quant_transfer / dither_transfer_model / ra_gain_model；
#   * **无读取点**：`stage1_reading`（全库无 `self.stage1_reading` 比较点，只在
#     工厂方法里被写入，用来记录采用了哪种读数；实际差别体现在该工厂同时设定
#     的 b1 / units_per_lsb1 等字段值上）、`dem_mode`（见下方注释）。
# 这两个字段仍然入表：拼错的取值应当被拒绝；同时它们是"字段名承诺了未接线的
# 机制"这一缺陷类的实例，登记在这里比让它们看起来已生效要诚实。
# --------------------------------------------------------------------------
LEGAL_VALUES: dict[str, tuple[str, ...]] = {
    "stage1_reading": ("paper_consistent", "paper_literal", "legacy_codeword"),
    "dither_transfer_model": ("range", "granularity", "dual_port"),
    "dither_mode": ("off", "analog", "quantizer", "sampling"),
    "dither_split_bank": ("sub", "main"),
    "dither_quant_transfer": ("quantizer", "rdac"),
    "ra_gain_model": ("charge", "fixed"),
    "calibration": ("none", "gain", "gain_beta"),
    # dem_mode 现在**真的决定调度器**：三个仿真入口都经 scheduler.make_scheduler
    # 取调度器（rotate -> Scheduler，permute -> ShuffledScheduler），显式传入的
    # 调度器与它不一致时拒绝运行。注意"接线"不等于"调度正确"：洗牌调度器自身
    # 的样本归属缺陷仍未闭合（见 tests/audit/test_review_contracts.py 的
    # TestR1SampleOwnership，xfail(strict=True)）。
    "dem_mode": ("rotate", "permute"),
    "dac_arch": ("unary", "split"),
}


# --------------------------------------------------------------------------
# stage1_reading 标签 -> b1 的对应关系。从三种读法的**定义**直接导出，不是
# 第二份需要手工同步的事实表：
#
#   paper_consistent  b1 + 2b 增强 = 9b              -> b1 = 7（默认读数）
#   paper_literal     字面"9b in the first stage"    -> b1 = 9
#   legacy_codeword   v6.1：6b 粗判决 + 3b dither    -> b1 = 6
#
# 标签只是元数据，真正改变机制的是 b1（以及工厂方法一并设定的后端字段）。
# 两者不一致时，用户会以为自己切换了架构、实际没有 —— 与拼错的枚举值同类，
# 只是更隐蔽，因此在仿真入口拒绝（外部复核 2026-09-11 第三轮）。
#   注：`Config(b1=10)` 这类"只想动 b1"的配置仍可构造、可 validate()（它测的
#   是 DAC 电平数判据），只是不能送进仿真入口 —— legality 与 validate 分离。
# --------------------------------------------------------------------------
READING_B1: dict[str, int] = {
    "paper_consistent": 7,
    "paper_literal": 9,
    "legacy_codeword": 6,
}


@dataclass
class Config:
    """全部模型参数的唯一来源；每个字段都在 PARAM_GRADES 中登记来源分级。"""

    # ---------------- 系统与目标 ----------------
    fs: float = 40e6  # 采样率
    n_bits_target: int = 20  # 目标位数（只用于定义 LSB 目标，不参与内部运算）
    v_fs: float = 3.0  # 差分满幅峰值（V），输入范围 = ±v_fs
    target_dr_db: float = 94.6  # 发表值；用于反推 RA 噪声预算（ra_out_noise_rms=None 时）

    # ---------------- 第一级 SADC ----------------
    b1: int = 7  # 第一级 SADC 判决位数（含义见 stage1_reading）
    # 第一级分辨率的**架构读数**。论文 [00] 同时披露了两条互相约束的事实：
    #   (a) "...resulting in 9b quantization in the first stage."
    #   (b) "the dither range is enhanced by 2b when transferred from the
    #        quantizer to the RDAC."
    # 本模型的机制里，"量程增强 2^b" 的 b 就是「一个第一级判决步包含多少
    # RDAC 单位步」= log2(DAC 电平数 / 2**b1)。两条联立：
    #       b1 + b_enh = 9,  b_enh = 2  =>  b1 = 7（units_per_d1 = 4 = 2²）
    # 可选读数（外部审计 F1 要求显式声明，不得悄悄选定）：
    #   "paper_consistent"（默认）: b1=7 —— 在「码值量程增强 == 对未知输入的
    #                              额外判决位数」这一**假设**下唯一自洽的分配。
    #                              该等同正是 ADR 0003 §1 拒绝过的那一种，所以
    #                              这里是一个**假设**，不是收敛结论；PARAM_GRADES
    #                              把 b1 标为 ASSUMED（外部复核 2026-09-11 R3）。
    #   "paper_literal"          : b1=9 —— 只取 (a) 的字面读法；此时
    #                              units_per_d1=1，增强 0b，与 (b) 冲突。
    #   "legacy_codeword"        : b1=6 —— v6.1 读法（增强 3b）；仅为复现旧结果。
    stage1_reading: str = "paper_consistent"
    # dither 从量化器转移到 RDAC 的机制。论文 [00]：dither range "is enhanced
    # by 2b when the result is transferred from the quantizer to the RDAC"。
    #   "range"      : 论文口径（默认）—— 转移 = 在 RDAC 栅格上精确扣除
    #                  d_code = −round(d/ΔD)；"2^b 增强"是**余量能力**
    #                  （units_per_d1 = 2^b），由 stage21 作为推导量报出，
    #                  不由粒度实验冒充证明。
    #   "granularity": v6.1 读法 —— 转移时按更细粒度重新取整（保留对照）。
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
    sampling_tau_mismatch: float = 0.0  # Δτ [s]；>0 表示 SADC 通路更快
    sadc_mismatch_enable: bool = False
    sadc_mismatch_sigma: float = 0.0  # SADC 阈值失配 sigma（V，折输入）

    # ---------------- 级间增益 / 残差放大器 ----------------
    g0: float = 32.0  # 名义级间增益
    gain_error: float = 0.0  # fixed 模式下 G_actual = g0*(1+gain_error)
    # RA 增益模型（v3 审计修正：反馈电容必须真正进入传输关系，二选一，不许混写）：
    #   "charge" : 电荷一致模型 G[n] = C_active[n] / C_F_true。
    #              C_active 是**本次样本实际选中的 slice 集合**的总电容（逐样本），
    #              C_F 是这颗芯片的实际反馈电容。全局/slice 级失配由此进入
    #              增益通路（整体偏差 = 增益误差，DEM 轮转选中集合变化 = 增益波动）。
    #   "fixed"  : 理想固定增益 G = g0*(1+gain_error)，与电容无关（仅作对照）。
    ra_gain_model: str = "charge"
    ra_out_noise_rms: float | None = None  # None -> 自动标定到 target_dr_db（在 s=1 冻结一次）
    ra_v_clip: float = 1.98  # RA 输出摆幅（先检查自身饱和，再送 ADC2）
    # paper_consistent 读数：g0*delta1 = 1.5 V，
    # 1.2×adc2_v_max = 1.98 V。
    ra_enable_noise: bool = True

    # ---------------- RDAC ----------------
    n_slices: int = 18  # slice 池（8 采集 + 8 保持 + 2 spare）
    n_active: int = 8  # 每次参与残差输出的 slice 数
    n_unit_per_slice: int = 64  # 每 slice 物理单位数（3b 横向 x 3b 纵向 = 8x8）
    n_units_headroom: int = 0  # 额外 dither 量程（v1 默认 0，dither 走输入注入）
    c_total0: float = 20.5e-12  # cap_scale=1 时 RDAC 总采样电容（PPT: 20.5 pF）
    cap_scale: float = 1.0  # 面积缩放 s
    chi: float = 2.0  # kT/C 系数：1 = 单端；2 = 差分两侧独立
    mismatch_enable: bool = True
    # cap_scale=1 时的单位电容失配 sigma。
    # 1e-4 是**行为学标定值**：令 s=1、DEM 开时的 SNDR ≈ 93.4 dB，对齐论文 SNDR=93.5 dB。
    # 它不是 PDK 值 —— 由 mismatch_from_pdk() 估算的物理值约 1e-3，差 ~10 倍，
    # 这个缺口正是"必须做单位权重校准"的证据（见报告）。
    mismatch_sigma0: float = 1e-4
    # 失配的**空间分解**（v2）。三项平方和为 1，保证总 sigma 不变：
    #   global : 全阵列共模（C_fb 失配 + 整体工艺偏差）。DEM 完全无效。
    #   slice  : slice 级共模（版图梯度 / 刻蚀负载）。DEM 跨 slice 有效。
    #   unit   : unit 级独立随机（Pelgrom A/sqrt(WL)）。DEM 完全有效。
    # (0,0,1) 退化为 v1 的纯独立假设——那是物理上的乐观下界，不能当设计依据。
    mismatch_split: tuple = (0.15, 0.35, 0.50)  # (global, slice, unit) 的**方差**占比
    # 显式线性版图梯度，单位 = **相对失配 / 单位坐标**（绝对量，不被 sigma 缩放），
    # (gx, gy)，坐标归一化到 [-0.5, 0.5]。例如 gy=1e-3 -> 全 y 跨度上失配变化 1e-3。
    # v3 审计修正：此前梯度在 sigma 缩放之前加入，实际跨度被悄悄乘了 sigma。
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
    #   = 0.6 LSB20（后端量化可忽略）。
    # 这些值由 Config.paper_consistent() 从 Delta1 推导，勿手改：
    #   adc2_v_min = −0.10·G0·Δ1   adc2_v_max = +1.10·G0·Δ1
    #   adc2_n_bits = ceil(log2((v_max−v_min)/(LSB20·G0)))
    adc2_n_bits: int = 14
    adc2_v_min: float = -0.15  # -0.1 * g0 * delta1
    adc2_v_max: float = 1.65  #  1.1 * g0 * delta1

    # ---------------- 数字功能开关 ----------------
    dem_enable: bool = False
    # off       : 关闭
    # analog    : 输入注入（v1）→ 直接从 ±V_FS 扣量程，代价是可用范围变小
    # sampling  : 采样态电荷注入（专利 [10] Fig.6 / Fig.19）→ **不占输入量程**
    #             固定 2D 个单位不接输入而接 ±V_FS：其中 D+d_u 个接 +V_FS、
    #             D−d_u 个接 −V_FS。信号衰减 α = 1−2D/N 恒定，注入量 = d_u·step。
    #             代价从"量程"换成"恒定增益 α"，可在数字端精确补偿（但会按 1/α 放大后端噪声）。
    dither_mode: str = "off"  # off | analog | sampling
    dither_amplitude_lsb1: float = 2.0  # 幅度，单位 Delta1（analog 模式）
    dither_units_range: float = 2.0  # D：dither 幅度，单位 = RDAC 单位当量（sampling 模式）
    # 采样 dither 掩码落在哪个阵列（split 拓扑）：sub（默认，桥接衰减后权重小）
    # 或 main。α 必须由**同一份掩码**推导：被移出输入连接的 2D 个单位，
    # 其信号电荷贡献按所在阵列的权重扣除（v5.1 第三轮审计 §四）。
    dither_split_bank: str = "sub"  # sub | main（仅 dac_arch="split" 时生效）
    # 离散掩码口径（v5.1 三轮审计 §四：连续插值只是数学基准，物理开关只能
    # 实现整数单位选择）。True 时 dither_code 取整数 d_u ∈ [−D, D]：
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
    # 观测通路一阶带宽（v3 审计修正：Δt 不能只靠缩短数学间隔，要看电路能不能建立）。
    # 从提取开始建立的一阶模型，窗口末端采样时的残余建立误差：
    #     eps = exp(-2*pi*f_bw*dt)
    # 实际观测增益 = G_N*(1-eps) —— 数字端仍按名义 kappa 扣除，差额就是 beta 误差。
    # None = 理想（eps=0，只用于算法验证）。validate() 会报告给定 eps 预算所需带宽。
    ktc_observe_bw_hz: float | None = None
    # t2 - t1，单位 Ts。**这是 KTC 方案最关键的一个参数**：
    # 校正项 kappa*v_N = G_R*(n_R - dx)，其中 dx 比 n_R 大约三个数量级，
    # 所以真正被放大进 ADC2 的是 G_R*|dx| —— 与 G_N/kappa 怎么分无关。
    # 由此得到硬带宽上限：2*pi*f*A*G_R*dt < ADC2 余量。
    # dt = Ts/256 时该上限约 5.6 MHz，正好覆盖 PPT 标注的 "DC - 5MHz" 信号带。
    ktc_dt_fraction: float = 1.0 / 256.0
    calibration: str = "none"  # none | gain | gain_beta
    dem_mode: str = "rotate"  # rotate（循环轮转）| permute（每状态独立随机排列）

    # ================================================================
    # v6 架构口径字段：显式记录"读法"以避免审计中出现的口径混淆。
    # ================================================================
    # stage1_reading / dither_transfer_model / dither_enhancement_bits
    # 已定义在第一级 SADC 段（上文），此处不再重复。
    # "PDK 等效失配"对照用的面积律估算，仅用于校准压力测试：
    # 论文 [00] 没有披露 PDK 失配数值，本字段属于 [假设]。
    pdk_sigma_est_ppm: float = 1117.0

    # ================================================================
    # v5 结构建模：DAC 拓扑（等权 unary 是分段结构的特例）
    # ================================================================
    # "unary" : v1-v4 的纯等权阵列（保留为对照/回归基准）
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
    # v5 动态误差（[00_1] PPT p.32 点名的三项）
    # ================================================================
    # --- (a) 输入建立：采样开关导通电阻 + 源阻抗，一阶 RC ---
    # 硬设计约束：20.5 pF @ 40MS/s 要建立到 0.1%，需 R_total ≈ 60 Ω
    # （Ts*0.45/(6.9*C)=1.6 ns）-> 采样开关必须自举。这是模型反推的规格。
    dyn_input_settling: bool = False
    dyn_r_source: float = 30.0  # 驱动器 + 走线串联电阻 [ohm]
    dyn_r_on: float = 20.0  # 采样开关导通电阻 [ohm]（自举开关量级）
    dyn_t_sample_frac: float = 0.45  # 采样相时长 / Ts
    # 开关导通电阻随信号电平的调制（V_GS 随输入变）-> tau 与码相关 -> 真 INL
    dyn_ron_code_coeff: float = 0.1  # rho：tau = tau0 * (1 + rho*(2k/N - 1))

    # --- (b) 参考建立：每次切换从参考抽取电荷，有限去耦 + 有限恢复带宽 ---
    dyn_ref_settling: bool = False
    dyn_c_decouple: float = 4.7e-6  # 参考去耦电容 [F]（片外典型量级）
    dyn_tau_ref: float = 20e-9  # 参考缓冲恢复时间常数 [s]
    dyn_t_conv_frac: float = 0.40  # 转换相时长 / Ts
    dyn_ref_dynamic_ratio: float = 1.0  # 动态（码跳变）分量 / 静态（码值）分量

    # --- (c) 数字串扰：数字走线到顶极板的耦合 ---
    dyn_crosstalk: bool = False
    dyn_c_xtalk_common: float = 1e-18  # 共模耦合电容 [F]（所有切换单位共享，DEM 无效）
    dyn_c_xtalk_unit: float = 5e-19  # 单位耦合电容 [F]（每个切换单位一份，DEM 有效）
    dyn_v_digital: float = 1.0  # 数字节点摆幅 [V]
    # 单调切换下每次转换的单位翻转数 A(k) = 2*min(k, N-k)（三角波，中码最大）

    # --- (d) slice 池（pipeline.py，v6 整体信号流）---
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
    # 逐 slice 输入参考 offset 失配（交织误差四件套之 offset，逐字审计补齐）。
    # V_os,i ~ N(0, sigma)，每颗芯片一次、跨样本持久；采集组保持值
    # x_hold = x + mean(V_os[acq]) -> 样本级误差 = mean(V_os[acq])（常数型）。
    # 固定 A/B 两组 -> (−1)^n 调制的**常数** -> 杂散在 f_S/2（不随 f_IN 平移），
    # 与 skew/带宽/增益杂散（f_S/2±f_IN，信号相关）构成判别性指纹。
    slice_offset_sigma_v: float = 0.0
    # 独立量化器 sDAC 的电容占 RDAC 活跃电容的比例（小电容 -> 建立快 -> kT/C
    # 噪声大，由 sigma_sadc_sampling 单独刻画；这里只影响其建立误差）。
    sadc_cap_ratio: float = 0.05

    # ================================================================
    # v6.1 逐字审计缺口闭合（论文 [00] / PPT [00_1] 逐字对照的剩余项）
    # ================================================================
    # --- (1) 量化器侧 dither 与"量程 2b 增强"（论文："the dither range is
    #     enhanced by 2b when the result is transferred from the quantizer
    #     to the RDAC"）---
    # 机制：dither d_Q 加在**量化器输入**（x_sadc），粗码在 x+d_Q 上决策；
    # 数字侧把 d_Q 转移给 RDAC 码（d_u = −round(d_Q/gran)/gran·gran [单位]），
    # 残差回到名义 bin。**转移粒度 gran** 决定残差里的 dither 余项
    # d_Q − round(d_Q) 的大小：
    #   "quantizer"：按 Δ1 取整 -> 余项 ±Δ1/2，×G0 = ±1.5 V >> ADC2 窗口 -> 溢出；
    #   "rdac"     ：按 step0 = Δ1/8 取整 -> 余项 ±step0/2，×G0 = ±0.19 V，
    #                落在窗口内由 ADC2 吸收 —— 这就是"转移到 RDAC 增强了
    #                log2(units_per_lsb1)=3b"的可操作口径（论文写 2b，
    #                模型测上界 3b，不强行拟合，见 stage21 判据文本）。
    dither_quant_transfer: str = "rdac"  # quantizer | rdac

    # --- (2) RA auto-zero（PPT p.34-35 两个 dB 数的结构化）---
    # auto-zero 消 RA 的 offset 与低频噪声（含 1/f），代价是存储电容 kT/C +
    # 噪声折叠 -> PPT 披露 SNDR 代价 1.6 dB。行为建模 = RA 输出噪声 × 折叠
    # 因子 10^(cost/20)。注意口径：PPT 的 −1.6 dB 以 RA 自身/整机为参照
    # 由其预算结构决定；折到本模型整机预算（RA 占噪声功率 ~74%）后整机
    # ΔSNDR ≈ −1.2 dB —— stage22 做"预测 vs 实测"一致性检验，不硬凑 dB 数。
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
    rdac_bitwise_bits: int = 6  # [假设] 逐位装载位数 = SADC 位数

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
        """第一级对**未知输入**实际拥有的判决电平数。

        这是审计 F1 的核心量。``paper_literal`` 读数下等于 ``2**b1``；
        ``legacy_codeword`` 读数下 dither 是已知量，不增加对输入的判决信息，
        因此判决电平数仍为 ``2**b1``（b1=6 时 = 64），而**码字**宽度是 9 bit。
        两者相差 8 倍，直接决定残差摆幅、RA 输出范围和后端位预算。

        Returns:
            第一级对未知输入实际拥有的判决电平数（个，无量纲）。
            [派生] 口径见上：与 codeword 读数下的 9 bit 码字宽度不同，勿混用。
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
        """返回与 v6.1 完全等价的配置，用于复现已发表旧结果。

        v6.1 把论文的 "9b in the first stage" 读成 6b 粗判决 + 3b dither 拼成
        9b 码字，并据此设定后端 15 bit / [-0.3, 3.3] V。外部审计指出该读法下
        判决电平只有 64 个。此处保留它**只为可复现性**；新工作请用
        :meth:`paper_consistent`。

        Returns:
            一个 ``stage1_reading="legacy_codeword"`` 的 Config
            （b1=6，判决电平 64，dither 增强 3b）。
        """
        return cls(
            b1=6,
            stage1_reading="legacy_codeword",
            # v6.1 读法下 units_per_lsb1 = 512/2^6 = 8 = 2³，所以**披露的**
            # 增强位数在这里被读成 3b（大于论文的 2b —— 这正是审计点名之处）。
            # 显式写出而不是靠默认值，否则 validate() 的
            # "dither 增强位数 == log2(DAC 电平数 / 2**b1)" 会 FAIL。
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

        This preserves the legacy grid-ratio interpretation for comparison.
        It does not establish that 7 decisions plus 2 dither bits reproduce the
        paper's nine quantization bits. See `paper_literal` for the independent
        range/decision candidate and ADR 0003 for the distinction.

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

        A 63-main/8-sub complete-count topology is an explicit modeling choice,
        not a claim that the paper discloses these physical capacitor counts.
        With beta=1/8 it spans 512 uniform codes over the full 6 V input range.
        Dual-port dither is a separately declared ideal charge-injection model.

        Args:
            **overrides: Explicit configuration overrides; backend settings are
                rederived unless explicitly supplied.

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
        """名义活跃采样电容 = 本次实际参与的 n_active 个 slice 的总电容。

        注意这**不等于** c_total0 * n_slices/n_active —— c_total0 本身就是
        按 8 个活跃 slice 的 512 个单位定义的（PPT 的 20.5 pF 指活跃采样电容）。
        整个 18-slice 资源池的电容是 c_total0 * n_slices / n_active = 46.1 pF，
        其中未参与采样的部分不能贡献 kT/C（v3 审计修正）。

        Args:
            cap_scale: 面积缩放 s（无量纲，默认 None 表示用 self.cap_scale）；
                [假设] 探索旋钮。

        Returns:
            名义活跃采样电容（F）。= (n_active·n_unit_per_slice − headroom)·
            c_unit0·s；[派生] 自 c_total0（[披露]）。
        """
        s = self.cap_scale if cap_scale is None else cap_scale
        return (
            self.c_unit0 * self.n_active * self.n_unit_per_slice
            - self.n_units_headroom * self.c_unit0
        ) * s

    @property
    def g_actual(self) -> float:
        """含增益误差的实际级间增益 G = g0·(1 + gain_error)。

        Returns:
            实际级间增益 G（无量纲），= g0·(1 + gain_error)。[派生]
        """
        return self.g0 * (1.0 + self.gain_error)

    # ---------------- v5：分段 DAC 派生量 ----------------
    @property
    def dac_n_units(self) -> int:
        """DAC 物理单位总数（unary：n_active×n_unit_per_slice；split：n_main+n_sub）。

        Returns:
            DAC 物理单位总数（个）。unary = n_active·n_unit_per_slice；
            split = n_main + n_sub。[派生]
        """
        if self.dac_arch == "unary":
            return self.n_active * self.n_unit_per_slice
        return int(self.dac_n_main) + int(self.dac_n_sub)

    @property
    def dac_levels(self) -> int:
        """分段结构的总电平数 = n_main * n_sub。unary 退化为其单位数。

        Returns:
            DAC 可寻址电平总数（个）。split = n_main·n_sub；
            unary 退化为 n_active·n_unit_per_slice。[派生]
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
        """分段 DAC 的单位电容（总面积固定为采样电容）。

        面积预算按 n_main + n_sub + 1 个单位分摊（桥接电容也占面积），
        这样不同拓扑的**总电容面积**可比。

        Args:
            cap_scale: 电容缩放因子（无量纲，> 0）。None 表示取 cfg.cap_scale。[派生]

        Returns:
            分段 DAC 单位电容（F）。总电容面积固定为 c_total0·s，
            按 n_main + n_sub（+ 1 个桥接电容）分摊。[派生]
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
        """主阵列一个单位的名义步长（V）。

        Returns:
            主阵列单位名义步长（V），= 2·V_FS / n_main；
            unary 拓扑退化为 rdac_step。[派生]
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
        """信号传递系数 α = 采样开关掩码移除的信号电荷占比的补。

        v5.1 第三轮审计 §四：α 不能再用一个统一的"单位数比"公式代表所有
        拓扑 —— split 的子阵列单位对 C_sig 的贡献是 β·c_u（桥接衰减后），
        主阵列单位是 c_u，两者权重不同。

        * unary：2D 个等权单位移出输入 → α = (N−2D)/N（旧公式，不变）。
        * split：2D 个单位按 dither_split_bank 从主/子阵列移出，
          α = 1 − 2D·w_bank/C_sig_nom，w_bank = c_u（主）或 β·c_u（子）。
          例：(64,8)、D=2、掩码在子阵列 → α = 64.5/65 = 0.992308，
          而旧的 512 等权公式给 508/512 = 0.992188 —— 数值接近但来源错误，
          换拓扑/换 D 后偏差会放大。

        仍未闭合的问题（已标注）：α 目前只覆盖**信号电荷**这一份；
        输入负载与噪声传递系数尚未由同一份掩码生成（下一版逐相位推导）。

        Returns:
            信号传递系数 α（无量纲，0 < α ≤ 1）。[派生]
            口径与勿回退教训见上：换拓扑时不得退回 512 等权公式。
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
        """按**本次实际参与采样的电容**计算 kT/C（v3 审计修正）。

        没有参与本次采样的备用 slice 不能替这个样本降低 kT/C，
        所以噪声必须绑定逐样本的 C_active[n]，不能用整池总电容。

        Args:
            c_active: 本次实际参与采样的电容（F）。[派生]

        Returns:
            逐样本 kT/C 噪声 rms（Vrms），= sqrt(kT / c_active)。[派生]
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
        """Pelgrom 面积律：sigma ∝ 1/sqrt(单位电容)，以 c_unit0(cap_scale) 为锚点。

        **这是拓扑比较的关键**：等权 unary（c_u = 40 fF）与分段
        （c_u = 285 fF）在**同样总电容面积**下，单位电容大 7 倍
        -> 失配 sigma 小 sqrt(7) ≈ 2.7 倍。这是分段结构的根本收益，
        必须让它在模型里真实生效，而不是被固定 sigma0 掩盖。

        Args:
            c_unit: 单位电容（F）。[假设]

        Returns:
            单位电容失配 sigma（无量纲相对误差），按 Pelgrom 面积律
            sigma ∝ 1/sqrt(c_unit)，以 c_unit0(cap_scale) 为锚点。[假设]
        """
        if not self.mismatch_enable:
            return 0.0
        # 锚点必须是**标称单位电容**，不能乘 cap_scale。
        # v5 审计修正：原式 ref = c_unit0*cap_scale，而 c_unit 也含同一个
        # cap_scale 因子，两者相除把面积因子约掉了 —— 实测 unit_cap 从
        # 280.82 fF 变到 17.55 fF（16 倍）时 sigma 恒定 37.76 ppm，
        # 等价于宣称"把电容缩到 1/16 还能免费保住失配"，物理上不可能。
        # 修正后 sigma ∝ 1/sqrt(c_unit) ∝ 1/sqrt(cap_scale)，
        # 与 kT/C 的 1/sqrt(cap_scale) 同向 —— 缩电容要同时付两笔代价。
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
        """Δx 的建立系数：输入变化在窗口内逐渐积累（线性斜坡），

            eta_x = 1 - (tau_a/T_w) * (1 - exp(-T_w/tau_a))

        v4 审计修正：**eta_n != eta_x**。旧版把同一个 (1-eps) 同时乘 n_R 和 dx，
        隐含"输入变化也是窗口开始就出现的阶跃"——物理上不成立。
        低带宽极限（T_w << tau_a）下 eta_x = eta_n / 2；高带宽极限二者都 -> 1。

        Returns:
            Δx 的建立系数 η_x（无量纲）。[派生] 与 η_n 不等，勿合并回退。
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
        """名义 beta = (1+beta_error) * kappa * G_N / G_R。

        v3 注意：charge 模式下 G_R 逐样本 = C_active[n]/C_F，此处返回的是
        以名义 G_R = g0 计算的参考值；逐样本真实 beta 由 ktc.observe 按 g_vec 计算。

        Returns:
            名义 beta（无量纲），= (1 + beta_error)·κ·G_N / G_R。[派生]
            逐样本真实 beta 由 ktc.observe 按 g_vec 计算，此处为参考值。
        """
        if not self.ktc_enable:
            return 0.0
        return (1.0 + self.ktc_beta_error) * self.kappa_eff() * self.ktc_gain_n / self.g0

    # ================= 合法性检查（在仿真入口调用）=================
    def legality_violations(self) -> list[str]:
        """Reasons this configuration cannot be simulated at all.

        Scope is deliberately narrow and explicit — this is **not** a stricter
        :meth:`validate`. ``validate`` reports adequacy (how much margin a legal
        design has), and several of its records can be ``False`` for a design
        that is still worth studying. Legality is the other thing entirely: an
        unrecognised enum value means a branch will silently never be taken, and
        an impossible size means the algebra below is undefined. Those must be
        refused at the entry, not reported.

        Returns:
            list[str]: 每条是一句可读的违规说明；空列表表示合法。
        """
        bad: list[str] = []
        for field_name, allowed in LEGAL_VALUES.items():
            actual = getattr(self, field_name)
            if actual not in allowed:
                bad.append(
                    f"{field_name}={actual!r} 不是已实现取值（可选：{', '.join(allowed)}）；"
                    f"此前会静默退化为默认分支"
                )

        # 标签与参数必须一致。`stage1_reading` 只是一个名字，改变机制的是 b1：
        # 名字说一种读法、b1 却是另一种，等于用户以为自己切换了架构而实际没有
        # （外部复核 2026-09-11 第三轮）。对应关系只在 READING_B1 里维护。
        expected_b1 = READING_B1.get(self.stage1_reading)
        if expected_b1 is not None and self.b1 != expected_b1:
            bad.append(
                f"stage1_reading={self.stage1_reading!r} 对应的 b1 应为 {expected_b1}，"
                f"但 b1={self.b1} —— 标签说一种读法、参数是另一种。用 "
                f"Config.paper_consistent() / Config.legacy_v61() 切换读数，"
                f"或显式把 b1 设成与该标签相符的值"
            )

        if self.fs <= 0.0:
            bad.append(f"fs={self.fs!r} 必须为正（采样率 [Hz]）")
        if self.v_fs <= 0.0:
            bad.append(f"v_fs={self.v_fs!r} 必须为正（满幅峰值 [V]）")
        if self.n_bits_target < 1:
            bad.append(f"n_bits_target={self.n_bits_target!r} 必须 >= 1")
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
            ConfigError: 若 :meth:`legality_violations` 非空；异常消息逐条列出
                违规项，便于直接定位拼写错误。
        """
        bad = self.legality_violations()
        if bad:
            raise ConfigError(
                "配置不合法，已被仿真入口拒绝（不是「检查表里写着 FAIL」，是直接拒绝）：\n  - "
                + "\n  - ".join(bad)
            )

    # ================= 边界检查 =================
    def validate(self, verbose: bool = True) -> dict:
        """先检查两个判据，再检查其它一致性条件。

        Args:
            verbose: 是否打印检查表（bool，默认 True）；仅控制输出，不影响返回。

        Returns:
            检查字典（dict[str, tuple[float, float|None, bool, str]]）。每项 =
            (实测值, 阈值|None, 是否通过, 单位)；覆盖第一级读数自洽、后端分辨
            能力、残差溢出、噪声预算、KTC 带宽上限等判据；[派生]。
        """
        lsb = self.lsb_target
        d1 = self.delta1
        d2 = self.delta2
        g = self.g_actual

        # (实测值, 阈值|None, 是否通过, 单位)：阈值为 None 表示"只记录不判定"。
        checks: dict[str, tuple[float, float | None, bool, str]] = {}

        # 判据 0（外部审计 F1 / B16）：第一级分辨率的**读数**必须与 DAC 拓扑
        # 自洽。`stage1_reading="paper_literal"` 要求 b1 位判决电平全部可由
        # DAC 的物理电平表达，即 2**b1 <= DAC 可用电平数。
        #   等权拓扑：可用电平 = n_units_sig（= n_active*n_unit_per_slice
        #              − headroom）；
        #   分段拓扑：可用电平 = dac_n_main * dac_n_sub。
        # 不自洽时（如把 b1=9 套在 (64,8) 分段阵列上）模型不会报错，
        # 而是静默把 residue 推出 ADC2 窗口、输出跳到伏级 —— 这正是审计
        # 要求"先修正骨架"的量化理由。此判据把这种组合变成显式 FAIL。
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

        # 判据 0b：`dither_enhancement_bits` 是**披露值**（论文："enhanced by 2b"），
        # 不是可以随读数任意漂移的自由参数。它必须等于
        #     log2(units_per_lsb1) = log2(DAC 电平数 / 2**b1)
        # 也就是「一个粗判决步含多少个 RDAC 单位步」。
        # 这条等式把 ADR 0003 的收敛解写成了机器可校验的约束：
        #   paper_consistent (b1=7, 512 电平) -> 增强 2b ✓
        #   paper_literal    (b1=9, 512 电平) -> 增强 0b ✓（与披露 2b 冲突，故非默认）
        #   legacy_codeword  (b1=6, 512 电平) -> 增强 3b ✓（v6.1 读法，> 披露值）
        # 若有人只改 b1 而不重推读数，这里立刻 FAIL，不会静默放行。
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

        # KTC：**观测通路**的摆幅上限（口径修正见下方注释）。
        #
        # 旧口径（v7.0.0 起）是 f_max = ADC2 余量 / (2π·v_fs·G0·Δt)，即把
        # 校正项 κ·v_N 当成 **ADC2 模拟量程**的消费者。这个前提与 ADR 0006
        # 冲突：校正现在在**数字域**扣除（ADC2.quantize_with_correction 返回
        # quantize(vra) − κ·v_N），over 只反映 vra 本身是否越界，κ·v_N 无论
        # 多大都不占 ADC2 量程。旧口径在默认参数下算出 2.5465 MHz 并判 FAIL
        # —— 外部复核（2026-09-11 第三轮）复算确认那是**节点取错**造成的
        # 假失败，而不是电路限制。判据必须绑定它实际约束的节点。
        #
        # 真实约束在观测通路上：观测节点的可用摆幅 ra_v_clip，观测量经 G_N
        # 放大后为 G_N·2π f A Δt，令其不超过 clip 即得下面的上限。它与紧随
        # 其后的「Nyquist 摆幅 < clip」是同一不等式的两个实例（一个解 f，
        # 一个代入 f = fs/2）；门限来源不同（5 MHz 取自披露的信号带，
        # fs/2 取自采样率），所以两条都保留，而不是留一条"更宽"的。
        #
        # 边界（第五份复核 §11）：本判据**只管摆幅，不管建立**。"不越摆幅"
        # 与"在 Δt = Ts/256 ≈ 97.66 ps 的提取窗口内及时建立"是两回事——
        # 若按单极点阶跃建立到 0.1%，需要 f_BW ≥ ln(1000)/(2π·Δt) ≈ 11.26 GHz
        # （条件性设计压力估算，不是实际电路的既定需求）。也就是说 f_max
        # PASS 不等于 KTC 取消扩展可落地；提取窗口的建立/噪声/量化折衷仍是
        # 开放的设计问题，见 docs/model_scope.md 的 KTC 行。
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
            # v3 审计修正：缩短 Δt 是把问题转移给观测通路的建立，不是免费午餐。
            # 报告"要在这个窗口内建立到 eps，观测通路至少需要多大一阶带宽"。
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

        # ---- v5：分段 DAC 结构自洽性 ----
        if self.dac_arch == "split":
            # RDAC 电平数必须同时覆盖 (a) 2**b1 个粗判决电平 与 (b) 论文披露的
            # dither 量程增强 2**dither_enhancement_bits 个单位步。
            #   需求 = 2**b1 * 2**enh
            # 这条判据曾把 "+3b" 写死，源自 v6.1 的 b1=6 读法；换到
            # paper_consistent（b1=7、enh=2）后它会给出 2^10=1024 > 512 的
            # **假 FAIL** —— 把与读数无关的常数当作物理需求，正是本次审计
            # 要求清除的一类缺陷。现在增强位数从配置取，随读数自动跟随。
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

        # 判据：RA 增益口径必须是**已实现**的取值之一。此前 "fixed" 在
        # sim_split / pipeline 里被硬编码的电容比静默忽略（固定增益对照实验
        # 因此从未真正生效），而任何拼写错误也会悄悄退化成 "charge"。
        # 契约：配置在某条链路里要么按声明生效，要么被显式拒绝，不能静默忽略
        # （外部复核 2026-09-11）。
        # 取值集合来自 LEGAL_VALUES（单一来源）；check_legal() 用同一张表在
        # 仿真入口直接把非法配置拒绝掉，本记录是给报告读者看的第二道。
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
            等价于 dataclasses.asdict，不递归转换嵌套对象。
        """
        return asdict(self)


def noise_budget(cfg: Config) -> dict:
    """把噪声预算拆开：kT/C、RA、ADC2 量化、KTC 观测通路。

    两条原则（v3 审计修正）：
    1. **预算与功能开关解耦**：这是"设计目标预算"，不是"实际电路噪声模型"。
       enable_sampling_noise 只决定仿真里是否加噪声，不得改变反推出的 RA 预算。
    2. **RA 噪声在 cap_scale=1 标定一次之后就锁定**：它是放大器电路的属性，
       不应随采样电容缩放而变。若按"总预算 - kT/C"逐点反推，缩到某个 s 之后
       kT/C 会超过总预算，把 RA 噪声算成 0 —— 那是错的，会把缩电容的代价人为抹掉。

    Args:
        cfg: 配置对象（Config）。

    Returns:
        dict: 各噪声源的 rms（Vrms）与合计（Vrms），键覆盖 kT/C、RA、
        ADC2 量化、KTC 观测通路。[派生] 预算与功能开关解耦，见上两条原则。
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
    """**根据工艺假设**估算单位电容失配 sigma（不是 PDK 测量值）。

        A_u   = C_unit / density                 [um^2]
        sigma = (a_sigma_pct_um / 100) / sqrt(A_u)

    参数来源分级（v3 审计修正，禁止混用）：
      [披露] 论文/PPT 明确给出的   -> 约束结构与公开指标
      [拟合] 为重现基准而设置的    -> 只证明模型能重现某类现象
      [假设] 本函数这类工艺估算    -> 在拿到真实 PDK 数据前不得用于尺寸/功耗结论
    mismatch_sigma0=1e-4 属于 [拟合]；本函数返回值属于 [假设]。

    Args:
        cfg: 配置对象（Config）。
        a_sigma_pct_um: Pelgrom 匹配系数（%·um）。[假设] 典型 MIM 电容 1%·um 量级。
        density_ff_per_um2: 单位面积电容（fF/um²）。[假设] 用于把电容换算成面积。

    Returns:
        单位电容失配 sigma（无量纲相对误差）。[假设] 在拿到真实 PDK 数据前
        不得用于尺寸、功耗与良率结论。
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
