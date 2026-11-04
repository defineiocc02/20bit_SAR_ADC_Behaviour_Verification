# 20 位 / 40 MS/s 流水 SAR ADC：行为模型与数字实现

[English](README_EN.md) · [目录规范](docs/PROJECT_LAYOUT.md) · [模型范围](docs/model_scope.md) · [DSP 接口](ad4080_dsp/docs/SAR20_INTERFACE.md)

本仓库依据 [ISSCC 2024 Session 9.8 论文](https://doi.org/10.1109/ISSCC49657.2024.10454329)和相关专利，研究两级残差 SAR ADC 的切片调度、电容失配、数字校准与后处理。它包含 Python 行为模型、SAR 数字 RTL，以及独立的 AD4080 风格 DSP 子系统。默认采样率为 **40 MS/s**。

> **证据边界**：这是研究原型；20 位是数字输出码宽，不等于 20-bit ENOB。图中的性能数字来自指定条件下的行为仿真，不是芯片实测，也不代表版图、PDK 或时序签核。

## 架构与配置

![两级 SAR 架构与工程配置](docs/readme/fig/unified_1_architecture.png)

*图 1．按公开文字和本仓库源码独立绘制的架构示意，不是论文原图。*

论文披露 20 b / 40 MS/s、18 个切片及共享残差放大等线索。模型把采样、粗量化、残差电荷、RA、ADC2 和数字重构串联；结构 RTL 提供相位控制、比较器接口和数字重构。DSP 通过项目接入层对接重构输出，具体接线见下文。

| 配置 | 采样率 | 一级量化与 DAC | 说明 |
|---|---:|---|---|
| `Config()` | 40 MS/s | 7 位、unary | 原有默认行为配置；Python SADC 采用阈值查找，不模拟逐位 SAR 时序 |
| `Config.paper_literal()` | 40 MS/s | 9 位、63+8 split | 贴近论文数字位数的候选；63+8 物理分配是本项目假设 |
| `sar20_digital_core` 结构 RTL | 目标 40 MS/s | 9 位、63+8 split | 16 个数字相位/样本；若每相位一个时钟，预算为 640 MHz，尚无目标工艺 STA 证明 |

两种 Python 配置不应被当作完全相同的电路，也不能仅凭标称采样率推断 RTL 实际可运行速率。参见 [`config.py`](src/adi_model/config.py)、[`sadc.py`](src/adi_model/sadc.py) 和 [RTL 算术契约](docs/rtl/RTL_ARITHMETIC_CONTRACT.md)。

## 论文、专利与工程实现

以下表格直接列出文献机制、工程实现和未闭合边界。

| 机制 | 工程中已有 | 尚需注意 |
|---|---|---|
| 粗量化器 / 残差 DAC 分离 | Python 量化、电荷模型和结构 RTL 信号路径 | 电容分配、开关时序等含工程假设，未得到论文完整网表 |
| 电容失配与 DEM | 固定物理单元、权重拟合、单元重排及 RTL 重构 | DEM 改变误差分布，不自动测得或消除电容误差；bridge mask 的 Python/RTL 合同仍待统一 |
| 交织失配 | slice offset、时序偏差、带宽差异及调度敏感性模型 | 尚无逐 slice offset、时间偏差、带宽的完整硬件估计与校正闭环 |
| dither | 行为注入/数字补偿和 RTL 发生器 | 不同模式的范围、注入与补偿合同须分别检查；大输入可能越界 |
| RA、参考与 ADC2 | RA/参考动态行为和理想 ADC2 量化 | 参考负载事件与结构 RTL 已决位事件仍有差异；ADC2 自身非线性学习、模拟电路和 PDK 验证未完成 |

完整出处和代码定位见 [`mechanism_inventory.json`](mechanism_inventory.json)、[`sources_manifest.json`](sources_manifest.json)、[工程与文献对照](docs/engineering_vs_patents_papers.md)和 [已知限制](docs/rtl/KNOWN_LIMITS_20261104.md)。主要公开资料包括 [量化器与残差 DAC 分离](https://patents.google.com/patent/US10516408B2/en)、[dither](https://patents.google.com/patent/US10505561B2/en)、[DEM](https://patents.google.com/patent/US10511316B2/en) 与 [交织跟踪](https://patents.google.com/patent/US10707889B1/en)。公开资料与项目候选实现不是一一还原关系。

## 电容失配：如何校准，效果是什么

![拟合权重到 RTL 的路径](docs/readme/fig/unified_3_calibration.png)

*图 2．现有片外拟合和 RTL 重构路径；按工程实现独立绘制。*

数字观测与独立参考输入送入 Python 批量拟合器，得到冻结的有效单元权重；导出器将其量化为 Q30 权重和 Q32 标量，装入 RTL `weight_store`、`calib_regs`，由 `recon_core` 重构输出。固定测试例使用 2048 笔训练、1279 阶满秩矩阵和 128 笔独立留出样本；详细合同与条件见 [ADR 0019](docs/adr/0019-fitted-weight-rtl-closure.md)。

![1.05 MHz 电容失配校准前后频谱](docs/paper_comparisons/figures/02_ac_calibration.png)

*图 3．对应讲稿第 38 页输入频率的行为对照：40 MS/s、1.05 MHz、峰值 2.85 V、N=16000、seed 42。训练与留出数据分开，使用同一模拟物理池和精确外部训练参考。讲稿未公开幅度、FFT 和完整噪声条件。*

含噪时输出 SNDR 为 **88.88 → 92.46 dB**；误差标准差为 **72.53 → 48.04 µV**。模型噪声预算使用了拟合的 94.6 dB，因此不能把本结果当成对论文 SNR 的独立预测。更多静态误差和无噪对照见 [逐页实验对照](docs/paper_comparisons/README.md)。

现有 RTL 对拍说明拟合寄存器能被装载并产生预期的定点输出；训练观测来自 Python 模型。**真实结构 DUT 原始观测 → 学习 → 装载 → 独立 DUT 留出复验**尚未闭合，不能称为完整片内自校准。

## 交织失配：杂散与总误差

![10 ps 时序失配与调度的输出频谱](docs/paper_comparisons/figures/01_timing_skew.png)

*图 4．对应讲稿第 21–22 页：40 MS/s、时序失配标称 10 ps；本图假设该值为独立 slice 静态高斯标准差，输入约 4.0015 MHz / 2.85 V 峰值，N=16384，随机噪声关闭。讲稿未公开精确输入、10 ps 的统计定义或 FFT 设置。*

固定轮换与随机 8-of-18 调度下，`fs/2 − fin` 处杂散从 **−96.44 降到 −123.05 dBFS/bin**，但本例对理想输入的误差 RMS 从 **127.97 增至 182.15 µV**。这是频谱重分配，不是时序偏差学习或补偿。其余种子、另一种 10 ps 定义、1 kHz、线性度、参考负载及实验条件边界见 [逐页实验对照](docs/paper_comparisons/README.md)。

slice 失调、增益与带宽差异也会造成交织误差；既有 [失调/增益对照图](docs/readme/fig/unified_7_interleave_effect.png)说明随机调度对杂散和总 RMS 的不同影响。当前仍无逐 slice 时序、带宽的独立硬件学习与补偿闭环。

## DSP 接入与错误处理

![SAR 与 DSP 的码制和错误处理](docs/readme/fig/unified_4_dsp.png)

*图 5．工程接入示意：按当前 RTL 独立绘制。*

[`sar20_system_top.sv`](ad4080_dsp/rtl/sar20_system_top.sv) 实例化 SAR 数字核与 DSP；也可单独使用 [`sar20_ad4080_top.sv`](ad4080_dsp/rtl/sar20_ad4080_top.sv) 接收 SAR 结果、逐样本标志及独立的 `analog_ovf` 粘滞位。入口将 offset-binary 转为 DSP 内部补码，并行出口转回 offset-binary；串行数据仍为补码。任一结果标志或模拟溢出粘滞位触发阻断并锁存故障。`dsp_restart` 或 `sar_epoch_clear` 复位 DSP，**同时清空其 FIFO、滤波状态和寄存器配置**；模拟粘滞位须由 SAR 的 epoch clear 清除，随后需要重新配置。

接口侧输出接受/拒收事件与 ID；滤波和 FIFO 的结果**尚不携带逐结果 ID 或输入标志**。两侧要求同一时钟及同步释放复位，没有新增 CDC。单 lane 数字串行器在每 16 时钟一个输入样本的条件下不能无损发送 20 位帧，且 FIFO 数据未接入串行发送器；不包含 DDR 差分 PHY。SINC5 补偿系数默认是占位值。其余事项见 [DSP README](ad4080_dsp/README.md)。

DSP 的独立 `cal_two_point` 当前对固定横坐标拟合 `y=m·x+c`。若以固定已知输入为横坐标、测得原始码为纵坐标，得到的是**正向传递系数**，不能直接作为原始码的逆向校正；它也没有接入顶层自动学习流程。

## 数字部分工作分工

按功能归为五块；每块说明它在信号链中的作用和主要任务：

| 工作块 | 干什么 | 主要任务 |
|---|---|---|
| SAR 控制与转换时序 | 组织采样、逐次比较、残差转换和结果输出，让各阶段处理同一个样本 | 完善相位和决策时序，统一 Python 与结构 RTL 的参考负载事件，并核对样本与状态的对应关系 |
| DEM 与交织误差处理 | DEM 通过切片选择和电容单元重排，改变固定失配的误差频谱；交织补偿估计并校正各 slice 的失调、增益、时序或带宽差异 | 核对 8/18 切片调度、单元重排及 bridge 掩码；增加可辨识的观测、估计与补偿，比较杂散和总误差。DEM 不等于失配校准 |
| 电容校准与数字重构 | 利用外部参考拟合电容有效权重，冻结系数后将粗、细转换结果合成为数字输出 | 打通结构 DUT 的观测 → 拟合 → 装载 → 独立留出验证，补充 ADC2 自身非线性和异常处理 |
| DSP 与数据输出 | 对重构后的数据执行滤波、缓存、配置和错误处理，并提供外部输出 | 修正两点校准的逆向补偿，贯通 FIFO 到输出，解决串行吞吐以及输出数据与状态的对应关系 |
| 集成与验证 | 检查各模块组合后数据、控制和误差处理是否符合预期 | 完成端到端样本关联与复位合同，补充形式等价、CDC/RDC、目标 PDK 综合与 STA 等证据 |

目前 SAR 控制、DEM、权重拟合和 DSP 均已有原型；逐 slice 的交织误差学习与补偿尚未形成完整硬件闭环。具体证据和限制见 [已知限制](docs/rtl/KNOWN_LIMITS_20261104.md)与 [逐页实验对照](docs/paper_comparisons/README.md)。

## 来源与验证

![参数分级和证据边界](docs/readme/fig/unified_5_evidence.png)

*图 6．按运行时参数来源账本独立绘制。拟合和假设产生的结果不能升级为披露值或芯片测量值。*

Python 模型、RTL 仿真、历史 FPGA 证据与目标 PDK 综合/STA 分属不同验证层级。图 3–4 来自基线 `6863fc3` 的 40 MS/s 行为仿真；均不是晶体管或硅片数据。历史版本的详细说明保留在 [`docs/history/`](docs/history/)，历史测试结果不自动代表当前工作树重新通过。

### 快速开始

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
python -c 'from adi_model import Config; print(Config().fs, Config().b1, Config().dac_arch)'
pytest -m 'not slow' -q
```

RTL 回归需要 Verilator 5.x 和 C++ 编译器；DSP 独立脚本需要 Icarus Verilog：

```bash
python tools/run_open_rtl.py --tops calibration_fit_tb sar20_dsp_integration_tb sar20_system_top_tb
BUILD_DIR="${TMPDIR:-/tmp}/ad4080-dsp-build" bash ad4080_dsp/sim/run_all.sh
MPLBACKEND=Agg PYTHONPATH=src python docs/readme/make_unified_figures.py
PYTHONPATH=src python docs/readme/reproduce_interleave_40m.py \
  --out "${TMPDIR:-/tmp}/sar-adc-interleave-new" --compare-frozen
```

重绘脚本读取随库保存的轻量数据；它重画已记录的结果，不会重新训练模型。交织复现脚本的 `--out` 必须是尚不存在的新目录。完整仿真输出建议写到仓库外，例如 `adi-run-all --results-dir "${TMPDIR:-/tmp}/sar-adc-results"`。目录职责和历史证据例外见 [文件规范](docs/PROJECT_LAYOUT.md)。

## 许可与引用

代码采用 [BSD-3-Clause](LICENSE)，第三方权利和专利声明见 [NOTICE](NOTICE) 与 [CITATION.cff](CITATION.cff)。示意图和结果图由本仓库独立绘制，不复制论文、讲稿或专利原图；原文文件不随仓库分发。本项目与 Analog Devices 无从属或背书关系，不授予专利许可。
