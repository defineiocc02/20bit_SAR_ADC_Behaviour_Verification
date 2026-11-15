# docs 索引

最后更新：2026-11-15
**先看这一页。** `docs/` 顶层只保留 **14 份当前有效文档**，其余全部在 `_history/`。

---

## 一、核心功能（读这五份就够）

| 顺序 | 文件 | 作用 |
|:--:|:---|:---|
| 1 | [`../README.md`](../README.md) | 交付什么、怎么跑、目录结构 |
| 2 | [`D3D4_IP_INTERFACE.md`](D3D4_IP_INTERFACE.md) | **IP 对外合同**：端口/时序/两种模式/位序/合法性标志 |
| 3 | [`D1_D6_STATUS_20261115.md`](D1_D6_STATUS_20261115.md) | **D1–D6 复现状态表** + 原文定位 + 本轮核验 |
| 4 | [`ACCEPTANCE_STATUS.md`](ACCEPTANCE_STATUS.md) | 哪个模块在什么证据下通过 |
| 5 | [`CONTRACT_CURRENT.md`](CONTRACT_CURRENT.md) | 适用哪份合同、与旧实现的冲突 |

## 二、四份专项（各自回答一个问题）

| 文件 | 回答什么问题 |
|:---|:---|
| [`SYNTH_D3D4.md`](SYNTH_D3D4.md) | 综合/门级等价结果？hold 为什么只有那么点余量？ |
| [`PHASE_AB.md`](PHASE_AB.md) | 相位发生器为什么选 round-robin 而不是伪随机？ |
| [`BRIDGE_VS_FIG31.md`](BRIDGE_VS_FIG31.md) | bridging 为什么不照 PPT 图改？ |
| [`HANDOFF_D3D4.md`](HANDOFF_D3D4.md) | 接手者注意什么？哪里最可能错？ |

## 三、原理与合同（参考）

| 文件 | 作用 |
|:---|:---|
| [`DIGITAL_ALGORITHM.md`](DIGITAL_ALGORITHM.md) | D1–D6 原理、公式、与源码对应 |
| [`MECHANISM_UNDERSTANDING.md`](MECHANISM_UNDERSTANDING.md) | 机制级理解（论文层 vs 专利层）⚠️ 其中"保留列未接主链"一句已过时 |
| [`ISSCC2024_ONCHIP_CONTRACT_v2.md`](ISSCC2024_ONCHIP_CONTRACT_v2.md) | 合同 v2 正文 |
| [`ISSCC2024_ONCHIP_CONTRACT_v2_1_ADDENDUM.md`](ISSCC2024_ONCHIP_CONTRACT_v2_1_ADDENDUM.md) | v2.1 补充，**冲突时以它为准** |
| [`LIB_STATUS.md`](LIB_STATUS.md) | 工艺库可用性核查 |

## 四、子目录

| 目录 | 文件数 | 内容 | 谁在用 |
|:---|--:|:---|:---|
| `_history/` | 98 | **所有已作废/历史文档**（含原 archive、release_v8.2.x、report、restructure） | 只作追溯 |
| `mechanisms/` | 19 | 机制清单与判据 | `tools/` 下 4 个脚本引用 |
| `rtl/` | 32 | 各 RTL 模块的实现说明 | 代码注释引用 |
| `adr/` | 22 | 架构决策记录 | 交叉引用 |
| `acceptance/` | 21 | 历史验收记录（按提交哈希分目录） | 追溯 |
| `caaz/` + `randomization_caaz/` | 20 | CAAZ 扩展材料 | `extensions/caaz` 相关 |
| `spec/` | 12 | 规格草稿 | 追溯 |
| `figures/` | 8 | `DIGITAL_ALGORITHM.md` 引用的原图（PDF 渲染） | 被引用 |
| `flow/verification/` | 5 | 验证基线说明 | 交叉引用 |
| `guide/` | 4 | QUICKSTART / TERMS / ARCHITECTURE / DEBUGGING | 上手用 |
| `decisions/` | 1 | 单条决策记录 | 追溯 |

---

## 五、`_history/` 里有什么

原来散在 `docs/` 顶层的 38 份历史 md，加上 `archive/`（34 份验收记录）、
`release_v8.2.{0,1,2}/`、`report/`、`restructure/` 全部收进这里。

**不要拿 `_history/` 里的东西当现行规格。** 但有几类值得保留：

| 类型 | 例子 | 为什么留 |
|:---|:---|:---|
| 已作废规格 | `DIGITAL_SPEC.md`（4+3+2 / `c−U`） | 理解"为什么现在改成 3+3+3 / `c+U`" |
| 旧链记录 | `TOP_INTEGRATION.md`、`SYNTH_RESULT.md` | `digital_top_w` 的 8509 笔证据 |
| 过程审计 | `ALGORITHM_BASELINE_AUDIT.md` | 走过的弯路 |

---

## 六、本轮（2026-11-15）精简了什么

| 动作 | 数量 |
|:---|:---|
| 删除编辑器临时文件 `tools/algo_chain.pyWECvrC` | 1 |
| 删除孤儿脚本 `tools/g3_checks.py`、`tools/plot_dem_dither.py` | 2 |
| 顶层 md 移入 `_history/` | 38 |
| 历史子目录移入 `_history/` | 5（archive + 3×release + report + restructure） |
| 单文件目录合并进 `guide/` | 2（debugging、design） |
| **docs 顶层 md** | **52 → 14** |
