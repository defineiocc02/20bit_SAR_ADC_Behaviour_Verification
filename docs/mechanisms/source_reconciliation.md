# 原文判读纠正记录（source reconciliation）

基线 `9beca9f`；本文只记录**我此前判读错的地方**、直接证据、修正结论与影响。

> 取证条件：本地 PDF 无文本层，`patents.google.com` 在本机网络下 curl 502 / WebFetch 失败，
> 因此改用**扫描正文页逐页读图**。下文每条都标了页/栏，可复查。

---

## R1 【撤回】FIG.20 的 `−1` 与"保留列接负轨"

**旧说法**（我上一轮写的）："MX1 的一个输入是 `−1`，把某列接到负轨"。

**复查结论**：那条读法不成立。MUX 旁的 `0/1` 是**选择控制位**，不是数据；
`352` 的保留输入与 `350` 的跨片数据经 MUX 配合，**不是把某一列永久接负轨**。
在正文中也没有找到"negative rail column"的支持句。

**处理**：删除 `docs/mechanisms/figures/US10511316B2_FIG20_reserved_column_mux.jpg`
所配的"接 `−1` 到真实列"解释；保留图本身（图是原件，解释是我加的）。

**待证**：保留输入最终承载什么，需要正文对应段落；本轮未定位到，标 UNRESOLVED。

---

## R2 【修正】`362` 是 signal gate，不是大型 crossbar

**旧说法**："FIG.19 有跨片 crossbar 362，实现里没有"。

**复查结论**：`362` 在正文语境里是**信号门**（可用门逻辑或 MUX 实现），
不能按图形外观要求"新增任意连接的大型 crossbar"。

**处理**：把 `dem_fig11.md` 里"缺 crossbar"改为"缺 `362` 的信号门功能"，
并明确它只需要实现**原文选定逻辑**，不要求通用互连。

---

## R3 【修正】`fig21_bridge.py` 已有保留列候选，不能说"全库没有"

`control/fig21_bridge.py::map_fig21_bridge` 已含 `reserved_column`、`vertical_slot`、
`correction_q32`。我此前笼统写"保留列未实现"是不准确的。

**正确表述**：已有候选实现，**未接主链、未做独立证明**；下一步是审计其几何、
采样条件、名义补偿与调用方，而不是再写第四套。

---

## R4 【修正】dither 的 `D_A` 不构成"实现不完整"

正文明确讨论 **A=6** 与**七切片示例**。图上写 `D_A` 是"可取位数"的意思。
六位实现本身没问题；真正要对上的是**七片示例 ↔ 八片活动组 ↔ 物理权重**的映射。

---

## R5 【未决】dither `224` 的极性必须逐端口读

图上门极 `224` 的**两个输入符号相反**（ADC 输出支路标负、dither 支路标正）。
我此前"看到 300/222 是 +，就把模型统一改成 subtract"是**只改一个号的错误做法**。

**处理**：建立极性账本（输入坐标→注入电荷方向→SADC 原码→224 两输入→RDAC 命令→
RA 极性→ADC2 码制→最终去扰项），整体符号变换允许等价，但必须有完整映射。
账本未建完前，`ASSUMED_SUBTRACTIVE` 保持原状并标注"未验证"。

---

## R6 【重要，与删除指令冲突】专利正文明确披露 63 单元阵列

**这是本轮最需要你确认的一条。**

US10511316B2 第 23 栏原文（页 43，左栏）：

> "For example, the sampling DACs 210.1 to 210.**n** were all built the same, and the
> residue DAC has **8 slices** summing to **40 pF** (for noise purposes) then each slice
> would have a capacitance of **5 pF**. This 5 pF is split up between, say, **31 unit
> cells in a 5 bit example or 63 unit cells in a 6 bit example** of a sampling DAC
> array. This gives a **unit capacitor size of 161 fF in the 5 bit case or 79 fF in
> the 6 bit case**."

同栏紧接着：

> "each slice may present a capacitance of **3.2 pF**, but the total D2A converter
> presents for the purposes of thermal noise amounts to **25.6 pF**"

### 含义

1. **63 单元（6 bit）是原文明确披露的采样 DAC 结构**，不是工程臆造。
   现有 `63+8` 里的 63，与这段是同一数量级、同一读法。
2. 原文同时给出**至少三组不同数值**：40 pF/8 片、5 pF/片、3.2 pF/片/25.6 pF；
   论文给的是 **20.5 pF**。这些是**不同实施例**，必须并列记录，不能合并。
3. 因此"把 63+8 从工作树彻底删除"会同时删掉一个**原文披露的结构**。

### ⚠️ 裁决状态（2026-11-13 补记：**已裁决**）

用户的裁定是**选项 B 的加强版**：63+8 工程替代模型**整体否决**，
主链统一到专利 [11] FIG.11/19/20 的 **17/5** 阵列（见 `HANDOVER.md` §1 第 3 条）。

因此本节的"选项 A"（保留 63 单元作为采样 DAC 实现）**未被采纳**。
保留下文的推理过程，是为了说明"63 是原文披露的结构"这一事实本身成立——
但工程上选择**不复刻该实施例**，而不是因为它不存在。

### 原建议（历史记录，需你裁决 → 已按上文裁决）

| 选项 | 含义 |
|:---|:---|
| A | 保留 63 单元作为**采样 DAC 的唯一实现**，Fig.19 的 15×2C+2×1C 作为**DEM 阵列**——两者本就不在同一层，不是"替代关系" |
| B | 若你认为芯片只采用 Fig.19 那套，则按指令删除 63 单元，但需在报告中注明"删除了原文 col.23 披露的 6-bit/63-cell 实施例" |

我倾向 A：它同时满足"不臆造"与"不删除披露内容"。但这是你的决定。

---

## R7 已有的数值修订（与删除指令无关，独立成立）

放大 FIG.19 主阵列后确认：跨桥之后是 **8C/4C/2C/1C 四个**电容，
现有 `SUB_WEIGHTS = [8,4,2,1,1]` 的末位是把**桥接电容**误当成子阵列单元。
按 `main=32C / sub=15C / bridge=1C / β=1/16` 复算，端点比值 **1.000000**（精确），
`512 × 1/16 C = 32C = main` 整除。

这条与 R6 互相独立：无论走 A 还是 B，`SUB_WEIGHTS` 的 5 项都应改 4 项。

---

## 影响清单

| 文件 | 需要改 |
|:---|:---|
| `docs/mechanisms/dem_fig11.md` | 撤 R1、改 R2、补 R3 |
| `docs/mechanisms/source_crosscheck_20261112.md` | 同上；T04 结论由 PASS(图) 改为 PARTIAL |
| `docs/decisions/20261112_fig11_unification.md` | 增加 R6 的裁决分支 |
| `src/adi_model/topology/capacitors.py` | `SUB_WEIGHTS` 5→4（R7） |
| `calibration/mask_calibration.py` | 符号/分母/活动掩码（本轮未动，见 S1） |

## 仍未解决的取证问题

* 保留输入的正文段落（R1）未定位；
* `362` 的实现形式（门/MUX）原文只给功能，不给结构；
* 三组电容数值分别对应哪个实施例，需通读全文才能分派。
