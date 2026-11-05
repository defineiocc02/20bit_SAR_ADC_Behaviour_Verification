# 旧注释归档：非现行状态入口

基线为1e973cb。以下保留迁出生产代码的历史叙述，不作为本轮重新验证或现行参数定义；本文中的旧行号和库外backup路径只说明归档上下文，完整原文件可用 `git show 1e973cb:src/adi_model/<文件名>` 取得。现行解释以生产代码、ADR和 [STATUS](../STATUS.md) 为准。

# 六个信号链 Python 模块的注释维护记录

本次只改注释/docstring，基线为 `1e973cb82bb6ba39dba3b7d82e43f10ec42434c3`。原源码完整备份在 `backup/src/adi_model/`。以下归档原注释中的历史叙述，保留历史理由，不把旧状态或旧数字当作本次重跑结果。

## 当前注释订正与接口边界

- `slice_pool.py`：明确物理 ID、组内 slot 和 sample ID；固定制造电容重用与 runner 电气状态清零分开。残差增益为 `G=C_signal/Cf`，外部输入信号系数为 `G*alpha`；负载、噪声等效电容不能混用。每片先解 beta 再求和。采样 dither 固定保留所选阵列末尾 2D 单元，因此不随 sid 改 alpha。
- `metrics.py`：区分 SNDR/SNR/全谱 SFDR/谐波 SFDR/THD，bin 与 Hz，幅度与 PSD，均值/标准差/RMS，浮点输出与整数码解码。将旧 16 bin 频率例子的错误注释改为 `16*fs/N`；40 MHz/16384 时为 39.0625 kHz，阈值常数没有变。
- `ra.py`：noise_out 是 RA 输出 RMS；折到存储残差除 G，折到采样掩码前输入除 G*alpha。旧 AZ 噪声倍率与 held-noise CAAZ 分开，动态带宽倍率不等于周期平稳噪声传递。
- `chopping_az.py`：整个 raw 链逐样本运行、钳位、量化、重构后才配对；全部 raw 码与异常保留。说明 bin 中心整数公式、模 2**32 连续 ID、raw/pair 速率和两点响应，不把 4.3 dB 测量拟合成模型目标。
- `pretracking.py`：仅从到时可见的实际 coarse code 名义估计；同刻 ready 可用，未来判决不可见。明确 pending/recent/own、源 ID、shape、单位及异常调用并非事务回滚。这里没有逐 slice 低频学习器；未来学习不能读取 plant 的 v_os/t_skew/tau_rel 真值。
- `interleave_tracking.py`：保留受 [12] 启发的独立电荷基准及原 doctest；其中 xhat=理想输入+假设噪声不进入主路径，也不能作为运行时校准观测。相邻交换不是生产 18/8 分配器，电荷比不能直接转成带宽/噪声比。

## 有意保留的实际行为，未作功能改动

- `ResidueAmplifier.evaluate(g=None)` 实际回落 cfg.g_actual；生产 charge 路径显式传物理 gain，但 helper 本身不强制拒绝缺参。原 docstring 写成 charge 必传已订正。
- `PhysicalSlicePool.acquire` 仅更新 v_top/held_valid；不登记 held_sample，也不增加使用计数。其 skew 是组平均一阶近似，返回误差不显式加回 skew*dxdt。
- `check_causality` 仅按相邻计划组的交集核验，不替代真实电荷、首周期预热和唯一 ID 检查。
- `bw_ratio_absolute` 的跟踪侧低于阈值可得到 0；复位侧无正下界或电荷无效时返回 NaN。原注释“任一侧无约束均 NaN”与代码不符，本次只改注释。

## 从生产代码迁出的原历史说明

### slice_pool.py 原引言

```text
Physical 18-slice pool with cross-cycle causality and slice-bound mismatch.

Why this module exists
----------------------
An external audit of v6.1 demonstrated — numerically — that the interleaving in
the previous model was not physical. Two findings, reproduced independently in
``tests/audit/test_audit_findings.py::TestF2SliceCausality``:

1. ``ShuffledScheduler.reserve_dual`` drew an *independent* 8-of-18 permutation
   every conversion cycle. Over 8191 cycle-to-cycle transitions, exactly **one**
   had the conversion group equal to the previous acquisition group; on average
   only **3.56 of 8** converting slices had actually acquired the sample being
   converted. A real capacitor cannot convert a charge it never sampled.
2. The 8-of-18 choice did not reach the capacitor weights. Running the
   phase-accurate path with a fixed schedule and with a shuffled schedule gave
   **bit-identical** output (max difference 0.0 uV), so "shuffling spreads the
   interleaving tones" was being demonstrated on error *coefficients* rather
   than on the physical capacitor array.

This module replaces both behaviours with an explicit physical pool.


```

### _fft_sfdr 原审计叙述

```text
全谱 SFDR：**先扣除基波，再对残余做加窗全谱搜峰**。

==========================================================================
v5 审计修正 —— 这里原来有一个会把 SFDR 钳死在 25 dB 的静默 bug
==========================================================================
旧实现用「保护区内逐 bin 的 [基波, 怀疑杂散] 联合 LSQ」来分离主瓣内的
近端杂散。它只在**相干采样**（fin 恰为整数 bin）下成立 —— 此时设计矩阵
离散正交。非相干采样下两列频率间隔仅 3~8 bin，记录长度内相位差很小，
设计矩阵接近奇异，拟合出的"杂散幅度"实际是在拟合窗函数的主瓣滚降。

实证（v5 自查）：**零噪声、零杂散的纯正弦被报出 SFDR = 25.05 dB**，
且拟合幅度随离基波距离单调衰减（-8bin:34.4 -> +3bin:25.1 -> +8bin:33.8 dBc），
形状与 BH4 主瓣完全吻合 —— 分离根本没有发生。
旧自检（stage10.4 / stage11.5）全部用相干 fin，所以从未触发；
正式流程也全程用 _coherent_fin()，故已发布报告未受污染。
但这是**静默错误**：任何非相干输入（真实 ADC 测试的常见做法）都会跌到
25 dB 且不报错。

修正原理：窗泄漏来自**信号本身**（有限长截断 + 加窗），不是杂散。
用精确频率的 LSQ 把基波从时域扣掉后，残余里就不再有主瓣泄漏，
近端 bin 与远端 bin 因此变得一视同仁，直接全谱搜峰即可。
LSQ 扣除精度与频率是否整数 bin 无关，故本方法对非相干输入同样成立。

fin 已知时应直接传入（sine_fit_metrics 就是这么做的）；
fin=None 时内部用 DTFT 峰值细化估计到 ~1e-3 bin。

Args:
    x: 实数信号数组（已含噪声/杂散） [V]。
    fs: 采样率 [Hz]。
    guard_bins: 基波附近置零的半宽 [bin]；None 时 fin 已知取 0、
        未知取 3（吸收频率估计残差，并标注近端适用限制）。
    fin: 已知基波频率 [Hz]；None 则内部用 DTFT 峰值细化估计。

Returns:
    (SFDR_dB, spur_Hz)：扣除基波后全谱搜峰得到的杂散抑制比 [dB] 与
    杂散频率 [Hz]。无杂散（残余被基波主导）时返回 (inf, 0.0)。
Side effects: 无（纯函数，不改 x 也不存内部状态）。
```

### _band_max 原低频切片问题记录

```text
Peak magnitude in a +/- guard-bin window, with the window clamped.

Clamping matters: at low input frequencies ``fund_bin`` can be 1, and the
unclamped slice ``X[1-4 : 1+4+1]`` wraps to an empty array, which raised
``ValueError: zero-size array to reduction operation maximum`` for
``f_in <= 5 kHz``. Reported by independent review (finding B11).

Args:
    X: Magnitude spectrum, ``(n // 2 + 1,)``.
    bin_idx: Centre bin.
    guard: Half-width of the window, in bins.

Returns:
    Maximum magnitude in the clamped window, or 0.0 if the spectrum is
    empty.
```

### _fft_harmonics 原混叠历史说明

```text
FFT 口径的谐波提取（相干采样），**折叠频率 + 去重**。

v3 审计修正：高频输入时部分谐波混叠后与基波或其他谐波重合
（fs=40 MHz、fin=5 MHz 时 7/9 次谐波折回 5 MHz 与基波重合）。
时域最小二乘在这类频点上设计矩阵共线，会把基波能量重复计入，
实测纯 5 MHz 正弦被报出 THD=-6.5 dB 的假谐波。
规则：混叠后落在基波 guard 带内的谐波丢弃；与已计谐波重合的丢弃。

Args:
    x: 实数信号数组（相干采样前提） [V]。
    fs: 采样率 [Hz]。
    fin: 基波频率 [Hz]，必须相干（整数 bin）否则混叠去重失效。
    nharm: 考察的最高谐波次数 [无量纲]，默认 12。
    guard_bins: 谐波/基波保护的半宽 [bin]，默认 4。

Returns:
    字典，字段与单位：
        A1 [V]           基波 0-peak 幅度；
        harmonics        谐波列表 [(次数, 幅度 V)]；
        dropped          被丢弃谐波列表 [(次数, 原因)]；
        THD_dB [dB]      谐波口径总失真（不含噪声）；
        harm_SFDR_dB [dB] 谐波口径杂散抑制比。
Side effects: 无（纯函数）。
```

### ra.py 原模块中的版本/预算记录

```text
ra.py -- 残差放大器（RA）：电荷一致增益、输出噪声、饱和。

============================================================================
物理模型与文献出处
============================================================================
论文 [00]：RA 是共享的（PPT p.11 "Shared residue amplifier, 40% of ADC
power consumption"），GMR + OTA 架构、带 auto-zero（PPT p.34-35：消
offset/漂移、噪声代价 -1.6 dB、ADC2 动态采样带宽 +1.3 dB —— 这两个 dB
电路未复现；下述噪声倍率仅作现象预算，见适用域）。行为模型取电荷守恒口径：

    G[n]   = C_active[n] / C_F_true      （v3 审计修正：逐样本，由同一组
                                            物理电容决定，失配由此走增益通路）
    v_R[n] = G[n]·r[n] + n_RA,out        （输出端噪声是电路属性，不随 G[n] 变）

* n_RA,out 的大小在 cap_scale=1 时由 target_dr_db 反推标定一次并**锁定**
  （config.noise_budget 的两条原则），不随电容缩放漂移——缩电容扫描的
  关键口径，防止"总预算 - kT/C 逐点反推"把 RA 噪声算成 0。
* **先饱和再送 ADC2**：ra_v_clip 检查在 ADC2 之前。不能先把大信号在
  数学上被 ADC2 余量吸收、掩盖真实放大器已饱和的事实。KTC 支路里的
  两条放大通路各自独立检查饱和（ktc.py）。
* ra_gain_model="fixed"：理想固定增益 G = g0·(1+gain_error)，仅作对照，
  用于隔离"电荷一致"与"固定增益"两种口径的差异（v3 审计实验）。

单位契约：residue / 输出均为 [V]；noise_out 为输出端 RMS [V]
（config.ra_out_noise_rms 语义 = 输出端，折输入时除以 G）。

参数来源分级：
    g0 = 32               [披露]（PPT 图标注）
    ra_v_clip             [假设]（由profile指定；不是器件摆幅签核）
    noise_out             [拟合]（由 target_dr_db=94.6 反推，逐字审计：
                          论文正文写 94.2，取 PPT 94.6 因与 NSD 8.8nV 自洽）

适用域声明（勿夸大）：
    * auto-zero：v6.1 起结构化建模 —— ra_autozero=True 时白噪声按折叠因子
      10^(1.6/20) 放大（PPT 披露代价）、1/f 整体移除（auto-zero 的收益面）；
      RA自身静态offset未作为独立物理源注入；不能据此声称它已被器件消除。
      全局有效offset可由权重回归估计，仅估gain的旧校准不等于offset校准；
    * 本类提供静态增益/孔径噪声；生产 split 通路的有限带宽、压摆与摆幅
      由 conversion.ConversionEngine 联合求解。信号响应不等于开关噪声传递；
    * 不建模 RA 复用/功耗循环（PPT ~50% idle time）——对输出行为无影响，
      只影响功耗，而功耗不在行为模型预测范围内。
```

### RA 构造器原预算审计注释

```python
        # 锚点基线口径（回答第五份复核 §7 的"锚点在前还是在后"）：
        # resolve_ra_noise 反推的锚点 = **AZ 关、动态带宽关**的对照基线；
        # 下面两个系数按固定次序叠加在其上（先 ×AZ 代价、再 ×动态带宽），
        # 不做二次反推 —— 即本模型声称的 target DR 是"无 AZ/无动态带宽"意义
        # 下的目标，两个机制的净收益由 stage22 的预算推导口径另行入账。
        # RA auto-zero（PPT p.34-35，−1.6 dB）：消 offset/低频噪声（含 1/f），
        # 代价 = 存储电容 kT/C + 噪声折叠 -> 白噪声 ×10^(cost/20)。
        # 口径注记：PPT 的 −1.6 dB 参照系（RA 自身还是整机）未披露；
        # 折到本模型整机预算（RA 占噪声功率 ~74%）后整机 ΔSNDR ≈ −1.2 dB，
        # stage22 做"预算推导 vs 实测"一致性检验，不硬凑披露值。
```

### 低频谐波可靠性原审计注释

```python
    # ---- 可靠性标记 ----------------------------------------------------
    # 记录长度给不出足够低的频率分辨率时，二次谐波落在基波窗主瓣内，
    # 谐波口径的 THD/harm_SFDR 会把基波泄漏当成谐波（fin=5 kHz、N=16384
    # 时实测报出 THD=-13 dB 的假谐波）。这里不静默返回一个好看的数，
    # 而是显式置 nan 并标记不可靠 —— 与审计"有数字不等于有证据"一致。
```
