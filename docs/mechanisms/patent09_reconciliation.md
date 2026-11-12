# [09] US10516408B2 逐页正文核对（2026-11-13 首次完成）

> 证据层级：**原件正文逐段读图**。该 PDF 全 33 页 `get_text()` 为 0 字符（整册无文本层），
> 但第 20–32 页是**清晰可读的正文印刷页**（第 1–19 页是图页）。
> 本轮按页读完了 Overview 段与 FIG.1 的逐比特判读描述，下列引文均为逐字抄录。

---

## 1. 已核对到的正文（逐字）

| # | 位置 | 原文 | 对模型的意义 |
|:--:|:---|:---|:---|
| 1 | p.20 右栏 | "FIG. 10 is a schematic diagram showing further modifications to the arrangement shown in FIG. 7 where the **residue amplifier is omitted**" | 存在"无 RA"的实施例；本工程必须有 RA，属**选择其一**，不是遗漏 |
| 2 | p.20 右栏 | "FIG. 11 ... where **a second stage ADC is arranged to control the DAC slices of the first stage**" | 二级 ADC 反向控制一级 DAC 切片；本工程的 RDAC follower 正是该结构 |
| 3 | p.20 左栏 | "FIG. 2 ... a two stage DAC where a first stage processes the **more significant bits** ... and the second stage processes the **least significant bits**" | 两级 DAC 位分配；与 17/5 的"主段 MSB + 子阵列 LSB"一致 |
| 4 | p.21 左栏 | "...it is provided a **sliced DAC comprising a plurality of substantially identical switched capacitor sampling DACs adapted to be connected in parallel** to form a composite sampling DAC output **having reduced sampling thermal noise compared to the thermal noise of any single slice**" | 切片并联降热噪声——这是 18 片池的**噪声学理由**，模型里对应 kT/C 的 C_norm 按并联计 |
| 5 | p.21 左栏 | "the sampling DAC stages **do not participate in the analog to digital conversion**, for example they do not participate in bit trials of a successive approximation converter ... **The sampling DAC stages may be set bit by bit, or bits may be set in groups so as to reduce transient flows**" | 采样切片不参与位试验，只作 DAC 复现；"逐位或成组置位以减小瞬态电流"——对应 RDAC follower 的逐事件置位 |
| 6 | p.22 右栏 | "a further approach to providing redundancy is to change the **'radix'** of the capacitors ... from 2 ... to a **smaller number such as 1.8**. Thus the ratio of one capacitor to its neighbor becomes 1.8 rather than 2. This inserts redundancy into the array such that an **incorrect bit decision can be corrected** as the conversion process proceeds." | radix<2 引入冗余；本模型目前 radix=2（`coarse & ~clear` 的二进制前缀表），**无冗余** |
| 7 | p.22 右栏 | "...by using a **radix less than 2**, but then the **switch sequence needs to be examined and further converted into a binary word**." | radix<2 需要额外的"序列→二进制码"转换层 |
| 8 | p.22 左栏 | "the switched capacitor array ... is typically **terminated by an additional terminating capacitor having a value equal to the least significant capacitor**" | 终止电容 = 最小权电容 |
| 9 | p.22 左栏 | "redundancy is implemented so as to allow errors of **either sign** ... to be corrected" | 冗余要能纠正双向错误 |

## 2. 与当前模型的对照

| 结构 | 原件 | 本模型 | 状态 |
|:---|:---|:---|:---:|
| 两级 DAC 位分配（MSB 段 / LSB 段） | FIG.2 + p.20 | 主段 5 位 + 子阵列 4 位 | ✅ |
| 切片并联降热噪声 | p.21 | `C_norm` 按 8 片并联计 | ✅ |
| 采样切片不参与位试验、按事件置位 | p.21 | `rdac_drv` / follower 逐事件 | ✅ |
| 二级 ADC 反向控制一级 DAC | FIG.11 + p.20 | RDAC follower | ✅ |
| **radix<2 冗余** | p.22 | **无（radix=2）** | ❌ 缺口 |
| 终止电容 | p.22 | 子阵列第 5 个 1C 哑单元（接低轨） | ⚠️ 形态不同 |

## 3. 本轮落地：冗余判据模块（不进主链）

新增 `src/adi_model/control/redundancy.py`，把第 6/7/8/9 条变成**可复算的判据**：

```python
radix_weights(radix, n_bits)            # 权重序列（最高位在前）
has_single_error_margin(weights, include_terminator=...)   # 单错余量判据
sequence_to_binary_map(weights)         # 开关序列 → 二进制码（单调化）
```

实测（4 位阵列）：

| radix | 唯一码/16 | 重复码 | 单错余量（含终止电容） |
|:---:|:---:|:---:|:---:|
| 2.00 | 16 | 0 | 需要终止电容才满足 |
| 1.90 | 16 | 0 | 满足 |
| **1.80** | **13** | **3** | 满足 |
| 1.70 | 12 | 4 | 满足 |

`radix=1.8` 起出现重复码（=冗余代价），与正文"inserts redundancy"一致；
`radix=2` 无重复码，与"纯二进制无冗余"一致。**这两条互为反向检验**，
由 `tests/unit/test_redundancy.py` 钉住。

## 4. 明确不做的

* **不把 1.8 当成这颗芯片的披露值**：正文写的是 "such as 1.8"（举例），
  且没有给出本芯片的电容序列、纠错译码表或 radix 选取依据；
* **不改主链位分配**：接入 r<2 冗余会同时改变码本律与 RTL 前缀表，
  属于架构级变更，需要单独的验收（当前主线尚未闭环 dither，不宜叠加）；
* **不声称"已实现冗余"**：本轮只交付判据与映射工具，主链仍是 radix=2。

## 5. 补齐说明

至此六份专利（[09]–[14]）**均已完成至少一轮原件核对**：
[09] 本轮（正文 p.20–22 + 图 1–19 清单）、[10] FIG.19/20 + 摘要页正文、
[11] FIG.11/16/17/19/20/21 + col.23 正文、[12]/[13]/[14] 图。
