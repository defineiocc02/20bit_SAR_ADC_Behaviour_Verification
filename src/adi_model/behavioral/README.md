# `adi_model.behavioral` —— 采样、参考、AZ、RA、ADC2、噪声

| 功能 | 锚点 | 实现 |
|:---|:---|:---|
| 采样与采样噪声 | [`#sample`](#sample) | `sampler.py` |
| 参考（含动态建立） | [`#ref`](#ref) | `reference.py`、`reference_charge.py` |
| 普通 AZ | [`#az`](#az) | `ra_az.py` |
| RA 与 ADC2 | [`#ra`](#ra) | `ra.py`、`adc2.py` |
| TRACK / AUX | [`#track`](#track) | `../control/track_aux.py` |
| 噪声预算 | [`#noise`](#noise) | `noise.py`、`low_frequency_noise.py` |

标注：**【原文披露】**／**【依据原文推导】**／**【本工程实现】**／**【工程假设／未确认】**。

---

## 0. 这个模块解决什么问题

前级采样到的电荷不是理想值：采样开关有热噪声、参考要建立、RA 有有限增益和
建立过程、AZ 抵消失调、ADC2 有量化与带宽。这些效应决定 SNDR 的上限。

**当前最重要的结论**（本轮实测）：**SNDR 88 dB 不是热噪声限制的**。

| 条件 | SNDR | 说明 |
|:---|---:|:---|
| σ=0、噪声关 | 120.9 | 管线本身不是瓶颈 |
| **σ=0、噪声开** | **95.0** | 纯热噪声（kT/C + RA） |
| **σ=0.1%、噪声关** | **89.1** | **纯器件失配** |
| σ=0.1%、噪声关、确定性轮转 | 90.4 | 去掉随机换片 |
| σ=0.1%、噪声开 | 88.1 | 两者叠加 |

热噪声单独到 95.0 dB，与论文 93.7 dB 同量级；**失配比它低 6 dB，是主因**。
随机换片再吃 1.4 dB。数据：`docs/reviews/20261113_p3p4/data/sndr_decomposition.json`。

---

<a id="sample"></a>
## 1. 采样

【本工程实现】`capture()` 产生 `x_sadc` 与 RDAC 采样输入；采样噪声用
`Ceq = Cnorm²/Σ(Cm+β²Cs)`，不是 `Cnorm`。

【工程假设／未确认】`chi=2`（差分噪声系数）、300 K、`sampling_tau_mismatch`
为建模假设，非器件数据。

---

<a id="ref"></a>
## 2. 参考

【原文披露】论文 Fig.9.8.3 与 PPT：转换阶段由粗参考供电荷、RA 阶段用精参考；
RA 开始时参考尚未达到最终精度。

【本工程实现】`reference_charge.py` 逐事件电荷程序 + `reference.py` 一阶建立。

【工程假设／未确认】`tau_ref` 由论文 65% 建立点**反解**得到——这是
"校准到目标的模型参数"，其曲线只能证明自洽，**不能作为独立复现证据**。

**原件核对（2026-11-12，US10826519B1 FIG.5，Sheet 5/16，图存
`docs/mechanisms/figures/US10826519B1_FIG5_reference_buffer.jpg`）**：
该专利的实施例是一个**有源参考缓冲器**——MP1/MP2/MP3 电流镜、MN1/MN2 输入对、
MN3 偏置、一个反相器，右侧控制块带 `threshold_adjust` 与 `trial<N:1>`，
输出 `Vref_internal`，输入 `Vref`。

**必须说清的区别**：这份专利讲的是"怎么产生并微调一个内部参考电压"，
不是"转换阶段用粗参考、RA 阶段切精参考"。后者来自论文 Fig.9.8.3 与 PPT。
**两者不能合并成一个方案**——本模型的"粗/精切换"只有论文依据，
US10826519B1 的那套 buffer 与 `trial` 控制**未实现**。

---

<a id="az"></a>
## 3. 普通 AZ

【本工程实现】逐笔自零：本笔保存失调、放大时扣除；保持误差按
`read_time − az_time` 计，不随记录绝对时间增长。

【已验证】`tests/unit/test_ra_az.py`：保持时间从本笔 AZ 时刻算起；N 笔进 N 笔出，
不引入 CAAZ 的配对或二抽一。

【工程假设／未确认】PPT 的 "−1.6 dB 代价 / +1.3 dB 改善" 没有同条件参数支撑，
**未强行凑**。

---

<a id="ra"></a>
## 4. RA 与 ADC2

【原文披露】32× 残差放大、12 位后端、动态采样带宽（PPT 35 页）。

【本工程实现】`ra.evaluate`：`vra = G·residue + n_ra`，单极点可选；
`adc2.quantize_codes`：12 位 mid-rise，码仓中心 ties-to-even。

【未验证】动态 ADC2 宽/窄带只跑过波形，**没有出噪声指标**——没有噪声注入对照，
不能声称降噪。

---

<a id="track"></a>
## 5. TRACK 与 AUX

【原文披露】US10707889B1：采集前用其他 ADC 已可用的转换信息预置；
US10541702B1 FIG.2：辅助输入为背栅寄生节点提供充电路径。

【本工程实现】`control/track_aux.py`：电荷记账 + 时间因果判据
`t_source_ready + t_control + t_precharge ≤ t_deadline`；不是 `id < id`。

【已验证】最小回看 2 笔由时间判据推出，`lookback=1` 被否；输入电荷 39.05→12.2 pC。
**【工程假设／未确认】** `lookback=2` 是**本配置**的结果，不是专利普遍要求。

【未实现】AUX 支路未接；只做输出侧电荷记账，没有真实注入网络。

---

<a id="noise"></a>
## 6. 噪声预算与归因

【本工程实现】`ra_noise_rms_v = 1 mV`（RA 输出节点，默认值）、采样 kT/C、
驱动噪声、低频噪声。

【工程假设／未确认】1 mV 是"取整的噪声预算"，不是从测量反推的分量。

**归因方法**：不同条件不能直接比残差。本项目曾把"A2−A1 功率差"当热噪声，
那是对的（只翻噪声开关）；但把"DEM 开/关的残差差"当噪声是错的（同时改了状态分布）。

---

## 7. 缺口

| 项 | 状态 |
|:---|:---|
| 共享 RA 跨笔状态 | **未实现**（每笔理想初态） |
| 动态参考进主链 | 未接（A7 NOT_RUN） |
| ADC2 宽/窄带降噪 | 未验证 |
| AUX 支路 | 未实现 |
| 参考逐事件峰值负载 | 未验证 |

```bash
PYTHONPATH=src python3 -m pytest tests/unit/test_ra_az.py -q
PYTHONPATH=src python3 -m pytest tests/unit/kernels/ -q
```
