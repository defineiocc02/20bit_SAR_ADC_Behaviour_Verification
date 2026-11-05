#!/usr/bin/env python3
"""将显式数字观测投影为有界的重构测试向量。

实际转换选通、逐单元信号系数和已知电压项均来自输入记录。
工具不运行 ADC 流水线、物理映射器或随机激励，也不生成系数真值。
全信号采样的折叠条件逐单元校验，不依赖权重值。
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

import numpy as np

from adi_model.fixed_point import FixedPointReconstructor
from adi_model.weight_calibration import DigitalObservation
from export_rtl_params import (
    _digest,
    _integer,
    _json_text,
    _known_keys,
    _object,
    _required,
    load_contract,
)


def _pack(values, width: int) -> int:
    return sum(int(value) << (width * index) for index, value in enumerate(np.asarray(values).flat))


def build_vectors(
    contract: dict, decoder: FixedPointReconstructor, observations_path: Path
) -> dict[str, str]:
    """校验显式观测及逐单元折叠条件，再计算整数输出。

    观测必须处于静态电荷域，且信号系数全部为 1。逐单元电压项按所选整数格式
    取偶舍入后，须与已知公共项及实际选通精确一致；不是按权重近似比较。
    """
    raw = _object(observations_path)
    _known_keys(raw, {"schema_version", "scope", "static_charge_domain", "samples"}, "observations")
    if raw.get("schema_version") != 2 or not isinstance(raw.get("scope"), str) or not raw["scope"]:
        raise ValueError("observations require schema_version=2 and an explicit provenance scope")
    if type(raw.get("static_charge_domain")) is not bool or not raw["static_charge_domain"]:
        raise ValueError(
            "this reconstruction projection requires an explicitly static charge domain"
        )
    rows = _required(raw, "samples", "digital reconstruction projection")
    if not isinstance(rows, list) or not 0 < len(rows) <= 100000:
        raise ValueError("a nonempty bounded samples list is required")
    required = {
        "sample_id",
        "adc2_code",
        "state",
        "control_word",
        "known_common_q",
        "slice_ids",
        "main_masks",
        "sub_masks",
        "signal_mask",
        "known_voltage_term_v",
        "overflow",
    }
    for row in rows:
        if not isinstance(row, dict) or set(row) != required:
            raise ValueError(
                "each observation must explicitly supply exactly the documented digital fields"
            )
        _integer(row["sample_id"], "sample_id", 0, 2**32 - 1)
        _integer(row["state"], "state", 0, 2 ** contract["state_bits"] - 1)
        _integer(row["control_word"], "control_word", 0, 2 ** contract["word_bits"] - 1)
        _integer(row["adc2_code"], "adc2_code", 0, 2 ** contract["spec"].adc2_n_bits - 1)
        if type(row["overflow"]) is not bool:
            raise ValueError("overflow must be an explicitly observed Boolean")
        scale = 2**decoder.format.voltage_fraction_bits
        _integer(
            row["known_common_q"],
            "known_common_q",
            -(2 ** (decoder.format.voltage_bits - 1)) + scale,
            2 ** (decoder.format.voltage_bits - 1) - 1 - scale,
        )
    data = DigitalObservation(
        spec=contract["spec"],
        slice_ids=np.asarray([r["slice_ids"] for r in rows]),
        adc2_code=np.asarray([r["adc2_code"] for r in rows]),
        main_masks=np.asarray([r["main_masks"] for r in rows]),
        sub_masks=np.asarray([r["sub_masks"] for r in rows]),
        signal_mask=np.asarray([r["signal_mask"] for r in rows]),
        known_voltage_term_v=np.asarray([r["known_voltage_term_v"] for r in rows]),
        overflow=np.asarray([r["overflow"] for r in rows]),
        sample_ids=np.asarray([r["sample_id"] for r in rows]),
        static_charge_domain=True,
    )
    data.validate()
    if decoder.spec != data.spec or decoder.format != contract["format"]:
        raise ValueError("explicit register image does not match Config/format/binding")
    if not np.all(data.signal_mask == 1):
        raise ValueError("UNSUPPORTED per-unit signal mask for the selected all-signal RTL fold")
    masks = np.concatenate((data.main_masks, data.sub_masks), axis=2)
    checks = 0
    for index, row in enumerate(rows):
        for slot in range(data.spec.n_active):
            for unit in range(data.spec.shape[1]):
                term = data.known_voltage_term_v[index, slot, unit]
                scaled = term / data.spec.v_fs * 2**decoder.format.voltage_fraction_bits
                if not np.isfinite(scaled):
                    raise ValueError("nonfinite quantized per-unit voltage term")
                actual = int(np.rint(scaled))
                expected = row["known_common_q"] + (1 - 2 * int(masks[index, slot, unit])) * scale
                if actual != expected:
                    raise ValueError(
                        "UNSUPPORTED per-unit quantized voltage term; no approximate weight test"
                    )
                checks += 1
    words = decoder.reconstruct(data)
    columns = [
        "sample_id",
        "adc2_code",
        "known_common_q",
        "slice_ids",
        "main_masks",
        "sub_masks",
        "expected_word",
        "clip_low",
        "clip_high",
        "analog_bad",
    ]
    output = ["# " + " ".join(columns)]
    for index, row in enumerate(rows):
        output.append(
            " ".join(
                (
                    f'{row["sample_id"]:08x}',
                    f'{row["adc2_code"]:x}',
                    f'{row["known_common_q"] & ((1 << decoder.format.voltage_bits) - 1):016x}',
                    f'{_pack(row["slice_ids"], 5):x}',
                    f'{_pack(row["main_masks"], 1):x}',
                    f'{_pack(row["sub_masks"], 1):x}',
                    f"{int(words.code[index]):05x}",
                    str(int(words.clipped_low[index])),
                    str(int(words.clipped_high[index])),
                    str(int(words.analog_overflow[index])),
                )
            )
        )
    vectors = "\n".join(output) + "\n"
    manifest = {
        "schema_version": 2,
        "scope": "COMPONENT_RECONSTRUCTION_PROJECTION",
        "input_record_scope": raw["scope"],
        "mapping_or_silicon_equivalence": "NOT_EVALUATED",
        "sample_count": len(rows),
        "per_unit_fold_equalities": checks,
        "clipped_low": int(np.count_nonzero(words.clipped_low)),
        "clipped_high": int(np.count_nonzero(words.clipped_high)),
        "analog_bad": int(np.count_nonzero(words.analog_overflow)),
        "columns": columns,
        "input_sha256": {
            **contract["input_sha256"],
            "observations_json": _digest(observations_path.read_bytes()),
        },
        "register_sha256": _digest(_json_text(decoder.to_dict()).encode("utf-8")),
        "vector_sha256": _digest(vectors.encode("utf-8")),
        "field_packing": "slot0_lsb_unit0_lsb; signed common term is two's complement",
    }
    return {
        "reconstruction_inputs.hex": vectors,
        "reconstruction_manifest.json": _json_text(manifest),
    }


def build(
    config_path: Path,
    format_path: Path,
    binding_path: Path,
    registers_path: Path,
    observations_path: Path,
) -> dict[str, str]:
    """读取显式系数，不另建名义系数或重新拟合替代值。"""
    contract = load_contract(config_path, format_path, binding_path)
    decoder = FixedPointReconstructor.from_dict(_object(registers_path))
    files = build_vectors(contract, decoder, observations_path)
    manifest = json.loads(files["reconstruction_manifest.json"])
    manifest["input_sha256"]["registers_json"] = _digest(registers_path.read_bytes())
    files["reconstruction_manifest.json"] = _json_text(manifest)
    return files


def write_bundle(files: dict[str, str], output: Path, *, check: bool) -> None:
    """检查模式逐字节比较；写入模式拒绝已有输出，只清理本次新建的未完成目录。"""
    if check:
        for name, content in files.items():
            target = output / name
            if not target.is_file() or target.read_bytes() != content.encode("utf-8"):
                raise ValueError(f"reconstruction vector drift: {target}")
        return
    output.mkdir(parents=True, exist_ok=False)
    try:
        for name, content in files.items():
            (output / name).write_text(content, encoding="utf-8")
    except OSError:
        shutil.rmtree(output)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    for name in ("config", "format", "binding", "registers", "observations", "out"):
        parser.add_argument("--" + name, required=True, type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    files = build(args.config, args.format, args.binding, args.registers, args.observations)
    write_bundle(files, args.out, check=args.check)
    print("RECONSTRUCTION_VECTORS_COMPLETE nonempty=1 physical_mapping_verified=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
