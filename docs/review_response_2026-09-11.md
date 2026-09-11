# 对 2026-09-11 外部复核的逐条裁定

- **对象**：`main` 分支（`bef22e2`，v7.0.0 发布后状态）
- **复核来源**：外部独立复核（第二份，针对 v7.0.0；第一份针对冻结基线 `adi_model_release_v6.1`，回应见 `docs/audit_response.md`）
- **裁定日期**：2026-09-11
- **方法**：**每条指控都在真实代码上独立复现或证伪**，下表数字为本仓库自己测出的值，不引用复核方给出的数字作为证据。复现命令见 §5。

---

## 0. 立场

复核的核心指控不是"代码写错了"，而是：

> **部分修复已经进入新模块和测试，却没有进入产生系统级结果的主路径；部分架构假设又被赋予了比证据更强的名称。这会出现一种危险情况——测试通过了，但原本需要证明的事情仍然没有被证明。**

**这条元判断成立，而且我在核验过程中发现了一处比复核更严重的问题：ADR 0005 §3.4 是一句不实声明**（见 §2.1）。一份状态标为"已采纳"的 ADR 声称两条信号链都从同一个物理池取得物理量，而该池在两个 runner 里都不存在。

同时，复核的一条关键表述**需要加限定**，否则会造成与它反对的同样性质的过度概括：

> "`run_pipeline()` 当前使用的仍然是……旧调度实现……**这证明了新 helper 的性质，却没有证明系统级修复已经完成**。"

前半句正确；但"主路径因此是错的"**不成立**——默认调度（A/B ping-pong）在样本归属上是**正确**的，错的只有洗牌路径。把两者混为一谈，会把一个真问题夸大成三个假问题。§2.1 给出区分。

**逐条裁定汇总**

| 编号 | 指控 | 裁定 | 本轮动作 |
|:---|:---|:---|:---|
| R1 | 物理 slice 修复未接入主路径 | **成立（需加限定）** | 记录为未闭合，加强制标记测试 |
| R2 | `None` 代表启用拟合却被过滤掉 | **成立** | **已修** |
| R2b | 改数值后仍继承原披露标签 | **成立** | **已修**（新增 `overridden`） |
| R3 | `7+2=9` 被当作已证明的位数分配 | **成立**（证据强度问题） | 记录，待你决定是否改名 |
| R4 | KTC 是"理想数字观测上界"而非可实现架构 | **成立** | 记录为未闭合 + 显式边界声明 |
| R5 | 数值 FAIL 不使 CI 失败 | **成立** | **已修** |
| R6 | `ra_gain_model="fixed"` 在 split 主路径失效 | **成立** | **已修** |
| R7 | `flicker_series` 提前返回条件写反 | **成立** | 未修（会改 stage23 数值，见 §4） |
| **R8** | **（本仓库自查追加）ADR 0005 §3.4 不实声明** | **成立** | **已修** |

---

## 1. 哪些结论本仓库继续接受，不打算翻过来

复核建议的定位——"可审计的两级精密 SAR 架构与机制研究平台"——与 `docs/STATUS.md` 的既有定位一致，**接受，不改**。

复核明确指出"不要靠哪一种配置更容易得到目标 SNDR 来裁定位数"，这条方法论约束**接受**，并已作为 §4 的待办前提。

---

## 2. 逐条核验

### 2.1 R1 — 物理 slice 的修复没有接入主信号链

#### 成立的部分（代码级事实）

| 检查 | 结果 |
|:---|:---|
| `PhysicalSlicePool` 被哪些 **runner** 使用 | **无**（`pipeline.py` / `sim_split.py` / `sim.py` 均无引用） |
| `PhysicalSlicePool` 被哪些 **实验** 使用 | **无**（`experiments.py` 无引用） |
| 被哪些文件引用 | `__init__.py`（公开 API）、`tests/unit/test_slice_pool.py`、`tests/audit/test_audit_findings.py` |
| `run_pipeline` 的实际调度 | `sched = scheduler or Scheduler(cfg)`；`conv, acq = sched.reserve_dual(...)` |
| `run_pipeline` 的增益 | `g_vec = np.full(n_samples, c_sig / chip.C_feedback_true)`（整段常数） |
| DAC 误差是否接收 8/18 选择 | **否**。`SplitDAC.evaluate_physical(self, k_eq, sid)` 无选择参数 |

因此复核那句判断——"审计测试中关于'因果性已修复'的用例，实际测试的是新 `PhysicalSlicePool`……这证明了新 helper 的性质，却没有证明系统级修复已经完成"——**成立**。

#### 需要加限定的部分：默认路径本身是因果正确的

复核未区分两条调度路径。本仓库实测（`N = 8192`，seed `20260911`，独立复现）：

| 调度 | 跨周期衔接 | 完整接上前次采集组的次数 | 平均覆盖 | 同周期最大交叠 |
|:---|--:|--:|--:|--:|
| `Scheduler`（**`run_pipeline` 缺省**） | 8191 | **8191** | **8.000 / 8** | 0 |
| `ShuffledScheduler`（stage19 洗牌实验） | 8191 | **0** | **3.547 / 8** | 0 |
| `PhysicalSlicePool.shuffle_causal`（无人调用） | 8191 | 8191 | 8.000 / 8 | 0 |

理论期望 $E[|\mathcal C \cap \mathcal A|] = 8\times 8/18 = 3.556$，与实测 3.547 吻合——这是"两次**独立** 8-of-18 抽取"的指纹，**不是随机种子不好**。复核在这一点上的论证方法是对的。

**结论的精确形式**：

- 被否定的是"**18-slice 物理调度与跨周期因果已获系统级验证**"这一证据 —— **成立**；
- 但"主路径转换了没采过的电荷"只对**洗牌路径**成立，对**默认路径**不成立。

因此本仓库把 R1 记为**未闭合**，并以 `xfail(strict=True)` 强制标记（见 §3），而不是宣称已修。

#### 追加问题 R8：ADR 0005 §3.4 不实声明

`docs/adr/0005-physical-slice-pool.md` 状态为**已采纳**，§3.4 写：

> "**`pipeline` 与 `sim_split` 的一致性被保持。** 两条链都通过
> `build_first_stage_quantizer`（ADR 0004）与**同一个池**取得物理量，
> 退化等价仍是逐位一致。"

这句话在代码上不成立：两条链都没有使用该池。§3.5 的"第三个子指控（DAC 求值未接收选择）被单独关闭"同样不成立——`sid` 被接收了，但**选择 `conv` 没有**，而原指控针对的正是选择。

这与复核说的"部分架构假设被赋予了比证据更强的名称"是同一类缺陷，且出现在**文档**而非代码里，更应纠正。本轮已改写 ADR 0005 的这两条，并新增 §4 未闭合项。

### 2.2 R2 — `None` 恰恰代表启用拟合

**成立，已修。** 实测（修复前）：

```
resolve_ra_noise(Config()) = 1.0876e-3 V      # 拟合正在生效
fitted_in_use              = ['mismatch_sigma0', 'sadc_rdac_gain_mismatch']
'ra_out_noise_rms' in fitted_in_use = False   # 漏报
```

过滤条件是 `v.grade is FITTED and v.value is not None`，而 `ra_out_noise_rms` 存 `None` 正是因为拟合在生效。默认配置下最吃重的一个拟合参数，在"给评审读的第一份报告"里是隐形的。

**修复**：新增 `RESOLVED_SENTINELS` 表，把哨兵字段经解析器求出运行期值并记进 `Graded.note`；`fitted_in_use` 的判据改为"当前值能否移动一个数"（哨兵 = 解析成功即激活）。同时新增 `resolved_sentinels` 字段，让读者看到拟合**解出了多少**，而不只是"有一个拟合开着"。修复后：

```
fitted_in_use       = ['mismatch_sigma0', 'ra_out_noise_rms', 'sadc_rdac_gain_mismatch']
resolved_sentinels  = {'ra_out_noise_rms': '... resolved at runtime -> 0.001087642850799346'}
```

### 2.3 R2b — 改数值后仍继承原披露标签

**成立，已修。** 实测（修复前）：`Config(fs=80e6)` 的 `fs` 仍为 `disclosed`，来源仍是 `"[00] abstract: 40 MS/s"`。

**修复**：`audit_provenance` 新增 `overridden` —— 等级为 `DISCLOSED`/`DERIVED` 且当前值已不等于 `Config` 字段默认值的字段。`Config()` 的 `overridden` 为空；`Config(fs=80e6)` 报 `['fs']`。

注意这里保留了一个刻意的边界：**不改写 `source` 字符串**。来源句描述的是"论文怎么写"，而 `overridden` 描述的是"这个数还是不是论文那个"。把两者拆开比把来源句改掉更不容易骗人。

### 2.4 R3 — `7 + 2 = 9` 目前不能被当成位数的证明

**成立（证据强度问题，非算术问题）。**

证据在仓库自己的文档里：

- ADR 0003 §1 明确写道："`k = coarse*8 + dither` 扩大的是**码值表示范围**，不会凭空增加对未知输入的 3 位**判决**信息"；
- 同一 ADR 的 §2 随即用 `b1 + b_enh = 9`（`b_enh` = 码值量程增强）联立求解，得到 `b1 = 7`，并称之为"与 (a)(b) 同时相容的**唯一**分配"。

"唯一性"只在一个**被假设的等同**下成立：把"第一级 9b 量化"读成"判决位数 + 码值量程位数"。这恰好是 §1 刚刚拒绝过的那种等同。复核对此的判断成立。

仓库其实已经如实分级了：`provenance.PARAM_GRADES["b1"] = ASSUMED（"ARCHITECTURAL READING"）`。**问题出在措辞而非分级**：ADR 标题"已采纳"、`Config.paper_consistent()` 这个名字、以及"唯一分配"，都读起来像结论而非假设。`stage1_reading` 反而被标为 `DISCLOSED`。

**本轮不改代码**：改名会动公共 API（`Config.paper_consistent` 被测试与实验引用），属版本级决策。建议见 §4。

### 2.5 R4 — KTC 更接近"理想数字观测上界"

**成立，未修（架构级）。** 代码事实：

```python
vq, over = self.quantize(v)
return vq - np.asarray(correction, dtype=float), over
```

`correction = κ·v_N` 是浮点数组，直接数字域相减；**观测电压 `v_N` 的量化、编码、延迟都没有建模**。ADR 0006 记录的是"把减法移到量化之后"这一架构选择及其量程收益，确实没有把观测转换的代价接进来。复核给出的输入折算噪声

$$\sigma_{q_N,\mathrm{in}} = \frac{|\kappa|\,\Delta_N}{\hat G\,\alpha\sqrt{12}}$$

方向正确，可作为补齐该通道时的验收式。**本轮动作**：在 `docs/model_scope.md` 的适用域里把 KTC 收益的标注改为"已含部分模拟非理想，但数字观测读取理想化"（见 §3 变更清单）。

### 2.6 R5 — CI 需要否决错误结果，而不只是证明程序能跑完

**成立，已修。** `tools/run_all.py` 此前逐条 `print(PASS/FAIL)` 并写入 `results.json`，**全文没有任何 `sys.exit` / `raise`**。CI 的 sweep 步骤只看退出状态，因此"报告写着 FAIL"与"CI 变红"互不相关。

**修复**：新增 `adi_model.acceptance.hard_failures()`（独立成模块是为可单测——`run_all.py` 是脚本，import 它会跑完全部实验），`run_all.py` 末尾据其退出非零。

判定范围**显式列举**，不做递归布尔扫描（复核也提醒了这一点——配置里的 `False` 与设计上就该失败的反例实验不等于验收失败）：

- `R[name]` 是 dict 且含 `"PASS"` 键 → 1 条验收记录（10 条）
- `R[name]` 键名以 `_summary` 结尾 → 每个 value 1 条记录（5 组）

合计覆盖 **35 个布尔判据**（10 个顶层验收记录 + 5 个 `_summary` 组下的 25 个布尔项），与报告逐条 print 的内容一一对应。用当前（全通过）的 `results.json` 验证：**误报 0 条**（`hard_failures` 返回空列表），且规则本身有单测钉住（含"反例实验的 `False` 不计入"这条边界）。

### 2.7 R6 — `ra_gain_model="fixed"` 在 split 主路径没有控制增益

**成立，已修。** 代码事实：

| 链路 | 增益来源 | `fixed` 是否生效 |
|:---|:---|:---:|
| `sim.py`（unary） | 读 `cfg.ra_gain_model` | ✅ |
| `sim_split.py` | `np.full(n, c_sig / C_feedback_true)` 硬编码 | ❌ |
| `pipeline.py` | 同上 | ❌ |

附带发现：`ResidueAmplifier.gain_vector()` **是全库死代码**（无任何调用点）——它实现了分支，但两条链路都绕过它。

**修复**：两条链路改为 `ra.gain_vector(np.full(n, c_sig), chip.C_feedback_true)`。验证（`gain_error=0.01`，`n=2048`）：

```
sim_split  charge g=31.999950  fixed g=32.320000  max|Δout|=4.6349e-04 V   fixed生效=True
pipeline   charge g=31.999950  fixed g=32.320000  max|Δout|=4.6349e-04 V   fixed生效=True
```

默认 `charge` 下 `gain_vector` 返回的正是 `c_sig/C_F`，与原表达式逐位相同 —— **默认路径数值零改动**（见 §3 的字节比对）。

另按复核"要么按契约生效，要么被明确拒绝"的原则，`Config.validate()` 新增判据 `RA 增益口径 ra_gain_model ∈ {charge, fixed}`：拼写错误（如 `"Fixd"`）从此是显式 FAIL，而不是静默退化成 `charge`。

### 2.8 R7 — 短记录闪烁漂移补偿的提前返回条件写反

**成立，未修（会改 stage23 数值）。** 代码：

```python
if f_corner <= f_min or f_min <= f_low:
    return x
```

`fs=40 MHz`、`n=32768`、`fc=40 Hz`、`t_obs=10 s` 时 `f_min=1220.7 Hz`，`f_corner <= f_min` 为真 → **直接返回**。而函数注释写的是"转角低于可分辨下限，**且**观测时长内也没有可积功率 -> 无可补分量"，即应为 `and`。实测该参数组下 `nonzero_samples = 0 / 32768`。

两处需同时修：

1. 条件由 `or` 改为判断"可积区间是否为空"，即 `f_upper = min(f_corner, f_min)`，再检查 `f_upper > f_low`；
2. 积分上限用 `f_upper` 而非 `f_min`（`fc < f_min` 时把 1/f 形状外推到 `f_min` 会高估）。

**本轮不改**：默认 `flicker_corner_hz = 0.0`，故对默认主路径数值中性；但 stage23 显式传 `flicker_corner_hz=40.0`，修好之后那一组结果会变，属参考输出变更，见 §4。

---

## 3. 本轮已完成的变更

| 文件 | 变更 | 数值影响 |
|:---|:---|:---|
| `src/adi_model/provenance.py` | `RESOLVED_SENTINELS` + 解析值入 `note`；`fitted_in_use` 判据改为"是否激活"；新增 `resolved_sentinels` / `overridden` | 无（不在结果 JSON 里） |
| `src/adi_model/sim_split.py` | 增益经 `ra.gain_vector`，不再硬编码电容比 | **默认零**；`fixed` 模式从此生效 |
| `src/adi_model/pipeline.py` | 同上 | 同上 |
| `src/adi_model/config.py` | `validate()` 新增 `ra_gain_model` 取值判据 | 仅向结果 JSON 增 1 条校验记录 |
| `src/adi_model/acceptance.py` | **新增**：`hard_failures()` 判定规则 | 无 |
| `tools/run_all.py` | 末尾据 `hard_failures(R)` 退出非零 | 无 |
| `docs/adr/0005-physical-slice-pool.md` | 删除 §3.4 不实声明，§3.5 改为如实描述，新增未闭合项 | 无 |
| `docs/model_scope.md` | KTC 收益标注补"数字观测读取理想化"边界 | 无 |
| `tests/audit/test_review_contracts.py` | **新增** 13 条契约测试（10 通过 + 3 强制 xfail） | 无 |
| `tests/unit/test_provenance.py` | 原 `test_fitted_in_use_lists_only_truthy_values` 钉住了被修正的错误行为，改为 `test_fitted_in_use_lists_active_values`，并新增 `overridden` 用例 | 无 |

**三个未闭合缺陷以 `xfail(strict=True)` 强制标记**：标记在，测试报告里就能看见；缺陷一旦被修好，`strict` 会让 XPASS 变成失败，**逼着人摘掉标记**，不会留下过期的"已完成"印象。

**数值零改动验证**：全量实验复跑与复跑前基线逐键比对 —— **值变化 0 处**；唯一差异是 `validate` 新增的那 1 条校验记录（该记录本身含 `实际/门限/PASS/单位` 四个字段，此前误记为"4 条记录"）。

| 项 | 值 |
|:---|:---|
| 复跑前 `results.json` SHA256 | `fb77298df88e302e200a5f604bcdaf4471420176961c1abfe7a512cd542bfd3c`（v7.0.0 发布值） |
| 复跑后 `results.json` SHA256 | `65c047a93b7dbc7b85da9ff00f86b9f1b2ccdfe2f51fba7d0b3508adc34ecdec`（v7.0.1） |
| 逐键值级差异 | **0 处**（唯一新增键：`validate` → `RA 增益口径 ra_gain_model ∈ {charge, fixed}`） |

---

## 4. 未闭合（需先决定影响面，再动手）

以下三项都会**变更参考输出** `tools/results/results.json`，而 v7.0.0 已发布并附带该文件的哈希。因此不在本轮静默修改。

### 4.1 R1 主路径改造（复核建议的"第一批"）

统一物理状态源，让实际采样电容、DAC 权重、残差增益、输出样本编号出自同一份选择结果；主路径强制携带 `sample_id / conv_slice_ids / acq_slice_ids / held_sample / valid`。

验收（复核的表述精确，采纳为判据）：

> 改变实际选中的电容，在非零失配条件下，是否改变了对应的内部电荷、DAC 权重或增益？转换出来的样本，是否确实来自那些电容持有的输入？

**影响**：stage19 的洗牌组（"带宽失配降 31 dB / timing skew 降 42 dB"）数值会变——那正是被 R1 否定的那组结论。

### 4.2 R7 闪烁漂移补偿

修好之后 stage23 的 40 Hz 组不再是零序列。

### 4.3 R3 读数的措辞

建议其一（不静默实施）：把 `Config.paper_consistent()` 重命名为不预设结论的名字（如 `Config.hypothesis_7b_plus_2b()`），旧名保留为别名并标 deprecated；ADR 0003 去掉"唯一分配"的表述，改为"在'码值量程增强 = 判决能力'这一**假设**下自洽"。保留其他读法并分别推导残差范围、判决次数、dither 注入与转移关系。

**前提（采纳复核的方法论约束）**：不得用"哪种配置更容易得到目标 SNDR"来裁定位数。

### 4.4 R4 KTC 观测通道

为 `v_N` 补上"量化 → 编码 → 数字端"的可实现通道（观测 ADC 或复用 ADC2），并把 $\Delta_N$ 折输入噪声接进预算；在此之前收益只能标为性能上界。

---

## 5. 复核方式

```bash
# 契约测试（10 通过 + 3 强制 xfail；xfail 即未闭合缺陷清单）
PYTHONPATH=src python -m pytest tests/audit/test_review_contracts.py -q

# R1：独立复现三条调度的跨周期归属
PYTHONPATH=src python -c "
import numpy as np
from adi_model import Config
from adi_model.scheduler import Scheduler, ShuffledScheduler
cfg = Config(); n = 8192
for name, s in (('Scheduler', Scheduler(cfg)),
                ('ShuffledScheduler', ShuffledScheduler(cfg, np.random.default_rng(20260911)))):
    conv, acq = s.reserve_dual(n, np.random.default_rng(20260911))
    full = sum(1 for i in range(1, n) if set(conv[i]) == set(acq[i-1]))
    print(f'{name:18s} full={full}/{n-1} mean_cov={np.mean([len(set(conv[i])&set(acq[i-1])) for i in range(1,n)]):.3f}/8')
"

# R2：默认 RA 拟合必须出现在 fitted_in_use 里
PYTHONPATH=src python -c "
from adi_model import Config, audit_provenance
r = audit_provenance(Config())
print('fitted_in_use      =', r['fitted_in_use'])
print('resolved_sentinels =', r['resolved_sentinels'])
print('overridden(fs=80M) =', audit_provenance(Config(fs=80e6))['overridden'])
"

# R5：硬性验收规则
PYTHONPATH=src python -c "
from adi_model.acceptance import hard_failures
print(hard_failures({'pipeline': {'PASS': False}, 's12_summary': {'a': True, 'b': False}}))
print(hard_failures({'反例对照': {'loss_db': -6.0}}))   # 非验收记录 -> 不计
"

# R6：fixed 增益必须进入两条链的输出
PYTHONPATH=src python -m pytest tests/audit/test_review_contracts.py -q -k ra_gain_model

# 全量验收（数值 FAIL 现在会返回非零退出码）
adi-run-all --results-dir /tmp/sweep
```

---

## 6. 本文件与 `docs/audit_response.md` 的关系

`docs/audit_response.md` 回应的是**针对冻结基线 v6.1** 的第一份审计（A01–A07）。本文件回应的是**针对 v7.0.0** 的第二份复核。两者的裁定对象、证据与未闭合清单都不重合，不合并、不互相覆盖。

## 更新记录

| 日期 | 变更 |
|:---|:---|
| 2026-09-11 | 初版：8 条逐条裁定，5 条已修、3 条记为未闭合 |
| 2026-09-11 | 发布为 v7.0.1；补齐两版 `results.json` 的实际 SHA256；订正 §3 中"新增 4 条记录"的误记（实为 1 条） |
