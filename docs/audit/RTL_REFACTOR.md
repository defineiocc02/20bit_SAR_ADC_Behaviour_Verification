# RTL 修复与 CI 重构验收

基线 `45f96df784f93c0370b362a01b75b150d394f2a3`。起点包含 `7408edf` 与 `bbcef8e`。所有仿真、开源综合、原生商业工具和 Python 回归在同一虚拟机的独立任务目录实跑。Phase 1 产品实测绑定的 DSP Git 对象为 `54b26147c2c05602c0606937f12d49e28eef6ffa`。既有手册逐沿数据保留其原发布对象，当前回归以[新版机读回执](../../subsys/ad4080_peripherals/evidence/peripheral-review.json)为准。

## Phase 0 工具实测

原生 VCS W-2024.09-SP1 以 `-full64` 编译并执行 SystemVerilog。DC 与 Formality W-2024.09-SP3 可启动，DC 读取整核源和层级。原 VM 自带 Verilator 4.028 和 Python 3.6.8。Ubuntu 24.04 隔离目录实跑 Verilator 5.030、Icarus 12.0、Yosys 0.33、YoWASP Yosys 0.71、Python 3.12.3、pytest 9.2.0 及 Tcl 8.6.14。原生 VCS 验收脚本另用该任务私有的 Python 3.12.12。

基线 VCS 在三个旧台架复现历史 Flash 外部输入悬空。开源核心四组台架及外围 38 个回归组基线通过。pytest 初跑缺 Tcl，增加隔离依赖后真实执行全部工程用例。

## Phase 1 裁定

| 项目 | 修改和实测 |
| --- | --- |
| DEM 保护码与桥接 | 对完整 10 位码在 520 及以上饱和。为桥接临时量完整赋值，并把相位环减法限定在 3 位。旧版在 528–535 两个 coin 档共 16 项失败，修订后 520–535 共 32 向量通过。 |
| 调度种子与推进 | 同拍片权推进使用沿前随机数并照常完成。PRNG 写回保留新种子供下一笔使用。VCS 旧版得到 `1d3c8cf2`，修订后为写入的 `12345678`。 |
| 双上下文复位 | 原生产 `acq_conv_ctx` 已用 `posedge clk or negedge rst_n`，现有异步低有效写法保留。三个旧端到端台架显式关闭未使用的独立 Flash 返回，原验证断言通过。 |
| SAR/DSP 适配 | 异步复位由硬复位输入驱动，换代在核心沿清状态。新转换故障不截断已冻结 FIFO 的读回。旧版在故障后退役 0 字，修订后按序退役 2 字。没有映射的增益写入明确拒绝，单位增益处理仍有效。 |
| 水位、滤波与 ROM 状态 | 水位写入按实际参数容量钳位，Rev. B 表 56 规定的 CSR 复位值仍为 `0x4000`。表 62 超界码对功能钳位，原始写入字段可读回。表 47 的 ROM CRC/ECC 状态保持只读。目标宏决定它的实际错误来源。 |
| 配置 SPI | 许可未知态仍关断。SDI 先在外部 SCLK 采样，解析器经核心域时钟事件消费数据。保留短保持用例，并登记数据跨域最大延迟和 Pad 建立保持约束。 |
| POR 与电源许可 | POR 事件握手用共同硬 POR 换代，FIFO 独立复位时不重放已经确认的事件。VCS 旧版重放 1 次，修订后为 0。电源、Bank 与发送许可分开在各目的域释放。 |
| 读出链 | I/O 活动错误先寄存再冲刷读回。会话字数在请求握手期间保持，增加稳定性断言。结果 SPI 的内部 I/O 状态使用两级同步的 FULL。主机按外部 FULL 发起首个 DCLK，外露时序仍需 Pad 约束。 |

[ADI Rev. B 手册表 47](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf#page=83) · [表 56](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf#page=91) · [表 62](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf#page=94)

## 实跑门禁

以下从虚拟机中的专用工作目录执行。使用 `V21_WORKDIR` 传入尚未创建的绝对路径。外围验证的官方输入可用 `--adi-noos-archive` 和 `--adi-hdl-source-dir` 指向已冻结来源，验收器会核对源文件散列。

| 命令 | Phase 1 结果 |
| --- | --- |
| `bash tools/verify_v21.sh` | 11 项 VCS RTL 用例全通过，RND96 恒等式、位宽证明和生产源码抽取检查通过。 |
| `python -m pytest -q` | 1943 通过。 |
| `python tools/run_open_rtl.py --simulator verilator --tops tb_analog_if v21_sample_cadence sar20_dsp_integration_tb --out "$RUN_OUT/rtl"` | 三组通过。 |
| `adi-simulate` 的固定 1024 笔 CLI 向量，随后用 `tools/check_default_rtl.py` 比对生产 RTL 的 `samples.csv` 和 CLI 的 `codes.npz` | 1792 笔逐标签，每笔 36 个字段全部相符。附件中的旧脚本名在本检出没有对应文件。 |
| `python subsys/ad4080_peripherals/verify/run_peripheral_audit.py --repo . --work-parent "$RUN_OUT/peripheral" --receipt "$RUN_OUT/peripheral.json"`，同时指定实际工具与官方输入 | 38/38，失败 0，包含 SPI 短保持、产品正常及标称速率、SINC5/PF1、ADCToolbox 校准与通用结构检查。 |
| `yowasp-yosys -p 'read_verilog -sv rtl/dem_mask_v3.sv; hierarchy -check -top dem_mask_v3; proc; opt; check -assert; select -assert-none t:$dlatch'` | DEM 通用结构报告 0 问题，0 锁存器。 |
| `dc_shell -f` 读取 v21 生产清单，执行 `analyze`、`elaborate`、`link`、`check_design` | 展开生产顶层成功，通用层级 latch 名称计数为 0。目标库映射和等价留待后续独立门禁。 |

## Phase 2 模型、定点与窗口边界

- 重构 S1a 原先已将 27 位偏移符号扩展至 64 位并与 32 位量化结果相减。保持此数据通路。生产顶层开放 `alpha_q30_cfg`，默认 `2^30` 表示单位增益，在重构受理沿与输入标签同时锁存。合法范围由原重构模块检查，默认码流和 IEEE 有符号运算保持一致。
- 权重表未锁定时，读出的主／子权重对求和树为零。移除倒数链未使用的寄存器。ADC2 捕获批准和登记共享原 `can_accept` 判据，同沿旧码返回优先处理旧 owner。
- SINC1、SINC5、PF1 在本次抽取消费的输入窗口按 OR 保存正负限幅，再随各自有效结果发布。VCS 对三路分别实测两扇窗口。直接 PF1 叶在应用非法配置时给出诊断，依表 62 使用最大档位的行为仍参加数值对拍。
- 四态 Icarus 对相同 FIFO 台架实测。旧版读有效在第一个核心沿之前未清，修订后读有效异步清，队列清除同时清除 overflow 和 underflow，并继续逐字比较物理快照。结果 `checks=2726`。读回链在线资格撤销时弃掉未返回的读预留。
- Python 处理保护命令 520 时仍得到饱和掩码，拒绝超出物理命令范围，bridge 的两条路径都返回独立副本。零倒数与 RTL 一致。样本预算可用 `direction="worst"` 约束最小奇异值方向。可选 `cf_slices` 接受 18 个有限正反馈电容，使用与名义 Cf=16 一致的相对电容单位。

Phase 2 在同一 VM 上通过 11 项 VCS、五组 Verilator 专项、1,943 项 pytest 及 38 项外围验收。默认 Python/RTL 重新逐笔核对 1,792 笔与每笔 36 个字段。原始手册测试的数学数据与厂商接口边界仍从冻结资料读取，新 DSP 源以[当前锁定对象](../../subsys/ad4080_peripherals/evidence/dsp-revision.txt)为准。

## 后续门禁

Phase 3 的全库严格 Lint 尚待放行。原始盘点发现空引脚连接和变量遮蔽告警，生产源仍存在四态比较。Phase 4 的目标目录和统一顶层在 Phase 3 验收后进入。实物依赖、CRC24 和 ROM/OTP 状态继续由[实物待办](../../subsys/ad4080_peripherals/evidence/peripheral_open_items.csv)跟踪。
