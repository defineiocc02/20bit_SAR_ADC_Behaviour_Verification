# Python 快速上手

在仓库根目录运行以下命令。`tools/pyenv.sh` 选择 Python 3.10 或以上的解释器，配置模型导入路径。已有环境可先执行 `bash tools/pyenv.sh --check`。[主入口](../../README.md)

## 跑 64 笔并检查输出

~~~bash
run_dir="$(mktemp -d /tmp/sar-adc-demo.XXXXXX)"
bash tools/pyenv.sh --run -m adi_model simulate \
  --samples 64 --output "$run_dir/sim" > "$run_dir/stdout.json"
ls -1 "$run_dir/sim"
~~~

输出包含 `codes.npz`、`metadata.json`、`assumptions.json`。先查 `metadata.json` 的结果身份、输入域和样本数，再看码、模拟量及标志。`source_conformance=NOT_ASSESSED` 是单次 CLI 运行的证据边界，代表该运行不承担与原文的逐项核对。位宽为 20 位时，实际精度仍要用针对性的统计检验。

## 查看历史工具的一笔信号

~~~bash
trace_dir="$(mktemp -d /tmp/sar-adc-trace.XXXXXX)"
bash tools/pyenv.sh --run tools/trace_one_sample.py --out "$trace_dir"
~~~

输出 `one_sample_trace.json`。其中无扰动部分记录模型的一笔判决、余差、ADC2 和重构。扰动部分在同一输入上派生等效支路示例，缺完整模拟尾段。v21 的模式、合法码域及误差标志另见[工程合同](../CONTRACT_CURRENT.md)。

## 重绘机理图或定量图

~~~bash
fig_dir="$(mktemp -d /tmp/sar-adc-figures.XXXXXX)"
bash tools/pyenv.sh --run tools/run_review_figures.py --out "$fig_dir"
~~~

输出包含机理图、定量图、SVG 和 `figures.json`。只画机理图时追加 `--mechanisms-only`。定量重画包括校准训练。静态残差和频谱图分别核对各自的采样、输入和异常计数。[已归档的图及逐图读法](../figures/20261117/README.md)

消融矩阵另由 `tools/run_p4_ablation.py` 写入指定的结果目录，配置来源为 [实验矩阵](../../subsys/experiments/configs/matrix.py)。图册和矩阵可能采用不同工况或指标，配对时应查两份清单的记录。

## 对接与排查

[模型架构](ARCHITECTURE.md)说明主要数据流，[调试](DEBUGGING.md)说明配置、码域和握手排查。需要运行集成 ADC 的数字 RTL 时，从 [v21 顶层和编译清单](../../rtl/README.md)进入。
