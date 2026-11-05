"""检查按日期保留的审阅快照及扩展覆盖是否完整且可追溯。"""

import hashlib
import importlib
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REVIEW = ROOT / "docs/reviews"
SOURCE_BASELINE = "20261105-source-coverage.json"
SOURCE_EXTENSION = "20261105-caaz-source-coverage.json"
CODE_BASELINE = "20261105-code-coverage.json"
CODE_EXTENSION = "20261105-caaz-source-code-coverage.json"


def _load(name: str) -> dict:
    return json.loads((REVIEW / name).read_text(encoding="utf-8"))


def _registered_sources() -> dict:
    manifest = json.loads((ROOT / "sources_manifest.json").read_text(encoding="utf-8"))
    rows = manifest["sources"] + manifest["background_sources"]["items"]
    assert len(rows) == len({row["id"] for row in rows})
    return {row["id"]: row for row in rows}


def test_source_review_covers_each_registered_page_once():
    sources = _registered_sources()
    baseline, extension = (_load(name) for name in (SOURCE_BASELINE, SOURCE_EXTENSION))
    # 这些是按日期冻结的阅读记录；后续资料通过扩展记录加入，
    # 不改写原始集合以暗示当时已读过后来的资料。
    assert baseline["totals"] == {
        "documents": 18,
        "pages": 596,
        "text_read": 596,
        "image_checked": 596,
    }
    assert extension["extends"] == SOURCE_BASELINE
    assert (
        extension["baseline_coverage_sha256"]
        == hashlib.sha256((REVIEW / SOURCE_BASELINE).read_bytes()).hexdigest()
    )
    assert extension["totals"] == {
        "documents": 2,
        "pages": 45,
        "text_read": 45,
        "image_checked": 45,
    }
    documents = baseline["documents"] + extension["documents"]
    assert len(documents) == len({row["source_id"] for row in documents}) == len(sources)
    assert {row["source_id"] for row in documents} == set(sources)
    # 同内容的别名不增加唯一来源数量。
    assert len({row["sha256"] for row in documents}) == len(documents)
    for report in (baseline, extension):
        assert report["totals"]["documents"] == len(report["documents"])
        assert report["totals"]["pages"] == sum(row["pages"] for row in report["documents"])
    for document in documents:
        source = sources[document["source_id"]]
        assert document["sha256"] == source["sha256"]
        assert document["pages"] == source["pages"]
        pages = document["page_review"]
        assert [row["pdf_page"] for row in pages] == list(range(1, source["pages"] + 1))
        assert all(row["text_read"] and row["image_checked"] for row in pages)
    manifest = json.loads((ROOT / "sources_manifest.json").read_text(encoding="utf-8"))
    assert manifest["meta"]["corpus_documents"] == len(documents)
    assert manifest["meta"]["corpus_pages"] == sum(row["pages"] for row in documents)


def test_caaz_source_alias_is_explicit_and_not_a_third_document():
    sources = _registered_sources()
    paper = sources["PKU_VLSI2025_C32_1_PAPER"]
    slides = sources["PKU_VLSI2025_C32_1_SLIDES"]
    assert (paper["pages"], slides["pages"]) == (3, 42)
    assert paper["sha256"] != slides["sha256"]
    assert paper["source_role"] == slides["source_role"] == "OPTIONAL_RESEARCH_EXTENSION"
    aliases = slides["aliases"]
    assert len(aliases) == 1
    alias = aliases[0]
    assert alias["file"] == "C32_C32-1.pdf"
    assert alias["sha256"] == slides["sha256"]
    assert alias["pages"] == slides["pages"]
    assert alias["counted_as_document"] is False
    assert alias["file"] not in {row["file"] for row in sources.values()}
    extension = _load(SOURCE_EXTENSION)
    assert extension["excluded_aliases"] == [
        {
            "alias_file": alias["file"],
            "canonical_source_id": slides["id"],
            "sha256": alias["sha256"],
            "pages": alias["pages"],
            "counted_as_document": False,
        }
    ]


def test_each_source_requirement_has_a_valid_applicability_and_mechanism_mapping():
    sources = set(_registered_sources())
    inventory = json.loads((ROOT / "mechanism_inventory.json").read_text(encoding="utf-8"))
    mechanisms = {row["id"] for row in inventory["mechanisms"]}
    baseline = _load("20261105-requirements-trace.json")
    extension = _load("20261105-caaz-source-requirements.json")
    assert len(baseline["requirements"]) == 126
    assert extension["extends"] == "20261105-requirements-trace.json"
    assert (
        extension["baseline_trace_sha256"]
        == hashlib.sha256((REVIEW / "20261105-requirements-trace.json").read_bytes()).hexdigest()
    )
    assert len(extension["requirements"]) == 20
    rows = baseline["requirements"] + extension["requirements"]
    assert len(rows) == len({row["req_id"] for row in rows})
    allowed = {
        "target_selected",
        "optional_patent_embodiment",
        "background_comparison",
        "product_interface_reference",
        "unresolved_source_conflict",
        "optional_research_extension",
    }
    for report in (baseline, extension):
        counts = dict.fromkeys(report["counts_by_scope"], 0)
        for row in report["requirements"]:
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
        assert counts == report["counts_by_scope"]
    for row in extension["requirements"]:
        assert row["scope"] == "optional_research_extension"
        assert row["mechanism_ids"] == ["X02"]
        # 阅读原文不等于执行过本项目验证。
        assert row["source_branch_verification_status"] == "NOT_RUN"
        assert row["source_branch_implementation_status"] == "NOT_ASSESSED_BY_SOURCE_BRANCH"
        assert row["implementation_status_source"]["mechanism_id"] == "X02"


def test_caaz_remains_an_optional_source_derived_sample_domain_extension():
    inventory = json.loads((ROOT / "mechanism_inventory.json").read_text(encoding="utf-8"))
    caaz = next(row for row in inventory["mechanisms"] if row["id"] == "X02")
    assert "OPTIONAL_SOURCE_EXTENSION" in inventory["config_classes"]
    assert caaz["embodiment"] == "OPTIONAL_SOURCE_EXTENSION"
    assert {row["id"] for row in caaz["sources"]} == {
        "PKU_VLSI2025_C32_1_PAPER",
        "PKU_VLSI2025_C32_1_SLIDES",
    }
    assert caaz["default_enabled"] is False
    assert caaz["default_disable_contract"] == {
        "python": "ChoppingAZParameters.mode='off'",
        "rtl": "P_CAAZ_ENABLE=0",
    }
    assert caaz["completion"] == "PARTIAL_SAMPLE_DOMAIN"
    # 原文披露 CAAZ；本项目选择的原始／配对采样率是
    # 工程假设，不能据此继承原文硅片性能。
    assert set(caaz["evidence_source"]) == {"DISCLOSED", "DERIVED", "ASSUMED"}
    equations = _load("20261105-caaz-source-equations.json")
    assert equations["pair_rate_choice"]["raw_hz"] == 40_000_000
    assert equations["pair_rate_choice"]["pair_hz"] == 20_000_000
    assert equations["pair_rate_choice"]["status"].startswith("ENGINEERING_CHOICE;")


def test_reviewed_production_file_inventory_includes_baseline_and_extension():
    baseline, extension = (_load(name) for name in (CODE_BASELINE, CODE_EXTENSION))
    assert baseline["production_python_modules"] == 49
    assert baseline["production_rtl_modules"] == 41
    baseline_rows = baseline["files"]
    assert len(baseline_rows) == 90
    baseline_paths = {row["path"] for row in baseline_rows}
    assert len(baseline_paths) == len(baseline_rows)
    assert extension["extends"] == CODE_BASELINE
    assert (
        extension["baseline_coverage_sha256"]
        == hashlib.sha256((REVIEW / CODE_BASELINE).read_bytes()).hexdigest()
    )
    extension_rows = extension["files"]
    extension_paths = {row["path"] for row in extension_rows}
    assert len(extension_paths) == len(extension_rows)
    # 日期化快照只与当时Git基线比较，不改写历史JSON去冒充当前源。
    historical = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", baseline["baseline_commit"]],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    expected_baseline = {
        name
        for name in historical
        if (name.startswith("src/adi_model/") and name.endswith(".py"))
        or (name.startswith("rtl/") and name.endswith(".sv"))
        or (name.startswith("ad4080_dsp/rtl/") and name.endswith(".sv"))
    }
    assert baseline_paths == expected_baseline
    expected_extension = baseline_paths | set(extension["added_files"])
    assert baseline_paths | extension_paths == expected_extension
    assert extension["current_inventory"]["production_modules"] == len(expected_extension)
    assert set(extension["added_files"]) == extension_paths - baseline_paths
    assert set(extension["rereviewed_files"]) == extension_paths & baseline_paths
    assert extension["added_files"], "An extension cannot claim new-module coverage without rows"
    assert extension["current_inventory"] == {
        "production_python_modules": sum(path.endswith(".py") for path in expected_extension),
        "production_rtl_modules": sum(path.endswith(".sv") for path in expected_extension),
        "production_modules": len(expected_extension),
    }
    assert extension["extension_totals"] == {
        "files": len(extension_rows),
        "full_read_lines": sum(row["reviewed_lines"] for row in extension_rows),
        "added_files": len(extension_paths - baseline_paths),
        "rereviewed_files": len(extension_paths & baseline_paths),
    }
    for row in baseline_rows:
        assert row["full_read"]
        assert row["read_ranges"] == [[1, row["baseline_lines"]]]
        # 按日期保留的审计摘要标识当时快照，不用于标识未来 HEAD。
        assert re.fullmatch(r"[0-9a-f]{64}", row["current_sha256"])
        assert re.fullmatch(r"[0-9a-f]{64}", row["baseline_sha256"])
    for row in extension_rows:
        assert row["full_read"]
        assert row["read_ranges"] == [[1, row["reviewed_lines"]]]
        assert re.fullmatch(r"[0-9a-f]{64}", row["reviewed_sha256"])


def test_caaz_review_records_exclude_private_paths_and_source_payloads():
    for name in (
        SOURCE_EXTENSION,
        CODE_EXTENSION,
        "20261105-caaz-source-requirements.json",
        "20261105-caaz-source-equations.json",
        "20261105-caaz-source-review.md",
    ):
        text = (REVIEW / name).read_text(encoding="utf-8")
        assert "/Users/" not in text
        assert "ZoteroLinkedFiles" not in text
        assert '"source_pdf"' not in text
        assert '"text_path"' not in text
        assert '"image_file"' not in text
        assert '"ocr_evidence"' not in text


def test_current_runtime_source_set_has_current_imports_and_explicit_rtl_manifest():
    """当前集合另检可定位/可导入/清单完整，不把静态集合检查写作已审读或已仿真。"""
    current_python = {p.name: p for p in (ROOT / "src/adi_model").glob("*.py")}
    assert {
        "defaults.py",
        "dither.py",
        "pipeline_engine.py",
        "weight_calibration.py",
    } <= current_python.keys()
    assert not {"ktc.py", "ref_track.py", "sim_split.py", "digital_core.py"} & current_python.keys()
    for filename, path in current_python.items():
        name = "adi_model" if filename == "__init__.py" else "adi_model." + path.stem
        loaded = importlib.import_module(name)
        assert Path(loaded.__file__).resolve() == path.resolve()
    manifest = (ROOT / "rtl/rtl_sources.f").read_text().splitlines()
    declared = [
        line.strip() for line in manifest if line.strip() and not line.lstrip().startswith("#")
    ]
    current_rtl = {p.relative_to(ROOT).as_posix() for p in (ROOT / "rtl").rglob("*.sv")}
    assert len(declared) == len(set(declared))
    assert set(declared) == current_rtl
    inventory = json.loads((ROOT / "mechanism_inventory.json").read_text())
    current_production = current_rtl | {
        p.relative_to(ROOT).as_posix() for p in current_python.values()
    }
    current_production |= {
        p.relative_to(ROOT).as_posix() for p in (ROOT / "ad4080_dsp/rtl").glob("*.sv")
    }
    referenced = {
        name
        for row in inventory["mechanisms"]
        for name in row["code"]
        if name.startswith(("src/adi_model/", "rtl/", "ad4080_dsp/rtl/"))
    }
    assert referenced <= current_production
