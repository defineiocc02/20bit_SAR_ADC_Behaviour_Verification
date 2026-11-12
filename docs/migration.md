# 迁移记录（20b10mtask → main）

> 分支：`merge/adi-repro`；基线 `main bc9a16a`。
> 原则：main 包为骨；task 的纯内核/控制算法迁入；CAAZ 只进扩展。

## 1. 已迁入

| 原路径（task 750a2e0） | 新路径（main） | 测试 |
|:---|:---|:---|
| `src/adi_model/codes.py` | `src/adi_model/codes.py` | `tests/unit/kernels/test_codes.py` |
| `src/adi_model/topology/capacitors.py` | `src/adi_model/topology/capacitors.py` | 内核/控制测试共用 |
| `src/adi_model/behavioral/noise.py` | `src/adi_model/behavioral/noise.py` | `test_noise.py` |
| `src/adi_model/behavioral/reference.py` | `src/adi_model/behavioral/reference.py` | `test_reference.py` |
| `src/adi_model/behavioral/reference_mux.py` | `src/adi_model/behavioral/reference_mux.py` | `test_reference.py` |
| `src/adi_model/control/timing.py` | `src/adi_model/behavioral/timing_jitter.py` | `test_timing.py` |
| `src/adi_model/behavioral/transients.py` | `src/adi_model/behavioral/transients.py` | `test_transient_kernels.py` |
| `src/adi_model/behavioral/impairments.py` | `src/adi_model/behavioral/impairments.py` | `test_impairments.py` |
| `src/adi_model/hashing.py` | `src/adi_model/hashing.py` | 由证据工具使用 |
| `src/adi_model/metrics/metrics.py` | `src/adi_model/metrics/metrics_lsb20.py` | 内核测试 |
| `task1/model/scheduler.py` | `src/adi_model/control/scheduler.py` | `test_scheduler.py` |
| `task1/model/module1.py` | `src/adi_model/control/module1.py` | `test_module1.py` |
| `task1/model/module1_bridge.py` | `src/adi_model/control/module1_bridge.py` | `test_module1_bridge.py` |
| `task1/model/fig11_dem.py` | `src/adi_model/control/fig11_dem.py` | `test_fig11_dem.py` |
| `task1/model/fig21_bridge.py` | `src/adi_model/control/fig21_bridge.py` | `test_fig21_bridge.py` |
| `task1/model/codebook.py` | `src/adi_model/control/codebook.py` | 控制测试 |
| `task2/model/dither_word_gen.py` | `src/adi_model/control/dither_word.py` | `test_dither_word.py` |
| `task1/experiments` 的 `SwitchState/Q32` | `src/adi_model/contracts/task_types.py` | `test_contract_types_events.py` |

## 2. 隔离

| 原路径 | 新路径 |
|:---|:---|
| `src/adi_model/chopping_az.py` | `extensions/caaz/model/chopping_az.py` |
| `tests/unit/test_chopping_az.py` | `extensions/caaz/tests/test_chopping_az.py` |
| `rtl/control/caaz_cycle_ctrl.sv` | `extensions/caaz/rtl/caaz_cycle_ctrl.sv` |
| `rtl/calibration/caaz_pair_processor.sv` | `extensions/caaz/rtl/caaz_pair_processor.sv` |
| `sim/tb/caaz_pair_*` | `extensions/caaz/tb/` |

主 `rtl/rtl_sources.f` 已移除两行 CAAZ；`sar_structural_ctrl` 与
`sar20_digital_core` 已删 CAAZ 端口/实例；`dout` 直接逐笔退休。

## 3. 未迁移/未接入

- `task2/eight/model.py` 的八片负注入模型（仍依赖 task1.experiments 的私有函数）。
- `task3` 的 CAAZ（有意不迁）。
- `task_integration` 的整顶层 RTL 对拍（只保留 VCS 编译）。
- `task*` 的 evidence 大二进制（归档，不进源码包）。
- `pretracking/AUX` 到主 `ConversionEngine` 的接入。

## 4. 回退

每个迁移是独立 commit；`git revert <sha>` 回到迁移前。
CAAZ 扩展可用 `extensions/caaz/` 独立回放；主包不依赖它。
