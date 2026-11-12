# W0-F 基线复现与工具状态

> 目的：记录"当前两棵树到底能不能跑、跑出什么、哪些跑不了"。
> 所有命令都在本机实跑；未跑的一律标 `NOT_RUN`，不用"应该能跑"代替。
> 证据层级：L0 结构 / L1 行为 / L2 RTL / L3 后仿 / L4 PVT / L5 硅。本节只有 L0–L1，RTL 为 `NOT_RUN`。

---

## 0. 结论

| 树 | 能不能装 | 能不能跑 | 基线结果 | 主要缺口 |
|:---|:---:|:---:|:---|:---|
| `main @ bc9a16a` | ✅ `hatchling` + `adi-simulate`/`adi-validate-config` | ✅ CLI + 512 笔 simulate | **1584 passed / 3 failed / 47 errors** | Verilator 缺失（47 个 error + 1 failure）、shallow clone 缺历史对象、hatchling 未装 |
| `20b10mtask @ 750a2e0` | ❌ `pyproject.toml` 无 `[build-system]`/`[project]` | ✅ `PYTHONPATH=.:src pytest` | **220 passed / 1 failed** | `adctoolbox==0.9.1` 未装；Verilator/Icarus/Yosys 全缺，RTL `NOT_RUN` |

两棵树的"基线"都不是全绿，但原因不同：main 的 50 个非通过项全是**工具/历史**缺口；task 的 1 个失败是**钉死环境依赖**缺口。二者都不能写成"回归通过"。

---

## 1. main 基线

### 1.1 安装/入口

`pyproject.toml`（`bc9a16a`）有：

- `[build-system] requires = ["hatchling>=1.24"]`
- `[project] name = "sar-adc-behaviour-verification"`、`version = "9.0.0"`
- `[project.scripts] adi-validate-config = "adi_model.cli:main_validate_config"`、`adi-simulate = "adi_model.cli:main_simulate"`
- `[tool.hatch.build.targets.wheel] packages = ["src/adi_model"]`

本机未装 hatchling，所以**没有执行 `pip install`**；用 `PYTHONPATH=src` 运行源码入口，等价于可编辑安装的导入路径，但**不等于**验证了 wheel/sdist。`test_sdist_...` 失败正是缺 hatchling，属工具缺口。

### 1.2 CLI

```bash
cd <repo>
PYTHONPATH=src python3 -m adi_model --help
```

实际输出（节选）：

```
usage: python -m adi_model [-h] {validate-config,simulate} ...
  validate-config     只检查显式JSON配置
  simulate            实际运行工程默认或明确覆盖
```

`simulate --help` 暴露 `--amplitude/--frequency/--samples/--seed/--parameters/--config-overrides/--output`。

### 1.3 512 笔 simulate（真实运行）

```bash
PYTHONPATH=src python3 -m adi_model simulate --samples 512 --output /tmp/w0/main_sim
```

结果：`status=SIMULATION_COMPLETE`，`result_role=UNCALIBRATED_NOMINAL_20BIT`，`source_reproduction=false`，`n_samples=512`，`n_codes=512`，`raw_invalid=0`，`final_clip_low/high=0`。

输出文件：`codes.npz`、`metadata.json`、`assumptions.json`（共约 40 KB）。

关键推断（`assumptions.json` / `metadata.json`）：

| 项 | 值 | 等级 | 说明 |
|:---|:---|:---|:---|
| `fs_hz` | 40 MHz | DISCLOSED | ISSCC2024 锚点 |
| `input_differential_peak_v` | 3.0 V | DISCLOSED | ±3 V，6 Vpp |
| `stage1_bits` | 9 | DISCLOSED | 论文 “9b quantization” |
| `output_bits` | 20 | DISCLOSED | 输出码宽 |
| `n_slices / n_active` | 18 / 8 | DISCLOSED | 18 池、8 活跃 |
| `active_capacitance_anchor_f` | 20.5 pF | DISCLOSED_ANCHOR_WITH_NODE_ASSUMPTION | 非提取输入网络 |
| `n_main / n_sub` | 63 / 8 | ASSUMED_GEOMETRY | 显式 512 码分段实现 |
| `unit_cap_f` | 40.039 fF | DERIVED_WITH_ASSUMPTION | 20.5pF/[8×(63+8/8)] |
| `bridge_cap_f` | 45.759 fF | DERIVED_WITH_ASSUMPTION | 8Cu/7，零 sub 寄生 |
| `feedback_cap_f` | 640.625 fF | DERIVED_WITH_ASSUMPTION | 20.5pF/32 |
| `fixed_point` | Q30/Q32/64/48/96/20 | ENGINEERING_ARITHMETIC_CHOICE | 非论文格式 |

`enabled` 标志（默认）：

```
sampling_noise=true, driver_noise=false, sadc_response_approximation=false,
ra_output_noise=true, unit_mismatch=true, bridge_mismatch=true,
slice_resource_randomization=true, basic_segmented_decoder=true,
unit_dem=false, source_dither=false, feedback_mismatch=false
```

`unmodeled` 明确列出：完整 source lower/upper 电容开关网络、source 3D unit DEM、输入 RC/chop、物理片时序/带宽/失调、RA/ADC2 非线性、参考动态、FGA/CBIAS 噪声、桥/复位采样噪声协方差、PDK/版图/signoff。

> 结论：main 的默认运行是 **L1 名义诊断**，不是"ADI 复现完成"；它自己把 `source_reproduction=false`、`precision_claim=code width only` 写在输出里。

### 1.4 pytest

```bash
PYTHONPATH=src python3 -m pytest -q
```

实际：**1584 passed, 3 failed, 47 errors in 45.73s**。

3 个 failure 的原因：

| 测试 | 原因 | 归类 |
|:---|:---|:---|
| `tests/integration/test_mutation_twoneck.py::test_actual_rtl_integer_math_detects_both_declared_arithmetic_mutants` | `FAIL: simulator not found; configure VERILATOR` | 工具缺失 |
| `tests/unit/test_review_trace.py::test_reviewed_production_file_inventory_includes_baseline_and_extension` | `git ls-tree d1e1f87...` 返回 128；本克隆 `git rev-list --count HEAD = 1` | shallow clone 缺历史 |
| `tests/unit/test_robustness_2026_09_25.py::test_sdist_excludes_third_party_originals_and_keeps_real_public_assets` | `ModuleNotFoundError: hatchling` | 工具缺失 |

47 个 error 全部来自 `tests/integration/test_source_bound_replay.py` 的 `actual_capture` fixture，根因同一句：`FAIL: simulator not found; configure VERILATOR`。

> 结论：main 的回归**不是代码失败**，是环境不完整；但在 W1 前必须补齐 Verilator + 完整 Git 历史 + hatchling，否则无法把"迁移不改变行为"作为 L1/L2 门禁。

---

## 2. 20b10mtask @ 750a2e0 基线

### 2.1 重要版本差异

| 项 | 值 |
|:---|:---|
| 用户给的基准 | `750a2e0bf747d2755b070fc7549af8448531473a`（2026-11-09） |
| 本地分支 `20b10mtask` | `5094b2e156a7660c85d3bf02bcd6d5a13786377e`（2026-11-07） |
| 关系 | 远端比本地**多 49 个 commit**，`ahead=49, behind=0`；本地缺 `750a2e0` 对象 |
| 处理 | 不更新本地分支；用 GitHub API 按 blob 稀疏导出 `750a2e0` 的 474 个文本文件到 `/tmp/w0/task`（12.7 MB），二进制证据按需补 |

新增的 49 个 commit 包含 M0–M6：源文台账、基线指纹、`noise.py`、前端噪声注入、参考动态/RA 非线性、时偏/带宽、码口径统一。**不能用 `5094b2e` 的旧结论代替 `750a2e0`。**

### 2.2 包与入口

`pyproject.toml`（`750a2e0`）只有：

- `[tool.pytest.ini_options]`（testpaths 收 `src/tests`、`task1/tests`、`task2/tests`、`task2/eight/tests`、`task3/tests`、`task_integration/tests`、`task_reconstruction/tests`；`pythonpath=[".","src"]`）
- `[tool.ruff]`

**没有 `[build-system]`、没有 `[project]`、没有 `[project.scripts]`。** 因此 task 树本身**不可安装**；`src/adi_model/__init__.py` 只有 18 行、无 `__all__`/导出。

### 2.3 pytest

```bash
cd /tmp/w0/task
PYTHONPATH=.:src python3 -m pytest src/tests task1/tests task2/tests task2/eight/tests \
  task3/tests task_integration/tests task_reconstruction/tests -q
```

首次：**218 passed, 3 failed in 7.37s**。

3 个失败中，2 个是稀疏导出缺 `.npz`（不是树缺陷），1 个是环境：

| 测试 | 原因 | 处理 |
|:---|:---|:---|
| `task2/tests/test_fig19_seven.py::test_fractional_word_alone_reproduces_archived_ideal_experiment` | 缺 `task2/evidence/fig19_seven/fig19_seven_data.npz` | 从 API 补 518 KB，**复跑通过** |
| `task2/eight/tests/test_independent_charge.py::test_independent_charge_oracle` | 缺 `task1/evidence/bridge_lms/sigma_2pct/3pct_adc2_100mv/observations.npz` | 补 2.45 MB×2，**复跑通过** |
| `task2/eight/tests/test_baseline_frozen.py::test_baseline_fingerprint_is_unchanged` | `ModuleNotFoundError: adctoolbox`；测试提示 `uv run --python 3.12 --with numpy==2.5.3 --with matplotlib --with adctoolbox==0.9.1` | **未通过**（环境钉死） |

修正后：两个缺证据项单独复跑通过（218 + 2 = **220 passed**）；剩余 1 个 `baseline_frozen` 仍因缺 `adctoolbox` 失败。**全量 220 未重跑**，此处按"220 passed / 1 failed（env）"记录，不声称已全量复跑。

### 2.4 RTL/物理

`trace/digital_status.json`（2026-11-08）明确：

```
modules=11, testbenches=11, tasks=5
gaps: 本机无 verilator / iverilog / yosys，RTL 回归无法在此环境执行
tools: verilator.found=false, iverilog.found=false, yosys.found=false
```

因此 task 的 RTL/DRC/LVS/STA 结果**本次未重跑**；只能引用已归档的 receipt（GDS/DRC/LVS/STA），并按 L2/L3 证据等级标注，不升级为本次运行。

---

## 3. 环境清单

| 工具 | 状态 | 影响 |
|:---|:---:|:---|
| Python | 3.13.12 | task 的冻结指纹要 3.12 + numpy 2.5.3；当前不匹配 |
| numpy/scipy/matplotlib | 已装 | main/task Python 测试可跑 |
| pytest | 已装 | 同上 |
| hatchling | **未装** | main 的 sdist 测试失败；wheel 未验证 |
| Verilator | **未装** | main 47 error + 1 failure；task RTL 全不能跑 |
| Icarus/iverilog | **未装** | 同上 |
| Yosys | **未装** | 无综合验证 |
| git 历史 | **shallow（depth=1）** | main 的 `test_review_trace` 失败；不能做历史对比 |
| `adctoolbox` | **未装** | task `baseline_frozen` 失败 |

> 本节所有"未通过"都不应被后续报告写成"代码回归失败"，也不能被忽略；W1 的验收必须先在补齐工具的环境中重跑同一命令。

---

## 4. 复现命令汇总

```bash
# main
cd <repo>
PYTHONPATH=src python3 -m adi_model --help
PYTHONPATH=src python3 -m adi_model simulate --samples 512 --output /tmp/w0/main_sim
PYTHONPATH=src python3 -m pytest -q

# task (sparse export)
cd /tmp/w0/task
PYTHONPATH=.:src python3 -m pytest src/tests task1/tests task2/tests task2/eight/tests \
  task3/tests task_integration/tests task_reconstruction/tests -q

# 钉死环境（未执行）
uv run --python 3.12 --with numpy==2.5.3 --with matplotlib --with adctoolbox==0.9.1 \
  python tools/snapshot_baseline.py --check
```
