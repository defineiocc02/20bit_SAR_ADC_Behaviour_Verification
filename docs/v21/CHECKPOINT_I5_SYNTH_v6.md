# I5 综合检查点 v6：更正 v5 的面积数字（发现黑盒问题）

时间：2026-11-16　基线：`f1aff2f`
工艺：TSMC 28HPC+ 7T `ssg0p81vm40c`　工具：DC W-2024.09-SP3

## 1. 必须更正 v5 的数字

v5 报告了「完整顶层收敛，89,693 µm²，slack +16.80 ns」。
**那个数字是错的** —— 综合时 `recip_rnd_v21` 是黑盒，没有展开。

### 怎么发现的

要做门级仿真验证时，VCS 报：

```
Error-[URMI] Unresolved modules
/tmp/dcwork/adc_digital_ip_v21_syn.v, 283861
"recip_rnd_v21 u_recip_rnd( ... )"
  Module definition of above instance is not found in the design.
```

查 DC 日志找到根因：

```
Error: Width mismatch on port 'g_q30' of reference to 'recip_rnd_v21'
       in 'recon_v21'. (LINK-3)
```

recon 的 `G_q30` 是 **64 位**，而 `recip_rnd_v21.g_q30` 是 **44 位**。
DC 链接失败后把整个 `recip_rnd_v21` 当黑盒，
于是 v5 的 89,693 µm² **完全不含它的逻辑**，983 秒的"收敛"也是假的
（黑盒不需要优化）。

### 修正

`recon_v21` 的例化改为 `.g_q30(G_q30[43:0])`（G 的合同域上限 2^36，44 位足够）。

## 2. 修正后的真实数字

| 指标 | v5（黑盒，错） | **v6（真实）** | 差异 |
|:---|--:|--:|:---|
| 链接错误 | 1（LINK-3） | **0** | — |
| 网表里 `recip_rnd_v21` 定义 | **0** | **13** | — |
| 综合耗时 | 583 s（无优化） | **623 s（有优化）** | — |
| cells | 142,065 | **151,300** | +9,235 |
| Combinational area | 86,176.69 | **93,559.82** | +7,383 |
| Noncombinational area | 3,516.44 | **3,796.42** | +280 |
| **Total cell area** | 89,693.13 | **97,356.24** | **+7,663 (+8.5%)** |
| 未时钟寄存器 | 0 / 1,863 | **0 / 2,018** | — |
| 最差 slack | +16.80 ns | **+9.07 ns** | −7.73 ns |
| 关键路径 | `x3_q_reg[31] → code_o_reg[19]` | **`u_recip/inv_q60_reg[0] → i_q60_reg[26]`** | 移到 recip 内部 |

**关键路径现在落在 `recip_rnd_v21` 内部的 Newton 迭代上**
（`inv_q60_reg → i_q60_reg`），这是预期位置——倒数计算是这条链最重的一段。
slack +9.07 ns 在 25 ns 周期下仍有 36% 余量。

## 3. 面积对比（更新后的口径）

| 对象 | 面积 | 口径 |
|:---|--:|:---|
| 原型数字区 | 60,000 µm² | **照片图估的版图区域** |
| 本工程完整顶层 | **97,356 µm²** | **标准单元综合面积** |

数值比 ≈ **1.62×**。仍然**不能**解读为"我们差 1.62 倍"，理由同 v5 §4：
口径不同、原型范围未知、本结果未做面积优化。

但数字本身现在可信：`recip_rnd_v21` 确实在网表里，链接错误为 0。

## 4. 这个教训值得记下

**"综合收敛"和"面积数字"都必须用网表自证，不能只看 DC 的 STEP 打印。**

具体检查项：
1. `grep -c "^module <子模块名>"` 网表 —— 子模块定义必须在
2. `grep -c "LINK-3\|Width mismatch"` DC 日志 —— 必须为 0
3. 门级仿真能编译 —— 未解析模块会立刻暴露

v5 只看了 `STEP_COMPILE_OK` 和 `step area`，没做这三项，所以错了。
本文件已把这三项写进检查流程。

## 5. 门级等价的进展与阻塞

**已完成**：
- 写了 `tb/v21/tb_trace.sv`：逐字段输出轨迹（避免格式串计数错误），
  RTL 侧已跑通，生成 1325 行轨迹。

**阻塞**：
DC 网表把 `w_main_tbl[18][16][40]` 与 `w_sub_tbl[18][4][40]`
**展开成扁平端口**（`w_main_tbl[0][0][38]` 这种），
而 TB 用数组端口连接，VCS 报 `PCTM` 类型不匹配。

解决需要：
- 方案 A：为门级单独写一份 TB，用手工拼接的扁平端口
  （18×16×40 + 18×4×40 = 14,400 根线，需脚本生成）
- 方案 B：综合时给这两个端口加 `set_dont_touch` 或改用 memory 推断
- 方案 C：先做**不带权重表**的门级等价（把 w_main/w_sub 固定在 RTL 常量），
  验证其余全部逻辑

方案 C 最快且能覆盖绝大部分逻辑，但**不能**证明权重表通路等价。

**未做**：Formality 形式等价。
`fm_shell` 进入 `(setup)` 模式后命令集与 DC/PT 不同
（`link_library`、`set_top_designs`、`set_container` 都不存在），
本轮多次尝试未跑通，如实记录。

## 6. 复跑

```bash
# 综合（约 623 s），产物含完整 recip_rnd_v21
scp rtl/v21/recon_v21.sv vm-yian:/tmp/ip/rtl/v21/
scp flow/synth/v21/dc_synth_v21.tcl vm-yian:/tmp/
ssh vm-yian 'rm -rf /tmp/dcwork && mkdir -p /tmp/dcwork &&
  PATH=/opt/synopsys/syn/W-2024.09-SP3/bin:$PATH \
  SNPSLMD_LICENSE_FILE=27080@localhost \
  dc_shell -f /tmp/dc_synth_v21.tcl > /tmp/dcwork/dc.log 2>&1'

# 三项自证（必须全过）
grep -c "^module recip_rnd_v21" /tmp/dcwork/adc_digital_ip_v21_syn.v   # 应 > 0
grep -c "LINK-3" /tmp/dcwork/dc.log                                     # 应 = 0
grep -E "Total cell area" /tmp/dcwork/area.rpt
```
