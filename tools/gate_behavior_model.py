#!/usr/bin/env python3
# =============================================================================
# 文件名     : gate_behavior_model.py
# 模块名     : tools.gate_behavior_model
# 版本       : v1.0
# 功能       : 跑定版门禁 G1-G7，写出 gate.json 与 receipt.json。
#
# 设计说明   :
#   * 门禁的价值在于**允许红**。这个脚本不修任何东西、不调任何参数，只做两件事：
#     把每一门的实测证据固定下来，把没过的门连同缺口清单一起写进收据。
#     一个只会亮绿的 gate 脚本等于没有 gate。
#   * G2（pytest）用子进程跑，不用进程内的 pytest——进程内跑会把本脚本自己也
#     收集进去，且插件状态受当前解释器影响。
#   * G7 检查的是「旧收据的 source_sha256 有没有被动过」。本轮一个都没动：
#     旧收据记录的是当时的字节，改了就是伪造历史。已被本轮改动覆盖的收据
#     记在 receipt.json 的 stale_receipts 里，指明原因与替代者，而不是去改它。
# -----------------------------------------------------------------------------
# 变更记录
# -----------------------------------------------------------------------------
# 初始版本，M9。
# =============================================================================

"""跑定版门禁 G1-G7，写出 gate.json 与 receipt.json。"""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# 门禁里要用到的既有工具。
MATRIX_TOOL = "tools/check_source_matrix.py"
LAYERING_TOOL = "tools/check_layering.py"
BASELINE_TOOL = "tools/snapshot_baseline.py"

# G5 的 A1-A5 结论从 M7 的产物读，不重跑——重跑要 40 秒且结果确定。
ALIGNMENT_JSON = "task2/evidence/paper_alignment/paper_alignment.json"

# G6 的判据窗口，与 paper_alignment 里的常量保持一致（那边改这里也要改）。
STATIC_DNL_WINDOW_LSB20 = 0.35
STATIC_INL_WINDOW_LSB20 = 3.5


def run_tool(args: list[str], timeout_s: int = 900) -> tuple[int, str]:
    """跑一个仓库内的工具，返回退出码与合并后的输出。"""
    completed = subprocess.run(
        [sys.executable, *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=timeout_s,
        check=False,
    )
    return completed.returncode, (completed.stdout + completed.stderr).strip()


def source_matrix_counts() -> dict:
    """从台账统计各状态的行数，并列出 MISSING 的条目。"""
    path = ROOT / "trace/source_matrix.csv"
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    counts: dict[str, int] = {}
    missing = []
    for row in rows:
        status = row["status"]
        counts[status] = counts.get(status, 0) + 1
        if status == "MISSING":
            missing.append({"source_id": row["source_id"], "quantity": row["quantity"]})
    return {"total": len(rows), "counts": counts, "missing": missing}


def gate_structure() -> dict:
    """G1：台账结构完整（MISSING=0、OUT_OF_SCOPE 有理由、路径可解析）。"""
    code, output = run_tool([MATRIX_TOOL])
    counts = source_matrix_counts()
    # check_source_matrix 不带 --strict 时只校验字段与可追溯性，MISSING 不算错；
    # G1 要求的是 MISSING=0，所以这里单独判。
    passed = code == 0 and counts["counts"].get("MISSING", 0) == 0
    return {
        "gate": "G1",
        "name": "结构完整",
        "status": "PASS" if passed else "FAIL",
        # 把「脚本校验」与「MISSING 计数」分开写：前者过了、后者不为 0，
        # 和前者就报错，是两种完全不同的失败，detail 里必须能分开读。
        "detail": (
            f"台账 {counts['total']} 行；脚本校验 exit {code}（0=字段/枚举/路径全过）；"
            f"MISSING={counts['counts'].get('MISSING', 0)}；"
            f"状态分布 {counts['counts']}"
        ),
        "missing": counts["missing"],
        "tool_output": output.splitlines()[-3:],
    }


def gate_regression() -> dict:
    """G2：全量 pytest。"""
    code, output = run_tool(["-m", "pytest", "-q", "--no-header"], timeout_s=1800)
    tail = [line for line in output.splitlines() if "passed" in line or "failed" in line]
    return {
        "gate": "G2",
        "name": "回归",
        "status": "PASS" if code == 0 else "FAIL",
        "detail": tail[-1] if tail else "pytest 未给出汇总行",
    }


def gate_layering() -> dict:
    """G3：库层无反向依赖。"""
    code, output = run_tool([LAYERING_TOOL])
    tail = [line for line in output.splitlines() if line.startswith("——")]
    return {
        "gate": "G3",
        "name": "分层",
        "status": "PASS" if code == 0 else "FAIL",
        "detail": tail[-1] if tail else "check_layering 无汇总行",
    }


def gate_baseline() -> dict:
    """G4：零噪声预设的 8 组指标与指纹逐字段相等。"""
    code, output = run_tool([BASELINE_TOOL, "--check"])
    return {
        "gate": "G4",
        "name": "基线冻结",
        "status": "PASS" if code == 0 else "FAIL",
        "detail": output.splitlines()[-1] if output else "",
    }


def gate_numeric() -> dict:
    """G5：A1-A5 全过。"""
    path = ROOT / ALIGNMENT_JSON
    if not path.exists():
        return {
            "gate": "G5",
            "name": "数值对标",
            "status": "NOT_RUN",
            "detail": f"缺少 {ALIGNMENT_JSON}，先跑 paper_alignment.py",
        }
    result = json.loads(path.read_text(encoding="utf-8"))
    statuses = {key: result[key]["status"] for key in ("A1", "A2", "A3", "A4", "A5")}
    failed = [key for key, status in statuses.items() if status != "PASS"]
    return {
        "gate": "G5",
        "name": "数值对标",
        "status": "PASS" if not failed else "FAIL",
        "detail": f"A1-A5 = {statuses}",
        "failed_items": failed,
        "values": {key: result[key].get("dr_paper_db") for key in ("A1", "A3")},
    }


def gate_static() -> dict:
    """G6：静态 DNL/INL 进窗口。"""
    path = ROOT / "task2/evidence/paper_alignment/static.json"
    if not path.exists():
        return {
            "gate": "G6",
            "name": "静态对标",
            "status": "NOT_RUN",
            "detail": "缺少 static.json，先跑 paper_alignment.py --static",
        }
    rows = json.loads(path.read_text(encoding="utf-8"))
    target = next(
        row
        for row in rows
        if row["group"] == "fig21_dem"
        and row["dither"] == "on"
        and row["mode"] == "lms"
        and row["sigma"] == 1e-3
    )
    worst_dnl = max(abs(target["dnl_min_lsb20"]), abs(target["dnl_max_lsb20"]))
    dnl_ok = worst_dnl <= STATIC_DNL_WINDOW_LSB20
    inl_ok = target["inl_peak_lsb20"] <= STATIC_INL_WINDOW_LSB20
    return {
        "gate": "G6",
        "name": "静态对标",
        "status": "PASS" if (dnl_ok and inl_ok) else "FAIL",
        "detail": (
            f"|DNL| {worst_dnl:.3f} LSB20（窗口 ±{STATIC_DNL_WINDOW_LSB20}）"
            f" ⇒ {'OK' if dnl_ok else 'OUT'}；"
            f"INL {target['inl_peak_lsb20']:.3f} LSB20（窗口 ≤{STATIC_INL_WINDOW_LSB20}）"
            f" ⇒ {'OK' if inl_ok else 'OUT'}"
        ),
        "failed_items": [name for name, ok in (("DNL", dnl_ok), ("INL", inl_ok)) if not ok],
    }


def gate_receipts() -> dict:
    """G7：旧收据的 source_sha256 未被改动。"""
    script = ROOT / "tools/verify_receipts.py"
    if not script.exists():
        return {
            "gate": "G7",
            "name": "收据",
            "status": "NOT_RUN",
            "detail": "tools/verify_receipts.py 不存在；本轮以只读方式核对，未改任何旧收据",
        }
    code, output = run_tool(["tools/verify_receipts.py"])
    missing = [line.strip() for line in output.splitlines() if "MISSING" in line]
    stale = [line.strip() for line in output.splitlines() if "STALE_BYTES  different" in line]
    return {
        "gate": "G7",
        "name": "收据",
        "status": "PASS" if code == 0 else "FAIL",
        "detail": output.splitlines()[-1] if output else "",
        "missing": missing,
        "stale_bytes": stale,
        "note": (
            "这些是既有状态，不是本轮引入。3 条 MISSING 全是 handoff_manifest.json 用"
            "相对路径记录 artifacts（rtl/、tb/、tools/ 相对 task_reconstruction/），"
            "而 verify_receipts 在仓库根解析，所以找不到；文件本身都在。"
            "1 条 STALE_BYTES different 是 task_reconstruction/tools/run_checks.py 的字节"
            "与 result.json 记录不符，本轮没有动过它。本轮未修改任何旧收据的 source_sha256。"
        ),
    }


def stale_receipts() -> list[dict]:
    """列出因本轮改动而失效的归档收据，指明原因与替代者。

    不改这些文件：它们记录的 source_sha256 是「当时字节」，改了就是伪造历史。
    重签是下一轮独立任务。
    """
    return [
        {
            "path": "task1/evidence/effects/summary.json",
            "reason": "run_effects.py 的 delays 改由 adi_model.timing.TimingConfig 提供",
            "verified": "重跑 run_effects 与归档的 interleave.fixed_ab / interleave.dynamic "
            "/ dem 三块逐字段完全一致（M5 gate: effects_archive_identical）",
            "superseded_by": None,
        },
        {
            "path": "task1/evidence/adc2_dither/summary.json",
            "reason": "inl_dnl._output_codes 改调 adi_model.codes.to_unsigned",
            "verified": "pytest 146 passed，含 test_adc2_dither 的 4 条（M6）",
            "superseded_by": None,
        },
        {
            "path": "task2/evidence/baseline_fingerprint/fingerprint.json",
            "reason": "experiment.py 的 _capture 增加 noise / reference 参数与注入路径",
            "verified": "snapshot_baseline --check 8 项逐字段相等（噪声与参考默认全关）",
            "superseded_by": None,
        },
    ]


def main() -> int:
    """跑全部门禁并写 gate.json / receipt.json。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=ROOT / "task2/evidence/behavior_freeze")
    parser.add_argument("--skip-slow", action="store_true", help="跳过 G2 的全量 pytest（调试用）")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    gates = [gate_structure()]
    if args.skip_slow:
        gates.append({"gate": "G2", "name": "回归", "status": "NOT_RUN", "detail": "已跳过"})
    else:
        gates.append(gate_regression())
    gates += [gate_layering(), gate_baseline(), gate_numeric(), gate_static(), gate_receipts()]

    passed = [gate["gate"] for gate in gates if gate["status"] == "PASS"]
    failed = [gate["gate"] for gate in gates if gate["status"] == "FAIL"]
    not_run = [gate["gate"] for gate in gates if gate["status"] == "NOT_RUN"]
    gate_doc = {
        "schema": "adi_behavior_model_gate_v1",
        "frozen_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "summary": {
            "pass": passed,
            "fail": failed,
            "not_run": not_run,
            "verdict": "GREEN" if not failed and not not_run else "RED",
        },
        "gates": gates,
    }
    (args.out / "gate.json").write_text(
        json.dumps(gate_doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    receipt = {
        "schema": "adi_behavior_freeze_receipt_v1",
        "frozen_at": gate_doc["frozen_at"],
        "scope": "行为级模型复现（M0-M9）；RTL 全程只读，未触及 22 个 .sv/.svh/.v 文件",
        "gates": gate_doc["summary"],
        "stale_receipts": stale_receipts(),
        "cannot_claim": [
            "G5 的 A3 未过、G6 的 DNL 未过：本模型缺 C-V 非线性和开关电荷注入，"
            "低频段产生不出论文那 0.5 dB 的谐波/杂散，DNL 也停在结构性地板 0.64 LSB20",
            "G1 的 MISSING=11：这些是尚未实现的指标项，不是遗漏的台账行",
            "A1 的闭合有自证成分：噪声 σ 是按 94.2 dB 反推的，它验的是注入链路与口径，"
            "不是「芯片真是 94.2」",
            "逐源噪声分配全部是 S5_假设，只有合计受论文约束",
            "G7 的 3 条 MISSING 与 1 条 STALE_BYTES 都是既有状态（路径基准与一个未动过的文件），"
            "不是本轮引入；本轮只读旧收据，未改任何 source_sha256",
            "本收据不构成 RTL 或物理 signoff：行为模型通过与否与 RTL 功能正确性无关",
        ],
    }
    (args.out / "receipt.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(gate_doc["summary"], ensure_ascii=False))
    return 0 if gate_doc["summary"]["verdict"] == "GREEN" else 1


if __name__ == "__main__":
    raise SystemExit(main())
