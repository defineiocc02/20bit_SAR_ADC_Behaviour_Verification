# 逐模块综合摸底

时间：2026-11-16　基线：`b409359`
工艺：TSMC 28HPC+ `ssg0p81vm40c`　周期 25 ns
脚本：`flow/synth/v21/syn_module.tcl`（每模块独立 elaborate + compile -map_effort medium）

## 1. 结果

| 模块 | 耗时 | cells | 面积 µm² | slack | 判定 |
|:---|--:|--:|--:|--:|:---|
| `sar_evt_v21` | **3 s** | 321 | 254.31 | +21.21 | 正常 |
| `adc2_capture` | **3 s** | 560 | 625.73 | +22.71 | 正常 |
| `acq_conv_ctx` | **6 s** | 1,716 | 1,255.87 | +21.08 | 正常 |
| `sched18` | **7 s** | 2,338 | 1,265.38 | +16.62 | 正常 |
| `recip_q60` | **20 s** | 11,308 | 9,246.99 | +3.02 | 正常 |
| `recip_rnd_v21` | **32 s** | 20,073 | 16,619.82 | +3.02 | 正常 |
| **`wsum_v21`** | **> 2.5 min 未完成** | — | — | — | **慢** |
| **`recon_v21`** | **> 10 min 未完成** | — | — | — | **卡住** |

**前 6 个模块合计 71 秒，全部满足 25 ns。**

## 2. 两个瓶颈

### 2.1 `recon_v21`：卡在 alpha 除法器

编译日志显示端点始终是 `z1_q_reg[63]/D` / `z1_q_reg[43]/D`，对应：

```systemverilog
// recon_v21.sv:287
z1_q <= rnd64(d1a_q * 64'sd1073741824, {32'd0, alpha1a_q});
```

这是 **64 位 × 2^30 → 64 位可配置除数除法器**。

**关键观察**：顶层把 alpha 固定为 2^30：

```systemverilog
// adc_digital_ip_v21.sv:616
.alpha_q30(32'd1073741824),   // alpha=1.0 旁路（首版）
```

所以 `alpha_bypass` 在顶层恒为真，这个除法器在顶层是**死逻辑**。
但 `recon` 单独综合时 alpha 是输入端口，DC 必须完整展开 → 卡住。

### 2.2 `wsum_v21`：二维数组查表

```systemverilog
acc = acc + wt_main[ids[s*5 +: 5]][j];   // 8 slot × 16 列 × 18 行
```

必须展开成 2,304 路 × 40 位 MUX。即使改成内部寄存器堆，
查表逻辑仍然存在（只是输入从端口变成寄存器）。

## 3. 对后端工作的意义

| 项 | 结论 |
|:---|:---|
| 可独立综合的模块 | 6 个（占总面积约 30,270 µm²，若按此推算）|
| 需先解决的瓶颈 | `recon`（alpha 除法）、`wsum`（查表 MUX）|
| 分模块后端可行性 | **可行**——6 个模块都已产出网表 |

## 4. 未完成

| 项 | 状态 |
|:---|:---|
| `wsum_v21` 完整结果 | 运行中（后台，预算 30 min）|
| `recon_v21` 完整结果 | 超时终止，需先解决 alpha 除法器 |
| `dem_mask_v3` / `dither_pre` / `dem_phase_*` | 未测（纯组合，预计很快）|
| `follower_rt` | 未测 |
| `adc_digital_ip_v21` 顶层 | 依赖上述 |

## 5. 复跑

```bash
# 单模块
scp flow/synth/v21/syn_module.tcl vm-yian:/tmp/
ssh vm-yian 'cd /tmp && export PATH=/opt/synopsys/syn/W-2024.09-SP3/bin:$PATH \
  SNPSLMD_LICENSE_FILE=27080@localhost && \
  cat > /tmp/m.tcl <<EOT
set MOD <module>
set RTL {<files>}
source /tmp/syn_module.tcl
EOT
  dc_shell -f /tmp/m.tcl > /tmp/m.log 2>&1'
grep -E "CHK_MODULE|CHK_CELLS|Total cell area|slack" /tmp/m.log
```
