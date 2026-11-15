"""拒绝格式或锚点错误的静态清单引用，不据此声称语义覆盖。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from adi_model.inventory_gate import verify

REQUIRED_FIXTURE_TASKS = ("FIXTURE-A", "FIXTURE-B")


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
    # 文件只用于字节身份；pages 为合成登记值，不是 PDF 内容验证。
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
                "pages": 1,
            }
        ],
        "background_sources": {
            "items": [
                {
                    "id": "B0",
                    "file": "background.pdf",
                    "sha256": hashlib.sha256(b"background fixture").hexdigest(),
                    "pages": 1,
                    "hash_registered": True,
                }
            ]
        },
    }
    inventory = {
        "coverage_obligations": {
            "required_task_ids": list(REQUIRED_FIXTURE_TASKS),
            "pending_items": [],
        },
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
                "task_refs": list(REQUIRED_FIXTURE_TASKS),
                "experiments": [{"key": "not_executed", "status": "NOT_RUN"}],
            }
        ],
    }
    reference_dir = repo / "references/assets"
    reference_dir.mkdir(parents=True)
    documents = []
    for source_id, filename, payload in (
        ("S0", "primary.pdf", b"primary fixture"),
        ("B0", "background.pdf", b"background fixture"),
    ):
        asset = reference_dir / filename
        asset.write_bytes(payload)
        documents.append(
            {
                "source_id": source_id,
                "target_relative_path": asset.relative_to(repo).as_posix(),
                "sha256": hashlib.sha256(payload).hexdigest(),
                "bytes": len(payload),
                "pages": 1,
            }
        )
    _write_json(
        repo / "references/source_manifest.json",
        {
            "schema_version": 1,
            "documents": documents,
            "documents_count": 2,
            "pages_as_file_inventory": 2,
        },
    )
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
    assert not result.static_refs_pass
    assert any(record.level == "FAIL" and record.check == check for record in result.records)
    assert not any(record.check == "mechanisms.M01.static_refs" for record in result.records)


@pytest.mark.parametrize("symbol", ["TestExact", "test_top_level", "test_async"])
def test_exact_top_level_class_or_function_is_a_static_anchor(inventory_repo: Path, symbol: str):
    inventory = _inventory(inventory_repo)
    inventory["mechanisms"][0]["tests"] = [f"tests/test_symbols.py::{symbol}"]
    _write_json(inventory_repo / "mechanism_inventory.json", inventory)
    result = verify(inventory_repo)
    assert result.static_refs_pass
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
    assert verify(inventory_repo).static_refs_pass


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
    assert not result.static_refs_pass
    assert any(
        "无法静态解析" in record.detail for record in result.records if record.level == "FAIL"
    )


def test_registered_background_source_id_can_be_referenced(inventory_repo: Path):
    inventory = _inventory(inventory_repo)
    inventory["mechanisms"][0]["sources"][0]["id"] = "B0"
    _write_json(inventory_repo / "mechanism_inventory.json", inventory)
    result = verify(inventory_repo)
    assert result.static_refs_pass
    assert any(
        record.check == "sources.B0.sha256" and record.level == "PASS" for record in result.records
    )


def test_legacy_unregistered_background_source_is_explicitly_blocked(inventory_repo: Path):
    manifest = _source_manifest(inventory_repo)
    manifest["background_sources"]["items"] = [{"id": "B0", "hash_registered": False}]
    _write_json(inventory_repo / "sources_manifest.json", manifest)
    result = verify(inventory_repo)
    assert result.static_refs_pass
    background = [record for record in result.records if record.check.startswith("sources.B0.")]
    assert len(background) == 1 and background[0].level == "BLOCKED"
    assert "NOT_RUN" in background[0].detail


def test_background_source_hash_mismatch_fails(inventory_repo: Path):
    manifest = _source_manifest(inventory_repo)
    manifest["background_sources"]["items"][0]["sha256"] = "0" * 64
    _write_json(inventory_repo / "sources_manifest.json", manifest)
    result = verify(inventory_repo)
    assert not result.static_refs_pass
    assert any(
        record.check == "sources.B0.sha256" and record.level == "FAIL" for record in result.records
    )


def test_missing_external_background_source_uses_verified_repository_identity(inventory_repo: Path):
    manifest = _source_manifest(inventory_repo)
    manifest["background_sources"]["items"][0]["file"] = "missing.pdf"
    _write_json(inventory_repo / "sources_manifest.json", manifest)
    result = verify(inventory_repo)
    assert result.static_refs_pass
    assert any(
        record.check == "sources.B0.sha256" and record.level == "PASS" and "随库" in record.detail
        for record in result.records
    )


def test_background_and_primary_source_ids_must_be_unique(inventory_repo: Path):
    manifest = _source_manifest(inventory_repo)
    manifest["background_sources"]["items"][0]["id"] = "S0"
    _write_json(inventory_repo / "sources_manifest.json", manifest)
    result = verify(inventory_repo)
    assert not result.static_refs_pass
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
    assert verify(inventory_repo).static_refs_pass


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


def test_static_reference_success_does_not_execute_or_approve_not_run_experiment(inventory_repo):
    result = verify(inventory_repo)
    assert result.static_refs_pass
    assert not result.required_states_ready
    blocked = [r for r in result.records if r.scope == "REGISTERED_REQUIRED_STATE"]
    assert blocked and all(r.level == "BLOCKED" for r in blocked)


@pytest.mark.parametrize("tasks", [None, [], ["A", "A"], [True], "FIXTURE-A"])
def test_required_ids_are_explicit_unique_strings_not_old_global_defaults(inventory_repo, tasks):
    inventory = _inventory(inventory_repo)
    inventory["coverage_obligations"]["required_task_ids"] = tasks
    _write_json(inventory_repo / "mechanism_inventory.json", inventory)
    result = verify(inventory_repo)
    assert not result.static_refs_pass
    assert any(
        r.check == "coverage.required_task_ids" and r.level == "FAIL" for r in result.records
    )


def test_missing_coverage_schema_is_not_filled_with_retired_task_list(inventory_repo):
    inventory = _inventory(inventory_repo)
    inventory.pop("coverage_obligations")
    _write_json(inventory_repo / "mechanism_inventory.json", inventory)
    assert not verify(inventory_repo).static_refs_pass


def test_missing_required_state_is_reported_separately_from_valid_static_refs(inventory_repo):
    inventory = _inventory(inventory_repo)
    inventory["mechanisms"][0]["experiments"] = []
    _write_json(inventory_repo / "mechanism_inventory.json", inventory)
    result = verify(inventory_repo)
    assert result.static_refs_pass
    assert not result.required_states_ready


def _references(repo):
    return json.loads((repo / "references/source_manifest.json").read_text())


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_manifest",
        "empty",
        "duplicate_id",
        "count",
        "total_pages",
        "bool_pages",
        "bool_size",
        "bad_hash",
    ],
)
def test_reference_assets_have_explicit_complete_identity(inventory_repo, mutation):
    path = inventory_repo / "references/source_manifest.json"
    if mutation == "missing_manifest":
        path.unlink()
    else:
        data = _references(inventory_repo)
        if mutation == "empty":
            data["documents"] = []
        elif mutation == "duplicate_id":
            data["documents"][1]["source_id"] = "S0"
        elif mutation == "count":
            data["documents_count"] = 3
        elif mutation == "total_pages":
            data["pages_as_file_inventory"] = 99
        elif mutation == "bool_pages":
            data["documents"][0]["pages"] = True
        elif mutation == "bool_size":
            data["documents"][0]["bytes"] = True
        else:
            data["documents"][0]["sha256"] = "0" * 64
        _write_json(path, data)
    result = verify(inventory_repo)
    assert not result.static_refs_pass
    assert any(r.check.startswith("references.") and r.level == "FAIL" for r in result.records)


@pytest.mark.parametrize("relative", ["../outside.pdf", "/tmp/outside.pdf", "src/implemented.py"])
def test_reference_asset_paths_do_not_escape_or_point_to_non_reference_files(
    inventory_repo, relative
):
    data = _references(inventory_repo)
    data["documents"][0]["target_relative_path"] = relative
    _write_json(inventory_repo / "references/source_manifest.json", data)
    assert not verify(inventory_repo).static_refs_pass


def test_bad_external_source_is_not_hidden_by_a_good_repository_copy(inventory_repo, monkeypatch):
    external = inventory_repo.parent / "bad_external"
    external.mkdir()
    (external / "primary.pdf").write_bytes(b"wrong external contents")
    (external / "background.pdf").write_bytes(b"background fixture")
    monkeypatch.setenv("ADC_SOURCES_DIR", str(external))
    result = verify(inventory_repo)
    assert not result.static_refs_pass
    assert any(r.check == "sources.S0.sha256" and r.level == "FAIL" for r in result.records)


def test_same_bytes_can_supply_a_distinct_alias_without_reassigning_the_source_uid(inventory_repo):
    data = _references(inventory_repo)
    data["documents"][0]["source_id"] = "REPOSITORY_ALIAS_S0"
    _write_json(inventory_repo / "references/source_manifest.json", data)
    manifest = _source_manifest(inventory_repo)
    manifest["sources"][0]["file"] = "absent_external.pdf"
    _write_json(inventory_repo / "sources_manifest.json", manifest)
    result = verify(inventory_repo)
    assert result.static_refs_pass
    assert {"S0", "REPOSITORY_ALIAS_S0"} <= result.registered_source_ids
    assert any(r.check == "sources.S0.sha256" and r.level == "PASS" for r in result.records)


def test_missing_external_and_repository_source_remain_visible(inventory_repo):
    data = _references(inventory_repo)
    data["documents"] = [data["documents"][0]]
    data["documents_count"] = 1
    data["pages_as_file_inventory"] = 1
    _write_json(inventory_repo / "references/source_manifest.json", data)
    manifest = _source_manifest(inventory_repo)
    manifest["background_sources"]["items"][0]["file"] = "missing_everywhere.pdf"
    _write_json(inventory_repo / "sources_manifest.json", manifest)
    result = verify(inventory_repo)
    assert result.static_refs_pass
    assert any(r.check == "sources.B0.file" and r.level == "BLOCKED" for r in result.records)
