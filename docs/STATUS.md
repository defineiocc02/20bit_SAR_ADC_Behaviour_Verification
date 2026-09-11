# STATUS — 项目现状（v7.0.0 快照；v7.0.1 / v7.0.2 增量见 CHANGELOG）

> **读前须知（2026-09-11 追加）**：本文是 **v7.0.0 发布时**的现状快照，其中的
> 测试条数等数字**未随 v7.0.1 / v7.0.2 更新**。增量（v7.0.1：5 条审计修复 +
> 3 条 `xfail(strict=True)` 跟踪的未闭合项 + 2 处文档不实声明订正；v7.0.2：门禁
> 覆盖配置自检、覆盖值不再通过 `require`、非法配置在入口被拒绝）见
> `CHANGELOG.md`、`docs/review_response_2026-09-11.md` 与
> `docs/review_response_2026-09-11b.md`。
> 当前实测：`pytest` **167 passed, 3 xfailed**（含 7 条 docstring 示例，已开启
> `--doctest-modules`）；`ruff` / `format` / `mypy` 各 0；
> `adi-run-all` 退出码 0（打印 1 项已登记的已知限制）。

> 本文是**发布前的现状快照**，回答三件事：现在有什么、哪些是硬的、哪些还是债。
> 与 `README.md`（对外介绍）、`CHANGELOG.md`（变更历史）、`docs/audit_response.md`
> （审计逐条响应）、`docs/model_scope.md`（适用域边界）配合使用。
>
> **一句话**：这是一个**为被审计而写**的 SAR ADC 行为模型——每个数字都带机器可查的
> 来源分级，四条 CI 门禁全部真实生效，134 条测试中有相当一部分是"抓住回退"的对抗性回归。

---

## 1. 规模

| 项目 | 数量 | 备注 |
|:---|---:|:---|
| 源码 | 13 313 行 / 27 个模块 | `src/adi_model/` |
| 测试 | 1 876 行 / **134 条** | `tests/`，含 unit / audit / regression / golden |
| 工具 | 1 708 行 | `tools/run_all.py`、`tools/make_report.py` |
| 文档 | 1 585 行 / 10 篇 | 含 8 篇 ADR + 审计响应 + 适用域声明 |
| 实验阶段 | **24 个** (`stage1`–`stage24`) | `src/adi_model/experiments.py`，"可执行论文" |
| 结果产物 | `tools/results/results.json` + 7 张 PNG | 随仓库发布，便于对照 |

---

## 2. 四条门禁（全部真实生效）

| 门禁 | 命令 | 状态 | 本轮修复 |
|:---|:---|:---:|:---|
| Lint | `ruff check .` | ✅ 0 | 6 797 → 0（6251 条为中日韩标点误报，已按规则关闭并注明原因；余 546 条为真实问题，逐一修） |
| Format | `ruff format --check .` | ✅ 0 | 全库统一 |
| Type | `mypy` | ✅ 0 | 147 → 0（`object=None` 哨兵改为 `float \| None`；`SplitDacLike` 结构化协议破除循环导入） |
| Test | `pytest -q` | ✅ **134 passed** | 覆盖 3.10–3.13 矩阵 |
| 可复现性 | `adi-run-all` | ✅ 25/25 验收 | `--results-dir` 与 CLI 对齐，`pytest -m slow` 收集 0 条的历史问题已修 |

语句覆盖 **50 %**（3 729 语句 / 1 806 未覆盖；`fail_under = 35`）。
未覆盖部分集中在 `experiments.py` 的长尾 stage 分支与绘图代码。

---

## 3. 审计收口（v6.1 外部审计）

`docs/audit_response.md` 逐条响应，全部有对应回归测试守护：

| 系列 | 内容 | 状态 |
|:---|:---|:---|
| F1–F10 | 结构性与数值性缺陷（第一级分辨率误读、slice 因果性、DA 栅格单一真源等） | ✅ 已修 + 测试守护 |
| A07.3 | skew 用 `np.gradient` → 近 Nyquist 低估 **25.6 dB**；改为解析导数 | ✅ 已修 + **静态守卫**（禁止 `np.gradient` 回到信号路径） |
| A07.4 | 直方图从 mean/sigma 重采样（伪造分布）；改用真实逐芯片 SNDR | ✅ 已修 |
| A07.6 | 驱动噪声进参考口径但不进整链误差（1 mV 读成 0.99 µV）；新增 `err_vs_clean` | ✅ 已修 + 三套误差口径分离 |
| A06 | 共享 RA 上的时变参考 | ⛔ **明确不实现**，见 `model_scope.md` §4 |
| C1–C4 | CI 门禁本身失效（`--out` vs `--results-dir`、`pytest -m slow` 收集 0、ruff 全红、mypy 全红） | ✅ 已修 + `tests/unit/test_cli.py` 静态校验 CI 与 CLI 一致 |

---

## 4. 来源分级（本项目的承重墙）

每个参数都带机器可查的等级，可运行期断言（`require()`）：

| 等级 | 含义 | 使用边界 |
|:---|:---|:---|
| `[披露]` disclosed | 论文 / PPT 原文给出 | 可写进论文对比表 |
| `[拟合]` fitted | 为复现基准而标定 | 只证明"模型能重现某类现象" |
| `[假设]` assumed | 工艺估算（Pelgrom 等） | **不得**用于尺寸 / 功耗 / 良率结论 |
| `[派生]` derived | 由上述量算出 | 随上游等级继承 |
| `[研究扩展]` research_extension | 本仓库原创 | **不得**归属于 Analog Devices（见 `NOTICE`） |

88 项 `PARAM_GRADES` 登记 + `audit_provenance()` 一键体检。

---

## 5. 注释工业化（本轮完成）

| 指标 | 之前 | 现在 |
|:---|---:|---:|
| 缺 docstring 的 API 节点 | 41 | **0** |
| 缺 `Args`/`Returns` 的节点 | 184 | **0** |
| 受检 API 节点总数 | 336 | 336 |

规范：一行摘要（含口径与单位）→ `Args:`（含义 + **单位** + 来源分级）→ `Returns:`
（结构 + 单位）→ 必要时的 `Raises:` / `Side effects:` / `Notes:`（公式、近似层级、文献编号）。
`experiments.py` 的每个 stage 另需"验证什么 / 怎么验证 / 判据 / 返回键说明"四件套。

**零数值改动验证**：注释收口前后各跑一次 `tools/run_all.py`，
`results.json` **SHA256 完全相同**
（`fb77298d…42bfd3c`，93 751 字节）。

---

## 6. 已知技术债（诚实清单）

| # | 债务 | 影响 | 计划 |
|:-:|:---|:---|:---|
| D1 | **输出 schema 漂移无 golden 测试守护** | 重命名内部变量曾误伤 JSON 输出键，靠人工字节比对才发现 | 加 `tests/golden/test_results_schema.py`，锁键名与单位 |
| D2 | 覆盖率 50 %，长尾 stage 分支与绘图未覆盖 | 回归风险集中在 `experiments.py` | 优先补 stage 判据的断言型测试 |
| D3 | `dither_alpha` 仅覆盖**信号电荷**一份；输入负载与噪声传递系数尚未由同一份掩码生成 | split 拓扑换 D 值后偏差会放大 | 下一版逐相位推导（已在 docstring 中标注） |
| D4 | `experiments.py` 关闭了 `D103` 与部分 mypy 检查 | 该文件仍是最薄弱环节 | 注释已补齐；类型豁免待逐 stage 收敛 |
| D5 | 未实现 A06（时变参考下的共享 RA） | 无法解释该器件如何达到其披露精度、50/69 mW 功耗 | 明确列为越界，见 `model_scope.md` §4 |

---

## 7. 开源就绪度

| 项目 | 状态 |
|:---|:---|
| `LICENSE`（BSD-3-Clause + 第三方 IP 声明） | ✅ |
| `NOTICE`（ADI 商标与"研究扩展"归属声明） | ✅ |
| `CITATION.cff`（含 ISSCC 2024 DOI 与"不是本仓库 DOI"的显式说明） | ✅ |
| `README.md`（中文，默认首页）/ `README_EN.md`（English） | ✅ |
| `CONTRIBUTING.md` / `CODE_OF_CONDUCT.md` / `SECURITY.md` | ✅ |
| Issue / PR 模板（含**来源分级**必答项） | ✅ |
| `.pre-commit-config.yaml` / GitHub Actions CI | ✅ |
| 占位符（`<owner>`）与绝对路径泄漏扫描 | ✅ 0 命中 |
| sdist / wheel 构建 | ✅ `python -m build` |
| 发布包 + SHA256 校验 | ✅ |

---

## 8. 复现本状态

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

ruff check .            # 0
ruff format --check .   # 0
mypy                    # 0
pytest -q               # 134 passed
adi-run-all             # 25/25 验收，约 40 s
```
