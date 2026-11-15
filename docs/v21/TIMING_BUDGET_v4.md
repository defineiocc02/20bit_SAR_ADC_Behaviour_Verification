# 三类时间预算实测 v4（更正 v7 的黑盒面积）

时间：2026-11-16　基线：`0dd6a87` + 本轮修正
网表：DC W-2024.09-SP3，619 s　角：`ssg0p81vm40c`　周期 25 ns

## 1. 又发现一次黑盒：v7 的 76,222 µm² 是假的

v7 报告「拆分 S1 后面积降到 76,221.56 µm²，slack +19.38 ns」。
做门级等价时又抓到问题：

```
VCS: Module definition of recon_v21 is not found in the design
```

查 DC 日志：

```
Error: recon_v21.sv:165: The register symbol 'v1_q' has already been defined. (VER-954)
Warning: Unable to resolve reference 'recon_v21' in 'adc_digital_ip_v21'. (LINK-5)
```

**根因**：我拆分 S1 时，在「级间寄存器」段保留了 `v1_q`/`tag1_q`/
`err1_q`/`z1_q` 的旧声明，又在 S1 段新增了同名声明 —— **重复定义**。

后果：DC 拒绝 link `recon_v21`，整个模块变成黑盒，
网表里连 `DW_div` 都没有（`grep -c DW_div` = 0）。
所以 v7 的 76,222 µm² 不含 recon 的任何逻辑。

**特别值得记**：这种情况下 **VCS 仿真仍然 8213 项全过**
（重复声明只是 warning）。是 DC 的 analyze 和门级编译才暴露的。

→ **教训：仿真通过 ≠ 可综合。**

## 2. 修正后的真实数字（v8）

| 指标 | v7（黑盒，错） | **v8（真实）** | 说明 |
|:---|--:|--:|---|
| VER-954 重复定义 | 4 | **0** | |
| LINK-5 未解析 | 3 | **0** | |
| 网表里 `recon_v21` 定义 | **0** | **1** | |
| 网表里 `DW_div` | **0** | **2** | alpha 除法确实在 |
| cells | 127,359 | **151,346** | |
| Combinational area | 73,736.08 | **93,575.79** | |
| Noncombinational area | 2,485.48 | **3,978.90** | |
| **Total cell area** | 76,221.56 | **97,554.69 µm²** | |
| 未时钟寄存器 | 0 / 1,311 | **0 / 2,116** | |
| 最差 slack | +19.38 | **+9.39 ns（MET）** | |
| 违例数 | 0 | **0** | |

## 3. S1 拆分确实消除了违例

对比 v6（拆分前）与 v8（拆分后，真实）：

| 指标 | v6（拆分前） | **v8（拆分后）** |
|:---|--:|--:|
| 最差 slack | **−2.36（VIOLATED）** | **+9.39（MET）** |
| 违例数 | 3+ | **0** |
| Total cell area | 97,356.24 | 97,554.69 |
| cells | 151,300 | 151,346 |

**拆分消除了违例，面积基本不变（+0.2%）** —— 这是合理的：
把一个 27.34 ns 的路径切成两段，逻辑总量不变，
但每段都满足 25 ns。

v7 声称的「面积降到 76,222」是黑盒造成的假象，**已撤回**。

## 4. 关键路径

```
slack = +9.39 ns (MET)
Startpoint: u_recon/u_recip_rnd/u_recip/inv_q60_reg[0]/CP
Endpoint:   u_recon/u_recip_rnd/i_q60_reg[26]
```

即 `recip_q60` 的 Newton 迭代输出寄存器。这是设计里最重的组合段，
25 ns 下余量 37.6%。

## 5. 与原型面积对比

| 对象 | 面积 | 口径 |
|:---|--:|:---|
| 原型数字区 | 60,000 µm² | 照片图估的版图区域 |
| 本工程完整顶层 | **97,554.69 µm²** | 标准单元综合面积 |

数值比 **1.63×**。**不能**解读为"差 1.63 倍"：口径不同、
原型范围未披露、本结果未做面积优化。

## 6. 两个「报告误导」教训汇总

本次 I5 阶段共遇到两次「报告看起来通过、实际不对」：

| # | 现象 | 根因 | 自证手段 |
|:--:|:---|:---|:---|
| 1 | v5 报 89,693 µm²、583 s 收敛 | `recip_rnd_v21.g_q30` 位宽不匹配（LINK-3），模块变黑盒 | `grep '^module <子模块>'` 网表必须 > 0 |
| 2 | v7 报 76,222 µm²、+19.38 ns | `v1_q` 等重复声明（VER-954），`recon_v21` 变黑盒 | `grep -c VER-954` 必须 = 0；门级能编译 |

两次都是**门级编译**把问题暴露出来的。所以三项自证必须每次做：

```bash
grep -c "^module recon_v21 "     $NETLIST   # 必须 > 0
grep -c "^module recip_rnd_v21 " $NETLIST   # 必须 > 0
grep -cE "VER-954|LINK-3|LINK-5" $DC_LOG    # 必须 = 0
grep -c "DW_div"                 $NETLIST   # 必须 > 0（alpha 除法还在）
```

## 7. 复跑

```bash
ssh vm-yian 'rm -rf /tmp/dcwork && mkdir -p /tmp/dcwork &&
  PATH=/opt/synopsys/syn/W-2024.09-SP3/bin:$PATH \
  SNPSLMD_LICENSE_FILE=27080@localhost \
  dc_shell -f /tmp/dc_synth_v21.tcl > /tmp/dcwork/dc.log 2>&1'

grep -E "Total cell area|Number of cells" /tmp/dcwork/area.rpt
grep -c "VER-954\|LINK-5" /tmp/dcwork/dc.log
```
