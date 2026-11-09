# 原文资料

本分支收录维护者选定的 **4 份 PDF，共 92 页**。原件的正文、图片、版权页与内嵌声明保持原样。页数、字节数和 SHA-256 见 [文件清单](source_manifest.json)。

| 资料 | 论文 | 讲稿 | 用途 |
| :--- | :--- | :--- | :--- |
| Bodnar 等，ISSCC 2024，9.8 | [论文（3 页）](ISSCC2024_PAPER.pdf) | [讲稿（44 页）](ISSCC2024_SLIDES.pdf) | 两级 SAR 架构、交织调度、DEM 与 dither |
| Wang 等，VLSI 2025，C32-1 | [论文（3 页）](PKU_VLSI2025_C32_1_PAPER.pdf) | [讲稿（42 页）](PKU_VLSI2025_C32_1_SLIDES.pdf) | 模块三的 CAAZ 控制与样本配对参考 |

论文正式入口为 [ISSCC 2024](https://doi.org/10.1109/ISSCC49657.2024.10454329) 和 [VLSI 2025](https://doi.org/10.23919/VLSITechnologyandCir65189.2025.11075015)。六件专利及版权说明见 [NOTICE](../NOTICE)。

## 专利：只有 OCR 转写，没有 PDF

[`patent/`](patent/) 收录的是 **US10505561B2（dither 方法）** 的 vision OCR 转写全文与 FIG.19 单张附图裁图。**PDF 依维护者要求不入库**，所以这里看到的不整齐、下标讹化、双栏交错都不是漏收，是转写本身的形态。

引它之前先读 [patent/README.md](patent/README.md) 的四条声明：OCR 性质、校对范围（只覆盖 `page_pdf=22..36`）、页码双坐标（FIG.19 的**图**在 p22、**文字描述**在 p33）、裁图边界（不含 226，不含 FIG.20）。台账在 [`trace/source_matrix.csv`](../trace/source_matrix.csv)。

原件版权和内嵌声明归各权利人所有。仓库的 BSD 软件许可仅适用于本工程软件，不向这些 PDF 授予新权利。
