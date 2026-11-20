# I2 检查点：逐事件开关控制（v2.1 协议）

时间：2026-11-15　方案：v1.4 §8.1/§8.2、§11.2 I2、§11.3
**状态：PASS（37 项检查，0 错误）**

## 当前 SHA

- I1 交付：`16ff803`（+ `b195c9b` 修 gitignore）
- I2 交付：见本文件所在 commit

## 复用了什么

| 资产 | 用法 | 是否改动 |
|:---|:---|:---|
| `rtl/dem_mask_v3.sv` | 3+3+3 译码 + bridge | **未改** |
| `rtl/dither_pre.sv` | 字→八片 pre 译码 | **未改** |
| `rtl/sched18.sv` | 8/8/2 片组递推 | **未改** |
| `rtl/dem_phase_rr.sv` | round-robin 相位 | **未改** |
| `rtl/follower_rt.sv` | 旧协议 9 事件 / `prefix−U` | **未改**（旧链 8509 笔依赖它） |
| `tools/bridge_charge_check.py` | bridge 66,688 组守恒 | 复跑仍 PASS |

## 实际补了什么

| 文件 | 内容 |
|:---|:---|
| `rtl/v21/sar_evt_v21.sv` | v2.1 事件协议：Flash+6 SAR=7 / guard 5 事件、`k=prefix+U` 10 位容器、原子应用资格 |
| `tb/v21/tb_i2_events.sv` | 6 个场景 37 项检查 |

**为什么新写而不是改 `follower_rt`**：旧协议（9 事件、`prefix−U`、9 位 k、guard 不可达）
绑着旧链 8509 笔与 6246 事件两套回归。原地改会同时破坏旧证据与新协议，
所以另立一份，旧的保留作为"旧协议兼容性"资产。

## 事件表（方案 §8.1，[E] 工程拟定次序）

| 路径 | 提交次序 | 次数 |
|:---|:---|:--:|
| 普通 F<7 | Flash `prefix=F<<6` → bit5…bit0 | **7** |
| guard F=7 | Flash 448 → 阈 512 → bit2…bit0 | **5** |

**关键：Flash 本身算第 1 次提交**。早期版本把 Flash 放在 `start` 里不计序号，
导致整条序列错位一个事件（实测 evt2 期望 517 却读到 453）。

## 验收覆盖（37 项）

| 场景 | 内容 | 结果 |
|:---|:---|:---|
| A | guard 路径，U=5，F=7：k = **453/517/517/519/519** | 全 OK |
| A | `final_k=519`、`done`、`final_applied` | OK |
| B | 普通路径，U=0，F=4：k = **256×5/258/258** | 全 OK |
| B | `final_k=258`、`done` | OK |
| C | done 后多发事件 → 物理写门关闭 | OK |
| D | 错 qid → 拒绝 + 粘滞 err | OK |
| E | 乱序序号（期望 1 给 3）→ 拒绝 | OK |
| F | k>520（prefix=516+U=5=521）→ `k_bad` 置位且 **不产生 apply_fire** | OK |

**A/B 的 k 序列与方案 §11.3 逐值一致。**

## 三个真实缺陷（都已修）

1. **事件序号错位**：Flash 未计入提交序号 → 全部 k 滞后一个事件。
2. **`k_o` 用了 `prefix_q`（提交前）而非 `prefix_accept`（提交后）**：
   evt2 期望 517 读到 453。方案 §8.2 要求"每个已决事件应用对应的物理开关"，
   那次应用用的就是提交后的 k。
3. **测试采样点错**：在 posedge 后 #1 读组合信号，此时 `exp_evt_q` /
   `prefix_q` 已被非阻塞赋值推进，读到的是"下一事件的值"。
   正确做法是在 posedge **之前**读（实测该点 `can_apply=1 apply=1 k=453`，
   与方案完全吻合）。这条记在 TB 注释里，避免下次重踩。

## 综合确认

```
CHK total=65 clocked=65
CHK unclocked=0
```

28HPC+ `ssg0p81vm40c`，25 ns，`compile_ultra`：**236 cells，0 个未时钟单元**。

## 可复跑命令

```bash
ssh vm-yian 'cd /tmp/i1t && export SNPSLMD_LICENSE_FILE=27080@localhost \
  PATH=/opt/synopsys/vcs/W-2024.09-SP1/bin:$PATH VCS_HOME=/opt/synopsys/vcs/W-2024.09-SP1 \
  && vcs -full64 -sverilog -timescale=1ns/1ps sar_evt_v21.sv tb_i2_events.sv -o s2 && ./s2'
```

期望：`RESULT: PASS (37 checks)`

## 剩余问题

| # | 问题 | 去向 |
|:--:|:---|:---|
| 1 | 未与 `dem_mask_v3` 串接（尚未生成 160 位 post） | I2 联调（下一步） |
| 2 | `apply_fire` 尚未驱动物理 `post_wr`/`rdac_post_state` | I2 联调 |
| 3 | 未与 `acq_conv_ctx` 的 CONV 上下文绑定 | I2 联调 |
| 4 | 波形未保存 | I2 联调一并补 |
| 5 | guard 退回路（阈被拒→回普通 7 事件）只做了逻辑，未单独测 | I2 联调 |
| 6 | `sar_final_i` 输入未使用（末事件由内部 `last_evt` 判定） | 待确认是否要外部覆盖 |

## 恢复时第一条命令

```bash
cd /Users/zhaoyi/src/20bit_SAR_ADC_Behaviour_Verification && git log --oneline -3 && git status --short
```

然后从"剩余问题 1–3"继续：把 `sar_evt_v21` + `dem_mask_v3` + `acq_conv_ctx`
串成完整 IP，用同一组 A/B 向量做逐事件 160 位 mask 对拍。
