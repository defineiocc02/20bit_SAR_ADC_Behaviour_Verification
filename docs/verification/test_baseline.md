# 测试基线与工具状态（P0）

> 分支 `merge/adi-repro`；基线 `main bc9a16a`。所有命令实际运行；未运行项标 `NOT RUN`。
> 证据层级：L1 行为/合同、L2 数字 RTL 仿真；无后仿/硅测。

## 1. 工具

| 工具 | 位置/版本 | 状态 |
|:---|:---|:---|
| Python | 3.13.12（Mac） | 可用 |
| numpy/scipy/matplotlib/pytest | 已装 | 可用 |
| **VCS** | 虚拟机 `vm-meow`：`/opt/synopsys/vcs/W-2024.09-SP1`，W-2024.09-SP1_Full64 | **可用**；需 `VCS_TARGET_ARCH=amd64`、`LM_LICENSE_FILE=27080@localhost` |
| Icarus | Windows `C:\iverilog\bin`，12.0 | 可用但**无法 elaborate** 主 RTL 的 SV 构造（`weight_store`/`div_floor` 等） |
| Verilator / Yosys | 未找到 | **NOT RUN** |
| hatchling | 未装 | sdist 测试 `NOT RUN` |
| adctoolbox | Windows/vm-meow 默认环境未找到 | task `baseline_frozen` **NOT RUN** |
| git 历史 | 本地 shallow（depth=1） | `test_review_trace` 的 `d1e1f87` 查询失败 |

## 2. 主回归

```bash
cd /Users/zhaoyi/src/20bit_SAR_ADC_Behaviour_Verification
PYTHONPATH=src python3 -m pytest -q
```

结果：**1699 passed / 3 failed / 47 errors**（44.18 s）。

| 非通过项 | 原因 | 归类 |
|:---|:---|:---|
| `tests/integration/test_mutation_twoneck.py::...` | `FAIL: simulator not found; configure VERILATOR` | 工具缺失 |
| `tests/unit/test_review_trace.py::test_reviewed_production_file_inventory...` | shallow clone 缺 `d1e1f87` | Git 历史 |
| `tests/unit/test_robustness_2026_09_25.py::test_sdist...` | `ModuleNotFoundError: hatchling` | 依赖缺失 |
| 47 个 `test_source_bound_replay.py` errors | `actual_capture` 需 Verilator | 工具缺失 |

**结论**：可运行范围内没有未解释的失败；3 failed/47 errors 全部是环境/工具，不是本次改动引入。

## 3. 新增/迁移测试

| 范围 | 命令 | 结果 |
|:---|:---|:---|
| 内核 | `pytest tests/unit/kernels` | **78 passed** |
| 控制 | `pytest tests/unit/control` | **41 passed** |
| 合同/profile + 普通 AZ | `pytest tests/unit/test_contracts_profiles.py tests/unit/test_ra_az.py` | **14 passed** |
| 合同类型/事件 | `pytest tests/unit/test_contract_types_events.py` | **5 passed** |
| 机制入口 | `pytest tests/unit/test_mechanisms.py` | **3 passed** |
| 八片模型 | `pytest tests/unit/test_eight_slice.py` | **3 passed** |
| 主链普通 AZ | `pytest tests/unit/test_simulate_ordinary_az.py` | **3 passed** |
| CAAZ 隔离 | `pytest tests/unit/test_caaz_isolation.py` | **1 passed** |
| CAAZ 扩展 | `PYTHONPATH=.:src pytest extensions/caaz/tests/test_chopping_az.py` | **32 passed** |

## 4. 主链普通 AZ（P1 冒烟）

```bash
PYTHONPATH=src python3 -m adi_model simulate --samples 32 --output /tmp/adi-demo
```

API 对照（`DefaultModel.simulate(..., ordinary_az=...)`）：

| 配置 | 结果 |
|:---|:---|
| 无 AZ | 32 码 |
| AZ enabled, offset=0, noise=0 | **32 码，逐位一致** |
| AZ enabled, offset=1 mV | **32 码，全部码改变**；N→N，无配对 |

## 5. VCS RTL

### 5.1 主 top 编译

```bash
export VCS_HOME=/opt/synopsys/vcs/W-2024.09-SP1
export VCS_TARGET_ARCH=amd64
export LM_LICENSE_FILE=27080@localhost
vcs -full64 -sverilog -timescale=1ns/1ps \
  +incdir+. +incdir+rtl/params +incdir+rtl \
  -f rtl/rtl_sources.f rtl_params.vh \
  source_control_binding.sv source_acquisition_binding.sv \
  -top sar20_digital_core -o simv
```

结果：**0 error**，`simv` 生成；1 条 TFIPC 警告（`cal_sample_context` 少接 3 个输出）。

### 5.2 `source_control_contract_tb` 仿真

```bash
vcs ... sim/tb/source_control_contract_tb.sv sim/tb/wrap_source_control_tb.sv \
  -top wrap_source_control_tb -o tb_simv
./tb_simv +weights=sim/fixtures/source_control_component/weights.hex \
  +scalars=sim/fixtures/source_control_component/scalars.hex \
  +trace_dir=/tmp/trace_out +frames=32
```

结果：**`SOURCE_CONTROL_COMPONENT_COMPLETE observations=56 outputs=54 checks=368 epochs=2`**，仿真时间 6.725 µs，CPU 0.17 s。

边界：这是 **source_control_component 夹具**（N_UNIT_MAIN=2、N_UNIT_SUB=1、ADC2_BITS=6），不是 20b 全芯片；368 个 `require_ok` 是组件合同检查，不等于整链 scoreboard。`SOURCE_CONTROL_ANALOG_MAPPING_TIMING_CAAZ_NOISE_NOT_VERIFIED` 由 tb 自己打印。

## 6. P0 结论

- 核心包 `src/adi_model` 无 `from task*`/`import task*`（仅一处注释）。
- 可运行范围内无未解释失败；3 failed/47 errors 均归类为工具/历史/依赖。
- 不能把 47 个 error 计入 PASS；Verilator 测试保持 `NOT RUN`。
