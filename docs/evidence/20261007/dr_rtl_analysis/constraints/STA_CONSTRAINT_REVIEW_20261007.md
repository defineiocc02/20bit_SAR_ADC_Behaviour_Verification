# 20 位 SAR RTL：Dr. RTL 约束、STA 与综合风险复核

日期：2026-10-07（Asia/Shanghai）  
仓库：`/Users/zhaoyi/Obsidan/项目库/outputs/sar_adc_review_20260919/calibration-closure`  
复核 HEAD：`01ed2b3e4fc4c7c9efd2153cfbfa5d456b10fb74`  
目标顶层：`sar20_digital_core`；当前默认 `P_STRUCTURAL=1, P_RECON_STAGES=7`。

## 1. 结论先行

1. 本工程目前有可复查的 Vivado 2018.3 历史综合与布线报告，但没有对本复核 HEAD 逐字节重新综合/布线的报告。历史 P5 物理包与本 HEAD 的 RTL 文件 SHA 不同；单独的 `baseline/source_identity.json` 显示其 26 个 RTL 源文件在剔除注释和空白后 token 一致。这把历史 P5 结构与当前 P5 代码联系起来，但不证明当前 DCP 身份、形式等价或当前工具结果。历史 P7 报告未在本次建立相同 token 身份桥。
2. P5 的历史 25 ns FPGA 布线只证明内部寄存器路径在 `xc7vx690tffg1761-2`、40 MHz 条件下 setup/hold 通过；全路径仍有 65,048 个 hold 失败端点，最差 `-2.278 ns`。失败路径从零延迟配置输入 `cfg_wdata[3]` 到权重存储 FF。它既不是板级接口闭合，也不是 640 MHz ASIC 验收。
3. 当前默认 P7 与历史 P5 是不同的除法器流水参数。P7 每拍串联 7 个 64 位 restoring step，接受到有效结果 11 拍；P5 每拍 5 个 step，15 拍。P5 理论上仍满足 16 拍帧，但只留 1 拍余量；P7 留 5 拍。P5 通常降低每拍除法器组合深度、P7 通常降低运算总拍数，这些是结构推论，必须在同一 RTL、工具、库和约束下验证。现有 P5/P7 数字条件不同，不能直接作优劣比较。
4. 当前 Vivado OOC XDC 与历史报告采用 `set_input_delay 0`、`set_output_delay 0`，因此 `check_timing` 可显示无未约束路径，但没有真实外部发射/接收预算。ASIC 方面，本仓库没有可绑定的 Liberty、宏时序、RC、MMMC/OCV、IO 预算和目标签核角。现有 DC 脚本的零延迟默认值也不是验收约束。
5. 本工程没有已生效的 multicycle path。`set_multicycle_path` 不在综合/布线脚本中，重开的 P5 DCP `exceptions.rpt` 六类例外均为 0。phase0 fine-context→phase15 Stage-A 有15个间隔，phase9 residue→phase0 fine-context 约有7个间隔，均只是需要严格验证的窄范围候选；相位14产生的 `fine_code` 到相位15 Stage-A 只有1个间隔。不能把整帧16周期或整个 `u_context → u_recon` 设为MCP。
6. 目前最有依据的候选方向是减少真实路径上的高扇出和跨区布线、对当前 P5/P6/P7 做同条件参数扫、最后才评估精准 context MCP。依 Dr. RTL 流程，任何候选在等价和相同条件 STA/面积对照完成前都不应晋级；本复核未改 RTL，也未运行新 EDA。

## 2. 范围、身份和方法

本次依本机 `asic-rtl-timing` skill 核对工程源、约束脚本、原始报告和 Dr. RTL 策略库。本代理仅做本地只读检查，没有运行 Vivado、Design Compiler、OpenSTA 或形式工具。审查过程中另有远端只读重开历史 post-route DCP 并导出报告；`execution.json` 与 `scope.txt` 证明没有综合、布局、布线或约束编辑。没有安装工具，也没有修改仓库 RTL。

源文件默认参数来自当前 `rtl/top/sar20_digital_core.sv`。该源码声明 P7 默认，P5 是显式参数配置。当前 P5 的 source-token 身份桥位于：

`/Users/zhaoyi/Obsidan/项目库/outputs/sar_adc_dr_rtl_analysis_20261007/baseline/source_identity.json`

身份桥记录当前 HEAD、顶层、参数，以及历史 P5 manifest 对应的 26 个 RTL 文件 SHA 与 token 比较。它的限定语是“token 相同（忽略注释/空白），不是顺序等价，也不是当前 DCP 身份证明”。因此下文 P5 路径用于解释该结构 profile 的既有物理行为，不冒充本轮当前字节的重新实现结果。

P7 OOC 报告来自 `docs/evidence/20260930/vivado_complete_p7`。该归档显示 P7 baseline 与 candidate 都是旧源包，candidate 同时修改 reducer 与 divider；目前没有证据把该二者结果单独归因，也没有在本次确认两份 P7 源包与 HEAD token 一致。P7 数字只作历史风险说明。

## 3. 工程约束入口审查

### 3.1 Vivado OOC screening

`synth/run_vivado_ooc.tcl` 生成并读入 XDC：

```tcl
create_clock -name core_clk -period $period [get_ports clk]
set_clock_uncertainty 0.05 [get_clocks core_clk]
set_input_delay 0.0 -clock core_clk [get_ports -filter {DIRECTION == IN && NAME != clk}]
set_output_delay 0.0 -clock core_clk [all_outputs]
```

综合 profile 由 `-generic P_RECON_STAGES=$stages` 决定；默认 25 ns 示例是 FPGA screening。该脚本明确称其不是 ASIC PPA/signoff。0 延迟是显式实验假设：每个输入输出都被计入路径，因此工具可能报告“没有缺少 I/O delay”，但此值没有板级、PAD、宏接口或接收端依据。

### 3.2 BUFG 路由检查入口

`synth/run_vivado_buffered_impl.tcl` 从旧 OOC DCP 插入一个 `BUFG`，在 `xc7vx690tffg1761-2` 指定 `BUFGCTRL_X0Y0`，然后做 opt/place/phys-opt/route。约束保留单个 `core_clk`、0.05 ns user uncertainty，并设置 propagated clock。它特意保留零输入/输出延迟、未路由外部时钟，也没有加 false path 或 MCP。因此它可分析内部时钟网络和历史 OOC 边界下的路径，不能等同完整板级时序。

该流程分别输出 all-path 与真正 FF clock-pin→FF data-pin 的 max/min 路径；状态也分别记录 `TIMING_MET` 与 `REGREG_TIMING_MET`。这种拆分有效避免把内部寄存器通过误写成全接口通过。

### 3.3 Design Compiler 脚本默认约束

`synth/run_dc.tcl` 以 `$CLK_PERIOD` 建时钟，输入输出 delay 分别由 `IN_DELAY/OUT_DELAY` 给定，默认均为 `0.0`；输入 drive 使用环境给的 drive cell，输出按 `$LOAD_PF` 加载；uncertainty 是周期的 2%，clock/input transition 是周期的 5%。这些默认适合流程冒烟/相对筛查，不是 ASIC 接口预算。历史 README 记有外部 TSMC 28 nm HPC+ TT RVT `.db` 和其它角，但当前 repo 没有该数据库或宏/物理库文件，且文档明确写这些旧 DC 结果未在当前轮复测。不能把文档参数当成当前已绑定的实际库角。

### 3.4 目前缺少的真实约束输入

在 repo 中没有被本次确认的 current-head 签核 SDC/XDC。要做真实 ASIC 640 MHz STA，至少还需：

- 1.5625 ns 时钟目标对应的源、jitter、latency/uncertainty、占空比与 PVT/MMMC 角；
- 与综合网表匹配的 Liberty `.lib/.db`，包含目标标准单元、时序模型、IO/level shifter、存储器和模拟宏 black-box timing arcs；
- RC extraction / wire-load 或 SPEF 及相应角、OCV/AOCV/POCV、CPPR 和 CTS 时钟模型；
- 对每一组真实输入/输出规定 min/max delay、参考边沿、发送端 clock-to-out、接收 setup/hold、驱动、负载、封装/板级偏差；
- Flash/比较器、ADC2、RA、RDAC、reference/AZ/采样控制等模拟宏的最早/最晚响应、稳定窗口、脉宽和非重叠要求；
- reset/enable/config 的来源及同步性合同，真实管脚/接口模式和工作模式。

在这些信息缺失时，工具可测“内部逻辑在给定 screening 约束下的路径”，但不能得出 ASIC 时序闭合或 PPA 优势。

## 4. 历史真实 Vivado 证据及其边界

### 4.1 P5 post-synthesis

归档：`docs/evidence/20260930/shared_old_trial_synth/`；Vivado 2018.3、`xc7vx690tffg1761-2`、P5、25 ns OOC。`readings.json` 报告 108,842 LUT、66,492 FF、20 DSP、0 BRAM、0 latch。综合时 setup WNS `+10.640 ns`、hold WHS `-0.264 ns`，hold failing endpoints 61,404 / 200,305。该流程 status 判据主要看 setup；因此 `SYNTH_COMPLETE_TIMING_MET` 不能解读为 setup/hold 均通过。最差 post-synth setup path 是 `u_recon/u_div/rem_reg[0]/C` → `u_recon/u_div/q_reg[60]/D`，数据延迟 14.312 ns，其中 cell 8.404 ns、估算互连 5.908 ns、110 logic levels（93 CARRY4）。这是估算互连，不是 route delay。

### 4.2 P5 post-route

归档：`docs/evidence/20260930/shared_old_trial_route_25ns/`，冻结输入 DCP SHA-256 `63a6faacdc44e54da02514a70ff658ac31756853496114513018ac5714cc856e`；post-route DCP SHA-256 `fba86312b0c96add78ca388b972a52ab77c1b781eec34c1b91027b1ea7358072`。Vivado 2018.3、7vx690t-ffg1761、-2 speed file、25 ns，内部 BUFG 已布线；外部时钟没有布线。

| 指标 | all-path | FF→FF（寄存器 clock pin 到 data pin） |
|---|---:|---:|
| setup WNS | +0.946 ns | +0.946 ns |
| hold WHS | -2.278 ns | +0.052 ns |
| setup 失败 endpoints | 0 | 0 |
| hold 失败 endpoints | 65,048 / 200,285 | 0（该过滤集） |

布线完整、route errors=0、DRC errors=0、check_timing 报告的 16 类检查为 0，未约束路径组 0；这些均只在所记录的 OOC 零 I/O delay 条件下成立。

真实最差 FF→FF setup 路径：

`u_structure/u_context/fine_slices_reg[1][3]/C` → `u_recon/rails_s_reg[65]/D`

其 data delay `23.914 ns`，logic `5.291 ns`，route `18.623 ns`（`77.875%`），40 primitive levels（21 CARRY4 + 19 LUT）。路径包括 ID/片选择、掩码逻辑、高扇出网络和归约/加法。`structural_ids[1][3]` net fanout=81；一个匹配信号 net fanout=72、该段 route=2.434 ns；`physical_on34_out[50]` fanout=47、该段 route=4.375 ns。证据指向局部性和连线负担，而不仅是 RTL 运算符数量。注意 rebuilt hierarchy 会跨源模块移动逻辑，不能按 hierarchy 表把 LUT 精确归属到某个 RTL 模块。

最差 all-path hold 是：

`cfg_wdata[3]`（input port，input delay=0）→ `u_wstore/w_q_reg[13][36][3]/D`

data delay `0.510 ns`，其中 0 logic、0.510 ns route；该 sink clock delay `2.476 ns`，hold uncertainty `0.085 ns`，WHS `-2.278 ns`。`cfg_wdata[3]` 在该网表中有 1320 fanout。由于接口 min delay 被写为 0，不能判断真实发送端 clock-to-out 与板级/片上到达差。要闭合这个报告的 OOC 边界，可由真实输入 min delay、时钟到达、buffer/IO 和端点 skew 一起重算；不能以 RTL 任意插逻辑、添加正 min delay 或假路径直接把数字变绿。

内部最差 hold 则是 `u_structure/u_context/residue_reg[570]/C` → `u_structure/u_context/fine_main_reg[6][59]/D`，WHS `+0.052 ns`，只有约 52 ps 裕量。这提醒即使在 25 ns FPGA 约束下，真实内部 hold 也很紧；ASIC 快角/CTS/OCV 需重新检查。

#### 2026-10-07 对冻结 P5 DCP 的只读重导报告

远端 Vivado 2018.3 重新打开既有 post-route DCP，仅重导时序、例外、clock、扇出和利用率报告。`baseline/reopened_route/execution.json` 记录输入与输出 DCP SHA-256 均为 `fba86312b0c96add78ca388b972a52ab77c1b781eec34c1b91027b1ea7358072`，并注明未综合、未布局布线、未编辑约束；`scope.txt` 标明 P5、`xc7vx690tffg1761-2`。这是对旧实现的报告复核，不是 current HEAD P7 的新物理实现。

重导 `loaded_constraints.xdc` 与旧条件一致：`core_clk=25.000 ns`，user uncertainty `0.050 ns`，所有非 `clk` 输入和所有输出 delay 均为 `0.000 ns`，时钟传播，`BUFGCTRL_X0Y0`。报告确认当前 DCP 上六类例外均为 0（false path、clock groups、multicycle、max delay、max delay datapath-only、min delay）；所以 MCP 并未实际生效。`check_timing.rpt` 列出的 12 类检查均为 0 个问题，且零未约束路径的结果受“所有 IO 均已显式赋零 delay”这一筛查假设限制。原实现归档的 status 摘要按其报告清单计为 16 类检查；本次重新导出的 Vivado `check_timing.rpt` 实际列出 12 类，两者均为零问题但统计清单不同，不能把 12 和 16 相加或当作 28 项独立检查。

| 重导报告范围 | 首条路径 / 结果 | 对 RTL 与约束的解释 |
|---|---|---|
| 全局 max/setup | `u_structure/u_context/fine_slices_reg[1][3]/C` → `u_recon/rails_s_reg[65]/D`；WNS `+0.946 ns`，数据 `23.914 ns`，logic `5.291 ns`、route `18.623 ns`（`77.875%`），40 个 primitive levels（21 CARRY4 + 19 LUT） | FF→FF，始于 `fine_slices` launch FF 的 clock pin，经过片选/权重归约到 Stage-A `rails_s` 捕获 FF。布线占主导，不能只按 RTL 算术级数判断。结合相位合同，它是 phase0 fine context 到 phase15 recon Stage-A 的首要 15T MCP 候选，但当前仍按 1T 计时。|
| 全局 min/hold | `cfg_wdata[3]` → `u_wstore/w_q_reg[13][36][3]/D`；WHS `-2.278 ns`，data `0.510 ns`（0 logic、全 route），65,048 个 all-path failing endpoints | 顶层输入 min delay 被设为 0，路径没有真实外部发送端 clock-to-out/板级最早到达预算；不能靠任意 delay 或 false path 消除。|
| FF→FF min/hold | `u_structure/u_context/residue_reg[570]/C` → `u_structure/u_context/fine_main_reg[6][59]/D`；WHS `+0.052 ns`，data `0.263 ns`（0 logic levels） | 与 phase9 `residue_capture` 到下一个 phase0 `quiet_sample` 的 residue→fine packet transfer 相符，RTL 序列约 7 个完整时钟间隔。它可作为另一条窄范围 7T MCP 研究候选；并非已批准约束。|

重导 `high_fanout.rpt` 的前几项为 `u_wstore/SR[0]` 61,236、`u_wstore/u_idx[2]` 6,776、`u_calib/phase0` 1,864、`u_structure/u_phase/phase_reg[3]_2[0]` 1,369、`u_calib/cfg_ready_reg_4[0]` 1,368、`u_wstore/cfg_clear_valid_0` 1,323、`cfg_wdata[46]` 1,322（大多数 `cfg_wdata` 位为 1,321）。`u_wstore/SR[0]` 的实例名和 LUT1 驱动与 `weight_store` 内 `if (!rst_n)` 的同步复位网络一致；报告未导出 sink pin 清单，因此“复位网络”是结合源码和网名的强推断，不把 61,236 直接等同于具体 FF 数。`u_idx[2]` 和配置输入扇出则指向动态索引/广播写数据的布线压力。`high_fanout.rpt` 对 `cfg_wdata` 的扇出多数列为1,321，而详细 `timing_min_paths.rpt` 对 `cfg_wdata[3]` 路径网标为1,320；这是不同报告命令的统计口径差异，路径报告中的 net fanout 用于解释具体时序段，高扇出报告用于排序筛选，不将两数混成同一精确 load 计数。

该扇出清单只说明网络负载规模，不表示这些网都在最差 setup 路径上；max 路径自身可确认的是 `structural_ids` fanout 81、其匹配网 fanout 72，以及 `physical_on34_out[50]` fanout 47。必须对候选复跑 net timing、placement、route 和 setup/hold，才能判断局部复制是否改善了关键路径。


### 4.3 P7 1.5625 ns post-synthesis screening

归档：`docs/evidence/20260930/vivado_complete_p7/`，Vivado 2018.3、同一 Virtex-7 part、P7、请求时钟周期 1.5625 ns（报告显示 1.563 ns）、0.05 ns user uncertainty，输入输出 delay=0。无 BUFG 和 actual route，报告有 `Timing 38-242`，表示 `HD.CLK_SRC` 未设置导致时钟延迟/偏斜估算受限；不应把 639.795 MHz constraint 显示值称为 Fmax。

baseline WNS `-17.426 ns`、WHS `-0.264 ns`，setup/hold failing endpoints 分别 71,067 / 61,733。最差 setup 为 `u_recon/u_div/dv_reg[0]/C` → `u_recon/u_div/q_reg[60]/D`，data `18.941 ns`（logic `12.734 ns` + estimated route `6.207 ns`），135 levels，其中 117 CARRY4。它明确暴露 P7 divider 的每拍组合链长度。

同归档 P7 candidate 同时改变 `cal_weight_reduce.sv` 与 `div_floor.sv`，不能隔离单项贡献。candidate LUT 从 171,246 增为 199,039（+16.23%），WNS 改善 0.314 ns 到 `-17.112 ns` 但仍失败，WHS仍 `-0.264 ns`，setup failing endpoints 仍69,461，hold failing endpoints 61,735。candidate 没 route，属于 combined old-source 实验；仅说明这组修改在该历史 screen 下没有闭合，也没有显示 PPA 优势。

## 5. P5 / P7 参数和功能时序

当前 RTL 默认 P7。按当前 `recon_core` 注释和计算 `ceil(63/P_STAGES)+2`：

| profile | 每拍串行 divider step | divider loop 次数 | 接受 start 到 `dout_valid` 完整时钟间隔 | 16 拍帧余量 |
|---|---:|---:|---:|---:|
| P5 | 5 | 13 | 15 | 1 |
| P6 | 6 | 11 | 13 | 3 |
| P7 默认 | 7 | 9 | 11 | 5 |

P5 并非已被当前默认 profile 采用；当前 P5 route 报告要求外部显式传 `P_RECON_STAGES=5`。降低 P_STAGES 会减少 divider 单拍链中的连续减法/选择级数，通常对 setup 有利，但增加运算周期；P5 余量仅1拍，任何启动/提交/错误归属/清除使能改变都可能破坏16拍节拍。P7余量较大但每拍组合工作更深。已有数值不可用来宣称哪个在640MHz下更快，因为P5实测是25ns FPGA、P7实测是1.5625ns FPGA screening，库/参数与源身份也不完全匹配。

## 6. MCP：报告事实与可行边界

### 6.1 当前没有任何生效 MCP

当前 `run_vivado_ooc.tcl` 和 `run_vivado_buffered_impl.tcl` 均没有 `set_multicycle_path`。重开 DCP 的 `loaded_constraints.xdc` 也没有 MCP；对应 `exceptions.rpt` 六类例外计数（false path、clock groups、multicycle、max、max datapath-only、min delay）全部为0。因此既有 WNS/WHS 都是未加 MCP 的一拍约束结果。文档中的候选讨论不是工具已采纳的约束。

### 6.2 两类窄范围相位路径可研究 MCP

当前相位控制给出两种可能的精确时序关系。第一种是 `cal_sample_context` 在 `quiet_sample` / phase0 把 residue packet 转入 `fine_*` context，随后 `recon_start` 在 phase15 为真，`recon_core` 在该沿捕获 Stage-A。对明确由 phase0 更新、phase15 捕获且期间稳定的 exact context FF→Stage-A FF 路径，架构上有15个完整时钟间隔。重导 max 首路径 `fine_slices_reg[1][3]` → `rails_s_reg[65]` 正好落在该类 cone，是首先应核对的端点白名单；以 640 MHz 计算 nominal window 是 `15 × 1.5625 = 23.4375 ns`，但这只是周期关系，不是 ASIC slack。

第二种是 `residue_capture` 在 phase9 写入 `residue`，下一个 phase0 `quiet_sample` 将它移入 `fine_*`，约有7个完整时钟间隔。重导 FF→FF min 首路径 `residue_reg[570]` → `fine_main_reg[6][59]` 与这段数据包转移相符，可研究独立的精确 7T setup/paired-hold 关系。它不是当前 setup 关键路径，也不应为了让时序报告更好看而机械添加 MCP。

`fine_code` 在 phase14 捕获、phase15 Stage-A 再捕获，只有1个周期；配置总线写入/weight-store 更新也必须按真实接口维持正常一拍约束。不能把整片 `u_context`、`u_recon` 或所有控制路径放宽成 7T/15T。

### 6.3 如后续尝试 MCP 的门槛

- 从综合网表实际 FF pin 建两组白名单：phase0 fine-context→phase15 Stage-A 的精确 15T 端点，以及 phase9 residue→phase0 fine-context 的精确 7T 端点；不按层级 wildcard 放宽，也不因为 min path slack 小就放宽该 min path。
- 证明普通运行、首次有效帧、同步 `rst_n`、`cfg_clear_valid`、disable/re-enable、cfg epoch、busy/cancel、无效 comparator 和 error flags 下，source 在目标 capture 前稳定，并且 destination 中间周期不会采到被放宽的数据。
- 为同钟关系按目标工具的 edge semantics 成对定义 setup/hold；常见同边沿 N setup 配 N−1 hold 只是待工具确认的模板，需据实际 rise/fall、`-start/-end` 与路径端点计算。
- 工具确认例外命中数、起终点和没有误覆盖；重新检查全部 max/min、输入输出、recovery/removal、pulse-width 与例外冲突。若添加约束改变候选状态，应执行逻辑/时序合同等价检查和 post-route STA。
- MCP 只放宽其所证明的 context cone；divider、MAC、fine_code、cfg 和模拟宏路径仍需按自己的真实捕获预算闭合。

当前有限控制 wrapper 的相位事务证明不是全状态形式证明：不覆盖完整 weight/recon datapath、全部 top-level clear/re-enable 与异步外设时序，故不足以直接授权把 MCP 写入签核 SDC。

## 7. Reset、CDC 与 I/O 时序风险

当前 RTL 的时序块均为 `always_ff @(posedge clk)`，`rst_n` 是同步低有效复位；没有 `negedge rst_n` 式异步复位 FF，也没有显式 CDC synchronizer/async clock group。Top-level 注释明确写 `cfg`、比较器数据和 ready 同 `clk`，无 CDC 插入。重导的 `u_wstore/SR[0]` 61,236 fanout 与 `weight_store` 的同步 `if (!rst_n)` 清零结构相符，说明 reset 分发/扇出是结构性布线风险；没有 DCP sink-pin 清单时不应把它声称为精确的 reset load composition。`tp_clock`、phase 和 acquire/compare enable 是模拟宏控制输出，不能因为名字像 clock 就声明为数字 generated clock。

本次复核的 reset/译码优化候选必须保持当前边界合同：`weight_store` 的同步低有效 `rst_n` 清数值权重、行和与写入 bitmap；`clear_load`/`cfg_clear_valid` 只使完整加载 bitmap 和配置 epoch 失效，数值权重/row totals/`sum_all` 保留。`cfg_rdata` 是无额外寄存延迟的组合读回，按 weight/scalar/control/status/window 优先级选择，非法地址读0，且不能新增 `cfg_ready` 对读回的门控；权重写入仅在 `cfg_bus_ok`、`!cfg_ready` 等条件下接受，clear 与写入的优先关系、权重合法性检查和错误优先级都要保留。因而局部 reset driver/decode 可以是实现候选，但增加一级寄存、把同步 reset 改成异步、把 epoch clear 合并为存储清零，都会改变现有合同，不能作为单纯 PPA 重写。

工程实现必须保证外部 `rst_n`、`cfg_*`、Flash thermometer、coarse/fine comparator valid/data、ADC code/ready、RA/RDAC/ADC2 overrange 和 injection 在各自捕获沿满足同步输入合同。如果比较器/宏回读实际异步或由相位控制事件产生，需建立宏级 handshake/stable-window 或匹配的同步策略；对多位 thermometer 独立加同步器可能破坏码字一致性，不能视为无代价通用修复。同步复位若由异步来源去使能，也要保证释放时不在采样边沿附近抖动；当前 RTL 未提供复位同步器。

零延迟 XDC 下“无 missing input delay”只是约束对象已匹配，不证明其最小/最大到达正确。真实 I/O budget 缺失会同时影响 setup 和 hold。尤其 0 min 输入 delay 与 OOC 内部 BUFG skew 的组合，造成上述 cfg_wdata hold 负裕量；真实接口时钟关系可能改善或恶化，需有发送端和接收端数据后才能算。

## 8. Dr. RTL 策略库映射：候选与不晋级条件

策略来源是已安装 skill 随附的 `references/dr-rtl-patterns.md`。其置信度是搜索优先级，不是工具结果保证。

| 证据对应瓶颈 | Dr. RTL 模式 | 可试的受控候选 | 必须保持/验证 |
|---|---|---|---|
| 历史P5 setup路径77.875%为route；重开旧P5 DCP 还看到 `u_wstore/SR[0]` 61,236、`u_idx[2]` 6,776、`phase0` 1,864、`cfg_wdata` 各位约1,321 fanout | 高置信度 #7 高扇出寄存信号分扇区；#8 组合译码本地复制 | 先分别对 `w_q` 同步清零网络做物理分区/等价局部驱动实验，对 `wr_slice/wr_unit` 做局部 one-hot decode；只在目标实现保留分区、且同一周期写入/读回不变时继续。权重数据广播本身可能仍需跨分区布线 | 完整测 LUT/FF、net load、slew/cap、cell/net delay、拥塞、route；保持同步低有效复位与 `cfg_clear_valid` 分域、bitmap清除/数据保留、`cfg_rdata` 组合读回优先级、非法地址归零、读写周期与错误优先级。不要对所有网全局复制，也不要把历史扇出当 current P7 实测 |
| 历史 P5/P7 setup都曾出现 divider CARRY4链；P7主路径 117 CARRY4 | 高置信度 #10 borrow/lookahead 只针对报告确认的借位链；#9 carry-select 宽加法候选 | 首先只在同一 current HEAD 做 P5/P6/P7 参数化基线；若 P7内部仍是最差路径，再独立试局部减法/进位实现 | 有符号 floor、wide denominator、borrow、quotient/remainder 和边界输出形式等价；额外面积、每拍 cycle、15/13/11拍 latency、16拍吞吐和 hold 同时测。ASIC 与 FPGA 映射可能完全不同 |
| rail/MAC 多项归约可能导致算术链 | medium #18 reduction tree；#20 multi-operand add rebalance | 先查看current mapped cone；对一个有限树层或分组归约做单变量实验 | W_BITS/V_BITS、截断/符号、溢出和舍入完全一致；原RTL已有显式树时避免“重写表达式就变快”；综合必须证明确有深度/面积改善 |
| `slice_pool_ctrl` 计数/优先选择、顶层地址判定 | high #1/#2 共享谓词；#3 one-hot decode（若编码合同允许） | 只有成为实际 critical path 才预译码；利用固定18slice范围缩窄可达索引/比较 | reset、clear、非法状态和 priority/last-wins semantics；组合 decode 复制可能增加面积/扇出 |
| Phase→start 或宏控制路径 | high #12 注册边界输出（需架构允许）；low #34 禁止猜测插寄存 | 当前不能直接插寄存级；先看真实 STA/宏 setup/hold，再写新的时序/模拟合同 | phase14→15的1T边界、模拟比较deadline、16相位节拍和所有 sample ID/error 对齐 |
| 仅因高扇出排名插“空”逻辑或任意复制 reset | low #33/#32 | 不建议空加中间 wire、手写 mux 层级、把同步复位改为异步复位、给复位打一拍或无依据地复制驱动 | 必须先确认 sink 类型、critical-path 关联和目标工具是否保留分区；任一 reset 复制都要证明每个 posedge 上的复位语义完全一致并守住配置读回/clear 合同 |

### 历史优化候选的晋级状态

报告按原始证据保留失败或未完成结论，不将早期候选当作已晋级优化：`vivado_cached_p5_route_25ns_fail` 是已完成布线但 setup/hold 均失败（WNS `-0.280 ns`、all-path WHS `-2.361 ns`）；`vivado_complete_p7/optimized` 是 divider+reducer 合并改动，LUT 增加 `16.23%` 且 setup/hold 仍失败；`local_row_trial_route_congestion` 的 Vivado route 因 congestion level 7 中止，没有合法 post-route timing。另有 `weight_msb` 候选通过了功能/lint，但没有综合或实现结果，故不能作为面积、时序或 PPA 成果。它们是负对照/未验收记录；本报告提出的 reset 分区和局部译码只是待测假设。

复位扇出可以分两档验证。低风险档是在综合/实现阶段以每个存储 slice/row 为消费扇区，尝试工具支持的等价驱动复制/物理 buffer tree，而不改变每个 posedge 的同步 reset 取值。更高潜力、也更需等价门禁的 RTL 假设，是让 18×71 个 48-bit `w_q` 数据 FF 不再逐位 reset，增加独立的 per-word `initialized` 位：rst 清 `initialized`，accepted write 同拍写数据并置位，所有 `old_by_slice`/`selected_weight` 读取在未 initialized 时输出零；`cfg_clear_valid` 只清现有 `written` complete-load bitmap，不清 `initialized`，因此清配置 epoch 后仍能读出保留的数值权重。18×71 个 valid FF 远少于 18×71×48 个数据 FF，但读路径会增加 gating，面积/功耗/时序是否获益不能预判。它必须单独做全场景等价与 A/B 综合/布线，并覆盖 reset、clear、clear+write、覆盖写、配置读回、row/sum 更新；不能复用会被 `cfg_clear_valid` 清零的 `written` 位替代 `initialized`。

## 9. 下一轮可执行STA/综合步骤

1. 以当前 HEAD 和 `sources_manifest` 冻结一个源包；单独分别以 `P_RECON_STAGES=5,6,7` elaboration/synthesis，统一顶层、工具版本、part、`P_STRUCTURAL=1`、时钟、约束与综合选项。先采集每个 profile 的 max/min、分段起终点、resource、check_timing、exceptions 和源 SHA。P5/P6/P7 不能交叉借报告。
2. FPGA筛查先用用户实际接口约束；当前零 I/O XDC 只能标为screening。对 OOC 内部路径和外部接口分别报告，不删除失败路径。以当年目标40MHz验证 FPGA 可实现频率时，周期应由真实板级时钟确定，而不是用于证明ASIC 640MHz。
3. ASIC评估拿到真实 foundry cell/macro `.lib/.db`、LEF/RC/RC corners、MMMC/OCV与接口合同后，建立1.5625 ns nominal constraint；先无MCP跑baseline，确认库链接/宏弧、单位、clock waveform、I/O min/max、IO drive/load、recovery/removal和unconstrained完整性。
4. 基线 STA 逐条保存 `startpoint/endpoint`、clock edge/latency、required/arrival/slack、cell/net delay、fanout/slew/cap 与 RTL cone；post-placement、post-CTS、post-route分别保留 setup/min hold、clock tree、exceptions、DRC、SPEF覆盖。不能用综合 estimated route代替布线结果。
5. 对实际最差 cone 每次只改一类：局部复制/分组归约、P stage参数或算术拆分之一。先做相对基线的逻辑等价/行为回归，再同条件综合、route 和 max/min比较；不达标不晋级，禁止只报告setup或最好path。
6. 最后再对已有控制证据覆盖的 context FF→Stage-A FF 精准验证MCP候选；不改变fine_code、MAC、divider和cfg一拍约束。校验MCP匹配和paired hold后重跑完整场景 STA。

## 10. 本次复核没有证明的事项

- current HEAD 的新综合资源、P5/P6/P7相对排名、ASIC 640 MHz闭合、功耗、芯片面积和PPA优势；
- 真实板级 I/O setup/hold、所有宏 settling/CDC/亚稳态、完整 SoC clock/reset 质量；
- MCP已在工具中生效，或该MCP候选已由全状态 formal/完整STA证明；
- 历史 P5 物理报告等同 current byte-identical DCP；历史 P7 与 current HEAD token-equivalent；
- 通过 `check_timing` 的计数为0即可取代真实输入/输出预算和跨角 signoff。

## 11. 主要源文件及证据位置

- Current RTL/default: `rtl/top/sar20_digital_core.sv`, `rtl/core/recon_core.sv`, `rtl/core/sar_structural_ctrl.sv`, `rtl/core/cal_sample_context.sv`, `rtl/params/rtl_params.vh`。
- Synthesis/STA Tcl: `synth/run_vivado_ooc.tcl`, `synth/run_vivado_buffered_impl.tcl`, `synth/run_vivado_impl.tcl`, `synth/run_dc.tcl`。
- Implementation plan & existing interface-budget checklist: `synth/IMPLEMENTATION_PLAN.md`, `synth/FPGA_SYNTHESIS_AND_STA.md`。
- P5 reports: `docs/evidence/20260930/shared_old_trial_synth/`, `docs/evidence/20260930/shared_old_trial_route_25ns/`。
- P7 reports: `docs/evidence/20260930/vivado_complete_p7/{baseline,optimized}/`。
- P5-to-current token bridge: `baseline/source_identity.json` in the parent output folder.
- 2026-10-07 frozen-P5-DCP report re-export: `baseline/reopened_route/{execution.json,scope.txt,out/}` plus `baseline/reopened_route_sha256.json`; this is report regeneration only.
- Report/source SHA inventory: `evidence_sha256.txt`; exact read-only inspection commands and provenance boundary: `commands.txt`.
