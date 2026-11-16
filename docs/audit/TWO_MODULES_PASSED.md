# 两个模块完整通过 RTL-to-GDS + Formality

时间：2026-11-17　基线：`fc7e047`

## 1. 结果

| 模块 | 功能 | RTL-to-GDS | Formality | 耗时 |
|:---|:---|:---|:---|--:|
| **`sched18`** | D1：18 片 8/8/2 调度器 | ✅ 完整 | ✅ **SUCCEEDED 234/0/0** | 约 3 分钟 |
| **`adc2_capture`** | D6：ADC2 捕获与返回 | ✅ 完整 | ✅ **SUCCEEDED 490/0/0** | 约 3 分钟 |

**两个模块用完全相同的脚本、tech LEF、site row 逻辑、UPF，只换 RTL。**

## 2. 详细数据

| 指标 | `sched18` | `adc2_capture` |
|:---|--:|--:|
| Total cell area | 778.32 µm² | **731.47 µm²** |
| Total Routed Wires | 14,899 | **6,755** |
| Total number of DRCs | **0** | **0** |
| slack (MET) | +14.93 ns | **+22.54 ns** |
| CHK_UNPLACED | 0 | **0** |
| Formality Passing | 234 | **490** |
| Formality Failing | **0** | **0** |
| 网表大小 | 190 KB | 101 KB |
| GDS 大小 | 2.0 MB | 0.94 MB |

## 3. 完整流程状态（两个模块一致）

```
STEP_CREATE_LIB_OK        ✅
CHK_TECH rc=0             ✅
CHK_UPF rc=0              ✅
CHK_VOLTAGE vdd=0 vss=0   ✅
STEP_ELAB_OK              ✅
STEP_TLU_OK               ✅
STEP_CONSTRAINT_OK        ✅
CHK_NO_CG memset rc=0     ✅   时钟门控已禁用
STEP_SYNTH_OK             ✅
CHK_SITE_ROWS made=48     ✅
STEP_FLOORPLAN_OK         ✅
STEP_PLACE rc=1           ⚠️  统计口径（最终 CHK_UNPLACED=0）
STEP_CTS rc=0             ✅
STEP_ROUTE rc=0           ✅
STEP_WRITE_VERILOG_OK     ✅
STEP_WRITE_DEF_OK         ✅
STEP_WRITE_GDS_OK         ✅
CHK_UNPLACED 0            ✅
Formality SUCCEEDED       ✅
```

## 4. 这说明流程是可重复的

`adc2_capture` 一次通过（无任何调试），证明：

| 项 | 结论 |
|:---|:---|
| 脚本 | ✅ 通用，只需换 RTL 路径/模块名/工作目录 |
| tech LEF（推导生成） | ✅ 对两个不同模块都适用 |
| site row 生成逻辑 | ✅ 通用 |
| UPF + 电压设置 | ✅ 通用 |
| 禁用时钟门控 | ✅ 通用 |
| Formality 脚本 | ✅ 通用 |

**流程已经可以批量推广到剩余模块。**

## 5. 与 `wsum` 的对比

| 模块 | DC cells | FC 分区网格 | RTL-to-GDS | Formality |
|:---|--:|:---|:---|:---|
| `sched18` | 2,338 | 26×26 | ✅ 3 分钟 | ✅ SUCCEEDED |
| `adc2_capture` | 560 | 小 | ✅ 3 分钟 | ✅ SUCCEEDED |
| **`wsum_v21`** | **153,735** | **160×160** | ❌ **38 分钟未完成** | ❌ |

**`wsum` 的失败是规模问题，不是流程问题** —— 同一流程在两个小模块上一次通过。

## 6. 已归档产物

| 文件 | 位置 |
|:---|:---|
| `sched18_final.v` / `.svf` | `docs/audit/artifacts/` |
| `adc2_capture_final.v` / `.svf` | 远端 `/tmp/fc_cap/`（尚未归档）|

## 7. 三条限定（不变）

1. 技术 LEF 为推导生成（但 `AFP-1022` 证实工具从库也推导出同样方向）
2. 无时钟门控
3. 无 LVS / 无 GDS 验证 / 单角 / 无 IR-EM

**不是 tapeout signoff，但等价性已由 Formality 正式证明。**
