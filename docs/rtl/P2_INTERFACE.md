# P2/P3 模块接口与向量格式（冻结）

- 状态：**已冻结**，2026-09-18
- 上游：`docs/rtl/RTL_ARITHMETIC_CONTRACT.md`（算术，冻结）、`docs/rtl/P1_INTERFACE.md`（P1 已交付的 M1–M5）
- 适用：`rtl/core/` 下 M6–M13、M16、M17 与 `rtl/top/` 下的 M8、顶层，以及 `sim/tb/p2_tb.sv`
- 本文与 P1 文档**同一套全局约定**（§0.1），仅补充新增项

---

## 0. 全局约定（对 P1 §0 的增补）

| 项 | 约定 |
|:---|:---|
| 时钟/复位 | 同 P1：`clk` + **同步**低有效 `rst_n` |
| 握手 | 无。**固定延迟**，用 `*_valid` / `done` / `busy` 指示；无 ready/valid 反压 |
| 参数化 | **允许**模块参数，但默认值必须**取自 `rtl_params.vh`**（`parameter int P_X = HEADER_CONST`）。这样既只有一份真相源，又能让 L2 oracle 用不同尺寸重例化。**禁止**写与头文件常量同名的参数（会自我遮蔽） |
| 位宽纪律 | 内部中间量一律显式声明宽度；**不得依赖隐式扩位**。任何 `'(...)` 尺寸转换都要写明理由（P1 的 RTL-2/RTL-3 就是栽在这里） |
| 有符号 | 只要表达式里出现一个**无符号**操作数，整条按无符号算。混合运算处一律显式 `$signed()`，或全部走 `signed` 局部量（P1 的 RTL-3 教训） |
| 数组端口 | 一律用**打包**二维数组 `logic [A-1:0][B-1:0]` |
| 不可综合构造 | 禁 `#delay`、`initial` 产生逻辑、`real`、动态数组、`fork/join`、`break`/`continue`（VCS 可编译但综合不认） |

### 0.1 `global clock enable` 的问题

本设计**不使用**全局 `gclk_en` 门控。所有模块在 `clk` 上以 `en`/`load`/`start` 脉冲控制推进。
理由：门控时钟在 28nm 需要额外的 CTS 约束与 CDC 检查，而本设计吞吐足够让逻辑空转。
P5 若确认功耗吃紧，再作为独立优化项引入。

---

## 1. 模块清单与阶段归属

| ID | 模块 | 文件 | 阶段 | 一句话职责 |
|:---|:---|:---|:---:|:---|
| — | `div_floor` | `rtl/core/div_floor.sv` | P2 | 有符号**floor** 除法（唯一除法器），恢复余数法 |
| M9 | `adc2_dec` | `rtl/core/adc2_dec.sv` | P2 | 原始后端码 → Q32 bin center（唯一舍入点，ties-to-even） |
| M11 | `recon_core` | `rtl/core/recon_core.sv` | P2 | **定点重构核**：权重×掩码求和 → 受检算术 → floor 除 → 20 bit 饱和输出 |
| M7 | `slice_alloc` | `rtl/core/slice_alloc.sv` | P2 | 18-of-8 分配，A/B ping-pong，`conv[n] = acq[n-1]` |
| M6 | `rdac_drv` | `rtl/core/rdac_drv.sv` | P2 | 8 个活动 slice 的 unary 开关码扇出到 18 个物理 slice |
| M16 | `ctrl_fsm` | `rtl/core/ctrl_fsm.sv` | P2 | 相位节拍生成 + 流水控制脉冲 |
| M17 | `status_regs` | `rtl/core/status_regs.sv` | P2 | 粘滞溢出/剪裁/错误码 |
| M12 | `weight_store` | `rtl/core/weight_store.sv` | P3 | 18 × 71 Q30 权重存储（含写入窗口） |
| M13 | `calib_regs` | `rtl/core/calib_regs.sv` | P3 | 系数寄存器 + 合法性校验 + `cfg_ready` |
| M8 | `sadc_enc` | `rtl/top/sadc_enc.sv` | P2 | 比较器阵列温度计 → 二进制（**在核外**，见 `rtl/README.md` §3.1） |
| — | `sar20_digital_core` | `rtl/top/sar20_digital_core.sv` | P2 | 顶层集成 |

拓扑（顶层内部，全部同一时钟域）：

```
                     ┌──────────────┐
        ┌───────────▶│ slice_alloc  │── acq_slices / conv_slices / bank
        │            └──────────────┘
        │                    │ conv_slices
        │                    ▼
        │            ┌──────────────┐   ┌───────────────┐
        │            │ weight_store │──▶│  w_q[18][71]  │
        │            └──────────────┘   └───────┬───────┘
        │                    ▲                  │
        │            ┌──────┴───────┐          │
        │            │ calib_regs   │── offset_q / min_q / max_q / dem_en / bridge_en
        │            └──────────────┘          │
        │                                      │
   ┌────┴─────┐   sid    ┌──────────────┐      │
   │dem_state │─────────▶│ dem_addr_gen │      │
   │  _gen    │          └──────┬───────┘      │
   └──────────┘                 │ main_logical │
                       ┌────────▼───────┐      │
        dither_gen ───▶│  swap_decode   │      │
                       └────────┬───────┘      │
                                │ counts       │
                       ┌────────▼───────┐      │
                       │  unit_therm    │      │
                       └───┬────────┬───┘      │
                     masks │        │ rail     │
                           ▼        ▼          ▼
                    ┌──────────────────────────────────┐
                    │           recon_core             │◀── adc2_code / inj_q
                    └───────────────┬──────────────────┘
                                    │ dout / flags
                           ┌────────▼────────┐
                           │  status_regs    │
                           └─────────────────┘
```

> `ctrl_fsm` 为图中所有时序模块提供相位脉冲，为避免线团未画出。

---

## 2. `div_floor` —— 唯一的运行时除法器

**职责**：`q = floor(a / d)`，`a` 有符号、`d` 无符号且 `> 0`。**这是整个数字核里唯一的除法**（契约 §3.5）。

**为什么必须是 floor**：RTL 的 `/` 向 0 截断，Python 的 `//` 向 −∞ 取整；负商差 1 个 LSB，且只在半个码流上出错。见契约 §0。

```systemverilog
module div_floor #(
    parameter int P_W_A    = ACC_BITS - V_FRAC - 1,  // 被除数位宽（含符号）= 63
    parameter int P_W_D    = 60,                     // 除数位宽（无符号）
    parameter int P_STAGES = 7                       // 每拍级联的恢复除法级数
)(
    input  logic                    clk,
    input  logic                    rst_n,
    input  logic                    start,   // 一拍脉冲
    input  logic signed [P_W_A-1:0] a,
    input  logic        [P_W_D-1:0] d,
    output logic signed [P_W_A-1:0] q,
    output logic                    err,     // d == 0：结构性错误（不是溢出）
    output logic                    busy,
    output logic                    done     // 一拍脉冲，q 有效
);
```

**算法（恢复余数法，逐位）**：

```
W_PAD = ceil(P_W_A / P_STAGES) * P_STAGES        // 高位补零对齐到整拍
mag   = a[P_W_A-1] ? (~a + 1) : a                // |a|，无符号 P_W_A 位
R     = 0                                        // 余数，W_R = max(P_W_A,P_W_D)+1 位
Q     = 0                                        // 商幅值，W_PAD 位
for i = 0 .. W_PAD-1:                            // 从高位到低位
    R = (R << 1) | mag_bit(i)                    // mag 在其位宽之上补 0
    if (R >= d) { R = R - d; Q_bit(i) = 1 } else { Q_bit(i) = 0 }
q_abs = Q[P_W_A-1:0]
q     = a[P_W_A-1] ? -(q_abs + (R != 0)) : q_abs
```

**正确性论证**（写进模块头，供复核者复算）：

1. 恢复余数法对无符号 `mag / d` 给出**精确**商与余数（`d > 0`）。
2. `W_PAD ≥ P_W_A` 且高位补零只产生前导 0 商位，商值不变。
3. 负无符号修正：`a < 0` 时 `floor(a/d) = -ceil(|a|/d) = -(q_abs + (R≠0))`。
4. `d = 0` 时判定为 `err`（对应契约 §4.3 的"结构性错误"，**不是**溢出）。
5. **不可能溢出**：`|a| < 2^(P_W_A-1)`、`d ≥ 1` ⟹ `|q| ≤ |a| < 2^(P_W_A-1)`，`P_W_A` 位有符号装得下。

**时序**：
- `N = W_PAD / P_STAGES` 拍级联（每拍 `P_STAGES` 级）。
- `start` 在第 `t` 拍为高 → 第 `t+1` 拍起 `busy` 为高 → 第 `t+N` 拍末级完成 → 第 `t+N+1` 拍 `done` 为高，`q` 当拍有效。
- **固定延迟 = N + 1 拍**。`P_STAGES` 是面积/时序旋钮，默认 7（`P_W_A=63` → `N=9` → 延迟 10 拍）。
- `busy` 期间忽略 `start`（不做反压；调用方由 `ctrl_fsm` 保证不重叠）。

> ⚠️ **已知的吞吐限制（如实登记，不要在文档里含糊）**：本实现是**迭代**除法器，`P_W_A=63`、`P_STAGES=7` 时单次占 9 拍。
> 在 `PHASES=16` 的相位预算下够用（1 样本 / 16 拍）。若 P5 综合显示时序吃紧而必须减小 `P_STAGES`，
> **吞吐会立刻不达标**。替代方案（阵列除法器 / 倒数 ROM + 修正）登记为 P6 优化项，
> 采用前必须补"商误差恰为 0"的证明与独立验证。

---

## 3. M9 `adc2_dec` —— 后端码 → Q32 bin center

**职责**：契约 §3.1 的**唯一运行时舍入**。纯组合。

```systemverilog
module adc2_dec #(
    parameter int P_ADC2_BITS = ADC2_BITS
)(
    input  logic [P_ADC2_BITS-1:0]  adc2_code,
    input  logic signed [V_BITS-1:0] adc2_min_q,
    input  logic signed [V_BITS-1:0] adc2_max_q,
    output logic signed [V_BITS-1:0] fine_q,
    output logic                     ovf      // fine_q 装不进 V_BITS 有符号寄存器
);
```

**算式**（逐字对应契约 §3.1）：

```
delta = adc2_max_q - adc2_min_q                      // 有符号
n     = (2*adc2_code + 1) * delta                    // 有符号，宽 W_MUL = P_ADC2_BITS+1+V_BITS
sh    = round_half_even_shift(n, P_ADC2_BITS + 1)
fine_q = adc2_min_q + sh                             // 溢出则 ovf = 1，fine_q 输出钳位值
```

`round_half_even_shift(x, k)` 的硬件实现（契约 §3.1 注记的落地）：

```
q     = $signed(x) >>> k            // 算术右移 = floor(x / 2^k)
r     = x[k-1:0]                    // 二补数低 k 位 == x mod 2^k == 余数（对负数同样成立）
half  = 1 << (k-1)
res   = q + ((r > half) || ((r == half) && q[0]))
```

**必须复现的既有判据**：`tests/unit/test_fixed_point.py::test_signed_ties_to_even` 用除数 2 固定了
`−7→−4, −5→−2, −3→−2, −1→0, 1→0, 3→2, 5→2, 7→4`。RTL 的 `round_half_even_shift` 必须复现这 8 个值
（TB 用 `k=1` 直接驱动这 8 个输入）。

**边界**：`n` 的宽度取 `W_MUL = P_ADC2_BITS + 1 + V_BITS`（不用更窄，避免静默截断）；
`adc2_min_q + sh` 用 `W_MUL` 宽的有符号加法，再判是否落在 `[-2^63, 2^63)`，否则 `ovf = 1`。

---

## 4. M11 `recon_core` —— 定点重构核

**职责**：契约 §3.2–§3.5 + §4 的全部落地。**这是全数字核里唯一算术上完整的部分。**

```systemverilog
module recon_core #(
    parameter int P_N_ACTIVE  = N_ACTIVE,
    parameter int P_N_MAIN    = N_UNIT_MAIN,
    parameter int P_N_SUB     = N_UNIT_SUB,
    parameter int P_N_SLICES  = N_SLICES,
    parameter int P_ADC2_BITS = ADC2_BITS,
    parameter int P_STAGES    = 7,           // 传给 div_floor
    parameter int P_DIT_N     = 2*DITHER_UNITS_RANGE,             // 采样掩码单位数 = 4
    parameter int P_DIT_END   = DITHER_SPLIT_IS_SUB ? N_UNIT_TOTAL : N_UNIT_MAIN
)(
    input  logic                                  clk,
    input  logic                                  rst_n,
    input  logic                                  cfg_ready,
    input  logic                                  start,        // 一拍脉冲：本拍输入有效
    input  logic                                  clr_ovf,      // 显式清除粘滞标志
    input  logic                                  sampling_mask_en,  // dither_mode == "sampling"
    // ---- 本次转换的开关状态 ----
    input  logic [P_N_ACTIVE-1:0][4:0]            slice_id,     // 物理 slice 号
    input  logic [P_N_ACTIVE-1:0][P_N_MAIN-1:0]   main_on,      // unary 掩码（来自 M5）
    input  logic [P_N_ACTIVE-1:0][P_N_SUB-1:0]    sub_on,
    input  logic [P_DIT_N-1:0]                    dither_rail,  // 采样掩码单位轨极性（来自 M5）
    // ---- 后端码与已知注入 ----
    input  logic [P_ADC2_BITS-1:0]                adc2_code,
    input  logic signed [V_BITS-1:0]              inj_q,        // I，Q32
    // ---- 权重与系数 ----
    input  logic [P_N_SLICES-1:0][P_N_MAIN+P_N_SUB-1:0][W_BITS-1:0] w_rom,
    input  logic signed [V_BITS-1:0]              offset_q,
    input  logic signed [V_BITS-1:0]              adc2_min_q,
    input  logic signed [V_BITS-1:0]              adc2_max_q,
    // ---- 输出 ----
    output logic [OUT_BITS-1:0]                   dout,
    output logic                                  dout_valid,   // 固定延迟脉冲
    output logic                                  clip_low,     // 与 dout 同拍
    output logic                                  clip_high,
    output logic                                  acc_ovf,      // **粘滞**
    output logic                                  gain_err,     // gain <= 0（结构性）
    output logic                                  adc2_ovf,     // adc2_dec 的溢出
    output logic                                  busy
);
```

### 4.1 三个掩码和（契约 §3.2）

对 8 个活动 slice × 71 个单位求和，权重取自 `w_rom[slice_id[a]][u]`：

```
mask_unit(a,u) = sampling_mask_en && (P_DIT_END-P_DIT_N <= u < P_DIT_END)
a_bit(a,u)     = mask_unit(a,u) ? 0 : 1
s_rail(a,u)    = (1 - 2*on(a,u)) + (mask_unit(a,u) ? (2*dither_rail[u-(P_DIT_END-P_DIT_N)] - 1) : 0)

sum_W   = Σ W                        (total，与开关状态无关，只随 slice 选择变)
sum_Wa  = Σ W * a_bit                (= gain)
sum_Won = Σ W * on
sum_Wr  = Σ_{mask_unit} W * (2*rail - 1)

rails   = sum_W - 2*sum_Won + sum_Wr
gain    = sum_Wa
total   = sum_W
```

> **为什么不是直接算 `Σ W*s_rail`**：`s_rail ∈ {−2,−1,0,1,2}` 需要条件取负，等价但更贵；
> 上面的展开式只用"与门 + 加法树"，代价更低且与 `_terms` 的语义逐项可对。

**求和宽度**：`SUM_BITS = 64`。依据：寄存器合法性要求 `ΣW < 2^60`（契约 §5），
树内每层最多再带 1 位进位，64 位余量充足。**这个宽度依赖载入期不变量，已登记为依赖项。**

### 4.2 受检算术（契约 §3.3–§3.5、§4）

设 `wscale = 2^W_FRAC`、`vscale = 2^V_FRAC`、`count = 2^OUT_BITS`、`limit = 2^(ACC_BITS-1)`。

| 量 | 宽度 | 说明 |
|:---|---:|:---|
| `F` | `V_BITS` | `adc2_dec` 输出 |
| `op1 = (F − O) * wscale` | `W_WIDE = 128` | `|F−O| < 2^64`，×2^30 → 94 位 |
| `op2 = rails * vscale` | 128 | `|rails| < 2^61`，×2^32 → 93 位 |
| `op3 = total * inj_q` | 128 | `total < 2^64`、`|inj_q| < 2^63` → 127 位，128 位装得下 |
| `num = op1 − op2 − op3` | 128 | 精确 |
| `gv = gain * vscale` | 96 | `gain < 2^64`，×2^32 |
| `s1 = num + gv` | `W_S1 = 97` | 仅当 `num` 通过检查时才有意义 |
| `shifted = s1 * count` | `W_SHIFTED = 117` | `count` 是 2 的幂 → **纯移位，无乘法器** |
| `denom = 2*gain*vscale` | 96 | = `gain << 33` |
| `A1 = $signed(shifted[95:33])` | `P_W_A = 63` | 合法的 `shifted` 落在 96 位内，故取低 63 位即精确 |

**检查顺序与"降级计算"的口径**（必须与 Python 的结果一致）：

```
ovf_ops = |op1| ≥ limit || |op2| ≥ limit || |op3| ≥ limit || |num| ≥ limit
```

> 关键点：`shifted` 与 `denom` 的检查在 `ovf_ops` 之后。**若 `ovf_ops` 已成立，`shifted` 的
> 窄位宽计算可能不再精确 —— 但这不影响可观测行为**，因为两种情况下结果都是"标志置位、
> 输出保持上一拍合法值"。Python 侧是抛 `OverflowError`，RTL 侧是粘滞标志 + 保持。
> 二者**判定集合相同**：`{op1..op6 中任一越界} ∪ {gain ≤ 0}`。
>
> 反过来必须成立：**`ovf_ops` 不成立时，`shifted` 的 117 位计算不得回绕**。
> 论证：`ovf_ops` 不成立 ⟹ `|num| < 2^95` 且 `denom < 2^95` ⟹ `gain < 2^62` ⟹ `|gv| < 2^94`
> ⟹ `|s1| < 2^95 + 2^94 < 2^96` ✓（97 位装得下）⟹ `|shifted| < 2^116` ✓（117 位装得下）。

**最终字**：`word = div_floor(A1, gain)`（契约 §3.5.1 的嵌套地板恒等式，P0 已数值验证）。

```
clip_low  = (word < 0)
clip_high = (word >= count)
dout      = word < 0 ? 0 : (word >= count ? count-1 : word[OUT_BITS-1:0])
gain_err  = (gain == 0)                  // 结构性；不是溢出
```

### 4.3 时序与延迟

| 项 | 约定 |
|:---|:---|
| 输入采样 | `start` 为高那一拍的 `clk` 上升沿锁存求和结果与除数 |
| 延迟 | **`RECON_LAT = N_CYC + 2 = 11`**（`P_STAGES=7`、`N_CYC=9`）。**口径**：`start` 为高的那一拍记作第 1 拍，`dout_valid` 出现在第 11 拍。初稿写 `N+3=12`，是另一种口径（多算了 `start` 之后的那一拍）；**以 T4 的实测为准**，二者不一致时改文档 |
| 吞吐 | 受 `busy` 限制：`start` 之间至少间隔 `N` 拍（`ctrl_fsm` 用 16 拍保证） |
| `dout_valid` | 宽度 1 拍的脉冲；`dout`/`clip_*` 与它**同拍**有效且保持到下一次 |
| 复位/未配置 | `cfg_ready = 0` 时 `dout = 0`、`dout_valid = 0`；`acc_ovf` 由复位清零 |

> `RECON_LAT` 必须由 TB **实测**（数 `start` 到 `dout_valid` 的拍数）并断言等于模块内 `localparam`。
> 只写文档不实测，等于没验证。

---

## 5. M7 `slice_alloc` —— 18-of-8 分配

```systemverilog
module slice_alloc (
    input  logic                       clk,
    input  logic                       rst_n,
    input  logic                       cfg_ready,
    input  logic                       sample_en,     // 每样本第一拍的脉冲
    output logic [N_ACTIVE-1:0][4:0]   acq_slices,
    output logic [N_ACTIVE-1:0][4:0]   conv_slices,
    output logic                       conv_valid,
    output logic                       bank,          // 0/1 交替，驱动 DEM 的 en_a/en_b
    output logic [31:0]                sample_idx,
    output logic [N_ACTIVE-1:0][4:0]   group          // = conv_slices（便于清点）
);
```

**来源**：`adi_model/timing.build_slice_plan` 的 `strategy="pingpong"`、`spare_period=None` 分支：

```
groups = arange(2*N_ACTIVE).reshape(2, N_ACTIVE)      // [0..7] 与 [8..15]
parity = n % 2
acq[n]  = groups[parity]
conv[n] = groups[1 - parity]
valid   = n >= 1
```

**不变量（写成 SVA/TB 断言）**：

| ID | 不变量 |
|:---|:---|
| INV-4a | `conv[n]` 逐元素等于 `acq[n-1]`（**所有权传递**，18-of-8 的核心） |
| INV-4b | `acq_slices` 内部**无重复**；`conv_slices` 内部无重复 |
| INV-4c | `conv_slices` 与 `acq_slices` 在同一 `n` 上**不相交** |
| INV-4d | `acq`/`conv` 中的元素恒 < 16（`n_slices=18` 但 pingpong 只用 0..15，如实登记） |

> `bank` 是否等于 `n % 2` 必须与模型核对：`tools/export_rtl_vectors.py` 的 P2 报告里要给出
> `all(result.bank == arange(n) % 2)` 的实测布尔值。**若不等，以模型为准并改本文件。**

---

## 6. M6 `rdac_drv` —— 开关码扇出

```systemverilog
module rdac_drv (
    input  logic                                  clk,
    input  logic                                  rst_n,
    input  logic                                  load,        // 一拍脉冲
    input  logic [N_ACTIVE-1:0][4:0]              slice_id,
    input  logic [N_ACTIVE-1:0][N_UNIT_MAIN-1:0]  main_on,
    input  logic [N_ACTIVE-1:0][N_UNIT_SUB-1:0]   sub_on,
    input  logic [2*DITHER_UNITS_RANGE-1:0]       dither_rail,
    output logic [N_SLICES-1:0]                   slice_sel,
    output logic [N_SLICES-1:0][N_UNIT_MAIN-1:0]  main_sw,
    output logic [N_SLICES-1:0][N_UNIT_SUB-1:0]   sub_sw,
    output logic [N_SLICES-1:0][2*DITHER_UNITS_RANGE-1:0] dither_sw
);
```

**语义**：`load` 那一拍把 8 个活动 slice 的掩码写到 `main_sw[slice_id[a]]` 等对应位置，
其余物理 slice 的开关**清零**。`load` 之外保持（输出为寄存器）。

**边界声明（写进模块头，别让人误解）**：本模块**只是数字侧的寄存器扇出**，
**不是**底板开关驱动电路。真实驱动的时序、电荷注入、非交叠时钟属模拟域，由 Spectre 承接。

**不变量**：`slice_id` 必须两两不同（INV-4b）；若出现重复，`rdac_drv` 的行为是"后者覆盖前者"，
**不报错** —— 这是刻意的：把错误报在 `slice_alloc` 的断言里，而不是在这里做隐式防护。

---

## 7. M16 `ctrl_fsm` —— 相位与流水控制

```systemverilog
module ctrl_fsm #(
    parameter int P_PHASES = PHASES          // = 16
)(
    input  logic                    clk,
    input  logic                    rst_n,
    input  logic                    cfg_ready,
    input  logic                    run,             // 高 = 连续转换
    output logic [P_PHASES-1:0]     phase_onehot,    // 相位节拍（one-hot）
    output logic [$clog2(P_PHASES)-1:0] phase,
    output logic                    sample_en,       // 相位 0 的脉冲 = 新样本
    output logic                    acq_phase,       // 相位 0..6 为高：采集/跟踪
    output logic                    sadc_latch,      // 相位 8
    output logic                    dem_advance,     // 相位 10
    output logic                    rdac_load,       // 相位 11
    output logic                    adc2_latch,      // 相位 14
    output logic                    recon_start,     // 相位 15
    output logic [31:0]             sample_idx
);
```

**相位分配**（计划 §4.2，`PHASES=16`）：

| 相位 | 动作 |
|:---:|:---|
| 0 | 新样本；`sample_en`、`slice_alloc` 推进 |
| 0–6 | 采集/跟踪（`acq_phase` 为高） |
| 7 | 采样沿 t1 |
| 8 | SADC 判决编码（`sadc_latch`） |
| 9 | 保留 |
| 10 | DEM 状态推进 + k 合并（`dem_advance`） |
| 11 | RDAC 置位（`rdac_load`） |
| 12–14 | RA 建立 + ADC2 采样量化；相位 14 `adc2_latch` |
| 15 | 后端译码 + 定点重构（`recon_start`） |

**不变量**：`phase` 在 `cfg_ready=0` 时恒 0 且所有脉冲为 0；`sample_idx` 只在相位 0 递增。

---

## 8. M17 `status_regs` —— 粘滞状态与错误码

```systemverilog
module status_regs (
    input  logic        clk,
    input  logic        rst_n,
    input  logic        clr,             // 显式清除（清粘滞标志，不影响错误码）
    // 事件输入（各与 dout 同拍或独立）
    input  logic        ev_acc_ovf,
    input  logic        ev_gain_err,
    input  logic        ev_adc2_ovf,
    input  logic        ev_clip_low,
    input  logic        ev_clip_high,
    input  logic        ev_analog_ovf,   // rdac_ovf | adc2_ovf | ra_sat
    input  logic [31:0] err_code,        // 来自 calib_regs / weight_store
    output logic        acc_ovf_sticky,  // 粘滞，只由 clr / rst_n 解除
    output logic        gain_err_sticky,
    output logic        adc2_ovf_sticky,
    output logic        clip_low_last,
    output logic        clip_high_last,
    output logic        analog_ovf_sticky,
    output logic [31:0] status_word,     // {err_code, 6 个标志}
    output logic [31:0] status_clr_value
);
```

**口径（契约 §4.4）**：

| 标志 | 粘滞？ | 解除方式 |
|:---|:---:|:---|
| `acc_ovf` | **是** | 仅 `clr` 或 `rst_n`。理由：Python 侧是 `raise`，"这次转换的结论不可信"；RTL 无法抛异常，粘滞是唯一诚实的等价物 |
| `gain_err` | 是 | 同上（结构性错误） |
| `adc2_ovf` | 是 | 同上 |
| `analog_ovf` | 是 | 同上（ADR 0014 要求它独立保留） |
| `clip_low` / `clip_high` | **否** | 逐样本更新（它们是**本次码**的属性，不是故障） |

> `clip_*` 不粘滞是刻意的：把它做成粘滞会让"码到边"看起来像"芯片坏了"。

---

## 9. M12 `weight_store` / M13 `calib_regs` —— 寄存器与载入

```systemverilog
module weight_store #(
    parameter int P_N_SLICES = N_SLICES,
    parameter int P_N_UNITS  = N_UNIT_TOTAL
)(
    input  logic             clk,
    input  logic             rst_n,
    input  logic             cfg_ready,     // 1 = 已生效 → **禁止写**
    input  logic             wr_en,         // 一拍脉冲
    input  logic [4:0]       wr_slice,
    input  logic [6:0]       wr_unit,
    input  logic [W_BITS-1:0] wr_data,
    output logic             err_write,     // 1 = 本次写被拒
    output logic [P_N_SLICES-1:0][P_N_UNITS-1:0][W_BITS-1:0] w_q
);

module calib_regs #(
    parameter int P_ADC2_BITS = ADC2_BITS
)(
    input  logic                      clk,
    input  logic                      rst_n,
    input  logic                      wr_en,        // 一拍脉冲
    input  logic [3:0]                sel,          // 0 offset_q / 1 adc2_min_q / 2 adc2_max_q
                                                   // 3 dem_en / 4 bridge_en / 5 sampling_mask_en
    input  logic signed [V_BITS-1:0]  data_v,       // sel <= 2 用
    input  logic                      data_b,       // sel >= 3 用
    input  logic                      validate,     // 一拍脉冲：跑合法性校验
    input  logic                      clear_valid,  // 一拍脉冲：撤销 cfg_ready
    output logic                      cfg_ready,
    output logic [31:0]               err_code,
    output logic signed [V_BITS-1:0]  offset_q,
    output logic signed [V_BITS-1:0]  adc2_min_q,
    output logic signed [V_BITS-1:0]  adc2_max_q,
    output logic                      dem_en,
    output logic                      bridge_en,
    output logic                      sampling_mask_en
);
```

**读写窗口**（`rtl/README.md` §3.2 承诺的契约，这里落地）：

| 条件 | 行为 |
|:---|:---|
| `cfg_ready = 0` 且 `wr_en` | 写入生效 |
| `cfg_ready = 1` 且 `wr_en` | **写入被忽略**，`err_write = 1`，`err_code = ERR_CFG_WRITE` |
| `validate = 1` | 跑校验；通过则 `cfg_ready <= 1`；失败则 `cfg_ready` 保持 0 且 `err_code` 置位 |
| `clear_valid = 1` | `cfg_ready <= 0`（可重载） |

**`validate` 的校验项**（对应契约 §5 与 `FixedPointReconstructor.__post_init__`）：

| 检查 | 由谁执行 | 失败码 |
|:---|:---|:---|
| 每个权重 `W` 满足 `0 < W < 2^47` | **`weight_store` 写入点** | `ERR_W_RANGE` |
| `ΣW < 2^60` | **`weight_store` 写入点** | `ERR_W_SUM` |
| `adc2_min_q < adc2_max_q` | `calib_regs.validate` | `ERR_RANGE_EMPTY` |
| `offset_q`/`min_q`/`max_q` 在 `[-2^63, 2^63)` | `calib_regs.validate` | `ERR_V_RANGE`（**永不触发**，见下） |

> **契约修正（2026-09-18，实现时发现的自相矛盾）**：本节的表原先要求 `calib_regs` 校验权重，
> 但 §9 的**冻结端口表里 `calib_regs` 没有权重输入** —— 表与端口表互相矛盾。
> 落地选择是**不动端口**，把权重合法性放到 `weight_store` 的写入点（写入点失败更快、职责更清楚）。
> 连带两条必须如实登记的后果：
> 1. `calib_regs` 的 `ERR_W_RANGE` / `ERR_W_SUM` **永不触发** —— 它们在 `weight_store` 侧触发；
> 2. `ERR_V_RANGE` **也永不可能触发** —— `offset_q`/`min_q`/`max_q` 的端口类型本身就是
>    `logic signed [V_BITS-1:0]`，越界值在连接处已被截断。保留该码只为编码完备，不是可达路径。
>
> **`ΣW` 的求和必须与 `recon_core` 用同一宽度口径**（两者都用 `SUM_BITS = 64`），否则会出现
> "校验通过但重构溢出"的错配。TB 用**同一个**向量核对两侧。

---

## 10. M8 `sadc_enc`（核外，`rtl/top/`）

```systemverilog
module sadc_enc #(
    parameter int P_B1    = B1,
    parameter int P_N_CMP = (1 << B1) - 1        // = 511
)(
    input  logic [P_N_CMP-1:0] cmp_raw,          // 比较器阵列温度计（bit i = x > thr[i]）
    output logic [P_B1-1:0]    sadc_code
);
```

**语义**：`sadc_code = popcount(cmp_raw)`。**只做编码**，不含比较器本体与阈值生成
（阈值属模拟域；`rtl/README.md` §3.1 已记录这个层级选择）。

**不变量**：`cmp_raw` 必须是**温度计码**（`1` 连续在前/后）。若不是，popcount 仍给出一个值 ——
**RTL 不做合法性检查**，由模拟侧保证；TB 只对合法输入断言。

---

## 11. 顶层 `sar20_digital_core`

端口采用计划 §4.1 的定义，唯一改动是把 `main_sw/sub_sw/dither_mask_sw/slice_sel` 的
宽度从 `[N_SLICES-1:0][...]` 保持不变，但**增加** `env` 相关与状态回读：

```systemverilog
module sar20_digital_core (
    input  logic        clk,
    input  logic        rst_n,
    // ---- 配置/寄存器接口 ----
    input  logic        cfg_wr,
    input  logic [15:0] cfg_addr,
    input  logic [63:0] cfg_wdata,
    output logic [63:0] cfg_rdata,
    input  logic        cfg_validate,
    input  logic        cfg_clear_valid,
    output logic        cfg_ready,
    // ---- 模拟域回读（数字输入）----
    input  logic [B1-1:0]        sadc_code,
    input  logic                 sadc_rdy,
    input  logic [ADC2_BITS-1:0] adc2_code,
    input  logic                 adc2_rdy,
    input  logic                 ra_sat,
    input  logic                 rdac_ovf,
    input  logic                 adc2_over,   // 契约修正 2，见下
    input  logic signed [V_BITS-1:0] inj_q,   // 已知注入 I（Q32）；契约修正 1，见下
    // ---- 数字输出到模拟域 ----
    output logic [N_SLICES-1:0]                       slice_sel,
    output logic [N_SLICES-1:0][N_UNIT_MAIN-1:0]      main_sw,
    output logic [N_SLICES-1:0][N_UNIT_SUB-1:0]       sub_sw,
    output logic [N_SLICES-1:0][2*DITHER_UNITS_RANGE-1:0] dither_sw,
    output logic                                      sw_valid,
    // ---- 最终输出 ----
    output logic [OUT_BITS-1:0] dout,
    output logic                dout_valid,
    output logic                clip_low,
    output logic                clip_high,
    output logic                analog_ovf,
    output logic                acc_ovf,
    // ---- 观测 ----
    output logic [31:0]         status_word
);
```

> **端口表修正（2026-09-18）**：本节初版**漏列了 `inj_q`**。`recon_core` 的 `num` 里有
> `− total·I` 这一项，`I` 必须从顶层进；而 §11 的 `cfg_addr` 映射里也没有它的位置
> （它**逐样本**变，而 cfg 寄存器是配置期写一次的）。所以它是顶层输入端口，不是寄存器。
> 这一处修正由实现阶段发现，已登记在 `rtl/top/sar20_digital_core.sv` 的模块头。
>
> **契约修正 2：顶层还需 `input logic adc2_over`（2026-09-18，L3 测试发现）**。
> `analog_ovf = rdac_ovf | adc2_ovf | ra_sat` 里的 `adc2_ovf` 指模型的 `adc2_over`：
> `adi_model/adc2.py` 定义 `over = (v < vmin) | (v > vmax)`（**严格外侧**，`v == vmax`
> 映射到最后一个码、**不算** over）。因此它**不可能从 `adc2_code` 反推** —— 码到轨既可能
> "恰好在轨"也可能"越轨被夹住"。它是与 `ra_sat`/`rdac_ovf` 同级的**模拟域回读输入**。
> 初版 §11 端口表**漏列了它**，导致 `analog_ovf` 在 L3 上恒不反映该项（实测 202 行失配）。
> 另：`adc2_dec` 自己的 `ovf` 是**寄存器溢出**，与 `adc2_over` 是两回事，两者都保留。
>
> 同样由实现阶段确定、原文未定义的三处（均为 `[假设]` 级设计选择，已在模块头登记）：
> `run` 接 `cfg_ready`；`sw_valid` = `rdac_load` 打一拍；
> **`sid_hold`** —— 在 `dem_advance` 那一拍捕获 `sid` 并保持，否则相位 11/15 会看到
> **已推进过**的 DEM 态，开关码与本次样本对不上。
>
> ⚠️ **顶层的参数一致性自检在综合中无效（2026-09-18 实测）**：顶层用 17 条
> `initial $fatal(1, ...)` 检查"模块参数是否与 `rtl_params.vh` 一致"。DC 报
> `Warning: ... The statements in initial blocks are ignored. (VER-281)` ——
> **整块被丢掉，一条都不执行**。所以这套防线**只对仿真有效，对综合完全无效**。
> 要真正卡住参数漂移，必须在**综合流程侧**另做一致性检查（例如在 `run_dc.tcl` 里
> 单独读一遍 `rtl_params.vh` 与 RTL 的默认参数值做比对并让退出码非零）。
> 这与"`simv` 对 `$fatal` 返回 0""`dc_shell -f` 对 Tcl 报错返回 0"属**同一类**：
> **报告说 OK，但实际没有任何东西在检查**。

**`cfg_addr` 映射（冻结）**：

| 地址 | 内容 | 写 | 读 |
|:---|:---|:---:|:---:|
| `0x0000 + u*8`（u = 0..70） | slice `s` 的权重（`s` 由 `0x2000 + s*0x100` 的窗口寄存器选） | ✓ | ✓ |
| `0x1000` | `offset_q` | ✓ | ✓ |
| `0x1008` | `adc2_min_q` | ✓ | ✓ |
| `0x1010` | `adc2_max_q` | ✓ | ✓ |
| `0x1018` | 控制位 `{…, sampling_mask_en, bridge_en, dem_en}` | ✓ | ✓ |
| `0x1020` | `status_word` | — | ✓ |
| `0x2000 + s*0x100` | 权重窗口：slice 号 `s` | ✓ | ✓ |

> 这个映射是**RTL 的设计选择**，不是模型的契约。它存在的唯一目的是让 P3 的
> "JSON 寄存器镜像 → RTL 载入"有一个确定的通路。`tools/export_rtl_vectors.py` 里的
> 载入序列必须与本表逐字一致。

---

## 12. P2/P3 向量文件格式（**已冻结，以本节为准**）

沿用 P1 的约定（注释 `//` → 一行 `#DATA` → 纯数字数据；打包字段 **LSB = 地址 0**）。
`$fscanf(fd, "%h", v)` 逐列读，跳过 `//` 与 `#` 前缀行（`#DATA` 之后才是数据）。

> **为什么初稿的清单被改掉**：初稿里 `p2_l2_oracle.hex` 是 2²⁰ 行、
> `p2_l2_ramp.hex` 是 65 913 行、`p2_l2_div.hex` 是 10⁵ 行 —— 都**远超仓库 pre-commit 的
> 512 KB 单文件上限**。改法是：能变成**性质**的就变成性质，能进 TB 的判据就进 TB。

| 文件 | 行数 | 列（hex 宽度） | 覆盖 |
|:---|---:|:---|:---|
| `p2_oracle_spec.hex` | 3 | `w0(12) w1(12) w2(12)`（三行相同，便于按行或按列读） | oracle 的 3 个 Q30 权重 |
| `p2_oracle_cfg.hex` | 1 | `adc2_min_q(16) adc2_max_q(16) offset_q(16) adc2_n_bits(2) out_count(6) expect_dout_eq_code(1)` | oracle 的系数与尺寸 |
| `p2_recon_coef.hex` | 1 | `offset_q(16) adc2_min_q(16) adc2_max_q(16)` | 生产配置的系数 |
| `p2_scalar.hex` | 126 | `adc2_code(3) adc2_n_bits(2) adc2_min_q(16) adc2_max_q(16) offset_q(16) exp_fine_q(16) exp_ovf(1)` | `adc2_dec` 定向（**逐行**换 `n_bits`） |
| `p2_div.hex` | 70 | `a(16) d(16) exp_q(16)` | `div_floor` **定向**（62 例含 28 负 / 6 零 / 7 个 `d=1`） |
| `p2_sat.hex` | 61 | `inj_q(16) adc2_code(3) w_scale(1) exp_word(5) exp_clip_low(1) exp_clip_high(1)` | 剪裁（**4 bit oracle 后端** + `w_scale`） |
| `p2_masks.hex` | 520 | `slice_id[8](2 each) main_on[8](16 each) sub_on[8](2 each) adc2_code(3) inj_q(16) exp_word(5) exp_clip_low(1) exp_clip_high(1)` | `recon_core` 的**真实掩码**用例 |
| `p2_ramp.hex` | 2054 | 同 `p2_masks` 末加 `rel_step(2 signed)` | 511 个进位 × 4 点（抽稀，见下） |
| `p2_alloc.hex` | 71 | `n(2) acq[8](2) conv[8](2) bank(1) conv_valid(1)` | `slice_alloc` 的 pingpong 表 |
| `p2_link_weights.hex` | 1284 | `slice(2) unit(2) w_q(12)` | 18×71 权重（所有 recon 用例共用这一份） |
| `p2_link_stim.hex` | 520 | `bank(1) coarse(3) dither(2) inj_q(16) adc2_code(3)` | **L3 只给最外层激励** |
| `p2_link_expected.hex` | 517 | `dout(5) clip_low(1) clip_high(1) analog_ovf(1)` | L3 期望 |
| `p2_regs.json` | — | `FixedPointReconstructor.to_dict()` 镜像 | P3 载入 |
| `p2_report.json` | — | 见下 | 统计与自证 |

> **`p2_recon_coef.hex` 为什么必须单独一个文件**：`offset_q`/`adc2_min_q`/`adc2_max_q`
> 是**配置期**寄存器，而 `p2_masks`/`p2_ramp`/`p2_link_stim` 里逐行的 `inj_q` 是
> **逐样本**量 —— 二者不能挤在一张表的行里。TB 若拿不到它就无法驱动生产尺寸的
> `recon_core`（本配置下三值恰为 `0 / 0 / 2^33`，但那不能靠碰巧）。
>
> **`out_count(6)` 列**：`2^OUT_BITS = 2^20`，用来让 TB 断言"TB 例化的 `OUT_BITS`
> 与导出器实际用的 `output_bits` 一致" —— 光看 `n_bits` 不够。

### 12.1 实现阶段对 §12 的修正（逐条登记，均已由 Python 侧门禁复核）

| # | 初稿写法 | 实测修正 | 依据 |
|:---:|---|---|:---|
| 1 | oracle `adc2_max_q = 2^25` | **`2^33`** | 我当初算错：`delta = 2^12 · 2^21 = 2^33`（不是 `2^12·2^13`）。用 `2^25` 时 `word` 恒 0，oracle 恒等映射直接失效 —— 已实测 |
| 2 | `p2_scalar` 无 `adc2_n_bits` 列 | **加此列**，列序紧跟 `adc2_code` | `P_ADC2_BITS` 是 elaboration 期参数、不是运行时端口，逐行换 `n_bits` 的文件没有这一列**无法判读** |
| 3 | `slice(1) unit(1)` | **`slice(2) unit(2)`** | 1 个 hex 位撑不下 `slice=17`/`unit=70` |
| 4 | 斜坡 9 点/进位（4599 行） | **4 点/进位（2054 行）** | 4599 行 = 943 KB **超 pre-commit 的 512 KB 上限**；4 点仍覆盖每个进位两侧（`rel_step<0` 与 `>0` 各 2 行），跨度 2 个码、3 个单调区间 |
| 5 | `p2_sat` 无后端位宽约定 | **固定 4 bit + `w_scale`** | 它复现的是 `straight_backend(4)`，TB 必须用 `P_ADC2_BITS=4` 重例化；`w_scale` 把 `gain/denom` 挪到另一个量级 |
| 6 | `p2_sat` 含 `acc_ovf` 列 | **不含** | 冻结列表达不了"粘滞 + 保持上一拍"。**溢出路径改由 TB 自建激励**（见 §13） |
| 7 | ties-to-even 用 `n_bits=12` + `delta=4096x` | **用 `n_bits=1` + `delta=2x`** | 除数 2 无法用 0 位端口表达；`delta=4x` 在 `n_bits=1` 下是**整除**、余数恒 0，测不到平局 |
| 8 | `p2_l2_div` 10⁵ 行 | **62 例定向** | 主体判据改在 TB 侧用**定义式** `q·d ≤ a < (q+1)·d`（≥256 位宽算），与模型无关 |
| 9 | — | `adc2_dec` 的 `ovf` 在合法寄存器下**结构上不可达** | `|delta| < 2^64` ⇒ `|round_even_divide(n, 2^(k+1))| ≤ |delta|`，商不会越出 `V_BITS`。`exp_ovf` 全 0 是**真实结论**，不是没测 |
| 10 | — | `n = (2c+1)·delta` 在 `W_MUL` 位内**极端组合下可回绕** | `min_q=-2^63 / max_q=2^63-1 / n_bits=20` 时 `> 2^84`；该组合无模型期望值，**TB 不做 bit-exact** |

### 12.2 第 10 条（`n` 的位宽边界）推导与适用域

`adc2_dec.sv` 里 `n = (2·adc2_code + 1) · (adc2_max_q − adc2_min_q)`，宽度
`W_MUL = P_ADC2_BITS + 1 + V_BITS + 1`（`P_ADC2_BITS=20` 时 = **86** 位有符号）。

```
|2c+1|      < 2^(P_ADC2_BITS+1) = 2^21
|max_q−min_q| < 2^V_BITS        = 2^64          （两者都是 64 位有符号寄存器）
==> |n| < 2^85  < 2^85，落在 86 位有符号的范围内 [−2^85, 2^85)
```

**结论**：在**寄存器合法域内**（两个 Q32 寄存器各自在 `[−2^63, 2^63)`）`n` 不会回绕，
86 位是够的。真正的问题不在回绕，而在**这个极端组合在模型里没有对应的期望值**：
`min_q = −2^63` 与 `max_q = 2^63−1` 意味着归一化电压 ≈ ±2^31，早就超出
`from_weights` 的合法域（`|v| < 2^30`）。所以：

* 该组合**不是**"RTL 有 bug"的情形，而是**模型侧不产生向量**的情形；
* TB 因此**不对该组合做 bit-exact 比对**（无黄金值可依），只保证它在合法域外；
* 如果将来有人把 `V_BITS` 调大或放宽 `from_weights` 的合法域，**必须重算 `W_MUL`**
  并在契约里重新登记 —— 这条就是留给那时的提醒。

**改成「性质」的两个测试**（不再落文件，理由与做法都要能复算）：

| 初稿 | 改法 | 为什么这样仍然是**独立**判据 |
|:---|:---|:---|
| 2²⁰ 全码 oracle（2²⁰ 行） | TB 自己循环 `c = 0..2²⁰−1`，断言 `dout == c` 且无任何标志。oracle 的权重从 `p2_oracle_spec.hex` 读入 | 「`dout == c`」是模型侧 `test_every_twenty_bit_word_and_code_width_without_a_huge_analog_record` 断言过的**性质**，不是逐行黄金值；TB 断言同一条性质，Python 门禁断言该性质在模型侧成立 |
| 除法器 10⁵ 行 | TB 侧**定义式自检**：`q·d ≤ a < (q+1)·d`（用 ≥256 位宽算），外加 `p2_div.hex` 的定向用例 | 这是 `floor` 的**定义本身**，与模型实现**无关**；模型复刻反而会共享同一个错误 |

`p2_report.json` 必含：`bank_equals_index_mod_2`（**实测**布尔）、`oracle_property`、
`peak_bits`（`op1/op2/op3/num/shifted/denom` 的实测最大 `bit_length`，D3 义务）、
`coverage`、`link_checks`、`waves`（波形与配置，便于复算）。

**ties-to-even 怎么在 12 bit 后端下被测到**（否则这 8 个样例测不了）：
`P_ADC2_BITS=12` ⇒ `K=13`、除数 `2¹³`。取 `adc2_code=0`（`2c+1=1`）与 `delta = 4096·x`，则
`fine = min_q + round_even_divide(4096x, 8192) = min_q + round_even_divide(x, 2)`。
取 `x ∈ {−7,−5,−3,−1,1,3,5,7}`、`min_q = 1000` ⇒ `fine = 1000 + {−4,−2,−2,0,0,2,2,4}`，
即复现 `test_signed_ties_to_even` 的 8 个样例。负 `delta` 意味着 `max_q < min_q` ——
这是**刻意的**（在测舍入函数，不是在生产配置），已在向量注释头与报告里如实登记。

**oracle 的构造（关键，必须写清，否则复现者不知道那些奇怪常数从哪来）**：

模型侧 `tests/unit/test_fixed_point.py::straight_backend` 的尺寸是 `n_active=1, n_main=1, n_sub=2,
adc2_n_bits=20`，与生产配置（8/63/8/12）不同。因此 oracle **用 `recon_core` 的模块参数重例化**
（`P_N_ACTIVE=1, P_N_MAIN=1, P_N_SUB=2, P_ADC2_BITS=20`），而不是改生产配置。构造：

```
权重：三个单位之和 = 2^30   （例：w = {2^30-2, 1, 1}，均为正）   → gain = total = 2^30
adc2_min_q = 0, adc2_max_q = 2^25, offset_q = 0
掩码全 0 → plus = 0 → s = +1 全部单位 → rails = total = 2^30
I = 0
F   = round_even((2c+1) * 2^25 / 2^13) = (2c+1) * 2^12
num = F*2^30 - 2^30*2^32
A1  = num + gain*2^32 = F*2^30 = (2c+1)*2^42     （经 >>>33 得 (2c+1)*2^29）
word= floor((2c+1)*2^62 / 2^63) = floor((2c+1)/2) = c
```

于是 `c ∈ [0, 2^20)` 时 `dout = c`，**每个 20 bit 字恰好出现一次**，
且解码电压步长恒为 `2*v_fs/2^20`（模型侧断言 `diff == 6/2^20`，因 `v_fs=3.0`）。

> **oracle 必须由向量导出器给出的权重驱动**，不能在 TB 里手写常数 —— 手写就等于
> 在测试里复制一份参数表（契约 §9 禁止）。导出器把 `w[]` 一起写进 `p2_l2_oracle.hex` 的前几列。

**斜坡的构造**：`tests/unit/test_fixed_point.py::test_all_coarse_carries_preserve_monotonicity_and_bounded_code_width`
用 `run_pipeline` 跑 511×(129) 个点，间距 `lsb/16`。RTL 侧不重跑整条模拟链，
而是把模型侧 **`adc2_code` + `inj_q` + 掩码** 作为激励导出（这正是 §12 的 L3 口径），
期望 `word` 由模型给出；RTL 只负责"给定这些数字输入，输出是否一致"。

---

## 13. P2/P3 出口判据

| 判据 | 形式 |
|:---|:---|
| L0 `div_floor` | 与 Python `math.floor(a/d)` **逐样本相等**（含 `a<0`、`a=0`、`d=1`、`d` 为 2 的幂、`|a|` 接近 `2^62`） |
| L0 `adc2_dec` | 与 `round_even_divide` 一致；**必须复现 `test_signed_ties_to_even` 的 8 个样例**（`k=1` 驱动） |
| L0 `slice_alloc` | 64 拍逐拍与模型的 `groups[parity]` 相等；INV-4a..4d 全绿 |
| L2 oracle | 2²⁰ 全码：`dout == c` 逐样本相等；解码电压**恰好 1 LSB 宽**（由 `word` 的差恒为 1 证明）；无 `clip_*` |
| L2 溢出 | 越界输入 → `acc_ovf` 置位（粘滞）且 `dout` **保持上一拍合法值**（不回绕、不变成另一端的值） |
| L2 剪裁 | `clip_low/high` 与 `analog_ovf` **三者互不替代**（复现 `test_output_saturation_is_separate_from_analog_saturation`） |
| L2 镜像 | `p2_regs.json` → `calib_regs`+`weight_store` → 与 Python 同样的输出（复现 `test_register_image_roundtrip_and_illegal_widths`） |
| L3 链路 | 512 样本与 `to_codes().code` **逐样本 bit-exact** |
| 综合 | `synth/` 下 DC 跑通，给出面积/时序基线；无 latch、无不可综合构造 |

**必须准备的反例测试（防测试说谎）**：

| 反例 | 期望 |
|:---|:---|
| 把 `div_floor` 的负数修正去掉（退化成向 0 截断） | 全码 oracle 的"恰好 1 LSB 宽度"**必须失败** |
| 把 `clip_high` 的判据从 `>= count` 改成 `> count` | 剪裁用例**必须失败** |
| 把 `acc_ovf` 改成"逐拍复位"（不粘滞） | 溢出用例**必须失败** |
| 把权重 ROM 地址错位一格 | L3 bit-exact **必须失败** |
| 把 `slice_alloc` 的 `conv` 改成 `acq` 同组 | INV-4a **必须失败** |

---

## 14. 位宽收缩（D3）的实测入口

`docs/rtl/RTL_ARITHMETIC_CONTRACT.md` §8 登记了这条义务。P2 的实测口径：

1. `tools/export_rtl_vectors.py` 在导出 L2/L3 向量时，**报告** `shifted`、`num`、`op1..op3`、`denom`
   的实际最大值与 `bit_length`；
2. TB 在跑完 oracle + 斜坡 + L3 后，把 RTL 内部（通过 `recon_core` 的调试输出端口）观察到的
   最大值 dump 出来，与模型侧对照；
3. **只有两边一致**，才允许按计划 §5.3 收缩。收缩必须新写 ADR 并附新旧宽度差异表。

> P2 **不**做收缩。收缩是 P5 之后的事，且必须先有实测。

**实测结果（2026-09-18，`tests/unit/test_recon_mirror.py::test_measured_peaks_are_far_below_the_declared_bounds`）**：

| 量 | 实测峰值（幅值位） | 声明界 |
|:---|---:|---:|
| `num` | **67** | 95 |
| `shifted` | **88**（含符号 89） | 95 |

`shifted` 的 89 与 P0 阶段 `CodeStream.peak_accumulator_bits = 89` **两条独立路径互相印证**。
**声明界比实测高 7 位**，所以收缩空间是实打实的 —— 但必须补完"全码 + 极端权重 + 满幅"三重扫掠
才敢动，且要新写 ADR。

### 14.1 一个反直觉的实测事实（写下来给将来改宽度的人）

把 `W_S1` 从 97 **调窄 2 位到 95，门禁仍然是 PASS** —— 因为契约声明的界（`|num| < 2^95`）
与真实可达范围（`2^67`）差了 **28 个二进制数量级**，窄 2 位根本咬不到数据。

所以"够用"**不能由声明界反推**，必须由实测证明。这条已写成测试的一部分
（`test_narrowing_a_width_is_detected` 同时断言"窄 2 位仍 PASS"与"压到 70 位必 FAIL"），
若哪天"窄 2 位"变红了，就说明实测峰值已经顶到声明界附近，**必须重评位宽策略**。

---

## 15. P2 实现阶段抓出的缺陷账本（每条都能被门禁复现）

### 15.1 RTL 缺陷

| # | 位置 | 现象 | 根因 | 谁抓到 |
|:---:|:---|:---|:---|:---|
| **RTL-4** | `div_floor.sv` | `a = 0x4000_0000_0000_0001, d = 1` 时 `Q[62]` 应为 1，实测 **`Q[56]` = 1**；T1 报 30 错、T2 报 419 错 | 一个时钟内级联 `P_STAGES` 级，第 `s` 级产生的商位必须落在该组的**第 `(P_STAGES-1-s)` 位**（MSB 优先）；原写成 `qbits[s]`，于是 `q_next = (quo << P_STAGES) \| qbits` 把每组**按位倒序**拼进去。**`P_STAGES=1` 时反转不可见**，所以 P1 阶段（无除法器）查不出来 | T1/T2 单向测试 |
| **RTL-5** | `recon_core.sv` | 定向溢出用例下 `acc_ovf` 该置位却为 0；`gain_err` 同理 | `ovf_pend <= any_ovf` 与 `gerr_pend <= (gain_s == 0)` 写在 **`start` 那一拍**，但 `any_ovf` 依赖的是 **stage-A 寄存器**，而那一拍寄存器里装的还是**上一个样本**的值 —— 于是标志**晚一个样本**。oracle 与掩码用例**从不发生溢出**，两种写法给出同一个答案（0），所以只有定向溢出激励能区分 | T8b 定向溢出 |

> **RTL-4 的一个方法论盲区（必须记住）**：我当时已经有一个"RTL 位宽镜像"脚本
> （`sim/ref/recon_rtl_mirror.py`，4 组配置全 PASS），**它没抓到 RTL-4**。原因是镜像里
> `div_floor` 直接写成了 Python 的 `a // d` —— **镜像的是"应该做什么"，不是"RTL 实际怎么做的"**。
> 位级实现没被镜像，那一层的位序缺陷天然逃逸。补法是**把位级算法也镜像进来**，
> 并且加一条"把镜像切到错误位序，断言它确实与 `a // d` 不符"的反例测试。

### 15.1b 工具链/流程缺陷（"报告说 OK，但实际没人检查"）

| # | 位置 | 事实 | 后果 |
|:---:|:---|:---|:---|
| TL-1 | `simv` | 默认对 `$error`/`$fatal` 返回 **0** | CI 会把仿真失败当成功。必须加 `-exitstatus`（P1 已修） |
| TL-2 | `dc_shell -f` | Tcl 报错时**仍返回 0** | 综合失败被当成功。已在 `run_synth.sh` 用顶层 catch 兜住 |
| TL-3 | DC `elaborate` | `initial` 块被**整块忽略**（`VER-281`） | 顶层 17 条参数一致性 `$fatal` **在综合中一条都不执行**。必须另做 DC 侧检查 |
| TL-4 | `synth/run_synth.py` 的 `--files` | 传目录原先被**静默跳过**（`expand_files` 里 `continue`） | "no source files" 直接退出，看起来像"没找到文件"，实际是参数被吞了。已改成目录递归展开 |
| TL-5 | `urg`（VCS W-2024.09-SP1） | 在 `covdb_get_license()` 里**崩溃**（栈含 `libucapi.so`），rc=1、不产报告；而 `lmstat -c 27080@localhost -f VC-COVERAGE` 显示 **99 授权 / 0 在用** | **覆盖率采集成功、报告不可得**。试过 3 种 license 变量组合 + `HOSTNAME` + `-licqueue` 全部无效；无 DVE（W-2024.09 不再自带）。降级方案：`cov.vdb` 是标准产物，换一台 `urg` 正常的机器出报告 |
| TL-6 | `pgrep dc_shell` | 真正干活的进程名是 **`common_shell_exec`**（`comm` 截断到 15 字符，`dc_shell` 只是它的 `-shell` 参数），外层还被 `timeout` 包住（`argv[0]` = `timeout`） | 在作业 96% CPU 推进时 `pgrep dc_shell` **返回空** —— **假阴性**。若据此判定"它没在跑"并重启，会把正在推进的作业整段扔掉。改用 `pgrep -f 'run_dc.snapshot.tcl'` 或看 `phase.log` 时间戳 |
| TL-7 | VCS 并发断言的**默认文案** | 没写 `else` 的 `assert property` 失败时，VCS 打印 `... A8_clip_exclusive: started at X failed at X` —— **既不含 `Error` 也不含任何可搜关键词** | `run_vcs.sh` 的正则兜底（按 `fatal`/`error` 匹配）**0 命中**；只有 `simv -exitstatus` 的**退出码 2** 暴露了它。教训：**断言一律自带 `$error` + 可搜索文案**；判断"有没有报"**先看退出码，别信文本** |
| TL-9 | `synth/run_dc.tcl` 的 TNS 统计 | `set tns [dc_slack max 1000000]` → `get_timing_paths -nworst 1000000` 在 492k cell 的设计上 **13 分钟不返回** | **所有报告（area/timing/power/qor）都已写完，卡住的只有 `status.txt`** —— 把已到手的结果锁死在一个无关紧要的数字后面。小设计上跑 8 次都看不出来。已封顶 `nworst=2000` 并把 `TNS_NWORST=2000` 写进 `status.txt`（TNS 从此是**下界**，引用须连着上限） |
| TL-8 | `assert` 在**组合模块**里 | `assert(!bad)` 写在 `always_comb` 里，会在**中间态**求值（目标模块的多个输入同刻被改、组合链深度不同 → 看到"新 A + 旧 B"的混合态） | 实测：首次跑 `p2_tb` 时该断言命中 **7166 行**，而 TB 的数值比对全绿 —— **纯假阳性**。正解是**延迟即时断言** `assert #0`（Observed 区求值，所有 delta 稳定之后）|

> **共同形状**：不是算错，而是**判据本身不存在或不被执行**。
> 自查方法：对每一条"门禁"，问一句"**如果我故意破坏它，谁会红？**"
> 答不出来，就不是门禁。

### 15.2 测试台缺陷（比 RTL 缺陷更值得警惕：它们会让**正确实现**变红）

| # | 位置 | 现象 | 根因 |
|:---:|:---|:---|:---|
| TB-3 | `p2_tb.sv` `expect_eq("weights count", ...)` | 期望值打印成 **126**，实际 1278 | `N_SLICES` 是 **5 位**、`N_UNIT_TOTAL` 是 **7 位** localparam，`18*71` 在表达式里按 `max(5,7)=7` 位算 → 1278 截成 126。**位宽纪律不只适用于 RTL** |
| TB-4 | `p2_tb.sv` T7 `rel_step` | 单调性误报 **510** 次（= 511 − 1） | `rel_step` 列只有 2 个 hex 字符，用 64 位 `%h` 读进来后**没做符号扩展**，`0xf0` 被当成 +240 而非 −16 → "跨进位的跳变"也被当成"进位内"检查 |
| TB-5 | `p2_tb.sv` `while (!$feof(fd))` | 最后一行之后 short read → `$fatal` | `$feof` 只在**读过界之后**才为真。正确写法是**先读本行第一个字段**，读不到即 EOF |
| TB-6 | `p2_tb.sv` oracle/T8 实例几何 | 抄成 `n_main=1, n_sub=2` | 模型 `straight_backend` 的 `CalibrationSpec(1, 2, 1, ...)` 是 `n_active=1, dac_n_main=2, dac_n_sub=1`。**T5 恰好不受影响**（RDAC 码 = 0 时所有单位 `plus=0`、`a=1`，求和与主/子切分无关）—— "恰好不受影响"不等于"写对了" |
| TB-7 | `p3_top_tb.sv` 比 `analog_ovf` | 直接逐样本比 → **3356 行"失配"**，全是"粘滞位还留着上一行的 1" | 拿**逐样本判据**去比**粘滞位**。见 §16.4：正确口径是"与期望的累积 OR 比"，改后 0 失配 |
| TB-8 | `p3_top_tb.sv` 三个模拟标志的驱动相位 | 在相位 0 跟着下一行驱动 → 失配 202→**3357** | 这三个是**粘滞**量，必须在**捕获窗口的相位 8**放"将被捕获的那一行"的值，否则会把下一行的事件提前算进来 |
| TB-9 | `p2_tb.sv` oracle 实例的 `o_code` **从未初始化** | 断言 `A8a` 报 `clip_low/clip_high = X`（13 次、连续 12 拍）；探针显示 `a1=x → div_q=x`，而 `gain_s/total_s/rails_s` 均为已知值 | T4 **只检查 `dout_valid`、从不检查 `dout` 的值** → `adc2_code = X` 一路穿过所有功能判据而无人发现。**这是本轮唯一"让错误静默通过"的测试缺陷**（其余都是"让正确实现变红"）。教训：**把 DUT 的每一个输入端口在复位段就钉死**，不要"用到哪设到哪" |

> **TB-5 与 TB-3 的共性**：两者都不会让**错误**实现通过，而是让**正确**实现报错或崩溃；
> 与"放宽判据掩盖回归"同样有毒，只是方向相反。
> **TB-6 的教训**：一个写错的测试**可能因为被测对象恰好不敏感而全绿**，这类"绿灯"没有任何信息量。

---

## 16. P2/P3 完成记录（2026-09-18）

### 16.1 结果

| 项 | 实测 |
|:---|:---|
| 平台 | VCS `W-2024.09-SP1_Full64` @ `yian@192.168.38.129` |
| 模块级（`sim/tb/p2_tb.sv`） | **`checks = 1057289`，`errors = 0`，`simv` 退出码 0** |
| 顶层链路（`sim/tb/p3_top_tb.sv`） | **4095/4095 逐样本 bit-exact**（`dout` **+ `clip_low/high` + `analog_ovf`**），`checks=16386 / errors=0`，`exit 0` |

分项账本（最终一次运行，全部为 0）：

| 测试 | 覆盖 | 错误 |
|:---|:---|---:|
| T1 `div_floor` 定向 | 62 例（含 28 负 / 6 零 / 7 个 `d=1`） | 0 |
| T2 `div_floor` **定义式自检** | 500 × `q·d ≤ a < (q+1)·d`（≥256 位宽，**不依赖模型**） | 0 |
| T3 `adc2_dec` | 129 行、`n_bits ∈ {1,2,3,4,8,12,20}`、含 ties-to-even 8 样例 | 0 |
| T4 延迟 | 实测 **11** 拍 = 模块内 `RECON_LAT` 常量 | 0 |
| T5 **2²⁰ 全码 oracle** | **1 048 576** 个码，每码恰一次 | 0 |
| T6 真实掩码 | 512 行（掩码由模型 `_terms` 解码，非合成） | 0 |
| T7 进位密集斜坡 | 2044 行（511 进位 × 4 点）+ 单调性 | 0 |
| T8 剪裁 | 48 行（4 bit oracle 后端 × `w_scale`） | 0 |
| T8b 溢出粘滞 | TB 自建越界激励 + 保持/粘滞/显式清除 | 0 |
| T9 **模块级 L3 链路** | 511 样本，M1→M2→M3→M5→`recon_core`，只给最外层激励 | 0 |

顶层 `p3_top_tb` 的**判决性对照**：不加 dither 注入时 511/511 全不符，
且 **511 个差全部是 2048 的整数倍**（= 1 个 eff 单位），`|δ| ≤ 8 个 eff 单位`，
**没有一个例外** —— 这排除了"碰巧对上"，证明 dither 是**唯一**的差异源。

### 16.2 变异验证（门禁有牙的证据）

最终代码上做过一次**刻意回退**：把 `clip_lo_c` / `clip_hi_c` 改回无符号比较
（即重新引入 RTL-7），T8 立即报 **18 错**、`P2 RESULT: FAIL`；还原后回到 0。
另外三轮开发中的红→绿变迁同样构成证据：

| 门禁 | 缺陷版本 | 修复后 |
|:---|---:|---:|
| T1 / T2 `div_floor` | 30 / 419 | **0 / 0** |
| T7 单调性 | 510（我 TB 的符号扩展 bug） | **0** |
| T8 剪裁 | 18 | **0** |
| T8b 溢出粘滞 | 9 | **0** |

### 16.3 本轮共 4 个 RTL 缺陷 + 6 个测试台缺陷

见 §15.1 与 §15.2 的账本。**四个 RTL 缺陷分属四类根因**：

| # | 类别 | 一句话 |
|:---:|:---|:---|
| RTL-4 | **位序** | 组内商位排列写反（MSB 优先） |
| RTL-5 | **时序** | 溢出判定锁存在错误的拍，用了上一个样本的寄存器值 |
| RTL-6 | **协议** | 漏了契约 §4.4「溢出时 `dout_valid` 仍为 1」 |
| RTL-7 | **符号** | 有符号量与无符号字面量比较 → 整条按无符号算（**与 P1 的 RTL-3 同族**） |

> **最该记住的是 RTL-7**：同一个族系的缺陷在 P1 已经抓过一次、写进过文档与技能，
> P2 又犯了一遍，只是这次栽在比较器而不是算术上。
> **"知道规则"不等于"写代码时会检查它"** —— 必须在审查清单里把
> "每一处有符号比较，都问一遍另一个操作数是不是 signed" 变成机械动作。
>
> 而四个缺陷**都只在定向/边界激励下暴露**：2²⁰ 全码、512 行掩码、2044 行斜坡全绿。
> 这是"大规模测试全绿 ≠ 边界正确"的第四次独立实证。

### 16.4 粘滞标志的比对口径（一个实测踩出来的陷阱，必须传下去）

顶层的 `analog_ovf` 接的是 `status_regs.analog_ovf_sticky` —— 是**粘滞位**
（ADR 0014：一旦置位只由 `clr`/`rst_n` 解除）。而向量文件里那一列是**逐样本**量。
**两者定义不同，不能直接逐样本比。**

| 比法 | 结果 | 是否算缺陷 |
|:---|:---|:---|
| 直接逐样本比 | **3356 行"不同"** | **不算** —— 全是"粘滞位还留着上一行的 1" |
| **与期望的累积 OR 比**（到本样本为止） | **0 行失配** | ✅ 这才是粘滞位的正确逐样本口径 |

**不许把它"修"成逐样本**：有人会想到用 `cfg_clear_valid` 每样本清一次 —— 但该端口同时接
`calib_regs.clear_valid`，一发就把 `cfg_ready` 撤掉、**整条流水停摆**（已试过，不可用）。
更要紧的是：那等于**把粘滞语义改掉去迎合判据**，而粘滞语义是 ADR 0014 的硬要求。

另一个同源陷阱：这三个标志的**驱动相位**必须在**被捕获窗口的相位**（相位 8）放"将被捕获的
那一行"的值。若像数据通路那样在相位 0 就跟着下一行驱动，粘滞位会把**下一行**的事件提前算进来
—— 实测失配从 202 涨到 **3357**。

> **一般化**：**带记忆的输出（粘滞、计数、累加）不能用"逐样本相等"当判据**，
> 要么比累积量，要么在比之前显式复位。而且**不许为了迁就判据去改被测对象的语义**。
> 本轮实测的反例：先按逐样本比得到 3356 行"失配"，若据此去改 RTL 或放松判据，
> 就会把一个**正确**的粘滞实现改坏。

### 16.5 未纳入 P2 的事项（如实登记，不静默跳过）

* **顶层 dither 注入**：`sar20_digital_core` 无 dither 输入端口，链路级 bit-exact 靠仿真期
  `force`。**该路径不覆盖 `dither_gen`**，也不覆盖 `unit_therm` 的 `dither_rail` 分支 ——
  这两个模块仍归 P1 的统计判据。

* **L3 的覆盖边界（可判定的三分，勿笼统说"L3 全绿"）**：

  | 缺陷类 | L3 是否敏感 | 依据 |
  |:---|:---|:---|
  | `div_floor` **位序**（RTL-4） | **敏感** | mutant 实测：4084/4095 个 `dout` 变化（`errors` 202→6335） |
  | `clip_low/hi` **有符号比较**（RTL-7） | **敏感** | 新向量的 2 行 `clip_low` + 3 行 `clip_high` 全部逐位对上 |
  | `recon_core` **溢出时序**（RTL-5/RTL-6） | **不敏感 —— 原因是向量量程不够，不是结构上办不到** | L3 的 `inj_q` 最大只有 **2^27**，`op3 = total·inj_q ≈ 2^35·2^27 = 2^62 ≪ 2^95`，`acc_ovf` 全程 0 拍 |

  第三行必须写成"**没走到**"而非"不可能"：`|inj_q|` 合法域到 `2^63`、`total ≈ 2^35`，
  `op3` 理论上可达 `2^98 > 2^95`，**溢出是可达的**。而且即便把 `inj_q` 打大，
  **模型侧会 `raise OverflowError`、没有黄金值**，所以这一类**注定**只能由 `p2_tb` 的
  T8b（TB 自建越界激励、只断言行为不比对数值）覆盖 —— 这是分工的必然，不是测试偷懒。
* **`slice_alloc` 与 `sadc_enc`**：不在 `p2_tb` 内，由 `sim/tb/p2_smoke_tb.sv` 覆盖
  （INV-4a..4d、popcount 511+9 组）。
* **`p2_link_stim.hex` 的 coarse 覆盖只有 `[50, 462]`**（实测）。`coarse = 0` 附近的
  行为在向量里**没有对照值**，这是覆盖洞，已登记；要补需重生成向量。
* **覆盖率采集**：`-cm` 可用，P2 未开启。
* **位宽收缩**：实测入口与结果见 §14；**P2 不做收缩**。
* **形式验证 / SVA / CDC**：未做。

---

## 17. P5 综合实测（逐步追加，2026-09-18）

### 17.1 `recon_core` 单独综合，`P_STAGES=1`，`COMPILE_MODE=compile`，10 ns

来源 `area.rpt` / `timing.rpt` / `qor.rpt`；`RTL_PARAMS_SHA = 39b12360…`。

| 项 | 值 |
|:---|---:|
| Total cell area | **312 489.27** |
| Leaf cells | **492 504**（组合 491 761 / 时序 **743** / buf+inv 107 121） |
| WNS | **0.00**（0 条违规）→ **10 ns 收敛** |
| 关键路径 | slack **1.83 ns / 140 级**；终点 **`inj_r_reg[63]` → `ovf_pend_reg`** |
| hold | 743 条违规，最差 −0.12 ns（**未修**） |
| compile 墙钟 | 43 min 03 s |
| `N_CYC = ceil(63/1)` | **63 拍**（吞吐出局） |

### 17.2 瓶颈归属：`recon_core` 自己的组合算术，**不是除法器**

这条路径走 `op3 = total_s × inj_r`（**130 位有符号乘法**）→ `num` → **5 个量级比较器**
（`bad_op1/2/3/4/den`）→ `any_ovf` → `ovf_pend_reg`，140 级、8.00 ns 到达。

**这是结构性论证，不是拟合**：`div_floor` 是**迭代**实现，每拍只做 `P_STAGES` 级减法，
长度被流水寄存器切开，**不可能出现在跨 140 级逻辑的组合路径上** —— 它根本不在这条路径上。

### 17.3 ⚠️ **撤回**：17.2 的 P6 推论**已被 P=7 实测推翻**

17.2 只测了 `P=7` 那一个点，据此推出"真旋钮是把 130 位乘法与比较链流水化"。
**P=7 实测结果与这个推论相反，故本节原推论撤回**（撤回条件当初已预先登记：终点若变成
`div_floor` 内部即撤回 —— 它确实变了）。

`P_STAGES=7`（**顶层实际使用的值**）实测：

| 项 | 值 |
|:---|---:|
| Total cell area | **315 678.87** |
| Leaf cells | **500 997**（组合 500 262 / 时序 **735** / buf+inv 108 795） |
| WNS | **+0.002 29 ns** → 10 ns 名义收敛，但**余量只有 0.023%**（几乎贴线） |
| 关键路径 | **`u_div/dv_reg[62]` → `u_div/q_reg[62]`**，**283 级逻辑**、9.80 ns |
| 层次清单 | `DW01_cmp2_*` → `DW01_sub_*` → `DW01_add_1`，即 **7 级"比较 + 条件减"** 的 DesignWare 链 |
| compile 墙钟 | **47 min 58 s** |

**两个点合起来才是完整结论**：

| `P_STAGES` | `N_CYC` | `RECON_LAT` | ≤16? | 面积 | 最差 slack | 关键路径终点 | LoL |
|:--:|:--:|:--:|:--:|--:|--:|:---|--:|
| 1 | 63 | 65 | **✗** | 312 489.27 | **+1.83 ns** | `inj_r_reg[63]` → `ovf_pend_reg` | 140 |
| 4 | 16 | **18** | **✗ 不可行** | 313 859.99 | +1.39 ns | `u_div/cnt_reg[2]` → `u_div/q_reg[62]` | 204 |
| **7** | **9** | **11** | **✓** | 315 678.87 | **+0.0023 ns** | **`u_div/dv_reg[62]` → `u_div/q_reg[62]`** | **283** |

> **⚠️ `P=4` 在 `PHASES=16` 下不可行** —— 它的 `RECON_LAT = 16+2 = 18 > 16`，会与下一个样本的
> `recon_start` 相撞。它的 `+1.39 ns` 余量**买不到**。
> **可行的决策域是 `P ≥ 5`**（`N_CYC ≤ 14`）；`P=6`（`N_CYC=11`、`RECON_LAT=13`、余 3 拍）
> 是"保留调度余量前提下"的候选点，**未跑**（见 §17.7）。
>
> **教训（本轮我犯的判据错误）**：吞吐预算必须用**实现的实际延迟常量** `RECON_LAT = N_CYC + 2`，
> **不能只算算法本身的拍数** `ceil(63/P)`。用后者会把不可行点误判为"刚好可行"。

1. **`P_STAGES` 调大是把关键路径"搬进"除法器。** P=1 时除法器不关键，恰恰因为它一拍只做 1 级减法 —— 代价是 63 拍。
2. **面积代价极小**：+1.02%（cell +1.7%）。**真正贵的是时序余量**：+1.83 ns → +0.0023 ns。
   一句话：**P=7 用 1% 面积换了 7 倍吞吐（63→9 拍），但把 10 ns 的余量吃光了。**
3. **在 P=7 下，流水化 568 项加法树救不了 10 ns** —— 那条路此刻是**次要路径**。
   要动就得动**除法器**：减小 `P_STAGES`，或换成**阵列除法器 / 倒数 ROM**
   （`div_floor.sv` 头部已把这两条登记为 P6 备选项）。
4. 调度预算要求 `P≥5`：P=4 实际为 18 拍，不能选用。已测/未测状态以 §17.7 为准。

### 17.4 引用纪律（不连着引就会被误读）

1. **`recon_core` 单独综合只有 743 个时序单元** —— `weight_store` 的 **61 kbit 权值寄存器
   不在里面**（`w_rom` 是 `recon_core` 的输入端口）。所以
   **"61 kbit 权值寄存器是不是瓶颈"这个问题，`recon_core` 单独综合回答不了**；
   而且 312 489 **不能当顶层下界**（不含权值存储，也不含 M1–M5 / `slice_alloc` / `ctrl_fsm` / `status_regs`）。
2. 面积/时序数字必须连 **`RTL_PARAMS_SHA`** 与 **`COMPILE_MODE`** 一起引
   （头文件自带 `dirty tree : 1`；`compile` 与 `compile_ultra` 口径**不可直接比**）。
3. **TNS 是下界**，引用须连着 `TNS_NWORST`（见 §15.1b TL-9）。

### 17.5 扫描点的取舍（明确登记，不是漏跑）

单点墙钟 ≈45 min（`compile` 口径），6 点需 ~5 h，且该 VM（22 GB）一次只能跑一个
（`recon_core` 峰值 10.3 GB）。故取 **{7, 4, 63}**：

| `P_STAGES` | `N_CYC` | `RECON_LAT` | ≤16 拍（`PHASES=16` 预算）？ | 取/舍理由 |
|:--:|--:|:--:|:--:|:---|
| 1 | 63 | 65 | ✗ | 吞吐直接出局（但有面积/时序数据） |
| 4 | 16 | **18** | **✗** | 实测后发现**不可行**（`RECON_LAT=18 > 16`）；它的时序余量买不到 |
| 7 | 9 | ✓ | **生产值**（`sar20_digital_core.sv` 里硬编码），最相关 |
| 6 | 11 | 13 | ✓ | **补跑**：可行域门槛是 `P ≥ 5`，故真正的决策点在 5/6/7 之间；6 保留 3 拍调度余量 |
| 14 / 21 | 5 / 3 | ✓ | **明确不跑**：落在 (7,63) 之间、不跨任何边界，边际信息最低 |
| 63 | 1 | ✓ | 极端：一次迭代、63 级级联 |

### 17.6 `weight_store` 伪负载对照：**唯一变量是输出负载，一次发散、一次收敛**

> **编号说明**：本节起为追加内容，**§17.1–§17.5 的原文一字未改**。
> 分工时指定的编号是「§17.4/§17.5/§17.6」，但盘上 §17.4（引用纪律）与 §17.5（扫描点取舍）
> **已被占用**，故本轮新内容顺延为 **§17.6 / §17.7 / §17.8**，对应关系：
> 伪负载对照→§17.6、`P_STAGES` 表→§17.7、顶层发散→§17.8。
> （§17.3 末尾第 4 条那句"`P=4` …正在跑"已被后续实测覆盖，**按留痕原则未改原文**，
> 其结论见 §17.7。）

#### 两次运行的身份（"单变量"的证明）

| | 发散那次 | 收敛那次 |
|:---|:---|:---|
| 顶层 | `weight_store` | `weight_store` |
| 时钟 / corner | 10 ns / TT | 10 ns / TT |
| `RTL_PARAMS_SHA` | `39b12360…`（同一份头文件） | `39b12360…` |
| **实际执行的脚本快照 md5** | **`72430a67b5829c21e23d95ad9a8d318d`** | **`72430a67b5829c21e23d95ad9a8d318d`** |
| **启动时的输出负载** | **`--load 0.02`（默认，芯片级）** | **`--load 0`（不加载）** |
| `dc.log` | `~/adc_rtl_synth/wstore2_only/out/dc.log.diverged_load20fF`<br>md5 **`487db272df7401c1c4e107c0f01b11de`** | `~/adc_rtl_synth/wstore2_only/out_noload/dc.log`<br>md5 **`715ff346597e6efbe93ee1130c1aa6ca`** |

**两次运行的脚本快照 md5 完全相同** —— 所以差异只有 `--load` 一个。

#### 四个数 + 结论

| 量 | 发散（`--load 0.02`） | 收敛（`--load 0`） |
|:---|:---|:---|
| 进度表末行（原文） | `0:27:36 463465.4 120.61 7386593.5 5143391779.4` | `0:28:32 276362.0 0.00 −292.4 0.5` |
| 面积 | **463 465.4 µm²** 且仍在涨 | **274 205.081086 µm²**（`status.txt`），中间还微降 |
| 面积膨胀速率 | **+757 µm²/s**（443 020.0→463 465.4，0:27:09→0:27:36） | 无膨胀，末段面积**下降**（276 396→276 362） |
| `WORST NEG SLACK` | **120.61 ns，卡住不动** | **0.00**（真逻辑不可能 >1000 ns，见下） |
| `DESIGN RULE COST` | **5 143 391 779.4** | **0.5** |
| 结果 | **发散**：27 min 无报告、无 `status.txt`，主动停 | `DC_STATUS=OK`，`WNS=+5.61811`，`CELLS=282165` |

收敛那次的其余实测（`qor.rpt` / `timing.rpt`）：
`Sequential Cell Count = 61344`（**正好 `18×71×48`**）、组合 220 821、buf/inv 56 490、
**`Max Trans / Max Cap Violations = 0 / 0`**、`Levels of Logic 119`、关键路径
**`w_q_reg[16][32][0]` → `w_q_reg[7][5][18]`**（寄存器→寄存器，走写侧运行和
`sum_all`/`sum_excl`/`sum_new` —— `dc.log` 里被展成 **1277 个 `DW01_add_*` 模型**），
4.18 ns / +5.62 ns 余量，compile 墙钟 1755.09 s。
⚠️ hold 违规 **61344 条**（每个权值寄存器一条，最差 −0.11 ns）—— 本流程不修 hold。

#### 原因与**可推广**的结论

`weight_store` 当顶层跑时，`w_q`（**61344 bit**）成了**顶层输出端口**，默认按芯片级
0.02 pF/bit 加载 = **1.23 nF 的伪负载**；而在真实顶层里 `w_q` 是**内部网**，
直接驱 `recon_core.w_rom`，**根本不存在这个负载**。

> **可推广**：把某个块**当顶层单独综合**时，**不要把芯片级输出负载加到它
> （在真实设计里）内部的输出上** —— 那个伪负载足以让综合**直接不收敛**。
> 这只对"分层跑"成立，与设计本身的时序无关。

#### ⚠️ 这条**不解释**顶层发散（禁止越界引用）

顶层 `sar20_digital_core` 的真实输出只有
`main_sw`1134 + `sub_sw`144 + `dither_sw`72 + `slice_sel`18 + `dout`20 ≈ **1400 bit**，
**没有 61344 bit 的输出**。所以"伪负载"是 `weight_store` 那一次**已受控证实**的原因，
但**不能**拿来当顶层发散的机理；顶层发散的机理**未证实**（见 §17.8）。

#### 证据链的一处限制（如实登记）

`status.txt` 里的 `LOAD_PF=` 字段是**后来**才加进 `run_dc.tcl` 的
（`233f0d93…` 版才有）。**本节这一对用的是 `72430a67…` 版，没有该字段**，所以
两次的 `--load` 值来自**启动命令**，而不是来自 `status.txt`。本节的证据链是
「**脚本快照 md5 相同 + 启动参数不同**」；顶层 C1/A 那一对用新版脚本，`LOAD_PF=`
是机器可读的（见 §17.8）。若要求"`LOAD_PF=` 直接可比"，需用新版脚本重跑这一对 ——
**本轮未跑**，登记在 §17.9。

### 17.7 `P_STAGES` 全集表（含补跑点与显式"未跑"）

四个已测点**同口径**：`COMPILE_MODE=compile`、10 ns、TT corner、零线负载、
`RTL_PARAMS_SHA = 39b12360…`；脚本快照
`run_dc.tcl md5 = 72430a67b5829c21e23d95ad9a8d318d`、
`run_synth.sh md5 = a4d2696bcd0d422dba02d6a0a36e462c`（`sweep2_recon` 目录**刻意未升级**，
以保证 P=1/4/5/7 四点同版）。所有数字均为**实测**，出处
`~/adc_rtl_synth/sweep2_recon/out_p<N>/{status.txt, area.rpt, timing.rpt, qor.rpt}`。

| `P_STAGES` | `N_CYC` | `RECON_LAT` | ≤16? | Total cell area（实测） | leaf cell（实测） | 最差 slack（实测） | 关键路径终点（实测） | 逻辑级数（实测） | 状态 |
|:--:|--:|--:|:--:|--:|--:|--:|:---|--:|:---|
| 1 | 63 | 65 | ✗ | 312 489.268366 | 492 504 | +1.83 ns | `inj_r_reg[63]` → `ovf_pend_reg` | 140 | rc=0¹ |
| 4 | 16 | 18 | **✗ 不可行** | 313 859.994355 | 496 027 | +1.38771 ns | `u_div/cnt_reg[2]` → `u_div/q_reg[62]` | 204 | rc=0 |
| **5** | **13** | **15** | **✓（仅余 1 拍）** | **314 287.176352** | **497 053** | **+0.0021944 ns** | **`u_div/cnt_reg[1]` → `u_div/q_reg[62]`** | **260** | rc=0 |
| 7 | 9 | 11 | ✓（余 5 拍） | 315 678.874344 | 500 997 | +0.00229263 ns | `u_div/dv_reg[62]` → `u_div/q_reg[62]` | 283 | rc=0 |
| 6 | 11 | 13 | ✓（余 3 拍） | **未跑** | — | — | — | — | 未跑 |
| 14 | 5 | 7 | ✓ | **未跑** | — | — | — | — | 未跑 |
| 21 | 3 | 5 | ✓ | **未跑** | — | — | — | — | 未跑 |
| 63 | 1 | 3 | ✓ | **未跑** | — | — | — | — | 未跑 |

¹ P=1 那一行**没有 `status.txt`**（它在报告全部写完之后卡在
`get_timing_paths -nworst 1000000` 上 13 分钟不返回，被停掉 —— 见 §17.6 前文与 §15.1b）。
该行数字全部从 `area.rpt` / `timing.rpt` / `qor.rpt` 直读。

#### 从这张表能读出的三件事

1. **瓶颈归属随 `P_STAGES` 换路**（这段取代 §17.3 的 P6 推论）：
   * `P=1`：瓶颈在 `recon_core` 自己的组合算术（130 位乘法 + 5 个量级比较器 → `ovf_pend_reg`）；
   * `P ≥ 4`：瓶颈搬进 `div_floor`（P=4 起终点是 `cnt_reg[2] → q_reg[62]`，P=5/7 是 `cnt/dv_reg → q_reg[62]`）。
   * 逻辑级数单调：**140（P=1）→ 204（P=4）→ 260（P=5）→ 283（P=7）**；
     粗算 **每多一级级联 ≈ +28 级逻辑 ≈ +0.35 ns**（P=4→P=5 的 +56 级 ≈ 余量少掉的 1.39 ns，与这个斜率一致）。
2. **面积随 `P` 单调上升但幅度很小**：312 489 → 313 860 → 314 287 → 315 679，
   即 P=1 → P=7 只 **+1.02%**。
3. **在可行集合 {5, 6, 7} 内，10 ns 的时序余量不是区分项**：
   P=5 与 P=7 的最差 slack 都是 **+0.0022 / +0.0023 ns**（都贴着 0，`qor.rpt` 的
   `Critical Path Slack` 都是 `0.00`）—— 也就是 **DC 在两个点上都把设计刚好压到 10 ns**。
   真正的区分是**调度余量**：P=5 的 `RECON_LAT=15` 只余 1 拍，P=7 的 `RECON_LAT=11` 余 5 拍。
   → **取 P=7（生产现值）是对的**；如果哪天调度上能接受只余 1 拍，P=5 能省 0.44% 面积。
   **这不是"时序换吞吐"，是"面积换调度余量"。**

#### 未跑项（明确登记，不是漏跑）

| 点 | 为什么不跑 |
|:---|:---|
| `P=6` | 可行集合 {5,6,7} 的中间点；`RECON_LAT=13`（余 3 拍）。P=5 与 P=7 已把该集合的两端钉住，且两端的 slack 都是 `≈+0.002 ns`，中间点不跨任何边界 |
| `P=14` / `P=21` | 落在 (7, 63) 之间、不跨任何边界，边际信息最低 |
| `P=63` | 极端（一次迭代、63 级级联）。**实测过一半就停**：63 级级联把 DesignWare 模型撑到上千个（`dc.log` 里出现 `DW01_add_1251` 这种编号），39 分钟仍在建模型、没进映射；中途日志存 `out_p63/dc.log.stopped_for_top` |
| `compile_ultra` 口径的 `P_STAGES` | 本流程只做了 `compile` 口径。`compile` 与 `compile_ultra` **不可直接比**（§17.4 第 2 条） |
| `hold` 修复 | 本流程**不修 hold**（`compile` / `compile_ultra -no_autoungroup` 默认不修）。各行 `qor.rpt` 的 hold 违规 735~743 条、最差 −0.12 ns，**一律不参与判读** |
| 其它 corner | 只有 TT。SS/FF 未跑 |

### 17.8 顶层 `sar20_digital_core` 平铺综合：**本环境不可得**（机理未证实）

#### 结论（先说结论，再说证据）

> **顶层 `sar20_digital_core` 的面积/时序数字：本环境不可得。**
> "本环境" = 这台 22 GB 的 VM + `W-2024.09-SP3` + TT corner + 零线负载 + 本流程脚本
> （`synth/run_dc.tcl` / `run_synth.sh`，md5 见下）。
> **发散机理：未证实** —— 已排除 `--load`（伪负载）与 `-no_design_rule`（DRC 修复）两项，
> 其余候选没有证据，故**不写机理**。
> **能给的顶层结论只有 §5.1.1 的分层下界**，且必须保持"**实测相加的分层下界，不是顶层实测**"这个措辞。

两次独立的可行性尝试都失败：
* `compile_ultra`：101 分钟仍在 `Pass 1 Mapping`（`weight_store` 一处 70 min），**零 report**；
  `elaborate` 阶段 `hierarchical_cells = 110636`。
* `compile`（非 ultra）：**按预设判据判为发散**（下详）。

#### 单变量对照：C1 / A / B（三组同一版脚本）

三组**全部**用 `run_dc.snapshot.tcl` md5 = **`233f0d93a03c26ac31f7c693d9510a48`**
（`run_synth.sh` = `5a65a314fcb2b2ddf0cfd1661d544fdc`），同一 RTL、同一时钟 10 ns、同一 corner。

| 组 | 参数 | 与 C1 的唯一差异 |
|:---|:---|:---|
| **C1**（控制） | `--load 0.02`（=默认），DRF=1 | — |
| **A** | `--load 0` | 只有输出负载 |
| **B** | `--load 0.02 --no-design-rule` | 只有 DRC 修复开关 |

`-no_design_rule` **确实传进了 `compile`**（这一步专门验证过，否则 B 就是空实验）：

| 组 | `DC_INFO: compile args` | `DC_PHASE` |
|:---|:---|:---|
| C1 | `''` | `compile begin (mode=compile design_rule_fix=1)` |
| A | `''` | `compile begin (mode=compile design_rule_fix=1)` |
| **B** | **`'-no_design_rule'`** | **`compile begin (mode=compile design_rule_fix=0)`** |

⚠️ 但"传进去了"只证明**开关被 DC 接受**（无 option 报错），**证不出**它改变了 DC 内部行为。
所以 B 的结论是**条件式**的："在指定 `-no_design_rule` 的情况下仍然发散"，
不是"已证明 DRC 修复不是成因"。

#### 三组四数（全部为实测，来自各自的 `dc.log` 优化进度表末行）

| 量 | C1 | A | **B** |
|:---|---:|---:|---:|
| 面积（触发时） | 1 060 508.5 | 1 051 317.4 | **1 060 508.5** |
| `WORST NEG SLACK` | 4 235 941.00 | 4 235 906.50 | **4 235 941.00** |
| `setup_cost` | 260 356 538 368.0 | 260 354 392 064.0 | **260 356 538 368.0** |
| `DESIGN RULE COST` | 18 690 186 211.8 | 18 600 113 139.4 | **18 690 186 211.8** |
| 触发时刻 | 0:63:21 | 0:54:04 | 0:53:41 |
| `dc.log` md5 | `7613473295d6d27afee1cd2e61479e0c` | `9de6c27ef256adc3c1f253fdc57d6a91` | `74729cdd6d0aa4625c0ee102cf298d9b` |
| 判定 | 发散 | 发散 | 发散 |

**三条读法（每条只说到证据支持的那一步）**：

1. **`--load` 不是成因**：A 与 C1 只差输出负载，四数差异 <1%
   （slack 差 34.5 ns / 4.2e6 ns），发散轨迹不变。
   → 这条同时把 §17.6 的**伪负载结论限制在 `weight_store` 单模块**，**禁止**拿它解释顶层。
2. **`-no_design_rule` 也救不了**：B 与 C1 的四个数**逐位相同**。
3. **机理未证实**：两项排除后其余候选无证据。

#### 判据（预先声明，且**按判据判为发散**，不是"已验证发散"）

判据在**用之前**已在真实日志上自测：
`(WORST NEG SLACK > 1000 ns) || (DESIGN RULE COST > 1e8)` ——
两个真实发散日志都触发（`4 534.95 / 1.27e10`、`120.61 / 5.14e9`），
收敛日志不触发（`0.00 / 1.54e6`）。阈值刻意**不使用**"面积单调膨胀"，
因为收敛那次的中间面积会反向（`807 919 → 811 479 → 最终 315 678`）。

三组的触发原文（各自 `dc.log` 的最后一行进度表）：
```
C1:     0:63:21 1060508.5 4235941.00 260356538368.0 18690186211.8
A:      0:54:04 1051317.4 4235906.50 260354392064.0 18600113139.4
B:      0:53:41 1060508.5 4235941.00 260356538368.0 18690186211.8
```
`WORST NEG SLACK ≈ 4.2e6 ns ≈ 4.2 ms` 在真逻辑里不可能出现，所以按判据判为发散；
**但这不等于"已证明是组合环"** —— 本文件不写这个结论。

#### 与 A/B 无关的一条操作事实（保留，别丢）

平铺顶层两次尝试**都没有 `status.txt`**（一次超时、一次按判据杀掉），
所以顶层**没有**任何机器可读的 `status.txt` 字段；
上表四数全部从 `dc.log` 的进度表原文抄下来。

---

## 18. 交付风险与待决事项（必须由仓库负责人决定，不在实现方权限内）

### 18.1 版本控制状态（2026-09-19 更新）

截至 `df0a575`，`rtl/`、`sim/`、`docs/rtl/`、导出器、测试与 `synth/` 已受 Git 跟踪。
早期“全部未跟踪”的观察是历史状态，不能继续作为当前风险。新修改需连同测试、
参数/向量漂移门禁和对应源码 SHA 交付；本轮配置接口变更见
[ADR 0016](../adr/0016-rtl-configuration-and-dither.md)。

**分工说明（三件事不重叠，别混）**：

| 手段 | 治什么 | 不治什么 |
|:---|:---|:---|
| 版本控制（commit/tag） | **阻止**产物漂移、可回退 | — |
| `tools/export_rtl_params.py --check` | 产物与源码不一致时**门禁变红** | 源码本身被改了（那是正常变更） |
| `status.txt` 里的 `RTL_PARAMS_SHA=` | 让"这次综合用的是哪版参数"**事后可追** | **不阻止**漂移 |

**待决**：是否把上述路径纳入版本控制、以什么粒度提交（是否单独为 RTL 子树开分支/PR）、
以及是否调整 `release` 相关的 tag 策略。**实现方不擅自 `add`/`commit`/`push`**
（与既有纪律一致："未被授权不强推 main、不改旧 tag"）。

### 18.2 注释里的数值：复核后要"更正 + 留痕"，不要静默改

本轮实例：`synth/smoke/top_smoke.sv` 的注释里写的 `DITHER_UNITS_RANGE = 0` 等四句**全部有误**
（实际 `4'd2`、`dither_rail` 是 `[3:0]`、无 undriven 警告、`unit_therm` 例化端口 1062 个）。
复核者选择**保留更正记录（附时间线与逐条证据）而不是静默改掉** —— 这是对的：
**"注释里的参数值没人复核"本身就是一个独立风险点**，静默改掉会让这条风险无痕消失。


## 19. 2026-09-19 复核补充契约

配置装载完整性、写/提交仲裁、撤销在途样本、错误码 6 以及控制写串行器忙态的
当前定义以 [ADR 0016](../adr/0016-rtl-configuration-and-dither.md) 为准，覆盖上文早期冻结接口。
本次改动需重新综合；§17 的历史面积/时序不能自动归属于修复版本。
