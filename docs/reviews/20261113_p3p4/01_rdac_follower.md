# P3-1 RDAC follower：因果语义与参考开关代价

> 论文原话（ISSCC 2024 §9.8 p.2）："The RDAC works as a follower of the quantizer with the
> conversion results loaded as they develop."
> 本文把这个句子变成**可被反例推翻**的检查，而不是一条注释。

代码：`src/adi_model/control/follower.py`；测试：`tests/unit/test_follower.py`（12 项）；
证据：`data/p3_mechanisms.json` → `follower`。

---

## 1. 三种"同一个最终码"的加载方式

| 路径 | 事件数 | 是否可实现 | 最终电荷 | abs(dQ) 总量 |
|:---|:---:|:---:|:---:|:---:|
| follower（已决前缀逐事件） | 7 | ✅ | Q(c) | 1.00× |
| direct final（一次加载最终码） | 1 | ❌ 需要提前知道最终码 | Q(c) | **1.00×** |
| trial SAR（每比特先试探再撤销） | 18 | ✅ | Q(c) | **2.99×** |

模型实测（默认 63+8 池、18 片、8 活动、4096 笔 1 MHz 满幅正弦）：

| 量 | 值 |
|:---|:---|
| `final_charge_mismatch_c` | **0.0 C**（三条路径逐位相同） |
| `follower_over_direct_charge_ratio` | **1.000** |
| `trial_over_follower_charge_ratio` | **2.989** |
| follower 平均翻转数 / 笔 | 280.6 |
| trial 平均翻转数 / 笔 | 839.4 |
| follower 参考电流（abs(dQ)×fs） | **2.46 mA** |
| trial 参考电流 | 7.36 mA |
| 论文参考电流 | 1.8 mA（follower 为其 1.37×） |

**结论（可反驳）**：把事件拆细**不会**多花参考电荷；被消除的是**试探-撤销的往返电荷**。
所以"follower 比直接加载更费电"是错的，"follower 省的是 3× 的试探电荷"才是对的。
follower 的 2.46 mA 与论文 1.8 mA 同量级（1.37×），差额来自本模型没有实现的
粗预充（coarse pre-charge）只充单侧轨这一细节——论文的动态参考缓冲在转换期用
粗缓冲供电、RA 前才切到 REF_IN，电荷记账口径不同，不能直接当成误差。

---

## 2. 因果门禁：前缀截断不变性

`audit_causality` 对每个事件 e 构造两份只在 e 之后不同的前缀（未决位分别补全 0 与 511），
要求事件 e 的施加码不变。

| 被测实现 | 检查组合数 | 违约数 | 判定 |
|:---|:---:|:---:|:---:|
| 生产实现（恒等） | 28 672 | 0 | ✅ CAUSAL |
| 故意偷看最终码的实现 | 28 672 | **24 576** | ❌ 首个违约 `[sample,event]=[0,0]` |

这一步是本轮唯一能证明"没有提前读最终码"的证据。静态实验里"只给最终码"的写法
（旧 `task1/task2` 的 final-code static）在这个门禁下会直接判 `NOT_CAUSAL`。

---

## 3. 电荷零点的选择（容易被忽略的口径）

参考电荷必须相对**全底板接低轨**（码域下端点）计算，而不是相对"零电荷"：

* 采样态所有底板接 Signal，其到低轨的那部分电荷由**输入网络**承担，不属于参考轨在转换期付出的电荷；
* 若把零点设成 0，follower 会多算一次 `Q(code 0) ≠ 0` 的固定项，比值出现 1.125 的假差
  （本仓库测试里真实出现过，已修正）。

对应代码：`run_follower` 先算 `baseline_command = select(code_min)`，用它做 `previous`。

---

## 4. 论文机制对照

| 论文说法 | 本模型状态 | 证据 |
|:---|:---|:---|
| "results loaded as they develop" | ✅ 逐事件 7 次加载，因果门禁通过 | §2 |
| "the DAC is always set directly to the right state" | ✅ trial 路径作为反例存在，比值 2.99 | §1 |
| "reference current is minimal (1.8 mA)" | ⚠️ 2.46 mA，同量级；未建模粗预充单侧供电 | §1 |
| "reference switches can be made smaller / slower edges" | ❌ 未建模（开关尺寸/边沿速率不在 L1 行为级） | — |

---

## 5. 不能声称

* 不能声称**建立时间/边沿速率/开关非线性电容**已被建模——本模块只做电荷记账。
* 不能声称 2.46 mA 与 1.8 mA 的差是"模型误差"——两者电荷口径不同（是否含粗预充、单/双轨）。
* 不能声称 follower 改善静态精度——三条路径最终电荷逐位相同。
* 不能把本模块的 abs(dQ) 当成参考缓冲的功耗：它只是电荷×采样率的上界估计。
