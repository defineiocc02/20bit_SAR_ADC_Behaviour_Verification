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
