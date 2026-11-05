# 20 位／40 MS/s 流水 SAR ADC：行为模型与数字实现

[English](README_EN.md) · [当前状态](docs/STATUS.md) · [论文与讲稿](references/README.md) · [环境与贡献](CONTRIBUTING.md)

本工程研究多 slice 两级 SAR ADC 中，电容失配、采样／交织误差和数字重构怎样共同影响输出。它包含固定物理电容池的 Python 行为模型、SAR 转换控制与权重重构 RTL，以及独立的输出 DSP 原型；默认采样率参数为 **40 MS/s**。

目前最直接的结果是：从真实结构数字 DUT 的观察数据和独立已知输入片外训练有效位权，冻结并装回 DUT，在同一静态失配 plant 的独立留出样本上降低输出误差。**20 位指最终码宽；行为仿真、数字 RTL、静态 plant 联合验证和真实芯片性能是不同层级。** 本工程尚无目标工艺 STA、晶体管或版图后仿签核，模型中的采样率参数也不证明硬件运行速率。

## 系统结构与配置

![信号链、随机化位置与训练应用边界](docs/readme/fig/current_architecture.png)

*图 1．由当前执行路径和接口合同独立绘制的工程结构，非原芯片完整电路。18 个 slice 是 RDAC 采样／电容资源池：同周期 8 个转换、8 个采集、2 个待选，共享 RA 与 ADC2；不是 18 颗完整独立 ADC。*

| 入口 | 一级／DAC | dither 与调度 | 适合看什么 |
|---|---|---|---|
| Config() | 历史 7 位／unary 基准 | 原有基准设置；执行器各有限定 | 预算和早期机制。不要与九位 RTL 当成同一电路 |
| Config.paper_literal() | 9 位／63+8 split 候选 | generic 调度；启用 quantizer 时采用理想 dual-port 候选 | 物理池与机制敏感性，不是完整芯片网表 |
| Config.rtl_structural() | 9 位／63+8 split，12 位 ADC2 | 离散 range；16 相位；DEM 位联动 slice shuffle | 项目结构 RTL 的样本／mask／事件对照 |

三个工厂的 dither 默认均关闭；表中说明打开相应模式后的合同。结构配置的 16 个理想相位若各占一拍，对应 640 MHz 预算。63+8 分配、bridge 门控和部分模拟参数是工程选择；[配置与适用域](docs/model_scope.md)解释各入口不能混用的部分。

## 四组工作：做了什么，验证到哪里

| 工作 | 解决的问题 | 当前实现 | 验证范围 | 缺口与下一步 |
|---|---|---|---|---|
| **转换与物理信号链** | 保持电荷、时序和样本归属 | Python 采样池／动态链已接入；RTL 提供相位／码／ID | 电荷／因果检查、结构事件与静态 plant 对照；动态模型有各自近似 | 冻结真实比较器／宏关闭沿、局部保持与参考建立合同 |
| **DEM、dither 与 slice 调度** | 减弱失配的输入相关性，观察交织误差 | 已接入物理 mask、零和 bridge 候选、扰动注入／改码／逐 raw 重构 | 项目 mask 与事件一致；固定码使用统计、指定失配行为实验 | 使用偏置与近满幅余量须限域；逐 slice 学习和原芯片完整开关是另外的工作 |
| **训练、冻结与定点重构** | 校正静态有效位权／相关增益 | 片外已知参考拟合 C/Cf，冻结、Q30／Q32 装载及整数重构 | 真实数字 DUT＋固定静态 plant，独立训练／留出与整数对拍 | 真实参考、观察读出／带宽、漂移和动态误差仍需验收 |
| **输出与系统扩展** | 校正、缓存、传输及噪声研究 | DSP／FIFO／SPI 原型；默认关闭 CAAZ 样本域与数字 pair | 定向接口／异常检查；CAAZ 的特定噪声和组合条件实验 | 两点方向、滤波系数、FIFO／串行及元数据未闭合；CAAZ 模拟宏未验证 |

DEM 重排物理单元，dither 注入已知扰动，slice shuffle 改变参与本笔转换的物理组；它们都不自动测得逐 slice 时偏或电容权重。[DEM 与 dither 实现图](docs/randomization_caaz/README.md)分别展示实际 mask、参考轨和数字处理顺序。当前结构接口中，单元 DEM 和 slice shuffle 由同一位控制，不能假定可独立开关。

详细完成状态、条件和依赖只维护在[当前状态页](docs/STATUS.md)。CICC 的连续时间后级／LSB tracking averaging 尚未实现；已有输出 SINC 或 CAAZ 不能作为其复现证据。

## 三个代表结果

### 静态电容失配：训练和冻结重构有没有用

![同一静态plant独立留出数据的校准前后误差](docs/readme/fig/current_calibration.png)

*图 2．真实结构数字 DUT＋静态失配行为 plant；seed 42，DEM＋sampling D=2，4096 个训练／512 个独立留出样本，关闭外加随机噪声。主图前后共用纵轴，右图明确放大校准后残余。不是全码 INL/DNL 或芯片测量。*

实验只改变冻结重构系数，名义／拟合两方使用相同留出 raw 观察。训练器读取数字 mask、码和独立已知输入，不读取 plant 的真实电容。两颗芯片的输入等效误差标准差分别为 **55.75→2.08 µV** 和 **59.38→2.14 µV**，支持静态有效权重校正的作用；它不验证动态建立、参考误差、漂移或 ADC2 非线性。误差均值／RMS与条件见[数据摘要](docs/readme/data/structural_calibration_summary.json)。

[数据与复跑](docs/REPRODUCING.md#静态权重训练与结构dut留出) · [合同与观察位域](docs/adr/0021-structural-contract-calibration.md)

### 交织时偏：杂散变小，总误差会不会更大

![相同时偏下三种Python调度合同的最终整数码频谱与指标](docs/readme/fig/current_interleave.png)

*图 3．40 MS/s，约 4.001465 MHz／2.85 V 峰值，N=16384，相干矩形窗；固定同一组标称 10 ps 高斯 slice 时偏，其他随机噪声关闭。本图为 Python 行为仿真，第三策略按 RTL 分配合同执行，不是直接采集 SV DUT 的频谱。频谱来自最终 20 位码，纵轴为单边 dBFS/bin；不是论文未披露条件下的同条件复现。*

固定 A/B、generic 随机和按 RTL 分配合同运行的 Python 策略，其 **SNDR 为 95.98／84.22／84.93 dB，SFDR 为 96.00／108.75／110.93 dBc**。本例随机化分散了杂散，同时增大除直流和基波外的残余功率。拟合只用于计算指标，没有对输出作增益或相位校正。对理想输入的总误差 RMS 为 **127.97／182.15／176.44 µV**；该 RMS 保留基波增益／相位误差，与基波拟合后的 SNDR 残差不是同一口径。不能只凭 SFDR 推断总体收益。

[条件、数据与复跑](docs/REPRODUCING.md#交织调度的当前严格基线)；固定轮换、generic 随机与 RTL 分配规则不同，不能用一个“random”标签代替它们。

### dither：RDAC 码合法，为什么后级仍会越界

![dither近满幅时各节点的余量和异常](docs/readme/fig/current_dither_headroom.png)

*图 4．理想器件的结构 range 配置，40 MS/s、N=2048、fin=251Fs/2048、相干输入，扫描全部指定幅度并保留失败点；没有通过删异常点改善曲线。*

输入峰值 2.99 V 时，quantizer dither 的 RDAC 命令仍在 0…511，最终数字 clip 为零，但 SADC 输入有 18 次超出标称范围、RA 有 4 次饱和、ADC2 有 15 次溢出。相同条件的 off／sampling 没有这些模拟越界。说明该 range 实现需要单独检查近满幅余量，不能用“最终码没有 clip”判正常。结构 dither 的实际序列由 D=2 决定；浮点 amplitude 请求现已在有效配置报告中明确标为不生效。

[数据、边界定义与复跑](docs/REPRODUCING.md#dither的近满幅余量)；单元使用偏置及 bridge 条件统计见[机制专题](docs/randomization_caaz/README.md)。

## 当前最重要的下一步

1. **模拟时序与观察链路。** 真实保持／复位、参考与 ADC2 关闭沿未闭合，会影响当前功能夹具向电路落地。先冻结宏与观察输送合同；完成标志是采样保持／非交叠／截止建立有真实证据，且实际可读观察与独立参考跑通训练／冻结／留出、无跨 epoch 拼接。
2. **输出原型中的已确认缺口。** 两点逆向校正、有效滤波系数、FIFO—串行路径与逐结果元数据需要分别完成；验收按连续输出的码、ID和异常一致性，不只看模块有无实例化。
3. **范围和扩展分别立项。** DEM 使用偏置、dither 余量、调度取舍先保留严格诊断；逐 slice 估计、完整原芯片 bridge／dual-port、CAAZ 模拟宏和 CICC 后级是独立研究项。各自明确观察、目标与解除条件，不能全部归成“项目缺陷”。

CAAZ 与随机化组合还要求真实 CBIAS 在两周期保持、dither 注入点正确、两笔归一化噪声传递匹配；相邻两组 slice 不能简单锁成同一组。见[组合条件](docs/randomization_caaz/README.md)。

## 从哪里复现

先按[环境说明](CONTRIBUTING.md)安装 Python 依赖；结构 DUT 实验另需 Verilator 5.x 和 C++ 编译器。所有重新仿真／训练命令使用**尚不存在的新输出目录**。

**A．快速功能检查**：检查结构参考事件、样本归属和非法几何拒绝，不需要 Verilator，也不代表性能达标。

```bash
PYTHONPATH=src pytest -q tests/unit/test_structural_contract.py
```

**B．重新训练与仿真**：结构 profile、固定失配 plant、独立训练／留出；输出 raw 观察、冻结系数和 summary.json。成功还需摘要中留出改善、独立整数对拍及零异常成立。

```bash
OPENBLAS_NUM_THREADS=1 PYTHONPATH=src python tools/run_structural_calibration.py \
  --output "${TMPDIR:-/tmp}/sar-calibration-new" --train 4096 --holdout 512 --chip-seeds 42 91
```

**C．只重绘已有数据**：读取随库冻结数据，不重新仿真／训练；输出上述原创 PNG／SVG。图可重画只证明数据—图表达一致。

```bash
MPLBACKEND=Agg PYTHONPATH=src python docs/readme/make_overview_figures.py \
  --out "${TMPDIR:-/tmp}/sar-overview-figures"
```

交织和 dither 的重新仿真、成功判据、节点／频谱口径及历史数据处理见[完整复现入口](docs/REPRODUCING.md)。

## 按问题找代码与文档

| 想理解什么 | 入口 |
|---|---|
| 完整物理行为链与配置 | [pipeline_engine.py](src/adi_model/pipeline_engine.py) · [config.py](src/adi_model/config.py) · [适用域](docs/model_scope.md) |
| DEM、bridge 与实际物理组 | [dem.py](src/adi_model/dem.py) · [slice_pool.py](src/adi_model/slice_pool.py) · [调度](src/adi_model/timing.py) · [交织复跑](docs/readme/reproduce_interleave_40m.py) · [指标](src/adi_model/metrics.py) · [实现图](docs/randomization_caaz/README.md) |
| dither 在哪里加、怎样去除 | [sampler.py](src/adi_model/sampler.py) · [sampling_charge.py](src/adi_model/sampling_charge.py) · [数字观察与电荷项](src/adi_model/weight_calibration.py) |
| 权重如何学习、冻结与装载 | [weight_calibration.py](src/adi_model/weight_calibration.py) · [fixed_point.py](src/adi_model/fixed_point.py) · [ADR0021](docs/adr/0021-structural-contract-calibration.md) |
| RTL 接口、样本与异常 | [数字核](rtl/top/sar20_digital_core.sv) · [RTL 算术合同](docs/rtl/RTL_ARITHMETIC_CONTRACT.md) · [DSP 接口](ad4080_dsp/docs/SAR20_INTERFACE.md) |
| 当前限制与本轮核查 | [当前状态](docs/STATUS.md) · [模型范围](docs/model_scope.md) · [复核记录](docs/reviews/20261105-readability-maintenance.md) |
| 原文与历史证据 | [三组论文／讲稿原件](references/README.md) · [来源清单](sources_manifest.json) · [历史文档](docs/history/) |

代码采用 [BSD-3-Clause](LICENSE)；选定原件保留其原版权和内嵌声明，见[NOTICE](NOTICE)。本项目独立于原器件作者／厂商，不授予专利许可。版本历史和详细验证记录保留在链接文档，不作为首页主体。
