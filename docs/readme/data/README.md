# 首页历史数据与新结果的身份

af8／9bdf原有的JSON、NPZ与图数据按原字节保留。af8功能合同及9bdf文档保存了这些记录；实际受测源码仍由各JSON中的commit、dirty状态、源SHA和原输入确定，不能统称9bdf新运行。

| 资产组 | 原用途与范围 | 当前使用方式 |
| :--- | :--- | :--- |
| calibration_40m.{json,npz} | 40ae71d等旧模型的独立静态校准，训练／留出分开 | 历史比较，不作为新默认模型或新RTL闭环性能 |
| structural_calibration*_summary.json、structural_calibration_holdout.npz | 旧结构DUT＋固定静态plant，两颗失配种子及另一个48 µV假设噪声条件 | 保留原raw、均值／标准差／RMS、系数和源SHA；新calobs v2不沿用旧观察包 |
| interleave_40m及timing数组 | 旧A/B、generic与旧RTL规则Python调度；浮点误差、最终整数码和谱分别保存 | 不称SV频谱或当前时偏／带宽补偿；原source_head与dirty记录不刷新 |
| dither_headroom.json | 旧range／sampling／quantizer幅度网格，保留全部失败点与原端点 | 退役方案的历史诊断，不作为新Fig.19结果 |
| randomization_contract.json及DEM专题 | 旧SID／零和bridge候选的名义规则与使用统计 | 解释旧方案，不能冒充源3D DEM或新效果 |
| historical/ | d809da6等更早数组、迁移记录与原图身份 | 只按对应完整版本、输入和假设恢复 |

只读历史重绘器`make_overview_figures.py`从冻结数据出图，输出`historical_architecture`、`historical_calibration`、`historical_interleave`、`historical_dither_headroom`的PNG／SVG及独立manifest。它不运行当前ADC或训练器，不覆盖库内原图／数据。

```bash
MPLBACKEND=Agg python docs/readme/make_overview_figures.py --out ../sar-output/history-overview-001
```

已删除的`reproduce_interleave_40m.py`和旧DEM／dither／结构训练复现器只在[9bdf完整快照](https://github.com/defineiocc02/20bit_SAR_ADC_Behaviour_Verification/tree/9bdf7708f42733896d7ead89935be41b4258fb6f)恢复；当前树不保兼容入口。旧图重绘成功仅说明冻结数值与显示一致。

新架构快照和新结果使用独立名称、源SHA、数据及绘图清单。中间Python003图／数组仅留库外；本轮最终Python／RTL结果须由实际冻结后的新运行产生，不更改旧JSON来继承通过。

旧`sim/vectors/`的35个文件已按原字节迁至`docs/history/legacy_vectors_af8/`，配套README与SHA256SUMS用于核身份；旧manifest内部路径不改。另9份历史配置／系数／寄存器JSON保留原路径原字节。它们退出当前运行配置，完整历史实验仍须从[Git af8快照](https://github.com/defineiocc02/20bit_SAR_ADC_Behaviour_Verification/tree/af8aa37d43db40eb896e369305883ec89786547b)恢复，不将历史bundle装作新schema2输入。

`source_bound_static_calibration.json`和`source_bound_static_holdout.npz`提取自最终源码的实际`fullspan_positive`／`midrange_rank_negative`，绑定37c247c1…静态总收据、producer及36个loaded源码。中幅4096笔在rank1224/1279拒绝；正例512笔留出只覆盖[−2.6,+2.7] V，采样／RA随机噪声、dither和单元DEM关闭。[重新训练](../../REPRODUCING.md#3-静态校准重新训练固定与留出)与只读重绘分开；原执行的git_head和dirty状态原样保留。

`source_bound_rtl_calibration.json`和`source_bound_rtl_holdout.npz`提取自实际`full_typechecked_001`的512个目标OUT、同ID参考以及同版5cfb生产Python／独立整数码。原DUT manifest及其PENDING字段不变；数据账本追加2871d822…统一收据，登记训练、名义、固定、组件四个实际重放，以及两组各25类损坏拒绝。`source_bound_fig19_evidence.json`使用同一DUT的实际早prefix记录，并在当前Python上重跑±2 V的64字离散点及−2.9 V／word32反例。

本版重新提取的两件逐点NPZ和八件PNG／SVG与前次真实运行逐字相同；六份来源、生成与资产索引JSON绑定新记录。旧012／013、005／050–055及其中间收据仍保原身份；本次没有用新SHA改写原始记录。

[资产索引](source_bound_assets_manifest.json)列出四张新图的PNG／SVG、绘图脚本、参数／逐点输入和生成清单。图数据仅为指定工程静态用例；未建立源3D DEM、连续全幅、真实参考建立、CAAZ组合或工艺签核。
