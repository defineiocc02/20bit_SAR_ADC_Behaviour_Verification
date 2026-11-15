# I1 检查点：两笔采样上下文与物理路由

时间：2026-11-15　方案：v1.4 §11.2 I1 / §4.2 / §4.3 / §11.3
**状态：PASS（18 项检查，0 错误）**

## 当前 SHA

- 开工基线：`ac10e3a`（方案核对基线 `377ed52` 之后仅一个文档提交，RTL 零差异）
- I1 交付提交：见本文件所在 commit

## 开工基线实测

| 项 | 结果 |
|:---|:---|
| `tools/verify_d3d4_ip.sh` | L1 PASS / L2 PASS / L2.5 PASS / L2.6 PASS / L3 SKIPPED |
| `tools/bridge_charge_check.py` | 66,688 组，0 违例，PASS |
| 仿真器 | 远端 `vm-yian`，VCS W-2024.09-SP1 |
| 28HPC+ 库 | `/home/yian/PDK/tsmc28/.../tcbn28hpcplusbwp7t40p140ssg0p81vm40c_ccs.db` |
| 许可 | `SNPSLMD_LICENSE_FILE=27080@localhost`（root 与 yian 均有效） |

## 本例新增

| 文件 | 内容 |
|:---|:---|
| `rtl/v21/acq_conv_ctx.sv` | ACQ/CONV 双上下文、四态生命周期、物理片号路由、释放资格 |
| `tb/v21/tb_i1_two_sample.sv` | 两笔 A/B 定向验收（§11.3 向量） |
| `flow/build/filelists/files_adc_v21.f` | v21 生产综合清单 |
| `flow/build/filelists/files_adc_v21_sim.f` | v21 仿真清单 |

**复用未改**：`sched18` / `dither_pre` / `dem_mask_v3` / `dem_phase_rr` / `follower_rt` 全部原样。

## 设计中的两个真实缺陷（都已修）

### 1. 漏掉 SAMPLED 中间态（v1.0 设计错误）

v1.0 把"建立 ACQ"和"搬到 CONV"合成一步，导致 A 在 ACQ 时 `prepare_ready=0`，
B 的 prepare 永远等不到，两笔流水根本转不起来。

正确四态：
```
IDLE --prepare_fire--> ACQ --sample_event--> SAMPLED --下一次 prepare--> CONV --释放--> IDLE
```

### 2. `acq_pre_q` 从未被赋值 + 叶子接错驱动源

`acq_pre_q` 只有复位和复制，没有赋值点 —— 编译能过、综合能过，但 pre 路由全 0。
更深一层：叶子若由 `acq_word`（寄存器）驱动，prepare 那一拍它还是旧值，
算出的 pre 是上一笔的，存入后全错（实测 A 变成 word=0 的结果 `0x00000000`）。

修正：叶子由 **prepare 输入**（`prepare_word` / `prepare_v_pre`）驱动，
pre 在 prepare 沿算好并冻结；`acq_pre_q <= acq_pre_slot_i` 存入描述符。

## 验收覆盖（18 项）

| 判据 | 场景 | 结果 |
|:---|:---|:---|
| A 建立 ACQ、片号 0..7 | 场景 1 | OK |
| A `pre_slot = 0xFFFFFEFF` | 场景 1 | OK |
| E0 后 A 标记 sampled、未进 CONV | 场景 2 | OK |
| **CONV 来自 A 原组**（tag/片号/word=63） | 场景 3 | OK |
| **新 ACQ 是 B**、word=17、未采样 | 场景 3 | OK |
| **B `pre_slot = 0x64444444`** | 场景 3 | OK |
| **A 的冻结 pre 未被覆写** | 场景 3 | OK |
| 未捕获/未 hold → 不释放 | 场景 4 | OK |
| 只有 hold 无捕获 → 不释放 | 场景 4 | OK |
| 捕获+hold+安静窗 → 释放回池 | 场景 5 | OK |
| E1 同沿采 B | 场景 6 | OK |

**这四项是方案 §11.2 I1 的硬要求，全部通过**：
B 开始准备时 A 上下文不变 / 真正采过的原组才进转换 /
旧组在三条件满足前保持 / pre 按物理片号路由逐位对拍。

## 综合确认（无未时钟寄存器）

```
CHK total=176 clocked=176
CHK unclocked=0
```

用 28HPC+ `ssg0p81vm40c`，25 ns 约束，`compile_ultra`。564 cells / 527.53 µm²。
**0 个未时钟寄存器** —— 这是此前踩过的坑（`always_comb` + 变量下标被推断成锁存器），
本模块全部用 generate 展开的独立 `always_comb`，每处显式赋初值。

## 可复跑命令

```bash
# 远端（本机无仿真器）
ssh vm-yian 'mkdir -p /tmp/i1t' 
scp rtl/v21/acq_conv_ctx.sv rtl/dither_pre.sv tb/v21/tb_i1_two_sample.sv vm-yian:/tmp/i1t/
ssh vm-yian 'cd /tmp/i1t && export SNPSLMD_LICENSE_FILE=27080@localhost \
  PATH=/opt/synopsys/vcs/W-2024.09-SP1/bin:$PATH VCS_HOME=/opt/synopsys/vcs/W-2024.09-SP1 \
  && vcs -full64 -sverilog -timescale=1ns/1ps \
     acq_conv_ctx.sv dither_pre.sv tb_i1_two_sample.sv -o s1 && ./s1'
```

期望：`RESULT: PASS (18 checks)`

## 日志与波形位置

- 日志：远端 `/tmp/i1t/r.log`
- 波形：**本轮未保存 FSDB/VCD**。方案要求"双笔波形与自检日志"，
  日志已具备（逐场景 OK/ERROR），波形待补 —— 列入 I2 交付件。

## 剩余问题

| # | 问题 | 影响 | 去向 |
|:--:|:---|:---|:---|
| 1 | 未保存波形 | 方案要求"同一波形标记 prepare/采样/apply/捕获/释放" | I2 一并补 |
| 2 | `conv_hold_mask` 的 18 个 generate 实例各含 8 次比较 | 面积偏大（564 cells） | I5 综合时评估优化 |
| 3 | `sched18` 未在本层例化 | 片组目前由 TB 直接给 `sched_*` | I2 接真实 sched18 |
| 4 | post 通路恒 0、写门关闭 | I1 只做 pre | I2 |
| 5 | capture/hold 由 TB 桩驱动 | 未绑真实宏 | I3 |
| 6 | `qid` 未在描述符内 | 方案 §4.2 要求冻结 qid | I2（随事件协议一起） |

## 恢复时第一条命令

```bash
cd /Users/zhaoyi/src/20bit_SAR_ADC_Behaviour_Verification && git log --oneline -3 && git status --short
```

然后读本文件，从 I2 开始。
