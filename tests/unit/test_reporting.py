"""A report must show incomplete/failed evidence and escape result-provided text."""

from adi_model.reporting import render_report


def test_report_does_not_claim_success_for_missing_or_failed_gates():
    html = render_report({"pipeline": {"PASS": False, "判据": "<script>bad()</script>"}})
    assert "FAIL / INCOMPLETE" in html
    assert "&lt;script&gt;bad()&lt;/script&gt;" in html
    assert "<script>bad()" not in html
    assert "未提供证据" in html
