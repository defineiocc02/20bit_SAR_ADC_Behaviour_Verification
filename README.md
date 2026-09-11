# 20 位 SAR ADC 行为验证模型

[![CI](https://github.com/defineiocc02/20bit_SAR_ADC_Behaviour_Verification/actions/workflows/ci.yml/badge.svg)](https://github.com/defineiocc02/20bit_SAR_ADC_Behaviour_Verification/actions/workflows/ci.yml)
[![License: BSD-3-Clause](https://img.shields.io/badge/license-BSD--3--Clause-green.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12%20%7C%203.13-blue.svg)](https://www.python.org/)
[![Code style: ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![Checked with mypy](https://img.shields.io/badge/mypy-checked-2f6f9f.svg)](https://mypy-lang.org/)
[![Tests](https://img.shields.io/badge/tests-189%20passed-brightgreen.svg)](#5-测试与四道门禁)

> **一个用来被"审计"而不是被"相信"的两级残差 SAR ADC 行为模型。**
> 建模对象为 ISSCC 2024 Session 9.8（Bodnar 等，20 bit / 40 MS/s 精度 SAR）。
> 它输出的每一个数字，都带一条**机器可校验**的"这个数字从哪来"的声明。

**English: [README_EN.md](README_EN.md)** ｜ 开源与法律声明：[§12](#12-开源声明与许可)

---

## 目录

| § | 章节 |
|:-:|:---|
| 1 | [这是什么](#1-这是什么) |
| 2 | [为什么长成这样](#2-为什么长成这样) |
| 3 | [来源分级——承重的设计](#3-来源分级承重的设计) |
| 4 | [安装与上手](#4-安装与上手) |
| 5 | [测试与四道门禁](#5-测试与四道门禁) |
| 6 | [本模型不支持什么](#6-本模型不支持什么) |
| 7 | [可复现性](#7-可复现性) |
| 8 | [仓库结构](#8-仓库结构) |
| 9 | [参与贡献](#9-参与贡献) |
| 10 | [引用方式](#10-引用方式) |
| 11 | [参考文献](#11-参考文献) |
| 12 | [开源声明与许可](#12-开源声明与许可) |

---

## 1. 这是什么

一个**相位精确**的 Python 行为模型，建模对象见 `[00]`：第一级用小而快的 SADC
粗量化，经残差放大器驱动 18 slice 的 RDAC，再把残差交给第二级 ADC2；数字侧
把两级读数重构回一个输出；其上叠加 DEM、dither 与增益 β 校准。

它是一个**设计探索**模型：你可以把某个机制单独开关，然后看误差跑到哪里去了。
它**不是**晶体管级仿真器，**不是** PDK 精确的失配研究，也**不能**替代流片——
见[§6](#6-本模型不支持什么)。

| 开箱即有的东西 | |
|:---|:---|
| 27 个模块、约 1.25 万行库代码 | 参数分级、物理 slice 池、两条独立信号链 |
| 24 个验收 stage | 每个都同时返回**数据**和一条明确的**判据** |
| 189 个测试 | 其中每个审计缺陷都对应一条对抗性回归 |
| 8 份 ADR | 每个结构性决策背后的推理 |

---

## 2. 为什么长成这样

对上一版（`adi_model_release_v6.1`，已冻结存于本仓库之外供对照）的外部审计
发现了 **11 个缺陷**，而本仓库自身的自检**一个都抓不到**——因为每一级都在用
**自己的假设**校验**自己的输出**。最伤的几个：

| # | v6.1 的缺陷 | 后果 |
|:-:|:---|:---|
| A01 | 把 "9b quantization in the first stage" 读成 6b 粗量化 + 3b 码字 | 输入判决电平只有 64 个，不是 512 个 |
| A02 | 每个周期重新独立抽 8-of-18 的 slice 置换 | 8 个转换 slice 里有 3.56 个正持有被转换样本 |
| A03 | dither 码混用了"粗步"与"RDAC 单位步"两种单位 | 差 8 倍；修正后溢出率 32.7% → 16.0% |
| A04 | RA 噪声由目标 DR 反解 | 复现 94.6 dB 是恒等式，不是预测 |
| A05 | KTC 观测器与已披露机制并列呈现 | 本仓库的原创工作被读成已公开能力 |
| A06 | 动态参考缓冲 / 共享 RA 完全没有建模 | −1.6 dB 与 +1.3 dB 无法解释 |
| A07 | 静态 / 低频 / 图表证据被过度声称 | 含 6 个子项：蒙特卡洛直方图由 400 个重采样点画出；`np.gradient` 斜率在 19 MHz 处低 25.6 dB |

响应是**结构性**的，不是打补丁：

- 来源分级从"注释里的标签"变成**运行期的值**；
- slice 池变成**跨周期有记忆的物理对象**；
- 两条信号链共用**同一个**量化栅格构造函数；
- 噪声预算被明确标注为**锚点**而非预测；
- KTC 支路分级为 `RESEARCH_EXTENSION` 且**默认关闭**；
- 交织 skew 用的斜率改为**解析 / 谱**导数，不再是中心差分。

逐条对应关系见 [`docs/audit_response.md`](docs/audit_response.md)；每个决策背后
的推理见 [`docs/adr/`](docs/adr/)。

> **关于编号。** 审计报告用的编号是 `A01`–`A07`；对抗性测试用的稳定标识是
> `F1`–`F10`。两者的映射写在 `tests/audit/test_audit_findings.py` 的模块
> docstring 与 `docs/audit_response.md` 里。

---

## 3. 来源分级：承重的设计

每个 `Config` 字段都必须声明**这个数字从哪来**，而且等级跟着值一起走：

```python
>>> from adi_model import Config, SourceGrade, annotate_config, audit_provenance
>>> ann = annotate_config(Config.paper_consistent())
>>> ann["g0"]
Graded(32.0, [披露], '[00_1] figure annotation: G0 = 32', unit='')
>>> ann["mismatch_sigma0"].require(SourceGrade.DISCLOSED)
Traceback (most recent call last):
    ...
adi_model.provenance.GradingError: value graded FITTED ([拟合]) from
'100 ppm behavioural calibration; NOT a PDK value' is not valid here; ...
```

| 等级 | 含义 | 能否当作事实引用 |
|:---|:---|:--:|
| `DISCLOSED` `[披露]` | 从论文 / 幻灯片原文转录 | ✅ |
| `DERIVED` `[推导]` | 对已披露值做代数推导，无自由参数 | ✅ |
| `FITTED` `[拟合]` | 为复现已发表图表而标定——**是锚点，不是预测** | ❌ |
| `ASSUMED` `[假设]` | 无公开来源，仅用于敏感性研究 | ❌ |
| `RESEARCH_EXTENSION` `[研究扩展]` | 本仓库原创，不属于被建模的文献 | ❌ |

`audit_provenance(cfg)` 是评审者应该读的第一个东西——它列出了一个头条数字
究竟踩在哪些活假设上：

```python
>>> rep = audit_provenance(Config())
>>> rep["verdict"]
'OK: no ungraded parameters'
>>> rep["counts"]
{'disclosed': 11, 'derived': 9, 'assumed': 57, 'fitted': 3, 'research_extension': 8}
```

新增 `Config` 字段但**没有**分级 → 测试套件直接失败；分级表里留着一个已经
不存在的字段 → 同样失败。机制就是这么朴素：它把"某个人忘了标注一个参数"
从**发表后的勘误**变成了**一次红构建**。

---

## 4. 安装与上手

需要 Python ≥ 3.10。

```bash
git clone https://github.com/defineiocc02/20bit_SAR_ADC_Behaviour_Verification.git
cd 20bit_SAR_ADC_Behaviour_Verification

python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
```

### 六十秒跑出第一个数

```python
from adi_model import Config, sine_input, run_sim_split, sine_fit_metrics

cfg = Config(dac_arch="split", dem_enable=True, dither_mode="sampling")
res = run_sim_split(cfg, sine_input(0.9 * cfg.v_fs, 2.5e6), 2**15)
print(round(sine_fit_metrics(res.out, cfg.fs, 2.5e6)["SNDR_dB"], 2))
# -> 91.98   （单次运行、固定种子；不构成对已发表芯片的任何声称）
```

### 全量验收扫描

```bash
adi-run-all        # 24 个 stage -> tools/results/results.json + 7 张图
adi-make-report    # 把 results.json 与图打包成一个自包含 HTML 报告
```

或者不用控制台脚本：

```bash
PYTHONPATH=src python tools/run_all.py
```

设置 `ADI_MODEL_RESULTS_DIR`（或传 `--results-dir`）可以把输出写到
`tools/results/` 以外的地方。

---

## 5. 测试与四道门禁

```bash
pytest                  # 189 个测试，约 22 秒，不含长扫描
pytest -m audit         # 只跑审计衍生的对抗性回归
pytest --cov=adi_model  # 分支覆盖率，下限 35%
```

| 门禁 | 命令 | 7.0.0 之前 | 现在 |
|:---|:---|--:|--:|
| Lint | `ruff check .` | **6797** 个错误 | **0** |
| Format | `ruff format --check .` | 38 个文件里 37 个 | **0** |
| Types | `mypy --config-file=pyproject.toml` | **147** 个错误 | **0** |
| Tests | `pytest` | — | **189 passed, 4 xfailed** |

lint 与 type 两道门禁此前是**配好了但永远跑不通**，这跟没有门禁是一回事。
它们被记为 [`docs/audit_response.md`](docs/audit_response.md) 里的 **C3**、**C4**
两项，与两个 CI 缺陷并列（**C1**：CI 用了 CLI 根本没定义的参数；**C2**：那个
"全量扫描"实际收集到 0 个测试）。修这些门禁所付出的改动**没有改变任何一个
数字**：`tools/results/results.json` 前后**逐字节相同**。

---

## 6. 本模型不支持什么

引用任何数字之前请先读 [`docs/model_scope.md`](docs/model_scope.md)。简述：

- **不做 20 bit 码域声称。** `n_bits_target` 只用来定义 LSB 参考；模型输出是
  **模拟电压当量**，不是 20 bit 编码器输出。有一条测试专门断言"改
  `n_bits_target` 不会改变任何一个输出样本"，就是为了让这条边界一直可见。
- **不做良率预测。** 单位失配 sigma 要么是 100 ppm 的行为标定（`FITTED`），
  要么是 Pelgrom 面积律估计（`ASSUMED`），**都不是** PDK 实测。蒙特卡洛结果
  只是敏感性研究。
- **不做面积 / 功耗声称。** 缩电容研究缩放的是电容值，不是版图。
- **1/f 转折频率低于主记录分辨率。** 默认 `fs`/`N` 下 FFT _bin ≈ 1.2 kHz，
  40 Hz 转折分辨不出来；闪烁噪声发生器是在低 `fs` 下单独验证的，主记录里
  **不含**闪烁噪声。
- **KTC 观测器是我们的想法，不是论文的。** 已建模、已分级、默认关闭，
  不构成对已发表芯片的任何证据。

---

## 7. 可复现性

- **没有全局随机状态。** 每个随机调用都显式接受一个 `numpy.random.Generator`；
  `tests/integration/test_pipeline_equivalence.py` 会扫描源码来守住这一点。
- **逐位可复现。** 同种子两次运行输出数组完全相同，两条信号链都做了断言；
  CI 会把全量扫描跑两遍并逐字节比对 `results.json`。
- **两条独立实现必须一致。** `sim_split.py` 与 `pipeline.py` 独立实现了同一条
  信号流，在关闭全部非理想因素时被断言**逐位一致**。它们曾经悄悄不一致了
  两个版本，原因是各自推导量化步长——见
  [ADR 0004](docs/adr/0004-single-source-of-truth-quantiser-grid.md)。

---

## 8. 仓库结构

```
20bit_SAR_ADC_Behaviour_Verification/
├── src/adi_model/             库本体（27 个模块）
│   ├── provenance.py          来源分级：SourceGrade / Graded / PARAM_GRADES
│   ├── config.py              全部参数，带分级与 validate()
│   ├── slice_pool.py          物理 18 slice 池，跨周期因果性
│   ├── sadc.py                唯一的量化栅格构造函数
│   ├── dac_arch.py            等权 unary vs 分段（主/子 + 桥接电容）DAC
│   ├── pipeline.py            相位精确信号链
│   ├── sim_split.py           独立的 split-DAC 链路
│   ├── experiments.py         24 个验收 stage，各自返回数据 + 判据
│   └── cli.py                 控制台入口
├── tests/
│   ├── unit/                  分级、slice 池、CLI 连线
│   ├── integration/           跨模块不变量（等价性、确定性）
│   ├── audit/                 每个审计缺陷一条测试——在 v6.1 上全部失败
│   └── regression/            "跑出来"才发现（而非读出来）的缺陷
├── tools/
│   ├── run_all.py             全量扫描 -> results.json + 图
│   └── make_report.py         自包含 HTML 报告
├── docs/
│   ├── model_scope.md         什么能声称、什么不能   <- 必读
│   ├── audit_response.md       逐条审计响应
│   ├── review_response_2026-09-11.md    外部复核逐条裁定（第二轮）
│   ├── review_response_2026-09-11b.md   外部复核逐条裁定（第三轮）
│   ├── review_response_2026-09-11c.md   外部复核逐条裁定（第四轮）
│   ├── STATUS.md              发布前的项目现状快照
│   └── adr/                   8 份架构决策记录（0001-0008）
├── CITATION.cff               机器可读的引用元数据
├── NOTICE                     第三方文献引用与归属声明
└── LICENSE                    BSD-3-Clause + 权利范围说明
```

---

## 9. 参与贡献

见 [`CONTRIBUTING.md`](CONTRIBUTING.md)。要点：`ruff`、`mypy`、`pytest` 必须
全绿；新增参数**必须**分级；新增机制**必须**默认关闭并分级为
`RESEARCH_EXTENSION`。

安全问题：见 [`SECURITY.md`](SECURITY.md)。
社区准则：见 [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md)。

---

## 10. 引用方式

如果这个模型对你的工作有帮助，请引用它——引用里记录了版本号，读者才能复现
你的数字。

```bibtex
@software{zhao_2026_sar_adc_behaviour_model,
  author    = {Zhao, Reed},
  title     = {20-bit SAR ADC Behavioural Verification Model},
  version   = {7.0.5},
  year      = {2026},
  publisher = {GitHub},
  url       = {https://github.com/defineiocc02/20bit_SAR_ADC_Behaviour_Verification},
  license   = {BSD-3-Clause},
  note      = { 独立的学术行为模型；与 Analog Devices, Inc. 无从属、
                无背书、无授权关系。 }
}
```

供 GitHub 的 *Cite this repository* 按钮使用的机器可读元数据在
[`CITATION.cff`](CITATION.cff)。

**请同时引用被建模的架构本身**（见[§11](#11-参考文献) 的 `[00]`）——本仓库是
对那项工作的研究，不替代它。

---

## 11. 参考文献

| 编号 | 文献 |
|:---|:---|
| `[00]` | R. Bodnar 等，"A 9.3 nV/√Hz 20 b 40 MS/s 94.2 dB DR Signal-Chain Friendly Precision SAR Converter"，**ISSCC 2024**，Session 9.8，pp. 182–183。DOI: `10.1109/ISSCC49657.2024.10454329` |
| `[00_1]` | 同一工作的幻灯片（按页引用图与标注） |
| `[09]`–`[14]` | 覆盖量化器/RDAC 分段、采样侧 dither、DEM 排序、交织、辅助输入电荷与低功耗参考的专利族 |

参数级引用写在 `provenance.PARAM_GRADES` 里，一个参数一行。完整的第三方文献
清单（含**不属于**目标芯片的背景架构）见 [`NOTICE`](NOTICE)。

---

## 12. 开源声明与许可

**代码许可：BSD-3-Clause，见 [`LICENSE`](LICENSE)。**
你可以自由使用、修改、再分发本软件（包括商业用途），只需遵守 BSD 的三项
条件：保留版权声明、在二进制分发中复现该声明、不得用作者名义为衍生品背书。

### 12.1 独立性与无从属关系

本仓库是一个**独立的学术行为模型**，与 **Analog Devices, Inc.** **无从属、
无背书、无赞助、无授权**关系。作者与 Analog Devices 无任何隶属关系。

"Analog Devices"、"ADI"、"LTC" 是 Analog Devices, Inc. 的商标。它们在本仓库
中的出现仅为**名义性使用**（nominal use），目的只是标识被研究的公开文献，
**不表示**该公司对本仓库的任何认可。

### 12.2 用了什么、没用什么

| | |
|:---|:---|
| ✅ 使用了 | **仅限已公开**文献：ISSCC 2024 论文与幻灯片、已公开专利、公开数据手册。全部在 [`NOTICE`](NOTICE) 中列明，且**均不在本仓库内再分发**。 |
| ❌ 未使用 | 无任何硅片、网表、版图、PDK、设计数据库、内部文档，也无任何形式的非公开信息。 |

每一个量要么是公开来源转录（`[披露]`），要么是对公开值做代数推导
（`[推导]`），要么是**为复现已发表图表而标定**（`[拟合]`），要么是供敏感性
研究而假设（`[假设]`）。反推得到的参数**不会**被当作对任何产品的独立测量
结果呈现。

### 12.3 无专利许可；无担保

本仓库中的任何内容都**不授予**任何第三方专利或其它知识产权项下的任何许可
（明示或默示）。`NOTICE` 中列出的专利仅作为**机制参考**引用；其中描述的
实施方案**不是**本仓库所发布的实现。

软件按 **"原样"（AS IS）** 提供，不附任何形式的担保；作者不对因使用本软件
而产生的任何索赔或损害负责。它是**研究模型**，**不是**设计签核工具：
**不得**用于量产、安全关键或良率决策。

### 12.4 原创贡献

**KTC 噪声消除支路**（`adi_model/ktc.py`）是本仓库作者的**原创研究扩展**。
它在代码中被分级为 `RESEARCH_EXTENSION`、默认关闭，并且**不是** `[00]` 或
专利 `[09]`–`[14]` 所披露的特性。**请勿将其归属于 Analog Devices。**

### 12.5 对本声明提出异议

如果你认为自己是某项内容的权利人，且认为本仓库存在归属错误或超出许可范围，
请提交
[机密安全公告](https://github.com/defineiocc02/20bit_SAR_ADC_Behaviour_Verification/security/advisories)
或参见 [`SECURITY.md`](SECURITY.md)。归属与许可类更正按**最高优先级**处理。
