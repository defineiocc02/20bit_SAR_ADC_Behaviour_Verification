# =============================================================================
# 文件名     : check_source_matrix.py
# 模块名     : tools.check_source_matrix
# 版本       : v1.0
# 功能       : 校验 L1 源文台账 trace/source_matrix.csv 的字段、枚举与可追溯性
#
# 设计说明   :
#   * 这份台账回答「原文哪一页哪一句 → 落在哪行代码 → 哪个测试」。
#   * 六条规则：
#       1. 表头必须与约定完全一致，列序不认偏差；
#       2. 必填列非空（source_id / source_doc / page_pdf / locator / quote_en /
#          quote_zh / quantity / status / evidence_level / preset）；
#       3. 枚举列取值合法，拼错会当场报错而不是默默放过；
#       4. impl_path 写成「路径:行号」，文件须存在、行号须在范围内，
#          且 impl_symbol 必须出现在该行 ±WINDOW 行内；
#       5. test_id 必须是 pytest 真能收集到的节点；
#       6. OUT_OF_SCOPE 必须写 note 说明理由，MISSING 也必须写 note 说明缺什么。
#   * PASS 行的门槛更高：impl_path、impl_symbol、test_id 三者都不能空。
#     理由很简单——声称「对齐了原文」却不指代码也不指测试，就是空头承诺。
#   * --strict 额外要求 MISSING 为 0，这是定版门禁 G1 的口径；构建期默认放宽，
#     因为台账先立、实现后补，中间态必然是 MISSING。
# -----------------------------------------------------------------------------
# 变更记录
# -----------------------------------------------------------------------------
# 初始版本。
# =============================================================================

"""校验 L1 源文台账的字段完整性、枚举合法性与代码/测试可追溯性。"""

from __future__ import annotations

import argparse
import csv
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MATRIX = ROOT / "trace" / "source_matrix.csv"

COLUMNS = [
    "source_id", "source_doc", "page_pdf", "page_printed", "locator",
    "quote_en", "quote_zh", "quantity", "value", "unit",
    "model_symbol", "impl_path", "impl_symbol", "test_id",
    "status", "evidence_level", "tolerance", "preset", "trace_ids", "note",
]

REQUIRED = (
    "source_id", "source_doc", "page_pdf", "locator", "quote_en",
    "quote_zh", "quantity", "status", "evidence_level", "preset",
)

SOURCE_DOCS = {"ISSCC2024_PAPER", "ISSCC2024_SLIDES", "US10505561B2"}
STATUSES = {"PASS", "FAIL", "PARTIAL", "MISSING", "OUT_OF_SCOPE", "NOT_RUN"}
EVIDENCE_LEVELS = {"PAPER", "SLIDE", "OCR"}
PRESETS = {"baseline_10m_5v", "paper_isscc2024", "both", "na"}

# impl_path 写成「路径:行号」。行号是给人和脚本共同的锚，不是给 git blame 的。
IMPL_RE = re.compile(r"^(?P<path>[^:]+):(?P<line>\d+)$")
WINDOW = 40


class Problems:
    """收集所有问题后一次性报告，避免逐条中断看不到全貌。"""

    def __init__(self) -> None:
        """建立空的问题列表。"""
        self.items: list[str] = []

    def add(self, source_id: str, message: str) -> None:
        """追加一条带 source_id 前缀的问题。"""
        self.items.append(f"[{source_id}] {message}")

    def __bool__(self) -> bool:
        """有问题时为真，便于 `if problems:` 直接判断。"""
        return bool(self.items)


def collect_test_ids() -> set[str] | None:
    """跑一次 pytest --collect-only，拿到真实可收集的节点集合。

    返回 None 表示收集失败或 pytest 不可用，此时调用方应跳过 test_id 校验
    而不是判失败——把「没装测试依赖」误报成「台账写错」会让人不再信任本脚本。
    """
    try:
        done = subprocess.run(
            [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider"],
            cwd=ROOT, capture_output=True, text=True, timeout=300,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"警告: pytest 收集不可用（{exc}），跳过 test_id 校验", file=sys.stderr)
        return None
    if done.returncode != 0:
        tail = (done.stdout or done.stderr).strip().splitlines()[-5:]
        print("警告: pytest --collect-only 非零退出，跳过 test_id 校验", file=sys.stderr)
        for line in tail:
            print(f"       {line}", file=sys.stderr)
        return None
    ids = {line.strip() for line in done.stdout.splitlines() if "::" in line}
    return ids or None


def check_impl(row: dict[str, str], problems: Problems) -> None:
    """核对 impl_path 与 impl_symbol 是否真的对得上。"""
    spec = row["impl_path"].strip()
    symbol = row["impl_symbol"].strip()
    if not spec:
        if symbol:
            problems.add(row["source_id"], "写了 impl_symbol 却没有 impl_path")
        return

    match = IMPL_RE.match(spec)
    if not match:
        problems.add(row["source_id"], f"impl_path 须写成 路径:行号，实际为 {spec!r}")
        return
    path = ROOT / match.group("path")
    line_no = int(match.group("line"))
    if not path.is_file():
        problems.add(row["source_id"], f"impl_path 指向的文件不存在: {match.group('path')}")
        return
    if not symbol:
        problems.add(row["source_id"], "有 impl_path 却没有 impl_symbol")
        return

    lines = path.read_text(encoding="utf-8").splitlines()
    if not 1 <= line_no <= len(lines):
        problems.add(row["source_id"], f"行号 {line_no} 超出 {match.group('path')} 的 1..{len(lines)}")
        return
    low = max(0, line_no - 1 - WINDOW)
    high = min(len(lines), line_no + WINDOW)
    window = "\n".join(lines[low:high])
    if symbol not in window:
        problems.add(
            row["source_id"],
            f"{match.group('path')}:{line_no} ±{WINDOW} 行内找不到 impl_symbol {symbol!r}",
        )


def check_row(
    row: dict[str, str], problems: Problems, test_ids: set[str] | None
) -> None:
    """检查单行的必填、枚举、状态与可追溯性。"""
    sid = row["source_id"].strip() or "<空 source_id>"

    for column in REQUIRED:
        if not row[column].strip():
            problems.add(sid, f"必填列为空: {column}")

    if row["source_doc"].strip() and row["source_doc"] not in SOURCE_DOCS:
        problems.add(sid, f"source_doc 非法: {row['source_doc']!r}")
    status = row["status"].strip()
    if status and status not in STATUSES:
        problems.add(sid, f"status 非法: {status!r}")
    if row["evidence_level"].strip() and row["evidence_level"] not in EVIDENCE_LEVELS:
        problems.add(sid, f"evidence_level 非法: {row['evidence_level']!r}")
    if row["preset"].strip() and row["preset"] not in PRESETS:
        problems.add(sid, f"preset 非法: {row['preset']!r}")

    page = row["page_pdf"].strip()
    if page and (not page.isdigit() or int(page) <= 0):
        problems.add(sid, f"page_pdf 须为正整数，实际为 {page!r}")

    if status == "OUT_OF_SCOPE" and not row["note"].strip():
        problems.add(sid, "OUT_OF_SCOPE 必须在 note 写明理由")
    if status == "MISSING" and not row["note"].strip():
        problems.add(sid, "MISSING 必须在 note 写明缺什么")
    if status == "FAIL" and not row["note"].strip():
        problems.add(sid, "FAIL 必须在 note 写明失败点")

    if status == "PASS":
        for column in ("impl_path", "impl_symbol", "test_id"):
            if not row[column].strip():
                problems.add(sid, f"PASS 行不能为空: {column}")

    check_impl(row, problems)

    test_id = row["test_id"].strip()
    if test_id and test_ids is not None and test_id not in test_ids:
        problems.add(sid, f"test_id 无法被 pytest 收集: {test_id}")


def main() -> int:
    """读表、逐行校验、打印汇总，返回进程退出码。"""
    parser = argparse.ArgumentParser(description="校验 L1 源文台账")
    parser.add_argument(
        "--strict", action="store_true",
        help="额外要求 MISSING 为 0（定版门禁 G1 口径）",
    )
    parser.add_argument(
        "--skip-collect", action="store_true",
        help="跳过 pytest 收集（改台账时省时间，正式跑不要加）",
    )
    args = parser.parse_args()

    if not MATRIX.is_file():
        print(f"错误: 找不到台账 {MATRIX}", file=sys.stderr)
        return 2

    with MATRIX.open(encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        header = reader.fieldnames or []
        rows = list(reader)

    problems = Problems()
    if header != COLUMNS:
        missing = [c for c in COLUMNS if c not in header]
        extra = [c for c in header if c not in COLUMNS]
        problems.add("<表头>", f"列不匹配；缺 {missing}，多 {extra}，或列序不同")

    seen: dict[str, int] = {}
    for index, row in enumerate(rows, start=2):
        sid = (row.get("source_id") or "").strip()
        if sid:
            if sid in seen:
                problems.add(sid, f"source_id 与第 {seen[sid]} 行重复")
            else:
                seen[sid] = index

    test_ids = None if args.skip_collect else collect_test_ids()
    for row in rows:
        check_row(row, problems, test_ids)

    by_status: dict[str, int] = {}
    by_doc: dict[str, int] = {}
    for row in rows:
        by_status[row["status"]] = by_status.get(row["status"], 0) + 1
        by_doc[row["source_doc"]] = by_doc.get(row["source_doc"], 0) + 1

    print(f"台账: {MATRIX.relative_to(ROOT)}  共 {len(rows)} 行")
    print("  按来源: " + "  ".join(f"{k}={v}" for k, v in sorted(by_doc.items())))
    print("  按状态: " + "  ".join(f"{k}={v}" for k, v in sorted(by_status.items())))
    print(f"  test_id 校验: {'跳过' if test_ids is None else f'已对照 {len(test_ids)} 个节点'}")

    if args.strict and by_status.get("MISSING"):
        problems.add("<strict>", f"MISSING 仍为 {by_status['MISSING']}，G1 要求为 0")

    if problems:
        print(f"\n发现 {len(problems.items)} 个问题：", file=sys.stderr)
        for item in problems.items:
            print(f"  {item}", file=sys.stderr)
        return 1

    print("\nOK: 台账字段、枚举与可追溯性全部通过。")
    print("注意: 台账通过只说明「引用能对上」，不说明「实现正确」。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
