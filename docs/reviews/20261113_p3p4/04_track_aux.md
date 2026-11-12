# P3-4 TRACK 预置与 AUX 辅助输入：输入电荷记账

> 论文（§9.8 p.2）："the input current is linear with 350 μA_PP magnitude, allowing
> low-power drivers, normally used for sub-10MS/s ADCs"；"the ADC input filter bandwidth is
> reduced by a factor of ~2.5× due to full-period input signal acquisition"。
> 专利：US10797889（交织/预跟踪）、US10541702（auxiliary input for ADC input charge）。

代码：`src/adi_model/track_aux.py`；测试：`tests/unit/test_track_aux.py`（7 项）；
证据：`data/p3_mechanisms.json` → `track`。

---

## 1. 机制定义（本模型实现的部分）

| 机制 | 电荷从哪来 | 本模型算什么 |
|:---|:---|:---|
| TRACK | 空闲片底板按上一笔已决码预置，电荷来自参考轨/预充缓冲 | `|C_i(x[n] − x[n−1])|` 的实际逐片电容求和 |
| AUX | 独立辅助输入副本承担大部分电荷 | 与 TRACK 同一电荷式，只是来源换成 aux 缓冲 |

两者都只做**电荷记账**：不建运放、不积分 RC、不声称建立时间。

## 2. 因果性

| 检查 | 结果 |
|:---|:---:|
| 有合法来源的笔数 | 4 095 / 4 096（第 0 笔无来源） |
| `future_sources`（来源 ≥ 目标） | **0** |
| `self_sources`（来源 = 目标） | **0** |
| 偷看目标自身（反例） | ✅ 被 `cheating=True` 抓住 |

前 1 笔 `valid=False`、预测值 NaN，不允许用 0 V 冒充一次真实预置——那会把电荷算小。

## 3. 输入电荷实测（4096 笔 1 MHz 满幅正弦）

| 量 | 值 |
|:---|:---|
| 采样总电容（8 片合计） | **20.50 pF** |
| 无预置时平均 abs(dQ)/笔 | 39.05 pC |
| 有 TRACK 时 | **6.14 pC** |
| 电荷降低倍数 | **6.36×** |
| 1 MHz 下的理论相邻样本差 | 0.4708 V_pp → 9.65 pC_pp → **386 μA_pp** |
| 论文输入电流 | 350 μA_pp |
| 反解：350 μA_pp 对应频率 | **0.907 MHz** |

读法：论文没有给这个 350 μA 的测量频率。用**同一 20.5 pF、±3 V、40 MS/s** 反解，
它对应约 0.91 MHz 的相邻样本差分电流；在 1 MHz 下本模型给 386 μA_pp，差 10%。
这是一致性核对，不是验证。

**必须用精确式**：相邻样本差是 `2A·sin(πf/fs)`，不是 `2πfA/fs`。在 f/fs→0.5 时后者高估 57%，
用它去对 350 μA 会得到错误的频率结论（测试 `test_consecutive_sample_charge_matches_direct_difference`
把这个差别钉住）。

## 4. 与论文 "input filter bandwidth ×2.5" 的关系

全周期采集把 `T_ACQ` 从 25% 提到 100% `T_CLK`，允许的抗混叠带宽按同样比例放宽。
本模型能算的是**电荷**（§3），不能算**滤波器带宽**——那需要外部 RC 与驱动网络参数。
所以本条只列机制、不列数字。

---

## 5. 论文机制对照

| 论文/专利说法 | 本模型状态 | 证据 |
|:---|:---|:---|
| 输入电流 350 μA_pp | ⚠️ 同量级（1 MHz 下 386 μA_pp），频率口径未披露 | §3 |
| 全周期采集（8 采 / 8 转 / 2 闲） | ✅ 计划层面（`round_robin_plan`，相邻窗互斥） | P3-2 §3 |
| 预跟踪用"另一 ADC 的最近判决" | ✅ 来源严格早于目标，参考 `pretracking.py` 的历史深度接口 | §2 |
| AUX 独立源阻抗 | ❌ 未接：`input_network.py` 需要独立的 aux 波形与源阻抗参数 | — |

## 6. 不能声称

* 不能声称 TRACK/AUX 已进入主 `pipeline` 的逐笔循环——本轮只在电荷域完成记账与因果审计。
* 不能声称输入电流"复现"了 350 μA：论文没给测量频率，本模型只是给出对应频率。
* 不能声称驱动功耗：电荷×fs 是电流下界估计，不含缓冲静态电流与建立开销。
* 不能声称 AUX 的源阻抗/噪声已建模：那需要独立的 aux 支路参数，目前没有。
