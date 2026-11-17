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
