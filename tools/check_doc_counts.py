#!/usr/bin/env python3
# =============================================================================
# 文件名     : check_doc_counts.py
# 版本       : v1.0
# 功能       : 清点 Git 工作树的文档与 README。
# 设计说明   :
#   * 同时读取已跟踪和未忽略的新增文件，可在 git add 前检查迁移结果。
#   * Git 索引仍会列出待删除的旧路径，须检查工作树中实际存在的文件。
#   * 历史目录单列。索引页按入口导航，避免维护一份会漂移的手写计数。
# 变更记录   : 文档导航整理后改为直接清点。
# =============================================================================
from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE_PARTS = {"_history", "_superseded", "archive", "_legacy"}


# =============================================================================
# 1. 收集可见文件
# =============================================================================
def _working_tree_files() -> set[Path]:
    """返回实际存在的仓库相对路径，Git 失败时由调用者收到错误与退出码。"""
    # NUL 分隔保留空格、非 ASCII 路径和文件名中的换行，暂存和工作树重名去重。
    result = subprocess.check_output(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=ROOT
    )
    paths = {Path(name.decode()) for name in result.split(b"\0") if name}
    return {path for path in paths if (ROOT / path).is_file()}


# =============================================================================
# 2. 按目录身份清点
# =============================================================================
def main() -> None:
    """打印目录计数，供整理时复核。文档是否现行仍以入口页中的范围为准。"""
    paths = _working_tree_files()
    markdown = {path for path in paths if path.suffix.lower() == ".md"}
    docs = {path for path in markdown if path.parts[0] == "docs"}
    historical = {path for path in docs if ARCHIVE_PARTS.intersection(path.parent.parts)}
    readmes = {path for path in markdown if path.name.upper().startswith("README")}
    root_dirs = {path.parts[0] for path in paths if len(path.parts) > 1}
    root_files = {path.name for path in paths if len(path.parts) == 1}
    print(f"工作树可见文件（除 Git 忽略）：{len(paths)}")
    print(f"Git 可见根层目录：{len(root_dirs)}")
    print(f"Git 可见根层文件：{len(root_files)}")
    print(f"仓库根目录 Markdown：{sum(len(path.parts) == 1 for path in markdown)}")
    print(f"docs 根目录 Markdown：{sum(len(path.parts) == 2 for path in docs)}")
    print(f"docs 历史目录 Markdown：{len(historical)}")
    print(f"docs 其余 Markdown：{len(docs - historical)}")
    print(f"README 系列 Markdown：{len(readmes)}")


if __name__ == "__main__":
    main()
