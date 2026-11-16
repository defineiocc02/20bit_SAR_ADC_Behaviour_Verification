# LUT 修复后的真实指标（取代此前的错误数字）

时间：2026-11-16　基线：`bef42e8`
网表：`/tmp/dcwork2/adc_digital_ip_v21_syn.v`（DC W-2024.09-SP3）

## 1. 为什么所有旧数字作废

此前报的 `97,554.69 µm²` / `slack +9.39 ns` / `门级 1464 拍差异 0`
**全部基于一个功能错误的网表**：`recip_q60` 的 64 项 LUT 被 DC 丢弃
（`VER-281`），`u0_r_reg` 被 `OPT-1206` 删除，网表里
`i_q60 = {0...0,1,0} = 2` 恒为常数。

漏掉 31 bit 的 LUT 寄存器 + 一整张查表逻辑，面积和时序当然偏小偏松。

## 2. 修复后对比

| 指标 | 旧网表（错误） | **新网表（正确）** | 变化 |
|:---|--:|--:|:---|
| `VER-281`（initial 被忽略） | 1 | **0** | 修复 |
| `OPT-1206`（常量删除） | 146 | **89** | −39% |
| `u0_r_reg` 在网表 | **0** | **30** | 寄存器回来了 |
| cells | 151,346 | **165,374** | +9.3% |
| Combinational area | 93,575.79 | **104,890.38** | +12.1% |
| Noncombinational area | 3,978.90 | **4,060.73** | +2.1% |
| **Total cell area** | 97,554.69 | **108,951.11 µm²** | **+11.7%** |
| 寄存器（clocked） | 2,116 | **2,176** | +60 |
| 未时钟寄存器 | 0 | **0** | — |
| 最差 slack | +9.39 ns | **+3.02 ns** | **余量从 37.6% 降到 12.1%** |

## 3. 真实关键路径

```
slack = +3.02 ns (MET)
Startpoint: u_recon/u_recip_rnd/u_recip/u0_r_reg[29]/CP
Endpoint:   u_recon/u_recip_rnd/u_recip/inv_q60_reg[0]
data arrival 21.96 / required 24.98
```

**路径落在 Newton 迭代上**（`u0_r` 是 LUT 输出寄存器，`inv_q60` 是倒数结果），
这是设计里最重的组合段。修复后它才真正出现在时序报告里。

此前报的 `inv_q60_reg[0] → i_q60_reg[26]` 仍是同一条链的后半段，
但当时 `u0_r` 已被删，路径被截短了。

**余量 12.1%（3.02 ns）**：仍然满足 25 ns，但比之前的"37.6% 余量"紧得多。
这是必须如实报告的变化。

## 4. 门级等价（真正有效的验证）

用两笔完整事务（A guard raw=5120、B 普通 raw=2560），
从受理 → 采样 → 逐事件开关 → 余差捕获 → ADC2 返回 → 20 位输出：

```
RESULT: GATE_E2E_PASS
RTL   : accepted=5 captured=2 output=2
门级  : accepted=5 captured=2 output=2
轨迹  : RTL 1890 行 / 门级 1890 行，差异 0 处
```

交易数三项各 ≥2 的门禁通过 —— 说明激励确实产生了有效交易，
不是"两侧都无输出却判通过"。

对比修复前的同一测试：**58 处差异，全部在 `out_code`**
（RTL `0xFFF00` vs 网表 `0x80000`）。

## 5. 面积对比（更新）

| 对象 | 面积 | 口径 |
|:---|--:|:---|
| 原型数字区 | 60,000 µm² | 照片图估的版图区域 |
| 本工程完整顶层 | **108,951 µm²** | 标准单元综合面积 |

数值比 **1.82×**（此前用错误网表算的是 1.63×）。

**仍不能解读为"差 1.82 倍"**：口径不同（版图区域 vs 标准单元面积）、
原型红框范围未披露、本结果未做面积优化（`-map_effort medium`）。

## 6. 复跑

```bash
# 综合（约 620 s）
ssh vm-yian 'rm -rf /tmp/dcwork2 && mkdir -p /tmp/dcwork2 && \
  sed "s|/tmp/dcwork|/tmp/dcwork2|g" /tmp/dc_synth_v21.tcl > /tmp/dc_synth2.tcl && \
  PATH=/opt/synopsys/syn/W-2024.09-SP3/bin:$PATH \
  SNPSLMD_LICENSE_FILE=27080@localhost \
  dc_shell -f /tmp/dc_synth2.tcl > /tmp/dcwork2/dc.log 2>&1'

# 四项自证（必须全过）
grep -c "^module recon_v21 "     /tmp/dcwork2/adc_digital_ip_v21_syn.v   # >0
grep -c "^module recip_rnd_v21 " /tmp/dcwork2/adc_digital_ip_v21_syn.v   # >0
grep -c "VER-281"                /tmp/dcwork2/dc.log                     # 必须 0
grep -c "u0_r_reg"               /tmp/dcwork2/adc_digital_ip_v21_syn.v   # 必须 >0

# 门级 e2e（两笔完整事务 + 交易数门禁）
D3D4_RUNNER=vm-yian D3D4_WORKDIR=/tmp/gate4 \
  D3D4_NETLIST=/tmp/dcwork2/adc_digital_ip_v21_syn.v \
  bash tools/check_gate_e2e.sh
```

## 7. 新增的强制自证项

此前两次报假数字（89,693 与 97,555 µm²）都是因为子模块变黑盒而
DC 仍报 `STEP_COMPILE_OK`。现在必须在每次综合后跑：

```bash
grep -c "VER-281"  dc.log    # initial 被忽略 -> 必须 0
grep -c "u0_r_reg" netlist.v # LUT 寄存器在不在 -> 必须 >0
```

这两条是**本轮新增**的，因为它们正是这个缺陷的唯一早期信号。
