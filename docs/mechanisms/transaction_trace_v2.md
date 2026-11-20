# V2 事务 trace：三段主键与连续性判据（2026-11-13）

> 交付：`src/adi_model/simulation/transaction_trace.py` + 7 条单测。
> 依据：`docs/HANDOVER.md` §4-P2 的"事务 trace（epoch + sample_id + event_index）"。

---

## 1. 为什么需要三段主键

顶层对拍（V3/V4）要回答的是"**哪一笔的第几个事件**错了"。
如果只有总量（比如输出码流），对拍只能得出"总数不对"或"某处不一致"，
无法定位。三段主键把每一行钉到一个唯一的 (配置纪元, 采样序号, 笔内事件号) 上：

| 字段 | 含义 |
|:---|:---|
| `epoch` | 配置纪元。配置/权重/binding 在捕获期间不变的声明；变了必须开新 epoch |
| `sample_id` | 采样序号，同一 epoch 内唯一且单调 |
| `event_index` | 该笔内的第几个位试验，从 0 开始 |

## 2. 现在能交付到什么程度

| 层次 | 状态 |
|:---|:---|
| 三段主键定义 + 主键唯一性校验 | ✅ `TransactionTrace.__post_init__` |
| 从仿真结果抽 trace | ✅ `build_trace_from_result(result, epoch=...)` |
| 每笔 `event_index` 从 0 连续 | ✅ `event_index_restarts_each_sample` |
| 样本守恒 | ✅ `transaction_continuity`（`N_accepted = N_retired + N_cancelled + N_pending`）|
| **逐事件**（E>1）的电气观测 | ⬜ 需 `conversion_trace`（joint 动态路径），默认路径下为 `None` |

当前 `build_trace_from_result` 每笔写一行、`event_index` 全 0，
记录的是**终态码**。这是诚实的边界：默认静态路径没有逐事件的电气观测，
不伪造。

## 3. 样本守恒为什么是第一条判据

它是顶层对拍最基本的一条：进来的样本要么走了、要么被取消、要么还挂着，
**不能凭空多出来或消失**。任何丢样/重样都会破坏它。

```python
transaction_continuity(n_accepted=8, n_retired=6, n_cancelled=1, n_pending=1)  # True
transaction_continuity(n_accepted=8, n_retired=6, n_cancelled=0, n_pending=1)  # False
transaction_continuity(n_accepted=8, n_retired=9, n_cancelled=0, n_pending=0)  # False（多出来）
```

## 4. 明确不做的

* **不伪造逐事件观测**：`conversion_trace` 在默认路径是 `None`，此时只记终态码；
* **不推断 epoch**：`epoch` 由调用方声明（它是"配置没变"的**声明**，不是可推算的量）；
* **不接主链**：本模块只重组已有观测，不改仿真行为。
