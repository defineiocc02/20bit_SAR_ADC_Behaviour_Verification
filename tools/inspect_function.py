#!/usr/bin/env python3
"""用 AST 检查一个函数的边界与可抽取性 —— 替代手工数行。

## 为什么需要这个工具

2026-11-13 拆分 `execute_pipeline` 时，用 `str.index(文本)` 和手工数行
**连续失败两次**：
* 文本锚点 `dither_active = ...` 在文件里出现两次，`str.index` 取到第一个；
* 手工行号切错了函数结尾，拼接后出现 `'return' outside function`。

根因是"手工定位"不可靠。本工具用 `ast` 给出**唯一确定的** `lineno`/`end_lineno`，
并计算候选节点的自由变量数——**自由变量太多就说明它不是一个好的抽取单位**。

## 用法

```bash
PYTHONPATH=src python3 tools/inspect_function.py \
    --file src/adi_model/simulation/pipeline_engine.py --name execute_pipeline

PYTHONPATH=src python3 tools/inspect_function.py \
    --file src/adi_model/simulation/pipeline_engine.py --name execute_pipeline --min-lines 25
```

输出：函数范围、顶层大块（>min-lines）、以及每个候选块的自由变量数。
"""

from __future__ import annotations

import argparse
import ast
import builtins
import pathlib
import sys


def _free_variables(nodes: list[ast.stmt]) -> list[str]:
    """计算一段语句的自由变量：被读、但未在段内绑定的名字。"""
    bound: set[str] = set()
    loaded: set[str] = set()
    for node in nodes:
        for child in ast.walk(node):
            if isinstance(child, ast.Name):
                (bound if isinstance(child.ctx, ast.Store) else loaded).add(child.id)
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                args = child.args
                for arg in (*args.posonlyargs, *args.args, *args.kwonlyargs):
                    bound.add(arg.arg)
                if args.vararg:
                    bound.add(args.vararg.arg)
                if args.kwarg:
                    bound.add(args.kwarg.arg)
    return sorted(
        name
        for name in loaded - bound
        if not hasattr(builtins, name) and name != "__name__"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", required=True, type=pathlib.Path)
    parser.add_argument("--name", required=True)
    parser.add_argument("--min-lines", type=int, default=25,
                        help="只列出超过这个行数的顶层块（默认 25）")
    args = parser.parse_args()

    if not args.file.is_file():
        print(f"no such file: {args.file}", file=sys.stderr)
        return 2
    tree = ast.parse(args.file.read_text(encoding="utf-8"))
    target = next(
        (n for n in tree.body
         if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == args.name),
        None,
    )
    if target is None:
        print(f"function {args.name!r} not found at module level", file=sys.stderr)
        return 2
    end = target.end_lineno or target.lineno
    print(f"{args.name}: lines {target.lineno}..{end}  ({end - target.lineno + 1} lines)")
    print()
    print(f"top-level blocks over {args.min_lines} lines:")
    for index, node in enumerate(target.body):
        node_end = node.end_lineno or node.lineno
        length = node_end - node.lineno + 1
        if length <= args.min_lines:
            continue
        free = _free_variables([node])
        verdict = "extractable" if len(free) <= 12 else f"TOO COUPLED ({len(free)} free vars)"
        print(
            f"  [{index:2d}] {type(node).__name__:<10} {node.lineno}-{node_end}  "
            f"{length:3d} lines  -> {verdict}"
        )
        if len(free) <= 20:
            print(f"        free vars: {free}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
