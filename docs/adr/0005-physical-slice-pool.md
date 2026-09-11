# ADR 0005 — 交织 slice 必须是有记忆的物理对象

- 状态：**已采纳**
- 关联审计条目：**A02**（18-slice 调度缺少完整采样历史，物理电容未绑定选择结果）
- 影响模块：`slice_pool.py`、`pipeline.py`、`dac_arch.py`、`mapper.py`
- 回归测试：`tests/unit/test_slice_pool.py`、`tests/audit/test_audit_findings.py::TestF2SliceCausality`

---

## 1. 问题

论文 [00] 与专利 [09] Fig.16–17 描述的是一个**跨周期交错**的电容阵列：
一个样本由一个 slice 采集、由**同一个 slice**（或其合法接力者）转换。
"8 片转换 / 8 片采集 / 2 片备用"是 **18 套固定的物理 CDAC** 在时间上错开，
不是"每次抽 8 片"。

v6.1 的实现是：

```python
# scheduler.py —— 每个样本独立打乱 18 个编号
order = rng.permutation(18)
```

审计 A02 在 seed=20260910 / 8192 周期 / 8191 次转换下实测：

| 指标 | v6.1 | 物理因果要求的边界 |
|:---|--:|:---|
| 当前转换组完全等于前次采集组的次数 | 1 | — |
| 平均有多少片属于**前次采集组** | **3.5572 / 8** | 8 / 8 |
| 有 slice 在上一次转换后**立即**再被选中的次数 | 8180 | 0 |

也就是说：**约 55% 的转换用的是没有存住这个样本电荷的电容。**
"同周期 8+8 不重叠"这个检查通过，但它检查的是**并发**，不是**因果**。

另外 A02 还指出：`pipeline.py` 的物理 DAC 求值**没有接收本次 8/18 的选择**，
因此"固定调度"与"随机调度"在失配 1000 ppm / DEM 开 / 其余关闭时输出**逐位
相同**、最大差为零 —— 调度只改变了附加偏移项，没有改变 DAC 本身。

## 2. 决定

把 slice 池建模为**有状态的物理对象**：

```
PhysicalSlicePool(cfg, rng)
    .plan(n_cycles, rng, strategy)      -> 逐周期的 (acquire_group, convert_group)
    .check_causality(plan)              -> transitions / violations / mean_coverage
    .dac_error(conv, k, sid)            -> 绑定到**本次选中的物理单位**
    .signal_capacitance(conv)           -> 采样电容随选择变化（匹配效应，非增益变化）
```

两条硬约束，都是**可机器校验**的：

1. **采集 → 转换的因果延迟**：第 `n` 周期的转换组必须 ⊆ 第 `n−1` 周期的
   采集组。
2. **同周期不相交**：采集组 ∩ 转换组 = ∅（v6.1 已有的那条，保留）。

实现上提供两种策略，两者都必须通过同一套因果检查：

| 策略 | 说明 |
|:---|:---|
| `shuffle_causal` | 在"上一周期采集组"内部做随机置换；保留随机化，但是**因果的**随机化 |
| `pingpong` | 8+8+2 的确定性乒乓 |

结果（`plan(8192)`，`n_slices=18`，`n_active=8`）：

| 策略 | transitions | **violations** | mean_coverage | causal |
|:---|--:|--:|--:|:--:|
| `shuffle_causal` | 8191 | **0** | **8.0** | ✅ |
| `pingpong` | 8191 | **0** | **8.0** | ✅ |
| v6.1（参考） | 8191 | 8180 | 3.5572 | ❌ |

`mean_coverage` 从 3.5572 升到 **8.0** —— 每一次转换都用满 8 片真正持
有该样本的电容。

## 3. 后果

1. **失配不再被调度抹平。** `dac_error(conv, k, sid)` 把单位误差绑定到**被
   选中的物理单位**，因此"固定调度 vs 随机调度"的输出差不再为零：
   把转换组从 slice 0–7 换成 10–17，`max|Δe_dac| = 23.64 µV = 4.13 LSB20`
   （v6.1 为 **0.0**，因为求值根本没收选择）。这是 A02 要求"物理电容绑定
   选择结果"的直接体现。
2. **`signal_capacitance` 随选择变化。** 参与转换的那 8 片贡献采样电容，
   于是增益也随选择轻微变化 —— 但这是**匹配伪影**，实测 slice 0–7 与
   10–17 之间约 **7.2 ppm**，`tests/audit/test_audit_findings.py` 断言它
   落在 `(0, 1000) ppm` 区间内，不会被误当成增益变化。
3. **备用片的语义被显式化。** 2 片备用必须给出"预采集或合法更新"机制，
   否则它就是一个没有历史的电容 —— 检查会把这种情况报成 violation。
4. **`pipeline` 与 `sim_split` 的一致性被保持。** 两条链都通过
   `build_first_stage_quantizer`（ADR 0004）与同一个池取得物理量，
   退化等价仍是逐位一致。
5. **A02 的第三个子指控（DAC 求值未接收选择）被单独关闭**：
   `SlicePool.acquire_quantizer` / `SplitDAC.evaluate_physical(k, sid)`
   现在接收 `sid`（DEM 状态），`sid` 决定哪些物理单位被接通。

## 4. 未闭合的部分（如实记录）

- **`shuffle_causal` 不是论文的排布。** 论文的具体 slice 轮转次序未公开；
  本模型只保证"存在一个因果的排布"，不声称是原芯片的那一个。
- **18 套数组的完整寄生网表未建。** 每片一个节点的耦合只在集总层面近似
  （`dac_parasitic_ratio` / `dac_parasitic_spread`）。
- **A02 的"同一次输出混用当前采集组建立误差"问题**由 ADR 0005 的因果绑定
  部分缓解，但建立误差的**时变参考**部分依赖 ADR 之外的工作（审计 A06），
  尚未闭合 —— 见 `docs/model_scope.md` §4。

## 5. 复核方式

```bash
pytest tests/unit/test_slice_pool.py -q
pytest tests/audit/test_audit_findings.py::TestF2SliceCausality -q

PYTHONPATH=src python -c "
import numpy as np
from adi_model import Config
from adi_model.slice_pool import PhysicalSlicePool, check_causality
cfg = Config.paper_consistent()
for s in ('shuffle_causal', 'pingpong'):
    p = PhysicalSlicePool(cfg, np.random.default_rng(20260910))
    plan = p.plan(8192, np.random.default_rng(7), strategy=s)
    v = check_causality(plan)
    print(s, 'violations=%d mean_coverage=%.4f causal=%s'
          % (v['violations'], v['mean_coverage'], v['causal']))
"
```
