# [14] US10826519B1 FIG.5 参考缓冲器：结构与判据（2026-11-13）

> 依据：Sheet 5 of 16，图面清晰可读（已入库核对）。
> 交付：`src/adi_model/control/reference_buffer.py` + 7 条单测。
> **不接主链** —— 原件没有给出尾电流、带宽、负载电容的具体数值。

---

## 1. 图面结构（逐元件）

```
        VDD1 ──┬─────────┬──────────────┐
               │         │              │
             [MP1]     [MP2]          [MP3]── Vref_internal
               │         │              │      ↑
               ├───┬─────┤            [INV]    │
               │   │     │              ↑      │
             [MN1]│   [MN2]         (调节框)   │
          Vref ┘   │      └ Vref_internal ─────┘
                   │
                 [MN3] ── GND
                   ↑
                 Vbias
                                  调节框输入: Threshold_adjust, Trial<N:1>
```

| 元件 | 作用 |
|:---|:---|
| MN1 | 输入差分对的一侧，栅接 **Vref**（外部参考） |
| MN2 | 差分对另一侧，栅接 **Vref_internal**（缓冲输出）⇒ **反馈** |
| MP1 / MP2 | VDD1 上的电流镜，强制两支路电流相等 |
| MP3 | 输出管，栅由 INV 驱动，源接 Vref_internal |
| MN3 | 尾电流源（Vbias 偏置），接 GND |
| 调节框 | 输入 `Threshold_adjust` 与 `Trial<N:1>`（图上未展开） |

## 2. 三条语义（→ 三条判据）

### ① 闭环单位增益

Vref_internal 反馈到 MN2，与 MN1 的 Vref 比较，构成**负反馈跟随**。
因此输出 = Vref（加闭环失调），而不是"Vref 减某个压降"。

```python
closed_loop_output(vref_v=1.0)                 # → 1.0
tracking_error_v(vref_v=1.2, offset_v=1e-3)    # → 1e-3
```

**这条推翻了"参考缓冲器 = 源跟随器（会丢 Vgs）"的直觉读法。** 图上是带 INV 的
推挽式输出级 + 差分反馈，不是简单的源跟随。

### ② 尾电流决定可搬运电荷

MP1/MP2 是电流镜、MN3 是尾电流源 ⇒ 输出总电流受 **I_tail** 限制。
它在时间窗口 τ 内能给负载的电荷上界就是 `I_tail × τ`。

```python
reference_charge_budget(tail_current_a=1e-3, window_s=12.5e-9,
                        load_cap_f=2e-12, swing_v=0.5)
# available 1.25e-11 C, required 1e-12 C, sufficient=True
```

这解释了专利标题里的 **"Low Power"**：参考缓冲器的功耗被尾电流钉住，
而它必须在这一预算内供上切片 DAC 的参考电荷。

### ③ 参考不是绝对静止

调节框有两个输入：`Threshold_adjust`（阈值可调）与 `Trial<N:1>`（N 次试验）。
因此"参考整笔恒定"这个假设与原件结构冲突：

```python
reference_is_static(threshold_adjust_enabled=False, trial_count=1)   # True
reference_is_static(threshold_adjust_enabled=True,  trial_count=1)   # False
reference_is_static(threshold_adjust_enabled=False, trial_count=9)   # False
```

## 3. 与既有 `behavioral/reference.py` 的分工

| 模块 | 回答什么 |
|:---|:---|
| `behavioral/reference.py`（已有） | **多快建立**：`settle_error`、`dynamic_reference`、`required_tau` |
| `control/reference_buffer.py`（本轮新增） | **结构与容量是否自洽**：单位增益、电荷预算、参考是否可当静止 |

两者互补，没有重复实现。

## 4. 明确不做的

* **不给数值**：尾电流、环路带宽、负载电容在原件里都没有；
* **不接主链**：接入需要先确定"本芯片用不用这个缓冲器"；
* **不声称已实现参考缓冲**：只交付结构判据。
