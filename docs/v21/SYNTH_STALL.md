# 顶层综合在 `Processing 'wsum_v21'` 处停住（未解决）

时间：2026-11-16　基线：`74d96ba`
现象等级：**阻塞**，无错误码，无可复现的通过条件

## 1. 现象

`dc_shell -f dc_synth_v21.tcl` 在 compile 阶段停在：

```
  Processing 'wsum_v21'
```

之后日志不再增长，无网表产出。日志里 **0 条** `Error` / `Segmentation` /
`Killed` / `out of memory`。

## 2. 四个受控实验（全部停在同一处）

| 实验 | RTL 版本 | 脚本 | 停在 | 网表 |
|:---|:---|:---|--:|:---|
| `dcwork6` | HEAD + `sid_of` 钳位 | `dc_synth6.tcl` | 15,273 行 | 无 |
| `dcwork7` | HEAD 无钳位 | `dc_synth7.tcl` | 15,273 行 | 无 |
| `dcwork_exp` | **`dcwork4` 时期的** `recon`+`ctx` | `dc_synth_exp.tcl` | 15,266 行 | 无 |
| **`dcwork4b`** | 与 `dcwork4` 同脚本重放 | `dc_synth4b.tcl` | **15,273 行** | 无 |

**第 4 项是关键**：把 `dcwork4`（曾经成功的那次）的脚本原样重放，
现在也停在同一行。所以：

- **不是** `sid_of` 钳位导致的（`dcwork7` 无钳位同样停）
- **不是** 本轮 `recon`/`ctx` 改动导致的（`dcwork_exp` 用旧版同样停）
- **不是** 脚本或路径差异导致的（`dcwork4b` 就是原脚本）

## 3. 已排除的猜测（都曾被我当作原因，现撤回）

| 猜测 | 排除依据 |
|:---|:---|
| "钳位让组合复杂度爆炸" | `dcwork7` 无钳位同样停；且 `wsum` 单独综合 594 s 成功 |
| "本轮 recon 快照使锥变大" | `dcwork_exp` 用旧 recon 同样停 |
| "未调用的 `sid_of` 函数拖慢 elaborate" | 删除后（`74d96ba`）重放仍停 |
| 资源不足 | 12 GB 可用内存、599 GB 磁盘、license 计数 2,854 |

## 4. 已知的对照事实

| 事实 | 值 |
|:---|:---|
| `wsum_v21` **单独**综合 | **594 s 成功**，112,371 cells，68,171 µm² |
| 顶层 elaborate | **成功**（`STEP_ELAB_OK`，`CHK driving_cell_exists=1`） |
| 顶层 leaf cells（elaborate 后） | 82,325 |
| `dcwork4` 当时成功 | 约 620 s，网表 sha `769cc61f` |
| `dcwork4` 网表仍在 | `/tmp/dcwork4/adc_digital_ip_v21_syn.v`（27 MB） |

## 5. 当前状态

**同版网表未生成。** 因此：

- 门级等价（本轮功能修复后）**未验证**
- Formality（本轮功能修复后）**未验证**
- `u_wsum` 的 20 个 failing **仍未解决**（与本轮功能修复无关）

上一份可用网表是 `dcwork4`（`769cc61f`），对应 RTL `1fb9408`，
**不包含** IDLE 修复与 recon 对齐修复。用它做门级验证只能证明修复前的状态。

## 6. 可选的下一步（需要决策）

| 方案 | 代价 | 风险 |
|:---|:---|:---|
| A. 用 `-map_effort low` 重试 | 面积/时序变差 | 可能仍停 |
| B. 分模块综合后手工拼顶层 | 工作量大 | 等价性需另证 |
| C. 给 `wsum` 加 `set_dont_touch`，先出其余部分 | 缺失 wsum 逻辑 | 不是完整网表 |
| D. 用 `compile_ultra -incremental` 或分阶段 compile | 未知 | 未试 |
| E. 保持现状，先交付 RTL 级证据，后端标注"综合阻塞" | 无 PPA | 交付不完整 |

## 7. 复跑

```bash
ssh vm-yian 'rm -rf /tmp/dcwork7 && mkdir -p /tmp/dcwork7 &&
  sed "s|/tmp/dcwork|/tmp/dcwork7|g" /tmp/dc_synth_v21.tcl > /tmp/dc_synth7.tcl &&
  PATH=/opt/synopsys/syn/W-2024.09-SP3/bin:$PATH \
  SNPSLMD_LICENSE_FILE=27080@localhost \
  dc_shell -f /tmp/dc_synth7.tcl > /tmp/dcwork7/dc.log 2>&1'
# 观测：tail /tmp/dcwork7/dc.log 是否停在 "Processing 'wsum_v21'"
```
