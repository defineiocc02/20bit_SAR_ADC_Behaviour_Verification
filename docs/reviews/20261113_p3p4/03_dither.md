# P3-3 dither：电荷口、码偏移残余、范围预算

> 论文（§9.8 p.3）："Dither is used to complement the DEM linearization of the DAC.
> The architecture allows the dither range of the quantizer to be different from that of
> the RDAC. Thus, the dither range is enhanced by 2b when the result is transferred from
> the quantizer to the RDAC."
> 专利 US10505561B2 Fig.19 给出六位字拆成 lower/upper 的具体做法。

代码：`src/adi_model/control/dither_port.py`；测试：`tests/unit/test_dither_port.py`（10 项）；
证据：`data/p3_mechanisms.json` → `dither`。

---

## 1. 三个已知问题，逐条结案

已知问题见 `docs/reviews/20261112_w0/08_known_issues.md` §2/§3。

### 1.1 "早期 RDAC 候选码非法"

| 实现方式 | 码域 | 非法 (样本,事件) 数 |
|:---|:---|:---:|
| `k = c - U`（用码字空间换 dither 幅度） | 0..511 | **6 368**（64 字 × 512 码 × 7 事件的全枚举中） |
| 独立电荷口（RDAC 码保持 c） | 0..511 | **0** |

结论：非法码不是 bug，是**用码域表示 dither 的必然结果**。真实电路的 dither 有自己的电容，
不占 RDAC 码域；电荷口形式在原理上就没有这个约束。旧路径保留，但必须在文档里写清它只适用于
"码域足够宽"的假设，而本 ADC 的 9 位码域正好用满。

### 1.2 分段码本的"上段残余"

只有 **upper 部分 U** 能被码偏移表示；lower 部分 L 走 SADC 采样路径，码偏移根本无法表达它。
把两者混在一起算，会把"下段没实现"误读成"上段有残余"。所以 `code_shift_residual`
只比较 U：

| 池 | 最大残余 | 折合 | 判定 |
|:---|:---:|:---:|:---:|
| 默认 63+8（严格二进制，β=1/8） | 0.0000 mV | 0.000 LSB20 | ✅ 码偏移精确 |
| Fig.11 17/5（15×2C+2×1C，β=1/17） | **6.696 mV** | **1 170 LSB20** | ❌ 码偏移不成立（最坏码 129） |

Fig.11 阵列的电荷式是 `Q ∝ code/16 − (code&15)/272`：线性项之外还有 `(code&15)` 的修正，
所以任何非 16 倍数的码偏移都会留下与码相关的残差。任务树里量到的 4.596 mV 是另一组
B5:B6 相位下的同类现象；本仓库给出的是自己几何下的 6.696 mV，不搬运那个数。

### 1.3 64 个字下的输入下界

| 量 | 值 |
|:---|:---:|
| lower 最大幅度 `max(L)` | 3 Δ = **35.16 mV** |
| total 最大幅度 `max(T)` | 8 Δ = 93.75 mV |
| upper 最大码 `max(U)` | 5 |
| SADC 可用输入下界 | **−2.9648 V**（比 −3 V 少 35.16 mV = **6 144 LSB20**） |
| SADC 可用输入上界 | +3.0 V（L ≥ 0，上界不受影响） |

这就是已知问题里"下界不是 ±5 V 端点"的机制：`x − L` 要落进 SADC 输入域，L 越大可用输入下界越高。
**它是范围缺口，不是数值错误**，必须单列统计，不能靠"最终码没 clip"掩盖。

---

## 2. 任务树那个 −4.855238970588 V 的来源（复原，非披露）

`attenuated_dither_bound(5.0)` 给出：

```
deficit = v_fs × fraction × beta = 5 × (63/128) × (1/17) = 0.1447610294117647 V
bound   = −5 + 0.1447610294117647 = −4.855238970588235 V
```

与任务树的 −4.855238970588 V **12 位一致**。所以那个数的机制是：
**子阵列 β=1/17 衰减后仍有 63/128 满幅的 dither**。

证据等级：**从数值反推的复原（RECONSTRUCTED_FROM_NUMBER）**。`63/128` 与 `β=1/17` 是输入参数，
不是原文披露；`β=1/17` 在 `task1/SPEC.md` 里写明，`63/128` 是本轮反推得到的。
不能把这条写进"论文披露"。

---

## 3. 数字解扰：只减一次

`digital_descramble(total_v)` 返回 `−total_v`，单一口径。

旧路径的坑是"SADC 侧减 L、RDAC 侧减 T、数字侧再减一次 U"。本模块用 `total_q` 一个量贯穿
模拟与数字，`known_common_q = rdac_input_shift_q`，测试 `test_program_total_matches_port_for_all_words`
校验 64 个字的 `input_referred_v` 与 `program.total_v` 逐字相同。

---

## 4. 论文机制对照

| 论文/专利说法 | 本模型状态 | 证据 |
|:---|:---|:---|
| 六位字拆 lower/upper（Fig.19） | ✅ 位映射与 Q32 权在 `dither.py`，64 字全枚举 | `test_program_total_matches_port_for_all_words` |
| "dither range enhanced by 2b" | ✅ upper 码 U ∈ {0,1,4,5}，即 2 bit 的粗码范围 | `max_upper_code = 5` |
| dither 与 DEM 联合工作 | ⚠️ 两条路径尚未同时在主循环里跑（见 P4 A6 NOT_RUN） | `p4_ablation.json` |
| dither 电容自身失配 | ❌ 未建模 | — |

---

## 5. 不能声称

* 不能声称 dither 已经"改善 DNL"——本轮只给出范围/残余/解扰，没有把 dither 接进主循环跑统计。
* 不能声称 −4.855238970588 V 是原文给的数——它是**复原**，且属于另一套几何（±5 V、β=1/17）。
* 不能声称电荷口版本已在 RTL 实现——RTL 仍走旧码偏移路径。
* 不能把 6.696 mV 残余当成"符号错误"：符号是对的，残余来自码本形状。
