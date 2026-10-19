# 第 3 轮审查裁决：论文（ISSCC 2024 Session 9.8）

> 日期：2026-10-19 · 对象：`ad4080_rtl/rtl/`
> 资料：`[00]` 论文（有文本层，已逐段核对）与 `[00_1]` 讲稿（有文本层）。
> 原则：论文里每一条"数字可实现"的机制，必须落到（a）本 RTL 实现、
> （b）行为模型 `src/` 实现、（c）模拟核，或 [待确认]。

---

## 总表

| 论文机制 | 论文依据 | 归属 | 本包实现 |
|---|---|---|---|
| 18-slice 池、A/B ping-pong、causal 8/18 shuffle | Fig.9.8.1, "shuffler is not in the critical timing path" | 模拟交织前端 | (b) `src/scheduler.py` |
| 3 维 DEM（6b，二维自由度） | "overall DEM range becomes two-dimensional, with 6b total range" | 模拟核开关驱动 | (b) `src/dem*.py` |
| dither 与 DEM 互补，量化器→RDAC 转移时 dither 范围增强 2b | "Dither is used to complement the DEM linearization...enhanced by 2b" | 模拟核采样 | (b) `src/dither*.py` |
| 交织失配（offset/gain/timing/bandwidth） | "The obvious drawback of the interleaving comes from the offset, gain, timing and bandwidth mismatch artefacts" | 模拟核 | (b) `src/`（timing 靠 matched clocking，非数字校准） |
| 片上 offset/gain 校准（引用 [8] JSSC 2018） | references | **数字域** | (a) `sys_error_corr.sv` + `cal_two_point.sv` |
| 数字滤波 SINC1/SINC5/SINC5+comp | 手册 p.64-70（论文未展开） | **数字域** | (a) `sinc1/sinc5/sinc5_comp` |
| 16K FIFO + 触发模式 | 手册 p.57-63 | **数字域** | (a) `fifo_capture`/`result_fifo` |
| SPI/LVDS/GPIO/事件检测 | 手册 p.33-56 | **数字域** | (a) 顶层各模块 |

---

## 关键判定

### PP-1. "matched clocking scheme" 不是数字校准

论文把时序失配的抑制写成版图/时钟匹配 + shuffle 随机化（杂散落在
f_S/2−f_IN）。因此**本 RTL 没有、也不该有 timing-mismatch 数字校准模块**。
这一点与之前报告一致，且由论文原文支撑。

### PP-2. 论文的 DEM/dither 全部在模拟核，本 RTL 不含是正确边界

论文明确说 shuffler 不在关键时序路径上、DEM 与 dither 配合作用于 DAC。
这些都属于开关驱动/采样时序，是模拟核行为。本数字子系统只做手册定义的
配置接口、滤波、FIFO、校正、事件/GPIO/LVDS。**不实现 DEM/dither 不构成缺陷。**

### PP-3. 论文的"校准"是模拟前台校准，与 0x25-0x28 的逐样本 y=m·x+c 是两回事

- 论文引用 [8] 的 on-chip calibration 是模拟前台校准（测两点、写寄存器）。
- 本 RTL 的 `cal_two_point.sv` 实现的是**两点测量折算**（前台校准的数字侧），
  `sys_error_corr.sv` 实现的是**逐样本应用**。
- 两者合起来正好覆盖"校准流程的数字部分"；不存在"后台 LMS 单位权重校准"
  这种论文/手册都没写的东西，本 RTL 不臆造。

---

## 指标对照（论文 vs 本包模型）

| 指标 | 论文 | 本包行为模型 | 数字 RTL |
|---|---|---|---|
| SNDR | 94.2 dB DR / 91.7 dB SNDR（PPT/论文） | 93.5-94.3 dB（含 KTC 口径） | 不直接产生 SNDR（数字不引入噪声） |
| 采样率 | 40 MS/s | 同 | 数字按 valid_in 节奏 |
| 位宽 | 20 b | 同 | 同 |
| 校准 | 前台 offset/gain | 同 | 0x25-0x28 逐样本应用 |

数字 RTL 本身不产生 SNDR；它的作用是**不劣化**模拟核给出的码。本包的
位宽/符号/饱和策略（全链路 20-bit 补码、饱和并置 clip）保证这一点。

---

## 因论文审查需要补的 RTL/文档清单

| 优先级 | 事项 | 状态 |
|---|---|---|
| P2 | 论文 Fig.9.8.3 的"二维 DEM + dither 转移"在行为模型里的对照表 | 已在 `results/MODEL_REPORT.md` |
| P2 | 把"timing mismatch 不是数字校准"写进交付 README | 已做 |

**本轮没有发现需要改 RTL 的论文级差异。**
