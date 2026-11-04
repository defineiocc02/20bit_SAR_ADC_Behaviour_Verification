# 后续综合、STA 与布线实施计划

本计划把下一轮工作拆成有限、可审查的里程碑。它补充 [FPGA_SYNTHESIS_AND_STA.md](FPGA_SYNTHESIS_AND_STA.md) 的实际命令与来源校验，不另建 runner，也不声称本计划中的实验已经运行。使用真实顶层 [sar20_digital_core.sv](../rtl/top/sar20_digital_core.sv)；旧 `71e7d5a` 的已测数据作为冻结对照，当前排版源码使用自己的 raw SHA。

## 1. 目标与不可省略的边界

| 项目 | 本轮下一步的目标 | 当前已测/未知条件 |
|---|---|---|
| FPGA 核心 | 同一器件、P5/25 ns 基线下减少关键路径互连，维持功能与 16 拍吞吐 | Vivado 2018.3，`xc7vx690tffg1761-2`；旧冻结核心 setup/hold +0.946/+0.052 ns |
| FPGA 外部接口 | 建立有真实来源的 I/O min/max、时钟和引脚预算，随后完成板级实现 | 旧零 I/O OOC hold −2.278 ns；不能标为全接口时序闭合 |
| PPA | 同条件测量 RTL/物理候选的资源、实际路径、活动功耗，并判断代价 | 当前不声称功耗或全面 PPA 优势；vectorless 功耗不是签核结果 |
| ASIC | 对 1.5625 ns 核心时钟建立库/宏/时钟/RC预算，再按实际平台实现 | 当前 library、LEF/technology、RC、MMMC、memory/模拟宏模型及工具环境均需确认，不假定已安装 |

ASIC 的 640 MHz 目标是 **1.5625 ns/clock、16 clocks/frame、25 ns/sample（40 MS/s）**。FPGA 现有实测是 **25 ns/clock、400 ns/frame、2.5 MS/s**。两种“25 ns”的对象不同，不混用。源码排版或流程文档完善不会降低电路面积；只有经同条件实现验证的结构改变才能计入 PPA。

预布局阶段的“通过”指输入、流程和分析完整，可带着已登记的负裕量进入下一阶段。只有最终覆盖完整、要求的所有 setup/hold/脉宽及其他适用检查均达标，才叫时序闭合。不得把早期负裕量删除，也不得把估算正裕量当成签核。

## 2. FPGA 分阶段门禁

实际入口为 `run_vivado_ssh.py`、`run_vivado_ooc.tcl`、`run_vivado_buffered_impl.tcl` 与 `run_vivado_full_mapped.py/.tcl`，示例 CLI 见 [现有指南第 3/5/7 节](FPGA_SYNTHESIS_AND_STA.md)。主机用 `windows-codex`，工具路径用已测 `D:/Academic/Vivado2018/Vivado/2018.3/bin/vivado.bat`。没有 `run_vivado_buffered_impl.py`。

| 阶段 | 输入 | 操作 | 必须保留的报告/文件 | 通过条件 | 失败回退 |
|---|---|---|---|---|---|
| F0 冻结候选 | 指定 Git/worktree、24 个 `.sv`、参数头、源列表、TB、物理约束 | 记录逐文件字节 SHA、有效 token 对照；运行相关 RTL/lint；记录 P5/P6/P7 参数与接口/拍数 | 源包/manifest、代码差异、完整测试日志、工具版本、比较基线身份 | 该候选功能/位宽/ID/取消/flags 成立；所有有效变更可定位 | 返回最近合格候选；修源/测试判据，不改旧证据 SHA |
| F1 完整 OOC 综合 | F0 包、part、25 ns、P5、现存 Tcl/XDC | 综合全核；检查 undriven/blackbox/latch；读取资源、max/min、时钟与未约束问题 | 原始综合日志、status/manifest、utilization、min/max/summary、check_timing、DRC、post_synth.dcp | 工具/参数/源包一致，所有必要报告和路径存在；setup 与 hold 数值及风险已登记 | 流程错误先修命令/输入；未驱动树先修结构；不得把无效网表用于 PPA |
| F2 DCP 身份 | F1 完整 DCP 与原始 manifest/source tar | 源包成员逐项验 SHA；本机/远端独立核实 DCP 全字节、大小、SHA | 身份记录、源包校验、传输记录、原始 manifest 及新增 sealed/验收记录 | 来源有效且两端摘要一致；新运行有新身份 | 重新传回同一产物；身份不符就隔离，不能拿旧 DCP 补空缺 |
| F3 BUFG/ECO/布局 | F2 DCP、冻结实现 Tcl、P5/25 ns、`BUFGCTRL_X0Y0` | 插真实 BUFG；核对完整 clock endpoint 集；opt/place/phys_opt；在每个节点留 max/min/覆盖 | post-ECO/place/phys-opt checkpoint、clock/endpoint 及 SHA、各阶段 timing/coverage/utilization/DRC、拥塞观察 | 未改原数据逻辑/端点；时钟唯一且边界说明完整；各阶段 max/min 可分析，违规清单可定位 | ECO身份错误回 F2；布局拥塞先查扇出/局部性；调整一个变量后重启新实验 |
| F4 全部路由与 STA | F3 放置结果、相同约束 | route；真 clock-pin→data-pin 与全路径分别分析；检查脉宽、例外、未约束与 route/DRC | post_route.dcp/SHA、全部 setup/hold/summary/check_timing/exceptions、route/DRC、真实路径明细 | route complete、无错误/漏分析；核心闭合只对内部成立；全接口闭合还须所有真实 I/O 路径通过 | 分清核心/IO、logic/route、setup/hold；返回相应结构、布局或接口预算阶段 |
| F5 完整映射功能 | 同一 F1 DCP、接受 manifest、冻结 mapped TB/流程 | 用真实 vendor primitives/XSim 运行 full-top 功能仿真；审核 trace/ID/cancel/配置读回 | DCP/网表/TB 身份、export/xvlog/xelab/xsim 原始日志、trace 与独立审核 | 所测公共输出与判据相符；明确 no-SDF、异常/延迟覆盖范围 | 先查 DCP/参数绑定，再查功能；不以 RTL PASS 替代网表失败 |
| F6 板级与活动 PPA | 真实 I/O/时钟/引脚预算、F4 候选、代表性活动 | 创建有来源的板级 XDC，完整实现；在同活动窗口比较 A/B | I/O 预算来源、板级工程约束、全部 STA/DRC、activity 注释覆盖、资源/功耗/路径对照 | 全路径和适用宏接口均通过；A/B 输入与窗口相同，收益/代价可追溯 | 缺接口条件就保留未知；功耗覆盖不全就重建活动；无益候选回退 |

F1 的现存 Python status 只按 setup 筛查分类，不自动承诺 hold。F3/F4 的现存 Tcl 在 **post-route** 输出详细结果；表中的 post-ECO/place/phys-opt 阶段报告是下一实验应新增的记录点，本轮没有生成这些新报告。可以在冻结的实现 Tcl 副本中分别加入下面的诊断段，使用各自 `stage` 名称，每次重新取得 pin 集合，不能复用优化前已失效的对象集合：

```tcl
# 下一次实验的记录点模板，非已运行结果；stage 改为当前实际节点。
set stage post_place
set launch [all_registers -clock_pins]
set capture [all_registers -data_pins]
report_timing_summary -delay_type min_max -report_unconstrained \
    -file [file join $out ${stage}_summary.rpt]
report_timing -from $launch -to $capture -delay_type max -max_paths 20 \
    -path_type full_clock_expanded -file [file join $out ${stage}_reg_max.rpt]
report_timing -from $launch -to $capture -delay_type min -max_paths 20 \
    -path_type full_clock_expanded -file [file join $out ${stage}_reg_min.rpt]
check_timing -verbose -file [file join $out ${stage}_coverage.rpt]
report_utilization -file [file join $out ${stage}_utilization.rpt]
report_drc -file [file join $out ${stage}_drc.rpt]
write_checkpoint [file join $out ${stage}.dcp]
```

保存这个脚本副本的新 SHA，并注明只加诊断还是也改了优化命令。阶段 min/max 是综合估算、已放置估算或真实已路由，必须逐项标清。全路径报告继续保留；内部 pin 组报告不能隐藏外部失败。若要比较诊断加入对结果的影响，先用同候选做一次控制；不把新脚本 SHA 写成旧脚本。

## 3. 真实顶层 I/O 预算填写表

下面是待填写模板，端口来自实际顶层。每行应展开为具体位/端口、发送/接收实体、活动边沿、适用模式、数据来源及数值；**“待测/待定义”不等于零**。顶层同步契约要求输入满足 `clk`；若实物不满足，需设计 CDC/握手或同步模拟宏边界。

| 真实端口组 | 方向/适用模式 | 参考边沿与捕获/消费条件 | 必填 min/max 或波形要求 | 证据来源/签字项 |
|---|---|---|---|---|
| `clk`；`rst_n` | 时钟输入；同步低有效复位 | 主时钟真实入芯片位置；reset 在 `clk` 捕获 | 周期、duty、jitter/source latency；reset 到达/slew、释放规则 | 晶振/PLL/时钟树；板级/ASIC时钟与复位设计 |
| `cfg_wr cfg_addr cfg_wdata cfg_validate cfg_clear_valid` | 输入，两种顶层 profile | 正沿请求接受；clear/validate/write 优先级；不是异步软件电平 | 发送端 earliest/latest clock-to-out、走线/封装相位差、输入 min/max/slew | 上游寄存器/总线桥时序与 PCB 或宏模型 |
| `cfg_rdata cfg_ready` | 输出，读回组合、无读握手 | cfg_addr 改变后的读回消费边沿；ready 的同步观察 | 外部读回窗口/接收 setup、hold、负载；组合 addr→rdata 路径预算 | 软件/总线桥接口规格；接收寄存器/宏模型 |
| `flash_therm flash_valid` | 输入，结构模式 | `flash_sample` 对应的 Flash 冻结/valid；数据与valid同样本 | Flash eval/决策最早最晚到达、valid/data skew、同步稳定窗口 | Flash PVT瞬态或已表征宏时序；不能从 code 位宽推导 |
| `coarse_cmp_valid coarse_cmp_ge` | 两路输入，结构模式 | 各路 compare_enable、SAR accept/deadline 条件 | comparator min/max decision latency、ge/valid 相对稳定、异步与否 | 两路 comparator PVT/负载结果及数字握手契约 |
| `fine_cmp_valid fine_cmp_ge` | 输入，结构模式 | 后级比较接受与截止；上一笔 residue ID | comparator min/max decision latency、valid/data skew与建立保持 | 后级宏表征/与fine_trial的建立条件 |
| `sadc_code sadc_rdy adc2_code adc2_rdy` | 输入，兼容模式 | 兼容 phase8/14 捕获；结构模式可被裁掉 | code/rdy 最早最晚到达及同样本约束；按实际profile分析 | 外部编码器/量化器契约；未启用端口写明不适用 |
| `inj_q ra_sat rdac_ovf adc2_over` | 输入；已知注入与模拟异常回读 | 与本笔冻结上下文同ID；按相应 capture 边沿 | 注入值和异常位 min/max、稳定/同步规则；Q32仅是数值单位 | 注入数字源、RA/RDAC/ADC2异常产生模块 |
| `coarse_trial quantizer_dither coarse_compare_enable coarse_acquire_enable` | 输出到两路粗量化器 | 已决/试探的宏连接与下一次比较窗口 | 试探码传播、DAC settling、dither解释；eval/reset控制窗口、负载 | DAC/比较器宏时序与RC/PVT；不自动当作数字clock |
| `fine_trial fine_compare_enable fine_acquire_enable` | 输出到后级 | 后级试探建立/比较/采集窗口 | 输出最早最晚与负载、fine DAC建立、比较控制最小有效宽度 | 后级模拟宏时序/瞬态 |
| `analog_phase quiet_sample tp_clock ra_az ra_amplify ref_precharge ref_accurate` | 模拟控制/观测输出 | 真实相位区间、死区、AZ/采样/参考建立 | 各控制最早最晚切换、最小脉宽、dead time、模拟建立/负载 | 参考/RA/采样宏；控制波形不同于同步数据输出预算 |
| `acquiring_mask converting_mask aux_charge_enable hold_low_enable` | 18路宏控制输出 | 资源晋升/本笔所有权；开关及AUX消费窗口 | 相邻控制 skew、非重叠/最小有效窗、负载和电荷建立 | 18 slice/AUX宏与物理走线方案 |
| `flash_acquire_enable flash_sample acquisition_dither_rails` | Flash/采样dither控制输出 | Flash采集与采样轨冻结 | 脉宽、轨建立/最早最晚到达、load、宏参考关系 | Flash采样与dither底板宏 |
| `slice_sel main_sw sub_sw dither_sw sw_valid` | 数字到物理RDAC输出 | 物理 unit ID、已决更新；开关稳定区间 | 最早最晚传播、位间skew、底板建立与负载/电荷注入规则 | 真实驱动/DAC宏及输出RC，不能用“有效位”替代模拟建立 |
| `dout dout_valid dout_sample_id dout_flags clip_low clip_high analog_ovf acc_ovf status_word` | 数字输出/观测 | 输出公共事务ID/flags；sticky和last状态分清 | 接收端setup/hold、输出delay min/max、load；下游消费边沿/无背压契约 | 接收模块/接口规格；逐样本记录和错误消费规则 |

`tp_clock`、`flash_sample`、compare/acquire enable 是现有单 `clk` 数字核的宏控制，不能因名称含 clock/sample 就任意添加 generated clock。若真实外部电路用这些输出作锁存/评估参考，需单独表征其边沿关系、脉宽和接收时序；内部寄存器时钟仍是 `clk`。纯模拟接收节点没有数字 FF setup/hold 时，应规定模拟有效窗口/建立条件，不能捏造 `set_output_delay` 数值。

输入 delay 要区分发送端数据路径与参考时钟路径之差，可能为正或负；输出 `-min` 包含接收hold与相位关系，不能想当然写成正数。两者不是“留多少余量”的自由调参。min/max方法见官方 [UG903 v2018.3 第100–103页](https://docs.amd.com/v/u/2018.3-English/ug903-vivado-using-constraints)。

## 4. 1.5625 ns 的三方预算与未填写 SDC 模板

预算由三方共同确认，不能直接把1.5625 ns平均分成三份，也不由RTL代码行数推算：

1. **模拟宏/接口方**：给出 trial/DAC/参考建立、比较器 decision/valid/data 的 min/max、采集/AZ/评估窗、输入驱动与输出负载。每项注明PVT、负载、供电、失败概率/迟到处理及所对齐的样本ID。
2. **数字微架构方**：指定寄存器捕获边沿、数据依赖和每级运算，守住16拍吞吐与P5/P6/P7 latency、clear/cancel/flags；逐一证明允许的稳定窗口，不把帧长度自动变成多周期。
3. **物理/时钟方**：基于目标库和RC给出clock-to-Q、setup/hold、布线与clock skew/uncertainty、slew/load/OCV条件；对最慢/最快数据链分别核算，不把setup裕量用于抵消hold。

对同域一拍寄存器路径，若定义 `Δclk = capture_clock_arrival − launch_clock_arrival`，基本预算为：

```text
tCQ,max + tlogic,max + twire,max + tsetup + Usetup ≤ 1.5625 ns + Δclk
tCQ,min + tlogic,min + twire,min                 ≥ thold + Uhold + Δclk
```

这只是预算关系；实际签核以工具对具体边沿、corner/OCV、CPR、例外及宏时序弧的计算为准。输入/输出则依各自参考边沿及外部路径预算，不能把 comparator latency 简单塞进任意内部组合级。模拟精度所需建立时间若超过可用窗口，应共同修改相位/宏/数字捕获结构并重新回归。

以下为 **未填写、不可用于签核的部分 SDC 模板**，不是本轮运行过的约束文件，也不代表某款ASIC工具已安装。命令对象查询和单位须适配后续真实工具；按已经确认的 **1 ns time unit** 书写，其他单位必须受控换算。模板故意报错，不能未经接口/库评审删除保护后照搬。

```tcl
error "TEMPLATE_INCOMPLETE: library/IO/MMMC values have not been approved"
# 仅在资料齐全并经审核后移除上面的保护；以下不是完整签核SDC。
set ASIC_PERIOD_NS 1.5625
foreach field {LIB_TIME_UNIT_NS CLK_U_SETUP_NS CLK_U_HOLD_NS \
               CFG_IN_MIN_NS CFG_IN_MAX_NS RESULT_OUT_MIN_NS RESULT_OUT_MAX_NS} {
    if {![info exists $field]} {error "Missing approved budget: $field"}
}
if {$LIB_TIME_UNIT_NS != 1.0} {error "Convert approved values to the actual library time unit"}
create_clock -name core_clk -period $ASIC_PERIOD_NS [get_ports clk]
set_clock_uncertainty -setup $CLK_U_SETUP_NS [get_clocks core_clk]
set_clock_uncertainty -hold $CLK_U_HOLD_NS [get_clocks core_clk]
set cfg_in [get_ports {cfg_wr cfg_addr* cfg_wdata* cfg_validate cfg_clear_valid}]
set_input_delay -clock core_clk -max $CFG_IN_MAX_NS $cfg_in
set_input_delay -clock core_clk -min $CFG_IN_MIN_NS $cfg_in
set result_out [get_ports {dout* clip_low clip_high analog_ovf acc_ovf status_word*}]
set_output_delay -clock core_clk -max $RESULT_OUT_MAX_NS $result_out
set_output_delay -clock core_clk -min $RESULT_OUT_MIN_NS $result_out
# 还必须逐组填写Flash、两路粗/一路细比较器、注入、reset、cfg读回、模拟控制等。
# macro waveforms/load/slew、适用模式/corners、时钟来源和工具对象存在性检查未填写。
# 不在此模板中添加无证据的false path、multicycle或clock-route例外。
```

预算可能按端口不同，表中一组不能因为方便就全部用同一值。ASIC综合的驱动/负载单位来自当前库，不能复用历史 DC 表格里未复核的数值。设置虚拟/派生时钟、clock groups、case analysis或例外必须先说明真实接口/模式机制及对应验证，尤其不能通过切掉配置更新或模拟比较器路径掩盖失败。

## 5. 各类关键路径的定位顺序

每次从原始STA中的实际 start/end pin、active clock edge、path group、max/min corner开始，再看 cell/net 增量、fanout/cap/slew、placement与拥塞。先确认路径是真实工作机制，再改结构或物理；不根据源文件外观或“逻辑级多”直接换算法。

| 路径类型 | 依次定位 | 可评估的改进 | 必须复验的约束 |
|---|---|---|---|
| **fine ID→活跃行/rail归约**（当前主要瓶颈） | context ID和冻结位 → 物理行译码/窄掩码扇出 → 71 unit/18 row归约 → signed rail修正 → rails_s捕获；逐网看互连/位置 | 在后级SAR期间预计算T/G/R、局部行/列求和、分段寄存与局部布局；每次选一项 | 同ID/epoch/valid/bad，16拍吞吐，cancel/clear，位精确结果；新实际max/min |
| 系数写入→本行缓存 | 地址/值守卫 → 本行旧值选择 → row-old+new → FF；确认是否误走全局slice读回 | 保持已修共享old_by_slice结构；优化本地扇出/布局 | 同沿写、旧值保留、written完整性、非法/重复写、cfg_readback |
| ADC2译码/残差MAC→捕获 | 符号扩展、bin-center乘法与round-even → F/O/I与T/G/R → overflow域判断；看DSP/carry映射和net | 明确运算级边界，必要时局部流水/DSP寄存化 | 96-bit域、负floor、ties-even、吞吐/ID/flags；不能无误差证明截位 |
| floor除法内部 | 余数/除数FF → P_STAGES个比较/减法级 → 商/余数捕获；检查迭代busy释放 | 单变量P5/P6/P7对照 | 小位宽全域/大域oracle、负余数floor、连续启动15/13/11拍与16拍帧 |
| 资源调度/相位→宏控制 | phase/游标FF → 补集/ID/输出注册 → 模拟接收负载；对比组合/注册波形 | 减扇出、局部控制、实物接收边界 | 所有权、不重开compare、dead time、脉宽、迟到取消；端口窗口/模拟建立 |
| I/O最短hold | 真输入参考边沿 → external最早到达 → 内部短数据链/捕获clock；或输出→接收端要求 | 真实delay、时钟关系、工具短路径ECO或接口寄存边界 | min/max一起复验，CDC/握手，宏稳定窗；延长周期不能当成hold修复 |
| clk/reset/enable大扇出 | 时钟来源/传播、clock pin集合；reset/enable到底是同步data还是async pin | 合法时钟树、局部enable/物理分布；不要盲目复制逻辑 | 唯一时钟/端点集合、setup/hold/PW；有async端时另验recovery/removal |

旧实测最差 fine-ID→rail 路径为 **23.914 ns**，互连 **18.623 ns（77.875%）**、逻辑5.291 ns、40级。优先减少广域数据依赖与扇出，不能只继续缩小已经不在最差路径上的配置加法器。也不能将 `rebuilt` 的层级LUT数解释成对应源码的独立成本。

## 6. 同条件 P5/P6/P7 与 PPA A/B

先固定工具/part、P_STRUCTURAL=1、相位参数、seed、I/O假设、25 ns、0.05 ns uncertainty、BUFG站点及所有实现命令，只把 `--stages 5` 改为6或7。每点创建新源包/manifest/DCP/实现目录；使用完整26文件当前raw SHA，不能把A版本DCP交给B的TB标签。三个点的内部结果拍数应为15/13/11，帧仍为16拍。P6虽有RTL证据，现有full-mapped runner仅支持P5/P7；计划若要验P6完整网表，应先做明确P6适配并验证其来源与oracle，不能直接传不存在的选项或把P5日志改名。

每个点保存：综合/路由LUT/FF/DSP/BRAM、logic/net delay、关键路径起终点、max/min/slack/失败终点、clock端点/覆盖、route/DRC/拥塞、RTL与完整映射功能范围，以及活动功耗的窗口/注释覆盖。若无法保持同物理条件，注明变化并停止直接归因。先比较同周期的结构；选择候选后另开“只改变周期”的实现扫描。正裕量不能线性外推Fmax，单个未布线结果也不能作为最高保证频率。

权重表为62,316 FF，占全核66,492 FF的93.72%；其有效权重、written及行和状态分别60,066/1,278/972 bit。真正节省存储要先证明每笔系数读取带宽可降低，再选择分银行RAM/SRAM或适用结构DEM的前缀表示。保持18×71并行读口仅换数组声明不等于BRAM；保留原始权重追加朴素前缀会多72,432状态位。缩窄合法47-bit权重或96-bit域须给出可辨识/量化/重构误差预算，不能依一次训练样本最大值裁位。

功耗A/B至少分配置载入、连续转换、DEM/dither及取消/重新配置，使用相同频率、电压/温度、I/O负载、sample ID序列和有效分析窗口；记录startup是否排除。检查activity映射层级、时钟/数据注释覆盖、X/Z/未注释节点和模式比例，再报告dynamic/static/clock/DSP/IO分项。现有0.477 W为vectorless、无activity file，不能作为已经获得功耗优势的对照结论。活动导入格式/对象查询须按实际工具版本核对。

## 7. ASIC 从资料门禁到 ECO 的计划

未确认的资料应保持“未知”，不是使用旧DC/TSMC路径填默认值。开始ASIC实施前必须有获授权且可校验的 library/liberty及必要.db、LEF/technology、RC/extraction规则、供电温度条件、MMMC场景、memory宏、模拟宏时序/负载/约束、clock/reset/test结构和可用工具版本。确认库单位、缺失timing arc、PVT覆盖和授权范围；资料不齐时只做RTL/预算准备，不发布ASIC PPA。

| 阶段 | 输入 | 操作 | 必须保留报告/文件 | 通过条件 | 失败回退 |
|---|---|---|---|---|---|
| A0 工艺/宏/MMMC门禁 | 上述真实库、物理/RC/宏、实际工具；未填I/O表 | 单位/角/版本/授权与模型完整性审核；定义正常、配置、复位/测试等实际模式及各clock关系 | 文件身份、库单位/角、宏接口/模型清单、MMMC与预算评审 | 每个活动路径有适用库/RC/模式与外部预算，无猜测模型 | 保持未知，向对应接口/工艺方补资料；不能借旧结果跳过 |
| A1 综合与逻辑基线 | A0、冻结RTL/参数、1.5625 ns及合法SDC | analyze/elaborate/link；查latch/blackbox/参数；综合、max/min与设计规则分析；按可用方法核对等价 | 输入身份、日志、mapped网表、SDC/库、面积/功耗模型、setup/hold/覆盖、功能/等价 | 逻辑映射有效、数值契约不变；初步违规清单完整，未称postroute闭合 | 修解析/库/结构或预算；不能通过删守卫/截位消除关键路径 |
| A2 floorplan/placement | A1、真实die/core/utilization与macro/pin/power方案 | 布置memory/模拟宏与数字核；建立供电/区域；place与初步RC max/min | floorplan/placed数据库、拥塞/密度、macro/pin/供电、估算setup/hold与DRC | 合法放置/可路由，无缺失宏接口；路径与扇出位置明确 | 改一个区域/容量/微架构变量；回A1重综合有逻辑变化的候选 |
| A3 CTS | A2、真实clock源/uncertainty/树目标与接收pin | 建clock树、传播clock；查skew、latency、slew、clock gating/PW及max/min | CTS数据库、clock树/端点、max/min/覆盖、transition/cap/fanout、适用reset检查 | 所有clock接收端有真实树与要求，适用检查无漏项；违规完整 | 回时钟预算/placement修clock或短路径；不伪造ideal clock掩盖 |
| A4 route | A3、真实routing/antenna规则与RC corner | global/detail route、拥塞/DRC/antenna、必要合法物理修补；更新max/min | routed数据库/网表、route/DRC/antenna、时序和变更记录 | 路由完整；物理规则达标或明确未关闭清单；进入提取门禁 | 分清拥塞、长线、短线与宏边界；有结构变化回A1 |
| A5 extraction | A4、真实extraction deck/RC角 | 提取寄生、检查层/单位/网络/耦合完整；绑定网表/SDC/场景 | SPEF或实际工具寄生格式、提取日志/覆盖/身份、对应corner映射 | 寄生与当前routed数据库/网表匹配，所有场景和关键网络有模型 | 修提取规则/单位/缺网，禁止用pre-route RC补成postroute |
| A6 postroute STA与规则 | A5、全部要求模式/角、propagated clocks、宏模型 | 全路径setup/hold、适用recovery/removal、clock gating/PW、transition/cap/fanout；查coverage/例外；复核物理DRC | 各MMMC完整max/min与endpoint/coverage、时钟/宏跨界、适用检查、不适用理由、DRC清单 | 所有要求场景达标、无漏分析/无未解释例外，真实宏边界也通过 | 按关键路径类型回对应阶段；同步reset无async检查时写“不适用”，不伪造pass |
| A7 ECO闭环 | A6的具体违例与影响预算 | 选最小逻辑/时钟/布局/缓冲修复；重合法化、route、提取、全场景STA/DRC | ECO差异、输入/输出网表/数据库/寄生、全部重跑报告、回退点 | 修复目标且无其他场景/功能/规则回归，代价可量化 | 回退该ECO；重新定位因果，不能只重跑目标一条路径 |
| A8 功能/等价/活动功耗与交付 | A7最终网表、RTL/宏、活动与最终约束 | 正确比较基准的等价；完整功能/必要时序仿真；activity与功耗；封存版本 | 等价/功能/SDF使用范围、活动覆盖、功耗场景与模型、最终STA/DRC/源码身份 | 算法/ID/flags/16拍契约和所有物理验收成立，功耗/面积条件注明 | 功能失败回ECO前；活动不足重建；没有真实宏/库不发布全ADC指标 |

MMMC不预填SS/FF电压温度或RC名字。慢库/快库和max/min寄生按实际表征建立组合；不要假定单一SS就是所有setup最坏、单一FF就是所有hold最坏。输入宏PVT必须与场景相容。当前 `rst_n` 是同步data，核内没有因此新增async recovery/removal检查；若macro或后续设计有async reset/set/clock-gating pin，其恢复/移除和其他适用检查必须纳入。

上述数字实现 STA 与路由 DRC 不替代实际芯片交付的 LVS、制造级 DRC、供电 IR/EM、模拟版图/可靠性等签核；这些事项应由真实工艺与芯片集成流程逐项给出证据，未完成的不能写作通过。若后续加入 DFT/scan 或 clock gating，先冻结功能/test 模式、测试时钟、捕获/移位规则及新增端点，再纳入等价、MMMC、clock-gating/适用脉宽和复位检查，不把 test 路径从分析中默默裁掉。

## 8. 有限里程碑与完成定义

不承诺任意日期或机器时长；每个里程碑结束时留下原始日志、SHA、已知失败、下一动作与回退点，断线后先恢复原任务。

| 里程碑 | 完成定义 | 不能替代它的材料 |
|---|---|---|
| M0 可读且可追溯的候选 | 原始/当前token对照、当前raw SHA、模块职责和状态/数学边界、相关RTL/lint已验 | 仅排版截图或旧源码摘要 |
| M1 FPGA核心复现/分阶段诊断 | F0–F5证据绑定同一冻结输入；每物理节点max/min可追踪，最终核心max/min通过 | 综合setup或一张波形图 |
| M2 外部接口事实与板级闭合 | 全I/O填写表有真实来源，板级完整实现所有要求路径/规则通过 | 零I/O OOC内部REGREG_TIMING_MET |
| M3 PPA受控选择 | 同条件P5/P6/P7及至少所提结构A/B的数据、活动功耗覆盖、保留/否决理由 | 理论节省项数、单次vectorless估值、外推Fmax |
| M4 ASIC资料就绪 | A0所有未知补齐并获准使用；预算/场景/宏可分析 | 历史DC/PDK目录或FPGA资源换算 |
| M5 ASIC数字实现/STA闭环 | A1–A8同源网表/寄生/场景证据，640 MHz及规定PPA在真实全核条件下验收 | 只跑综合、零线负载或单一corner |
| M6 发布可复审交付 | 修改RTL/报告、原始证据目录与身份链、适用范围和残留项进入同一Git提交/PR | 口头保证或给旧图改标题 |

M0、现有旧冻结核心证据与本计划可以先交付；M2–M5由真实接口、ASIC资料和后续实验推进。没有达成的里程碑如实列未完成，不能把“计划完整”写成“布线/ASIC任务已完成”。
