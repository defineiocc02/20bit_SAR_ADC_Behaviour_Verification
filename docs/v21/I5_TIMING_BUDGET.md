# I5 时序预算（25 ns 公共采样周期）

时间：2026-11-15　工艺：TSMC 28HPC+　依据：方案 v1.4 §10

## 1. 三条必须核对的路径

方案 §10 要求至少核对三条：

| # | 路径 | 起点 | 终点 | 预算归属 |
|:--:|:---|:---|:---|:---|
| **P1** | 旧组释放 → 初始化 → pre 生效 → 下一笔采集 | `hold_ack` + quiet 计满 | 下一笔 `sample_event` | 25 ns 内须完成 |
| **P2** | Flash/比较结果可用 → 数字译码 → 物理 RDAC 开关生效 | 事件 `bit_valid` | `post_wr` 注册输出 | **逐事件最迟应用时刻** |
| **P3** | 最后开关生效 → 保持 → 参考切换 → RA 建立 → ADC2 捕获 | 最后一次 `apply_fire` | `adc2_capture_arm` | 须满足宏 hold/quiet |

## 2. 本工程的实际结构（对应到 RTL）

### P2：事件 → 开关

```
bit_valid_i
  -> sar_evt_v21 组合：prefix_accept / k_wide / k_bad
  -> dem_mask_v3 组合：160 位 slot mask（3+3+3 + bridge）
  -> 顶层 generate 展开：按 conv_ids 路由成物理 360 位
  -> post_state_q / post_wr_q 寄存
```

**这条路径在 25 ns 约束下是本设计的主关键路径**（leaf 级已验证 2.82 ns
@ `d3d4_ip`，见 `docs/SYNTH_D3D4.md`）。v21 层额外增加了
"按 8 个冻结片号把 slot 展开成物理" 的 generate 归约，需要单独看 STA。

### P3：最后一次 apply → arm

```
apply_fire_w & last_evt_w
  -> wsum_v21（18×20 权重选择求和，2 级寄存）
  -> wsum_valid
  -> adc2_arm_req = conv_valid & final_applied & wsum_valid & ~seen
  -> adc2_capture_arm_o
```

**这条路径比 P2 长**：wsum 有两级寄存 + 8×16 次加法。
用**电平**触发（`conv_valid & final_applied`）而不是单拍脉冲，
所以 2-3 拍的延迟不影响功能，只要在公共沿之前稳定即可。

### P1：释放 → 再采集

```
hold_ack -> ctx.conv_held_q
quiet_cnt 计满 -> release_fire_o -> conv_valid=0
  -> idle 掩码放回 -> 下一笔 prepare 可用
```

纯计数器逻辑，**远短于 25 ns**。

## 3. 实测数据（待综合完成后填入）

| 项 | 值 | 来源 |
|:---|:---|:---|
| 综合工具 | DC W-2024.09-SP3 | — |
| 库 | `tcbn28hpcplusbwp7t40p140ssg0p81vm40c_ccs` | 28HPC+ SS |
| 约束 | 25 ns，ZeroWireload | 待补 SDC 完整化 |
| 单元数 | 见 I5 综合报告 | — |
| 面积 | 见 I5 综合报告 | — |
| **setup slack** | 见 I5 综合报告 | — |
| **hold slack** | 见 I5 综合报告 | — |

## 4. 与论文 ~1 ns shuffler 预算的对照

论文 p.182 说 DEM shuffler "不在关键路径上"，PPT 29–31 的 `Round robin
shuffler` 是确定性的。本工程用 `dem_phase_rr`（round-robin）+ `dem_mask_v3`
组合译码，**关键路径在 3+3+3 译码与物理路由**，不在随机数发生器：

- xorshift 版：4.38 ns（已弃用，见 `docs/PHASE_AB.md`）
- round-robin 版：**0.57 ns**（纯组合查表 + 混合进制分解）

所以"~1 ns 预算"对本实现是宽松的；真正的瓶颈是 `recon_v21` 的除法器。

## 5. 已知的 PPA 问题（如实记录）

`recon_v21` 按方案 §9.3 用**精确整数除法**作为金标基线：

- `I = RND(2^60, G)` —— 64 位除以 64 位
- `X = floor((N*I)/2^36)` —— 96 位乘法
- `Q = RND(X, 96)` —— 96 位除以常数

综合日志显示 DC 连续例化多个 `DW_div_uns_*`（width61/b64、width61/b64…），
每个都要几十秒到几分钟。**这是当前 PPA 的主要来源。**

方案 §9.3 已给出处理路径：

> "初版倒数用**精确整数定义**作为金标。硬件复用 `recip_q60` 的 LUT/Newton 思路，
> 但现有模块近似 floor 与这里最近舍入不同，须乘回检查余数并修正；
> 无法证明差值界时采用精确除法基线。"

本轮**保留精确除法**以保证 8213 项逐位对拍（这是功能正确性的硬证据），
把倒数迭代明确列为 I5 之后的独立工作项 —— 因为它需要
"乘回检查余数" 的完整误差证明，不能在改数值的同时声称位精确。
