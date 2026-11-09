#!/usr/bin/env python3
# =============================================================================
# 文件名     : digital_status.py
# 模块名     : tools.digital_status
# 版本       : v1.0
# 功能       : 数字交付树的状态盘点——模块、TB、检查命令、工具与证据新旧。
#
# 设计说明   :
#   * 只读，不跑 RTL 回归。Verilator 不一定在本机，跑不了就如实报告「缺工具」，
#     而不是给一个假绿。真正跑回归的命令一并列出，交给有工具的环境执行。
#   * 「模块有没有 TB 覆盖」用引用计数判断，但用 POSIX 边界匹配而不是 \b：
#     BSD grep 的 \b 会漏配，这一点在本仓库已经踩过一次。
#   * 证据新旧用 mtime 而不是 git 时间：工作树里大量文件未提交，git 时间会
#     全部落到 HEAD 的提交时间上，看不出真实顺序。
# -----------------------------------------------------------------------------
# 变更记录
# -----------------------------------------------------------------------------
# 初始版本。
# =============================================================================

"""数字交付树的状态盘点。"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# 各 task 的 RTL 检查入口。命令只列出，不在这里执行——本机多半没有 Verilator。
CHECK_COMMANDS = {
    "task1": (
        "PYTHONPATH=.:src python task1/tools/run_rtl_checks.py --out <dir>",
        "PYTHONPATH=.:src python task1/tools/run_bridge_checks.py --out <dir>",
    ),
    "task2": ("PYTHONPATH=.:src python task2/tools/run_dither_word_checks.py --out <dir>",),
    "task3": ("PYTHONPATH=.:src python task3/tools/run_caaz_checks.py --out <dir>",),
    "task_integration": (
        "PYTHONPATH=.:src python task_integration/tools/run_checks.py --out <dir>",
    ),
    "task_reconstruction": (
        "PYTHONPATH=.:src python task_reconstruction/tools/run_checks.py --out <dir>",
    ),
}

# 判定「能不能跑 RTL」需要的外部工具。
EXTERNAL_TOOLS = ("verilator", "iverilog", "yosys")

MODULE_DECL = re.compile(r"^module\s+([A-Za-z0-9_]+)", re.MULTILINE)


def collect_modules() -> list[dict]:
    """收集所有 RTL 模块，并统计它在非 RTL 文件里被引用了几次。"""
    modules = []
    # 先把候选文本一次读进内存再匹配：逐个模块 rglob 全仓会把 11 个模块变成
    # 11 次全盘遍历，实测从 28 秒降到 1 秒级。
    corpus = []
    for candidate in ROOT.rglob("*"):
        if candidate.suffix not in (".sv", ".py", ".md", ".yaml"):
            continue
        posix = candidate.as_posix()
        if "/rtl/" in posix or "/.git/" in posix:
            continue
        try:
            corpus.append(candidate.read_text(encoding="utf-8", errors="ignore"))
        except OSError:
            continue
    for path in sorted(ROOT.glob("*/rtl/*.sv")) + sorted(ROOT.glob("*/*/rtl/*.sv")):
        text = path.read_text(encoding="utf-8", errors="ignore")
        match = MODULE_DECL.search(text)
        if not match:
            continue
        name = match.group(1)
        pattern = re.compile(rf"(^|[^A-Za-z0-9_]){re.escape(name)}([^A-Za-z0-9_]|$)")
        references = sum(1 for text in corpus if pattern.search(text))
        modules.append(
            {
                "module": name,
                "rtl": path.relative_to(ROOT).as_posix(),
                "lines": text.count("\n") + 1,
                "referenced_by": references,
            }
        )
    return modules


def collect_testbenches() -> list[dict]:
    """收集所有 TB 文件与其行数。"""
    benches = []
    for path in sorted(ROOT.glob("*/tb/*.sv")) + sorted(ROOT.glob("*/*/tb/*.sv")):
        text = path.read_text(encoding="utf-8", errors="ignore")
        benches.append(
            {
                "testbench": path.name,
                "path": path.relative_to(ROOT).as_posix(),
                "lines": text.count("\n") + 1,
            }
        )
    return benches


def latest_evidence(task: str) -> dict:
    """给出某个 task 的 evidence 目录里最新与最旧产物的时间。"""
    directory = ROOT / task / "evidence"
    if not directory.exists():
        return {"exists": False}
    stamps = [
        path.stat().st_mtime
        for path in directory.rglob("*")
        if path.is_file() and "__pycache__" not in path.as_posix()
    ]
    if not stamps:
        return {"exists": True, "files": 0}
    newest = max(stamps)
    oldest = min(stamps)
    return {
        "exists": True,
        "files": len(stamps),
        "oldest": datetime.fromtimestamp(oldest, UTC).isoformat(timespec="seconds"),
        "newest": datetime.fromtimestamp(newest, UTC).isoformat(timespec="seconds"),
    }


def tool_availability() -> dict:
    """检查外部 RTL 工具是否在 PATH 上。"""
    result = {}
    for name in EXTERNAL_TOOLS:
        found = shutil.which(name)
        version = ""
        if found:
            try:
                completed = subprocess.run(
                    [found, "--version"], capture_output=True, text=True, timeout=10, check=False
                )
                version = (completed.stdout or completed.stderr).splitlines()[0][:60]
            except (OSError, subprocess.SubprocessError):
                version = "版本未知"
        result[name] = {"found": bool(found), "path": found or "", "version": version}
    return result


def build_report() -> dict:
    """汇总结论。"""
    modules = collect_modules()
    benches = collect_testbenches()
    tools = tool_availability()
    per_task = {}
    for task, commands in CHECK_COMMANDS.items():
        task_modules = [item for item in modules if item["rtl"].startswith(f"{task}/")]
        task_benches = [item for item in benches if item["path"].startswith(f"{task}/")]
        per_task[task] = {
            "modules": len(task_modules),
            "testbenches": len(task_benches),
            "check_commands": list(commands),
            "evidence": latest_evidence(task),
        }
    gaps = []
    for item in modules:
        if item["referenced_by"] == 0:
            gaps.append(f"模块 {item['module']} 没有被任何非 RTL 文件引用")
    if not any(info["found"] for info in tools.values()):
        gaps.append("本机无 verilator / iverilog / yosys，RTL 回归无法在此环境执行")
    return {
        "schema": "adi_digital_status_v1",
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "summary": {
            "modules": len(modules),
            "testbenches": len(benches),
            "tasks": len(per_task),
            "gaps": gaps,
        },
        "tools": tools,
        "per_task": per_task,
        "modules": modules,
        "testbenches": benches,
    }


def main() -> int:
    """生成状态 JSON 并打印一张概览表。

    本工具盘点文件、引用和收据，不实际执行仿真或 EDA。
    不能根据测试台文件存在或模块被引用就把状态提升为验证通过。
    历史收据须与当前源码摘要核对后才可用于本版结论。
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=ROOT / "trace" / "digital_status.json")
    args = parser.parse_args()
    report = build_report()
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"模块 {report['summary']['modules']} 个，TB {report['summary']['testbenches']} 个")
    print(f"{'task':22s} {'模块':>4s} {'TB':>4s} {'检查命令':>6s}  证据文件")
    for task, info in report["per_task"].items():
        evidence = info["evidence"]
        count = evidence.get("files", 0)
        print(
            f"{task:22s} {info['modules']:>4d} {info['testbenches']:>4d} "
            f"{len(info['check_commands']):>6d}  {count}"
        )
    print("外部工具:", {name: info["found"] for name, info in report["tools"].items()})
    for gap in report["summary"]["gaps"]:
        print("缺口:", gap)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
