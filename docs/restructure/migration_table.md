# 工程结构迁移表（目标职责 → 现状 → 动作）

> 这张表是**迁移依据**，不是"计划说明"。每行给出：目标路径、现状文件、动作、行为是否变化、
> 验收方式、依赖顺序。**动作分四类**：`MOVE`（只搬家，行为不变）、`ADAPT`（薄适配，行为不变）、
> `MERGE`（合并重复公式，行为必须逐位一致）、`NEW`（新增职责）、`KEEP`（不动）。
>
> 硬约束：**先做 MOVE/ADAPT 并证明逐位一致，再改算法**。两件事不能进同一个提交。

---

## 1. 目标结构与现状对照

| 目标目录 | 现状 | 动作 | 行为变化 | 验收 |
|:---|:---|:---:|:---:|:---|
| `contracts/` | 已有 `src/adi_model/contracts/{specs,profiles}.py`、`contracts/task_types.py` | **KEEP** | 无 | 现有 8 项 profile 测试通过 |
| `topology/` | `dac_arch.py`（SplitChip/几何）、`capacitors.py`（权重与失配）、`mapper.py`（码本） | **MOVE** 三文件到 `topology/` | 无 | 全仓 import 改写后 `pytest tests/unit -k "cap or mapper or dac"` 逐位一致 |
| `control/` | `dem3d.py`、`dither.py`、`follower.py`、`track_aux.py`、`timing.py`、`scheduler.py` | **MOVE**；`dither_port.py` 一分为二（见 §2） | 无（MOVE 阶段） | 46 项机制测试 |
| `behavioral/` | 已有 `behavioral/ra_az.py`；`sampler.py`、`charge_ref*.py`、`reference*.py`、`ra.py`、`adc2.py`、`conversion.py`、`input_network.py`、`sampling_charge.py` | **MOVE** 到 `behavioral/` | 无 | 采样/参考/RA/ADC2 的单测 |
| `calibration/` | `mask_calibration.py`、`weight_calibration.py`、`fixed_point.py`、`reconstruction.py` | **MOVE** 合并目录 | 无 | 校准与重构测试 |
| `simulation/` | `pipeline.py`、`pipeline_engine.py`、`slice_pool.py`、`config.py` | **MOVE**；`pipeline_engine.py` 拆分（见 §3） | 无（拆分前后逐位一致） | 同一 seed 的 `codes.voltage` 逐位相同 |
| `analysis/` | `metrics.py`、`metrics_lsb20.py`、`benchmarks.py` | **MERGE** 两个 metrics（保留 `lsb20` 作为参数） | **不变**，但必须有对照 | 同一数组两种口径数值一致 |
| `experiments/` | `tools/run_*.py` 里的配置矩阵与阈值 | **NEW**：把 A0–A7 矩阵从 `tools/` 提到 `experiments/matrix.py` | 无 | `tools/` 只剩 argparse 与调用 |
| `tools/` | 现有 7 个脚本 | **ADAPT**：只留参数解析与打印 | 无 | 脚本输出 JSON 与迁移前逐字段相同 |
| `docs/mechanisms/` | 本轮新建（dither / DEM） | **NEW** | — | 六段结构齐全 |
| `docs/history/` | `docs/reviews/2026111*/` 历史审查 | **MOVE** 旧轮次到此，当前轮保留 | — | 首页只链一个权威入口 |
| `extensions/caaz/` | 已存在 | **KEEP**，主包零依赖 | 无 | CAAZ 隔离门禁仍 PASS |

## 2. `dither_port.py` 必须拆成两个身份

| 目标文件 | 内容 | 理由 |
|:---|:---|:---|
| `control/dither_program.py` | `Fig19DitherProgram`、`Fig19StaticSpec`（从 `dither.py` 提） | **数字**：字 → `L/U/T` |
| `behavioral/dither_injection.py` | `DitherPort`（等效电荷口） | **模拟**：注入电荷 |
| `analysis/dither_residual.py` | `code_shift_residual`、`range_ledger` | **分析**：只读，不进主链 |

现状三者在同一文件里，导致"哪部分是模型、哪部分是分析"看不清。
拆分后**不得**出现两份字解码（`from_words` 只能有一处）。

## 3. `pipeline_engine.execute_pipeline` 的拆分（最大的一块）

现状约 500 行、同屏 15+ 个活跃概念。目标：

```
simulation/run.py            execute_pipeline 的壳：装配 → 逐笔 → 收尾
simulation/acquisition.py    capture + 采样连接 + 初始态
simulation/conversion.py     逐事件：SADC 判决 → RDAC follower → 参考 → RA → ADC2
simulation/decode.py         vd0 + fine/g + dither 加回 + 校准应用
```

拆分验收（**必须逐位**）：

1. 同一 seed、同一输入，拆分前后 `codes.voltage` 的 SHA256 相同；
2. 同一 seed，动态配置（A7）的 `conversion_trace` 每个字段相同；
3. 事件数、事件时刻、RA 饱和计数相同。

## 4. 命名与接口（必须同时改）

| 现状问题 | 目标 |
|:---|:---|
| `x`、`r`、`v`、`state`、`data` 跨阶段复义 | `input_v`、`residue_v`、`ra_output_v`、`dem_state`、`observation` |
| `execute_pipeline` 一次吃 20+ 参数 | 打包成 `RunRequest`（样本源、拓扑、控制、观测、初始态五组），不再塞无类型 dict |
| 变量无单位 | 后缀 `_v`/`_c`/`_s`/`_f` 强制；数组在 docstring 标 shape |
| `metrics` 与 `metrics_lsb20` 两套口径 | 一套函数 + `lsb_v` 参数 |

## 5. 执行顺序（每步单独提交，失败可回退）

| 步 | 动作 | 风险 | 回退 |
|:---:|:---|:---|:---|
| 1 | `topology/`、`behavioral/`、`calibration/` 只搬家 | 低（import 改写） | `git revert` 单提交 |
| 2 | `metrics` 合并 + 对照测试 | 中（口径） | 保留旧函数为薄包装 |
| 3 | `dither_port` 三方拆分 | 中（可能改写调用） | 同上 |
| 4 | `execute_pipeline` 四段拆分，逐位对照 | 高 | 保留旧实现为 `run_legacy`，只在测试里用 |
| 5 | `experiments/` 提矩阵 | 低 | — |
| 6 | 文档重排（README + `docs/mechanisms` + `docs/history`） | 低 | — |

## 6. 不做什么

* **不重写模型**：迁移期只搬与拆，不改公式；公式改动必须单独提交并带对照。
* **不引入第二套主链**：`run_legacy` 只允许测试引用，生产路径只有一条。
* **不把 CAAZ 拉回主链**：`extensions/caaz` 保持零依赖。
* **不用"测试全绿"替代逐位对照**：搬家必须给 SHA 对照，不能只说 pytest 过了。

## 7. 当前状态（本表落盘时）

| 项 | 状态 |
|:---|:---|
| 两张示范专题（dither / DEM） | ✅ 已按六段写（`docs/mechanisms/`） |
| 迁移表 | ✅ 本文件 |
| 实际搬家 | ❌ 未开始（按第 5 节顺序，从第 1 步起） |
| README 重排 | ⏳ 与第 1 步同批 |
