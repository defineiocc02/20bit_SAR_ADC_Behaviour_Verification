# 20 位 / 40 MS/s 两步式 SAR ADC：按专利原图对照的行为级复现

[English](README_EN.md) · [当前状态](docs/STATUS.md) · [机制专题](docs/mechanisms/README.md) · [**验收对照报告**](docs/ACCEPTANCE_20261113.md) · [论文与讲稿](references/README.md) · [矩阵与图](docs/reviews/20261113_p3p4/figures/RESULTS_20261113.md) · [Python–RTL 一致性验证](verification/README.md)

[English](README_EN.md) · [当前状态](docs/STATUS.md) · [机制专题](docs/mechanisms/README.md) · [**验收对照报告**](docs/ACCEPTANCE_20261113.md) · [矩阵与图](docs/reviews/20261113_p3p4/figures/RESULTS_20261113.md) · [专利原件对照](docs/mechanisms/source_crosscheck_20261112.md) · [原文对照表](docs/current_digital_source_map.md) · [论文与讲稿](references/README.md) · [易理解性分析](docs/ENGINEERING_READABILITY.md) · [Python–RTL 一致性验证](verification/README.md)

**这个仓库要回答一个问题**：ADI ISSCC 2024 §9.8（20 b、40 MS/s、40 nm、两步式 SAR）的每个机制，
在我们自己的行为模型里**到底实现了没有、实现到什么程度、证据在哪**。

答案不是"做过哪些检查"，而是**沿专利原图逐个模块对上代码**：
每项技术给原图编号、器件/节点、对应函数、一笔真实样本、同条件开/关，以及**没实现的部分**。

---

## 0. 从哪读起

| 你是谁 | 走这条 |
|:---|:---|
| 第一次来，只想跑一次 | [`docs/guide/QUICKSTART.md`](docs/guide/QUICKSTART.md) → 最小运行 → 单样本 trace |
| 要改代码 | [`docs/design/ARCHITECTURE.md`](docs/design/ARCHITECTURE.md) → 数据流与状态归属 → 跑行为指纹 |
| 要查错 | [`docs/debugging/DEBUGGING.md`](docs/debugging/DEBUGGING.md)（8 个案例，逐个给入口） |
| 要核对专利 | [`docs/mechanisms/source_crosscheck_20261112.md`](docs/mechanisms/source_crosscheck_20261112.md)（含原件图） |
| 要判断可信度 | [`docs/acceptance/20261112-e14a0a0/report.md`](docs/acceptance/20261112-e14a0a0/report.md) |
| 名词看不懂 | [`docs/guide/TERMS.md`](docs/guide/TERMS.md) |

工程自检一条命令：`python3 tools/check_engineering.py`（**7 项**，失败非零退出）。

**从全新克隆验证可复现性**：

```bash
PYTHON=python3 bash tools/verify_clean_clone.sh              # 全部 7 项（含矩阵与图重跑）
SKIP_HEAVY=1 PYTHON=python3 bash tools/verify_clean_clone.sh # 跳过重活，约 30 秒
```

它会 `git clone` 到一个临时目录，在那里跑测试、门禁、最小运行、审计脚本、
行为指纹，并**重跑矩阵、确认与入库数据逐项一致**。用于排除"只在我本地能跑"。

---

## 1. 当前结论（一句话版）

**默认路径（论文描述的配置）与论文 [00] 的指标吻合。**

论文标题与正文/图中可核对的指标共 **6 项**：

* 标题给出 4 项：`9.3 nV/√Hz`（NSD）、`20 b`、`40 MS/s`、`94.2 dB`（DR）
* 正文给出 1 项：输入范围 `±3 V`
* 图 9.8.4 给出 1 项：INL 量级「20b level」——**只标数量级、未给数值**

> ⚠️ **INL 这一项不能算"吻合"**：论文图 9.8.4 只标「20b level」而不给数值，
> 且其测量口径（逐码 INL？电平数？平均方式？）未被复现。
> 本模型的 3.2 LSB20 是**256 电平平均传输误差**，与论文口径不同 ⇒
> 按 `docs/mechanisms/source_crosscheck_20261112.md` 的结论：
> **不得并列比较**。这里列出只为说明量级不矛盾（32768 LSB20 满量程下 3.2 属 20 位量级），
> 不作为"复现了论文 INL"的证据。

| 指标 | 论文 [00] | 本模型 | 说明 |
|:---|:---:|:---:|:---|
| 动态范围 DR | 94.2 dB | **94.33 dB** | 失配+噪声（A2）|
| 噪声谱密度 NSD | 9.3 nV/√Hz | **8.55 nV/√Hz** | 同上；**全 Nyquist 白噪声等效密度**口径 |
| 分辨率 / 采样率 | 20 b / 40 MS/s | 20 b / 40 MS/s | — |
| 输入范围 | ±3 V | ±3 V | 论文正文 |
| INL | 图 9.8.4 仅标「20b level」，**未给数值** | 3.2 LSB20 | **不可并列比较**，见下 |

**但这个吻合有一个前提必须说清**：电容失配 σ 是**从论文这两个指标反解**出来的
（σ=1e-4），不是论文披露值。两个独立指标同时命中，但**吻合 ≠ 复现**。
细节见 [验收对照报告](docs/ACCEPTANCE_20261113.md) 与
[校准推导](docs/mechanisms/paper_metrics_calibration.md)。

| 机制 | 状态 | 交付物 |
|:---|:---|:---|
| RDAC follower（分离量化器） | ✅ 已实现 | `behavioral/follower.py` |
| 三维 DEM | ✅ 已实现 | `control/dem3d.py` |
| 保留列 / 362 门 / 跨片分配 | ✅ **判据已交付**（未接主链） | `control/{reserved_column,shuffle_gate,cross_slice_mux}.py` |
| AUX 输入 | ✅ 判据已交付 | `control/aux_input.py` |
| 参考缓冲（FIG.5） | ✅ 判据已交付 | `control/reference_buffer.py` |
| radix<2 冗余 | ✅ 判据已交付 | `control/redundancy.py` |
| 分路 dither | ⚠️ 专利**可选增强**，与窗口不兼容 | `control/dither*.py`，见 §6 |

**六份专利 [09]–[14] 均已完成至少一轮原件核对。**

## 2. 原论文的总体结构（先有这张图，再谈实现）

论文 §9.8 的链路：**输入 → 8 片采样（另 8 片转换、2 片空闲）→ 3b Flash + 6b SAR 的小量化器（SADC）
→ 大盘 RDAC 跟随已决码 → 共享 RA 放大余差 → 12b ADC2 → 20 b 输出**。

三个与常规 SAR 不同的地方，也是本仓库的三条主线：

1. **量化器与 RDAC 分离**：量化器只判决，RDAC 不试探、只跟随 → 参考电流从"试探往返"里省出来。
2. **模拟域交织（ADI）**：18 片池、每笔 8 片，采集与转换并行 → 采集窗口拉到整个周期。
3. **数字侧三重线性化**：DEM（重排单元）、dither（打散量化）、权值校正（修确定性误差）。

## 3. 专利技术对照总表

> 专利原件不在仓库内。**六份专利 [09]–[14] 均已完成至少一轮原件核对**
> （部分图页无文本层，按原生分辨率逐要素读图）。
> 状态：`已实现` = 有代码 + 有门禁；`判据已交付` = 有可核对判据与单测、**未接主链**；
> `部分` = 有缺口；`未实现` = 明确空缺。

| 技术 | 对照原图 | 当前实现 | 状态 | 缺什么 |
|:---|:---|:---|:---:|:---|
| 分离量化器 + 切片 RDAC | US10516408B2 Fig.7/10/11/16–17 **已核** | `follower.py`、`pipeline_engine` | 等效 | 逐事件电荷已建；真实建立/边沿未建 |
| 分路 dither | US10505561B2 **Fig.19 已核** | `dither.py`、`dither_port.py` | 等效 | 物理电容/开关网络；专利可选增强，见 §6 |
| DEM 与跨段映射 | US10511316B2 **Fig.11/19/20 已核** | `dem3d.py` | 部分 | 保留列、362 门、跨片分配已有判据（§6.4），未接主链 |
| 动态参考 | US10826519B1 **FIG.5 已核** | `conversion.py`、`pipeline_engine` | 部分 | 三条结构判据已交付；A7 出不了 INL/SNR |
| TRACK / AUX | US10707889、US10541702B1 **FIG.1/2 已核** | `track_aux.py` | 部分 | AUX 判据已交付（`aux_input.py`），未接主链 |
| 普通 AZ | 论文/PPT 时序图 | `behavioral/ra_az.py` | 已实现 | 漂移/保持扫描未做 |

## 4. 各项技术的原图与实现摘要

### 4.1 分路 dither（Fig.19）→ [六段全文](docs/mechanisms/dither_fig19.md)

字分两段：**下段 L 进模拟**（`x−L` 送量化器）、**上段 U 进码**（RDAC 取 `c−U`），数字再加回 `T=L+UΔ`。
代码：`Fig19DitherProgram.from_words` → `lower_v/upper_code/total_v`；注入端 `DitherPort`（等效电荷口）。
真实样本见专题 §④（word 21：`L=2.714 LSB`、粗码 129→126）。
**缺口**：七片示例 vs 八片活动组、物理网络、环内闭环。

### 4.2 DEM（Fig.11 / Fig.19–21）→ [六段全文](docs/mechanisms/dem_fig11.md)

主阵列 15×2C + 2×1C、B4 在 16 个 1C 里选 8、子阵列二进制经 β=1/17 耦合。
横向按**物理 ID** 旋转 15 列，纵向按**组内位置**选 8/16——这个不对称是刻意的：
纵向若按 ID 取模，18 片池会选出 9~10 个单元，B4 权重当场变。
**缺口**：保留列、跨片分配规则、子阵列二进制不可 DEM。

### 4.3 其余四项

* **follower**：7 个已决事件逐次加载；前缀截断不变性 28 672 检查 / 0 违约。
* **TRACK**：两相流水线下最小回看 2 笔；`lookback=1` 会被时间判据直接否掉。
* **动态参考**：参考误差在 RA 入口 15.5 bit、孔径处 27 bit（`tau_ref` 由论文 65% 反解，属自洽）。
* **校准**：逐码表 `fit_code_correction`；逐单元 `fit_element_weights` 秩满但全节点残差 991 µV，未闭环。

## 5. 同条件开/关（可重跑）

同一颗芯片（seed 20261105，σ_unit = σ_bridge = **1e-4**，由论文指标反解）、
同一输入网格（512 电平 × 8 次；动态 1024 点、1 MHz 相干）。唯一变量是 DEM / dither / 校准。

| 配置 | INL [LSB20] | SNDR [dB] | DR [dB] |
|:---|---:|---:|---:|
| A0 理想 | 0.6 | 122.69 | 122.80 |
| A1 仅失配 | 3.1 | 103.99 | 101.48 |
| A2 失配+噪声（基线） | 3.2 | 94.70 | 94.33 |
| A3 +DEM | 1.6 | 94.47 | 93.79 |
| A4 +DEM+均匀计划 | 2.3 | 94.44 | 94.20 |
| A5 +逐码校准 | 2.3 | 94.84 | 94.33 |
| A6 +dither | 4125.5 | 39.44 | 39.79 |
| C1 DEM+校准 | 3.6 | 94.27 | 93.79 |
| C2 DEM+dither | 4125.8 | 39.44 | 39.79 |
| C3 全组合 | 4058.8 | 39.44 | 39.79 |

数据：`docs/reviews/20261113_p3p4/data/p4_ablation.json`（可重跑）。

**NSD 有两个口径，别混用**：

| 字段 | 值 | 定义 |
|:---|:---:|:---|
| `nsd_v_rthz_full` | **8.55 nV/√Hz** | 全 Nyquist 白噪声等效密度 `noise_rms/√(fs/2)`，与论文 9.3 对照用这个 |
| `nsd_v_rthz_dr` | 9.11 nV/√Hz | 按 DR 归一化后的等效密度 |

两者都是本模型算出来的，只是归一化带宽不同。校准 σ 时用的是前者的口径。

### 三条需要解释的结果

1. **A0 理想 122.80 dB** —— 零失配零噪声下，管线与 ADC2 量化不是瓶颈。
2. **A5 校准：2.3 LSB20，优于不校准的 3.2** —— 前提是**训练样本足够**。
   默认训练点数已从 8200 提到 **60000**（每码约 89 样本）：
   实测 8200 点时每码仅 13 个样本，LUT 会拟合到该次噪声实例，校准反而变差（6.23）；
   32800 点起开始优于基线，60000 点时达到 2.33。
   详见 [校准推导 §7](docs/mechanisms/paper_metrics_calibration.md)。
3. **A6/C2/C3 的 ~4100 LSB20** —— dither 是专利 [10] 的**可选增强**
   （论文正文 "dither" 出现 **0 次**），其 U 段需要 5.5 LSB1 残差摆幅，
   而 0.45 V 窗口是为**无 dither 的残差**设计的。见 §6。

### 5.1 SNDR 分解（同一 seed，只改一个条件）

1024 点、1 MHz 相干：

| 条件 | SNDR [dB] | DR [dB] | 说明 |
|:---|---:|---:|:---|
| σ=0，噪声关 | 122.69 | 122.80 | 管线与 ADC2 量化本身不是瓶颈 |
| **σ=0，噪声开** | **95.23** | **95.34** | **纯热噪声（kT/C + RA）** |
| **σ=1e-4，噪声关** | **103.99** | **101.48** | **纯器件失配** |
| **σ=1e-4，噪声开（A2）** | **94.70** | **94.33** | 两者叠加 —— **与论文 94.2 dB 吻合** |

读法：**在这个失配水平下，SNDR 是热噪声限制的，不是失配限制的。**

* 热噪声单独给 95.23 dB —— 与论文的 93.7 dB SNDR 同量级；
* 器件失配单独只到 103.99 dB，**比热噪声高 8.8 dB**；
* 两者叠加 94.70 dB，热噪声主导。

这与旧版 README 的结论相反（旧版基于 σ=1e-3，那时失配是主导项）。
σ 改为 1e-4 后，**限制项从失配变成了热噪声**。

## 6. 没做的与分不清的（不掩盖）

### 6.1 缺原件依据的两项

| 项 | 情况 |
|:---|:---|
| **ADC2 窗口为何是 0.45 V** | 论文/PPT/专利都没给。但 `0.45 = 1 LSB1·G + 2×0.0375` 说明它是为**无 dither 的残差**量身定的 |
| **S4 的编码规则** | 论文正文 "S4" 出现 **0 次**。已通过显式补偿绕过（解码器按物理增益换算 fine），不再是阻塞 |

### 6.2 dither 的正确理解

论文正文 "dither" 出现 **0 次**；dither 的 300/222/224 三个加法器全部来自专利 [10]。
它是**可选增强**，不是论文描述的机制。默认路径 A0–A5 本来就不带 dither。

A6/C2/C3 保留为「启用 dither 需要什么条件」的定量记录：
U 段需要 5.5 LSB1 残差摆幅 ⇒ 窗口需 ≥2.06 V（而当前 0.45 V）。

### 6.3 未实现的工程项

| 项 | 状态 | 影响 |
|:---|:---|:---|
| 逐单元权值闭环 | 未闭环 | 逐码 LUT 在样本充足时有效（§5.2、§7）；但每码一个值仍压不住同码不同状态 |
| 动态路径数字输出 | 部分 | A7 只能看波形 |
| 顶层对拍（V3/V4） | NOT_RUN | 本机无 VCS 环境 |
| 后仿 / PEX / 硅测 | 无 | 证据层级止于 L1 |
| `execute_pipeline` 拆分 | 未做（约 760 行） | 可读性 |

### 6.4 判据模块（已交付但**未接主链**）

保留列、362 信号门、跨片分配、AUX、参考缓冲、radix<2 冗余 —— 六项都有可核对判据与单测，
但**都没有接入主链**：原件没有给出"本芯片是否启用这些机制"的依据。
接入属设计选择，应由人决定，不靠猜。

## 7. 运行与调试入口

```bash
# 1) 一次名义运行（--output 必须是**新目录**，已存在会被拒绝）
PYTHONPATH=src python -m adi_model simulate --samples 4096 --output /tmp/adi-demo

# 2) 机制门禁（follower / DEM / dither / TRACK / 校准）
PYTHONPATH=src python -m pytest tests/unit/test_{follower,dem3d,dither_port,dither_charge_port,track_aux,mask_calibration}.py -q

# 3) 外部审查的修复回归（应 4/4 PASS）
PYTHONPATH=src python3 tools/check_audit_findings.py

# 4) 复算矩阵与图（两处 repeats 必须一致，否则图与矩阵数字对不上）
PYTHONPATH=src:. python3 tools/run_p4_ablation.py --dynamic-n 8192 --static-points 256 \
  --static-repeats 32 --out docs/reviews/20261113_p3p4/data
PYTHONPATH=src:. python3 tools/run_review_figures.py --points 256 --repeats 32 \
  --dynamic-n 8192 --out docs/reviews/20261113_p3p4/figures

# 5) 一笔样本的完整链路
PYTHONPATH=src python3 tools/trace_one_sample.py --out docs/mechanisms/data
```

**读代码顺序**：`docs/mechanisms/dither_fig19.md` 或 `dem_fig11.md`（先看原图与映射）
→ `src/adi_model/simulation/pipeline_engine.py`（唯一主链）→ `src/adi_model/defaults.py`（一颗芯片怎么装配）
→ `tools/run_p4_ablation.py`（矩阵怎么跑）。

## 8. 证据层级

本仓库只到 **L1 行为级（模拟浮点 + 20 位定点解码）** 与 **L2 数字 RTL 编译**；
没有后仿、没有 PEX、没有硅测。

**关于「论文指标已复现」**：六项指标确实吻合（§1），但失配 σ 是从论文两指标
反解出来的，因此准确说法是：

> 在「σ=1e-4」这个假设下，模型的行为级噪声预算与论文公布的两项独立指标一致。

**吻合 ≠ 复现。** 这不说明芯片内部每一个环节都与模型相同
（ADC2 窗口与 S4 编码仍未获原件确认）。
