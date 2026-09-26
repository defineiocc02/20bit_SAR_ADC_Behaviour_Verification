# 20 位 SAR ADC 行为验证模型

> 一个**可被审计**的高精度 SAR ADC 行为模型：每个参数都带来源等级，每个结论都能追到
> 文献或假设，每条物理机制都对应到具体代码与回归测试。

[![CI](https://github.com/defineiocc02/20bit_SAR_ADC_Behaviour_Verification/actions/workflows/ci.yml/badge.svg)](https://github.com/defineiocc02/20bit_SAR_ADC_Behaviour_Verification/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-BSD--3--Clause-3C6EA5.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10--3.13-5B9E8F.svg)](pyproject.toml)
[![Release](https://img.shields.io/badge/release-v8.2.2-D98A3D.svg)](CHANGELOG.md)

[English](README_EN.md) · [建模与验证指南](docs/behavioral-closure.md) · [当前适用范围](docs/model_scope.md) · [实施与证据账本](docs/IMPLEMENTATION_CHECKPOINT.md) · [逐专利/论文对应](docs/engineering_vs_patents_papers.md) · [关键技术原理](docs/key_technologies.md)

本工程基于 ISSCC 2024 Session 9.8 及相关已公开专利/论文，用 Python 构建一个**两级残差
SAR ADC 的物理行为模型**，用于检查电荷、时序、噪声、校准与数字重构是否相互自洽，
并给出继续做电路级仿真的工程依据。公开资料没有披露完整电路，因此具体电容分配、
部分相位时间、DEM 交换方式与 ADC2 范围都保留为**显式假设**——而不是悄悄填一个数。

---

## 目录

| # | 章节 | 你会在这一节看到什么 |
|:--|:---|:---|
| [0](#0-这是什么) | 这是什么 | 定位、关键数字、三个"与常见行为模型不同"的地方 |
| [1](#1-工程功能与边界) | 工程功能与边界 | 做什么 / 不做什么（硬边界逐条列出） |
| [2](#2-整体架构) | 整体架构 | 信号链、逐样本相位流水、数字可见性边界 |
| [3](#3-主要模块) | 主要模块 | 48 个模块的职责分组 + 关键技术支柱 |
| [4](#4-与专利论文的对应关系) | **与专利/论文的对应关系** | 逐机制 × 逐来源矩阵、逐专利对齐表、披露值锚点 |
| [5](#5-参数来源分级) | 参数来源分级 | 93 项参数的等级构成（运行时账本） |
| [6](#6-验证方法论) | 验证方法论 | 双实现互证、独立电荷真值、门禁与钉子 |
| [7](#7-结果总览) | 结果总览 | v8.x 关键指标与发布图集 |
| [8](#8-安装与最小验证) | 安装与最小验证 | 三条命令跑通第一条信号 |
| [9](#9-标准验证命令) | 标准验证命令 | 门禁、全量跑批、报告生成 |
| [10](#10-仿真推进顺序) | 仿真推进顺序 | 从静态电荷到电路级转交的六步 |
| [11](#11-来源与边界) | 来源与边界 | 当前口径与不可支持的主张 |
| [12](#12-工程结构) | 工程结构 | 目录职责与贡献要求 |
| [13](#13-引用方式) | 引用方式 | BibTeX 与 `CITATION.cff` |
| [14](#14-参考文献) | 参考文献 | 被建模架构与机制专利索引 |
| [15](#15-开源声明与许可) | 开源声明与许可 | 独立性、无专利许可、原创贡献边界 |

---

## 0. 这是什么

一个**行为级**模型（不是器件级、不是版图级），把一颗 20 位、40 MS/s 精度型 SAR ADC 的
物理链路——采样网络、量化器、残差 DAC、共享残差放大器、后端 ADC 与数字重构——在
时域逐相位地算出来，并对每一个数字给出**它从哪来**。

| 维度 | 事实 |
|:---|:---|
| 代码规模 | **48 个模块 / 18,913 行** Python + 配套定点 RTL、综合流与仿真向量 |
| 参数溯源 | **93 项参数全部带等级**：`[披露]` 9 · `[派生]` 8 · `[拟合]` 3 · `[假设]` 65 · `[原创扩展]` 8 |
| 机制覆盖 | **17 项机制**（M01–M16 + X01）逐条映射到论文/专利与代码落点 |
| 验收强度 | **52 条硬性判据**全通过；28 条无条件必需记录由 `acceptance.py` 逐条强制 |
| 回归规模 | **714 项测试**通过；CI 覆盖 Python 3.10–3.13 + 跑批确定性 + 构建产物 |
| 数值一致性 | 同一环境重复跑批 `results.json` **逐字节一致**；跨版本有逐叶子字节账 |

### 与常见行为模型的三个不同

1. **参数来源是运行时值，不是注释。** `provenance.PARAM_GRADES` 把每个参数的等级
   变成可断言的代码；新增 `Config` 字段没写等级，测试直接失败。
   所以"这个数从哪来"是一个可以被程序回答的问题。
2. **两套独立求解器互证。** 逐相位状态机 `pipeline.py` 与向量化 `sim_split.py`
   在非理想因素全关时**逐位等价**；电荷真值另有 `charge_ref.py` 独立推导，
   不复用主链路的闭式解。中间节点算错不会被"端到端结果看起来对"掩盖。
3. **数字侧被关在名义口径的笼子里。** 校准/DEM/dither 的收益之所以可信，
   是因为数字算法**读不到**物理真值 `chip.C_true`；物理真值只能用于事后评分。
   这条边界由验收强制，不是靠自觉。

---

## 1. 工程功能与边界

### ✅ 做了什么

- **物理采样与切片池**：固定逐切片电容、桥接/子节点寄生、真实 DEM 掩码与**采样归属**；
  18 片池（8 转换 + 8 采集 + 2 备用），转换组必须持有它实际采集的电荷（跨周期因果）。
- **连续输入网络**：共享源阻抗 + 逐支路非线性 `Ron` + 物理源电荷；共享 `Rs` **不按
  切片数平摊**；可选持续滤波总线状态。
- **两级转换**：第一级 9 位判决（`sDAC` 与 `RDAC` 分离）、残差顶板保持、有符号参考电荷
  事件、有限增益/带宽/压摆/摆幅的共享 RA、ADC2 宽窄带跟踪相位、已释放切片的参考反作用。
- **噪声与校准**：可辨识的单位权重训练（含噪声）、冻结系数、同芯片独立记录验证、
  原始后端码与受检定点输出；正确的单边 PSD、频带积分、混叠/秩诊断。
- **数字域**：Q30/Q32 + 96 位受检整型核，20 位 offset-binary 输出；穷举输出 oracle、
  全部粗进位、噪声 holdout。
- **RTL 交付**：可综合校准核与定点结构（`rtl/`），Verilator 仿真与变异门禁（`synth/`、`sim/`）。

### ❌ 不做什么（硬边界）

| 边界 | 说明 |
|:---|:---|
| 无器件/版图/晶体管级测量 | 没有 PDK、版图或硅片数据；失配、面积、功耗、良率都是**敏感性假设** |
| 不是 SNDR/DR 预测器 | 噪声预算证明"预算可满足"，不证明"电路达到"；RA 噪声由所选 DR 锚点**反推**（`[拟合]`） |
| 参考负载在名义轨处线性化 | 峰值 droop 只报小误差域，不是完整非线性参考缓冲电路求解 |
| 相位时间与逐位试探次序为假设 | 无晶体管级时序收敛与亚稳态概率预测 |
| 20 位输出 ≠ 20 ENOB | 有真实受检整数重构，但不等价于完整芯片码密度 INL/DNL 证明 |
| KTC 支路是原创扩展 | 不是 `[00]` 或专利 `[09]`–`[14]` 披露的特性，默认关闭，**不得归属 ADI** |
| 未闭合项用 `xfail(strict=True)` 跟踪 | 修好变 XPASS 即失败，不留过期的"已完成"印象 |

> 逐条能力边界以 [docs/model_scope.md](docs/model_scope.md) 为准（§ 可支持 / 需条件 / 不可支持）。

---

## 2. 整体架构

![两级残差 SAR 信号链与逐样本相位流水](docs/readme/fig/fig1_architecture.png)

*图 1：信号链（上）与逐样本相位流水（下）。本图为**按公开披露的文字描述重绘的原创示意图**，
非论文/专利原图复制。*

### 2.1 信号链：一个采样周期里发生了什么

连续输入与共享源阻抗 → 实际采集切片的保持电荷 → SADC 判决 → RDAC/DEM 开关指令 →
有符号参考负载 → 有限 RA 与 ADC2 采样 → 原始 ADC2 整数码 → 冻结权重与定点重构。

| 环节 | 代码落点 | 这一段在算什么 |
|:---|:---|:---|
| 采样网络 | `sampler.py` · `sampling_charge.py` | 双通路（SADC/RDAC）分别采保；采样态 dither 注入不占输入量程 |
| 第一级判决 | `sadc.py` · `mapper.py` | 量化器与残差 DAC **分离**；粗码误差落在可自愈窗口内 |
| 残差 DAC | `rdac.py` · `dac_arch.py` · `dem.py` | 顶板保持残差电荷；等权单位位置置换；桥接 `C_C` 边界残差 |
| 残差放大 | `ra.py` · `conversion.py` | 增益 `G[n] = C_active[n] / C_F` 逐样本由**实际用到的电容**决定 |
| 后端量化 | `adc2.py` · `reference_charge.py` | 两级窗口 + 余量；观察器校正后置，不占模拟量程 |
| 数字重构 | `digital_core.py` · `fixed_point.py` · `reconstruction.py` | `x̂ = (v_D0 + fine/Ĝ − d_corr)/α`，α 与 dither 掩码同源推导 |

### 2.2 逐样本相位流水

一个转换周期被写成显式相位机，每相位都有对应的物理约束与验收：

| 相位 | 物理内容 | 约束/验收要点 |
|:---|:---|:---|
| ① 采集 | 共享源阻抗下的连续输入跟踪、真实切片状态 | KCL/AC/电荷守恒与步长收敛检查 |
| ② 预跟踪 | 只用**已经可用**的量化判决做预充电 | 预充电必须影响真实切片与 SADC 电容状态 |
| ③ 粗转换 | 9 位逐次逼近（sDAC） | 粗进位全覆盖；RDAC/ADC2 零溢出 |
| ④ 残差建立 | 顶板保持、有符号参考电荷、参考 coarse/fine 恢复 | 参考扰动必须在切片复用前回写 |
| ⑤ 放大 | 有限带宽 / 压摆 / 摆幅的 RA | 卷积、重复极点、独立 ODE 三项互证 |
| ⑥ 细转换 | ADC2 宽窄带跟踪 + 实际孔径 | 孔径噪声预算独立记账 |
| ⑦ 释放 | 已释放切片的参考反作用 | 跨周期因果关系（`conv[n] = acq[n-1]`）可审计 |

### 2.3 数据可见性边界（本工程的方法论核心）

```
名义口径  v_D0    = DAC_nominal(b)         ← 数字算法唯一允许读取的 DAC 口径
物理真值  v_Dtrue = DAC_physical(b, chip)  ← 只用于模拟电路与事后评分
```

两条铁律由验收强制：① `DAC_nominal(M(c, state)) = V_target(c)` 对**任意** DEM state
成立（等权置换的结构性质，不是调参拟合）；② `dac_error` 出现在任何**算法路径**即为越界。
违反一次，"校准/DEM/dither 收益"的解释权就没了。

### 2.4 数字侧与可综合 RTL

行为模型的数字域与 RTL 交付物是同一套口径的两种实现：

| 层次 | 内容 | 位置 |
|:---|:---|:---|
| 定点算法模型 | Q30/Q32 + 96 位受检累加、20 位 offset-binary 输出、显式寄存器宽度与半开区间舍入 | `fixed_point.py` `digital_core.py` |
| 可综合 RTL | 双 SAR / 共享 3-bit Flash / 18-slice 调度；可综合校准核 | `rtl/` |
| 综合与仿真 | DC 综合流、Verilator 仿真、变异测试门禁 | `synth/` `sim/` |
| 接口契约 | RTL 算术契约、P1/P2 接口说明、综合复核记录 | `docs/rtl/` |

> **RTL 结构与校准更新（2026-09-20）**：默认顶层为双 SAR / 共享 3-bit Flash / 18-slice 调度；
> 使用旧粗细码向量时请显式设置 `P_STRUCTURAL=0`。详见
> [结构与数字校正说明](docs/rtl/STRUCTURAL_CALIBRATION_20260920.md) 与 ADR 0018。

---

## 3. 主要模块

![48 个模块按职责分 9 组](docs/readme/fig/fig2_modules.png)

*图 2：模块地图。分组与文件系统做**集合相等断言**——新增模块漏登记会让绘图脚本直接失败，
而不是悄悄过期。*

| 组 | 模块 | 职责 |
|:---|:---|:---|
| 配置·溯源·入口 | `config` `provenance` `inventory_gate` `serialization` `_arrays` `cli` | 合法性、派生量、**参数来源分级**、标准 JSON 产物 |
| 采样与模拟前端 | `sampler` `sampling_charge` `input_network` `aux_input` `sadc` | 双通路采保、共享源阻抗、辅助输入电荷、粗判决 |
| DAC·DEM·切片池 | `rdac` `dac_arch` `dem` `mapper` `slice_pool` `scheduler` `pretracking` | 分段 DAC 严格解、等权置换、物理池与因果调度 |
| 参考·RA·动态·后端 | `ra` `adc2` `conversion` `dynamics` `reference_charge` `ref_track` `interleave_tracking` `timing` | 联合转换动态与三类动态误差 |
| 主链路求解器 | `pipeline` `pipeline_engine` `sim` `sim_split` `chip` | 逐相位状态机与向量化实现共用物理 |
| 数字·校准·重构 | `digital_core` `fixed_point` `calib` `weight_calibration` `reconstruction` | 可辨识权重、冻结系数、受检定点输出 |
| 噪声模型 | `ktc` `noise_phase` `low_frequency_noise` | 逐相位噪声传递、原创 KTC 相消、慢状态 1/f |
| 验证·实验·报告 | `acceptance` `experiments` `closure_experiments` `benchmarks` `metrics` `reporting` | 必需判据门禁、全量实验、频谱口径与报告 |
| 独立参照实现 | `charge_ref` | **不复用**闭式解的独立电荷真值求解器 |

### 关键技术支柱

| # | 技术 | 一句话原理 | 代码 |
|:--|:---|:---|:---|
| T1 | 电荷一致闭包 | 同一组 `C_active` 同时决定 kT/C 噪声、RA 增益、KTC 的 β | `rdac` `ra` `sim` |
| T2 | 分段 DAC 严格解 | 两浮动节点联立解 + 输入等效统一口径（0.9972 的口径差曾造出 143 mV 假残差） | `dac_arch` `charge_ref` |
| T3 | 名义/物理分离 | 数字侧只许看名义口径 | `rdac` `digital_core` |
| T4 | 采样态 dither | 注入 / 改码 / 扣除三要素配对，`α = (N − 2D)/N` | `sampler` `reconstruction` |
| T5 | DEM 有效域 | 置换能消码相关失配；增益、`C_C`、共模串扰、主/子边界残差**消不掉** | `mapper` `dac_arch` |
| T6 | Rank-aware 可观测性 | `rank(U) = 64/512` 是**结构**问题，是校准可行性的判据 | `calib` |
| T7 | KTC 相消（原创） | `σ²_res = (a−κb)ᵀΣ(a−κb) + κ²σ_eN²`，κ 取联合最优 | `ktc` `noise_phase` |
| T8 | 动态误差三件套 | 码相关的输入建立 / 参考建立 / 数字串扰 ⇒ 确定性 INL | `dynamics` |
| T9 | 物理池因果 | `conv[n] = acq[n−1]`；洗牌必须作用在**物理电容**上 | `slice_pool` `scheduler` |
| T10 | 可测试的方法论 | 双实现等价、独立真值、运行时分级、门禁、`xfail(strict)` | 全库 |

> 每项技术的完整原理、公式、反例与"钉子测试"见 [docs/key_technologies.md](docs/key_technologies.md)。

---

## 4. 与专利/论文的对应关系

> **这一段是本工程与"凭感觉写的模型"的分界线。** 下表把每一项机制逐一映射回它的文献源头，
> 并标明三件事：文献披露了什么、代码实现了什么、两者对齐到什么程度。
>
> 引用规则：只引用**条目**（作者/会议/DOI/专利号），原文（PDF/讲稿/专利全文）
> **不随本仓库分发**；本节的示意图均为**按披露文字重绘的原创图**。见 [`NOTICE`](NOTICE)。

![契合点映射：文献披露的机制到本工程的代码落点](docs/readme/fig/fig7_mapping.png)

*图 3：契合点总览——左栏是文献来源，右栏是它喂养的机制与本工程的代码落点；
实线 = 已入主链路（端到端生效），虚线 = 仅机制级实现或机制对齐但排列空间/口径不同。
左上角数字是该来源支撑的机制数量。*

![机制 × 文献来源 对应矩阵](docs/readme/fig/fig3_source_alignment.png)

*图 4：17 项机制 × 9 个来源的对应矩阵，右侧标注实现程度。数据来自机器可读账本
[`mechanism_inventory.json`](mechanism_inventory.json)，由 `inventory_gate` 校验一致性。*

### 4.1 来源索引

| 编号 | 文献 | 在本工程中的角色 |
|:---|:---|:---|
| `[00]` | R. Bodnar 等，*A 9.3 nV/√Hz 20 b 40 MS/s 94.2 dB DR Signal-Chain Friendly Precision SAR Converter*，**ISSCC 2024**，Session 9.8，pp. 182–183。DOI `10.1109/ISSCC49657.2024.10454329` | **主架构目标**：拓扑、slice 池、共享 RA、auto-zero、dither 范围增强 |
| `[00_1]` | 同一工作的讲稿（按页引用图与标注） | 参数来源：NSD / DR / G0 / 18-slice 结构 / 噪声代价 dB 值 |
| `[09]` | US 10,516,408 B2 — Analog to digital converter stage | 量化器与残差 DAC 分离；粗码误差自愈窗口；两条采样通路**响应**匹配 |
| `[10]` | US 10,505,561 B2 — Method of applying a dither, and ADC | dither 的两种物理实现；注入/改码/扣除三要素配对 |
| `[11]` | US 10,511,316 B2 — Linearizing transfer characteristic by DEM | 等权单位置换 DEM；主/子各自轮转；边界残差难点 |
| `[12]` | US 10,707,889 B1 — Interleaving method for analog to digital converters | 交织 ADC 由另一路结果驱动的**跟踪状态更新** |
| `[13]` | US 10,541,702 B1 — Auxiliary input for ADC input charge | 辅助输入端口与电荷核算 |
| `[14]` | US 10,826,519 B1 — Low power reference for an ADC | 低功耗参考缓冲与整定回路 |
| `[01]`–`[08]` | Hurrell ISSCC 2010、ElShater ISSCC 2019、Li ISSCC 2023、Bannon VLSI 2014、LTC2387-18、Steensgaard ISSCC 2022、TI ADC3583、Shen JSSC 2018 | 背景/替代架构对照，**不构成**目标芯片的建模依据 |

### 4.2 逐机制对应（17 项）

| 机制 | 文献 | 代码落点 | 实现程度 | 已登记的缺口 |
|:---|:---|:---|:---|:---|
| M01 量化器/残差 DAC 分离 | `[00]` `[00_1]` `[09]` | `sadc` `pipeline` `config` | 已入主链路 | `b1` 读法非唯一（`paper_literal` / `legacy_v61` 保留） |
| M02 SADC 粗码误差自愈窗口 | `[09]` | `pipeline` `experiments` | 已入主链路 | — |
| M03 双采样通路响应匹配 | `[09]` | `sampler` `dynamics` | 已入主链路 | `sampling_tau_mismatch` 数值为 `[假设]` |
| M04 dither 采样态注入 + 三要素 | `[10]` | `sampler` `reconstruction` | 已入主链路 | 静态线性化收益在本配置为**负**结果（条件性） |
| M05 DEM 等权置换与名义守恒 | `[11]` | `mapper` `dac_arch` | 已入主链路 | ⚠️ 联合排列空间 **64 ≠ 512**（R16） |
| M06 主/子边界残差与桥接锯齿 | `[11]` | `dac_arch` | 已入主链路 | 三维映射按本工程候选实现重建 |
| M07 交织简并 → 校准可观测性 | `[11]` | `calib` | 已入主链路 | 校准算法为本工程实现，非原芯片算法 |
| M08 18-slice 池与交织静态失配 | `[00]` `[00_1]` | `scheduler` `slice_pool` `pipeline` | 已入主链路 | 31/42 dB 抑制数字不得用作匹配预算 |
| M09 动态误差三件套 | `[00_1]` | `dynamics` | 已入主链路 | `dyn_*` 全部 `[假设]`：用形状与趋势，不用值 |
| M10 失配预算 → 校准必要性 | `[00_1]` | `calib` `experiments` | 已入主链路 | 300 ppm 为 `[假设]` 目标；良率置信区间未收敛 |
| M11 交织跟踪状态更新 | `[12]` | `interleave_tracking` `pretracking` | 已入主链路 | 介电吸收记忆误差模型未建 |
| M12 辅助输入电荷核算 | `[13]` | `aux_input` `input_network` | 已入主链路 | `C_pg` 无披露值 → 比例为 `[假设]` |
| M13 低功耗参考整定回路 | `[14]` | `ref_track` | **机制级** | 整定回路事件未进主链路；误差检测可实现性未建模 |
| M14 共享 RA 增益 + AZ 预算 | `[00_1]` | `ra` `adc2` | 已入主链路 | AZ 相位级噪声传递未建（只有预算倍率） |
| M15 独立电荷参考求解器 | `[00]` | `charge_ref` `rdac` | 已入主链路 | SPICE 测试台未执行（无 PDK/授权） |
| M16 数字域数据流边界 | `[00]` | `digital_core` `adc2` | 已入主链路 | — |
| X01 KTC 观测消噪（**原创**） | 无文献源头 | `ktc` `noise_phase` | 已入主链路 | 理想读出上界；摆幅 ≠ 建立 |

### 4.3 逐专利对齐总表

| 专利 | 披露的核心机制 | 对应代码 | 对齐状态 |
|:---|:---|:---|:---|
| `[09]` US 10,516,408 B2 | 量化器与 RDAC 分离；粗码误差自愈窗口；**采样响应**（非仅电容值）匹配；小数权重异码合成 | `sadc.py` `sampler.py` `pipeline.py` | ✅ 已入主链路 |
| `[10]` US 10,505,561 B2 | 采样态电荷注入式 dither（不占输入量程）；注入/改码/扣除三要素；整数 dither 不进粗码 | `sampler.py` `reconstruction.py` | ✅ 已入主链路 |
| `[11]` US 10,511,316 B2 | 等权单位位置置换；主/子阵列各自轮转；**边界残差 DEM 消不掉**；跨 slice 低位分配破简并 | `mapper.py` `dac_arch.py` `calib.py` | ⚠️ 机制对齐（排列空间 64 ≠ 三维机制 512） |
| `[12]` US 10,707,889 B1 | 由另一路 ADC 结果驱动的跟踪状态更新；预充电影响真实电容状态 | `interleave_tracking.py` `pretracking.py` | ⚠️ 机制已入主链路；记忆误差模型未建 |
| `[13]` US 10,541,702 B1 | 辅助输入端口、驱动/复位电荷分别记账、带宽下界与噪声 √BW 比例 | `aux_input.py` `input_network.py` | ⚠️ 机制已入主链路；`C_pg` 比例为 `[假设]` |
| `[14]` US 10,826,519 B1 | 低功耗参考整定回路、外部补电荷衰减、粗/细位试参考精度 | `ref_track.py` | ⚠️ **仅机制级**；整定回路事件未进主链路 |

> **对齐状态的读法**：`✅` = 文献机制在整条信号链上生效并有端到端实验；`⚠️` = 机制被正确
> 复现，但排列空间/参数口径/集成深度与原文不同，**其数字不得引用为原文机制的定量收益**。
> 更详细的历史映射（含 v7 阶段的逐条论证）见
> [docs/engineering_vs_patents_papers.md](docs/engineering_vs_patents_papers.md)。

### 4.4 披露值 → 代码锚点

![披露值到代码锚点的双栏对照](docs/readme/fig/fig4_disclosed_anchors.png)

*图 5：论文/讲稿的每个头条数字都能落到一个带等级的代码锚点上。*

| 披露值（`[00]` / `[00_1]`） | 代码锚点 | 等级 |
|:---|:---|:---|
| NSD 8.8 nV/√Hz、DR 94.6 dB（正文 94.2） | `config.py` 量程校核：Vfs,rms = 2.111 Vrms → ±3.0 V 差分满幅 | `[披露]` |
| G0 = 32（图标注） | `config.g0`；残差裕量 = 0.15 V / G0 = **4.6875 mV** | `[披露]` |
| 20.5 pF 采样电容 / 512 个单位 | 步长 = 2·v_fs/512 → 11.719 mV；单位 40.04 fF | `[披露-推断]` |
| 18-slice 池（8 + 8 + 2） | `scheduler.reserve_dual` + `slice_pool` 因果不变量断言 | `[披露]` |
| 共享 RA 约占 ADC 功耗 40%；auto-zero 噪声代价 −1.6 dB、动态带宽 +1.3 dB | `ra.py` 折叠因子；stage22 只做符号与量级的**结构**校验 | `[披露]`（系数） |
| 第一级"9b quantization" + dither range "enhanced by 2b" | `b1 = 7` 读法（ADR 0003），两条备选读法保留 | `[假设]`（读法） |
| 论文实测 INL 2.2 LSB（≈ 2.307 LSB20） | `dynamics.py` 三项动态误差的**缺口收敛对象** | 比对目标 |

> ⚠️ 残差裕量 4.6875 mV 是**当前模型量程与增益配置下的推导值**（0.15 V ÷ G0=32），
> 不是专利披露的固定容差——数值随配置联动。

### 4.5 明确**未**实现的部分（诚实清单）

| 项 | 状态 | 说明 |
|:---|:---|:---|
| 采集窗口对照实验（短窗口 vs 交织延长窗口） | `NOT_RUN` | 现只有一阶标度律，无系统级对照场景 |
| 逐位切换大 DAC vs RDAC 直接设码对照 | `NOT_RUN` | 直接设码已隐含于顶板保持，但对照实验未建 |
| 介电吸收多时间常数记忆误差模型 | `NOT_RUN` | 跟踪因果性测试**不能**替代记忆误差模型 |
| AZ 相位级噪声传递（`C_AZ` 存储/释放、宽窄带切换） | `NOT_RUN` | 现只有预算倍率，非相位级传递 |
| SPICE/Spectre 小规模测试台 | `BLOCKED` | 本机无 PDK/SPICE 授权时交付可运行接口 + `NOT_RUN` 标记，**不伪造工具运行** |
| MC 良率置信区间与样本量收敛 | `NOT_RUN` | 低频 1/f 观察时长已覆盖；良率统计口径未收敛 |
| 参考缓冲**电路级**求解 | 未做 | 负载在名义轨处线性化，只报小误差域 droop |

---

## 5. 参数来源分级

![93 项参数的来源分级构成](docs/readme/fig/fig5_provenance_grades.png)

*图 6：`provenance.PARAM_GRADES` 的运行时构成（本图数据由脚本直接读取该账本，不硬编码）。*

分级不是注释，而是**运行时值**，由 `provenance.audit_provenance` 强制：

| 等级 | 含义 | 数量 |
|:---|:---|---:|
| `[披露]` DISCLOSED | 文献直接给出 | 9 |
| `[派生]` DERIVED | 由披露值 + 公式算出 | 8 |
| `[拟合]` FITTED | 为对齐公开指标而反推标定 | 3 |
| `[假设]` ASSUMED | 工程选取，非披露 | 65 |
| `[原创扩展]` RESEARCH_EXTENSION | 无文献源头（本仓库作者原创） | 8 |

**这就是这个模型可以被质疑着读的原因**：93 项里只有 9 项是文献直接给出的，65 项是工程假设。
规则是——`[假设]` 经公式计算后**不得**洗成纯披露结果；每个 headline 数字都能追到具体行。
参数级引用写在 `provenance.PARAM_GRADES` 里，一个参数一行。

---

## 6. 验证方法论

![四道防线：双实现等价、独立真值、运行时分级、门禁与钉子](docs/readme/fig/fig6_verification.png)

*图 7：让"结论可信"这件事本身可被测试。*

| 防线 | 做法 | 为什么有效 |
|:---|:---|:---|
| 双实现逐位等价 | `pipeline.py`（逐相位状态机）与 `sim_split.py`（向量化）在非理想全关时逐位一致 | 两套独立实现读同一接口读出同样语义，是"接口被一致解释"的最强校验 |
| 独立电荷真值 | `charge_ref.py` 逐相位节点方程另推一遍，**不复用**闭式解；不一致时先怀疑主循环 | 中间节点口径错误会被端到端正确性测试漏掉（v5 审计的核心教训） |
| 运行时分级 | 新增 `Config` 字段未登记等级 → 测试红 | 把"注释里的分级"变成不可绕过的断言 |
| 验收门禁 | 52 条硬性判据 + 28 条无条件必需记录；`xfail(strict=True)` 跟踪未闭合项 | 验收项不可静默消失；修好变 XPASS 即失败，逼人摘标记 |
| 字节账 | 参考产物 `results.json` 逐叶子对账，记录"哪些变了、为什么" | 数值变化必须被解释，不能用放宽门槛掩盖回归 |
| 对抗复核 | 钉死的隔离副本上由未参与实现者做证伪 + 变异检验 | 回退源码后对应测试是否**真的**失败，是测试有没有"牙"的判据 |

---

## 7. 结果总览

### v8.x 演进

v8.0.0 把物理 slice 池、交织预跟踪、辅助输入、参考/RA/ADC2 联立动态与定点数字核接入主链路；
8.1.0 新增数字侧定点 RTL（P0–P3）：可综合校准核、双 SAR/共享 3-bit Flash、18-slice 调度、
Verilator 仿真与变异测试门禁——行为级 `results.json` 数值与 v8.0.0 逐字节一致。

8.2.0 修正两处**统计独立性/口径**缺陷并加厚入口防护：MC 循环里失配抽签与噪声 rng 此前由
**同一整数种子**播种（实测两者取到**同一批随机数**、流完全重合；注意是流重合而非芯片退化），
现改为 `SeedSequence.spawn` 独立子流；单位串扰翻转口径统一为 A(k)/2。参考输出随之重算并逐项对账：
**926 个叶子中 878 个逐字节不变，48 个变化全部落在随机流相关分区**。

> **数值差异不可归因于修复**：对逐颗 SNDR 做自助法检验（20 000 次重抽样）后，5 个分区的
> **Δ 最差芯片与 Δ 标准差共 10 项全部落在零分布的 95% 区间内**（|z| ≤ 1.44）。即 n=16 / n=60 下，
> MC 与良率指标由抽样噪声主导。**不要**拿本模型的 MC 极值做良率论断。

8.2.1 针对 v8.2.0 的独立对抗性复核所揭示的**一整类**缺陷收口：域校验谓词只查符号或区间、
忘了查有限性，于是 `nan` / `±inf` 穿过守卫并在下游静默传播。共加固 32 行判定 / 13 个源与工具文件。

8.2.2 是**修 v8.2.1 的 CI 全红**的前向补丁。根因不在版本而在 **BLAS 后端**：`fit_unit_weights`
在 `svd()` 与权重守卫之间的算术是裸的，非有限因子让 `design` 的零元与 `theta` 的非有限元相乘
即 `0 * inf`（非法浮点运算）；x86-64 OpenBLAS 因此抛 `RuntimeWarning`，Apple Accelerate 不抛，
所以本地绿、CI 红。现补两处闸门：**SVD 因子闸门**（在任何算术与 rank 比较之前）与
**入口标度闸门**（`spec` 的物理标量，一次关掉 `design` 与 `fine_v` 两条 ingress）。

> **参考产物零变化**：三版指纹（v8.2.0 / v8.2.1 / v8.2.2）**逐字符相同**，926 个叶子全部
> 逐字节不变。CI 从 4 个 Python 版本全红转为 **9/9 job 全绿**。

字节账与显著性检验见 [CHANGELOG](CHANGELOG.md)；v8.2.0 的 7 张对比图与 `significance.json` 见
[docs/release_v8.2.0](docs/release_v8.2.0/)；v8.2.1 的加固普查见 [docs/release_v8.2.1](docs/release_v8.2.1/)；
v8.2.2 的 ingress 收口对照见 [docs/release_v8.2.2](docs/release_v8.2.2/)。

![v8.2.2 非有限 ingress 收口对照：同一探针在 v8.2.1 与修复后代码上实跑](docs/release_v8.2.2/fig/ingress_closure_v822.png)

![v8.2.2 参考产物字节账：三版指纹逐字符相同](docs/release_v8.2.2/fig/byte_account_v822.png)

![v8.1.0 → v8.2.0 关键指标对比](docs/release_v8.2.0/fig/headline_compare.png)

![自助法检验：全部 10 项 Δ 落在重抽样零分布 95% 区间内](docs/release_v8.2.0/fig/significance_null.png)

与 v7.0.10 聚合基线做 `results.json` 逐项对账：**626 个共有指标中 516 个完全一致**，
40 个为浮点级噪声（<1e-6 相对），**70 个实质变化**全部集中在物理主链路新覆盖的子系统。

![v7→v8 关键指标对比](tools/results/fig/v7_v8_compare.png)

核心指标（`paper_literal` 主配置，`results.json` 逐项可查）：

| 指标 | 数值 | 口径 |
|:---|---:|:---|
| ENOB | 20.58 bit | s1 无失配理想链路 |
| SNDR / SFDR | 125.6 / 155.5 dB | s1 无失配理想链路 |
| 输出噪声 rms | ≈1.0 µV | s1 |
| DEM 开/关 SNDR | 93.4 / 93.5 dB | s3 含失配 |
| MC 最差 SNDR / SFDR | 82.0 / 85.1 dB | `mc_pdk_off` 60 颗（PDK 失配，DEM 关） |
| 校准后误差贴地比 | 1.006 | `split_calib.noise_off` |

验证图集（由 `tools/run_all.py` 与 `tools/make_readme_compare.py` 生成，随 `results.json` 同步更新）：

| | |
|:---|:---|
| ![静态误差与 INL 贡献](tools/results/fig/static_curves.png) | ![DEM 谱](tools/results/fig/dem_spectrum.png) |
| *分段 DAC 静态误差、匹配拓扑对比与 INL 贡献分解* | *DEM 开/关 Spectrum：等权单位置换把失配杂散压回本底* |
| ![物理校准](tools/results/fig/physical_calibration.png) | ![KTC 折中](tools/results/fig/ktc.png) |
| *物理池校准：噪声保留训练、冻结权重、独立记录验证* | *KTC 观测消噪：缩电容与逐相位噪声传递的折中* |
| ![失配压力](tools/results/fig/mismatch_stress.png) | ![误差预算](tools/results/fig/budget.png) |
| *失配压力扫描：误差随失配幅值的标度* | *噪声/失配误差预算分解* |
| ![扫描](tools/results/fig/sweep.png) | ![信号链结构](tools/results/fig/v5_structure.png) |
| *参数扫描与可行域* | *分段子 DAC 信号链结构* |

---

## 8. 安装与最小验证

支持 Python 3.10–3.13；运行依赖为 NumPy、SciPy、Matplotlib。建议使用独立虚拟环境。

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
```

```python
import numpy as np
from adi_model import Config, sine_input
from adi_model.pipeline import run_pipeline
from adi_model.metrics import sine_fit_metrics

cfg = Config.paper_literal(dither_mode="off")
n = 16384
fin = cfg.fs * 307 / n
result = run_pipeline(cfg, sine_input(2.1, fin), n)
words = result.to_codes()
print(sine_fit_metrics(words.voltage, cfg.fs, fin))
print("analog overflow:", np.count_nonzero(words.analog_overflow))
print("output clipping:", np.count_nonzero(words.clipped_low | words.clipped_high))
print(result.effective_config)
```

显式选择论文或幻灯片噪声基准：

```python
from adi_model.benchmarks import PAPER_BENCHMARK, SLIDES_BENCHMARK
from adi_model.weight_calibration import run_with_split_calibration

cfg = PAPER_BENCHMARK.configuration(dither_mode="off", mismatch_sigma0=0.001)
validated = run_with_split_calibration(
    cfg, sine_input(2.2, cfg.fs * 307 / 16384, 0.5), 16384, n_cal=8192
)
print(validated.calibration_report)
registers = validated.state.weight_calibration.to_dict()
words = validated.to_codes()
```

默认训练是受控静态行为测量，保留采样/RA/驱动噪声，并使用独立随机流。
它没有实现片上参考源或校准时序/功耗；训练参考的系统误差需要额外预算。

---

## 9. 标准验证命令

```bash
ruff check .
ruff format --check .
mypy
pytest -q --cov
adi-run-all --results-dir ./output/verification
adi-make-report --results-dir ./output/verification
```

全量 sweep 包括历史机制基线、新物理参考/RA 检查、64 秒慢噪声状态、两颗完整芯片的不同训练
样本数与独立定点验证。门禁同时检查具体必需判据、数量下限和失败记录，失败时退出非零。
`results.json` 使用 UTF-8 标准 JSON；无法定义的数值记录为 null 并列出字段路径，
绝不写入 NaN/Infinity 数字。HTML 报告使用实际结果生成，区分不同架构/来源及其适用范围。

同一软件环境中重复 sweep 的 `results.json` 要逐字节一致；跨 NumPy/SciPy/BLAS 环境使用数值
容差验收。CI 运行 Python 3.10–3.13 测试、3.12 全量 sweep、独立双次确定性验证，以及 RTL 仿真
与构建产物（wheel 外部导入 + CLI）验证。

---

## 10. 仿真推进顺序

1. **静态电荷与范围**：关闭噪声/动态，检查全部粗进位和 RDAC/RA/ADC2 溢出；确认所选 7/9 位架构。
2. **独立物理实现**：固定 fabrication seed，验证实际 slice、电容、DEM 与 dither 掩码；重用芯片时保留物理参数，显式传运行配置。
3. **噪声和校准**：先完成秩/条件数检查，再比较训练规模与独立验证；保留驱动参考误差和残余失配。
4. **联合动态**：逐项引入共享 Rs、Ron、参考 coarse/fine、RA 带宽/压摆/摆幅、ADC2 宽窄带采样，再组合运行；增加时间步检查收敛。
5. **最终整数码**：测最终码流的 SNDR/SFDR 与溢出；静态均值误差、局部进位扫描和完整码密度 INL/DNL 分别报告。
6. **电路级转交**：用可接受的行为参数区间编写实际 Spectre 子模块规格，再以电路仿真替代假设。

---

## 11. 来源与边界

| 项目 | 当前口径 |
|---|---|
| 论文 `[00]` | 94.2 dB DR、9.3 nV/√Hz；独立保存 |
| 幻灯片 `[00_1]` | 94.6 dB DR、8.8 nV/√Hz、约 40 Hz 转角；独立保存 |
| RA 噪声 | 可由所选 DR 锚点反推；属于拟合，不是性能预测 |
| 参考负载 | 名义轨电压处线性化的实际有符号电荷；峰值 droop 给出适用域 |
| RA/ADC2 动态 | 已建有限信号响应；没有据此声称完整开关噪声传递 |
| 低频噪声 | 显式低截止的平稳慢状态；64 秒稀疏观测保持 40 MHz 物理时钟 |
| 20 位输出 | 有实际受检整数重构；不等于 20 ENOB 或完整芯片码密度证明 |
| KTC 观察器 | 研究扩展，默认关闭；未量化观察支路不进入当前定点接口 |
| PDK / 良率 / 功耗 | 没有器件与版图证据，不作硅级预测 |

详细公式、输入输出单位和测试依据见 [ADR 目录](docs/adr/)（0001–0018）与
[建模指南](docs/behavioral-closure.md)。历史审计/报告作为版本证据保留，
当前能力以[适用范围](docs/model_scope.md)和[实施账本](docs/IMPLEMENTATION_CHECKPOINT.md)为准。

**健壮性上已登记、但本版未处理的项**（明细与实测证据见 [CHANGELOG](CHANGELOG.md)）：

- 输入闸门保证**输入**有限，**不**保证中间量有限——有限但极端的输入仍可能在矩阵装配
  阶段溢出；
- `CalibrationSpec` 的整数字段不做类型/积分性校验；
- `charge_ref.py` 全文没有域守卫（属"守卫**缺失**"的另一类，需单独评审）；
- 定点核的反序列化路径不经 `validate()`。

---

## 12. 工程结构

| 位置 | 职责 |
|---|---|
| `src/adi_model/config.py` | 配置、合法性、派生量与来源分级 |
| `slice_pool.py`, `timing.py`, `pipeline_engine.py` | 物理实例、因果调度与主信号链 |
| `input_network.py`, `pretracking.py` | 共享输入网络和数字可用性 |
| `reference_charge.py`, `conversion.py` | 有符号电荷与联合转换动态 |
| `weight_calibration.py`, `fixed_point.py` | 可辨识训练、冻结系数与整数重构 |
| `metrics.py`, `low_frequency_noise.py` | 频谱口径与慢状态 |
| `closure_experiments.py`, `acceptance.py` | 独立验证协议与必需判据 |
| `serialization.py`, `reporting.py` | 标准数据产物与报告 |
| `rtl/`, `synth/`, `sim/` | 定点 RTL、DC 综合流、仿真向量与测试台 |
| `tools/`, `tests/`, `docs/adr/` | 全量流程、回归套件、可追溯设计决策 |
| `mechanism_inventory.json`, `sources_manifest.json` | 机器可读的机制/来源/状态总清单与哈希 |

贡献前请运行完整验证，说明数据口径、假设和误差来源。修改物理模型须增加能独立推翻实现的
验证；历史数值变动须解释，不用放宽门槛掩盖回归。

---

## 13. 引用方式

如果这个模型对你的工作有帮助，请引用它——引用里记录了版本号，读者才能复现
你的数字。

```bibtex
@software{zhao_2026_sar_adc_behaviour_model,
  author    = {Zhao, Reed},
  title     = {20-bit SAR ADC Behavioural Verification Model},
  version   = {8.2.2},
  year      = {2026},
  publisher = {GitHub},
  url       = {https://github.com/defineiocc02/20bit_SAR_ADC_Behaviour_Verification},
  license   = {BSD-3-Clause},
  note      = { 独立的学术行为模型；与 Analog Devices, Inc. 无从属、
                无背书、无授权关系。 }
}
```

供 GitHub 的 *Cite this repository* 按钮使用的机器可读元数据在
[`CITATION.cff`](CITATION.cff)。

**请同时引用被建模的架构本身**（见[§14](#14-参考文献) 的 `[00]`）——本仓库是
对那项工作的研究，不替代它。

---

## 14. 参考文献

| 编号 | 文献 |
|:---|:---|
| `[00]` | R. Bodnar 等，"A 9.3 nV/√Hz 20 b 40 MS/s 94.2 dB DR Signal-Chain Friendly Precision SAR Converter"，**ISSCC 2024**，Session 9.8，pp. 182–183。DOI: `10.1109/ISSCC49657.2024.10454329` |
| `[00_1]` | 同一工作的幻灯片（按页引用图与标注） |
| `[09]`–`[14]` | 覆盖量化器/RDAC 分段、采样侧 dither、DEM 排序、交织、辅助输入电荷与低功耗参考的专利族 |

参数级引用写在 `provenance.PARAM_GRADES` 里，一个参数一行。完整的第三方文献
清单（含**不属于**目标芯片的背景架构）见 [`NOTICE`](NOTICE)。

---

## 15. 开源声明与许可

**代码许可：BSD-3-Clause，见 [`LICENSE`](LICENSE)。**
你可以自由使用、修改、再分发本软件（包括商业用途），只需遵守 BSD 的三项
条件：保留版权声明、在二进制分发中复现该声明、不得用作者名义为衍生品背书。

### 15.1 独立性与无从属关系

本仓库是一个**独立的学术行为模型**，与 **Analog Devices, Inc.** **无从属、
无背书、无赞助、无授权**关系。作者与 Analog Devices 无任何隶属关系。

"Analog Devices"、"ADI"、"LTC" 是 Analog Devices, Inc. 的商标。它们在本仓库
中的出现仅为**名义性使用**（nominal use），目的只是标识被研究的公开文献，
**不表示**该公司对本仓库的任何认可。

### 15.2 用了什么、没用什么

| | |
|:---|:---|
| ✅ 使用了 | **仅限已公开**文献：ISSCC 2024 论文与幻灯片、已公开专利、公开数据手册。全部在 [`NOTICE`](NOTICE) 中列明，且**均不在本仓库内再分发**。 |
| ❌ 未使用 | 无任何硅片、网表、版图、PDK、设计数据库、内部文档，也无任何形式的非公开信息。 |

每一个量要么是公开来源转录（`[披露]`），要么是对公开值做代数推导
（`[派生]`），要么是**为复现已发表图表而标定**（`[拟合]`），要么是供敏感性
研究而假设（`[假设]`）。反推得到的参数**不会**被当作对任何产品的独立测量
结果呈现。

### 15.3 无专利许可；无担保

本仓库中的任何内容都**不授予**任何第三方专利或其它知识产权项下的任何许可
（明示或默示）。`NOTICE` 中列出的专利仅作为**机制参考**引用；其中描述的
实施方案**不是**本仓库所发布的实现。

软件按 **"原样"（AS IS）** 提供，不附任何形式的担保；作者不对因使用本软件
而产生的任何索赔或损害负责。它是**研究模型**，**不是**设计签核工具：
**不得**用于量产、安全关键或良率决策。

### 15.4 原创贡献

**KTC 噪声消除支路**（`adi_model/ktc.py`）是本仓库作者的**原创研究扩展**。
它在代码中被分级为 `RESEARCH_EXTENSION`、默认关闭，并且**不是** `[00]` 或
专利 `[09]`–`[14]` 所披露的特性。**请勿将其归属于 Analog Devices。**

同理，README 与 `docs/readme/` 中的示意图（图 1–图 7）由本仓库按公开披露的
**文字**描述重绘，**不复制**任何第三方论文/专利图像。

### 15.5 对本声明提出异议

如果你认为自己是某项内容的权利人，且认为本仓库存在归属错误或超出许可范围，
请提交
[机密安全公告](https://github.com/defineiocc02/20bit_SAR_ADC_Behaviour_Verification/security/advisories)
或参见 [`SECURITY.md`](SECURITY.md)。归属与许可类更正按**最高优先级**处理。
