# I4b 检查点：D1–D6 全链端到端贯通

时间：2026-11-15　方案：v1.4 §9.1–§9.4、§11.2 I4
**状态：PASS（24 项检查，0 错误）**

## 起点

`0977e14`（I3 完整：节点模型在仿真边界，20 项通过）

## 这一轮打通了什么

**从真实采样到 20 位输出，一次贯通：**

```
prepare/sample -> 逐事件 apply -> 物理 post -> 节点模型算 raw
  -> 实际握手返回 -> owner 快照 G/Wp/Wm -> D5 定点重构 -> 20 位码
```

A 得到 **0xFFF00**、B 得到 **0x80000**，与方案 §11.3 逐值一致。

## 新增

| 文件 | 内容 |
|:---|:---|
| `rtl/v21/wsum_v21.sv` | 生产侧 G/Wp/Wm 求和（18×20 权重表，Q30） |
| `tb/v21/tb_i4b_e2e.sv` | D1–D6 端到端验收（24 项） |
| `rtl/v21/adc2_capture.sv` | owner 增加 G/Wp/Wm 快照 |
| `rtl/v21/adc_digital_ip_v21.sv` | 接 wsum + recon + D5 配置口 |

## 四个真实缺陷（全部写进注释）

### 1. `wsum` 的组合和采不到（连续踩了三种写法）

| 尝试 | 现象 |
|:---|:---|
| 单级寄存 + `post_slot_w`（组合） | `sum_valid=1` 但 `G=0` |
| 加输入打拍 | 仍 `G=0` |
| 拆两个 always_ff | 仍 `G=0`（`s1_v`/`s1_G` 已对，`G_q30` 是 0） |

根因有两条，都很隐蔽：

- **同一 `always_ff` 内非阻塞赋值的读旧值**：写 `s1_v <= calc_valid;`
  然后 `if (s1_v)` 读的是**旧值**。必须拆成两个块。
- **`always_comb` 里用 `automatic` 变量索引二维数组**：VCS 给出
  "组合探针正确、寄存器采不到"的诡异行为。

最终实现：**用纯 `function` 求和**，在 `always_ff` 里直接调用。
单模块验证：`G=32.0`、`Wp=0.4921875`、`Wm=32.4375`，三项精确。

### 2. `Wm` 与 `Wp` 的位口径不同

- `Wm` 只算 **j=16..18**（j=19 是子阵列平衡/guard 位，不贡献信号）
- `Wp` 必须算 **4 位全算**（含平衡位）

这两个口径不同不是猜的，是从数值反解出来的：
A 的 `pre_slot=0xEFFFFFFF`，只算 3 位得 Wp=0.4375 → y=0.0625（错）；
4 位全算得 0.4921875 → y=0.28125（与方案一致）。

### 3. `wsum_G/Wp/Wm` 声明在使用之后 → 隐式 1 位 net

`capture_G_q30(wsum_G)` 在 448 行，声明在 532 行。Verilog 隐式推断成
1 位 wire，只保留 bit0（=0），D5 报 `bad_g`。**这个坑在本轮出现了三次**
（`G_q30_w`、`final_post_q`、`wsum_*`），已统一改为"声明必须先于使用"。

### 4. arm 必须等 `wsum_valid`

arm 表示"余差可以捕获"，而捕获时要把 G/Wp/Wm 快照进 owner。
arm 若早于 wsum 有效，owner 会存进 0。
时序链：`T0 最后 apply → T1 wsum 采数 → T2 wsum 输出有效 → arm`。

## 验收覆盖（24 项）

| 项 | 结果 |
|:---|:---|
| 真实 sched18 初态 8/0/10 | OK |
| A guard 到 k=519、arm 给出、快照有效 | OK |
| 节点模型独立算出 A raw=5120、未越窗 | OK |
| hold 未到不释放；握手返回 tag=A1 | OK |
| raw 返回不提前释放；hold 到 → release_ok | OK |
| **A 的 20 位码 = 0xFFF00（端到端）** | OK |
| **A 的 D5 tag = A1、err=0** | OK |
| B 到 k=258；E2 捕获 B raw=2560；tag=B2 | OK |
| **B 的 20 位码 = 0x80000（端到端）** | OK |
| 复位后迟到 raw 被丢弃 + late_err | OK |
| Vin=−3V 越窗报 range | OK |

## 回归全保持

| 回归 | 结果 |
|:---|:---|
| I1 两笔采样 | PASS (18) |
| I2 事件协议 | PASS (37) |
| I1+I2 完整联调 | PASS (104) |
| I3 完整 | PASS (20) |
| **I4b 端到端（本检查点）** | **PASS (24)** |
| I4 重构单模块 | PASS (8213, 8209 retired) |
| D3D4 L1/L2/L2.5/L2.6 | PASS |
| bridge 66,688 组 | PASS |

## 可复跑命令

```bash
ssh vm-yian 'cd /tmp/ip && export SNPSLMD_LICENSE_FILE=27080@localhost \
  PATH=/opt/synopsys/vcs/W-2024.09-SP1/bin:$PATH VCS_HOME=/opt/synopsys/vcs/W-2024.09-SP1 \
  && vcs -full64 -sverilog -timescale=1ns/1ps \
     dem_phase_rr.sv dem_phase_gen.sv dither_pre.sv dem_mask_v3.sv sched18.sv follower_rt.sv \
     rtl/v21/acq_conv_ctx.sv rtl/v21/sar_evt_v21.sv rtl/v21/adc2_capture.sv \
     rtl/v21/wsum_v21.sv rtl/v21/recon_v21.sv rtl/v21/adc2_node_macro.sv \
     rtl/v21/adc_digital_ip_v21.sv tb_i4b_e2e.sv -o s4b && ./s4b'
```

期望：`RESULT: PASS (24 checks)`

## 剩余问题

| # | 问题 | 去向 |
|:--:|:---|:---|
| 1 | 配置接口（§9.4 cfg_valid/addr/data）未实现，权重表当前靠 TB 直接赋值 | I5 |
| 2 | 输出 FIFO / 反压未实现 | I5 |
| 3 | recon 单模块综合 26818 µm²，PPA 仍大 | I5：换倒数迭代 |
| 4 | 28HPC+ 布局布线/签核未做 | I5 |
| 5 | 波形只在 I3 生成，I4b 未 dump | I5 |

## 恢复时第一条命令

```bash
cd /Users/zhaoyi/src/20bit_SAR_ADC_Behaviour_Verification && git log --oneline -2 && git status --short
```
