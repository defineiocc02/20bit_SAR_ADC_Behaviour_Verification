# RTL 目录说明（权威）

更新：2026-11-17　基线：`ffc10ef`

## 一、四个顶层并存（**不是新旧关系**）

`rtl/` 下有 **四个** 顶层模块，各自覆盖不同范围的交付物。
**不要把它们当成版本迭代，也不要互相替代。**

| 顶层 | 子实例 | 行数 | 覆盖范围 | 验证脚本 |
|:---|--:|--:|:---|:---|
| **`v21/adc_digital_ip_v21.sv`** | 8 | 627 | **D1/D2/D5/D6 全链**（v21 版）| `tools/verify_v21.sh` |
| `digital_top_w.sv` | 7 | 544 | **D6 权重路径**，`dem_shuffle` + `dither_split`（4+3+2 / `c−U`）| 多个 `tb/tb_*.sv` |
| **`d3d4_ip.sv`** | 6 | 454 | **D3/D4 IP 顶层**：`dem_phase_gen` + `dem_mask_v3` + `dither_pre` | `tools/verify_d3d4_ip.sh` |
| `digital_top.sv` | 4 | 34 | 早期骨架 | — |

### 关键区分（引自 `docs/ACCEPTANCE_STATUS.md` 第 40 行）

> 顶层 `digital_top_w` 仍走 `dem_shuffle` + `dither_split`（4+3+2 / `c−U`），**不含 D3/D4**。

**所以：**
- 想验 D3/D4 → 用 `d3d4_ip.sv`
- 想验 D6 权重路径 → 用 `digital_top_w.sv`
- 想验 D1/D2/D5/D6 全链 → 用 `v21/adc_digital_ip_v21.sv`

## 二、`v21/adc_digital_ip_v21.sv` 的实际实例化（8 个）

| 行 | 实例 | 模块 |
|--:|:---|:---|
| 192 | `u_pd` | `dither_pre` |
| 204 | `u_sched` | `sched18` |
| 219 | `u_ctx` | `acq_conv_ctx` |
| 293 | `u_evt` | `sar_evt_v21` |
| 320 | `u_dem` | `dem_mask_v3` |
| 478 | `u_adc2` | `adc2_capture` |
| 572 | `u_wsum` | `wsum_v21` |
| 611 | `u_recon` | `recon_v21` |

### **未在 v21 顶层实例化的模块**

| 模块 | 由谁实例化 |
|:---|:---|
| `dem_phase_rr` | 无（仅单模块验证）|
| `dem_phase_gen` | `d3d4_ip.sv` |
| `follower_rt` | 无（仅单模块验证）|

**因此这三个模块的 Formality `SUCCEEDED` 只是模块级证据，不构成 v21 顶层已接通的证据。**

## 三、目录结构

| 路径 | 内容 | 状态 |
|:---|:---|:---|
| `rtl/v21/` | v21 版 8 个模块 + 顶层 | ✅ 现行 |
| `rtl/*.sv`（散装） | `sched18`、`dem_mask_v3`、`dem_phase_gen`、`dem_phase_rr`、`dither_pre`、`follower_rt`、`recip_q60` | ✅ 现行（被多个顶层引用）|
| `rtl/core/` | 16 个早期核心模块 | ⚠️ 被 `digital_top`/旧 tb 引用，**勿删** |
| `rtl/top/` | `sadc_enc`、`sar20_digital_core` | ⚠️ 早期 |
| `rtl/{slice_pool,dem_shuffle,dither_split,recon_d5*,follower}.sv` | 被 `digital_top_w`/`d3d4_ip` 及其 23 处 tb/tool 引用 | ⚠️ **勿删** |

**本目录没有"废弃可删"的文件。** 每个文件都被某条验证链或某个 tb 引用（已实测）。

## 四、层级嵌套（面积不可横向相加）

```
recon_v21  → recip_rnd_v21 u_recip_rnd    (recon_v21.sv:314)
recip_rnd_v21 → recip_q60 u_recip          (recip_rnd_v21.sv:59)
```

## 五、已知缺陷（未修）

`recon_v21.sv:373`：

```systemverilog
err3_q <= err2_q | (|x3_q[95:31]);
```

| # | 缺陷 |
|:--:|:---|
| ① | 合法负数高位全 1 被误判为溢出 |
| ② | 读上一拍照旧值，却配本笔 tag |

**详见 `docs/audit/todo/README.md` §2（T-A）。本轮未修改。**
