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

import argparse
import os
import re
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
    paths = {Path(os.fsdecode(name)) for name in result.split(b"\0") if name}
    return {path for path in paths if (ROOT / path).is_file() or (ROOT / path).is_symlink()}


# =============================================================================
# 2. 按目录身份清点
# =============================================================================
def _filelist(name: str) -> list[str]:
    return [line.strip() for line in (ROOT / name).read_text().splitlines()
            if line.strip() and not line.lstrip().startswith("//")]


def check_layout(paths: set[Path], directories: set[str], files: set[str]) -> bool:
    """只固定公开入口的名称。历史文档、测试台、叶子模块的数量仍从工作树取得。"""
    expected_dirs = set(".github archive docs experiments flow references rtl src tb tools".split())
    expected_files = {"README.md", "Makefile", "pyproject.toml", ".gitignore", ".gitattributes"}
    pages = {p.name for p in paths if p.parent == Path("docs") and p.suffix == ".md"}
    checks = {
        "root directories": (directories, expected_dirs),
        "root files": (files, expected_files),
        "docs entry pages": (pages, {"CONTRACT_CURRENT.md", "STATUS.md", "INDEX.md"}),
    }
    inventory = _filelist("rtl/rtl_sources.f")
    rtl_files = {p.as_posix() for p in paths if p.parts[0] == "rtl" and p.suffix in (".sv", ".v")}
    checks["RTL inventory"] = (set(inventory), rtl_files)
    production = "rtl/filelists/files_adc_v21.f"
    inputs = _filelist(production)
    simulation = _filelist("rtl/filelists/files_adc_v21_sim.f")
    # 完整 RTL 清点包含组件和仿真宏。生产源只能来自该清点；生产集的可达性由图册检查器核对。
    # 模拟清单继承生产列表一次，附加文件存在且不得手抄一遍已经继承的生产源。
    checks["unknown production sources"] = (set(inputs) - rtl_files, set())
    include = "-f " + production
    extra = [name for name in simulation if name != include]
    checks["missing simulation sources"] = ({n for n in extra if Path(n) not in paths}, set())
    checks["repeated production sources in simulation"] = (set(extra).intersection(inputs), set())
    ok = True
    for key, (actual, expected) in checks.items():
        if actual != expected:
            print(f"FAIL {key}: extra={sorted(actual - expected)}, missing={sorted(expected - actual)}")
            ok = False
    if (not inputs or simulation.count(include) != 1
            or any(len(rows) != len(set(rows)) for rows in (inventory, inputs, simulation))):
        print("FAIL empty production filelist, duplicate file or invalid simulation include")
        ok = False
    # 只按路径去重会漏掉在另一个文件定义同名模块的情况。去除 SV 注释后
    # 清点本仓使用的 module 声明；层次可达性仍由实际顶层和生产清单核对。
    declarations = {}
    for name in sorted(rtl_files):
        body = re.sub(r"/\*.*?\*/|//[^\n]*", " ", (ROOT / name).read_text(), flags=re.DOTALL)
        for module in re.findall(r"(?m)^\s*module\s+(?:(?:static|automatic)\s+)?(\w+)", body):
            if module in declarations:
                print(f"FAIL duplicate RTL module {module}: {declarations[module]} and {name}")
                ok = False
            declarations[module] = name
    if ok:
        print(f"PASS layout and source inventories: {len(inputs)} production / {len(rtl_files)} RTL files / {len(declarations)} module names")
    return ok


def main() -> int:
    """打印目录计数，供整理时复核。文档是否现行仍以入口页中的范围为准。"""
    parser = argparse.ArgumentParser(description="清点工作树与文档入口")
    parser.add_argument("--check", action="store_true", help="核对已约定的目录、文档入口和 RTL 组装清单")
    args = parser.parse_args()
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
    doc_dirs = {p.parts[1] for p in paths if len(p.parts) > 2 and p.parts[0] == "docs"}
    print(f"docs 一级目录：{len(doc_dirs)}")
    print(f"仓库根目录 Markdown：{sum(len(path.parts) == 1 for path in markdown)}")
    print(f"docs 根目录 Markdown：{sum(len(path.parts) == 2 for path in docs)}")
    print(f"docs 历史目录 Markdown：{len(historical)}")
    print(f"docs 其余 Markdown：{len(docs - historical)}")
    print(f"README 系列 Markdown：{len(readmes)}")
    return int(args.check and not check_layout(paths, root_dirs, root_files))


if __name__ == "__main__":
    raise SystemExit(main())
