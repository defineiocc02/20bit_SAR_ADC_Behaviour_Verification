# I3 完整检查点：节点模型在仿真边界，经实际握手返回

时间：2026-11-15　方案：v1.4 §4、§7、§9、§11.2 I3、§11.3
**状态：PASS（20 项检查，0 错误）**

## 当前 SHA

- 起点：`8c95f80`（I4 单模块）
- 本检查点：见本文件所在 commit

## 五项架构改动（对应目标 I3 第 1、2 条）

### 1. arm 与登记拆开

| 信号 | 语义 | 驱动条件 |
|:---|:---|:---|
| `capture_arm_req` | **沿前**授权 | `conv_valid & final_applied & ~arm_done` |
| `capture_reg_req` | **沿后**登记 | `sample_event & residue_captured` |

早期版本 `arm = capture_req`，等于"事后才授权"，宏没有提前收到授权。
现在 arm 在最后一个合法 apply 之后立即给出，**不含 `sample_event`**。

### 2. hold 与 owner 解耦

原来 `hold_done_q` 只在 `owner_valid_q` 时置位，且 raw 返回时被清 0。
于是"raw 先回、hold 后到"这个最常见顺序下 hold 永远记不上，旧组永久卡住。

现在 hold 只看 `adc2_hold_ack_i`，与 owner 无关：
- 旧组释放依据自己的 hold 与安静窗
- **raw 返回不提前释放旧组**（实测验证）
- **raw 返回也不抹掉 hold 记录**

### 3. 节点宏移出 DUT，放到仿真边界

DUT 只送出**本笔不可变快照**：

```
node_post[159:0]  最后一次实际应用的 slot 级 post
node_pre[31:0]    采样时保存的 slot 级 pre
node_ids[39:0]    8 个物理片号
node_qid / node_tag / node_valid / node_h / node_v
```

节点宏（`adc2_node_macro`）在测试台里代表模拟宏 + Vin 真值，
独立算出 raw 与 range，再通过 `adc2_valid/adc2_ready` 握手送回。

**5120 / 2560 只作为计分板预期值**，由模型独立算出后比对。

### 4. 定点格式回到 Q24 / 27 位

目标明确"带 24 位小数的有符号 Vin 至少用 27 位"。
`27 位 = 1 符号 + 2 整数 + 24 小数`，分辨率 5.96e-8 V = 0.0104 LSB20。

中途曾退到 Q22（26 位）能跑通，但不符合口径，已改回。

### 5. 生产清单与仿真清单分离

`files_adc_v21.f`（生产）**不含**节点宏；`files_adc_v21_sim.f` 含宏与 TB。

## 最大收益：compile_ultra 从超时变完成

| 指标 | 节点宏在 DUT 内 | 移出后 |
|:---|--:|--:|
| compile_ultra | **超时被 kill** | **完成** |
| 单元数 | 34714（medium） | **7383** |
| 面积 | 28372.96 µm² | **4508.69 µm²** |
| slack @25 ns | — | **+22.15 ns (MET)** |
| 未时钟寄存器 | 0 | **0**（951 寄存器全部有时钟） |

面积降 **6.3×**。这不是优化技巧，而是**语义修正的副产品**：
模拟宏本来就不该进数字网表。

## 验收覆盖（20 项）

| 项 | 结果 |
|:---|:---|
| 真实 sched18 复位初态 8/0/10（首采样前无 CONV） | OK |
| A 的 guard 序列到 k=519 | OK |
| **arm 在最后 apply 后给出（早于公共沿）** | OK |
| 节点快照有效 | OK |
| **节点模型独立算出 A raw = 5120** | OK |
| A 未越窗 | OK |
| **hold 未到 → 不释放** | OK |
| **A 经实际握手返回，tag = A1（归属正确）** | OK |
| **raw 返回不提前释放旧组** | OK |
| hold 到 → release_ok=1 | OK |
| B 在 CONV，7 事件到 k=258 | OK |
| **E2：停收后捕获 B 余差，节点模型 raw = 2560** | OK |
| B 经握手返回，tag = B2 | OK |
| 复位后迟到 raw 被丢弃 | OK |
| late_err 置位 | OK |
| **Vin=−3V 越窗报 range** | OK |

## 回归（全部保持）

| 回归 | 结果 |
|:---|:---|
| I1 两笔采样 | PASS (18) |
| I2 事件协议 | PASS (37) |
| I1+I2 完整联调 | PASS (104) |
| I3 完整（本检查点） | **PASS (20)** |
| I4 重构 | PASS (8213, 8209 retired) |
| D3D4 L1 叶子 | PASS (133376 + 512) |
| D3D4 L2 IP | PASS (512 笔) |
| D3D4 L2.5 sched18 | PASS (20000 拍) |
| D3D4 L2.6 逐事件 | PASS |
| bridge 66,688 组 | PASS |

## 修的一个兼容性回归

顶层新增 `sched_ext_sel` 后，`tb_i12_full` 没接这个端口，它悬空成 0，
内部 sched18 接管，外部给的定向片组被忽略 → **16 项失败**。

教训：**新增端口后必须逐个 TB 核对，不能只看编译通过**（悬空网在 VCS 里
通常只报 warning）。已在该 TB 注释里记录。

## 波形

`+dump` 时生成 `tb_i3_full.vcd`（约 188 KB），已验证可追踪：

```
adc2_capture_arm_o / residue_captured / hold_ack
node_post / node_tag / adc2_rsp_valid / adc2_rsp_raw
```

## 可复跑命令

```bash
# 功能 + 波形
ssh vm-yian 'cd /tmp/ip && export SNPSLMD_LICENSE_FILE=27080@localhost \
  PATH=/opt/synopsys/vcs/W-2024.09-SP1/bin:$PATH VCS_HOME=/opt/synopsys/vcs/W-2024.09-SP1 \
  && vcs -full64 -sverilog -timescale=1ns/1ps \
     dem_phase_rr.sv dem_phase_gen.sv dither_pre.sv dem_mask_v3.sv sched18.sv follower_rt.sv \
     rtl/v21/acq_conv_ctx.sv rtl/v21/sar_evt_v21.sv rtl/v21/adc2_capture.sv \
     rtl/v21/adc2_node_macro.sv rtl/v21/adc_digital_ip_v21.sv tb_i3_full.sv -o s3f \
  && ./s3f +dump'
```

期望：`RESULT: PASS (20 checks)`，并生成 `tb_i3_full.vcd`

## 剩余问题

| # | 问题 | 去向 |
|:--:|:---|:---|
| 1 | `recon_v21` 尚未接进顶层（独立验证 8213 项已过） | I4b |
| 2 | recon 单独综合 26818 µm²，PPA 仍大 | I5：倒数迭代 |
| 3 | 配置接口（§9.4）未实现 | I4b |
| 4 | 输出 FIFO / 反压未实现 | I4b |
| 5 | 真实 hold/quiet 时序仍为 TB 桩 | §12 待模拟组确认 |
| 6 | 28HPC+ 布局布线/签核未做 | I5 |

## 恢复时第一条命令

```bash
cd /Users/zhaoyi/src/20bit_SAR_ADC_Behaviour_Verification && git log --oneline -2 && git status --short
```
