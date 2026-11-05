# ADR 0021：结构样本合同与真实 RTL 观测校准

> **历史合同（af8功能合同／9bdf文档快照）：**以下接口、码域、地址、位域和数值保留原记录，不作为新source binding／两采集端口／calobs v2的现行规范。当前合同的冻结状态见[CURRENT_INTERFACE_AND_ARITHMETIC.md](../rtl/CURRENT_INTERFACE_AND_ARITHMETIC.md)。


日期：2026-11-04。适用范围：本项目 18/8 slice、9 位粗量化、63+8 分裂 RDAC、12 位 ADC2 的结构研究配置。该工程合同与公开资料的对应关系、未经披露之处见 [bridge 说明](../binary_unary_bridge.md)。它更新 ADR 0018/0019 的当前状态，但保留其历史证据。

## 决策

1. 使用 `Config.rtl_structural()` 对照结构数字核。其 sample ID 从 2 开始并按 32 位回绕，复现结构 RTL 的预热和 slice 分配顺序；普通 Python 配置维持各自的历史行为，不因此宣称与结构 RTL 逐拍一致。
2. 结构 RDAC 负载事件为 3 位 Flash 前缀加 6 个已决位，共 7 次，重复码仍是独立事件。例如码 173 为 `128, 160, 160, 168, 172, 172, 173`。40 MS/s、16 个理想相位下对应相位 3–9；本模型的轨道负载仍按名义电压线性化，不是参考缓冲器电路求解。历史 trial/reject helper 只供旧实验使用。
3. DEM 与 bridge 启用且 `sid[8]=1` 时执行跨 8 个活动 slice 的名义零和 ±1 交换；码 0 和 511 不交换。两条 RTL 路径与 Python 使用这一**工程候选**。讲稿 `P=50%` 与 `sid[8]` 不是已证明的物理一一对应。
4. 采样 dither 在 RDAC 命令上加已知扰动，量化器 dither 在 RDAC 命令上减去扰动；实际单位依选择的 sub/main bank 换算。`rtl_structural()` 固定可比较的离散 `range` 合同；论文中量化器向 RDAC 的双端口范围增强未在此 RTL 闭合。命令越界标记异常。
5. `P_CAL_OBSERVE=1` 才启用结构 DUT 校准观测口；默认为 0。`cal_obs_valid` 在 reconstructor 接受样本的同一沿有效，重置或 `cfg_clear_valid` 优先取消该次观测，清配置后需重新装载与验证系数。各 epoch 独立采集，外部参考必须自行按 ID/时间对齐。

## 观测口与样本归属

`cal_obs_data[747:0]` 从低到高排列，8 个 slot 内元素均按低位编号在前：

| 位 | 字段 | 用途 |
|---|---|---|
| 11:0 | ADC2 原码 | 细量化观测 |
| 75:12 | 有符号 Q32 归一化注入量 | 重构和已知扰动 |
| 76 | analog bad | 该样本捕获的模拟异常 |
| 77 | sampling dither 使能 | 模式检查 |
| 81:78 | 4 位 dither rail | 检查采样扰动开关 |
| 145:82 | 8 × 8 子单元掩码 | 实际子阵列选择 |
| 649:146 | 8 × 63 主单元掩码 | 实际主阵列选择 |
| 689:650 | 8 × 5 slice ID | 实际物理 slice |
| 697:690 | 有符号 dither 码 | 记录注入量 |
| 706:698 | 9 位 DEM 状态 | 独立验证掩码 |
| 715:707 | 9 位 coarse 码 | 比较器判决 |
| 747:716 | 32 位 sample ID | 输入、观测和输出配对 |

新 `dout_analog_bad` 与 `dout_valid` 和 `dout_sample_id` 对齐；独立粘滞 `analog_ovf` 保留供保守停流。SAR—DSP 接入层当前仍使用粘滞位，尚未按此新增逐样本位处理后级元数据。

观测口是 748 位并行调试接口，**没有 ready、FIFO 或外部输送机制**。40 MS/s 连续捕获的名义总线量为 29.92 Gbit/s，实际设计须提供足够吞吐的采集、受控降速或抽取协议，并记录缺样/epoch；仅靠本接口不构成可量产的读出。`observations_from_rtl` 检查位宽、物理掩码、模式及 ID 连续性，默认拒绝丢失/重复/乱序，并在拟合中拒绝异常样本；捕获层不能静默跨配置 epoch。

## 闭环与结果

[可复现驱动](../../tools/run_structural_calibration.py) 以真实 Verilated 结构数字 DUT 驱动独立、固定失配的静态行为 plant。训练器只接收 DUT 观测和独立已知输入，不接收 plant 的物理单元真值。拟合 `1278` 个有效单元权重和 `1` 个偏移，冻结、量化装载到同一 DUT；使用不同激励且同一 plant 的留出数据，比较未校准/已校准输出，并与独立定点重构逐码核对。

| 测试 | 训练 / 留出 | 秩 | 输入等效误差标准差，校准前 → 后 |
|---|---:|---:|---:|
| seed 42，关闭外加噪声 | 4096 / 512 | 1279 | 55.75 → 2.08 µV |
| seed 91，关闭外加噪声 | 4096 / 512 | 1279 | 59.38 → 2.14 µV |
| seed 42，额外假设 48 µV RMS 输入等效噪声 | 8192 / 1024 | 1279 | 71.25 → 54.40 µV |

这些结果来自理想 640 MHz 相位时钟和静态仿真夹具；48 µV 为测试输入而非芯片测量，不能推断带动态建立、漂移、完整噪声和 ADC2 自身非线性时的表现。异常样本及同沿 clear 已做定向验证。两组无噪结果、数据与源文件哈希见 [结果 JSON](../readme/data/structural_calibration_summary.json)；含噪结果见 [噪声 JSON](../readme/data/structural_calibration_noise_summary.json)。

结构物理掩码独立枚举 512 个码 × 512 个 DEM 状态，零差异；三种 dither 模式下 10752 个真实 RTL RDAC 引脚事件和 Python 事件、相位、样本 ID 及整链粗码/最终命令对拍一致。可用以下命令重新执行（输出目录需不存在）：

```bash
python tools/run_open_rtl.py --tops bridge_mask_audit_tb rdac_event_contract_tb structural_adc_tb structural_adc_observer_off_tb
PYTHONPATH=src python tools/verify_rdac_events.py sim/artifacts/open_rtl/rdac_events.csv
OPENBLAS_NUM_THREADS=1 PYTHONPATH=src python tools/run_structural_calibration.py \
  --output "${TMPDIR:-/tmp}/sar-structural-calibration-new" \
  --train 4096 --holdout 512 --chip-seeds 42 91
```

本地仿真、独立掩码参考和软件拟合不替代目标工艺综合/STA、CDC/RDC、形式等价、模拟电路或 PEX；这些验证以及真实外部参考、观测口读出和比较器时序仍需系统实现。
