# RTL 目录说明（权威）

更新：2026-11-17　基线：`dfb21eb` 之后

## 一、只有一个现行顶层

**`rtl/v21/adc_digital_ip_v21.sv`** —— 唯一的现行顶层。

| 项 | 值 |
|:---|:---|
| 实例数 | 8 |
| 行数 | 627 |
| 验证 | `tools/verify_v21.sh` |

### 8 个实例化（逐行）

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

### 未实例化的三个模块

| 模块 | 状态 |
|:---|:---|
| `dem_phase_rr` | 仅单模块验证 |
| `dem_phase_gen` | 仅单模块验证 + 旧 `d3d4_ip` |
| `follower_rt` | 仅单模块验证 + 旧 `d3d4_ip` |

**它们的 Formality `SUCCEEDED` 是模块级证据，不构成整核已接通。**

## 二、旧顶层：已归档到 `_legacy/tops/`

为消除"四个顶层并存"的混乱，三个非 v21 顶层**已移入 `rtl/_legacy/tops/`**：

| 文件 | 原实例 | 覆盖 | 归档原因 |
|:---|--:|:---|:---|
| `d3d4_ip.sv` | 6 | D3/D4（`dem_phase_gen`/`dem_phase_rr`/`follower_rt`/`dem_mask_v3`/`dither_pre`/`sched18`）| 内容已被 v21 覆盖，仅差 3 个模块 |
| `digital_top_w.sv` | 7 | D6 权重路径（`dem_shuffle`/`dither_split` 旧算法）| 旧算法链，非现行 |
| `digital_top.sv` | 4 | 早期骨架 | 最早版本 |

### 归档后仍可验证

`d3d4_ip` 的验证链**仍然完整**：
`tools/verify_d3d4_ip.sh` 与 9 处脚本的路径已同步为 `rtl/_legacy/tops/`。

**实跑确认**（归档后）：

```
RESULT: PASS (512 tx bit-exact)
```

## 三、目录结构

| 路径 | 内容 | 状态 |
|:---|:---|:---|
| `rtl/v21/` | **8 个现行模块 + 唯一顶层** | ✅ 现行 |
| `rtl/*.sv`（散装） | `sched18`、`dem_mask_v3`、`dem_phase_gen`、`dem_phase_rr`、`dither_pre`、`follower_rt`、`recip_q60` | ✅ 现行（被 v21 顶层或单模块验证引用）|
| `rtl/_legacy/tops/` | 3 个旧顶层 | ⚠️ 归档，验证链仍可用 |
| `rtl/core/` | 16 个早期核心模块 | ⚠️ 被旧 tb 引用 |
| `rtl/top/` | `sadc_enc`、`sar20_digital_core` | ⚠️ 早期 |
| `rtl/{slice_pool,dem_shuffle,dither_split,recon_d5*,follower}.sv` | 被旧 tb/tool 引用 | ⚠️ 勿删 |

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
