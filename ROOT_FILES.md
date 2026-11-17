# 根目录文件清单（说明每个文件是什么）

更新：2026-11-17　基线：`e9dfdbe`

**本文档解决「根目录这些文件分别干什么、哪个是现行的」。**

## 一、仓库标准文件（不要动）

| 文件 | 作用 |
|:---|:---|
| `LICENSE` | 许可证正文 |
| `NOTICE` | 第三方声明 |
| `CITATION.cff` | 引用格式（Citation File Format）|
| `CODE_OF_CONDUCT.md` | 行为准则 |
| `CONTRIBUTING.md` | 贡献指南 |
| `SECURITY.md` | 安全策略 |
| `CHANGELOG.md` | 变更记录（91 KB，历史累积）|

## 二、构建与配置

| 文件 | 作用 |
|:---|:---|
| `pyproject.toml` | Python 打包与工具配置 |
| `requirements.txt` | 运行依赖 |
| `MANIFEST.in` | sdist 打包文件清单 |
| `Makefile` | 常用命令入口 |
| `.gitignore` / `.gitattributes` | git 配置 |
| `.pre-commit-config.yaml` | pre-commit 钩子 |
| `.github/` | CI 工作流 |

## 三、入口与索引（**读这三个**）

| 顺序 | 文件 | 作用 |
|:--:|:---|:---|
| 1 | **`README.md`** | 交付什么、怎么跑、目录结构 |
| 2 | **`PROJECT_MAP.md`** | 哪个文件是现行的、哪个是历史的；D1–D6 到文件的映射 |
| 3 | **`docs/STATUS.md`** | **唯一权威状态表**（能/不能复现、不能声称什么、作废数字清单）|

辅助索引：

| 文件 | 作用 |
|:---|:---|
| `docs/INDEX.md` | docs 目录总索引（124 份现行 / 115 份历史）|
| `rtl/README.md` | **四个顶层的关系**（最容易搞错的地方）|
| `docs/audit/README.md` | 审计与后端报告索引 |
| `docs/audit/todo/README.md` | 6 项待办（全部未开始、逐项需授权）|

## 四、目录

| 目录 | 文件数 | 内容 |
|:---|--:|:---|
| `rtl/` | 47 | **四个顶层 + 各层模块**（无废弃可删文件，见 `rtl/README.md`）|
| `tb/` | 69 | 测试台 |
| `tools/` | 65 py + 脚本 | 验证脚本、向量生成、绘图（`_legacy/` 2 个）|
| `src/adi_model/` | 88 | Python 行为模型 |
| `flow/` | 767 | 流程脚本与单元测试 |
| `docs/` | 124 现行 + 115 历史 | 文档 |
| `formal/` | 2 | `v21/run_fv.tcl`、`v21/recon_rnd_equiv.sv` |
| `manifest/` | 2 | 机制清单、来源清单 |
| `subsys/` | 78 | 子系统；含 `archive/180nm/`（**明确标注已归档**的 SMIC 180nm 历史工程）|
| `references/` | — | **论文/PPT/专利原件**（公开仓库不应含第三方版权材料，见 `.gitignore`）|

## 五、已清理的（本轮）

| 项 | 原因 |
|:---|:---|
| `.mypy_cache/`（21 MB）| 本地缓存，未被 git 跟踪，已在 `.gitignore` |
| `.pytest_cache/`（252 KB）| 同上 |
| `.ruff_cache/`（416 KB）| 同上 |
| 20 个 `__pycache__/`（约 2 MB）| 同上 |

**共清出约 23 MB，git 状态未变（这些文件从未入库）。**

## 六、`subsys/archive/180nm/` 为什么保留

它是 **SMIC 180nm 的历史工程**，README 明确写着「**状态：已归档，不再作为当前目标工艺**」，
并记录了切换原因与当时的实测数据（重构块 166,799 µm² / 4,940 cells、25 ns slack +0.002 ns）。

**这是有据可查的工艺选型证据，不是垃圾。** 保留原位。
