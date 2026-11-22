# Python 快速上手

`adi-simulate`、`python -m adi_model simulate`、`adi_model.simulate` 与 `default_model().simulate` 都调用生产数字 IP 的事务参考。默认输入是 40 MS/s 采样的 1 MHz 正弦，实际采样时刻为 n/fs。输入时钟和整核逐拍时序分别验收。[默认实现](../../src/adi_model/defaults.py) · [数字合同](../CONTRACT_CURRENT.md)

## 检查一笔和一串样本

~~~bash
work="$(mktemp -d)"
bash tools/pyenv.sh --run -m adi_model simulate --dc -0.6522216796875 --dither-word 53 --samples 1 --output "$work/one"
bash tools/pyenv.sh --run -m adi_model simulate --dc -0.6522216796875 --samples 1024 --output "$work/stream"
~~~

`one/codes.npz` 的 word、coarse、k、adc2_code、code 依次为 **53、202、206、6016、410304**。stream 的前四个字为 **45、7、17、40**，所有字的最终输出相同。每个样本同时保存 8 个物理片号、8 个采样和转换开关字、最多 8 个比较事件和各自的有效写命令。`event_count` 给出真实比较次数；该范围内的 −1 表示本次不写，范围外的 −1 是填充。

`out_valid` 表示该次受理在事务模型中有有效退休，非法 RDAC 命令保留原行、关闭出码。`adc2_over` 单列宏越窗标志，按宏合同返回 raw=0。`out_err` 记录重构的数字错误。`clipped_low/high` 分别记录最终码两端的饱和。`metadata.json` 登记运行身份、位宽、表摘要与各项计数，`assumptions.json` 列出实际宏与上游相位假设。

`--seed` 控制制造与噪声激励。片池默认复位种子为 0xACE10001，扰动字源固定为 0xC001D00D。若需要重播种片池，使用配置字段 `scheduler_seed`。研究芯片失配时，`device_weights` 为 18×20 的实际 Ceff/Cf，`weights_q30` 为独立加载的 18×20 个整数；默认使用名义器件和名义表。程序拒绝不匹配的几何、非法字及不能在配置端口表示的权重。

若要在出码前验证文件，`validate-config` 接受与 `simulate` 相同的 `--parameters` 和 `--config-overrides`，还可以同时验证两者。回执 `scope=adc_digital_ip_v21` 对应生产模型。低层组件沿用 `validate-config --config FILE.json`，回执为 `component_config`。

```bash
param_dir="$(mktemp -d)"
printf '%s\n' '{"adc2_n_bits":13,"n_main":16,"n_sub":4}' > "$param_dir/production.json"
bash tools/pyenv.sh --run -m adi_model validate-config --parameters "$param_dir/production.json"
```

## 与真实 RTL 对拍

~~~bash
bash tools/pyenv.sh --run tools/run_open_rtl.py --simulator verilator --tops v21_sample_cadence --out "$work/rtl"
bash tools/pyenv.sh --run tools/check_default_rtl.py "$work/rtl/sar_core_sample_cadence/samples.csv" --default-npz "$work/stream/codes.npz"
~~~

预期输出为 `DEFAULT_PYTHON_RTL_MATCH transactions=1792`。包括片内字、强制遍历、跨全码域混合激励，以及同一失配器件在两张加载表上的结果。复位、反压、采样与捕获沿的归属由 RTL 定向测试覆盖。[仿真入口](../../flow/sim/README.md) · [原文与算法逐项对照](../audit/AUDIT_ALGO_VS_SOURCE.md)

## 重绘行为模型图

数字 IP 顶层与状态机见[算法图册](../algorithms/README.md)。本节重绘带模拟宏假设的行为图；日常运行写入库外新目录，正式发布时才替换相应图集。

~~~bash
fig_dir="$(mktemp -d /tmp/sar-adc-figures.XXXXXX)"
bash tools/pyenv.sh --run tools/run_review_figures.py --out "$fig_dir"
~~~

输出包含机理图、定量图、SVG 和 `figures.json`。只画机理图时追加 `--mechanisms-only`。定量重画包括校准训练。静态残差和频谱图分别核对各自的采样、输入和异常计数。[已归档的图及逐图读法](../figures/behavioral/README.md)

消融矩阵另由 `tools/run_p4_ablation.py` 写入指定的结果目录，配置来源为 [实验矩阵](../../experiments/configs/matrix.py)。图册和矩阵可能采用不同工况或指标，配对时应查两份清单的记录。

## 对接与排查

[模型架构](ARCHITECTURE.md)说明主要数据流，[调试](DEBUGGING.md)说明配置、码域和握手排查。需要运行集成 ADC 的数字 RTL 时，从 [v21 顶层和编译清单](../../rtl/README.md)进入。
