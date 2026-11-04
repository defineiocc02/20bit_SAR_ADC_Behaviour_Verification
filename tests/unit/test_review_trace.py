"""Keep published reading coverage and requirement mappings complete and linked."""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REVIEW = ROOT / "docs/reviews"


def test_source_review_covers_each_registered_page_once():
    manifest = json.loads((ROOT / "sources_manifest.json").read_text())
    sources = manifest["sources"] + manifest["background_sources"]["items"]
    by_id = {row["id"]: row for row in sources}
    coverage = json.loads((REVIEW / "20261105-source-coverage.json").read_text())
    assert coverage["totals"] == {
        "documents": 18,
        "pages": 596,
        "text_read": 596,
        "image_checked": 596,
    }
    assert {row["source_id"] for row in coverage["documents"]} == set(by_id)
    for document in coverage["documents"]:
        source = by_id[document["source_id"]]
        assert document["sha256"] == source["sha256"]
        assert document["pages"] == source["pages"]
        pages = document["page_review"]
        assert [row["pdf_page"] for row in pages] == list(range(1, source["pages"] + 1))
        assert all(row["text_read"] and row["image_checked"] for row in pages)


def test_each_source_requirement_has_a_valid_applicability_and_mechanism_mapping():
    trace = json.loads((REVIEW / "20261105-requirements-trace.json").read_text())
    manifest = json.loads((ROOT / "sources_manifest.json").read_text())
    inventory = json.loads((ROOT / "mechanism_inventory.json").read_text())
    sources = {row["id"] for row in manifest["sources"] + manifest["background_sources"]["items"]}
    mechanisms = {row["id"] for row in inventory["mechanisms"]}
    rows = trace["requirements"]
    assert len(rows) == len({row["req_id"] for row in rows}) == 126
    allowed = {
        "target_selected",
        "optional_patent_embodiment",
        "background_comparison",
        "product_interface_reference",
        "unresolved_source_conflict",
    }
    counts = dict.fromkeys(allowed, 0)
    for row in rows:
        assert row["scope"] in allowed
        assert row["source_ids"] and set(row["source_ids"]) <= sources
        assert set(row["mechanism_ids"]) <= mechanisms
        assert row["expected_behavior"] and row["reason"]
        assert row["coverage_state"] in {
            "partial",
            "open",
            "comparison_only",
            "open_source_conflict",
        }
        counts[row["scope"]] += 1
    assert counts == trace["counts_by_scope"]


def test_reviewed_production_file_inventory_is_complete_with_valid_snapshot_hashes():
    report = json.loads((REVIEW / "20261105-code-coverage.json").read_text())
    expected = {p.relative_to(ROOT).as_posix() for p in (ROOT / "src/adi_model").glob("*.py")}
    expected |= {p.relative_to(ROOT).as_posix() for p in (ROOT / "rtl").rglob("*.sv")}
    expected |= {p.relative_to(ROOT).as_posix() for p in (ROOT / "ad4080_dsp/rtl").glob("*.sv")}
    assert report["production_python_modules"] == 49
    assert report["production_rtl_modules"] == 41
    rows = report["files"]
    assert len(rows) == 90
    assert {row["path"] for row in rows} == expected
    for row in rows:
        assert row["full_read"]
        assert row["read_ranges"] == [[1, row["baseline_lines"]]]
        # Dated audit hashes identify the reviewed snapshot, not future HEADs.
        assert re.fullmatch(r"[0-9a-f]{64}", row["current_sha256"])
        assert re.fullmatch(r"[0-9a-f]{64}", row["baseline_sha256"])
