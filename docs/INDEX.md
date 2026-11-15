# docs 目录索引

最后更新：2026-11-15
用途：**看文档前先看这一页**。`docs/` 顶层有 50 个 `.md`，其中只有 14 份是"当前有效"，
其余是历史过程记录。本页把它们分开，避免读到过期结论。

---

## 一、当前有效（先读这些）

按推荐阅读顺序：

| 顺序 | 文件 | 作用 |
|:--:|:---|:---|
| 1 | [`../README.md`](../README.md) | 这个仓库交付什么、怎么跑 |
| 2 | [`D3D4_IP_INTERFACE.md`](D3D4_IP_INTERFACE.md) | **IP 对外合同**：端口/时序/两种模式/位序/合法性标志 |
| 3 | [`CONTRACT_CURRENT.md`](CONTRACT_CURRENT.md) | 适用哪份合同、与旧实现的冲突清单 |
| 4 | [`ACCEPTANCE_STATUS.md`](ACCEPTANCE_STATUS.md) | **哪个模块在什么证据下通过** |
| 5 | [`SYNTH_D3D4.md`](SYNTH_D3D4.md) | 综合与门级等价记录（含 hold 争议说明） |
| 6 | [`HANDOFF_D3D4.md`](HANDOFF_D3D4.md) | 接手说明：做了什么、怎么验、哪里最可能错 |

## 二、本轮交付的四份专项说明

| 文件 | 内容 |
|:---|:---|
| [`PHASE_AB.md`](PHASE_AB.md) | 相位发生器 A/B：round-robin vs xorshift（统计 + 面积） |
| [`BRIDGE_VS_FIG31.md`](BRIDGE_VS_FIG31.md) | binary-to-unary bridging 与 PPT Fig.31 的核对（为何不照图改） |
| [`NINE_POINTS_STATUS.md`](NINE_POINTS_STATUS.md) | 论文九点披露的逐条实现状态 |
| [`SYNTH_D3D4.md`](SYNTH_D3D4.md) | 同上（综合部分） |

## 三、原理与合同（有效，但偏参考）

| 文件 | 作用 | 备注 |
|:---|:---|:---|
| [`DIGITAL_ALGORITHM.md`](DIGITAL_ALGORITHM.md) | D1–D6 原理、公式、与源码对应 | 已改为 3+3+3 / `c+U` 口径 |
| [`MECHANISM_UNDERSTANDING.md`](MECHANISM_UNDERSTANDING.md) | 机制级理解（论文层 vs 专利层） | 其中"保留列与两级 shuffler 未接主链"一句**已过时** |
| [`ISSCC2024_ONCHIP_CONTRACT_v2.md`](ISSCC2024_ONCHIP_CONTRACT_v2.md) | 合同 v2 正文 | 结构基线 |
| [`ISSCC2024_ONCHIP_CONTRACT_v2_1_ADDENDUM.md`](ISSCC2024_ONCHIP_CONTRACT_v2_1_ADDENDUM.md) | v2.1 补充条款 | **冲突处以 v2.1 为准** |
| [`CONTRACT_V2_REVIEW.md`](CONTRACT_V2_REVIEW.md) | v2 的评审意见 | 历史，但解释了很多决定的由来 |
| [`PHYSICAL_CONTRACT_DRAFT.md`](PHYSICAL_CONTRACT_DRAFT.md) | 物理口径草案 | 部分未定 |
| [`LIB_STATUS.md`](LIB_STATUS.md) | 工艺库可用性核查（28nm/SMIC180） | 标了 `vm-meow` 主机 |

## 四、已作废 / 仅作演进记录

**不要拿这些当现行规格。** 它们记录的是走过的弯路，有时反而是理解"为什么现在这样写"的最好材料。

| 文件 | 状态 |
|:---|:---|
| [`DIGITAL_SPEC.md`](DIGITAL_SPEC.md) | **已降级为历史件**。写的是 4+3+2 与 `k=c−U`，均被 v2.1 取代 |
| [`DIGITAL_PLAN.md`](DIGITAL_PLAN.md) | 历史计划，进度已不适用 |
| [`ACCEPTANCE_CHECKLIST.md`](ACCEPTANCE_CHECKLIST.md) | 历史验收表，被 `ACCEPTANCE_STATUS.md` 取代 |
| [`CHANGES_20261114.md`](CHANGES_20261114.md) | 那一天的改动记录 |

## 五、旧链（`digital_top_w`）的过程记录

这些文档描述的是 **D1–D6 旧链**（4+3+2 / `c−U`），与 `d3d4_ip` 不是同一条链。
读的时候务必分清。

| 文件 | 内容 |
|:---|:---|
| [`TOP_INTEGRATION.md`](TOP_INTEGRATION.md) | 旧顶层集成与 8509 笔回归 |
| [`D2_INTERFACE_SPEC.md`](D2_INTERFACE_SPEC.md) / [`D2_IFACE_IMPL.md`](D2_IFACE_IMPL.md) / [`D2_STAGE2.md`](D2_STAGE2.md) / [`D2_MODE_A.md`](D2_MODE_A.md) | D2 Follower 的事件协议演进 |
| [`FOLLOWER_RT.md`](FOLLOWER_RT.md) | follower_rt 的实现说明 |
| [`D5_PROD_VS_RTL.md`](D5_PROD_VS_RTL.md) | D5 重构的 Python vs RTL 对拍（旧链） |
| [`INV_GROUP_PLAN.md`](INV_GROUP_PLAN.md) | 9 组倒数表方案（旧链） |
| [`TIMING_W2.md`](TIMING_W2.md) / [`TIMING_PIPE2.md`](TIMING_PIPE2.md) / [`CRITICAL_PATH.md`](CRITICAL_PATH.md) | 除法器优化与时序分析（旧链） |
| [`SYNTH_RESULT.md`](SYNTH_RESULT.md) / [`SYNTH_28NM.md`](SYNTH_28NM.md) | 旧链的 28nm 综合记录 |
| [`VCS_RESULT.md`](VCS_RESULT.md) | 早期 VCS 结果 |

## 六、DEM / Dither 专项实验记录

| 文件 | 内容 |
|:---|:---|
| [`DEM_ABCD.md`](DEM_ABCD.md) | DEM 四组对照实验（固定/轮转 × 片组/相位） |
| [`DEM_SPECTRUM.md`](DEM_SPECTRUM.md) | DEM 频域实验 |
| [`DEM_3D_RANDOM.md`](DEM_3D_RANDOM.md) | 三维随机化探索 |
| [`DEM_ANSWERS.md`](DEM_ANSWERS.md) / [`DEM_PHASE_FIX.md`](DEM_PHASE_FIX.md) | DEM 问答与相位修复记录 |
| [`LOWER_DITHER.md`](LOWER_DITHER.md) | Lower dither 专项 |
| [`MULTISEED.md`](MULTISEED.md) | 多 seed 实验 |

## 七、审计与其他

| 文件 | 内容 |
|:---|:---|
| [`ALGORITHM_BASELINE_AUDIT.md`](ALGORITHM_BASELINE_AUDIT.md) | 算法基线冻结审计方案 |
| [`REPORT_VS_PAPER.md`](REPORT_VS_PAPER.md) | 报告与论文的对照 |
| [`TX_CONTROL_AUDIT.md`](TX_CONTROL_AUDIT.md) | 事务控制审计 |
| [`NO_TX_ROOTCAUSE.md`](NO_TX_ROOTCAUSE.md) | `NO_TX` 问题根因 |
| [`T_CONNECTION_CHECK.md`](T_CONNECTION_CHECK.md) | `t` 连接定义核查 |
| [`RESIDUE_MARGIN.md`](RESIDUE_MARGIN.md) | 余差窗口裕量 |
| [`CALIBRATION_RESULT.md`](CALIBRATION_RESULT.md) | 校准有效性结果 |
| [`OPT_ATTEMPT_20261113.md`](OPT_ATTEMPT_20261113.md) | 一次优化尝试的记录 |
| [`HANDOVER.md`](HANDOVER.md) / [`UNIFIED.md`](UNIFIED.md) | 早期交接与统一说明 |

## 八、子目录

| 目录 | 大小 | 内容 |
|:---|--:|:---|
| `evidence/` | 56M | 历史证据（按日期分目录，含图与日志） |
| `reports/` | 20M | 历史报告 |
| `paper_comparisons/` | 17M | 论文对照材料 |
| `readme/` | 13M | README 素材与图 |
| `reviews/` | 2.3M | 各轮评审记录 |
| `report/` | 2.2M | 报告草稿 |
| `spec/` | 2.0M | 规格草稿 |
| `randomization_caaz/` | 1.2M | CAAZ 随机化材料 |
| `figures/` | — | `DIGITAL_ALGORITHM.md` 引用的原图（从 PDF 渲染） |

这些目录**全部是历史材料**，不含当前生效的规格。要查证据链时才需要进。

---

## 一句话

**当前有效的只有 §一 + §二 + §三 里的十余份。**
看到 §四 之后的内容，先假设它已过期，除非能确认它描述的东西仍然成立。
