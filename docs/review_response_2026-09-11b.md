# 对 2026-09-11 **第三份**外部复核的逐条裁定

- **对象**：`main` 分支 `54ba47f`（v7.0.1 提交），复核方明确固定在该提交
- **复核来源**：外部独立复核（第三份）；第二份的裁定见 `docs/review_response_2026-09-11.md`
- **裁定日期**：2026-09-11
- **方法**：每条指控在真实代码上独立复现或证伪；下表数字为本仓库自己测出的值。
  复现命令见 §5。**本轮做了全量复跑**（`adi-run-all` 等价入口，约 46 s），
  因此"数值零改动"是本轮实测结论，不是推断。

---

## 0. 立场

复核这次的表述是准确的，且我接受它对上一轮的更正：

> 默认 `Scheduler` 的 A/B 乒乓调度，与 `ShuffledScheduler` 必须分开评价。

这与本仓库 §2.1 的限定一致（8191/8191 与 0/8191 是两条不同的路径），
**接受，不辩解**。

它给出的总评价——"已有明确执行链和较好审计意识、能够开展条件性机制研究，
但关键物理连接仍待完成的精密 SAR 行为建模平台"——与 `docs/STATUS.md` 的定位
一致，**接受**。

**逐条裁定汇总**

| 编号 | 指控 | 裁定 | 本轮动作 |
|:---|:---|:---|:---|
| T1 | 验收门禁漏掉 `validate` / `validate_ktc` 的 FAIL | **成立** | **已修**，并因此翻出一条**当前确实在失败**的需求（见 T1b） |
| T1b | （核验 T1 时发现）`KTC 校正项带宽上限 f_max` 实测 FAIL，从未被任何门禁报告 | **成立（本轮自查追加）** | 登记为显式已知限制 + 文档写明边界 |
| T1c | 非法配置只被"报告"，没有被入口拒绝 | **成立** | **已修**（`Config.check_legal()` + 三个入口） |
| T2 | 覆盖后的披露值仍能通过 `require(DISCLOSED)` | **成立** | **已修**（`Graded.overridden` + `effective_grade`） |
| T3 | "物理池已接入"的测试只是源码字串检查 | **成立** | **已修**（改为行为级契约 + 正向对照） |
| T4 | `model_scope.md` 仍与新的限定冲突 | **成立** | **已修**（§2.1 第 5 条 + §5 + §6） |
| T5 | `7 + 2 = 9` 的措辞 | **成立** | 正文已改（API 改名仍留作版本级决策） |
| T6 | （本轮自查发现）`dem_mode` 声明了但无任何读取点 | **成立（本轮自查追加）** | 记录 + 钉子测试 |

---

## 1. 哪些结论继续接受，不改

- **"这不是只改了文档。"** v7.0.1 的固定增益主路径修复、拟合漏报修复、退出码
  连接都是真实改动 —— 复核确认了这一点，本仓库不需要为此辩解，也不应据此
  自满。
- **不能把 stage19 的洗牌抑制量当作电路规格预算或融合方案的定量依据。**
  接受，并已写进 `model_scope.md` §2.1。
- **"旧版本可复现，不应变成主线保留已知错误的理由。"** 接受。这正是本轮
  不再坚持"数值不变"作为目标的原因：本轮改动恰好**天然**不改数值（见 §3.4），
  而不是为了不改数值而回避修改。

---

## 2. 三处新缺口的核验与处置

### 2.1 T1 — 门禁漏掉配置自检表

**成立。** v7.0.1 的规则只列举了两种顶层结构（含 `"PASS"` 键的 dict、名字以
`_summary` 结尾的 dict），而 `run_all.py` 实际还写出：

```
results["validate"][<检查名>]["PASS"]        # 11 项
results["validate_ktc"][<检查名>]["PASS"]    # 4 项
```

复核给出的反例在真实规则上复现，返回值与它说的一致：

```python
hard_failures({"validate": {"invalid_config": {"PASS": False}}})   # 修复前 -> []
                                                                   # 修复后 -> ['validate.invalid_config']
```

**处置**：新增 `acceptance.ACCEPTANCE_CONTAINERS = ("validate", "validate_ktc")`，
这两张表按"每个 value 一条记录"收集。同时按复核的提醒**没有**退回"递归扫描所有
`False`"——`test_collecting_a_container_must_not_swallow_switch_values` 钉子住
"配置里的 `False` 与反例实验的 `False` 都不计入"这条边界。

**并加了一道覆盖度下限**（`MIN_RECORDS = 50`）：判据条数少于下限即报错。理由是
"漏检"最容易伪装成"全通过"——如果某个记录类别在某次重构里消失，条数会掉下来，
而不是报告变绿。

> **一处仍未完全满足复核要求，如实记录**：复核建议"检查必需结果是否缺失"，
> 我实现的是**条数下限**，不是**逐名清单**。逐名清单在这里会变成第二份需要
> 手工维护的 schema（记录名是中文说明性键，会随设计决策改名），维护不善就会
> 腐烂成"清单过期但没人知道"。条数下限能抓住"整类消失"，抓不住"某一条改名后
> 少了一条且别处多了一条"。这是有意的取舍，不是遗漏。

### 2.2 T1b — 核验 T1 时翻出一条当前确实在失败的检查

把 `validate_ktc` 纳入门禁后，门禁立刻红了。实测：

| 项 | 值 | 门限 | 判定 |
|:---|--:|--:|:--:|
| KTC 校正项带宽上限 f_max (满幅) | 2.5465 MHz | 5 MHz | **FAIL** |

复算（`Config()`，`ktc_enable=True`）：

```
margin = min(adc2_v_max − G0·Δ1, −adc2_v_min) = min(1.65 − 1.5, 0.15) = 0.15 V
Δt     = Ts/256 = 97.656 ns · (fs = 40 MHz)
f_max  = margin / (2π · v_fs · G0 · Δt) = 0.15 / (2π · 3 · 32 · 9.7656e-8) = 2.5465 MHz
```

`config.py` 顶部注释写的是"dt = Ts/256 时该上限约 5.6 MHz，正好覆盖 PPT 标注的
DC–5MHz 信号带"——那个数**属于更早的参数**（需 `margin ≈ 0.33 V` 才成立），
已经过期。也就是说：**披露的信号带是 DC–5 MHz，而当前参数下 KTC 校正项在满幅时
只到 2.55 MHz**。这与 `adc2.py` 里"5 MHz 单音即已 10.3 % 溢出"的既有记录一致。

**处置**：**没有**为了保持 CI 绿色而把它藏起来，也**没有**为了让它变绿而临时改
参数（那会移动已发布数值）。做法是显式登记进 `acceptance.KNOWN_LIMITS`，带理由
与两条解除条件，并在每次运行时打印。账本语义等价于 `xfail(strict=True)`：

- 名字不在结果里 → 报错（条目过期）；
- 名字当前已经通过 → 报错（豁免必须失效）。

即"豁免无法残留"。同时把这条边界写进 `model_scope.md` §6。

### 2.3 T1c — 非法配置只被报告，没被入口拒绝

**成立。** 复核指出两件事，都验证了：

```python
# ① 检查表写着 FAIL ≠ 入口拒绝
_lean_cfg(ra_gain_model="Fixd").validate(verbose=False)["RA 增益口径 …"][2]  # False
# 但 gain_vector() 对任何非 "fixed" 字符串仍走电容比路径，仿真照跑
```

**处置**：采纳复核的"把配置合法性检查与性能验收分开"：

| | 目的 | 手段 | 不通过时 |
|:---|:---|:---|:---|
| 合法性 | "这组数字不是任何东西的模型" | `Config.LEGAL_VALUES` + `Config.check_legal()` | 入口**抛 `ConfigError`** |
| 性能/余量 | "这个（合法）设计在某处余量小" | `Config.validate()` 记录表 | 报告里 FAIL，可登记为已知限制 |

合法性范围**刻意收窄且显式**：枚举取值不在表内（9 个字段）、`fs`/`v_fs` 非正、
`n_bits_target < 1`、`dac_n_sub < 2`（桥接电容名义式在此发散）、
`2**b1 > dac_levels`（第一级读数无法被该 DAC 表达）。三个入口
（`run_sim` / `run_sim_split` / `run_pipeline`）均调用。

### 2.4 T2 — 覆盖后的披露值仍能通过 `require`

**成立。** 实测（修复前）：

```python
annotate_config(Config(fs=80e6))["fs"].require(SourceGrade.DISCLOSED)
# -> 80000000.0     # 一个没有任何披露依据的数，通过了"披露值"检查
```

原因与复核判断一致：等级贴在**字段名**上，`require()` 只看等级，不看值是否被改过。

**处置**：`Graded` 新增 `overridden` 字段，`annotate_config` 在
"DISCOSED/DERIVED 且当前值 ≠ 字段默认值"时置位；`require()` 改查
`effective_grade`，把已覆盖的 `DISCLOSED`/`DERIVED` **降级为 `ASSUMED`**
（语义上正确：没有出版来源覆盖"这个"值，只能做敏感性研究）。
`Graded.map()` 传递该标志，派生量无法洗白被覆盖的上游。修复后：

```python
ann = annotate_config(Config(fs=80e6))
ann["fs"].overridden              # True
ann["fs"].effective_grade.name    # 'ASSUMED'
ann["fs"].require(SourceGrade.DISCLOSED)   # -> GradingError
annotate_config(Config())["fs"].require(SourceGrade.DISCLOSED)  # -> 40000000.0（未改动的仍可引用）
```

`source` 字符串**仍未改写**（它描述"论文怎么写"，`overridden` 描述"这个数还是不是
论文那个"），与上一轮的取舍保持一致。

### 2.5 T3 — 物理池的"已接入"测试不是行为级证据

**成立。** 原用例：

```python
assert "PhysicalSlicePool" in inspect.getsource(...)
```

复核说得对：一个未使用的 import 甚至一句注释都能让它通过。

**处置**：改为行为级契约——转换出的样本必须能追溯到产生它的物理 slice
（`sample_id` / `conv_slice_ids` / `acq_slice_ids` / `held_sample`），这样内部电荷、
DAC 权重与残差增益才能绑到同一组电容上。同时补一条**正向对照**，防止这条
`xfail` 因为错误的原因通过：调度器**已经**返回逐样本的采集/转换索引数组，并且满足
跨周期归属关系——缺的是**绑定**，不是数据。两条都保留 `xfail(strict=True)`。

复核那句"不能机械要求最终输出一定变化"已被采纳为契约的写法：断言落在
**内部物理量与可追溯性**上，不落在 SNDR 是否下降上。

---

## 3. 本轮自查追加

### 3.1 `dem_mode` 声明了但没有读取点

做合法性表时发现：`dem_mode` 存在于 `Config` 与 `PARAM_GRADES` 中，但
**全库没有任何模块对它有比较分支**。实际拿到哪种调度，取决于调用方实例化的是
`Scheduler` 还是 `ShuffledScheduler`。这与 v7.0.0 的 `ra_gain_model` 是同一类缺陷
（名字许诺了一个没接线的机制）。用 `TestR10DemModeIsInert` 钉子住当前事实
（`rotate` 与 `permute` 输出逐位相同），**接上那天这条测试会失败**，逼人改写。

### 3.2 `require()` 这个"强制钩子"没有生产调用点

`require()` 的 docstring 自称"the enforcement hook"，但生产代码里**一次都没调用**
（只出现在 doctest 与测试里）。也就是说 `docs/STATUS.md` 里"来源分级是承重墙"
这个说法，目前只在"报告 + 测试"层面成立。本轮把 `overridden` 的强制做实了，
但"每个数在被使用处都受检查"仍然是**目标**而非**事实**，已写进 CHANGELOG 的
"Found while verifying"。

### 3.3 KTC `f_max` 的处置是判断，不是发现

把它登记为已知限制是一个**有后果的判断**：它把一条当前未满足的需求变成了
"不阻塞"。理由与解除条件写在 `KNOWN_LIMITS` 的条目里，我不在这里重复辩护，
但明确列出两个出口，供你选：

- **(a)** 把 `f_max` 抬到 ≥ 5 MHz（改 `adc2_v_min` / `Δt` / `G0` 之一）——
  会移动已发布数值，属架构决策；
- **(b)** 确认本项是**信息性记录**而非验收判据（它报告的是一个上界，不是
  一个"必须成立"的契约），把它从 `validate_ktc` 的 PASS/FAIL 语义中移出。

### 3.4 本轮数值影响：零，且是实测

全量复跑与随仓库发布的参考输出**逐字节相同**：

```
/tmp/sweep702/results.json        65c047a93b7dbc7b85da9ff00f86b9f1b2ccdfe2f51fba7d0b3508adc34ecdec
tools/results/results.json        65c047a93b7dbc7b85da9ff00f86b9f1b2ccdfe2f51fba7d0b3508adc34ecdec
```

命令退出码 `0`，并打印：

```
已知限制（计为不通过，但已登记理由，不阻塞）: 1 项
  KNOWN validate_ktc.KTC 校正项带宽上限 f_max (满幅)
硬性验收: 全部通过（50 条判据，其中 1 条为已登记的已知限制）
```

这次"数值不变"不是目标，而是结果：本轮的改动全在**门禁、分级语义与测试**上，
没有触碰物理主路径。

### 3.5 本轮自查追加（第二批）：三处"注释说了、实现没有"的地方

第三份复核的主题是"修复落在 helper 与测试里，没落在产生结果的路径上"。用同一把
尺子量文档与注释，又量出三处 —— 都在**我自己这一轮新写的内容**里：

**(1) `config.py` 的 `LEGAL_VALUES` 注释引用了一个不存在的测试文件。**
注释写"`tests/unit/test_config.py` 会核对这张表与 validate() 的记录一致"，而
`tests/unit/` 下只有 `test_cli.py` / `test_provenance.py` / `test_slice_pool.py`，
**没有 `test_config.py`**。同一句里还写着"每条取值都能在代码里找到对应的比较分支"，
实测不成立：`self.stage1_reading` 在全库**零比较点**（只在 `paper_consistent()` /
`legacy_v61()` 工厂里被写入），`dem_mode` 同样（即 §3.1 那条）。
已把注释改成据实登记：列出哪 7 个字段有比较分支、哪 2 个没有、以及为什么仍然入表；
并改指真实用例 `TestR4ConfigTakesEffectAtTheRunner::test_an_illegal_enum_is_refused_for_every_declared_field`。

**(2) 契约测试 docstring 的索引表把本轮章节号写错了。** 第二张表把本轮对应章节
标成"§3.1 三处"并指向 `docs/review_response_2026-09-11.md`（那是**第二份**裁定
文档）。实际的对应关系是 `-11b.md` 的 §2.1（门禁）/ §2.4（require）/ §2.5（物理池）
与 §3.1（dem_mode）。已改为分两轮列表、逐行标注本文档章节号，并额外说明
`xfail` 理由里的 "§3.3 of the third review" 指**复核方自己的** §3 第 3 条
（即本文档 §2.5），与本文档 §3.3（KTC `f_max` 的处置）不是同一节。

**(3) 写法上的补充：docstring 里的 `Examples:` 从未被 CI 执行。**
`pyproject.toml` 原本是 `testpaths = ["tests"]` 且没有 `--doctest-modules`，
所以 `acceptance.py` `provenance.py` 里那些 `>>>` 示例只是**装饰**，跑不跑没人管。
这与本轮的主题是同一件事：一段断言，没有被执行就等于没有被验证。已把 `src` 加入
`testpaths` 并开启 `--doctest-modules`，**7 条文档示例**现在进 CI。

顺带一处可用性修正：门禁在"存在已登记已知限制"时**不打印结论行**——日志里只有一条
`KNOWN`，读日志的人（或 CI 日志搜索）看不出这是干净跑完还是被截断。现在只要命令能
以 0 退出就必然打印结论行，并注明其中几条是已登记的已知限制。

---

## 4. 未闭合（与上一份一致，并接受复核的优先级排序）

复核建议的顺序是：先修软件层面的漏检，再集中修物理主路径，最后推进新机制的
净收益验证。**第一段本轮已完成**（T1/T1c/T2/T3）。剩下：

| 优先级 | 工作 | 完成标准 |
|:---:|:---|:---|
| 2 | 统一 slice 选择、采样历史、电荷、DAC 求值与增益来源 | 通过真实执行链的因果与电荷检查；重跑洗牌实验基线 |
| 2 | 修 `flicker_series` 提前返回条件 | stage23 的 40 Hz 组不再是零序列 |
| 3 | 为 KTC 的 `v_N` 建有限精度读取通道（量化/编码/延迟） | 净收益能在明确的实现约束与误差预算下给出 |
| 3 | `Config.paper_consistent()` 的中性改名（旧名留别名） | —— |

按复核的建议，`f_max` 这条也会在第二段一并处理（届时它会从已知限制账本里
退出，账本的"过期即报错"机制会提醒）。

**并且接受复核的方法论要求**：下一轮不再以"数值不变"为主要目标，而是给出
一张"哪些数值变化、由哪项物理修复引起"的差异表。

---

## 5. 复核方式

```bash
# 全量门禁：现在会覆盖配置自检表，并打印已知限制与结论行；数值 FAIL 会返回非零码
PYTHONPATH=src python tools/run_all.py        # 或 adi-run-all
# 预期：exit 0；打印
#   KNOWN validate_ktc.KTC 校正项带宽上限 f_max (满幅)
#   硬性验收: 全部通过（50 条判据，其中 1 条为已登记的已知限制）

# 全量测试：含 7 条 docstring 示例（--doctest-modules 已进 addopts）
PYTHONPATH=src python -m pytest -q
# 预期：167 passed, 3 xfailed

# T1：门禁现在能看见配置自检的 FAIL（复核的反例）
PYTHONPATH=src python -c "
from adi_model.acceptance import hard_failures, acceptance_records
print(hard_failures({'validate': {'invalid_config': {'PASS': False}}}))   # ['validate.invalid_config']
print(len(acceptance_records(__import__('json').load(open('tools/results/results.json')))))  # 50
"

# T1c：非法配置在入口被拒绝
PYTHONPATH=src python -c "
from adi_model import Config, ConfigError
for kw in ({'ra_gain_model':'Fixd'}, {'dac_arch':'segmented'}, {'dac_arch':'split','dac_n_sub':1}):
    try:
        Config(**kw).check_legal(); print('未拒绝', kw)
    except ConfigError as e:
        print('已拒绝', kw)
"

# T2：覆盖值不再通过 require
PYTHONPATH=src python -c "
from adi_model import Config
from adi_model.provenance import annotate_config, SourceGrade
try:
    annotate_config(Config(fs=80e6))['fs'].require(SourceGrade.DISCLOSED); print('未拒绝')
except Exception as e:
    print('已拒绝:', type(e).__name__)
"

# 本轮全部契约（25 通过 + 3 强制 xfail = 未闭合缺陷清单）
PYTHONPATH=src python -m pytest tests/audit/test_review_contracts.py -q
```
---

## 6. 与另两份文档的关系

| 文档 | 裁定对象 | 状态 |
|:---|:---|:---|
| `docs/audit_response.md` | 冻结基线 v6.1 的审计（A01–A07） | 历史，不动 |
| `docs/review_response_2026-09-11.md` | v7.0.0 的第二份复核（R1–R8） | 历史，不动 |
| **本文件** | v7.0.1（`54ba47f`）的第三份复核（T1–T6） | 本轮 |

三者的裁定对象、证据与未闭合清单都不重合，不合并、不互相覆盖。

## 更新记录

| 日期 | 变更 |
|:---|:---|
| 2026-09-11 | 初版：6 条逐条裁定，4 条已修（T1/T1c/T2/T3）、2 条记录（T1b 已知限制、T5 部分）；全量复跑确认数值零改动 |
