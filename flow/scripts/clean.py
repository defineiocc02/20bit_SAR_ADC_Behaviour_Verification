#!/usr/bin/env python3
# =============================================================================
# 文件名     : clean.py
# 功能       : 预览或清理当前检出内未跟踪的仿真产物，保留源码、存证与嵌套仓库。
# 设计说明   : Git 已跟踪的路径及其父目录不能作为整树删除目标。支持普通检出和
#              .git 为文件的 linked worktree。默认只预览，--apply 才写入。
# =============================================================================
"""从任意工作目录执行 python3 /path/to/flow/scripts/clean.py；--root 可指定另一个检出。"""

from __future__ import annotations

import argparse
import fnmatch
import os
import shutil
import subprocess
import sys
from pathlib import Path

# 清理器兼作远端 stdin 程序。普通脚本按自身在 flow/scripts 的位置定位。
# 远端包装总是传 --root，登录 shell 所在目录不能决定扫描哪一份仓库。
REPO_ROOT = Path.cwd() if __file__ == "<stdin>" else Path(__file__).resolve().parent.parent.parent

# --- 白名单：只有命中这些模式的路径才是候选 -------------------------------
# 目录（整棵子树一并删除，不再深入遍历）
DIR_NAMES = frozenset({"__pycache__", "csrc", "work"})
DIR_GLOBS = ("*.daidir", "simv*")
# 文件
FILE_GLOBS = ("*.pyc", "*.pyo", "*.vvp", "simv*", "*.log", "*.rpt")

# --- 保护名单：这些路径永不删除，遍历时直接跳过 ---------------------------
PROTECTED_DIRS = ("docs", "src", "rtl", "tb", "flow/tests", "flow/synth/results")
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


def _tracked_paths(root: Path) -> set[Path]:
    """返回索引中的路径及其祖先。目录名 work 或 simv* 也可能包含已跟踪文件。"""
    # Git 对带空格和换行的文件名会在文本输出中转义；NUL 输出与 os.fsdecode 保留实际路径。
    # 这里使用索引而非后缀判断。一份被有意跟踪的报告和临时报告可以同名。
    data = subprocess.check_output(["git", "-C", str(root), "ls-files", "--cached", "-z"])
    protected = set()
    for item in data.split(b"\0"):
        if item:
            path = Path(os.fsdecode(item))
            protected.add(path)
            protected.update(path.parents)
    return protected


def _tree_stats(path: Path) -> tuple[int, int] | None:
    """预览整树的大小和普通文件数。嵌套检出和读不全的目录一律不整树清理。"""
    total = files = 0
    # 不跟随符号链接计数，以免把远端挂载或另一个实验的内容算成本项目的清理量。
    # 一旦发现嵌套 Git 标记便放弃整个候选，人为创建的仓库可能就在名为 work 的目录中。
    stack = [path]
    while stack:
        try:
            with os.scandir(stack.pop()) as entries:
                for entry in entries:
                    if entry.name == ".git":
                        return None
                    if entry.is_dir(follow_symlinks=False):
                        stack.append(Path(entry.path))
                    elif entry.is_file(follow_symlinks=False):
                        total += entry.stat(follow_symlinks=False).st_size
                        files += 1
        except OSError:
            return None
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
    tracked = _tracked_paths(root)

    # 保护目录对子孙生效，判断须先于临时名称匹配。例如 flow/tests 内部也会出现 *.rpt。
    # 普通源码目录中的已跟踪文件并不妨碍清理旁边的独立、未跟踪日志。
    def walk(cur: Path, rel_prefix: str) -> None:
        try:
            entries = sorted(os.scandir(cur), key=lambda e: e.name)
        except OSError as exc:
            print(f"[warn] 无法读取目录 {cur}: {exc}", file=sys.stderr)
            return
        for entry in entries:
            rel = f"{rel_prefix}{entry.name}"
            try:
                if entry.name in NEVER_DESCEND or _is_protected(rel):
                    protected_seen.append(rel)
                    continue
                path = Path(entry.path)
                if entry.is_dir(follow_symlinks=False):
                    # worktree 的 .git 可能是普通文件，不能只用 is_dir 检测。
                    if (path / ".git").exists() or (path / ".git").is_symlink():
                        protected_seen.append(rel)
                    elif _match_dir(entry.name):
                        # 对递归删除目标要查“是否包含跟踪文件”。只检查目录本身是否被 Git 跟踪是不够的。
                        # 保护命中时整树保留，不单独捡走使旧实验可复查的配套文件。
                        stats = None if Path(rel) in tracked else _tree_stats(path)
                        if stats is None:
                            protected_seen.append(rel)
                        else:
                            candidates.append((path, "dir", *stats))
                    else:
                        walk(path, rel + "/")
                elif _match_file(entry.name) and Path(rel) not in tracked:
                    # 链接只处理链接本身；悬空链接也能预览或 unlink。
                    candidates.append((path, "file", entry.stat(follow_symlinks=False).st_size, 1))
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
            "保护 Git 跟踪路径、嵌套仓库和源码/测试目录\n"
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

    # 规范化根路径后才比较 .git 与实际工作树，可以从仓库外启动同一脚本。
    # 默认值与显式 --root . 在仓库根应得到相同的保护区域。
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

    try:
        # 支持 .git 文件形式的 linked worktree。根校验必须来自 Git 自己，否则
        # 指向上级仓库的空标记或嵌套路径可能用错索引和相对保护路径。
        actual = subprocess.check_output(["git", "-C", str(root), "rev-parse", "--show-toplevel"])
        if Path(os.fsdecode(actual.rstrip(b"\n"))).resolve() != root:
            print(f"[error] 需要该检出的 Git 根: {root}", file=sys.stderr)
            return 2
        candidates, protected_seen = scan(root)
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"[error] 无法读取 Git 索引: {exc}", file=sys.stderr)
        return 2
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
        print("dry-run：未删除任何文件。请在相同命令中增加 --apply")
        return 0

    print()
    # 执行阶段仅处理前面列出的候选；清理失败保留具体路径和非零退出状态。
    # 字节计数读取链接本身的 stat，悬空链接的目标无需存在。
    deleted = freed = 0
    errors: list[tuple[str, str]] = []
    for path, kind, size, files in candidates:
        try:
            # 预览和执行之间可能发生 git add；每个候选在删除前重新查询其索引范围。
            rel = path.relative_to(root).as_posix()
            # 使用 literal pathspec，带方括号或通配字符的文件名保持字面含义。
            # Git 不可用时进入错误分支，不降级成无索引的整树删除。
            indexed = subprocess.check_output(["git", "-C", str(root), "--literal-pathspecs", "ls-files", "-z", "--", rel])
            if indexed or (kind == "dir" and _tree_stats(path) is None):
                print(f"[skip] 受保护: {rel}")
                continue
            if kind == "dir":
                shutil.rmtree(path)
                freed += size
                deleted += 1
            else:
                freed += path.lstat().st_size
                path.unlink()
                deleted += 1
        except (OSError, subprocess.CalledProcessError) as exc:
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
