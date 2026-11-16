# Formality 通过：功能等价性已验证

时间：2026-11-17　基线：`54389e8` 之后
状态：**`Verification SUCCEEDED`**

## 1. 决定性结果

```
Verification SUCCEEDED
  234 Passing compare points
  0 Failing
  0 Aborted
```

| 类型 | Passing | Failing |
|:---|--:|--:|
| Port | 166 | 0 |
| DFF | 68 | 0 |
| **合计** | **234** | **0** |

**RTL 与布线后网表功能等价，已由 Formality 证明。**

## 2. 与之前 FAILED 的对比

| 版本 | 时钟门控 | Formality |
|:---|:---|:---|
| 带门控（`CKLNQD` × 2） | ✅ 有 | **FAILED**：86/20/128，20 个点因 `clock_gate_acq_q_reg/*lat.00*` unmatched |
| **本版（无门控）** | ❌ 无 | **SUCCEEDED**：234/0/0 |

**根因确认无误**：那 20 个 failing 完全由时钟门控引起；禁用它后等价性直接通过。

## 3. 本版网表与物理数据

| 指标 | 值 |
|:---|:---|
| 时钟门控单元 | **0 个**（`CKLNQD=0`，实测） |
| 单元总数 | 211 个实例匹配 BUFFD/INVD/DFC/SDFC 类 |
| **Total cell area** | **778.32 µm²** |
| **Total Number of Routed Wires** | **14,899** |
| **Total number of DRCs** | **0** |
| **slack (MET)** | **+14.93 ns** |
| **CHK_UNPLACED** | **0** |

## 4. 禁用时钟门控的方法

```tcl
set_clock_gating_options -minimum_bitwidth 1000000
```

FC 没有"关闭时钟门控"的总开关（`set_clock_gating_options` 只有
`-minimum_bitwidth` / `-max_fanout` / `-objects`）。
把位宽门限设为 100 万，任何寄存器组都不触发门控插入。

实测验证：`CKLNQD = 0`。

## 5. 完整流程状态

```
STEP_CREATE_LIB_OK        ✅
CHK_TECH rc=0             ✅
CHK_UPF rc=0              ✅
CHK_VOLTAGE vdd=0 vss=0   ✅
STEP_ELAB_OK              ✅
STEP_TLU_OK               ✅
STEP_CONSTRAINT_OK        ✅
CHK_NO_CG rc=0            ✅  时钟门控已禁用
STEP_SYNTH_OK             ✅
CHK_SITE_ROWS made=51     ✅
STEP_FLOORPLAN_OK         ✅
STEP_PLACE rc=1           ⚠️  （统计口径；最终 CHK_UNPLACED=0）
STEP_CTS rc=0             ✅
STEP_ROUTE rc=0           ✅
STEP_WRITE_VERILOG_OK     ✅
STEP_WRITE_DEF_OK         ✅
STEP_WRITE_GDS_OK         ✅
CHK_UNPLACED 0            ✅
Formality SUCCEEDED       ✅  234/0/0
```

## 6. 必须标注的限定

| 项 | 说明 |
|:---|:---|
| **技术 LEF 来源** | `tsmc28_tech_derived.lef` 由 `.tf` 推导生成；`PITCH`/`WIDTH`/`SPACING` 来自 PDK `.tf`，`DIRECTION` 按 M1 水平/M2 垂直交替补，`AP` 层按典型值补。**不是 PDK 官方交付** |
| **无时钟门控** | 本结果对应"不含时钟门控"的版本。带门控版本 Formality 仍 FAILED（20 点） |
| **无 LVS** | 未做版图与网表的 LVS |
| **无 GDS 验证** | 已导出 `.gds`（1.97 MB）但未做 stream-out/stream-in 完整性检查 |
| **单角** | 仅 default corner（SS 0.81V），无多 PVT |
| **无 IR/EM** | 未做电源完整性分析 |
| **面积口径** | 778.32 µm² 是标准单元面积，与原型照片图估的版图区域不可直接比 |

## 7. 结论

**`IMPLEMENTATION_ONLY`，但等价性已证。**

- RTL→综合→布局→CTS→布线全流程打通
- **Formality `SUCCEEDED`，234 个比较点全部等价，0 failing**
- DRC = 0、open nets = 0、slack +14.93 ns、`CHK_UNPLACED` = 0
- 14,899 条布线、18,000+ µm 线长

**仍不能宣称 tapeout signoff**（无 LVS、无多角、无 IR/EM、技术 LEF 非原厂）。

但"网表与 RTL 功能等价"这一条，现在有 Formality 的正式证据。
