# 当前合同入口（唯一）

**生效版本：v2 + v2.1 补充条款。冲突处一律以 v2.1 为准。**

| 文件 | 状态 | 说明 |
|:---|:---|:---|
| `ISSCC2024_ONCHIP_CONTRACT_v2.md` | **现行主体** | 结构、接口、物理网络 |
| `ISSCC2024_ONCHIP_CONTRACT_v2_1_ADDENDUM.md` | **现行补充，优先级最高** | 覆盖 v2 中的歧义 |
| `CONTRACT_V2_REVIEW.md` | 历史 | 我的评审意见，已被 v2.1 逐条回应 |
| `PHYSICAL_CONTRACT_DRAFT.md` | 历史 | 我的草稿，已被 v2 取代 |
| `D2_INTERFACE_SPEC.md` 等 | 历史 | 早于合同，仅作实施记录 |

## v2.1 冻结的关键参数（不得再改）

| 项 | 值 |
|:---|:---|
| SADC 码 c | 0..519 |
| RDAC 命令 k | 0..520 |
| 普通码分解 | k = 64H + 8V + L，H,V,L ∈ [0,7] |
| guard | 512..519：主阵列全选 + 子阵列 [r2,r1,r0,0]；520：子阵列 [1,1,1,1] |
| 保留列 | reserved = (7 + h_phase) % 8 |
| 主采样电容总量 | **512 Cu**（8 片 × 64 Cu） |
| 归一化增益 G | **32**（无量纲，= 512Cu / Cf(16Cu)） |
| D_sadc(c) = D_rdac(k) | -3 + 6c/512（同码相等） |
| bridge | slot=v_phase, macro=reserved, half=(acq_seq>>3)&1 |
| bridge 交换 | post[slot][j]=b4；全部 8 片 post[s][16]=a |
| Wp | 8 片 j=16..19 求和，w 已含 β=1/8 与 1/Cf |
| α, b | G1/G2 默认 1 和 0，片外标定 |
| 相位推进 | 仅在 acq_prepare_fire |
| ADC2 | 13 bit，[-0.1875, +0.5625)，0.75 V 跨度 |

## 当前状态

D3/D4 的通过情况与证据见 `docs/ACCEPTANCE_STATUS.md`，
复现命令 `make verify-d3d4`。

## 与旧实现的明确冲突（以合同为准）

| 旧实现 | 合同要求 |
|:---|:---|
| `dem_shuffle` 用 4+3+2（H=k[8:5], M=k[4:2]） | **3+3+3** |
| 16 个独立一元列 | **8 宏列 × 2 个 4Cu 半单元** |
| `recon_d5_w2` 细码固定除 32 | 用实际 G |
| 9 项 `inv_tab` 固定倒数 | 按实际片组算 G |
| 事件数固定 9 | **Flash + 6 SAR = 7**（最高区间另有 guard 路径） |
| `k = c - U` | **k = c + U**，且 `x_hat` 末项为 `-TΔ` |
