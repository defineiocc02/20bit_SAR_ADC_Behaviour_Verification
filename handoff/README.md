# 九个月任务书交付包

先看[NINE_MONTH_PLAN.pdf](NINE_MONTH_PLAN.pdf)。前7页可以直接用于执行：第1页任务表与数字spec，第2页接手现状与计划，第3–5页为DEM／dither／CAAZ任务和效果验收，第6页数字签核，第7页接口与交付。

后10页为原理与复现说明：第8–11页沿ISSCC架构及专利Fig.11／20／21讲电容、重排、电荷和LMS；第12–14页沿专利Fig.3／5／19及ISSCC讲稿讲dither；第15–17页沿北大Fig.5／6及讲稿讲斩波位置、噪声抵消、保持、频响和整数处理。

- [Markdown阅读稿](NINE_MONTH_PLAN.md)：任务书内容源；[LaTeX](NINE_MONTH_PLAN.tex)与[模板](report_template.tex)可编辑。
- [计划图](figures/nine_month_plan.png)与[验收链](figures/signoff_flow.png)为安排示意，不代表任务已完成；其余图片逐字节引用原归档。
- 原图按用户明确要求节选自提供的原件，图号、PDF页码、裁剪及摘要见[source_figures.json](source_figures.json)；版权归原权利人，软件许可不覆盖原图。没有新增完整第三方PDF。
- [构建记录](build_record.json)保存文档身份、页数和图片有效dpi；[编译日志](build.log)只把机器路径替换为占位符，诊断内容不变。该构建记录仅验证文档，不充当ADC、RTL或数字签核结果；行为级复核另列。
- [Python复核记录](python_reuse_check.json)登记20项DEM测试、65,536组名义关系及逻辑／物理使用诊断；逻辑起点不均未被判为行为缺陷，记录不代表数字签核。

在仓库根目录重建PDF，需要现有XeLaTeX、PyMuPDF、Matplotlib和本机已许可的模板字体：

```sh
python handoff/build_report.py --pdf-only --engine /path/to/xelatex
```

重转MD并生成计划图，另需md2latex转换器：

```sh
MPLBACKEND=Agg python handoff/build_report.py \
  --converter /path/to/md2latex.py --engine /path/to/xelatex
```

如需重新截取原图，使用已有获授权的原件，先执行：

```sh
python handoff/extract_source_figures.py \
  --dem-pdf /path/to/US10511316B2.pdf --dither-pdf /path/to/US10505561B2.pdf
```

该命令还读取仓库已有ISSCC讲稿与北大论文，不下载原件，不改变电路几何。分幅与原图方向须重新目视核对；渲染dpi不等于原始位图信息量的增加。

构建程序运行三遍XeLaTeX，关闭shell escape，清理私有临时目录；遇缺字、纵向溢出或编译错误终止。修改后仍需逐页目视，程序检查不能替代版面检查。
模板使用Times New Roman、Arial、Menlo、Songti SC和Hiragino Sans GB；其他平台替换为已安装且覆盖所需字符的字体后重新检查。字体文件不随包分发。

已有模块一、dither独立和重构收据的源码身份已核对；两份整体记录的部分摘要不一致，只登记为需定向重放，没有篡改收据或宣称本次重新跑过RTL。

## 行为级成果复核

[审计表](BEHAVIORAL_STATUS.md)列明有效成果与范围；[本次复算收据](behavioral_audit.json)包含24组频谱、16组线性度、四组NLMS与CAAZ四组的复核。可用现有固定依赖重新检查，输出必须新建：

```sh
PYTHONPATH=.:src OPENBLAS_NUM_THREADS=1 MPLBACKEND=Agg \
  python handoff/replay_behavioral_audit.py --out /tmp/sar-behavior-audit-new.json
```

这是已有数据的只读复算，不生成新制造样本或修改原曲线。行为验收不要求先完成器件开关／模拟瞬态。
