# W0-H 已知问题与负例登记

> 这些是**调查线索**，不是"已确认的算法错误"。W1 前逐条按当前代码复现；不能因为看到一条就改公式。
> 每条给：证据位置 / 现象 / 影响 / 当前判定 / 处理阶段 / 反例。

---

## 1. `ReferenceConfig` 显式 `precharge_frac=0.0` 被拒绝

| 项 | 内容 |
|:---|:---|
| 证据 | `src/adi_model/behavioral/reference.py:92`（task 树）：`"precharge fraction 0 means the reference starts from the full swing; that is a modelling choice, pass it explicitly rather than by default"` |
| 现象 | `tau_s > 0` 且 `precharge_frac == 0.0` 时，`__post_init__` 直接 `ValueError`；**无法区分"未指定"与"显式合法 0"** |
| 影响 | 无预充对照无法运行；用户被迫编一个非零值，污染参考建立实验 |
| 判定 | **真实接口缺陷**（不是物理错误） |
| 处理 | W3：`precharge_frac: float | None = None`；`None` 表示缺项，显式 0.0 接受；保留"0 是建模选择"的注释 |
| 反例 | 两个对照：`None` 必须拒绝；`0.0` 必须接受并产出"全摆幅起始"曲线 |

---

## 2. 64 个 dither 字的共同名义下限 −4.855238970588 V

| 项 | 内容 |
|:---|:---|
| 证据 | `README.md:108/139`；`task1/SPEC.md:26`；`task2/evidence/eight_negative/README.md:56`；`handoff/behavior_independent_charge.json:15`；`task2/eight_rtl/SPEC.md:40` |
| 现象 | 全部 64 字、八片实际选择下的最苛刻名义下限为 **−4.855238970588 V**，不是 −5 V；目标 ±5 V 端点未闭合 |
| 影响 | 满幅端点验收不能通过；不能用"最终码未 clip"掩盖内部 SADC lower/upper 过载 |
| 判定 | **真实范围缺口**；端点候选用理想 `x` 生成预采样 flag，是诊断上界，不是可实现硬件 |
| 处理 | W6/W7：给出因果 flag 来源、延迟、误差；拒绝率/超窗单独统计；不得静默限幅或压缩有效量程 |
| 反例 | 用未来输入真值造 flag → 必须被检查拒绝；满幅扫描必须报 lower/upper 余量，不只报最终码 |

---

## 3. 非均匀码本 `k=c−U` 的残余 4.596 mV

| 项 | 内容 |
|:---|:---|
| 证据 | `task1/evidence/bridge/README.md:18`：`B5:B6` 四个组合对应 `0、4.596、9.191、13.787 mV`；Q32 重构名义闭合误差最大 `0.548 nV` |
| 现象 | 零失配、`lower=0`、`coarse=16` 时，`VD(c)−VD(c−U)=UΔ` 不成立；word0/8/32 引起约 **4.596 mV** 额外残差；数字重构仍闭合 |
| 影响 | 只看最终码会误判"没问题"；RA/ADC2 余量被吃掉；不能把它误称"去扰符号错误" |
| 判定 | **结构性余量问题**，不是符号 bug |
| 处理 | W3/W6：加 `upper_residue_budget`；逐码/逐相检查 RA/ADC2 余量；保留 Q32 闭合作为独立对照 |
| 反例 | 把 4.596 mV 归零/改符号 → 必须触发余量检查失败 |

---

## 4. "RDAC set directly to final state" 的表述

| 项 | 内容 |
|:---|:---|
| 证据 | `task2/ORIGINAL_SOURCES.md:13`；`task1/README.md` 七片示例；`task2/eight/model.py` 静态实验只算最终码 |
| 现象 | 静态实验用"最终码"是实验选择；论文的 follower 是**已决结果随过程逐步加载**，避免重复 SADC 试探/撤销 |
| 影响 | 用最终码事后反推历史事件，会冒充因果转换；早期 follower 命令合法性无法验证 |
| 判定 | **表述/因果风险** |
| 处理 | W4：保留逐位 follower trace；静态实验明确标"final-code static"；新文档改写 |
| 反例 | 只给最终码、不给早期命令 → 必须标 `NOT_CAUSAL`，不能进 ADI-05 PASS |

---

## 5. main 旧 Fig.19 静态路径的 guard

| 项 | 内容 |
|:---|:---|
| 证据 | `pipeline_engine.py:152`（zero-noise/no dynamics）、`:170`（uniform512 codebook/identity decoder）、`:195`（no active-slice offset/skew binding）、`:274`（allSIGNAL acquisition）；`dither.py:248`（512-code domain）；`rtl_observation.py:162`（Fig19 metadata static profile） |
| 现象 | 旧路径明确限制 `uniform512/identity`；这是**保护**，不是缺陷 |
| 影响 | 若为接入 task 非均匀映射而放松 guard，会把未证明的 binding 混进来 |
| 判定 | **必须保留 guard**；新增具名 binding/版本，不得放宽 |
| 处理 | W3/W6：新 binding 单独版本；观察/特征合同升级；旧路径继续可回放 |
| 反例 | 把 task 非均匀码本塞进旧 Fig19 路径 → 必须拒绝 |

---

## 6. 两种 DEM 模式的校准系数不能互换

| 项 | 内容 |
|:---|:---|
| 证据 | `task1/EXPERIMENT_STANDARD.md`（A/B/C/D 同条件、独立训练）；`task1/STATE_AND_BASELINES.md`（固定 A/B 与动态 DEM 分开训练） |
| 现象 | 固定 A/B 与动态 DEM 的系数在对方模式下会失配；配置改变必须冻结/排空/取消/重载/启用 |
| 影响 | 混用系数会把 DEM 收益/退化算错 |
| 判定 | **合同问题** |
| 处理 | W1/W3：`FrozenCalibration.mode_id` + 五步切换；错误 mode 拒绝 |
| 反例 | 把固定 A/B 系数装进动态 DEM → 必须拒绝/报错，不能静默使用 |

---

## 7. 单元逻辑起点分布 ≠ 物理 DEM 失效

| 项 | 内容 |
|:---|:---|
| 证据 | `task1/README.md` 的"诊断基线"与 `STATE_AND_BASELINES.md`：单元逻辑起点分布不均不自动等于物理 DEM 失效；要看实际物理使用与固定失配性能 |
| 现象 | 统计"哪些单元先被用"不等于失配被平均；可能只是逻辑顺序 |
| 影响 | 容易误报"DEM 坏了"或"DEM 好了" |
| 判定 | **分析口径问题** |
| 处理 | W7：报告实际物理使用次数/掩码分布 + 固定失配下的 INL/DNL，不只报逻辑起点 |
| 反例 | 只改逻辑起点顺序 → 物理性能不应变化；若变化，说明有隐藏状态泄漏 |

---

## 8. CAAZ 重新进入主依赖

| 项 | 内容 |
|:---|:---|
| 证据 | `src/adi_model/__init__.py:25-29/224-227`；`rtl/rtl_sources.f:2-3`；`rtl/sar_structural_ctrl.sv:12/29-30/107`；`rtl/sar20_digital_core.sv:60-69`；`tests/unit/test_chopping_az.py`；task `task3/**`、`task_integration` 的 `caaz_*` 端口 |
| 现象 | CAAZ 已深入包导出、RTL filelist、结构控制、顶层端口、CI、docs |
| 影响 | 只移 `task3` 不能隔离；主包/主 RTL 仍会 import/例化 |
| 判定 | **必须按 07 清单逐项拆** |
| 处理 | W1：先建 `extensions/caaz/`，再删主导出/端口/filelist/CI；普通 AZ 先重建 |
| 反例 | 移除扩展目录后主包仍能 import/CLI/sim/RTL 编译；`rg caaz` 主路径为空 |

---

## 9. 台账滞后（新增）

| 项 | 内容 |
|:---|:---|
| 证据 | `trace/source_matrix.csv` 的 PAPER-06/07/08、SLIDE-05/09 仍 MISSING；但 `src/adi_model/behavioral/reference.py`、`timing.py` 已在 M4/M5 |
| 现象 | 台账状态与代码不同步 |
| 影响 | 覆盖矩阵会低报；W6 可能重复造轮子 |
| 判定 | **台账维护缺陷** |
| 处理 | W1：重跑 `tools/check_source_matrix.py`，逐行核对 `impl_path`；把 M4/M5 标 PARTIAL/PASS |
| 反例 | 代码删了但台账仍 PASS → 必须失败 |

---

## 10. 工具/环境（新增）

| 项 | 内容 |
|:---|:---|
| 证据 | `trace/digital_status.json`：verilator/iverilog/yosys 全 `found=false`；main pytest 47 error 全 `simulator not found` |
| 现象 | RTL 回归、门级、DRC/LVS 本次无法重跑 |
| 影响 | 不能声称 L2/L3 通过 |
| 判定 | **环境缺口** |
| 处理 | W1 先补 Verilator/Icarus/Yosys；补不齐就明确 `NOT_RUN`，不引用旧收据当本次结果 |
| 反例 | 用旧 SKY130 收据套新 main → 必须拒绝 |
