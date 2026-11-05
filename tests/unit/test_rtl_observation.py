"""检查显式 calobs-v2 解析、实际选通绑定和精确整数折叠。

较宽的状态／控制字段仅用于合成解析输入，不代表 RTL 设计。
格式、参考轨、原始选通及逐单元信号系数／电压项均在夹具中显式声明。
"""

import json
from copy import deepcopy
from dataclasses import asdict, replace
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from adi_model.fixed_point import FixedPointFormat
from adi_model.rtl_observation import extract_bitfields, observations_from_rtl
from adi_model.weight_calibration import CalibrationSpec, fit_unit_weights


def _raw_json(value):
    return (json.dumps(value, sort_keys=True) + "\n").encode()


@pytest.fixture
def packet_case():
    spec = CalibrationSpec(18, 8, 2, 1, 3.0, 12, -0.5, 0.5, "uniform_midrise", "half_open")
    fmt = FixedPointFormat(30, 32, 64, 48, 96, 20)
    geometry = {
        "n_main": 2,
        "n_sub": 1,
        "n_slices": 18,
        "n_active": 8,
        "code_min": 0,
        "code_max": 511,
    }
    fold = {
        "kind": "all_signal_symmetric_common_v1",
        "sampling_main": [[0, 0] for _ in range(18)],
        "sampling_sub": [[0] for _ in range(18)],
        "reference_low_v": -3.0,
        "reference_high_v": 3.0,
        "common_input_unit": "normalized_signed_q",
        "term_quantizer": "ties_even_per_unit",
    }
    # 使用实际component的两个源身份；宽字段记录仍仅为parser数学输入，非SV轨迹。
    fixture = Path(__file__).resolve().parents[2] / "sim/fixtures/source_control_component"
    producer = json.loads((fixture / "binding.json").read_text())
    source_control = (fixture / "source_control_binding.sv").read_bytes()
    acquisition_source = (fixture / "source_acquisition_binding.sv").read_bytes()
    document = {
        "schema_version": 1,
        "geometry": geometry,
        "control_word_bits": 65,
        "state_bits": 70,
        "sample_id_bits": 32,
        "source_control": deepcopy(producer["source_control"]),
        "acquisition_control": deepcopy(producer["acquisition_control"]),
        "dither_contract": deepcopy(producer["dither_contract"]),
        "reconstruction_fold": fold,
        "provenance": "COMPONENT_SYNTHETIC_DIGITAL",
    }
    order = [
        "sample_id",
        "coarse",
        "state",
        "control_word",
        "adc2_code",
        "known_common_q",
        "slice_ids",
        "main_masks",
        "sub_masks",
        "analog_bad",
    ]
    widths = {
        "sample_id": 32,
        "coarse": 9,
        "state": 70,
        "control_word": 65,
        "adc2_code": 12,
        "known_common_q": 64,
        "slice_ids": 40,
        "main_masks": 16,
        "sub_masks": 8,
        "analog_bad": 1,
    }

    def schema_for(doc):
        raw = _raw_json(doc)
        schema = {
            "schema_version": 2,
            "connection_binding_required": True,
            "payload_encoding": "hex_msb_first",
            "packed_index_order": "slot0_lsb_unit0_lsb",
            "field_order_msb_to_lsb": order.copy(),
            "field_widths": widths.copy(),
            "packet_bits": sum(widths.values()),
            "geometry": deepcopy(doc["geometry"]),
            "fixed_point_format": asdict(fmt),
            "reconstruction_fold": deepcopy(doc["reconstruction_fold"]),
            "source_binding_sha256": sha256(raw).hexdigest(),
            "input_sha256": {
                "config_json": sha256(b'{"fixture":"parser inputs only"}\n').hexdigest(),
                "format_json": sha256(_raw_json(asdict(fmt))).hexdigest(),
                "binding_json": sha256(raw).hexdigest(),
                "source_sv": sha256(source_control).hexdigest(),
                "acquisition_sv": sha256(acquisition_source).hexdigest(),
            },
        }
        return schema, raw

    def pack(values):
        word = 0
        for name in order:
            assert 0 <= values[name] < 1 << widths[name]
            word = (word << widths[name]) | values[name]
        return word

    def row(sample_id, control, state, common, ids, main, sub):
        return {
            "sample_id": sample_id,
            "coarse": 173,
            "state": state,
            "control_word": control,
            "adc2_code": 1234,
            "known_common_q": common % (1 << 64),
            "slice_ids": sum(int(v) << (5 * i) for i, v in enumerate(ids)),
            "main_masks": sum(int(main[i, j]) << (2 * i + j) for i in range(8) for j in range(2)),
            "sub_masks": sum(int(sub[i, 0]) << i for i in range(8)),
            "analog_bad": 0,
        }

    main = np.array([[(i + j) % 2 for j in range(2)] for i in range(8)], dtype=np.int8)
    sub = np.array([[i % 2] for i in range(8)], dtype=np.int8)
    first = row(0xFFFFFFFF, (1 << 64) + 17, (1 << 69) + 23, -(1 << 28), range(8), main, sub)
    second = row(0, (1 << 64) + 19, (1 << 69) + 29, 1 << 27, range(8, 16), 1 - main, 1 - sub)
    seen = {}

    def binding(nominal, controls):
        seen.update(controls)
        masks = np.concatenate((controls["main_masks"], controls["sub_masks"]), axis=2)
        term_q = controls["known_common_q"][:, None, None] + (1 << 32) * (
            1 - 2 * masks.astype(np.int64)
        )
        return np.ones_like(masks), term_q.astype(float) * nominal.v_fs / (1 << 32)

    schema, raw = schema_for(document)
    return SimpleNamespace(
        spec=spec,
        fmt=fmt,
        schema=schema,
        raw=raw,
        document=document,
        order=order,
        widths=widths,
        schema_for=schema_for,
        pack=pack,
        rows=[first, second],
        packets=[pack(first), pack(second)],
        binding=binding,
        seen=seen,
        main=main,
        sub=sub,
    )


def _decode(case, **overrides):
    arguments = {
        "packet_schema": case.schema,
        "binding_document": case.raw,
        "connection_binding": case.binding,
        "static_charge_domain": True,
        "fixed_configuration_epoch": True,
    }
    values = overrides.pop("packets", case.packets)
    arguments.update(overrides)
    return observations_from_rtl(values, case.spec, **arguments)


def test_structural_packet_decodes_actual_masks_and_arbitrary_width_signed_fields(packet_case):
    case = packet_case
    data = _decode(case)
    np.testing.assert_array_equal(data.sample_ids, [0xFFFFFFFF, 0])
    np.testing.assert_array_equal(data.slice_ids, [np.arange(8), np.arange(8, 16)])
    np.testing.assert_array_equal(data.main_masks[0], case.main)
    np.testing.assert_array_equal(data.sub_masks[1], 1 - case.sub)
    assert int(case.seen["control_word"][0]) == case.rows[0]["control_word"]
    assert int(case.seen["state"][1]) == case.rows[1]["state"]
    np.testing.assert_array_equal(case.seen["known_common_q"], [-(1 << 28), 1 << 27])
    assert np.all(data.signal_mask == 1)
    assert not hasattr(data, "epoch_id")
    as_hex = _decode(case, packets=[format(value, "x") for value in case.packets])
    np.testing.assert_array_equal(as_hex.adc2_code, data.adc2_code)


@pytest.mark.parametrize(
    "field",
    [
        "schema_version",
        "payload_encoding",
        "packed_index_order",
        "source_binding_sha256",
        "fixed_point_format",
    ],
)
def test_missing_packet_conventions_cannot_be_guessed(packet_case, field):
    schema = deepcopy(packet_case.schema)
    schema.pop(field)
    with pytest.raises(ValueError):
        _decode(packet_case, packet_schema=schema)


@pytest.mark.parametrize(
    "mutation",
    [
        "old_version",
        "field_order",
        "packet_width",
        "coarse_width",
        "state_width",
        "control_width",
        "waive_binding",
        "format_default",
        "format_drift",
    ],
)
def test_schema_drift_is_rejected_instead_of_using_old_layout(packet_case, mutation):
    schema = deepcopy(packet_case.schema)
    if mutation == "old_version":
        schema["schema_version"] = 1
    elif mutation == "field_order":
        schema["field_order_msb_to_lsb"][:2] = reversed(schema["field_order_msb_to_lsb"][:2])
    elif mutation == "packet_width":
        schema["packet_bits"] += 1
    elif mutation in ("coarse_width", "state_width", "control_width"):
        field = {"coarse_width": "coarse", "state_width": "state", "control_width": "control_word"}[
            mutation
        ]
        schema["field_widths"][field] -= 1
        schema["packet_bits"] -= 1
    elif mutation == "waive_binding":
        schema["connection_binding_required"] = False
    elif mutation == "format_default":
        schema["fixed_point_format"].pop("output_bits")
    else:
        schema["fixed_point_format"]["voltage_fraction_bits"] = 31
    with pytest.raises(ValueError):
        _decode(packet_case, packet_schema=schema)


@pytest.mark.parametrize(
    "target", ["top_binding", "input_binding", "source_sv", "raw_whitespace", "no_original_bytes"]
)
def test_source_identities_are_bound_to_original_json_bytes(packet_case, target):
    schema = deepcopy(packet_case.schema)
    raw = packet_case.raw
    if target == "top_binding":
        schema["source_binding_sha256"] = "0" * 64
    elif target == "input_binding":
        schema["input_sha256"]["binding_json"] = "0" * 64
    elif target == "source_sv":
        schema["input_sha256"]["source_sv"] = "0" * 64
    elif target == "raw_whitespace":
        raw += b" "
    else:
        raw = None
    with pytest.raises(ValueError):
        _decode(packet_case, packet_schema=schema, binding_document=raw)


@pytest.mark.parametrize(
    "mutation",
    [
        "version",
        "bool_signal",
        "non_signal",
        "reference_sign",
        "reference_missing",
        "bool_width",
        "term_rounding",
    ],
)
def test_unsupported_electrical_fold_is_not_silently_approximated(packet_case, mutation):
    doc = deepcopy(packet_case.document)
    fold = doc["reconstruction_fold"]
    if mutation == "version":
        doc.pop("schema_version")
    elif mutation == "bool_signal":
        fold["sampling_main"][0][0] = False
    elif mutation == "non_signal":
        fold["sampling_sub"][0][0] = 1
    elif mutation == "reference_sign":
        fold["reference_low_v"] = 3.0
    elif mutation == "reference_missing":
        fold.pop("reference_high_v")
    elif mutation == "bool_width":
        doc["state_bits"] = True
    else:
        fold["term_quantizer"] = "toward_zero"
    schema, raw = packet_case.schema_for(doc)
    with pytest.raises(ValueError):
        _decode(packet_case, packet_schema=schema, binding_document=raw)


@pytest.mark.parametrize(
    "wrong", ["gain_mask", "one_voltage_lsb", "missing_output", "missing_binding"]
)
def test_binding_must_return_exactly_supported_a_and_per_unit_q_terms(packet_case, wrong):
    case = packet_case

    def binding(spec, controls):
        signal, term = case.binding(spec, controls)
        if wrong == "gain_mask":
            signal[0, 0, 0] = 0
        if wrong == "one_voltage_lsb":
            term[0, 0, 0] += spec.v_fs / (1 << 32)
        if wrong == "missing_output":
            return None
        return signal, term

    with pytest.raises(ValueError):
        _decode(case, connection_binding=None if wrong == "missing_binding" else binding)


def test_binding_exception_is_not_replaced_with_nominal_data(packet_case):
    def failure(spec, controls):
        raise RuntimeError("explicit binding failed")

    with pytest.raises(RuntimeError, match="binding failed"):
        _decode(packet_case, connection_binding=failure)


@pytest.mark.parametrize("sample_id", [0xFFFFFFFF, 1, 0xFFFFFFFE])
def test_duplicated_missing_or_reordered_samples_are_not_hidden(packet_case, sample_id):
    changed = deepcopy(packet_case.rows[1])
    changed["sample_id"] = sample_id
    with pytest.raises(ValueError):
        _decode(packet_case, packets=[packet_case.packets[0], packet_case.pack(changed)])


def test_explicit_gap_policy_and_analog_bad_are_retained(packet_case):
    row = deepcopy(packet_case.rows[1])
    row["sample_id"], row["analog_bad"] = 1, 1
    data = _decode(
        packet_case,
        packets=[packet_case.packets[0], packet_case.pack(row)],
        require_consecutive=False,
    )
    np.testing.assert_array_equal(data.sample_ids, [0xFFFFFFFF, 1])
    np.testing.assert_array_equal(data.overflow, [False, True])
    with pytest.raises(ValueError, match="clipped conversions"):
        fit_unit_weights(data, np.array([0.1, 0.2]))
    with pytest.raises(ValueError, match="static charge-domain"):
        fit_unit_weights(
            replace(data, overflow=np.zeros(2, bool), static_charge_domain=False),
            np.array([0.1, 0.2]),
        )


def test_unobserved_configuration_epoch_requires_external_control_guarantee(packet_case):
    with pytest.raises(ValueError):
        _decode(packet_case, fixed_configuration_epoch=False)


def test_extra_payload_bits_and_signed_per_unit_overflow_are_rejected(packet_case):
    with pytest.raises(ValueError):
        _decode(packet_case, packets=[1 << packet_case.schema["packet_bits"]])
    row = deepcopy(packet_case.rows[0])
    row["known_common_q"] = (1 << 63) - 1
    with pytest.raises(ValueError):
        _decode(packet_case, packets=[packet_case.pack(row)])


def test_generic_small_bitfield_extraction_is_not_a_target_adc_width():
    assert extract_bitfields(
        0b101101, field_order_msb_to_lsb=["a", "b"], field_widths={"a": 2, "b": 4}
    ) == {"a": 2, "b": 13}
    with pytest.raises(ValueError):
        extract_bitfields(0, field_order_msb_to_lsb=["a"], field_widths={"a": 0})
    with pytest.raises(ValueError):
        extract_bitfields(4, field_order_msb_to_lsb=["a"], field_widths={"a": 2})


@pytest.mark.parametrize("bounds", [(None, None), (-1, 512)])
def test_nominal_code_domain_can_be_explicitly_absent_or_independent(packet_case, bounds):
    document = deepcopy(packet_case.document)
    document["geometry"].update(code_min=bounds[0], code_max=bounds[1])
    schema, raw = packet_case.schema_for(document)
    original = deepcopy(schema)
    data = _decode(packet_case, packet_schema=schema, binding_document=raw)
    np.testing.assert_array_equal(data.sample_ids, [0xFFFFFFFF, 0])
    assert schema == original
    assert _raw_json(document) == raw
    assert data.spec == packet_case.spec


@pytest.mark.parametrize(
    "bounds",
    [(None, 511), (0, None), (True, 511), (0, False), (5, 4), (0.0, 511), (-1, 512.0)],
)
def test_nominal_code_domain_never_fills_missing_or_coerces_types(packet_case, bounds):
    document = deepcopy(packet_case.document)
    document["geometry"].update(code_min=bounds[0], code_max=bounds[1])
    schema, raw = packet_case.schema_for(document)
    with pytest.raises(ValueError):
        _decode(packet_case, packet_schema=schema, binding_document=raw)


@pytest.mark.parametrize("field", ["code_min", "code_max"])
def test_nominal_code_domain_keys_remain_explicit_even_for_unknowns(packet_case, field):
    document = deepcopy(packet_case.document)
    document["geometry"].update(code_min=None, code_max=None)
    document["geometry"].pop(field)
    schema, raw = packet_case.schema_for(document)
    with pytest.raises(ValueError):
        _decode(packet_case, packet_schema=schema, binding_document=raw)


def test_schema_and_binding_geometry_do_not_equate_boolean_with_integer(packet_case):
    schema = deepcopy(packet_case.schema)
    schema["geometry"]["code_min"] = False
    with pytest.raises(ValueError):
        _decode(packet_case, packet_schema=schema)


@pytest.mark.parametrize(
    "missing",
    [
        "acquisition_control",
        "dither_contract",
        "acquisition_sv",
        "acquisition_path",
        "acquisition_hash",
        "sadc_node",
        "rdac_node",
        "voltage_unit",
    ],
)
def test_old_or_incomplete_acquisition_abi_is_rejected_without_zero_shift_defaults(
    packet_case, missing
):
    document = deepcopy(packet_case.document)
    schema, raw = packet_case.schema_for(document)
    if missing in ("acquisition_control", "dither_contract"):
        document.pop(missing)
    elif missing == "acquisition_sv":
        schema["input_sha256"].pop("acquisition_sv")
    else:
        key = {
            "acquisition_path": "sv_path",
            "acquisition_hash": "sha256",
            "sadc_node": "sadc_input_shift",
            "rdac_node": "rdac_input_shift",
            "voltage_unit": "voltage_unit",
        }[missing]
        document["acquisition_control"].pop(key)
    if missing != "acquisition_sv":
        schema, raw = packet_case.schema_for(document)
    with pytest.raises(ValueError):
        _decode(packet_case, packet_schema=schema, binding_document=raw)


@pytest.mark.parametrize(
    "field,value",
    [
        ("kind", "constant_zero"),
        ("sv_module_name", "source_control_binding"),
        ("sv_path", ""),
        ("sha256", "0" * 64),
        ("voltage_unit", "V"),
        ("sadc_input_shift", "add_to_all_rdac_sampled_units"),
        ("rdac_input_shift", "add_to_sadc_sampled_input"),
        ("connection_contract", " "),
    ],
)
def test_acquisition_identity_nodes_and_units_are_exact_declarations(packet_case, field, value):
    document = deepcopy(packet_case.document)
    document["acquisition_control"][field] = value
    schema, raw = packet_case.schema_for(document)
    with pytest.raises(ValueError):
        _decode(packet_case, packet_schema=schema, binding_document=raw)


@pytest.mark.parametrize("contract", [{"kind": "off"}, {"kind": "none", "provenance": " "}])
def test_dither_contract_requires_an_explicit_supported_role_and_provenance(packet_case, contract):
    document = deepcopy(packet_case.document)
    document["dither_contract"] = contract
    schema, raw = packet_case.schema_for(document)
    with pytest.raises(ValueError):
        _decode(packet_case, packet_schema=schema, binding_document=raw)
