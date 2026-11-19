# LGL-397 的最终根因：PDK 缺技术 LEF（含层布线方向）

时间：2026-11-16　基线：`fec05cc`

## 1. 最终证据链

### ① 层定义是有的（.tf 生效）

```tcl
create_lib -technology tsmcn28_9lm4X2Y2RUTRDL.tf -ref_libs <NDM>
get_layers -quiet *
-> 46 个层：PP PO CO M1 VIA1 M2 VIA2 M3 VIA3 M4 VIA4 M5 VIA5 ...
```

### ② 但所有层的布线方向都是 unknown

```tcl
M1.routing_direction = unknown
M2.routing_direction = unknown
M3.routing_direction = unknown
```

**这就是 `NEX-001 "Technology layer 'M1' setting 'routing-direction' is not valid"` 的来源。**

### ③ .tf 文件里没有方向信息

```bash
grep -inE "prefer|horiz|vertical|routing|direction" tsmcn28_9lm4X2Y2RUTRDL.tf
-> 无任何匹配
```

`.tf` 是 3852 行的纯几何技术文件（层厚、间距、via 规则），**不含布线方向**。

### ④ PDK 里没有任何含层定义的 LEF

```bash
find /home/yian/PDK/tsmc28 -name "*.lef"
-> 只有 4 个：
   installed/tech/tsmc28_7t_site_via.lef          34 行，0 个 LAYER
   TSMCHOME/.../tcbn28hpcplusbwp7t40p140.lef      单元 LEF，无 LAYER
   TSMCHOME/.../tcbn28hpcplusbwp7t40p140hvt.lef   同上
   TSMCHOME/.../tcbn28hpcplusbwp7t40p140lvt.lef   同上

逐个检查 "^LAYER " 定义：**全部为 0**
无 *.tlef、无 *tech*.lef
```

## 2. 因果链（完整）

```
PDK 缺技术 LEF（含 LAYER + routing-direction）
  ↓
层的 routing_direction = unknown
  ↓
NEX-001：M1~M9、AP 的 routing-direction 无效
  ↓
FC 无法建立合法的布线轨道与 site row
  ↓
LGL-397 × 49：No site rows found in floorplan
  ↓
place_opt 找不到可放置 buffer 的合法位置
  ↓
place_opt rc=1、clock_opt rc=1
  ↓
route_auto 无对象可布 → Routed Wires = 0
```

## 3. 已能做的与不能做的

| 阶段 | 状态 | 依据 |
|:---|:---|:---|
| DC 13 模块综合 | ✅ 完成 | 160,951 µm²，全满足 25 ns |
| FC `compile_fusion` | ✅ 完成 | 1,545 cells / **747.84 µm²** |
| FC 时序分析 | ✅ 有结果 | slack **+21.81 ns**（reg2out，估计 RC） |
| FC `initialize_floorplan` | ⚠️ 命令成功，但无 site row | LGL-397 |
| `place_opt` / `clock_opt` | ❌ rc=1 | 依赖 site row |
| `route_auto` | ❌ **0 条线** | 无放置结果 |
| DRC / LVS / GDS | ❌ | 无布线 |

## 4. 需要什么才能继续

**一份 28HPC+ 的技术 LEF（tech LEF / tlef）**，内容需含：

- 各层的 `LAYER M1 ... END M1` 定义
- `DIRECTION HORIZONTAL | VERTICAL`
- `PITCH`、`WIDTH`、`SPACING`
- `SITE core7T` 的完整定义

常见文件名：`tcbn28hpcplusbwp7t40p140_tech.lef`、`tsmcn28_9lm.tlef`、
或 `PR_tech/LEF/` 目录下的 tech LEF。

**当前 PDK 里没有这个文件。**

## 5. 结论

**后端工作到此为止，状态 `PARTIAL`。**

- 综合阶段完全打通，面积与时序数据**有效**
- 布局/布线不通，根因是 **PDK 缺技术 LEF**
- **不是配置问题，不是代码问题，是输入文件缺失**

**不宣称 tapeout signoff。**
