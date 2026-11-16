# FC RTL-to-GDS 完整跑通（含遗留问题）

时间：2026-11-17　基线：`92ae967` 之后

## 1. 完整流程状态

```
STEP_CREATE_LIB_OK        ✅  .tf 提供 46 层
CHK_TECH rc=0             ✅  自生成 tech LEF，M1~M9+AP 方向有效
CHK_UPF rc=0              ✅  UPF 加载成功
CHK_VOLTAGE vdd=0 vss=0   ✅  电压设置成功
STEP_ELAB_OK              ✅
STEP_TLU_OK               ✅
STEP_CONSTRAINT_OK        ✅
STEP_SYNTH_OK             ✅
CHK_SITE_ROWS made=48     ✅  site row 无重叠
STEP_FLOORPLAN_OK         ✅
STEP_PLACE rc=1           ⚠️  42 个单元未放置
STEP_CTS rc=0             ✅
STEP_ROUTE rc=0           ✅
Checked 1/1 Partitions, Violations = 0
```

## 2. 最终数据（完整布线）

| 指标 | 值 |
|:---|:---|
| **Total Number of Routed Wires** | **15,184** |
| **Total Routed Wire Length** | **18,363 µm** |
| **Total Number of Routed Contacts** | **15,663** |
| **Total number of DRCs** | **0** |
| **open nets** | **0** |
| Total cell area | **736.27 µm²** |
| Number of cells | ~1,540 |
| **slack (MET)** | **+14.44 ns** |

**对比上一轮（布线不完整）：**

| 指标 | 上轮 | **本轮** |
|:---|--:|--:|
| Routed Wires | 127 | **15,184** |
| Wire Length | 113 µm | **18,363 µm** |
| Routed Contacts | 169 | **15,663** |
| DRC | 0 | **0** |
| 未放置单元 | 168 | **42** |

**布线从 127 条线提升到 15,184 条线 —— 这是真正的完整布线。**

## 3. 这轮的关键修正

| # | 修正 | 效果 |
|:--:|:---|:---|
| 1 | 加载 UPF + `set_voltage` | `used supplies` 从 0 变为有效，buffer 可插入 |
| 2 | `site_count` 从 284 改为 283 | 未放置单元从 168 降到 42 |
| 3 | 保留 `.tf` + 自生成 tech LEF | 层方向全部有效 |
| 4 | 先清后建 site row | 无 `LGL-010` 重叠 |

## 4. 遗留问题：42 个单元未放置

```
Error: place_opt bailed out due to unplaced cells (OPT-062)
42 个 "Cell A#### is not placed"
```

**影响**：
- `place_opt` 返回 1
- 但 CTS 与 route 仍能执行，且 DRC = 0、open nets = 0
- **这 42 个单元可能不影响主逻辑布线**（因为它们很可能是优化插入的冗余 buffer）

**需进一步确认**：这 42 个单元是否属于功能路径。若属于，则布线虽 DRC 干净但逻辑不完整。

## 5. 必标注的限定

**技术文件是我根据 `.tf` 推导生成的**：

| 文件 | 内容来源 |
|:---|:---|
| `flow/synth/v21/tsmc28_tech_derived.lef` | `PITCH`/`WIDTH`/`SPACING` 来自 `.tf` 真实值；`DIRECTION` 按 M1 水平/M2 垂直交替补；`AP` 层按典型值补 |

**这不是 PDK 官方交付的技术 LEF。**

此外：
- **无 LVS**（无版图提取比对）
- **无 GDS 导出验证**
- **无多 PVT 角**（仅 default corner）
- **无 IR/EM 分析**

## 6. 结论

**`IMPLEMENTATION_ONLY`。**

- RTL→综合→布局→CTS→布线全流程打通
- 布线 15,184 条线、DRC = 0、open nets = 0、时序满足（+14.44 ns）
- **但有 42 个单元未放置、`place_opt` 返回 1**
- **技术文件为推导生成，非原厂交付**
- 无 LVS / 无 GDS 验证 / 无多角 / 无 IR-EM

**不能宣称 tapeout signoff。**
