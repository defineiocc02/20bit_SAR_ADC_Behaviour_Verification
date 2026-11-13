# 片上数字算法复现计划（D1–D6）

> 唯一目标：复现 ADI ISSCC 2024 S9.8 的**片上数字控制与数字重构算法**。
> 状态：`NOT_RUN` / `PARTIAL` / `PASS` / `FAIL` / `BLOCKED`。
> 判定规则：**模块存在、import 计数、测试文件存在、历史对拍记录都不能单独证明 PASS。**
> 每项必须写：原文依据 · Python 函数 · RTL 模块 · 测试文件与命令 · 实测结果 · 未过原因。
> 修订：2026-11-13，受测 HEAD `35d5542`（计划修订本身在此之后）；本文只记录已执行事实。

---

## 0. 实施例冻结

**DEM 主实施例 = US10511316B2 Fig.19–21**（两级 shuffler + 保留列 + MUX + 纵向分配）。
Fig.14 仅作原理对照，**不作第二套默认实现，暂不要求独立 RTL**。

专利未披露的随机序列、LFSR、流水延迟：采用**明确标注的工程设计**，
不声称与 ADI 原芯片逐位相同。

**旧 63/8 向量只作历史结果，不用于本规格验收。**

---

## 1. 数字接口合同

位宽以源码实际声明为准；未披露者标 **【工程】**。

| 模块 | 输入 | 输出 | 身份/时序 | 依据 |
|:---|:---|:---|:---|:---|
| Scheduler | sample_id, clk | `acq[8]` 物理 ID、`conv_source_cycle` | 每周期一次分配 | 论文 Fig.9.8.1 |
| Follower | 已决前缀 `p_i` | 逐事件 RDAC 命令 | 事件序 `event_index`【工程】 | 论文 Fig.9.8.1 |
| DEM | 粗码(9b)、片 ID、横/纵相位(各4b)【工程】 | `main[17]`、`sub[5]` 布尔 mask | 相位在转换前冻结 | US10511316B2 |
| Dither | 扰动字(6b)、粗码 | `upper_code`(int)、`lower_v`(V)、`total_v`(V) | 每笔冻结 | US10505561B2 |
| Weight Recon | ADC2 码、mask、片 ID、Q30 权重 | 20-bit offset-binary | 11 拍延迟【工程】 | 定点合同 |
| Digital Top | 全部上列 | 输出码、valid、错误标志 | reset/stall/排空 | 【工程】 |

**三条关键接口（RTL 可直接执行）**

| 关系 | 规则 |
|:---|:---|
| Follower → DEM | RDAC 命令随已决前缀逐事件更新；**DEM 的横/纵选择状态在转换开始前冻结**，转换过程中不得改变物理单元归属 |
| DEM → Weight Recon | 重构必须接收**真实物理片 ID + 实际 main/sub mask + 对应权重**，不能只收粗码；否则 DEM 换了电容，校准不知道用的是哪一组 |
| Dither → Digital Top | RTL 只传**扰动字、整数/定点控制量、码修正量与数字去扰量**；`lower_v`/`total_v` 是 Python 行为模型里的电压等效量，**不作为片上数字接口** |

---

## D1 Slice Scheduler

**要复现**：18 池 8 采集 / 8 转换 / 2 备用，样本身份贯穿。

**当前**：`dem3d.round_robin_plan` 实现窗口分配。

| 判据 | 状态 | 证据 |
|:---|:---|:---|
| 相邻采集窗互斥 | **PASS** | `test_scheduler_identity.py`（4 passed） |
| 每笔 8 个不同物理 ID | **PASS** | 同上 |
| 纯函数确定性（同参数重跑与连续子段一致） | **PASS** | 同上 |
| **运行中 stall 后保持状态 / reset 清空未完成事务 / valid-ready 背压 / 交界处身份** | **NOT_RUN** | 现有测试只证明纯函数可重复，**不是状态机 reset/stall 行为** |
| 整周期使用均衡 | **PASS** | 同上 |
| **无样本丢失或重复（计数守恒）** | **NOT_RUN** | 无 `N_acc = N_ret + N_canc + N_pend` 检查 |

**下一步**：补样本计数守恒与 valid 关联。

## D2 Follower

**要复现**：SADC 已决前缀 → RDAC 逐事件命令。**不要求改变最终输出码。**

| 判据 | 状态 | 证据 |
|:---|:---|:---|
| 只用已产生的判决 | PARTIAL | 前缀截断不变性（28672 检查） |
| 未来判决不影响过去命令 | PARTIAL | 同上 |
| 最终 mask 与粗码一致 | **PASS**（模块级） | `final_charge_mismatch_c == 0`（既有）；新增末拍码逐位一致 |
| **逐事件路径与"一次加载"确实不同** | **PASS**（模块级） | `test_follower_reaches_the_same_final_code_by_a_different_event_path`：中间事件必不同、末事件必相同 |
| **真实逐事件命令导出** | **PASS**（模块级） | `FollowerTrace.applied_codes` 存在；`test_every_event_emits_one_command_and_ignores_future_bits` 验 `checked = n×7` 且偷看实现必被抓 |
| **是否进入主链** | **FAIL** | 已核实调用路径：`follower.py` 存在且 21KB、有 12 项测试，但**全仓零 import**（`grep import.*follower` 在 src/ 无结果），`pipeline_engine`/`defaults` 中出现次数为 0 |

**下一步**：确认 `follower` 是否被 `dem3d` 间接调用；若否，接入并出逐事件 trace。

## D3 三维 DEM（主实施例 Fig.19–21）

**要复现**：二进制→一元→横向 shuffler→保留列→纵向分配→物理 mask。
**普通循环旋转 + 权重守恒不构成三维 DEM 完成。**

| 判据 | 状态 | 证据 |
|:---|:---|:---|
| `Σu = N_H` 名义权重守恒 | PARTIAL | 512 码不变量测试 |
| 置换无重复/丢失/非法选通 | PARTIAL | 同上 |
| **保留列恰好一列、位置合法** | **NOT_RUN** | 仅 `reserved_column.py` 判据 |
| **纵向不变式 `2Σy = 8B4+4B5+2B6`** | **NOT_RUN** | SPEC 算式，未实现 |
| **案例 (1,1,0) → 12C** | **NOT_RUN** | 待实现 |
| 512 码 × 合法状态全覆盖 | PARTIAL | 状态覆盖不全 |
| 物理单元使用统计（按真实片） | PARTIAL | 已改按 `slice_ids` 散列 |

**下一步**：实现保留列 + 两级 shuffler，并用 SPEC 的定向向量验收。

## D4 Split Dither

**要复现**：扰动字分路、码修正、数字去扰。
**七片专利示例与论文八片工作组分开说明；`/7` 不得直接当八片物理实现。**

| 判据 | 状态 | 证据 |
|:---|:---|:---|
| upper 不动 SADC 判决 | **PASS** | `test_dither_split_causality.py` |
| lower 动 SADC 判决 | **PASS** | 同上 |
| 两支路同时非零的实例 | **PASS** | word=33 |
| **64 字全遍历** | **NOT_RUN** | 仅 4 个定向字 |
| 去扰正确性与量化边界 | PARTIAL | 有输出一致性测试 |
| `/7` 与八片等效性 | **NOT_RUN** | 未证明 |

**不要求**每个扰动字输出完全相同；验的是去扰正确性与统计表现。
**注**：上表三条 PASS 仅覆盖已有定向字，**D4 整体仍为 PARTIAL**（64 字未全遍历）。

## D5 Weight Reconstruction

**要复现**：片外系数加载 → 物理片/mask 寻址 → 定点累加 → 20 bit。
**不要求复现未披露的片外训练算法。**

| 判据 | 状态 | 证据 |
|:---|:---|:---|
| 训练与重构同一方程 | **PASS** | 审查反例 0.2 ✓ |
| 不同片权重不混用 | PARTIAL | 按 `slice_ids` 散列 |
| 零失配恢复名义输出 | PARTIAL | `test_reconstruction_gain_pairing` |
| 与独立 Python 定点一致 | PARTIAL | `recon_core` 101 向量（**旧几何，不作新规格证据**） |
| **系数加载/版本切换/reset** | **NOT_RUN** | 无定向测试 |

逐 slice offset/gain 列为**可选扩展**，不作主线阻塞。
**注**：`x=0.2` 反例只证明浮点方程正确；**D5 的 RTL 系数寻址与定点运算仍为 NOT_RUN**。

## D6 Digital Top（最终验收，不可省）

**要复现**：`Scheduler → Follower → DEM/Dither → Weight Reconstruction → 20-bit Output`
的完整数字事务。

| 判据 | 状态 |
|:---|:---|
| 完整事务可执行（含 sample_id/slice_id/event_index/valid/延迟） | **NOT_RUN** |
| reset / stall / 排空下输出与计数正确 | **NOT_RUN** |
| Python 参考与 RTL **逐事件、逐字段**对拍 | **NOT_RUN** |

允许用理想行为级 ADC2 码作激励，**不要求晶体管级 plant**。
必须使用同一套冻结规格与**新生成**的向量。

---

## 2. 执行顺序

```
D1 + D2（控制基础）→ D3 DEM → D4 Dither → D5 重构 → D6 整链
```

每个模块完成即做对应 RTL 验证，**不留到最后批量对拍**。

## 3. 移出本专项硬门槛的内容

完整模拟节点方程、RA 晶体管、参考缓冲、PEX/AMS/版图、AUX/TRACK 的模拟实现、
片外校准训练算法、性能拟合——记为系统级依赖或后续扩展，**不阻止数字复现**。

## 4. 下一步最优先的三个代码任务

1. **D2**：核实 `follower` 的真实调用路径；若未接主链则接入，并导出逐事件 RDAC 命令。
2. **D1**：补样本计数守恒（`N_acc = N_ret + N_canc + N_pend`）与 valid 关联测试。
3. **D3**：实现保留列 + 两级 shuffler，**只按所选 Fig.19–21 的控制关系**验收：
   `2Σy = 8B4+4B5+2B6`（⇒ Σy ≤ 7）、保留列恰好一列、案例 (1,1,0)→12C。
   **`ΣS4 = 4B5+2B6+B7` 属 Fig.14，不得与 Fig.19–21 联合验收。**
