"""机制/来源清单一致性门禁的自测（阶段 A，任务书 §10）。

清单即证据：本测试保证 ``mechanism_inventory.json`` 与
``sources_manifest.json`` 里登记的每个锚点真实存在，且任务书 T01-T14
最低覆盖无缺口。清单引用与代码库脱节时，这里必须先红。
"""

from __future__ import annotations

from pathlib import Path

from adi_model.inventory_gate import build_summary, verify

REPO_ROOT = Path(__file__).resolve().parents[2]


def _result():
    return verify(REPO_ROOT)


def test_gate_passes_on_the_current_checkout() -> None:
    """清单登记的代码路径、测试 ID、来源 ID、枚举全部真实有效。"""
    result = _result()
    failures = [r for r in result.records if r.level == "FAIL"]
    assert result.ok, "清单门禁失败：" + "; ".join(f"{r.check}: {r.detail}" for r in failures)


def test_sources_are_locatable_or_explicitly_blocked() -> None:
    """每份原文要么哈希比对通过，要么登记 BLOCKED——不允许静默跳过。"""
    result = _result()
    checks = {r.check: r.level for r in result.records if r.check.startswith("sources.")}
    assert checks, "来源清单没有任何校验记录"
    for check, level in checks.items():
        assert level in ("PASS", "BLOCKED"), f"{check} 处于非法状态 {level}"


def test_task_coverage_has_no_gaps() -> None:
    """T01-T14 每项都被机制或待建项覆盖（任务书 §4 最低覆盖）。"""
    result = _result()
    coverage = [r for r in result.records if r.check == "coverage.T01-T14"]
    assert coverage and coverage[0].level == "PASS", "T01-T14 覆盖存在缺口"


def test_summary_is_strictly_typed() -> None:
    """validation_summary 内容严格类型：ok 布尔、counts 为三个非负整数。"""
    result = _result()
    summary = build_summary(result, REPO_ROOT)
    assert isinstance(summary["ok"], bool)
    assert summary["ok"] is True
    counts = summary["counts"]
    assert set(counts) == {"PASS", "FAIL", "BLOCKED"}
    assert all(isinstance(v, int) and v >= 0 for v in counts.values())
    for rec in summary["records"]:
        assert set(rec) == {"check", "level", "detail"}
