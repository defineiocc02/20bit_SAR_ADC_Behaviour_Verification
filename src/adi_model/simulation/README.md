# `adi_model.simulation` —— 唯一运行链、事件推进与结果

| 功能 | 锚点 | 实现 |
|:---|:---|:---|
| 主链编排 | [`#pipeline`](#pipeline) | `pipeline_engine.execute_pipeline` |
| 校验与拒绝 | [`#validate`](#validate) | `pipeline_engine._validate_request` |
| 物理池与资源 | [`#pool`](#pool) | `slice_pool.PhysicalSlicePool` |
| 状态归属 | [`#state`](#state) | `DigitalState` / `SampleCapture` |
| 结果与诊断 | [`#result`](#result) | `SimResult`、`diagnostic_reasons` |

---

## 0. 这个模块做什么

把 topology / control / behavioral / calibration 装成**一条**运行链，
逐笔推进事件，产出 `SimResult`。**不允许有第二条主链**。

当前 `execute_pipeline` 604 行（本轮从 691 行提出 98 行纯校验）。

---

<a id="validate"></a>
## 1. 校验先于状态

`_validate_request()` 是纯函数：只读参数、只抛 `ConfigError`，唯一产出是规范化后的
`n_samples`。**它拒绝什么，就定义了本模型支持什么**：

| 拒绝条件 | 含义 |
|:---|:---|
| `dither_program` 与 `dither_port` 同时给 | 两套拓扑互斥，必须选一套 |
| `dither_enable` 与实际拓扑不符 | 不允许"开标志但不接线" |
| `cfg.dyn_input_settling` | 连续采集需要完整相位绑定，静态 runner 不提供 |
| `cfg.dyn_crosstalk` | 串扰需要显式边沿/耦合电路绑定 |
| `pretracking_mode != off` 或 `auxiliary_cap_f != 0` | TRACK/AUX 需要完整采集态绑定 |

**这是"明确拒绝"而不是"静默关闭"**——被拒绝的功能不会退回默认路径。

---

<a id="pipeline"></a>
## 2. 一笔样本的阶段

| 阶段 | 位置 | 输入 → 输出 | 状态归属 |
|:---|:---|:---|:---|
| 采集 | `sampler.capture` | 波形 → `x_sadc`、采样电荷 | `SampleCapture`，单笔 |
| 量化 | `sadc.convert_codes` | `x_sadc` → `coarse[9]` | 无 |
| 掩码 | `pool.select` | 码+DEM 态+片号 → `main/sub_on` | 逐笔值 |
| 电荷 | `pool.charge_from_masks` | 掩码 → `charge_c` | 无 |
| 余差 | 引擎内 | `q_free − charge_c` → `residue` | 无 |
| RA/ADC2 | `ra.evaluate` → `adc2.quantize_codes` | → `vra` → `adc2_code` | 无 |
| 重构 | `reconstruction.reconstruct` → `FixedPointReconstructor` | → 20 位码 | 系数只读 |

---

<a id="pool"></a>
## 3. 物理池

18 片，每笔 8 采集 / 8 转换 / 2 空闲。`pool.reset_electrical_state()` 在每轮开始
清电气状态——**这是刻意的**：共享 RA 的跨笔状态**没有建模**。

【工程假设／未确认】真实芯片的 RA 会保留上一笔的初态；本模型每笔从理想零初态开始，
这会低估记忆效应。

---

<a id="state"></a>
## 4. 状态归属表

| 状态 | 谁持有 | 生命周期 |
|:---|:---|:---|
| `Config` | 调用方 | 整个 run，只读 |
| `SplitChip`（制造电容） | `DefaultModel` | 整个实例 |
| `DigitalState` | 调用方 / `initialize_state` | 跨记录可复用 |
| 池电气状态 | `PhysicalSlicePool` | **逐笔 acquire→release** |
| `SampleCapture` | 每次 capture 新建 | 单笔 |
| `SimResult` | 引擎返回 | 只读产物 |

没有可变全局状态。

---

<a id="result"></a>
## 5. 结果与"设计性 NaN"

`diagnostic_reasons` 非空时 `out` 全为 `NaN`，这是**明确语义**不是崩溃：

| 原因 | 位置 |
|:---|:---|
| 参考/RA/ADC2 带状态响应 | `pipeline_engine` 的 `diagnostic_reasons` |
| 采样节点非零初态 | 同上 |
| 非全 Signal 连接 | 同上 |

调用方必须读 `raw.nominal_diagnostic_available` 与 `nominal_diagnostic_reasons` 才能
判断"没结果"和"结果是 0"。

---

## 6. 缺口

| 项 | 状态 |
|:---|:---|
| 函数拆分 | 604 行，采集/逐事件/解码三段仍在函数内 |
| 动态名义解码 | 未实现（NaN） |
| 共享 RA 跨笔状态 | 未实现 |
| 事件 trace | 只有逐笔记录，没有 `epoch+event_index` |

```bash
PYTHONPATH=src python3 -m pytest tests/unit/test_pipeline_smoke.py -q 2>/dev/null || \
PYTHONPATH=src python3 -m pytest tests/unit -q -k pipeline
```

---

## 7. 与 RTL 的关系

`recon_core` 的 101 条向量与本引擎的定点通路位真一致；但**顶层 `sar20_digital_core`
只到 elaborate + 复位 idle**（`ELABORATED_ONLY`），没有跑过真实事务。
见 `verification/README.md`。
