# 旧注释归档：非现行状态入口

基线为1e973cb。以下保留迁出生产代码的历史叙述，不作为本轮重新验证或现行参数定义；本文中的旧行号和库外backup路径只说明归档上下文，完整原文件可用 `git show 1e973cb:src/adi_model/<文件名>` 取得。现行解释以生产代码、ADR和 [STATUS](../STATUS.md) 为准。

# 历史注释迁移记录

原始8份完整源码及全部旧注释/docstrings在baseline_sources中保留。以下为被压缩的历史叙述；代码中保留现行单位、公式与边界。

```text
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
# 本表声明合法取值。stage1_reading记录架构读数并由legality约束b1；
# dem_mode由scheduler.make_scheduler选择固定A/B或因果slice洗牌。
# 字段合法不代表在所有runner生效；unary不支持的动态请求由run_sim明确拒绝。
# --------------------------------------------------------------------------

```

```text
# --------------------------------------------------------------------------
# stage1_reading 标签 -> b1 的对应关系。从三种读法的**定义**直接导出，不是
# 第二份需要手工同步的事实表：
#
#   paper_consistent  historical seven-decision-bit hypothesis (default)
#   paper_literal     nine unknown-input decisions; separate 4x dither port
#   legacy_codeword   historical six-decision-bit model, retained as a baseline
#
# 标签只是元数据，真正改变机制的是 b1（以及工厂方法一并设定的后端字段）。
# 两者不一致时，用户会以为自己切换了架构、实际没有 —— 与拼错的枚举值同类，
# 只是更隐蔽，因此在仿真入口拒绝（外部复核 2026-09-11 第三轮）。
#   注：`Config(b1=10)` 这类"只想动 b1"的配置仍可构造、可 validate()（它测的
#   是 DAC 电平数判据），只是不能送进仿真入口 —— legality 与 validate 分离。
# --------------------------------------------------------------------------

```

```text
    # Explicit architecture hypotheses. The paper's nine quantization bits
    # concern unknown input; the separate two-bit dither range statement does
    # not add decision information. The nine-bit factory uses an assumed 4x
    # dual port, while the seven-bit default preserves historical comparisons.
    # Neither hypothesis supplies undisclosed ADC2 or switching circuit detail.

```

```text
    # dither 从量化器转移到 RDAC 的机制。论文 [00]：dither range "is enhanced
    # by 2b when the result is transferred from the quantizer to the RDAC"。
    #   "range"      : 历史 RDAC 栅格转移假设（默认）
    #                  d_code = −round(d/ΔD)；"2^b 增强"是**余量能力**
    #                  （units_per_d1 = 2^b），只是栅格比，
    #                  不由粒度实验冒充证明。
    #   "granularity": v6.1 读法 —— 转移时按更细粒度重新取整（保留对照）。

```

```text
    # RA 增益模型（v3 审计修正：反馈电容必须真正进入传输关系，二选一，不许混写）：
    #   "charge" : 电荷一致模型 G[n] = C_active[n] / C_F_true。
    #              C_active 是**本次样本实际选中的 slice 集合**的总电容（逐样本），
    #              C_F 是这颗芯片的实际反馈电容。全局/slice 级失配由此进入
    #              增益通路（整体偏差 = 增益误差，DEM 轮转选中集合变化 = 增益波动）。
    #   "fixed"  : 理想固定增益 G = g0*(1+gain_error)，与电容无关（仅作对照）。

```

```text
    # cap_scale=1 时的单位电容失配 sigma。
    # 1e-4 是**行为学标定值**：令 s=1、DEM 开时的 SNDR ≈ 93.4 dB，对齐论文 SNDR=93.5 dB。
    # 它不是 PDK 值 —— 由 mismatch_from_pdk() 估算的物理值约 1e-3，差 ~10 倍，
    # 这个缺口正是"必须做单位权重校准"的证据（见报告）。

```

```text
    # 显式线性版图梯度，单位 = **相对失配 / 单位坐标**（绝对量，不被 sigma 缩放），
    # (gx, gy)，坐标归一化到 [-0.5, 0.5]。例如 gy=1e-3 -> 全 y 跨度上失配变化 1e-3。
    # v3 审计修正：此前梯度在 sigma 缩放之前加入，实际跨度被悄悄乘了 sigma。

```

```text
    # off       : 关闭
    # analog    : 输入注入（v1）→ 直接从 ±V_FS 扣量程，代价是可用范围变小
    # sampling  : 采样态电荷注入（专利 [10] Fig.6 / Fig.19）→ **不占输入量程**
    #             固定 2D 个单位不接输入而接 ±V_FS：其中 D+d_u 个接 +V_FS、
    #             D−d_u 个接 −V_FS。信号衰减 α = 1−2D/N 恒定，注入量 = d_u·step。
    #             代价从"量程"换成"恒定增益 α"，可在数字端精确补偿（但会按 1/α 放大后端噪声）。

```

```text
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

```

```text
    # --- (2) RA auto-zero（PPT p.34-35 两个 dB 数的结构化）---
    # auto-zero 消 RA 的 offset 与低频噪声（含 1/f），代价是存储电容 kT/C +
    # 噪声折叠 -> PPT 披露 SNDR 代价 1.6 dB。行为建模 = RA 输出噪声 × 折叠
    # 因子 10^(cost/20)。注意口径：PPT 的 −1.6 dB 以 RA 自身/整机为参照
    # 由其预算结构决定；折到本模型整机预算（RA 占噪声功率 ~74%）后整机
    # ΔSNDR ≈ −1.2 dB —— stage22 做"预测 vs 实测"一致性检验，不硬凑 dB 数。

```

```text
    # 独立量化器 sDAC 的电容占 RDAC 活跃电容的比例（小电容 -> 建立快 -> kT/C
    # 噪声大，由 sigma_sadc_sampling 单独刻画；这里只影响其建立误差）。

```

```text
    # t2 - t1，单位 Ts。**这是 KTC 方案最关键的一个参数**：
    # 校正项 kappa*v_N = G_R*(n_R - dx)，其中 dx 比 n_R 大约三个数量级，
    # 所以真正被放大进 ADC2 的是 G_R*|dx| —— 与 G_N/kappa 怎么分无关。
    # 由此得到硬带宽上限：2*pi*f*A*G_R*dt < ADC2 余量。
    # dt = Ts/256 时该上限约 5.6 MHz，正好覆盖 PPT 标注的 "DC - 5MHz" 信号带。

```

```text
[config.py:766]
        # 锚点必须是**标称单位电容**，不能乘 cap_scale。
        # v5 审计修正：原式 ref = c_unit0*cap_scale，而 c_unit 也含同一个
        # cap_scale 因子，两者相除把面积因子约掉了 —— 实测 unit_cap 从
        # 280.82 fF 变到 17.55 fF（16 倍）时 sigma 恒定 37.76 ppm，
        # 等价于宣称"把电容缩到 1/16 还能免费保住失配"，物理上不可能。
        # 修正后 sigma ∝ 1/sqrt(c_unit) ∝ 1/sqrt(cap_scale)，
        # 与 kT/C 的 1/sqrt(cap_scale) 同向 —— 缩电容要同时付两笔代价。

```

```text
[config.py:955]
        # 标签与参数必须一致。`stage1_reading` 只是一个名字，改变机制的是 b1：
        # 名字说一种读法、b1 却是另一种，等于用户以为自己切换了架构而实际没有
        # （外部复核 2026-09-11 第三轮）。对应关系只在 READING_B1 里维护。

```

```text
[config.py:967]
        # 独立审查 2026-09-25：域守卫同时拒绝非有限值

```

```text
[config.py:1005]
        # 独立审查 2026-09-25：域守卫同时拒绝非有限值

```

```text
[config.py:1093]
        # 判据 0（外部审计 F1 / B16）：第一级分辨率的**读数**必须与 DAC 拓扑
        # 自洽。`stage1_reading="paper_literal"` 要求 b1 位判决电平全部可由
        # DAC 的物理电平表达，即 2**b1 <= DAC 可用电平数。
        #   等权拓扑：可用电平 = n_units_sig（= n_active*n_unit_per_slice
        #              − headroom）；
        #   分段拓扑：可用电平 = dac_n_main * dac_n_sub。
        # 不自洽时（如把 b1=9 套在 (64,8) 分段阵列上）模型不会报错，
        # 而是静默把 residue 推出 ADC2 窗口、输出跳到伏级 —— 这正是审计
        # 要求"先修正骨架"的量化理由。此判据把这种组合变成显式 FAIL。

```

```text
[config.py:1119]
        # 判据 0b：`dither_enhancement_bits` 是**披露值**（论文："enhanced by 2b"），
        # 不是可以随读数任意漂移的自由参数。它必须等于
        #     log2(units_per_lsb1) = log2(DAC 电平数 / 2**b1)
        # 也就是「一个粗判决步含多少个 RDAC 单位步」。
        # 这条等式把 ADR 0003 的收敛解写成了机器可校验的约束：
        #   paper_consistent (b1=7, 512 电平) -> 增强 2b ✓
        #   paper_literal: 9b + explicit independent dual-port amplitude ratio 4.
        #   legacy_codeword  (b1=6, 512 电平) -> 增强 3b ✓（v6.1 读法，> 披露值）
        # 若有人只改 b1 而不重推读数，这里立刻 FAIL，不会静默放行。

```

```text
[config.py:1213]
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

```

```text
[config.py:1245]
            # v3 审计修正：缩短 Δt 是把问题转移给观测通路的建立，不是免费午餐。
            # 报告"要在这个窗口内建立到 eps，观测通路至少需要多大一阶带宽"。

```

```text
[config.py:1280]
            # RDAC 电平数必须同时覆盖 (a) 2**b1 个粗判决电平 与 (b) 论文披露的
            # dither 量程增强 2**dither_enhancement_bits 个单位步。
            #   需求 = 2**b1 * 2**enh
            # 这条判据曾把 "+3b" 写死，源自 v6.1 的 b1=6 读法；换到
            # paper_consistent（b1=7、enh=2）后它会给出 2^10=1024 > 512 的
            # **假 FAIL** —— 把与读数无关的常数当作物理需求，正是本次审计
            # 要求清除的一类缺陷。现在增强位数从配置取，随读数自动跟随。

```

```text
[config.py:1324]
        # 判据：RA 增益口径必须是**已实现**的取值之一。此前 "fixed" 在
        # sim_split / pipeline 里被硬编码的电容比静默忽略（固定增益对照实验
        # 因此从未真正生效），而任何拼写错误也会悄悄退化成 "charge"。
        # 契约：配置在某条链路里要么按声明生效，要么被显式拒绝，不能静默忽略
        # （外部复核 2026-09-11）。
        # 取值集合来自 LEGAL_VALUES（单一来源）；check_legal() 用同一张表在
        # 仿真入口直接把非法配置拒绝掉，本记录是给报告读者看的第二道。

```

```text
        # 量化器侧 dither（论文："dither range is enhanced by 2b when
        # transferred from the quantizer to the RDAC"，stage21）：
        # d_Q 加在 **SADC 决策通路**，RDAC 保存通路不含 dither。
        # 粗码在 x+d_Q 上决策 -> 数字侧把 d_Q 转移给 RDAC 码（d_u 在
        # sim_split/pipeline 里按 cfg.dither_quant_transfer 的粒度取整），
        # 余项 d_Q − round(d_Q) 落进残差、由 ADC2 在其窗口内吸收。
        # dither_code 语义（本模式）= d_Q/Δ1 [Δ1 当量]（量化器粒度的转移值，
        # 仅供诊断；真正写进开关码的取整在主循环做，因为粒度依赖拓扑 step0）。

```

```text
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

```

```text
[weight_calibration.py:141]
        # 独立审查 2026-09-26（v8.2.2）：spec 的电压标度与 dither 档位范围此前没有任何闸门，
        # 而 `CalibrationSpec.from_config` 才走 cfg.check_legal()——手工构造的 spec 完全绕过。
        # 这些标量却不是旁观者：design 矩阵由 v_fs 派生，解码后的 fine_v 由 adc2_v_min/v_max
        # 派生。非有限值一旦漏进来，轻则让 svd 报出 LAPACK 层的 LinAlgError（甚至 “slice 0
        # has a NaN entry”），重则在 design 的零元与非有限 theta 之间算出 0*inf——一次非法
        # 浮点运算，是否被上报取决于 BLAS 后端。在入口一次性拦下，比在下游各处补丁更可靠。

```

```text
[weight_calibration.py:396]
    # 独立审查 2026-09-26（v8.2.2）：SVD 因子必须有限，且必须在任何矩阵乘法之前拦下。
    # 下面的 theta = vh.T @ ((u.T @ fine_v) / s) 以及 residual = fine_v - design @ theta
    # 会让 design 的零元与 theta 的非有限元相乘，即 0 * inf —— 一次非法浮点运算。是否被
    # 上报取决于 BLAS 后端：x86-64 OpenBLAS 置位 invalid 标志，numpy 于是抛
    # RuntimeWarning（本仓 filterwarnings 把 RuntimeWarning 升为硬错误），而 Apple
    # Accelerate 不置位，缺陷因此在本地潜伏、只在 CI 暴露。放在 rank 比较之前还有第二个
    # 理由：s 含 nan 时 "nan > x" 恒为假，rank 会被静默低估并报出误导性的可辨识性错误。

```

```text
[weight_calibration.py:418]
    # 独立审查 2026-09-25：权重须为有限正值

```

```text
        # 回归量 = alpha*x + d_nom - vd0。**alpha 只乘信号项**：
        # 采样态 dither 是直接注入到保存电荷里的，不经过 alpha 衰减
        # （v3 自查：把 alpha 乘到整个 (x+d-vd0) 上，sampling 模式校准会偏 ~4%）。

```

## 历史docstring原文

### config.py:1501 noise_budget

```text
把噪声预算拆开：kT/C、RA、ADC2 量化、KTC 观测通路。

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

```

### config.py:1537 mismatch_from_process_assumption

```text
**根据工艺假设**估算单位电容失配 sigma（不是 PDK 测量值）。

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

```

### config.py:404 stage1_levels

```text
第一级对**未知输入**实际拥有的判决电平数。

        这是审计 F1 的核心量。``paper_literal`` 读数下等于 ``2**b1``；
        ``legacy_codeword`` 读数下 dither 是已知量，不增加对输入的判决信息，
        因此判决电平数仍为 ``2**b1``（b1=6 时 = 64），而**码字**宽度是 9 bit。
        两者相差 8 倍，直接决定残差摆幅、RA 输出范围和后端位预算。

        Returns:
            第一级对未知输入实际拥有的判决电平数（个，无量纲）。
            [派生] 口径见上：与 codeword 读数下的 9 bit 码字宽度不同，勿混用。

```

### config.py:438 legacy_v61

```text
返回与 v6.1 完全等价的配置，用于复现已发表旧结果。

        v6.1 把论文的 "9b in the first stage" 读成 6b 粗判决 + 3b dither 拼成
        9b 码字，并据此设定后端 15 bit / [-0.3, 3.3] V。外部审计指出该读法下
        判决电平只有 64 个。此处保留它**只为可复现性**；新工作请用
        :meth:`paper_consistent`。

        Returns:
            一个 ``stage1_reading="legacy_codeword"`` 的 Config
            （b1=6，判决电平 64，dither 增强 3b）。

```

### config.py:636 c_active_nominal

```text
名义活跃采样电容 = 本次实际参与的 n_active 个 slice 的总电容。

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

```

### config.py:764 dither_alpha

```text
信号传递系数 α = 采样开关掩码移除的信号电荷占比的补。

        v5.1 第三轮审计 §四：α 不能再用一个统一的"单位数比"公式代表所有
        拓扑 —— split 的子阵列单位对 C_sig 的贡献是 β·c_u（桥接衰减后），
        主阵列单位是 c_u，两者权重不同。

        * unary：2D 个等权单位移出输入 → α = (N−2D)/N（旧公式，不变）。
        * split：2D 个单位按 dither_split_bank 从主/子阵列移出，
          α = 1 − 2D·w_bank/C_sig_nom，w_bank = c_u（主）或 β·c_u（子）。
          例：(64,8)、D=2、掩码在子阵列 → α = 64.5/65 = 0.992308，
          而旧的 512 等权公式给 508/512 = 0.992188 —— 数值接近但来源错误，
          换拓扑/换 D 后偏差会放大。

        当前物理pool还由同一掩码计算输入负载和信号系数；噪声等效电容
        保留掩码电容的采样噪声贡献，见slice_pool.split_coefficients。

        Returns:
            信号传递系数 α（无量纲，0 < α ≤ 1）。[派生]
            口径与勿回退教训见上：换拓扑时不得退回 512 等权公式。

```

### config.py:816 sigma_sampling_c

```text
按**本次实际参与采样的电容**计算 kT/C（v3 审计修正）。

        没有参与本次采样的备用 slice 不能替这个样本降低 kT/C，
        所以噪声必须绑定逐样本的 C_active[n]，不能用整池总电容。

        Args:
            c_active: 本次实际参与采样的电容（F）。[派生]

        Returns:
            差分χ=2口径的kT/C噪声rms（Vrms），=sqrt(2kT/c_active)。
            生产capture按cfg.chi另行计算；本静态辅助函数固定χ=2。

```

### config.py:934 ktc_eta_x

```text
Δx 的建立系数：输入变化在窗口内逐渐积累（线性斜坡），

            eta_x = 1 - (tau_a/T_w) * (1 - exp(-T_w/tau_a))

        v4 审计修正：**eta_n != eta_x**。旧版把同一个 (1-eps) 同时乘 n_R 和 dx，
        隐含"输入变化也是窗口开始就出现的阶跃"——物理上不成立。
        低带宽极限（T_w << tau_a）下 eta_x = eta_n / 2；高带宽极限二者都 -> 1。

        Returns:
            Δx 的建立系数 η_x（无量纲）。[派生] 与 η_n 不等，勿合并回退。

```

### config.py:964 beta_eff

```text
名义 beta = (1+beta_error) * kappa * G_N / G_R。

        v3 注意：charge 模式下 G_R 逐样本 = C_active[n]/C_F，此处返回的是
        以名义 G_R = g0 计算的参考值；逐样本真实 beta 由 ktc.observe 按 g_vec 计算。

        Returns:
            名义 beta（无量纲），= (1 + beta_error)·κ·G_N / G_R。[派生]
            逐样本真实 beta 由 ktc.observe 按 g_vec 计算，此处为参考值。

```

### reconstruction.py:110 update_gain

```text
最小二乘：fine 对 (alpha*x + d - vd0) 回归得到 G_actual。

            v_2 = G * (alpha*x + d_phys - vD_true) + noise
        数字端已知的自变量是 alpha*x + d_nom - vD0（d_nom = 名义 dither）。
        **v3 审计修正：d_known 必须传入。** 旧版回归量漏掉 d，dither 开启时
        dither 方差远大于 (x - vd0) 方差，斜率被严重拉低 —— 实测 analog dither
        下 G_hat = 6.68（真值 33.6），校准直接崩溃。

        **为什么不能拿 out 对 x 回归**（v1 的错误做法）：
            out = vD0 + v_2 / G_hat
        vD0 本身是 x 的阶梯函数，其平均斜率约等于 1，于是
            d(out)/d(x) = G/G_hat + (1 - G/G_hat) ≈ 1
        与 G 的真实偏差几乎无关。实测：G_real=32.16（+5e-3 误差）时
        该斜率只有 0.999962，校准量比正确值小 3 个数量级 —— 校准形同虚设。

        这个估计同时吸收级间增益误差与 RDAC 总电容偏差（满幅增益误差）。

        Args:
            x_known:   已知校准输入 x [V]（可直接获取）。
            vd0:       粗码标称电压 vD0 [V]。
            fine:      ADC2 输出 v2 [V]（RA 输出口径）。
            d_known:   可选 dither 名义项 d_nom [V]；缺失则退化为无 dither。
            alpha:     可选衰减 α [无量纲]；None = 1（仅乘信号项）。
        Returns:
            G_hat [无量纲]，更新后的增益估计（写入 state.estimated_gain）。
        Side effects: 写 state.estimated_gain（仅当 g>0）；denom<=0 时
            原样返回旧估计（不更新）。

```

### reconstruction.py:163 update_beta

```text
Beta 前台校准：对固定目标 x1 回归，再用 kappa_new = kappa/beta_hat 校正。

        x_hat = x1 + beta*dx + (1-beta)*n_R - e_D + ...
        beta_hat = <out - x1, dx> / <dx, dx>

        v3 审计修正（两条）：
        1. 旧版传入的 err 用 x_ref = x1 + beta_true*dx —— 参考已代入待估参数，
           估计结果恒为 1（实测 beta_true=1.2 时 beta_hat≈1.00013）。
           现在用**固定目标 x1**，噪声与 e_D 在多样本下平均掉。
        2. 校正规则改为 kappa_new = kappa_old / beta_hat：目标 beta=1，
           而 beta ∝ kappa，所以直接按比例缩。**不再读取 cfg.g_actual** ——
           数字侧不应知道真实模型参数。

        Args:
            out:   校准输出 x_hat [V]（已含噪声与 e_D）。
            x1:    固定目标 x1 = x(t1) [V]（禁用 x_ref 口径）。
            dx:    窗口内输入变化 x2−x1 [V]。
        Returns:
            beta_hat [无量纲]，当前实际 beta 的估计（写入 state.beta）。
        Side effects: 写 state.beta；按 kappa_new = kappa/beta_hat 写
            state.kappa（仅当 beta_hat>0）。

```

### sampler.py:37 SampleBatch

```text
一次采样的全部事件级实现（不可变语义：下游只读，改写仅限 sampling_dither_injection 的注入重标定——它必须同步改 x_rdac 与 dither）。

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

```

### sampler.py:83 capture

```text
按采样事件生成噪声；同一个 n_R 实现贯穿该样本的全部后续处理。

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
        dither_codes: 可选的已知整数扰动序列 [RDAC 单位]；用于结构 RTL
            精确对照，普通行为仿真仍使用独立随机源。

    Returns:
        SampleBatch（字段单位见 SampleBatch 的 docstring）。

    Side effects:
        消耗 rng（及 dither_rng，若给定）的随机抽取；不改传入的 cfg/chip。

```

### sampler.py:364 spectral_derivative

```text
Uniform-grid derivative via the FFT (exact for band-limited records).

    Why not ``np.gradient``
    -----------------------
    A central difference has amplitude response
    ``sin(2*pi*f/fs) / (2*pi*f/fs)``. At f = 0.95*(fs/2) it is about **0.0524**
    -- the derivative is underestimated by ~25.6 dB, so any effect that is
    proportional to ``dx/dt`` (interleaving timing skew is exactly that) is
    silently suppressed near Nyquist. An external audit measured this on v6.1
    (finding A07.3) and warned that low-frequency skew results must not be
    extrapolated.

    The spectral derivative is exact for resolved periodic tones strictly
    below Nyquist. A real Nyquist-bin component does not determine its continuous
    derivative; this routine returns its real spectral derivative (zero). It is the fallback when
    the input function cannot supply an analytic derivative.

    Args:
        x:  ``(N,)`` samples on a uniform grid.
        dt: Sample spacing [s].

    Returns:
        ``(N,)`` derivative estimate, same units as ``x``/``dt``.

```

### sampler.py:397 input_derivative

```text
``d/dt`` of an input waveform at times ``t`` -- skew's driving quantity.

    Preference order:

    1. **Analytic.** ``sine_input`` / ``dc_input`` attach a ``.derivative``
       attribute; a general callable may do the same. Exact at any ``fin``,
       including near Nyquist.
    2. **Spectral.** Otherwise :func:`spectral_derivative` on the uniform
       periodic grid, for resolved content strictly below ``fs/2``. At Nyquist
       the continuous derivative is not identifiable without extra information.

    ``np.gradient`` is deliberately **not** used: see
    :func:`spectral_derivative` for the 25.6 dB near-Nyquist bias that made
    it unacceptable (audit A07.3).

    Args:
        input_fn: any callable ``t -> x``; may carry ``.derivative``.
        t: ``(N,)`` uniform time grid [s].

    Returns:
        ``(N,)`` slope [V/s].

```

### sadc.py:153 units_per_first_stage_step

```text
第一级一个判决步长 = 多少个 DAC 单位步。

    这是 ``stage1_reading`` 与 DAC 拓扑之间的**唯一**换算关系，任何需要
    它的人都必须调用本函数，不得自行推导（外部审计 B16）。

    Args:
        cfg: 全局配置。
        dac: 分段拓扑的 :class:`~adi_model.dac_arch.SplitDAC`；等权拓扑传
             ``None``。

    Returns:
        单位步数（``int``，至少为 1）。
        等权拓扑下等于 ``cfg.units_per_lsb1`` = ``n_units_sig // 2**b1``；
        分段拓扑下等于 ``dac.levels // 2**b1``。两种口径在拓扑自洽时相等。

```

### sadc.py:174 build_first_stage_quantizer

```text
构造第一级量化器的**唯一入口**。

    pipeline 与 sim_split 必须都调用本函数。历史教训（外部审计 B16）：
    两条主循环各自推导阈值步长，b1=6 时 ``n_sub*step0`` 与 ``cfg.delta1``
    数值巧合相等因而长期掩盖了分歧；一旦按论文把第一级读到 9b，
    两者相差 8 倍，退化等价测试立刻失败。

    口径：
        * 提供 ``dac``（分段拓扑）时，阈值对齐 **DAC 自己的名义栅格**：
          ``thr[c] = v_lo + c * (units_per_d1 * step0)``。这样
          ``k_eq = coarse * units_per_d1`` 恰好落在阈值上，残差被限制在
          ``[0, units_per_d1*step0)`` 之内（无噪声/失配/饱和时）。
        * 不提供 ``dac``（等权拓扑）时，阈值就是理想栅格
          ``-v_fs + c * cfg.delta1``。

    Args:
        cfg: 全局配置。
        dac: 可选的分段 DAC 实例。

    Returns:
        配置好名义与实际阈值的 :class:`SADC`，全部非理想性已施加一次。

```
