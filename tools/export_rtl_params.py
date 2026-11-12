#!/usr/bin/env python3
"""从显式 Config、整数格式和源控制绑定生成 RTL 输入包。

当前只支持声明为全信号采样、对称参考轨的折叠子集。
本工具不提供器件默认值、物理选通映射、扰动实现、校准系数或模拟／时序验证结果。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import shutil
from dataclasses import asdict
from pathlib import Path

from adi_model.simulation.config import Config
from adi_model.control.dem import SplitSwitchGeometry
from adi_model.contracts.fixed_point import FixedPointFormat
from adi_model.calibration.weight_calibration import CalibrationSpec


def _json_text(value: object) -> str:
    return json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n"


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _object(path: Path) -> dict:
    def unique_object(pairs):
        result = {}
        for name, item in pairs:
            if name in result:
                raise ValueError(f"duplicate JSON field: {name}")
            result[name] = item
        return result

    def reject_constant(name):
        raise ValueError(f"nonfinite JSON constant: {name}")

    value = json.loads(
        path.read_text(encoding="utf-8"),
        object_pairs_hook=unique_object,
        parse_constant=reject_constant,
    )
    if not isinstance(value, dict):
        raise ValueError(f"{path}: an explicit JSON object is required")
    return value


def _required(mapping: dict, name: str, consumer: str):
    if name not in mapping or mapping[name] is None:
        raise ValueError(f"missing {name}; consumed by {consumer}")
    return mapping[name]


def _integer(value: object, name: str, low: int, high: int) -> int:
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{name} must be an explicit integer in [{low}, {high}]")
    return value


def _known_keys(value: dict, allowed: set[str], name: str) -> None:
    unknown = set(value) - allowed
    if unknown:
        raise ValueError(f"unknown {name} fields: {', '.join(sorted(unknown))}")


def load_contract(config_path: Path, format_path: Path, binding_path: Path) -> dict:
    """在写文件前校验全部消费字段及逐单元折叠条件。

    Args:
        config_path: 显式 Config JSON 路径，包含所消费的量化与目标规格。
        format_path: 显式整数位宽及小数位 JSON 路径。
        binding_path: 几何、时序、调度、参考连接及源控制模块的绑定 JSON 路径。

    Returns:
        经校验的配置、格式、规格、几何、源控制字节及输入摘要。

    Raises:
        ValueError: 输入缺项、越出工具支持域、绑定不匹配或逐单元折叠条件不成立。
    """
    cfg = Config.from_dict(_object(config_path))
    raw_format = _object(format_path)
    binding = _object(binding_path)
    for name in (
        "weight_fraction_bits",
        "voltage_fraction_bits",
        "voltage_bits",
        "coefficient_bits",
        "accumulator_bits",
        "output_bits",
    ):
        _required(raw_format, name, "RTL integer arithmetic")
    fmt = FixedPointFormat(**raw_format)
    supported = (30, 32, 64, 48, 96, 20)
    selected = (
        fmt.weight_fraction_bits,
        fmt.voltage_fraction_bits,
        fmt.voltage_bits,
        fmt.coefficient_bits,
        fmt.accumulator_bits,
        fmt.output_bits,
    )
    if selected != supported:
        raise ValueError("UNSUPPORTED RTL format; no implicit Q/width substitution")
    if binding.get("schema_version") != 1:
        raise ValueError("binding.schema_version must explicitly be 1")
    _known_keys(
        binding,
        {
            "schema_version",
            "geometry",
            "control_word_bits",
            "state_bits",
            "sample_id_bits",
            "timing",
            "scheduler",
            "divider_stages",
            "source_control",
            "reconstruction_fold",
            "provenance",
            "acquisition_control",
            "dither_contract",
        },
        "binding",
    )
    raw_geometry = _required(binding, "geometry", "physical interface")
    if not isinstance(raw_geometry, dict):
        raise ValueError("binding.geometry must be an object")
    for name in ("n_main", "n_sub", "n_slices", "n_active"):
        _required(raw_geometry, name, "physical geometry")
    # This consumer records actual masks, not an independent RDAC k codebook.
    # Retain explicit unknown endpoints without inventing a domain from coarse9.
    for name in ("code_min", "code_max"):
        if name not in raw_geometry:
            raise ValueError(f"missing {name}; declare the independent codebook or explicit null")
    geo = SplitSwitchGeometry(**raw_geometry)
    if (geo.n_slices, geo.n_active, cfg.n_slices, cfg.n_active, cfg.b1, cfg.n_bits_target) != (
        18,
        8,
        18,
        8,
        9,
        20,
    ):
        raise ValueError("source target requires 18 slices, 8 active, 9 coarse and 20 output bits")
    if geo.n_main + geo.n_sub > 128:
        raise ValueError("UNSUPPORTED physical unit-address dimension")
    if (
        isinstance(cfg.fs, bool)
        or not isinstance(cfg.fs, int | float)
        or not math.isfinite(cfg.fs)
        or cfg.fs <= 0
    ):
        raise ValueError("Config.fs must be finite and positive [Hz]")
    if type(cfg.dem_enable) is not bool or type(cfg.dither_enable) is not bool:
        raise ValueError("DEM and dither requests must be explicit Boolean values")
    spec = CalibrationSpec.from_config(cfg, geometry=geo)
    if spec.adc2_n_bits > 12:
        raise ValueError("UNSUPPORTED ADC2 decisions in the bound 16-tick engine")
    cw = _integer(
        _required(binding, "control_word_bits", "sample context"), "control_word_bits", 1, 64
    )
    sw = _integer(_required(binding, "state_bits", "sample context"), "state_bits", 1, 64)
    acquisition = _required(binding, "acquisition_control", "two actual acquisition nodes")
    expected_acquisition = {
        "kind": "sv_module",
        "sv_module_name": "source_acquisition_binding",
        "voltage_unit": "normalized_signed_q",
        "sadc_input_shift": "add_to_sadc_sampled_input",
        "rdac_input_shift": "add_to_all_rdac_sampled_units",
    }
    if not isinstance(acquisition, dict) or any(
        acquisition.get(name) != value for name, value in expected_acquisition.items()
    ):
        raise ValueError(
            "explicit source_acquisition_binding and both additive node meanings required"
        )
    _known_keys(
        acquisition,
        {*expected_acquisition, "connection_contract", "sv_path", "sha256"},
        "acquisition_control",
    )
    if (
        not isinstance(acquisition.get("connection_contract"), str)
        or not acquisition["connection_contract"].strip()
    ):
        raise ValueError("acquisition_control.connection_contract must describe supplied nodes")
    dither = _required(binding, "dither_contract", "source dither capability")
    if (
        not isinstance(dither, dict)
        or not isinstance(dither.get("provenance"), str)
        or not dither["provenance"].strip()
    ):
        raise ValueError("explicit dither capability kind and provenance required")
    if dither.get("kind") == "none":
        _known_keys(dither, {"kind", "provenance"}, "dither_contract")
        if cfg.dither_enable:
            raise ValueError(
                "UNSUPPORTED source lower/upper dither without a bound two-node profile"
            )
    elif dither.get("kind") == "patent_fig19_ideal_ports_v1":
        _known_keys(dither, {"kind", "provenance", "equivalent_polarity"}, "dither_contract")
        if (
            dither.get("equivalent_polarity") != "ASSUMED_NEGATIVE_SAMPLING_SHIFTS"
            or cw != 6
            or sw != 1
            or cfg.dem_enable
            or spec.v_fs != 3.0
            or (geo.n_main, geo.n_sub, geo.code_min, geo.code_max) != (63, 8, 0, 511)
        ):
            raise ValueError(
                "UNSUPPORTED Fig19 profile: explicit6/1controls,63+8,code0..511,Vfs3,noDEM required"
            )
    else:
        raise ValueError("UNSUPPORTED source dither capability; no range/4x/scalar fallback")
    if binding.get("sample_id_bits") != 32:
        raise ValueError("explicit sample_id_bits=32 engineering ABI required")
    timing = _required(binding, "timing", "macro-control schedule")
    if not isinstance(timing, dict):
        raise ValueError("binding.timing must be an object")
    _known_keys(
        timing,
        {
            "phases",
            "residue_capture_phase",
            "reference_on_phase",
            "fine_decision_deadline_phase",
            "tick_period_ns",
        },
        "timing",
    )
    phases = {
        "phases": 16,
        "residue_capture_phase": 9,
        "reference_on_phase": 10,
        "fine_decision_deadline_phase": 14,
    }
    if any(type(timing.get(k)) is not int or timing[k] != v for k, v in phases.items()):
        raise ValueError("UNSUPPORTED schedule; every supported phase must be explicit")
    tick = _required(timing, "tick_period_ns", "digital clock budget")
    if (
        isinstance(tick, bool)
        or not isinstance(tick, int | float)
        or not math.isfinite(tick)
        or tick <= 0
    ):
        raise ValueError("timing.tick_period_ns must be finite and positive [ns]")
    if not math.isclose(tick * timing["phases"] * cfg.fs, 1e9, rel_tol=1e-10):
        raise ValueError("explicit tick/frame period disagrees with Config.fs")
    scheduler = _required(binding, "scheduler", "causal slice ownership")
    if not isinstance(scheduler, dict) or scheduler.get("kind") != "causal_pool_scan":
        raise ValueError("explicit engineering scheduler.kind=causal_pool_scan required")
    _known_keys(scheduler, {"kind", "shuffle_enable", "seed"}, "scheduler")
    if type(scheduler.get("shuffle_enable")) is not bool:
        raise ValueError("scheduler.shuffle_enable must be explicit and separate from DEM")
    seed = _integer(_required(scheduler, "seed", "slice allocator"), "scheduler.seed", 1, 2**32 - 1)
    stages = _integer(
        _required(binding, "divider_stages", "integer latency"), "divider_stages", 1, 63
    )
    if math.ceil((fmt.accumulator_bits - fmt.voltage_fraction_bits - 1) / stages) + 2 > 16:
        raise ValueError("divider latency exceeds the explicit frame budget")
    fold = _required(binding, "reconstruction_fold", "weight-independent per-unit proof")
    if not isinstance(fold, dict) or fold.get("kind") != "all_signal_symmetric_common_v1":
        raise ValueError("UNSUPPORTED connection fold")
    _known_keys(
        fold,
        {
            "kind",
            "sampling_main",
            "sampling_sub",
            "reference_low_v",
            "reference_high_v",
            "common_input_unit",
            "term_quantizer",
        },
        "fold",
    )
    for name, units in (("sampling_main", geo.n_main), ("sampling_sub", geo.n_sub)):
        rows = _required(fold, name, "actual acquisition connections")
        if (
            not isinstance(rows, list)
            or len(rows) != geo.n_slices
            or any(
                not isinstance(row, list)
                or len(row) != units
                or any(type(item) is not int or item != 0 for item in row)
                for row in rows
            )
        ):
            raise ValueError(f"UNSUPPORTED {name}: every physical unit must explicitly be SIGNAL=0")
    if fold.get("reference_low_v") != -spec.v_fs or fold.get("reference_high_v") != spec.v_fs:
        raise ValueError("this fold requires explicit exact -Vfs/+Vfs references [V]")
    if (
        fold.get("common_input_unit") != "normalized_signed_q"
        or fold.get("term_quantizer") != "ties_even_per_unit"
    ):
        raise ValueError("explicit normalized_signed_q and ties_even_per_unit contracts required")
    source = _required(binding, "source_control", "actual physical mask implementation")
    if not isinstance(source, dict) or source.get("kind") != "sv_module":
        raise ValueError("UNSUPPORTED source-control kind; supply a synthesizable SV module")
    _known_keys(
        source,
        {"kind", "sv_module_name", "sv_path", "sha256", "connection_contract"},
        "source_control",
    )
    if source.get("sv_module_name") != "source_control_binding":
        raise ValueError("binding module must implement source_control_binding")
    module_name = _required(source, "sv_path", "source-control implementation")
    if not isinstance(module_name, str) or not module_name:
        raise ValueError("source_control.sv_path must be a nonempty path string")
    module_path = Path(module_name)
    if not module_path.is_absolute():
        module_path = binding_path.resolve().parent / module_path
    if not module_path.is_file() or module_path.stat().st_size >= 300 * 1024 * 1024:
        raise ValueError("binding SV must be readable and smaller than 300 MB")
    module_data = module_path.read_bytes()
    if source.get("sha256") != _digest(module_data):
        raise ValueError("source-control module digest mismatch")
    code = re.sub(r"/\*.*?\*/|//[^\n]*", " ", module_data.decode("utf-8"), flags=re.S)
    if re.findall(r"(?m)^\s*module\s+(\w+)", code) != ["source_control_binding"]:
        raise ValueError("source control SV must declare exactly its same-named module")
    acquisition_name = _required(acquisition, "sv_path", "source acquisition implementation")
    if not isinstance(acquisition_name, str) or not acquisition_name:
        raise ValueError("acquisition_control.sv_path must be a nonempty path")
    acquisition_path = Path(acquisition_name)
    if not acquisition_path.is_absolute():
        acquisition_path = binding_path.resolve().parent / acquisition_path
    if not acquisition_path.is_file() or acquisition_path.stat().st_size >= 300 * 1024 * 1024:
        raise ValueError("source acquisition SV must be readable and smaller than300MB")
    acquisition_data = acquisition_path.read_bytes()
    if acquisition.get("sha256") != _digest(acquisition_data):
        raise ValueError("source acquisition module digest mismatch")
    acquisition_code = re.sub(
        r"/\*.*?\*/|//[^\n]*", " ", acquisition_data.decode("utf-8"), flags=re.S
    )
    if re.findall(r"(?m)^\s*module\s+(\w+)", acquisition_code) != ["source_acquisition_binding"]:
        raise ValueError("source acquisition SV must declare exactly its same-named module")
    if not source.get("connection_contract") or not binding.get("provenance"):
        raise ValueError(
            "connection_contract and provenance required; golden output is not a binding"
        )
    return {
        "config": cfg,
        "format": fmt,
        "spec": spec,
        "geometry": geo,
        "binding": binding,
        "word_bits": cw,
        "state_bits": sw,
        "seed": seed,
        "stages": stages,
        "module_data": module_data,
        "acquisition_data": acquisition_data,
        "input_bytes": {
            "config.json": config_path.read_bytes(),
            "format.json": format_path.read_bytes(),
            "binding.json": binding_path.read_bytes(),
        },
        "input_sha256": {
            "config_json": _digest(config_path.read_bytes()),
            "format_json": _digest(format_path.read_bytes()),
            "binding_json": _digest(binding_path.read_bytes()),
            "source_sv": _digest(module_data),
            "acquisition_sv": _digest(acquisition_data),
        },
    }


def build(config_path: Path, format_path: Path, binding_path: Path) -> dict[str, str | bytes]:
    """返回完整且确定的输入包内容，不推断物理器件配置。"""
    data = load_contract(config_path, format_path, binding_path)
    cfg, fmt, spec, geo, binding = (
        data[k] for k in ("config", "format", "spec", "geometry", "binding")
    )
    params = {
        "W_FRAC": fmt.weight_fraction_bits,
        "W_BITS": fmt.coefficient_bits,
        "V_FRAC": fmt.voltage_fraction_bits,
        "V_BITS": fmt.voltage_bits,
        "ACC_BITS": fmt.accumulator_bits,
        "OUT_BITS": fmt.output_bits,
        "N_SLICES": geo.n_slices,
        "N_ACTIVE": geo.n_active,
        "N_UNIT_MAIN": geo.n_main,
        "N_UNIT_SUB": geo.n_sub,
        "N_UNIT_TOTAL": geo.n_main + geo.n_sub,
        "B1": cfg.b1,
        "ADC2_BITS": spec.adc2_n_bits,
        "PHASES": binding["timing"]["phases"],
        "CONTROL_WORD_BITS": data["word_bits"],
        "STATE_BITS": data["state_bits"],
        "RECON_UNROLL": data["stages"],
        "POOL_SHUFFLE_SEED": data["seed"],
        "POOL_SHUFFLE_ENABLE": int(binding["scheduler"]["shuffle_enable"]),
        "SOURCE_DEM_REQUEST": int(cfg.dem_enable),
        "SOURCE_DITHER_REQUEST": int(cfg.dither_enable),
    }
    fields = [
        ("sample_id", 32),
        ("coarse", 9),
        ("state", data["state_bits"]),
        ("control_word", data["word_bits"]),
        ("adc2_code", spec.adc2_n_bits),
        ("known_common_q", fmt.voltage_bits),
        ("slice_ids", geo.n_active * 5),
        ("main_masks", geo.n_active * geo.n_main),
        ("sub_masks", geo.n_active * geo.n_sub),
        ("analog_bad", 1),
    ]
    params["CAL_OBS_BITS"] = sum(width for _name, width in fields)
    lines = [
        "// Explicit Config/format/binding inputs. No device or physical-mapper defaults.",
        "// Source macro/timing correspondence is not certified by this header.",
        "`ifndef RTL_PARAMS_VH",
        "`define RTL_PARAMS_VH",
    ]
    lines += [f"localparam int unsigned {name} = 32'd{value};" for name, value in params.items()]
    lines += ["`endif", ""]
    packet = {
        "schema_version": 2,
        "field_order_msb_to_lsb": [name for name, _width in fields],
        "field_widths": dict(fields),
        "packet_bits": params["CAL_OBS_BITS"],
        "geometry": asdict(geo),
        "fixed_point_format": asdict(fmt),
        "reconstruction_fold": binding["reconstruction_fold"],
        "source_binding_sha256": data["input_sha256"]["binding_json"],
        "payload_encoding": "hex_msb_first",
        "packed_index_order": "slot0_lsb_unit0_lsb",
        "input_sha256": data["input_sha256"],
        "connection_binding_required": True,
        "scope": binding["provenance"],
    }
    manifest = {
        "schema_version": 1,
        "status": "EXPLICIT_RTL_INPUTS_GENERATED",
        "scope": "DIGITAL_BINDING_NOT_ANALOG_OR_TIMING_SIGNOFF",
        "config": cfg.to_dict(),
        "format": asdict(fmt),
        "calibration_spec": asdict(spec),
        "parameters": params,
        "input_sha256": data["input_sha256"],
        "fold": "a=1; term_q=known_common_q+(1-2*actual_mask)*2^V_FRAC per unit",
        "source_dither": binding["dither_contract"],
        "acquisition_control": binding["acquisition_control"],
        "binding_interface": "RTL_BINDING_ABI_2_COMMON_ACQUISITION",
    }
    files: dict[str, str | bytes] = {
        "rtl_params.vh": "\n".join(lines),
        "source_control_binding.sv": data["module_data"],
        "source_acquisition_binding.sv": data["acquisition_data"],
        "packet_schema.json": _json_text(packet),
    }
    files.update(data["input_bytes"])
    manifest["artifact_sha256"] = {
        name: _digest(content if isinstance(content, bytes) else content.encode("utf-8"))
        for name, content in files.items()
    }
    files["rtl_input_manifest.json"] = _json_text(manifest)
    return files


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--format", required=True, type=Path)
    parser.add_argument("--binding", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    files = build(args.config, args.format, args.binding)
    if args.check:
        for name, content in files.items():
            expected = content if isinstance(content, bytes) else content.encode("utf-8")
            target = args.out / name
            if not target.is_file() or target.read_bytes() != expected:
                raise SystemExit(f"explicit RTL input drift: {target}")
    else:
        args.out.mkdir(parents=True, exist_ok=False)
        try:
            for name, content in files.items():
                (args.out / name).write_bytes(
                    content if isinstance(content, bytes) else content.encode("utf-8")
                )
        except OSError:
            shutil.rmtree(args.out)  # 只清理本次调用新建的目录。
            raise
    manifest = json.loads(files["rtl_input_manifest.json"])
    print(f"EXPLICIT_RTL_INPUTS_COMPLETE files=8 source_dither={manifest['source_dither']['kind']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
