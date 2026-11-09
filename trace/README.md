# L1 源文台账

这里只有一份表和一份说明。表回答一个问题：

> **原文第几页哪一句 → 落在哪行代码 → 哪个测试能证明它。**

主表：[`source_matrix.csv`](source_matrix.csv)（43 行：论文 14 + 讲稿 21 + 专利 8）

## 为什么单独有一层

仓库原本有两层台账，各管一段，本轮**都不动**：

| 层 | 文件 | 回答什么 |
| :--- | :--- | :--- |
| L1 源文层（**本目录，新建**） | `trace/source_matrix.csv` | 原文说什么 → 代码在哪 → 哪个测试 |
| L2 任务需求层 | `task1/design/requirements.csv`、`task{3,integration,reconstruction}/requirements_trace.csv` | 本任务哪条需求由哪些测试覆盖 |
| L3 验证意图层 | `task*/verification_plan.yaml` | method / oracle / acceptance / evidence |

**不合并进 L2 的理由**：`task1/design/requirements.csv` 有 `rtl_block` 列，且 `status=PASS` 指的是 RTL 检查通过。把行为模型的事实塞进去，会长出「行为模型 PASS 看起来 RTL 也 PASS」这种假象，直接违反「RTL 冻结、证据不升级」。

L1 用 `trace_ids` 列反向引用 L2/L3 的 ID（如 `REQ-M1-13;V-DITHER`），由校验脚本保证引用不断链。

## 状态口径

| status | 含义 | 硬性要求 |
| :--- | :--- | :--- |
| `PASS` | 机制在代码里，且有测试守住 | `impl_path`、`impl_symbol`、`test_id` 三者都不能空 |
| `PARTIAL` | 机制只对上一部分，差异写在 `note` | — |
| `MISSING` | 机制不存在或数值未落地 | 必须在 `note` 写明缺什么、哪个里程碑补 |
| `FAIL` | 有实现但结果与原文不符 | 必须在 `note` 写明失败点 |
| `NOT_RUN` | 机制在，但对照实验还没跑 | — |
| `OUT_OF_SCOPE` | 行为模型不打算建模 | 必须在 `note` 写明理由 |

**`PASS` 不表示「数值对上了」。** 它只表示这条原文事实在代码里有对应物、并且有测试守着这个对应物。数值对标由 M7（A1–A5）与 M8（静态 12 格）另行判定，两者不互相顶替。

当前分布：`PASS=17  MISSING=12  OUT_OF_SCOPE=8  NOT_RUN=5  PARTIAL=1`。12 条 `MISSING` 就是本轮要补的缺口清单；定版门禁 G1 要求它归零。

## 怎么跑

```bash
uv run --with pytest --with numpy --with matplotlib python tools/check_source_matrix.py
```

加 `--strict` 则额外要求 `MISSING == 0`（G1 口径）。加 `--skip-collect` 跳过 pytest 收集，改表时省几秒，正式跑别加。

脚本查六件事：表头列序、必填非空、枚举合法、`impl_path` 的 `路径:行号` 存在且 `impl_symbol` 落在该行 ±40 行内、`test_id` 能被 `pytest --collect-only` 收到、`OUT_OF_SCOPE`/`MISSING` 有 `note`。

`impl_path` 写成 `路径:行号` 是刻意的：行号会随重构漂移，但漂到 ±40 行以外就说明结构变了，此时应该重新看一遍这条引用，而不是让脚本猜。

## 两条口径，防后面打架

### 1. 相对量在两条预设间不变

INL / DNL 若用 **LSB20 或 ppm** 表达，换 `full_scale_peak_v` 不改数值（LSB 电压同比缩放）。只有绝对量（µV、nV/√Hz、V）随预设变。

所以「baseline 的 INL 是 X LSB，paper 预设下会变成 1.667X」这类换算是错的——LSB 本身跟着缩放。

### 2. `mismatch_sigma_unit` 换 `unit_cap_f` 后相对失配不变

失配按 `sigma_f = σ_u · sqrt(C · C_u)` 生成（`capacitors.py:107`）。C 与 C_u 同比缩放时相对失配恒定：2C 单元的相对 σ 永远是 `0.707·σ_u`。

后果有两条，都要守住：
- paper 预设**不改**静态失配地板，所以 INL 的地板在两条预设下同量级；
- **不许**为了凑 INL 去改 `mismatch_sigma_unit`。M8 的判据里写死了这条。

## 页码仲裁：五种说法怎么收敛到两种

仓库历史上有 p10 / p22 / p26 / p27 / p33 五种关于「专利第几页」的说法。逐条核对 OCR 后收敛为：

| 说法 | 裁决 |
| :--- | :--- |
| **p22** | ✅ **FIG.19 的图**在这一页，页内自标 `Sheet 20 of 21` |
| **p26** | ✅ dither 机制与 summer 222/224 的文字说明（本模型极性的直接依据之一） |
| **p27** | ✅ 整数 dither 与分数 dither 的区分、FIG.5 描述 |
| **p33** | ✅ **FIG.19 的文字描述**：拆字、第二/第三 dither、残余模拟 dither |
| p10 | ❌ 无任何证据支持，OCR 也未覆盖该页（OCR 只从 p22 起） |

**「p22 派」和「p33 派」都对，只是指的不是同一个东西**（图 vs 描述文字）。详见 [`references/patent/README.md`](../references/patent/README.md)。

另有一条已知的图/文张力：FIG.19 的 224 画成 `u − c`，正文写「从 ADC 200 数字输出中减」即 `c − u`。二者只差 RDAC 码的加减约定，物理等价；本模型取正文写法（C9），已在 `PAT-05` 的 `note` 记明。**图的 ± 位置不作为极性依据。**

## 已知的源文内部散差（不是我们的误差）

对标窗口不是拍出来的，是源文自己就不一致：

| 量 | 论文 | 讲稿 | 台账行 |
| :--- | :--- | :--- | :--- |
| DR | 94.2 dB | **94.6 dB** | PAPER-12 / SLIDE-16 |
| NSD | 9.3 nV/√Hz | **8.8 nV/√Hz** | PAPER-12 / SLIDE-17 |
| SNR @1.05 MHz | 92 dB | **93.5 dB** | PAPER-13 / SLIDE-18 |
| INL/DNL 单位 | ppm | **LSB** | PAPER-11 / SLIDE-13 |
| DAC 采样带宽 | >10× f_Nyq | **15× f_Nyq** | PAPER-07 / SLIDE-05 |
| 参考电流 | 1.8 mA | **1.9 mA** | PAPER-06 语境 / SLIDE-08 |

所以 A1 的 ±0.5 dB、A4 的 ±1.0 dB 是从这张表反推的容差，不是留的余地。任何「模型差 0.4 dB 所以没对上」的判断，都必须先说明对标的是哪一页的哪个数。
