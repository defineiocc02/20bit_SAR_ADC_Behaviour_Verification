# 三类时间预算实测 v3：违例已消除

时间：2026-11-16　基线：`2faa862` + 本轮 S1 拆分
网表：DC W-2024.09-SP3，529 s　角：`ssg0p81vm40c`　周期 25 ns

## 1. 违例已修复

v2 报告的 setup 违例：

```
b_q24_cfg[1] -> u_recon/z1_q_reg[63]   slack -2.36 ns  (VIOLATED)
```

**修复方式**：把 recon 的 S1 级拆成两级。

| 级 | 内容 |
|:---|:---|
| S1a | `Y - b`（只有一次加减） |
| S1b | `alpha` 除法（`alpha == 2^30` 时旁路，只打拍） |

原本一级里同时做加减和 32 位除法，路径 27.34 ns；
拆开后每一级都远小于 25 ns。

**修复后**：

```
slack = +19.38 ns (MET)
Startpoint: w_main_tbl[5440]
Endpoint:   u_wsum/Wm_q30_reg[47]
```

关键路径移到 `wsum` 的权重求和上，这是预期的次重路径。
**全部 setup 违例消除。**

## 2. 综合指标全面改善

| 指标 | v6（拆分前） | **v7（拆分后）** | 变化 |
|:---|--:|--:|:---|
| cells | 151,300 | **127,359** | −15.8% |
| Combinational area | 93,559.82 | **73,736.08** | −21.2% |
| Noncombinational area | 3,796.42 | **2,485.48** | −34.5% |
| **Total cell area** | 97,356.24 | **76,221.56 µm²** | **−21.7%** |
| 未时钟寄存器 | 0 / 2,018 | **0 / 1,311** | −35.1% |
| 最差 slack | **−2.36（违例）** | **+19.38（MET）** | 消除违例 |
| 综合耗时 | 623 s | **529 s** | −15.1% |

**面积减少的意外收益**：拆分后 DC 不再需要为一条超周期的路径
做大量 sizing/buffering，反过来面积和寄存器数都降了。

## 3. 与原型面积的对比（更新）

| 对象 | 面积 | 口径 |
|:---|--:|:---|
| 原型数字区 | 60,000 µm² | 照片图估的版图区域 |
| 本工程完整顶层 | **76,222 µm²** | 标准单元综合面积 |

数值比 **1.27×**（v6 时是 1.62×）。

仍然**不能**说"差 1.27 倍"：口径不同、原型范围未披露、
本结果未做面积优化（`-map_effort medium`，无 `-area_effort`、无 retiming）。

## 4. 三类预算的状态

| 预算 | 状态 |
|:---|:---|
| ① 旧组释放 → 下一组采集 | **未取得独立路径** |
| ② 已决结果 → RDAC 开关生效 | **未取得独立路径** |
| ③ 末次生效 → ADC2 捕获 | hold 组 slack **24.49 ns** |

①② 未取得的原因同 v2 §3：DC ungroup 后寄存器名不带层次前缀，
按模块名过滤全 N/A；按功能名过滤时起终点共享寄存器，
`-from/-to` 取不到清晰边界。

**正确做法**（本轮仍未做）：
- 用 `report_timing -through` 按中间组合节点切分
- 或 `set_ungroup false` 保留层次后重跑

## 5. 功能验证（拆分后全过）

| 测试 | 结果 |
|:---|:---|
| `tb_i4_recon` | **PASS (8213 checks, 8209 retired)** |
| `tb_i4b_e2e` | **PASS (25 checks)** |

流水从 6 拍变 7 拍，两个测试的等待拍数已同步调整。

## 6. 复跑

```bash
# 综合（约 529 s）
ssh vm-yian 'rm -rf /tmp/dcwork && mkdir -p /tmp/dcwork &&
  PATH=/opt/synopsys/syn/W-2024.09-SP3/bin:$PATH \
  SNPSLMD_LICENSE_FILE=27080@localhost \
  dc_shell -f /tmp/dc_synth_v21.tcl > /tmp/dcwork/dc.log 2>&1'

# 违例检查（必须为 MET）
scp flow/synth/v21/sta_budget.tcl vm-yian:/tmp/
ssh vm-yian 'PATH=/opt/synopsys/syn/W-2024.09-SP3/bin:$PATH \
  SNPSLMD_LICENSE_FILE=27080@localhost dc_shell -f /tmp/sta_budget.tcl'
```
