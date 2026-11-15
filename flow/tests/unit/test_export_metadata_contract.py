"""检查实际输入字节与完整 calobs-v2 接口的一致绑定。"""

from __future__ import annotations

import json
import re

import pytest
from _rtl_test_support import BUNDLE_FILES, blob, copy_inputs, load_tool, read_json, sha, write_json


@pytest.fixture
def inputs(tmp_path):
    return copy_inputs(tmp_path)


@pytest.fixture
def exporter(monkeypatch):
    return load_tool("export_rtl_params", monkeypatch)


def test_manifest_identifies_every_input_and_artifact_byte(exporter, inputs):
    files = exporter.build(inputs.config, inputs.format, inputs.binding)
    manifest = json.loads(files["rtl_input_manifest.json"])
    assert manifest["schema_version"] == 1
    assert manifest["status"] == "EXPLICIT_RTL_INPUTS_GENERATED"
    assert manifest["scope"] == "DIGITAL_BINDING_NOT_ANALOG_OR_TIMING_SIGNOFF"
    assert set(manifest["artifact_sha256"]) == BUNDLE_FILES - {"rtl_input_manifest.json"}
    assert manifest["artifact_sha256"] == {
        name: sha(content) for name, content in files.items() if name != "rtl_input_manifest.json"
    }
    assert manifest["input_sha256"] == {
        "config_json": sha(inputs.config.read_bytes()),
        "format_json": sha(inputs.format.read_bytes()),
        "binding_json": sha(inputs.binding.read_bytes()),
        "source_sv": sha(inputs.source.read_bytes()),
        "acquisition_sv": sha(inputs.acquisition.read_bytes()),
    }
    assert all(re.fullmatch(r"[0-9a-f]{64}", h) for h in manifest["artifact_sha256"].values())
    for name, path in (
        ("config.json", inputs.config),
        ("format.json", inputs.format),
        ("binding.json", inputs.binding),
        ("source_control_binding.sv", inputs.source),
        ("source_acquisition_binding.sv", inputs.acquisition),
    ):
        assert blob(files[name]) == path.read_bytes()


def test_calobs_v2_has_one_order_and_consistent_bit_count(exporter, inputs):
    files = exporter.build(inputs.config, inputs.format, inputs.binding)
    packet = json.loads(files["packet_schema.json"])
    manifest = json.loads(files["rtl_input_manifest.json"])
    ordered = [
        ("sample_id", 32),
        ("coarse", 9),
        ("state", 3),
        ("control_word", 2),
        ("adc2_code", 6),
        ("known_common_q", 64),
        ("slice_ids", 40),
        ("main_masks", 16),
        ("sub_masks", 8),
        ("analog_bad", 1),
    ]
    assert packet["schema_version"] == 2
    assert packet["field_order_msb_to_lsb"] == [name for name, _ in ordered]
    assert packet["field_widths"] == dict(ordered)
    assert packet["packet_bits"] == sum(width for _, width in ordered)
    assert packet["packet_bits"] == manifest["parameters"]["CAL_OBS_BITS"]
    assert packet["packed_index_order"] == "slot0_lsb_unit0_lsb"
    assert packet["payload_encoding"] == "hex_msb_first"
    assert packet["connection_binding_required"] is True
    assert packet["input_sha256"] == manifest["input_sha256"]
    assert packet["source_binding_sha256"] == sha(inputs.binding.read_bytes())
    assert packet["fixed_point_format"] == read_json(inputs.format)
    assert packet["geometry"] == read_json(inputs.binding)["geometry"]
    assert "COMPONENT_SYNTHETIC_DIGITAL" in packet["scope"]


def test_common_contribution_is_not_relabelled_as_source_dither(exporter, inputs):
    manifest = json.loads(
        exporter.build(inputs.config, inputs.format, inputs.binding)["rtl_input_manifest.json"]
    )
    assert manifest["source_dither"] == read_json(inputs.binding)["dither_contract"]
    assert manifest["source_dither"]["kind"] == "none"
    assert manifest["parameters"]["SOURCE_DITHER_REQUEST"] == 0
    assert manifest["fold"] == "a=1; term_q=known_common_q+(1-2*actual_mask)*2^V_FRAC per unit"


def test_pool_shuffle_and_dem_request_are_distinct_inputs(exporter, inputs):
    cfg = read_json(inputs.config)
    cfg["dem_enable"] = False
    write_json(inputs.config, cfg)
    manifest = json.loads(
        exporter.build(inputs.config, inputs.format, inputs.binding)["rtl_input_manifest.json"]
    )
    assert manifest["parameters"]["SOURCE_DEM_REQUEST"] == 0
    assert manifest["parameters"]["POOL_SHUFFLE_ENABLE"] == 1
    cfg["dem_enable"] = True
    write_json(inputs.config, cfg)
    binding = read_json(inputs.binding)
    binding["scheduler"]["shuffle_enable"] = False
    write_json(inputs.binding, binding)
    manifest = json.loads(
        exporter.build(inputs.config, inputs.format, inputs.binding)["rtl_input_manifest.json"]
    )
    assert manifest["parameters"]["SOURCE_DEM_REQUEST"] == 1
    assert manifest["parameters"]["POOL_SHUFFLE_ENABLE"] == 0


def test_unused_electrical_parameters_stay_unknown(exporter, inputs):
    manifest = json.loads(
        exporter.build(inputs.config, inputs.format, inputs.binding)["rtl_input_manifest.json"]
    )
    for name in ("c_sadc", "ra_out_noise_rms", "temperature_k", "ra_v_clip", "sadc_thresholds_v"):
        assert manifest["config"][name] is None
    assert manifest["format"] == read_json(inputs.format)
    assert manifest["calibration_spec"]["adc2_endpoint_policy"] == "closed"


def test_input_whitespace_is_preserved_and_changes_identity(exporter, inputs):
    first = exporter.build(inputs.config, inputs.format, inputs.binding)
    inputs.config.write_text(
        json.dumps(read_json(inputs.config), indent=4) + "\n", encoding="utf-8"
    )
    second = exporter.build(inputs.config, inputs.format, inputs.binding)
    assert first["rtl_params.vh"] == second["rtl_params.vh"]
    assert first["config.json"] != second["config.json"]
    assert (
        json.loads(first["rtl_input_manifest.json"])["input_sha256"]
        != json.loads(second["rtl_input_manifest.json"])["input_sha256"]
    )


@pytest.mark.parametrize(
    "name",
    [
        "schema_version",
        "geometry",
        "timing",
        "scheduler",
        "source_control",
        "acquisition_control",
        "dither_contract",
        "reconstruction_fold",
        "provenance",
    ],
)
def test_binding_evidence_is_required(exporter, inputs, name):
    binding = read_json(inputs.binding)
    del binding[name]
    write_json(inputs.binding, binding)
    with pytest.raises(ValueError):
        exporter.build(inputs.config, inputs.format, inputs.binding)


@pytest.mark.parametrize("row_kind", ["main", "sub"])
def test_all_signal_fold_needs_actual_per_unit_connections(exporter, inputs, row_kind):
    binding = read_json(inputs.binding)
    binding["reconstruction_fold"][f"sampling_{row_kind}"][0][0] = 1
    write_json(inputs.binding, binding)
    with pytest.raises(ValueError, match="UNSUPPORTED"):
        exporter.build(inputs.config, inputs.format, inputs.binding)


def test_binding_digest_is_checked_against_actual_sv_bytes(exporter, inputs):
    inputs.source.write_text(inputs.source.read_text() + "// changed binding\n", encoding="utf-8")
    with pytest.raises(ValueError, match="digest"):
        exporter.build(inputs.config, inputs.format, inputs.binding)


@pytest.mark.parametrize("input_name", ["config", "format", "binding"])
def test_duplicate_and_nonfinite_json_is_not_normalized(exporter, inputs, input_name):
    path = getattr(inputs, input_name)
    original = path.read_bytes()
    path.write_text('{"duplicated": 1, "duplicated": 2}', encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate"):
        exporter.build(inputs.config, inputs.format, inputs.binding)
    path.write_text('{"invalid": NaN}', encoding="utf-8")
    with pytest.raises(ValueError, match="nonfinite"):
        exporter.build(inputs.config, inputs.format, inputs.binding)
    path.write_bytes(original)


@pytest.mark.parametrize(
    "field",
    [
        "kind",
        "sv_module_name",
        "sv_path",
        "sha256",
        "voltage_unit",
        "sadc_input_shift",
        "rdac_input_shift",
        "connection_contract",
    ],
)
def test_both_actual_acquisition_ports_need_complete_declarations(exporter, inputs, field):
    binding = read_json(inputs.binding)
    binding["acquisition_control"].pop(field)
    write_json(inputs.binding, binding)
    with pytest.raises(ValueError):
        exporter.build(inputs.config, inputs.format, inputs.binding)


def test_acquisition_source_digest_is_checked_against_its_own_actual_file(exporter, inputs):
    inputs.acquisition.write_bytes(inputs.acquisition.read_bytes() + b"// changed acquisition\n")
    with pytest.raises(ValueError, match="digest"):
        exporter.build(inputs.config, inputs.format, inputs.binding)


def test_each_sv_source_must_declare_its_one_correctly_named_module(exporter, inputs):
    binding = read_json(inputs.binding)
    payload = b"module source_control_binding; endmodule\n"
    inputs.acquisition.write_bytes(payload)
    binding["acquisition_control"]["sha256"] = sha(payload)
    write_json(inputs.binding, binding)
    with pytest.raises(ValueError, match="module"):
        exporter.build(inputs.config, inputs.format, inputs.binding)


def test_none_dither_binding_cannot_accept_a_source_dither_request(exporter, inputs):
    cfg = read_json(inputs.config)
    cfg["dither_enable"] = True
    write_json(inputs.config, cfg)
    with pytest.raises(ValueError, match="dither"):
        exporter.build(inputs.config, inputs.format, inputs.binding)
