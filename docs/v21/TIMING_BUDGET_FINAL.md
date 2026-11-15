# 三类时间预算实测（最终）

时间：2026-11-16　基线：`4c3a19f`
网表：`/tmp/dcwork/adc_digital_ip_v21_syn.v`（DC W-2024.09-SP3，619 s）
角：`ssg0p81vm40c`（SS 0.81 V −40 °C）　周期：25 ns（40 MS/s）
IO 约束：input/output delay 各 1.0 ns，`BUF_X2` 驱动，0.05 pF 负载

## 1. 三类预算实测

换算：`阶段可用时间 = 周期 − slack = 25 − slack`

### ① 旧组释放 → 下一组完成采集

| 量 | slack | 可用时间 | 占周期 |
|:---|---:|---:|---:|
| CONV 状态 → 安静窗计数 | 24.57 ns | 0.43 ns | 1.7% |
| 安静窗 → ACQ 描述符锁存 | 23.72 ns | 1.28 ns | 5.1% |
| **整段（CONV → ACQ 锁存）** | **23.73 ns** | **1.27 ns** | **5.1%** |

### ② 每次已决结果 → 物理 RDAC 开关生效

| 量 | slack | 可用时间 | 占周期 |
|:---|---:|---:|---:|
| 事件层内部（最长一段） | 21.87 ns | 3.13 ns | 12.5% |
| → `rdac_post_state_o` | 23.79 ns | 1.21 ns | 4.8% |
| → `rdac_pre_state_o` | **20.38 ns** | **4.62 ns** | **18.5%** |

**pre 侧比 post 侧紧**（20.38 vs 23.79）：pre 通路要经 `dither_pre`
的相位选择，组合逻辑更长。

### ③ 末次生效 → 经参考、RA、安静窗 → ADC2 捕获

| 量 | slack | 可用时间 | 占周期 |
|:---|---:|---:|---:|
| → `adc2_capture_arm_o`（沿前授权） | 22.59 ns | 2.41 ns | 9.6% |
| hold 判定内部 | 23.45 ns | 1.55 ns | 6.2% |
| 安静窗内部 | 22.29 ns | 2.71 ns | 10.8% |

**注意**：数字侧只覆盖到 `arm` 与 `hold/quiet` 的**逻辑判断**。
参考建立、RA 建立、模拟安静窗这三段属模拟域，不在本 STA 范围内。
方案 §10 的这类预算需要模拟侧给出，数字侧只能保证"判定逻辑"够快。

## 2. 全局最差路径

```
slack = +9.39 ns (MET)
Startpoint: u_recon/u_recip_rnd/u_recip/inv_q60_reg[0]/CP
Endpoint:   u_recon/u_recip_rnd/i_q60_reg[26]
```

`recip_q60` 的 Newton 迭代输出寄存器，25 ns 下余量 37.6%。
**全部 setup 违例为 0。**

## 3. 关键路径与最紧预算的对照

| 项 | slack | 换算后 |
|:---|---:|---:|
| 全局最差（Newton 迭代） | +9.39 ns | 15.61 ns 用掉 |
| 最紧的预算项（RDAC pre） | +20.38 ns | 4.62 ns 用掉 |

全局最差比三类预算里最紧的一项还紧 **11 ns**。
也就是说：**三类预算都不是瓶颈，recon 的倒数迭代才是。**

## 4. 面积与资源

| 指标 | 值 |
|:---|---:|
| Total cell area | **97,554.69 µm²** |
| cells | 151,346（组合 93,575.79，时序 3,978.90） |
| 未时钟寄存器 | **0 / 2,116** |
| 综合耗时 | 619 s |

与原型对比：97,555 / 60,000（照片图估版图区域）= **1.63×**。
口径不同、原型范围未披露、本结果未做面积优化 —— 不能解读为"差 1.63 倍"。

## 5. 这份报告的取数方法（可复跑）

三类预算**不是三条路径**，而是三个逻辑阶段。取数方法：

- ① 用 `-through [conv_*_q_reg/Q]` 再 `-to [acq_ids_q_reg/D]`，
  把跨多级寄存器的阶段切出来
- ② ③ 用 `-to [get_ports ...]` 直接取（实测 `-through` 在
  寄存器→端口场景返回 0）

**踩过的坑**：`get_pins -filter "full_name =~ <pat>"` 的模式**必须带前导 `*`**。
网表 ungroup 后引脚名形如 `\conv_q_reg[0]/Q`，
用 `conv_q_reg*` 匹配不到（返回 0），必须 `*conv_q_reg*`。
早期版本全部 N/A 就是这个原因。

复跑：

```bash
scp flow/synth/v21/sta_budget.tcl vm-yian:/tmp/
ssh vm-yian 'PATH=/opt/synopsys/syn/W-2024.09-SP3/bin:$PATH \
  SNPSLMD_LICENSE_FILE=27080@localhost \
  dc_shell -f /tmp/sta_budget.tcl > /tmp/budget.log 2>&1'
sed -n "/===BUDGET_START===/,/===BUDGET_END===/p" /tmp/budget.log
```

## 6. 未覆盖

| 项 | 说明 |
|:---|:---|
| 参考建立 / RA 建立 / 模拟安静窗 | 模拟域，数字 STA 不可及 |
| 后布线 STA | 无 P&R 结果 |
| hold 分析 | 本报告只做 setup（`-delay_type max`） |
| 多 PVT 角 | 只做了 SS 0.81V −40°C（setup 主用角） |
