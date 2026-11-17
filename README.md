# 20bit SAR ADC 数字 IP（ISSCC 2024 9.8 复现）

依据 ISSCC 2024 Session 9.8 公开机制、按明确工程假设实现的**可综合数字 IP**。
不是 ADI 硅片的还原（公开资料不足），也不声称与该芯片逐位一致。

规格：20 bit / 40 MS/s / ±3 V 差分　后端：TSMC 28HPC+（原型 40 nm ULP）

---

## 1. 先看这里：当前状态一句话

**RTL 功能已经验证；后端（网表）卡在综合，尚未产出当前版本的网表。**

| 层 | 状态 |
|:---|:---|
| D1–D6 RTL 功能 | ✅ 8 项回归全绿，两笔端到端 A=`0xFFF00` / B=`0x80000` |
| 本轮修掉的三个真实缺陷 | ✅ LUT `initial`、IDLE 8/8/1、recon 连续输入串样 |
| **同版网表（综合）** | ❌ **未生成**——顶层停在 `Processing 'wsum_v21'` |
| 门级等价 / 形式等价（当前版本） | ❌ 未做（依赖网表） |
| 布局布线 / DRC / LVS / GDS | ❌ 未做 |

**唯一权威状态表：[`docs/STATUS.md`](docs/STATUS.md)**
（其它 `docs/v21/*.md` 是专项证据；`docs/v21/_history/` 是已作废版本，不得引用）

> **历史数字已全部作废，不要再引用。** 曾报过的 89,693 / 76,222 / 97,555 /
> 108,951 µm² 与"门级 1464 拍差异 0"都有明确的作废原因，
> 见 `STATUS.md` §5。本条是硬约束。

---

## 2. 生产链文件（v21）

**这才是当前在用的链。** 全部位于 `rtl/v21/` 与 `tb/v21/`。

| 模块 | 文件 | 职责 |
|:---|:---|:---|
| **顶层** | `rtl/v21/adc_digital_ip_v21.sv` | 单时钟域，79 端口 |
| **D1** | `rtl/sched18.sv` | 18 片池调度（8 采集 / 8 转换 / 2 备用） |
| **D2** | `rtl/v21/sar_evt_v21.sv` | Flash/SAR 逐事件 follower |
| **D3** | `rtl/dem_mask_v3.sv` + `rtl/dem_phase_rr.sv` + `rtl/dem_phase_gen.sv` | DEM 3+3+3 + 相位 |
| **D4** | `rtl/dither_pre.sv` | 分裂 dither |
| **D5** | `rtl/v21/recon_v21.sv` + `rtl/v21/wsum_v21.sv` | 20 位定点重构（权重经窄配置口写入内部寄存器堆）|
| **D5 叶子** | `rtl/recip_q60.sv` + `rtl/v21/recip_rnd_v21.sv` | Newton 倒数 + 余数修正 |
| **D6** | `rtl/v21/acq_conv_ctx.sv` + `rtl/v21/adc2_capture.sv` | 事务/宏控制、ADC2 捕获 |
| **仿真边界** | `rtl/v21/adc2_node_macro.sv` | 节点宏（非综合） |
| 桥接 | `rtl/follower_rt.sv` | RDAC follower |

---

## 3. 一条命令验证

```bash
D3D4_RUNNER=vm-yian bash tools/verify_v21.sh
```

8 项回归 + 算术证据，预期全部 PASS：

```
tb_acq_ctx_fixed   两反例已修复
tb_audit3          10 checks
tb_i12_full        105 checks
tb_i3_full         20 checks
tb_i4b_e2e         25 checks
tb_recip_q60       43766 笔逐位
tb_recip_rnd       20817 笔逐位
tb_analog_if       54 checks（审查方原版反例）

[A] 数学恒等式 RND(n,96)            PROVEN
[A] X 的 32 位截断 |X|<2^31          PROVEN（前提 A1-A5）
[B] 生产 RTL 的 rnd96_const          PASS（从源码抽取）
[C] VC Formal 属性证明               未完成（不计入通过）
```

### 专项入口

| 命令 | 验什么 | 当前状态 |
|:---|:---|:---|
| `bash tools/check_recon_stream.sh` | 重构流水**连续输入**（相邻拍不同笔） | ✅ `RECON_STREAM_MATCH`，5 场景 0 错 |
| `bash tools/check_wsum_equiv.sh` | `wsum` **任意权重** 364 笔 | ✅ 0 处不符 |
| `bash tools/check_gate_e2e.sh` | 门级等价（两笔完整事务） | ⚠️ 依赖网表，当前无同版网表 |
| `bash tools/check_formal.sh` | Formality 形式等价 | ⚠️ 同上；最近结果 FAILED 20 failing |
| `bash tools/check_rtl_mutation.sh` | 证明检查有区分力 | ✅ |

所有脚本遵守同一约定：**空日志 / 截断 / 覆盖不足 / 任一检查未出现 PASS → 非零退出**。

---

## 3.5 权重表接口（v2.0 结构变更）

权重表**不再是端口数组**。原来是 18×16×40 + 18×4×40 = **14,400 根输入线**，
使 `wsum_v21` 的查表展开成 2,304 路 × 40 位 MUX；
`wsum` 单独综合 594 s / 112,371 cells（占顶层 68%），
顶层 `medium` 四次全部停在 `Processing 'wsum_v21'`，顶层端口数 15,775 也失真。

现在改为 `wsum_v21` **内部寄存器堆**（360 个 40 位字）+ 窄配置口：

```systemverilog
cfg_we, cfg_sel(0=主/1=子), cfg_sid[4:0](0..17), cfg_idx[4:0], cfg_data[39:0]
```

一拍写一个字，上电后灌 360 个字，运行期只读。
**顶层端口从 15,775 降到约 1,426。**

TB 侧用 `wt_write()` / `wt_load()` 灌权重（见 `tb/v21/tb_e2e_decl.svh`）。

> 门级工具（`tb_trace`、`tb_gate_equiv`、`gen_gate_tb.py`、`gen_wsum_gate_tb.py`）
> 仍按旧扁平端口，**对当前 RTL 不适用**，已在文件头标注。
> 待用新 RTL 重新综合后按新接口更新。

## 4. 三个类别必须分清

| 类别 | 含义 | 本仓库例子 |
|:---|:---|:---|
| **[A] 数学证明** | 不等式推导 + 全区间机器验证 | `prove_rnd96.py`、`prove_xwidth.py`、`check_recip_correction.py` |
| **[B] 生产 RTL 检查** | 从**生产源码**抽取逻辑，逐点对拍 | `check_rtl_rnd96.py`（74,618 点） |
| **[C] 工具形式化** | Formality / VC Formal | **未完成** |

**三者不互相替代。** `[A]` 过了不代表 `[B]` 过，`[B]` 过了不代表 `[C]` 过。

---

## 5. 目录结构

```
rtl/                 D1–D4 叶子（sched18 / dem_* / dither_pre / follower_rt）
rtl/v21/             生产链：顶层 + D2/D5/D6
tb/v21/              v21 测试台
tools/               验收入口（verify_v21.sh 为总入口）
flow/synth/v21/      DC 综合、STA、Formality 脚本
formal/v21/          VC Formal 尝试（未完成）
docs/v21/            本轮全部报告（见下）
docs/                合同、算法说明、历史 checkpoint
references/          ISSCC 9.8 公开资料
src/adi_model/       Python 参考模型（DEM/Dither/重构）
```

### docs/v21 现行文档

| 文件 | 内容 |
|:---|:---|
| **`STATUS.md`** | **唯一权威状态表**（能/不能复现什么、作废数字清单）|
| `SYNTH_STALL.md` | 综合停住的四组受控实验 |
| `FORMAL_STATUS.md` | Formality 当前状态 |
| `LUT_SYNTH_BUG.md` | `initial` 被综合丢弃的缺陷 |
| `RECON_ALIGN_FIX.md` | 连续输入串样修复 |
| `DRIVING_CELL_BUG.md` | 驱动单元静默失败 |
| `GATE_EQUIV.md`、`TIMING_BUDGET_FINAL.md`、`PROOF_LEDGER.md`、`PPA_DIV_VS_RECIP.md` | 专项记录 |
| `CHECKPOINT_I1…I4B.md`、`CHECKPOINT_AUDIT3.md` | 各阶段 checkpoint |

---

## 6. 阅读顺序

1. **本文**（状态 + 怎么跑）
2. [`docs/STATUS.md`](docs/STATUS.md)（能复现什么、卡在哪、不能声称什么）
3. [`docs/CONTRACT_CURRENT.md`](docs/CONTRACT_CURRENT.md)（适用哪份合同）
4. [`docs/v21/SYNTH_STALL.md`](docs/v21/SYNTH_STALL.md)（后端卡在哪）
5. [`docs/D3D4_IP_INTERFACE.md`](docs/D3D4_IP_INTERFACE.md)（旧 IP 的接口，仅历史参考）

---

## 7. 历史资产（不是当前生产链）

`d3d4_ip` 是更早的 D3/D4 独立 IP（`rtl/d3d4_ip.sv` + `tools/verify_d3d4_ip.sh`），
有自己的综合与门级等价记录。**它不含 D2/D5/D6，不是本项目的交付主线。**

保留原因：DEM / dither 的叶子级穷举对拍（133,376 + 512 组）与
`sched18` 统计仍在用，可作为 v21 的底层依据。

用它的入口是 `tools/verify_d3d4_ip.sh`；其综合数字（1770.08 µm² 等）
**只对应 d3d4_ip 这个小 IP，不代表 v21 全链**。

---

## 8. 已知边界

| 项 | 说明 |
|:---|:---|
| 范围 | D1–D6 数字链。不含模拟、不含完整数字系统 |
| 后端 | **TSMC 28HPC+ 综合未完成**（详见 SYNTH_STALL.md）。无 P&R / CTS / DRC / LVS |
| 形式等价 | **未通过**（FAILED，20 failing，对应旧网表） |
| hold / 功耗 | **不能签核** |
| 40 MS/s 吞吐 | **未证明**——单路径 slack 不能证明整笔事务吞吐 |
| 模拟域 | 参考建立 / RA / 安静窗不在数字 STA 范围 |
| 来源 | `3+3+3`、8 宏列×16 半单元、β=1/8、guard 512–520 均为 `[E]` 工程补全 |

---

## 9. 复现

```bash
git clone <repo> && cd 20bit_SAR_ADC_Behaviour_Verification
D3D4_RUNNER=vm-yian bash tools/verify_v21.sh
```

环境说明见 [`docs/v21/I5_ENVIRONMENT.md`](docs/v21/I5_ENVIRONMENT.md)。
远端工具与调用规范见 `reports/eda_environment/vm-yian-tools.md`。
