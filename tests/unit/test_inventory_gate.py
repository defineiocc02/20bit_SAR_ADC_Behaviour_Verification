"""检查当前仓库清单的引用有效性及静态检查的证据边界。

必需条目由清单显式登记，不从旧 T01–T14 常量补全。状态未就绪必须保留，
静态引用成功不能被改称模型、RTL 或文献语义已经验证。
"""

import json
from pathlib import Path

from adi_model.inventory_gate import build_summary, verify

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_gate_passes_on_the_current_checkout():
    result = verify(REPO_ROOT)
    failures = [f"{r.check}: {r.detail}" for r in result.records if r.level == "FAIL"]
    assert result.static_refs_pass, "静态清单错误：" + "; ".join(failures)


def test_sources_are_locatable_or_explicitly_blocked():
    result = verify(REPO_ROOT)
    sources = [r for r in result.records if r.check.startswith("sources.")]
    assert sources
    assert all(r.level in ("PASS", "BLOCKED") for r in sources)
    for record in sources:
        if record.level == "BLOCKED":
            assert record.detail.strip()


def test_explicit_required_task_coverage_has_no_gaps():
    inventory = json.loads((REPO_ROOT / "mechanism_inventory.json").read_text())
    required = inventory["coverage_obligations"]["required_task_ids"]
    assert required and len(set(required)) == len(required)
    result = verify(REPO_ROOT)
    checks = [r for r in result.records if r.check == "coverage.required_task_ids"]
    assert checks and all(r.level == "PASS" for r in checks)


def test_summary_preserves_unresolved_states_without_claiming_executed_tests():
    result = verify(REPO_ROOT)
    summary = build_summary(result, REPO_ROOT)
    assert type(summary["static_refs_pass"]) is bool
    assert type(summary["required_states_ready"]) is bool
    assert summary["source_conformance"] == "SOURCE_CONFORMANCE_NOT_ASSESSED"
    assert summary["model_tests_run"] is False
    assert summary["rtl_or_eda_run"] is False
    assert summary["evidence_scope"] == "STATIC_IDENTITY_AND_REFERENCE_CHECK_ONLY"
    assert set(summary["counts"]) == {"PASS", "FAIL", "BLOCKED"}
    assert all(type(v) is int and v >= 0 for v in summary["counts"].values())
    expected = [
        {"check": r.check, "level": r.level, "detail": r.detail}
        for r in result.records
        if r.scope == "REGISTERED_REQUIRED_STATE" and r.level != "PASS"
    ]
    assert summary["unresolved_required_states"] == expected
    assert summary["required_states_ready"] is (not expected)
    for record in summary["records"]:
        assert set(record) == {"check", "level", "detail", "scope"}
