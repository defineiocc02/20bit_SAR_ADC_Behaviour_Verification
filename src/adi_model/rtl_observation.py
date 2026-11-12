"""用显式且哈希绑定的连接模型解码唯一 calobs v2 ABI。

报文记录转换掩码、控制字/状态、原始码及样本 ID，不包含配置 epoch、采集电压或电容真值。
适配器仅支持已声明的全 SIGNAL、对称参考折叠，不推断另一电路，也不从历史报文恢复缺失元数据。
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict
from numbers import Real
from types import MappingProxyType
from typing import Any

import numpy as np

from adi_model._arrays import readonly as _readonly
from adi_model.contracts.fixed_point import FixedPointFormat
from adi_model.calibration.weight_calibration import CalibrationSpec, DigitalObservation

ConnectionBinding = Callable[
    [CalibrationSpec, Mapping[str, np.ndarray]], tuple[np.ndarray, np.ndarray]
]
_FIELD_ORDER = (
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
)
_FOLD_KIND = "all_signal_symmetric_common_v1"


def extract_bitfields(
    value: int, *, field_order_msb_to_lsb: Sequence[str], field_widths: Mapping[str, int]
) -> dict[str, int]:
    """按显式顺序提取无符号字，不推断字节或字段顺序。

    这是通用整数数学。采用小型合成字段列表，不代表建立了受支持的 ADC 观测 ABI。
    """
    order = tuple(field_order_msb_to_lsb)
    if (
        not order
        or any(not isinstance(name, str) or not name for name in order)
        or len(set(order)) != len(order)
        or set(field_widths) != set(order)
    ):
        raise ValueError("bitfield names/order must be unique and match the explicit width map")
    for name in order:
        if type(field_widths[name]) is not int or field_widths[name] <= 0:
            raise ValueError(f"bitfield {name} requires an explicit positive width [bit]")
    total = sum(field_widths.values())
    if type(value) is not int or value < 0 or value.bit_length() > total:
        raise ValueError("unsigned packet value does not fit its explicitly declared widths")
    result = {}
    shift = total
    for name in order:
        shift -= field_widths[name]
        result[name] = (value >> shift) & ((1 << field_widths[name]) - 1)
    return result


def _required(mapping: Mapping, name: str, context: str):
    if name not in mapping:
        raise ValueError(f"missing {context}.{name} in the explicit calobs v2 contract")
    return mapping[name]


def _mapping(value, name: str) -> Mapping:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be an explicit JSON object")
    return value


def _digest(value, name: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-fA-F]{64}", value) is None:
        raise ValueError(f"{name} must be an explicit SHA256 digest")
    return value.lower()


def _json_object(pairs):
    result = {}
    for name, value in pairs:
        if name in result:
            raise ValueError(f"duplicate key {name!r} in binding JSON")
        result[name] = value
    return result


def _required_text(mapping: Mapping, name: str, context: str) -> str:
    value = _required(mapping, name, context)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{context}.{name} must be a nonempty declared string")
    return value


def _validate_acquisition_contract(
    binding: Mapping,
    source_control: Mapping,
    inputs: Mapping,
    geometry: Mapping,
    widths: Mapping,
    spec: CalibrationSpec,
) -> None:
    """校验唯一双文件 ABI 的声明与摘要，不执行或解析 SV 原件。"""
    if (
        _required(source_control, "kind", "source_control") != "sv_module"
        or _required(source_control, "sv_module_name", "source_control") != "source_control_binding"
    ):
        raise ValueError("source_control must explicitly declare source_control_binding")
    for name in ("sv_path", "connection_contract"):
        _required_text(source_control, name, "source_control")
    _required_text(binding, "provenance", "binding_document")
    acquisition = _mapping(
        _required(binding, "acquisition_control", "binding_document"), "acquisition_control"
    )
    expected = {
        "kind": "sv_module",
        "sv_module_name": "source_acquisition_binding",
        "voltage_unit": "normalized_signed_q",
        "sadc_input_shift": "add_to_sadc_sampled_input",
        "rdac_input_shift": "add_to_all_rdac_sampled_units",
    }
    for name, value in expected.items():
        if _required(acquisition, name, "acquisition_control") != value:
            raise ValueError(f"UNSUPPORTED acquisition_control.{name} declaration")
    if set(acquisition) != {*expected, "sv_path", "sha256", "connection_contract"}:
        raise ValueError("acquisition_control must supply exactly the declared dual-node fields")
    for name in ("sv_path", "connection_contract"):
        _required_text(acquisition, name, "acquisition_control")
    if (
        _digest(
            _required(acquisition, "sha256", "acquisition_control"), "acquisition_control.sha256"
        )
        != inputs["acquisition_sv"].lower()
    ):
        raise ValueError("acquisition-control hash differs from input_sha256.acquisition_sv")
    dither = _mapping(_required(binding, "dither_contract", "binding_document"), "dither_contract")
    _required_text(dither, "provenance", "dither_contract")
    kind = _required(dither, "kind", "dither_contract")
    if kind == "none":
        if set(dither) != {"kind", "provenance"}:
            raise ValueError("unsupported fields in dither_contract kind=none")
    elif kind == "patent_fig19_ideal_ports_v1":
        if (
            set(dither) != {"kind", "provenance", "equivalent_polarity"}
            or dither["equivalent_polarity"] != "ASSUMED_NEGATIVE_SAMPLING_SHIFTS"
            or (widths["control_word"], widths["state"]) != (6, 1)
            or (geometry["n_main"], geometry["n_sub"], geometry["code_min"], geometry["code_max"])
            != (63, 8, 0, 511)
            or spec.v_fs != 3.0
        ):
            raise ValueError("UNSUPPORTED: Fig19 metadata differs from the explicit static profile")
    else:
        raise ValueError("UNSUPPORTED dither_contract kind; no legacy or implicit binding fallback")


def _validate_fold(fold: Mapping, geometry: Mapping, spec: CalibrationSpec) -> None:
    if _required(fold, "kind", "reconstruction_fold") != _FOLD_KIND:
        raise ValueError("UNSUPPORTED: the packet connection fold is not implemented")
    if _required(fold, "common_input_unit", "reconstruction_fold") != "normalized_signed_q":
        raise ValueError("the known-common input must explicitly use normalized_signed_q")
    if _required(fold, "term_quantizer", "reconstruction_fold") != "ties_even_per_unit":
        raise ValueError("UNSUPPORTED: voltage terms must explicitly select ties_even_per_unit")
    for name, expected in (("reference_low_v", -spec.v_fs), ("reference_high_v", spec.v_fs)):
        voltage = _required(fold, name, "reconstruction_fold")
        if (
            not isinstance(voltage, Real)
            or isinstance(voltage, bool)
            or not math.isfinite(voltage)
            or voltage != expected
        ):
            raise ValueError(
                f"UNSUPPORTED: {name} must explicitly equal its declared symmetric Vfs rail [V]"
            )
    for key, units in (("sampling_main", geometry["n_main"]), ("sampling_sub", geometry["n_sub"])):
        states = _required(fold, key, "reconstruction_fold")
        if (
            not isinstance(states, list)
            or len(states) != geometry["n_slices"]
            or any(not isinstance(row, list) or len(row) != units for row in states)
            or any(type(state) is not int or state != 0 for row in states for state in row)
        ):
            raise ValueError(
                f"UNSUPPORTED: {key} must explicitly declare SIGNAL=0 for every physical unit"
            )


def _validate_schema(
    schema: Mapping, binding_document: bytes, spec: CalibrationSpec
) -> tuple[Mapping, FixedPointFormat]:
    if (
        type(_required(schema, "schema_version", "packet_schema")) is not int
        or schema["schema_version"] != 2
    ):
        raise ValueError("only explicit calobs schema_version=2 is supported")
    if _required(schema, "payload_encoding", "packet_schema") != "hex_msb_first":
        raise ValueError("calobs v2 requires explicit hex_msb_first payload encoding")
    if _required(schema, "connection_binding_required", "packet_schema") is not True:
        raise ValueError("calobs v2 must explicitly require a nominal connection binding")
    if _required(schema, "packed_index_order", "packet_schema") != "slot0_lsb_unit0_lsb":
        raise ValueError("calobs v2 requires explicit slot0_lsb_unit0_lsb packed indexing")
    order = _required(schema, "field_order_msb_to_lsb", "packet_schema")
    if not isinstance(order, list) or tuple(order) != _FIELD_ORDER:
        raise ValueError("calobs v2 field order differs from the generated RTL ABI")
    widths = _mapping(_required(schema, "field_widths", "packet_schema"), "field_widths")
    # 在任何移位或分配之前检查全部位宽。
    extract_bitfields(0, field_order_msb_to_lsb=_FIELD_ORDER, field_widths=widths)
    packet_bits = _required(schema, "packet_bits", "packet_schema")
    if type(packet_bits) is not int or packet_bits != sum(widths.values()):
        raise ValueError("packet_bits differs from the explicit field-width sum")
    geometry = _mapping(_required(schema, "geometry", "packet_schema"), "geometry")
    geometry_names = {"n_main", "n_sub", "n_slices", "n_active", "code_min", "code_max"}
    if set(geometry) != geometry_names or any(
        type(geometry[name]) is not int for name in ("n_main", "n_sub", "n_slices", "n_active")
    ):
        raise ValueError(
            "geometry must explicitly supply four integer dimensions and both code endpoints"
        )
    lower, upper = geometry["code_min"], geometry["code_max"]
    # 独立 RDAC 码域不是报文的粗码域；本折叠只消费实际 mask，不补齐未使用的端点。
    if (lower is None) != (upper is None) or (
        lower is not None and (type(lower) is not int or type(upper) is not int or lower > upper)
    ):
        raise ValueError("geometry code endpoints must both be null or ordered explicit integers")
    if (
        geometry["n_slices"] != 18
        or geometry["n_active"] != 8
        or geometry["n_main"] <= 0
        or geometry["n_sub"] <= 0
        or (geometry["n_slices"], geometry["n_active"], geometry["n_main"], geometry["n_sub"])
        != (spec.n_slices, spec.n_active, spec.dac_n_main, spec.dac_n_sub)
    ):
        raise ValueError(
            "calobs v2 geometry does not match the explicit 18/8 digital specification"
        )
    format_values = _mapping(
        _required(schema, "fixed_point_format", "packet_schema"), "fixed_point_format"
    )
    format_names = {
        "weight_fraction_bits",
        "voltage_fraction_bits",
        "voltage_bits",
        "coefficient_bits",
        "accumulator_bits",
        "output_bits",
    }
    if set(format_values) != format_names:
        raise ValueError("calobs v2 requires all six explicit fixed-point format fields")
    fmt = FixedPointFormat(**dict(format_values))
    if tuple(asdict(fmt).values()) != (30, 32, 64, 48, 96, 20):
        raise ValueError(
            "UNSUPPORTED: this RTL packet arithmetic requires explicitly selected 48/Q30,64/Q32,96/20"
        )
    expected = {
        "sample_id": 32,
        "coarse": 9,
        "adc2_code": spec.adc2_n_bits,
        "known_common_q": fmt.voltage_bits,
        "slice_ids": spec.n_active * 5,
        "main_masks": spec.n_active * spec.dac_n_main,
        "sub_masks": spec.n_active * spec.dac_n_sub,
        "analog_bad": 1,
    }
    if any(widths[name] != width for name, width in expected.items()):
        raise ValueError("calobs v2 field widths do not match the explicit geometry/format")
    if not isinstance(binding_document, bytes) or not binding_document:
        raise ValueError("the original binding JSON bytes are required for identity verification")
    actual_hash = hashlib.sha256(binding_document).hexdigest()
    if (
        _digest(
            _required(schema, "source_binding_sha256", "packet_schema"), "source_binding_sha256"
        )
        != actual_hash
    ):
        raise ValueError("source binding JSON hash does not match packet_schema")
    inputs = _mapping(_required(schema, "input_sha256", "packet_schema"), "input_sha256")
    for name in ("config_json", "format_json", "binding_json", "source_sv", "acquisition_sv"):
        _digest(_required(inputs, name, "input_sha256"), "input_sha256." + name)
    if inputs["binding_json"].lower() != actual_hash:
        raise ValueError("input_sha256.binding_json differs from the actual binding document")
    try:
        binding = json.loads(binding_document.decode("utf-8"), object_pairs_hook=_json_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("binding_document must be the original valid UTF-8 JSON bytes") from error
    binding = _mapping(binding, "binding_document")
    if (
        type(_required(binding, "schema_version", "binding_document")) is not int
        or binding["schema_version"] != 1
    ):
        raise ValueError("the explicit source-control binding document requires schema_version=1")
    binding_geometry = _mapping(
        _required(binding, "geometry", "binding_document"), "binding geometry"
    )
    if (
        set(binding_geometry) != geometry_names
        or any(type(binding_geometry[name]) is not type(geometry[name]) for name in geometry_names)
        or binding_geometry != geometry
    ):
        raise ValueError("binding geometry differs from the packet schema")
    for name in ("control_word_bits", "state_bits", "sample_id_bits"):
        if type(_required(binding, name, "binding_document")) is not int or binding[name] <= 0:
            raise ValueError(f"binding {name} must explicitly supply a positive integer width")
    if (
        _required(binding, "control_word_bits", "binding_document") != widths["control_word"]
        or _required(binding, "state_bits", "binding_document") != widths["state"]
        or _required(binding, "sample_id_bits", "binding_document") != 32
    ):
        raise ValueError("binding word/state/sample-ID widths differ from the packet schema")
    source_control = _mapping(
        _required(binding, "source_control", "binding_document"), "source_control"
    )
    if (
        _digest(_required(source_control, "sha256", "source_control"), "source_control.sha256")
        != inputs["source_sv"].lower()
    ):
        raise ValueError("binding source-control hash differs from input_sha256.source_sv")
    _validate_acquisition_contract(binding, source_control, inputs, geometry, widths, spec)
    fold = _mapping(
        _required(schema, "reconstruction_fold", "packet_schema"), "reconstruction_fold"
    )
    binding_fold = _mapping(
        _required(binding, "reconstruction_fold", "binding_document"), "binding reconstruction_fold"
    )
    _validate_fold(fold, geometry, spec)
    _validate_fold(binding_fold, geometry, spec)
    return widths, fmt


def _unsigned_array(values: list[int], bits: int) -> np.ndarray:
    # object 整数保留调用方声明的超过 64 bit 的控制字。
    return np.asarray(values, dtype=np.uint64 if bits <= 64 else object)


def _control_snapshot(values: np.ndarray) -> np.ndarray:
    if not values.dtype.hasobject:
        return _readonly(values, values.dtype)
    snapshot = values.copy()
    snapshot.flags.writeable = False
    return snapshot


def observations_from_rtl(
    packets: Iterable[int | str],
    spec: CalibrationSpec,
    *,
    packet_schema: Mapping[str, Any],
    binding_document: bytes,
    connection_binding: ConnectionBinding,
    static_charge_domain: bool,
    fixed_configuration_epoch: bool,
    require_consecutive: bool = True,
) -> DigitalObservation:
    """解码一次受控 calobs v2 捕获，并核对受支持的整数折叠。

    fixed_configuration_epoch=True 是调用方对当前捕获期间配置、权重与 binding 不变的声明，
    不是报文解码出的字段；reset、clear 与重新配置必须划分捕获边界。
    调用方还须提供所捕获硬件的显式 ADC2 电压模型。哈希标识已声明的生产输入，不是电气测量；
    binding 异常直接传播，缺失元数据不选择回退。
    """
    if type(fixed_configuration_epoch) is not bool or not fixed_configuration_epoch:
        raise ValueError("calobs v2 requires a controlled, externally fixed configuration epoch")
    if type(static_charge_domain) is not bool or type(require_consecutive) is not bool:
        raise ValueError("static-domain and sample-order policies must be explicitly Boolean")
    if not isinstance(spec, CalibrationSpec) or not callable(connection_binding):
        raise ValueError("calobs v2 requires explicit nominal specification and connection binding")
    widths, fmt = _validate_schema(_mapping(packet_schema, "packet_schema"), binding_document, spec)
    decoded = []
    for packet in packets:
        if isinstance(packet, str):
            text = packet.strip()
            if not text or re.fullmatch(r"[0-9a-fA-F]+", text) is None:
                raise ValueError("packet text must contain unsigned hex_msb_first digits")
            value = int(text, 16)
        else:
            value = packet
        decoded.append(
            extract_bitfields(value, field_order_msb_to_lsb=_FIELD_ORDER, field_widths=widths)
        )
    if not decoded:
        raise ValueError("calobs v2 capture must contain at least one packet")
    n, active = len(decoded), spec.n_active
    ids = np.empty((n, active), dtype=np.int64)
    main = np.empty((n, active, spec.dac_n_main), dtype=np.int8)
    sub = np.empty((n, active, spec.dac_n_sub), dtype=np.int8)
    for i, row in enumerate(decoded):
        for slot in range(active):
            ids[i, slot] = (row["slice_ids"] >> (5 * slot)) & 31
            for unit in range(spec.dac_n_main):
                main[i, slot, unit] = (row["main_masks"] >> (slot * spec.dac_n_main + unit)) & 1
            for unit in range(spec.dac_n_sub):
                sub[i, slot, unit] = (row["sub_masks"] >> (slot * spec.dac_n_sub + unit)) & 1
    sample_ids: np.ndarray = np.asarray([row["sample_id"] for row in decoded], dtype=np.int64)
    if n > 1:
        steps = np.diff(sample_ids) & 0xFFFF_FFFF
        if (
            np.any(steps == 0)
            or np.any(steps > 0x7FFF_FFFF)
            or (require_consecutive and np.any(steps != 1))
        ):
            raise ValueError(
                "calobs v2 sample IDs are missing, duplicated or reordered within the capture"
            )
    common = np.asarray(
        [
            row["known_common_q"] - (1 << fmt.voltage_bits)
            if row["known_common_q"] & (1 << (fmt.voltage_bits - 1))
            else row["known_common_q"]
            for row in decoded
        ],
        dtype=np.int64,
    )
    codes: np.ndarray = np.asarray([row["adc2_code"] for row in decoded], dtype=np.int64)
    bad: np.ndarray = np.asarray([row["analog_bad"] for row in decoded], dtype=bool)
    controls = {
        "sample_id": sample_ids,
        "coarse": _unsigned_array([row["coarse"] for row in decoded], 9),
        "state": _unsigned_array([row["state"] for row in decoded], widths["state"]),
        "control_word": _unsigned_array(
            [row["control_word"] for row in decoded], widths["control_word"]
        ),
        "adc2_code": codes,
        "known_common_q": common,
        "slice_ids": ids,
        "main_masks": main,
        "sub_masks": sub,
        "analog_bad": bad,
    }
    bound = connection_binding(
        spec, MappingProxyType({name: _control_snapshot(value) for name, value in controls.items()})
    )
    if not isinstance(bound, tuple) or len(bound) != 2:
        raise ValueError(
            "connection binding must explicitly return (signal_mask, known_voltage_term_v)"
        )
    data = DigitalObservation(
        spec=spec,
        slice_ids=ids,
        adc2_code=codes,
        main_masks=main,
        sub_masks=sub,
        signal_mask=bound[0],
        known_voltage_term_v=bound[1],
        overflow=bad,
        sample_ids=sample_ids,
        static_charge_domain=static_charge_domain,
    )
    if not np.all(data.signal_mask == 1):
        raise ValueError("UNSUPPORTED: connection binding is not the declared all-SIGNAL fold")
    with np.errstate(over="ignore", invalid="ignore"):
        term_q = np.rint(data.known_voltage_term_v / spec.v_fs * 2**fmt.voltage_fraction_bits)
    if np.any(~np.isfinite(term_q)):
        raise ValueError("UNSUPPORTED: connection binding produced unrepresentable voltage terms")
    masks = np.concatenate((main, sub), axis=2)
    limit = 1 << (fmt.voltage_bits - 1)
    scale = 1 << fmt.voltage_fraction_bits
    for i in range(n):
        for slot in range(active):
            for unit in range(spec.shape[1]):
                expected = int(common[i]) + scale * (1 - 2 * int(masks[i, slot, unit]))
                if not -limit <= expected < limit or int(term_q[i, slot, unit]) != expected:
                    raise ValueError(
                        "UNSUPPORTED: per-unit connection terms do not exactly fold in the declared Q format"
                    )
    return data
