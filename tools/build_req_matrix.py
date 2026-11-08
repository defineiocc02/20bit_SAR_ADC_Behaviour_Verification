#!/usr/bin/env python3
# =============================================================================
# 文件名     : build_req_matrix.py
# 模块名     : tools.build_req_matrix
# 版本       : v1.0
# 功能       : 把五个 task 的 verification_plan.yaml 汇成一张需求追踪矩阵。
#
# 设计说明   :
#   * 数字交付树里 33 条 REQ 分散在 task1/task2/task3/task_integration/
#     task_reconstruction 五份计划表，此前没有任何一处能一次看到全貌；
#     而 evidence 的 JSON 里没有 REQ 字段，所以「哪份证据对应哪条 REQ」
#     无法自动建链——本脚本只做**声明层**的汇总，不假装知道证据对应关系，
#     那一列一律写「待确认」。
#   * 不引入 pyyaml：五份计划表的格式是固定的一行式 `{...}` 与缩进块式两种，
#     正则足够，且不会因为新增依赖让 CI 多一个安装步骤。
#   * 声明了 `status: BLOCKED` 的项单独标出：那是作者自己写下的「缺输入」，
#     比任何外部推断都可信。
# -----------------------------------------------------------------------------
# 变更记录
# -----------------------------------------------------------------------------
# 初始版本。
# =============================================================================

"""把五份 verification_plan.yaml 汇成一张需求追踪矩阵。"""

from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# 三份计划表没有声明 top:，按各 task 的主模块补上（这是从目录结构能确定的，
# 不是猜）。表里声明了 top 的（task3 / task_integration）以表为准。
TOP_FALLBACK = {
    "task1": "module1_top",
    "task2": "dither8_top",
    "task_integration": "sar20_digital_lms_top",
    "task_reconstruction": "sar20_lms_recon",
}

# 计划表位置与它归属的 task。task1 的计划表不在 task1 根下而在 design/。
PLANS = (
    ("task1", "task1/design/verification_plan.yaml"),
    ("task2", "task2/eight_rtl/verification_plan.yaml"),
    ("task3", "task3/verification_plan.yaml"),
    ("task_integration", "task_integration/verification_plan.yaml"),
    ("task_reconstruction", "task_reconstruction/verification_plan.yaml"),
)

# CSV 列序。evidence 一列固定写「待确认」——见模块头的说明。
FIELDS = (
    "req_id",
    "task",
    "top_module",
    "verification_id",
    "method",
    "observed_items",
    "declared_status",
    "evidence",
    "note",
)

# 五种写法归一用的正则。这个仓库的验证计划有四种 schema、两种方向：
#   task1            requirements: [- id: REQ-xx, test_ids: [...]]   需求→测试
#   task3            requirements: {REQ-xx: {verification, method}}  需求→验证
#   task_integration requirements: {REQ-xx: {test, checks}}          需求→测试
#   task2            checks: [- id: V-xx, requirements: [REQ-xx]]    测试→需求
#   task_reconstruction checks: [- req: REQ-xx, test: ...]           测试→需求
# 方向相反意味着不能只按「谁在行首」判断，必须先认出容器再决定主键。
TOP_LINE = re.compile(r"^top:\s*([A-Za-z0-9_]+)", re.MULTILINE)
SECTION = re.compile(r"^(requirements|checks):\s*$")
MAPPING_KEY = re.compile(r"^\s{1,4}(REQ-[A-Z0-9-]+):\s*(.*)$")
LIST_REQ_KEY = re.compile(r"^\s*-\s*(?:id|req):\s*(REQ-[A-Z0-9-]+)")
LIST_TEST_KEY = re.compile(r"^\s*-\s*id:\s*(V-[A-Z0-9-]+)")
SCALAR = re.compile(r"(verification|method|status|test|oracle):\s*([A-Za-z0-9_\-]+)")
INLINE_BRACKET = re.compile(r"(coverage|checks|test_ids|requirements):\s*\[([^\]]*)\]")


def _items(body: str) -> list[str]:
    """把 `[a, b, c]` 的正文切成条目。"""
    return [part.strip() for part in body.split(",") if part.strip()]


def parse_plan(path: Path) -> tuple[str | None, list[dict]]:
    """解析一份计划表，返回 (top 模块名, 归一后的需求条目)。

    归一后的每条是 ``{"req": ..., "tests": [...], "method": ..., "status": ...,
    "oracle": ...}``。无论原表是需求驱动还是测试驱动，输出都是「一条需求一行」。

    Args:
        path: 计划表路径。

    Returns:
        top 模块名（没有则 None）与条目列表。
    """
    text = path.read_text(encoding="utf-8")
    top_match = TOP_LINE.search(text)
    top = top_match.group(1) if top_match else None

    entries: list[dict] = []
    pending: dict | None = None
    direction = "requirement"  # requirement = 需求在左，test = 测试在左

    def flush() -> None:
        nonlocal pending
        if pending is not None:
            entries.append(pending)
            pending = None

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        section = SECTION.match(stripped)
        if section:
            flush()
            direction = "requirement" if section.group(1) == "requirements" else "test"
            continue

        if direction == "requirement":
            mapping = MAPPING_KEY.match(line)
            if mapping:
                flush()
                pending = {
                    "req": mapping.group(1),
                    "tests": [],
                    "method": "",
                    "status": "",
                    "oracle": "",
                }
                body = mapping.group(2)
                for key, value in SCALAR.findall(body):
                    pending[key if key != "verification" else "test"] = value
                for key, value in INLINE_BRACKET.findall(body):
                    if key in ("coverage", "checks"):
                        pending["tests"].extend(_items(value))
                continue
            listed = LIST_REQ_KEY.match(line)
            if listed:
                flush()
                pending = {
                    "req": listed.group(1),
                    "tests": [],
                    "method": "",
                    "status": "",
                    "oracle": "",
                }
                continue
            if pending is not None:
                for key, value in SCALAR.findall(stripped):
                    pending["test" if key == "verification" else key] = value
                for key, value in INLINE_BRACKET.findall(stripped):
                    if key in ("coverage", "checks", "test_ids"):
                        pending["tests"].extend(_items(value))
            continue

        # 测试驱动有两种写法：task2 用 `- id: V-xx` + `requirements: [...]`，
        # task_reconstruction 用 `- req: REQ-xx` + `test: <名字>`。两者都归到
        # 同一个 pending 结构上，最后统一反转成「一条需求一行」。
        listed_req = LIST_REQ_KEY.match(line)
        if listed_req:
            flush()
            pending = {
                "req": listed_req.group(1),
                "tests": [],
                "method": "",
                "status": "",
                "oracle": "",
                "requirements": [],
            }
            continue
        listed_test = LIST_TEST_KEY.match(line)
        if listed_test:
            flush()
            pending = {
                "req": "",
                "tests": [listed_test.group(1)],
                "method": "",
                "status": "",
                "oracle": "",
                "requirements": [],
            }
            continue
        if pending is not None:
            pending.setdefault("requirements", [])
            for key, value in INLINE_BRACKET.findall(stripped):
                if key == "requirements":
                    pending["requirements"].extend(_items(value))
            for key, value in SCALAR.findall(stripped):
                if key == "status":
                    pending["status"] = value
                elif key == "oracle":
                    pending["oracle"] = value
                elif key == "test":
                    pending["tests"].append(value)
    flush()

    # 测试驱动的表要反转成「一条需求一行」。
    if direction == "test":
        merged: dict[str, dict] = {}
        for entry in entries:
            for req in entry.get("requirements", []) or ([entry["req"]] if entry["req"] else []):
                row = merged.setdefault(
                    req,
                    {"req": req, "tests": [], "method": "", "status": "", "oracle": ""},
                )
                row["tests"].extend(entry["tests"])
                row["status"] = row["status"] or entry["status"]
                row["oracle"] = row["oracle"] or entry["oracle"]
        return top, list(merged.values())

    return top, entries


def build_rows() -> list[dict]:
    """汇总全部计划表，返回矩阵行。

    一条需求一行。「证据」一列固定写「待确认」——evidence 的 JSON 里没有 REQ
    字段，自动建链会得到看似精确、实则瞎猜的结果；这一列留给人补。
    """
    rows = []
    for task, relative in PLANS:
        path = ROOT / relative
        if not path.exists():
            rows.append(
                {
                    "req_id": "—",
                    "task": task,
                    "top_module": "",
                    "verification_id": "",
                    "method": "",
                    "observed_items": "",
                    "declared_status": "MISSING_PLAN",
                    "evidence": "待确认",
                    "note": f"计划表不存在：{relative}",
                }
            )
            continue
        top, entries = parse_plan(path)
        for entry in entries:
            rows.append(
                {
                    "req_id": entry["req"],
                    "task": task,
                    "top_module": top or TOP_FALLBACK.get(task, ""),
                    "verification_id": " ".join(entry["tests"]),
                    "method": entry["method"],
                    "observed_items": entry.get("oracle", ""),
                    "declared_status": entry["status"] or "声明为通过（未见显式阻塞）",
                    "evidence": "待确认",
                    "note": "",
                }
            )
    return rows


def summarize(rows: list[dict]) -> dict:
    """按 task / method / 声明状态做统计。"""
    by_task: dict[str, int] = {}
    by_method: dict[str, int] = {}
    blocked = []
    for row in rows:
        by_task[row["task"]] = by_task.get(row["task"], 0) + 1
        method = row["method"] or "未声明"
        by_method[method] = by_method.get(method, 0) + 1
        if row["declared_status"] == "BLOCKED":
            blocked.append(row["req_id"])
    return {
        "total": len(rows),
        "by_task": by_task,
        "by_method": by_method,
        "blocked": blocked,
    }


def main() -> int:
    """生成矩阵 CSV 与统计 JSON，并把统计打到 stdout。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=ROOT / "trace")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    rows = build_rows()
    csv_path = args.out / "req_matrix.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)

    stats = summarize(rows)
    (args.out / "req_matrix.json").write_text(
        json.dumps(stats, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
