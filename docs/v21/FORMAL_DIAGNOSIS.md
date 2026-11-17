# Formality 20 个 failing compare point 的根因诊断

时间：2026-11-16　工具：Formality W-2024.09-SP3 / Design Compiler W-2024.09-SP3 / VCS W-2024.09-SP1
诊断对象：两版网表

| 网表 | 生成时间 | 大小 | 说明 |
|:---|:---|--:|:---|
| `/tmp/dcwork/adc_digital_ip_v21_syn.v`（旧） | 10:17 | 20,102,248 B | `initial` LUT 缺陷版，题面那 20 个 failing 出自它 |
| `/tmp/dcwork/adc_digital_ip_v21_syn.v`（新） | 11:07 | 22,416,826 B | `f_lut` 修复版，md5 `5e738fe5…`；旧版已被覆盖 |

结论等级：**根因已定位并经独立仿真证实**；**不是** signoff 结论

> 本文只做诊断与分析，**未修改任何产品 RTL**（所有实验性改动都在 `/tmp` 下的副本里）。
> 修复由 `878a348`（`recip_q60` LUT 改 `case`）与 `45e54f1`（`wsum` 显式展开）完成，
> 本文 §0.2 与 §5.5 给出修复后的复验结果。
>
> 证据分层：
> - **确定层**：§2.2（旧网表 12/12 输出 2）、§5.5（新网表 12/12 正确）—— 独立仿真，不依赖 Formality
> - **工具观测层**：§1、§3.4 的 Formality 输出 —— 工具说了什么，不等于结论
> - **未闭环**：§2.3、§4.2、§6 —— **明确不下结论**

---

## 0. 结论（先给答案）

### 0.1 一句话

**20 个 failing 是两件独立的事，都已定位，且第一件已修复并复验：**

| # | 根因 | 归属 | 状态 |
|:--:|:---|:---|:---|
| A | `rtl/recip_q60.sv` 用 `initial` 初始化 64 项 LUT → DC 丢弃 → `i_q60` 在网表里恒为常数 **2** | **第 5 类：真正的逻辑不等价** | **已修复（`878a348`）并复验通过**（修复后 `i_q60_reg` failing = **0**） |
| B | `w_main_tbl`/`w_sub_tbl` 展平后的端口在实现侧未被约束（旧网表只有 bit 0，修复后扩展到全 bit，见 §3.4.2） → `u_wsum` 的 `G/Wp/Wm` 低位不可判 | **第 5 类**（"网表带出未约束端口"，匹配因素见 §4.2） | **未修复**，修复 A 后**全部 20 个 failing 都变成这一簇** |

### 0.2 修复前后对照（同一套流程、同一份 RTL 源树）

| 指标 | 修复前（10:17 网表） | 修复后（11:07 网表） |
|:---|--:|--:|
| `i_q60_reg` failing | **14** | **0** ✅ |
| `u_wsum` failing | 6（`Wm_q30[0..5]`） | **20**（`G_q30` 8 + `Wp_q30` 6 + `Wm_q30` 6） |
| Unmatched **reference** compare points | 2073 | **0** ✅ |
| Cut-points（REF） | 1984 | **0** ✅ |
| Unread | 130 | 25 |
| `Unconstrained Implementation Inputs` | 46 | **2833** |
| `Unmatched Cone Inputs` | 4367 | 5676（**100% 是权重表**，见 §3.4） |
| Passing | 2580 | 2589 |
| Unverified | 430 | 614 |

**修复 A 是成功的**：`i_q60` 的 14 个 failing 全部消失，且"未匹配的参考侧 compare point"
从 2073 直接归零 —— 这证明 `initial` 派生出的 1984 个 cut-point 确实是 `i_q60` 失败的根源。

**修复 A 暴露了 B**：`i_q60` 消失后，剩下的 20 个 failing **全部集中在 `u_wsum`**，
且分布从"只看 `Wm`"扩展到"`G`/`Wp`/`Wm` 三者都有"——这是 §3 描述的权重表端口问题的**纯化表现**。

### 0.3 关于"是否需要加合法域约束"

**两簇都不需要，本文也不建议加任何约束。**
因此不涉及"合同是否明确、上游是否保证"这一判定分支。

特别声明：`analyze_points` 会建议对未约束端口加 `set_constant <port> 0/1`。
**本文明确不采纳** —— 那是把不确定输入桩成常量以"消掉"失败，
属于审查禁止的"未经说明就裁输入域"。本文只引用它的诊断价值。

---

## 1. `analyze_points` 的原始输出摘要

`analyze_points` 必须在 `verify` 之后同会话调用。本次共跑了 6 轮（2 轮作废）：

| 轮次 | 日志 | 目的 | 结果 |
|:---|:---|:---|:---|
| A | `/tmp/fmdiagA.log` | 10:17 网表 + 当前 RTL | 20 failing，但 `analyze_points` 因我脚本的选项错误（`-point_type register` 应为 `reg`）在它之前中止 |
| B | `/tmp/fmdiagB.log` | 同上 | **作废**：RTL 在 10:23 被改（`wsum_v21.sv`），与网表版本不一致（日志有 `SVF Design Consistency Check: Files with checksum difference: 1`） |
| **C** | **`/tmp/fmdiagC.log`** | **RTL 与 10:17 网表精确对齐**（`recip_q60` md5 `aa719f73…`、`wsum_v21` md5 `1658c32d…`，与 DC 当时读入的一致），`SVF Design Consistency` = 0 | **修复前的主证据** |
| F1 | `/tmp/fmfix.log` | 修复后网表 + `/tmp/ip/rtl/` | **作废**：源树与网表不同源，`SVF Design Consistency` 报 `recip_rnd_v21.sv` 校验和不符 |
| **F2** | **`/tmp/fmfix2_review_130645.log`** | **修复后网表 + `/tmp/gate3/rtl/`（与网表同源）**，`SVF Design Consistency` = 0 | **§3.4 的主证据**：`i_q60` failing = 0，`u_wsum` 20 failing |
| S/N | `/tmp/fmsvf.log`、`/tmp/fmnames.log` | 追 rejected guidance / 端口命名 | 未跑完（`Process terminated by kill`），不作结论依据 |

> 第 B 轮作废这件事本身值得记：**改了 RTL 却拿旧网表去跑等价，结论一定是废的**。
> 这也是我在 C 轮里坚持先 `md5sum` 双方再开跑的原因。

### 1.1 Run C（旧网表）的验证结果

```
Verification FAILED
 2580 Passing compare points
   20 Failing compare points (20 matched, 0 unmatched)
  430 Unverified compare points

Matched Compare Points     BBPin    Loop   BBNet     Cut    Port     DFF     LAT   TOTAL
---------------------------------------------------------------------------------------
Passing (equivalent)           0       0       0       0    1119    1461       0    2580
Failing (not equivalent)       0       0       0       0       0      20       0      20
Unverified                     0       0       0       0       1     429       0     430
Not Compared
  Constant reg                                                        48       0      48
  Unread                       0       0       0       0       0     130       0     130
```

### 1.2 20 个 failing 点的完整名单（Run C）

```
u_recon/u_recip_rnd/i_q60_reg[0]  [1]  [2]  [3]  [6]  [10] [11] [13] [14]
                              [17] [18] [20] [21] [24]      → 14 个
u_wsum/Wm_q30_reg[0] [1] [2] [3] [4] [5]                     →  6 个
```

**注意与题面给的名单不同**：题面（09:15 那轮）是 `i_q60_reg` 19 bit + `Wm_q30_reg[39]`，
本轮（10:17 网表）是 `i_q60_reg` 14 bit + `Wm_q30_reg[0..5]`。

这一差异本身就是证据：**失败集合随网表版本漂移，说明它由网表内部结构决定，而不是某个固定的架构/合同差异。**

### 1.3 `analyze_points -failing` 的四个分类小节

`analyze_points` 输出约 3.5 万行，归入四类：

```
Found 46 Unconstrained Implementation Inputs
Found 4367 Unmatched Cone Inputs
Found 1 Rejected Guidance Command      （reg_constant）
Found 207 Required Inputs
```

#### (a) `Found 46 Unconstrained Implementation Inputs` —— `Wm_q30` 的直接根因

46 个端口全部是**权重表的 bit 0（及个别相邻低位）**：

```
i:/WORK/adc_digital_ip_v21/w_main_tbl[9880]
    Unmatched in the implementation cones for 6 compare point(s):
        i:/WORK/adc_digital_ip_v21/u_wsum/Wm_q30_reg[0] ... [5]
    Try adding this command before verify:
        set_constant i:/WORK/adc_digital_ip_v21/w_main_tbl[9880] 0
```

46 个端口按 (数组, bit) 归类：

| 数组 | bit 0 | bit 1 | bit 3 | bit 4 |
|:---|--:|--:|--:|--:|
| `w_main_tbl` | 33 | 1 | 2 | 2 |
| `w_sub_tbl` | 8 | 0 | 0 | 0 |
| **合计** | **41** | **1** | **2** | **2** |

按 3D 坐标看，是 `w_main_tbl[0][*][0]`、`[14][*][0]`、`[15][*][0]`、`[17][*][0]`
和 `w_sub_tbl[0][*][0]`、`[14][1][0]`、`[15][*][0]`、`[17][*][0]` —— **清一色 bit 0**。

#### (b) `Found 4367 Unmatched Cone Inputs` —— `i_q60` 的直接根因

形态统计：

```
Is globally unmatched                              8694 条
Exists in the ref cone but not in the impl cone      30 条
Exists in the impl cone but not in the ref cone      10 条
Matched with cell                                    40 条
```

其中 `i_q60` 的锥由两条关键条目支配：

```
r:/WORK/adc_digital_ip_v21/u_recon/u_recip_rnd/u_recip/inv_q60_reg[9]
    Is globally unmatched affecting 13 compare point(s):
        i:/WORK/adc_digital_ip_v21/u_recon/u_recip_rnd/i_q60_reg[0] [2] [3] [6]
        [10] [11] [13] [14] [17] [18] [20] [21] [24]
------------------------------------------------------------------
r:/WORK/adc_digital_ip_v21/u_recon/u_recip_rnd/u_recip/g_q30_o_reg[29]
    Matched with cell i:/WORK/adc_digital_ip_v21/u_recon/u_recip_rnd/u_recip/g_q30_o_reg[29]/*dff.00*
    Exists in the ref cone but not in the impl cone for 13 compare point(s):
        （同一批 13 个 compare point）
```

`inv_q60_reg` 全局未匹配的条目共 **54 条**，`g_q30_o_reg` "存在于 ref 锥但不在 impl 锥"的共 **60 条**。
它们指向同一批 13–14 个 `i_q60_reg` bit。

**这组条目是"参考侧有这一级寄存器、实现侧没有"的典型形态** —— 与 §2 的
`u0_r_reg` 被删、Newton 迭代被展平成组合逻辑完全吻合。

#### (c) `Found 1 Rejected Guidance Command` —— `reg_constant`

```
The rejection of some SVF guidance commands will almost invariably
cause verification failures.
reg_constant
```

`reg_constant` 在 Guidance Summary 里的账是 **88 accepted / 58 rejected**（共 146）。
即 DC 有 58 条"这个寄存器是常量"的指导没能被 Formality 接受。
**(c) 与 (b) 是同一件事的两面**：DC 认定某些寄存器是常量（于是删了/优化了），
Formality 拒绝照做，两边结构就对不上了。

#### (d) `Found 207 Required Inputs`

列出对失败模式必需的输入，例如：

```
i:/WORK/adc_digital_ip_v21/u_evt/final_applied_q_reg/*dff.00*
    Fans out to 6 failing and 5 passing points and has logic value '1' for 6 compare point(s):
        i:/WORK/.../u_wsum/Wm_q30_reg[0] ... [5]
i:/WORK/adc_digital_ip_v21/final_post_q_reg[0]/*dff.00*
    Fans out to 6 failing and 2 passing points and has logic value '0' for 6 compare point(s)
```

这确认 `Wm_q30` 的失败由 `final_post_q`（post mask）+ `u_wsum` 权重表这条路径驱动，
与 §3 的端口未匹配结论一致。

---

## 2. 失败 bit 与通过 bit 的差异分析

题面给的分布是 **09:15 轮：19 错 / 8 过**（通过的是 bit 5, 9, 13, 15, 16, 17, 18, 22，
已从 `/tmp/fmv12.log` 逐条核对）。本轮 **10:17 网表：14 错 / 13 过**。

**关键结论：散布式失败不是"某些 bit 逻辑深度不同"，而是"整个寄存器被换成了常数"。**
（但"为什么不是 27 位全错"这一点尚未完全解释，见 §2.3。）

### 2.1 网表结构：`i_q60` 退化成了常数 2

从 10:17 网表里 `recip_rnd_v21` 模块抽出 27 个 `i_q60_reg` 的 D 端，实测分布：

```
n2   : 26 个 bit（bit 0..3, 5..26）
N70  :  1 个 bit（bit 4）
```

即 **27 个 D 端中 26 个接同一根 `n2`**，只有 bit 4 还残留一条独立驱动
（`CKAN2D1BWP7T40P140 U39 ( .A1(i_c[4]), .A2(n28), .Z(N70) )`）。
`n2` 的驱动是：

```verilog
TIELBWP7T40P140 U3 ( .ZN(n2) );   // 恒 0
TIELBWP7T40P140 U4 ( .Z(n1) );    // 恒 1（U4 为 TIEH）
```

即 **26 个 bit 的 D 端根本不再依赖任何输入**，恒为常数。
这正是"寄存器被整体换成常数"的直接形态。

真正决定性的两处：

```verilog
// (1) recip_q60 的输出被整体接成一根网
assign inv_q60[0]  = \inv_q60[26] ;
assign inv_q60[1]  = \inv_q60[26] ;
...                                        // 27 条全部相同
assign inv_q60[26] = \inv_q60[26] ;

// (2) 那根网的驱动是一个 TIE-LOW 单元
TIELBWP7T40P140 U3 ( .ZN(\inv_q60[26] ) );   // 恒 0
```

而 `recip_rnd_v21` 里：

```verilog
  recip_q60 u_recip ( ..., .valid_o(r_valid), .g_q30_o(r_g), .err_o(r_err) );
  // r_inv 没有单独端口，直接来自 inv_q60 —— 已被 tie 成 0

  TIELBWP7T40P140 U3 ( .ZN(n2) );   // 恒 0
  TIEHBWP7T40P140 U4 ( .Z(n1) );    // 恒 1

  recip_rnd_v21_DW02_mult_0_DW02_mult_200 mult_102 (
      .A({n2,n2,...,n2,n1}), .B(r_g), .TC(n2), .PRODUCT({... prod2[43:0] ...}) );
  // 乘法器 A 端 = {0,...,0,1} = 1  → 乘法退化成直通
```

于是 `q0/q1/prod/rem` 全部退化，`i_c` 变成常数，
`i_q60_reg` 的 D 端全部是常数 —— **`i_q60` 恒等于 2**。

> 小节说明：`U3` 这个标号在 `recip_q60` 与 `recip_rnd_v21` 两个模块里各有一个
> （DC 的 `uniquify` 未完全生效，SVF 里 `uniquify` 被拒 566 条）。
> 两者都是 `TIELBWP7T40P140`，分别驱动 `\inv_q60[26]` 和 `n2`，都是恒 0。
> 这不影响结论，但说明**网表内部存在跨模块重名**，读网表时必须限定在模块作用域内。

### 2.2 决定性的独立仿真证据（不依赖 Formality）

把 10:17 网表的 `recip_rnd_v21` + `recip_q60` 两个模块单独抽出
（`/tmp/fmproof/net_recip.v`，829 行），与**同一份 RTL** 用**完全相同的 testbench** 比对。

testbench（`/tmp/tb_scan.v`）：每个 G 独立复位 → 单次 `valid_i` 脉冲 → 等 `valid_o` → 采样 `i_q60`。
覆盖合同域 `[2^34, 2^36]` 内 12 个点（含上下界）。

| G | 精确 `RND(2^60,G)` | RTL 输出 | **10:17 网表输出** |
|--:|--:|--:|--:|
| 17179869184（2^34 下界） | 67108864 | 67108864 ✅ | **2** ❌ |
| 20000000000 | 57646075 | 57646075 ✅ | **2** ❌ |
| 25000000000 | 46116860 | 46116860 ✅ | **2** ❌ |
| 30000000000 | 38430717 | 38430717 ✅ | **2** ❌ |
| 34359738368（2^35） | 33554432 | 33554432 ✅ | **2** ❌ |
| 40000000000 | 28823038 | 28823038 ✅ | **2** ❌ |
| 45000000000 | 25620478 | 25620478 ✅ | **2** ❌ |
| 51539607552（2^36 上界） | 22369621 | 22369621 ✅ | **2** ❌ |
| 55000000000 | 20962209 | 20962209 ✅ | **2** ❌ |
| 60000000000 | 19215358 | 19215358 ✅ | **2** ❌ |
| 65000000000 | 17737254 | 17737254 ✅ | **2** ❌ |
| 68719476736（2^36） | 16777216 | 16777216 ✅ | **2** ❌ |

**RTL 与精确 RND：12/12 全对。网表：12/12 全部输出常数 2。**

逐拍 trace 进一步证明**只有数据通路坏了**（`/tmp/fmproof/simtr_rtl`、`simtr2`）：

```
RTL  : cyc=8 rst_n=1 v_i=0 | v_o=1 i_q60=33554432 err=0
网表 : cyc=8 rst_n=1 v_i=0 | v_o=1 i_q60=2        err=0
                                   ↑ 仅此一处不同；valid/err/时序逐拍一致
```

### 2.3 关于"散布式"：一个必须如实标注的未解释项

**先说已核实的事实**：在 12 个合法 G 上，RTL 输出的 27 个 bit **每一个都取到过 0 和 1 两种值**
（逐 bit 实测，无一例外）：

```
bit  0..26：RTL 值集合 = [0, 1]  → 全部可被反例捕获
```

也就是说，**不存在"某些 bit 天生与常数 2 一致"这种情况**。
按纯功能推理，`i_q60` 若恒为常数 2，**27 个 bit 应当全部 failing**。

**但实测是 14 个 failing（10:17 轮）/ 19 个 failing（09:15 轮），并非 27 个全错。**

这条矛盾我**未能解释**。可能的解释（均未验证，不作为结论）：

1. **`i_q60` 并非全部拍恒为 2**：我的 testbench 只覆盖"复位→单脉冲→等 valid_o"这一种时序；
   Formality 探索的是所有输入序列。若在其他序列下 `i_q60` 会取别的值（例如
   `i2_q_reg` 那 37 bit 常量折叠、或 `valid` 通路的分支使某些拍走了不同路径），
   部分 bit 就可能在所有可达状态上恰好一致。
2. **`Formality` 的"通过"含义**：`Passing (equivalent)` 指该 compare point 的
   参考锥与实现锥在所有可达状态上等价。若某个 bit 的**参考锥本身也被优化得只剩常数**
   （例如 `inv_q60_reg` 的 54 条全局未匹配使参考侧也被退化），两边可能"同为常数"而判等。
3. **`u0_r_reg` 在参考侧的行为**：Formality 里 `initial` 是被建模的（不像 DC 忽略它），
   所以参考侧 LUT 有值；但 `guide_reg_constant` 被接受的那 88 条可能让部分
   参考侧寄存器也被当常量，从而与实现侧的常数"对上"。

**这一点必须如实记为"未完全定位"**，不能因为"根因已经找到常数 2"就跳过。
§4.2 已把同类未闭环项列出。

**可以确定的是**：无论 8 个（或 13 个）通过的 bit 为何恰好通过，
**"网表对 12 个合法 G 全部输出 2、RTL 全部正确"这一实测事实不变**，
`i_q60` 的数据通路确实是坏的，这是真实逻辑不等价。

### 2.4 失败集合漂移

| 网表版本 | `i_q60` 实际值 | `i_q60` failing bit | `Wm_q30` failing |
|:---|:---|:---|:---|
| 09:15 版（`/tmp/dcwork` 09:34 产物，网表 `5799a26d…`） | 常数 2（同一缺陷） | 19 个：0,1,2,3,4,6,7,8,10,11,12,14,19,20,21,23,24,25,26 | `[39]` |
| 10:17 版 | 常数 2（同一缺陷） | 14 个：0,1,2,3,6,10,11,13,14,17,18,20,21,24 | `[0..5]` |

两版网表的 `i_q60` 都是常数 2，但 failing 集合不同：

- 交集 **11 个**：`0,1,2,3,6,10,11,14,20,21,24`
- 仅 10:17 轮失败：`13,17,18`
- 仅 09:15 轮失败：`4,7,8,12,19,23,25,26`
- 并集 22 个；**两版都通过的只有 5 个**：`5,9,15,16,22`

（题面说 09:15 轮有 8 个 bit 通过：`5,9,13,15,16,17,18,22`；
本文从 `/tmp/fmv12.log` 逐条核对，确认 09:15 轮通过的是 `5,9,13,15,16,17,18,22` 这 8 个 ——
其中 `13,17,18` 在 10:17 轮转为失败，所以两版共同通过的只剩 5 个。）
说明 **"27 位中哪几位失败"不是稳定的设计属性**，取决于该版网表里
`i_q60` D 端组合逻辑的具体折叠形态。原因同 §2.3 的未解释项。

**这一漂移本身是重要线索**：它指向"实现侧结构被大幅改写"，
而不是"某个固定的合同/架构级差异"。这也是排除第 1 类（版本不一致）
和第 4 类（合法域/环境）的旁证之一 —— 后两者的失败集合应当是稳定的。

---

## 3. `u_wsum` 簇（B 类）的分析

> 命名说明：本节分析的是 **B 类**（`u_wsum` 的 `G_q30`/`Wp_q30`/`Wm_q30`）。
> 在 10:17 网表上它只表现为 `Wm_q30_reg[0..5]` 这 6 个点；
> 在 11:07 修复后网表上它扩展为 20 个点（见 §3.4）。
> 本节 §3.1–§3.3 用 10:17 网表的观测，§3.4 用修复后网表的观测。

`Wm_q30_reg[39]`（09:15 轮）与 `Wm_q30_reg[0..5]`（10:17 轮）**不是** `i_q60` 的连带效应 ——
修复 `i_q60` 之后它不但没消失，反而扩大到 20 个点（§3.4），证明它是独立问题。

### 3.1 根因：权重表输入端口未被约束

10:17 网表上，`analyze_points` 报 `Found 46 Unconstrained Implementation Inputs`，
**全部是 `w_main_tbl`/`w_sub_tbl` 的 bit 0**（见 §1.3(a)），
它们各自 "Unmatched in the implementation cones for 6 compare point(s)"，
而这 6 个 compare point 正是 `u_wsum/Wm_q30_reg[0..5]`。

机制：

1. `wsum_v21` 里 `Wm = Σ post-mask ? w_main_tbl[sid][j] : 0`，是纯加法树
2. DC 把权重表展平成 14400 个顶层输入端口（`w_main_tbl[0..11519]`、`w_sub_tbl[0..2879]`）
3. 其中 **41 个 bit 0 端口**在实现侧被当成**自由变量**（未约束）
4. `Wm_q30` 的低位由这些 bit 0 的加法进位决定 → 不可判 → failing

为什么是**低 6 位**：`Wm` 是 8 片 × 20 位的加法和。
每片的 LSB（bit 0）都是"1 个 Q30 LSB"权重，41 个这样的 LSB 项一起决定
和的低位（`2^6 = 64 > 41`，所以最多影响到 bit 5）。
这与观察到的 `Wm_q30_reg[0..5]` **精确吻合**。

> 修正说明：修复后网表上的观测（§3.4.2）显示未约束端口**不只是 bit 0**、
> 而是几乎全部 bit。所以"只有 bit 0"是 10:17 那一版网表的**特定形态**，
> 不是这个问题的普遍规律。§3.4 的观测更完整，应以它为准。

### 3.2 为什么参考侧/实现侧端口对不上

网表里 `w_main_tbl` 的顶层声明是 escaped 3D 名字：

```verilog
  input clk, rst_n, calc_valid, \w_main_tbl[0][0][39] , \w_main_tbl[0][0][38] , ...
```

而 `adc_digital_ip_v21` 里 `wsum_v21` 的实例连接是**扁平拼接**：

```verilog
  wsum_v21 u_wsum ( .clk(clk), .rst_n(rst_n), .calc_valid(node_valid_o),
                    .ids_i(node_ids_o), .pre_mask_i(node_pre_o), .post_mask_i(node_post_o),
                    .w_main_tbl({\w_main_tbl[0][0][39] , \w_main_tbl[0][0][38] , ... }) );
```

参考侧（RTL）的 `w_main_tbl` 是 `input logic [39:0] w_main_tbl [0:17][0:15]` —— 一个**数组端口**；
实现侧是一串**扁平标量端口**。Formality 需要靠名字/结构把两者对上。

**这里有一个我未能解释的矛盾**（详见 §4.2）：
`report_unmatched_points -point_type port` 在 ref 与 impl **两侧都报 "No unmatched points"**，
但 `analyze_points` 又把这 46（后来 2833）个端口列为 "Unconstrained Implementation Inputs"。

### 3.3 `G_q30` 高位常量折叠是**合法**的

`wsum_v21` 里还有 1 个 TIE 单元 `TIELBWP7T40P140 U3 ( .ZN(net1713285) )`，
它驱动 `G_q30[49..63]`（15 bit），而 `G_q30[47..63]` 本就被 `OPT-1206` 当常量删掉，
SVF 里有对应的 `guide_reg_constant`，**这是合法的常量折叠**：
`G = Σ 8×16 个 Q30 权重`，高位确实恒 0。

**所以 `G_q30` 高位不是缺陷。** 这一点要和 §3.4 里 `G_q30_reg[3..18]` 的 failing 区分开 ——
后者是**低位**被未约束端口影响，与高位常量折叠无关。

### 3.4 修复 `i_q60` 之后：`u_wsum` 簇成为全部 20 个 failing

这是本次诊断**最后拿到的、也是最关键的**一步。
用修复后的网表（11:07，由 `/tmp/gate3/rtl` 综合，`recip_rnd_v21` md5
与仓库一致；`SVF Design Consistency` = 0）重跑完整 Formality：

```
Verification FAILED
 2589 Passing / 20 Failing / 0 Aborted / 614 Unverified

Matched Compare Points     BBPin    Loop   BBNet     Cut    Port     DFF     LAT   TOTAL
Passing (equivalent)           0       0       0       0    1119    1470       0    2589
Failing (not equivalent)       0       0       0       0       0      20       0      20
```

**20 个 failing 全部在 `u_wsum`，`i_q60` 一个不剩：**

```
u_wsum/G_q30_reg[3] [4] [5] [6] [8] [12] [13] [18]      8 个
u_wsum/Wp_q30_reg[0] [2] [8] [10] [13] [15]             6 个
u_wsum/Wm_q30_reg[0] [2] [6] [11] [14] [15]             6 个
```

### 3.4.1 三分法与数组的对应关系完全吻合

把 2833 个未约束端口逐一映射到"它影响哪些 compare point"：

| compare point 簇 | 受影响端口数 | 涉及数组 |
|:---|--:|:---|
| `G_q30_reg[3,4,5,6,8,12,13,18]` | 511–1782 | **仅 `w_main_tbl`** |
| `Wp_q30_reg[0,2,8,10,13,15]` | 13–222 | **仅 `w_sub_tbl`** |
| `Wm_q30_reg[0,2,6,11,14,15]` | 68–1208 | **`w_main_tbl` + `w_sub_tbl`** |

这与 RTL 的语义**精确对应**：

```systemverilog
// wsum_v21.sv
f_G  : 只用 w_main_tbl                              → G  只受 main 影响
f_Wp : 只用 w_sub_tbl                               → Wp 只受 sub 影响
f_Wm : w_main_tbl（j=0..15） + w_sub_tbl（j=0..3）   → Wm 两者都受
```

**这条对应关系是"未约束端口 = 权重表展平后的输入"的独立佐证** —— 如果根因是别的
（例如某个控制信号），不可能出现这么干净的"哪个数组影响哪个输出"的分工。

### 3.4.2 未约束端口的 bit 分布：从"只有 bit 0"变成"全 bit 均匀"

| | 修复前（10:17） | 修复后（11:07） |
|:---|:---|:---|
| 未约束端口总数 | 46 | **2833** |
| `w_main_tbl` | 38（其中 33 个 bit 0） | **2471**（bit 0–18 各约 128 个） |
| `w_sub_tbl` | 8（全部 bit 0） | **362**（bit 0–15 各约 22 个） |

修复前只有 **bit 0** 未被约束，所以只影响 `Wm` 的低 6 位 ——
这也是为什么修复前 `Wm_q30` 只 failing `[0..5]`。

修复后变成**几乎所有 bit 都未约束**，于是失败扩展到 `G`/`Wp`/`Wm` 的多个 bit。
**`i_q60` 的修复本身没有引入这个问题** —— 它只是让 `u_wsum` 的问题
从"被 `i_q60` 的失败掩盖"变成"完全暴露"。

### 3.4.3 `Unmatched Cone Inputs` 100% 是权重表

修复后的 `analyze_points`：

```
Found 2833 Unconstrained Implementation Inputs
Found 5676 Unmatched Cone Inputs      ← 其中 "Is globally unmatched" 5668 条，
                                         5668 条全部是 w_main_tbl / w_sub_tbl
Found 2355 Required Inputs
```

**`i_q60` 相关的 unmatched 条目已完全归零**（修复前 `inv_q60_reg` 54 条、
`g_q30_o_reg` 60 条）。这再次确认 A 类缺陷已被彻底消除。

### 3.4.4 结论

`u_wsum` 的 20 个 failing **不是 RTL 逻辑错误**，而是
**DC 把数组端口展平成 14400 个扁平标量端口后，Formality 未能把实现侧这些端口
与参考侧的数组端口完全对应**，于是它们被当成自由变量。

按 §4.1 的分类，这属于**第 5 类**（网表侧存在未被约束的输入，功能不可判）；
但它与 A 类（`initial` 被丢弃）性质不同：**A 类是网表真的错了，B 类更可能是
"数组端口 ↔ 扁平端口"的可追溯性问题**。

**建议的下一步（未执行，供决策）**：
1. 在 DC 里对 `w_main_tbl`/`w_sub_tbl` 加 `set_verification_priority` 或
   保留数组端口（`hdlin_enable_hier_map` + `set_verification_top`，见 §3.5）
2. 或改用扁平端口的 RTL 包装层（把 `[0:17][0:15]` 改成 `[11519:0]`），
   让参考侧与实现侧端口结构一致
3. **不要**用 `set_constant` 把未约束端口绑死

### 3.5 关于 `guide_hier_map`（Formality 自己给出的提示）

每一次运行 Formality 都打印：

```
ATTENTION: No guide_hier_map commands were found in the SVF. It is
           important to enable guide_hier_map generation in Design Compiler
           to avoid SVF rejections that can cause aborted/failing points
           or long run times during verification.
           In Design Compiler the recommended methodology uses variable
           hdlin_enable_hier_map and command set_verification_top.
```

这与 §3.4.4 的判断**方向一致**：缺少 `guide_hier_map` 会让分层映射丢失，
正是"数组端口 ↔ 扁平端口"对不上的机制之一。

**但要注意**：这条提示从一开始就在，而 `i_q60` 的失败与它无关
（`i_q60` 是 `initial` 被丢弃）。所以**不能**把 20 个 failing 全归给这一条 ——
A 类已证明不是。

---

---

## 4. 归类与证据

### 4.1 判定

| 簇 | 失败点（10:17 网表） | 失败点（11:07 修复后） | 五类归属 | 依据 |
|:---|:---|:---|:---|:---|
| **A** | `i_q60_reg[*]` 14 个 | **0 个** ✅ | **第 5 类：真正的逻辑不等价** | 12 个合法 G 扫描：网表 12/12 输出常数 2，RTL 12/12 正确；`TIELBWP7T40P140` 直接驱动 `inv_q60`；`VER-281` 明示 `initial` 被丢弃；修复后 failing 归零 |
| **B** | `Wm_q30_reg[0..5]` 6 个 | `G_q30` 8 + `Wp_q30` 6 + `Wm_q30` 6 = **20 个** | **第 5 类**（网表带出未约束输入） | 2833 个 `Unconstrained Implementation Inputs` 全部是权重表端口；"哪个数组影响哪个输出"与 RTL 语义精确对应（§3.4.1） |

### 4.2 A 类与 B 类的区别（关键）

两者都归第 5 类，但**性质完全不同**：

| | A 类（`initial` LUT） | B 类（权重表端口） |
|:---|:---|:---|
| 网表功能是否真的错了 | **是**（`i_q60` 恒为 2） | **不确定**：可能只是 Formality 没匹配上 |
| 判据 | 已用独立仿真证明（12/12 错） | 未做独立仿真（见下） |
| 是否需要改 RTL | 是（改 `case`） | 不一定；可能只需改综合/验证配置 |
| 是否已修复 | **是**（`878a348`），复验 `i_q60` failing=0 | **否** |

**B 类为什么不能简单归成第 2 类（匹配问题）**：

- 支持第 2 类：`report_unmatched_points -point_type port` 在 ref/impl **两侧都报 "No unmatched points"**（`/tmp/fmport.log`），说明顶层端口数量和名字是匹配的
- 支持第 5 类：`analyze_points` 把这 2833 个端口列在 **"Unconstrained Implementation Inputs"** 下，即它们在实现侧是**自由变量**
- **这两个观察矛盾，我未能解释**（`/tmp/fmnames.log` 用 `get_ports` 拉两侧端口名对照，跑到 `SVF BEGIN` 后被 kill）

因此本报告把 B 类保守地标为 **"第 5 类，但匹配因素未完全排除"**，并列为未闭环项（§6）。

**B 类未做独立仿真的原因**：与 A 类不同，B 类**不能**用"抽出模块单独仿真"来判定 ——
`wsum_v21` 的权重表是**顶层输入端口**，必须与完整设计的激励配合才有意义。
而当时的门级 e2e 测试（`git show 674b0d8:tools/check_gate_e2e.sh`）喂的是**名义权重表**，不覆盖"权重表任意值"，
所以即使 e2e 全过也不能证明 B 类无问题。**这一层证据目前是缺的。**

### 4.3 明确排除的假设（与题面对照）

| 假设 | 是否成立 | 证据 |
|:---|:---|:---|
| 域外分支（`g_ok`）差异 | **否** | 12 个 G 全在合同域 `[2^34,2^36]` 内，网表仍全错；域外解释不了 |
| `rem` 无符号下溢 | **否** | 网表 `inv_q60` 被 TIE-LOW 拉成 0、乘法器退化成 ×1，与下溢模型无关 |
| SVF 没加载 | **否** | 两轮 `SVF Design Consistency` = 0（C 轮与修复后轮）；Guidance Summary 1977+ accepted |
| 复位/初始化（第 3 类） | **否** | 不是复位覆盖不全，是 `initial` 不可综合；且修复后 `i_q60` 已归零，与复位无关 |
| 参考/网表版本不一致（第 1 类） | **曾出现，已排除** | B 轮因 10:23 改 RTL 而作废；C 轮与修复后轮均用 md5 对齐 + `SVF Design Consistency`=0 |
| 匹配/切分点（第 2 类） | **B 类可能相关** | §4.2，未完全闭环 |
| **`i_q60` 是真实逻辑错** | **是** | §2.2 的 12 点扫描 + §5.5 修复后归零 |

**没有任何一条指向"需要加合法域约束"。** 因此不涉及合同确认问题。

---

## 5. 真实逻辑不等价：具体是哪段 RTL 与网表的差异

### 5.1 缺陷源码

`rtl/recip_q60.sv`（修复前，HEAD `45e54f1^`）：

```systemverilog
    // 64 项种子表：LUT[i] = 2^60 // (2^30 + (i+0.5)·2^24)
    logic [30:0] lut [0:63];
    initial begin
        lut[ 0] = 31'd1065418243; lut[ 1] = 31'd1049152316;
        ...                                    // 共 64 项
    end

    assign u0 = lut[idx];
```

以及后续：

```systemverilog
    always_ff @(posedge clk or negedge reset_n) begin
        ...
            u0_r <= u0;                        // 第 129 行
```

### 5.2 综合器怎么处理

DC 日志（`/tmp/dcwork/dc.log:395`）：

```
Warning: /tmp/ip/rtl/recip_q60.sv:82: The statements in initial blocks are ignored. (VER-281)
```

后续（`OPT-1206`，共 146 条）：

```
Information: The register 'u0_r_reg[30]' is a constant and will be removed.
Information: The register 'u0_r_reg[29]' is a constant and will be removed.
...                                            // 31 条，u0_r_reg[30:0]
```

### 5.3 差异链

```
initial 被忽略 (VER-281)
    ↓
lut 全 X → u0 是常量
    ↓
u0_r_reg[30:0] 被当常量删除 (OPT-1206 × 31)
    ↓
Newton 迭代 u1 = u0 + u0·e 退化（u0 是常量）
    ↓
inv_q60 不再是 G 的函数
    ↓
网表：assign inv_q60[0..26] = inv_q60[26]，由 TIELBWP7T40P140 驱动（恒 0）
    ↓
recip_rnd_v21 的乘法器 A 端 = {0,...,0,1} = 1 → 乘法退化为直通
    ↓
i_q60_reg 的 D 端全部是常数  →  i_q60 ≡ 2
```

### 5.4 为什么此前所有验证都没抓到

| 验证 | 为什么漏掉 |
|:---|:---|
| `tb_recip_q60`（43766 笔） | **RTL 级仿真**：`initial` 在仿真里**完全有效**，LUT 正常加载 |
| `tb_recip_rnd`（20817 笔） | 同上 |
| `tb_i4b_e2e` | 同上 |
| 旧门级烟测 | 观察沿太少；且 `raw` 由节点模型给固定值，`i_q60` 的误差被输出饱和/量化掩盖 |
| **Formality** | **抓到了**（20 failing） |
| **本次独立仿真** | **抓到了**（网表 12/12 输出常数 2） |

**教训：`initial` 初始化的 ROM 表在"仿真 + 综合 + 形式等价"这条链上是不可接受的。**
仿真通过 ≠ 可综合，可综合 ≠ 可形式验证 —— 这个缺陷只有形式等价和足够长的门级仿真能发现。

### 5.5 修复与复验（已由主流程完成，此处记录复验结果）

修复提交 `878a348`：`initial` 块改为 `case` 语句函数 `f_lut(idx)`，数值由脚本从原
`initial` 块提取（非手抄）。

我用**同一套 testbench**（`/tmp/tb_scan.v`）对修复后的网表复验：

| 检查项 | 修复前（10:17 网表） | 修复后（11:07 网表） |
|:---|:---|:---|
| `dc.log` `VER-281` | 1 | **0** |
| `dc.log` `OPT-1206` 中 `u0_r_reg` | 31 条 | **1 条**（且 `u0_r_reg[30]` 本就恒 0，见下） |
| 网表 `u0_r_reg[*]` 存在位数 | **0** | **30**（`[0..29]`） |
| 网表 `assign inv_q60[n] = inv_q60[26]` | 27 条 | **0** |
| 网表 TIE 驱动 `inv_q60` | 有（`TIEL… U3`） | **0** |
| `recip_q60`+`recip_rnd_v21` 及全部 DW 子模块的**模块总数**（抽件统计） | 4 | **30** |
| 其中 `recip_q60` 系模块（含 DW 子模块） | 1（无任何子模块） | **17** |
| **12 个合法 G 的输出** | **12/12 全为 2** ❌ | **12/12 全部正确** ✅ |

修复后 12 点扫描实测：

```
SCAN G=17179869184 out=67108864     SCAN G=40000000000 out=28823038
SCAN G=20000000000 out=57646075     SCAN G=45000000000 out=25620478
SCAN G=25000000000 out=46116860     SCAN G=51539607552 out=22369621
SCAN G=30000000000 out=38430717     SCAN G=55000000000 out=20962209
SCAN G=34359738368 out=33554432     SCAN G=60000000000 out=19215358
                                    SCAN G=65000000000 out=17737254
                                    SCAN G=68719476736 out=16777216
```

与 §2.2 精确 RND 列逐项一致。

#### Formality 侧复验（`/tmp/fmfix2_review_130645.log`）

| 指标 | 修复前 | 修复后 |
|:---|--:|--:|
| `i_q60_reg` failing | 14 | **0** ✅ |
| 未匹配的 reference compare point | 2073 | **0** ✅ |
| Cut-points（REF） | 1984 | **0** ✅ |
| Unread | 130 | 25 |
| Passing | 2580 | 2589 |
| **总 failing** | 20 | **20**（全部转为 `u_wsum`，见 §3.4） |

**注意**：总 failing 数没有减少，但**内容完全换了** —— `i_q60` 归零，
`u_wsum` 从 6 个扩到 20 个。所以"`i_q60` 修好了"是真的，
"Formality 通过了"是假的。**不能把前者说成后者。**

**关于修复后仍剩的 1 条 `u0_r_reg[30]` 常量删除**：这是**合法**的。
64 项 LUT 的值域是 `[538976288, 1065418243]`，全部 `< 2^30 = 1073741824`，
即 `lut[*]` 的 bit 30 **恒为 0**。DC 删除 `u0_r_reg[30]` 是正确的常量优化，
不影响功能。这一点已用 Python 逐项核验（`max(LUT) = 1065418243 < 2^30`）。

---

## 6. 未闭环项（如实记录）

| # | 项 | 状态 | 原因 / 说明 |
|:--:|:---|:---|:---|
| 1 | **B 类（`u_wsum` 20 个 failing）是否真是网表功能错误** | **未闭环** | §4.2：`report_unmatched_points` 两侧皆 "No unmatched points" 与 `analyze_points` 报 2833 个未约束端口**矛盾未解释**；且无法用"抽模块仿真"判定（权重表是顶层端口）。注意：**已复核的只是"结论可复现"，不是"矛盾已解释"** |
| 2 | **为什么 27 位不是全错**（§2.3） | **未解释** | 12 个合法 G 上 27 个 bit 全部取到过 0/1，按纯功能推理应全 failing；实测 14/19 个 |
| 3 | `guide_hier_map` 缺失是否是 B 类的成因 | **未验证** | §3.5 的提示方向一致，但**没有做过"加了 `guide_hier_map` 再跑"的对照实验** |
| 4 | ref/impl 端口命名对照表 | **未取得** | `/tmp/fmnames.log` 跑到 `SVF BEGIN` 后被 kill |
| 5 | `i_q60` 修复后的**完整回归** | **部分完成** | 已做：12 点 G 扫描（网表侧 12/12 正确）、`VER-281`=0、`u0_r_reg` 回归、Formality `i_q60` failing=0。**未做**：仓库级 `tools/verify_v21.sh` 8 项全量回归 |
| 6 | 首次 `/tmp/fmfix2.log` 被覆盖 | **已闭环** | 用唯一时间戳名重跑 `/tmp/fmfix2_review_130645.log`，11 项关键数字**逐项复现**（§7.1） |

### 已排除的无效尝试（避免重复）

1. **域外分支 / `g_ok` 约束** —— 12 个 G 全在合同域 `[2^34,2^36]` 内，网表仍全错。**不要**加 G 范围约束来"消除"失败。
2. **`rem` 无符号下溢** —— 网表 `inv_q60` 被 TIE-LOW 拉成 0、乘法器退化成 ×1，与下溢模型无关。该假设已回退。
3. **SVF 未加载** —— `STEP_SVF_OK`；两轮 `SVF Design Consistency` 均为 0。
4. **复位语义（第 3 类）** —— 不是复位覆盖不全，是 `initial` 不可综合。
5. **`set_constant` 压掉失败** —— `analyze_points` 建议对未约束端口加 `set_constant`，**明确不采纳**：属审查禁止的"裁输入域"。
6. **只靠门级 e2e 证明 B 类无问题** —— e2e 喂的是名义权重表，不覆盖"权重表任意值"，**不能**作为 B 类的证据。

---

## 7. 证据与复现

### 7.1 日志清单（远端 `vm-yian:/tmp/`）

| 文件 | 内容 |
|:---|:---|
| `/tmp/fmdiagC.log` | **主证据（修复前）**：10:17 网表 + 对齐 RTL，verify + `analyze_points -failing` 完整输出（75,741 行） |
| **`/tmp/fmfix2_review_130645.log`** | **主证据（修复后，可复核）**：11:07 网表 + `/tmp/gate3/rtl`（`SVF Design Consistency`=0），`i_q60` failing=0、`u_wsum` 20 failing、`analyze_points` 约 10.6 万行 |
| `/tmp/fmfix2.log` | **已作废**：首次那轮（原始约 8 万行输出）在 13:00 被外部重跑覆盖，现仅存 6.5 KB 启动片段；已由复核轮替代 |
| `/tmp/fmfix.log` | **作废**：修复后网表第一次跑，但 RTL 用了 `/tmp/ip/rtl/`（源树与网表不同源），`SVF Design Consistency` 报 `recip_rnd_v21.sv` 校验和不符 |
| `/tmp/fmdiagA.log` | 修复前第一轮（`analyze_points` 因脚本选项错误中止），20 failing 名单 |
| `/tmp/fmdiagB.log` | **作废**（RTL 版本漂移），保留作反面例证 |
| `/tmp/fmv12.log` | 09:15 那轮完整结果（题面引用） |
| `/tmp/dcwork/dc.log` | 综合日志：**11:07 版本**（`VER-281`=0）；10:17 版本已被覆盖 |
| `/tmp/dcwork/adc_digital_ip_v21_syn.v` | **11:07 修复后网表**（22,416,826 B，md5 `5e738fe5…`）；10:17 诊断对象已被覆盖 |
| `/tmp/gate3/rtl/` | 修复后网表（11:07）的**真实源树**，md5 与仓库完全一致 |
| `/tmp/fmproof/` | 独立仿真工作区（`net_recip.v`=旧网表抽取、`net_recip_new.v`=新网表抽取、`tb_scan.v`） |
| `/tmp/fmdiag_C.tcl`、`/tmp/fmfix2.tcl` | 两个主轮次的脚本（可直接复跑） |
| `/tmp/fmport.log` | `report_unmatched_points` 探针（两侧皆 "No unmatched points"） |

> **重要（本次第二次踩"源树不对齐"）**：11:07 那次 DC 读的是 `/tmp/gate3/rtl/`，
> **不是** `/tmp/ip/rtl/`。我第一次跑修复后网表的 Formality 时用了 `/tmp/ip/rtl/`，
> 结果 `SVF Design Consistency` 报 `recip_rnd_v21.sv` 校验和不符（`/tmp/fmfix.log`），
> 该轮**作废**（`/tmp/fmfix.log`）。改用 `/tmp/gate3/rtl/` 后校验和归零（`/tmp/fmfix2_review_130645.log`）。
>
> 附：11:07 那次 DC 用的 `recip_rnd_v21.sv` 是 **4912 B**（= 仓库版，无 `prod2`），
> 而 `/tmp/ip/rtl/v21/recip_rnd_v21.sv` 是 **5712 B**（09:24 的实验版）。
> 两者不等价 —— 这也是"必须用同一棵源树"的又一个理由。

> **日志覆盖与复核（重要，过程记录）**
>
> 首次那轮写的是 `/tmp/fmfix2.log`，其原始输出（约 8 万行，含 `analyze_points` 完整输出）
> 在 **13:00 被一次外部重跑覆盖**，缩为 6,584 B 的启动片段。
> 本文曾一度只在"覆盖前读取"的记录上引用该轮数字。
>
> **已用唯一时间戳文件名重跑复核**：`/tmp/fmfix2_review_130645.log`
> （同一份 `fmfix2.tcl`、同一网表、同一 RTL 源树）。复核结果**逐项复现**：
>
> | 项 | 首次（已覆盖） | 复核（`fmfix2_review_130645.log`） | 一致 |
> |:---|--:|--:|:--:|
> | Passing | 2589 | **2589** | ✅ |
> | Failing | 20 | **20** | ✅ |
> | Unverified | 614 | **614** | ✅ |
> | Constant reg | 48 | **48** | ✅ |
> | Unread | 25 | **25** | ✅ |
> | Unconstrained Impl Inputs | 2833 | **2833** | ✅ |
> | Unmatched Cone Inputs | 5676 | **5676** | ✅ |
> | 其中 glob unmatched 权重表条目 | 5668 | **5668** | ✅ |
> | 其中 `i_q60`/`inv_q60` 条目 | 0 | **0** | ✅ |
> | Required Inputs | 2355 | **2355** | ✅ |
> | 20 个 failing 名单 | 见 §3.4 | **逐字一致** | ✅ |
>
> 因此 §0.2、§3.4、§4.1、§5.5 的全部数字**均可从复核日志独立核对**，
> 不再依赖"覆盖前的记忆"。
>
> **教训**：多人/多轮共用 `/tmp` 下固定日志名会被静默覆盖。
> 后续用带时间戳的唯一名，例如 `fmfix2_$(date +%H%M%S).log`。

### 7.2 独立仿真复现（不依赖 Formality，A 类）

```bash
# 1) 从网表抽出 recip 相关模块
python3 - <<'PY'
src = open("/tmp/dcwork/adc_digital_ip_v21_syn.v").read().split("\n")
out, keep = [], False
for l in src:
    if l.startswith("module "):
        n = l.split()[1].split("(")[0]
        keep = n.startswith("recip_q60") or n.startswith("recip_rnd_v21")
    if keep:
        out.append(l)
    if l.startswith("endmodule"):
        keep = False
open("/tmp/fmproof/net_recip.v", "w").write("\n".join(out) + "\n")
PY

# 2) 12 点 G 扫描（tb_scan.v 在 /tmp/fmproof/）
cd /tmp/fmproof
L=/home/yian/PDK/tsmc28/digital_technology/TSMCHOME/digital/Front_End/verilog/tcbn28hpcplusbwp7t40p140_110a/tcbn28hpcplusbwp7t40p140.v
vcs -full64 -sverilog -timescale=1ns/1ps -o simscan_net \
    net_recip.v tb_scan.v $L -Mdir=csrc_scan_net
./simscan_net | grep '^SCAN'        # 旧网表：12/12 输出 2

# 3) RTL 侧同一 testbench
vcs -full64 -sverilog -timescale=1ns/1ps -o simscan_rtl \
    recip_q60.sv recip_rnd_v21.sv tb_scan.v -Mdir=csrc_scan_rtl
./simscan_rtl | grep '^SCAN'        # 12/12 正确
```

`tb_scan.v` 的要点（避免采样错位，这是本报告里试错最多的地方）：

- 每个 G **独立复位** → 单次 `valid_i` 高脉冲 → 等 `valid_o` → 采样 `i_q60`
- 用 `@(negedge clk)` 驱动激励、`@(posedge clk)` 采样，**不要**在 `@(posedge clk)` 同一时刻
  既驱动又采样（那样会读到同一个沿的旧值，产生假象）
- 我最初写的队列式 testbench（`tb_cmp.v`）因为无法知道 `valid_o` 对应哪一次输入，
  得到过"RTL 也全错"的错误结果，已废弃

### 7.3 Formality 复跑

```bash
# 修复后网表（注意用 /tmp/gate3/rtl，与网表同源）
ssh vm-yian 'cd /tmp && PATH=/opt/synopsys/fm/W-2024.09-SP3/bin:$PATH \
  SNPSLMD_LICENSE_FILE=27080@localhost \
  fm_shell -f /tmp/fmfix2.tcl > /tmp/fmfix2_$(date +%H%M%S).log 2>&1'
grep -E "Compare point .* failed|Passing compare|Failing compare" /tmp/fmfix2_*.log
```

**跑之前务必先确认版本对齐**（本次踩了两次坑）：

```bash
md5sum /tmp/gate3/rtl/recip_q60.sv /tmp/gate3/rtl/v21/wsum_v21.sv  # 应与网表同源
grep -c "SVF Design Consistency" /tmp/fmfix2_*.log                # 必须为 0
```

---

## 8. 总结

**20 个 failing 是两件独立的事。**

### A 类：`i_q60` —— 真实逻辑错误，已定位、已修复、已复验

`rtl/recip_q60.sv` 用 `initial` 块初始化 64 项 Newton 种子表，DC 直接丢弃
（`VER-281`）→ `u0_r_reg[30:0]` 被当常量删除（`OPT-1206`）→ Newton 退化 →
`inv_q60` 被 `TIELBWP7T40P140` 拉成恒 0 → 乘法器退化成 ×1 → **网表 `i_q60` 恒等于 2**。

铁证（不依赖 Formality）：12 个合同域内的 G 逐一扫描，**RTL 12/12 全对，
网表 12/12 全部输出 2**。

修复（`878a348`，`initial` → `case`）复验：
`i_q60_reg` failing **14 → 0**；未匹配的参考侧 compare point **2073 → 0**；
`VER-281` **0**；`u0_r_reg[0..29]` 回到网表；网表侧 12 点扫描 **12/12 正确**。

### B 类：`u_wsum` —— 权重表端口未约束，未修复

`i_q60` 修好后，剩下的 **20 个 failing 全部集中在 `u_wsum`**
（`G_q30` 8 + `Wp_q30` 6 + `Wm_q30` 6）。
`analyze_points` 报 **2833 个 `Unconstrained Implementation Inputs`，全部是
`w_main_tbl`/`w_sub_tbl` 端口**；且"哪个数组影响哪个输出"与 RTL 语义精确对应
（`f_G` 只用 main、`f_Wp` 只用 sub、`f_Wm` 两者都用）。

**这一簇我倾向判断为"数组端口 ↔ 扁平端口"的可追溯性问题（第 2 类），
但保守归为第 5 类**，因为 `report_unmatched_points` 两侧都报 "No unmatched points"
与 `analyze_points` 的结论矛盾，且缺独立仿真证据（§4.2）。

### 两簇都不需要加任何域约束

因此不涉及"合同是否明确、上游是否保证"的判定。
`analyze_points` 建议的 `set_constant` 被明确拒绝。

### 下一步建议（未执行）

1. **B 类优先试 `guide_hier_map`**：在 DC 里加 `set_app_var hdlin_enable_hier_map true`
   与 `set_verification_top`，重综合后重跑 —— 这是 Formality 自己反复提示的方向，
   也是"数组端口对不上"的最直接解释
2. 若仍不行，考虑给 `wsum_v21` 加一层扁平端口包装（`[11519:0]`）使两侧结构一致
3. **不要**用 `set_constant` 把未约束端口绑死来"消掉"失败

### 证据分层声明

| 层 | 内容 | 确定性 |
|:---|:---|:---|
| 独立仿真 | §2.2（10:17 网表 12/12 输出 2）、§5.5（修复后 12/12 正确） | **确定** |
| 工具观测 | §1、§3.4 的 Formality 输出（Passing/Failing/Unverified、analyze_points 分类）；**已用 `/tmp/fmfix2_review_130645.log` 独立复现 11 项数字** | 工具输出，非结论（但可复现） |
| 未闭环 | §2.3（散布式）、§4.2（B 类匹配因素）、§6 #3（`guide_hier_map` 对照） | **不下结论** |

**本文不是 signoff 结论。** Formality 当前仍是 `FAILED`（20 failing，全在 `u_wsum`）。
