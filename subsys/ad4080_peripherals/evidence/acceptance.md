# 数字外围审查、修正与验收

审查从产品实际配置 SPI 开始，经过系统校正、滤波、事件、同一份结果存储与串行读出。转换团队的数字核仍从原生产入口交付。外围产品的各个构建入口从 [同一份 DSP 提交号](dsp-revision.txt) 读取源码。各功能的实际责任方、闭合条件和运行范围见[职责矩阵](../interfaces/function_ownership.csv)与[逐模块原件核对](MANUAL_TRACE.md)。

**回归与器件验收分别记录。** 完整运行的每组名称、完成行和输入摘要在[统一回执](peripheral-review.json)中。硬件模式、板级电气和工艺资料的待办均保留[负责人和关闭条件](peripheral_open_items.csv)。

**逐拍观察。** 图 96 的先加后乘、图 83 的阈值报警、图 99 的采样／写存储分相，以及图 89 的真实串行位对各有独立判断。输入时间、线上实际值与不确定的物理传播时间分别记录。[图 96 整数判别](../calibration/rtl_verification.json) · [产品引脚数据](../integration/product/evidence/pin_edges.csv) · [独立检查器](../verify/check_signal_edges.py)

## 发现和实际处置

| 优先级与问题 | 旧行为或证据缺口 | 修改与复测 |
| --- | --- | --- |
| 高：未完采集与存储自测冲突 | 记录尚未写完，却能受理待机。在跨域保持请求发出、AON 真正授权之前，自测已经能使 SRAM 内容作废。功能请求被内部 valid/ready 掩掉还会绕过 entry/reinit 检查。 | 增加未完记录联锁，把保持和破坏性授权分别同步，以原始功能请求判断是否静默。复测撤回短请求、核心停钟、同沿改模式/复位及重建。服务壳合计 13 个场景。[旧断言](../integration/evidence/audit_red.txt) · [新回归](../integration/evidence/regression.txt) |
| 高：OTP 旧响应被新会话接受 | 旧宏应答能被当作第一字，旧校验 PASS 能提交未由该次请求认证的影子表。 | 在相应请求获受理前出现无归属响应，分别以原因 10、11 关闭会话。合法同沿应答、掉电及重新初始化另测。11 组通过。请求已受理后与合法响应无法区分的旧包，仍由宏适配器负责隔离。[新旧同向量回执](../otp/verification.txt) |
| 高：参考掉压未到达配置状态 | 单核寄存器用复位常量代替模拟 POR，时钟停止时的真实参考事件可能丢失。 | 服务系统捕捉参考失效并跨域，硬 POR 作废旧会话；0x14[3] 可由实际事件置位并经写 1 清除。另区分冷启从未合格的 REFIN 和合格后再次掉压对固定测试字的影响。[服务回执](../integration/evidence/regression.txt) |
| 高：滤波启动码被当作有效记录 | 内部数值 valid 缺少 CNV 与滤波建立语义。 | 对已选档按真实 CNV、每笔输入归属、数字运算完成和对外公布分别判定。图 99 的 SINC1 两倍抽取实际在 E5/E7/E9 公布、E6/E8/E10 写入三个结果。五阶 CIC 与补偿共用；各档频响和建立分别验证。[原件和时序](MANUAL_TRACE.md) · [数据面用例](../interfaces/dsp_review/README.md) |
| 高：水位分开写入和非法 GPIO 组合 | 从默认 0x4000 分两次普通写转为 0x01AA，中间值可能被水位夹限；旧 GPIO bank 可吞入保留功能码。 | 主机先确认完整水位再 arm，普通写有兼容配方，严格模式一次提交完整实体。原寄存器对 GPIO 0xA 及无效组合整字拒绝，两 nibble 都保留旧值。[厂商驱动与配置检查](../interfaces/control_review/README.md) |
| 高：结果记录原先只验证载荷 | 此前验证到单路逻辑位对，未交付记录头和尾字段。图 87/88/89 实际画明头为 `0xAA`，四线 SPI 是每条线均发同一头。 | 增加可综合记录组帧器，单 LVDS 复用已有载荷链，缺 CRC 服务拒绝。默认 SPI 支持 ADI 固件实际读取的 `3N+1` 或 `3N+4` 字节前缀。两种配置分别完成 98,509 和 227 次 beat 检查、各 15 个故障检查，含满深度 SPI4。独立参考对四组数据、三种模式逐拍核对。一个工程 CRC 例子不决定商用 CRC 多项式。[手册与固件裁定](../io/evidence/record-source-check.md) · [组帧 RTL 合同](../io/FIFO_RECORD.md) |
| 中：运输未上线时的拒收不易发现 | 核心本地复位已释放、I/O 仍停钟时，误送的结果无法识别为丢弃。 | 以 `core_drop_o` 报告启动拒收。`core_overflow_o` 单独记录上线后的满写。两档 40 MS/s、滑相、满空、三类复位冲刷及两档首次采样均有定向结果，12 组通过。持续速率和首次 CNV 相位分别验收。[搬运回执](../transport/verification.txt) |
| 高：工具空跑被报为综合通过 | 用一个直接退出 0 的替身运行两个旧入口，两者仍印出结构通过。核对工具退出码不足以构成网表证据。 | 逐项检查仿真完成行、新生成的 Yosys JSON、正确顶层和真实存储形状。相同替身在两个新入口均退出 1，空仿真和错顶层也会被拒绝。[验证器](../integration/check_tool_run.py) · [反例回执](../integration/evidence/audit_gate.txt) |
| 中：面积和物理核查漏拒 | 裸片界外的占用条带、重复 JSON 字段、侵入 Pad 的软区、同边重叠端口和带不同语义修饰的 DEF 条目原可被接受。 | 约束完整矩形、字段唯一性、端口互斥和禁放原句语义。面积预算和候选宏接收各有 17 个夹具用例，共 34/34；几何负例 22/22，规划几何未改。KLayout 与 gdstk 分别回读。[预算门禁](../budget/README.md) · [几何回执](floorplan-check.json) |

## 从校准输入到记录读回

- **配置 SPI 到结果 SPI。** DSP 单元台架以工程已量化增益、OFFSET、SINC1 N=4 驱动阈值与水位 3，结果为 1885、2535、−3315，SPI1 头和三字依次为 `AA 00075D 0009E7 FFF30D`。数字位序检查与封装传播时限分开。[数据面用例](../interfaces/dsp_review/README.md)
- **实际四银行 SRAM 到单路 LVDS。** 两个实读字经过跨时钟队列、可综合读回/组帧装配和外部测试 CRC 服务，独立接收端得到 `AA 95E2B 80001 81A4D4`，共 72 位、36 对。多读第 3 个地址、未绑定 CRC、半帧掉电、中止后重开均有检查。`81A4D4` 对应这次工程参数，商业产品的覆盖位和 CRC 参数需由原厂或实测确定。[整记录回执](../integration/evidence/readback_link.txt) · [图 89](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf#page=60)

- **主机估计系数到片内校正。** 向合成通道注入 +0.5% 增益和 +80 码零偏。按图 96 先加 OFFSET、再乘 GAIN，独立向量在 g=129/128、x=0、O=1024 下实出 1032。手册两点与 ADCToolbox 正弦两种估计在此例均得到 G=349、O=−80；1,024 个留出码的 RMS 由 1348.1522 降为 0.4038 LSB20。三笔产品 SRAM 读回为 −300000、0、250000；实物校准需真实输入和增益码表。[逐码回执](../calibration/rtl_verification.json) · [SPI 读回](../integration/product/evidence/calibration.txt)
- **DSP 与一份结果阵列。** 普通三字全链记录为 `AA_0075D_009E7_FF30D_C69FFD`。图 99 的另一独立记录按每个实际 I/O 时隙复原为 `AA_000C8_00258_003E8_CED817`。两种均使用声明的工程 CRC 服务；商业参数的证据要求见[记录接口](../io/FIFO_RECORD.md)。[产品实测](../integration/product/README.md)
- **原厂开源资源。** 官方 no-OS C 驱动经真实配置叶子对寄存器和水位等发起事务；官方 FPGA HDL 收端核实时 LVDS 拼字。数据面、控制夹具与完整产品的收据分别列出，模拟采样原语的结果不升级为板测。[no-OS 交互](../interfaces/control_review/noos_validation.txt) · [原版 HDL](../io/adi_receiver/README.md)

## 未发现新增逻辑缺陷的部分

- `result_store` 供 16,384×20 单口访问；49,156 个检查周期覆盖全地址、跨银行和复位。MBIST 两个底图各执行 163,840 次访问，位翻转和缺读有效均被定位。另对冻结 DSP 的普通队列和完整记录控制分别试验。裸队列在非空未满时可同沿读写；记录控制器在捕获与读回之间互斥。本次 16K 专项用反方向请求保持有效做压力，捕获写/读各 16,384 次且从未同沿访问 SRAM。直接用底层队列代替已仲裁的物理 1RW 口会丢失这一前提。[银行回执](result-store.json) · [MBIST](../memory/evidence.json) · [队列/控制对照台](../verify/pinned_fifo_contract_tb.sv)
- 对冻结队列原小台架，强行将 `DEPTH=32` 改为 16384 会先触发其固定 200 µs 看门狗。本门禁保留原台架默认深度，再用独立、按深度定时的 16K 用例验证真实原 RTL；冻结的捕获状态机另以 16K 运行覆盖早事件快照。该超时归在测试台范围。[冻结核心与适配统计](rtl_ownership.json)
- 单双路实时映射的 1,109 个字级检查和 22 位自由输入的组合 SAT 保留。主 SRAM 的四银行、转换组与外围的面积归属、两条模数窗的独立回读仍是独立验证项。工程几何与单元仿真不会自动证明目标工艺面积、带宽、压降或签核。[I/O 证据](../io/handoff_manifest.json) · [几何](floorplan-check.json)

## 手册原件的可复核性

逐模块表从源码声明中找出设计模块和验证专用模块。可选原件检查按 SHA-256 固定 95 页 Rev. B，核查所有手册页锚点及引用的实际图题／表题。每次会打印当前条目、引用和裁定数量。[逐模块正文](MANUAL_TRACE.md) · [图 96 回执](../calibration/rtl_verification.json)

已下载的 Rev. B 原件可用下列命令复核。从仓库根执行，`python3` 需可导入 PyMuPDF（`fitz`）。检查器核原件哈希、页数和逐个编号的实际图题/表题。

```sh
node subsys/ad4080_peripherals/verify/check_manual_trace.mjs --check-local --manual-pdf /tmp/ad4080-manual.pdf
```

## 联调和复跑

人员分工、交接样本与正常/异常操作顺序见[工作树首页](../README.md#外围怎么分工)。总后端接收各岗按同一输入记录交付的期望值、实际值、第一笔差异和源码身份。

从外围子目录运行以下命令。`ADC_FUNCTION_REPO` 指向包含固定 Git 提交的只读检出。`VERILATOR` 和 `YOSYS` 可指定现有工具，`--geometry-python` 用于选择可导入 KLayout/gdstk 的解释器。编译产生在全新的 ASCII 临时目录中。

```sh
# 先按 calibration/README.md 在独立环境安装固定版本的估计器，并激活该环境。
python3 -B verify/run_peripheral_audit.py --case adctoolbox_host
# 全套回归需要 Verilator、Clang、Yosys、ripgrep、KLayout 和 gdstk。
# 两个官方仓的代码均锁定提交与文件摘要，以下形式允许下载到本轮独立目录。
python3 -B verify/run_peripheral_audit.py --jobs 2 --fetch-adi
```

若离线运行，将 `--fetch-adi` 换成 `--adi-noos-archive <官方提交源码包> --adi-hdl-checkout <含固定提交的官方HDL检出>`。可分别用 `--geometry-python` 和 `--calibration-python` 指定已经安装相应依赖的解释器。独立 ZIP 还须用 `--repo` 指向含冻结 DSP 提交的 Git 检出。

默认运行全部登记项。`--case` 只验证选定项。`--require-device-closure` 会在当前仍有实物/物理待办时给出非零退出码，便于把数字回归与产品签收分别使用。[未闭合的 10 类问题、负责人及关闭条件](peripheral_open_items.csv) · [功能 IP 只读记录](function-repo-read-only.json)
