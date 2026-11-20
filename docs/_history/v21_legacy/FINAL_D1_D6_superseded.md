# ISSCC 2024 9.8 数字复现：D1–D6 状态表

时间：2026-11-16　基线：`054cfed`
工艺：TSMC 28HPC+（原型 40 nm ULP）　规格：20 bit / 40 MS/s / ±3 V

> **本表只写当前版本 `054cfed` 上重新跑过的结果。**
> 此前报过的综合数字（89,693 / 97,555 / 108,951 µm²）与门级结论
> （"1464 拍差异 0"）都已作废，原因见 §4，不在本表引用。

## 1. D1–D6 功能状态

| 模块 | 内容 | RTL | 回归 |
|:---|:---|:---:|:---:|
| **D1** | 18 片 8/8/2 调度（`sched18`） | ✅ | 4096 拍逐位 + L2.5 20000 拍 |
| **D2** | Flash/SAR 逐事件 follower（`sar_evt_v21`） | ✅ | I2 37 项 + I1I2 105 项 |
| **D3** | DEM 3+3+3 + bridge | ✅ | L1 133376 对 + bridge 66688 组 |
| **D4** | 分裂 dither | ✅ | L1 512 点 + L2 512 笔 |
| **D5** | 定点重构（`recon_v21` + `wsum_v21`） | ✅ | I4 8213 项 + 连续输入 5 场景 |
| **D6** | 事务/宏控制（`acq_conv_ctx` + `adc2_capture`） | ✅ | 2 反例 + I3 20 项 + I4b 25 项 |

## 2. 在 `054cfed` 上重新跑过的验证

| 验证 | 结果 | 入口 |
|:---|:---|:---|
| 8 项 RTL 回归 | **全绿** | `tools/verify_v21.sh` |
| 连续输入 5 场景 | **`RECON_STREAM_MATCH`**，0 错 | `tools/check_recon_stream.sh` |
| IDLE 分区 | **8/0/10、8/0/10、8/8/2**，并集 18、交集 0 | `tb/v21/tb_idle_partition.sv` |
| 任意权重 wsum | **364 笔 × 3 字段 0 处不符** | `tools/check_wsum_equiv.sh` |

关键码值：A `raw=5120` → **0xFFF00**；B `raw=2560` → **0x80000**。

## 3. 本轮修复的三个真实缺陷

| 缺陷 | 表现 | 提交 |
|:---|:---|:---|
| `recip_q60` LUT 用 `initial` 初始化 | DC 丢弃（`VER-281`）→ 网表 `i_q60` 恒为常数 2 | `878a348` |
| IDLE 直接取 `sched_idle`（推进后位图） | 稳定 A/B 为 8/8/**1**，漏一片 | `6b2cc7b` |
| recon 用实时总线取参数 | 连续 10 笔中 **8 笔码错**（A 期望 0x80000 实得 0x90000） | `fe7535f` |

三项均在 `054cfed` 上复验通过。

## 4. 后端状态：阻塞

| 项 | 状态 |
|:---|:---|
| **同版网表（`054cfed`）** | ❌ **未生成**。顶层综合停在 `Processing 'wsum_v21'`，四组受控实验全部复现 |
| 门级等价（当前版本） | ❌ 未做（依赖网表） |
| Formality（当前版本） | ❌ 未做（依赖网表） |
| `u_wsum` 20 个 failing | ❌ 未解决（对应旧网表 `485abbbd`，不是当前版本） |
| 布局布线 / 后布线 STA / DRC / LVS / GDS | ❌ 未做 |

### 作废的历史数字（不得再引用）

| 数字 | 作废原因 |
|:---|:---|
| 89,693 µm² | `g_q30` 位宽不匹配 → `recip_rnd_v21` 变黑盒 |
| 76,222 µm² | `v1_q` 重复声明 → `recon_v21` 变黑盒 |
| 97,555 µm² / +9.39 ns | LUT 被 DC 丢弃，网表功能错误 |
| 108,951 µm² / +3.02 ns | 驱动单元修正后测，但 **RTL 是 `1fb9408`，不含本轮两项功能修复** |
| "门级 1464 拍差异 0" | 基于错误网表；且 1464 是**日志行数**，仅 90 个观察沿、2 笔输出 |

## 5. 现有可用产物

| 产物 | 哈希 | 对应 RTL |
|:---|:---|:---|
| `/tmp/dcwork/adc_digital_ip_v21_syn.v` | `485abbbd` | `878a348`（含 LUT 修复，无驱动修正） |
| `/tmp/dcwork4/adc_digital_ip_v21_syn.v` | `769cc61f` | `1fb9408`（含驱动修正，无本轮功能修复） |

**两者都不是 `054cfed` 的同版网表。**

## 6. 复现命令

```bash
# RTL 级（当前版本，全部可跑）
D3D4_RUNNER=vm-yian bash tools/verify_v21.sh
D3D4_RUNNER=vm-yian bash tools/check_recon_stream.sh
D3D4_RUNNER=vm-yian D3D4_NETLIST=/tmp/dcwork4/adc_digital_ip_v21_syn.v \
  bash tools/check_wsum_equiv.sh
D3D4_RUNNER=vm-yian python3 tools/prove_xwidth.py
D3D4_RUNNER=vm-yian python3 tools/check_recip_correction.py
```

综合阻塞的详细记录见 `SYNTH_STALL.md`。
