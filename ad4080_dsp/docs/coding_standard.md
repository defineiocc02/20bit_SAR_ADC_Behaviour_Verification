# AD4080 数字子系统 RTL 编码与注释规范

> 版本 1.0 · 2026-10-19 · 适用于 `ad4080_rtl/rtl/`、`ad4080_rtl/sim/`
>
> 本规范参考并裁剪自以下工业实践：
> - Google / CHIPS Alliance **Verible SystemVerilog Style Guide**（lint 规则的事实标准）
> - **systemverilog.io Style Guide**（PEP8 风格的注释与排版约定）
> - **Doxygen** 注释标签（`@file/@brief/@details/@param/@note`）
> - IEEE 1800-2017 可综合子集
> - 业界常见的 IP 交付检查单（Lint / CDC / RDC / 综合 / DFT 五项门禁）

---

## 1. 强制工具门禁

任何 RTL 提交前必须通过：

```bash
bash scripts/lint.sh
```

该脚本执行三件事，任一失败即返回非零：

1. `verible-verilog-lint`：按 `.verible.rules` 检查（语法 + 风格 + 易错结构）
2. `verible-verilog-format --verify`：格式必须与格式化器一致（幂等）
3. 文件头完整性检查：每个 `.sv` 必须含 `@file`、`@brief`、`@details`、
   `@author`、`@date`、`@version`、`手册依据`、`时钟域`、`复位`、`可综合性`

规则文件 `.verible.rules` 与脚本 `scripts/lint.sh` 随代码入库，**不允许**
在本地用 `--rules=-all` 绕过。确实需要豁免时，必须在该行写
`// verilog_lint: waive <rule-name>` 并在上一行写 `// WAIVER: <理由>`。

---

## 2. 命名

| 对象 | 规则 | 示例 |
|---|---|---|
| 文件 | `lower_snake_case.sv`，与模块名一致 | `sinc5_decim.sv` |
| 模块 | `lower_snake_case` | `fifo_capture` |
| 参数 | `UPPER_SNAKE_CASE` | `IN_W`, `FIFO_DEPTH` |
| 信号/端口 | `lower_snake_case` | `chain_valid` |
| 寄存器 | 后缀 `_r`（可选，模块内统一即可） | `gain_r` |
| 低有效 | 后缀 `_n` | `rst_n`, `cs_n` |
| 时钟 | `clk` / `clk_<domain>` | `clk` |
| 复位 | `rst_n`（异步、低有效） | `rst_n` |
| 常量 | `localparam` + `UPPER_SNAKE_CASE` | `ACC_W` |
| 实例 | `u_<功能>` | `u_regs`, `u_spi` |

禁止：`reg`、`wire` 用于内部信号（统一 `logic`）；隐式位宽；魔法数字
（必须给 `localparam` 或注释来源）。

---

## 3. 文件头（每个 `.sv` 必须有）

```systemverilog
//=============================================================================
// @file    <文件名>
// @brief   <一句话功能>
// @details <2-5 行：结构、算法、关键取舍>
// @project AD4080 数字子系统
// @author  AD4080 Digital Team
// @date    YYYY-MM-DD
// @version X.Y.Z
//-----------------------------------------------------------------------------
// 手册依据 : AD4080 Rev.B p.xx Table yy（没有手册依据就写"无，设计约定"）
// 时钟域   : clk（单时钟）/ 其他
// 复位     : rst_n 异步低有效
// 延迟     : <输入到输出的时钟数>
// 吞吐     : <每拍/每 N 拍一个样本>
// 可综合性 : 是 / 否（原因）
// 依赖     : <被例化的模块>
// 修订记录 :
//   1.0.0 YYYY-MM-DD 初版
//=============================================================================
```

字段缺一不可。**没有手册依据的实现必须显式标注** `[设计约定]` 或
`[假设]`，不能伪装成手册要求。

---

## 4. 模块头与端口注释

```systemverilog
module foo #(
    parameter int IN_W = 20,     // 输入位宽，bit；合法范围 8..24
    parameter int DEPTH = 16384  // FIFO 深度，必须是 2 的幂
) (
    input  logic             clk,      // 系统时钟，100 MHz
    input  logic             rst_n,    // 异步复位，低有效
    input  logic [IN_W-1:0]  x,        // 输入样本，二进制补码
    output logic [IN_W-1:0]  y,        // 输出样本，二进制补码
    output logic             valid     // 单拍有效，与 y 同拍
);
```

端口注释必须写清：**方向已由语法表达，注释只写语义**（编码格式、单位、
有效电平、时序关系、取值范围）。不要写 `// 输入` 这种同义反复。

---

## 5. 注释标签体系（正文强制使用）

| 标签 | 用途 | 示例 |
|---|---|---|
| `NOTE:` | 设计意图、非显然的取舍 | `// NOTE: 这里故意不复位存储器内容` |
| `ASSUMPTION:` | 未从手册/仿真确认的假设 | `// ASSUMPTION: 主机两次访问间隔 > 1 us` |
| `SYNTH:` | 综合约束/推断说明 | `// SYNTH: 该数组应推断为单口 SRAM` |
| `CDC:` | 跨时钟域处理 | `// CDC: 2FF 同步，源时钟 < clk/8` |
| `LATENCY:` | 关键路径延迟 | `// LATENCY: valid_in -> valid_out = 2 clk` |
| `TODO:` | 已知未实现 | `// TODO(P2): 支持 SHORT_INSTRUCTION` |
| `FIXME:` | 已知缺陷 | `// FIXME: D>=128 时舍入常数溢出` |
| `WAIVER:` | lint 豁免理由 | `// WAIVER: 位宽由参数保证，工具无法推导` |

规则：
- 注释解释 **为什么**，不复述 **做了什么**（代码已经说明）。
- 注释是完整句子，中英文均可，但同一文件内保持一致。
- 行内注释只用于端口/参数/常量声明；逻辑块用上方独立注释。
- 禁止 `//*****`、`//////////` 之类的装饰性围栏；分节用
  `// --- 小节名 ---`。
- 修改代码必须同步修改注释；过期注释视为缺陷。

---

## 6. 编码禁用项（会被 lint 或评审拒绝）

1. **推断锁存器**：`always_comb` 的所有输出必须在所有分支赋值，或在块首赋默认值。
2. **不完整 `case`**：必须有 `default`；状态机例外需用 `unique case` 并配套 `default`。
3. **时序块用阻塞赋值**：`always_ff` 只能用 `<=`；`always_comb` 只能用 `=`。
4. **组合环**：禁止 `assign a = b; assign b = a;` 或 always_comb 互相依赖。
5. **隐式位宽/符号**：跨位宽赋值必须显式扩展或截断；有符号运算必须
   `$signed()`，禁止依赖工具默认。
6. **多驱动**：同一信号只能在一个 `always` 或一个 `assign` 中驱动。
7. **门控时钟**：RTL 内不产生门控时钟；用 clock-enable。
8. **异步信号直接进逻辑**：必须先 2FF 同步（`// CDC:` 标注）。
9. **`initial` 赋初值用于可综合逻辑**：只允许用于参数检查/仿真断言。
10. **`X` 传播依赖**：不允许用 `casex`。

---

## 7. 时钟、复位、CDC

- 全设计单时钟 `clk`；SPI 的 `sclk/cs_n` 与 GPIO、CNV 均为异步输入，
  一律 2FF 同步后使用，并在注释标 `// CDC:`。
- 复位统一 **异步置位、低有效**（`always_ff @(posedge clk or negedge rst_n)`）。
  真实流片需在顶层加"异步复位同步释放"，已在 `docs/synthesis_review.md` 登记。
- 存储器阵列（FIFO RAM）**不复位内容**，只复位指针与标志；注释说明原因。
- 跨时钟域不得传递多 bit 总线（除非格雷码/握手）；本设计无跨域总线。

---

## 8. 定点数约定（本项目专用）

| 对象 | 格式 | 舍入 | 溢出 |
|---|---|---|---|
| ADC 结果 | 20-bit 二进制补码 | — | 饱和并置 clip 标志 |
| 滤波器累加 | 有符号，位宽 = 输入 + 增益位数 | `(x + 2^(S-1)) >>> S` | 输出饱和 |
| GAIN 系数 | Q0.24，K=512 | 同上 | — |
| 补偿 FIR | Q1.15 | 同上 | 输出饱和 |
| 阈值 | 12-bit 补码，LSB=1.46484 mV=256 ADC LSB | — | — |

任何定点运算必须在注释中写清：**输入格式、输出格式、舍入方式、饱和策略**。

---

## 9. 验证钩子

- 每个模块的 `valid_in/valid_out` 必须能仅凭时序图复核；模块头写清延迟。
- 关键不变量用注释列出（如 `INV-F1: count ∈ [0, DEPTH]`），并在 TB 中检查。
- 可综合代码内不放 `$display`；调试用 `` `ifdef SIMULATION `` 包裹。

---

## 10. 评审检查单（提交前逐条打勾）

- [ ] 文件头 10 个字段齐全，手册页码/表号可查
- [ ] 每个端口有语义注释；每个参数有范围/单位
- [ ] 每个 `always_ff` 上方注明时钟域、复位、功能
- [ ] 每个 `always_comb` 有默认赋值，无 latch
- [ ] 所有 `case` 有 `default`
- [ ] 有符号/位宽扩展显式
- [ ] 假设、未实现、豁免均有标签
- [ ] `bash scripts/lint.sh` 通过
- [ ] TB 覆盖新增行为（或登记未覆盖原因）

---

## 11. 与 Agent 协同开发的约定

本仓库的 RTL 由"人 + 多 Agent"协同产出，为避免 Agent 常见问题，额外约定：

1. **单一事实来源**：手册页码、寄存器地址、位域只能来自
   `docs/ad4080_traceability.md`；Agent 不得凭记忆补全。
2. **先审查后改码**：每个 Agent 的产出是 `docs/review_*.md`，主设计者
   逐条裁决后再改 RTL；审查报告与代码改动必须能一一对应。
3. **禁止"看起来对"**：没有仿真器时必须如实写"未运行仿真"，并用
   `verible-verilog-lint` 做语法门禁。
4. **改动可回溯**：每个 commit 只做一件事；提交信息写清"哪条差异→哪个文件"。
5. **Agent 不碰冻结接口**：顶层端口清单冻结后，任何改动先改文档再改代码。
