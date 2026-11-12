# 工程可读性度量基线（2026-11-13）

> 用途：给后续重构一个**可对比的起点**。这些数字都能用 `tests/unit/test_public_api_docs.py`
> 与下面的脚本复算，不是印象。

---

## 1. 公开 API 文档覆盖率

| 口径 | 数量 | 覆盖率 |
|:---|:---:|:---:|
| 全部公开函数（含嵌套闭包） | 374 | **96.0%** |
| 其中缺 docstring | 15 | — |
| └ 嵌套局部辅助函数 | 11 | 由外层 docstring 覆盖，**不要求** |
| └ **真正的模块级公开 API** | **4** | 已补齐（见下） |

补的 4 个：`cli.error`、`EventLog.add` / `for_sample` / `all`。

守卫：`tests/unit/test_public_api_docs.py`
* 模块级公开函数/方法**必须**有 docstring；
* 整体覆盖率**不得低于 90%**（防止用无文档的辅助函数稀释可读性）。

## 2. 函数长度

超过 60 行的函数共 **45 个**。最长几个：

| 行数 | 位置 |
|:---:|:---|
| **683** | `simulation/pipeline_engine.py::execute_pipeline` |
| 272 | `defaults.py::inference_table` |
| 228 | `inventory_gate.py::_check_code_and_tests` |
| 213 | `defaults.py::simulate` |
| 212 | `defaults.py::__init__` |
| 174 | `behavioral/reference_charge.py::physical_reference_program` |

### 为什么本轮**不**拆 `execute_pipeline`

它是唯一主链，683 行，只有一个分节标记（224 减法器）。拆它需要：

1. 先冻结一个逐位可比的基线（行为指纹覆盖 12 个输出数组）；
2. 按"装配 / 采集 / 逐事件转换 / 解码"四段切；
3. 每切一段跑一次指纹比对，逐段提交。

**这是一项独立工作**，与本轮的文档/计数守卫不是一类事情。本轮把它记为基线，
下一轮或后续单独做。

## 3. 复算方式

```bash
# 文档覆盖率
PYTHONPATH=src python3 -m pytest tests/unit/test_public_api_docs.py -q

# 函数长度
PYTHONPATH=src python3 -c "
import ast, pathlib
root = pathlib.Path('src/adi_model')
rows = []
for p in root.rglob('*.py'):
    tree = ast.parse(p.read_text())
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            length = (n.end_lineno or n.lineno) - n.lineno + 1
            if length > 60:
                rows.append((length, str(p), n.name))
rows.sort(reverse=True)
print(len(rows), 'functions over 60 lines')
for r in rows[:6]:
    print(r)
"
```

---

## 4. 行为指纹基线（2026-11-13 更新）

### 4.1 漂移与原因

本会话改了四个参数，全部下游到指纹，共漂移 **12 个字段**：

| 参数 | 基线时 | 现在 |
|:---|:---:|:---:|
| `unit_mismatch_std` | 1e-3 | **1e-4**（论文指标反解）|
| `adc2_n_bits` | 12 | **14**（ADR-0003）|
| `ra_v_clip` | 0.5 | **0.45**（= 窗口宽度）|
| `C_sub` 装配列数 | 5 | **4**（第 5 位是虚线占位）|

漂移字段：`codes_voltage` / `float_out` / `coarse` / `adc2_code` / `main_masks`
（`static_dem_off` 与 `static_dem_on` 两组）。与改动完全对应，非意外。

已重新生成并复查：`behavior fingerprint matches the baseline`。

### 4.2 顺带修掉两处硬编码

`tools/behavior_fingerprint.py` 里 σ 写死了两处：

```python
model = _build_model(1e-3, True)                       # ← 写死
"model": "DefaultModel(mismatch=1e-3, noise on)",      # ← 写死
```

改成从 `DefaultParameters()` 读取。基线里现在写的是真实值：
`DefaultModel(mismatch=1e-04, adc2_n_bits=14, noise on)`。

这是本会话反复出现的同一类问题（"参数只有一个定义点"）。

### 4.3 新增 C7 门禁

`check_engineering.py` 从 6 项增至 **7 项**。C7 校验：
**基线元数据必须与 `defaults.py` 里的默认值一致**。

**反向检验已做**：把 σ 临时改成 5e-4，C7 立刻报

```
FAIL  C7 fingerprint baseline  baseline says '...mismatch=1e-04...' but defaults.py has mismatch=5e-04
```

实现上**不 import adi_model**（该检查跑在系统 python 3.9，仓库要求 3.13），
改用正则从源码读默认值。这样 C7 在任何解释器下都能跑。

### 4.4 为什么这条门禁重要

指纹漂移**不会自己报错**：除非有人主动跑 `--check`，基线会一直显示为最新。
本会话的 12 个字段漂移就是这样积累了两周没人发现的。
现在 C7 让"基线描述必须跟上代码"进入常规门禁。

---

## 5. execute_pipeline 拆分进展（2026-11-13）

### 5.1 已做：抽出校验+派生段

`_prepare_conversion()`（103 行）从主函数抽出，涵盖 DEM/ID/采样记录校验与
dither 拓扑派生。抽取前逐行核对确认该段**无副作用**。

* `execute_pipeline`：683 → **662 行**
* 验证：行为指纹 `all *_sha256 fields equal` ⇒ **逐位不变**
* 测试：1483 passed（与抽取前完全一致）

### 5.2 未做：转换循环（尝试后回退）

原计划继续抽出 630–790 行的逐事件转换循环，**两次尝试均失败并回退**：

| 尝试 | 结果 |
|:---|:---|
| 按 `dither_active = ...` 文字锚点定位 | 该文本在文件里出现**两次**（`_validate_request` 内也有），`str.index` 取到第一个，切错位置 |
| 按行号 630–790 定位 | 行号本身正确，但拼接时把 `def` 行重复，导致 `'return' outside function` |

**根因**：`execute_pipeline` 实际延伸到**文件末尾**（第 1000 行），
`adc2_code` 之后还有约 207 行属于它。我误把 `_prepare_conversion` 里
705 行的 `return result` 当成了它的结尾。

### 5.3 结论：这类重构需要机械化定位而非手工数行

两次失败都不是"想法错了"，而是**定位手段不可靠**：

* 用文本锚点 → 重复文本导致歧义；
* 用行号 → 行号会随每次编辑漂移，且拼接边界容易错。

**正确做法**（下一轮应当采用）：
1. 用 `ast` 解析出 `execute_pipeline` 的**确切 `lineno` / `end_lineno`**，
   而不是手工数行；
2. 在 AST 层面切分：把目标 `For` 节点的语句列表整体搬到新函数；
3. 每次搬迁后立即跑指纹比对（本轮已验证这条安全网有效）。

本轮到此为止：**保留已成功的 21 行抽取，不为追求进度而强行完成第二刀。**
主函数 662 行仍偏长，但状态是**已验证、逐位不变**的。

---

## 6. 用 AST 替代手工定位（2026-11-13 新增工具）

### 6.1 上一轮为什么失败

拆分 `execute_pipeline` 时，我用 `str.index(文本)` 与手工数行，**连续失败两次**：

| 手段 | 失败方式 |
|:---|:---|
| 文本锚点 `dither_active = ...` | 该文本在文件里出现**两次**，`str.index` 取到第一个 |
| 手工行号 630–790 | 行号本身对，但函数实际延伸到 998 行，拼接后 `'return' outside function` |

用 AST 一查就清楚了：

```
execute_pipeline: lines 337..998  (662 lines)
顶层 For 有 3 个：(566, 569) (574, 583) (640, 790)
```

`next(n for n in ep.body if isinstance(n, ast.For))` 会取到 **566 行那个 4 行的循环**。

### 6.2 新增 `tools/inspect_function.py`

用 AST 给出：
* 函数的确切 `lineno`/`end_lineno`；
* 顶层大块（> `--min-lines`）及其**自由变量数**；
* 判定：自由变量 ≤ 12 → `extractable`；更多 → `TOO COUPLED`。

实测 `execute_pipeline`：

| 块 | 行数 | 自由变量 | 判定 |
|:---|:---:|:---:|:---|
| Expr | 39 | 0 | extractable |
| Assign（`_prepare_conversion` 调用）| 27 | 11 | extractable |
| If | 34 | 9 | extractable |
| **For（转换循环）** | **151** | **42** | **TOO COUPLED** |
| If | 28 | 4 | extractable |
| **Assign（结果装配）** | **85** | **53** | **TOO COUPLED** |

**结论：那个 151 行的 For 不是好的抽取单位。** 抽出它要么传 42 个参数
（可读性反而更差），要么重新设计数据流——那是另一项工作。

判断"该不该拆"不该凭感觉，这个工具给出可复算的依据。

### 6.3 本轮真正做的：消除重复

AST 扫描发现 `dither_program is not None` 出现 5 次、`dither_port is not None` 4 次，
其中 `dither_active` 的完整表达式在**两个函数里各写一遍**。

新增 `_dither_topology()` 统一判定（含互斥校验），两处调用改为
`_dither_topology(...) != "none"`。重复归零，指纹逐位不变。

**这比"把 151 行搬到另一个函数"更有实际价值**：后者只是移动代码，
前者消除了一处随时会漂移的双写。

---

## 7. 两轮拆分后的实际收益（2026-11-13）

### 7.1 已完成的拆分

| 函数 | 拆分前 | 拆分后 | 新函数 |
|:---|:---:|:---:|:---|
| `inventory_gate._check_sources` | 104 行 | **37 行** | `_verify_source_entries` (101) |
| `dem3d.usage_uniformity` | 80 行 | **37 行** | `_audit_one_code` (76) |

主函数体合计削减 **110 行**。两轮都以"指纹逐位不变"验证。

### 7.2 "超 60 行函数数"从 45 变 46 —— 这个指标在变差

因为每次拆分都会**新增一个函数**：主函数降到 60 以下，新函数可能仍在 60 以上。
所以"超 60 行的函数个数"不是衡量重构收益的好指标。

**更合适的指标**：
* 主函数体行数（上表），
* 最长函数的行数（仍是 `execute_pipeline` 662 行），
* 以及"最大的可抽取块是否已抽完"。

### 7.3 剩余候选与判定

用 `inspect_function.py` 扫描后，剩下的可抽取块：

| 块 | 行数 | 自由变量 | 判定 |
|:---|:---:|:---:|:---|
| `_validate_request` 的 If | 54 | 7 | extractable |
| `run_pipeline` 的 Expr | 39 | 0 | extractable |
| `execute_pipeline` 的 Expr | 39 | 0 | extractable |
| **`execute_pipeline` 转换循环** | **151** | **42** | **TOO COUPLED** |
| **`execute_pipeline` 结果装配** | **85** | **53** | **TOO COUPLED** |

**`execute_pipeline` 那 662 行的主体（两个 TOO COUPLED 块，共 236 行）
拆不动**——它们的耦合来自数据流本身，不是代码组织问题。
要继续降低这 662 行，需要**重新设计数据流**（例如把逐笔循环改成显式的
phase 对象），那是独立的架构工作，不能靠机械抽取。

### 7.4 教训：静态分析负责筛选，测试负责验证

两轮里 AST 的自由变量分析都**漏掉了实际问题**：

| 轮 | 漏掉的东西 | 后果 |
|:---|:---|:---|
| 48 | `reference_assets`（只在被调用函数体内出现）| 89 个测试失败 |
| 49 | 把 `for code in codes:` 与 `report["codes"]` 一起搬走 | 1 个测试失败 |

它们不是"未定义引用"（那种静态分析能抓），而是**结构上不该出现在新位置**的代码。
⇒ 抽取后必须立刻跑全量测试。这条已写进工具的 docstring。

---

## 8. 63+8 遗留清理（2026-11-13）

用户明确要求删除 63+8 替代拓扑。本轮扫描源码，找到并处理了以下残留。

### 8.1 已修：对外规格 profile 写着旧拓扑

`contracts/profiles.py` 的 `ADI_40M`（从包顶层导出，是对外规格声明）原写：

```python
n_main=63, n_sub=8, c_unit_f=40.04e-15, adc2_bits=12
```

已更正为当前实现：

```python
n_main=17, n_sub=5, c_unit_f=7.7799e-14, adc2_bits=14
```

并新增 `test_adi_profile_matches_default_parameters`：
**profile 声明的拓扑必须与 `DefaultParameters` 逐项一致**。
本会话已多次遇到两处定义漂移（σ、ADC2 位数、ra_clip、S4 权重），
这条守卫把规格声明钉到实现上。

### 8.2 未修（记录在案）：dither 合同的几何约束

两个文件里有 RTL 观测/参数导出的 dither 合同，硬编码要求几何 `(63, 8, 0, 511)`：

* `src/adi_model/rtl_observation.py:159`
* `tools/export_rtl_params.py:215`

在 17/5 下这条合同不再成立。**本轮试改过，但导致
`test_fig19_component_entry` 失败**（错误信息仍是 "63+8"，说明触发的是
另一个子句，需要实际运行才能定位），已逐字节回退。

**为什么不硬改**：这条路径属于 RTL 向量导出/对拍，而本机没有 VCS 环境，
无法端到端验证。在验证手段缺失时改合同，等于给自己制造"看起来修好了"的假象。

**要修它需要**：先有一个能跑的 RTL 对拍环境（或至少一个 golden 向量集），
才能确认改完的合同确实描述的是当前硬件。

### 8.3 已确认属于合理保留的 63

| 位置 | 原因 |
|:---|:---|
| `eight_slice.py` 的 `[0, 63]` | 6 位 dither word 的取值范围，与几何无关 |
| `defaults.py` 的注释 | 说明"旧值 40.04 fF 对应 63+8"，是历史说明 |
| `contracts/specs.py` 的 `n_main: int = 63` | 该 dataclass 的默认值；`ADI_40M`/`ENG_10M` 都显式传参覆盖，不生效 |

---

## 9. 移除过时的测试排除清单（2026-11-13）

### 9.1 发现

从本会话开始，我一直用这条命令跑测试：

```bash
pytest tests/unit tests/verification \
  --ignore=tests/unit/test_vivado_buffered_impl.py \
  --ignore=tests/unit/test_clockless_inspection.py
```

排除理由是"早期 `/usr/bin/tclsh` 报 couldn't create error file for command: not owner"。

**本轮实测：这两个文件全部通过（138 passed），全量 1623 passed / 0 failed。**
PATH 里的 `/usr/bin/tclsh` 是真实 tclsh（不是当时临时做的 shim），
说明当初的环境问题已不存在。

### 9.2 后果

排除清单让 **138 个测试长期不被检查**，而报告里说"1485 passed"时，
实际可运行的是 1623 个。这是一个**统计口径问题**：
数字看起来在增长，但被排除的部分没人知道是否还健康。

### 9.3 处理

`tools/verify_clean_clone.sh` 移除排除参数，改为跑全部测试。
干净克隆验证通过（含全部 1623 个）。

### 9.4 教训

**排除清单会过期。** 排除一个测试通常是因为"当前环境跑不了"，
但环境会变（工具装好了、路径修了），而排除参数不会被自动重检。

做法：排除参数旁必须写清**排除理由与复查条件**；
每次环境变化后应重新试跑被排除的测试。
本轮之所以发现，是因为主动去跑了那两个文件——
它们不在任何常规检查里。

---

## 10. 测试范围两次不完整的教训（2026-11-13）

### 10.1 第一次：排除清单过期（§9）

排除 2 个文件（138 个测试），理由是早期 tclsh 环境问题。环境修好后清单没更新。

### 10.2 第二次：只跑部分目录

本会话全程只跑 `tests/unit` + `tests/verification`，而仓库里还有：

| 目录 | 文件数 | 本会话此前 |
|:---|:---:|:---|
| `tests/audit/` | 2 | **从未运行** |
| `tests/regression/` | 5 | **从未运行** |
| `tests/integration/` | 10 | **从未运行** |

首次运行：**9 failed + 47 errors**。

### 10.3 已修 8 项

全部是本会话改动导致或暴露的过时断言：

| 文件 | 问题 |
|:---|:---|
| `test_audit_findings.py` | `reconstruct()` 未传 `physical_gain`，与 simulate 路径不同口径 |
| `test_review_contracts.py` | 旋转测试对整个 17 列 `np.roll`，把 B4/S4 卷进选中集 → 权重不守恒 → RA 限幅 |
| `test_engineering_defaults.py` ×6 | adc2 位数 12→14、名义增益 32→31.028、电容 20.5→19.878 pF、阈值末位 3.0→3.0117 等 |

### 10.4 剩余 6 项（已定位，未修）

* `test_fig19_static_ports.py`（5）：8/64 元素不匹配
* `test_mutation_twoneck.py`（1）
* `test_source_bound_replay.py`（47 errors）：需 VERILATOR，本机无

### 10.5 验证脚本已全覆盖

`verify_clean_clone.sh` 现在跑四个目录 + integration（仅排除需 VERILATOR 的那个文件）。
**当前 integration 那组会如实报 FAIL**——剩余 6 个问题尚未解决，脚本不掩盖。

### 10.6 教训

两次不完整都不是"故意隐瞒"，而是**默认范围从来没人检查过**：

* 第一次是排除参数写着，但没人定期重试；
* 第二次是我沿用了前几轮的命令，而没有去问"仓库里还有哪些测试目录"。

**修复方式**：把"跑全部测试"写进脚本，而不是依赖记忆里的命令。
