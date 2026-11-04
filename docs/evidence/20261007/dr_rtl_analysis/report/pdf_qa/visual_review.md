# 33 页 PDF 视觉 QA 记录

## 被检版本与结论

- 文件：`report.pdf`，33 页，最终 SHA-256：`9ba9cf25203de34d72bd7d1f1ee200f4029784fd130bf7874462d5c0a8b8e8b2`。
- 编译：`compile.log` 记录 XeLaTeX 两遍均成功输出 33 页，`report.log` 也确认 33 页。日志无 Overfull/Underfull box、未定义引用、缺失字符或 LaTeX/Package Warning。Fandol CJK script 支持提示为 fontspec 信息项；页面中未见缺字或可读性问题。
- 结论：33 页均已逐页目视检查；正文、附录、表格、目录及图表可读，未见裁边、重叠、空白页、缺字、表格断裂或图形缺失。上一版孤立的 SHA/命令清单已并入附录 B 证据清单同页，问题已解决。

## 最终版本像素复核

首次对 33 页版本完成逐页检查后，报告作者只对生成的 `.tex` 行尾空格做清理，并重新编译。随后对最终 PDF 重新渲染全部页面，并比较两次渲染的逐页 PNG SHA-256：前一版 PDF SHA-256 为 `150794c779b1803c66cd62b2797c623ba9a04323d54f55d82d4bbd4a7709be82`，最终 PDF SHA-256 为 `9ba9cf25203de34d72bd7d1f1ee200f4029784fd130bf7874462d5c0a8b8e8b2`；33/33 页像素文件完全一致，变化页数为 0。结果保存在 `final_pixel_compare.json`，两组逐页像素 SHA 保存在 `previous_render_pixel_sha256.json` 和 `page_inventory.json`。因此终稿的画面与已逐页目视检查的版式完全一致。

## 渲染和逐页检查证据

使用缓存环境命令：

```text
uv run --offline --no-project --python 3.12 --with matplotlib==3.11.2 --with pymupdf python report/pdf_qa/render_pdf_qa.py report/report.pdf report/pdf_qa
```

生成 `page_01.png` 至 `page_33.png`，每页 2.0× 渲染（约 1191 × 1684 px）；检查者实际逐张查看了全部 33 页。`contact_01.jpg` 至 `contact_06.jpg` 用于版面总览。检查范围为 PDF 第 1–5、6–10、11–15、16–20、21–25、26–30、31–33 页，重点查看目录续页、跨页表格、图表、9pt 附录清单和最终页。

`page_inventory.json/csv` 记录 PDF 页数、SHA、逐页文本字符数、文本边界、图像/矢量对象数量以及渲染图 SHA-256。所有页面均有可提取文本且文本边界位于纸张范围内；辅助边界余量最小值为左 56.53 pt、右 55.42 pt、上 25.84 pt、下 30.54 pt。边界数据用于机械筛查，裁切和可读性判断依据逐页图像检查。

## 重点改版核查

- PDF 第 6 页新增 CI 实际 merge commit 身份说明完整可读，正文说明 checkout merge commit 与审查 HEAD 仅 README 不同，并列出 `baseline/ci_tree_binding.json` 证据位置。
- PDF 第 30 页（印刷页 28）附录 B.11 的末条 “Report/source SHA inventory” 与来源清单同页。上一版近空白孤行页消失；页底是正常页脚留白，没有额外续页。
- PDF 第 31–33 页附录 C 正文为 9pt；测量定义、乘法宽度解释、generic 结构结果和检查项清楚可读，段落续接正常。收尾说明和 artifacts 清单同页，没有单独稀疏尾页。
- 目录显示页码连续至附录 C.5；页眉、页脚和图表标注清楚。

## 页段记录

| PDF 页 | 目视结果 |
|---|---|
| 1–4 | 封面、摘要和两页目录清楚，目录页码层级连续。 |
| 5–10 | 第一章正文、公式、表格和 CI merge commit 说明清楚；跨页表格续接自然。 |
| 11–12 | 第二章路径/扇出图表及 P5/P7 对比可读，图例、数值和说明完整。 |
| 13–20 | 附录 A 复核文字、控制合同表格、模块条目和已知边界均可读，无跨页截断。 |
| 21–30 | 附录 B 的约束、历史 STA 证据、MCP 候选条件、执行步骤及来源清单可读；末条已回并前文。 |
| 31–33 | 附录 C 的 generic 测量定义、乘法语义、结果、结构检查及 artifacts 清单可读，9pt 正文无裁切。 |

## 范围边界

本次只写入 `report/pdf_qa/` 的最终渲染、清单和 QA 记录；未修改 `report.tex`、`report.pdf`、RTL 或 35 页历史 QA，也未运行综合/STA。QA 判断排版可读性，不代替技术内容复核或 EDA signoff。
