# RTL 算术契约（RTL arithmetic contract）

- 状态：**已冻结（P0 交付物 1/2）**，2026-09-18
- 上位依据：`docs/adr/0014-fixed-point-output-contract.md`（不可变）、`src/adi_model/fixed_point.py`（实现）
- 既有判据：`tests/unit/test_fixed_point.py`（8 个用例，本契约不得与之冲突）
- 适用对象：`rtl/` 子树下全部可综合模块；**只约束数字域整数算术**
- 生成本文件的参数来自 `tools/export_rtl_params.py`，单一真相源是 `adi_model.config.Config`

> **English abstract.** This contract freezes the integer arithmetic of the digital
> core: Q30 effective `C/Cf` weights in 48-bit signed registers, Q32 normalized
> `V/Vfs` voltages in 64-bit signed registers, a **checked** 96-bit signed
> accumulator that raises instead of wrapping, a 20-bit offset-binary mid-rise
> output, one runtime round-half-to-even shift, and one runtime **floor**
> division. It restates — and does not extend — ADR 0014. Where this document and
> ADR 0014 disagree, ADR 0014 wins and this document is wrong.

---

## 0. 为什么需要这份契约

ADR 0014 写的是"数字核输出什么"；它没有写"RTL 怎么算出这个输出"。两者之间有一个
**只有硬件才会踩的缝**：

| 缝 | Python 的行为 | RTL 的默认行为 | 后果 |
|:---|:---|:---|:---|
| 整除 | `//` 向 **−∞** 取整（floor） | `/` 向 **0** 截断 | 负商差 1 个 LSB，且**只在半个码流上出错**，仿真激励选得好就发现不了 |
| 舍入 | `np.rint` = 半值向偶数 | 常见写法是"加 0.5 再截断" | 权重与 bin center 出现系统性半 LSB 偏差 |
| 溢出 | `raise`，不 wrap | 默认静默回绕 | 极端码上输出变成另一端的值，看起来"还在量程内" |
| 位宽 | Python 任意精度 | 必须显式声明 | 声明过窄 → 溢出；过宽 → 面积 |

这份契约把这四个缝逐一钉死。**任何一条改动都必须走 ADR，不得就地修改。**

---

## 1. 量的定义与单位

### 1.1 符号表

| 符号 | 含义 | 单位 | 定点 | 来源 |
|:---|:---|:---|:---|:---|
| `W` | 每个物理单位的**有效** `C/Cf` 权重（子阵列单位已含桥接衰减 β） | 无量纲 | **Q30**，正有符号整数 | 离线标定 / 名义几何 |
| `a` | 该单位本次是否接信号 | {0,1} | 1 bit | DEM 开关译码 |
| `s` | 该单位底板轨符号 | {−1,0,+1} | 2 bit 有符号 | 开关译码 + 已知注入 |
| `I` | 已知注入量（dither / 采样掩码的已知部分） | V/Vfs | **Q32** 有符号 | 数字侧已知量 |
| `F` | 后端（ADC2）码仓中心电压 | V/Vfs | **Q32** 有符号 | §3.1 由原始码算出 |
| `O` | 后端偏移系数 | V/Vfs | **Q32** 有符号 | 标定寄存器 |
| `G` | 量化后的信号增益 `Σ(W·a)` | 无量纲 | 正整数，**必须 > 0** | 本次开关状态决定 |
| `word` | 最终输出字 | offset-binary | **20 bit** | 本契约输出 |

单位契约：**除 `word` 外全部是"归一化到 Vfs"的无量纲定点量**。

> **措辞修正（2026-09-18，外部复核）**：早先写的是"RTL 内部不出现伏特"，这句**容易被过度理解**。
> 正确的约束是：**RTL 内不做浮点运行时的伏特运算**，但允许、而且必须传递**带明确位宽、符号与
> 缩放定义的定点编码**——`F`/`O`/`I` 本身就是 Q32 的归一化 `V/Vfs`，它们有确切的物理对应关系。
> 禁止的是把浮点行为模型直接搬进 RTL 的伏特值，不是禁止用整数承载模拟观测量。
> 单位换算在模型与导出侧完成；进入 RTL 的归一化编码与标定整数一律由本契约定义。

### 1.2 三类"电压"的区分（易混，必须区分）

| 名称 | 定义 | 进 RTL？ |
|:---|:---|:---|
| `F`（bin center） | `adc2_min_q + round_half_even(...)` | ✅ 是 |
| `O`（offset） | 标定寄存器 | ✅ 是 |
| `Vfs` | 满幅峰值（默认 3.0 V） | ❌ 否，仅离线评分 |
| 解码电压 `v = Vfs·(2(word+0.5)/2²⁰ − 1)` | 最终码 → 输入等效电压 | ❌ **仅评分**，见 §7 |

---

## 2. 位宽与格式（冻结值）

来源：`fixed_point.FixedPointFormat` 默认实例。RTL 必须**逐字**实现这张表。

| 参数 | 值 | 说明 | 合法域（实现强制） |
|:---|---:|:---|:---|
| `W_FRAC` | 30 | 权重小数位 | `W_FRAC < W_BITS ≤ 62` |
| `W_BITS` | 48 | 权重寄存器位宽（含符号） | 同上 |
| `V_FRAC` | 32 | 电压小数位 | `V_FRAC ≤ 48` |
| `V_BITS` | 64 | 电压寄存器位宽（含符号） | `V_FRAC < V_BITS ≤ 64` |
| `ACC_BITS` | **96** | 受检累加器位宽（含符号） | `8 ≤ ACC_BITS ≤ 256` |
| `OUT_BITS` | 20 | 输出位宽 | `OUT_BITS ≤ 30` |

派生整数量：

| 名称 | 表达式 | 说明 |
|:---|:---|:---|
| `wscale` | `2^W_FRAC` = 2³⁰ | 权重定标 |
| `vscale` | `2^V_FRAC` = 2³² | 电压定标 |
| `count` | `2^OUT_BITS` = 2²⁰ | 输出码总数 |
| `limit` | `2^(ACC_BITS−1)` = 2⁹⁵ | 受检累加器上界（**含符号位**） |

---

## 3. 运行时算术（RTL 必须逐位复现的部分）

运行时只有 **5 步**。全部为整数运算，无浮点。

### 3.1 后端码仓中心 `F` — 唯一的运行时"舍入"

```
n   = (2·adc2_code + 1) · (adc2_max_q − adc2_min_q)          # 有符号整数
F   = adc2_min_q + round_half_even_shift(n, adc2_n_bits + 1)
```

其中 `round_half_even_shift(x, k)` 定义为：

```
q = floor(x / 2^k)          # 等价于有符号右移 k 位（算术右移）
r = x − q·2^k               # 余数，符号随 x
k_shifted = 2^k
result = q + ( 2r > k_shifted  or  (2r == k_shifted and (q & 1) == 1) )
```

实现注记（已验证，见 `tests/unit/test_rtl_export.py`）：

* 除数 `2^(adc2_n_bits+1)` 是 **2 的幂** → 硬件上是**移位 + 一组比较器**，无除法器。
* `2r == k_shifted` 是**平局**判定 → 向偶：由 `q` 的最低位决定。
* `(q & 1) == 1` 对负数同样成立（二补数的最低有效位即奇偶性）。
* 在合法配置下 `adc2_max_q > adc2_min_q` 且 `adc2_code ≥ 0`，故 `n ≥ 0`、`q ≥ 0`——
  **但 RTL 不得依赖这一条**，必须按有符号通式实现，否则换配置会静默错。

对照既有判据：`tests/unit/test_fixed_point.py::test_signed_ties_to_even` 用除数 2 固定了
8 个样例（`−7→−4, −5→−2, −3→−2, −1→0, 1→0, 3→2, 5→2, 7→4`）。RTL 的 `round_even_divide`
必须复现这 8 个值。

### 3.2 三个掩码和

```
rails  = Σ ( W · s )        # 逐 (slice, unit) 求和；s ∈ {−1,0,+1}
gain   = Σ ( W · a )        # G，必须 > 0
total  = Σ ( W )            # 与开关状态无关的名义总量
```

* 求和范围：`n_active × (dac_n_main + dac_n_sub)` = 8 × 71 = 568 个权重项。
* `W` 从权重 ROM 按 `(slice_id, unit_idx)` 取出，**索引顺序必须与
  `weight_calibration._terms` 一致**（历史上 A07.6 类缺陷即由索引/口径错配造成）。
* `total` 在开关状态固定的配置下是**常量**，可预计算；但 DEM 改变不了它（等权置换），
  因此可以做成 RTL 常量。**注意 `total` 不是 `gain`**——`gain` 随掩码变。

### 3.3 分子

```
num = (F − O)·wscale  −  rails·vscale  −  total·I
```

### 3.4 平移后的分子（"shifted"）

```
shifted = ( num + gain·vscale ) · count
```

### 3.5 最终字 — 唯一的运行时"除法"，且是 **floor**

```
denom = 2·gain·vscale = gain · 2^33
word  = floor( shifted / denom )              # 向 −∞ 取整
```

**这一步是整份契约里最容易写错的地方。** RTL 的 `/` 默认向 0 截断，必须补修正：

```
q_trunc = shifted / denom                     # RTL 默认语义，向 0 截断
r       = shifted % denom                     # 余数，符号随被除数
word    = (r != 0 && shifted < 0) ? (q_trunc − 1) : q_trunc
```

#### 3.5.1 推荐的硬件分解（把 2 的幂剥掉）

因为 `denom = gain · 2^33`，而嵌套地板恒等式
`floor(floor(A/m)/n) = floor(A/(mn))`（`A` 整数、`m,n` 正整数）成立，于是

```
A1   = shifted >>> 33          # 算术右移 33 位 —— 即 floor(shifted / 2^33)
word = floor( A1 / gain )      # 只剩一个 40 bit 量级的无符号除数
```

**收益**：33 位移位在硬件上是免费连线；于是"96 位 ÷ 73 位"退化为
"63 位 ÷ 40 位（典型值）"，除数与被除数都缩小一个数量级。

**义务**：这条恒等式**必须在 RTL 侧用与 §3.5 相同的向量集验证**，不得以"数学上显然"为由跳过。
（恒等式已在本契约的 P0 门禁中被数值验证；RTL 实现的验证属 P2。）

#### 3.5.2 除以 `gain` 的候选实现（P2 决策，此处只登记约束）

| 方案 | 约束 |
|:---|:---|
| 迭代除法器 | 必须给出迭代次数上界；`gain` 位宽决定循环长度 |
| 倒数 ROM + 一次乘法 + 修正 | `gain` 的取值空间必须有界；需给出 ROM 深度与修正项的正确性证明 |
| 恒定除法（若 `gain` 在给定配置下为常量） | **仅当** `gain` 与 DEM/开关状态无关时成立；`gain = Σ(W·a)` **随掩码变化**，故一般**不成立**，不得假设 |

> 无论选哪种，都必须满足：商误差 **恰为 0**（这是 bit-exact 的前提，见 D2）。
> 浮点近似除法不满足本契约。

---

## 4. 溢出语义 —— 不 wrap

来源：ADR 0014「Overflow raises an error; arithmetic does not wrap」。

### 4.1 受检操作数（6 个）

```
op1 = (F − O)·wscale
op2 = rails·vscale
op3 = total·I
op4 = num
op5 = shifted
op6 = denom
```

### 4.2 判定

```
若任意 op 满足  (op ≤ −2^95) 或 (op ≥ +2^95)   → 溢出
```

**边界的开闭是精确的**：代码用的是 `not (-limit <= x < limit)`，即区间
`[−2^95, 2^95)` 内合法，端点 `+2^95` **越界**。RTL 的有符号比较器必须复现这个
半开区间，不能写成 `|x| ≤ 2^95`。

### 4.3 附加断言

| 断言 | 违反时 |
|:---|:---|
| `gain > 0` | `gain ≤ 0` 是**结构性错误**，不是溢出；置错误码并保持输出不变 |
| `denom > 0` | 同上（`denom = gain·2^33`，由上一行蕴含） |
| `adc2_max_q > adc2_min_q` | 空量程，载入期拒绝 |

### 4.4 RTL 的行为约定

| 事件 | `acc_ovf` | `dout` | `dout_valid` |
|:---|:---|:---|:---|
| 正常 | 0 | 饱和后的 `word` | 1 |
| 任一 op 越界 | **1（粘滞）** | **保持上一拍合法值** | 1（时序不打断） |
| `gain ≤ 0` | 0 | 保持上一拍合法值 | 1 |
| 复位前 / 配置未载入 | 0 | 0 | **0** |

**粘滞**：`acc_ovf` 一旦置位，只有 `rst_n` 或显式清除寄存器能复位。
理由：Python 侧是 `raise`，即"这一次转换的结论不可信"；RTL 无法抛异常，
粘滞标志是它在硬件上唯一诚实的等价物——**不允许它自己消失**。

### 4.5 输出饱和（与溢出分开）

```
clip_low  = (word < 0)
clip_high = (word ≥ count)          # count = 2^20
dout      = min(max(word, 0), count − 1)
```

`word` 由 §3.5 的整数除法直接给出，**不经过 20 位截断**——先饱和，后截断。

### 4.6 模拟溢出标志必须独立保留

`analog_ovf = rdac_ovf | adc2_ovf | ra_sat`，与 `clip_low/high` **三者互不替代**
（既有判据：`test_output_saturation_is_separate_from_analog_saturation`）。
数字剪裁只能说明"码流到边了"，不能说明"模拟前端已经饱和"。

---

## 5. 载入期算术（离线，不进 RTL）

以下运算**在 RTL 之外**完成，RTL 只负责把它们的结果装进寄存器。
列出它们是为了让"换一颗芯片时怎么重算"有唯一答案，以及让复现者知道
**RTL 里不会出现这些运算**。

| 项 | 规则 | 位置 |
|:---|:---|:---|
| 权重量化 | `W = rint(w · 2^30)`，**半值向偶** | `FixedPointReconstructor.from_weights` |
| 电压系数量化 | `q = int(rint(v · 2^32))`，半值向偶 | 同上 |
| 权重合法性 | `0 < W < 2^47`，且 `ΣW < 2^60` | `__post_init__` |
| 电压寄存器合法性 | `−2^63 ≤ q < 2^63` | `__post_init__` |
| 权重辨识（SVD） | 离线 PC/ARM，结果冻结 | `weight_calibration.fit_unit_weights` |

> `np.rint` 的语义就是 IEEE754 默认的 round-half-to-even。若将来换用别的工具量化，
> **必须显式复现这一条**，否则权重会整体偏半个 LSB，且偏的方向随数值而变——
> 这类误差在校准残差里看不出来。

---

## 6. 不变量（RTL 侧必须写成断言）

| ID | 不变量 | 来源 |
|:---|:---|:---|
| A1 | 同一逻辑粗码在不同 DEM 状态下，**选中单位数恒定** | `mapper.nominal_conservation_check` |
| A2 | `DAC_nominal(M(c,state)) = V_target(c)` 对任意 DEM 状态成立 | `mapper` 头注释 |
| A3 | DEM 状态按 **bank 内序号**推进，每 bank 完整遍历 512 态 | `dem_state_sequence` |
| A4 | `s ∈ {−1,0,+1}` 且 `(b_minus_dac − I)/v_fs` 必须为整数；**小数掩码一律拒绝** | `reconstruct` 的 `np.allclose` 检查 |
| A5 | `gain > 0` | `reconstruct` |
| A6 | 6 个受检操作数落在 `[−2^95, 2^95)` | §4.2 |
| A7 | 溢出**不 wrap**；粘滞标志只由复位/显式清除解除 | §4.4 |
| A8 | 权重 ROM 索引顺序 = `_terms` 的 `(slice, unit)` 顺序 | §3.2 |

A4 的硬件含义值得单独说明：**RTL 不实现分数掩码**。ADR 0014 已明确
"fractional mask interpolation is rejected"；RTL 侧应当在译码阶段就保证
`b_minus_dac` 与 `I` 的差是 `v_fs` 的整数倍，任何小数路径**不进入本契约**。

---

## 7. 本契约**不**覆盖的内容（防止误用）

| 项 | 状态 | 理由 |
|:---|:---|:---|
| 解码电压 `v = Vfs·(2(word+0.5)/2²⁰ − 1)` | **仅离线评分** | 需要 `v_fs`；会引入浮点；不属数字核输出 |
| KTC 观测支路校正 | **排除** | ADR 0014 拒绝其未量化观察支路进整数接口；`to_codes()` 在 `ktc_enable` 时直接报错 |
| 分数 RDAC 码 | **拒绝** | ADR 0014 |
| 模拟域：采样保持、SADC 阈值、CDAC 电荷、RA、参考、互连 | **不在契约内** | 连续时间 / 浮点，由 Spectre 承接 |
| 噪声与失配抽样 | **不在契约内** | 芯片真值，仿真期生成 |
| 相位划分（`PHASES`） | **RTL 设计选择，`[假设]`** | 由浮点时长量化而来，误差记入精度预算；见计划 §4.2 / R6 |
| 面积、时序、功耗 | **不在契约内** | P5 |

---

## 8. 验证义务

本契约的每一条都要有对应的、**能推翻实现**的检查。分工如下：

| 层 | 检查 | 归属 |
|:---|:---|:---|
| P0 | 契约与 `FixedPointReconstructor` 的算式一致（退化恒等式、round-even 8 样例） | `tests/unit/test_rtl_export.py` |
| P0 | 寄存器镜像 → `from_dict` → 重构结果逐位相同 | 同上 |
| P0 | 导出物可复现（重跑逐字节一致） | 同上 |
| P2 | RTL `recon_core` 与 `FixedPointReconstructor` **逐样本 bit-exact** | `sim/tb/` |
| P2 | 2²⁰ 全码理想后端 oracle：每码恰一次、单调、恰 1 LSB 解码宽度 | 同上 |
| P2 | 511 个 9 bit 进位处 1/16-LSB 密集斜坡：无回退、无跳码 | 同上 |
| P2 | 溢出路径：越界输入 → 粘滞标志置位且**不回绕** | 同上 |
| P2 | **反例**：把 §3.5 换成向 0 截断 → 测试必须变红 | 同上 |
| P2 | **反例**：把 §4.2 的边界改成闭区间 → 测试必须变红 | 同上 |

> 最后两条是这份契约是否"活着"的判据。一条在故意做错时仍然通过的检查，是装饰品。

---

## 9. 变更流程

1. 本契约**冻结后不就地修改**。
2. 需要改动时：新写一份 ADR（编号续 ADR 0015 之后），标注取代本契约的哪一条，并说明代价。
3. 若改动的起因是 ADR 0014 本身被取代，则以新 ADR 为准，本契约整体重写。
4. 任何改动必须同时更新 `tools/export_rtl_params.py` 的导出物与 §8 的对应检查。

---

## 附：一页速查（贴墙用）

```
wscale = 2^30        vscale = 2^32        count = 2^20        limit = 2^95
W : Q30 正有符号 48 bit      (0 < W < 2^47,  ΣW < 2^60)
F,O,I : Q32 有符号 64 bit

n    = (2·adc2_code + 1)·(adc2_max_q − adc2_min_q)
F    = adc2_min_q + round_half_even_shift(n, adc2_n_bits+1)
rails= Σ(W·s)        gain = Σ(W·a) > 0        total = Σ(W)
num  = (F−O)·2^30 − rails·2^32 − total·I
A    = (num + gain·2^32)·2^20
word = floor(A / (gain·2^33))
     = floor( (A >>> 33) / gain )            # 推荐分解，需验证
clip_low  = word < 0
clip_high = word ≥ 2^20
dout      = clamp(word, 0, 2^20 − 1)
6 个受检操作数: (F−O)·2^30, rails·2^32, total·I, num, A, gain·2^33
                 越出 [−2^95, 2^95)  →  acc_ovf 粘滞，输出保持，不 wrap
```
