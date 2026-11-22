# 20 位两级 SAR ADC 数字 IP

这套数字逻辑把两次量化合成一个 20 位结果。前级 SAR 先给出粗判决，数字 IP 据此逐位切换残差 DAC（RDAC）。第二级 ADC（ADC2）测量剩余电压并返回 13 位原码。要算准最终结果，还需要知道**这一笔实际用了哪八片电容，采样和转换时分别打开了哪些开关**。顶层将这些记录、校准权重和原码按样本标签配对，再输出最终码。

**单芯片数字顶层是 `ad4080_chip_top`。** 它把转换核产出的 20 位码接到系统零点/增益校正、滤波、触发记录及 SPI/LVDS 读出。由外围服务共享 16,384 × 20 位结果存储。转换核 `adc_digital_ip_v21` 是这个顶层的子模块，默认 Python 模型对应其中的扰动、DEM 和定点重构。[单芯片 RTL](rtl/top/ad4080_chip_top.sv) · [转换核 RTL](rtl/sar_core/adc_digital_ip_v21.sv) · [接口合同](docs/CONTRACT_CURRENT.md)

| 命令 | 结果与前提 |
| --- | --- |
| `make lint` | 四套层级的严格 Verilator Lint，及转换核、外围、总顶层的 sv2v/Yosys 结构检查。 |
| `make sim-core` | 在配置了 VCS 的运行主机执行转换核全回归。 |
| `make sim-periph` | 外围仿真、结构和手册对拍。ADI 开源驱动及接收器通过 `ADI_NOOS_ARCHIVE`、`ADI_HDL_SOURCE_DIR` 指定。 |
| `make sim-top` | 单芯片两笔理想宏转换的码、标签、真实 DSP 接收及故障隔离。 |
| `make cal` | ADCToolbox 主机估计、26 档滤波频响及真实 SPI 写入后读回。 |
| `make audit` | 目录、相对链接、算法图册、手册追溯和交付 SHA-256。 |

从仓库根运行。可用 `RUN_ROOT` 指定一个工作树外的运行目录。复现结果用 `git rev-parse HEAD` 标识。Python 包为 [9.0.1+adi.repro](pyproject.toml)。工具版本和完整验收命令见[重构报告](docs/audit/RTL_REFACTOR.md)。

[**打开数字 IP 图册（四页主图＋算法与状态机附录）**](docs/algorithms/digital-ip-atlas.pdf) · [在线读算法、状态机与可编辑原图](docs/algorithms/README.md) · [逐图对照论文与授权专利](docs/algorithms/README.md#直接核对原图与原文)

[**AD4080 产品外围数字端验收**](rtl/ad4080_periph/integration/product/ACCEPTANCE.md)涵盖配置 SPI、滤波、存储及由已知输入驱动的 40 MS/s 外围连续 LVDS 测试。[逐模块手册对照 PDF](rtl/ad4080_periph/report/manual-trace.pdf)

![实测 tag 54，沿字 53 的两条采样通路追到 410304 码](docs/algorithms/figures/digital-transaction-flow.svg)

<a id="先选你要看的东西"></a>
## 从一笔采样看功能

<a id="跟一笔样本走完数字主线"></a>

| 步骤 | 数字 IP 做什么 | 对应实现与图 |
| --- | --- | --- |
| 选片与交接 | 18 个物理片中，8 片采集、8 片转换、2 片备用。受理新请求时保存片号、样本标签和量化器编号。前一采集组整组转入转换。 | [片选 RTL](rtl/sar_core/sched18.sv) · [片号逐拍图](docs/algorithms/digital.md#schedule) |
| 生成并注入扰动 | 生产时将 `dither_force_i` 接 0。每次受理，片内产生一个六位字。所选的 SADC 接收低段七位开关，另一套保持。八片 RDAC 接收完整采样预置 `pre`。 | [两端开关译码](rtl/sar_core/dither_pre.sv) · [扰动通路与交替写入状态图](docs/algorithms/digital.md#pre) |
| 跟随判决并轮换单元 | 数字逻辑只提交前级已确定的位。动态单元匹配（DEM）让不同电容承担同一个名义码。最高区拒绝 512 试探时，本拍关闭写门，从 bit5 继续；末次实际写入的开关记作 `post`。 | [SAR 状态图](docs/algorithms/digital.md#events) · [DEM 开关矩阵](docs/algorithms/digital.md#dem) |
| 捕获与重构 | 用八片的物理编号查片外标定、片内装载的权重。ADC2 返回原码后，使用同笔 `post` 的权重加回转换贡献，扣除同笔 `pre` 的权重，输出 20 位码、标签和状态。 | [ADC2 归属](rtl/sar_core/adc2_capture.sv) · [权重计算](rtl/sar_core/wsum_v21.sv) · [定点重构](rtl/sar_core/recon_v21.sv) |

## 效果与验证条件

| 可以核对的效果 | 实测或模型条件 |
| --- | --- |
| **扰动能否扣回** | 本地 Verilator 驱动生产顶层，理想宏读取真实 SADC、RDAC 开关。固定输入连续 1,024 笔，片内字源覆盖全部 64 个字，每笔输出 410304。单独在重构端漏扣字 53 的采样权重，输出多 13,568 码；重复扣则少 13,568 码。[逐笔数据与复现命令](docs/audit/TREE_AUDIT_REPORT.md#dither-word-audit) |
| **错配能校正多少** | 给一个物理子单元施加已知偏差，同批 192 笔原码使用名义权重时，10 笔低 32 码、14 笔高 32 码。装入已知系数后，192 笔全部回到参考值。全码 DNL/INL 另由静态电荷模型评估，图注列出失配、训练和测试条件。[192 笔权重配对图](docs/algorithms/figures/integer-arithmetic.svg) · [全码 DNL/INL 对照](docs/audit/TREE_AUDIT_REPORT.md#全码静态线性度) |
| **能否连续 40 MS/s** | 在 640 MHz 理想数字时钟、比较判决和宏握手按约到达时，顶层连续交付 192 笔，输出间隔 25 ns。数字时钟本身为 40 MHz 时，第一笔余差在预定采样沿尚未获得捕获授权。实际模拟宏时限和同版整核物理时序待验收。[时钟条件与对照结果](docs/STATUS.md#持续采样率) |

直接看图：[64 个字的去扰与断线对照](docs/algorithms/figures/dither-transfer.svg) · [第 32 沿的三个 tag](docs/algorithms/figures/capture-handover.svg) · [512 试探的写门](docs/algorithms/figures/sar-events.svg) · [DEM 开关覆盖](docs/algorithms/figures/physical-masks.svg) · [同条件全码 INL/DNL](docs/figures/static_linearity/linearity-0.0001.svg)。

## 文件放在哪里

根目录有 10 个目录和 5 个文件（含隐藏项）。文件是本页、`Makefile`、`pyproject.toml`、`.gitignore`、`.gitattributes`。

| 根目录 | 内容和入口 |
| --- | --- |
| [rtl/](rtl/README.md) | 单芯片装配在 `top/`，转换在 `sar_core/`，校正与滤波在 `ad4080_dsp/`，控制、存储和读出在 `ad4080_periph/`。 [编译清单](rtl/filelists/README.md) |
| [tb/](tb/) | SystemVerilog 测试台、C++ 顶层驱动与冻结向量。集成 ADC 的事件、交接、连续采样和重构检查在 [tb/v21/](tb/v21/)。 |
| [src/](src/adi_model/) | Python 包 `adi_model`。公共入口在 [defaults.py](src/adi_model/defaults.py)，逐事件比较与重构在 [digital_conversion.py](src/adi_model/digital_conversion.py)。`control/` 实现控制算法，`topology/` 和 `behavioral/` 描述电荷与非理想，`simulation/` 推进样本，`calibration/`、`metrics/` 求系数和误差。 |
| [flow/](flow/) | [sim/](flow/sim/README.md) 说明仿真和夹具，`tests/` 保存 Python 回归，`formal/`、`synth/` 保存形式验证和后端流程。 |
| [tools/](tools/) | 可执行入口。定向 RTL 测试用 `run_open_rtl.py`，正式 VCS 回归用 `verify_v21.sh`，结构与图册分别由 `check_engineering.py` 和 `v21_algorithm_atlas.mjs` 核对。 |
| [docs/](docs/INDEX.md) | [接口合同](docs/CONTRACT_CURRENT.md)、[项目状态](docs/STATUS.md)、[算法与状态机](docs/algorithms/README.md)、[审计证据](docs/audit/README.md)。行为图和全码线性度图在 `figures/`，旧工程记录在 `_history/`。 |
| [references/](references/README.md) | 论文、讲稿、来源清单。查论文原图和相应页码从这里进入。 |
| [experiments/](experiments/) | Fig.11 电荷研究和 CAAZ 独立扩展。 |
| [archive/](archive/) | 旧数字核的历史对照源码。 |
| [.github/](.github/) | CI 工作流、协作约定及维护模板。 |

## 跑起来

从仓库根运行。下面三条只读检查分别清点工作树、核对工程链接及接口、核对算法图与图源。

~~~bash
python3 -B tools/check_doc_counts.py --check
python3 -B tools/check_engineering.py
node tools/v21_algorithm_atlas.mjs --check
~~~

**用 Python 查看同一笔数字转换。** 以下命令把输入固定在 −0.6522216796875 V，并用测试引脚强制字 53。结果文件应显示粗码 202、RDAC 命令 206、ADC2 原码 6016、最终码 410304。去掉 `--dither-word`，即由片内复位序列生字。普通正弦演示直接运行 `python -m adi_model simulate`。[Python 快速上手](docs/guide/QUICKSTART.md) · [功能、原文与实现差异](docs/audit/AUDIT_ALGO_VS_SOURCE.md)

~~~bash
run_dir="$(mktemp -d)"
bash tools/pyenv.sh --run -m adi_model simulate --dc -0.6522216796875 --samples 1 --dither-word 53 --output "$run_dir/sim"
bash tools/pyenv.sh --run - "$run_dir/sim/codes.npz" <<'PYTHON'
import numpy as np, sys
with np.load(sys.argv[1]) as data:
    print(*(int(data[name][0]) for name in ("coarse", "k", "adc2_code", "code")))
PYTHON
~~~

**复核数字顶层。** 本机有 Verilator 和 C++ 工具链时，以下命令记录连续采样、扰动和定点重构。顶层的 `summary.csv` 对照各场景，`samples.csv` 保存逐笔引脚与出码。正式 VCS 回归、综合与等价各有独立入口。[仿真与 RTL 测试](flow/sim/README.md) · [综合与等价](flow/synth/README.md)

~~~bash
rtl_dir="$(mktemp -d)"
bash tools/pyenv.sh --run tools/run_open_rtl.py --simulator verilator \
  --tops v21_sample_cadence tb_i4_recon tb_recon_stream --out "$rtl_dir/results"
~~~

[文件归属与维护](docs/meta/REPOSITORY_LAYOUT.md) · [变更记录](docs/meta/CHANGELOG.md) · [English overview](docs/meta/README_EN.md)
