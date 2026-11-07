# US10505561B2 原文转写入库

本目录只放 **OCR 转写文本** 与 **一张附图裁图**。专利 PDF **不入库**（维护者决定），因此这里的一切都不能自证无转写错误。

- 文件清单、字节数、SHA-256、覆盖页范围：[`manifest.json`](manifest.json)
- 全文台账（原文 → 代码 → 测试）在 [`../../trace/source_matrix.csv`](../../trace/source_matrix.csv)

## 四条声明

### 1. 本目录是 OCR 转写，不是文本层

`US10505561B2_OCR.txt` 由扫描件经 vision OCR 得到，**没有 PDF 文本层可用**。具体后果：

- 下标被系统性讹化。位名 `D6, D5, D4, D3, D2, D1` 在转写里会出现 `D$s`、`Ds`、`DE`、`D,`、`D›` 等形态（见 `page_pdf=22` 附图页与 `page_pdf=33` 正文页）。
- 双栏被压平。同一行的左右两栏文字会交错，逐句引用时必须按语义拼接，不能按物理行读。
- 数字与字母互讹：`A-6` 实为 `A=6`；`betore` 实为 `before`；`pertormance` 实为 `performance`。

凡引用本目录，`evidence_level` 一律记 `OCR`，不得记 `PRIMARY`。

### 2. 校对范围

只核对过本模型实际依赖的段落，逐条列在 `trace/source_matrix.csv` 的专利行里（`source_doc=US10505561B2`，共 8 行）。其余页**未校对**，包括题录页、权利要求全部条文、FIG.1–FIG.18 的文字描述。

`manifest.json.coverage` 写明 OCR 只覆盖 `page_pdf=22..36`（36 页扫描件的后半段 15 页）。**前半段缺失**，所以「专利没写某个参数」这类否定判断在本仓库不成立——只能说「OCR 覆盖范围内未见」。

### 3. 页码双坐标

两套页码并存，不可互换：

| 坐标 | 含义 | 例 |
| :--- | :--- | :--- |
| `page_pdf` | OCR 源文件内的 1 基页序号 | FIG.19 附图在 `page_pdf=22` |
| `page_printed` | 专利自身的 Sheet 标注 | 同页写 `Sheet 20 of 21` |

**FIG.19 的图在 `page_pdf=22`，它的文字描述在 `page_pdf=33`。** 仓库历史上「p22 派」与「p33 派」都对，只是指的不是同一个东西（前者指图，后者指描述文字）。OCR 未保留印刷栏号，故本目录**不记栏号**——任何「第 N 栏」的说法都无法在本仓库内复核。

### 4. 裁图边界

`US10505561B2_FIG19.png` 只含 FIG.19：

- **含**标号 `200`（ADC）、`220`（SAMPLING DAC）、`222`（采样求和节点）、`224`（码修正节点）、`300`（量化器求和节点）、`400`（DITHER GENERATOR）。
- **不含** `226`——该标号不在这张图里，早前把它写进需求是笔误。
- 不含 FIG.20（`page_pdf=23`）。FIG.4、FIG.6A–F、FIG.20 只有 OCR 正文可引，**没有图形证据**。

## 由图直接读出、且与正文有张力的一处

FIG.19 的 `224` 画成：`+` 在 `D6, D4` 入口、`−` 在 ADC 200 输出入口、出口进 `220`，即图上算的是 `u − c`。
正文（`page_pdf=33`，OCR 第 1355 行）写的是：

> a value of 0, 1, 4, or 5 … **is subtracted from the digital output of the ADC part 200** before being used to set the sampling residue DAC 220

即 `c − u`。两者不矛盾：它们只差 **RDAC 码的加减约定**（图对应「码加进残差」，正文对应「码从残差减」），物理上是同一件事。

本模型采用**正文写法** `k = c − u`（冻结合同 C9），并在此记录图的写法，避免后人误以为漏读图。这条张力已作为 `FIG_TEXT_TENSION` 写入台账 `note` 列，**图的 `±` 位置不作为极性依据**。
