# ADR 0007 — 参数来源分级升级为运行时机制

- 状态：**已采纳**
- 关联审计条目：**A04**（DR 与失配量是输入标定，不是独立预测）、
  A05（KTC 支路是研究扩展）
- 影响模块：`provenance.py`、`config.py`、`experiments.py`、全部 stage 输出
- 回归测试：`tests/unit/test_provenance.py`

---

## 1. 问题

v6.1 有一个**文档约定**：每个参数在注释里标 `[披露]` / `[拟合]` / `[假设]`。
约定本身是对的，执行方式是错的 —— 它没有强制力。审计 A04 的具体指控：

> `config.py` 明确反推 RA 输入参考噪声：
> `σ_RA² = max[(V_FS,rms·10^(−DR_target/20))² − σ_kTC² − σ_q2², 0]`。
> 关闭失配后的 DC 噪声检查：目标 DR 90 / 94.6 / 98 dB，测得 90.0148 /
> 94.6106 / 98.0014 dB。**因此模型重现 94.6 dB 是噪声预算闭合，不是从独立
> 电路参数预测得到。**

也就是说：一个由目标值**反推**出来的参数，被写在一份"全部 PASS"的汇总里，
和从论文抄来的数字并列，读者无法分辨。这不是算错了，是**记账方式**错了。

A05 是同一问题的另一种表现：KTC 观测器是**我们的**想法，却和已披露机制
并列出现在结果里，让人读成"原芯片具备这个能力"。

**一份注释拦不住这件事。注释不能被测试。**

## 2. 决定

把分级从注释约定升级为**运行时值**，并让三件事可被机器检查：

### (1) 值随身携带来源

```python
@dataclass(frozen=True)
class Graded(Generic[T]):
    value: T
    grade: SourceGrade
    source: str          # 引用出处或推导式
    unit: str = ""
```

`Graded.require(...)` 在运行时断言"这个数字的等级够不够格写进这句话"。

### (2) 每个 `Config` 字段必须在表中登记

`PARAM_GRADES`（88 项）逐字段给出 `(等级, 出处)`：

| 等级 | 中文 | 可当事实引用 |
|:---|:---|:--:|
| `DISCLOSED` | `[披露]` | ✅ |
| `DERIVED` | `[推导]` | ✅ |
| `FITTED` | `[拟合]` | ❌ |
| `ASSUMED` | `[假设]` | ❌ |
| `RESEARCH_EXTENSION` | `[研究扩展]` | ❌ |

`SourceGrade.quotable` 把"能不能引用"编码成一个属性，而不是一条口头约定。

### (3) 两个方向的漂移都是红构建

| 漂移 | 谁抓住 |
|:---|:---|
| 新加字段但没登记等级（或键名写错） | `test_param_grades_key_matches_field_name` |
| 没登记等级的字段被静默当成 `[假设]` | `test_ungraded_field_defaults_to_assumed` |
| 登记了已删除的字段（孤儿） | `test_ungraded_is_empty_by_construction` |

### (4) 审阅入口是一个人能读完的

```python
audit_provenance(cfg) -> {
    "counts": {...},          # 各等级计数（按 PARAM_GRADES 全表）
    "ungraded": [...],        # 必须为空
    "fitted_in_use": [...],   # 当前取值非零的拟合参数
    "assumed_in_use": [...],  # 当前取值非零的假设参数
    "verdict": "OK: no ungraded parameters",
}
```

实测（三种读数一致）：

| 读数 | counts | ungraded | verdict |
|:---|:---|--:|:---|
| `Config()` | `{assumed: 57, derived: 9, disclosed: 11, fitted: 3, research_extension: 8}` | **0** | OK |
| `Config.paper_consistent()` | 同上 | **0** | OK |
| `Config.legacy_v61()` | 同上 | **0** | OK |

`PARAM_GRADES` 共 **88** 项：`assumed 57 / derived 9 / disclosed 11 /
fitted 3 / research_extension 8`。

`*_in_use` 是 `counts` 的**子集**，因为它排除了取值为零（等于"未启用"）的
参数：当前 `assumed_in_use` 有 38 项、`fitted_in_use` 有 2 项
（`mismatch_sigma0`、`sadc_rdac_gain_mismatch`）——
即"正在生效的"比"已登记的"少，而**这个差本身就是信息**。

## 3. 后果

1. **`fitted_in_use` 就是 A04 要的那份清单。** 读者不必读代码，只要看这一项
   就知道哪些"漂亮数字"是拿目标值锚回去的。表里共 3 项 `FITTED`：

   | 字段 | 出处（表中原话） |
   |:---|:---|
   | `ra_out_noise_rms` | `None => reverse-solved from target_dr_db (ANCHOR, not prediction)` |
   | `mismatch_sigma0` | `100 ppm behavioural calibration; NOT a PDK value` |
   | `sadc_rdac_gain_mismatch` | `set to be consistent with >11b matching [00]` |

   前两项正是 A04 点名的反推链路（`ra_out_noise_rms` 默认 `None`，
   即"未启用"，所以不在 `fitted_in_use` 里）。注意 `target_dr_db = 94.6 dB`
   本身是 `DISCLOSED`（论文披露值）—— 被标为拟合的是**由它反推出来的噪声**，
   这个区分正是 A04 的核心：锚点可以引用，"重现锚点"不可以当预测。
2. **`RESEARCH_EXTENSION` 有 8 项，其中 KTC 支路默认关闭。** 这是 A05 要求
   的"不得归功于原作者"在代码层面的落地；`ktc.py` 的模块 docstring 也写明
   了"our idea, not the paper's"。
3. **`[假设]` 的 57 项被显式暴露，而不是被 94.6 dB 这个数字掩盖。**
   ADC2 的 14 bit 与 [-0.15, 1.65] V 就在这 57 项里 —— 它们与 ADR 0003 的
   读数自洽，但**不是披露值**。
4. **代价：新增参数时必须同步写一行表。** 这是刻意的摩擦 —— 它把"忘了标注"
   从发表后的勘误变成一个红色的构建。

## 4. 未闭合的部分

- **`Graded` 目前主要用于审计入口与文档，没有强制贯穿每一条计算路径。**
  也就是说：一个用 `FITTED` 参数算出的中间量，本身不自动携带等级。
  完全贯穿需要把 `Config` 的每个属性都改成返回 `Graded`，代价过大。
  折中方案是 `audit_provenance()` 给出"哪些等级的参数正在生效"。
  这是一个**已知的、被记录的**折中，不是遗漏。
- **分级正确性靠人工。** 机制能保证"有登记"，不能保证"登记得对"。
  把 `mismatch_sigma0` 标成 `[披露]` 仍然能通过测试 —— 但那条表是
  review 的**唯一位置**，比散落在 26 个文件里的注释好得多。

## 5. 复核方式

```bash
pytest tests/unit/test_provenance.py -q

PYTHONPATH=src python -c "
import json
from adi_model import Config, audit_provenance, grade_of
rep = audit_provenance(Config.paper_consistent())
print('counts   :', json.dumps(rep['counts'], sort_keys=True))
print('ungraded :', rep['ungraded'])
print('fitted   :', rep['fitted_in_use'])
print('verdict  :', rep['verdict'])
print('g0 grade :', grade_of('g0'))
# -> counts   : {\"assumed\": 57, \"derived\": 9, \"disclosed\": 11, \"fitted\": 3, \"research_extension\": 8}
# -> ungraded : []
# -> verdict  : OK: no ungraded parameters
"
```
