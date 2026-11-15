# I4 检查点：20 位定点重构

时间：2026-11-15　方案：v1.4 §9.1–§9.4、§11.2 I4、§11.3
**状态：PASS（8213 项检查，8209 笔退休，0 错误）**

## 当前 SHA

- I3 交付：`d2fa859`
- 本检查点：见本文件所在 commit

## 实际补了什么

| 文件 | 内容 |
|:---|:---|
| `rtl/v21/recon_v21.sv` | 逐字实现 §9.3 整数 oracle 的可综合重构通路 |
| `tools/gen_vec_i4_recon.py` | 金标生成器（与 RTL 同一套规则） |
| `tb/v21/tb_i4_recon.sv` | 8213 项逐位对拍 |
| `tb/v21/vec_i4_recon.txt` | 8209 组金标 |

## §9.3 整数规则（逐字实现，未做任何"改进"）

```
RND(n,d)，d>0：符号对称的最近舍入，恰半时离零
Y  = -3145728 + 1536*raw + 768
Z  = Y - b                       （alpha == 2^30 旁路）
Z  = RND((Y-b)*2^30, alpha)      （其余合法 alpha）
N  = Z*64 + 6*Wm - 3*G - 6*Wp
I  = RND(2^60, G)
X  = floor((N*I)/2^36)           （有符号算术右移）
Q  = RND(X, 96)
unclipped = Q + 524288
code = min(1048575, max(0, unclipped))
out_sat = (code != unclipped)
```

## 两个已知点精确命中

| 笔 | raw | Wm (Q30) | Wp (Q30) | 期望 | 实测 |
|:---|--:|--:|--:|:---|:---|
| A | 5120 | 34829500416 (32.4375) | 528482304 (0.4921875) | `0xFFF00` | **`0xFFF00`** |
| B | 2560 | 17314086912 (16.125) | 142606336 (0.1328125) | `0x80000` | **`0x80000`** |

与方案 §11.3 逐值一致。

## 覆盖（8213 项）

| 项 | 组数 | 结果 |
|:---|--:|:---|
| A/B 定向 | 2 | 全对 |
| **ADC2 raw 全域** | 8192 | 全对（code/sat/err/tag 四项逐位） |
| raw 端点 | 4 | 全对 |
| alpha 边界（2^30 / 2^29 / 2^31 / 1） | 4 | 全对 |
| b 边界（0/±1/±2^20） | 5 | 全对 |
| G=0、alpha=0 违例 | 2 | 正确报 err 且不出结果 |
| epoch 不匹配 | 1 | 报 err + 保留 tag |
| 复位 | 1 | out_valid=0 |

**每笔恰好退休一次**：8209 组金标 → 8209 笔退休，计数器核对一致。

## 四个踩过的坑（全部写进注释）

### 1. 输入整数用 `int` 装不下 Q30 权重

金标里 `G_nom = 32*2^30 = 34359738368` 需要 36 位，`int`（32 位）截断成 0
→ `bad_g=1` → 全部报 err。改 `longint`。

### 2. 输出检查时机错

`out_valid` 是寄存输出，在 posedge 更新。若等 negedge 撤掉 `in_valid`
再检查，读到的是下一拍的 `out_valid=0`（原测试全部报"无 out_valid"）。
必须在 posedge 后立即检查。

### 3. `$fscanf` 带 `%s` 在 VCS 上提前返回

金标行尾有名字字段，`$fscanf("%d ... %s\n")` 在某些版本上返回 0，
表现为"载入 0 组"却不报错。改用 `$fgets` + `$sscanf`，并加"载入过少即 FATAL"。

### 4. 探针 TB 漏了时钟

`logic clk=0` 没有 `always #5 clk=~clk;`，仿真在 `Time: 0 ps` 直接结束，
看起来像"测试通过"——**这是最危险的一类假通过**。现在所有 TB 都检查
`always` 存在且日志里有非零仿真时间。

## 综合（28HPC+，25 ns）

```
CHK total=55 clocked=55
CHK unclocked=0
```

`compile -map_effort medium`：**30153 cells / 26818.09 µm²**。

**PPA 未闭合**（如实记录）：96 位乘 + 大位宽除法的代价很大。
方案 §9.3 已明确路径：

> "初版倒数用**精确整数定义**作为金标。硬件复用 `recip_q60` 的 LUT/Newton 思路，
> 但现有模块近似 floor 与这里最近舍入不同，须乘回检查余数并修正；
> 无法证明差值界时采用精确除法基线。"

所以本轮用精确除法保证**功能位精确**，倒数迭代与 PPA 优化列入 I5。
同时 `compile_ultra` 在含 `adc2_node_macro` 的完整顶层上仍会超时（I3 已记录）。

## 可复跑命令

```bash
python3 tools/gen_vec_i4_recon.py > tb/v21/vec_i4_recon.txt
ssh vm-yian 'cd /tmp/ip && export SNPSLMD_LICENSE_FILE=27080@localhost \
  PATH=/opt/synopsys/vcs/W-2024.09-SP1/bin:$PATH VCS_HOME=/opt/synopsys/vcs/W-2024.09-SP1 \
  && vcs -full64 -sverilog -timescale=1ns/1ps rtl/v21/recon_v21.sv tb_i4_recon.sv -o s4 \
  && ./s4'
```

期望：`RESULT: PASS (8213 checks, 8209 retired)`

## 剩余问题

| # | 问题 | 去向 |
|:--:|:---|:---|
| 1 | 面积 26818 µm²（recon）+ 28373 µm²（ADC2 宏） | I5 |
| 2 | `compile_ultra` 超时 | I5：换倒数迭代 + 流水 |
| 3 | recon 尚未接进顶层（当前独立验证） | I4b：接 adc2_rsp 通路 |
| 4 | 配置接口（§9.4 cfg_valid/addr/data）未实现 | I4b |
| 5 | 输出 FIFO / 反压未实现 | I4b |
| 6 | 片外系数训练的独立验证集 | I4b |
| 7 | 波形仍未 dump | I5 |

## 恢复时第一条命令

```bash
cd /Users/zhaoyi/src/20bit_SAR_ADC_Behaviour_Verification && git log --oneline -2 && git status --short
```
