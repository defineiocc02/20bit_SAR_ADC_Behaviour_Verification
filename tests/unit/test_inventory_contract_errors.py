"""Reject malformed static inventory references without claiming semantic coverage."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from adi_model.inventory_gate import TASK_IDS, verify


def _write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


@pytest.fixture
def inventory_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "tests").mkdir()
    (repo / "src/implemented.py").write_text("VALUE = 1\n", encoding="utf-8")
    (repo / "tests/test_symbols.py").write_text(
        '"""class TestTextOnly: not a declaration."""\n'
        "class TestExact:\n    pass\n"
        "class TestPrefixLonger:\n    pass\n"
        "def test_top_level():\n    pass\n"
        "async def test_async():\n    pass\n"
        "def factory():\n"
        "    class TestNested:\n        pass\n"
        "    def test_nested():\n        pass\n",
        encoding="utf-8",
    )
    materials = tmp_path / "materials"
    materials.mkdir()
    (materials / "primary.pdf").write_bytes(b"primary fixture")
    (materials / "background.pdf").write_bytes(b"background fixture")
    monkeypatch.setenv("ADC_SOURCES_DIR", str(materials))
    manifest = {
        "sources": [
            {
                "id": "S0",
                "file": "primary.pdf",
                "sha256": hashlib.sha256(b"primary fixture").hexdigest(),
            }
        ],
        "background_sources": {
            "items": [
                {
                    "id": "B0",
                    "file": "background.pdf",
                    "sha256": hashlib.sha256(b"background fixture").hexdigest(),
                    "hash_registered": True,
                }
            ]
        },
    }
    inventory = {
        "axes": {
            "implementation_degree": {"values": ["MECHANISM_MODEL"]},
            "evidence_source": {"values": ["ASSUMED"]},
            "verification_nature": {"values": ["MATH_CONSISTENCY"]},
        },
        "config_classes": {"PAPER_BASELINE": "target", "PROJECT_EXTENSION": "extension"},
        "mechanisms": [
            {
                "id": "M01",
                "implementation_degree": "MECHANISM_MODEL",
                "embodiment": "PAPER_BASELINE",
                "evidence_source": ["ASSUMED"],
                "verification_nature": ["MATH_CONSISTENCY"],
                "sources": [{"id": "S0", "location": "PDF page 1, Figure 1"}],
                "code": ["src/implemented.py"],
                "tests": ["tests/test_symbols.py::TestExact"],
                "task_refs": list(TASK_IDS),
                "experiments": [{"key": "not_executed", "status": "NOT_RUN"}],
            }
        ],
    }
    _write_json(repo / "sources_manifest.json", manifest)
    _write_json(repo / "mechanism_inventory.json", inventory)
    return repo


def _inventory(repo: Path) -> dict:
    return json.loads((repo / "mechanism_inventory.json").read_text(encoding="utf-8"))


def _source_manifest(repo: Path) -> dict:
    return json.loads((repo / "sources_manifest.json").read_text(encoding="utf-8"))


def _assert_mechanism_failure(repo: Path, inventory: dict, check: str) -> None:
    _write_json(repo / "mechanism_inventory.json", inventory)
    result = verify(repo)
    assert not result.ok
    assert any(record.level == "FAIL" and record.check == check for record in result.records)
    assert not any(record.check == "mechanisms.M01.static_refs" for record in result.records)


@pytest.mark.parametrize("symbol", ["TestExact", "test_top_level", "test_async"])
def test_exact_top_level_class_or_function_is_a_static_anchor(inventory_repo: Path, symbol: str):
    inventory = _inventory(inventory_repo)
    inventory["mechanisms"][0]["tests"] = [f"tests/test_symbols.py::{symbol}"]
    _write_json(inventory_repo / "mechanism_inventory.json", inventory)
    result = verify(inventory_repo)
    assert result.ok
    static = [record for record in result.records if record.check == "mechanisms.M01.static_refs"]
    assert len(static) == 1 and static[0].level == "PASS"
    assert "未执行测试" in static[0].detail


@pytest.mark.parametrize("source_id", ["UNKNOWN", None, 0])
def test_unknown_or_invalid_source_id_fails(inventory_repo: Path, source_id):
    inventory = _inventory(inventory_repo)
    inventory["mechanisms"][0]["sources"][0]["id"] = source_id
    _assert_mechanism_failure(inventory_repo, inventory, "mechanisms.M01.sources")


@pytest.mark.parametrize("location", ["", " \n\t", None, 1])
def test_source_location_must_be_a_nonempty_string(inventory_repo: Path, location):
    inventory = _inventory(inventory_repo)
    inventory["mechanisms"][0]["sources"][0]["location"] = location
    _assert_mechanism_failure(inventory_repo, inventory, "mechanisms.M01.sources")


@pytest.mark.parametrize("nature", [["UNREGISTERED"], [], "MATH_CONSISTENCY", None])
def test_verification_nature_must_use_the_declared_axis(inventory_repo: Path, nature):
    inventory = _inventory(inventory_repo)
    inventory["mechanisms"][0]["verification_nature"] = nature
    _assert_mechanism_failure(inventory_repo, inventory, "mechanisms.M01.verification_nature")


@pytest.mark.parametrize("field", ["code", "tests", "sources"])
def test_reference_fields_must_be_arrays(inventory_repo: Path, field: str):
    inventory = _inventory(inventory_repo)
    inventory["mechanisms"][0][field] = "not an array"
    _assert_mechanism_failure(inventory_repo, inventory, f"mechanisms.M01.{field}")


def test_literature_mechanism_cannot_omit_source_references(inventory_repo: Path):
    inventory = _inventory(inventory_repo)
    inventory["mechanisms"][0]["sources"] = []
    _assert_mechanism_failure(inventory_repo, inventory, "mechanisms.M01.sources")


def test_project_extension_can_explicitly_have_no_literature_reference(inventory_repo: Path):
    inventory = _inventory(inventory_repo)
    inventory["mechanisms"][0]["sources"] = []
    inventory["mechanisms"][0]["embodiment"] = "PROJECT_EXTENSION"
    _write_json(inventory_repo / "mechanism_inventory.json", inventory)
    assert verify(inventory_repo).ok


@pytest.mark.parametrize("kind", ["parent", "absolute", "symlink", "absolute_inside"])
def test_code_reference_cannot_escape_or_require_an_absolute_path(inventory_repo: Path, kind: str):
    outside = inventory_repo.parent / "outside.py"
    outside.write_text("VALUE = 1\n", encoding="utf-8")
    paths = {"parent": "../outside.py", "absolute": str(outside)}
    if kind == "symlink":
        (inventory_repo / "src/link.py").symlink_to(outside)
        relative = "src/link.py"
    elif kind == "absolute_inside":
        relative = str(inventory_repo / "src/implemented.py")
    else:
        relative = paths[kind]
    inventory = _inventory(inventory_repo)
    inventory["mechanisms"][0]["code"] = [relative]
    _assert_mechanism_failure(inventory_repo, inventory, "mechanisms.M01.code")


@pytest.mark.parametrize(
    "symbol", ["TestPrefix", "test_top", "TestTextOnly", "TestNested", "test_nested"]
)
def test_prefix_text_and_nested_declarations_are_not_top_level_anchors(
    inventory_repo: Path, symbol: str
):
    inventory = _inventory(inventory_repo)
    inventory["mechanisms"][0]["tests"] = [f"tests/test_symbols.py::{symbol}"]
    _assert_mechanism_failure(inventory_repo, inventory, "mechanisms.M01.tests")


def test_test_file_symlink_cannot_escape_repository(inventory_repo: Path):
    outside = inventory_repo.parent / "external_test.py"
    outside.write_text("class TestExact:\n    pass\n", encoding="utf-8")
    (inventory_repo / "tests/test_link.py").symlink_to(outside)
    inventory = _inventory(inventory_repo)
    inventory["mechanisms"][0]["tests"] = ["tests/test_link.py::TestExact"]
    _assert_mechanism_failure(inventory_repo, inventory, "mechanisms.M01.tests")


@pytest.mark.parametrize(
    "test_id",
    ["tests/../external_test.py::TestExact", "/tmp/test.py::TestExact", "tests/test_symbols.py"],
)
def test_invalid_test_id_path_or_format_fails(inventory_repo: Path, test_id: str):
    inventory = _inventory(inventory_repo)
    inventory["mechanisms"][0]["tests"] = [test_id]
    _assert_mechanism_failure(inventory_repo, inventory, "mechanisms.M01.tests")


def test_unparseable_test_module_is_a_visible_failure(inventory_repo: Path):
    (inventory_repo / "tests/test_symbols.py").write_text("def broken(:\n", encoding="utf-8")
    result = verify(inventory_repo)
    assert not result.ok
    assert any(
        "无法静态解析" in record.detail for record in result.records if record.level == "FAIL"
    )


def test_registered_background_source_id_can_be_referenced(inventory_repo: Path):
    inventory = _inventory(inventory_repo)
    inventory["mechanisms"][0]["sources"][0]["id"] = "B0"
    _write_json(inventory_repo / "mechanism_inventory.json", inventory)
    result = verify(inventory_repo)
    assert result.ok
    assert any(
        record.check == "sources.B0.sha256" and record.level == "PASS" for record in result.records
    )


def test_legacy_unregistered_background_source_is_explicitly_blocked(inventory_repo: Path):
    manifest = _source_manifest(inventory_repo)
    manifest["background_sources"]["items"] = [{"id": "B0", "hash_registered": False}]
    _write_json(inventory_repo / "sources_manifest.json", manifest)
    result = verify(inventory_repo)
    assert result.ok
    background = [record for record in result.records if record.check.startswith("sources.B0.")]
    assert len(background) == 1 and background[0].level == "BLOCKED"
    assert "NOT_RUN" in background[0].detail


def test_background_source_hash_mismatch_fails(inventory_repo: Path):
    manifest = _source_manifest(inventory_repo)
    manifest["background_sources"]["items"][0]["sha256"] = "0" * 64
    _write_json(inventory_repo / "sources_manifest.json", manifest)
    result = verify(inventory_repo)
    assert not result.ok
    assert any(
        record.check == "sources.B0.sha256" and record.level == "FAIL" for record in result.records
    )


def test_missing_registered_background_source_file_is_blocked(inventory_repo: Path):
    manifest = _source_manifest(inventory_repo)
    manifest["background_sources"]["items"][0]["file"] = "missing.pdf"
    _write_json(inventory_repo / "sources_manifest.json", manifest)
    result = verify(inventory_repo)
    assert result.ok
    assert any(
        record.check == "sources.B0.file" and record.level == "BLOCKED" for record in result.records
    )


def test_background_and_primary_source_ids_must_be_unique(inventory_repo: Path):
    manifest = _source_manifest(inventory_repo)
    manifest["background_sources"]["items"][0]["id"] = "S0"
    _write_json(inventory_repo / "sources_manifest.json", manifest)
    result = verify(inventory_repo)
    assert not result.ok
    assert any(
        record.check == "sources.duplicate_id" and record.level == "FAIL"
        for record in result.records
    )


def _rtl_contract(repo: Path, status: str = "DIGITAL_IMPLEMENTED") -> dict:
    (repo / "src/digital.sv").write_text("module digital; endmodule\n", encoding="utf-8")
    (repo / "tests/digital_tb.sv").write_text("module digital_tb; endmodule\n", encoding="utf-8")
    return {
        "status": status,
        "code": ["src/digital.sv"],
        "tests": ["tests/digital_tb.sv"],
        "boundary": "Digital controls only; analog settling is external.",
    }


@pytest.mark.parametrize(
    "status", ["DIGITAL_IMPLEMENTED", "CONTROL_ONLY", "NOT_IMPLEMENTED", "NOT_APPLICABLE"]
)
def test_optional_rtl_contract_uses_explicit_status_and_file_anchors(
    inventory_repo: Path, status: str
):
    inventory = _inventory(inventory_repo)
    contract = _rtl_contract(inventory_repo, status)
    if status in ("NOT_IMPLEMENTED", "NOT_APPLICABLE"):
        contract["code"] = contract["tests"] = []
    inventory["mechanisms"][0]["rtl_contract"] = contract
    _write_json(inventory_repo / "mechanism_inventory.json", inventory)
    assert verify(inventory_repo).ok


@pytest.mark.parametrize("status", ["PASS", "implemented", None])
def test_rtl_contract_rejects_undefined_status(inventory_repo: Path, status):
    inventory = _inventory(inventory_repo)
    contract = _rtl_contract(inventory_repo)
    contract["status"] = status
    inventory["mechanisms"][0]["rtl_contract"] = contract
    _assert_mechanism_failure(inventory_repo, inventory, "mechanisms.M01.rtl_contract")


@pytest.mark.parametrize("boundary", ["", " \t", None])
def test_rtl_contract_requires_a_nonempty_boundary(inventory_repo: Path, boundary):
    inventory = _inventory(inventory_repo)
    contract = _rtl_contract(inventory_repo)
    contract["boundary"] = boundary
    inventory["mechanisms"][0]["rtl_contract"] = contract
    _assert_mechanism_failure(inventory_repo, inventory, "mechanisms.M01.rtl_contract")


@pytest.mark.parametrize("status", ["DIGITAL_IMPLEMENTED", "CONTROL_ONLY"])
@pytest.mark.parametrize("field", ["code", "tests"])
def test_rtl_implemented_status_requires_code_and_test_anchors(
    inventory_repo: Path, status: str, field: str
):
    inventory = _inventory(inventory_repo)
    contract = _rtl_contract(inventory_repo, status)
    contract[field] = []
    inventory["mechanisms"][0]["rtl_contract"] = contract
    _assert_mechanism_failure(inventory_repo, inventory, "mechanisms.M01.rtl_contract")


@pytest.mark.parametrize("field", ["code", "tests"])
@pytest.mark.parametrize("reference", ["../outside.sv", "/tmp/outside.sv", "missing.sv", None])
def test_rtl_contract_paths_must_exist_inside_repository(
    inventory_repo: Path, field: str, reference
):
    inventory = _inventory(inventory_repo)
    contract = _rtl_contract(inventory_repo)
    (inventory_repo.parent / "outside.sv").write_text("module outside; endmodule\n")
    contract[field] = [reference]
    inventory["mechanisms"][0]["rtl_contract"] = contract
    _assert_mechanism_failure(inventory_repo, inventory, "mechanisms.M01.rtl_contract")


def test_rtl_contract_cannot_be_a_scalar(inventory_repo: Path):
    inventory = _inventory(inventory_repo)
    inventory["mechanisms"][0]["rtl_contract"] = "DIGITAL_IMPLEMENTED"
    _assert_mechanism_failure(inventory_repo, inventory, "mechanisms.M01.rtl_contract")
