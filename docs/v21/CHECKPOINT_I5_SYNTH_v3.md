# I5 综合检查点 v3：定位到 `code_o_reg.D` 的单一长锥

时间：2026-11-16　工艺：TSMC 28HPC+　工具：DC W-2024.09-SP3
基线：`2fb14a4`

## 1. 本轮定位过程（含两次自我否定）

### 1.1 第一次判断错了：以为除法器被优化掉

上一轮 `divs=0` 让我以为 DC 把除法器优化掉了。**实际是我的
`get_cells -filter {ref_name =~ DW_div*}` 语法无效**，返回空集合。
改用 `foreach_in_collection` + `string match` 后看到：

```
DIV _sel1254  (recon_v21_DW_div_uns_0)
DIV _sel1254  (recon_v21_DW_div_uns_1)
```

网表里 `u_div` 有 20957 处引用，除法器确实在。

### 1.2 第二次判断也错了：以为 `code_o_reg` 扇入为空

`report_timing` 一直报 `code_o_reg[x] -> code_o_reg[x]` 自环，
`data arrival` 只有 0.15~0.52 ns，让我以为 D 端没接逻辑。

查网表实际连接：

```
DFCNQD1BWP7T40P140 \code_o_reg[0]  ( .D(n140), .CP(clk), ... )
NR2D0BWP7T40P140 U136 ( .A1(n131), .A2(n133), .ZN(n140) );
```

D 端接的是真实组合逻辑。**自环是"没有 IO 约束时 DC 只报寄存器内部路径"
的查询假象**，不是设计缺陷。

### 1.3 真正的发现：加 IO 约束后块级也超时

关键对照实验：

| 设置 | 块级耗时 | 结束状态 |
|:---|:---|:---|
| 无 IO 约束（只有 create_clock + driving_cell + load） | **约 3 分钟** | 完成，22522 µm²，报自环 |
| 加 `set_input_delay` / `set_output_delay` | **10 分钟未收敛** | 卡在 `code_o_reg[8]/D` |

**块级和顶层卡在同一个端点族**（`code_o_reg[*]/D`）。

所以"块级 3 分钟能过"是**假象**：没有 IO 约束，DC 不需要优化数据通路，
只做映射就出网表；一旦有真实约束，同一条链立刻暴露。

## 2. 根因：`code_o_reg.D` 是一个包了两次 64 位除法的单周期锥

`code_o_reg` 的 D 端依赖链（全部在一个时钟周期内）：

```
code_c       <- unclipped = Q_wide + 524288
Q_wide       <- sign_extend(rnd96_const(x32))     32 位乘移位
x32          <- $signed(X_wide[31:0])             截断
X_wide       <- (N_wide * I_q60) >>> 36           41x27 位乘
I_q60        <- rnd64(2^60, G_q30)                **64 位除法 #1**
N_wide       <- Z*64 + 6Wm - 3G - 6Wp             41 位乘加
Z_q24        <- rnd64((Y-b)*2^30, alpha)          **64 位除法 #2**
```

**两个 64 位除法器、一个 41 位乘加、一个 32 位乘移位，串在同一个
25 ns 周期里。** DC 要在这里做时序驱动优化，搜索空间极大，
这就是它不收敛的原因。

这不是约束写错、不是库没读到、不是跨层 ungroup ——
是 recon 被设计成单周期组合完成整条链。

## 3. 本轮已排除的假设

| 假设 | 排除方式 |
|:---|:---|
| 跨层 ungroup 导致输入锥变大 | `set_ungroup false` 后 19 min 仍卡同一端点 |
| `link_library` 写法错误导致库没读到 | 修好后（`[list "*" $DB]`）结果完全不变 |
| 除法器被常量传播优化掉 | 网表里 `u_div` 20957 处引用 |
| `code_o_reg` 扇入为空 | 网表里 D 端接真实逻辑 |
| 块级综合本身没问题 | 加 IO 约束后块级同样卡住 |

## 4. 结论与下一步

**当前状态**：
- RTL 功能正确：`tb_i4_recon` 8213 项 PASS
- 5 项回归全绿
- [A] 数学证明 + [B] 生产 RTL 检查 已完成
- **综合不可复现**：不论块级还是顶层，只要给真实约束就不收敛

**下一步只有一个方向**：把 `code_o_reg.D` 这条锥切开。
但上一轮尝试"只把除法单独打一拍"失败了（8210 FAIL），
**当时没有留下首笔失败的具体信号归属证据**，所以不能照原样重试。

正确的做法是：
1. 先在 RTL 里把这条链拆成**明确的中间寄存器级**，
   每一级只做一件事（Z 级、N 级、I 级、Q 级）
2. 每加一级，立刻用 `tb_i4_recon` 验证，并记录该级的
   `N`/`I`/`valid`/`tag`/`epoch`/错误标志是否同笔
3. 一次只动一个边界，不同时改多处
4. 级数由时序决定，不预设

**在此之前不声称任何 PPA 结论**：
- `22522 µm²` 是无约束映射的面积，不代表可实现面积
- `+24.83/+24.44 ns` 是自环 slack，无意义
- `37.5%`（相对原型图估 60000 µm²）**不应引用**

## 5. 恢复命令

```bash
cd /Users/zhaoyi/src/20bit_SAR_ADC_Behaviour_Verification
git log --oneline -1     # 应为 2fb14a4 或其后
D3D4_RUNNER=vm-yian bash tools/verify_v21.sh
python3 tools/prove_xwidth.py
```
