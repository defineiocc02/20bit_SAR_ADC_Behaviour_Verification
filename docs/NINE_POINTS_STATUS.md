# 论文九点披露：当前实现状态逐条核对

时间：2026-11-15
基准：`merge/adi-repro` 含 `out_en` 修复（本轮提交）
原文：ISSCC 2024 §9.8 论文 3 页正文
核对方：实际读当前 RTL 端口与 TB 判据，不凭记忆

---

## 总览

| # | 论文披露 | 状态 | 一句话 |
|:--:|:---|:---|:---|
| 1 | 18 片池动态选 8 片 | ✅ **已实现** | 三态机 + 掩码/片 ID 输出，统计验证 |
| 2 | 2 个备用片用于 shuffling | ✅ **已实现** | IDLE 恒 2 片，每拍在 10 候选中丢 2 |
| 3 | 两相位交织（采集占满周期） | ✅ **已实现** | `sched18` 接入，ACQ/CONV 并行 |
| 4 | 量化器与 RDAC 完全分离 | ⚪ **数字边界外** | 本 IP 只吃 k，不产生量化 |
| 5 | 第二个 flash 量化器共享 | ❌ **未实现** | 只有 `ev_flash_code_i` 输入口，无可共享量化器 |
| 6 | 前级 9b 量化 | ⚠️ **部分** | 能表达 0..511，但"是否严格 9b"口径未定 |
| 7 | RDAC follower 边产生边载入 | ✅ **已实现**（本轮修复） | k 与 mask 都随事件更新 8/8 |
| 8 | DEM 与 dither 联用 | ✅ **已实现** | 同拍算 mask 与 pre |
| 9 | 测量指标（INL/DNL/DR/噪声） | ❌ **数字端不可验证** | 需模拟闭环 |

统计：**已实现 5 项、部分 1 项、边界外 1 项、未实现 2 项。**

---

## 逐条核对

### 1. 18 片池动态选 8 片 —— ✅ 已实现

**论文原句**：
> "the RDAC is composed of 8 slice DACs (sDAC) converting the signal, and
> 8 sDACs acquiring the signal, **dynamically selected from a pool of 18 sDACs**"

**当前实现**：`rtl/sched18.sv`，18 位 ACQ/CONV/IDLE 三态机。
`d3d4_ip` 输出 5 个相关口：

```
acq_mask_o[17:0]   本拍采集组
conv_mask_o[17:0]  本拍转换组
idle_mask_o[17:0]  备用片
acq_ids_o[39:0]    采集组片号（升序，5 bit/个）
conv_ids_o[39:0]   转换组片号
```

**证据**：
- `tools/sched18_stats.py` 20000 拍：χ²=0.9（临界 27.587）、单片不平衡度 0.99%、
  结构不变量 0 违例
- `tb_sched18.sv`：4096 拍与 Python 模型逐位一致，0 错误
- `tb_d3d4_ip.sv`：每拍检查 ACQ∩CONV=∅、|ACQ|=|CONV|=8、|IDLE|=2、并集=18

**与原文的差异**：原文说"dynamically selected"，我用 xorshift 选片。
原文没说随机序列形式，这部分**无法逐位比对**，只证结构。

---

### 2. 2 个备用片用于 shuffling —— ✅ 已实现

**论文原句**：
> "Two spare sDACs are introduced to **allow shuffling of the sampling DACs**
> and spread the residual interleaving tones."

**当前实现**：`sched18` 每拍从 `avail = ~acq_q`（10 片）中丢 2 片进 IDLE，
剩余 8 片作为下一拍 ACQ。IDLE 恒为 2 片，由结构保证（不靠自检）。

**证据**：`tools/sched18_stats.py` 实测 IDLE 对覆盖 **153/153 = C(18,2) 满覆盖**，
频次 min=106 / max=162（期望 130.7）。

**未做的一条**：原文说"spread the residual interleaving tones"，
这是**频谱效果**。我只有结构覆盖证据，**没有做 FFT 验证交织音被摊开**。

---

### 3. 两相位交织 —— ✅ 已实现

**论文原句**：
> "**Two phase interleaving** of the RDAC extends the acquisition time to the
> full clock period, reducing the required sampling bandwidth by a factor
> of 2-4 times."

**当前实现**：`d3d4_ip` 内例化 `sched18`，与相位发生器共用 `adv`。
ACQ 组与 CONV 组同拍并行，`conv_q <= acq_q` 保证 CONV 是上一拍的 ACQ。

分工：
- **mask（RDAC 开关）服务 CONV 组** —— 转换期才需要 RDAC
- **pre（dither 采样预置）服务 ACQ 组** —— 采样期才需要预置

**证据**：`tb_d3d4_ip.sv` 每拍检查五条不变量（ACQ∩CONV=∅、各 8 片、IDLE=2、
并集=18），512 拍 0 错误。

**故障注入验证判据不是空壳**：把 `sched18` 的 `drop` 从"丢 2 片"改成"丢 1 片"，
TB 立刻报 `|ACQ|=9 != 8`。

**未做的一条**：原文说"reducing the required sampling bandwidth by a factor
of 2-4 times"。这是**带宽收益**，属于模拟侧，本 IP 无法验证。

---

### 4. 量化器与 RDAC 完全分离 —— ⚪ 数字边界外

**论文原句**：
> "the **quantizer is fully separated** from the residue generating DAC (RDAC)"

**为什么判"边界外"**：这是模拟架构决策（两个独立电路块）。
本 IP 的输入就是 `k_i`（RDAC 码），它不关心 k 是怎么量化出来的。
**验不验都不影响本 IP 的正确性**，但也不能说"我实现了分离"。

---

### 5. 第二个 flash 量化器共享 —— ❌ 未实现

**论文原句**：
> "a **second quantizer** has been introduced … The flash-ADC can be shared,
> thanks to a **<1ns return to acquisition** time"

**当前状态**：`d3d4_ip` 有 `ev_flash_code_i[2:0]` 输入口，但那只接收
3 位 Flash **结果**；没有任何模块**产生**这个结果，也没有"共享/回采集"逻辑。

仓库里有 `rtl/core/adc2_dec.sv`，但：
- 它不在 `files_d3d4_ip.f` 里
- 不在 `d3d4_ip` 的任何数据通路上

**所以这条是纯缺失。** 需要说明：这个量化器本质是模拟块（"built out of
one slice DAC"），数字端只能提供控制接口。要补的话，合理范围是补
"双量化器的仲裁与回采集时序控制"，不是补一个 SAR 引擎。

---

### 6. 前级 9b 量化 —— ⚠️ 部分

**论文原句**：
> "The matched-quantizer sDAC with the RDAC allows >11b matching,
> resulting in **9b quantization** in the first stage"

**当前实现**：
- `k_i` 是 **10 位**（0..1023），合同范围 0..520（0..511 + guard 512..520）
- `follower_rt` 的 `k_ev_o` 是 **9 位**（0..511），事件流模式下 **guard 不可达**
- 3b Flash + 6b SAR = 9b 的**分解**只在 `follower_rt` 的注释里描述，
  代码层面没有"3b flash 段 + 6b SAR 段"的显式分界

**为什么判"部分"**：
- 能表达 9b 码范围 ✓
- 但"9b 量化"暗示的是**量化精度**（>11b 匹配下的 9b 有效位），
  这需要模拟侧匹配数据才能验证 ✗
- 事件流模式下 guard 缺失是**已知缺口**（如实记录在 HANDOFF §5.0c）

---

### 7. RDAC follower 边产生边载入 —— ✅ 已实现（本轮修复）

**论文原句**：
> "The RDAC works as a **follower** of the quantizer with the conversion
> results **loaded as they develop**."

**这条我今天发现并修了一个真实缺陷**，过程值得记：

**第一版（有缺陷）**：我接入了 `follower_rt`，k 逐事件累加，TB 验证
`k_ev_o` 8/8 次严格增长，报 PASS。
**但 mask_o 没有随事件更新** —— 输出寄存的使能写的是 `valid_i && ready_o`，
而事件流模式下 `valid_i` 恒为 0。所以：

> k 在累加，但 **RDAC 开关命令（mask_o）纹丝不动**。

这就是"论文说要边产生边载入，我实现成了边产生边攒着"。
**表面 PASS，实际功能断裂。** 根因是 TB 只比了 `k_ev_o`，没比 `mask_o`。

**修复**：输出使能改为

```systemverilog
assign out_en = ev_mode_i ? (ev_bit_valid_i & reset_n)
                          : (valid_i & ready_o);
```

事件流模式下每个有效事件都触发一次 RDAC 更新。

**修复后证据**（TB 新增判据，同时比 k 与 mask）：

```
follower: k 逐事件累加 8/8 次 -> 边产生边载入成立
follower: mask_o 随事件变化 8/8 次 -> RDAC 开关逐事件更新成立
```

实测序列：k = 1 → 3 → 7 → 15 → 31 → 63 → 127 → 255 → 511，
每个事件 mask 都跟着变。

**注意 TB 计数踩过一个坑**：最初加 `prev_mask !== 0` 的守卫，
把第 0 个事件后的首次变化跳过了，只数到 7/8，误判为 RTL 有问题。
实际是 TB 的计数 bug。

**仍缺的**：论文列的六条好处（reference current 1.8mA、switch 更小、
译码时序不关键……）都是**模拟侧收益**，数字端无法验证。

---

### 8. DEM 与 dither 联用 —— ✅ 已实现

**论文原句**：
> "a **dynamic-element-matching algorithm can be used in conjunction with
> dither**, as the shuffler is **not in the critical timing path**."

**前半句（联用）**：✅ `d3d4_ip` 同一拍内同时算 `mask_o`（DEM）与 `pre_o`（dither），
两者共用相位但独立生成。512 拍 0 失配，门级等价也 0 失配。

**后半句（不在关键路径）**：❌ **不成立。** 我的综合结果显示
40 级逻辑、2.82 ns 关键路径，路径是
`相位 → dem_mask_v3 → mask_o 寄存器`。**DEM 就在关键路径上。**

论文能这么说是因为它的 shuffler 有独立时序预算（RDAC follower 模式下
binary-to-unary 译码不关键）。我没有实现那个解耦。

**所以这条是"联用做到、解耦没做到"。**

---

### 9. 测量指标 —— ❌ 数字端不可验证

**论文数据**：INL 2.2/−1.8 LSB、DNL ±0.27 LSB（20b 级）、9.3nV/√Hz、
94.2dB DR、40MS/s、40nm、1.8mA 参考电流。

**为什么不可验证**：全部是模拟/系统指标，需要：
- 真实电容失配（我只有 ±0.3% 的假设值）
- 噪声源（热噪、闪烁、抖动）
- 完整的 SADC/RDAC/RA 闭环
- FFT 与直方图工具链

**当前数字端能做的极限**是 `tools/phase_ab.py`、`sched18_stats.py` 这类
**结构/分布**验证，不是性能指标。

---

## 一句话总结

> **九点里，5 点已实现且有实测证据（1、2、3、7、8），1 点部分（6），
> 1 点数字边界外（4），2 点未实现（5、9）。**

必须同时说清的是：**已实现的都只是"机制结构"，不是"性能收益"。**
论文的 94.2dB DR / 9.3nV/√Hz 与这个 IP 之间没有因果关系 ——
第 2、3、7、8 条对应的模拟侧收益（交织音摊开、采样带宽降 2-4×、
参考电流 1.8mA、DEM 不在关键路径）**我都无法验证**，其中第 8 条的后半句
我明确**没做到**。

---

## 附：本轮为核对九点新做的修复

| 项 | 问题 | 修法 | 验证 |
|:---|:---|:---|:---|
| 第 7 条 | `mask_o` 在事件流模式下不更新（`valid_i` 恒 0） | 输出使能加 `out_en` 分流 | k 8/8 + mask 8/8，门级 0 错误 |
| TB | 计数守卫跳过首次变化，误报 7/8 | 改判据 `i > 0` | 8/8 |

综合（重跑）：3012 cells / 1767.63 µm²，40 级逻辑、2.82 ns，
setup +19.88 ns、hold +0.00095 ns。
