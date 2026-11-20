# 数字外围审查、修正与验收

本轮以旧交付 ZIP 的 SHA-256 `23c4b80789b53c8d173bcdea1dad374814853ae48d67a0a05a55eb78e768d114` 和功能仓提交 `7408edf8db9a41eebdd756378b69d2775f107dec` 为基线。审查沿“配置 → 校正/滤波 → 事件 → 记录/存储 → 组帧/读出”展开，同时检查上电、掉电、测试所有权和版图约束。已有转换 IP 从 Git 对象只读核对。功能矩阵区分 36 项的归属、实测范围、回归入口和剩余条件。[责任矩阵](../interfaces/function_ownership.csv) · [完整运行回执](peripheral-review.json) · [原仓状态](function-repo-read-only.json)

**最终统一复跑为 29 组通过，0 组失败，10 类产品级条件待关闭。** 这些是独立运行组数。一次组内的数十万次访问、单个参数用例和整芯片签核不能相加算成完成度。板级接口基线按 ADI Rev. B 实物资料限定。[本轮 29 组回执](peripheral-review.json) · [手册](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf)

## 发现和实际处置

| 优先级与问题 | 旧行为或证据缺口 | 修改与复测 |
| --- | --- | --- |
| 高：未完采集与存储自测冲突 | 记录尚未写完，却能受理待机。在跨域保持请求发出、AON 真正授权之前，自测已经能使 SRAM 内容作废。功能请求被内部 valid/ready 掩掉还会绕过 entry/reinit 检查。 | 增加未完记录联锁，把保持和破坏性授权分别同步，以原始功能请求判断是否静默。复测撤回短请求、核心停钟、同沿改模式/复位及重建。服务壳合计 13 个场景。[旧断言](../integration/evidence/audit_red.txt) · [新回归](../integration/evidence/regression.txt) |
| 高：OTP 旧响应被新会话接受 | 旧宏应答能被当作第一字，旧校验 PASS 能提交未由该次请求认证的影子表。 | 在相应请求获受理前出现无归属响应，分别以原因 10、11 关闭会话。合法同沿应答、掉电及重新初始化另测。11 组通过。请求已受理后与合法响应无法区分的旧包，仍由宏适配器负责隔离。[新旧同向量回执](../otp/verification.txt) |
| 高：电源状态和配置寄存器分离 | 零时钟绑定仍可能放开配置和转换。冻结寄存器堆本身不接 REFIN 新掉压事件，睡眠/待机和服务壳之间也无贯通的命令路径。 | 零频保持禁止，模拟 POR 事件在常开域捕捉。独立桥把真实 SPI、原寄存器和电源主管接在一起。新事件与写 1 清零碰撞时保留事件，软件复位由两方同沿受理。电源 8 组加 3 个参数测试；控制基线与主机检查 187 项、薄桥 561 项。后两项为外供 IOVDD 数字台架。[电源回执](../power/verification.txt) · [控制面回执](../interfaces/control_review/validation.txt) |
| 高：滤波启动码被当作有效记录 | 冻结 DSP 的 SINC5、N=4、W=1 在立即布防时确实存下 `0`。配置 SPI 可在非空闲时写 FILTER_CONFIG，数值 valid 不代表器件所定义的首笔已建立。 | 在外围接入处加入起录与改配置保护。同一测试条件下，在第 44 个模拟 CNV、第 9 个所选输出出现后授权，实际读回 `−1104`。包括停 CNV、恢复、早事件、旁路换批与未绑定补偿拒绝。完整商品滤波就绪引脚时序、任意原始主机绕过受控命令的情形仍需实际产品绑定。[守卫 RTL](../interfaces/dsp_review/ad4080_record_guard.sv) · [实测顺序](../interfaces/dsp_review/README.md) |
| 高：水位分开写入、非法 GPIO 路由 | 从默认 `0x4000` 写水位 `0x01AA`，普通分字节写在中间被夹限，实读 `0x0100`。冻结 GPIO 选择可接受保留编码 `0xA`。 | 主机参考采用严格双字节实体写、完整回读和模式 11 粒度校验，错误水位不 arm。独立薄桥对 `0x1A/0x1B` 两个地址、高低两个字段及六个保留/未定义码共 24 种组合整字拒绝，保持原值。冻结代码仍可重现原行为。OFFSET 采用 p.92 表 60 的 `0x25/0x26`，记录了 p.71 叙述的地址冲突。[配置与例程](../interfaces/control_review/README.md) |
| 高：结果记录原先只验证载荷 | 此前验证到单路逻辑位对，未交付记录头和尾字段。图 87/88/89 实际画明头为 `0xAA`，四线 SPI 是每条线均发同一头。 | 增加可综合记录组帧器，单 LVDS 复用已有载荷链，缺 CRC 服务拒绝。默认 SPI 支持 ADI 固件实际读取的 `3N+1` 或 `3N+4` 字节前缀。两种配置分别完成 98,509 和 227 次 beat 检查、各 15 个故障检查，含满深度 SPI4。独立参考对四组数据、三种模式逐拍核对。一个工程 CRC 例子不决定商用 CRC 多项式。[手册与固件裁定](../io/evidence/record-source-check.md) · [组帧 RTL 合同](../io/FIFO_RECORD.md) |
| 中：运输未上线时的拒收不易发现 | 核心本地复位已释放、I/O 仍停钟时，误送的结果无法识别为丢弃。 | 以 `core_drop_o` 报告启动拒收。`core_overflow_o` 单独记录上线后的满写。两档 40 MS/s、滑相、满空、三类复位冲刷及两档首次采样均有定向结果，12 组通过。持续速率和首次 CNV 相位分别验收。[搬运回执](../transport/verification.txt) |
| 高：工具空跑被报为综合通过 | 用一个直接退出 0 的替身运行两个旧入口，两者仍印出结构通过。核对工具退出码不足以构成网表证据。 | 逐项检查仿真完成行、新生成的 Yosys JSON、正确顶层和真实存储形状。相同替身在两个新入口均退出 1，空仿真和错顶层也会被拒绝。[验证器](../integration/check_tool_run.py) · [反例回执](../integration/evidence/audit_gate.txt) |
| 中：面积和物理核查漏拒 | 裸片界外的占用条带、重复 JSON 字段、侵入 Pad 的软区、同边重叠端口和带不同语义修饰的 DEF 条目原可被接受。 | 约束完整矩形、字段唯一性、端口互斥和禁放原句语义。面积预算和候选宏接收各有 17 个夹具用例，共 34/34；几何负例 22/22，规划几何未改。KLayout 与 gdstk 分别回读。[预算门禁](../budget/README.md) · [几何回执](floorplan-check.json) |

## 从校准输入到记录读回

- **配置 SPI 到结果 SPI。** 在冻结 DSP 的明确工程系数下，用真实 SPI 配置用户校正、SINC1 四倍抽取、内部高阈触发与水位 3。均值 196 未触发，后续 1106 触发。记录读回 `1886、2536、−3314`。从 SDOB 独立重组得 `AA 00075E 0009E8 FFF30E`，共 80 个线位。此试验从数字转换码边界驱动，在受控时钟下核对数据面。该项使用原 DSP 台架。单存储产品装配及跨环验证见下文。[用例及复跑入口](../interfaces/dsp_review/README.md)
- **实际四银行 SRAM 到单路 LVDS。** 两个实读字经过跨时钟队列、可综合读回/组帧装配和外部测试 CRC 服务，独立接收端得到 `AA 95E2B 80001 81A4D4`，共 72 位、36 对。多读第 3 个地址、未绑定 CRC、半帧掉电、中止后重开均有检查。`81A4D4` 对应这次工程参数，商业产品的覆盖位和 CRC 参数需由原厂或实测确定。[整记录回执](../integration/evidence/readback_link.txt) · [图 89](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf#page=60)

- **主机估计系数到片内校正。** 在已知正弦上调用 ADCToolbox，获得 `OFFSET=-80、GAIN=0x15D`。在工程映射 K=1、SHIFT=15 下，1,024 个留出码的正弦法误差 RMS 从 1348.1522 降到 0.6358 LSB20；用手册两点法为 0.7718。对冻结 `sys_error_corr` 核对了 8,234 个有效输出。随后在同一产品顶层经真实配置 SPI 写入和读回，四银行中三笔独立记录由 `−301420、80、251330` 改善到 `−300001、0、250000`。实物有效增益码表和 OFFSET 灵敏度可用本包台架工具量取。[校准代码与台架步骤](../calibration/README.md) · [完整记录回执](../integration/product/evidence/calibration.txt)
- **原 DSP 与一份结果阵列。** 由同一 SPI、滤波、触发与四个 4096×20 银行产生三字 46 对记录。满深度、掉电、自测和模式 `11` 跨环均做定向回归；环写后的真实首两读址为 `0x3FFF → 0x0000`。Yosys 层级另有一个 8×22 跨域队列。[组装与运行](../integration/product/README.md) · [普通产品回执](../integration/product/evidence/verification.txt)
- **与 ADI 程序互操作。** 固定提交的原版 no-OS C 驱动经配置 RTL 完成 47 项主机断言与 126 帧 SPI。现有水位写序与冻结寄存器的冲突有实测，主机配方在读回完整水位后布防。原版 ADI FPGA 收端在理想采样原语下通过单双 lane 各两个起始相位。实际厂商 FIFO CRC24 需由独立原始帧确定。[no-OS 回执](../interfaces/control_review/noos_validation.txt) · [ADI HDL 收端回执](../io/adi_receiver/evidence.txt) · [CRC 采集及判别程序](../io/adi_crc/README.md)

## 未发现新增逻辑缺陷的部分

- `result_store` 供 16,384×20 单口访问；49,156 个检查周期覆盖全地址、跨银行和复位。MBIST 两个底图各执行 163,840 次访问，位翻转和缺读有效均被定位。另对冻结 DSP 的普通队列和完整记录控制分别试验。裸队列在非空未满时可同沿读写；记录控制器在捕获与读回之间互斥。本次 16K 专项用反方向请求保持有效做压力，捕获写/读各 16,384 次且从未同沿访问 SRAM。直接用底层队列代替已仲裁的物理 1RW 口会丢失这一前提。[银行回执](result-store.json) · [MBIST](../memory/evidence.json) · [队列/控制对照台](../verify/pinned_fifo_contract_tb.sv)
- 对冻结队列原小台架，强行将 `DEPTH=32` 改为 16384 会先触发其固定 200 µs 看门狗。本门禁保留原台架默认深度，再用独立、按深度定时的 16K 用例验证真实原 RTL；冻结的捕获状态机另以 16K 运行覆盖早事件快照。该超时归在测试台范围。[冻结核心与适配统计](rtl_ownership.json)
- 单双路实时映射的 1,109 个字级检查和 22 位自由输入的组合 SAT 保留。主 SRAM 的四银行、转换组与外围的面积归属、两条模数窗的独立回读仍是独立验证项。工程几何与单元仿真不会自动证明目标工艺面积、带宽、压降或签核。[I/O 证据](../io/handoff_manifest.json) · [几何](floorplan-check.json)

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
