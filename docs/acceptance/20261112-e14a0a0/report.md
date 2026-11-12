# 验收自检报告 — 20261112-e14a0a0

受测版本：`e14a0a0`（分支 `merge/adi-repro`，工作区干净 0 差异）
审查基线：`0752889`；本轮起点 `b6be77b`
环境与命令：`environment.json` / `commands.txt`；证据：`evidence/`

> 本报告按验收纪律执行：**先运行、后判断**。每项状态只取
> PASS / FAIL / PARTIAL / BLOCKED / NOT_RUN，不把 BLOCKED、NOT_RUN、
> 预期失败或跳过算作通过。**首次检查结果**与**修复后结果**分开记录。

---

## 一、验收状态

| 项目 | 状态 | 主要证据 | 阻塞问题 |
|:---|:---|:---|:---|
| **G1 工程结构与可读性** | **PARTIAL** | 结构审计 `evidence/structure_audit.txt`；`check_metrics_equivalence` 零漂移 | 主引擎函数本体 691 行未拆；44 个函数 >60 行 |
| **G2 确定性缺陷回归** | **PASS** | 4 项反例测试（`test_dem3d.py` / `test_fig19_static_ports.py` / `test_reference_models.py`） | 无 |
| **G3 重构行为保持** | **PASS** | 指纹 12 项 SHA256 与基线一致；工具 `--check` 返回 0；篡改后返回 1；重复写返回 2 | 无 |
| **G4-a Python–RTL V1** | **PASS** | 7 模块 / 4931 向量 / 0 不一致（VCS） | 无 |
| **G4-b Python–RTL V2** | **PARTIAL** | 调度器逐拍状态、div/recon 延迟周期已对拍 | 缺统一的 `epoch+sample_id+event_index` 事务 trace 与事件排序/背压检查 |
| **G4-c 数字顶层 V3** | **PARTIAL** | `ELABORATED_ONLY`：elaborate + 复位 idle 实测 | 配置写入与样本流 NOT_RUN |
| **G4-d 模拟闭环 V4** | **NOT_RUN** | `results_closed_loop.json` 写明缺件 | 无逐事件命令流桥 |
| **G5 INL/DNL 与效果实验** | **PARTIAL** | 11 配置同条件矩阵；F03 九曲线 | DNL 仍是斜坡步长分箱，非码密度 |
| **G6 专利原图对应** | **PARTIAL** | dither / DEM 两篇六段专题 | 其它 4 项只有素材；原件未逐字核对 |
| **G7 CI、打包与复现** | **PARTIAL** | 本地 pytest 1543 passed、CLI 可用、收集正常 | rtl job BLOCKED（Verilator）；ruff/mypy 既有债务 |

**没有一项被标成"全部 PASS"。** 其中 G2/G3/G4-a 是可复查的硬通过。

---

## 二、首次检查结果 vs 修复后

本轮首次检查共 8 项判 FAIL。其中 **4 项是可修复的实质缺陷，已全部修复并复验**；
另 3 项（函数级可读性、ruff、mypy）首检即 FAIL 且**仍未修复**，另 1 项（动态数字输出）判 NOT_RUN。
先列已修复的 4 项：

| # | 检查项 | 首次检查 | 证据 | 修复 | 复验 |
|:--|:---|:---|:---|:---|:---|
| 1 | 单一权威实现（G1） | **FAIL**：`metrics.py` 与 `metrics_lsb20.py` 是两份独立副本，17/17 同名，14 个逐字节相同、3 个只差 `int(round())` | 结构审计 + AST 摘要对比 | `metrics_lsb20` 收成薄转发 | **PASS**：七入口 × 两侧 × 前后共 28 个摘要完全一致 |
| 2 | 行为指纹可验收性（G3） | **FAIL**：工具只写文件、静默覆盖、永远退出 0 | 读源码 + 实跑 | 补 `--check` / 首处差异 / exit 1 / 拒绝覆盖 exit 2 | **PASS**：3 项测试 + 手工三种退出码 |
| 3 | 验证器能发现错误（G4.2） | **FAIL**：7 个 comparator 只被证明"能接受正确数据" | 无相关测试 | 9 项故障注入 | **PASS**：9/9 被拒绝且字段名正确 |
| 4 | 图与表同源（G5） | **FAIL**：`figures.json.source_sha = 38df6a7`，HEAD 已 `b6be77b`；F03 6 条曲线 vs 矩阵 11 配置 | 读 JSON | 在 `1e915ec` 重生成 12 图，F03 扩到 9 条 | **PASS**：同为 256×32、同 SHA；校准行差异已写入口径说明 |

另有一项**撤回**：上一轮把 `gain` 判为"合同与 RTL 不一致"是误读
（`a` 是信号连接、`on` 是转换开关，合同明写"普通单位 a=1"）。改正后
`recon_core` 仍 101/101，RTL 一直是对的。

---

## 三、仍未关闭的问题

| 严重度 | 问题 | 位置 | 最小复现 | 影响 | 下一步 |
|:---|:---|:---|:---|:---|:---|
| **高** | 主引擎单函数 691 行、同屏 15+ 概念 | `simulation/pipeline_engine.py::execute_pipeline` | `wc -l` + AST 函数长度 | 可读性、改动风险 | 按"装配/采集/逐事件/解码"四段拆，指纹逐位对照 |
| **高** | V3 功能级未验证 | `rtl/top/sar20_digital_core.sv` | `run_verification --level rtl_top` 返回 `ELABORATED_ONLY` | 不能声称整链无丢样/重样 | 写完整 cfg map（71 权重+3 标量+18 窗口）+ `cfg_validate` |
| **高** | 逐码校准与 DEM 原理性不兼容 | `calibration/mask_calibration.py`、矩阵 C1/C3 | `run_p4_ablation.py`，看 C1 INL 26.9 vs A3 6.2 | 校准不能与 DEM 同用 | 改为"活动 mask × 单元权值"特征 |
| **中** | DNL 只是斜坡步长分箱 | `tools/run_p4_ablation.py::_static` | 读 `dnl_bin_note` 字段 | 不能称 20 位 DNL | 转换阈值搜索或码密度法 |
| **中** | V2 缺统一事务 trace | `verification/cosim/scoreboard.py` | 无 `epoch+event_index` 字段 | 事件排序/背压未验 | 扩 trace 字段与排序检查 |
| **中** | ruff 74 / mypy 8 既有债务 | 全仓 / `reference.py` 等 | `ruff check`、`mypy src/adi_model` | CI lint/type 红 | 逐项补类型与文档串（基线即 95/8，本轮未增加） |
| **中** | 4/6 专利机制无专题 | `docs/mechanisms/` | 目录列表 | 复现范围不能声明 | 按同一六段模板补写 |
| **低** | 44 个函数 >60 行 | 全包 | AST 统计 | 局部可读性 | 逐步拆，带指纹 |
| **低** | `metrics` 包级薄适配是临时方案 | `metrics/__init__.py` | 读注释 | 旧的 `from adi_model import metrics` 依赖它 | 收敛调用方后移除 |

---

## 四、本轮实际改善

**修复的错误（4 项，均有反例）**

1. DEM 逐片审计统计的是逻辑 slot（`main_on.sum(axis=0)`），不是物理片 → 按真实 `slice_ids` 散列；反例：每片只用 21/63，旧聚合报 63/63、新统计报 21/63。
2. 未被选中的物理片会被过滤 → 现保留为零行，并有守恒校验。
3. 迁移漏了 `from adi_model import <模块名>`，使整组 dither 故障注入测试 ImportError 失效 → 修好并留注释。
4. `.mem` 位宽按十六进制位数推断，63 位负数读成大正数 → 按声明位宽扩展。

**真正收敛的重复实现**

`metrics_lsb20.py`（727 行）从独立副本收成 17 行薄转发，公式只剩 `metrics.py` 一处；等价性用七入口摘要前后对照，零漂移。

**新增的有效覆盖**

- 故障注入 9 项（mask 翻转 / 舍入 / floor-vs-trunc / 延迟 / slice 交换 / sample_id 错绑 / 丢行 / 输出 ±1 LSB）；
- 行为指纹工具三种退出码的 3 项测试；
- `a` 与 `on` 的区分测试（防止再次误读合同）；
- DEM 物理片统计 2 项反例；
- β 广播轴 1 项（覆盖"活动片数 == 子单元数 = 8"的巧合情形）。

**仍未得到支持的性能结论**

- "20 位 DNL"——当前只是斜坡步长分箱；
- "校准全面改善"——A5 让 DNL 从 16.5 变差到 18.4，与 DEM 同开更差；
- "整链无丢样/重样"——V3 只到 elaborate+idle；
- "论文指标复现"——INL 最好 3.3 LSB20，口径与论文不同。

---

## 五、G1 读者走查（一笔样本，入口 → 输出）

公共入口 `python -m adi_model simulate` → `defaults.simulate`（`defaults.py:783`）
→ `DefaultModel.simulate`（`defaults.py:552`）→ `run_pipeline`（`simulation/pipeline.py:8`）

| 段 | 实际位置 | 输入 → 输出 | 状态归属 |
|:---|:---|:---|:---|
| 配置与拓扑 | `defaults.py:552` 内构造 `Config` / `SplitChip` / `Mapper` | 参数 → cfg、chip、codebook | `DefaultModel` 持有，运行期只读 |
| 采集与片分配 | `sampler.capture`（`behavioral/sampler.py:68`）+ `slice_pool.acquire`（`simulation/slice_pool.py:193`） | 波形 → `x_sadc`、`rdac_input_v`、采样电荷 | `SampleCapture`；池内电气状态由 `pool` 持有并在 `release` 时清 |
| SADC 判决 | `sadc.build_first_stage_quantizer`（`behavioral/sadc.py:94`）→ `convert_codes` | `x_sadc` → `coarse[9b]`、`sadc_over` | 无状态 |
| DEM/dither 与 RDAC | `pool.select`（`slice_pool.py:158`）→ `charge_from_masks`（`:172`） | 码+state+slice_ids → `main_on/sub_on`、`charge_c` | 掩码是逐笔值，不跨笔保存 |
| 参考/RA/ADC2 | `pool.held_effective_node_charge`（`:266`）→ `ra.evaluate`（`behavioral/ra.py:92`）→ `adc2.quantize_codes`（`:41`） | 电荷 → 余差 → `vra` → `adc2_code` | RA/ADC2 为纯函数，无历史 |
| 数字重构 | `reconstruction.reconstruct`（`calibration/reconstruction.py:36`） | `vd0`、`fine`、`gain` → 浮点输出 | 无状态 |
| 定点解码 | `FixedPointReconstructor`（`contracts/fixed_point.py:78`） | 浮点 → 20 位整数码 + flags | 系数冻结后只读 |
| 指标 | `metrics.summarize`（`metrics/metrics.py:607`） | 记录 → SNDR/SFDR/NSD | 只读，不改仿真数据 |

**结论**：单条链路可以在**一个包内**走通，不需要读历史脚本；但
`execute_pipeline`（691 行）内部仍混着"装配 / 采集 / 逐事件 / 解码"四段，
走查时必须在该函数内来回跳。**易理解性判 PARTIAL，不判通过。**

---

## 六、四个独立判定

**1. 工程重构是否通过验收？**
**不通过（PARTIAL）。** 目录分层、单一权威实现、依赖方向、CAAZ 隔离已成立并有证据；
但主引擎未拆分，函数级可读性未达标。按"结构重构"与"可读性重构"分开看：
前者 PASS，后者 FAIL。

**2. 已声称的数字功能是否通过 Python–RTL 一致性验收？**
**部分通过。** 7 个模块 V1 位真一致（4931 向量、0 不一致），且验证器已证明能抓错；
但 V2 缺统一事务 trace、V3 只到 elaborate、V4 未运行。因此**不能**声称"数字功能整链一致"。

**3. 行为模型及技术效果实验是否足以支持当前结论？**
**部分足以。** "DEM 主要改善 INL、dither 主要改善 DNL、逐码校准与 DEM 不兼容"
这三条有同条件 11 配置数据支撑；"20 位 DNL""校准全面改善""论文指标复现"
三条**不被支持**，且已有反证。

**4. 是否达到论文对应指标与所声明复现范围？**
**未达到。** 最好 INL 3.3 LSB20（C2），论文 ±2.2 ppm 口径不同且更严；
6 项专利机制中 2 项有完整专题；无后仿、无 PEX、无硅测。

---

## 七、能否建议合并

按验收规则，"生产审计假通过"与"结果来源混用"属于**不得建议作为稳定版本合并**的类别。
本轮在**修复前**这两类问题都实际存在：

- DEM 逐片审计把 slot 统计命名为物理片统计（生产审计假通过）；
- 图记录的是 12 个提交之前的 `source_sha`，而首页已引用新数字（结果来源混用）。

两者均已修复并有反例/复验证据（见 §二）。因此当前结论是：

> **修复后的 `e14a0a0` 可以进入合并评审，但不能以"机制复现完成"或
> "整链一致"的名义合并。** 阻止"完整验收"的最少必要修复是：
> ① 拆 `execute_pipeline`；② 打通 V3 的配置写入与样本流；
> ③ 把校准特征从"逐码"改成"活动 mask × 单元权值"。

---

## 八、复现本报告

见 `commands.txt`。所有数字都来自 `evidence/` 下的原始输出；
`p4_ablation.json` 与 `figures.json` 同为 256 电平 × 32 次、同一 `source_sha`。
