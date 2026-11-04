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

![公开机制与工程代码对应关系](docs/readme/fig/unified_2_sources.png)

*图 2．按机器可读机制清单独立绘制；圆点表示来源登记，不表示对应的晶体管电路已实现。*

| 机制 | 工程中已有 | 尚需注意 |
|---|---|---|
| 粗量化器 / 残差 DAC 分离 | Python 量化、电荷模型和结构 RTL 信号路径 | 电容分配、开关时序等含工程假设，未得到论文完整网表 |
| 电容失配与 DEM | 固定物理单元、权重拟合、单元重排及 RTL 重构 | DEM 改变误差分布，不自动测得或消除电容误差；bridge mask 的 Python/RTL 合同仍待统一 |
| 交织失配 | slice offset、时序偏差、带宽差异及调度敏感性模型 | 尚无逐 slice offset、时间偏差、带宽的完整硬件估计与校正闭环 |
| dither | 行为注入/数字补偿和 RTL 发生器 | 不同模式的范围、注入与补偿合同须分别检查；大输入可能越界 |
| RA、参考与 ADC2 | RA/参考动态行为和理想 ADC2 量化 | 参考负载事件与结构 RTL 已决位事件仍有差异；ADC2 自身非线性学习、模拟电路和 PDK 验证未完成 |

完整出处和代码定位见 [`mechanism_inventory.json`](mechanism_inventory.json)、[`sources_manifest.json`](sources_manifest.json) 和 [工程与文献对照](docs/engineering_vs_patents_papers.md)。主要公开资料包括 [量化器与残差 DAC 分离](https://patents.google.com/patent/US10516408B2/en)、[dither](https://patents.google.com/patent/US10505561B2/en)、[DEM](https://patents.google.com/patent/US10511316B2/en) 与 [交织跟踪](https://patents.google.com/patent/US10707889B1/en)。公开资料与项目候选实现不是一一还原关系。

## 电容失配：如何校准，效果是什么

![拟合权重到 RTL 的路径](docs/readme/fig/unified_3_calibration.png)

*图 3．现有片外拟合和 RTL 重构路径；按工程实现独立绘制。*

数字观测与独立参考输入送入 Python 批量拟合器，得到冻结的有效单元权重；导出器将其量化为 Q30 权重和 Q32 标量，装入 RTL `weight_store`、`calib_regs`，由 `recon_core` 重构输出。固定测试例使用 2048 笔训练、1279 阶满秩矩阵和 128 笔独立留出样本；详细合同与条件见 [ADR 0019](docs/adr/0019-fitted-weight-rtl-closure.md)。

![40 MS/s 电容失配校准前后效果](docs/readme/fig/unified_6_calibration_effect.png)

*图 4．独立审计的行为模型数据重绘：40 MS/s、2.2 V 峰值、N=16384、seed 42、失配 σ=1000 ppm；右侧为误差频谱，标题 SNDR 来自输出码流。训练与留出数据分开，但使用同一个模拟物理池和精确外部训练参考。*

在图中带采样与 RA 噪声的条件下，误差标准差由 **81.61 µV 降到 43.43 µV**，输出 SNDR 由 **85.61 dB 提升到 91.08 dB**。94.2 dB 噪声预算含拟合参数，这组数字只能描述该仿真条件。数据、源版本和脚本见 [`docs/readme/data/`](docs/readme/data/) 与 [`make_unified_figures.py`](docs/readme/make_unified_figures.py)。

现有 RTL 对拍说明拟合寄存器能被装载并产生预期的定点输出；训练观测来自 Python 模型。**真实结构 DUT 原始观测 → 学习 → 装载 → 独立 DUT 留出复验**尚未闭合，不能称为完整片内自校准。

## 交织失配：杂散与总误差

![40 MS/s 交织失配与调度效果](docs/readme/fig/unified_7_interleave_effect.png)

*图 5．本工程行为模型 40 MS/s 对照：N=2048、输入约 2.949 MHz / 2.2 V 峰值、seed 42、随机噪声关闭；两种调度均未拟合失配参数。*

slice 失调会在 `fs/2` 产生交织相关误差；slice 电容/增益失配可在 `fs/2 − fin` 出现旁带。本例中，随机 8-of-18 调度使所标杂散分别降低约 22 dB 和 18 dB，但总误差 RMS 分别从 **8.12 增至 15.74 µV**、从 **7.10 增至 11.15 µV**。因此它是误差频谱重分配，不等同于误差被校准。

时序偏差会随输入斜率影响采样误差；带宽失配还依赖动态采样建立路径是否开启。它们目前可做行为敏感性分析，尚不能说已完成时间偏差或带宽误差的独立学习与硬件补偿。

## DSP 接入与错误处理

![SAR 与 DSP 的码制和错误处理](docs/readme/fig/unified_4_dsp.png)

*图 6．工程接入示意：按当前 RTL 独立绘制。*

[`sar20_ad4080_top.sv`](ad4080_dsp/rtl/sar20_ad4080_top.sv) 接收 SAR 的 `dout`、`dout_valid`、`dout_sample_id` 和 `dout_flags`：入口将 offset-binary 转为 DSP 内部补码，并行出口转回 offset-binary；串行数据仍为补码。任一 SAR 错误标志触发拒收并锁存故障，防止 SAR 保持的旧码混入滤波器。`dsp_restart` 或 `sar_epoch_clear` 复位 DSP，**同时清空其 FIFO、滤波状态和寄存器配置**，重启后需重新配置。

接口侧输出接受/拒收事件与 ID；滤波和 FIFO 的结果**尚不携带逐结果 ID 或输入标志**。两侧要求同一时钟及同步释放复位，没有新增 CDC。DSP 支持的校正、滤波、SPI、FIFO 和串行数字逻辑及其未完成事项见 [DSP README](ad4080_dsp/README.md)；不包含 DDR 差分 PHY，SINC5 补偿系数默认是占位值。

## 来源与验证

![参数分级和证据边界](docs/readme/fig/unified_5_evidence.png)

*图 7．按运行时参数来源账本独立绘制。拟合和假设产生的结果不能升级为披露值或芯片测量值。*

Python 模型、RTL 仿真、历史 FPGA 证据与目标 PDK 综合/STA 分属不同验证层级。图 4 来自基线 `40ae71d` 的独立审计；图 5 来自集成基线 `d809da6` 的 40 MS/s 行为对照。两者均不是晶体管或硅片数据。历史版本的详细说明保留在 [`docs/history/`](docs/history/)，历史测试结果不自动代表当前工作树重新通过。

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
python tools/run_open_rtl.py --tops calibration_fit_tb sar20_dsp_integration_tb
BUILD_DIR="${TMPDIR:-/tmp}/ad4080-dsp-build" bash ad4080_dsp/sim/run_all.sh
MPLBACKEND=Agg PYTHONPATH=src python docs/readme/make_unified_figures.py
PYTHONPATH=src python docs/readme/reproduce_interleave_40m.py \
  --out "${TMPDIR:-/tmp}/sar-adc-interleave-new" --compare-frozen
```

重绘脚本读取随库保存的轻量数据；它重画已记录的结果，不会重新训练模型。交织复现脚本的 `--out` 必须是尚不存在的新目录。完整仿真输出建议写到仓库外，例如 `adi-run-all --results-dir "${TMPDIR:-/tmp}/sar-adc-results"`。目录职责和历史证据例外见 [文件规范](docs/PROJECT_LAYOUT.md)。

## 许可与引用

代码采用 [BSD-3-Clause](LICENSE)，第三方权利和专利声明见 [NOTICE](NOTICE) 与 [CITATION.cff](CITATION.cff)。示意图和结果图由本仓库独立绘制，不复制论文、讲稿或专利原图；原文文件不随仓库分发。本项目与 Analog Devices 无从属或背书关系，不授予专利许可。
