---
name: Bug report
about: 报告一个可复现的错误（数值、口径、门禁或文档）
title: "[BUG] "
labels: bug
assignees: ''
---

## 1. 现象（What happened）

<!-- 一句话说清：哪个函数/哪个 stage/哪个命令，给出了什么结果。 -->

## 2. 期望（What you expected）

<!-- 期望值是多少？依据是什么（论文原文 / 解析式 / 另一条独立实现）？ -->

## 3. 最小复现（Minimal reproduction）

```python
from adi_model import Config, run_sim_split, sine_input

cfg = Config(...)          # 请给出完整配置，或说明用了哪个预设
...
```

或命令行：

```bash
adi-run-all --results-dir /tmp/repro
```

## 4. 参数来源分级（Provenance）

本仓库的核心机制是**每个参数都带来源分级**。请指出相关参数属于哪一类，
否则无法判断这是 bug 还是"模型本来就没承诺过"：

- [ ] [披露] disclosed —— 论文/PPT 原文给出
- [ ] [拟合] fitted —— 为复现基准标定
- [ ] [假设] assumed —— 工艺估算，明确不得用于良率结论
- [ ] [派生] derived —— 由上述量算出
- [ ] [研究扩展] research_extension —— 本仓库原创，非 ADI 披露特性

## 5. 环境

- OS：
- Python 版本：
- numpy 版本：
- 安装方式（`pip install -e .` / 源码直接 `PYTHONPATH=src`）：
- 提交号（git rev-parse HEAD）：

## 6. 门禁状态（若相关）

```bash
ruff check . && ruff format --check . && mypy && pytest -q
```

## 7. 补充材料

<!-- 图、频谱、日志片段、results.json 差异等。 -->
