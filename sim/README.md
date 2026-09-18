# `sim/` —— 仓库级 RTL 仿真子树

本目录**不属于** `rtl/`（可综合代码）。它放 testbench、独立参考实现、黄金向量与运行脚本。

| 子目录 | 内容 | 谁生成 |
|:---|:---|:---|
| `tb/` | RTL testbench（`p1_tb` / `p2_tb` / `p2_smoke_tb` / `p3_top_tb`） | 手写 |
| `ref/` | **自检型**独立参考实现（跑一遍打印 PASS/FAIL 并以退出码表达） | 手写 |
| `vectors/` | 黄金向量与元数据（`.hex` / `.json`） | `tools/export_rtl_params.py`、`tools/export_rtl_vectors.py` |
| `smoke/` | VCS 通路自检用的最小例子 | 手写 |
| `artifacts/` | 运行产物（编译中间件、日志、dump）—— **已 gitignore，不进仓库** | 运行生成 |

---

## 1. 怎么跑

### 1.1 前置（本机 Windows 主机每次都要设）

```bash
export PATH="/usr/bin:/bin:/mingw64/bin:/c/Windows/System32"
PY="C:/Users/Administrator/miniconda3/python.exe"     # python 不在 PATH 上
```

> **`scp` 被解析到 Windows OpenSSH 会让上传失败**：`tools/run_rtl_sim.py` 内部把本地路径
> 转成 MSYS 形式（`/d/...`），Windows 版 `scp` 不认。解法是把便携 Git 的 usr/bin 前置：
> ```bash
> export PATH="/c/Users/Administrator/.workbuddy/binaries/PortableGit/versions/1.2.0/usr/bin:$PATH"
> ```

### 1.2 各 testbench 的调用

```bash
# P1：纯数字组合/状态逻辑（462 万次比对中的 464 816 次）
"$PY" tools/run_rtl_sim.py --top p1_tb --src rtl/core rtl/params sim/vectors \
    --tb sim/tb/p1_tb.sv --incdir rtl/params \
    --sim-arg "+vdir=sim/vectors" "+outdir=." --fetch "p1_m4_hist.txt"

# P2：定点重构核 + 唯一除法器（9 项，含 2^20 全码 oracle）—— 约 4~6 分钟
"$PY" tools/run_rtl_sim.py --top p2_tb --src rtl/core rtl/params sim/vectors \
    --tb sim/tb/p2_tb.sv --incdir rtl/params \
    --sim-arg "+vdir=sim/vectors" --fresh

# P2 外设冒烟（slice_alloc / sadc_enc / 寄存器通路）
"$PY" tools/run_rtl_sim.py --top p2_smoke_tb --src rtl/core rtl/top rtl/params \
    --tb sim/tb/p2_smoke_tb.sv --incdir rtl/params --fresh

# P3：顶层端到端（511 样本 bit-exact）—— 见 sim/tb/p3_top_tb.sv 模块头里的 +injdith 等开关
"$PY" tools/run_rtl_sim.py --top p3_top_tb --src rtl/core rtl/top rtl/params sim/vectors \
    --tb sim/tb/p3_top_tb.sv --incdir rtl/params \
    --sim-arg "+vdir=sim/vectors" --fresh
```

**退出码**：`0` 通过 / `2` 编译失败 / `3` 仿真 `$fatal` / `5` 超时。判定以 `simv` 的退出码为准
（`run_vcs.sh` 已加 `-exitstatus`；**裸 `simv` 对 `$error`/`$fatal` 返回 0**）。

### 1.3 参考实现的自检

```bash
PYTHONPATH=src "$PY" sim/ref/dem_closed_form.py          # DEM 闭式变换，四命题
PYTHONPATH=src "$PY" sim/ref/recon_rtl_mirror.py         # recon_core 的位宽镜像，4 组配置
```
两者都以**退出码**表达结论：全部通过 `0`，任一失败非零。

---

## 2. 向量文件格式（所有 `.hex` 共用）

```
// 注释行（若干行）
#DATA
<纯数字数据，行内单空格分隔>
```

读法：**先跳过 `//` 与 `#DATA` 标记，再读数据**。判定标记必须用 `$sscanf(line, "%s", tok)` 取
token 再比字符串 —— **不要**用 `line[N-1:0] == "#DATA"`：`$fgets` 把首字符放在寄存器 **MSB**，
按位比较会取到寄存器尾部而永远匹配不上（P1 踩过）。

打包字段一律 **LSB = 地址 0**，便于 testbench 直接用 `{1'b0, bus[p]}` 与掩码比较。

### 2.1 两个反复踩的读取陷阱

| 陷阱 | 症状 | 正确做法 |
|:---|:---|:---|
| 窄 hex 列不做符号扩展 | `%h` 读进 64 位变量后 `$signed(v)` 把 `0xf0` 当成 **+240** 而非 −16 | 取低 N 位再扩：`$signed(v[7:0])` |
| `while (!$feof(fd)) { 读一行 }` | `$feof` 只在**读过界之后**才为真 → 最后一行后再读一次 → short read → `$fatal` | **先读本行第一个字段**，读不到即 EOF 并退出 |

### 2.2 期望值只能来自模型

所有 `.hex` 的期望列都由 `adi_model` 的 `FixedPointReconstructor` / `round_even_divide` 产出，
**不允许**在 TB 或导出器里按公式重写一遍 —— 那等于把"黄金"变成"第二份实现"。
唯一例外是 TB 侧的**定义式自检**（`p2_tb` 的 T2 用 `q·d ≤ a < (q+1)·d` 验除法器），
那是与模型无关的独立数学判据。

---

## 3. 重新生成向量

```bash
PYTHONPATH=src "$PY" tools/export_rtl_params.py  --config paper_literal --emit-stimulus 64
PYTHONPATH=src "$PY" tools/export_rtl_vectors.py --config paper_literal
# 漂移门禁：磁盘产物是否与当前源码一致（不一致返回 1）
PYTHONPATH=src "$PY" tools/export_rtl_params.py  --config paper_literal --check
PYTHONPATH=src "$PY" tools/export_rtl_vectors.py --config paper_literal --check
```

`--check` 是**两条独立的过期检测**：(a) 源码/配置改动后旧产物被判过期；(b) 产物被手工篡改后
被判不一致。两者都由 `tests/unit/test_rtl_export.py` / `test_rtl_vectors.py` 覆盖。

**单文件上限 512 KB**（pre-commit 限制）。2²⁰ 全码 oracle **不落文件**：
它的输入是循环下标本身，TB 自己循环并断言 `dout == adc2_code` 这条**性质**。

---

## 4. 覆盖账本与「门禁是否有牙」

| testbench | 覆盖 |
|:---|:---|
| `p1_tb` | M1–M5 逐个穷举 + **链路 T7**（只给最外层激励） |
| `p2_tb` | T1/T2 除法器、T3 `adc2_dec`、T4 延迟、**T5 2²⁰ 全码 oracle**、T6 掩码、T7 斜坡、T8/T8b 剪裁与溢出、**T9 模块级链路** |
| `p2_smoke_tb` | 寄存器通路、`slice_alloc` 的 INV-4a..4d、`sadc_enc` popcount |
| `p3_top_tb` | 顶层端到端 511 样本 bit-exact |

**写新用例时问一句**：这条断言**在什么输入下会红**？答不出来，它就没在测东西。
本仓库已有的"变红"证据（每条都在 `docs/rtl/` 里登记）：
改坏 LCG 常数、反转温度计单调方向、把 `--check` 改成永远返回 0、
把 `conv[n]` 接成与 `acq[n]` 同组、把 `clip_lo/hi` 改回无符号比较。

> **注意方向**：测试台缺陷常常是让**正确实现变红**，而不是让错误实现通过 —— 两者同样有毒。

---

## 5. 写断言（SVA）的三条硬规矩 —— 每条都是实测踩出来的

断言层在 `sim/tb/p2_sva_bind.sv`，用 `bind` 挂到 RTL 上，**不改 RTL 源码**。
它验"**每一次时钟沿**"；TB 验"**与模型逐位相等**"。两者缺一不可。

### 5.1 组合模块里必须用 `assert #0`，不能用普通即时断言

```systemverilog
always_comb begin
  assert #0 (!bad) else $error("...");   // ✅ Observed 区求值，所有 delta 稳定之后
//assert   (!bad) else $error("...");      // ❌ 会在中间态求值
end
```

**为什么**：目标模块的多个输入若在同一时刻被改、而它们经过的**组合链深度不同**，
普通即时断言会看到"新的 A + 旧的 B"这种混合态。实测：`unit_therm` 的温度计断言
用普通写法时**误报 7166 行**，而同一份激励下 TB 的数值比对**全绿** —— 纯假阳性。

另外：`dem_addr_gen` / `unit_therm` 是**纯组合模块、没有 `clk`**，所以只能绑即时断言；
`slice_alloc` / `recon_core` / `div_floor` 有时钟，可以用并发属性。
**绑之前先确认目标模块到底有没有 `clk` 端口** —— 给没有 `clk` 的模块写
`assert property (@(posedge clk) ...)` 会直接编译不过。

### 5.2 每条断言都必须自带 `$error` 与**可搜索的文案**

```systemverilog
// ❌ 没写 else -> VCS 用默认文案 "started at X failed at X"，不含 Error、不含任何关键词
assert property (@(posedge clk) disable iff (!rst_n) !(a && b));

// ✅ 自带文案与真实值
assert property (@(posedge clk) disable iff (!rst_n) !(a && b))
  else $error("A8b: a AND b both set (a=%0b b=%0b)", a, b);
```

**实测代价**：本项目曾有一类断言失败**13 次**，但因为用了默认文案，
`grep "Error"` 与 `grep "violated"` 都返回 **0**，而 `run_vcs.sh` 的正则兜底
（按 `fatal`/`error` 匹配）也是 **0 命中**；**唯一暴露它的是 `simv -exitstatus` 的退出码 2**。

### 5.3 判断"断言有没有报"，**先看退出码，别信文本**

`simv -exitstatus` 的退出码：`0` 通过 / `2` 断言失败 / `3` `$fatal`。
日志文本是**软的**（格式随工具版本变、可被静默忽略），退出码是**硬的**。

> 一般化：**判据必须能被机械地发现**。任何"靠日志长什么样来判断"的门禁，
> 都要配一个退出码级别的兜底，否则失败会以一个沉默的退出码形式存在。

### 5.4 断言"不覆盖"的东西要写清楚

`p2_sva_bind.sv` 覆盖的是**逐拍成立的结构性不变量**（排列是否双射、掩码是否温度计、
粘滞标志是否真粘滞、剪裁标志是否互斥、`d==0` 是否报错）。
它**不比对数值**、**不覆盖没例化的配置分支**、**不覆盖模拟域语义** ——
这些归 TB 与 `sim/ref/` 的独立参考实现。
