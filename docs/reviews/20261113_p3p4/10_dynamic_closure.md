# A7 全动态闭环：参数冻结、能跑通什么、不能声称什么

> 这是 P4 里唯一一条从 NOT_RUN 变成 RUN 的配置（A7）。核心结论先说：
> **动态路径能跑通并给出相位波形，但所有 R/C/BW/时刻都是我显式假设的，
> 其中 `tau_ref` 还是从论文"65% RA 相位达到 20b"反解出来的——
> 只能证明自洽，不能证明论文。**

代码：`tools/run_review_figures.py` 的 `_dynamic_config()` / `figure_dynamics()`；
图：`figures/F07_reference_ra_adc2.png`；数据：`figures/figures.json` 的 `F07*`。

---

## 1. 冻结的参数集（全部 ASSUMED）

| 参数 | 值 | 依据 | 等级 |
|:---|:---:|:---|:---:|
| `quantizer_time_s` | 6 ns | 论文"conversion ~7 ns"取整到 SADC 段 | ASSUMED |
| `ra_duration_s` | 12 ns | 剩余 25 ns 周期里给 RA/ADC2 12 ns | ASSUMED |
| `rdac_event_times_s` | 0.3/0.9/1.5/2.1/2.7/3.3/3.9 ns | 7 个已决事件均分 6 ns 量化窗 | ASSUMED |
| `reference_accurate_start_s` | 5.0 ns | 粗→精参考切换在 RA 前 1 ns | ASSUMED |
| `fine_reference_tau_s` | 1.5 ns | 未披露 | ASSUMED |
| `ra_response_model` | `single_pole` | 论文只说"integrating RA 不适配"，未给 BW | ASSUMED |
| `ra_bandwidth_hz` | 66 MHz | τ=2.4 ns，12 ns 内约 5τ | ASSUMED |
| `ra_dc_gain` / `ra_slew_v_s` | `ideal` / `unlimited` | 先隔离带宽，不叠增益误差 | ASSUMED |
| `adc2_response_model` | `wide_narrow` | slides："DAC BW set to 15× fNyq" | **推导**（15×20 MHz=300 MHz） |
| `adc2_wide_bandwidth_hz` | 300 MHz | 同上 | 推导 |
| `adc2_narrow_bandwidth_hz` | 150 MHz | 窄带值未披露，取一半 | ASSUMED |
| `adc2_narrow_start_s` | 10 ns | 必须在孔径 12 ns 之前 | ASSUMED |
| `dyn_c_decouple` | 1 nF | 未披露；只影响 droop 幅度 | ASSUMED |
| `dyn_tau_ref` | 0.56 ns | **由"65% RA 相位完成 20b"反解**：0.65×12 ns ÷ ln(2^20) | **反解** |

## 2. 跑通后得到什么（F07）

| 量 | 实测 |
|:---|:---|
| 相位时刻 | 5.0 / 6.0 / 16.0 / 18.0 ns（参考切换 / RA 入口 / ADC2 窄带 / 孔径） |
| RA 入口的参考误差 | **64.0 µV** = 21 ppm = **15.5 bit**（对 3 V 轨） |
| 孔径处的参考误差 | **0.0215 µV** = 7e-9 ≈ 27 bit |
| RA 相位内多建立的位数 | **11.5 bit** |
| RA 饱和 | 0 次 |

三条读法：

1. "RA 入口只有 ~15b、RA 相位内补到 20b 以上"与论文叙述**方向一致**；
2. 但 `tau_ref` 是按这句话反解的，所以这是**自洽检查**，不是独立验证；
3. 27 bit 的终值说明"参考不是 20b 的限制项"——这条与论文"参考不是瓶颈"一致，
   且不依赖 tau 的具体值（只要 tau 让它在 RA 内收敛）。

## 3. 跑了什么、没跑什么

| 项 | 状态 |
|:---|:---|
| `ConversionEngine` 消费逐事件参考程序 | ✅ 跑通（32 笔，无 RA 饱和） |
| 参考粗/精切换 + 建立 | ✅ 有波形（F07a） |
| RA 单极点 + ADC2 宽/窄带切换 | ✅ 有波形（F07b） |
| 与采样域白噪声组合 | ❌ **被模型拒绝**：`sample-domain white-noise composition with this nonideal conversion is not implemented`。动态配置只能零噪声跑 |
| 输出码/INL/SNDR | ❌ 动态路径下 `out` 全是 NaN——标量诊断子集不含状态化响应。要出指标必须先补"动态路径的数字重构" |
| 参数敏感性（tau_ref、C_dec、RA BW） | ❌ 未扫。一条曲线不能说明"参数选对了" |
| 全温度/电压角 | ❌ 未做 |

## 4. 论文对照：能声称与不能声称

**能声称**

* 这套显式参数下，`ConversionEngine` 能把"逐事件参考 → RA → ADC2"串起来跑，且不饱和；
* 参考在 RA 入口约 15.5 bit、RA 相位内补到 20 bit 以上，与论文的定性叙述一致。

**不能声称**

* 不能声称"复现了论文的 65%/20b 参考建立"——tau 是从这句话反解的，属于循环论证；
* 不能声称"参考不是瓶颈"是模型发现的——它只说明在我给的 tau 下不是；
* 不能声称动态配置的 INL/SNR/DR——输出还没有数字重构；
* 不能声称 ADC2 的 300/150 MHz 是论文值——只有 15× 是推出来的，窄带值是我编的。

## 5. 下一步（按依赖排序）

1. **动态路径的数字重构**：现在 `out` 为 NaN，等于动态配置出不了指标。这是 A7 从"能跑"
   变成"能比"的前提。
2. **参数敏感性**：`tau_ref` ×3、`C_dec` ×3、`RA BW` ×3，报告哪些结论对参数不敏感。
3. **与噪声组合**：模型目前直接拒绝，需要先定义"非理想转换下的噪声传递"口径，
   否则动态配置永远只能是零噪声演示。
