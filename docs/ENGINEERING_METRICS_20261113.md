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
