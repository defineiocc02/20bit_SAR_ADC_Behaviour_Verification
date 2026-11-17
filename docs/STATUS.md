# 复现状态（单一权威版本）

**本文件是唯一权威状态。** 其它专项文档是证据记录，
`docs/_history/` 是已作废版本，**不得引用**。

版本：`a9193e4`　分支：`merge/adi-repro`　日期：2026-11-17

---

## 一、能复现什么（RTL 功能层）

| 模块 | 内容 | 状态 |
|:---|:---|:---|
| **D1** | 18 片 8/8/2 调度（`sched18`） | ✅ 4096 拍逐位 + 20000 拍统计 |
| **D2** | Flash/SAR 逐事件 follower（`sar_evt_v21`） | ✅ I2 37 项 + I1I2 105 项 |
| **D3** | DEM 3+3+3 + bridge | ✅ 133,376 对 + 66,688 组 |
| **D4** | 分裂 dither | ✅ 512 点 + 512 笔 |
| **D5** | 定点重构（`recon_v21` + `wsum_v21`） | ✅ 8213 项 + 并发 5 场景 + 任意权重 364 笔 |
| **D6** | 事务/宏控制（`acq_conv_ctx` + `adc2_capture`） | ✅ 2 反例 + 20 项 + 25 项 |
| 配置 | 权重生命周期与范围验收 | ✅ 40 项 |

**一条命令**：`D3D4_RUNNER=vm-yian bash tools/verify_v21.sh`（预期 9 项全绿）

**本轮实测抽样**：`tb_recip_q60` → `RESULT: PASS (43766 inverses bit-exact vs Python)`

---

## 一B、原文依据（逐条可定位，证据等级 [A]/[P]）

### 2.1 [A] ISSCC 2024 §9.8 论文 p.182（PDF 第 1 页）

| 机制 | 原句 | 本工程对应 |
|:---|:---|:---|
| 18 片池选 8 | "the RDAC is composed of 8 slice DACs (sDAC) converting the signal, and 8 sDACs acquiring the signal, **dynamically selected from a pool of 18 sDACs**" | `sched18.sv` |
| 2 备用片 | "**Two spare sDACs** are introduced to allow shuffling of the sampling DACs and spread the residual interleaving tones" | `sched18.sv` IDLE=2 |
| 两相位交织 | "**Two phase interleaving** of the RDAC extends the acquisition time to the full clock period, reducing the required sampling bandwidth by a factor of 2-4 times" | `d3d4_ip` 接入 sched18 |
| 量化器分离 | "the **quantizer is fully separated** from the residue generating DAC (RDAC)" | 数字边界外 |
| 第二量化器 | "a **second quantizer** has been introduced … The flash-ADC can be shared, thanks to a **<1ns return to acquisition** time" | **未实现** |
| 前级 9b | "The matched-quantizer sDAC with the RDAC allows >11b matching, resulting in **9b quantization** in the first stage" | k 0..511 |
| RDAC follower | "The RDAC works as a **follower** of the quantizer with the conversion results **loaded as they develop**" | `follower_rt.sv` |
| DEM+dither | "a **dynamic-element-matching algorithm can be used in conjunction with dither**, as the shuffler is **not in the critical timing path**" | 联用已实现；**"不在关键路径"未做到**（§四.5） |

### 2.2 [A] ISSCC 2024 讲稿（44 页 PPT）

| 页 | 内容（原文行） | 用途 |
|:--:|:---|:---|
| p.29–31 | `3 dimensions of DEM` / `Selection of the slices - 8/18` / `3b horizontal segmentation` / **`Round robin shuffler`** / `3b vertical segmentation` / （p.31 追加）`Binary to unary bridging` | DEM 三维度定义 |
| p.31 图 | 蓝框 `[8][8][8][8]` 标 **DEM**；红框 `[4][2][1]` 标 **Dither**；绿箭头标 **P=50%** | bridging 语义 |
| p.32 | `INL=2.2/-1.8LSB`、`DNL=±0.27LSB`、`Min hits/code ~250` | 性能指标（数字端不可验） |
| p.33–35 | `Residue Amplifier` / `GMR + OTA` / `RDAC reference is still settling during RA phase` / `Auto-Zero removes offset and drift` / `RA noise reduced by dynamic ADC2 sampling BW` | RA/ADC2，数字边界外 |

**关键判读**：`Round robin shuffler` 与 `3b horizontal segmentation` 是**并列的两条**，
不是"round-robin 就是横向分段"。这一点影响 §五.2 的定性。

### 2.3 专利

| 文件 | 依据 | 级别 |
|:---|:---|:---|
| US10516408B2 | 独立量化器、RDAC follower | [P]，与论文[B]一致 |
| US10511316B2 Fig.19–21 | 分段、保留列、横/纵打乱 | [P] **扫描件无文字层**，只能读图（实测 pypdf 提取全空） |
| US10505561B2 Fig.19 | 量化器与 RDAC 的**分路** dither，七片 worked example | [P] 八片与 `/8` 为 [E] |
| US10707889B1 | 交织与 tracking 实施例 | [P] 未纳入本轮 |
| US10541702B1 / US10826519B1 | 预充、参考切换接口 | [P] 仅接口核对，未实现 |

---

---

## 二、后端状态（13 个模块）

| 类别 | 数量 | 模块 |
|:---|--:|:---|
| **完成 RTL→GDS + Formality SUCCEEDED** | **7** | `sched18`、`adc2_capture`、`acq_conv_ctx`、`sar_evt_v21`、`dem_phase_rr`、`follower_rt`、`dem_phase_gen` |
| 布线完成、router DRC 未归零 | 2 | `dither_pre`（2）、`dem_mask_v3`（27）|
| 预算内未完成 | 4 | `wsum`、`recon`、`recip_q60`、`recip_rnd` |

**Formality 比较点合计 2,189，0 failing。**

**详情**：`docs/audit/ALL_13_MODULES_FINAL.md`

---

## 三、不能声称什么

| 不能声称 | 原因 |
|:---|:---|
| foundry DRC clean | 7 个通过模块的 DEF **无 PG 布线几何**（`SPECIALNETS` 中 VDD/VSS 仅 `( PIN )` + `( * VDD )`，无 `ROUTED`）|
| LVS clean | 未做 |
| tapeout signoff | 无多 PVT 角、无 IR/EM |
| 整核集成完成 | `adc_digital_ip_v21` **从未综合成功**；`dem_phase_rr`/`dem_phase_gen`/`follower_rt` **未在 v21 顶层实例化** |
| D5 算法不可实现 | 实测只支持「冻结输入与预算下未完成」，三个模块失败原因各不相同 |
| D5 占整核 95.8% | 已撤回：`recon`→`recip_rnd`→`recip_q60` 三层嵌套，面积不可相加 |

---

## 四、已知未修缺陷

**`recon_v21.sv:373`**

```systemverilog
err3_q <= err2_q | (|x3_q[95:31]);
```

| # | 缺陷 |
|:--:|:---|
| ① | 合法负数高位全 1 被误判为溢出 |
| ② | 读上一拍照旧值，却配本笔 tag |

**现有测试向量不覆盖**「前笔负 X → 后笔正常」序列（`recon_oracle.py` 的 `schedule()` 中多笔 case 的 `raw` 全为默认 2048）。

**详见** `docs/audit/todo/README.md` §2（T-A，需授权）。

---

## 五、三条硬性限定

1. **技术 LEF 为推导生成**，非 PDK 官方交付（`AFP-1022` 证实方向推导与工具一致）
2. **无时钟门控**（带门控版 Formality FAILED，20 点）
3. **router DRC=0 ≠ foundry DRC clean**

---

## 六、作废数字清单（不得再引用）

| 数字 | 作废原因 |
|:---|:---|
| 「D5 占数字面积 95.8%」 | 层级嵌套导致重复计数 |
| 「13 模块合计 160,951 µm²」 | 同上，仅为算术和 |
| 「4 个模块物理不可行」 | 实为 2 预算到期 + 1 人工终止 + 1 未合法放置后继续 CTS |
| 「同一根因：大位宽运算」 | 三个失败模式各不相同 |

---

**历史版本**：`docs/_history/v21_status/STATUS_d5a1576.md`（2026-11-16，已作废）
