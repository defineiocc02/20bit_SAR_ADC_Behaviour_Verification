# 工作树与文件归属

本页用于定位文件和判断是否适合搬动。项目结果在[状态表](../STATUS.md)，规格在[工程合同](../CONTRACT_CURRENT.md)，首次使用从[根 README](../../README.md)进入。

| 路径 | 目录职责与典型文件 |
| --- | --- |
| [`src/adi_model/`](../../src/adi_model/) | Python 行为模型、几何、校准、整数解码和 CLI。`defaults.py` 实例化默认模型，公开 profile 位于 `contracts/profiles.py`。 |
| [`rtl/`](../../rtl/README.md) 与 [`tb/`](../../tb/) | v21、独立核心和历史兼容顶层按入口分开。`tb/` 下的 `.mem`、`.txt` 可能是 TB 逐项读取的冻结向量。 |
| [`flow/`](../../flow/)（包括 [`formal/`](../../flow/formal/)） | 仿真、验证、综合和属性脚本。`flow/build/filelists/` 中的 `.f` 由编译入口读取。`.sdc`、`.upf`、`.lef` 分别服务于约束、电源意图和物理工具。 |
| [`tools/`](../../tools/) | Python 入口、对拍工具、向量和制图生成器。`tools/_legacy/` 保存已退役工具，[`tools/results/`](../../tools/results/README.md)保存旧结果和旧图。 |
| [`subsys/`](../../subsys/) | DSP 子系统、CAAZ 扩展以及实验配置。`subsys/experiments/configs/matrix.py` 是消融和效果图的运行输入。 |
| [`docs/`](../INDEX.md) 与 [`references/`](../../references/README.md) | 合同、设计依据、专题、存证、归档和维护者选定的原件。看的图在 [`docs/figures/20261117/`](../figures/20261117/README.md)，图与生成输入由清单绑定。 |
| [`flow/verification/manifests/`](../../flow/verification/manifests/) | 版本化的机制与来源清单，供源码、验证与披露范围追踪。 |
| 根目录、[`.github/`](../../.github/) | 根层 `pyproject.toml` 提供 Hatchling 构建及 CLI，`README.md` 提供项目入口，两个点文件承载 Git 规则。CI、维护与安全说明、pre-commit 配置位于 `.github/`。 |
| [工具依赖](../../tools/requirements.txt)、[验收命令](../../flow/Makefile)与[版本记录](CHANGELOG.md) | 按运行工具、验证流程和文档元信息分别维护。许可、第三方声明和引用文件保留在 [LICENSE](LICENSE)、[NOTICE](NOTICE)、[CITATION.cff](CITATION.cff)。 |

## 几类容易误删的文件

| 文件 | 为什么保留 |
| --- | --- |
| `flow/sim/fixtures/` 中的 `.sv/.hex/.json` | 明确绑定到仿真用例的源码、参数及权重输入。同名 binding 模块由各自的 `binding.json` 配套选择。 |
| `flow/tests/fixtures/` 中的 `.rpt` | 测试门禁读取的归档诊断输入，和开发机生成的综合报告分开存放。 |
| `flow/verification/reports/dumps/` 中的 `.out` | 既有候选验证的文本输出。部分故障注入用例将其中的 dump 作为输入。 |
| [`docs/audit/artifacts/`](../audit/artifacts/README.md) 中的 `.svf/.v/.txt` | 冻结的单模块等价、网表和综合记录。 |
| `docs/verification/vcs_source_control_trace/` 中的 `.csv` 与 `docs/reviews/20261105-*.json` | 带运行身份的证据。需要连同同目录说明、配置身份和边界一起阅读。 |
| `docs/figures/` 的 `.png/.svg/.json` | 发布预览、可编辑矢量图和运行清单属于一套交付。造图命令应先写独立输出目录。 |

## 已归并的内部入口

| 旧位置 | 归属 |
| --- | --- |
| `formal/` · `manifest/` | 分别归入 [属性验证](../../flow/formal/)和[验证清单](../../flow/verification/manifests/)。 |
| 根级验收和依赖配置 | [flow/Makefile](../../flow/Makefile)负责数字验收，[工具依赖](../../tools/requirements.txt)配套 Python 运行入口。 |
| 根级社区、版本与法务文件 | [社区与协作说明](../../.github/)集中到 `.github/`，版本、许可和引用说明由 `docs/meta/` 管理。 |
| `MANIFEST.in` | [旧打包清单](../_history/restructure/MANIFEST.in)保留 setuptools 时代的规则，现用构建配置在 [pyproject.toml](../../pyproject.toml)。 |
| `docs/review/` | 独立专家复核与其他专题复核集中在 [docs/reviews/](../reviews/)。 |
| `docs/validation-environment.json` | 放入[当时的验证环境档案](../_history/archive/validation-environment.json)，随检查点阅读。 |
| `docs/audit/TREE_AUDIT_PROMPT.md` | [审查任务原件](../_history/restructure/TREE_AUDIT_PROMPT.md)，报告仍从审计索引读取。 |
| `subsys/profiles/` | 具名 profile 说明进入 [Python 合同](../../src/adi_model/contracts/README.md)，原来独立指针保留在历史区。 |
| `tools/run_review2_figure.py` | [旧制图原件](../../tools/_legacy/run_review2_figure.py)依赖早期几何和测试环境。当前图使用 [run_review_figures.py](../../tools/run_review_figures.py)。 |

## 历史与生成目录

- `docs/_history/`、`docs/audit/_superseded/` 是带版本范围的档案。`rtl/_legacy/` 和 `subsys/archive/` 仍包含被历史 testbench 或清单引用的 RTL，修改路径前要核对入口。
- 本机编译暂存、综合输出、Python 缓存和未发布的图进入 Git 忽略规则。用 `git ls-files --others --ignored --exclude-standard` 查本机残留。Git 跟踪的证据例外以相应 README 中的来源说明为准。
- 工具和文档路径迁移时，同时更新入口、构建清单和自动化的调用。README 维护目录自身的用途和入口，状态数字由相应证据页维护。

可用 `git ls-files` 查跟踪文件，用 `python3 tools/check_doc_counts.py` 直接清点当前文档范围。结构审查的基线与结论见 [2026-11-17 独立审查](../audit/TREE_AUDIT_REPORT.md)。

现行文档相对路径由 `tools/check_engineering.py` 的 C3 核验。C4 的旧图／矩阵配对与 C7 的行为指纹各有独立基线，现行图册用自己的 SHA-256 清单。归档同名数据须保留原运行身份。
