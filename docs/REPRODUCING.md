# 复现：功能检查、重新仿真和仅重绘

所有命令从仓库根目录执行。环境安装见 [CONTRIBUTING](../CONTRIBUTING.md)；Python 路径用 `PYTHONPATH=src` 指向当前源码。仿真、训练和完整矩阵都要求新的输出目录，避免覆盖冻结证据。只重绘可以选择明确的绘图目录，不会重新训练或修改模型。

## 三类操作的含义

| 类型 | 做什么 | 正常退出能说明什么 |
|---|---|---|
| A：功能／合同检查 | 运行已知输入、边界和异常断言 | 指定合同被检查，不等同动态性能或电路实现 |
| B：重新仿真／训练 | 产生新 raw、观察、拟合、留出与指标 | 流程跑完；还必须检查数值判据、异常和适用条件 |
| C：指标重算／仅重绘 | 从随库数据计算指标、生成 PNG／SVG | 数据与图表一致，不证明当前源码重新产生了同一数据 |

本页各结果的代码／配置／输入摘要在其 JSON 中，历史记录保留受测版本。当前状态见 [STATUS](STATUS.md)，不要从这里的单项命令推断所有工作完成。

## 快速结构合同检查

需要 NumPy、SciPy、Matplotlib 和 pytest，不需要 Verilator。

```bash
PYTHONPATH=src pytest -q tests/unit/test_structural_contract.py
```

该模块检查九位结构的已决参考事件、重复 ID 拒绝、`rtl_structural` 的相位／样本归属及非法几何。部分电荷判据使用单独的基准配置，不能把这组断言解释为某一电路的 SNDR。成功判据是测试实际收集并通过，而非仅能 import 包；完整模拟链和性能使用后续实验。

## 静态权重训练与结构DUT留出

需要 Verilator 5.x、C++ 编译器和 Python 数值依赖。`VERILATOR` 可指定已配置的命令；本机工具位置属于环境配置，不写死在仓库命令中。

```bash
OPENBLAS_NUM_THREADS=1 PYTHONPATH=src python tools/run_structural_calibration.py \
  --output "${TMPDIR:-/tmp}/sar-calibration-new" \
  --train 4096 --holdout 512 --chip-seeds 42 91
```

工具编译结构数字 DUT，由固定静态失配 plant 回答比较器请求。这里Python cfg的mismatch_enable=False只用于名义合同与事件对照，SV plant的失配由其chip seed独立产生，未被关闭；独立已知输入与数字观察用于片外拟合。每颗芯片训练后冻结，名义／拟合系数应用于相同留出 raw，并检查与独立整数重构一致。

输出包括 build.log、每颗芯片的训练／留出观察与参考、冻结系数／寄存器，以及 summary.json。成功要求：观察和参考 ID 对齐、无异常被静默过滤、训练可辨识、RTL 码与独立整数模型相同；无外加噪声夹具要求留出误差标准差降至原值的一半以下。该阈值是静态功能验收，不是 20-bit ENOB 指标。

首页图 2 的[轻量留出数据](readme/data/structural_calibration_holdout.npz)与[摘要](readme/data/structural_calibration_summary.json)保留两颗芯片；图只选既定 seed42 展示，seed91 的结果不删除。误差均值、标准差和 RMS 分别报告。它不是完整全码 INL/DNL，也没有动态建立／漂移／真实参考和 ADC2 自身非线性。

另一个既有含噪条件可独立运行：

```bash
OPENBLAS_NUM_THREADS=1 PYTHONPATH=src python tools/run_structural_calibration.py \
  --output "${TMPDIR:-/tmp}/sar-calibration-noise-new" \
  --train 8192 --holdout 1024 --chip-seeds 42 --noise-rms-uv 48
```

48 µV RMS 是该 plant 的输入等效假设噪声，不是原芯片测量。对应摘要见[含噪留出](readme/data/structural_calibration_noise_summary.json)，不能把它与无噪夹具或其他行为训练的数值混成一条性能曲线。

## 交织调度的当前严格基线

需要 Python 数值依赖；本脚本不执行 SV。

```bash
OPENBLAS_NUM_THREADS=1 PYTHONPATH=src python docs/readme/reproduce_interleave_40m.py \
  --out "${TMPDIR:-/tmp}/sar-interleave-new" --compare-frozen
```

一次调用保留两类不同产物：

1. 早期四个 offset／gain 案例的内部浮点误差 trace；它们用于数值回归，不称最终整数码频谱。
2. 相同固定 10 ps 标称时偏下的三策略最终 20 位码、完整单边功率谱和指标：固定 A/B、generic 因果随机、按 RTL allocator 合同的 Python 策略。第三条不是本轮采集的 SV DUT 频谱。

输出 interleave_40m.json、interleave_40m.npz、interleave_timing_40m.npz 和 interleave_timing_codes_40m.npz。随库[当前 JSON](readme/data/interleave_40m.json)保存配置、实际调度调用、同一时偏数组与输入摘要；[数据分工](readme/data/README.md)说明当前与历史基线。

`--compare-frozen` 对原四个浮点数组仍使用 `rtol=0, atol=1e-14`，新整数码和谱数据使用相应的严格比较。数组相同只证明指定生产合同和数值基线未漂移，不证明随机调度总能改善性能。

首页图 3：fs=40 MHz、N=16384、fin=1639fs/N、2.85 V 峰值、seed42；静态高斯时偏标称 sigma10 ps，同一实现的实际 sigma 在 JSON 中。关闭其他随机噪声／电容失配，使用相干矩形窗和单基波 bin。功率谱为单边 dBFS/bin，以 3 V 峰值正弦作为 0 dBFS；SFDR 用基波／最大非基波谱峰的 dBc，SNDR 用基波／除 DC 与基波外的功率。独立 FFT 与生产正弦拟合指标交叉核对。

参考误差 RMS 保留基波幅相偏差；fit residual RMS 不包含拟合的 DC／基波。拟合只用于指标，输出没有被软件增益／相位校正。所有模拟越界和最终数字 clip 另行统计；不因曲线不好看而删除工作点或样本。

旧 d809da6 的冻结数组与元数据保留在 [historical](readme/data/historical/)，桥接合同迁移记录解释为何当前 gain trace 发生变化。不能回退正确算法来迁就旧图，也不能删除严格比较或放宽容差。

## dither的近满幅余量

需要 Python 数值依赖，不需要 Verilator。

```bash
OPENBLAS_NUM_THREADS=1 PYTHONPATH=src python tools/reproduce_dither_headroom.py \
  --out "${TMPDIR:-/tmp}/sar-dither-headroom-new"
```

工具采用理想器件的 `Config.rtl_structural()`，fs40 MHz、N2048、fin=251fs/N、phase0，保持同一物理池和可共用随机实现。off／sampling／quantizer 三模式各扫描 8 个固定幅度；全部 24 格和每格 2048 个输出保留。有效码域、RA 原有摆幅与 ADC2 原有窗口保持不变。

输出 summary.json、完整配置／来源摘要和 raw_data 下各格 NPZ。成功退出表示扫描完成；合法输入范围是否满足要求，还必须检查每格的 SADC 输入范围、RDAC 请求码、RA 饱和、ADC2 溢出与最终 clip。计数可重叠，不能相加当作独立失败样本数。

SADC 的诊断区间是半开 `[−Vfs,+Vfs)`；恰好达到上端点与明显超过输入范围分别解释。最终 clip 为零不等于模拟通路正常。数据见[近满幅摘要](readme/data/dither_headroom.json)；幅度网格不是完整输入域或频率域保证。

结构量化器 dither 实际是固定 D=2 的 LFSR 单位序列，最大名义 dQ 为 23.4375 mV；`effective_config` 保留请求并报告浮点 amplitude 不驱动此序列。generic quantizer 的幅度字段确实进入执行，再经过离散栅格取整，不应将两者混成一个扫参入口。

## DEM、CAAZ及数字组合的详细入口

源码推导的 DEM／dither 实现图可重绘：

```bash
PYTHONPATH=src python docs/readme/make_randomization_figures.py \
  --out "${TMPDIR:-/tmp}/sar-randomization-figures"
```

这是生产译码和名义合同图，不执行性能仿真或硬件对拍。DEM 固定码使用计数还需分别看固定 slot 与被 allocator 选中的实际物理单元；名义守恒不保证统计等使用。重新统计并画使用分布：

```bash
MPLBACKEND=Agg OPENBLAS_NUM_THREADS=1 PYTHONPATH=src python tools/reproduce_dem_usage.py \
  --out "${TMPDIR:-/tmp}/sar-dem-usage-new"
```

工具输出 dem_usage.json 与 PNG／SVG：固定k=10遍历512个SID，并运行seed42、16384笔的generic／RTL合同Python调度，按每个实际物理单元被选中的机会数归一化。成功要求名义加权码守恒、全部计数及分母一致；结果不要求每个单元等概率，也不计算校准后性能。[随库统计](randomization_caaz/data/dem_usage.json)与[分布图](randomization_caaz/fig/dem_usage.png)保留实际偏置。

当前 bridge 工程合同的硬件掩码检查需要 Verilator：

```bash
python tools/run_open_rtl.py --tops bridge_mask_audit_tb rdac_event_contract_tb
PYTHONPATH=src python tools/verify_rdac_events.py sim/artifacts/open_rtl/rdac_events.csv
```

该公开 runner 的暂存输出在被忽略的 sim/artifacts/open_rtl/；不应提交编译日志。模拟宏、noise gain 和真实开关结构不由这个数字 PASS 证明。

CAAZ 的 42 格静态组合和控制、实际数字诊断及限制见 [组合专题](randomization_caaz/README.md)与 [CAAZ 入口](caaz/README.md)。这些工作分别验证 H/K 假设、量化影响和数字归属；不是给所有模拟连接方式签发组合通过。

## 只从冻结数据重绘首页

需要 Matplotlib 与可用中文字体；脚本不会安装字体或修改工具环境。图中的数值从已保存 JSON／NPZ读取，自检后绘制：

```bash
MPLBACKEND=Agg PYTHONPATH=src python docs/readme/make_overview_figures.py \
  --out "${TMPDIR:-/tmp}/sar-overview-figures"
```

输出四张当前首页 PNG／SVG。图中局部放大明确标注，不对误差曲线平滑，不过滤异常点。重绘成功与重新仿真达标是两件事；若需要更新结果，先重跑 B 类生产脚本、校验数据，再重画。
