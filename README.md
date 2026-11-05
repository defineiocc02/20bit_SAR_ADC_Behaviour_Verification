# 20 位／40 MS/s 流水 SAR ADC：行为模型、静态校准与数字实现

[English](README_EN.md) · [详细状态](docs/STATUS.md) · [论文与讲稿](references/README.md) · [环境与贡献](CONTRIBUTING.md)

## 项目目的与范围

本工程研究多切片（slice）两级 SAR ADC 中的电容失配、采样／交织误差，以及数字校准能修正哪些误差。实际交付三部分：**ADC 行为模型、静态失配校准流程、数字控制与重构 RTL**。动态元件匹配（DEM）、已知扰动（dither）和切片调度在对应实现与实验中评价；输出 DSP 和斩波协助自归零（CAAZ）作为扩展单独说明。

**20 位是最终输出码宽，40 MS/s 是默认采样率参数。** 当前证据包括行为仿真、数字 RTL 仿真，以及数字 RTL 与模拟被测 ADC 的行为模型联合验证；不等同于20位有效位数（ENOB）或目标工艺硬件速率。目标工艺时序、晶体管与版图后仿尚未签核。

## 已经完成的工作与当前状态

| 工作项 | 实际完成的内容 | 证据入口 | 当前结论 | 尚未完成的内容 |
|---|---|---|---|---|
| **ADC 行为模型** | 按实际切片保存采样电荷，模拟一级判决、残差 DAC、共享残差放大器与后级量化；包含失配、噪声及有限建立模型 | [模型范围与假设](docs/model_scope.md) · 下方交织／dither 实验 | 可运行转换与误差实验；已发现调度取舍和余量限制，未证明完整动态性能满足目标 | 真实开关、关闭沿、参考建立和器件噪声传递验证 |
| **静态失配校准流程** | 用独立已知输入与数字观测训练有效位权，固定系数、装入 RTL，并在未参与训练的数据上输出整数码 | [图1数据](docs/readme/data/structural_calibration_summary.json) · [训练与验证](docs/REPRODUCING.md#静态权重训练与结构dut留出) | 指定静态条件下效果已有验证，两颗失配模型的误差标准差均下降 | 真实参考、观测读出、漂移／动态训练及后级非线性 |
| **数字控制与重构 RTL** | 实现转换时序、物理选择、DEM／dither、样本归属、权重装载、定点重构及异常处理 | [接口规则](docs/adr/0021-structural-contract-calibration.md) · [计算规则](docs/rtl/RTL_ARITHMETIC_CONTRACT.md) · [受测提交CI](https://github.com/defineiocc02/20bit_SAR_ADC_Behaviour_Verification/actions/runs/41970250280) | 项目掩码／事件与 Python 一致，指定整数结果对拍及数字回归通过 | 比较器／模拟宏真实时序与电气接口验证、外部观察输送、目标工艺实现与时序验证 |

**扩展当前做到哪里：** DSP 已有校正、滤波、FIFO 和串行原型，两点校正方向、有效滤波系数、FIFO—串行连通及逐结果元数据仍有缺口。CAAZ默认关闭；启用时将40 MS/s的单次转换配对为20 MS/s输出。其数字配对与条件行为实验已有结果，真实自归零电容保持及噪声传递／信号增益之比（H/K）尚未验证。扩展单独评价，详见[状态页](docs/STATUS.md)。

## 主要实验结果

### 静态校准效果已有验证：误差标准差 55.75→2.08 µV

片外训练有效电容比位权后固定系数，名义／训练系数处理**同一批未参与训练的原始观测**，两方均输出最终20位整数码。观测包含粗码、后级码、实际切片和开关选择；训练器另用独立已知输入，不读取真实电容值。

![指定静态条件下校准前后误差标准差55.75降至2.08微伏](docs/readme/fig/current_calibration.png)

*图1｜静态校准效果在指定条件下已有验证。数字RTL＋固定静态失配行为模型，seed42、DEM＋sampling D=2，4096训练／512独立验证样本，无外加随机噪声。前后同纵轴，右图明确放大残余。[数据](docs/readme/data/structural_calibration_summary.json) · [重新训练与验证](docs/REPRODUCING.md#静态权重训练与结构dut留出)。*

第二颗模型（seed91）的标准差为 **59.38→2.14 µV**，结果同样保留。均值、标准差与均方根（RMS）分别记录。这证明静态有效位权校正的效果，未验证动态建立、真实参考、漂移或后级非线性，也不是完整全码INL/DNL。

### 交织取舍已量化：SFDR 提高 14.93 dB，SNDR 下降 11.05 dB

同时比较无杂散动态范围（SFDR）与信号对噪声和失真比（SNDR）。固定A/B切片分配、通用模型的随机选择、**按RTL规则运行的Python选择策略**使用同一时偏池、输入和最终码量化。标题差值对应第三种策略相对固定A/B：它分散杂散，同时增加非基波残余功率。完成的是调度取舍研究，逐切片时偏估计与补偿尚未实现。

![三种Python调度策略的频谱及SFDR和SNDR取舍](docs/readme/fig/current_interleave.png)

*图2｜行为模型中的交织比较。40 MS/s、约4.001465 MHz／2.85 V峰值，N=16384，同一标称10 ps高斯时偏实现，关闭电容失配及其他随机噪声；相干矩形窗，最终20位码、全Nyquist频段的单边dBFS/bin频谱。第三条不是SV输出频谱。[条件与数据](docs/readme/data/interleave_40m.json) · [重新仿真与严格比较](docs/REPRODUCING.md#交织调度的当前严格基线)。*

三策略相对理想输入的误差RMS为 **127.97／182.15／176.44 µV**，保留基波增益／相位误差；SNDR使用剔除直流和拟合基波后的残余功率。拟合只计算指标，未校正输出。该结果不能推广为随机调度总能改善精度，也不称论文未披露条件下的定量复现。

### dither 余量问题已定位，近满幅越界仍存在

扫描输入幅度并逐节点计数，定位到结构量化器dither的近满幅限制。**峰值2.99 V时，残差DAC（RDAC）命令仍合法、最终码没有数字裁剪（clip），内部仍会越界：一级量化器（SADC）输入超标称范围18次、残差放大器饱和4次、后级量化器溢出15次。** 同条件的关闭扰动／采样扰动模式没有这些模拟越界。

![dither近满幅扫描保留已测失败点并区分内部越界与最终裁剪](docs/readme/fig/current_dither_headroom.png)

*图3｜范围诊断：问题已定位，越界尚未处理。理想器件结构配置，40 MS/s、N=2048、fin=251Fs/2048；保留全部24个幅度／模式组合及失败点。节点计数可重叠，不能相加。[数据与节点定义](docs/readme/data/dither_headroom.json) · [重新扫描](docs/REPRODUCING.md#dither的近满幅余量)。*

结构扰动序列由固定D=2决定，浮点amplitude字段不控制它；有效配置报告已明确提示。后续需明确合法工作幅度及后级余量，再验证处理方案；本轮没有改变原量程、丢弃失败点或放宽判据。

### DEM：选择与统计已有结果，增量精度收益仍需单独验证

| 统计对象 | 已观察到的结果 | 分母与结论 |
|---|---|---|
| 固定码k=10、固定逻辑位置，遍历512状态 | 主单元56正轨选中16次，其余62个各8次；名义零和码交换（bridge）开启后，部分子单元出现160／96次，其余为128次 | 分母为512状态；名义加权码守恒，但正轨选择不均衡 |
| 16384笔实际物理选择，seed42 | 通用随机和按RTL规则的Python选择中，主单元56约2倍的正轨选择偏置仍可见 | 每个物理切片以自身被选中的次数作分母；不能统一除16384或套用固定逻辑位置计数 |

这里统计mask=1的正轨选择，mask=0仍有负轨电荷项，不能称为电容未参与。[分布图、原始计数与分母](docs/randomization_caaz/README.md)保留全部结果。DEM自身的增量精度收益还需同一物理池、输入和量化方式下的性能证据。

## 系统怎样工作

![行为模型、数字重构与附加原型怎样协作](docs/readme/fig/current_architecture.png)

*图4｜三项交付物的关系与作用位置：蓝框为模拟行为／接口，绿框为数字RTL，灰色虚线框为输出原型。完成程度以上方成果表为准。本图为原创工程结构，非原芯片完整电路复原。*

18个切片是采样／电容资源池，同周期8个转换、8个采集、2个待选，共享残差放大器与后级量化器。一级量化器独立采样，并保持本笔时刻和样本归属一致。切片选择决定物理组；DEM改变同一码所用的电容；dither在指定节点注入已知扰动并配套改码／重构；训练位权在数字端应用。

相邻转换须消费先前采好的物理组。结构RTL同一位控制单元DEM与切片随机选择，不能当成独立开关。有效位权表示单元电容／反馈电容的有效比值（C/Cf），不唯一确定绝对电容值，具体步骤见[实现图](docs/randomization_caaz/README.md)。

| 配置入口 | 一级量化／DAC与调度 | 用途与模型假设 |
|---|---|---|
| `Config()` | 历史7位／等权单元阵列（unary），各执行入口有限定 | 预算与早期机制；不与九位RTL当成同一电路 |
| `Config.paper_literal()` | 9位／63+8分段阵列（split）候选，普通行为调度 | 物理池与机制敏感性；开启量化器扰动时采用理想双端口假设 |
| `Config.rtl_structural()` | 9位／63+8分段阵列、12位后级、16相位 | 对照结构RTL的样本、掩码和参考事件；采用离散扰动转移规则 |

三配置的dither默认均关闭。16相位若各占一拍，对应640 MHz实现预算，功能仿真未证明可达该时钟。63+8分配、bridge门控和部分模拟参数是工程选择，见[配置适用域](docs/model_scope.md)。

## 尚未完成的工作

**已发现、尚未处理的问题或范围限制：**

| 问题 | 下一步做什么 | 需要产出什么证据 |
|---|---|---|
| 近满幅dither耗尽内部余量 | 明确工作幅度及注入／码转移／后级量程方案 | 同一网格的逐节点范围、饱和与最终裁剪记录，保留失败点 |
| DEM使用偏置（统计限制） | 先定义均衡目标，再比较映射及性能代价 | 同一物理池的使用统计、SFDR／SNDR和总误差，不能只比次数 |
| DSP原型的校正、滤波与输出缺口 | 修两点方向、有效系数、FIFO—串行路径及元数据 | 连续输出的码、样本ID、异常与吞吐一致性记录 |

**尚缺的验证与工程接口：** 首先冻结真实采样保持、复位、非交叠和关闭沿，验证参考／放大器／后级在截止前建立；再确定独立参考与观测读出方案，用实际可读数据完成训练、固定系数和独立验证，缺样或系数切换不能混拼。现有748位观测口没有FIFO／ready／外部输送，40 MS/s下名义数据量29.92 Gbit/s，需要带宽预算或受控采集。目标工艺实现、时序、跨时钟域和晶体管／版图后仿须独立验证。

**可选扩展：** 逐切片时偏／带宽学习、原芯片完整bridge／双端口电路、CAAZ模拟宏、CICC连续时间后级分别立项。CAAZ需两周期保持同一自归零状态，并验证H/K匹配；现有输出SINC、预跟踪或数字配对不等同CICC后级跟踪平均。各项先定义观察、信号／噪声预算与完成标志，见[依赖与解除条件](docs/STATUS.md#按依赖排序的未闭合项)及[组合分析](docs/randomization_caaz/README.md)。

## 怎样复现、从哪里读代码

先按[环境说明](CONTRIBUTING.md)准备依赖。重新实验使用**尚不存在的新输出目录**；完整设置、输出文件和判据见[复现说明](docs/REPRODUCING.md)。

**A．快速功能检查。** 需Python数值依赖和pytest；检查参考事件、样本归属及非法配置，不需Verilator，也不评价精度。

```bash
PYTHONPATH=src pytest -q tests/unit/test_structural_contract.py
```

**B．重新训练与验证。** 另需Verilator 5.x和C++编译器；结构配置与固定静态失配模型，输出原始观测、固定系数和summary.json。成功需样本对齐、整数对拍、零异常及指定静态改善判据成立。

```bash
OPENBLAS_NUM_THREADS=1 PYTHONPATH=src python tools/run_structural_calibration.py \
  --output "${TMPDIR:-/tmp}/sar-calibration-new" --train 4096 --holdout 512 --chip-seeds 42 91
```

**C．只重绘已有数据。** 需Matplotlib和可用中文字体；读取随库JSON／NPZ生成上述PNG／SVG及输入哈希记录，不重新仿真、训练或改善数值。

```bash
MPLBACKEND=Agg PYTHONPATH=src python docs/readme/make_overview_figures.py \
  --out "${TMPDIR:-/tmp}/sar-overview-figures"
```

| 想理解什么 | 代码与详细入口 |
|---|---|
| 模型怎样转换、引入误差 | [信号链](src/adi_model/pipeline_engine.py) · [配置](src/adi_model/config.py) · [适用范围](docs/model_scope.md) |
| DEM、dither与物理选择 | [DEM](src/adi_model/dem.py) · [物理池](src/adi_model/slice_pool.py) · [采样注入](src/adi_model/sampling_charge.py) · [机制与统计图](docs/randomization_caaz/README.md) |
| 系数怎样训练、固定与应用 | [位权拟合](src/adi_model/weight_calibration.py) · [定点重构](src/adi_model/fixed_point.py) · [观察与装载规则](docs/adr/0021-structural-contract-calibration.md) |
| RTL怎样保持归属、处理异常 | [数字核](rtl/top/sar20_digital_core.sv) · [计算规则](docs/rtl/RTL_ARITHMETIC_CONTRACT.md) · [DSP原型接口](ad4080_dsp/docs/SAR20_INTERFACE.md) |
| 怎样复跑、还有哪些限制 | [复现入口](docs/REPRODUCING.md) · [详细状态](docs/STATUS.md) · [本轮裁定与维护](docs/reviews/20261105-readability-maintenance.md) |
| 原文与历史证据 | [三组论文／讲稿](references/README.md) · [来源清单](sources_manifest.json) · [历史文档](docs/history/) |

代码采用[BSD-3-Clause](LICENSE)；选定原件保留原版权与内嵌声明，见[NOTICE](NOTICE)。本项目独立于原器件作者／厂商，不授予专利许可。图中结论所用代码版本、配置、数据和生成脚本由相邻JSON与复现记录追溯。
