# 归档产物说明

时间：2026-11-17　基线：`d024928`

## 本目录内容

| 文件 | 说明 | 大小 |
|:---|:---|--:|
| `sched18_final.v` | FC 布线后网表（D1 模块）| 190 KB |
| `sched18.svf` | 综合期等价变换记录（供 Formality 用）| 25 KB |

## 对应的完整数据

这两份是从远端 `/tmp/fc_final/` 拷贝的正式产物。
同目录下还有体积较大的二进制文件**未入库**：

| 文件 | 远端路径 | 大小 |
|:---|:---|--:|
| `sched18_final.def` | `vm-yian:/tmp/fc_final/sched18_final.def` | 1.5 MB |
| `sched18_final.gds` | `vm-yian:/tmp/fc_final/sched18_final.gds` | 2.0 MB |

## 验证方式

```bash
# Formality：RTL vs 网表
ssh vm-yian 'export PATH=/opt/synopsys/fm/W-2024.09-SP3/bin:$PATH \
  SNPSLMD_LICENSE_FILE=27080@localhost; \
  fm_shell -f /tmp/fm_run3.tcl > /tmp/fm_nocg.log 2>&1'
grep -E "Verification (SUCCEEDED|FAILED)" /tmp/fm_nocg.log
# 预期：Verification SUCCEEDED
#       234 Passing compare points / 0 Failing
```

## 该网表对应的设计

| 项 | 值 |
|:---|:---|
| 模块 | `sched18`（D1：18 片 8/8/2 调度器）|
| 工艺 | TSMC 28HPC+ `ssg0p81vm40c` |
| 周期 | 25 ns（40 MS/s）|
| **Total cell area** | **778.32 µm²** |
| **Routed Wires** | **14,899** |
| **DRC** | **0** |
| **slack (MET)** | **+14.93 ns** |
| 时钟门控 | **无**（`CKLNQD = 0`，为 Formality 通过而禁用）|

## 重要限定

1. **技术 LEF 是推导生成的**（`flow/synth/v21/tsmc28_tech_derived.lef`），
   不是 PDK 官方交付。`PITCH`/`WIDTH`/`SPACING` 来自 `.tf` 真实值，
   `DIRECTION` 按 M1 水平/M2 垂直交替补，`AP` 层按典型值补。

2. **无时钟门控**。带门控版本的 Formality 为 FAILED（20 个点，
   根因 `clock_gate_acq_q_reg/*lat.00*` unmatched）。

3. **无 LVS、无 GDS 验证、单角、无 IR/EM**。

**这些都是真实产物，但不是 tapeout signoff 级证据。**
