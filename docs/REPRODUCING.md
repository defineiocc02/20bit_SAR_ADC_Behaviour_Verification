# 复现入口：默认运行、显式绑定与历史重绘

命令在仓库根目录执行；依赖准备见[CONTRIBUTING](../CONTRIBUTING.md)。模型、RTL运行和导出使用尚不存在的库外目录，重绘也请选择新目录。环境变量指向已有工具或明确输入；证据以实际执行、源／输入摘要与结果为准。

## 1. 实际运行默认模型

```bash
python -m pip install -e .
python -m adi_model simulate --samples 256 --output ../sar-output/nominal-demo-001
# 安装后的等价入口
adi-simulate --samples 256 --output ../sar-output/nominal-demo-002
```

命令实际调用集中工程默认模型。未指定激励时为2.7 V差分正弦峰值、1 MHz、4096 raw、seed20261105；频率不被暗中改为相干值。默认误差和噪声参数见[README](../README.md#系统怎样工作与默认值)。输出角色为`UNCALIBRATED_NOMINAL_20BIT`，没有在此命令中训练系数。

| 输出 | 内容与验收边界 |
| :--- | :--- |
| stdout摘要JSON | `model_run=true`表示完成模型运行；包含inferences、enabled、unmodeled、异常与clip计数，不宣称原芯片复现 |
| `codes.npz` | N长码、sample ID、模拟异常／各节点标志、clip及输入数组；不静默删坏点 |
| `assumptions.json` | 当前默认推断、覆盖来源及职责边界 |
| 最后写入的`metadata.json` | 输出完成记录、激励、来源摘要；缺此文件不能把部分目录当完整结果 |

可用完整选项名：`--amplitude --frequency --samples --seed --parameters FILE.json --config-overrides FILE.json --output NEW_DIR`。两个JSON分别覆盖`DefaultParameters`和低层`Config`；未知字段、重复键或缺失活跃输入拒绝，不能借覆盖恢复旧配置。CLI暂不提供dither字开关。

```python
from adi_model import simulate, sine_input

run = simulate(sine_input(2.7, 1e6), 256)
print(run.codes.code.shape)
print(run.metadata["digital_coefficients"])
```

## 2. 配置检查与软件合同

```bash
adi-validate-config --config FILE.json
PYTHONPATH=src pytest -q tests/unit/test_source_contract.py tests/unit/test_cli.py
```

配置检查的成功记录仍为`model_run=false`，只检查输入，不运行模型。软件测试须实际收集并通过；同名旧测试的历史数量不继承到新内容。不同仿真器、配置、CI与物理层级各记自己的执行证据。

## 3. 静态校准：重新训练、固定与留出

当前接口按独立已知输入与数字观察拟合有效C/Cf，先检查秩与条件数，再固定schema2系数、量化装载，在独立留出上比较名义／拟合系数消费的同一批raw。默认`simulate`产生的是名义输出；低层学习器是[fit_unit_weights](../src/adi_model/weight_calibration.py)，[FixedPointReconstructor](../src/adi_model/fixed_point.py)负责整数应用。

公共[run_static_calibration.py](../tools/run_static_calibration.py)实际运行全跨度正例`fullspan_positive`及拒绝反例`midrange_rank_negative`；[新数据](readme/data/source_bound_static_calibration.json)保留原summary、producer、36个实际加载源码及产物SHA。下面重新运行模型、拟合并固定系数，不是读取旧指标重绘：

```bash
OPENBLAS_NUM_THREADS=1 PYTHONPATH=src python tools/run_static_calibration.py \
  --out ../sar-output/static-calibration-001 --seed 42 --train 4096 --holdout 512 \
  --training-reference-seed 20261105 --training-run-id 0 --holdout-run-id 1 \
  --training-peak-v 3.0 --holdout-peak-v 2.65 --holdout-dc-v 0.05 --holdout-bin 37
```

将输出改为另一新目录、`--training-peak-v`改为2.8可复现中幅不可辨识反例：返回码1，写failure.json，rank1224/1279，拒绝产生冻结系数和留出精度结论。两种训练都用4096笔，不能通过隐去拒绝样本取得满秩。

| 产物 | 必须能核对的内容 |
| :--- | :--- |
| 训练与独立参考 | 精确激励、N、单位、采样身份和实际数字观察；不读取物理真值 |
| 可辨识性 | 设计维数、实际秩、条件数；保留激励不足的拒绝反例 |
| 固定与装载 | schema2完整规格、观察域、系数／寄存器SHA及一次量化规则 |
| 留出 | 同raw名义／固定系数、最终20位码、独立整数oracle、全部异常与clip |
| 工具与源身份 | 实际producer及loaded源码运行前后摘要、完成summary与产物SHA；目录拒覆盖 |

全跨度正例在无dither／单元DEM、关闭采样／RA随机噪声条件下，独立留出RMS85.829→2.07577 µV，512笔最终码与独立整数oracle一致；留出只覆盖[−2.6,+2.7] V。它只执行N个真实Python样本，case文件的trace_scope为`PYTHON_DIGITAL_RECORD_ONLY_NOT_SV_CAPTURE`。三列stimulus／权重／标量的格式与DUT驱动对齐，不表示已执行SV；不能添加未执行的RTL postamble来扩大其观测范围。

## 4. Fig.19可选静态端口

默认dither关闭。Python接口可向`simulate(..., dither_words=words)`显式提供N个0..63整数六位字；没有隐藏PRNG。位序、Q32低位权、负极性、采集端口和改码由[Fig.19实现](../src/adi_model/dither.py)及[原文对照](current_digital_source_map.md)限定。

该路径只作受限工程静态等效，指定Python与RTL工作点见[实际事件](readme/data/source_bound_fig19_evidence.json)。以下API例显式关闭随机噪声，并选择均匀工程ADC模型：

```python
import numpy as np
from adi_model import DefaultParameters, simulate, sine_input

n = 1024
run = simulate(
    sine_input(2.0, 37 * 40e6 / n), n, seed=42,
    parameters=DefaultParameters(unit_mismatch_std=0.0, bridge_mismatch_std=0.0),
    config_overrides={"enable_sampling_noise": False, "ra_enable_noise": False},
    dither_words=np.arange(n, dtype=np.int64) % 64,
)
```

非法RDAC候选事件保留trace并拒绝，不夹码或删除事件；最终合法码不能证明早prefix合法。已测±2.0 V／0等离散点和指定正弦不构成连续全域，7事件整数码域检查不证明模拟／参考建立。负极性、Q32和18/8 common-voltage映射为工程假设；完整电容、开关噪声、动态及3D DEM组合仍需独立实现／验证。

## 5. 显式导出与RTL组件执行

完整核没有隐式参数头或默认source mapper。高级入口需要明确Config、定点格式、binding JSON及实际`source_control_binding`／`source_acquisition_binding`源码；数学夹具只证明标明的数学／协议范围。[现行ABI2合同](rtl/CURRENT_INTERFACE_AND_ARITHMETIC.md)列出8文件包、两采集端口及calobs v2字段、单位；合同与最终执行证据分开。

静态DUT学习的公共驱动会使用仓库中明确标为工程等效的Fig.19夹具，实际运行4096训练、固定系数及512留出；不调用旧模式或隐式原芯片mapper。先生成真实DUT记录，再由生产Python按同物理池与有序计划独立重放：

```bash
SAR_RUN_ROOT="$(mktemp -d)"
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 PYTHONPATH=src \
  python tools/run_fig19_rtl_calibration.py --out "$SAR_RUN_ROOT/calibration"

OPENBLAS_NUM_THREADS=1 PYTHONPATH=src python tools/verify_source_bound_replay.py \
  --trace "$SAR_RUN_ROOT/calibration/holdout_fitted" \
  --inputs "$SAR_RUN_ROOT/calibration/inputs" \
  --plant "$SAR_RUN_ROOT/calibration/plant_metadata.json" \
  --plant-parameters "$SAR_RUN_ROOT/calibration/plant_replay_only.json" \
  --stimulus "$SAR_RUN_ROOT/calibration/holdout_fitted/stimulus.txt" \
  --config "$SAR_RUN_ROOT/calibration/inputs/config.json" \
  --prefix-table sim/fixtures/fig19_ideal_ports/rdac_prefix_table.json \
  --registers "$SAR_RUN_ROOT/calibration/holdout_fitted/registers.json" \
  --out "$SAR_RUN_ROOT/replay-fitted"
```

只看较小的64字组件记录可用`--component-only --out NEW_DIR`；该入口不训练、不做SVD，manifest明确写`COMPONENT_ONLY_NO_TRAINING`。学习参数不会暗中改变这个组件场景。完整运行中，前级训练word0，留出64字各8次、峰值2 V的指定正弦；不能把与上节不同激励、不同计划的RMS差异当成dither收益。

实际DUT案例为N+3程序控制、N+2物理采样、前N目标中的expected_good笔退休，3条程序尾不退休；正常留出expected_good=N，负例N=6只4笔。重放读取真实5类CSV、两只source模块与8文件包、物理参数文件和实际系数；它不重新执行SV，也不把尾部模型目标当ADC2已观测。有限窗通过不证明停采、排空、park、无损重启或模拟idle。

[当前DUT＋同池证据](readme/data/source_bound_rtl_calibration.json)保留DUT原manifest和后续真实收据的关联。最终replayer5cfb的四次实际正例分别重放训练4096、同一留出的名义／固定各512、组件64；正常窗没有status／sticky／fault。同版25个trace损坏与25个来源／标量／文件边界损坏全部拒绝。需要核对其他正常case时，在上述九个参数中把`--trace`、`--stimulus`、`--registers`和新`--out`一起换到该case；负例不能作为正常窗口输入重放器。

单独测试控制合同可导出下列2+1数学夹具。自定义网络仍须满足所选bench的输入、尺寸与采集合同：

```bash
SAR_CONTROL_ROOT="$(mktemp -d)"
PYTHONPATH=src python tools/export_rtl_params.py \
  --config sim/fixtures/source_control_component/config.json \
  --format sim/fixtures/source_control_component/format.json \
  --binding sim/fixtures/source_control_component/binding.json \
  --out "$SAR_CONTROL_ROOT/inputs"

python tools/run_open_rtl.py --tops source_control_contract_tb source_control_caaz_tb \
  --inputs-dir "$SAR_CONTROL_ROOT/inputs" --frames 32 \
  --weights sim/fixtures/source_control_component/weights.hex \
  --scalars sim/fixtures/source_control_component/scalars.hex \
  --out "$SAR_CONTROL_ROOT/run"

PYTHONPATH=src python tools/check_source_control_trace.py \
  --inputs-dir "$SAR_CONTROL_ROOT/inputs" \
  --registers sim/fixtures/source_control_component/registers.json \
  --trace-dir "$SAR_CONTROL_ROOT/run/source_control_caaz_tb" --caaz 1 \
  --out "$SAR_CONTROL_ROOT/caaz-check"
```

`--tops`与`--out`均需显式给出；输出目录位于版本库外且未存在。需要数值格式、向量或权重的组件还要求相应输入。成功必须有实际编译／执行、完成marker及所选检查计数；没有“34项默认全过”或“已知失败即通过”的套件。

### CAAZ数字组件

上例`--caaz 1`的现行8包指定组件得到56个OBS、54个RAW与4个配对结果；普通路径为56个OBS、54个OUT。普通检查将`--caaz`改为0、trace路径改为`source_control_contract_tb`并选择新输出目录。三个严格profile的实际收据SHA256为`8fda0a90eeec015f232160e97b27ada777c08e337efa10d57dbf897dccd78d92`，各自`-Wall`无warning／waiver；上述普通runner不自动执行这三个严格profile。

<details>
<summary>重生配对单元的13周期数学夹具</summary>

```bash
python - "$SAR_CONTROL_ROOT/pair-events.json" <<'PY'
import json
import sys
from pathlib import Path

# 每行依次为复位、epoch clear、输入码、样本ID、chop和AZ代。
cases = (
    [(0, 0, 0, 0, 0, 0), (1, 0, 0, 2, 0, 8), (1, 0, 1048575, 3, 1, 8),
     (1, 0, 1048575, 4, 0, 9), (1, 0, 0, 5, 1, 9), (1, 0, 10, 6, 0, 10),
     (1, 0, 10, 7, 1, 10)],
    [(0, 0, 0, 0, 0, 0), (1, 0, 0, 2, 1, 0), (1, 0, 0, 3, 0, 0),
     (1, 1, 0, 4, 1, 0), (1, 0, 100, 20, 0, 4), (1, 0, 90, 21, 1, 4)],
)
records = []
for case_id, rows in enumerate(cases):
    for cycle, (reset, clear, code, sample, chop, az) in enumerate(rows):
        inputs = dict(rst_n=reset, epoch_clear=clear, in_valid=1, in_code=code,
                      in_sample_id=sample, in_chop_neg=chop, in_az_pair_id=az,
                      in_az_valid=1, in_flags=0, in_analog_bad=0)
        records.append(dict(case_id=case_id, cycle=cycle, inputs=inputs))
fixture = dict(
    schema="pair-component-input-v1",
    binding=dict(code_bits=20, sample_id_bits=32, az_id_bits=32, flags_bits=5,
                 code_format="offset_binary_bin_center", output_quantizer="floor"),
    expected=dict(record_count=13, case_count=2, valid_output_count=4), records=records,
)
with Path(sys.argv[1]).open("x", encoding="ascii") as handle:
    handle.write(json.dumps(fixture, indent=2) + "\n")
PY

python tools/export_pair_component_vectors.py --input "$SAR_CONTROL_ROOT/pair-events.json" \
  --out "$SAR_CONTROL_ROOT/pair-inputs"
python tools/run_open_rtl.py --tops caaz_pair_vectors_tb \
  --vectors "$SAR_CONTROL_ROOT/pair-inputs/pair_component_vectors.txt" \
  --expected-cycles 13 --expected-cases 2 --expected-valid-outputs 4 \
  --out "$SAR_CONTROL_ROOT/pair-run"
python tools/check_pair_component_trace.py --input "$SAR_CONTROL_ROOT/pair-events.json" \
  --trace "$SAR_CONTROL_ROOT/pair-run/caaz_pair_vectors_tb/actual.csv" \
  --out "$SAR_CONTROL_ROOT/pair-check"
```

实际配对单元有523个bench检查、325个独立字段核对，涵盖两case和4个结果。此处只验数字归属／配对，未运行真实CBIAS、H/K、模拟AZ噪声或Fig.19＋CAAZ。

</details>

DSP入口同样显式选组件；例如不依赖完整SAR绑定的队列检查：

```bash
python tools/run_open_rtl.py --tops result_fifo_tb --out ../sar-output/fifo-check-001
```

工具由当前环境或`VERILATOR`等变量指定。缺工具、缺绑定、编译失败或marker缺失都可见失败；不自动安装或回退旧二进制。每种工具、参数和源SHA对应自己的证据，不能合并计数宣称协议全覆盖。

## 6. 重绘与历史实验

四张新图只读默认快照、两组真实留出和Fig.19原事件数据，无仿真／训练：

```bash
MPLBACKEND=Agg python docs/readme/make_source_bound_figures.py \
  --calibration-inputs docs/readme/data/source_bound_static_calibration.json \
  --fig19-inputs docs/readme/data/source_bound_fig19_evidence.json \
  --rtl-calibration-inputs docs/readme/data/source_bound_rtl_calibration.json \
  --out ../sar-output/current-figures-001
```

旧首页图的冻结数据仍可重绘；这是历史数据展示，不产生新模型性能：

```bash
MPLBACKEND=Agg python docs/readme/make_overview_figures.py \
  --out ../sar-output/history-overview-001
```

历史重绘输出为historical_architecture／calibration／interleave／dither_headroom的PNG／SVG和historical_figures_manifest.json。保留旧JSON、NPZ与图原字节，不把其中的commit、dirty状态或源SHA改成新版本。[数据说明](readme/data/README.md)固定各记录身份。旧训练、交织、DEM／dither仿真与CAAZ组合命令仅在[9bdf历史复现说明](https://github.com/defineiocc02/20bit_SAR_ADC_Behaviour_Verification/blob/9bdf7708f42733896d7ead89935be41b4258fb6f/docs/REPRODUCING.md)和对应Git af8完整执行快照下解释；不能将退休代码混入当前运行。[可执行档案](history/EXECUTABLE_ARCHIVES.md)说明如何取得旧原路径。

重绘需要Matplotlib、NumPy和脚本列出的中文字体；缺字体明确失败。绘图器前后核对当前Python正／负例的producer与36个loaded源码，以及`full_typechecked_001`、同版四正／两组负控的源／工具身份；[可重绘资产索引](readme/data/source_bound_assets_manifest.json)列出每件数据、图与脚本的字节数和SHA。源变化时应重跑并核对新收据，不能刷新旧摘要。重绘不会覆盖任何历史性能记录。

历史向量已从sim/vectors迁至[历史目录](history/legacy_vectors_af8/README.md)，原35文件／SHA与旧manifest内部路径保持；9份历史配置／系数／寄存器JSON仍留原路径。完整旧实验从Git af8恢复。另可用`python docs/caaz/make_figures.py --out NEW_EXTERNAL_DIR`只重绘三张历史CAAZ图；输出`historical_caaz_*`，不是新CAAZ／RTL结果。
