> 历史版本文档：下文记录 v7 阶段的实现与审计。当前物理链路、定点输出、噪声与验收范围见 [model_scope.md](model_scope.md) 和 [实施账本](IMPLEMENTATION_CHECKPOINT.md)。

# 对独立审计报告的逐条回应

**对象**：`adi_model_release_v6.1`（冻结基线）的独立审计，
见 `adi_v61_audit/审计报告.md`。
**回应者**：本仓库（`adi_sar_model/`）。
**日期**：2026-09-10。

---

## 0. 立场

审计的结论是：

> "目前不能称为 ISSCC 2024 9.8 原架构的完整有效复现，也不能以其全部 PASS
> 证明目标芯片性能或新增 KTC 缩容方案已具备电路可实现性。"

**本仓库接受这个结论，并且不打算通过调参把它翻过来。**

我们把目标从"复现原芯片"改成审计建议的定位：

> "基于 ISSCC 2024 9.8 及相关专利启发的两级 SAR 行为级研究平台，已验证若干
> 电荷与误差处理机制的内部一致性，用于架构修正和条件性参数探索；尚未完成与
> 原芯片时序、物理 slice 池及全部精度机制的一致性验证。"

因此本文件里**没有一条**"已完全修复，性能达标"的表述。每条下面的
「仍未闭合」小节是这份文档里最重要的部分。

### 审计的元结论，以及我们的回应

> "each stage validated its output against its own assumptions"

这是全部 11 条指控的共同成因。因此回应不是逐条打补丁，而是换治理结构：

| 治理改动 | 挡住了哪一类问题 |
|:---|:---|
| 来源分级从注释变成运行时值（[ADR 0007](adr/0007-provenance-as-a-runtime-value.md)） | A04 / A05：拟合值被当成披露值引用 |
| 第一级读数由**两条披露联立**唯一确定（[ADR 0003](adr/0003-stage-1-resolution.md)） | A01：口径可以悄悄换 |
| 两条主循环共用一个栅格构造器（[ADR 0004](adr/0004-single-source-of-truth-quantiser-grid.md)） | 两个实现各自解释接口 |
| slice 池变成有记忆的物理对象（[ADR 0005](adr/0005-physical-slice-pool.md)） | A02：检查并发 ≠ 检查因果 |
| 判据阈值从写死常数改为**解析推导** | 多处：阈值比物理先过期 |

一个可检验的自我声明：**审计提出的每一条指控，现在都有一条会失败的测试。**
见 `tests/audit/test_audit_findings.py`（40 条）、
`tests/regression/test_observer_headroom.py`、以及
`tests/regression/test_skew_derivative.py`（19 条）。
这些测试对 v6.1 全部 FAIL。

审计报告用 `A01`–`A07` 编号；测试里用更短的 `F1`–`F10` 作为**稳定的测试
标识符**（`CHANGELOG.md` 引用的是这一套）。两者的对应关系写在
`tests/audit/test_audit_findings.py` 的模块 docstring 里：

| 审计条目 | 测试标识 | 测试类 |
|:---|:---|:---|
| A01 | F1 | `TestF1FirstStageReading` |
| A02 | F2 | `TestF2SliceCausality` |
| A03 | F3 | `TestF3DitherUnits` |
| A04 | F4 | `TestF4NoiseBudgetIsAnAnchor` |
| A05 / A06 | — | `tests/regression/test_observer_headroom.py` |
| A07.1 | F8 | `TestF8EvidenceScope` |
| A07.2 | F9 | `TestF9FlickerBandLimit` |
| A07.3 | — | `tests/regression/test_skew_derivative.py` |
| A07.4 | F10 | `TestF10MonteCarloProvenance` |
| A07.5 | — | `TestExtraLowFrequencyMetrics` |

---

## A01 — 第一级 9 位被换成 6 位粗量化加 3 位 DAC 栅格

**判定：指控成立。** 这是最严重的一条，因为它改变整个后端预算。

### 复现

审计给出的对照（关闭噪声/失配/dither，16384 点近满量程斜坡）：

| 配置 | 粗量化/信号 DAC 电平数 | Δ1 | 最大残差 | 最大 RA 输出 |
|:---|--:|--:|--:|--:|
| 原 b1=6 | 64 / 64 | 93.75 mV | 93.74565 mV | 2.999861 V |
| 同模型改 b1=9 | 512 / 512 | 11.71875 mV | 11.71847 mV | 0.374991 V |

本仓库把这两行都变成了可重跑的对照（`Config.legacy_v61()` 与
`Config(stage1_reading="paper_literal", b1=9, dither_enhancement_bits=0, …)`）。

### 处置

审计的关键要求是：**"修复必须重新预算残差、后端范围、dither 和匹配误差，
而非只改一个参数。"** 我们照做了，而且把"选哪个读数"变成一个**联立求解**：

论文同时披露两条互相约束的事实 ——

> (a) "...resulting in **9b quantization** in the first stage."
> (b) "the dither **range is enhanced by 2b** when transferred from the
> quantizer to the RDAC."

在本模型的机制里，(b) 的"增强 2^b"就是「一个第一级判决步含多少个 RDAC 单位
步」，即 `log2(DAC 电平数 / 2**b1)`。两条联立：

```
b1 + b_enh = 9 ,  b_enh = 2   =>   b1 = 7 ,  units_per_lsb1 = 4
```

**默认读数因此改为 7b SADC + 2b 量程增强 = 9b 第一级量化**，并把整个后端按
Δ1 重推（`Config.paper_consistent()` 自动派生）：

| 量 | v6.1 (b1=6) | 本仓库 (b1=7) |
|:---|--:|--:|
| 一级判决电平 | 64 | **128**（DAC 512 电平 / 4 单位每步） |
| Δ1 | 93.75 mV | **46.875 mV** |
| RA 输出 span | 3.0 V | **1.5 V** |
| ADC2 | 15 bit [-0.3, 3.3] V | **14 bit [-0.15, 1.65] V** |
| Δ2/G0 | — | **3.433 µV = 0.60·LSB20** |

`validate()` 新增硬校验，把不自洽组合变成显式 FAIL 而不是静默出垃圾数据：

| 判据 | 表达式 |
|:---|:---|
| 第一级读数与 DAC 拓扑自洽 | `2**b1 <= DAC 电平数` |
| 第一级栅格可整除 | `DAC 电平数 % 2**b1 == 0` |
| dither 增强位数与栅格一致 | `dither_enhancement_bits == log2(DAC 电平数 / 2**b1)` |
| 后端分辨能力 | `Δ2/G0 <= LSB20` |
| 残差不溢出 | `G0·Δ1 < adc2_v_max` |

三种读数全部通过各自的自洽检查（后端预算不同）：

| 读数 | b1 | upl1 | 增强 | ADC2 | validate |
|:---|--:|--:|--:|:---|:---|
| `paper_consistent`（默认） | 7 | 4 | 2b | 14b [-0.15, 1.65] | ✅ |
| `paper_literal` | 9 | 1 | 0b | 14b [-0.0375, 0.4125] | ✅ |
| `legacy_codeword` | 6 | 8 | 3b | 15b [-0.30, 3.30] | ✅ |

### 仍未闭合

- **ADC2 的 14 bit 与 [-0.15, 1.65] V 仍是 `[假设]`。** 论文未公开 ADC2 的
  位数与量程；这是"与本读数自洽"的取值，不是披露值。它现在被
  `audit_provenance()` 明确列为 57 项假设之一。
- **没有声称已复现原芯片。** 见 §0。
- 详细推导与备选读数见 [ADR 0003](adr/0003-stage-1-resolution.md)。

---

## A02 — 18-slice 调度缺少采样历史，物理电容未绑定选择结果

**判定：指控成立（三个子项全部成立）。**

### 复现

审计在 seed=20260910 / 8192 周期 / 8191 次转换下实测：

| 指标 | 审计实测 | 物理因果要求 |
|:---|--:|:---|
| 当前转换组完全对应前次采集组 | 1 次 | 全部 |
| 平均属于前次采集组的片数 | **3.5572 / 8** | 8 / 8 |
| 有 slice 连续处于转换组 | 8180 次 | 0 |

第三子项：`pipeline` 的物理 DAC 求值**没有接收 8/18 选择**，因此固定调度与
随机调度的输出**逐位相同**、最大差为 **0**。

### 处置

把 slice 池建成有状态的物理对象 `PhysicalSlicePool`，两条硬约束可机器校验：

1. **采集→转换的因果延迟**：第 `n` 周期的转换组 ⊆ 第 `n−1` 周期的采集组。
2. **同周期不相交**：采集组 ∩ 转换组 = ∅（v6.1 已有的那条，保留）。

| 策略 | transitions | **violations** | mean_coverage | causal |
|:---|--:|--:|--:|:--:|
| `shuffle_causal` | 8191 | **0** | **8.0** | ✅ |
| `pingpong` | 8191 | **0** | **8.0** | ✅ |
| v6.1 | 8191 | 8180 | 3.5572 | ❌ |

第三子项单独关闭：把 `sid`（DEM 状态）传进 `SplitDAC.evaluate_physical` /
`PhysicalSlicePool.dac_error`，选择因此改变哪些物理单位被接通：

| 指标 | v6.1 | 本仓库 |
|:---|--:|--:|
| 两个不同 8/18 组之间的 `max|Δe_dac|` | **0.0 µV** | **23.64 µV = 4.13 LSB20** |

### 仍未闭合

- **`shuffle_causal` 不是论文的排布。** 论文的具体 slice 轮转次序未公开；
  我们只保证"存在一个因果的排布"。
- **18 套数组的完整寄生网表未建**，只在集总层面近似。
- 审计 A02 末段关于"当前采集组建立误差混入同一次输出"的问题**部分缓解但
  未闭合**：它依赖 A06 的时变参考建模，那一条本身也没有闭合。
- 详见 [ADR 0005](adr/0005-physical-slice-pool.md)。

---

## A03 — dither 码值单位错误，2b 增强机制未对齐

**判定：指控成立。**

### 复现

审计引用的 v6.1 代码：

```python
gran = step0 if transfer == 'rdac' else d1_eff
d_code = -round(dither / gran)
k_eq = coarse * units_per_lsb1 + d_code     # <- 单位系统不一致
```

`k_eq` 以 RDAC 单位步 `step0` 为单位，而 `gran = d1_eff` 时 `d_code` 是
**粗量化单位**，缺少 `gran/step0` 因子（v6.1 配置下 = 8）。

审计精确重跑 stage21（N=16384、fin=2.49267578125 MHz、seed=1255）：

| 条件 | 原代码 | 仅修正单位 |
|:---|--:|--:|
| 粗粒度转移 ADC2 溢出率 | 32.6721% | **15.9668%** |
| 粗粒度转移 RMS 误差 | 16308.07 µV | **5698.59 µV** |
| RDAC 粒度转移溢出率 | 0% | 0% |
| stage21 PASS | true | **false** |

**关键点：修正单位之后原判据翻成 FAIL** —— 说明原 PASS 依赖一个错误基线。
本仓库把换算抽成唯一入口 `mapper.dither_transfer_code()`，两条主循环都必须
调用它，函数 docstring 完整记录了这段历史与数值。

### 处置

- 单位换算收敛到 `dither_transfer_code(cfg, dither_volts, step_rdac=…,
  step_coarse=…)`，返回**RDAC 单位步**口径的偏移量。
- **stage21 的判据被重写**：不再要求"粗粒度对照溢出率 >30%"（那是对错误
  基线的依赖），而是判"落在 ADC2 窗口内"这一物理条件本身。
- 论文的"增强 2b"与 ADR 0003 的读数绑定：`dither_enhancement_bits = 2`
  且 `units_per_lsb1 = 4 = 2²`，两者由 `validate()` 互相约束。

### 仍未闭合

- **"把一个 dither 数值以更细的栅格取整"不等于论文的机制。** 审计明确说：
  > "该命题不等同于把一个 dither 数值以 8 倍更细的栅格取整。"

  我们承认这一点：本模型的 `dither_transfer_model="range"` 是**对该机制的
  一个可计算近似**，不是对 [10] Fig.5 / Fig.19 中 fractional/integer 或
  lower/upper dither 分工的还原。这一点已写入 `docs/model_scope.md`。
- 修正单位**不代表**方案已正确实现论文，也**不代表**粗粒度方案完全无溢出。

---

## A04 — 目标 DR 与失配量是输入标定，不是独立性能预测

**判定：指控成立。** 这是"注释拦不住"的教科书案例。

### 复现

审计引用 `config.py`：

```
σ_RA² = max[(V_FS,rms·10^(−DR_target/20))² − σ_kTC,baseline² − σ_q2², 0]
```

关闭失配后的 DC 噪声检查：

| 目标 DR | 测得 DR |
|--:|--:|
| 90 dB | 90.0148 dB |
| 94.6 dB | 94.6106 dB |
| 98 dB | 98.0014 dB |

即"重现 94.6 dB"是**噪声预算闭合**，不是从独立电路参数预测。

### 处置

把分级升级为运行时机制（[ADR 0007](adr/0007-provenance-as-a-runtime-value.md)）。
当前 3 项 `FITTED`：

| 字段 | 表中原话 |
|:---|:---|
| `ra_out_noise_rms` | `None => reverse-solved from target_dr_db (ANCHOR, not prediction)` |
| `mismatch_sigma0` | `100 ppm behavioural calibration; NOT a PDK value` |
| `sadc_rdac_gain_mismatch` | `set to be consistent with >11b matching [00]` |

`target_dr_db = 94.6 dB` 本身是 `DISCLOSED`（论文披露值）；被标为拟合的是
**由它反推出来的噪声**。这个区分是 A04 的核心：

> 锚点可以引用，"重现锚点"不可以当预测。

`audit_provenance(cfg)["fitted_in_use"]` 就是 A04 想要的那份清单，读者不必
读代码。另外，审计提到的版本口径差异（digest 9.3 nV/√Hz + 94.2 dB vs
PPT 8.8 nV/√Hz + 94.6 dB）已在 `provenance.PARAM_GRADES` 与模块注释里写明
取的是 PPT 锚点。

### 仍未闭合

- **1117 ppm 与 100 ppm 的差距只能支持"在所设假定下的校准压力测试"**，
  不能倒推实际芯片所需校准倍率或良率。这一条已被采纳为
  `docs/model_scope.md` §4 的边界，并在 README 的"不支持的结论"里复述。
- **没有 PDK 数据。** `mismatch_from_pdk` 是面积律估计（`[假设]`），
  不是测量值。

---

## A05 — KTC 支路是研究扩展，理想观测器尚无电路证据

**判定：指控成立（性质是"条件性算法结论"，不是"数字作弊"）。**

### 复现

审计独立检查（失配关、RA 白噪声开、0.9 满幅、1.24267578125 MHz、
N=16384、观测支路附加噪声为零）：

| 总电容比例 | KTC | observer BW | SNDR |
|--:|:--|--:|--:|
| 1 | 关 | — | 93.666 dB |
| 1/4 | 关 | — | 91.149 dB |
| 1/4 | 开 | 理想 | **95.010 dB** |
| 1/4 | 开 | 1 GHz | 93.474 dB |
| 1/4 | 开 | 3 GHz | 94.845 dB |
| 1/4 | 开 | 8 GHz | 95.006 dB |

并指出：fs=40 MHz、Δt=Ts/256 时，一阶建立要剩余误差 ≤1% 需带宽约 7.5 GHz。

### 处置

1. **定位**：`ktc.py` 的模块 docstring 明确声明这不是 ADI 已公开模块；
   在 `PARAM_GRADES` 里 **8 项**标为 `RESEARCH_EXTENSION`。
2. **默认关闭**：`ktc_enable=False` 是默认值；`audit_provenance` 的
   `research_extension` 分组让"哪些结论依赖它"一目了然。
3. **修掉一个真实的量程缺陷**（审计未单列，我们在回归中撞到）：
   观测器校正量原写在模拟侧 `quantize(vra − κ·v_N)`，导致近 Nyquist 时
   ADC2 被顶出量程。实测：

   | 输入频率 | `κ·v_N` rms | 旧写法溢出率 | 新写法溢出率 |
   |:---|--:|--:|--:|
   | `fs/64` | 31.2 mV | 0.00% | 0.00% |
   | `fs/8` | 249.9 mV | **12.24%** | 0.00% |
   | `fs/2` | **1413.7 mV** | **49.63%** | 0.00% |

   改为数字域扣除 `ADC2.quantize_with_correction(vra, κ·v_N)`，见
   [ADR 0006](adr/0006-observer-correction-is-digital.md)。**这解除了量程
   约束，不构成电路证据。**

### 仍未闭合

- **没有真实观测网络噪声协方差、额外采样噪声、输入变化、限幅、负载与功耗
  预算。** 审计要求的这一串，我们**一项也没有做**。
- 所有缩容结论都写作"在给定观测条件下"的系统级可行域，**不是**芯片性能
  保证。这一条写入 README §4 与 `docs/model_scope.md`。
- 按审计建议把它**另分支研究**（P2）—— 本仓库的做法是保留为默认关闭的
  可选模块，而不是从主干删掉，这样旧结果仍可复现。

---

## A06 — 共享 RA + 动态参考的关键耦合没有闭合

**判定：指控成立，且这是我们**最没资格反驳**的一条。**

### 复现（审计引用的文献事实）

> [00] 第 1 页明确说明：转换相由 coarse pre-charge reference 供电荷；
> RA 前切换 REF_IN；RA 开始时参考仅约 15 位准确；至 RA 相约 65% 才实现
> 20 位建立。[00_1] 第 33–35 页为 GmR + OTA、AZ、ADC2 宽/窄采样带宽时序。

而 v6.1 的 `ra.py` 是"即时增益 + 噪声 + 限幅"，没有有限带宽、稳定性、slew
与跨样本记忆；AZ −1.6 dB 与动态带宽 +1.3 dB 主要作为**噪声乘数**进入，
不是从实际传递函数求得。

### 处置（**部分**）

- 我们**没有**实现审计要求的"事件级联立"。这是一个诚实的空白。
- 已做的三项：(1) `ra.py` 的 flicker 生成器改为带限（转角以上置零），
  删掉了一个物理上不可能的注入项；(2) `dynamics.py` 的参考恢复保留为
  码相关终态误差，但它的性质被显式标注为 `[假设]` 而不是机制；
  (3) AZ 与动态带宽的 dB 数被标为 `[披露]`（来自 PPT p.34–35），
  但它们**以乘数形式进入**这件事被写进模块注释。
- `docs/model_scope.md` 把"共享 RA + 动态参考的因果路径"列为
  **不能证**的清单第一条。

### 仍未闭合（审计的最小修复顺序 P1）

> "用明确的 coarse/fine reference 切换及 RA 动态方程求 ADC2 采样值。
> 先用简单一阶/二阶模型也可，重点是因果路径、模式切换和参数依据。"

**这一条我们没有做。** 它需要新增一个时变参考 + RA 跟踪方程的状态空间，
并重新验证 stage19/20/22/23 的全部判据 —— 是一个独立的、比本次治理重构
更大的工作。因此：

- 本仓库**不声称**能解释"论文为何在该参考建立曲线下仍获得目标精度"；
- **不声称**能解释"为何不宜用积分型 RA"；
- **不声称**能给出 50/69 mW 或信号链 FoM 的量化改善。

---

## A07 — 静态、低频与图形证据需要限制口径

**判定：6 个子项全部成立。其中 2 项是我们在复现中额外发现的缺陷。**

### A07.1 平均误差曲线 ≠ 全 20 位码密度 DNL

成立。`n_bits_target` 只定义目标 LSB；独立改变 18/20 **不改变输出**。
本仓库把这条编译成测试：
`tests/audit/test_audit_findings.py::TestF8EvidenceScope::test_n_bits_target_does_not_change_the_analog_output`
—— 改 `n_bits_target` 一个采样都不许变。README §4 第一条就是
"No 20-bit code-domain claim"。

### A07.2 短记录系统测试不能验证 40 Hz 转角

成立。fs=40 MHz / N=32768 时首个非 DC bin 为 1220.703 Hz，而 fc=40 Hz，
主系统闪烁序列**全零**。处置：

- `ra.py` 不再在转角以上注入（带限修正）；
- stage23 的判据改为"从 χ² 自由度推导容差"（原为写死的 3 dB，而低带仅
  2 个 bin，容差本身不成立）；
- 主记录**不含**闪烁这一点写进 README §4 与 `docs/model_scope.md`
  （`TestF9FlickerBandLimit` 断言它）。

### A07.3 skew 的 `np.gradient` 导数偏差 —— **额外发现的缺陷**

审计指出：正弦中央差分导数幅度比 = `sin(2πfin/fs)/(2πfin/fs)`，在 5 / 10 /
19 MHz 处为 0.9003 / 0.6366 / **0.0524**，故不能把低频结论外推到近 Nyquist。

**我们独立复算并确认，然后把它端到端复现了一遍**（同一记录跑两次，一次用
出厂估计器，一次把 `np.gradient` 打回去）：

| fin | 中央差分增益 \|H\| | 理论抑制 1/\|H\| | 实测比值 | 判据 |
|--:|--:|--:|--:|:---|
| 5 MHz | 0.9003 | 1.11 | **1.1** | ≥1.05 ✅ |
| 10 MHz | 0.6366 | 1.57 | **1.6** | ≥1.40 ✅ |
| 15 MHz | 0.3001 | 3.33 | **3.3** | ≥2.80 ✅ |
| 18 MHz | 0.1093 | 9.15 | **9.2** | ≥7.00 ✅ |
| 19 MHz | 0.0524 | 19.1 | **18.3** | ≥12.0 ✅ |

处置：新增 `sampler.input_derivative()` —— 优先用**解析导数**
（`sine_input` / `dc_input` 自带 `.derivative` 属性），否则用**谱导数**
（FFT，对带限记录在 [0, fs/2] 内增益为 1）。`pipeline.py` 改为调用它，
并加一条**静态守卫**测试禁止 `np.gradient` 重新出现在信号路径里。

回归测试 `tests/regression/test_skew_derivative.py`（19 条）中，
上表每一行都是一个参数化用例 —— 判据不是"看起来对不对"，而是
**估计器的幅度响应**。

### A07.4 MC 直方图重采样 —— **审计点名的图形造假**

成立。`run_all.py` 的"60 颗虚拟芯片 SNDR 分布"图用 `(mean, sigma)` 重新生成
**400 个**高斯样本，不是 60 个原始 MC 点。处置：

- `experiments.monte_carlo_*` 一律**保留 per-chip 原始样本**
  （`sndr_per_chip`）；
- 直方图直接画原始点，**不再重采样**；
- 加了一条**静态守卫**测试扫描源码，禁止出现
  `rng.normal(mean, sigma)` 形式的直方图伪造
  （`TestF10MonteCarloProvenance::test_histogram_is_not_resampled`）。

### A07.5 低频指标函数 ValueError —— **额外发现的缺陷**

成立（审计列为"测量工具边界缺陷"）。`fs=40 MHz`、`N=16384` 时 1 kHz / 5 kHz
调用触发空数组 `ValueError`。处置：`metrics._band_max()` 做 bin 钳位，
并增加 `harmonics_reliable` / `harmonics_dropped` 字段——
**不是把异常吞掉，而是显式声明该频点下谐波不可靠**。测试见
`TestExtraLowFrequencyMetrics`（1 kHz / 100 kHz / 1 MHz / 5 MHz / 19 MHz
五个频点全覆盖）。

### A07.6 驱动噪声同时进入参考与输出 —— 口径必须分开

成立，而且**这一条我们一开始改错了**：曾把 `err_to_x1` / `err_to_x2`
当成"整链误差"，但它们其实是 `out − x(t1)` 与 `out − x(t2)`
（两个**固定采样时刻**的参考），驱动器噪声在这两个量里都会被抵消 ——
因为 `capture()` 把 nd 加进了 `x1`/`x2` 本身。

正确处置：记录驱动器噪声**加入之前**的输入。

| 字段 | 含义 |
|:---|:---|
| `out − x(t1)` | `err_to_x1` —— 固定时刻参考（校准/评价用） |
| `out − x(t2)` | `err_to_x2` —— β=1 的设计目标参考 |
| `out − x_clean(t1)` | **`err_vs_clean`** —— 整链误差（A07.6 要的量） |

实测（`fs/8`，0.9 满幅，非理想全关）：

| `driver_noise_rms` | `err`（内部） | `err_to_x1` | **`err_vs_clean`** |
|:---|--:|--:|--:|
| 0 mV | 1.117 µV | 1.117 µV | **1.117 µV** |
| 1 mV | **0.990 µV** | 0.990 µV | **991.967 µV** |

即：1 mV 的驱动器噪声在 `err` 里只看起来像 1 µV，而在 `err_vs_clean`
里是 992 µV —— 与审计的观察（"err 仍约 1 µV，但相对干净输入的误差约
1 mV"）一致。`driver_noise_rms = 0` 时 `err_vs_clean` 与 `err_to_x1`
**逐位相同**，所以默认分析路径不受影响（测试断言了这一点）。

三条主循环（`sim.py` / `sim_split.py` / `pipeline.py`）都实现了该字段。
回归测试：`tests/regression/test_error_reference.py`（8 条）。

---

## C 系列 — 自查发现的工具链缺陷（审计未覆盖）

审计只审了模型。把同样的方法用在**验证模型的基础设施**上，得到四条缺陷 ——
它们共同的含义是：这个仓库声称的门禁，在改造前**一条也没有真正生效**。

| # | 缺陷 | 后果 | 处置 |
|:-:|:---|:---|:---|
| **C1** | CI 调用 `adi-run-all --out`，而 CLI 只定义 `--results-dir` | reproducibility job 在跑第一个实验之前就以 `unrecognized arguments` 退出 | CI 改用真实 flag；`test_cli.py::TestCIMatchesTheCLI` 静态校验 workflow 中的每个 flag |
| **C2** | CI 的 `pytest -m "slow"` 收集 **0 条**（没有任何测试带该标记） | "完整扫描" job 什么都没跑却显示 PASS | CI 改为直接调用 `adi-run-all`；同一测试类断言 workflow 仍调用它 |
| **C3** | `ruff check .` 报 **6797** 个错误；`ruff format --check` 要求重排 37/38 个文件 | lint job 恒红 ⇒ 等于没有门禁 | 配置对齐 + 修真实问题，见下 |
| **C4** | `mypy` 报 **147** 个错误 | type job 恒红 ⇒ 等于没有门禁 | 修至 **0**，见下 |

### C3：6797 个里 92% 是规则对中英混排的误报

`RUF001/002/003` 在中文全角标点（`，。：`）上触发 —— 而本仓库的注释与文档
**刻意**是中文。更隐蔽的是 `D415`/`D400`：ruff **不认识中文句号** `。`，会把
`做一件事。` 判为"应以句号结尾"，其自动修复会追加 ASCII 句点，产出 `。.`。

这不是代码有问题，是规则不适用于本仓库。处置：`pyproject.toml` 显式忽略这几类
并注明原因。剔除误报后剩 **546** 个分布在 39 个文件，逐类修掉：
`F401` 未用导入 17、`F841` 未用变量 17、`E702` 分号同行 12、`N816` 16 …

其中 17 个"未用变量"逐条核对过，确认是死代码而非潜伏缺陷 —— 例如
`dynamics.gamma` 的旧推导已被 `g_st`/`g_dy` 取代。删除后 134 条测试全绿。

### C4：147 个里 4 个是真正的类型谎言

`Config` 有四个字段声明为 `object = None`（`ra_out_noise_rms`、`ktc_kappa`、
`ktc_observe_bw_hz`、`adc2_dyn_bw_ratio`）。它们用 `None` 表示"自动推导"，
但 `object` 让每一个下游 `float(...)` 都失去检查。改为 `float | None` 后，
配合原有的 `is None` 守卫，mypy 自己就能收窄 —— 类型标注因此开始**表达意图**，
而不只是满足工具。

同理，`sadc.build_first_stage_quantizer(cfg, dac: object)` 掩盖了真实契约：
第一级其实只需要 `levels` 与 `_nominal_endpoints()`。改为结构化的
`SplitDacLike` Protocol（结构化而非具体导入，以免与 `dac_arch` 构成循环）。

### 保全证据：清理没有改变任何一个数

`tools/results/results.json` 与清理前**逐字节相同**，验收扫描 0 FAIL。

---

## 审计「值得保留的部分」→ 本仓库的对应守护

审计认可的内部机制，我们不但在重构中保留，且每条都有测试：

| 审计认可 | 本仓库的守护 |
|:---|:---|
| 固定失配与逐样本噪声分离 | `build_chip(cfg, mismatch_seed)` 固定 vs `rng` 逐样本 |
| 同一 `n_R` 的相关性 | KTC 支路沿用同一 `n_R`，`ktc.py` docstring 说明 |
| 名义 DEM 置换守恒 | `digital_core.verify_nominal_conservation()` |
| 信号/噪声/负载等效电容区分 | `c_active_nominal()` 等显式方法 |
| 噪声协方差与解析/MC 对照 | stage16 `noise_transfer` |
| rank / 零空间可观测性检查 | `calib.use_matrix` / `null_space_projection_error` |
| out-of-sample 校准验证 | stage14 / stage17 分离训练与验证输入 |

审计特别提到 stage17（桥接误差单独激励）：关噪声 92.018 → 1.2556 µV；
开噪声 100.498 → 40.3031 µV，接近理想地板 40.2868 µV。**但**它支持的是
"该假定网络、受限误差源和已知校准输入下的参数校正"，**不代表** 18-slice
全部电容权重与动态误差已联合校准 —— 这个限定被保留在
`docs/model_scope.md` 里。

---

## 落实审计的「最小修复顺序」

| 优先级 | 审计要求 | 状态 |
|:---|:---|:---|
| **P0** | 冻结目标与电压/码值单位；建立原架构基线；禁止开启 KTC；明确一级 9 位 | ✅ ADR 0003 + [ADR 0002](adr/0002-frozen-baseline-and-repo-split.md)；KTC 默认关 |
| **P0** | 修正 dither 码域换算及其测试 | ✅ ADR 0003 / F3；stage21 判据重写 |
| **P0** | 把 slice 状态绑定到物理阵列；采集后才转换；备用片给出合法机制 | ✅ ADR 0005；violations 8180 → 0 |
| **P1** | 参考与 RA 事件级联立（coarse/fine 切换 + RA 动态方程） | ❌ **未做**（见 A06） |
| **P1** | 独立校准与测量：训练/验证分离、保存真实 MC 原始点、拟合参数不再充当验证答案 | ⚠️ **部分**：MC 原始点已保留、训练/验证已分离；全码静态、跨频率失配敏感性**未做** |
| **P2** | 另分支研究 KTC：从相位节点方程推观测传递与噪声协方差 | ❌ **未做**（当前仅默认关闭 + 分级标注） |

---

## 附：本仓库对 v6.1 的数值对照

| 指标 | v6.1（冻结） | 本仓库 | 判据来源 |
|:---|--:|--:|:---|
| 一级判决电平 | 64 | **128** | ADR 0003 |
| Δ1 | 93.75 mV | **46.875 mV** | ADR 0003 |
| ADC2 | 15b [-0.3, 3.3] V | **14b [-0.15, 1.65] V** | ADR 0003 |
| slice 平均覆盖率 | 3.5572 / 8 | **8.0 / 8** | ADR 0005 |
| 跨周期违规 | 8180 | **0** | ADR 0005 |
| 8/18 选择对 DAC 误差影响 | 0.0 µV | **23.64 µV** | ADR 0005 |
| 粗粒度 dither 溢出率 | 32.67% | 由单位换算修正（原判据已废弃） | A03 |
| 近 Nyquist 观测器溢出率 | 49.63% | **0.00%** | ADR 0006 |
| 未分级的参数 | — | **0**（88 项全登记） | ADR 0007 |
| 对审计 11 条指控的回归测试 | 全部 FAIL | **全部 PASS** | `tests/audit/` |
