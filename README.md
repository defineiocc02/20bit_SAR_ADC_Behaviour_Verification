# 20b10mtask｜20 位、10 MS/s SAR ADC 数字交付树

这是**硕士同学的任务交付树**。项目以 ISSCC 2024 的交织前级和相关专利为参考，采用 9 位前级，目标为 20 位输出码、10 MS/s 原始转换；后续结合北大斩波辅助自归零。当前先完成**切片调度与 DEM**，再做真实分路 dither 和 CAAZ。

[任务规格与分工](task1/SPEC.md) · [模块一](task1/README.md) · [统一 LMS 实验标准](task1/EXPERIMENT_STANDARD.md) · [原始论文与专利](references/README.md)

## 20 位 INL／DNL：三组对照

同一颗 **2% 固定电容失配**虚拟芯片、理想 12 位第二级、输入等效 ±100 mV；三组的 dither 均关闭。LMS 两条路径分别训练、冻结，再用独立输入评估。图中每个 20 位码分别计算，纵轴使用 **LSB20＝9.5367 μV**。

![20 位逐码 INL／DNL：无 DEM 无 LMS、只有 LMS、DEM 加 LMS](task1/evidence/inl_dnl/full20/adc2_12bit_sigma_2pct_three_arms_body_zoom.png)

| 处理方式 | 峰值 INL（LSB20） | DNL 范围（LSB20） | 零宽内码 |
| :--- | ---: | :--- | ---: |
| DEM 关、LMS 关 | 901.930 | −1.000～+9.238 | 802,491 |
| DEM 关、仅 LMS | 3.737 | −1.000～+4.118 | 678,189 |
| 跨段 DEM 开＋独立 LMS | 3.479 | −1.000～+4.120 | 336 |

图中的 DEM 纵轴已放大，超出范围的极值、点数和缺码均有标注。未校准基线的输出端高端裁剪对应 2.640 mV 输入宽度；它与 ADC2 超窗分别统计。[3% 对照图](task1/evidence/inl_dnl/full20/adc2_12bit_sigma_3pct_three_arms_body_zoom.png) · [全范围及端点放大](task1/evidence/inl_dnl/full20/README.md) · [原始数据与计算方法](task1/evidence/inl_dnl/README.md)

固定 A/B 使用两组固定状态；动态 DEM 使用 4 组种子各 128 个状态快照作等权统计，**不是每笔都重新抽电容**。这些是理想两节点和理想 ADC2 的统计结果，不是实际芯片的逐笔 DNL。按这组模型，DEM 明显减少统计零宽码，但最小 DNL 仍为 −1 LSB20。[什么是 512 个状态、什么是无 DEM](task1/STATE_AND_BASELINES.md)

## Dither 是什么效果？

下图两边都开启 DEM 和同一套已冻结 LMS，**只比较理想细级减性 dither 关闭／开启**；采用 16 个 DEM 状态和 16 个扰动字。它是理解机制的数学实验，**不是已经实现专利 Fig.19 的物理 dither**，也不能消除 ADC2 过载。

![理想 dither 关闭和开启的 20 位 INL／DNL](task1/evidence/adc2_dither/full20/adc2_12bit_sigma_2pct_dither.png)

[3% dither 对照](task1/evidence/adc2_dither/full20/adc2_12bit_sigma_3pct_dither.png) · [12 位第二级量程分析](task1/ADC2_DITHER.md) · [统计方式和原始数据](task1/evidence/adc2_dither/README.md)

## 现在交付到哪里？

| 项目 | 当前状态 | 入口 |
| :--- | :--- | :--- |
| 切片调度＋DEM | 18／8／8／2、有序 ID、释放资格和 Fig.21 跨段候选已有 Python／RTL 对拍；跨段名义补偿与模拟连接仍须系统验证 | [代码与接口](task1/BRIDGE_SPEC.md) · [数字验证](task1/evidence/bridge_rtl/README.md) |
| 电容失配＋LMS | 2%／3% 固定失配有独立训练、留出、INL／DNL 和频谱对照；LMS 目前是**片外 Python** | [LMS 对照](task1/evidence/bridge_lms/README.md) · [INL／DNL](task1/evidence/inl_dnl/README.md) |
| SKY130 数字后端 | 跨段顶层已导出 GDS；6.25 ns 下 TT 角 setup／hold 通过。非跨段顶层尚有 setup 违例，独立 DRC／LVS 尚未执行 | [GDS、时序和版本](task1/physical/SKY130HD.md) |
| 分路 dither | 原文 Fig.19 物理注入及 RTL 闭环尚未交付 | [模块二计划](task2/README.md) |
| CAAZ | 数字控制与模拟宏联合尚未交付 | [模块三计划](task3/README.md) |

SKY130 是本阶段指定的数字工艺；它**不是**未来 180 nm BCD 的签核结果。当前目标输出码宽为 20 位，不等于已证明 20 位有效精度。权重训练与重构乘加的最终硬件归属待定。

## 运行和目录

需要 Python 3.12、Verilator；校准和静态分析使用固定提交的 ADCToolbox。输出目录应是**尚不存在的仓库外目录**。

```sh
python -m pip install -r task1/requirements-calibration.txt
PYTHONPATH=.:src python -m pytest -q task1/tests
PYTHONPATH=.:src python task1/tools/run_rtl_checks.py --out /tmp/task1-rtl-run-001
PYTHONPATH=.:src python task1/tools/run_bridge_checks.py --out /tmp/task1-bridge-run-001
```

| 路径 | 用途 |
| :--- | :--- |
| `task1/rtl/`、`model/`、`tb/`、`tests/` | 模块一 RTL、Python 模型和验证 |
| `task1/experiments/`、`evidence/` | 实验脚本、中文图片、原始数据和收据 |
| `task1/physical/`、`design/` | SKY130 GDS／STA、约束、需求与验证记录 |
| `task2/`、`task3/` | 后续 dither 与 CAAZ 的交付计划 |
| `src/adi_model/`、`tools/` | 本任务复现依赖的电荷／频谱参考和 RTL 工具辅助 |
| `references/` | ISSCC 与北大论文、讲稿及专利索引 |

校准对照统一执行[失配与 LMS 标准](task1/EXPERIMENT_STANDARD.md)：同一物理失配池、明确开关、训练／留出分离，并同时报告收益、噪声和越界。完整复现及条件见 [模块一说明](task1/README.md)。
