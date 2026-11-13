# 工程易理解性分析

受测版本：`02c3676`。本文只回答一个问题：**一个没参与开发的人，能不能读懂并用起来。**

---

## 1. 量化画像

| 指标 | 实测 | 判断 |
|:---|---:|:---|
| `src/adi_model` 函数总数 | 489 | — |
| 平均长度 / 中位数 | 23.8 / **12 行** | **整体是好的**：一半函数在 12 行以内 |
| >60 行 | 41（8.4%） | 长尾 |
| >80 行 | 23（4.7%） | 长尾 |
| >150 行 | **6** | 需要处理 |
| 最大嵌套深度 | 5（3 处） | 偏深 |
| 同名公开函数组 | 38 | 多数是合法重名（不同类的 `evaluate`/`from_dict`），**已收敛 1 组真实重复** |
| 短名/泛名出现 | data 117、state 68、x 64、v 45、r 34 | 多数在数学局部，少数跨阶段 |

**结论：不是"整体写得烂"，而是"少数几个巨型函数把入口堵死了"。**
中位数 12 行说明大部分代码是可读的；问题集中在 6 个 >150 行的函数上。

### 六个必须处理的函数

| 行数 | 位置 | 为什么难读 |
|---:|---|---|
| **691** | `simulation/pipeline_engine.py::execute_pipeline` | 一个函数里同时做校验、采集、逐事件转换、参考/RA/ADC2 推进、数字解码、结果封装；同屏 15+ 个活跃概念 |
| 260 | `defaults.py::inference_table` | 纯数据表，但 260 行硬编码，读者无法快速定位某一项 |
| 228 | `inventory_gate.py::_check_code_and_tests` | 静态审计器，嵌套 5 层 |
| 204 | `defaults.py::simulate` | 装配 + 运行 + 解码混在一起 |
| 172 | `behavioral/conversion.py::step` | 三态（参考/RA/ADC2）在同一函数推进 |
| 5 层嵌套 | `control/dem3d.py::fig11_masks` | 掩码生成的循环嵌套过深 |

---

## 2. 读者走查（一笔样本，入口 → 输出）

| 段 | 实际位置 | 读者要跳几次 |
|:---|:---|:---|
| 入口 | `python -m adi_model simulate` → `defaults.py:783` | 1 |
| 装配 | `defaults.py:552`（`DefaultModel.simulate`，204 行） | 2 |
| 采集 | `behavioral/sampler.py:68` + `simulation/slice_pool.py:193` | 3 |
| SADC | `behavioral/sadc.py:94` | 4 |
| DEM/dither/RDAC | `slice_pool.py:158/172` | 5 |
| 参考/RA/ADC2 | `slice_pool.py:266` → `ra.py:92` → `adc2.py:41` | 6–8 |
| 重构 | `calibration/reconstruction.py:36` | 9 |
| 定点解码 | `contracts/fixed_point.py:78` | 10 |
| 指标 | `metrics/metrics.py:607` | 11 |

**好的部分**：这条链在**一个包内**就能走完，不需要读历史脚本或 `tools/`。
每个节点都有明确归属（`SampleCapture` / `PhysicalSlicePool` / `DigitalState`）。

**坏的部分**：第 3–8 段全部发生在 `execute_pipeline` 的 691 行内部。
读者必须在同一个函数里来回跳 300 行才能看清"哪一步产生 `q_residue`、
哪一步消费它"。这是**唯一的真实门槛**。

---

## 3. 命名与作用域

### 值得肯定的

- 物理片 / 逻辑 slot / 单元三层区分已经在关键位置落实：
  `physical_slice_ids`、`conv`（转换组）、`slice_ids`、`unit`；
  这轮的 DEM 统计缺陷正是因为没有区分 slot 与物理片，现已修正并有反例。
- 单位后缀成体系：`_v`/`_f`/`_c`/`_s`/`_hz`/`_q30`/`_lsb20`。
- 少量短名（`x`、`r`、`v`）集中在数学局部，符合"局部公式允许短变量"的规则。

### 待改的

| 位置 | 问题 | 建议 |
|:---|:---|:---|
| `pipeline_engine` 的 `term` / `command` / `drive` | 跨 3 个阶段复用，含义在变 | 拆成 `sampling_connection_term`、`rdac_command`、`dac_drive` |
| `conversion.step` 的 `state` | 同时指参考节点、RA 节点、ADC2 节点 | 拆成 `reference_state` / `ra_state` / `adc2_state` |
| `defaults.py` 的 `program` | 既指 dither program 又指运行程序 | dither 侧改名 `dither_program` |

---

## 4. 与"单一权威实现"的关系

本轮把 `metrics_lsb20.py`（727 行副本）收成 17 行薄转发，是**唯一一处真实的双份公式**。
其余 37 组同名函数经抽查属于不同类的同名方法（`evaluate`、`from_dict`、`main`），
不构成漂移风险。

仍需注意：`control/fig11_dem.py` 与 `control/fig21_bridge.py` 各有一套 `main_flat`，
代表 **Fig.11 几何**与 **Fig.21 桥接**两个不同实施例。它们**不是重复实现**，
但共用相似命名，容易误读。建议在模块头显式写"这是另一实施例，不是替代实现"。

---

## 5. 重构方案（带验收条件）

| 步 | 动作 | 验收 |
|:--:|:---|:---|
| 1 | 把 `execute_pipeline` 拆成 `_validate request` / `_acquire` / `_convert_one` / `_decode` 四个私有函数 | `behavior_fingerprint.py --check` 12 项 SHA256 不变；`tests/unit` 全绿 |
| 2 | `conversion.step` 按三个状态拆 | 同一 seed 的 `conversion_trace` 每字段相同 |
| 3 | `dem3d.fig11_masks` 把 5 层嵌套降为 2 层（先建索引再查表） | 掩码 SHA256 不变 |
| 4 | 改 `term/command/drive/state/program` 五个跨阶段名 | 仅重命名，指纹不变 |

前三步都能用现成工具证明"行为没变"（`--check` 已支持非零退出），
不需要人工比对数字。

---

## 6. 判定

| 维度 | 状态 | 理由 |
|:---|:---|:---|
| 目录与职责分层 | **PASS** | 7 个子包职责清楚，依赖方向零违规 |
| 单一权威实现 | **PASS** | 唯一一处真实重复已收敛并有等价性证据 |
| 单函数可读性 | **FAIL** | 6 个 >150 行函数，`execute_pipeline` 691 行 |
| 命名与作用域 | **PARTIAL** | 单位后缀成体系；5 处跨阶段复义 |
| 读者走查 | **PARTIAL** | 链在一个包内可走通；但 3–8 段挤在同一个函数里 |

**一句话**：这个工程不是"看不懂"，是"入口那一段太长"。把 691 行拆开之后，
可读性可以从 PARTIAL 提到 PASS，而且不需要改动任何数值——现成的指纹工具就能证明。
