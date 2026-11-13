# ADI ISSCC 2024 §9.8 行为级复现 · 验收报告单（常驻）

> **这份文件是工程结束前的硬性对照表。任何一轮收尾，都必须逐项更新它；没有更新就不算结束。**
> 状态只允许：PASS / PARTIAL / FAIL / BLOCKED / NOT_RUN。没有实际运行记录的项目一律不得 PASS。
> 本表不是计划，是已执行事实的记录。承诺、历史测试、未经复核的描述都不能填进"结果"。

## 0. 项目身份

| 项 | 值 |
|:---|:---|
| 仓库 | `defineiocc02/20bit_SAR_ADC_Behaviour_Verification` |
| 分支 | `merge/adi-repro` |
| 原始审查基线 | `2657b0d` |
| 本次受测 HEAD | `2657b0d`（已推送；`main` 在 `bc9a16a`，未合并） |
| 工作区 | 干净（仅他人 B1 线 4 个未提交文件：`verification/vectors/generate.py`、`verification/contracts/{__init__,profile}.py`、`tests/unit/test_rtl_profile_consistency.py`） |
| 有效 Profile | `17/5` 接口 · 子阵列实装 **4 列**（第五个 C 未装配）· ADC2 **14 位** · σ=**1e-4** |
| 采用资料 | ISSCC 2024 §9.8 论文 + PPT、US10505561B2（dither）、US10511316B2（DEM）、US10507889B1（交织/预跟踪）、US10541702B1（AUX）、US10826519B1（参考）、US10516408B2（转换级） |
| 验收日期 | 2026-11-13 |

---

## A. 五项核心验收（当前状态）

| 编号 | 对象 | 通过条件 | 结果 | 依据 |
|:---|:---|:---|:---|:---|
| **G1** | 论文与专利架构 | 选定实施例的电容/节点/时序/信号路径明确，差异有记录 | **PARTIAL** | 主阵列 15×2C+B4+S4、桥 C、桥后 8/4/2/1 已核到图；**第五个 C 已确认存在（实线、无位标）但未装配**；S4 不进码本的原因已推导（5 位主码装不下 32C） |
| **G2** | 基本 ADC 行为 | 输入→采样→SADC→RDAC→RA→ADC2→重构闭合；独立电荷与码值验证通过 | **PARTIAL** | 码值侧已验证（见 B 节实测）；**独立节点求解与生产 plant 的一致**未完成 |
| **G3** | 专利核心机制 | 每项有原图、主链调用、真实样本、独立测试、开/关效果 | **FAIL** | 保留列/shuffle gate/cross-slice/AUX/参考 buffer 仅判据模块，**未接主链**；DEM 是逐片旋转而非两级 shuffler；dither 4 条 xfail（窗口 1.24 vs 需求 5.50 LSB1） |
| **G4** | 性能复现 | INL/DNL/SNR/SNDR/SFDR/DR 按正确方法测量 | **FAIL** | `check_engineering` C4 报 figures stale（`59d17e5` 生成）；现有图与数字不对应当前 profile |
| **G5** | 工程易理解性 | 唯一默认主链、模块清楚、README 可读、可独立重跑 | **PARTIAL** | 文档矛盾已清（`26c684a`）；`execute_pipeline` 671 行、`defaults.py` 1038 行未拆 |

---

## B. 基本 ADC 逐项（本轮实测）

| 项目 | 检查内容 | 状态 | 证据 |
|:---|:---|:---|:---|
| 输入与采样 | 差分电压、采样时刻、初态 | PARTIAL | 采样路径存在；逐相连接表未建 |
| 电容拓扑 | 全部实际电容/固定电容/桥/寄生/反馈 | **FAIL** | 第五个 C（哑元）未装配；解码器侧该列权重取 `2/2^30` 冒充正值 |
| SADC | 判决时序、粗码、量程、阈值 | PARTIAL | 单调性验证通过；时序未单独验 |
| RDAC | 物理单元 mask、底板切换、电荷 | PARTIAL | `fig11_masks` 进主链；第五列无掩码位 |
| RA | 余差、反馈电荷、增益、饱和 | PARTIAL | 名义 gain=32 与 plant `g_vec` 关失配下逐位相等；**人工缩放 `C_sample/C_convert`=1.031311 未解释** |
| ADC2 | 输入范围、码制、位宽、量化 | PARTIAL | 14 位、0.45 V 窗口；**窗口装不下 dither 上段（缺 4.26 LSB1）** |
| 数字重构 | 不依赖隐藏真值、恢复输入并保留误差 | PARTIAL | 浮点/定点双路存在；`x=0` 在装 5 列时偏 10.7 mV（已记录） |
| 连续转换 | 片资源、样本 ID、历史状态 | PARTIAL | 18 片调度有；跨笔状态未建模 |

### 本轮已执行的两条基线（理想：零失配、零噪声）

**① 全量程粗扫**（512 点，±2.99 V）

```
输出 1747 … 1046828（≈0.17%…99.9% FS）  单调非降 ✅  逆序 0
每步 2045 码 ≈ 1 粗 LSB（11.72 mV）✅
```

**② 单个粗 LSB 内细扫**（65 点，跨度 11.719 mV）

| 项 | 实测 | 理论 |
|:---|:---|:---|
| 输出码跨度 | **2047 LSB20** | 2048 |
| 单步 | **31 / 32 / 33** | 32.0 |
| 单调性 | 非降，逆序 0 | — |

**结论**：粗 9 位判决 + 后级细分确实拼成 20 位，跨度与比例正确（31/32/33 抖动来自 12 位 ADC2 的 32 LSB20 量化步长）。
**尚未证明**：只在 1 个粗码内验过，未做 512 码全扫；无失配/噪声，故这不是性能。

### 完整样本走查

`输入→物理片→采样电荷→SADC判决→RDAC命令→RA余差→ADC2码→20位输出`
——**未完成**。`tools/trace_one_sample.py` 只重算数字侧（A6 未接主链），且其示例字在 `2657b0d` 才修正为双支路非零。

---

## C. 专利核心技术

| 技术 | 原图 | 主链调用 | 独立正确性 | 效果对照 | 总状态 |
|:---|:---|:---|:---|:---|:---|
| 分离 SADC/RDAC | 论文 Fig.9.8.3 | 是 | 部分（前缀截断不变性） | 未做 | PARTIAL |
| RDAC Follower | 论文/专利 | **否**（`follower.py` 未在主链 import 链确认） | 有单元测试 | 未做 | PARTIAL |
| 模拟交织 18 片 | 论文 | 是 | 有 | 未做 | PARTIAL |
| 三维 DEM / 跨段 | US10511316B2 FIG.19/20 | 是（`fig11_masks`） | 部分 | **是**（旋转式，非 shuffler） | PARTIAL |
| 分路 Dither | US10505561B2 FIG.19 | 是（v1） | 部分（upper 不动 SADC 已验） | **否**（参数不自洽） | PARTIAL |
| 普通 AZ | 论文 | 是 | 有 | 未做 | PARTIAL |
| 动态参考 / RA / ADC2 | 论文 Fig.9.8.3 | 部分 | 否 | 未做 | PARTIAL |
| TRACK | US10707889B1 | **否** | 有判据 | 未做 | NOT_RUN |
| AUX | US10541702B1 FIG.1/2 | **否** | 有判据 | 未做 | NOT_RUN |
| 权值校准与重构 | 片外取系数 | 是（逐码表） | 有（含反例） | 有（A/B/C/D） | PARTIAL |

**判据模块 ≠ 已实现**：`reserved_column` / `shuffle_gate` / `cross_slice_mux` / `aux_input` / `reference_buffer` 均有测试但**未接主链**。

---

## D. 性能验收

| 指标 | 论文 | 当前行为模型 | 状态 |
|:---|:---|:---|:---|
| 分辨率 | 20 bit | 结构上成立（B 节细扫） | PARTIAL |
| 采样率 | 40 MS/s | 未验证 | NOT_RUN |
| DR | 94.2 dB | 94.33 dB（σ=1e-4 拟合配置） | **不得声称**（σ 为拟合值） |
| 输入折算 NSD | 9.3 nV/√Hz | 9.1066 nV/√Hz（小信号）/ 8.5489（大信号） | **两个身份未统一** |
| INL / DNL | — | 无有效值 | NOT_RUN |
| SNR / SNDR / SFDR | — | 无有效值 | NOT_RUN |

**硬性禁止**：调 σ 贴近论文后宣称独立复现；256 点平均传输误差冒充全 20 位 INL；粗网格差分冒充单码 DNL；浮点 LUT 冒充 RTL 定点校准。

---

## E. 工程易理解性

| 项 | 状态 | 说明 |
|:---|:---|:---|
| 唯一默认入口 | PARTIAL | `python -m adi_model` 是入口，但 `tools/` 有 7 个脚本各自装配 |
| 源码结构 | PARTIAL | 七包分层合理；`execute_pipeline` 671 行、`defaults.py` 1038 行 |
| 模块 README | PARTIAL | 7 包都有；**未嵌原图** |
| 根 README | PARTIAL | 有导航，但状态层次仍需读者先懂定义 |
| 结果可信 | **FAIL** | C4 stale |
| 调试入口 | PARTIAL | `trace_one_sample` 仅数字侧 |
| 新人走查 | **未做** | 需一次只靠 README + 代码的独立走查 |

---

## F. Python / 定点 / RTL

| 层 | 条件 | 结果 |
|:---|:---|:---|
| L1 Python 浮点 | 主链与技术正确 | PARTIAL |
| L2 Python 定点 | 系数/位宽/舍入/饱和正确 | PARTIAL（有 `recon_core` 101 向量，但属旧几何） |
| L3 RTL 模块 | 同合同逐位一致 | **NOT_RUN**（`results_module.json` = `NOT_RUN`；向量 `meta.json` 仍是 63/8） |
| L4 RTL 顶层 | 配置加载、转换、reset、stall、排空 | **ELABORATED_ONLY** |
| L5 RTL+plant 闭环 | RTL 命令驱动行为级 plant | **NOT_RUN** |

---

## G. 阻止合并 main 的硬性问题（当前存在）

| # | 问题 | 位置 | 证据 | 最小修复 |
|:--:|:---|:---|:---|:---|
| 1 | 用极小伪权重掩盖拓扑不一致 | `defaults.py:388` `dummy = 2/2^30` | 注释自述"电学贡献可忽略" | 改为显式类型：真电容则保留真实值+固定轨常数项；padding 则显式不存在 |
| 2 | 隐式缩放改变已保存库仑 | `pipeline_engine.py` `q_free*(C_convert/C_sample)`，比 1.031311 | `test_reconstruction_gain_pairing` 的 `ratio==1.0314` 守的就是它 | 用逐相节点方程表达，或删除并重推 |
| 3 | 声称完成的机制未进主链 | `control/{reserved_column,shuffle_gate,cross_slice_mux,aux_input,reference_buffer}.py` | 主链 import 链中无引用 | 要么接入，要么 README 降级为"判据" |
| 4 | 多套默认规格并存 | Python 17/5/14bit vs RTL 63/8/12bit | 向量 `meta.json` 与 `rtl_params.vh` | 冻结单一 profile 后再改 RTL |
| 5 | 图与结果不对应当前 profile | `docs/reviews/20261113_p3p4/figures/` | C4 stale（`59d17e5`） | 物理定稿后一次重跑 |
| 6 | 指标前提不独立 | σ=1e-4 由 DR/NSD 反解 | `defaults.py` 推断表 | 标为 fitted profile，另立物理先验 |

---

## H. 最终审批（当前）

| 项 | 结论 |
|:---|:---|
| 基本 ADC 验收 | **PARTIAL** |
| 专利机制验收 | **FAIL** |
| 性能对标验收 | **FAIL** |
| 工程易理解性 | **PARTIAL** |
| 数字与 RTL | **NOT_RUN** |

**当前实际完成**：输入→20 位码的粗/细链路在理想条件下单调、满量程、细分比例正确（2047/2048）；名义口径与 plant 同源（关失配逐位相等）；校准重构方程已修正并有反例；验证器能拒绝缺行/空 dump/非零退出/全片翻位。

**当前不能声称**：任何性能指标命中论文；任何专利机制"已复现"；RTL 与行为模型一致；保留列/AUX/参考 buffer 已实现。

**与论文最大差距**：机制层——DEM 是旋转不是两级 shuffler；dither 参数装不进第二级窗口；保留列/跨片/TRACK/AUX 全部未进主链。

**下一步最少要改什么**：① 消掉 `dummy = 2/2^30` 与 `C_sample/C_convert` 两处"看起来闭合"的处理；② 用独立节点矩阵对 x=0.3 V 复算，与 plant 比对；③ 512 码全扫出真 DNL。三项通过前，不得进入性能对标。

**是否建议批准合并 main**：**否**。最终决定由用户作出。
