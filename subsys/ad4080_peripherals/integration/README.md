# AD4080 外围服务 RTL 装配

`ad4080_peripheral_services` 实例化供电管理、只读 OTP shadow、单口结果 SRAM 及 MBIST 仲裁，还有 I/O 域的 DDR 边沿字映射。`files.f` 以交付根目录为基准，直接引用外围各模块的原始源码。功能 IP 通过配置、转换许可、存储请求及完整字接口接入。

## 时钟、复位与正常路径

| 域 | 事务 | 复位和跨域合同 |
| --- | --- | --- |
| `clk_aon_i` | 电源状态和模式、OTP 读与整像校验、BIST 会话申请 | `cfg_hw_reset_no` 由 VDD11 POR 异步拉低，释放在 AON 域。供电状态由 supervisor 处理。OTP 宏电源资格 `otp_power_good_aon_i` 由宏适配器在 AON 域提供。 |
| `clk_core_i` | 结果 SRAM、MBIST、功能读写、转换许可 | `fifo_reset_no` 为硬 POR 下的核心域复位，供 SRAM 端口、MBIST 和外部 FIFO 元数据共用。`core_reset_no` 另外管辖可软件复位的核心数据通路。 |
| `clk_io_i` | 已交付的完整字、lane 模式、边沿槽向量 | `io_reset_no` 清外部 I/O 事务。映射是组合逻辑，使用在本域成组交付的字和模式。I/O 必须先有内部字时钟才能释放许可。 |

硬 POR 释放后，AON 的首次初始化计数完成即建立 OTP 启动资格。OTP loader 等宏资格、适配器冲刷完成、各字响应和完整镜像判决。这些条件可在 REFIN、VDD33、IOVDD 及模拟 `ready` 未到达时完成。校验通过沿原子地发布完整影子，逻辑字 0 位于最低位。AON 转换许可同时要求这一提交、供电主管的运行许可与 BIST 互锁空闲。核心写许可、核心读许可和 I/O 字许可均异步撤销、在目的域经两级同步释放。影子镜像供 AON 域消费者整体采纳，跨域 trim 搬运及施加时序由外部宏适配器负责。

对结果口，在 `clk_core_i` 上升沿以 `result_req_valid_core_i && result_req_ready_core_o` 受理请求，地址、写标志和写数据属于同一笔。读在受理沿后返回 `result_read_valid_core_o` 和 `result_read_data_core_o`，消费端只在 valid 高时取样。返回口无应答 ready，外部功能 IP 对每笔读预留消费位置。写许可用转换准入，读许可用独立 FIFO 读出资格。SRAM 在硬 POR 后视为未初始化，由外部 FIFO 有效态管理。配置侧保留电源主管的控制地址和普通访问基础许可，功能地址译码、FIFO 指针、阈值与功能数据机由功能 IP 管理。

`clients_idle_aon_i` 汇总转换、已受理的存储操作和在途 I/O。`capture_incomplete_aon_i` 由记录控制器同步送至 AON，从布防保持到最终写入实际受理，或由记录控制器显式废弃。两个采集字之间空拍时仍阻止软复位、低功耗写和破坏性测试。已完成但未读的记录可在无在途操作时接受上述非破坏性命令，FIFO 元数据按硬复位域保存。外供待机可保留已有记录的读口资格，LVDS 与新转换暂停。`por_ana_event_aon_o` 将参考故障事件交给配置域，粘滞状态及写 1 清零由 CSR 负责。

## BIST、故障与退出

| 事件 | 装配层的行为 |
| --- | --- |
| 同拍测试申请和模式/软件复位命令 | 具备资格的 BIST 请求先封住管理侧空闲许可和新转换。未完成记录时测试申请保持未受理，记录仍可继续产生结果。 |
| BIST 进入、运行 | 核心收到保持申请后先停止新功能访问，已受理读可以排尽。AON 收到保持确认后才授予 `bist_mode_grant_aon_o`，其高有效授权在核心经两级释放。启动同拍须 `bist_start_core_i && bist_start_ready_core_o`。除 `bist_func_quiesced_core_i` 外，实际结果请求 valid 也须为低。测试返回隐藏，I/O 字许可随 SRAM 内容作废撤销。 |
| BIST 退出或中止 | AON 撤回授权立即禁止后续测试访问，仍等待核心释放回执。一次短申请也完成往返。若发生破坏性访问，功能侧重建指针和有效态、撤回旧请求，再给 `bist_func_reinit_done_core_i`。核心口仍有 held valid 或未排尽返回时保持作废。 |
| 软件复位 | `core_reset_no` 下降并重新初始化。OTP 已提交的 shadow、SRAM 阵列和外部 FIFO 硬复位域保留。|
| REFIN 或 VDD33 丢失 | 模拟和核心写许可撤销。保留电源下既有结果仍按主管的读口策略服务。 |
| IOVDD 丢失 | 封住读出和 I/O 字；符合供电及 OTP 资格的新转换仍可生产结果。 |
| OTP 宏供电丢失、读错或校验失败 | 影子和相应发布许可被遮蔽，OTP loader 进入失败终态。需要协调硬 POR 与适配器重新冲刷，启动一轮新加载。硬 POR 在校验返回窗口优先。 |

## I/O 搬运接缝

外部搬运器在 `clk_io_i` 域驱动 `io_sample_i[19:0]`、`io_dual_lane_i`、`io_intf_check_i` 和 `io_word_valid_i`，保持整笔至 `io_word_ready_o`。`io_edge_words_valid_o` 高的该周期，同域消费者一次采纳四组 10 位边沿向量和 `io_slot_count_o`。向量 bit 0 对应最早槽，同一槽先下降沿再上升沿。当前连接约定每个打开许可的 I/O 周期可采纳一个完整字，若消费者正在移出上一帧，搬运器应推迟交付。

### REFIN 与固定接口检查字

Rev. B 第 24 页区分本次冷启动从未有 REFIN 和曾提供参考后移除两种情况。服务壳采纳 VDD33 与 REFIN 的外部合格信号，以两个 AON 沿确认后在第三个沿登记本次硬 POR 会话的参考历史。固定字许可再经 I/O 域两级释放。硬 POR 清历史。软复位、低功耗和后续参考掉落可以保留历史，仍分别受初始化、正常模式和 I/O/OTP/BIST 条件约束。第 84 页表 48 的固定字为 `0xAC5D6`。新转换和新写事务走主管的当前参考资格，参考掉落时撤销。已经交给 I/O 域的普通字和保持供电下的 FIFO 读口继续按各自会话许可工作。[Rev. B 第 24、26–28、83–84 页](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf#page=24)

三域台架直接通过服务壳与原边沿字映射器核对 `DA_FALL/DA_RISE/DB_FALL/DB_RISE`。本次证据止于完整字 ready/valid 和逻辑边沿向量。产品读回装配的固定字输入绑为 0，并使用普通 I/O 字许可作为 FIFO 会话资格，实时 LVDS 和物理引脚由产品集成负责。[实际台架回执](evidence/regression.txt)

### POR_ANA 故障交付

Rev. B 第 24 页和第 83 页表 47 将参考故障状态映射为 `DEVICE_STATUS(0x14)[3]`，该位默认置 1、写 1 清除。服务壳使用主管的真实 `por_ana_event_aon_o`。AON 改变请求位后等核心域确认，在途时新事件合并为一笔待交付。核心停钟后保留请求，在恢复后的接收沿把 `por_ana_event_core_o` 交给 `ad4080_top.por_ana_event_i`。产品开启 `P_POR_ANA_EVENT_ENABLE`，核心 bank 的 bit3 为唯一粘滞位，同沿新事件优先于 W1C。既有无绑定用户默认关闭该能力。

交付两端共用 VDD11 硬 POR，分别在 AON 和核心时钟同步释放。硬 POR 取消旧待发事件并将 CSR 回到复位默认。IOVDD、软件初始化和功耗模式转换不取消已捕获的事件。参考持续低压只产生一次发生沿。一笔在途和一笔待发之外的新故障合并，故障状态可保证置位，次数或原始脉宽由别处记录。真实最短可捕获脉宽、跨域恢复和 MTBF 由物理实现验收。独立的 `ROM_CRC_ERR` 保持来源待接。

可从本目录复跑 `run_verify.sh` 验证监视器到核心交付，并用真实寄存器接收端单独重现同沿 W1C。

```sh
por_csr_obj=$(mktemp -d /tmp/ad4080-por-csr.XXXXXX)
"${VERILATOR:-verilator}" --binary --timing --assert --output-split 0 --compiler clang \
  -CFLAGS '-std=c++20' -j 2 -Wno-PINMISSING --top-module por_ana_csr_tb --Mdir "$por_csr_obj" \
  ../ad4080_dsp/rtl/ad4080_regs.sv integration/por_ana_csr_tb.sv
"$por_csr_obj/Vpor_ana_csr_tb"
```

此台架只观察已提交 CSR 事务，故省略无关的寄存器输出端。实测开启和默认关闭两种配置、首次和重复置位、同沿优先、后续清除、软硬复位及长指令状态位。产品串行 `0x14` 读回由产品顶层台架验收。[回执与源哈希](evidence/regression.txt)

实时转换结果可使用独立的 `transport/ad4080_result_transport.sv` 完成整字 CDC、边沿映射和槽推进。实时来源按同一个核心沿交付 `result_valid_core_i`、`result_core_i`、`dual_lane_core_i` 和 `intf_check_core_i`。双 lane 与固定接口检查字由这个实时/接口测试入口提供。其发端在上层定义的无反压合同下工作，满字丢弃有独立诊断。

### 采后 FIFO 的单 lane 载荷读回

`ad4080_result_readback_link.sv` 实现推荐连接，复用同一个 transport 与边沿映射源。Rev. B 第 59 页规定 FIFO 的 LVDS 访问仅单 lane。`read_req_valid_core_i` 高时如果同时请求双 lane 或 `INTF_CHECK`，适配器置 `read_mode_error_core_o`、保持 ready 低且不产生 SRAM 请求。上游收到明确拒绝后撤回或更正该描述符，窗口内无需推进 FIFO 读指针。合法读回传输 20 位原载荷、DA 十个双沿位对，DB 输出为零。[AD4080 Rev. B 第 59–60 页](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf#page=59)

| 绑定面 | 时序合同 |
| --- | --- |
| FIFO 读描述符 | 上游在采集完成后发读地址和选项。以 `read_req_valid_core_i && read_req_ready_core_o` 受理。适配器仅在 transport 在线、至少有一格空位且无在途返回时提出 SRAM 读。 |
| 壳层单口 | 上游将 `sram_read_req_*_core` 和写请求做显式互斥仲裁。读的 ready 只在仲裁选中该读且壳层许可时返回。该绑法上所有 SRAM 读返回专供本适配器，固定在受理后的下一核心沿取样。缺失、额外或无法交付的返回锁存 `read_protocol_error_core_o` 并挡住新读，直到会话重置。 |
| I/O 资格和冲刷 | 壳层 `fifo_reset_no`、`io_reset_no`、`io_word_ready_o` 分别接 `fifo_reset_ni`、`io_reset_ni`、`io_session_allow_i`。任一撤销共同冲刷在途读预留、队列及在发槽。`io_start_allow_i` 为本域新帧调度，普通暂停会送完当前字。外层记录控制处理失效时尚未发送的读回。 |
| 测试静默 | `link_idle_core_o` 在已受理读与最后一槽退休后置位；共同冲刷时为空。结合外部来源停止与功能流水空确认 `bist_func_quiesced_core_i`。BIST 作废 SRAM 时维持原静默回执。 |
| PHY 逻辑输入 | PHY 在 `clk_io_i` 上升沿采纳 `io_pair_valid_o`、`io_first_o`、`io_last_o` 和 DA 双沿位。选用该绑定时壳层的并行字映射入口保持空闲。 |

### 完整单 lane LVDS 记录的装配入口

将 `integration/ad4080_fifo_lvds_record_link.sv` 作为读回装配叶子，与原壳层共用一块结果 SRAM。它包含一个原 `ad4080_result_readback_link` 与一个 `io/ad4080_fifo_record_framer.sv`。完整端口在 `integration/readback_files.f` 和 `integration/files.f` 展开，旧裸载荷入口仍可单独使用。

| 连接 | 受理和退出 |
| --- | --- |
| 壳层与单口 | 用上表的 FIFO 复位、I/O 复位和会话允许。将本叶子的 `sram_read_req_*` 与功能写入显式仲裁，已选读的同一拍 ready 和唯一读返回都接回本叶子。 |
| 描述符和读地址 | I/O 域以 `record_start_valid_i && record_start_ready_o` 检查 `record_count_i`、格式与 CRC 服务，真实受理看 `record_start_accept_o`。有效字数在请求往返期间保持。核心侧 `read_req_valid_core_i && read_req_ready_core_o` 依次取外部冻结地址列表，信用只允许提交本条记录的字数。 |
| CRC 和装载拍 | CRC 服务在真实命令受理沿开始，经 `crc_valid_i/crc_ready_o` 提供线序尾值。`beat_pair_o` 与有效位、整记录首末及 `beat_kind_o` 同拍。PHY 必须逐拍承接已经发出的载荷，头和 CRC 不占 SRAM 字。 |
| 错误与重新进入 | 未绑定格式或 CRC 服务在首头前拒绝，地址无受理。运行故障或显式 `abort_i` 中止后冲刷旧搬运，新的 start 待会话往返结束。供电撤销直接撤销整个 epoch，上层抛弃未完成记录并决定是否重读。对测试静默，将 `record_idle_core_o` 与上游来源停止和功能流水空合取。 |

第 59–60 页图 89 的布局经逻辑位对联测。真实 SRAM 两字 `95E2B` 和 `80001` 组出 `AA_95E2B_80001_81A4D4`，依次为 4 个头位对、20 个载荷位对、12 个 CRC 位对。`81A4D4` 使用测试 CRC24 多项式 `864CFB` 和初值 `B704CE`，按交付的头和两个实读字计算，独立 GF(2) 长除作黄金。产品 CRC 参数/覆盖范围、实际 ODDR/DCO/CNV 相位和 PHY 电气绑定仍由相应负责人验收。记录组帧和独立 DSP 的联调证据也依输入版本分别归属。

## 复现与证据

在交付目录执行以下命令。`VERILATOR` 可覆盖默认的 `verilator` 命令。设置 `YOSYS` 时追加通用综合结构检查。

```sh
bash integration/run_verify.sh
YOSYS=yosys bash integration/run_verify.sh
bash integration/run_linked.sh
YOSYS=yosys bash integration/run_linked.sh
```

脚本在独立 ASCII `/tmp` 目录产生 lint、编译、仿真及可选 Yosys 日志。每次检查实际仿真完成行，检查本轮 Yosys 日志和生成 JSON 的目标顶层。服务层 JSON 须有四个 4096×20 存储实例，完整记录装配叶子 JSON 只有一个默认 8×22 搬运队列。运行前后校验源文件和入口脚本的 SHA-256。严格 `-Wall` 唯一抑制为既有 SRAM 银行叶子的文件命名提示。OTP 默认 1 字 × 1 位与 1 周期预算为占位，集成须指定来自实际宏与镜像格式的参数。

Verilator 5.30 使用 10 MHz AON、14 ns 核心周期和 18 ns I/O 周期。试验镜像为 3 字 × 8 位，实读银行 3 地址 `0x3002` 的 `0x95E2B`，单双 lane 及固定接口检查字分别比对。

| 场景 | 仿真观察 |
| --- | --- |
| 冷启动至转换 | 模拟参考及 I/O 电源未合格时已读取完 OTP，供电条件先就绪仍挡住转换。校验通过发布 `0x5CA712` 后，AON 和核心依次获得转换许可。 |
| 结果位序 | 单 lane 的 DA FALL、DA RISE 为 `0x3B1`、`0x21E`，DB 全零。双 lane 的 DA FALL、DA RISE、DB FALL、DB RISE 为 `0x015`、`0x01C`、`0x006`、`0x013`。固定检查字使用独立的期望向量。 |
| 未完记录及待机 | 总线瞬时空闲且记录未完时，同沿软复位、待机和测试均被挡住，转换仍可继续。记录完成后可进待机，并保留旧 SRAM 读；wake confirm 陈旧或缺失时禁止转换。 |
| 保留与供电故障 | 软件复位保留影子和 SRAM 结果。REFIN 掉落禁止新写且保留旧读。IOVDD 掉落关闭读和边沿字，转换资格仍有效。 |
| BIST 进入与退出 | 保持阶段提前 start 无破坏性操作。核心停钟时短申请保留，功能读响应遮罩通过。测试启动和重建恢复都拒绝 held valid，实际中止报原因 3。 |
| OTP 故障三组 | 宏供电失效撤销发布并锁存原因 1。校验通过返回窗口内硬 POR 阻止提交。独立的拒绝判决锁存原因 3，影子和转换资格均无效。 |
| REFIN 冷启与再次掉落 | 其余电源和 OTP 已好时，冷启从未合格的参考使固定字 ready/valid 为 0。首次参考合格经 AON 三沿、I/O 两沿开放固定字。参考已合格后掉落，新转换和写许可异步撤销。固定字保持有效，参考故障事件在第三个随后 AON 沿送达。 |
| I/O 停钟及低功耗恢复 | IOVDD 掉压即撤固定字，即使 I/O 已停钟。重供复钟后，从目的许可请求起第二个 I/O 沿恢复。待机、睡眠退出的附加恢复请求分别出现在命令受理后第 1001、1801 个 AON 沿。台架暂不给 done 时保持关闭，收到本轮完成才恢复。该延迟来自仿真激励，手册图 44 的实物 `t_recover` 留给模拟与系统验收。 |

源哈希及运行片段见 [sources.sha256](evidence/sources.sha256) 和 [regression.log](evidence/regression.txt)。旧包复现、修正和剩余绑定详见 [审查记录](REVIEW.md)。

`run_linked.sh` 另行展开 `readback_files.f`。同一个真实壳层 SRAM 在联测中分别选择裸载荷和整记录读回，FIFO 数据均经过各自实际的 readback 与 transport。核心侧台架写 SRAM、发读描述符，I/O 监视器独立重组位对。Verilator 5.30 检查了以下场景。

| 场景 | 跨包观察 |
| --- | --- |
| 合法载荷与模式拒绝 | 双 lane 与 `INTF_CHECK` 均报模式拒绝且 SRAM 无请求，原地址随后可合法读出。 |
| 队列满与连续传输 | 四格积满时第五笔持续五个核心周期未发读。释放后连续重组 `95E2B`、`80001`、`3F317`、`FEDC9`、`1AB07`，每字十槽，保留顺序且未报丢弃。 |
| IOVDD 冲刷与复电 | 在首槽已交付、另有字排队时掉压，停止两域及当前帧，VDD11 的 SRAM 仍保留。复电后一段观察窗口无旧槽，合法新读的首字为 `3BC71`。 |
| BIST 静默 | SRAM 测试开始撤销 I/O 会话时，已排空回执保持。显式中止与元数据重建后可恢复核心读写。 |
| 单拍返回合同 | 隐藏一次实读返回或注入无配对返回。均记录协议故障，同沿新地址没有被误受理。 |
| 整记录 | 格式未绑定或 CRC 服务不可用均拒绝，活动记录的中止会冲刷旧数据。两字预算阻断第三地址，真实整记录逐位符合黄金。 |

严格 RTL lint 和 Yosys 可选通用结构检查均通过。证据见 [readback_link.log](evidence/readback_link.txt) 和 [readback_sources.sha256](evidence/readback_sources.sha256)。

验证范围是外围 RTL 回归与通用结构检查。工艺 OTP/SRAM 宏、校验算法与 trim 映射、实际功能 IP 或 pad 绑定、物理隔离、跨域 MTBF、RDC/CDC 专项验证和库级 STA 仍是各自集成项。这里的 SRAM 运行由可推断存储模型支撑，首个装配台架的 BIST 运行到实读并中止。全地址算法覆盖参见 `memory/` 的独立证据。
