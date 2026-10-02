# FPGA 综合、布线与时序复核指南

本指南对应仓库现存 Vivado 入口及已保存的完整顶层证据。目标是让每次综合有明确输入身份、让功能回归和物理时序分别验收，并给后续板级与 ASIC 实现提供可操作方向。历史 DC/TSMC 28 nm 文档仍在 [README.md](README.md)，其中的环境与数字没有在本轮重新实测，不能用于证明当前 RTL 的 ASIC PPA。

逐阶段的输入、操作、必读报告、通过条件和失败回退，真实顶层 I/O 预算表、ASIC 资料门禁及受控 PPA 实验，见 [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md)。其中的后续实验保持计划身份；本指南的现存命令和历史结果各有明确范围。

## 1. 已测结论及适用范围

以下数字属于 `71e7d5a` 所用的冻结源码包，原始记录见 [综合归档](../docs/evidence/20260930/shared_old_trial_synth/)、[布线归档](../docs/evidence/20260930/shared_old_trial_route_25ns/) 和 [完整网表归档](../docs/evidence/20260930/shared_old_trial_full_mapped/)。工程排版版只改变注释和空白，须以有效 SystemVerilog token 恒等检查建立对应关系；它有新的文件字节 SHA，不能把旧源包 SHA 写成当前源码身份。若下一次改了任何有效 token、宏、字符串、参数或文件列表，应重新综合并重新绑定网表、布线和回归证据。

| 条件/结果 | 已记录值 | 可以说明什么 |
|---|---|---|
| 工具/器件 | Vivado 2018.3 / `xc7vx690tffg1761-2` | 该工具、该速度等级的 FPGA 结果 |
| 重构配置/时钟 | P5 / 25 ns / uncertainty 0.05 ns | 40 MHz 约束；不是 640 MHz ASIC 验收 |
| 综合资源 | 108,842 LUT，66,492 FF，20 DSP，0 BRAM，0 latch | 完整顶层综合资源，相对直接前代 LUT 增加 2.483% |
| 综合后 setup / 全路径 hold | +10.640 / −0.264 ns | 估算互连的综合结果，不能替代实际布线 |
| 布线后寄存器间 setup / hold | +0.946 / +0.052 ns | 记录条件下核心寄存器时序通过 |
| 布线后全 OOC hold | −2.278 ns，65,048 个失败终点 | 外部接口时序仍未闭合 |
| 实现完整性 | 全部路由，0 route error，0 DRC error；16 类 `check_timing` 问题为 0 | 工具流程及所记录约束覆盖检查通过，不等于板级约束正确 |
| 完整综合网表功能仿真 | 438 输出、3,900 配置读回、7,680 协议检查、3 次取消 | 公共端口功能见证；无 SDF，不证明纳秒时序 |

实际结果同时保留 `REGREG_TIMING_MET=1` 与 `TIMING_MET=0`。固定 16 拍启动间隔下，40 MHz 对应 **2.5 MS/s**。本次没有更高频率的重新实现扫描，不能用 `25-WNS` 直接发布更高保证频率，也不能由 20-bit 输出宽度推断 20-bit ENOB 或论文动态范围。

## 2. 工具入口与运行位置

| 文件 | 运行位置/真实接口 | 行为 |
|---|---|---|
| [run_vivado_ssh.py](run_vivado_ssh.py) | 本机 Python；SSH 主机显式传入 | 冻结源包、隔离远端目录、运行综合、收回报告 |
| [run_vivado_ooc.tcl](run_vivado_ooc.tcl) | Vivado；四个位置参数 `PART OUT PERIOD_NS P_RECON_STAGES` | 完整顶层 OOC 综合，输出 `post_synth.dcp` |
| [run_vivado_buffered_impl.tcl](run_vivado_buffered_impl.tcl) | Vivado；四个位置参数 `INPUT_DCP NEW_OUT_DIR PERIOD_NS BUFGCTRL_XxYy` | 从既有 DCP 插入真实 BUFG、opt/place/phys_opt/route、报告 setup/hold |
| [run_vivado_full_mapped.py](run_vivado_full_mapped.py) | 安装 Vivado/XSim 的机器；不是 SSH 驱动 | 验证 DCP 身份、导出完整功能网表、运行 XSim、复核 trace |
| [run_vivado_full_mapped.tcl](run_vivado_full_mapped.tcl) | Vivado；四个位置参数 `INPUT_DCP NEW_OUT_DIR EXPECTED_PART MANIFEST_P_RECON_STAGES` | 只导出 `write_verilog -mode funcsim`，不综合、不布线 |

**仓库没有 `run_vivado_buffered_impl.py`。** 布线通过真实 Tcl 入口执行，远端传输与启动包装要另行保存；完整网表 Python 也没有 `--host` 参数，不能在 Mac 上把 Windows 工具路径当成本地可执行文件。

当前已测主机别名为 `windows-codex`，Vivado 为 `D:/Academic/Vivado2018/Vivado/2018.3/bin/vivado.bat`，远端 Python 为 `C:/Users/Administrator/miniconda3/python.exe`。主机 IP 可由现存 SSH 配置解析；使用别名避免在脚本、报告里各维护一份地址。

## 3. 完整顶层综合：输入必须在优化前冻结

从仓库根执行以下命令。脚本默认 host 是历史 Linux VM、默认周期 1.5625 ns、默认 P7，因此本轮条件全部显式传入。

```bash
python3 synth/run_vivado_ssh.py \
  --host windows-codex --remote-os windows \
  --vivado D:/Academic/Vivado2018/Vivado/2018.3/bin/vivado.bat \
  --part xc7vx690tffg1761-2 --stages 5 --period 25 \
  --timeout 14400
```

追加 `--prepare-only` 只创建本地包，不联系远端、不启动 EDA；去掉该选项重新调用会创建另一份新包，不是在原目录续跑。`--settings` 只用于 Linux。综合支持 `--stages 5..63`，完整网表 runner 当前只接受 P5/P7；P6 的 RTL 功能通过不能写成已有 P6 完整综合网表证明。

脚本按 `rtl/rtl_sources.f` 读取 `.sv`，加入所有 `rtl/params/*.vh`、源列表和综合 Tcl；不是随意 glob 所有历史 RTL。它生成 `synth/artifacts/vivado/<UTC>_p5/manifest.json` 和 `source.tar.gz`，远端使用新的 `adc_rtl_vivado/run.<GUID>`，不覆盖既有实验。保留工作树状态、Git commit、逐文件 byte SHA、Tcl、XDC、器件、工具版本、P5 参数及周期，避免“换了一份头文件却仍引用旧报告”。

Tcl 在综合前创建 `core_clk` 和 0.05 ns uncertainty，为非时钟输入与全部输出设置零延迟，然后执行：

```tcl
synth_design -top sar20_digital_core -part $part -mode out_of_context \
  -flatten_hierarchy rebuilt -generic P_RECON_STAGES=$stages
```

零 I/O 延迟是 OOC 筛查假设，不是板级预算。`rebuilt` 可能迁移逻辑归属，不能把某个层级的 LUT 行当作该源模块独立面积，也不能把父子层级资源相加。

综合接受条件是完整 manifest/status、工具版本/器件/参数一致、存在有效 DCP、无黑盒、无 `[Synth 8-3848] ... does not have driver`。脚本会拒绝这种 undriven 综合网表，即使工具产出了正 setup。退出 `0` 仅表示综合完成且 setup 筛查通过；`3` 表示完成但 setup 为负；其他非零是流程失败。Windows Vivado batch 包装可能把负 setup 的 raw 退出码返回为 0，脚本保存 raw 值并据完整 status 归一化，不以一条 `PASS` 字符串覆盖错误。

## 4. 冻结 DCP：来源校验与文件摘要是两道检查

先人工核对 `manifest.json` 的接受状态、参数、日志和产物完整性，再对源包成员与 `sources_sha256` 逐一比对。需要确认源包实际字节，不仅检查当前工作树；打包后修改本地文件不能改变当次输入。随后记录完整 `post_synth.dcp` 的字节数、SHA-256、本地路径、原始远端路径及 manifest SHA。传输后两端独立计算摘要，匹配后才能交给下一阶段。摘要相同能确认文件身份，但摘要本身不证明综合有效，不能跳过前面的来源检查。

以下本地检查只读既有源包和 DCP，写一份新的身份记录，不运行 EDA。先把任务变量 `SAR_SYNTH_RUN` 设为本次脚本打印的目录。

```bash
python3 - "$SAR_SYNTH_RUN" <<'PY'
from pathlib import Path
import hashlib, json, sys, tarfile
run = Path(sys.argv[1]).resolve()
digest = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
m = json.loads((run / 'manifest.json').read_text())
assert m['status'] in {'SYNTH_COMPLETE_TIMING_MET', 'SYNTH_COMPLETE_TIMING_NOT_MET'}
assert not m.get('failure_reason') and not m.get('undriven_diagnostics')
with tarfile.open(run / 'source.tar.gz', 'r:gz') as t:
    assert set(t.getnames()) == set(m['sources_sha256'])
    for name, expected in m['sources_sha256'].items():
        assert t.getmember(name).isfile()
        assert hashlib.sha256(t.extractfile(name).read()).hexdigest() == expected
dcp = run / 'out/post_synth.dcp'
assert dcp.is_file() and dcp.stat().st_size > 0
record = {'manifest_sha256': digest(run / 'manifest.json'),
          'source_archive_sha256': digest(run / 'source.tar.gz'),
          'dcp_sha256': digest(dcp), 'dcp_bytes': dcp.stat().st_size,
          'remote_directory': m['remote_directory'],
          'arguments': m['arguments']}
with (run / 'dcp_identity.local.json').open('x') as f:
    json.dump(record, f, indent=2)
print(json.dumps(record, indent=2))
PY
```

旧硬件见证的综合 DCP 为 50,563,819 字节、SHA `63a6faacdc44e54da02514a70ff658ac31756853496114513018ac5714cc856e`；布线 DCP 为 SHA `fba86312b0c96add78ca388b972a52ab77c1b781eec34c1b91027b1ea7358072`。这些身份只属于旧冻结运行。新综合必须填写本次算出的摘要，不把这两个值复制进去制造“同版本”。原始 runner manifest 保持原字节；增加身份/验收记录时另存文件，或保存原件后制作注明来源的 sealed 副本。

## 5. BUFG 与完整路由：执行现存 Tcl

把已冻结 `manifest.json`、`dcp_identity.local.json` 和当前流程脚本复制到远端新的工作根，按仓库相对路径保留 `synth/`、`sim/tb/`。复制完成后复核摘要。下面在该根的 **Windows PowerShell** 执行；DCP 路径取该次 manifest，不猜测历史 GUID。脚本文件应为本次冻结版本。

```powershell
$ErrorActionPreference = 'Stop'
$m = Get-Content -Raw manifest.json | ConvertFrom-Json
$identity = Get-Content -Raw dcp_identity.local.json | ConvertFrom-Json
if ((Get-FileHash manifest.json -Algorithm SHA256).Hash.ToLowerInvariant() -ne $identity.manifest_sha256) {
  throw 'Synthesis manifest differs after transfer'
}
if ($m.arguments.stages -ne 5 -or $m.arguments.period -ne 25 -or $m.arguments.part -ne 'xc7vx690tffg1761-2') {
  throw 'This implementation example requires the recorded P5/25 ns/part profile'
}
$dcp = Join-Path $m.remote_directory 'out/post_synth.dcp'
if ((Get-FileHash $dcp -Algorithm SHA256).Hash.ToLowerInvariant() -ne $identity.dcp_sha256) {
  throw 'DCP identity differs after transfer'
}
$work = Join-Path $env:USERPROFILE ('adc_rtl_vivado/impl.' + [Guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Path $work
& 'D:/Academic/Vivado2018/Vivado/2018.3/bin/vivado.bat' `
  -mode batch -nojournal -log (Join-Path $work 'vivado.log') `
  -source synth/run_vivado_buffered_impl.tcl `
  -tclargs $dcp (Join-Path $work 'out') 25.0 BUFGCTRL_X0Y0
$LASTEXITCODE | Set-Content (Join-Path $work 'vivado_exit_code.txt')
```

此命令确实启动实现，指南编写时没有重新执行。调用结束后读取 `$work/out/status.txt` 及原始日志，保存命令、脚本 SHA、输入 manifest SHA、DCP SHA、stdout/stderr 和 raw 返回码。远端长任务可复用 `run_vivado_ssh.py` 中已用的进程树启动方式，但不存在可直接调用的 buffered-impl Python CLI。SSH 观察超时不能当成 EDA 已停止；先查询同一进程/输出状态，不能重新启动第二份任务或终止用户的其他 Vivado 作业。

实现 Tcl 要求新输出目录、未布线综合 DCP、Vivado 2018.3、7-series、唯一 `core_clk` 和未占用的 BUFGCTRL 站点。它只把外部 `clk` 接到一个真实 BUFG 输入，原寄存器时钟网络由 BUFG 输出驱动；检查全部顺序时钟终点集合在 ECO 前后及布线后一致，而不是抽查几个 FF。然后执行：

```tcl
opt_design
place_design
phys_opt_design
route_design
```

输出 `post_clock_eco.dcp`、`post_route.dcp`、前后端点名单及 SHA、时钟 nodes/pips、路由/DRC、时钟、例外和 timing 报告。原输入 DCP 在结束时重新计算 SHA，确保没有被覆盖。已测 66,508 个时钟终点集合完全一致，内部时钟真实路由；**外部 clk→BUFG 输入仍为 OOC 边界，`EXTERNAL_CLOCK_ROUTED=0`**。OOC 的边界性质见官方 [UG905 v2018.3](https://docs.amd.com/v/u/2018.3-English/ug905-vivado-hierarchical-design)；不能把内部时钟路由当成板级晶振到器件的完整实现。

## 6. 读 STA：setup、hold、覆盖和例外分别验收

`-from [get_clocks core_clk] -to [get_clocks core_clk]` 不是严格的寄存器间路径筛选：相对该 clock 约束的输入/输出端口也可能进入集合。当前 Tcl 使用真实原语 clock/data pin，且保留全路径报告作并列诊断：

```tcl
set launch [all_registers -clock_pins]
set capture [all_registers -data_pins]
report_timing -from $launch -to $capture -delay_type max \
  -max_paths 20 -path_type full_clock_expanded -input_pins
report_timing -from $launch -to $capture -delay_type min \
  -max_paths 20 -path_type full_clock_expanded -input_pins
report_timing_summary -delay_type min_max -report_unconstrained
check_timing -verbose
report_exceptions
```

完整性与数值检查依次为：所有时钟被定义并传播；无未约束/用户忽略路径组；16 类 `check_timing` 计数全部已解析且为零；路由完整、无 route/DRC error；再分别检查 setup 的 WNS/TNS/失败终点、hold 的 WHS/THS/失败终点和脉宽 WPWS/TPWS。解析遇到缺字段、未知类别、重复类别或缺路径必须失败，不能按零处理。报告方法参考官方 [UG906 v2018.3](https://docs.amd.com/v/u/2018.3-English/ug906-vivado-design-analysis)，具体查询接口见 [UG835 v2018.3](https://docs.amd.com/v/u/2018.3-English/ug835-vivado-tcl-commands)。

setup 和 hold 通常不是同一条最差路径。setup 正裕量不证明 hold；只有 `check_timing=0` 也不证明 I/O 延迟有真实来源。当前全路径 hold 为负时必须保留 `TIMING_MET=0`，即使寄存器间时序通过。不能添加无依据的 `set_false_path`、multicycle、`CLOCK_DEDICATED_ROUTE` 例外或正 input minimum delay 来使状态变绿。多周期也不能仅由“16 拍一个样本”推导：内部寄存器可能每拍更新，必须证明其真实捕获使能和数据稳定条件，并处理 setup/hold 的配对约束。

## 7. 完整综合网表：证明功能，没有替代 STA

在含当前 `synth/` 和 `sim/tb/structural_mapped_tb.sv` 的远端根运行下面的 PowerShell 命令。沿用上节通过校验的 `$dcp`、`$identity`，指定新输出目录。runner 只支持 P5/P7，默认 prepare-only；`--execute` 才启动 Vivado、xvlog、xelab、xsim。

```powershell
$mappedOut = Join-Path $env:USERPROFILE ('adc_rtl_vivado/mapped.' + [Guid]::NewGuid().ToString('N'))
& 'C:/Users/Administrator/miniconda3/python.exe' synth/run_vivado_full_mapped.py `
  --dcp $dcp --synthesis-manifest manifest.json `
  --dcp-sha256 $identity.dcp_sha256 --stages 5 `
  --vivado D:/Academic/Vivado2018/Vivado/2018.3/bin/vivado.bat `
  --out $mappedOut --timeout 3600 --execute
```

runner 在输入捕获前确认综合 manifest 接受状态、part/P5 一致及 DCP SHA，复制到只属于本次输出的 `input/`；导出完整顶层功能网表并连接真实 vendor primitive 库和 `glbl`，然后复核每个输出、flags、ID、配置读回、取消与 trace 结束条件。还要由调用方确认源包、流程脚本和 testbench 的真实摘要；runner 的 DCP 验证不能替代整个 source package 校验。

本轮完整网表记录为三种模式各 146 行，共 438 输出，435 对相邻有效输出满足 `Δclock=16×ΔID`；另有 3,900 配置读回、7,680 协议检查、3 次取消。此 bench 的全部观测 flags 为零，未核对内部启动延迟；非零异常位与内部拍数由单独 RTL benches 覆盖。它是 **post-synthesis functional simulation，无 SDF**，没有覆盖布线延迟、模拟建立或真实比较器亚稳态。只有从同一输入 DCP 出发的功能和物理两条证据均成立，才能说“该冻结硬件核心的数字功能和记录条件下寄存器时序通过”。

## 8. 修复外部 hold：先补接口事实，再修改 XDC

当前 OOC `set_input_delay 0.0` 同时作用于 min/max，假设外部数据可在参考边沿立刻到达，却没有实现外部时钟树。板级输入 `-min/-max` 应来自发送端最早/最晚 clock-to-out、数据/时钟走线与封装差、参考相位和抖动；输出约束应来自接收端 setup/hold 及板级相位关系。具体 min/max 含义与输入相位可正可负见官方 [UG903 v2018.3 第 100–103 页](https://docs.amd.com/v/u/2018.3-English/ug903-vivado-using-constraints)。

下一阶段为 Flash、粗/细比较器、配置接口、数据输出逐组建立时序预算表，记录数据来源、参考边沿、最早/最晚到达和同步方式；补真实引脚、I/O standard、时钟输入结构及 package/板级约束，再从新的板级工程完整布局布线。当前接口假定外部比较器 valid/data 满足统一 `clk` 的建立/保持；若实际异步，必须设计并验证 CDC/握手或明确模拟宏的同步时序契约，不能给整组比较器数据简单设 false path。

若真实 I/O 约束下仍有 hold 失败，先定位最短数据链、时钟相位/偏斜和发送端边界，再让实现工具修补短路径或修改接口寄存边界。核对功能拍数、输入可接受时间及量化器窗口后重跑；不能只延长时钟周期来假定 hold 被修复。现有 Tcl 固定零 I/O 筛查，不能用它发布板级签核；板级流程必须保存独立 XDC 和 scope。

## 9. 继续优化 PPA：从实测路径而非源文件外观出发

修复后最差 setup 为 `u_structure/u_context/fine_slices_reg[1][3]/C` → `u_recon/rails_s_reg[65]/D`，数据延迟 23.914 ns，逻辑 5.291 ns、互连 18.623 ns，**布线占 77.875%**，40 级逻辑。下一轮优先检查物理 ID 译码、活跃行掩码扇出、系数归约和 rail 符号处理的实际网连接/位置，比较以下两类方案：

- 保持位精确算法，利用已冻结的 fine 样本上下文，在后级 SAR 期间分段预计算 `T/G/R`，phase15 再结合最终 `F`。中间量必须带同一 sample ID、epoch、valid/bad，clear/disable/cancel 后失效；不引用下一样本的实时 DEM。逐级切分、局部寄存与布局分别做一次受控对照，记录新增 FF、路径变化及 16 拍吞吐。
- 降低系数读取带宽后，比较分银行 RAM/SRAM 或结构 DEM 的前缀表示。当前 `weight_store` 的 62,316 FF = 60,066 权重有效位 + 1,278 written 位 + 972 行和位，占全核 66,492 FF 的 **93.72%**。保持全部 18×71 并行系数读口仅改数组名字，不会自动得到 BRAM。若保留原权重又追加朴素前缀缓存，会额外增加 72,432 状态位，不宜直接采用。

`T/G/R` 预计算和前缀表示是后续候选，当前生产 RTL 尚未实现其综合收益。不能缩窄合法权重/96-bit 运算域来绕过误差证明；不能为了 DSP 启用内部流水而未经复核改变 latency/ID/flags 对齐。每次只改变一个结构变量，同工具、part、XDC、P5 与 BUFG 条件下比较实际 route，再按路径数量、扇出与拥塞决定是否需要 floorplan。全局 `DONT_TOUCH` 或过紧区域可能放大拥塞，不是默认修复。

最终 vectorless 报告 0.477 W 总功耗、0.152 W 动态、0.325 W 静态，没有仿真 activity file。后续从配置装载、连续转换、DEM/dither、取消等真实模式生成活动窗口并检查注释覆盖，才能比较功耗；目前不能据这个估值宣称功耗优势。

## 10. ASIC 640 MHz 的后续验收

ASIC 目标周期是 1.5625 ns。FPGA CARRY4/DSP/BUFG 的延迟和资源不能换算成标准单元面积或 ASIC Fmax；本轮没有目标 PDK/库角/RC 条件下的当前 RTL 签核。历史 README 的 DC、TSMC 路径与 IP 都只是待复核记录，不应从旧机房信息推断当前可用 license 或 PDK。

先确认获授权的 `.lib/.db`、LEF/technology、RC corners、供电/温度、单位、memory macro 与模拟宏时序模型，再冻结 ASIC 源码/参数、1.5625 ns 时钟及真实 I/O budget。检查 elaboration/link、latch/blackbox、多时钟/CDC、关键寄存器和算术位宽；综合报告注明工艺、库、角、驱动和负载单位，不能把含零线负载模型的 setup 当成布线 Fmax。

物理流程依次做 floorplan/placement、时钟树、路由与寄生提取，在要求的模式与角下检查 setup、hold、recovery/removal、脉宽及约束覆盖。慢角不一定对所有路径最坏、快角也不自动涵盖所有 hold 场景，应依项目库/RC 组合建立 multi-corner multi-mode 集合；跨模拟宏路径需要真实输入延迟、输出负载与时钟关系。保存 SPEF/SDC/网表/工具版本和报告摘要，并检查电源、面积、拥塞及长线修补的代价。只有真实全核、真实宏边界与要求 corners 全部通过，才能宣称 ASIC 640 MHz 达标。

## 11. 每个里程碑的交付与门禁

| 里程碑 | 必须保留的证据 | 失败时的下一步 |
|---|---|---|
| 源码工程化 | 当前逐文件 byte SHA、有效 token 对照、RTL 测试/lint | token 有差异则作为硬件改动验收，不继承旧结果 |
| 综合 | 原始 source tar/manifest、Tcl/XDC、日志、状态、资源和完整 DCP 身份 | undriven/黑盒/缺字段先修流程或结构，不做 PPA 结论 |
| 布线/STA | 原始 DCP 身份、真实 clock/data pin 分组、路由/DRC/覆盖及 min/max 报告 | 分清内核与 I/O、logic 与 route、setup 与 hold |
| 完整网表功能 | 同一 DCP、导出网表身份、vendor 工具日志、trace 与测试范围 | 功能不符先查网表和参数绑定，再查位精确 oracle |
| 板级接口 | 有来源的最早/最晚 I/O budget、实际时钟/引脚、全路径 STA | 补真实条件或修改接口，不伪造延迟/时序例外 |
| ASIC/PPA | 获授权库/RC/MMMC、完整物理实现、活动数据与误差预算 | 640 MHz 或功耗未达标继续结构/物理对照，不以 FPGA 结果代替 |

重跑与可读性修订都应新增运行 ID 和自己的 manifest，保留历史失败与被否决候选。全文引用的旧数字可逐项回到原始档案；执行示例只描述当前脚本接口，不意味着已替新排版字节跑过一次 EDA。
