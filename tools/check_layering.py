# =============================================================================
# 文件名     : check_layering.py
# 模块名     : tools.check_layering
# 版本       : v1.0
# 功能       : 检查分层与依赖方向，把「不许反向依赖」从口头约定变成可执行断言
#
# 设计说明   :
#   * 三条规则：
#       1. src/adi_model（库层）不得 import 任何 task* 模块；
#       2. <task>/model（模型层）不得 import <task>/experiments 或 <task>/tools；
#       3. 任何文件不得 from 另一模块 import 下划线开头的名字（跨文件私有依赖）。
#   * 规则 3 只报「跨文件」的：同文件内用私有名是正常封装，不是问题。
#   * R1／R2 违规使退出码非零；R3 仅列出私有跨文件依赖，当前不作为门禁。
#   * 不做自动修复：改名会牵动调用点，必须人工过一遍并跑回归。
# -----------------------------------------------------------------------------
# 变更记录
# -----------------------------------------------------------------------------
# 初始版本。
# =============================================================================

"""检查库层/模型层/工具层的依赖方向与跨文件私有导入。"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

SKIP = {".git", "__pycache__", ".pytest_cache", ".ruff_cache", "build", "dist"}


def module_name(path: Path, root: Path) -> str:
    return ".".join(path.relative_to(root).with_suffix("").parts)


def imports_of(path: Path) -> list[tuple[str, list[str]]]:
    """返回 [(被导入模块, 导入的名字列表)]，只取绝对导入。"""
    tree = ast.parse(path.read_text(errors="replace"))
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and not node.level and node.module:
            found.append((node.module, [alias.name for alias in node.names]))
        elif isinstance(node, ast.Import):
            found.extend((alias.name, []) for alias in node.names)
    return found


def check(root: Path) -> list[tuple[str, str, str]]:
    problems = []
    for path in sorted(root.rglob("*.py")):
        if SKIP & set(path.parts):
            continue
        rel = path.relative_to(root)
        parts = rel.parts
        where = str(rel)
        for target, names in imports_of(path):
            # 规则 1：库层不得依赖任何任务层
            if parts[0] == "src" and target.split(".")[0].startswith("task"):
                problems.append((where, "R1_库层反向依赖", target))
            # 规则 2：模型层不得依赖同任务的驱动层
            if len(parts) >= 3 and parts[1] == "model" and target.startswith(f"{parts[0]}."):
                leaf = target.split(".")[1]
                if leaf in {"experiments", "tools"}:
                    problems.append((where, "R2_模型层依赖驱动层", target))
            # 规则 3：跨文件私有导入
            owner = module_name(path, root).rsplit(".", 1)[0]
            for name in names:
                if name.startswith("_") and not name.startswith("__") and target != owner:
                    problems.append((where, "R3_跨文件私有导入", f"{target}.{name}"))
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--rule", action="append", help="只查指定规则，可重复")
    args = parser.parse_args()
    problems = check(args.root.resolve())
    if args.rule:
        wanted = set(args.rule)
        problems = [p for p in problems if p[1].split("_")[0] in wanted]
    for where, rule, target in problems:
        print(f"{rule:18s} {where:60s} {target}")
    summary: dict[str, int] = {}
    for _, rule, _ in problems:
        summary[rule] = summary.get(rule, 0) + 1
    print("—— 合计 " + (", ".join(f"{k} {v}" for k, v in sorted(summary.items())) or "无违规"))
    return 1 if summary.get("R1_库层反向依赖") or summary.get("R2_模型层依赖驱动层") else 0


if __name__ == "__main__":
    sys.exit(main())
