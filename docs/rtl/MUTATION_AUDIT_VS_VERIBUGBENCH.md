# 变异审计：对齐 VeriBugBench 框架（Phase 0 完成，Phase 1/2 未做）

**参照对象**：Meng et al., *VeriBugBench: An Empirically Grounded Framework for Constructing Verilog RTL Debugging Benchmarks*, arXiv:2609.18022（2026-09-16，cs.AR）。

| 阶段 | 状态 |
|:---|:---|
| **Phase 0** 账本审计（算子映射 + 覆盖矩阵 + 保留判据复核 + 偏差声明） | **完成（本文件）** |
| Phase 1 变异 campaign（自写注入器 + 观测 trace + 均匀抽样 + 变异分数） | **未做** |
| Phase 2 数字与偏差声明入库、回链 `P2_INTERFACE.md` | **未做** |

---

## §0 目的、边界与"转用"声明

**借它三样东西**：
1. ⟨动作, 构件, 上下文⟩（`a ∈ {update, insert, delete, move}`）与 **19 个算子 / 5 个构族**的分类法；
2. **保留判据**：编译通过 **且** 完整执行 **且** 在输出的观测 trace 上出现**字段级**差异（X/Z 计为观测值；**公共前缀之外不算**）；
3. **指标定义**：变异分数 `MS = K/M`、行覆盖、单例行迹唯一度、时序状态熵。

**不借它的工具链**：其前端是 **Pyverilog（Verilog 子集）**，而本项目 `rtl/` 是 **SystemVerilog**（`logic` / `always_ff` / `'0` / `$signed` / 打包数组），Pyverilog 解析不了。故 Phase 1 的注入器必须**自写**，且注入器自身要按本项目纪律做**两端验证**（能造出"必被检出"的变异，也能造出"必然等价"的变异）。

**★ 转用声明（所有数字必须随此句搬运）**：该论文的测度"characterize benchmark construction and artifact quality **rather than the performance of a particular debugging technique**"。我们拿它的指标来量**我们自己 TB 的故障检出强度**，属**转用**；不得反过来声称"我们的 TB 达到了该基准的某个水平"。

---

## §1 论文框架要点（只作交叉核对的引用）

| 构族 | 族内算子 |
|:---|:---|
| Expression | ExprUpdate / ExprInsert / ExprDelete |
| Bit-vector & access | PartselectUpdate / PartselectInsert / PartselectDelete / PointerUpdate / PointerInsert / PointerDelete |
| Assignment | NSubUpdate / NSubInsert / NSubDelete / NSubMove / AssignN2B / AssignB2N |
| Timing | EdgeFlip / EdgeInsert / EdgeDelete |
| Port | IOFlip |

论文自己的关键限定：算子保留条件之一是"**逆变换与适用条件能实现为 Pyverilog AST 上的受控单点改动**"；`ExprUpdate` 的定义**明确排除单字面量替换**。保留判据的精确形式见 §4。

---

## §2 我们的实例账本（14 条）

- **P1 / P2 的真实缺陷 7 条**（P1 3 条 + P2 4 条）：原文与处置见 `docs/rtl/P2_INTERFACE.md` §15 缺陷账。此处只登记其**动作/构件/算子归属**。
- **定向变异 7 条**：定义见 `sim/artifacts/mutation_check.py` 的 `MUTANTS` 字典（含 1 条**负对照**）。

| # | 来源 | 文件 | 改动（前 → 后，摘要） | 动作 | 构件 | 论文算子 | 证据 |
|--:|:--|:--|:--|:--|:--|:--|:--|
| 1 | P1 缺陷 | `swap_decode.sv` | `i*` 行列两半写反 | update | 索引表达式 | PointerUpdate（近似） | §15 |
| 2 | P1 缺陷 | `unit_therm.sv` | `3'(N_ACT)` 把 8 截成 0 | update | 位宽转换 | **库外/近似**（无 cast 族） | §15 |
| 3 | P1 缺陷 | `dither_gen.sv` | `k_cmd` 有符号/无符号混用 | update | 表达式 | ExprUpdate（近似：改符号性） | §15 |
| 4 | P2 缺陷 | `div_floor.sv` | `qbits[s]` → `qbits[P_STAGES-1-s]` | update | 位选/索引 | PointerUpdate | §15 + `sim/artifacts/decide_divfloor.py` |
| 5 | P2 缺陷 | `recon_core.sv` | 溢出锁存位置 `start` 拍 → `stage_b` 拍 | **move** | 语句位置 | **NSubMove** | §15 |
| 6 | P2 缺陷 | `recon_core.sv` | 溢出/结构性错误分支**漏**拉 `dout_valid` | **insert**（修复方向）→ 注入为 delete | 非阻塞赋值 | **NSubDelete** | §15 |
| 7 | P2 缺陷 | `recon_core.sv` | `clip_lo_c/clip_hi_c` 有符号 → 无符号比较 | update | 表达式 | ExprUpdate | §15 |
| 8 | 定向变异 `ws_wok` | `weight_store.sv` | `w_ok = (wr_data != W_ZERO) && (wr_data < W_MAX)` → `1'b1` | update | 守卫表达式 | ExprUpdate / ExprDelete | `mut_ws_wok.txt` |
| 9 | 定向变异 `ws_cap` | `weight_store.sv` | 容量阈值 `2^60` → `2^50` | update | **字面量** | **库外** | `mut_ws_cap.txt` |
| 10 | 定向变异 `ws_capoff` | `weight_store.sv` | 删去容量条件项 `&& (sum_new < SUM_MAX)` | **delete** | 表达式项 | **ExprDelete**（**负对照：期望不被检出**） | `mut_ws_capoff.txt` |
| 11 | 定向变异 `sr_nonsticky` | `status_regs.sv` | 删去自保持项 `acc_ovf_sticky \| …` | **delete** | 表达式项 | **ExprDelete** | `mut_sr_nonsticky.txt` |
| 12 | 定向变异 `rd_noclear` | `rdac_drv.sv` | `slice_sel <= '0` → `slice_sel <= slice_sel` | update | 非阻塞赋值 | **NSubUpdate** | `mut_rd_noclear.txt` |
| 13 | 定向变异 `cf_rdac10` | `ctrl_fsm.sv` | `ph == PW'(11)` → `PW'(10)` | update | **字面量**（带 cast） | **库外** | `mut_cf_rdac10.txt` |
| 14 | 定向变异 `cb_le` | `calib_regs.sv` | 空量程判据 `<` → `<=` | update | 关系表达式 | **ExprUpdate** ★ 最标准的一条 | `mut_cb_le.txt` |

> 表内 8/10/11/12/13/14 的"前 → 后"取自 `mutation_check.py` 的字符串常量，**逐字可核**。

---

## §3 覆盖矩阵（19 个算子）

| 构族 | 已覆盖 | 空 |
|:---|:---|:---|
| Expression | ExprUpdate（#3,#7,#8,#14）· ExprDelete（#10,#11） | **ExprInsert** |
| Bit-vector & access | PointerUpdate（#1,#4）· PartselectUpdate（#2，近似） | **PartselectInsert · PartselectDelete · PointerInsert · PointerDelete** |
| Assignment | NSubUpdate（#12）· NSubDelete（#6）· NSubMove（#5） | **NSubInsert · AssignN2B · AssignB2N** |
| Timing | — | **EdgeFlip · EdgeInsert · EdgeDelete（整族为空）** |
| Port | — | **IOFlip（整族为空）** |

**统计**：5 个构族里 **3 个有实例**；19 个算子里 **11 个为空**（Timing 整族 3 个 + Port 1 个 + Partselect/Pointer 的 Insert/Delete 4 个 + ExprInsert 1 个 + NSubInsert/AssignN2B/AssignB2N 3 个）。

**落在论文算子库之外**：**2 / 13 ≈ 15%**（#9 `ws_cap`、#13 `cf_rdac10`）——两条都是**纯字面量替换**。论文把 `ExprUpdate` 定义为"表达式**算子**更新，**而非单字面量替换**"。含义：**我们目前花的验证力气里，有一块在这份经验语料里不构成一类修复模式**。要么把它们标为库外、要么不进"与论文可比"的统计。

---

## §4 保留判据复核（逐条对论文的 5 条）

论文判据（原文要点）：**编译通过** → **完整执行** → 与干净设计在项目提供的 CSV 观测 trace 上**以记录的时间字段为索引**、按行序比较**公共行前缀**、**每个日志观测字段按字符串**比较、**X/Z 计为已观测值**；**超出公共前缀的部分不构成可观测性**；任一行任一字段不同即 output-observable。

| 判据 | 我们现状 | 结论 |
|:---|:---|:---|
| ① 编译通过 | 全部实例都在 `run_rtl_sim.py` 里真编译过 | **满足** |
| ② 完整执行 | `sim/tb/p3_top_tb.sv` 与 `p2_tb` 在**跑完之后**才 `$fatal(1)` 报 FAIL；断言用 `$error` | **满足**（不靠提前中止"变红"） |
| ③ 输出字段级差异 | 我们的判据是 **`MISMATCH:` 计数** + **SVA 断言关键字命中**（见 `mutation_check.py` 的 `expect`） | **只到"计数级/断言级"，未到"字段级"** |
| ④ 差异在公共前缀内 | 我们没有逐样本落盘的观测 trace，无法按论文方式对齐 | **无法判定** |
| ⑤ X/Z 计为观测值 | 我们的比较器是否把 X/Z 计入，**未核** | **待核** |

**必须补的一件事**：给 TB 加 `+trace=<file>`，逐样本落 `{sample_idx, dout, clip, analog_ovf}`（`sample_idx` 充当论文里"记录的时间字段"），再实现论文比较器。**在此之前，本项目的"门禁能变红"只能宣称到"计数级/断言级"这一步**，不能宣称到字段级。

**一处我们比论文更严的地方**：`ws_capoff`（#10）是**故意设计成不被检出**的**负对照**。论文的漏斗把这类 equivalent / unobserved mutant **丢弃**；我们把它**留下来当对照**，用来证明"断言不是无条件变红"。这条应当保留。

---

## §5 偏差与不可比声明（必须随数字一起搬运）

1. **样本偏差（最要紧）**：14 条里 **11 条是"我们自己的 TB 已经抓到的缺陷的回退"**，2 条（#10 除外）是照着既有断言设计的 → **样本对"可观测"是被选择过的**。任何由它算出的变异分数**向上偏**，**不得**当作变异分数估计值。要对齐论文漏斗，必须重新做**均匀抽样**（合法机会 → 每算子均匀抽样 → 保留）。
2. **抽样概率**：我们**没有**论文那种"枚举合法 ⟨算子,文件,节点⟩ 机会 → 采样 b=20 → 去重"的漏斗，故**无法**报"合法机会数 / 候选数 / 保留数"三档。
3. **单项目**：我们只有 1 个设计（`rtl/` 16 个 `.sv`），**不能**做跨项目未加权宏平均；只能报单项目数字（项目级 = 实例级）。
4. **库外实例**：#9 / #13 见 §3。
5. **判据强度**：见 §4，Phase 1 之前是"计数级"。
6. **前端约束**：Pyverilog 不支持 SystemVerilog → 与论文的**算子实现细节**不可逐条对照，只能对照**分类与判据**。

---

## §6 Phase 1 计划（待执行）

1. **自写注入器** `tools/mutate_rtl.py`：只实现**本项目 SV 子集内可做且能给出守卫**的算子 ——
   Expression（Update/Delete + 有限 Insert）、Bit-vector & access（PartselectUpdate/Delete、PointerUpdate）、
   Assignment（NSubUpdate/NSubDelete/NSubMove）、**补齐 Timing（EdgeFlip/Insert/Delete：改 `always_ff` 的敏感沿/事件控制）**、
   **补齐 Port（IOFlip：改端口方向或位宽）**。每条算子 = matcher + guard + transform，并写明**在本 SV 子集下的适用边界**。
2. **TB 补 `+trace=<file>`**：逐样本落 CSV，`sample_idx` 为时间字段。
3. **比较器** `tools/trace_compare.py`：实现论文判据（公共前缀 + 逐字段字符串 + X/Z 计入），输出 killed / unobserved / 分类计数。
4. **Campaign** `tools/mutate_campaign.py`：枚举合法机会 → 每算子均匀抽样 → 逐个编译+跑 → 判 killed → 出 `MS`、每族覆盖率、**未检出清单**。
5. **注入器两端验证（先做，且不做完不许跑 campaign）**：① 已知必被检出的变异（如 #14 `cb_le` 的等价形式）**必须 killed**；② 已知等价/无观测的变异（#10 `ws_capoff` 一类）**必须 NOT killed**。**给不出这两端，campaign 的数字不可信。**
6. **Phase 2**：数字 + §5 声明写进本文件 §7，并在 `docs/rtl/P2_INTERFACE.md` **追加**一节回链本文件。

---

## §7 Phase 1 结果

**未做。** 本节的占位是刻意的：在注入器完成两端验证、且 TB 有 `+trace` 之前，任何"变异分数"都不能进本文件。
