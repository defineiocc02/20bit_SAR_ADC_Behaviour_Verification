# W0 调查报告：main × 20b10mtask 合并前审查

> 基准：`main bc9a16a`（2026-11-06）；`20b10mtask 750a2e0`（2026-11-09，远端）。
> 本文是 **W0 交付**：只调查/只读/基线复现/方案设计，**没有改任何生产代码、没有建合并分支、没有推送**。
> 目录：`docs/reviews/20261112_w0/`（新增，未跟踪）。

---

## 0. 先说结论

1. **`20b10mtask` 不是 main 的平行树，而是它的后代 + 大删除**：远端 `750a2e0` 比本地 `5094b2e` 多 49 个 commit；相对 main `bc9a16a` 是 **+582 / −1851 / ~8**，删掉了 main 的 37 个 `src/adi_model` 模块（9792 行）、23 个 RTL、68 个测试、1565 个文档。**直接 `git merge` 会 fast-forward，把 main 删空。**
2. **合并基础必须是 main 的包/配置/主引擎/RTL**；`20b10mtask` 提供逐沿调度、具名 DEM/dither、独立电荷/定点/RTL 与证据框架，作为**验证层**迁入。
3. **task 树本身不可安装**：`pyproject.toml` 只有 pytest/ruff，没有 `[build-system]`/`[project]`；`src/adi_model/__init__.py` 只有 18 行、无导出。main 才是唯一可安装包。
4. **task 的强项是"证据工程"**：43 行源文台账、A/B/C/D 四组实验标准、LSB20 逐码 INL/DNL、Fig.11/Fig.21 DEM、八片 dither、Q28 重构、RTL/DRC/LVS/STA、负例如实保留。
5. **task 的台账只覆盖 US10505561B2 一个专利**；其余 5 个专利未入机器表；且 PAPER-06/07/08、SLIDE-05/09 仍标 MISSING，但 `reference.py`/`timing.py` 已在 M4/M5 落地——**台账滞后**。
6. **`task2/eight/model.py:31-34` 仍反向 import `task1/experiments`**（`SwitchState`/`Q32`/`MapperTable`），主模型依赖实验目录；这是 W2 的第一个结。
7. **CAAZ 在 main 和 task 都深度嵌入**：`__init__` 导出、RTL filelist、`sar_structural_ctrl`、`sar20_digital_core` 端口、tests、docs；task 的 task3/integration/reconstruction 也有。只移 `task3` 不能隔离。
8. **普通 AZ 不能拿 `conventional_az` 顶替**：main 的 `conventional_az` 含两点平均/降采样（N/2），不是 ADI 的每笔全速率 AZ；W1 必须新建 `ra_az.py`，再移 CAAZ。

---

## 1. 基线与工具（真实运行）

| 树 | 命令 | 结果 |
|:---|:---|:---|
| main | `PYTHONPATH=src python -m adi_model simulate --samples 512` | ✅ `SIMULATION_COMPLETE`，512 码，`source_reproduction=false` |
| main | `PYTHONPATH=src pytest -q` | **1584 passed / 3 failed / 47 errors**；50 个非通过全是 Verilator 缺失、shallow clone 缺 `d1e1f87`、hatchling 未装 |
| task | `PYTHONPATH=.:src pytest ...`（7 个目录） | 首次 218 passed / 3 failed；补 2 个 `.npz` 后两项单独复跑通过（218+2=220），剩 1 个 `adctoolbox` 环境失败；**全量未重跑** |
| task | RTL/DRC/LVS/STA | **NOT_RUN**；`trace/digital_status.json` 明确 verilator/iverilog/yosys 全缺 |

> 详细命令、输出与失败原因见 [`06_baseline.md`](06_baseline.md)。

---

## 2. 交付物

| 文件 | 内容 | 对应要求 |
|:---|:---|:---|
| [`01_structure.md`](01_structure.md) | 两棵树目录、入口链、模块状态、依赖方向、结构对照 | W0-A |
| [`02_migration.md`](02_migration.md) | main/task 逐模块处置（保留/迁移/适配/重建/隔离/归档）、顺序、验收 | W0-B |
| [`03_contracts.md`](03_contracts.md) | `TopologySpec/EventRecord/SampleContext/SwitchCommand/FrozenCalibration` 草案、单位、事件、定点、宏接口 | W0-C |
| [`04_workpackages.md`](04_workpackages.md) | W0–W8 工作包、依赖、负责人、文件边界、验收、回退、风险登记 | W0-D |
| [`05_coverage.md`](05_coverage.md) | 论文/PPT/6 专利覆盖矩阵、台账缺口、profile、完成度口径 | W0-E |
| [`06_baseline.md`](06_baseline.md) | 真实运行命令/结果、工具清单、失败原因 | W0-F |
| [`07_caaz_isolation.md`](07_caaz_isolation.md) | CAAZ 逐文件/逐端口清单、普通 AZ 重建、隔离验收 | CAAZ 专项 |
| [`08_known_issues.md`](08_known_issues.md) | 8 条已知问题 + 2 条新增（台账滞后/工具） | 负例登记 |

---

## 3. 关键修正（相对上一版方案）

上一版 `docs/reviews/20261112_merge_plan.md`（未跟踪）基于本地 `5094b2e`。本版修正：

1. **版本变了**：远端 `750a2e0` 比本地多 49 个 commit，新增 M0–M6（源文台账、基线指纹、noise、前端噪声、参考动态/RA 非线性、时偏/带宽、码口径）。不能用旧结论。
2. **task 已经不是"纯任务树"**：`src/adi_model` 有 12 个共享内核（`codes/capacitors/noise/reference/reference_mux/timing/transients/impairments/charge_ref/metrics/hashing`），但 `__init__` 仍是空壳、`pyproject` 仍不可安装。
3. **台账是 M0 的**：PAPER-06/07/08 等仍 MISSING，但 M4/M5 代码已存在；W1 必须先重审台账，不能直接拿它当覆盖矩阵。
4. **失败数不是"全绿"**：main 50 个非通过、task 1 个环境失败；必须如实写。
5. **CAAZ 清单更具体**：给出 `__init__` 25–29/224–227、`rtl_sources.f` 2–3、`sar_structural_ctrl` 8/12/29–30/107、`sar20_digital_core` 60–69、task integration 43/57–60/80/85/99–101 等行号。

---

## 4. 没有做什么

- 没有改 `src/`、`rtl/`、`tests/`、`task*/`、`references/`。
- 没有建 `merge/*` 分支、没有 merge、没有 push。
- 没有把 task 的 956 MB 全树拉下来；只稀疏导出 474 个文本文件（12.7 MB），二进制证据按需补了 3 个 `.npz`（约 5.4 MB）。
- 没有装 Verilator/Icarus/Yosys/hatchling/adctoolbox；RTL/DRC/LVS/STA 未重跑。
- 没有把"220 passed"或"512 码"写成 ADI 复现完成。

---

## 5. 等待确认的决策点

1. **合并基础**：是否确认"main 包/主引擎/RTL 为基，task 为验证层"？
2. **CAAZ 隔离**：是否确认 `extensions/caaz/` 目录 + 主包零依赖 + 普通 AZ 新建 `ra_az.py`？
3. **profile**：是否确认 `adi_isscc2024_40m_6vpp` / `engineering_10m_10vpp` / `patent_examples` 三值，且每次运行强制落 `topology_id`？
4. **W1 范围**：是否先做"合同骨架 + CAAZ 隔离 + 普通 AZ"，暂不迁 DEM/dither？
5. **工具**：是否允许在 W1 前安装 Verilator/Icarus/Yosys 与 `hatchling`/`adctoolbox`，以恢复可签核基线？

确认后按 `04_workpackages.md` 的 W1 连续执行；未确认前不动生产代码。
