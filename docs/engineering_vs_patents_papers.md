# 整体代码工程与专利/论文原理对比

> **文档定位**：把本仓库的每一块代码工程逐一对回它的文献源头——
> 主架构论文 [00]（ISSCC 2024 Session 9.8）与机制专利 [09]–[14]，
> 标明三件事：(1) 文献披露的原理是什么；(2) 代码实际实现了什么；
> (3) 两者的对齐状态（✅ 对齐 / ⚠️ 机制对齐但口径不同 / ❌ 未实现）。
>
> 引用规则与 `../NOTICE` 一致：本文只引用条目（作者/会议/DOI/专利号），
> **不转载任何第三方原文**；所有"论文说 X"级别的主张对应 `DISCLOSED`
> 参数，见 `provenance.PARAM_GRADES`；证据边界以
> [`model_scope.md`](model_scope.md) 为准，本文不重复其结论，只给出
> 模块级出处映射。

---

## 1. 参考文献索引（引用条目，原文不随仓库分发）

| 编号 | 文献 | 在本工程中的角色 |
|:---|:---|:---|
| [00] | R. Bodnar et al., "A 9.3 nV/√Hz 20b 40MS/s 94.2dB DR Signal-Chain Friendly Precision SAR Converter", ISSCC 2024, 9.8, pp.182–183. DOI: 10.1109/ISSCC49657.2024.10454329 | **主架构目标**：拓扑、slice 池、共享 RA、auto-zero、dither 范围增强 |
| [00_1] | ISSCC 2024 Session 9.8 讲稿（作者副本） | 参数来源（NSD/DR/G0/18-slice 结构/噪声代价 dB 值） |
| [09] | US 10,516,408 B2 — Analog to digital converter stage | 量化器与残差 DAC 分离；SADC 误差自愈窗口；两条采样通路响应匹配 |
| [10] | US 10,505,561 B2 — Method of applying a dither, and ADC | dither 的两种物理实现（输入注入 vs 采样态电荷注入）；注入/改码/扣除三要素配对 |
| [11] | US 10,511,316 B2 — Linearizing transfer characteristic by DEM | 等权单位置换 DEM；主/子阵列各自轮转；主/子边界残差难点 |
| [12] | US 10,707,889 B1 — Interleaving method for analog to digital converters | 交织 ADC 的跟踪状态更新（机制级模型已实现：`interleave_tracking.py`，M11；主链路集成未做，见 §4/§6） |
| [13] | US 10,541,702 B1 — Auxiliary input for ADC input charge | 辅助输入端口与电荷核算（机制级模型已实现：`aux_input.py`，M12；主链路集成未做，见 §4/§6） |
| [14] | US 10,826,519 B1 — Low power reference for an ADC | 低功耗参考缓冲方案（机制级模型已实现：`ref_track.py`，M13；回答 A06 的机制层；主链路集成未做） |
| [01]–[08] | Hurrell ISSCC 2010、ElShater ISSCC 2019、Li ISSCC 2023、Bannon VLSI 2014、LTC2387-18、Steensgaard ISSCC 2022、TI ADC3583、Shen JSSC 2018 | 背景/替代架构对照，**不属于**目标芯片，见 §7 |

---

## 2. 总体架构对比：论文 [00] 的数据流 vs 代码的相位流水线

论文 [00] Fig. 9.8.1 的数据流（18-slice 池 + 匹配量化器 sDAC + RDAC + 共享 RA + ADC2）
在 `pipeline.py` 中被实现为**逐相位状态机**（每样本一个转换周期）：

```
论文 [00] 概念结构                 代码实现（pipeline.py 周期 n）
─────────────────────────────     ─────────────────────────────────────────
量化器 sDAC（与 RDAC 分离）   →   SlicePool.acquire_quantizer（独立
                                    sDAC，cfg.sadc_cap_ratio × 活跃电容）
8 sDAC 转换 / 8 sDAC 采集      →   scheduler.reserve_dual → 转换组/采集组
（18 片池，2 片 spare）             （slice_pool.py 物理池，跨周期因果）
RDAC 保存残差电荷              →   SplitDAC.evaluate_physical + 顶板保持
                                    v_top[i] ← x_R − v_D,0(k[n])
共享残差放大器 RA              →   ra.py：电荷一致增益 G=C_active/C_F
ADC2 解细码                    →   adc2.py：静态量化 + 溢出统计
数字重构                       →   digital_core.reconstruct：
                                    x̂ = (vD0 + fine/Ĝ − d_corr)/α
```

**对齐状态**：结构 ✅。两个独立实现（`pipeline.py` 逐相位状态机 与
`sim_split.py` 逐样本向量化）在非理想因素全关时**逐位等价**
（stage19 验收①，`test_degenerate_equivalence_is_bitwise`）——这是
"共享接口被两套实现一致解释"的最强校验，也是本工程与"只跑一条通路"
的行为模型在方法论上的分界线。

**工程特有、文献没有的部分**（本仓库方法论，非 ADI 披露）：

| 工程机制 | 文件 | 目的 |
|:---|:---|:---|
| 参数来源运行时分级 `DISCLOSED/DERIVED/FITTED/ASSUMED` | provenance.py | 把"注释分级"变成可测试的运行时值；每个 headline 数字可追责到文献或假设 |
| 独立电荷参考求解器 | charge_ref.py | 不复用 SplitDAC 闭式解，逐相位节点方程独立生成真值，防"口径局部替换"系统性风险 |
| 模拟/数字数据流边界 | digital_core.py | 数字算法禁止读 `chip.C_true`；`dac_error` 只许事后评分，出现在算法路径即越界 |
| 验收记录门禁 | acceptance.py | `REQUIRED_RECORDS` + 严格 bool 判定（`np.bool_` 归一化，v7.0.4），验收项不可静默消失 |

---

## 3. 逐模块对比：专利/论文原理 → 代码实现

### 3.1 `sadc.py` — 第一级粗量化器 ↔ [00] + [09] §1.3

| 文献原理（披露） | 代码实现 | 状态 |
|:---|:---|:---:|
| [00]："The quantizer is fully separated from the residue generating DAC (RDAC)"；量化器由一片 slice DAC 构成，matched-quantizer sDAC + RDAC 实现 >11b（讲稿口径 >12b）匹配 | 独立量化器 sDAC（pipeline 显式建模）；`config.validate` 以 12b 作门限 | ✅ |
| [09] §1.3：粗码误差不会直接叠加到输出——残差被推出名义 bin，只要放大后仍在 ADC2 窗口内，输出不受影响 | flash 阈值 + searchsorted；可恢复余量 = (ADC2 余量)/G ≈ **4.6875 mV**——注意这是**当前模型量程与增益配置下的推导值**（0.15 V 余量 ÷ G0=32），非专利披露的固定容差；数值随配置联动（experiments.stage19 验收②，9B 实验） | ✅ 机制（数值附配置条件） |
| 第一级"9b quantization"与 dither range"enhanced by 2b"两条披露的联立读法 | `b1 = 7`（ADR 0003），`units_per_lsb1 = 4`；`paper_literal`/`legacy_v61` 备选读法保留、不静默选用；`b1` 标 `ASSUMED`（架构读数） | ⚠️ 读法自洽但非唯一 |

### 3.2 `sampler.py` — 双采样通路与 dither 注入 ↔ [09] §1.5 + [10]

| 文献原理（披露） | 代码实现 | 状态 |
|:---|:---|:---:|
| [09] §1.5：两条采样通路（SADC/RDAC）**匹配的是采样响应，不只是电容值**；Δτ 造成一阶跟踪误差 −Δτ·dx/dt（信号相关，非静态增益） | `x_S`/`x_R` 两条独立通路；`sampling_tau_mismatch` 参数（机制来自 [09]，数值 [假设]）；通路不匹配的 INL 效应归 dynamics | ✅ |
| [10] Fig.6/Fig.19：dither 的物理实现为**采样态电荷注入**——2D 个单位不接输入而接 ±V_FS，不占输入量程 | `sampling_dither_injection`：掩码单位接 ±V_FS；输入注入 vs 采样态衰减的代价对比（stage9A）；α = (N−2D)/N 恒定衰减在 `reconstruction.py` 由**同一份掩码**推导 | ✅ |
| [10] §2.6：整数 dither 不进粗码 | 注入/改码/数字扣除三要素配对（`DitherState`，缺一不可）；dither 只改 RDAC 码，粗码路径零注入 | ✅ |
| [10]：同一 `n_R` 实现贯穿该样本全部后续处理（事件语义） | `sampler.capture` 事件级实现；`dither_changes_in_window=True` 显式 `NotImplemented`，不静默走错口径 | ✅ |

### 3.3 `rdac.py` / `dac_arch.py` — DAC 拓扑 ↔ [00] + [11] + [10] Fig.19

| 文献原理（披露） | 代码实现 | 状态 |
|:---|:---|:---:|
| [00]：RDAC = 18 slice × 单位阵列，20.5 pF / 512 单位 | `step = 2·v_fs/512`（[披露-推断]）；LUT 由 `chip.C_true` 一次构建、只读 | ✅ |
| [10] Fig.19 / [09] Fig.12：小数权重可用 slice **异码合成**实现（实数码 k） | 9C 实验：异码合成名义守恒、但物理误差访问改变；`mapper` 提供分数码路径 | ✅ |
| [11]：DEM = **等权单位的位置置换**；主/子阵列用**不同步长**各自轮转 | `mapper.map_code`：横向 3b（slice 轮转 `(sid·331)%64`）⊗ 纵向 3b（slice 内 `(sid·173)%8`）；`DAC_nominal(M(c,state)) = V_target(c)` 对任意 state **结构性**成立（非调参） | ✅ 机制 / ⚠️ 排列空间（见下） |
| [11] 核心难点：主/子各自 DEM 后逼近**各自**平均值，叠加桥接比例误差 → 边界残差 DEM 消不掉 | `dac_arch.boundary_error` 把它做成**可观测量**；12.3 实验量化；桥接误差单自由度、一次校准可消（与 [10] 动机一致的架构级结论） | ✅ |
| 等权拓扑没有的项：桥接电容 C_C 比例误差 `δw_S/w_S ≈ (1−β)·δC` 是一阶**系统性** INL（周期=主阵列 LSB 锯齿），**DEM 完全无效** | 两浮动节点严格解（非近似）：β、C_ser、C_eff 闭式；DEM 对 C_C 项无效作为结论写入文档 | ✅ |

**⚠️ R16（第五份复核钉子）**：两个轮转都由同一个 `sid` 驱动，联合物理
排列空间是 **64**（`lcm(64,8)`），不是 `N_DEM_STATES = 512` 个标签暗示
的 512——主→子排列 1:1 映射。这**不违反** [11] 的机制（等权置换、
名义守恒均成立），但 [11] 描述的是切片选择 + 横向/纵向 shuffle + 二进制
到 unary 桥接的**三维**机制，本模型的排列空间与它不同构：任何"DEM
收益"数字不得引用为 [11] 机制的定量复现。钉子在
`TestR16DemPermutationSpace`；能力边界见 `model_scope.md` §4.11。

### 3.4 `scheduler.py` / `slice_pool.py` — 18-slice 池与交织 ↔ [00] + [12]

| 文献原理（披露） | 代码实现 | 状态 |
|:---|:---|:---:|
| [00]：默认 A/B 乒乓调度的集合关系（8 转换 + 8 采集、逐样本两两不相交） | `Scheduler.reserve_dual` + `SlicePool.invariants` 断言 | ✅ 调度不变量成立 |
| [00]：18 片池的合法采样历史（转换组必须持有它实际采集的电荷） | `PhysicalSlicePool.shuffle_causal`：8191/8191 跨周期因果、coverage 8.000/8 | ✅ 辅助模块已验证，**未接入主入口**（R1/R2，xfail 跟踪） |
| [00]：主链路中 8/18 随机调度与电荷、权重、增益的联动（"2 片 spare 打散交织杂散"的物理收益） | 主入口用内部 `SlicePool` + 固定 `c_sig`；`ShuffledScheduler` 每周期独立抽取（0/8191 因果） | ⚠️ 未闭合，定量收益待重测 |
| 交织杂散位置 f_S/2、f_S/2±f_IN（结构性结论） | stage19 验收③④；[假设] 的是 skew/带宽失配**幅度**，位置可用 | ✅ |
| 采样时确定的 allocation 不可追溯更改 | Allocation 采样时确定并保存（硬约束）；`reserve_dual` 逐样本两两不相交由 `invariants` 断言 | ✅ |

> **关于 stage19 的 31 dB（带宽失配）/ 42 dB（timing skew）抑制数字（2026-09-11
> 第六份复核订正）**：这是**当前非因果洗牌实现**（`ShuffledScheduler`，作用于
> 误差系数而非物理电容）的实验结果，**不作为物理架构收益或设计预算引用**。
> 物理池接入主路径（R1/R2）之前，这两个数不能用于放宽 slice 间匹配指标。

**⚠️ 与 [00] 口径的两处已登记差异**（详见 `model_scope.md` §2.5）：
1. `ShuffledScheduler` 每周期独立抽取 → `conv[n] = acq[n−1]` 仅 0/8191
   成立（真实电容不能转换它没采过的电荷）。`PhysicalSlicePool` 修复了
   跨周期因果（8191/8191、coverage 8.000/8），但**尚未接入主路径**
   （R1/R2，`xfail(strict=True)` 跟踪）。
2. `PhysicalSlicePool` 证明"洗牌打散杂散"必须作用在**物理电容权重**
   上，而不是误差系数上——这正是本工程从 v6.1 审计里学到的、论文
   [00] 结构隐含但没有明说的约束。

**❌ [12]（交织跟踪）**：专利 [12] 的核心是由另一颗 ADC 的结果驱动的
**跟踪状态更新**（tracking state update）。`grep aux|tracking src/` →
0 命中（第五份复核 §5 实测）。交织的**静态**失配效应有建模，跟踪
机制没有——任何驱动 sizing/杂散抑制的主张不得引用 [12]。

### 3.5 `calib.py` / `mapper.py` — 校准可观测性 ↔ [11]

| 文献原理（披露） | 代码实现 | 状态 |
|:---|:---|:---:|
| [11]：打破交织映射下单位简并的手段 = 低位**跨 slice 分配**，让同一码值对应不同单位集合 | `calib.observability()`：使用矩阵 U 的秩 = 可辨识维数；v4 实测 rank = 64/512 是**结构**问题；DEM/随机置换/专门校准模式是打破手段，各有激励与数据量代价 | ✅（判据框架） |
| —（本工程独立结论，[11] 未给出量化方法） | `ridge_fit` 只在可辨识子空间做最小二乘（不假装估全部）；`required_samples` 反推观测数；out-of-sample + 整数/分数码双口径防 in-sample 自欺 | ✅ 工程 |

### 3.6 `dynamics.py` — 动态误差 ↔ [00] 讲稿 p.32 + [14]

| 文献原理（披露） | 代码实现 | 状态 |
|:---|:---|:---:|
| [00_1] p.32：点名输入建立 / 参考建立 / 数字串扰是限制 INL 的非理想源（v1–v4 缺失它们导致 |INL| 0.07 LSB vs 论文实测 2.2 LSB、30 倍缺口） | 三项分别建模：**(a)** 输入建立（τ 码相关 → 码相关 INL）；**(b)** 参考建立（静态抛物线 bow + 动态斜率项）；**(c)** 数字串扰（三角形翻转数 A(k)，共模部分 DEM 无效） | ✅ 机制 / ⚠️ 数值（`dyn_*` 全部 [假设]，用形状与趋势、不用值） |
| [14]（低功耗参考）：参考缓冲方案 | **未实现**。参考路径是瞬时增益+噪声+clip；无 coarse/fine MUX 事件、无跟踪方程、无有限带宽/压摆/跨样本记忆。这是审计 A06——本模型与 [00] 架构之间**最大的单点缺口**：不能解释参考仅 ~15b 精度时 RA 起动、~65% RA 相位才到 20b 的方案为何成立，也不能解释为何积分式 RA 不可行 | ❌ |
| 输入建立 τ 的口径 | 聚合单节点 RC 是"保守上界"**只在支路开关项主导时成立**；星形网络公共模式 τ = R_s·C_total + R_on·C_slice，R_s 项不随划分缩小（例：1.025 ns vs 0.666 ns，仅 1.54× 非 8×）——R17 钉子（`TestR17CommonModeTau`） | ⚠️ 已限定 |

### 3.7 `ra.py` — 共享残差放大器 ↔ [00]

| 文献原理（披露） | 代码实现 | 状态 |
|:---|:---|:---:|
| [00_1] p.11：共享 RA，占 ADC 功耗 ~40%；GMR + OTA 架构 | **行为级**增益、噪声、限幅已实现：电荷一致口径 `G[n] = C_active[n]/C_F_true`。**但主入口（pipeline.py:348–354）把固定标量 `c_sig` 复制成常向量传入 `gain_vector`**——接口支持逐样本增益 ≠ 主入口已产生物理正确的逐样本增益；转换组对应的增益变化、复用初始状态与有限建立过程**尚未完整接入**（第六份复核 §2.2 订正） | ⚠️ 行为级成立，逐样本物理增益未在主入口成立 |
| p.34–35：auto-zero 消 offset/漂移；噪声代价 −1.6 dB；ADC2 动态采样带宽 +1.3 dB | **披露效果的预算模型**：白噪声按折叠因子 10^(1.6/20) 放大、1/f 整体移除；两个 dB 数值做符号与量级的**结构**校验（stage22）。**未建立** AZ 电容/开关/宽带建立-窄带采样的**相位级噪声传递模型**——噪声倍率与披露一致只说明预算采用了该披露，不证明相位行为被模拟（第六份复核 §2.3 订正） | ⚠️ 效果系数对齐，非电路结构对齐 |
| （RA 噪声预算） | `noise_out` 由 target_dr_db=94.6 **先锚后乘**（锚点 = AZ 关 + 动态带宽关的基线，v7.0.5 口径注记）：模型证明预算*可满足*，不证明电路*达到* | ⚠️ [拟合] |
| 有限 GBW 建立、RA 复用/功耗循环 | 未建模（RA 瞬时建立；建立类误差由 dynamics 三项承担；~50% idle 只影响功耗，功耗不在预测域） | ❌（口径声明） |

### 3.8 `noise_phase.py` / `ktc.py` — KTC 观测消噪 ↔ **无文献源头（原创扩展）**

**明确声明**（`NOTICE` 末段 + `provenance`：`ktc_enable` 标
`NOT in [00] or [09]-[14]; original research extension`）：KTC 噪声观测
消去分支是本仓库作者的**原创研究扩展**，不是 [00] 或专利 [09]–[14]
披露的任何特性，**不得归属于 Analog Devices**。

| 原理 | 代码实现 | 状态 |
|:---|:---|:---:|
| 同一个采样噪声实现 `n_R` 必须同时进入残差通路与观测通路（物理相关性建模，非算法偷读真值） | `v_R = G_R(x1+n_R−vD)+e_R`；`v_N = G_N(n_R−dx)+e_N`；数字端 `κ·v_N` 扣除 | ✅ 原创框架 |
| 逐相位噪声状态传递：残差通路 a=[1,β]/C_sig、观测通路 b=[1,γβ]/C_sig；相消后 σ²_res=(a−κb)ᵀΣ(a−κb)+κ²σ_eN² | `noise_phase.py` 三推论逐项验证（γ=1→κ_opt=1；γ=0→子阵列噪声结构性不可消；C_n,eq = C_sig²/(A+β²B) 与主循环口径一致） | ✅ |
| κ 最优性 | **v7.0.5 修正**：`kappa_optimal` 原来只最小化采样噪声残余（aᵀΣb/bᵀΣb），与同模块 `sigma_res_analytic` 的总残余目标（含 κ²σ_eN²）不一致；现接受 `sigma_eN`，σ_eN>0 时返回 aᵀΣb/(bᵀΣb+σ_eN²)（联合最优，第五份复核 §11 公式）。默认参数保历史行为，钉子 `TestR18KappaOptimalWithObserverNoise` | ✅（本轮修复） |
| 观测通路摆幅 | f_max 判据读在**实际约束的节点**（观测通路）：134.45 MHz，覆盖披露频带（R12 修正：旧 2.5465 MHz 是错误节点读数）；但 0.1% 建立另需 f_BW ≈ 11.26 GHz（条件性设计压力估算）——**摆幅 ≠ 建立**（R13，`model_scope` §4.13） | ⚠️ 上界 |

### 3.9 `adc2.py` / `digital_core.py` / `reconstruction.py` — 后端与数字核

| 原理 | 代码实现 | 状态 |
|:---|:---|:---:|
| [00]：两级 SAR，后端解放大残差 | ADC2 静态量化；单极性窗口 [−0.15, +1.65] V（G0·Δ1 + 10% 余量）；Δ2 = 109.9 µV → 折输入 0.6·LSB20（量化可忽略，validate 判据 1） | ✅ |
| ADR 0006（本工程）：**观察器校正量不进 ADC2 量程**——旧实现 `quantize(vra − κ·v_N)` 把频率相关校正项塞进模拟量程，近 Nyquist 达 1.18 V（RA 摆幅 79%），结构性错误非余量不足 | `quantize_with_correction`：先量化、后扣除 | ✅ |
| 数字侧唯一可见口径 = 名义 DAC 值 | `digital_core` 数据流边界（§2 表）；校准回归量必须含 dither 名义项（漏 `d_once` → G_hat 崩到 6.68，v3 事故） | ✅ |

---

## 4. 专利逐件对齐总表

| 专利 | 披露的核心原理 | 代码位置 | 状态 |
|:---|:---|:---|:---:|
| [09] US 10,516,408 B2 | 量化器/残差 DAC 分离；SADC 误差自愈窗口；两条采样通路**响应**匹配；Fig.12 小数权重 | sadc.py、sampler.py、pipeline.py、stage9B/9C、stage19② | ✅ |
| [10] US 10,505,561 B2 | 采样态电荷注入式 dither（不占输入量程）；注入/改码/扣除三要素；整数 dither 不进粗码 | sampler.py、reconstruction.py（α）、stage9A | ✅ |
| [11] US 10,511,316 B2 | 等权单位置换 DEM；主/子各自轮转（不同步长）；主/子边界残差 = DEM 消不掉的可观测量；跨 slice 低位分配破简并 | mapper.py、dac_arch.py（boundary_error）、calib.py、stage12.3 | ⚠️ 机制对齐；排列空间 64 ≠ 512（R16），非三维机制 |
| [12] US 10,707,889 B1 | 由另一 ADC 结果驱动的跟踪状态更新 | `interleave_tracking.py`（M11）：`v_hold[i] = x[i−1]` 恒等式、慢信号 kickback 电荷比 reset <0.2、近 Nyquist 劣化区间如实复现。**带宽/噪声收益不按电荷比例换算**（相对建立约束与电荷无关；绝对残差约束下电荷进对数项，第八份复核订正） | ⚠️ 机制级实现 |
| [13] US 10,541,702 B1 | 辅助输入端口及其电荷核算（输入驱动收益） | `aux_input.py`（M12）：R_f 上限放大 (C_f+C_pg)/C_f 倍、最低带宽下界同比例降、噪声 rms ∝ √BW（√ 比例）；辅助通路建立可行性已检查（τ_aux 超窗即拒绝）；gate_boost 零调制 = **理想自举对照**（仅关 V_GS 项） | ⚠️ 机制级实现 |
| [14] US 10,826,519 B1 | 低功耗参考缓冲方案 | `ref_track.py`（M13）：独立行为模型——整定数随 [假设] 增益 g 而定（0.9→6/0.6→12/0.3→25），外部补电荷衰减、粗/细位试参考精度结构（容忍窗 = 主配置契约 4.6875 mV，非 Δ1/2）。**RA/ADC2 联合建立仍未验证** | ⚠️ 机制级实现 |

> [12]/[13]/[14] 接入的**前置条件**（发版纪律）：会移动已发布数值，
> 必须附新旧差异表并过全量门禁；钉子 R1/R2（物理池上主路径）先行。

## 5. 论文 [00] 关键披露值 → 代码锚点

| 披露值（[00]/[00_1]） | 代码锚点 | 等级 |
|:---|:---|:---:|
| NSD 8.8 nV/√Hz、−167.6 dBFS/Hz、DR 94.6 dB（正文 94.2，取 94.6 因与 NSD 自洽） | config.py 量程校核（Vfs_rms = 2.111 Vrms → ±3.0 V 差分满幅） | DISCLOSED |
| G0 = 32（图标注） | config.g0 | DISCLOSED |
| "9b quantization in the first stage" + dither range "enhanced by 2b" | b1 = 7 读法（ADR 0003） | ASSUMED（读法） |
| 20.5 pF 采样电容 / 512 单位 | step = 11.719 mV、单位 40.04 fF | DISCLOSED-推断 |
| 共享 RA 40% 功耗；auto-zero −1.6 dB、动态带宽 +1.3 dB | ra.py 折叠因子；stage22 结构校验 | DISCLOSED（系数）/ 未建电路 |
| 18-slice 池（8+8+2） | scheduler.py | DISCLOSED |
| INL 2.2 LSB（论文实测） | dynamics 三项建模后的缺口收敛对象 | 比对目标 |

## 6. 能力边界（引用，不重复论证）

逐条边界以 [`model_scope.md`](model_scope.md) 为准（§2 可支持 / §3
需条件 / §4 不可支持）。与本文直接相关的四条硬边界：

1. **DEM**：64 联合排列 ≠ [11] 三维机制（§4.11）——DEM 数字不得引用为
   [11] 的定量收益。
2. **[12]/[13]**：机制级模型已实现（M11–M12），但 pipeline 主链路仍
   零命中（§4.12）——主链路口径的输入驱动/交织跟踪 sizing 结论
   无从谈起。
3. **参考路径**：A06 的机制层已由 `ref_track.py`（M13）回答；主链路
   集成（coarse/fine MUX 事件进 pipeline）仍未做。
4. **KTC**：摆幅判据 ≠ 建立判据（§4.13）；κ_opt 已修正为含观测噪声的
   联合最优，但电路实现（提取带宽/观测量化/时序）仍开放。

## 7. 背景文献 [01]–[08] 的角色

| 文献 | 在本工程中的用途 |
|:---|:---|
| [01] Hurrell 18b ISSCC 2010 | 高精度 SAR 背景架构对照（非目标拓扑） |
| [02] ElShater 双死区 ring amp ISSCC 2019 | 两步 SAR + RA 结构的背景对照 |
| [03] Li 预测式电平移位输入缓冲 ISSCC 2023 | 输入驱动方案背景（未实现其机制） |
| [04] Bannon 18b VLSI 2014 | DR/INL 权衡背景 |
| [05] LTC2387-18 / [07] ADC3583 数据手册 | 信号链友好型精密 SAR 的商用对照点 |
| [06] Steensgaard 24b ISSCC 2022 | 噪声-效率极限背景 |
| [08] Shen 16b JSSC 2018 | 中速高精度 SAR 背景对照 |

均**不构成**目标芯片的建模依据；出现在 `NOTICE` 仅作溯源。

## 8. 引用合规声明

- 本文引用的所有第三方文献仅以条目形式出现；原文（PDF/讲稿/专利全文）
  **不随本仓库分发**（`.gitignore` 结构性兜底 `*.pdf`/`*.ppt(x)`/`*.doc(x)` 等）。
- "专利 X 披露了 Y"均为机制级转述；**实施例 ≠ 本仓库实现**
  （`NOTICE` 原则）。数值上的对齐状态以本文各表"状态"列为准。
- KTC 分支为原创研究扩展，不得归属 Analog Devices。

## 更新记录

| 日期 | 版本 | 变更 |
|:---|:---|:---|
| 2026-09-11 | v7.0.7 | 第六份复核订正：[12] 编号 US 10,707,889 B1（联网核实）；§3.4 18-slice 拆三行、31/42 dB 加"非因果实现"限定；§3.7 RA 改"行为级成立"、AZ 归"效果预算模型"；§3.1 余量数值加配置条件 |
| 2026-09-11 | v7.0.5 | 初版：逐模块/逐专利对齐表、R16/R17/R18 与 A06 边界、引用合规声明 |
