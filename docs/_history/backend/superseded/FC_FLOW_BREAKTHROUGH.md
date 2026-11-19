# FC 流程突破：综合与 floorplan 首次完整通过

时间：2026-11-16　基线：`47e223c` 之后

## 1. 根本性修复：技术文件用错了

### 我之前一直当作"技术 LEF"的文件

```
/home/yian/PDK/tsmc28/digital_technology/installed/tech/tsmc28_7t_site_via.lef
  总行数        34
  LAYER 定义    0        ← 一个都没有
  SITE 定义     1        （只有 core7T 的 SIZE）
  VIA 定义      2
```

**它是一个"别名补充文件"，不是技术文件。**

### PDK 里真正的技术文件

```
installed/tech/synopsys_pr/N28_PRTF_Syn_v1d5a/PR_tech/Synopsys/TechFile/
  HVH/  VHV/
  tsmcn28_9lm4X2Y2RUTRDL.tf      ← 9 层金属，与本库 1p9m 匹配
```

### 正确用法：`.tf` 给层，site LEF 给 site

```tcl
create_lib -technology $TF -ref_libs $NDM $WORK/ref/lib   # .tf 提供 LAYER
...
set_top_module $DESIGN
read_tech_lef $SITE                                        # site_via.lef 提供 core7T
```

## 2. 效果对比

| 项 | 修复前 | **修复后** |
|:---|:---|:---|
| `LAYER` 定义 | **0** | 完整 9 层 |
| `OPT-045` | 2~6 次 | **0 次** |
| `compile_fusion` | Phase 5 停 | **Phase 51，STEP_SYNTH_OK** |
| `initialize_floorplan` | 失败 | **STEP_FLOORPLAN_OK** |

## 3. 完整流程实测结果

```
STEP_CREATE_LIB_OK       ✅
STEP_ELAB_OK             ✅
CHK_SITE_LEF rc=0        ✅
STEP_TLU_OK              ✅
STEP_CONSTRAINT_OK       ✅
STEP_SYNTH_OK            ✅  compile_fusion 完成（Phase 51）
STEP_FLOORPLAN_OK        ✅
STEP_PLACE rc=1          ❌
STEP_CTS   rc=1          ❌
STEP_ROUTE rc=0          ⚠️ 命令执行了，但没布线
```

### 综合结果（有效数据）

```
Number of ports:              204
Number of nets:              1,596
Number of cells:             1,545
  combinational:             1,475
  sequential:                   70
Number of buf/inv:             196
Combinational area:         590.94 µm²
Noncombinational area:      156.90 µm²
**Total cell area:          747.84 µm²**
```

### 时序结果（有效）

```
Startpoint: acq_q_reg[0]（clk 上升沿触发器）
Endpoint:   ids_next_o[10]（输出端口）
Path Group: reg2out_default
slack (MET): 21.81 ns
```

## 4. 但布线是假的

```
Total Number of Routed Wires   = 0
Total Routed Wire Length       = 0 micron
Total Number of Routed Contacts= 0
timing graph nets = 1594, **routed nets = 0**
```

**`route_auto` 返回 rc=0，但一条线都没布。**

原因：`place_opt` 和 `clock_opt` 都返回 rc=1（失败），单元没有被正式放置，
所以布线阶段没有可布的对象。

`DRC = 0` 也是"没有线可查"的假象，**不能当作 DRC 通过**。

## 5. 两个问题仍未解决

| 问题 | 状态 | 说明 |
|:---|:---|:---|
| `LGL-397`（无 site row） | 49 次 | 加了 `-use_site_row` 和 site LEF 都没消除 |
| `place_opt` rc=1 | 未通过 | 依赖 site row |

**`OPT-045` 已解决**（`.tf` 修复），但 `LGL-397` 是另一个独立问题。

## 6. 可交付的有效数据

| 项 | 数据 | 可信度 |
|:---|:---|:---|
| 综合面积 | **747.84 µm² / 1,545 cells** | ✅ 有效 |
| 时序 slack | **+21.81 ns**（reg2out） | ✅ 有效（但未布线，基于估计 RC） |
| 布线 | 0 条线 | ❌ 无效 |
| DRC | 0 | ❌ **无效**（无布线可查） |

## 7. 结论

**综合阶段完全打通**（这是 40 多轮来第一次），面积与时序数据有效。
**布局与布线未打通**，卡在 `LGL-397`（site row）。

**不宣称 tapeout signoff。** 无有效布线、无 DRC/LVS、无 GDS。
