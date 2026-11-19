---
name: Bug report
about: 报告可复现的数字 IP、模型、工具或文档错误
title: "[BUG] "
labels: bug
assignees: ''
---

## 发生了什么

<!-- 写出入口或文件、输入、观察值与期望值。涉及论文或专利时请附图号、页码或实施例。 -->

## 怎样复现

从仓库根执行 `git rev-parse HEAD`，贴出提交号和触发问题的最小命令。以下是生产 Python 的单笔基准，可替换为出错的输入。

```bash
run_dir="$(mktemp -d)"
bash tools/pyenv.sh --run -m adi_model simulate --dc -0.6522216796875 --dither-word 53 --samples 1 --output "$run_dir/sim"
bash tools/pyenv.sh --run - "$run_dir/sim/codes.npz" <<'PYTHON'
import sys
import numpy as np
with np.load(sys.argv[1]) as data:
    print(*(int(data[key][0]) for key in ("coarse", "k", "adc2_code", "code")))
PYTHON
```

基准的四个数依次为 `202 206 6016 410304`。报告其他场景时，请给出实际字源或强制字、物理片号、样本标签与异常字段。RTL 问题请附测试台、时钟和发生异常的时钟沿。文档问题直接给出页面及冲突段落。[运行入口与字段](../../docs/guide/QUICKSTART.md)

## 运行环境

<!-- 操作系统、Python 或仿真器版本、安装方式。若复用现有日志，请注明生成它的提交。 -->
