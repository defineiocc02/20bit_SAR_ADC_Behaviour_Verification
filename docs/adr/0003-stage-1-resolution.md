# ADR 0003 — 第一级分辨率的架构读数

- 状态：**已被 ADR 0007 取代**。下文保存历史推理，不代表当前架构结论。
- 关联审计条目：**A01**（已披露架构映射错误）
- 影响模块：`config.py`、`sadc.py`、`adc2.py`、`mapper.py`、`experiments.py`

---

当前决定见 [ADR 0007](0007-independent-decisions-and-dither.md)：9b 决策与 dither
范围独立。本文历史上的“7+2 唯一分配”论证不成立，不能作为论文事实引用。

## 1. 问题

目标论文 [00] 正文给出两个**互相约束**的事实，但没有给出把二者对齐的位数分配：

> (a) "...resulting in **9b quantization in the first stage**."

> (b) "the **dither range is enhanced by 2b** when transferred from the
> quantizer to the RDAC."

v6.1 读作 (a) = 6b 粗码 + 3b RDAC 细分，并把 (b) 当作"码值表达范围变大"。
外部审计 A01 指出这不是同一件事：`k = coarse*8 + dither` 扩大的是**码值表示
范围**，不会凭空增加对未知输入的 3 位**判决**信息；`sadc.py` 实际只输出
64 个区间。审计同时给出灵敏度对照：改成 512 个区间后 Δ1 从 93.75 mV 降到
11.72 mV，最大 RA 输出从 3.0 V 降到 0.375 V —— 整个后端预算随之改变。

**核心困难**：论文没有公开 ADC2 的位数与量程，因此"哪种读法"不能靠单条
披露裁定，必须让两条披露**同时**成立。

## 2. 决定

把"一个第一级判决步包含多少个 RDAC 单位步"当作**唯一的自由变量**，
令两条披露联立求解：

```
b1 + b_enh = 9          （披露 a：一级码字 9b = 粗位 b1 + 增强位 b_enh）
b_enh      = 2          （披露 b：转移时 dither 量程增强 2b）
=>  b1 = 7,  units_per_lsb1 = 2^b_enh = 4
```

即：**SADC 7 bit（Δ1 = 46.875 mV），一个粗判决步 = 4 个 RDAC 单位步，
一级码字 7 + 2 = 9 bit。**

由此**派生**（不再手改，`validate()` 硬校验）：

| 量 | 值 | 推导 |
|:---|:---|:---|
| Δ1 | 46.875 mV | `2·v_fs / 2^b1` |
| 残差范围 | [0, Δ1] = [0, 46.875] mV | 单极性 |
| RA 输出 | [0, G0·Δ1] = [0, 1.5] V | G0 = 32 |
| ADC2 | 14 bit，[-0.15, +1.65] V | 上/下各 ~10% 余量 |
| LSB2 | 109.863 µV | 1.8 V / 2^14 |
| LSB2 / G0 | 3.433 µV = **0.60·LSB20** | ≤ 1 LSB20，后端不成为瓶颈 |

## 3. 单一旋钮，三个读数

实现上只有一个真正的旋钮：**`Config.b1`**。其余全部派生：

```python
@property
def units_per_lsb1(self) -> int:
    """一个 Delta1 对应多少个 RDAC 单位。"""
    return self.n_units_sig // (2 ** self.b1)      # 512 // 2**b1
```

`stage1_reading` 是一个**自我描述标签**（写进 provenance、写进结果 JSON），
不是会隐式改变数值的开关 —— 选错读数必须由人写下 `b1=…`，并在 JSON 里留下
一个不匹配的标签，`tests/audit/test_audit_findings.py` 会对此断言。

| `stage1_reading` | b1 | units_per_lsb1（派生） | 增强位数 | 为何不作为默认 |
|:---|--:|--:|--:|:---|
| `paper_consistent`（默认） | 7 | 4 | 2 | 与 (a)(b) 同时相容的**唯一**分配 |
| `paper_literal` | 9 | 1 | 0 | 只取 (a) 字面；增强 0b 与 (b) 直接冲突 |
| `legacy_codeword` | 6 | 8 | 3 | v6.1 读法；增强 3b **大于**披露的 2b，仅为复现旧结果 |

三种读数都通过 `validate()`（各自的后端预算不同）：

| 读数 | b1 | upl1 | dither 增强 | 后端 ADC2 |
|:---|--:|--:|--:|:---|
| `paper_consistent` | 7 | 4 | 2b | 14 bit，[-0.15, +1.65] V |
| `paper_literal` | 9 | 1 | 0b | 14 bit，[-0.0375, +0.4125] V |
| `legacy_codeword` | 6 | 8 | 3b | 15 bit，[-0.30, +3.30] V |

`Config.paper_consistent()` / `Config.legacy_v61()` 构造前两者中的两个；
`audit_provenance()` 对三种读数都返回 `OK: no ungraded parameters`。

## 4. 后果

1. **旧读数的下游判据全部失效并已重推。** 切到 b1=7 后 `units_per_lsb1`
   由 8 变 4，子阵列（`n_sub = 8`）第一次被码调制，stage13 base 行的确定性
   INL 由 0.33 升到 3.29 LSB20。该效应有解析预测（子节点寄生 →
   `0.5·v_FS·|g_true − g_nom| / LSB20`，预测 3.17、实测 3.29，比值 1.04），
   见 `experiments.stage13_dynamics()["sub_weight_inl"]`。
2. **这是如实入账，不是回归。** 论文 PPT p.31 的 "Binary-to-unary bridging"
   校正处理的正是这一子权失配；本模型**未实现**该校正，因此 base 行高于
   论文的 2.2 LSB 是**应有**的结果（见 `docs/model_scope.md` §4）。
3. **A01 的实质要求被满足**：改读数不再是改一个参数，`validate()` 会检查
   `2**b1 ≤ DAC 电平数`、`Δ2/G0 ≤ LSB20`、`残差峰值 < ADC2 上界`；三条任一
   不成立即 FAIL。`tests/audit/test_audit_findings.py` 对三种读数逐一断言。
4. **没有声称已复现原芯片。** ADC2 的 14 bit 与 [-0.15, 1.65] V 仍是
   `[假设]`（PPT 未公开），只是"与本读数自洽"的取值。ADR 不改写这条边界。

## 5. 复核方式

```bash
pytest tests/audit/test_audit_findings.py -k resolution -q
pytest tests/regression/test_observer_headroom.py::TestSubWeightInducedINL -q

PYTHONPATH=src python -c "
from adi_model import Config
rows = [('paper_consistent', Config.paper_consistent()),
        ('paper_literal',    Config(b1=9, dither_enhancement_bits=0,
                                    stage1_reading='paper_literal', adc2_n_bits=14,
                                    adc2_v_min=-0.0375, adc2_v_max=0.4125, ra_v_clip=0.45)),
        ('legacy_codeword',  Config.legacy_v61())]
for name, c in rows:
    bad = [k for k, (_, _, ok, _) in c.validate(verbose=False).items() if not ok]
    print('%-16s b1=%d upl1=%-2d enh=%db  FAIL=%s'
          % (name, c.b1, c.units_per_lsb1, c.dither_enhancement_bits, bad))
"
```

`validate()` 中与本节直接相关的判据：

| 判据 | 表达式 | 作用 |
|:---|:---|:---|
| `第一级读数与 DAC 拓扑自洽` | `2**b1 <= DAC 电平数` | 把"b1=9 套在 (64,8) 分段阵列"变成显式 FAIL，而不是静默把残差推出 ADC2 窗口 |
| `第一级栅格可整除` | `DAC 电平数 % 2**b1 == 0` | 栅格对不齐时直接报错 |
| `分段 DAC 电平数 >= 2**b1 * 2**增强位数` | 取 `dither_enhancement_bits` | 曾把 `+3b` 写死（v6.1 读法），换读数后产生 **假 FAIL**；现在随读数自动跟随 |
| `后端分辨能力 Delta2/G0 <= LSB20` | 派生量 | 后端不得成为 20b 目标的瓶颈 |
| `残差峰值 G0*r_max < ADC2 上限` | 派生量 | 溢出边界 |
