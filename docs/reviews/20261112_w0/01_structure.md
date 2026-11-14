# W0-A 分支结构、入口与依赖对照

> 基准：`main bc9a16a`（2026-11-06）；`20b10mtask 750a2e0`（2026-11-09，远端）。
> 本地 `20b10mtask` 仍是 `5094b2e`，比远端少 49 个 commit；本文所有 task 结论以 `750a2e0` 的稀疏导出为准。
> 证据层级：L0 结构 + L1 可运行入口；RTL 未重跑（工具缺失）。

---

## 1. 结论

1. **main 是可安装的工程骨架**：`hatchling` + 两个 console script + `src/adi_model` 39 模块 + `rtl/` 23 + `tests/` 68 + `docs/` 1565。
2. **20b10mtask 是任务交付/验证树**：`src/adi_model` 只剩 12 个共享内核（`__init__.py` 18 行、无导出），主体在 `task1/2/3`、`task_integration`、`task_reconstruction`；`pyproject.toml` **没有 `[build-system]`/`[project]`**，本身不可安装。
3. **两棵树不是"平行两套"**：`20b10mtask` 是 `main` 的后代，且删除了 main 的 1851 个文件；任何直接 `git merge` 都会 fast-forward 成 task 树。合并必须"以 main 为树、增量取 task"。
4. **task 的 M0–M6 已经把一部分"共享内核"抽出来了**（`codes/capacitors/noise/reference/timing/transients/impairments/reference_mux`），但 **`task2/eight/model.py` 仍反向 import `task1/experiments`**，依赖方向未闭合。
5. **task 的源文台账是机器可读的**（43 行，PASS 18 / MISSING 11 / OUT_OF_SCOPE 8 / NOT_RUN 5 / PARTIAL 1），但**只覆盖 US10505561B2 一个专利**，其余 5 个专利未入表；且部分 MISSING 行（参考建立/带宽/时偏）在 M4/M5 已有代码，台账滞后。
6. **CAAZ 在 main 和 task 都深度嵌入**：main 的 `__init__` 导出、`rtl_sources.f`、`sar_structural_ctrl`、`sar20_digital_core` 端口，task 的 `task3`/`task_integration` 端口；不能靠删目录隔离。

---

## 2. main 树

### 2.1 入口链（实际符号）

```
python -m adi_model
  └─ src/adi_model/__main__.py
      └─ cli.main_simulate / main_validate_config      (src/adi_model/cli.py)
          └─ defaults.default_model()                  (src/adi_model/defaults.py:705)
              └─ DefaultModel.simulate()               (src/adi_model/defaults.py:551)
                  └─ pipeline_engine.execute_pipeline() (src/adi_model/simulation/pipeline_engine.py:52)
                      ├─ sampler.capture()              (src/adi_model/behavioral/sampler.py:68)
                      ├─ conversion.ConversionEngine    (src/adi_model/behavioral/conversion.py:379)
                      └─ SimResult                      (src/adi_model/simulation/sim.py:23)
```

`SimResult` 同时保存：`coarse/k/sid/main_masks/sub_masks/sampling_*_connections/sampled_*_charge_c/residue_charge_c/vd_true/vra/adc2_input_voltage/adc2_code/fine/ra_sat/adc2_over/out/err/x_ref`，以及 `digital_observation`、`dither_program`、`dither_trace`、`conversion_trace`、`calibration_report`。这是 main 作为"唯一主引擎"的最大资产：**一笔样本的全部物理与数字字段都在同一个合同里**。

### 2.2 模块分组

| 组 | 文件（`src/adi_model/`） | 作用 | 状态 |
|:---|:---|:---|:---|
| 配置/合同 | `config.py`, `defaults.py`, `serialization.py`, `provenance.py`, `fixed_point.py`, `_arrays.py` | `Config`/`DefaultParameters`/schema/Q 格式/来源分级 | 实现，主链 |
| 几何/电容 | `dac_arch.py`, `slice_pool.py`, `charge_ref.py`, `sampling_charge.py`, `reference_charge.py` | 63+8 几何、18 片池、独立电荷参考 | 实现，主链 |
| 转换 | `pipeline.py`, `pipeline_engine.py`, `conversion.py`, `sim.py`, `sadc.py`, `adc2.py`, `sampler.py`, `input_network.py`, `dynamics.py`, `ra.py` | SADC/RDAC/RA/ADC2/输入网络 | 实现，主链 |
| 控制 | `scheduler.py`, `dem.py`, `dither.py`, `mapper.py`, `pretracking.py`, `chopping_az.py`, `low_frequency_noise.py`, `timing.py` | 调度、DEM、dither、预跟踪、AZ | 部分默认关/拒绝 |
| 校准/重构 | `calib.py`, `weight_calibration.py`, `reconstruction.py` | 片外训练、冻结系数、整数重构 | 实现 |
| 观测/CLI | `metrics.py`, `rtl_observation.py`, `benchmarks.py`, `inventory_gate.py`, `cli.py` | 指标、RTL 观测、清单门禁 | 实现 |

### 2.3 默认状态（来自 512 笔 simulate 的 `metadata.json`）

| 开关 | 默认 | 含义 |
|:---|:---:|:---|
| `sampling_noise` | true | 采样噪声 |
| `driver_noise` | false | 驱动噪声 |
| `ra_output_noise` | true | RA 输出噪声 |
| `unit_mismatch` | true | 单位失配 |
| `bridge_mismatch` | true | 桥失配 |
| `slice_resource_randomization` | true | 片级随机选片 |
| `basic_segmented_decoder` | true | 基本分段译码 |
| `unit_dem` | **false** | 单位级 DEM 未开 |
| `source_dither` | **false** | 源 dither 未开 |
| `feedback_mismatch` | false | 反馈电容失配 |

`unmodeled` 明确列出：完整 source lower/upper 开关网络、source 3D unit DEM、输入 RC/chop、物理片时序/带宽/失调、RA/ADC2 非线性、参考动态、FGA/CBIAS 噪声、桥/复位采样噪声协方差、PDK/版图/signoff。

### 2.4 扩展模块的实际连接状态

| 模块 | 状态 | 证据 |
|:---|:---|:---|
| `pretracking.py` + `input_network.py` | **拒绝启用**，除非有完整 active acquisition-state binding | `pipeline_engine.py:182-186` `raise ConfigError("pretracking/AUX requires a complete active acquisition-state binding")` |
| `chopping_az.py` | 默认 `mode="off"`；`conventional_az` 含两点平均/降采样；`chopping_assisted` 为 CAAZ | `chopping_az.py:32/41/232` |
| `low_frequency_noise.py` | 有模块、有导出；默认主链未作为必选噪声源 | `__init__.py:79`；`simulate` 默认未列 |
| `dem.py` | 基本分段译码默认开；unit DEM 默认关 | `metadata.enabled.unit_dem=false` |
| `dither.py` | Fig.19 program 存在；`source_dither` 默认 false | `metadata.enabled.source_dither=false` |

---

## 3. 20b10mtask 树（750a2e0）

### 3.1 入口/调用链

task 没有统一 CLI；入口是**每个任务的脚本/测试**：

```
task1/model/module1.Module1.step()
  ├─ scheduler.SliceScheduler.step()      (task1/model/scheduler.py:73)
  ├─ fig11_dem.map_fig11()                (task1/model/fig11_dem.py:102)
  └─ codebook.map_record()                (task1/model/codebook.py:35)

task2/eight/experiment.run()
  ├─ model.coarse_decision()              (task2/eight/model.py:117)
  ├─ model.active_features()              (task2/eight/model.py:151)
  ├─ model.fit_features()                 (task2/eight/model.py:169)
  └─ model.ChargePool/ConversionState     (task2/eight/model.py:182/212)

task2/model/dither_word_gen.DitherWordGenerator.step()  (task2/model/dither_word_gen.py:44)
task3/model/caaz20.CaazOracle.step()                    (task3/model/caaz20.py:84)

RTL: task_integration/rtl/sar20_digital_lms_top.sv
  ├─ sar20_digital_control（调度/DEM/dither/命令）
  └─ sar20_lms_recon（Q28 重构）
```

### 3.2 `src/adi_model` 的 12 个共享内核

| 文件 | 关键符号 | 覆盖 | 与 main 的关系 |
|:---|:---|:---|:---|
| `codes.py` | `COARSE_BITS=9`, `OUTPUT_BITS=20`, `to_unsigned`, `to_signed`, `midpoint_v` | 码宽/码-电压换算 | main 有 `mapper/metrics`，但缺单一真源 |
| `capacitors.py` | `ExperimentConfig`, `Capacitors`, `cap_pool`, `unpack`, `feedback_charge`, `voltage_equivalent` | 10MS/s 工程几何/失配 | main `dac_arch/slice_pool` 是 40MS/s 另一套 |
| `noise.py` | `NoiseConfig`, `thermal_sigma`, `white`, `flicker`, `FrontEndNoise` | kT/C、白噪、1/f | main 噪声分散在 `sampling_charge/ra/adc2/low_frequency_noise` |
| `reference.py` | `ReferenceConfig`, `settle_error`, `precharge_fraction`, `dynamic_reference`, `required_tau`, `ra_transfer` | 参考建立/RA 非线性 | main `reference_charge` 有电荷，无动态建立 |
| `reference_mux.py` | `ReferenceBinding`, `SwitchedReference`, `CouplingBinding` | 两轨参考切换 | main 无同名模块 |
| `timing.py` | `sine_slope`, `aperture_jitter`, `jitter_limited_snr`, `dac_pole_attenuation`, `TimingConfig` | 抖动/带宽 | main `timing.py` 是相位/事件，不是抖动 |
| `transients.py` | `DynamicConfig`, `SineDrive`, `RampDrive`, `ChoppedDrive`, `CoupledNode`, `rail_recovery`, `ra_endpoint` | 连续激励/瞬态 | main `dynamics.py`/`sampler.py` 有重叠 |
| `impairments.py` | `ImpairmentSet`, `only`, `all_of` | 故障注入 | main 无统一故障集 |
| `charge_ref.py` | 独立电荷参考 | 节点 KCL | main 有同名/更强版本 |
| `metrics.py` | 频谱/线性度 | 指标 | main 有 `metrics.py`，需合并 |
| `hashing.py` | 摘要 | 证据绑定 | main `provenance` 有 |
| `__init__.py` | 18 行，无导出 | — | **不能替代 main 包** |

### 3.3 任务目录

| 目录 | 文件数 | 内容 | 状态 |
|:---|:---:|:---|:---|
| `task1/` | 309（含 208 evidence） | 调度、Fig.11/Fig.21 DEM、module1、codebook、RTL 5、tests 10 | 实现，Python 测试通过 |
| `task2/` | 154 | dither_word_gen、fig19_seven、eight 模型、eight_rtl、物理 evidence | 实现；`eight/model.py` 反向依赖 |
| `task3/` | 54 | CAAZ 20bit 队列模型、RTL、formal/tb、evidence | 实现，**待隔离** |
| `task_integration/` | 32 | `sar20_digital_top`/`sar20_digital_lms_top`、RTL 对拍、GDS/DRC/LVS/STA | 实现，含 CAAZ |
| `task_reconstruction/` | 22 | Q28 片上重构核、RTL、tests | 实现 |
| `trace/` | 10 | `source_matrix.csv`、`req_matrix.csv`、`digital_status.json`、`milestones.json` | 台账，部分滞后 |
| `delivery/` | 26 | negative/历史交付、zip/manifest | 归档 |
| `handoff/` | 40 | 九个月计划、行为状态 | 文档 |

### 3.4 台账现状

`trace/source_matrix.csv` 43 行：

| 状态 | 行数 | 说明 |
|:---|:---:|:---|
| PASS | 18 | 引用能对上，**不代表实现正确** |
| MISSING | 11 | 参考建立、DAC 带宽、随机时偏、THD、NSD/1/f、参考两段切换等 |
| OUT_OF_SCOPE | 8 | 论文/讲稿中不在本模型范围 |
| NOT_RUN | 5 | 静态线性度、DR/SNR/NSD、低频 DR 等 |
| PARTIAL | 1 | PAT-06 残余 dither/后级扣除 |

按来源：`ISSCC2024_SLIDES` 21、`ISSCC2024_PAPER` 14、`US10505561B2` 8。
按 preset：`both` 18、`paper_isscc2024` 17、`na` 8。

**关键缺口**：只覆盖 dither 专利；`US10516408B2`、`US10511316B2`、`US10707889B1`、`US10541702B1`、`US10826519B1` 未入机器台账。main 的 `docs/archive/engineering_vs_patents_papers.md` 有文字对照，但没有 claim_id/测试绑定。

`trace/req_matrix.csv` 33 行：32 行 `declared_status=声明为通过（未见显式阻塞）`、`evidence=待确认`；1 行 `BLOCKED`。这不是可签核证据。

---

## 4. 依赖方向与违规

### 4.1 期望方向

```
contracts  ←  control / behavioral / calibration  ←  simulation  ←  cli / experiments
extensions/caaz  →  只依赖 contracts + 稳定公共 API；主包不反向依赖
```

### 4.2 实际违规

| 位置 | 违规 | 影响 |
|:---|:---|:---|
| `task2/eight/model.py:31-34` | `from task1.experiments.adctoolbox_calibration import SwitchState`；`bridge_lms_comparison.Q32`；`residue_control_study.MapperTable` | 主模型依赖实验目录；`MapperTable` 又依赖实验私有函数；无法作为库迁移 |
| `task2/eight/model.py:35` | `from task1.model.scheduler import GROUP_SLICES, POOL_SLICES` | 应改为 `adi_model.contracts`/`capacitors` |
| `task1/model/module1.py:20` | `from adi_model.topology.capacitors import DEFAULT_DEM_SEED, GROUP_SLICES, POOL_SLICES` | 方向正确，但 `capacitors` 目前是 10MS/s 专用配置，不是通用合同 |
| `src/adi_model/__init__.py` | 18 行、无导出 | task 包不能当库导入 |
| `task2/eight/model.py` `MapperTable` | 依赖 `task1/experiments/residue_control_study` 私有函数 | 下沉时必须先抽出 `MapperTable` 的纯数据/映射，不能整目录搬 |

---

## 5. 两棵树的结构对照

| 维度 | main | 20b10mtask | 判断 |
|:---|:---|:---|:---|
| 安装 | hatchling + console scripts | 无 build-system | main 为骨架 |
| 包 API | `__init__.py` 200+ 行，导出 40+ 符号 | 18 行，无导出 | main |
| 主引擎 | `DefaultModel.simulate` 单入口 | 每任务脚本，无统一入口 | main |
| 物理状态 | `SimResult` 全字段 | 任务模型各自 dataclass | main |
| 逐沿参考 | `scheduler.py` 按样本/计划 | `task1/model/scheduler.py` 逐沿 + stall/clear | task |
| DEM | `dem.py` 基本分段；unit_dem 默认关 | Fig.11/Fig.21 具名候选 + RTL | task |
| dither | Fig.19 program，source_dither 默认关 | 6-bit 字发生器 + 八片物理模型 + Fig.19 七片例 | task |
| 参考 | `reference_charge.py` 电荷；无动态建立 | `reference.py` 建立/预充/RA 非线性 | task |
| 时偏/带宽 | `timing.py` 相位/事件 | `timing.py` 抖动/极点/带宽 | task |
| 校准 | `weight_calibration.py` + 冻结系数 | NLMS + Q28 + 片上重构 RTL | 两者互补 |
| CAAZ | `chopping_az.py` + RTL + docs | `task3` + integration + recon | **都要隔离** |
| 证据 | 按日期/报告 | 按任务/claim + manifest + raw | task |
| 源文台账 | 文字对照 + ADR | 机器 CSV 43 行 | task（但只 1 个专利） |
| 工作点 | 40MS/s、±3V、20.5pF、9b | 10MS/s、10V、1pF、0.1% | **不同 profile** |

---

## 6. 结构层结论

1. **main 是唯一能当"可安装主包"的树**；task 的 `pyproject` 和 `__init__` 不具备这个能力。
2. **task 是唯一有"逐沿调度 + 具名 DEM/dither + 独立电荷/定点/RTL"的树**；这些必须迁入 main，而不是让 main 去适配 task 的包结构。
3. **两棵树的"同名模块"大多不是同一实现**（`scheduler`、`timing`、`capacitors`、`reference`），迁移必须按符号 diff，禁止整文件覆盖。
4. **`task2/eight/model.py` 的反向依赖是第一个必须拆的结**；不拆，主模型永远依赖实验目录。
5. **CAAZ 不是"一个目录"**：main 的 `__init__`、RTL filelist、结构控制、数字顶层端口、tests、docs 都有；task 的 `task3`/`task_integration` 也有。隔离要按清单逐项拆，不能只移 `task3`。
