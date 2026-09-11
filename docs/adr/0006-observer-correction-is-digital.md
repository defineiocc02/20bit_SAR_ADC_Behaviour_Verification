# ADR 0006 — 观测器校正必须在数字域扣除

- 状态：**已采纳**
- 关联：审计 A05（KTC 支路是研究扩展）与 A06（共享 RA / 动态参考未闭合）
- 影响模块：`adc2.py`、`sim.py`、`sim_split.py`、`pipeline.py`
- 回归测试：`tests/regression/test_observer_headroom.py`

---

## 1. 问题

观测器（KTC / 噪声抵消支路）的作用量是「转换相存下的相关噪声」`v_N`，
数字端已知乘上 `κ` 后可从残差里减掉。v6.1 把它写在**模拟侧**：

```python
fine, adc2_over = adc2.quantize(vra - state.kappa * vnc)   # v6.1
```

这在数值上"能跑"，但把**校正量**算进了 ADC2 的**量程**。`κ·v_N` 不是小量：
它正比于输入斜率，近 Nyquist 时可以达到整个量程的规模。

用默认 `paper_consistent` 读数 + `ktc_enable` 的实测（`fs = 40 MHz`，
`N = 2^13`，满幅正弦）：

| 输入频率 | `vra` 范围 | `κ·v_N` rms | 旧写法 ADC2 溢出率 | 新写法溢出率 |
|:---|:---|--:|--:|--:|
| `fs/64` | [-0.003, 1.503] V | 31.2 mV | 0.00% | 0.00% |
| `fs/8` | [-0.004, 1.506] V | 249.9 mV | **12.24%** | 0.00% |
| `fs/2`（Nyquist） | [-0.004, 1.506] V | **1413.7 mV** | **49.63%** | 0.00% |

ADC2 窗口是 [-0.15, +1.65] V，而 `vra` 已占满 [0, 1.5] —— 顶部只剩约 144 mV
余量。也就是说 `κ·v_N` 从 46 mV 起就在吃余量，到高频时把它整个吃掉。

旧的 stage20 / stage21 / stage11 判据之所以"通过"，是因为它们只看
**低频**记录（校正量还没长大）。这是一类典型缺陷：**判据的频点避开了
机制的暴露区间**。

## 2. 决定

量化先做，校正放数字域：

```python
v2 = quantize(vra) − κ·v_N
```

实现为 `ADC2.quantize_with_correction(vra, κ·v_N)`，并且**上游不得再自行做
模拟相减** —— 函数的 docstring 明确写了这条契约。

两种写法的差别：

| 方面 | 旧：`quantize(vra − κ·v_N)` | 新：`quantize(vra) − κ·v_N` |
|:---|:---|:---|
| ADC2 量程占用 | `vra` 与 `κ·v_N` 之和 | **只有 `vra`** |
| 溢出语义 | `over` 混合了校正量越界 | `over` = ADC2 真实看到的越界 |
| 量化噪声 | 少一次 Δ2 重化 | 多一次 Δ2 重化（相对 LSB20 = 0.6，可忽略） |
| 校正量大小 | 受量程约束 | **任意大都不影响 `over`** |

物理上这也是对的：观测器校正量在**数字端**由同一颗系数 `κ` 算出，它不需要
在模拟域先减一次 —— 模拟域相减只会多引入一次有损的、受量程限制的操作。

## 3. 后果

1. **`over` 的语义被澄清。** 它现在只反映"ADC2 实际看到的电压是否越界"，
   因此可以当作量程预算的判据使用；以前它同时承载了两个含义。
2. **校正正确性边界不变。** 数字域扣除不改变 `κ` 是否被正确标定 —— 那是
   `β` 校准的事（见下）。它只解除量程约束。
3. **`β` 校准的收敛目标被显式化。** `run_with_calibration` 的增益/β 迭代
   现在收敛到 `(g0 / ktc_gain_n) / (1 + ktc_beta_error)`；
   回归测试断言相对误差 < 1%。
4. **三个主循环必须一致。** `sim.py`、`sim_split.py`、`pipeline.py` 三处
   都改成调用同一个方法；退化等价测试（两链逐位一致）覆盖这一点。
5. **A05 的地位没有改变。** 这仍是我们自己的观测器，标为
   `RESEARCH_EXTENSION`，默认关闭。本 ADR 只保证"开启时它不因为量程而
   假性失败"，不代表它已有电路证据。

## 4. 复核方式

```bash
pytest tests/regression/test_observer_headroom.py -q
```

该文件包含：

- **数字域扣除不改变 `over`**：把 `correction` 放大到任意倍，`over` 不变；
- **反事实守卫**：旧的 `quantize(v − big)` 写法在同样输入下必然全溢出 ——
  这条断言让"回到旧写法"变成红构建；
- **β 校准收敛**：迭代后的 `κ` 与解析目标差 < 1%。

```python
from adi_model import Config
from adi_model.adc2 import ADC2
import numpy as np
a = ADC2(Config()); cfg = Config()
v = np.full(4096, cfg.adc2_v_max * 0.99)
corr = np.full(4096, 2.0)                       # 远超余量的校正量
_, over_new = a.quantize_with_correction(v, corr)   # -> all False
_, over_old = a.quantize(v - corr)                  # -> all True
```
