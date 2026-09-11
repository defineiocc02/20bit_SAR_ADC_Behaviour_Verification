# ADR 0008 — 斜率用精确/谱导数，不用 `np.gradient`

- 状态：**已采纳**
- 关联审计条目：**A07.3**（skew 的 `np.gradient` 导数偏差）
- 影响模块：`sampler.py`、`pipeline.py`
- 回归测试：`tests/regression/test_skew_derivative.py`（19 条）

---

## 1. 问题

交织采样时钟的失配 `δt` 产生的误差是

```
ε_skew = δ̄(acq) · dx/dt
```

所以"斜率估得准不准"**直接就是结果对不对**。v6.1 用
`np.gradient(x, t)`（中心差分）求 `dx/dt`。

中心差分的幅度响应是

```
|H(f)| = sin(2πf/fs) / (2πf/fs)
```

| f / fs | 0.125 | 0.25 | 0.375 | 0.45 | 0.475 | 0.5 |
|:---|--:|--:|--:|--:|--:|--:|
| \|H\| | 0.9003 | 0.6366 | 0.3001 | 0.1093 | 0.0524 | 0 |

也就是说 **19 MHz / 40 MHz 处斜率被低估 25.6 dB**，
20 MHz 处直接是 0。任何正比于 `dx/dt` 的结论在接近 Nyquist 时都会被
静默压掉 —— 外部审计 A07.3 点的就是这一点，并明确要求"不能把低频通过
外推到接近 Nyquist"。

## 2. 决定

新增一个单一入口 `sampler.input_derivative(input_fn, t)`，优先级：

1. **解析导数**：输入对象自带 `.derivative` 属性时直接用。
   `sine_input` / `dc_input` 都会附上（正弦解析导数在任意 `fin` 精确）。
2. **谱导数**：否则用 :func:`sampler.spectral_derivative` ——
   `ifft(1j·ω·fft(x))`，在 `[0, fs/2]` 内响应为 **1**，对带限周期记录精确。

`np.gradient` **不再出现在信号路径里**。

## 3. 后果（端到端实测，同一记录跑两次）

用 `monkeypatch` 把 `np.gradient` 打回 `pipeline.input_derivative`，
测"准确斜率 / np.gradient"的误差比：

| fin | \|H(fin)\| | 理论 1/\|H\| | **实测比值** | 判据 |
|--:|--:|--:|--:|:---|
| 5 MHz | 0.9003 | 1.11 | **1.1** | ≥1.05 ✅ |
| 10 MHz | 0.6366 | 1.57 | **1.6** | ≥1.40 ✅ |
| 15 MHz | 0.3001 | 3.33 | **3.3** | ≥2.80 ✅ |
| 18 MHz | 0.1093 | 9.15 | **9.2** | ≥7.00 ✅ |
| 19 MHz | 0.0524 | 19.1 | **18.3** | ≥12.0 ✅ |

实测比值与 `1/|H|` 逐点吻合（5 MHz: 1.1 vs 1.11；19 MHz: 18.3 vs 19.1）
—— 这条链被完整闭合了，不是"看起来更好了"。

另外两条守护：

- **静态守卫**：扫描 `pipeline.py` 源码，禁止 `np.gradient` 出现在信号路径。
- **守卫的守卫**：`test_the_guard_would_catch_the_historical_line` 保证
  正则不会退化成空操作（这正是 F10 那条静态检查最初失手的原因）。

## 4. 未闭合的部分

- **谱导数只对均匀栅格成立。** 目前 `t_grid = arange(n)/fs` 是均匀的；
  若将来引入非均匀时间栅格，必须回到解析路径或改用非均匀加权差分。
- **非相干记录下谱导数有边缘/泄漏误差**（实测增益误差 < 5%），
  因此在没有解析导数的场景（自定义 callable）里它是"显著优于中心差分"，
  而不是"精确"。docstring 里写明了这一点。
- **skew 误差本身仍是标度律验证。** `slice_timing_skew_s` 在
  `PARAM_GRADES` 里是 `[假设]`（论文只报告 0.6 ps 的**设计结果**，
  不是可用的分布参数）。本 ADR 修的是**测量工具**，不是机制本身。

## 5. 复核方式

```bash
pytest tests/regression/test_skew_derivative.py -q

PYTHONPATH=src python -c "
import numpy as np
from adi_model.sampler import sine_input, input_derivative, spectral_derivative
fs, n = 40e6, 4096
t = np.arange(n) / fs
for fin in (5e6, 10e6, 19e6):
    d = input_derivative(sine_input(2.7, fin), t)
    print(f'fin={fin/1e6:5.1f} MHz  rms(dx/dt) = {np.std(d):.4e} V/s  '
          f'(analytic ratio {np.std(d)/(2*np.pi*fin*2.7/np.sqrt(2)):.4f})')
"
```
