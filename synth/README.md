# `synth/` —— Design Compiler 综合通路（TSMC 28nm HPC+ / RVT）

本目录把「SystemVerilog → DC 综合 → 面积/时序报告」这条链路固定成可复用脚本。
目标不是跑一次出个数字，而是**下次换模块只改参数、不重写流程**，并且
**流程失败与「时序没收敛」必须是两个不同的退出码**，不会被混成「跑完了」。

> 2026-09-20 复核：修复了 ultra 模式实际调用 compile、零 I/O delay 被省略、
> TNS 误取最小值、成功标记掩盖工具异常，以及负载的单位换算。
> 本文历史 PPA 数据保留为历史记录；受旧命令/约束影响的数字需重跑，不用于证明本分支性能。
> 详见 [RTL 与综合专项报告](../docs/rtl/RTL_SYNTHESIS_REVIEW_20260920.md)。
> 当前库记录为 1 pF/unit，因此 `LOAD_PF=0.02` 传给 set_load 的值为 0.02。
> 换用其他库时必须核实单位并设置环境变量 `LIB_CAP_UNIT_PF`（fF 库为 0.001）；
> 该变量随环境传入 DC 并记录到 status.txt，不自动猜测库单位。

---

## 1. 依赖的环境（都在 EDA 虚拟机上）

| 项 | 值 |
|---|---|
| SSH | `yian@192.168.38.129`（公钥免密） |
| 远端活动根 | `~/adc_rtl_synth/` —— **脚本只在这个目录下读写** |
| DC | `/opt/synopsys/syn/W-2024.09-SP3/bin/dc_shell`（实测版本 `W-2024.09-SP3`） |
| License | systemd `snpslmd.service`（active），`LM_LICENSE_FILE=27080@localhost`；`SNPSLMD_LICENSE_FILE` 指向 `/opt/synopsys/scl/2025.03/admin/license/Synopsys.lic` |
| PDK | `/home/yian/PDK/tsmc28hpcplus/` |
| PDK 环境脚本 | `/home/yian/PDK/tsmc28hpcplus/config/env.sh`（`run_synth.sh` 自动 source） |

### 1.1 DC 实际用的库文件（**基础 RVT / TT 角**）

```
/home/yian/PDK/tsmc28hpcplus/TSMCHOME/digital/Front_End/timing_power_noise/CCS/
    tcbn28hpcplusbwp7t40p140_180a/
        tcbn28hpcplusbwp7t40p140tt0p9v25c_ccs.db      <-- 本次综合用的就是这个
```

* 库内部名：`tcbn28hpcplusbwp7t40p140tt0p9v25c_ccs`（**839** 个 lib cell）
  （另一个数是 939：那是 `get_lib_cells */*` 把 DC 内置的 gtech / standard.sldb 也算进去的结果，别混用）
* `default_max_transition = 0.4859` ns（脚本默认按这个值设 `set_max_transition`）
* `nom_voltage = 0.9 V`、`nom_temperature = 25`、`nom_process = 1`；库**没有** `nominal_voltage` 属性，电压写在 `operating_conditions("tt0p9v0p9v25c")` 里
* 单位：`time_unit = 1ns`、`capacitive_load_unit = 1pf`、`voltage_unit = 1V`、`current_unit = 1mA`、`leakage_power_unit = 1nW`
* `default_wire_load_selection = "WireAreaForZero"`、`default_wire_load = "ZeroWireload"` → **零线负载模型**（见 §7 风险）
* `default_input_pin_cap = 0.00045165` pF、`default_fanout_load = 1`

**注意 corner 命名不一致**（这是 PDK 自带的，不是笔误）：

| 类型 | 命名 | 例 |
|---|---|---|
| Liberty `.lib` | `<角>0p9v` 形式 | `...tt0p9v0p9v25c_ccs.lib` |
| 编译好的 `.db` | 不带重复电压 | `...tt0p9v25c_ccs.db` |

PDK 自己的 `config/env.sh` 也是这么区分的：`SIGNOFF_LIB_CORNERS="ssg0p81v0p81v125c ffg0p99v0p99vm40c tt0p9v0p9v25c ffg0p99v0p99v125c"` 对
`SIGNOFF_DB_CORNERS="ssg0p81v125c ffg0p99vm40c tt0p9v25c ffg0p99v125c"`。
`run_dc.tcl` 两种命名都会去试，所以 `--corner` 传哪个都能命中。

### 1.2 可选的其它 corner（同一个目录，基础 RVT）

| `--corner` | 条件 | 用途 |
|---|---|---|
| `tt0p9v25c`（默认） | TT 0.9V 25°C | 基线 |
| `ssg0p81v125c` | SS 0.81V 125°C | 慢角（setup 最坏） |
| `ssg0p81vm40c` | SS 0.81V −40°C | 慢角低温 |
| `ffg0p99v125c` | FF 0.99V 125°C | 快角 |
| `ffg0p99vm40c` | FF 0.99V −40°C | 快角低温（hold 最坏） |

另有 `...hvt_180a` / `...lvt_180a` 两个变体目录（HVT/LVT 器件），结构相同；
本流程默认只用基础 RVT。NDM 格式的库（`ndm/tcbn28hpcplusbwp7t40p140.ndm` 等）
是给 Fusion Compiler 用的，**本流程不用**。

### 1.3 有没有用 TSMC 参考流程？

**没有现成的 DC 综合参考流程可复用。** PDK 里能找到的东西：

* `/home/yian/PDK/tsmc28hpcplus/bin/build_ndm.tcl` —— 建 NDM（给 FC 用），
  但它明确用 `Front_End/timing_power_noise/CCS/${variant}_180a/*.db`，
  这一点和本流程的库路径**互相印证**（说明我们找的路径是对的）。
* `/home/yian/PDK/tsmc28hpcplus/installed/tech/synopsys_pr/N28_PRTF_Syn_v1d5a/`
  —— 这是 **PRTF（Physical Reference Flow）的工艺文件**（`.tf` / antenna rule），
  对应 APR，不是 DC 综合脚本。
* `config/env.sh` —— 库路径的官方声明，本流程直接 source 它，
  所以库路径不是我们手写死字符串，而是 PDK 自己说的。

综合脚本（`run_dc.tcl`）是我们自己写的；`config/env.sh` 是唯一被复用的 PDK 资产。

---

## 2. 文件清单与调用方式

| 文件 | 在哪跑 | 作用 |
|---|---|---|
| `synth/run_dc.tcl` | VM | DC 主流程：`read_db → analyze → elaborate → link → check_design → 约束 → compile_ultra → 报告 → 判退出码`。参数全走环境变量 |
| `synth/run_synth.sh` | VM | shell wrapper：解析 `--参数`、source PDK `env.sh`、补 license 变量、调 `dc_shell -no_gui -f run_dc.tcl`、把 `status.txt` 与 dc_shell 退出码合成**一个明确退出码** |
| `synth/run_synth.py` | 本机 | 驱动：按仓库相对路径 `scp` 同步到 `~/adc_rtl_synth/<name>/{src,inc}`，远端跑 `run_synth.sh`，把报告取回 `synth/artifacts/<name>/`，退出码原样透传 |
| `synth/check_syntax.sh` | VM | `run_dc.tcl` 的**语法门禁**（秒级）：用一个独立 `tclsh` 把脚本源进来，DC 命令被桩函数顶掉，只做解析。避免"拼错一个括号要等 12 分钟才知道" |
| `synth/smoke/top_smoke.sv` | — | smoke 顶层 wrapper（见 §4） |
| `synth/tests/param_probe.sv` | — | `--params` 链路的**活体探针**（面积随 `P_WIDTH` 近似线性），见 §2.1。不是 RTL 交付物 |
| `synth/tests/badparams/rtl_params.vh` | — | **故意改坏的**参数头文件（只差 `PHASES` 一处），用于证明参数一致性检查**能变红**，见 §7.1 |
| `synth/sweep_p_stages.sh` | VM | `P_STAGES` 扫描的串行驱动器（每点一个 `out_p<N>/`，写 `rc_p<N>.txt` 与 `SWEEP_DONE`），见 §5.1 |
| `synth/artifacts/` | 本机 | 取回的报告（**已 gitignore**） |
| `synth/.gitignore` | — | 忽略 `artifacts/`、`*.rpt`、`*.log`、`*.ddc`、`*.db` 等 |

### 一行调用示例

```bash
# 本机（Git Bash）。smoke 用默认文件集，只需给 name/top/周期
C:/Users/Administrator/miniconda3/python.exe synth/run_synth.py \
    --name smoke_10ns --top top_smoke --clk-period 10

# 目录模式：rtl/core 与 rtl/top 递归展开成 .sv；rtl/params 只作 include 目录
C:/Users/Administrator/miniconda3/python.exe synth/run_synth.py \
    --name top_10ns --top sar20_digital_core --clk-period 10 \
    --files rtl/core rtl/top rtl/params --incdirs rtl/params

# 扫顶层 HDL 参数（不改源文件）
C:/Users/Administrator/miniconda3/python.exe synth/run_synth.py \
    --name recon_p4_10ns --top recon_core --clk-period 10 \
    --files rtl/core/recon_core.sv rtl/core/div_floor.sv rtl/core/adc2_dec.sv \
    --incdirs rtl/params --params "P_STAGES=4"
```

```bash
# 直接在 VM 上（脚本自己 source PDK env、补 license）
cd ~/adc_rtl_synth/<name>
./run_synth.sh --top top_smoke --files "a.sv b.sv" --incdirs /path/inc \
               --clk-period 10 --out-dir ./out
```

### 常用参数

| 参数 | 说明 | 默认 |
|---|---|---|
| `--top` / `TOP` | 顶层模块名 | 必需 |
| `--files` / `FILES` | 源文件列表（空格分隔）。**支持目录**：目录会递归展开成其中的 `.sv`/`.v`（不收 `.vh`/`.svh`，那是 `--incdirs` 的事） | 必需 |
| `--clk-period` / `CLK_PERIOD` | 时钟周期 (ns) | 必需 |
| `--out-dir` / `OUT_DIR` | 报告输出目录 | 必需 |
| `--clk-name` | 时钟端口名 | `clk` |
| `--incdirs` | `` `include `` 搜索目录 | 空（smoke 默认 `rtl/params`） |
| `--defines` | `+define+` 宏 | 空 |
| `--params` / `PARAMS` | **顶层 HDL 参数覆盖**，如 `"P_STAGES=4"`。走 `elaborate -parameters`，用来扫面积/时序旋钮而**不改源文件** | 空（用 RTL 默认值） |
| `--compile-mode` / `COMPILE_MODE` | `ultra` = `compile_ultra -no_autoungroup`；`compile` = 非 ultra（快得多、面积/时序差一些）。**引用数字时必须连着这一行一起引** | `ultra` |
| `--check-only` / `DC_CHECK_ONLY=1` | 只做到"参数一致性检查 + `check_design`"就退出（不起 compile），几十秒红绿一次 | 关 |
| `--dc-timeout` / `DC_TIMEOUT` | 远端 `dc_shell` 超时秒数 | 3600 |
| `--corner` | 工艺角（见 §1.2） | `tt0p9v25c` |
| `--lib-db` | 直接指定 `.db`，覆盖 `--corner` | 空 |
| `--max-trans` | 覆盖库的 `default_max_transition` | 0.4859 (库值) |
| `--drive-cell` | 输入驱动单元 | `BUFFD2BWP7T40P140` |
| `--load` | 输出负载 (pF) | `0.02` |
| `--in-delay` / `--out-delay` | 输入/输出延迟 (ns) | `0.0` |
| `--preflight` | 只做库+license 体检（约 49 s），不综合；此时 `--top/--files/--clk-period` 都可省 | 关 |

`--params` 走的是 `elaborate -parameters`，链路是
`run_synth.py --params` → `run_synth.sh --params` → 环境变量 `PARAMS` →
`run_dc.tcl` 里 `elaborate ... -parameters $::PARAMS`。
**失效是静默的**（参数被忽略只会悄悄用默认值跑完），所以：

* `status.txt` 里有 `PARAMS=` 一行，记下本次生效的参数；
* `dc.log` 里有 `DC_INFO: design fingerprint: TOP=... PARAMS='...' hierarchical_cells=N`
  ——同一批扫描里两个不同参数点若指纹与面积完全一样，就是"参数没生效"的红灯；
* `synth/tests/param_probe.sv` 是这条链路的**活体探针**（面积随 `P_WIDTH` 近似线性），
  正/负对照见 §2.1。

### 2.1 `--params` 链路的正/负对照（实测）

`synth/tests/param_probe.sv` 是一个面积与 `P_WIDTH` 近似线性的玩具设计。
三次实跑（TT corner，`--clk-period 5`）：

| 运行 | `--params` | `status.txt` 里 `PARAMS` | Total cell area | CELLS | rc |
|---|---|---|---|---|---|
| `ctl_pw_default` | （不给） | `(default)` | **35.280000** | 40 | 0 |
| `ctl_pw128` | `P_WIDTH=128` | `P_WIDTH=128` | **564.872001** | 642 | 0 |
| `ctl_pwbad` | `P_WIDTH=-4` | `P_WIDTH=-4` | **26.460000** | 30 | 0 |

读法：

* 第一行 vs 第二行 —— 同一个源文件、同一套脚本，**面积 16 倍差**。这就是
  `elaborate -parameters` 真的生效的硬证据；如果链路断了，这两行会完全相同。
* 第三行 —— `P_WIDTH=-4` **没有失败**（我原本预期的"非法值必失败"判据是错的），
  但它的面积与默认值不同，说明参数串确实被解析并吃进去了。
  教训：**非法输入被拒绝 与 合法输入被采纳 是两件事**，门禁要用后者。

`synth/run_synth.py --params` → `synth/run_synth.sh --params` → `PARAMS` 环境变量
→ `run_dc.tcl` 的 `elaborate -parameters` 这一整条链路，就是靠上面这张表钉死的。

### 产出（`OUT_DIR` 下固定文件名）

`area.rpt`、`timing.rpt`、`power.rpt`、`qor.rpt`、`check_design.rpt`、
`status.txt`（机器可读：`DC_STATUS / WNS / TNS / AREA / CELLS / TOP / CLK_PERIOD / PARAMS / COMPILE_MODE / RTL_PARAMS_SHA / LIB_DB / PHASE / TIMESTAMP`）、
`phase.log`（**无缓冲**阶段日志：`dc.log` 是块缓冲的，几十 KB 才 flush 一次，
"还在推进"和"卡死了"只能靠它区分）、
`dc.log`（dc_shell 全量输出）、`elaborate.log`（elaborate/link 的日志，用于抓未解析引用）、
`run_dc.snapshot.tcl`（本次实际执行的 `run_dc.tcl` 快照）。

本地 `synth/artifacts/` 下已有的实跑产物（**全部 gitignore**，不进版本库）：

| 目录 | 内容 |
|---|---|
| `smoke_10ns/` | 基线（§4.1） |
| `smoke_20ns/` `smoke_5ns/` `smoke_2ns/` `smoke_1ns/` `smoke_0.75ns/` `smoke_0.6ns/` `smoke_0.5ns/` | §5 的周期扫描 |
| `smoke_e2e_20ns/` | 经 `run_synth.py` 的一次完整成功路径 |

---

## 3. 退出码

| 码 | 含义 | 怎么区分出来的 |
|---|---|---|
| `0` | 综合完成且 **WNS ≥ 0** | `status.txt: DC_STATUS=OK` |
| `1` | **工具/流程失败**：库读不进、`analyze`/`elaborate`/`link`/`compile_ultra` 报错、license 拿不到、源文件列表不全（有未解析引用）、事后取不到任何 timing path、超时（默认 3600 s，`DC_TIMEOUT` 可调） | `DC_STATUS=FAILED` / `NO_TIMING`，或 dc_shell 非零退出且无 `status.txt` |
| `2` | **参数错误**：缺 `--top/--files/--clk-period/--out-dir`，或 `--clk-period` 非正数 | 参数校验分支 |
| `3` | **综合正常完成，但时序未收敛（WNS < 0）** —— 这是有意义的结果，报告完整可用 | `DC_STATUS=TIMING_NOT_MET` |
| `4` | 综合结束但拿不到 / 无法解析 `status.txt`（异常，需人工看 `dc.log`） | 兜底分支 |
| `5` | **RTL 参数与冻结契约不一致**（`DC_STATUS=PARAMS_MISMATCH`）——**有意拒绝综合**，不是流程故障：说明有人改了 `rtl_params.vh`、或绕过了参数检查 | `run_dc.tcl` 的 `dc_check_rtl_params`，见 §7.1 |
| `90` | 仅 `run_synth.py`：本地侧错误（文件缺失、ssh/scp 不可用、远端路径不安全等） | Python 侧 |

`run_synth.py` 把远端的码原样返回，所以本机 `echo $?` 就能拿到上表的值。

---

## 4. smoke case 与真实结果

`synth/smoke/top_smoke.sv` 是**为综合专门写的薄 wrapper**：`rtl/core/` 下
`dem_state_gen` / `dem_addr_gen` / `swap_decode` / `dither_gen` / `unit_therm`
是并行子模块、没有顶层，所以这里只做端口绑定和常量驱动把它们全部例化，
并把组合输出打一拍寄存器，好让 DC 报出真实的 reg→reg 路径：

```
dither_gen(flop) --+--> swap_decode --+--> unit_therm --> main_on_q(flop)
dem_state_gen(flop)--> dem_addr_gen ---+
coarse_in(port) -----------------------+
```

`rtl_params.vh` 通过 `+incdir+rtl/params` 找到；**没有改动 `rtl/` 下任何文件**。

### 4.1 10 ns（基线）实跑结果 —— 全部来自真实报告

| 指标 | 值 | 出处 |
|---|---|---|
| 工艺角 / 库 | TT 0.9V 25°C，基础 RVT | `status.txt: LIB_DB` |
| 时钟周期 | 10.00 ns（`create_clock`，端口 `clk`） | `qor.rpt` |
| **WNS（setup）** | **+7.62 ns（MET）** | `timing.rpt: slack (MET) 7.62` |
| TNS | 0.00 | `qor.rpt` |
| 违规路径数 | 0 | `qor.rpt` |
| 关键路径延时 | 2.18 ns，逻辑级数 40 | `qor.rpt` |
| **Total cell area** | **4364.528002** | `area.rpt` |
| **Number of cells** | **6297**（leaf 6292） | `area.rpt` / `qor.rpt` |
| 其中时序单元 | 595 | `qor.rpt` |
| 其中反相器/缓冲 | 1526 inv + 20 buf = 1546 | `qor.rpt` |
| Number of nets / ports | 7601 / 2222 | `area.rpt` |
| Worst hold violation | **−0.12 ns，28 条** | `qor.rpt` |
| 运行墙钟 | ≈12 min（含报告） | 实测 |

**面积单位**：Liberty 的 `area` 是库自定义单位，TSMC 该库惯例按 µm² 标定；
`report_area` 里 `Total area: undefined`（没有 metal 面积），
所以引用时应写作「4364.53 （Liberty area unit，惯例 µm²）」，**这个单位我没有独立核实**。

**hold 有 28 条 −0.12 ns 的违规**：这是纯综合（零线负载、没有 CTS/布线）下的正常现象，
不算问题，但做基线时要记住 setup 与 hold 的结论方向是相反的 —— 而且**长周期下 hold 更差**
（20 ns 时有 595 条、最差 −0.33 ns），见 §5。

---

## 5. 时钟周期扫描（求 Fmax）

同一个 `top_smoke`，同一个库（TT 0.9V 25°C / RVT），只改 `--clk-period`。
所有数字来自 `~/adc_rtl_synth/smoke/out_<P>/status.txt` 与 `qor.rpt`（已取回
`synth/artifacts/smoke_<P>ns/`）。

| 周期 (ns) | WNS (ns) | 收敛? | 退出码 | Total cell area | leaf cell 数 | 实测关键路径 (ns) | 逻辑级数 | hold 违规 | 墙钟 (s) |
|---|---|---|---|---|---|---|---|---|---|
| 20 | +17.394 | MET | 0 | 4364.724002 | 6293 | 2.21 | 40 | 595 条，最差 −0.33 | 576 / 321¹ |
| 10 | +7.61644 | MET | 0 | 4364.528002 | 6292 | 2.18 | 40 | 28 条，最差 −0.12 | 732 |
| 5 | +2.72766 | MET | 0 | 4364.724002 | 6293 | 2.17 | 40 | 13 条，最差 −0.02 | 831 |
| 2 | +0.00285888 | MET（贴 0） | 0 | 4368.154002 | 6289 | 1.96 | 42 | 0 | 814 |
| 1 | +0.0000189543 | MET（贴 0） | 0 | 4625.208020 | 8184 | 0.96 | 31 | 0 | 375 |
| 0.75 | +0.0000874996 | MET（贴 0） | 0 | 4802.588023 | 8335 | 0.72 | 31 | 0 | 332 |
| 0.6 | +0.0000118613 | MET（贴 0） | 0 | 6213.395970 | 10949 | 0.56 | 28 | 0 | 329 |
| **0.5** | **−0.0815564** | **VIOLATED** | **3** | 7938.294012 | 11960 | 0.56（尽力后的最好值） | 27 | 0 | 594 |

¹ 20 ns 跑了两次：第一次（576 s）被 §6 坑 4 的操作性事故污染了退出码（数字有效）；
冻结脚本后干净重跑（321 s）得到**完全一样的 WNS/面积/cell 数**，退出码 0。

### 怎么读这张表

* **周期 ≥ 2 ns 时段是完全自由的**：DC 只要把关键路径压在 ~2.2 ns 就收工，
  所以 20/10/5 ns 的面积、cell 数、关键路径长度基本一模一样
  （4364.5~4364.7 / 6292~6293 / 2.17~2.21）—— 换句话说 **5 ns 以内不再有面积代价**。
* **2 ns 起 DC 才开始真的重新综合**，而且越紧越贵：

  | 周期 | 面积相对 5 ns | cell 相对 5 ns |
  |---|---|---|
  | 2 ns | +0.1% | −0.1% |
  | 1 ns | +6.0% | +30% |
  | 0.75 ns | +10.0% | +32% |
  | 0.6 ns | +42.3% | +74% |
  | 0.5 ns | +81.9%（且仍不收敛） | +90% |

* **0.6 ns 收敛、0.5 ns 不收敛，两者关键路径都是 0.56 ns。**
  0.5 ns 那次 DC 已经尽力（cell 从 6289 涨到 11960、逻辑级数压到 27），
  但实测关键路径仍是 0.56 ns，所以 slack 为负。于是：

  > **Fmax（TT 0.9V 25°C、基础 RVT、零线负载）≈ 1/(0.56 ns) ≈ 1.8 GHz。**
  >
  > * 可以确认**收敛点 ≥ 1.6 GHz**（0.6 ns 那一次 WNS = +1.19e−5 ns，刚压线过，余量≈0）
  > * 上界 ≈ **1.8 GHz**（0.5 ns 那一次实测关键路径 0.56 ns 是 DC 的最好成绩）
  >
  > 这是**综合意义上**的 Fmax，且**偏乐观**（零线负载，见 §7）。
  > 工程上用建议只引用 **1 ns / 1 GHz** 这一点：它有完整报告、面积代价
  > 只有 +6%，而且不用赌优化器。
* 退出码行为在生产里被验证到了：0.5 ns 那次返回 **3**（不是 1）——
  综合正常完成、报告齐全、只是时序没收敛，这正是我们要的区分。
* **setup 与 hold 方向相反**：长周期下 DC 不做优化，hold 反而更差
  （20 ns 有 595 条、最差 −0.33 ns；5 ns 只剩 13 条、−0.02）。
  本流程只做了 `compile_ultra -no_autoungroup`，**没有修 hold**，
  所以基线只引用 setup（WNS）。
* 面积单位见 §4.1；**这些时序数字都偏乐观**，原因见 §7（零线负载模型）。

### 5.1 `recon_core` 的 `P_STAGES` 扫描（吞吐 ↔ 时序的真实交易）

`P_STAGES` 是 `div_floor` 里"一个时钟内级联几级恢复余数"的旋钮：
`N_CYC = ceil(63 / P_STAGES)`，63 = `ACC_BITS - (V_FRAC+1) = P_W_A`。
顶层 `rtl/top/sar20_digital_core.sv:475` 把它**写死成 7**。吞吐预算来自 `PHASES = 16`，
即要求 `N_CYC ≤ 16`（换算成 `P_STAGES ≥ 4`）。

驱动：`synth/sweep_p_stages.sh`（VM 侧，串行、每点独立 `out_p<N>/`、写 `rc_p<N>.txt`
与 `SWEEP_DONE`）。**口径**：`COMPILE_MODE=compile`（非 ultra），10 ns，TT corner，
`RTL_PARAMS_SHA=39b12360d1858e7cfbe05f18614d5d2f270e11627fda33b942b00f4a266d83a8`，
`run_dc.tcl md5=72430a67…`、`run_synth.sh md5=a4d2696b…`。
**`compile` 与 `compile_ultra` 的数字不可直接比较**，跨批次引用时必须连口径一起引。

| `P_STAGES` | `N_CYC` | RECON_LAT=N_CYC+2 ≤16 拍? | Total cell area | leaf cell 数 | 最差 slack | 关键路径终点 | 逻辑级数 | 状态 |
|---|---|---|---|---|---|---|---|---|
| 1 | 63 | **✗** | 312489.268366 | 492504 | **+1.83 ns** | `inj_r_reg[63]` → `ovf_pend_reg` | 140 | rc=0，`status.txt` 缺（见下） |
| **4** | **16** | **✗（18 拍）** | **313859.994355** | **496027** | **+1.38771 ns** | **`u_div/cnt_reg[2]` → `u_div/q_reg[62]`** | **204** | rc=0，3120 s |
| 7 | 9 | ✓ | 315678.874344 | 500997 | **+0.00229263 ns** | `u_div/dv_reg[62]` → `u_div/q_reg[62]` | **283** | rc=0，3121 s |
| 63 | 1 | ✓ | — | — | — | — | — | 已停止，未完成映射 |

14 与 21 **没有跑**：它们落在 (7, 63) 之间、不跨任何边界，边际信息最低。
吞吐那一列不需要综合即可算出（见上表），P=1 在吞吐上直接出局。

#### 这张表说明什么（**结论与直觉相反，值得单独记住**）

1. **`P_STAGES ≥ 4` 时，关键路径全在 `div_floor` 内部。**
   P=1 时 `div_floor` 一拍只做 1 级减法（1 个比较器 + 1 个条件减），它不可能成为长组合路径，
   于是关键路径落在 `recon_core` 自己的算术上（`op3 = total_s × inj_r` 是 **130 位乘法**，
   路径终点的 `ovf_pend_reg` 由 5 个量级比较器合并而来）。
   到 P=4 关键路径已经换成 `u_div/cnt_reg[2] → u_div/q_reg[62]`（含迭代计数器译码）；
   P=7 换成 `u_div/dv_reg[62] → u_div/q_reg[62]`，`timing.rpt` 的层次清单直接列成
   `div_floor_..._DW01_cmp2_*` → `DW01_sub_*` → `DW01_add_1`。
   **逻辑级数单调**：140（P=1）→ 204（P=4）→ 283（P=7），大约**每多一级级联 ≈ +28 级逻辑 ≈ +0.35 ns**。
2. **面积几乎不要钱，贵的是时序余量。**
   | `P_STAGES` | 面积相对 P=1 | cell 相对 P=1 | 最差 slack |
   |---|---|---|---|
   | 1 | — | — | +1.83 ns |
   | 4 | **+0.44%** | +0.72% | **+1.38771 ns** |
   | 7 | **+1.02%** | +1.72% | **+0.00229263 ns** |

   **P=4 相对 P=7 小 0.58%、cell 少 4970，而 10 ns 余量从 0.0023 ns 变成 1.39 ns。**
3. **调度结论（2026-09-19 更正）**：必须使用 `RECON_LAT=ceil(63/P)+2`。
   P=4 的实际延迟是 18 拍，在 PHASES=16 下不可用；保留当前 P=7。
   已测点与未跑点以 [P2 §17.7](../docs/rtl/P2_INTERFACE.md) 为准。
   **40 MS/s 尚未闭合**：每 16 拍一个结果要求 640 MHz（1.5625 ns）；10 ns 仅对应
   6.25 MS/s 的调度上限。这些单模块历史综合结果不等于完整顶层目标时序。
4. **对 P6 的含义**：只要 `P_STAGES ≥ 4`，**"流水化 568 项加法树"没有收益**
   ——关键路径在除法器里，加法树不是限制项。要动就动除法器本身
   （最便宜的是减小 `P_STAGES`；`div_floor.sv` 头部登记的阵列除法器 / 倒数 ROM 更贵，
   且需要补"商误差恰为 0"的证明与独立验证）。只有当 P 降到 1
   （63 拍、吞吐出局）时，加法树/乘法器才成为关键路径。

#### 两条与这张表绑定的操作事实

* **P=1 那一行没有 `status.txt`**：它在报告**全部写完**之后卡在
  `get_timing_paths -nworst 1000000` 上 13 分钟不返回，被我停掉（见 §6 坑 14）。
  该行的数字全部从 `area.rpt` / `timing.rpt` / `qor.rpt` 直接读，报告本身完整。
  封顶成 `nworst=2000` 后，P=7 这一点 `status.txt` 完整、rc=0 —— 修复被验证有效。
* 单点墙钟 **≈52 min**（P=7 实测 3121 s，其中 compile 2877.84 s）。
  这台 22 GB 的 VM 一次只能跑一个（P=1 峰值 10.3 GB，P=7 峰值 10.3 GB），
  所以 6 个点要 ~5 h —— 这正是把点集收到 {1, 4, 7, 63} 的原因。

---

## 6. 踩过的坑（都做过对照实验，都会静默出错）

1. **dc_shell 在 `-f script.tcl` 模式下，脚本里的 Tcl 错误不会让进程非零退出。**
   它只打印 `script stopped at line N due to error`，回到 `dc_shell>` 提示符，
   **退出码 0**。所以「脚本炸了」和「跑成功了」在退出码上完全一样。
   `run_dc.tcl` 的应对：整个流程包在 `proc dc_main` 里 + 顶层 `catch`；
   致命错误一律 `exit <code>`（Tcl 的 `exit` 不会被外层 catch 拦下）。

2. **`target_library` 必须写 `.db` 的完整路径，写库名不行。**
   同一个 8-bit counter、同一个库里，对照实验（DC W-2024.09-SP3）：

   | 写法 | 结果 |
   |---|---|
   | `target_library = tcbn28hpcplusbwp7t40p140tt0p9v25c_ccs` | `Error: Could not read the following target libraries` + `Error: No target library found. (OPT-1312)`，**Total cell area = 0.000000**（完全没映射） |
   | `target_library = /.../tcbn28hpcplusbwp7t40p140tt0p9v25c_ccs.db` | 正常映射，**Total cell area = 28.126000** |

   `read_db` 并不会让「写库名」变可用（两种写法都带 `read_db`）。
   另外 `link_library` 必须写成 `[list * <path>]` **两个元素**，
   写成 `[list "* <path>"]` 会得到一个含空格的字符串，DC 去找名为
   `"* tcbn..._ccs"` 的文件。

3. **应用变量不能用 `set` 在 proc 里赋值。**
   `set target_library ...` 写在 proc 内只创建 proc 局部变量，DC 的应用变量
   根本没被赋值，症状与坑 2 一样（`No target library found`），而且
   `compile_ultra` 会**长时间空转不退出**。必须用 `set_app_var`，并在赋值后
   `get_app_var` 回读核实（`run_dc.tcl` 里就做了这个断言）。

附带一条：`get_timing_paths` **没有 `-quiet` 选项**（写了会报 `CMD-010`），
WNS 是从 `get_timing_paths -delay max -nworst 1` 的路径对象上读 `slack` 属性；
面积则是把 `report_area` 的输出抓成字符串再正则提数的（`get_attribute [current_design] area`
在这个版本上返回空串）。

4. **跑批进行中不要覆盖 `run_synth.sh` / `run_dc.tcl`。**
   这是真事故：bash 是**按字节偏移增量读**脚本的，`dc_shell -f` 也在启动时整份读 tcl。
   在 20 ns 那一次综合还在跑的时候，我把仓库里的 `run_synth.sh` scp 上去了，
   结果那一次**综合成功、`status.txt` 数字全对，wrapper 却返回了假的 `rc=2`**
   （读到了 `usage 2` 分支的残片）。这类故障最难查，因为它看起来像参数错误。
   对策：`run_synth.sh` 现在会先把 `run_dc.tcl` 拷成 `$OUT_DIR/run_dc.snapshot.tcl`
   再执行，之后改仓库里的 tcl 影响不到正在跑的这一次，并且这份快照就是
   "报告由哪版脚本产出"的证据（`run_synth.py` 会把它一起取回本地）。

5. **远端相对路径有两个不同的基准，别混。**
   `scp` 的目的地是相对 `$HOME` 解析的；但 `run_synth.sh` 会先
   `cd "$HOME/<name>"` 再调 `dc_shell`，所以传给 `--files` / `--incdirs` /
   `--out-dir` 的**必须是相对 `<name>/` 的**（`src/...`、`inc/...`、`out`）。
   这个坑在 `run_synth.py` 里踩了两次：
   * `--out-dir` 写成 `<name>/out` → `$HOME/<name>/<name>/out`，多套一层目录，
     报告取不回来；
   * `--files` 写成 `adc_rtl_synth/<name>/src/...` → dc_shell 在
     `<name>/` 下找不到文件，直接 `exit 1`（报 `source file not found`，
     看起来像文件真的不存在，其实是路径基准错了）。
   现在 `run_synth.py` 只传 `src/...` / `inc/...` / `out`，并有断言兜底。

6. **`--files` 传目录会被静默跳过。**
   早先 `expand_files()` 对目录是 `continue`（静默丢弃），于是
   `--files rtl/core rtl/top rtl/params` 这种**按目录给**的写法里三个目录全被扔掉。
   后果分两种：全都匹配不到 → 直接以「no source files」退出（还算好的）；
   只要还有一个别的文件 → 会在**少文件**的状态下综合出一份看起来正常的报告。
   现在目录会递归展开成其中的 `.sv`/`.v`（且**只收这两种**：`.vh`/`.svh` 是
   `` `include `` 目标，走 `--incdirs`，混进 `analyze` 的文件列表会报错）。
   `rtl/params` 这种"只有头文件"的目录会打一条 `NOTE` 而不是报错。

7. **`elaborate -parameters` 失效是静默的。**
   参数被忽略只会"用默认值跑完"、报一个人畜无害的面积数字。扫参数时如果这条
   链路断了，多个参数点会给出**一模一样的数**，很容易被顺手解释成"该参数对面积
   不敏感"。对策有三层：`status.txt` 记 `PARAMS=`；`dc.log` 打
   `DC_INFO: design fingerprint: ... hierarchical_cells=N` 供横向比对；
   `synth/tests/param_probe.sv` 做正/负对照（见 §2.1）。
   另外一个反直觉的实测结论：**非法参数值不一定会被拒绝**
   （`P_WIDTH=-4` 被 DC 照单全收），所以"拿非法值能否失败"当门禁是不可靠的，
   要用"不同合法值给出不同结果"来证明链路通。

8. **`initial` 块（以及里面的 `$fatal` 自检）在综合里被整块丢掉。**
   `rtl/top/sar20_digital_core.sv:110-128` 有 17 条 `$fatal(1, "参数不一致")`
   一致性断言，本意是"参数被人改了就当场炸"。综合时的真实行为是
   `elaborate.log` 的第 2 行：

   ```
   Warning: ./src/rtl/top/sar20_digital_core.sv:111: The statements in initial blocks are ignored. (VER-281)
   ```

   —— 只是**一句 Warning**，不报错、退出码 0、综合照常完成。
   所以这条防线**只对仿真有效，对综合完全无效**：参数漂了会安静地综合出
   一块面积/时序都不同、但报告看起来完全正常的网表。
   这和 README 坑 1（`dc_shell -f` 下 Tcl 报错仍返回 0）是同一族缺陷：
   **"没有报错"不等于"检查执行过了"**。
   要在综合侧卡住，只能把一致性检查做成流程的一部分（例如综合前先跑
   `tools/` 里的参数检查、或在 `run_dc.tcl` 里显式断言），不能依赖 RTL 的
   `initial` + `$fatal`。**已经做了**：见 §7.1。

9. **`pgrep dc_shell` 查不到正在跑的 dc_shell（假阴性）。**
   实测：一次顶层综合正在 96% CPU 上推进时，
   `pgrep dc_shell` → **空**，而 `pgrep -f 'run_dc.snapshot.tcl'` → 两个进程。
   原因：真正的工作进程名是 `common_shell_exec`（`comm` 只有 15 字符），
   而外层是 `timeout 5400 .../dc_shell`，其 `argv[0]` 是 **`timeout`**。
   所以"按名字查进程"在这套流程里**不可靠**。
   判断"还在跑吗"要用 `pgrep -f 'run_dc.snapshot.tcl'`（按完整命令行匹配），
   或者直接看 `phase.log` 的时间戳。
   我这边就因为这条假阴性，一度以为作业已经死了——**它其实还在跑**。
   停止作业前必须先按命令行确认，否则会误杀。

10. **`dc.log` 是块缓冲的，看不到进度。**
    实测：顶层综合的 `dc.log` 有 **19 分钟没有任何新行**（mtime 不动），
    但同一时刻进程 CPU 96%、RSS 从 3.9 GB 涨到 6.3 GB —— 它一直在干活。
    原因：DC 输出到**文件**（非 tty）时按块 flush。
    后果：只看 `dc.log` 会把"在推进"误判成"卡死了"（也会把"卡死了"误判成
    "在推进"）。对策：`run_dc.tcl` 的 `dc_phase` 往 `phase.log` 追加**并 flush**，
    每个阶段一行时间戳，这是唯一可靠的进度信号。

11. **`dc_shell` 会把 `puts` 里的非 ASCII 字符打成 `?`。**
    实测（同一台 VM、同一版 DC）：
    `puts "DC_PARAM_FAIL: $bad 条不一致"` → 日志里是 `DC_PARAM_FAIL: 2 ????`；
    `puts "... 端口 $port 位宽 = ..."` → `... ?? dout ?? = ...`。
    也就是说**关键信息（哪个常量、哪个端口）会整块丢掉** —— 一个"能变红但
    看不出红在哪"的门禁等于半个门禁。
    规则：**进 log 的字符串一律 ASCII**，中文解释只写在注释里（注释不进 log）。

12. **在 Tcl 里写 `$name(...)` 会被当成数组元素。**
    `puts "port $nm([dict get $got $nm])"` 报
    `can't read "nm(20)": variable isn't array` —— Tcl 把 `$nm(` 解析成
    数组引用。要写成 `${nm}(...)`。同类坑还有 `get_ports dout*` 把 1 位的
    `dout_valid` 也算进去（于是位宽报 21 而不是 20）。两条都实测踩过，
    而且**都被 `dc_main` 的顶层 `catch` 转成了退出码 1** —— 兜底有效，
    但"检查自己写错"和"设计真的错了"在输出上长得一样，所以判据本身要可复核
    （见 §7.1 里红例只有一条不一致）。

13. **`phase.log` 是 append 的，同一个 `OUT_DIR` 上重跑会混。**
    症状：绿例改完脚本又跑一次，`phase.log` 里出现两段 `analyze begin`，
    看起来像"analyze 跑了两遍"。对策：`dc_main` 开头就把 `phase.log` 删掉重来
    （`status.txt` 由 wrapper 删，各自只删自己的）。

14. **`get_timing_paths` 的 `-nworst` 不能给大数 —— 会把已经跑完的作业锁死。**
    原先 `set tns [dc_slack max 1000000]`。在 `recon_core`
    （leaf cell **492504**、组合 491761 / 时序仅 743）上，这一句
    **跑了 13 分钟以上还没返回**。真实代价与症状都很值得记：

    * 最讽刺的地方：**那一刻所有报告都已经写完了**
      （`area.rpt` 16:06:58、`timing.rpt` 16:07:04、`power.rpt` 16:08:24、
      `qor.rpt` 16:08:26、`check_design.rpt` 16:08），卡住的**只有 `status.txt`**
      —— 等于把已经拿到的结果锁死在一个无关紧要的数字后面。
    * 只在**大**设计上暴露：同一个脚本在 smoke（6292 cell）上连跑 8 次都没事。

    对策：`nworst` 封顶（现在 2000），并把上限写进 `status.txt` 的 `TNS_NWORST=`。
    本次修复后，TNS 字段为返回路径中负 slack 的和，并标记
    `TNS_KIND=sampled_path_negative_slack_sum`。同一 endpoint 可有多条路径，
    且最多取 2000 条；它不等于完整 endpoint TNS。旧代码实际重复计算最小值，
    旧字段不能按累计违例解释。签核应另读完整 STA 汇总。

15. **"一次只能跑一个 DC"是硬约束，而且 OOM 的症状是静默的。**
    实测内存占用：`recon_core`（`compile` 口径）峰值 **10.3 GB**；
    顶层 `sar20_digital_core` 的 `compile_ultra` 到 6.3 GB 还在涨。
    VM 只有 22 GB 且上面还有别人的作业。所以长任务一律**串行**排队
    （`synth/sweep_p_stages.sh` 就是为此写的），并且**用 `setsid nohup` 脱离
    ssh 会话** —— 本地 shell 被杀（实测发生过，本地后台任务有墙钟上限）
    不应该打断远端几十分钟的作业。判断"远端还在跑吗"用
    `pgrep -f 'run_dc.snapshot.tcl'`（**不要** `pgrep dc_shell`，见坑 9）。

---

## 7. 已验证 / 未验证

### 已实际验证

* `dc_shell -no_gui -f` 能启动、license 通、版本 `W-2024.09-SP3`。
* 库 `.db` 能 `read_db` 进来，`default_max_transition = 0.4859`、
  `default_operating_conditions = nom_pvt tt0p9v25c nom_pvt`、839 个 cell。
* `--preflight`（只读库、不综合，约 49 s）四种输入都验证过：
  `tt0p9v25c` → OK；`tt0p9v0p9v25c` → 归一化成 `tt0p9v25c` 后 OK；
  `ssg0p81v0p81v125c` → 归一化成 `ssg0p81v125c` 后 OK（`max_transition=0.5007`）；
  `bogus_corner` → 退出码 1 并列出该目录下所有可用 `.db`。
* `analyze → elaborate → link → check_design → 约束 → compile_ultra → report_*` 全流程在 smoke case 上跑通，产出 5 个报告文件。
* 退出码：成功 = `0`（10 ns / 20 ns 两次）；流程失败 = `1`（故意传一个不存在的
  `--top`，端到端从 `run_synth.py` 一路传回 `1`，`dc.log` 里有真实报错）；
  **时序未收敛 = `3`**（0.5 ns 那次，真实跑出来，不是只写了分支）。
* `run_synth.py` 的同步与取回：`~/adc_rtl_synth/<name>/{src,inc}` 目录结构、
  `+incdir+` 路径、报告与 `run_dc.snapshot.tcl` 落到 `synth/artifacts/<name>/`；
  远端路径被断言限制在 `~/adc_rtl_synth/` 之内。
* **`run_synth.py` 的一次完整成功路径**：`--name smoke_e2e_20ns --top top_smoke
  --clk-period 20` → `rc=0`、9/9 产物取回本地、WNS=17.394 / 面积 4364.724002，
  墙钟 5 min 50 s（产物在 `synth/artifacts/smoke_e2e_20ns/`）。
* 时钟周期扫描 20/10/5/2/1/0.75/0.6/0.5 ns 全部真跑过，见 §5；
  8 次运行的报告都在 `synth/artifacts/smoke_<P>ns/`。
* `check_syntax.sh` 正反两例都试过（好文件 rc=0；往 `dc_main` 里塞一个未闭合
  方括号 rc=1 并打出真实错误）。
* `--preflight`（只读库、不综合，约 49 s）四种输入都验证过：

### 未验证 / 有残留风险

* **零线负载模型**：库的 `default_wire_load_selection = WireAreaForZero`、
  `default_wire_load = ZeroWireload`，本流程也没有 `read_parasitics` 或
  `set_wire_load_model`。所以综合给的时序**是乐观的**（没有互连 RC），
  真实 Fmax 会低于 §5 的数字（这条路径上还有 1 个 fanout > 1000 的 net，
  DC 是按 fanout 1000 估的）。要可信的 Fmax 得走 DC-Topographical / FC + SPEF。
* **只有 TT 角**：§5 的 Fmax 是 TT 0.9V 25°C 下的。setup 签核要看 SS 0.81V 125°C，
  hold 要看 FF 0.99V −40°C。`--corner` 参数支持，但没有跑完整三角。
* **hold 没有修**：`compile_ultra -no_autoungroup` 默认不修 hold，`qor.rpt` 在各周期下
  都有 hold 违规（20 ns 时有 595 条、最差 −0.33 ns）。基线只应引用 setup（WNS）。
* **Fmax 只做了 8 个离散点、没有做二分**：结论是"0.6 ns 收敛 / 0.5 ns 不收敛"，
  真正的边界在 0.5~0.6 ns 之间，没有逐点收窄。
* **`run_synth.py --files` 传"任意模块列表"没有实跑过**：默认 smoke 文件集
  （`SMOKE_FILES`）的成功路径与失败路径都端到端跑过，`--preflight` 也跑过；
  但换一个别的顶层、别的文件集时，只验证了同步后的远端目录结构正确，
  没有真的对它跑过一次综合。
* **`unit_therm.dither_rail` 在本 wrapper 里悬空**（它是子模块的**输出**，
  悬空合法）。`rtl/params/rtl_params.vh` 里是
  `localparam [3:0] DITHER_UNITS_RANGE = 4'd2;`，端口宽度 `[3:0]`。
  ⚠️ **更正（2026-09-18）**：本节与 `synth/smoke/top_smoke.sv:25` 原先都写
  "`DITHER_UNITS_RANGE = 0`、宽度 `[-1:0]`、循环体不执行、有 undriven 警告"。
  逐条复核后**四句都错**：头文件里是 2；该端口是输出、悬空不产生 undriven 警告
  （`synth/artifacts/smoke_10ns/elaborate.log` 里 `undriven|not driven`
  一条都没有）；`unit_therm` 实测端口数 1062，与"循环体不执行"不相容。
  **时间线判定**：`rtl_params.vh` mtime = 10:36:57，`top_smoke.sv` = 11:05:43，
  第一次 smoke 的 `status.txt` = 11:33:31 —— 所有 smoke 数字都在**现在这份**
  头文件下产出。所以"旧数字是另一版参数、不保证复现"这个说法**不成立，已撤回**。
  教训：**注释与文档不要复述参数值**（不会随头文件更新），要用就引用符号本身。
* **`rtl/params/rtl_params.vh` 没有被 git 跟踪**（`git status` 里是 `??`，
  不是被 `.gitignore` 忽略 —— 整个 `rtl/` `docs/rtl/` `sim/` `tools/*.py`
  都未纳入版本控制，而 `HEAD` 已经是一个 "v8.0.0" 发布提交）。
  也就是说：**这份头文件没有版本记录**，谁重新生成一次它、下游所有面积/时序数字
  就跟着变，而 `git diff` 里**看不到任何变化**。
  这不是"已经漂移了"，而是"漂移了也没人会发现"，并且回不到之前那一版。
  这是 `status.txt` 里加 `RTL_PARAMS_SHA=`（头文件自带的 `payload sha`）的直接原因：
  它把"这份报告是哪一版参数产出的"变成可追的。**把 `rtl/params/` 纳入版本控制
  该由仓库负责人决定**，不在本流程的改动范围内。
* **`report_power` 的数字**没有做独立核对（零线负载 + 无 SAIF 翻转率，
  动态功耗基本不可用）。
* 面积单位（µm²）未独立核实，见 §4.1。
* 本流程**单点**跑一次约 12 min（6292 cell），RAM 峰值约 9 GB。VM 只有 22 GB 且
  同时在跑别人的 Fusion Compiler 工程，所以**不要并发跑多个 DC**。
  若与别人的作业抢内存导致 OOM，症状会是 dc_shell 被 kill 而不是报错。
* **平铺顶层 `sar20_digital_core` 综合不出来（实测，不是没跑）**。这一条是本节
  最重要的一条，单独展开说：

  * `compile_ultra`：101 分钟仍在 `Pass 1 Mapping`（`weight_store` 一处 70 min，
    `recon_core` 25 min 起），**没有产出任何 report**。`elaborate` 阶段的
    `hierarchical_cells = 110636`。
  * `compile`（非 ultra，`COMPILE_MODE=compile`）：跑满 78 分钟后**发散**。
    `dc.log` 的优化进度表原文（`top_compile/out/progress_table_tail.txt`）：

    ```
        1:13:47 1137569.6 3766090.50 231537623040.0 13988214427.7
        1:13:48 1141658.7 3766090.50 231537623040.0 13988212446.0
        1:13:49 1145750.8 3766090.50 231537623040.0 13988210437.3
        1:13:50 1149845.9 3766090.50 231537623040.0 13988208401.5
        1:13:55 1153927.9 3766090.50 231537623040.0 13988206438.8
        1:14:29 1178585.2   4534.95 1563569.6 12667024861.4
    ```

    读法：**面积以约 4000 µm²/s 单调膨胀**（1129403 → 1178585），
    而 `WORST NEG SLACK` 卡在 **3.77e6 ns**（≈3.8 ms，物理上不可能是真逻辑路径）、
    `SETUP COST` 2.3e11、`DESIGN RULE COST` 1.4e10 —— **只涨面积、不涨时序**。
    同时**空闲内存被吃到只剩 1 GB**（机器上还有别人的 DC 作业），
    有把别人作业一起 OOM 掉的风险，所以主动停掉并留证
    （`top_compile/out/dc.log.diverged`）。
  * **机理我没有证实，不要当成结论**。我原本的假说是"某个网扇出到上万个时序单元
    （`clk` / `rst_n`）而流程又对整个 design 下了 `set_max_transition`，于是 DC 去造
    巨大缓冲树"。加了一个 `DC_DIAG`（只在 `--check-only` 下跑）去测，结果**不支持也不
    否定**这个假说：
    * `fanout_load` 属性在**未映射**网表上取不到值（所有阈值都是 0，直接打
      `clk`/`rst_n` 的 `fanout_load`/`total_capacitance` 出来是空串）——
      所以第一版诊断输出的"没有大扇出网"是**把取不到值当成了测到 0**，已改掉；
    * 改成数 pin 数后：顶层 `clk` 只连 **37** 个 pin、`rst_n` **15** 个
      —— 扇出是**分层摊开**的，顶层这一层看不出来。
    * 实测到的确定数字只有两个：`sequential_cells = 63837`、
      `recon_core` 单独是 492504 个 leaf cell 而只有 743 个时序单元。
  * **可辩护的解释（仍属推断）**：`recon_core` 本身是一张巨大的**纯组合 mux + 加法树网**
    （`wsel[ai][ui] = w_rom[slice_id[ai]][ui]` 是 568 个 18 选 1 的 48 位 mux，
    再汇成 568 项加法树）。**分层跑之所以好看，是因为那时 `w_rom` 是顶层输入端口**，
    DC 可以把它当成一个到达时间固定的边界；平铺之后它变成 61344 个寄存器驱动的内部网，
    优化问题的规模与形态都变了。
  * **结论**：这台 VM（22 GB、还要与别人共用）上，**平铺顶层在本流程里跑不出来**。
    能给的顶层结论只能是 §5.1.1 的**分层下界**。

#### 5.1.1 分层下界（`recon_core` + `weight_store` 相加）

顶层跑不出来时，按团队约定用两块分开综合再相加作为**下界**
（下界是因为顶层还有 `ctrl_fsm` / `calib_regs` / `slice_alloc` / `status_regs` /
`rdac_drv` / DEM 系与 sm 等模块未被计入）。口径与 §5.1 一致：`COMPILE_MODE=compile`、
10 ns、TT、`RTL_PARAMS_SHA=39b12360…`。

| 块 | Total cell area | leaf cell 数 | WNS@10ns | 关键路径终点 | 逻辑级数 |
|---|---|---|---|---|---|
| `recon_core`（`P_STAGES=7`，即顶层 `:475` 的现值） | 315678.874344 | 500997 | +0.00229263 | `u_div/dv_reg[62]` → `u_div/q_reg[62]` | 283 |
| `recon_core`（`P_STAGES=4`） | 313859.994355 | 496027 | +1.38771 | `u_div/cnt_reg[2]` → `u_div/q_reg[62]` | 204 |
| `weight_store`（输出不加载，见下） | 274205.081086 | 282165 | **+5.61811** | **`w_q_reg[16][32][0]` → `w_q_reg[7][5][18]`** | 119 |
| **下界合计（`P_STAGES=7`）** | **589883.955430** | **783162** | | | |

下界合计只是把两块相加，**不含顶层还有的** `ctrl_fsm` / `calib_regs` / `slice_alloc` /
`status_regs` / `rdac_drv` / `dem_state_gen` / `dem_addr_gen` / `dither_gen` /
`swap_decode` / `unit_therm` / `sadc_enc` 与互连 —— 所以是**下界**，不是顶层面积。

`weight_store` 单独实测的确定数字：`Sequential Cell Count = 61344`（正好是
`18×71×48` 个权值寄存器）、`Combinational = 220821`、`Buf/Inv = 56490`、
`max_trans / max_cap 违规 = 0 / 0`、compile 墙钟 1755.09 s。
它的关键路径是 **寄存器→寄存器**（`w_q_reg[16][32][0]` → `w_q_reg[7][5][18]`），
走的是**写侧运行和更新**（`sum_all` / `sum_excl` / `sum_new`：排除被写地址那项、
再加上新值）—— `dc.log` 里可见它被展开成 **1277 个 `DW01_add_*` 模型**
（`weight_store_DW01_add_1272` … `_1277`）。4.18 ns / +5.62 ns 余量，**不是瓶颈**。
⚠️ `hold` 违规 **61344 条**（每个权值寄存器一条，最差 −0.11 ns）：本流程不修 hold
（见 §7），零线负载下同一阵列内寄存器之间的短路径本来就容易违反。

#### `--load` 的受控对照：**同一个模块，唯一变量是输出负载，一次发散一次收敛**

`weight_store` 第一次跑**发散**了，症状与平铺顶层**一模一样**：

| 量 | 发散那次（默认 `--load 0.02`） | 收敛那次（`--load 0`） |
|---|---|---|
| 进度表 | `0:27:36 463465.4 120.61 7386593.5 5143391779.4` | `0:28:32 276362.0 0.00 −292.4 0.5` |
| 面积走势 | 434k → 463k，**以 ~700 µm²/s 单调膨胀** | 276k 并在**缓慢下降** |
| `WORST NEG SLACK` | **卡在 120.61 ns 不动** | **0.00** |
| `DESIGN RULE COST` | **5.1e9** | **0.5** |
| 结果 | 27 分钟无报告，主动停（`out/dc.log.diverged_load20fF`） | `DC_STATUS=OK`，rc=0 |

同一份 RTL、同一版脚本（`run_dc.tcl md5=72430a67…`）、同一个时钟与 corner，
**唯一改动是输出负载**。原因是**方法论上的错**：`weight_store` 当顶层跑时 `w_q`
（61344 bit）成了**顶层输出端口**，默认按芯片级 0.02 pF/bit 加载；而在真实顶层里
`w_q` 是**内部网**、直接驱 `recon_core.w_rom`，**根本不存在这个负载**。
→ **结论：把某个块当顶层单独综合时，不要把芯片级的输出负载加到它（在真实设计里）内部的输出上；
全阵列端口负载会放大综合优化成本。**

2026-09-20 纠正：上述历史分析将命令参数解释为 20 fF，但旧脚本额外乘了 1000；
按本文件记录的 1 pF 库单位，实际应为每端口 20 pF。历史日志名不证明真实负载；
因此该次不收敛不能只归因于全阵列结构，必须修正单位后重跑。
这一条对"分层跑"这种做法是通用的，不只是这个设计。

**顶层的关键路径终点：拿不到。** 上表是**分块各自**的关键路径终点，
**不能说成"顶层的关键路径"** —— 顶层跑不出来，这是分层结果的局限，必须一起说。

---

## 7.1 RTL 参数一致性检查：红/绿实证（`synth/tests/badparams/`）

**要解决的问题**：`rtl/top/sar20_digital_core.sv:110-128` 有 17 条 `$fatal(1, ...)`
参数一致性自检，但综合时 DC 把 `initial` 块**整块丢掉**（`VER-281`，只是一句
Warning）。于是"参数被人改了就当场炸"这条防线**只对仿真有效**，综合侧完全失效。
详见 §6 坑 8。

**做法**（`run_dc.tcl` 的 `dc_check_rtl_params`，在 `elaborate` 之后、`compile` 之前）：
三个独立来源两两比对。

| 源 | 内容 | 挡什么 |
|---|---|---|
| **A** | `rtl_params.vh`（`INCDIRS` 里找到的那一份）解析出的 `localparam` | — |
| **B** | **冻结契约值**（`docs/rtl/RTL_ARITHMETIC_CONTRACT.md` §4，与顶层 17 条断言逐条对应）+ 两条派生恒等式 | 源文件**漂移** |
| **C** | `elaborate` 后设计上**可观测的端口位宽**（`slice_sel=18`、`main_sw=18*63`、`sub_sw=18*8`、`dout=20`、`inj_q=64`、`adc2_code=12`） | `--params` **覆盖**、顶层端口接错 |

只做 A-vs-B 挡不住"参数被 override 改掉"（A、B 都没变，变的是电路）；
只做 A-vs-C 挡不住"头文件自己漂了"（C 会跟着一起变）。两边都要有。

**红/绿两例（实跑，`--check-only`，每次约 1 min）**

```bash
# 绿：真头文件
run_synth.py --name pc_green --top sar20_digital_core --files rtl/core rtl/top \
             --incdirs rtl/params --clk-period 10 --check-only
```

```
DC_INFO: rtl params check: header = inc/rtl/params/rtl_params.vh
DC_INFO: rtl params payload_sha=39b12360d1858e7cfbe05f18614d5d2f270e11627fda33b942b00f4a266d83a8 parsed=35 constants
DC_INFO: width ok: slice_sel = 18 (== N_SLICES * 1)
DC_INFO: width ok: dout = 20 (== OUT_BITS * 1)
DC_INFO: width ok: inj_q = 64 (== V_BITS * 1)
DC_INFO: width ok: adc2_code = 12 (== ADC2_BITS * 1)
DC_INFO: width ok: main_sw = 1134 (== N_UNIT_MAIN * 18)
DC_INFO: width ok: sub_sw = 144 (== N_UNIT_SUB * 18)
DC_PARAM_OK: rtl params consistent (sha=39b12360d1858e7c..., header=inc/rtl/params/rtl_params.vh)
DC_CHECK_ONLY_OK: params + check_design passed (no compile); phase log = out/phase.log
```
→ `rc=0`，`status.txt: DC_STATUS=CHECK_ONLY_OK`

```bash
# 红：故意把 PHASES 26->8 的头文件（synth/tests/badparams/rtl_params.vh）
run_synth.py --name pc_red --top sar20_digital_core --files rtl/core rtl/top \
             --incdirs synth/tests/badparams --clk-period 10 --check-only
```

```
DC_INFO: rtl params check: header = inc/synth/tests/badparams/rtl_params.vh
DC_INFO: rtl params payload_sha=NA parsed=35 constants          <-- 改过的头文件没有 payload sha
DC_PARAM_MISMATCH: PHASES = 8 (header) != 16 (frozen contract section 4)
DC_INFO: width ok: slice_sel = 18 (== N_SLICES * 1)   ...（其余 5 项位宽仍然 OK）
DC_PARAM_FAIL: 1 mismatch(es) found
DC_FATAL(exit 5): RTL params inconsistent with the frozen contract; refusing to synthesize
```
→ `rc=5`，`status.txt: DC_STATUS=PARAMS_MISMATCH`，**综合被拒绝**

**读法**：红例**只差 PHASES 一处**，其余 6 项位宽检查照旧通过 —— 归因是唯一的，
不是"一片红"。这说明门禁的每一条判据都在独立工作。夹具与真头文件的 diff 只有
"横幅注释 + `PHASES` 那一行"，可随时复核。

`rtl/params/rtl_params.vh` 里自带由 `tools/export_rtl_params.py` 生成的
`payload sha : <hex>`，它被原样记进 `status.txt` 的 `RTL_PARAMS_SHA=` 一行 ——
"这份报告是哪一版参数产出的"从此有不可辩驳的锚点（红例里它是 `NA`，
正是因为改过的头文件丢了那一行）。

**改坏头文件的等价做法**（若不想引入 `synth/tests/badparams/`）：在 VM 上
`~/adc_rtl_synth/` 下复制一份改过的头文件、用 `--incdirs` 指过去即可 ——
**不必改仓库里的 `rtl/params/rtl_params.vh`**（那是冻结资产）。本仓库选择把夹具
放进 `synth/tests/` 是为了让这个红例**可复现**（不用靠"记得当时改过什么"）。
