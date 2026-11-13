# `adi_model.control` —— DEM、dither、follower 与调度

本包管四件事，各自有独立章节与锚点：

| 功能 | 锚点 | 主要实现 |
|:---|:---|:---|
| DEM 与跨段编码 | [`#dem`](#dem) | `fig11_dem.map_fig11`、`dem3d.fig11_masks`（`unary_rotation_masks` 已删除） |
| 分路 dither 与数字去扰 | [`#dither`](#dither) | `dither.Fig19DitherProgram`、`dither_port.DitherPort` |
| SADC/RDAC 分离与 follower | [`#follower`](#follower) | `follower.py` |
| 调度与采集/转换交接 | [`#schedule`](#schedule) | `dem3d.round_robin_plan`、`simulation/slice_pool_ctrl`（RTL 侧） |

标注约定，全文统一：

* **【原文披露】** 原件明确画/写出的连接、参数、结果
* **【依据原文推导】** 在列明假设后由原文结构推出
* **【本工程实现】** 当前源码实际做的事
* **【工程假设／未确认】** 原文没有，或本轮未核实

原件取证条件：六份专利为**扫描图像 PDF，无文本层**；`patents.google.com`
在本机网络下 curl 502、WebFetch 失败，因此正文靠**逐页读扫描页图**核对。
下文每条都标到 Sheet 号或栏号，可复查。

---

<a id="dem"></a>
## 1. DEM 与跨段编码（US10511316B2 FIG.19 / FIG.20）

### 1.1 这个模块解决什么问题，做到哪一步

元件失配让每个物理单元的权重偏离名义值。若每次转换都用同一组单元，
失配表现为固定的传递非线性（INL/DNL 台阶）。DEM 通过逐笔改变"哪些单元参与"
把固定误差变成随样本变化的误差，再用平均压下去。

**当前状态**：15×2C+2×1C 的掩码映射已实现并在 `fig11_masks` 里可调用；
**保留位置与跨片分配未实现**；主链已统一到 `fig11_masks`（17/5），旧的 63+8
掩码路径已删除。子阵列的哑元端接**尚未进网络**，因此 β 仍按 1/16 计算，
应为 1/17——这是当前最明确的已知物理偏差，见 §1.7。

### 1.2 原图逐块（FIG.19，Sheet 17/29）

```
[15 × 2C]   B4=C   S4=C  ──桥 C──  8C   4C   2C   C
  B0-B3                            B5   B6   B7  B8
        ↘ UNARY ENCODER 306 → shuffler 350 ┐
                     shuffler 352（15×2C）→ 元件 362 → DS1…DS8
```

| 原图编号 | 原文中的含义 | 输入 | 输出／连接 | 工作阶段 | 本工程对应 |
|:---|:---|:---|:---|:---|:---|
| B0–B3 | 15 个 2C 一元电容 | 一元码 `thermal` | 底板接高/低参考 | 转换 | `MAIN_WEIGHTS[:15]` |
| B4 | 1C，独立控制位 | 码位 | 底板 | 转换 | `MAIN_WEIGHTS[15]` |
| S4 | 第二个 1C | 码位 | 底板 | 转换 | `MAIN_WEIGHTS[16]` |
| 桥 C | 跨段衰减 | 主节点 | 子节点 | 全程 | `BRIDGE_CAP_UNITS` |
| B5–B8 | 8C/4C/2C/1C 二进制 | 低 4 位 | 子节点底板 | 转换 | `SUB_WEIGHTS` |
| 306 | 一元编码器 | 二进制码 | 一元码 | 转换前 | `map_fig11` 内 |
| 350 / 352 | 两级 shuffler | 一元码 | 打乱后的列 | 转换前 | **未实现**（现为逐片旋转） |
| 362 | **信号门** | 两 shuffler 输出 | 送到 DS 列 | 转换前 | **未实现** |

**362 是 signal gate，不是大型 crossbar**——正文只给功能，不给实现结构，
所以不能按图形外观要求"任意互连"。这是本轮对上一版说法的修正。

### 1.3 保留位置（FIG.20，Sheet 18/29）

FIG.20 里 `352` 有一个**独立的 `0` 输入**（预留位置），其输出经
**MX1–MX4** 与 `350` 的跨片数据配合；MUX 旁的 `0/1` 是**选择控制位，不是数据**。

【已撤回】上一版写的"MX1 的一个输入是 `−1`，把某列接到负轨"**不成立**，
原文没有"永久接负轨的列"这种说法。

【本工程实现】没有实现该机制。`fig11_masks` 里 `horizontal_phase=15` 映射到 0
只是**相位别名**，不等于保留位置。【工程假设／未确认】保留位置最终承载什么，
正文对应段落本轮未定位到。

### 1.4 核心关系与成立条件

【依据原文推导】名义驱动权重按图上结构：

```
main  = 15×2C + 1C(B4) + 1C(S4) = 32C
sub   = 8C + 4C + 2C + 1C       = 15C
bridge= 1C
beta  = C/(C+15C) = 1/16
LSB   = 1/16 C,  512 × 1/16 C = 32C = main   (整除)
unit_cap = 20.5 pF / [8×(32 + 15/16)] = 77.80 fF
```

校验：`group_cnorm = 8×(32+15/16) = 263.5000 C`，
`group_drive(511) = 8×32 + (1/16)×8×15 = 263.5000 C`，**比值 1.000000**。
端点落在 ±v_fs，说明这套读数是自洽的。

**修正记录**：`topology/capacitors.py` 原有 `SUB_WEIGHTS=[8,4,2,1,1]`，
末位那个 1C 是**把桥接电容误当成了子阵列单元**，导致 β 被算成 1/17、端点差 0.33%。
本轮已核对原图确认应为四项。**代码尚未改**（见 §1.7）。

### 1.5 代码定位

| 原文动作 | Python | 关键字段/shape | RTL | 主链调用 |
|:---|:---|:---|:---|:---|
| 二元→一元 + 跨段 | `fig11_dem.map_fig11` | `Fig11Masks.main/sub`，`[slot, unit]` | 未实现 | 否 |
| 掩码打包 | `dem3d.fig11_masks` | `SplitSwitchCommand` | `rdac_drv` | 否 |
| ~~逐片旋转~~ | ~~`dem3d.unary_rotation_masks`~~ | — | — | **已删除** |
| 逐物理片使用统计 | `dem3d._per_physical_slice_counts` | `[n_slices, n_unit]` | 无 | 审计 |

`fig11_masks` 强制 `geometry.n_main == 17 and n_sub == 5`，其它几何直接拒绝。

### 1.6 实验与反例

* **物理片统计反例**（`tests/unit/test_dem3d.py::test_physical_slice_audit_is_not_slot_aggregation`）：
  构造"每片只用 21/63、各片窗口不同"，旧的 `main_on.sum(axis=0)` 报 63/63 全覆盖，
  按 `slice_ids` 散列后报 21/63。**旧实现是假覆盖**。
* **β 广播轴**（`test_beta_expands_along_the_sub_unit_axis`）：本芯片恰好
  `n_sub == n_active == 8`，`beta[:, None]` 形状兼容但索引错，测试用两种维度组合钉住。

【未验证】保留位置、362 信号门、跨片分配都没有实验；不能声称"复现了 FIG.19"。

### 1.7 与原文的差异

| 原文 | 当前 | 影响 | 下一步 |
|:---|:---|:---|:---|
| 15×2C+2×1C 唯一阵列 | 已进主链（17/5） | 旧 63+8 性能数据不能作为证据 | 已作废，需按新拓扑重跑 |
| 保留位置 + MUX | 未实现 | 相位别名被误当保留列 | 按 FIG.20 实现 |
| 362 信号门 | 未实现 | 现为逐片旋转 | 实现门功能即可，不必 crossbar |
| `SUB_WEIGHTS` 4 项 | 代码仍是 5 项 | β 偏 6%、端点差 0.33% | 改常量 + 重跑 |

复现命令：

```bash
PYTHONPATH=src python3 -m pytest tests/unit/test_dem3d.py -q
PYTHONPATH=src python3 -m pytest tests/unit/test_fig11_vs_dem3d.py -q
```

---

<a id="dither"></a>
## 2. 分路 dither 与数字去扰（US10505561B2 FIG.19）

### 2.1 解决什么问题，做到哪一步

小信号时前级量化器的码分布集中，权重误差与量化台阶混在一起无法平均掉。
dither 在采样/判决路径上注入已知扰动，把码"打散"，再在数字域扣掉已知量。

**当前状态**：两组实现并存——`v1`（code-shift，`Fig19DitherProgram`）与
`v2`（独立电荷口，`DitherPort`）。v1 已进主链并有因果测试；v2 已进主链但
**分路因果未验证**。

### 2.2 原图逐端口（FIG.19，Sheet 20/21）

| 原图元件 | 接的是什么 | 本工程对应 | 一致？ |
|:---|:---|:---|:---|
| 400 DITHER GENERATOR | 输出 `D_A…D_1`（A 为可取位数） | 6 位字 | **部分**：正文讨论 A=6 |
| 300 加法器 → ADC 200 | **D5,D3,D2,D1** | `lower_v`（L 支路） | **分组一致** |
| 224 减法器 → DAC 220 控制 | **D6,D4** | `upper_code`（U 支路） | **分组一致** |
| 222 加法器 → DAC 220 输入 | 全字 | `x_rdac_input` | **分组一致** |

**极性必须逐端口读**：300 与 222 是加法、224 是减法，三者不在同一坐标。
【本工程实现】统一用 `subtract`，代码里标为 `ASSUMED_SUBTRACTIVE`——
**这是假设，不是原文披露**。不能因为看到 300 是"+"就批量把模型改成加法。

### 2.3 时序与阶段

| 阶段 | 动作 | 保存/释放 | 何时有效 |
|:---|:---|:---|:---|
| 采样 | 222 把全字加到采样 DAC 输入 | 采样电荷含 T | 采样结束 |
| 量化 | 300 把 L 加到量化器输入 | 无状态 | 判决瞬间 |
| 码生成 | 224 从 SAR 码减去 U | 码 `k=c−U` | 逐事件 |
| 数字 | 重构扣掉已知 T/L | 无状态 | 退休 |

### 2.4 核心关系

【依据原文推导】以粗码 LSB 为单位：`U = 4·D6 + D4`，`L = 2·D5 + (4·D3+2·D2+D1)/7`，
`T = U + L`；`x_SADC = x − L·Δ`、`k = c − U`、`r = x − T·Δ − B(c−U) = x − L·Δ − B(c)`。

【工程假设／未确认】分母 **7** 与"七切片示例"来自说明书正文，**正文为扫描件不可读，
本轮未核对**。八片活动组不能直接套 `/7`，也不能机械改 `/8`——这条没有解决。

### 2.5 代码定位

| 原文动作 | Python | 字段 | RTL | 主链 |
|:---|:---|:---|:---|:---|
| 字→U/L/T | `Fig19DitherProgram.upper_code/lower_v/total_v` | `words[6]` | 无 | 是 |
| 采样侧注入 | `sampler.capture(..., injected)` | `injected[V]` | 无 | 是（v1） |
| 电荷口注入（v2） | `dither_port.DitherPort.require_words` | `words` | 无 | 是（v2） |
| 数字去扰 | `pipeline_engine` 重构项 | `term` | `recon_core.inj_q` | 是 |

### 2.6 实测（本轮新增，零失配零噪声 DC 0.4 V）

```
word= 0   coarse=290  out=594193
word=32   coarse=290  out=594193   ← 只置 D6(upper)：SADC 判决不变
word=16   coarse=288  out=594193   ← 只置 D5(lower)：SADC 判决移动
word=48   coarse=288  out=594193
```

**在这条（v1）路径上分路成立**，与"upper 也移动 SADC"的说法不符。
三条测试钉住：`tests/unit/test_dither_split_causality.py`。

【未验证】**charge-port v2 没有做这个测试**。v2 把完整 `total_v` 传给采样函数，
理论上会让 SADC 与 RDAC 同时看到 `x−T`。若要判定 v2 是否丢失分路，
必须在 v2 上重跑同一组探针——这是本条最大的缺口。

### 2.7 差异与缺口

| 原文 | 当前 | 影响 |
|:---|:---|:---|
| 七片示例 + `/7` 权重 | 八片活动组，权重沿用 `/7` | 两组不可直接等效，未证明 |
| 物理 dither 电容/开关 | 无（理想电压/电荷注入） | 有限电容、参考负担、噪声都没进模型 |
| 224 双输入反号 | v1 统一 subtract | 极性账本未建 |

```bash
PYTHONPATH=src python3 -m pytest tests/unit/test_dither_split_causality.py -q
PYTHONPATH=src python3 -m pytest tests/unit/test_dither_charge_port.py -q
```

---

<a id="follower"></a>
## 3. SADC/RDAC 分离与 follower

**职责**：SADC 只产生判决；RDAC 按**已决前缀**逐事件加载，不复制"试探—撤销"。

| 项 | 内容 |
|:---|:---|
| 因果约束 | 事件 i 只能消费前 i 个判决；未来位不影响过去事件 |
| 实现 | `follower.py`；前缀表 `complete_flash3_sar6_prefix_table` |
| 验证 | 前缀截断不变性 28 672 检查 / 0 违约；同码三条路径最终电荷闭合 61.3 pC |
| 与原文差 | 参考电荷的逐事件**峰值负载**未验证；只证明了净电荷 |

【本工程实现】的 `lookback=2` 是时间判据结果，不是专利普遍要求。

---

<a id="schedule"></a>
## 4. 调度与采集/转换交接

| 项 | 内容 |
|:---|:---|
| 池 | 18 片，每笔 8 采集 / 8 转换 / 2 空闲 |
| 因果 | 采集组 = 上上笔转换组；相邻转换组互斥 |
| 实现 | `dem3d.round_robin_plan`；RTL `slice_pool_ctrl`（V1 已 100 拍逐字段对拍） |
| 身份 | 逻辑 slot 与物理 `slice_ids` 必须分开；失配跟物理片走 |
| 未验证 | 采样孔径、共享 RA 跨笔状态 |

---

## 5. 本包仍未关闭的问题

1. **子阵列哑元未进网络**：图面桥后是 8C/4C/2C/C/**C** 五个电容，
   代码只把前四个当可切换位、也没把第五个计入 cnorm，导致 β=1/16（应为 1/17）、
   cnorm 少 1/17 C。**改 β 会同时破掉重构的 x=0 精确性（实测偏 10.7 mV）**，
   必须与码本分母、解码器一起改，不能单独改这一个常数。
2. 保留位置、362 信号门未实现。
3. `SUB_WEIGHTS` 5 项 → 4 项未改。
4. dither v2 分路未验证。
5. 七片/八片等效性未证明。
