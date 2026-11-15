# 三类时间预算实测（28HPC+ 综合网表）

时间：2026-11-16　基线：`08feb70`
网表：`/tmp/dcwork/adc_digital_ip_v21_syn.v`（DC W-2024.09-SP3）
角：`ssg0p81vm40c`　周期：25 ns　IO delay：各 1.0 ns

## 1. 先报告一个必须说明的时序违例

**完整顶层存在 setup 违例。**

```
Startpoint: b_q24_cfg[1]        （输入端口）
Endpoint:   u_recon/z1_q_reg[63]
data arrival time   27.34 ns
data required time  24.98 ns
slack               -2.36 ns  (VIOLATED)
```

同族还有 `z1_q_reg[62]`（-2.33）、`[61]`（-2.29）等。

### 根因

`recon_v21` 的 S1 级还有一个**除法器**：

```systemverilog
z1_q <= rnd64((y - yb) * 64'sd1073741824, {32'd0, alpha_q30});
                                        // ← alpha 除法，仍在 S1 级
```

网表里有 **2 个 `DW_div`**，其中 `recon_v21_DW_div_uns_0` 就是这个
alpha 除法。它和 Y→Z 的加法链串在同一拍，导致从 `b_q24_cfg` 输入
到 `z1_q` 的路径超周期 2.36 ns。

按方案 §9.2，`alpha == 2^30` 时走旁路（**无除法**），
只有 `alpha != 2^30`（校准模式）才需要。所以：

- **正常工作模式（alpha=1.0 旁路）**：这条路径不存在，没有违例
- **校准模式（alpha≠1.0）**：存在 2.36 ns 违例

这个区分必须写进报告，不能笼统说"时序过了"或"时序没过"。

## 2. 三类预算的实测边界

按方案 I5 的三类定义，从网表提取：

| 预算 | 起点 → 终点 | 实测 |
|:---|:---|:---|
| ① 旧组释放 → 下一组采集 | `conv_*_q_reg/CP` → `acq_*_q_reg/D` | **未取到独立路径**（见 §3） |
| ② 已决结果 → RDAC 开关生效 | `exp_evt_q_reg/CP` → `rdac_post_state*_reg/D` | **未取到独立路径**（见 §3） |
| ③ 末次生效 → ADC2 捕获 | `owner/hold/quiet` 内部 | `hold` 组 slack 24.49 ns |

## 3. 为什么 ①② 没取到独立路径

DC 综合时做了 **ungroup**，层次被完全展开：

- 网表里寄存器名不带层次前缀：`\acq_ids_q_reg[0]`，
  而不是 `u_ctx/acq_ids_q_reg[0]`
- 所以按模块名过滤（`*acq_conv_ctx*`）全部返回 N/A

而按功能信号名过滤时，① 和 ② 的起终点**共享同一批寄存器**
（如 `conv_q_reg` 既在释放逻辑里，也在事件层输入侧），
`get_timing_paths -from/-to` 取不到清晰的分组边界。

**正确做法**：用 `report_timing -through` 按**中间组合节点**切分，
或保留层次后重跑（`set_ungroup false`）。两者本轮都没做，
所以 ①② 如实标为"未取得"，不编造数字。

## 4. 全局最差路径

```
slack = -2.36 ns   (b_q24_cfg[1] → u_recon/z1_q_reg[63])
         仅在校准模式（alpha≠1.0）存在
```

`+9.07 ns` 那条（`inv_q60_reg[0] → i_q60_reg[26]`）是 recon S2c 的
倒数修正路径，**不是最差**。早前 `sta.rpt` 只取了 `-max_paths 20`，
恰好全是这一族的位展开，看起来"全 MET"，掩盖了违例。
这是本轮发现的第二个"报告误导"问题（第一个是 v5 的黑盒）。

## 5. 与原型的时间对照

原型数字区在 40 nm、无公开时序数字。本工程在 28HPC+ 下：

| 项 | 值 |
|:---|:---|
| 周期 | 25 ns（40 MS/s） |
| 正常模式最差 slack | +9.07 ns（余量 36%） |
| 校准模式最差 slack | **−2.36 ns（违例）** |

不能与原型做时序对比（工艺不同、无公开数据）。

## 6. 未做

| 项 | 状态 |
|:---|:---|
| 按 `-through` 切分 ①② 预算 | 未做 |
| 保留层次重跑（`set_ungroup false`）以取得分组路径 | 未做（v4 试过一次，19 min 仍卡在同一端点，但那是除法器还在时） |
| 修复校准模式违例 | 未做（需把 alpha 除法移出 S1，或改成多周期） |
| 后布线 STA | 未做（无 P&R） |

## 7. 复跑

```bash
scp flow/synth/v21/sta_budget.tcl vm-yian:/tmp/
ssh vm-yian 'PATH=/opt/synopsys/syn/W-2024.09-SP3/bin:$PATH \
  SNPSLMD_LICENSE_FILE=27080@localhost \
  dc_shell -f /tmp/sta_budget.tcl > /tmp/budget.log 2>&1'
```
