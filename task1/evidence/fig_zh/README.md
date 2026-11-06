# 中文效果图

| 图 | 比较条件 |
| :--- | :--- |
| [交织时偏](交织时偏.png) | 同一组切片时偏，比较固定 A/B 与动态选片。 |
| [固定失配下的 DEM](固定失配下的DEM.png) | 同一颗虚拟芯片，只切换单元 DEM。这是未校准诊断。[独立 LMS 对照](../bridge_lms/README.md) · [逐码 INL／DNL](../inl_dnl/README.md) |
| [理想减性扰动](理想减性扰动.png) | 跨段 DEM 开启、LMS 系数冻结，只切换理想细级扰动。[模型和边界](../adc2_dither/README.md) |
| [后级余量](后级余量.png) | 同一组冻结 LMS 权重，比较直接用粗码与邻码居中。图中为全幅极值，越界笔数来自正弦留出。[数据说明](../residue_control/README.md) |

从仓库根目录使用项目的 Python 环境运行。默认检查系统中文字体，可用 `--font` 指定本地字体文件。

```sh
PYTHONPATH=.:src python -m task1.experiments.chinese_effect_plots
```

PNG 为 200 dpi，同名 SVG 保留矢量曲线和字形。[来源与产物校验清单](manifest.json)
