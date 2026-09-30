# SAR ADC RTL、论文与专利核对报告

本目录是多文件中文 XeLaTeX 报告的自有源文件包，入口为 `report.tex`，章节位于 `sections/`，所需图件位于 `figures/`。使用 `ctexrep` 的 Fandol 字体集；复编译不依赖作者的 macOS 系统中文字体。

报告包仅分发自行撰写的 TeX、原创结构图、根据已授权使用的数字证据自行绘制的科学图及生成的报告 PDF。**不分发用户提供的 ISSCC 论文、演示稿、专利 PDF 或其他参考文献原件。** 正文保留文献编号、页码和权利要求定位，读者需自行取得原始文献以复核相应来源。

## 编译

需要已经安装的 XeLaTeX，以及源码使用的 `ctex`、Fandol、AMS 数学包、`geometry`、`booktabs`、`longtable`、`array`、`tabularx`、`graphicx`、`xcolor`、`hyperref`、`fancyhdr`、`enumitem`、`float`、TikZ/PGF 等 TeX 包。本脚本不安装或更新任何工具，也不启用 shell escape。

从任意目录执行，路径有中文或空格时保留引号：

```sh
sh '/path with spaces/报告/build_report.sh'
```

脚本优先使用显式 `XELATEX`，否则依次查找 `PATH`、macOS `/Library/TeX/texbin/xelatex`、用户 TinyTeX。也可指定一个可执行文件；该变量不能包含额外选项或 shell 命令：

```sh
XELATEX='/path with spaces/TeX/bin/xelatex' \
  sh '/path with spaces/报告/build_report.sh'
```

脚本在报告目录内创建独立的 `.build/report.XXXXXX/`，运行两遍 XeLaTeX；若引用、目录或辅助文件仍变化，再运行第三遍。每遍控制台、TeX 日志、编译器路径/版本及 `.fls` 依赖记录均保留。输出 PDF 位于该构建目录，**不会自动覆盖包内已经交付的 `report.pdf`**。失败也保留目录，便于检查真实诊断。

非零编译返回码、TeX Error、最终日志的 Overfull box、缺失字符、未定义引用/引文或三遍后仍需重跑均返回失败。普通 Warning/Underfull 会打印以供排版检查。成功状态只表示编译与这些日志门禁通过；仍须渲染全部页面并检查字体、表格、图中文字、溢出与引用，再决定是否替换发布 PDF。

源码与说明统一保存为 UTF-8。不要因终端显示乱码而批量重写正文；先核对原文件字节、终端编码与 PDF。使用不同 TeX/字体版本可能导致分页或 PDF 字节变化，应记录新构建身份，不能沿用旧 PDF 摘要。

## 发布身份与科学结论

最终发布包应随附 `delivery_manifest.json`，由发布者在最终编译和逐页视觉复核后生成。本脚本不生成该最终清单，也不声称当前开发目录已完成最终发布。清单至少记录：

- 报告源文件和所用图件的 SHA-256；输出 PDF 的 SHA-256 与实际页数。
- XeLaTeX 版本、构建命令、最终日志摘要与逐页视觉检查记录。
- 对应 Git 提交、受测 RTL 内容摘要，以及每项 CI、功能仿真、综合网表、布局布线证据各自的 manifest 路径和 SHA-256。
- 各证据的实际结论与适用范围，明确历史失败、已发布版本及尚未验收候选之间的区别。

以该最终清单及其引用的原始证据判断 PDF 身份和结论。README 不预填页数，也不将“编译成功”写成 RTL、mapped functional、FPGA timing 或 ASIC 签核成功。旧图的 PASS 仅对应其归档源码与运行条件，不能移用于随后修改的缓存候选。640 MHz 是目标 ASIC 约束；FPGA 结果按实际器件、约束和完成阶段解释。模拟性能、专利结构覆盖及未实现边界仍以正文中可追溯的限定为准。

## 图件与证据复现

编译必须携带所有被 `\includegraphics` 引用的图件；只提供 TeX 不足以重建 PDF。已有 PNG/PDF 可以直接用于编译，不需要重新运行仿真器或 Python。

重新制图按 [`audit_figures/README.md`](audit_figures/README.md) 的逐图命令进行，并取得相应的原始日志、manifest、哈希清单及指定 Git 对象。需要 Matplotlib/NumPy 的步骤属于重新制图，而非 XeLaTeX 编译依赖。图件生成脚本不等同于重新执行全部 RTL/EDA 实验。

部分历史原始 CSV、完整仿真构建目录及大 DCP 位于外部证据包，不随普通 Git clone 自动提供。特别是历史小单元 XSim 图使用完整 CSV，而不只是图中可见片段。缺失这些外部原始数据时，可以用随包自画图复编译报告，但不能声称已经独立重跑或重建对应实验。历史证据和新证据不得通过改文件名混用。

## 最小打包清单

用于阅读与复编译的最小发布包：

```text
README.md
build_report.sh
report.tex
report.pdf                    # 最终复核后的输出
delivery_manifest.json        # 最终发布者生成；不可用开发检查点冒充
sections/*.tex                # 所有直接和间接引用章节
figures/<all referenced files> # 保持正文中的相对路径
```

当前有条件引用的测量表也应随发布包携带；不能因为 `\IfFileExists` 会跳过文件就将它漏打包。发布后新增章节或图件时，以实际 `\input`/`\includegraphics` 依赖及构建 `.fls` 复核清单。

为了可重建科学图并追溯演变，建议同时携带 `audit_figures/` 中的自有脚本、README、图件来源哈希/QA 清单，以及 `history/` 中保留的自有旧图和生成材料。实际数据源按清单从仓库 `docs/evidence/` 或外部证据包获取；不要把无关用户文件或参考文献原件加入包中。

`.build/`、临时 `.aux/.toc/.out`、旧开发编译日志和整页渲染缓存不属于最小包。最终构建日志及视觉 QA 的精简记录应另行保留并由 `delivery_manifest.json` 引用。
