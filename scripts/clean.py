#!/usr/bin/env python3
"""清理仓库内的仿真/构建临时产物（默认 dry-run，只列不删）。

用法::

    python3 scripts/clean.py            # 扫描并列出将被删除的路径
    python3 scripts/clean.py --apply    # 真正删除

设计原则：**白名单匹配**。目录名/文件名必须命中脚本内写死的模式才会进入候选，
受保护路径（.git、docs/、src/、rtl/、tb/、tests/、synth/results/）在遍历阶段
直接剪枝，任何情况下都不会被删除。全程不使用 ``rm -rf``，只用
``shutil.rmtree`` / ``os.unlink`` 作用在已匹配的单个路径上。
"""

from __future__ import annotations

import argparse
import fnmatch
import os
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# --- 白名单：只有命中这些模式的路径才是候选 -------------------------------
# 目录（整棵子树一并删除，不再深入遍历）
DIR_NAMES = frozenset({"__pycache__", "csrc", "work"})
DIR_GLOBS = ("*.daidir", "simv*")
# 文件
FILE_GLOBS = ("*.pyc", "*.pyo", "*.vvp", "simv*", "*.log", "*.rpt")

# --- 保护名单：这些路径永不删除，遍历时直接跳过 ---------------------------
PROTECTED_DIRS = ("docs", "src", "rtl", "tb", "tests", "synth/results")
NEVER_DESCEND = frozenset({".git"})


def _is_protected(rel: str) -> bool:
    """rel 为相对仓库根的正斜杠路径。"""
    for p in PROTECTED_DIRS:
        if rel == p or rel.startswith(p + "/"):
            return True
    return False


def _human(size: int) -> str:
    value = float(size)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if value < 1024 or unit == "GiB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GiB"


def _tree_stats(path: Path) -> tuple[int, int]:
    """返回 (总字节数, 文件数)；不跟随符号链接，统计失败时忽略。"""
    total = files = 0
    stack = [path]
    while stack:
        cur = stack.pop()
        try:
            with os.scandir(cur) as it:
                for entry in it:
                    try:
                        if entry.is_symlink():
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(Path(entry.path))
                        elif entry.is_file(follow_symlinks=False):
                            total += entry.stat(follow_symlinks=False).st_size
                            files += 1
                    except OSError:
                        continue
        except OSError:
            continue
    return total, files


def _match_dir(name: str) -> bool:
    return name in DIR_NAMES or any(fnmatch.fnmatchcase(name, g) for g in DIR_GLOBS)


def _match_file(name: str) -> bool:
    return any(fnmatch.fnmatchcase(name, g) for g in FILE_GLOBS)


def scan(root: Path) -> tuple[list[tuple[Path, str, int, int]], list[str]]:
    """返回 (候选列表, 被保护跳过的相对路径列表)。

    候选元组为 (绝对路径, "dir"/"file", 字节数, 文件数)。
    """
    candidates: list[tuple[Path, str, int, int]] = []
    protected_seen: list[str] = []

    def walk(cur: Path, rel_prefix: str) -> None:
        try:
            entries = sorted(os.scandir(cur), key=lambda e: e.name)
        except OSError as exc:
            print(f"[warn] 无法读取目录 {cur}: {exc}", file=sys.stderr)
            return
        for entry in entries:
            rel = f"{rel_prefix}{entry.name}"
            try:
                if entry.is_symlink():
                    # 符号链接只按文件处理：删除链接本身，绝不进入其目标
                    if _match_file(entry.name):
                        candidates.append((Path(entry.path), "file", 0, 0))
                    continue
                if entry.is_dir(follow_symlinks=False):
                    if _match_dir(entry.name):
                        size, files = _tree_stats(Path(entry.path))
                        candidates.append((Path(entry.path), "dir", size, files))
                    elif entry.name in NEVER_DESCEND:
                        continue
                    elif _is_protected(rel):
                        protected_seen.append(rel)
                    else:
                        walk(Path(entry.path), rel + "/")
                elif entry.is_file(follow_symlinks=False):
                    if _match_file(entry.name) and not _is_protected(rel):
                        size = entry.stat(follow_symlinks=False).st_size
                        candidates.append((Path(entry.path), "file", size, 1))
            except OSError as exc:
                print(f"[warn] 跳过 {entry.path}: {exc}", file=sys.stderr)

    walk(root, "")
    candidates.sort(key=lambda item: str(item[0]))
    return candidates, protected_seen


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="清理仓库临时产物（默认 dry-run，只列不删）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "保护路径：.git/ docs/ src/ rtl/ tb/ tests/ synth/results/\n"
            "删除范围：__pycache__/ *.pyc *.pyo *.vvp simv* *.daidir work/ csrc/ *.log *.rpt"
        ),
    )
    parser.add_argument("--apply", action="store_true", help="真正删除（默认只列出）")
    parser.add_argument(
        "--root",
        type=Path,
        default=REPO_ROOT,
        help=f"要清理的仓库根（默认 {REPO_ROOT}）",
    )
    args = parser.parse_args(argv)

    root = args.root.expanduser().resolve()
    if not root.is_dir():
        print(f"[error] 不是目录: {root}", file=sys.stderr)
        return 2
    if root == Path(root.anchor) or root == Path.home().resolve():
        print(f"[error] 拒绝在根目录或家目录上运行: {root}", file=sys.stderr)
        return 2
    if not (root / ".git").exists():
        print(f"[error] {root} 不像 git 仓库（缺少 .git/），为安全起见中止", file=sys.stderr)
        return 2

    candidates, protected_seen = scan(root)
    total_bytes = sum(c[2] for c in candidates)
    total_files = sum(c[3] for c in candidates)

    print(f"仓库根: {root}")
    print(f"模式  : {'apply（删除中）' if args.apply else 'dry-run（只列出，加 --apply 才删除）'}")
    print(f"保护  : .git/, {', '.join(d + '/' for d in PROTECTED_DIRS)}")
    print()

    if not candidates:
        print("没有需要清理的临时产物。")
    else:
        print("待清理路径：")
        for path, kind, size, files in candidates:
            rel = path.relative_to(root).as_posix()
            extra = f" ({files} 个文件)" if kind == "dir" else ""
            print(f"  [{kind:4}] {_human(size):>9}  {rel}{extra}")
        print()
        print(f"合计: {len(candidates)} 项, {total_files} 个文件, {_human(total_bytes)}")

    if protected_seen:
        print()
        print("受保护目录（已跳过）: " + ", ".join(f"{p}/" for p in sorted(protected_seen)))

    if not candidates:
        return 0

    if not args.apply:
        print()
        print("dry-run：未删除任何文件。核对无误后运行：python3 scripts/clean.py --apply")
        return 0

    print()
    deleted = freed = 0
    errors: list[tuple[str, str]] = []
    for path, kind, size, files in candidates:
        try:
            if kind == "dir":
                shutil.rmtree(path)
                freed += size
                deleted += 1
            else:
                freed += path.stat().st_size
                path.unlink()
                deleted += 1
        except OSError as exc:
            errors.append((path.relative_to(root).as_posix(), str(exc)))

    print(f"已删除 {deleted} 项, 释放 {_human(freed)}")
    if errors:
        print(f"删除失败 {len(errors)} 项：", file=sys.stderr)
        for rel, msg in errors:
            print(f"  {rel}: {msg}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
