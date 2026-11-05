# 论文与讲稿原件

本目录按维护者明确选定的本机文件保存三组资料，共 **6 个 PDF、158 页**。讲稿原件本身是 PDF，不是可编辑 PPTX。文件仅规范命名，正文、图像、版权页和内嵌声明均保持原样；逐文件页数、大小及 SHA-256 见 [source_manifest.json](source_manifest.json)。

| 资料 | 论文 | 讲稿／PPT 的 PDF 原件 | 技术定位 |
|---|---|---|---|
| ISSCC 2024，9.8，Bodnar 等 | [3 页论文](ISSCC2024_PAPER.pdf) | [44 页讲稿](ISSCC2024_SLIDES.pdf) | 20 位／40 MS/s 架构、DEM、dither 和 slice 调度的主要对照 |
| VLSI 2025，C32-1，Wang 等（北大／清华） | [3 页论文](PKU_VLSI2025_C32_1_PAPER.pdf) | [42 页讲稿](PKU_VLSI2025_C32_1_SLIDES.pdf) | 闭环动态放大器和斩波辅助自归零 CAAZ |
| CICC 2026，13-6，Jin 等（电子科大） | [4 页论文](CICC2026_13_6_PAPER.pdf) | [62 页讲稿](CICC2026_13_6_SLIDES.pdf) | 连续时间第二级 SAR、LSB 重复与 tracking averaging |

论文的正式入口分别为 [ISSCC 2024 DOI](https://doi.org/10.1109/ISSCC49657.2024.10454329)、[VLSI 2025 DOI](https://doi.org/10.23919/VLSITechnologyandCir65189.2025.11075015)、[CICC 2026 DOI](https://doi.org/10.1109/CICC65509.2026.11509489)。CICC 讲稿对应桌面的 `13-6_Jin.pdf`；文件名含 `CT2_User` 的已有副本实际上与这份 CICC 论文相同，不另计一份。`A_High-Linearity_High-Stability_Dynamic_Amplifier.pdf` 是另一篇 TCAS-II 论文，未混入本组。

三组资料不是同一套实现。当前工程包含 ISSCC／专利机制的项目候选及北大 CAAZ 样本域扩展；CICC 的连续时间后级、反馈电容与 SINC² tracking averaging 尚未由本工程复现。收录原件也不表示已经实现全部电路或完成全部组合验证。

原件的版权与内嵌使用声明归原作者／出版方所有，不适用本工程的 BSD 软件许可，不因存放于此而获得新的授权。Python wheel 和 sdist 不包含这些 PDF；从发行包阅读本页时，请访问 [GitHub 资料目录](https://github.com/defineiocc02/20bit_SAR_ADC_Behaviour_Verification/tree/main/references)获取仓库中的对应文件。

这里的 158 页是文件清点口径。首页原有的 20 份／641 页完整阅读审计保留其历史范围；CICC 资料的新身份核对不计为 62 页讲稿的完整技术复核。
