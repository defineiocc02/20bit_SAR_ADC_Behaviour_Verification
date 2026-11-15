# I1+I2 完整联调检查点

时间：2026-11-15　方案：v1.4 §4.1/§4.3、§8.1/§8.2、§11.2 I1/I2、§11.3
**状态：PASS（104 项检查，0 错误）**

## 当前 SHA

- I2 交付：`e426b90`
- 本检查点：见本文件所在 commit

## qid 冻结修复核对（本轮首要）

| 项 | 结果 |
|:---|:---|
| `prepare_qid` 输入 | 已加，随描述符冻结 |
| `acq_qid_q` → `conv_qid_q` | 提升时随其他字段一起搬运 |
| 事件层 qid | 用 `acq_qid`（commit 沿 ACQ 仍是被提升那一笔） |
| 修复前症状 | `qid_ok=0`，A 的 5 个事件全部被拒（apply=0） |

## 四项补充要求

### 1. 旧组释放与末笔排空 ✅

- **同沿优先级**：`release_fire_o && !do_promote` —— promote 优先。
  原来 release 块在后会覆盖 promote 刚写的 `conv_valid_q<=1'b1`，
  造成"B 被提升后立刻消失、conv_* 数据停在 B 上但 valid=0"。
- **被拒 prepare 不改 CONV**：ACQ 未采样时 `prepare_ready=0`，
  实测 A 的 CONV（片号 + tag）未被改动。
- **stop 排空**：新增 `stop_i`。`do_drain = stop_i & acq_can_promote & ~conv_valid_q`，
  已采过的 ACQ 无须新 prepare 即可转 CONV；E2 独立捕获完成后正常释放。

### 2. 物理输出同沿注册 ✅

按 §4.1「某沿 apply_fire 时，沿后 post_wr[sid] 与新 rdac_post_state 一起有效」：
- `post_state_q` / `post_wr_q` 寄存输出，apply 沿后一整拍同时有效
- 写门每拍重新产生，空泡/非法事件为 0
- 数据只在有效写时更新，其余片保持原状态
- 逐事件同时比对：160 位 slot、360 位物理 post、18 位写门、tag/qid/final

**踩过的坑**：测试原本每事件用 360'd0 重建期望，把 A 留在片 0..7 的
合法残留（`0x7FFFF`）当成错误。§4.1 明确"未选物理片保持原状态"，
改用累积影子模型才对。

### 3. F=7 拒绝 512 阈反例 ✅

- 阈被拒后 `prefix` 保持 448（不写 RDAC）
- **阈试探不占提交序号**：`exp_evt_q` 回退到 2，bit5 用序号 2
- 总提交仍 **7 次**（Flash + bit5..bit0）
- c=480（448+32）、U=5 → **k=485**，`final_k=485` 实测通过

**踩过的坑**：测试原本期望阈被拒后序号为 3、bit5 用 3..bit0 用 8，
把正确的 RTL 判成错。

### 4. B 全流程 + 双笔波形 ✅

- B 的 7 个事件逐次全对（k/160/360/18 位/tag/qid/final）
- E1 同沿采 B + 捕获 A 余差
- E2 停收后捕获 C 的最后余差
- 由片号 `mask_of()` 生成写片位图，与 RTL 逐位断言一致

## 验收覆盖（104 项，9 个阶段）

| 阶段 | 内容 |
|:---:|:---|
| 1 | prepare A（guard F=7, word=63, h=0 v=3 coin=1） |
| 2 | A 提升进 CONV；ACQ 未采样时 prepare 被拒且不改 A 的 CONV |
| 3 | A 的 5 事件：k=453/517/517/519/519 + 160/360/18 位全对 |
| 4 | 空泡写门=0；已写状态保持 |
| 5 | 错 qid、done 后多发事件 → 均被拒且写门=0 |
| 6 | E1 采 B + 捕获 A 余差 → A 释放 |
| 7 | B 的 7 事件：k=256×5/258/258 + 全字段对拍 |
| 8 | B 释放 → C 经 `stop_i` 独立转 CONV → E2 捕获 → 释放 |
| 9 | F=7 拒 512 → 回普通六位 → c=480,U=5 → k=485，7 次提交 |

## 综合确认（28HPC+）

```
CHK total=583 clocked=583
CHK unclocked=0
```

`ssg0p81vm40c`，25 ns，`compile_ultra`：**4987 cells / 3019.48 µm²，0 个未时钟单元**。

## 既有资产保持

| 资产 | 结果 |
|:---|:---|
| DEM 133,376 组 | PASS |
| dither pre 512 组 | PASS |
| bridge 66,688 组 | PASS |
| L2 IP 512 笔 | PASS |
| L2.5 sched18 20,000 拍 | PASS |
| L2.6 逐事件对齐 | PASS |
| tb_i1_two_sample 18 项 | PASS |
| tb_i2_events 37 项 | PASS |

## 可复跑命令

```bash
# 单元级
ssh vm-yian 'cd /tmp/ip && export SNPSLMD_LICENSE_FILE=27080@localhost \
  PATH=/opt/synopsys/vcs/W-2024.09-SP1/bin:$PATH VCS_HOME=/opt/synopsys/vcs/W-2024.09-SP1 \
  && vcs -full64 -sverilog -timescale=1ns/1ps \
     dem_phase_rr.sv dem_phase_gen.sv dither_pre.sv dem_mask_v3.sv sched18.sv follower_rt.sv \
     rtl/v21/acq_conv_ctx.sv rtl/v21/sar_evt_v21.sv rtl/v21/adc_digital_ip_v21.sv \
     tb_i1_two_sample.sv tb_i2_events.sv tb_i12_full.sv -o all && ./all'
```

期望：I1 `PASS (18)`、I2 `PASS (37)`、完整联调 `PASS (104)`

## 日志与波形

- 日志：远端 `/tmp/ip/rfull.log`
- **波形：仍未保存 FSDB/VCD**（方案要求双笔波形）。这是本轮唯一未交项，
  列入 I3 首件事（接 ADC2 时会一并加 dump）。

## 剩余问题

| # | 问题 | 去向 |
|:--:|:---|:---|
| 1 | 波形未 dump | I3 首件 |
| 2 | `sched18` 未在顶层例化（片组仍由 TB 给） | I3 |
| 3 | ADC2 捕获/hold 仍是 TB 桩 | I3 |
| 4 | 无 20 位输出 | I4 |
| 5 | `sar_final_i` 输入未使用 | 待确认是否需外部覆盖 |

## 恢复时第一条命令

```bash
cd /Users/zhaoyi/src/20bit_SAR_ADC_Behaviour_Verification && git log --oneline -2 && git status --short
```
