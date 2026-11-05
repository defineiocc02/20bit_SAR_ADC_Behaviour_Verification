"""检查机制、来源清单的静态引用一致性（阶段 A，任务书 §10）。

核对 mechanism_inventory.json 与 sources_manifest.json 登记的代码路径、
测试标识、来源和枚举锚点，并检查任务书 T01–T14 的清单覆盖。
静态引用可定位不表示相应机制已执行验证或达到性能要求。
"""

from __future__ import annotations

from pathlib import Path

from adi_model.inventory_gate import build_summary, verify

REPO_ROOT = Path(__file__).resolve().parents[2]


def _result():
    return verify(REPO_ROOT)


def test_gate_passes_on_the_current_checkout() -> None:
    """检查清单登记的代码路径、测试标识、来源及枚举可定位。"""
    result = _result()
    failures = [r for r in result.records if r.level == "FAIL"]
    assert result.ok, "清单门禁失败：" + "; ".join(f"{r.check}: {r.detail}" for r in failures)


def test_sources_are_locatable_or_explicitly_blocked() -> None:
    """检查原文摘要匹配，或显式登记 BLOCKED，不静默跳过。"""
    result = _result()
    checks = {r.check: r.level for r in result.records if r.check.startswith("sources.")}
    assert checks, "来源清单没有任何校验记录"
    for check, level in checks.items():
        assert level in ("PASS", "BLOCKED"), f"{check} 处于非法状态 {level}"


def test_task_coverage_has_no_gaps() -> None:
    """检查 T01–T14 均由机制或待建项覆盖，范围见任务书 §4。"""
    result = _result()
    coverage = [r for r in result.records if r.check == "coverage.T01-T14"]
    assert coverage and coverage[0].level == "PASS", "T01-T14 覆盖存在缺口"


def test_summary_is_strictly_typed() -> None:
    """检查 validation_summary 的 ok 为布尔值，三个 counts 均为非负整数。"""
    result = _result()
    summary = build_summary(result, REPO_ROOT)
    assert isinstance(summary["ok"], bool)
    assert summary["ok"] is True
    counts = summary["counts"]
    assert set(counts) == {"PASS", "FAIL", "BLOCKED"}
    assert all(isinstance(v, int) and v >= 0 for v in counts.values())
    for rec in summary["records"]:
        assert set(rec) == {"check", "level", "detail"}
