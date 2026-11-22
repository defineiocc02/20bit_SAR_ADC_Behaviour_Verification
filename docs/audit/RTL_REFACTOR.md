# RTL 修复与 CI 重构验收

基线 `45f96df784f93c0370b362a01b75b150d394f2a3`。起点包含 `7408edf` 与 `bbcef8e`。所有仿真、开源综合、原生商业工具和 Python 回归在同一虚拟机的独立任务目录实跑。Phase 1 产品实测绑定的 DSP Git 对象为 `54b26147c2c05602c0606937f12d49e28eef6ffa`。既有手册逐沿数据保留其原发布对象，当前回归以[新版机读回执](../../rtl/ad4080_periph/evidence/peripheral-review.json)为准。

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
| `python rtl/ad4080_periph/verify/run_peripheral_audit.py --repo . --work-parent "$RUN_OUT/peripheral" --receipt "$RUN_OUT/peripheral.json"`，同时指定实际工具与官方输入 | 38/38，失败 0，包含 SPI 短保持、产品正常及标称速率、SINC5/PF1、ADCToolbox 校准与通用结构检查。 |
| `yowasp-yosys -p 'read_verilog -sv rtl/sar_core/dem_mask_v3.sv; hierarchy -check -top dem_mask_v3; proc; opt; check -assert; select -assert-none t:$dlatch'` | DEM 通用结构报告 0 问题，0 锁存器。 |
| `dc_shell -f` 读取 v21 生产清单，执行 `analyze`、`elaborate`、`link`、`check_design` | 展开生产顶层成功，通用层级 latch 名称计数为 0。目标库映射和等价留待后续独立门禁。 |

## Phase 2 模型、定点与窗口边界

- 重构 S1a 原先已将 27 位偏移符号扩展至 64 位并与 32 位量化结果相减。保持此数据通路。生产顶层开放 `alpha_q30_cfg`，默认 `2^30` 表示单位增益，在重构受理沿与输入标签同时锁存。合法范围由原重构模块检查，默认码流和 IEEE 有符号运算保持一致。
- 权重表未锁定时，读出的主／子权重对求和树为零。移除倒数链未使用的寄存器。ADC2 捕获批准和登记共享原 `can_accept` 判据，同沿旧码返回优先处理旧 owner。
- SINC1、SINC5、PF1 在本次抽取消费的输入窗口按 OR 保存正负限幅，再随各自有效结果发布。VCS 对三路分别实测两扇窗口。直接 PF1 叶在应用非法配置时给出诊断，依表 62 使用最大档位的行为仍参加数值对拍。
- 四态 Icarus 对相同 FIFO 台架实测。旧版读有效在第一个核心沿之前未清，修订后读有效异步清，队列清除同时清除 overflow 和 underflow，并继续逐字比较物理快照。结果 `checks=2726`。读回链在线资格撤销时弃掉未返回的读预留。
- Python 处理保护命令 520 时仍得到饱和掩码，拒绝超出物理命令范围，bridge 的两条路径都返回独立副本。零倒数与 RTL 一致。样本预算可用 `direction="worst"` 约束最小奇异值方向。可选 `cf_slices` 接受 18 个有限正反馈电容，使用与名义 Cf=16 一致的相对电容单位。

Phase 2 在同一 VM 上通过 11 项 VCS、五组 Verilator 专项、1,943 项 pytest 及 38 项外围验收。默认 Python/RTL 重新逐笔核对 1,792 笔与每笔 36 个字段。原始手册测试的数学数据与厂商接口边界仍从冻结资料读取，新 DSP 源以[当前锁定对象](../../rtl/ad4080_periph/evidence/dsp-revision.txt)为准。

## Phase 3 独立门禁

| VM 实跑命令 | 结果 |
| --- | --- |
| `YOSYS=yowasp-yosys python tools/verify_rtl_quality.py --synth --out "$RUN_OUT/strict"` | 扫描 43 个生产文件。三个顶层的强制 Lint 均为 0 告警和 0 错误。核心、外围的 Yosys `check -assert` 通过，均无锁存器。 |
| `bash tools/check_rtl_mutation.sh --core-only "$RUN_OUT/mutations"` | Icarus 正用例通过，DEM 回绕变异和丢种子变异均成功编译后被各自断言捕获。 |
| `python tools/check_peripheral_counterexamples.py --out "$RUN_OUT/four-state"` | Icarus 证明复位后 POR 旧令牌重放为 0。增益独立对拍 51 个码和 406 项检查通过。 |
| `bash tools/prove_core_safety.sh "$RUN_OUT/safety"` | SBY 的 PDR 证明保护码饱和。另一个 SBY 任务执行 Yosys 两步归纳，采集片数及 ACQ/CONV 互斥均证明。 |
| `python tools/run_open_rtl.py --ci-coverage --simulator verilator --tops tb_i4_recon tb_recon_stream tb_analog_if v21_sample_cadence result_fifo_tb spi_slave_tb cnv_ctrl_contract_tb sys_error_corr_contract_tb sinc5_decim_tb --out "$RUN_OUT/coverage"` | 九组严格编译和真实执行通过。初始化扰动、覆盖率均开启。1792 笔乘 36 字段对拍无差异。 |
| `bash tools/verify_v21.sh` 与 `bash tools/check_rtl_mutation.sh` | 11 项 VCS 回归通过，原算术三种真实变异全部检出并复原。 |
| 全量 pytest 和外围同版验证 | 1943 项通过，外围 38/38。 |
| `fm_shell` 比较 Phase 2 与 Phase 3 的 DEM 通用逻辑网表 | 160 个端口比较点相等，失败 0。范围为单个 DEM 叶。 |

将跨域保持源逐位独立同步，再在目的域合并。软件复位对核心停钟仍能撤销硬件释放资格。生产 Lint 门禁只保留原有的 `result_store.sv` 文件名例外。覆盖率路径使用运行时可设置的夹具输入，避免 Verilator 5.030 把端口默认值优化为不可赋值的 C++ 常量。

## Phase 4 单芯片、文件归属与实跑结果

[单芯片 RTL](../../rtl/top/ad4080_chip_top.sv)装入同一转换核、公共退役器和唯一外围产品。并行 DSP 验证包装也复用公共退役器。产品内部的 `result_fifo` 保留队列指针和状态，数据由同一个 `result_store` 保存。总顶层接真实的已译码 CNV，结果在指定接收槽保留数据和标签。模拟事件的出码截止时间由宏集成约束。

| 命令 | 虚拟机结果 |
| --- | --- |
| `make lint` | 生产文本检查通过，45 源。四个顶层 Verilator 均为零告警、零错误。Yosys 转换核、外围、总顶层均通过 `check -assert`，各无锁存器。总顶层展开为唯一 16,384 × 20 SRAM，四个 4096 × 20 银行。 |
| DC W-2024.09-SP3，`analyze -format sverilog`、`elaborate`、`link`、`check_design` | 读取同一结构检查目录中的 sv2v 展开输入。转换核、外围和参数化总顶层均成功，通用单元名称的锁存计数均为 0。记录仍含常量引脚及未消费诊断的通用 Lint 消息。 |
| `make sim-top` | 启动、实际 ADC2 模型、重构至 DSP 的两条带标签样本通过。A1 的 `0xFFF00` 转为 `0x7FF00`，B2 的 `0x80000` 转为 `0x00000`。22 项断言及无码故障封锁通过。夹具为 100 MHz 核心时钟，按两笔重构完成时间发 CNV。 |
| `make sim-core` | 11 项 VCS 用例全部通过，算术恒等式、条件位宽证明及源码抽取检查通过。 |
| `make sim-periph` | 38/38 组通过。包括真实寄存器、校准与滤波、共享 SRAM 及外部理想输入驱动的 40 MS/s 连续 LVDS。[机读回执](../../rtl/ad4080_periph/evidence/peripheral-review.json) |
| `python -m pytest -q` | 1,943 通过，包含目录身份、归档变异目标与冻结记录哈希。 |
| `make cal` | 图 96 判别码为 1032，26 个滤波档和 1,392 个动态字实测通过，SPI 配置校准读回通过。 |
| [CI 使用的覆盖率仿真](../../.github/workflows/ci.yml)及 `tools/check_default_rtl.py` | 九组通过，默认 Python 与实际转换顶层 1,792 笔 × 36 个字段全部相符。独立 DSP 收码台架运行 1,032 项通过。 |
| `tools/check_peripheral_counterexamples.py`、`tools/check_rtl_mutation.sh --core-only`、`tools/prove_core_safety.sh` | Icarus 和 VCS 四态反例一致，两种逻辑变异捕获，DEM 的 PDR 及调度归纳通过。 |
| `make audit`、`ruff check --select E9,F63,F7,F82 .`、`mypy --config-file=pyproject.toml` | 目录、图册、手册追溯、SHA 清单、Python 语法及 88 个源文件类型检查通过。来源索引静态一致，器件未披露义务保持开放。 |

独立 DSP 收码台架另加 `--coverage --x-initial unique --x-assign unique` 时，Verilator 5.030 生成了常量赋值左值的 C++，因此该次覆盖率构建未完成。其 1,032 项通过记录来自该工具的常规定向运行。

工作树保留五层生产入口 `rtl/{sar_core,ad4080_dsp,ad4080_periph,top,filelists}`。旧独立数字核已迁至 `archive/rtl_core/`，研究实现位于 `experiments/`。Git 可见根树为 10 个目录、5 个文件（含隐藏项），根层说明页只有 README。公开参考 PDF 的 SHA-256 保持原值。[实例归属](../../rtl/ad4080_periph/evidence/rtl_ownership.json) · [参考来源](../../references/source_manifest.json)

### 提交与修改文件

Phase 1 为 `564913e`，Phase 2 为 `61f3f15`，Phase 3 为 `212a86a`。Phase 4 的 DSP 锁定对象为 `2efe16df`。各阶段的完整版本及实际文件列表用以下命令复核。

```bash
git log --first-parent --oneline 45f96df..HEAD
git diff --name-status 564913e^1 564913e
git diff --name-status 61f3f15^1 61f3f15
git diff --name-status 212a86a^1 212a86a
git log --first-parent --format=%H -1 -- rtl/top/ad4080_chip_top.sv |
  xargs -I{} git diff --name-status '{}^1' '{}'
```

## 实物边界

总顶层的本轮两笔回归使用理想宏，整芯片 40 MS/s、真实采后记录和引脚时序仍依赖转换/宏截止期和硬件联调。商业器件的存储、OTP、CRC24、电源、PHY、封装和 PDK 项保留在[实物待办](../../rtl/ad4080_periph/evidence/peripheral_open_items.csv)。现有布局为工艺未绑定的规划几何。[布局原图和约束](../../rtl/ad4080_periph/floorplan/README.md)
