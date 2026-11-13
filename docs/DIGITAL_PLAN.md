# ADI ISSCC 2024 S9.8 · 片上数字算法复现计划书

> 本文件是**执行计划与完成标记**。每完成一项就在这里改状态并附证据链接。
> 状态：`NOT_RUN`（未跑）/ `PARTIAL`（有证据但不完整）/ `PASS`（判据全过）/ `BLOCKED`。
> 与 `ACCEPTANCE_CHECKLIST.md` 的区别：那张是**总验收**，这张是**数字算法的执行计划**。

更新：2026-11-13 · 受测 HEAD `fffb85f`（已推送）

---

## 0. 任务边界

复现**片上数字算法**：调度、follower、DEM、dither、权重重构。
**不复现**晶体管级电路、不做 PEX、不建通用仿真平台。

判据：**原文依据 → 公式 → Python 真执行 → RTL 真实现 → Python/RTL 对拍**。

---

## 1. 冻结的数字接口（唯一口径）

| 模块 | 输入 | 输出 | 状态 |
|:---|:---|:---|:---|
| Slice Scheduler | sample_id、时钟 | 8 活动物理片 ID、采样/转换命令 | 18 池 |
| Follower | 已决前缀 | RDAC 逐事件命令 | 前缀状态 |
| DEM | 粗码、片 ID、DEM 状态 | 各片 main/sub mask | 横/纵状态 |
| Dither | 扰动字、粗码 | 分路控制量、RDAC 修正、去扰量 | 扰动状态 |
| Weight Recon | ADC2 码、mask、片 ID、系数 | 校准后 20 bit | 系数寄存器 |
| Digital Top | 时钟/reset/valid | 输出码、valid、错误标志 | 流水状态 |

---

## 2. 执行项与完成标记

### D1 Slice Scheduler — 18 池 8/8/2

| 判据 | 状态 | 证据 |
|:---|:---|:---|
| 同片不同时在 ACQ 与 CONV | PARTIAL | `dem3d.round_robin_plan` 已实现 |
| 每笔样本有确定物理片 ID | PARTIAL | `slice_ids` 贯穿 |
| ID 随样本正确传递 | PARTIAL | 事件记录含 slice_ids |
| reset/stall 下身份不丢不乱 | **NOT_RUN** | 无定向测试 |
| 连续转换计数守恒 `N_acc=N_ret+N_canc+N_pend` | **NOT_RUN** | 未实现计数检查 |

### D2 Follower — 已决前缀驱动

| 判据 | 状态 | 证据 |
|:---|:---|:---|
| 每次更新只用已产生的判决 | PARTIAL | 前缀截断不变性 28672 检查 |
| 改未来判决不影响过去命令 | PARTIAL | 同上 |
| 最终命令与粗码一致 | PARTIAL | 同码三路径净电荷闭合 |
| 输出真实逐事件命令（非回放） | **NOT_RUN** | 无逐事件 trace |
| **进入主链** | **FAIL** | `control/follower.py` 主链 import 计数 = 0 |

### D3 DEM — 先选实施例

**实施例选择**：主目标 **Fig.19–21**（含保留列）；Fig.14 作对照、不进主链。
（用户未裁决时按此默认，文档中已标注）

| 判据 | 状态 | 证据 |
|:---|:---|:---|
| 二进制↔一元名义权重守恒 | PARTIAL | `Σu = N_H` 有测试 |
| 置换无重复/丢失/非法选通 | PARTIAL | 512 码不变量测试 |
| 保留列恰好一列、位置合法 | **NOT_RUN** | 仅判据模块 |
| 纵向不变式 `2Σy=8B4+4B5+2B6` | **NOT_RUN** | 仅 SPEC 算式 |
| 案例 (B4,B5,B6)=(1,1,0) → 12C | **NOT_RUN** | 待实现 |
| 512 码 × 合法状态全覆盖 | PARTIAL | 有逐码测试，未覆盖全部状态 |
| 物理单元使用统计（按真实片） | PARTIAL | 已改按 `slice_ids` 散列 |

### D4 Split Dither

| 判据 | 状态 | 证据 |
|:---|:---|:---|
| 64 字全遍历 | **NOT_RUN** | 仅 4 个定向字 |
| upper / lower 分别验证 | PASS | `test_dither_split_causality.py`（upper 不动判决、lower 动） |
| 至少一组两支路同时非零 | PASS | word=33（U=4, L=1/7） |
| 极性与码制明确 | PARTIAL | v1 统一 subtract，标为假设 |
| 不重复减 upper | PARTIAL | 有去扰测试 |
| 最终输出不随字变 | PASS（v1） | 四字输出一致 |
| `/7` 与八片等效性 | **NOT_RUN** | 未证明，已标 |

### D5 权重校准与数字重构

| 判据 | 状态 | 证据 |
|:---|:---|:---|
| 不同片权重不混用 | PARTIAL | 按 `slice_ids` 散列 |
| mask 变则系数跟着变 | PARTIAL | 重构读实际 mask |
| 零失配恢复名义输出 | PARTIAL | `test_reconstruction_gain_pairing` |
| 非零失配下与独立 Python 定点一致 | PARTIAL | 反例 0.2 ✓ |
| 系数加载/版本切换/reset | **NOT_RUN** | 无定向测试 |
| 逐 slice offset/gain | **NOT_RUN** | 全局 LUT ≠ 逐片 |

---

## 3. RTL 与对拍

| 层 | 状态 | 说明 |
|:---|:---|:---|
| L1 Python 浮点 | PARTIAL | 基本 ADC 码域已验 |
| L2 Python 定点 | PARTIAL | 方程已修；`abs=0` 假守卫已清 |
| L3 RTL 模块位真 | **NOT_RUN** | 向量 `meta.json` 仍是 63/8 |
| L4 RTL 顶层 | **ELABORATED_ONLY** | 未跑真实事务 |
| L5 RTL+plant 闭环 | **NOT_RUN** | 未建 |

**旧 63/8 向量只作历史证据，不替新 profile 背书。**

---

## 4. 四类有效图（只交这四类）

| 图 | 证明什么 | 状态 |
|:---|:---|:---|
| 数字架构与数据流 | 各模块如何组成数字链 | 待做 |
| DEM 物理单元选择图 | 不同 DEM 状态下的选通分配 | 待做 |
| Dither 与数字去扰 | 扰动字如何改变控制并被消除 | 待做 |
| 校准前后输出误差 | 已知失配能否被重构修正 | 待做 |

**必须来自实际运行记录，不用示意图。**

---

## 5. 下一步（按序，不并行）

1. **D2 follower 接入主链**（当前 import=0，最该先补的一环）
2. D1 补 reset/stall 身份守恒测试
3. D3 实现 Fig.19–21 保留列 + 两级 shuffler，用三条不变式验收
4. D4 做 64 字全遍历
5. D5 补系数加载/版本切换/reset
6. profile 冻结后重导出 RTL 参数并重跑 V1

每完成一项，回到本文件把状态改掉并附证据。
