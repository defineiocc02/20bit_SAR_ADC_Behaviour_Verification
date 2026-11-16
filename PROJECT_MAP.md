# 仓库地图

**先看 [`README.md`](README.md)。** 本文只解决"哪个文件是现行的、哪个是历史的"。

---

## 一、当前生产链（v21）

D1–D6 全链。改动只应发生在这里。

| 层 | 文件 |
|:---|:---|
| **顶层** | `rtl/v21/adc_digital_ip_v21.sv` |
| **D1** | `rtl/sched18.sv` |
| **D2** | `rtl/v21/sar_evt_v21.sv` |
| **D3** | `rtl/dem_mask_v3.sv`、`rtl/dem_phase_rr.sv`、`rtl/dem_phase_gen.sv` |
| **D4** | `rtl/dither_pre.sv` |
| **D5** | `rtl/v21/recon_v21.sv`、`rtl/v21/wsum_v21.sv` |
| **D5 叶子** | `rtl/recip_q60.sv`、`rtl/v21/recip_rnd_v21.sv` |
| **D6** | `rtl/v21/acq_conv_ctx.sv`、`rtl/v21/adc2_capture.sv` |
| **仿真边界** | `rtl/v21/adc2_node_macro.sv`（非综合） |
| 桥接 | `rtl/follower_rt.sv` |

### 测试台（`tb/v21/`）

**在用**（被 `tools/verify_v21.sh` 或专项入口引用）：

| 文件 | 用途 | 入口 |
|:---|:---|:---|
| `tb_acq_ctx_fixed.sv` | 两项控制反例 | `verify_v21.sh` |
| `tb_audit3.sv` | 审计 3 项 | 同上 |
| `tb_i12_full.sv` | I1/I2 集成 105 项 | 同上 |
| `tb_i3_full.sv` | I3 两笔余差 20 项 | 同上 |
| `tb_i4b_e2e.sv` | 端到端 25 项 | 同上 |
| `tb_e2e_decl.svh` / `tb_e2e_body.svh` | 上者的共享声明与激励体 | 同上 |
| `tb_recip_rnd.sv` | 修正倒数 20817 笔 | 同上 |
| `tb_analog_if.sv` | 审查方 54 项反例 | 同上 |
| `tb_recon_stream.sv` | **连续输入** 5 场景 | `check_recon_stream.sh` |
| `tb_wsum_scan.sv` | 任意权重 364 笔 | `check_wsum_equiv.sh` |
| `tb_idle_partition.sv` | IDLE 分区 18 片 | 手工 |
| `tb_trace.sv` | 门级轨迹 | `check_gate_e2e.sh` |

**历史/不再单独运行**：`tb_i1_two_sample.sv`、`tb_i2_events.sv`、`tb_i3_adc2.sv`、
`tb_i4_recon.sv`、`tb_i12_integration.sv`、`tb_gate_equiv.sv`
—— 其覆盖已被上表合并或被 `check_recon_stream.sh` 取代。**保留作追溯，不是入口。**

---

## 二、验收入口

**只有一个总入口**：`tools/verify_v21.sh`（8 项回归 + [A]/[B] 证据）。

专项入口：
`check_recon_stream.sh`、`check_wsum_equiv.sh`、`check_gate_e2e.sh`、
`check_formal.sh`、`check_rtl_mutation.sh`、`check_rtl_rnd96.py`。

**历史入口**：`verify_d3d4_ip.sh`、`verify_d3d4.sh`、`run_topw.sh`、`topw_mutation.sh`、
`verify_clean_clone.sh` —— 属旧链，见 §四。

---

## 三、文档

| 目录 | 内容 |
|:---|:---|
| `docs/v21/` | **本轮全部报告**。首选 `FINAL_D1_D6.md` |
| `docs/` | 合同（`CONTRACT_CURRENT.md`）、算法说明、旧 IP 接口 |
| `docs/_history/` | 早于合同 v2.1，仅追溯 |

`docs/v21/` 里已被取代的文档已在 `db238e8` 删除（13 个），
现存 27 份均为现行或阶段性 checkpoint。

---

## 四、历史资产（不是交付主线）

| 资产 | 是什么 | 为什么还在 |
|:---|:---|:---|
| `rtl/d3d4_ip.sv` | 早期 D3/D4 独立 IP | 叶子级穷举对拍（133,376+512）仍作 v21 的依据 |
| `rtl/digital_top_w.sv` | 旧全链顶层（4+3+2 / `c−U`） | 兼容资产，**不含** v21 的 D5/D6 |
| `tools/verify_d3d4_ip.sh` 等 | 旧链入口 | 同上 |

**它的综合数字（1770.08 µm² / 2.82 ns）只对应 `d3d4_ip`，不代表 v21 全链。**

---

## 五、禁止事项

1. **不要引用作废数字**：89,693 / 76,222 / 97,555 / 108,951 µm²、"门级 1464 拍差异 0"。
   作废原因见 `docs/v21/FINAL_D1_D6.md` §4。
2. **不要把 `d3d4_ip` 的数字当 v21 的**。
3. **不要把 `[A]`/`[B]`/`[C]` 混为一谈**（数学证明 / 生产 RTL 检查 / 工具形式化）。
4. **不要用无同版网表做门级或形式结论**——当前 `054cfed` 没有网表。
