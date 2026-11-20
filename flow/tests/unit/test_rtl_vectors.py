"""检查显式重构投影，不自动运行 ADC 或生成旧参考向量。"""

from __future__ import annotations

import json
from fractions import Fraction
from pathlib import Path

import numpy as np
import pytest
from _rtl_test_support import (
    copy_inputs,
    input_args,
    invoke,
    load_tool,
    read_json,
    sha,
    snapshot_inputs,
    write_json,
)


@pytest.fixture
def inputs(tmp_path):
    return copy_inputs(tmp_path)


@pytest.fixture
def exporter(monkeypatch):
    return load_tool("export_rtl_vectors", monkeypatch)


@pytest.fixture
def observations(inputs):
    rows = []
    for index, code in enumerate((0, 63, 32, 31, 1, 62)):
        ids = [(index * 3 + slot * 2) % 18 for slot in range(8)]
        if index == 0:
            main, sub = [[0, 0] for _ in range(8)], [[0] for _ in range(8)]
        elif index == 1:
            main, sub = [[1, 1] for _ in range(8)], [[1] for _ in range(8)]
        else:
            main = [[(slot + index) % 2, (slot + index + 1) % 2] for slot in range(8)]
            sub = [[(slot + index) % 2] for slot in range(8)]
        common = (0, 0, -(1 << 20), 1 << 20, -256, 256)[index]
        masks = np.concatenate((main, sub), axis=1)
        terms = [[3.0 * (common / (1 << 32) + 1 - 2 * int(mask)) for mask in row] for row in masks]
        rows.append(
            {
                "sample_id": (0, 1, 37, 1 << 31, (1 << 32) - 2, (1 << 32) - 1)[index],
                "adc2_code": code,
                "state": index,
                "control_word": index % 4,
                "known_common_q": common,
                "slice_ids": ids,
                "main_masks": main,
                "sub_masks": sub,
                "signal_mask": [[1, 1, 1] for _ in range(8)],
                "known_voltage_term_v": terms,
                "overflow": index == 3,
            }
        )
    path = inputs.folder / "observations.json"
    write_json(
        path,
        {
            "schema_version": 2,
            "scope": "COMPONENT_SYNTHETIC_DIGITAL; explicit arithmetic rows, no mapping claim",
            "static_charge_domain": True,
            "samples": rows,
        },
    )
    return path


def _build(exporter, inputs, observations):
    return exporter.build(
        inputs.config, inputs.format, inputs.binding, inputs.registers, observations
    )


def _rows(files):
    lines = files["reconstruction_inputs.hex"].splitlines()
    assert lines[0].startswith("# ")
    return [line.split() for line in lines[1:]]


def _half_even(ratio):
    quotient, remainder = divmod(ratio.numerator, ratio.denominator)
    return quotient + int(
        2 * remainder > ratio.denominator or (2 * remainder == ratio.denominator and quotient % 2)
    )


def _independent_word(row, registers):
    """从显式逐单元数据和精确比例独立推导整数输出。"""
    fmt, spec = registers["format"], registers["spec"]
    vs, ws = 1 << fmt["voltage_fraction_bits"], 1 << fmt["weight_fraction_bits"]
    fine = registers["adc2_min_q"] + _half_even(
        Fraction(
            (2 * row["adc2_code"] + 1) * (registers["adc2_max_q"] - registers["adc2_min_q"]),
            1 << (spec["adc2_n_bits"] + 1),
        )
    )
    gain = 0
    products = []
    for slot, identity in enumerate(row["slice_ids"]):
        for unit, weight in enumerate(registers["weights_q"][identity]):
            gain += weight * row["signal_mask"][slot][unit]
            normalized = Fraction(row["known_voltage_term_v"][slot][unit]) / Fraction(spec["v_fs"])
            products.append(weight * _half_even(normalized * vs))
    fine_product = (fine - registers["offset_q"]) * ws
    known_sum = sum(products)
    numerator = fine_product - known_sum
    count = 1 << fmt["output_bits"]
    shifted, denominator = (numerator + gain * vs) * count, 2 * gain * vs
    limit = 1 << (fmt["accumulator_bits"] - 1)
    assert all(
        -limit <= value < limit
        for value in [*products, fine_product, known_sum, numerator, shifted, denominator]
    )
    word = shifted // denominator
    return min(max(word, 0), count - 1), int(word < 0), int(word >= count), int(row["overflow"])


def test_projection_is_deterministic_and_checks_each_physical_unit(exporter, inputs, observations):
    first = _build(exporter, inputs, observations)
    assert first == _build(exporter, inputs, observations)
    assert set(first) == {"reconstruction_inputs.hex", "reconstruction_manifest.json"}
    manifest = json.loads(first["reconstruction_manifest.json"])
    assert manifest["sample_count"] == 6
    assert manifest["per_unit_fold_equalities"] == 6 * 8 * 3
    assert manifest["mapping_or_silicon_equivalence"] == "NOT_EVALUATED"
    assert manifest["scope"] == "COMPONENT_RECONSTRUCTION_PROJECTION"


def test_every_packed_field_and_integer_word_is_recomputed_independently(
    exporter, inputs, observations
):
    files = _build(exporter, inputs, observations)
    actual = _rows(files)
    raw = read_json(observations)["samples"]
    registers = read_json(inputs.registers)
    for fields, sample in zip(actual, raw, strict=True):
        assert len(fields) == 10
        assert int(fields[0], 16) == sample["sample_id"]
        assert int(fields[1], 16) == sample["adc2_code"]
        assert int(fields[2], 16) == sample["known_common_q"] % (1 << 64)
        assert int(fields[3], 16) == sum(
            identity << (5 * slot) for slot, identity in enumerate(sample["slice_ids"])
        )
        for position, name in ((4, "main_masks"), (5, "sub_masks")):
            flattened = [bit for slot in sample[name] for bit in slot]
            assert int(fields[position], 16) == sum(
                bit << index for index, bit in enumerate(flattened)
            )
        expected = _independent_word(sample, registers)
        assert (int(fields[6], 16), *(int(value) for value in fields[7:])) == expected
    assert any(int(row[7]) for row in actual)
    assert any(int(row[8]) for row in actual)
    assert sum(int(row[9]) for row in actual) == 1


def test_manifest_binds_all_files_and_measured_counts(exporter, inputs, observations):
    files = _build(exporter, inputs, observations)
    manifest = json.loads(files["reconstruction_manifest.json"])
    rows = _rows(files)
    assert manifest["schema_version"] == 2
    assert manifest["input_sha256"] == {
        "config_json": sha(inputs.config.read_bytes()),
        "format_json": sha(inputs.format.read_bytes()),
        "binding_json": sha(inputs.binding.read_bytes()),
        "source_sv": sha(inputs.source.read_bytes()),
        "acquisition_sv": sha(inputs.acquisition.read_bytes()),
        "observations_json": sha(observations.read_bytes()),
        "registers_json": sha(inputs.registers.read_bytes()),
    }
    assert manifest["vector_sha256"] == sha(files["reconstruction_inputs.hex"])
    assert manifest["sample_count"] == len(rows)
    assert manifest["clipped_low"] == sum(int(row[7]) for row in rows)
    assert manifest["clipped_high"] == sum(int(row[8]) for row in rows)
    assert manifest["analog_bad"] == sum(int(row[9]) for row in rows)
    assert manifest["columns"] == files["reconstruction_inputs.hex"].splitlines()[0][2:].split()
    assert "slot0_lsb_unit0_lsb" in manifest["field_packing"]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("adc2_code", 64),
        ("adc2_code", -1),
        ("state", 8),
        ("control_word", 4),
        ("sample_id", 1 << 32),
        ("overflow", 1),
        ("known_common_q", True),
        ("known_common_q", (1 << 63) - (1 << 32)),
    ],
)
def test_invalid_raw_fields_are_rejected_before_projection(
    exporter, inputs, observations, field, value
):
    payload = read_json(observations)
    payload["samples"][0][field] = value
    write_json(observations, payload)
    before = snapshot_inputs(inputs)
    with pytest.raises(ValueError):
        _build(exporter, inputs, observations)
    assert snapshot_inputs(inputs) == before


@pytest.mark.parametrize(
    "defect",
    [
        "empty",
        "dynamic",
        "missing_scope",
        "old_schema",
        "unknown_field",
        "missing_masks",
        "duplicate_slice",
        "nonbinary_mask",
        "unsupported_signal",
    ],
)
def test_unbound_or_unsupported_observations_are_rejected(exporter, inputs, observations, defect):
    payload = read_json(observations)
    row = payload["samples"][0]
    if defect == "empty":
        payload["samples"] = []
    elif defect == "dynamic":
        payload["static_charge_domain"] = False
    elif defect == "missing_scope":
        del payload["scope"]
    elif defect == "old_schema":
        payload["schema_version"] = 1
    elif defect == "unknown_field":
        row["coarse_inferred_from_masks"] = 0
    elif defect == "missing_masks":
        del row["main_masks"]
    elif defect == "duplicate_slice":
        row["slice_ids"][1] = row["slice_ids"][0]
    elif defect == "nonbinary_mask":
        row["main_masks"][0][0] = 2
    else:
        row["signal_mask"][0][0] = 0
    write_json(observations, payload)
    with pytest.raises(ValueError):
        _build(exporter, inputs, observations)


def test_weighted_cancellation_cannot_hide_a_wrong_per_unit_binding(exporter, inputs, observations):
    payload = read_json(observations)
    payload["samples"][0]["known_voltage_term_v"][0][0] += 3.0 / (1 << 32)
    payload["samples"][0]["known_voltage_term_v"][0][1] -= 3.0 / (1 << 32)
    registers = read_json(inputs.registers)
    registers["weights_q"] = [[1 << 30] * 3 for _ in range(18)]
    write_json(inputs.registers, registers)
    write_json(observations, payload)
    with pytest.raises(ValueError, match="per-unit quantized voltage term"):
        _build(exporter, inputs, observations)


def test_quantized_fold_accepts_sub_half_lsb_voltage_difference(exporter, inputs, observations):
    first = _build(exporter, inputs, observations)
    payload = read_json(observations)
    payload["samples"][0]["known_voltage_term_v"][0][0] += 3.0 / (1 << 35)
    write_json(observations, payload)
    second = _build(exporter, inputs, observations)
    assert first["reconstruction_inputs.hex"] == second["reconstruction_inputs.hex"]
    assert first["reconstruction_manifest.json"] != second["reconstruction_manifest.json"]


def test_register_interface_mismatch_is_not_repaired(exporter, inputs, observations):
    registers = read_json(inputs.registers)
    registers["spec"]["adc2_endpoint_policy"] = "half_open"
    write_json(inputs.registers, registers)
    with pytest.raises(ValueError, match="does not match"):
        _build(exporter, inputs, observations)


def test_vector_write_is_atomic_and_check_is_read_only(
    exporter, inputs, observations, tmp_path, monkeypatch
):
    files = _build(exporter, inputs, observations)
    output = tmp_path / "new_vectors"
    exporter.write_bundle(files, output, check=False)
    exporter.write_bundle(files, output, check=True)
    before = {p.name: p.read_bytes() for p in output.iterdir()}
    with pytest.raises((OSError, ValueError)):
        exporter.write_bundle(files, output, check=False)
    assert {p.name: p.read_bytes() for p in output.iterdir()} == before
    target = output / "reconstruction_inputs.hex"
    target.write_bytes(target.read_bytes() + b"changed\n")
    with pytest.raises(ValueError, match="drift"):
        exporter.write_bundle(files, output, check=True)
    assert target.read_bytes().endswith(b"changed\n")
    failed = tmp_path / "failed_vectors"
    original = Path.write_text

    def fail_manifest(path, value, *args, **kwargs):
        if path.parent == failed and path.name == "reconstruction_manifest.json":
            raise OSError("synthetic vector write failure")
        return original(path, value, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", fail_manifest)
    with pytest.raises(OSError, match="synthetic vector write failure"):
        exporter.write_bundle(files, failed, check=False)
    assert not failed.exists()


@pytest.mark.parametrize("missing", ["--registers", "--observations", "--out"])
def test_projection_cli_requires_all_external_inputs(
    exporter, inputs, observations, tmp_path, monkeypatch, missing
):
    args = [
        *input_args(inputs),
        "--registers",
        str(inputs.registers),
        "--observations",
        str(observations),
        "--out",
        str(tmp_path / "never_created"),
    ]
    index = args.index(missing)
    del args[index : index + 2]
    with pytest.raises(SystemExit) as error:
        invoke(exporter, args, monkeypatch)
    assert error.value.code == 2
    assert not (tmp_path / "never_created").exists()
