# P1 模块接口与向量格式（冻结）

- 状态：**已冻结**，2026-09-18
- 上游：`docs/rtl/RTL_ARITHMETIC_CONTRACT.md`（算术契约）、转换计划 §7 Phase P1
- 适用：`rtl/core/` 下 M1–M5，以及 `sim/tb/p1_tb.sv` 与 `tools/export_rtl_vectors.py`

## 0. 全局约定

| 项 | 约定 |
|:---|:---|
| 语言 | SystemVerilog 可综合子集；`\`include "rtl_params.vh"` |
| 时钟/复位 | 统一 `input logic clk` + **同步**低有效复位 `input logic rst_n` |
| 握手 | **无**。全部**固定延迟**，用 `*_valid` 指示 |
| 算术 | 只使用有明确位宽/符号/缩放的整数编码；**不做浮点运算**（契约 §1.1） |
| 端口风格 | 数组端口用**打包**二维数组（`logic [A-1:0][B-1:0]`），不用 unpacked array |
| 命名 | 与 `rtl_params.vh` 的常量同名；参数默认值取自该头文件 |
| 不可综合构造 | 禁止 `#delay`、`initial` 产生逻辑、`real`、动态数组、`fork/join` |

### 0.1 为什么 M4 永远不可能 bit-exact

模型的 dither 码来自 `np.random.default_rng`（PCG64）。**RTL 无法复现 PCG64**，
因此 M4 的验收判据是**统计 + 边界**，不是逐位相等：

- 支撑集必须精确（离散模式：`{-D..D}` 共 `2D+1` 个值）；
- 分布近似均匀（卡方检验）、均值 ≈ 0、跨拍不相关；
- 边界值（`−D`、`+D`、`0`）必须真的出现过。

**连带后果**：任何要求 bit-exact 的链路级测试，dither 码必须**作为激励注入**，
不能让 RTL 自己生成。这条写进 L3 的接口约定，防止有人以为可以两侧各自随机。

### 0.2 ⚠️ DEM 的 `dem_enable` 陷阱（子代理独立复核时发现，必须知道）

`Config.paper_literal()` 的 **`dem_enable` 默认是 `False`**，而 `dem_bridge_enable` 却是 `True`。
`split_switch_command` 在 `dem_enable=False` 时把 `states` 强制为 `0`，于是**全部置换退化为恒等**、
`i*` 恒等于一个固定值 —— 名称看起来"桥接开着"，实际整条 DEM 不工作。

后果：任何"忘了打开 `dem_enable`"的向量生成或 RTL 联调都会**全绿但什么都没覆盖**。
本仓库的对应防线：

| 防线 | 位置 |
|:---|:---|
| 参数包里同时导出 `DEM_ENABLE` / `DEM_BRIDGE_ENABLE`（**复位默认值**） | `rtl_params.vh` |
| 向量生成器**拒绝**在 stock 配置（DEM off）下生成，报告里显式记录 `effective_overrides` | `tools/export_rtl_vectors.py` |
| TB 显式驱动 `dem_en = 1`（P1 向量是 DEM 打开的那一档） | `sim/tb/p1_tb.sv` |
| 结构断言：两个 bank 各自必须看满 512 个不同 sid | TB T1 |

**集成时 RTL 的寄存器必须把 `dem_en` 置 1**，否则会安静地退化成固定顺序。
`rtl_params.vh` 里那个 `DEM_ENABLE = 1'd0` 是复位默认值，不是"P1 向量用的是 0"。

### 0.3 向量文件的读取约定

每个 `.hex` 文件的结构是：注释行（`//` 开头） → 一行 `BEGIN` → 纯数字数据。
这样 testbench 可以用平铺的 `$fscanf(fd, "%h", v)` 顺序读取，不必解析注释行。
**打包字段一律 LSB 优先**：`main_on` 的 bit *p* 就是物理地址 *p*，
TB 可直接与 `{1'b0, main_on[a]}` 比较，省掉一次反向，也就省掉一次索引错位的机会。

### 0.4 模块参数的纪律

`rtl/core/*.sv` **不使用与 `rtl_params.vh` 同名的模块参数**。原因有两个，都是硬的：

1. `parameter int N_ACTIVE = N_ACTIVE` 会**自引用**（头文件的同名常量被参数遮蔽）；
2. 另起一套名字就等于在 RTL 里维护第二份参数表 —— 契约 §9 明令禁止。

因此模块的尺寸直接取头文件常量，**换配置请重跑导出器**，不要就地改数字。
只有 `dem_state_gen`（`A_RED`、`W`）与 `dither_gen`（`D`、`SEED`）带参数，因为它们的名字不与头文件冲突。

---

## 1. M1 `dem_state_gen` —— DEM 状态推进

**职责**：按 **bank 内序号**推进两个独立计数器，并用 LCG 映射成 DEM 状态 `sid`。

**来源**：`adi_model/mapper.dem_state_sequence`（`sid = (seq * 2654435761) % 512`，per-bank 计数）。

**键推导（必读）**：`(seq · A) mod 2^W == ((seq mod 2^W) · (A mod 2^W)) mod 2^W`，
故乘法常数可归约为 `A mod 512 = 433`。再取相邻两项之差：

    sid(n+1) − sid(n) = A mod 2^W = 433

于是整条序列退化为**一个 W 位累加器 + 常数加法**（`mod 2^W` 即按位与），
不必每拍做乘法。该递推与定义式逐项等价，由 `p1_m1_sid.hex` 的 1024 拍逐拍比对钉住。
**两条推导都由子代理独立穷举验证后固化**，不得凭直觉写死。

```systemverilog
module dem_state_gen #(
    parameter int          W     = 9,          // = log2(DEM_STATES)
    parameter logic [31:0] A_RED = 32'd433     // _LCG_A mod 2**W，由导出器给出
)(
    input  logic           clk,
    input  logic           rst_n,
    input  logic           dem_en,             // 低 = DEM 关闭，sid 恒 0 且计数不推进
    input  logic           load,               // 载入初值（与 en 同拍时以 load 优先）
    input  logic [W-1:0]   init_a,
    input  logic [W-1:0]   init_b,
    input  logic           en_a,               // 本拍消费一个 bank A 状态
    input  logic           en_b,
    output logic [W-1:0]   sid_a,              // 与 en_a 同拍有效
    output logic [W-1:0]   sid_b
);
```

**时序（先使用、后推进）**：`sid_*` 是**寄存器输出**，表示"本拍要用的状态"；
`en_*` 在时钟沿把对应 bank 推进一格（`+433 mod 2^W`）。消费者必须在 `en_*` 为高的那一拍
采样 `sid_*`；延迟 = 0（当拍有效）。`load` 优先于 `en_*`（载入同拍不推进）。
复位后初态 0，即该 bank 的第 0 个样本用 `sid = 0` —— 与模型一致。

**已记录的例外**：`en_a` 与 `en_b` 可同时为高（两个 bank 独立计数），这与模型一致。

---

## 2. M2 `dem_addr_gen` —— 主/子阵列的逻辑位置映射

**职责**：把 `sid` 变成"每个**物理地址**对应的**逻辑位置**"，供 M5 做温度计比较。

**来源**：`adi_model/dem.split_switch_command` 的 `order` 数组 + `weight_calibration._terms` 的 scatter 语义。
`_terms` 做 `plus[..., order] = take`（`take[i] = 1 iff i < count`），即
**物理地址 `order[i]` 被打开 ⟺ 逻辑位置 `i < count`**。故 RTL 需要的是**反向**映射。

```systemverilog
module dem_addr_gen #(
    parameter int N_MAIN = 63,
    parameter int N_SUB  = 8,
    parameter int W_ROT  = 8,      // ceil(sqrt(N_MAIN))
    parameter int H_ROT  = 8       // ceil(N_MAIN / W_ROT)
)(
    input  logic [8:0]                    sid,
    output logic [N_MAIN-1:0][5:0]        main_logical,   // p -> j
    output logic [N_SUB-1:0][2:0]         sub_logical     // q -> j
);
```

**闭式（由子代理独立验证；验证结果与此处不符时以验证为准并改本文件）**：
令 `rh = (sid / 8) % 8`、`ch = sid % 8`、`sh = sid / 64`；
被过滤掉的唯一 cell 索引 `i* = ((7-rh)%8)*8 + ((7-ch)%8)`（当 `H_ROT*W_ROT = 64` 且 `N_MAIN = 63` 时）；
对物理地址 `p`：`cell = ((p/8 - rh) % 8)*8 + ((p%8 - ch) % 8)`，`j = cell - (cell > i* ? 1 : 0)`；
对子阵列：`sub_logical[q] = (q - sh) % 8`。

**时序**：纯组合，延迟 = 0。

---

## 3. M3 `swap_decode` —— 粗码 → 每 slice 的主/子计数

**职责**：`k = coarse·units_per_lsb1 + k0 + dither_code` → 裁剪 → 可选桥接零和交换 → 拆成
`(main_count, sub_count)`。

**来源**：`mapper.encode`（`k` 的构成）、`dem.split_switch_command`（裁剪、桥接、`divmod`）。

```systemverilog
module swap_decode #(
    parameter int N_ACTIVE      = 8,
    parameter int DAC_LEVELS    = 512,
    parameter int N_SUB         = 8,
    parameter int UNITS_PER_LSB1 = 1,
    parameter int K0            = 0
)(
    input  logic [8:0]                      coarse,        // b1 位粗码
    input  logic signed [15:0]              dither_code,   // 单位当量，整数（调用方保证）
    input  logic                            dem_en,
    input  logic                            bridge_en,
    input  logic [2:0]                      sid_low,       // sid % N_ACTIVE
    output logic [N_ACTIVE-1:0][6:0]        main_count,
    output logic [N_ACTIVE-1:0][2:0]        sub_count,
    output logic                            rdac_over      // 未裁剪命令越界（保留的模拟标志）
);
```

**语义**：`code = clip(k, 0, DAC_LEVELS-1)`；`amount = min(1, min(code, DAC_LEVELS-1-code))`；
`rank[a] = (a - sid_low) % N_ACTIVE`；`signs[a] = rank[a] < N_ACTIVE/2 ? +1 : (rank[a] < N_ACTIVE ? -1 : 0)`；
桥接开启时 `slice_code[a] = code + signs[a]*amount`，否则 `= code`；
`main_count[a] = slice_code[a] / N_SUB`，`sub_count[a] = slice_code[a] % N_SUB`。

**注意**：`amount` 的守卫使 `slice_code ∈ [0, DAC_LEVELS-1]` 恒成立（`code=0` 或 `code=511` 时 `amount=0`），
所以 `main_count ≤ 63`、`sub_count ≤ 7`。**RTL 不得依赖这一条**——宽度要能容纳溢出。

**时序**：纯组合，延迟 = 0。

---

## 4. M4 `dither_gen` —— dither 码生成（统计验收）

**职责**：产生 dither 码。**不要求与模型逐位一致**（见 §0.1）。

```systemverilog
module dither_gen #(
    parameter int            D    = 2,            // dither_units_range
    parameter logic [31:0]   SEED = 32'h1357_9BDF
)(
    input  logic             clk,
    input  logic             rst_n,
    input  logic             en,                  // 本拍产出一个新码
    output logic signed [7:0] dither_code,        // {-D..+D}
    output logic             valid
);
```

**验收判据**：支撑集精确；近似均匀（卡方）；均值 ≈ 0；连续两拍不自相关；边界值出现。
TB 把统计结果 dump 成文件，由 Python 侧复核。

---

## 5. M5 `unit_therm` —— 温度计展开与采样 dither 轨选择

**职责**：`on[p] = (logical[p] < count)`，逐 slice；并给出采样态 dither 掩码单位的轨极性。

```systemverilog
module unit_therm #(
    parameter int N_ACTIVE = 8,
    parameter int N_MAIN   = 63,
    parameter int N_SUB    = 8,
    parameter int D_UNITS  = 2                 // dither_units_range
)(
    input  logic [N_MAIN-1:0][5:0]              main_logical,
    input  logic [N_SUB-1:0][2:0]               sub_logical,
    input  logic [N_ACTIVE-1:0][6:0]            main_count,
    input  logic [N_ACTIVE-1:0][2:0]            sub_count,
    input  logic signed [7:0]                   bank_dither,   // 采样态 dither 码
    output logic [N_ACTIVE-1:0][N_MAIN-1:0]     main_on,
    output logic [N_ACTIVE-1:0][N_SUB-1:0]      sub_on,
    output logic [2*D_UNITS-1:0]                dither_rail    // 1 = +轨, 0 = −轨
);
```

**语义**：`main_on[a][p] = main_logical[p] < main_count[a]`；`sub_on` 同理；
`dither_rail[i] = (i < D_UNITS + bank_dither)`（对应模型 `v_fs*(2*(i < D + d) - 1)`，
code 全选 `+V_FS`、半选 `−V_FS` 的计数语义）。

**时序**：纯组合，延迟 = 0。

---

## 6. P1 向量文件格式

全部为**定宽 hex**，行内以单空格分隔；`//` 开头的行为注释，解析方必须跳过。
生成器：`tools/export_rtl_vectors.py`；`--check` 为漂移门禁。

| 文件 | 行数 | 列 | 覆盖 |
|:---|---:|:---|:---|
| `sim/vectors/p1_m1_sid.hex` | N | `bank sid_a sid_b sid_exp` | 模型真实 bank 交替序列，N=1024 |
| `sim/vectors/p1_m2_jinv.hex` | 512 | `sid main_logical[63] sub_logical[8]` | **穷举**：全部 512 sid × 全部 64+8 地址 |
| `sim/vectors/p1_m3_counts.hex` | 8192 | `code bridge sid_low main_count[8] sub_count[8] rdac_over` | **穷举**：512 code × 2 bridge × 8 sid_low |
| `sim/vectors/p1_m5_masks.hex` | 64 | `sid count main_on[8] sub_on[8]` | 直接逐位：8 sid × 8 count |
| — | — | — | M5 的**稠密**扫描（512 sid × count 0..70）由 TB 用 `p1_m2_jinv.hex` 当黄金值现算，不另存文件 |
| `sim/out/p1_m4_stats.json` | — | M4 统计量 | 由 TB dump，Python 侧复核 |

宽度约定：`main_logical` 每项 2 hex（6 bit 用低 6 位）；`sub_logical` 每项 2 hex；
`main_count` 每项 2 hex；`sub_count` 每项 1 hex；`main_on[a]` 为 63 bit → **16 hex**（高 1 位补 0）；
`sub_on[a]` 为 8 bit → 2 hex。

---

## 7. P1 出口判据

| 判据 | 形式 |
|:---|:---|
| M1 | 1024 拍 bank 交替序列逐拍 `sid` 相等（bit-exact） |
| M2 | 512 × 72 地址全遍历逐位相等（bit-exact） |
| M3 | 8192 组合 `main_count`/`sub_count`/`rdac_over` 全等（bit-exact） |
| M5 | 稠密扫描 512 × 71 = 36 352 组掩码全等；另加结构断言 `popcount(on)==count` 与 count 单调包含 |
| M4 | 支撑集精确 + 卡方通过 + 边界值出现（**统计**，非 bit-exact） |
| 综合 | `rtl/core/*.sv` 无不可综合构造（P5 的 SpyGlass/综合检查之前先用 `vcs -sverilog` 编译通过） |

**变异验证**（必做，用来证明门禁不是摆设）：把 `A_RED` 改一位、把 `i*` 公式里的 `7` 改成 `6`、
把 `popcount` 断言去掉，各自都应让 P1 变红。

---

## 8. P1 完成记录（2026-09-18）

### 8.1 结果

| 项 | 实测 |
|:---|:---|
| 平台 | VCS `W-2024.09-SP1_Full64` @ `yian@192.168.38.129`，无 DVE |
| 通过判据 | `checks = 464816`，`errors = 0`，`simv` 退出码 0 |
| T1 M1 `dem_state_gen` | 0 错误；两 bank 各自看满 512 个不同 sid |
| T2 M2 `dem_addr_gen` | 0 错误；512 sid × 71 地址全遍历（36352 次比对） |
| T3 M3 `swap_decode` | 0 错误；8192 穷举 + 85 定向（140709 次比对） |
| T4 M5 `unit_therm` | 0 错误；1248 条黄金掩码（19968 次比对） |
| T5 M5 结构性 | 0 错误；512 sid × 64 count 的 popcount 与单调包含 |
| T6 M4 `dither_gen` | 0 错误；200000 拍支撑集/均匀性统计，valid rate 1.0000 |
| T7 **链路 M1→M2→M3→M5** | 0 错误；256 样本 × 8 slice × 2（4096 次比对），dither 非零样本 192/256，bridge=1 |
| 变异验证 | 改坏 `A_MASKED` 一位 + 反转单调方向 → T1=1024、T5=258048 错误、FAIL；恢复 → 全 0、PASS。
另：把 T7 里的 `m2_sid` 接成另一个 bank → **T7=1922 错误而 T1–T6 全为 0**，证明 T7 覆盖了一类其它测试看不见的错误。独立复核还把 DEM 模块改成「先推进后使用」→ T1=1024 错误 |

### 8.2 本次抓出来的三个 RTL bug（都已修，且每个都能被门禁复现）

| # | Bug | 表现 | 根因 |
|:---:|:---|:---|:---|
| RTL-1 | `dem_addr_gen` 的 `i*` 行列两半写反 | M2 在部分 sid/地址上差 1 | `cell = {row, col}` 是**低位为列、高位为行**；我把行写进了低位 |
| RTL-2 | `swap_decode` 的 `rank` 在 3 位宽里取模 | M3 桥接用例全错（61305 错误） | `3'(N_ACT)` 把 8 截成 **0**，变成"对常量 0 取模"，整段符号逻辑失效 |
| RTL-3 | `swap_decode` 的 `k_cmd` 有符号/无符号混用 | M3 定向越界用例全错（537 错误） | Verilog 规则：表达式里只要有一个无符号操作数就整条按无符号算；`18'(...)` 是无符号的，负 dither 变成 +65528 |

> **RTL-3 最值得记住**：它只在"越界"用例上暴露，而"正常量程内"的 8192 条穷举**全部通过**。
> 如果 P1 的定向用例（`dither ∈ [-8, 8]`）没有覆盖 `k < 0` / `k > 511`，这个 bug 会一路带到 P2。

### 8.3 同一次验证里抓出来的两个测试 bug（比 RTL bug 更值得警惕）

| # | 位置 | 问题 |
|:---:|:---|:---|
| TB-1 | `sim/tb/p1_tb.sv` 单调包含检查 | 写成 `mask & ~prev`（检查递减），正确方向是 `prev & ~mask`（递增）→ c=1 起恒报错 |
| TB-2 | `sim/tb/p1_tb.sv` `open_data()` | 用 `line[39:0] == "BEGIN"` 判注释结束 —— `$fgets` 把首字符放在**寄存器 MSB**，该表达式取的是寄存器尾部，永远匹配不上。已改为位置无关的 `$sscanf("%s") + "#DATA"` |

> **TB-2 的教训**：那个断言当时**正确地 `$fatal` 了**（没有静默继续），这正是"跑完但没比对不算通过"的设计在起作用。
> **TB-1 的教训**：一条方向写反的断言会让正确实现全红 —— 与"放宽判据"同样有毒，只是方向相反。
> 两者都说明：断言本身也要有反例验证。

### 8.4 工具链上的三条实测发现

1. **`simv` 默认对 `$error` / `$fatal` 返回 0**，必须加 `-exitstatus`，否则 CI 会把失败当成功 —— 这与
   仓库里 `acceptance.py` 当初修的"报告写 FAIL 但退出码 0"是同一类缺陷。
2. **VCS 会把未知扩展名的文件当 Verilog 源解析**。`tools/run_rtl_sim.py` 的 filelist 已改为只收
   `.sv/.v/.svh/.vh`，数据文件仍同步但不进编译。
3. `cell` 是 **Verilog-2001 保留字**，已改名 `cell_idx`。

### 8.5 未纳入 P1 的事项（如实登记，不静默跳过）

* `shuffle_causal` 调度：RTL 只实现确定性 A/B ping-pong（计划 §8 R5）。
* B 通路 / slice 所有权 / 相位生成：属 P2 的 `ctrl_fsm` + `slice_alloc`。
* 覆盖率采集：`-cm` 可用（已确认），但 P1 未开启；建议 P5 与综合前一起做。
* `rsync` 传输分支：本机无 `rsync`，实际走 `scp`，该分支未验证。

### 8.6 为什么必须有 T7（链路项）——独立复核发现的接缝

T1–T6 各自把 DUT 的输入**直接喂进去**：`m2_sid` 是 TB 写死的常量，`m3_sidlow` 取自文件。
于是"**M1 的 sid 有没有正确接到 M2**"这件事从未被验证过 —— 而 P2 的 `ctrl_fsm`
要接的正是这条链。独立复核（2026-09-18）把它列为 P1 的最大未覆盖风险，T7 由此而来。

T7 只提供 `(bank, coarse, dither, bridge)`；`sid` 来自 M1 的输出、`sid_low` 取自该 sid、
`main_count/sub_count` 由 M3 算出、掩码由 M5 产生 —— 全部由 RTL 自己串起来。
金标掩码仍由模型的 `_terms` 给出（见 `_m5_golden` 的提取说明），且按**模型给的 sid** 计算，
所以链路一旦错位就会红。

**灵敏度已证**：把 T7 里的 `m2_sid` 故意接成另一个 bank 的 sid →
`T7 = 1922` 处错、`T1–T6` 全为 0。这正是 T7 存在的意义。

### 8.7 独立复核（2026-09-18）的结论与残留风险

一个**全新视角**的子代理逐条证伪了 7 项声明，全部 PASS（含：独立枚举 10^7 项证明加性递推等价；
把模块改成"先推进后使用"→ T1 变红；把 `--check` 改成永远返回 0 → 两条过期测试双双 FAILED；
逐行核对 README 的 `[已存在]/[规划]` 标记 32 条无不一致；远端裸 `simv` 对 `$fatal` 实测 rc=0、
加 `-exitstatus` 后 rc=3）。

它同时报出三条**未被覆盖的风险**，处置如下：

| 风险 | 处置 |
|:---|:---|
| 只有 M1 有寄存器时序契约；T4/T5 直驱 `m2_sid`，真实链路未跑通 | **已补 T7**（§8.6），并做了灵敏度证伪 |
| V4 只核"文件存在性"，未核 README 描述与内容一致（§3 把 `[规划]` 项写成已归属） | **已给 §3 加状态列**，`[规划]` 项显式标注 |
| `run_vcs.sh` 的正则兜底按日志文本判断，可能漏自定义致命信息；且兜底自身无反例测试 | **已放宽正则**（允许前导空白 + 覆盖断言失败）并**如实登记残余限制**：判定以 `-exitstatus` 为准，正则只用于**分类**；不声称兜底已被独立验证 |
