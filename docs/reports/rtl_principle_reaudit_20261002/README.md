# 原理与综合结构独立复核补充

[PDF](report.pdf) · [独立 LaTeX 源](report.tex) · [Markdown 集成分析](../../rtl/PRINCIPLE_AND_SYNTHESIS_REAUDIT_20261002.md)

本次补充 46 页，逐项解释全部 24 生产模块和 2 参数头、校正数学、原文映射、时序差距及综合/STA/布线验收计划。与既有 115 页报告分别保存，不用补充文档的身份覆盖历史 EDA。

源码基线 `71935ab`；生产 RTL 未修改。源默认 P7，历史物理 P5；native editor 成功，本机严格 XeLaTeX 导出完成，最终日志无硬错误、缺字、溢出框或未定义引用。逐页 QA 和输出 SHA 见 [证据](../../evidence/20261002/principle_reaudit/README.md)。

单个 `report.tex` 已含正文、三个详细附录和原创矢量图，无额外输入文件或外部图依赖，可直接在内置编辑器查看/编译。若要由 Markdown 重新生成：

```sh
python tools/build_principle_reaudit_report.py --repo . --out /tmp/sar_reaudit_report.tex
```

生成器检查冻结的 26 个 RTL SHA；已验证生成源与交付源逐字节相同。导出在 XeLaTeX 环境从源文件目录使用 basename 严格编译至少两次：

```sh
xelatex -interaction=nonstopmode -halt-on-error -file-line-error report.tex
xelatex -interaction=nonstopmode -halt-on-error -file-line-error report.tex
```

运行目录应为复制到独立输出目录后的源目录，避免混入构建中间文件。使用 ctex/Fandol 与标准 TeX 包，不需要 shell-escape。
