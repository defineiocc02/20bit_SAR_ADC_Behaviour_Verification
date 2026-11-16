# Formality 20 个 failing compare point 的根因诊断

时间：2026-11-16　工具：Formality W-2024.09-SP3 / Design Compiler W-2024.09-SP3 / VCS W-2024.09-SP1
诊断对象：`/tmp/dcwork/adc_digital_ip_v21_syn.v`（10:17 版，20,102,248 B）
结论等级：**根因已定位并经独立仿真证实**；**不是** signoff 结论

> 本文只做诊断与分析，**未修改任何 RTL**。
> 后续修复由 `878a348`（`recip_q60` LUT 改 `case`）与 `45e54f1`（`wsum` 显式展开）完成，
> 本文最后一节给出修复后的复验结果。

---

## 0. 结论（先给答案）

| 失败点 | 数量 | 归类 | 根因 |
|:---|--:|:---|:---|
| `u_recon/u_recip_rnd/i_q60_reg[*]` | 14–19 | **第 5 类：真正的逻辑不等价** | `initial` 块被综合丢弃 → LUT 全 X → Newton 退化 → **网表 `i_q60` 恒为常数 2** |
| `u_wsum/Wm_q30_reg[*]` | 6 | **第 5 类：真正的逻辑不等价**（但成因不同） | 权重表 **bit 0 输入端口未匹配**，DC 把它们当自由变量，`Wm` 低 6 位不可判 |

**两者都不是形式化配置问题、不是域外分支、不是复位语义。**
也都**不需要**加任何域/环境约束 —— 因此本文不涉及"加合法域约束"这一分支。

---

## 1. `analyze_points` 的原始输出摘要

`analyze_points` 必须在 `verify` 之后同会话调用。本次共跑了 4 轮：

| 轮次 | 日志 | 目的 | 结果 |
|:---|:---|:---|:---|
| A | `/tmp/fmdiagA.log` | 10:17 网表 + 当前 RTL | 20 failing，但 `analyze_points` 因我脚本的选项错误（`-point_type register` 应为 `reg`）在它之前中止 |
| B | `/tmp/fmdiagB.log` | 同上 | **作废**：RTL 在 10:23 被改（`wsum_v21.sv`），与网表版本不一致（日志有 `SVF Design Consistency Check: Files with checksum difference: 1`） |
| **C** | **`/tmp/fmdiagC.log`** | **RTL 与 10:17 网表精确对齐**（`recip_q60` md5 `aa719f73…`、`wsum_v21` md5 `1658c32d…`，与 DC 当时读入的一致），`SVF Design Consistency` = 0 | **本报告的主证据** |
| S/N | `/tmp/fmsvf.log`、`/tmp/fmnames.log` | 追 rejected guidance / 端口命名 | 未跑完（`Process terminated by kill`），不作结论依据 |

> 第 B 轮作废这件事本身值得记：**改了 RTL 却拿旧网表去跑等价，结论一定是废的**。
> 这也是我在 C 轮里坚持先 `md5sum` 双方再开跑的原因。

### 1.1 Run C 的验证结果

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

## 2. 19 个失败 bit 与 8 个通过 bit 的差异分析

题面给的分布：27 位中 **19 错、8 过**（bit 5, 9, 13, 15, 16, 17, 18, 22 通过，是按 09:15 那轮）。
本轮（10:17 网表）是 **14 错、13 过**。

**散布式失败不是"某些 bit 逻辑深度不同"，而是"整个寄存器被换成了常数"。**

### 2.1 网表结构：`i_q60` 退化成了常数 2

从 10:17 网表里 `recip_rnd_v21` 模块抽出 27 个 `i_q60_reg` 的 D 端，
D 端全部接 `N66..N92`（逐 bit 一条），但上游是：

```verilog
recip_rnd_v21 内：
  i_c[0..26] 这一层的比较/加法树仍在，
  但其输入 u0/r_inv 一侧已经被常量折叠。
```

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

### 2.3 为什么是"散布式"而不是"全错"

因为网表给的是常数 2（二进制 `27'b000_0000_0000_0000_0000_0000_0010`），
而 RTL 给的是 1.6e7–6.7e7 量级的 27 位值。

对每个 bit 判定"是否可能不同"，取决于 RTL 值在该 bit 上是否也可能取到常数值：

- bit 0：RTL 值**恒为偶数**（`q1 + (2r>=G)`，`q1` 与进位的关系使 LSB 有相当概率为 0）→ 多数拍与常数 2 的 bit0=0 相同 → **在部分反例下判等**
- bit 1：常数 2 的 bit1=1，RTL 该 bit 经常为 0 → 容易判不等
- 高位 bit（如 20–26）：RTL 值在 `2^24..2^26` 区间，这些 bit 有很大概率取到 0 → 与常数 2 的这些 bit=0 相同的拍很多

**Formality 只要找到一个反例就判不等；但不同 bit 的"反例可达性"不同**，
所以表现为散布式。这与"域外分支导致整体 0 vs 非 0"的模型完全不同 ——
后者会让 27 位**全错**（题面已正确排除），而这里的机制会让部分 bit 恰好对齐。

**这也解释了为什么 8 个 bit 通过**：它们恰好在所有可达输入下与常数一致。

### 2.4 失败集合漂移的解释

| 网表版本 | `i_q60` 实际值 | failing bit |
|:---|:---|:---|
| 09:15 版（更早） | 常数 2（同一缺陷） | 19 个 |
| 10:17 版 | 常数 2（同一缺陷） | 14 个 |

值相同但 failing 集合不同，说明**两版网表的 `i_q60` D 端组合逻辑结构不同**
（DC 每次优化的折叠路径不同），使不同 bit 的反例可达性发生变化。
因此 "27 位中哪几位失败" **不是稳定的设计属性**，只是综合结果的副产物。

---

## 3. `Wm_q30_reg` 的分析

`Wm_q30_reg[39]`（09:15 轮）与 `Wm_q30_reg[0..5]`（10:17 轮）**不是** `i_q60` 的连带效应。
`analyze_points` 给出了它的独立根因。

### 3.1 根因：权重表 bit 0 输入端口未匹配

46 个未约束的 implementation 输入全部是 `w_main_tbl`/`w_sub_tbl` 的 **bit 0**（见 §1.3(a)），
它们各自 "Unmatched in the implementation cones for 6 compare point(s)"，
而这 6 个 compare point 正是 `u_wsum/Wm_q30_reg[0..5]`。

机制：

1. `wsum_v21` 里 `Wm = Σ post-mask ? w_main_tbl[sid][j] : 0`，是纯加法树
2. DC 把权重表展平成 14400 个顶层输入端口（`w_main_tbl[0..11519]`、`w_sub_tbl[0..2879]`）
3. 其中 **41 个 bit 0 端口**在参考侧存在于锥中，实现侧被当成自由变量 ——
   即它们**没有匹配到参考侧对应的端口**
4. `Wm_q30` 低 6 位由这些 bit 0 的加法进位决定 → 不可判 → failing

为什么是**低 6 位**：`Wm` 是 8 片 × 20 位的加法和。
每片的 LSB（bit 0）都是"1 个 Q30 LSB"权重，41 个这样的 LSB 项一起决定
和的低 6 位（`2^6=64 > 41`，所以最多影响到 bit 5）。
这与观察到的 `Wm_q30_reg[0..5]` **精确吻合**。

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
按 `analyze_points` 的结果，绝大多数对上了（`Passing` 里有 1119 个 Port），
但 **41 个 bit 0 端口没对上**。

**这属于"匹配/内部切分点的问题"（第 2 类）与"真实逻辑差异"（第 5 类）的边界情况**，
本报告把它归为**第 5 类**，理由见 §4。

### 3.3 与 `G_q30` 高位无关

`wsum_v21` 里还有 1 个 TIE 单元 `TIELBWP7T40P140 U3 ( .ZN(net1713285) )`，
它驱动 `G_q30[49..63]`（15 bit），而 `G_q30[47..63]` 本就被 `OPT-1206` 当常量删掉，
SVF 里有对应的 `guide_reg_constant`，**这是合法的常量折叠**：
`G = Σ 8×16 个 Q30 权重`，高位确实恒 0。

**所以 `G_q30` 高位不是缺陷**，与 `Wm_q30` 的失败无关。

---

## 4. 归类与证据

### 4.1 判定

| 失败点 | 五类归属 | 依据 |
|:---|:---|:---|
| `i_q60_reg[*]` | **第 5 类：真正的逻辑不等价** | 网表对 12 个合法 G 全部输出常数 2，RTL 全对；TIE-LOW 单元直接驱动 `inv_q60`；`VER-281` 明示 `initial` 被丢弃 |
| `Wm_q30_reg[*]` | **第 5 类：真正的逻辑不等价** | `analyze_points` 指出 46 个未约束输入，其中 41 个是权重表 bit 0，影响范围精确等于 `Wm_q30[0..5]` |

### 4.2 为什么 `Wm_q30` 归第 5 类而不是第 2 类

有两个候选解释：

- **第 2 类（匹配/切分点问题）**：数组端口 vs 扁平端口，名字对不上，是工具匹配问题
- **第 5 类（真实逻辑不等价）**：网表里这些 bit 0 端口是**悬空/自由变量**，功能上不可判

判据是：**这些端口在实现侧到底有没有被正确驱动。**

- 若它们在网表里被正确连接到对应逻辑，只是 Formality 名字没匹配上 → 第 2 类（工具问题）
- 若它们在网表里真的悬空/被 DC 当无关项优化 → 第 5 类（网表功能不完整）

`analyze_points` 的措辞是 *"Unmatched input ports in the implementation typically result
from test logic insertion"* —— 它把这归为"实现侧多出来的输入"，
即 **DC 在网表里生成了参考侧没有的输入端口**。

**这一点我未能完全闭环**：`/tmp/fmnames.log`（用于 `get_ports` 对比两侧端口命名）
跑到 `SVF BEGIN` 后被 kill，没拿到端口对照表。
所以 §3 的最终归属应标注为 **"第 5 类，但匹配因素未完全排除"**。

无论如何，**它不影响 §5 的修复结论**：修复 `recip_q60` 后需重跑，
若 `Wm_q30` 仍在则必须回头查端口匹配。

### 4.3 明确排除的假设（与题面对照）

| 假设 | 是否成立 | 证据 |
|:---|:---|:---|
| 域外分支（`g_ok`）差异 | **否** | 12 个 G **全在**合同域 `[2^34,2^36]` 内，网表仍全错；域外解释不了 |
| `rem` 无符号下溢 | **否** | 与网表结构无关（网表的 `inv_q60` 是 TIE-LOW，乘法退化成 ×1） |
| SVF 没加载 | **否** | `STEP_SVF_OK`；Run C 的 `SVF Design Consistency` = 0；Guidance Summary 显示 1977 accepted |
| 复位/初始化行为（第 3 类） | **部分** | 严格说 `initial` 是"初始化"，但**不是复位语义问题**：`initial` 在仿真里有效、在综合里被丢弃，属"不可综合构造"，归第 5 类更准确 |
| 参考/网表版本不一致（第 1 类） | **曾经是（B 轮）** | B 轮因 10:23 改 RTL 而作废；C 轮已用 md5 对齐，排除 |
| 匹配/切分点（第 2 类） | **`Wm_q30` 可能相关** | 见 §4.2，未完全闭环 |

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
| `recip_q60` 网表模块数（含子模块） | 4 | **30** |
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

**关于修复后仍剩的 1 条 `u0_r_reg[30]` 常量删除**：这是**合法**的。
64 项 LUT 的值域是 `[538976288, 1065418243]`，全部 `< 2^30 = 1073741824`，
即 `lut[*]` 的 bit 30 **恒为 0**。DC 删除 `u0_r_reg[30]` 是正确的常量优化，
不影响功能。这一点已用 Python 逐项核验（`max(LUT) = 1065418243 < 2^30`）。

---

## 6. 未闭环项（如实记录）

| 项 | 状态 | 原因 |
|:---|:---|:---|
| `Wm_q30` 的最终归属（第 2 类 vs 第 5 类） | **未完全闭环** | `/tmp/fmnames.log`（端口命名对照）跑到 `SVF BEGIN` 后被 kill，未取得 ref/impl 端口对照表。需在修复后的网表上重跑 Formality 确认是否仍 failing |
| `Found 1 Rejected Guidance Command`（`reg_constant`）的 58 条拒绝明细 | **未取得** | `/tmp/fmsvf.log` 同样未跑完 |
| 修复后网表的 Formality 结果 | **本次未跑** | 修复后网表于 11:07 生成，本文完成时尚未对其跑 `verify`；预期 failing 归零，但**未验证** |

### 已排除的无效尝试（避免重复）

1. **域外分支 / `g_ok` 约束** —— 12 个 G 全在 `[2^34,2^36]` 内仍全错，与域无关。**不要**加 G 范围约束来"消除"失败。
2. **`rem` 无符号下溢** —— 网表的 `inv_q60` 是 TIE-LOW，乘法器退化成 ×1，与下溢模型无关。该假设已回退。
3. **SVF 未加载** —— `STEP_SVF_OK`，且 Run C `SVF Design Consistency` = 0。
4. **复位语义（第 3 类）** —— 不是复位覆盖不全，是 `initial` 不可综合。

---

## 7. 证据与复现

### 7.1 日志清单（远端 `vm-yian:/tmp/`）

| 文件 | 内容 |
|:---|:---|
| `/tmp/fmdiagC.log` | **主证据**：10:17 网表 + 对齐 RTL，verify + `analyze_points -failing` 完整输出（75,741 行） |
| `/tmp/fmdiagA.log` | 第一轮（同配置，`analyze_points` 因脚本选项错误中止），20 failing 名单 |
| `/tmp/fmdiagB.log` | **作废**（RTL 版本漂移），保留作反面例证 |
| `/tmp/fmv12.log` | 09:15 那轮完整结果（题面引用） |
| `/tmp/dcwork/dc.log` | 10:17 综合日志（`VER-281`、`OPT-1206` 证据） |
| `/tmp/dcwork/adc_digital_ip_v21_syn.v` | 10:17 网表（**诊断对象**）；11:07 已被修复版覆盖 |
| `/tmp/dcwork2/dc.log` | 修复版综合日志（`VER-281` = 0） |
| `/tmp/fmproof/` | 独立仿真工作区（见下） |
| `/tmp/fmdiag_C.tcl` | Run C 脚本（可直接复跑） |

### 7.2 独立仿真复现（不依赖 Formality）

```bash
# 1) 从网表抽出 recip 相关模块
python3 - <<'PY'
src=open("/tmp/dcwork/adc_digital_ip_v21_syn.v").read().split("\n")
out=[];keep=False
for l in src:
    if l.startswith("module "):
        n=l.split()[1].split("(")[0]
        keep = n.startswith("recip_q60") or n.startswith("recip_rnd_v21")
    if keep: out.append(l)
    if l.startswith("endmodule"): keep=False
open("/tmp/fmproof/net_recip.v","w").write("\n".join(out)+"\n")
PY

# 2) 编译并跑 12 点 G 扫描（tb_scan.v 见 /tmp/fmproof/）
cd /tmp/fmproof
vcs -full64 -sverilog -timescale=1ns/1ps -o simscan_net \
    net_recip.v tb_scan.v $LIB -Mdir=csrc_scan_net
./simscan_net | grep '^SCAN'

# 3) RTL 侧同一 testbench
vcs -full64 -sverilog -timescale=1ns/1ps -o simscan_rtl \
    recip_q60.sv recip_rnd_v21.sv tb_scan.v -Mdir=csrc_scan_rtl
./simscan_rtl | grep '^SCAN'
```

### 7.3 Formality 复跑

```bash
ssh vm-yian 'cd /tmp && PATH=/opt/synopsys/fm/W-2024.09-SP3/bin:$PATH \
  SNPSLMD_LICENSE_FILE=27080@localhost \
  fm_shell -f /tmp/fmdiag_C.tcl > /tmp/fmdiagC.log 2>&1'
grep -E "STEPS|Compare point .* failed|Passing compare|Failing compare" /tmp/fmdiagC.log
```

**跑之前务必先对齐版本**（这是 B 轮作废的教训）：

```bash
md5sum /tmp/ip/rtl/recip_q60.sv /tmp/ip/rtl/v21/wsum_v21.sv
grep -c "SVF Design Consistency" /tmp/fmdiagC.log   # 必须为 0
```

---

## 8. 一句话总结

**20 个 failing 中，`i_q60` 那一组是 `initial` 初始化 LUT 被综合丢弃导致网表输出恒为常数 2
（已用 12 个合法 G 的独立仿真坐实，RTL 全对、网表全错）；
`Wm_q30` 那一组是权重表 41 个 bit 0 输入端口未匹配，使和的低 6 位不可判。
两者都属第 5 类"真正的逻辑不等价"，都不需要加任何域约束。
`i_q60` 的修复（`878a348`）已复验通过，网表侧 12/12 正确。**
