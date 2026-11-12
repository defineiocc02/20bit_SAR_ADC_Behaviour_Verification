#!/usr/bin/env python3
"""运行显式Fig19理想双节点的真实RTL、静态训练及同raw留出。

制造数据只供独立plant；训练器仅接收真实DUT packet与已知输入。该夹具不证明
原芯片注入电容、模拟保持/建立、DEM、噪声、动态校准或目标工艺时序。
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True

import numpy as np  # noqa: E402

from adi_model.simulation.config import Config  # noqa: E402
from adi_model.topology.dac_arch import SplitChip  # noqa: E402
from adi_model.defaults import default_model  # noqa: E402
from adi_model.contracts.fixed_point import FixedPointReconstructor  # noqa: E402
from adi_model.rtl_observation import observations_from_rtl  # noqa: E402
from adi_model.calibration.weight_calibration import fit_unit_weights  # noqa: E402
from export_fitted_rtl import build_fitted_registers  # noqa: E402
from export_rtl_params import build  # noqa: E402
from run_open_rtl import (  # noqa: E402
    CORE_SOURCES,
    REPO,
    alias_runtime,
    checked_run,
    command_for,
    completion,
    digest,
    new_output,
    validate_inputs,
    verilator_compiler_args,
    write_json,
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def dither_terms(word: int) -> tuple[int, int, int]:
    """按独立源合同逐位求整数L/T/U，不调用SV译码或实际电容。"""
    require(type(word) is int and 0 <= word < 64, "six-bit Fig19 word required")
    upper = 4 * ((word >> 5) & 1) + ((word >> 3) & 1)
    lower = 33554432 * ((word >> 4) & 1)
    lower += 9586981 * ((word >> 2) & 1)
    lower += 4793490 * ((word >> 1) & 1) + 2396745 * (word & 1)
    return lower, lower + upper * 16777216, upper


def rows(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        data = list(reader)
    require(
        bool(data) and all(None not in row and None not in row.values() for row in data),
        f"empty or malformed trace: {path}",
    )
    return data


def unpack_ids(value: str) -> list[int]:
    packed = int(value, 16)
    require(0 <= packed < 1 << 40, "ordered physical IDs exceed eight5bit slots")
    ids = [(packed >> (5 * slot)) & 31 for slot in range(8)]
    require(len(set(ids)) == 8 and all(i < 18 for i in ids), "invalid physical IDs")
    return ids


def binding_features(spec, controls):
    """只读数字word/actual masks及名义参考；不接收制造值或系数。"""
    words = controls["control_word"]
    total_q = np.asarray([dither_terms(int(word))[1] for word in words], dtype=np.int64)
    require(
        np.array_equal(controls["known_common_q"], -total_q),
        "actual frozen common term differs from observed word Fig19 T",
    )
    masks = np.concatenate((controls["main_masks"], controls["sub_masks"]), axis=2)
    known = spec.v_fs * (-total_q[:, None, None] / 2**32 + 1 - 2 * masks)
    return np.ones_like(masks, dtype=np.int8), known


def round_even(numerator: int, denominator: int) -> int:
    quotient, remainder = divmod(numerator, denominator)
    return quotient + int(
        2 * remainder > denominator or (2 * remainder == denominator and quotient % 2)
    )


def scalar_word(image: FixedPointReconstructor, obs, index: int, total_q: int) -> tuple[int, int]:
    """逐unit任意精度整数参考，不使用DUT归约树或生产重构中间量。"""
    fmt = image.format
    fine = image.adc2_min_q + round_even(
        (2 * int(obs.adc2_code[index]) + 1) * (image.adc2_max_q - image.adc2_min_q),
        1 << (image.spec.adc2_n_bits + 1),
    )
    gain, known = 0, 0
    masks = np.concatenate((obs.main_masks[index], obs.sub_masks[index]), axis=1)
    for slot, physical in enumerate(obs.slice_ids[index]):
        for unit, on in enumerate(masks[slot]):
            weight = int(image.weights_q[int(physical), unit])
            gain += weight
            known += weight * (-total_q + (1 - 2 * int(on)) * (1 << fmt.voltage_fraction_bits))
    numerator = (fine - image.offset_q) * (1 << fmt.weight_fraction_bits) - known
    shifted = (numerator + gain * (1 << fmt.voltage_fraction_bits)) * (1 << fmt.output_bits)
    quotient = shifted // (2 * gain * (1 << fmt.voltage_fraction_bits))
    flags = (8 if quotient < 0 else 0) | (16 if quotient >= 1 << fmt.output_bits else 0)
    return min(max(quotient, 0), (1 << fmt.output_bits) - 1), flags


def verify_trace(
    folder: Path,
    inputs: Path,
    register_path: Path,
    stimulus: list[tuple],
    plant_weights: np.ndarray,
    config: Config,
    *,
    capture_samples: int,
    plan_output: Path | None = None,
) -> tuple[dict, object, np.ndarray]:
    """核完整合法程序和物理尾部；仅前N目标参与学习或退休比较，不声称停机。"""
    controls = rows(folder / "controls.csv")
    samples = rows(folder / "samples.csv")
    residues = rows(folder / "residues.csv")
    events = rows(folder / "rdac_events.csv")
    results = rows(folder / "events.csv")
    n = capture_samples
    require(
        type(n) is int
        and 1 <= n <= 8192
        and len(stimulus) == n + 3
        and stimulus[n:] == [(0, 0.0, 0)] * 3,
        "finite capture requires N target records and three explicit legal postamble records",
    )
    physical_count = n + 2
    require(
        len(controls) == len(stimulus)
        and len(samples) == len(residues) == physical_count
        and len(events) == 7 * physical_count,
        "actual source/sample/residue/event coverage differs from the finite program",
    )
    expected_ids = list(range(2, physical_count + 2))
    require(
        [int(row["sample_id"]) for row in samples] == expected_ids
        and [int(row["sample_id"]) for row in residues] == expected_ids,
        "sample/residue identity missing or reordered",
    )
    require(
        all(row["epoch"] == "0" and row["kind"] in ("OBS", "OUT") for row in results),
        "only epoch0 OBS/OUT records are legal in this reset-bounded capture",
    )
    positive_capture = not any(row[2] for row in stimulus[:n])
    for row in results:
        status = int(row["status_word"], 16)
        fault = int(row["conversion_fault"])
        require(int(row["analog_sticky"]) == 0, "capture has a sticky analog error")
        if positive_capture:
            require(status == fault == 0, "positive capture has a protocol/conversion fault")
        else:
            require(
                status in (0, 13 << 6) and fault == int(status != 0),
                "early-prefix negative has an unexpected error rather than source binding13",
            )
    control_ids = []
    for index, (control, (word, reference, _)) in enumerate(zip(controls, stimulus, strict=True)):
        lower_q, total_q, _ = dither_terms(word)
        require(
            (
                int(control["sample_id"]),
                int(control["word"]),
                float(control["reference_v"]),
                int(control["actual_control_word"]),
                int(control["control_valid"]),
                int(control["sadc_shift_q"]),
                int(control["rdac_shift_q"]),
            )
            == (index + 2, word, reference, word, 1, -lower_q, -total_q),
            "actual source handshake/registered two-node control differs from its program owner",
        )
        control_ids.append(unpack_ids(control["acquiring_ids"]))
    by_id = {int(row["sample_id"]): row for row in residues}
    plans = []
    early_invalid = []
    physical_compares = 0
    previous_pins = (0, 0, 0)
    for index, (sample, residue, (word, reference, rejected)) in enumerate(
        zip(samples, residues, stimulus[:physical_count], strict=True)
    ):
        sid = index + 2
        lower_q, total_q, upper = dither_terms(word)
        require(
            int(sample["word"]) == int(residue["word"]) == word
            and float(sample["reference_v"]) == float(residue["reference_v"]) == reference,
            "accepted word/reference differs from source stimulus",
        )
        require(
            (int(sample["sadc_shift_q"]), int(sample["rdac_shift_q"])) == (-lower_q, -total_q),
            "actual acquisition ports do not implement the source two-node contract",
        )
        sadc_v, rdac_v = (
            reference - lower_q / 2**32 * config.v_fs,
            reference - total_q / 2**32 * config.v_fs,
        )
        require(
            abs(float(sample["sadc_sample_v"]) - sadc_v) <= 1e-13
            and abs(float(sample["rdac_sample_v"]) - rdac_v) <= 1e-13,
            "lower/total did not enter the two actual held inputs",
        )
        ids = unpack_ids(residue["converting_ids"])
        acq = unpack_ids(residue["next_acquiring_ids"])
        require(
            ids == unpack_ids(sample["acquiring_ids"])
            and ids == control_ids[index]
            and acq == control_ids[index + 1]
            and not set(ids) & set(acq),
            "actual conversion did not own preceding acquisition",
        )
        require(
            int(sample["acquiring_mask"], 16) == sum(1 << i for i in ids),
            "ordered IDs differ from actual acquisition mask",
        )
        if plans:
            require(ids == plans[-1]["acq"], "conv[n+1] differs from actual acq[n]")
        plans.append(
            {
                "sample_id": sid,
                "conv": ids,
                "acq": acq,
                "word": word,
                "reference_v": reference,
                "actual_reference_high_v": config.v_fs,
                "actual_reference_low_v": -config.v_fs,
                "nominal_reference_high_v": config.v_fs,
                "nominal_reference_low_v": -config.v_fs,
            }
        )
        coarse = min(max(math.floor((sadc_v + config.v_fs) * 512 / (2 * config.v_fs)), 0), 511)
        require(
            int(residue["coarse_final"]) == coarse and int(residue["rdac_final"]) == coarse - upper,
            "actual final c/k differs from sampled SADC and source upper code",
        )
        group = events[7 * index : 7 * index + 7]
        invalid_prefix = False
        for j, event in enumerate(group):
            prefix = coarse & ~((1 << (6 - j)) - 1)
            k = prefix - upper
            accepted = 0 <= k <= 511
            require(
                (
                    int(event["sample_id"]),
                    int(event["event_index"]),
                    int(event["phase"]),
                    int(event["coarse_prefix"]),
                    int(event["rdac_code"]),
                    int(event["word"]),
                )
                == (sid, j, j + 2, prefix, k, word),
                "actual prefix sequence or owner differs",
            )
            require(
                int(event["common_q"]) == -total_q
                and (int(event["accepted"]), int(event["bound_valid"]), int(event["bound_error"]))
                == (int(accepted), int(accepted), 0 if accepted else 13),
                "prefix overflow was clamped/omitted or lost its binding error",
            )
            pins = tuple(
                int(event[name], 16) for name in ("selected_mask", "main_physical", "sub_physical")
            )
            if accepted:
                main = sum(
                    int(unit < k // 8) << (physical * 63 + unit)
                    for physical in ids
                    for unit in range(63)
                )
                sub = sum(
                    int(unit < k % 8) << (physical * 8 + unit)
                    for physical in ids
                    for unit in range(8)
                )
                require(
                    pins == (sum(1 << p for p in ids), main, sub),
                    "accepted event differs from real registered physical pins",
                )
            else:
                require(pins == previous_pins, "invalid prefix drove new pins instead of holding")
                invalid_prefix = True
            previous_pins = pins
            physical_compares += 1
        require(
            invalid_prefix == bool(rejected),
            "explicit rejected-sample policy differs from all7events",
        )
        require(
            tuple(
                int(residue[name], 16)
                for name in ("selected_mask", "main_physical", "sub_physical")
            )
            == previous_pins,
            "actual residue pins differ from the last registered prefix event",
        )
        if invalid_prefix:
            require(
                coarse - upper >= 0,
                "negative control must isolate early-prefix from final legality",
            )
            early_invalid.append(sid)
        f = 0.0
        for physical in range(18):
            if physical not in ids:
                continue
            for unit in range(71):
                packed = int(residue["main_physical"] if unit < 63 else residue["sub_physical"], 16)
                bit = (
                    packed
                    >> (physical * (63 if unit < 63 else 8) + (unit if unit < 63 else unit - 63))
                ) & 1
                f += float(plant_weights[physical, unit]) * (rdac_v + config.v_fs * (1 - 2 * bit))
        require(
            abs(f - float(residue["ra_v"])) <= 2e-12,
            "independent physical charge operator differs from real held-input/pin plant",
        )
        raw = min(
            max(
                math.floor(
                    (f - config.adc2_v_min) * 4096 / (config.adc2_v_max - config.adc2_v_min)
                ),
                0,
            ),
            4095,
        )
        require(
            int(residue["adc2_code"]) == raw and int(residue["analog_bad"]) == 0,
            "raw ADC2 or analog validity differs from independent static plant",
        )
    obs_rows = [row for row in results if row["kind"] == "OBS"]
    out_rows = [row for row in results if row["kind"] == "OUT"]
    good_ids = [index + 2 for index, row in enumerate(stimulus[:n]) if not row[2]]
    require(
        [int(row["sample_id"]) for row in obs_rows] == good_ids
        and [int(row["sample_id"]) for row in out_rows] == good_ids
        and bool(good_ids),
        "DUT lost/duplicated accepted sample or emitted rejected prefix",
    )
    image = FixedPointReconstructor.from_dict(json.loads(register_path.read_text()))
    schema = json.loads((inputs / "packet_schema.json").read_text())
    # Numeric equality cannot authenticate identity: equal weights or a slot
    # permutation can preserve every code while assigning the wrong physical row.
    # Bind every packet field directly to independently recorded physical trace.
    expected_ordered_ids = []
    expected_main_arrays = []
    expected_sub_arrays = []
    for row in obs_rows:
        sid = int(row["sample_id"])
        residue = by_id[sid]
        ordered = unpack_ids(residue["converting_ids"])
        expected_ordered_ids.append(ordered)
        main_physical = int(residue["main_physical"], 16)
        sub_physical = int(residue["sub_physical"], 16)
        main_slots = [(main_physical >> (p * 63)) & ((1 << 63) - 1) for p in ordered]
        sub_slots = [(sub_physical >> (p * 8)) & 255 for p in ordered]
        expected_main_arrays.append([[(value >> u) & 1 for u in range(63)] for value in main_slots])
        expected_sub_arrays.append([[(value >> u) & 1 for u in range(8)] for value in sub_slots])
        value = int(row["packet_hex"], 16)
        require(0 <= value < 1 << schema["packet_bits"], "packet exceeds declared width")
        fields = {}
        for name in reversed(schema["field_order_msb_to_lsb"]):
            width = schema["field_widths"][name]
            fields[name] = value & ((1 << width) - 1)
            value >>= width
        total_q = dither_terms(stimulus[sid - 2][0])[1]
        expected_fields = {
            "sample_id": sid,
            "coarse": int(residue["coarse_final"]),
            "state": 0,
            "control_word": stimulus[sid - 2][0],
            "adc2_code": int(residue["adc2_code"]),
            "known_common_q": (-total_q) % (1 << schema["field_widths"]["known_common_q"]),
            "slice_ids": int(residue["converting_ids"], 16),
            "main_masks": sum(value << (63 * slot) for slot, value in enumerate(main_slots)),
            "sub_masks": sum(value << (8 * slot) for slot, value in enumerate(sub_slots)),
            "analog_bad": int(residue["analog_bad"]),
        }
        require(
            fields == expected_fields,
            f"packet identity/control/mask differs from actual physical trace at sample{sid}",
        )
    packets = [row["packet_hex"] for row in obs_rows]
    observation = observations_from_rtl(
        packets,
        image.spec,
        packet_schema=schema,
        binding_document=(inputs / "binding.json").read_bytes(),
        connection_binding=binding_features,
        static_charge_domain=True,
        fixed_configuration_epoch=True,
        require_consecutive=not early_invalid,
    )
    require(
        np.array_equal(observation.sample_ids, np.asarray(good_ids))
        and np.array_equal(observation.slice_ids, np.asarray(expected_ordered_ids))
        and np.array_equal(observation.main_masks, np.asarray(expected_main_arrays))
        and np.array_equal(observation.sub_masks, np.asarray(expected_sub_arrays)),
        "production observation identity differs from physical row/pin trace",
    )
    reconstructed = image.reconstruct(observation)
    for i, row in enumerate(out_rows):
        sid = int(row["sample_id"])
        scalar, flags = scalar_word(image, observation, i, dither_terms(stimulus[sid - 2][0])[1])
        require(
            (int(row["code"]), int(row["flags"]), int(row["analog_bad"])) == (scalar, flags, 0),
            "real retired word/flags differ from independent arbitrary-precision oracle",
        )
        require(
            int(reconstructed.code[i]) == scalar
            and int(observation.adc2_code[i]) == int(by_id[sid]["adc2_code"]),
            "production parser/integer decoder differs from actual raw/independent code",
        )
    if plan_output is not None:
        require(not plan_output.exists(), "refusing to overwrite a physical plan record")
        write_json(
            plan_output,
            {
                "rows": plans,
                "capture_samples": n,
                "program_samples": len(stimulus),
                "unretired_postamble": 3,
                "scope": "ACTUAL_SV_ORDERED_IDS_REFERENCES_WORDS_FINITE_WINDOW_NOT_DRAINED",
            },
        )
    return (
        {
            "capture_samples": n,
            "program_samples": len(stimulus),
            "source_accepts": len(controls),
            "samples": physical_count,
            "residues": physical_count,
            "good": len(good_ids),
            "early_prefix_rejected_ids": early_invalid,
            "actual_pin_event_compares": physical_compares,
            "integer_compares": len(good_ids),
            "two_node_compares": physical_count,
            "positive_capture": positive_capture,
            "normal_protocol_status_zero": positive_capture,
            "postamble_sampled": 2,
            "unretired_postamble": 3,
            "finite_capture": True,
            "capture_mode": "FINITE_WINDOW_CONTINUOUS_SOURCE_NOT_DRAINED",
            "status": "PASS",
        },
        observation,
        np.asarray([stimulus[sid - 2][1] for sid in good_ids]),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--fixture-dir", type=Path, default=REPO / "sim/fixtures/fig19_ideal_ports")
    parser.add_argument(
        "--plant-json",
        type=Path,
        help="Optional explicit fixed actual Cmain/Csub/Cbridge/Cp/Cf; plant-only",
    )
    parser.add_argument("--shared-vectors", type=Path)
    parser.add_argument("--plant-seed", type=int, default=42)
    parser.add_argument(
        "--train", type=int, default=4096, help="Learning-mode samples; unused by component-only"
    )
    parser.add_argument(
        "--holdout", type=int, default=512, help="Learning-mode samples; unused by component-only"
    )
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--component-only",
        action="store_true",
        help="Only capture the explicit64word component fixture with nominal coefficients; no SVD/training",
    )
    args = parser.parse_args()
    out = None
    manifest = {
        "status": "RUNNING",
        "scope": "FIG19_IDEAL_TWO_NODE_STATIC_DUT_NOT_PHYSICAL_SIGNOFF",
    }
    try:
        require(
            1 <= args.train <= 8192 and 1 <= args.holdout <= 8192 and 0 <= args.seed < 2**32,
            "train/holdout must be1..8192 even in component-only; seed must be uint32",
        )
        require(
            args.component_only or (args.train > 1279 and args.holdout >= 64),
            "learning mode requires train>1279 and holdout>=64",
        )
        out = new_output(args.out)
        fixture = args.fixture_dir.resolve()
        config_path, format_path, binding_path = (
            fixture / name for name in ("config.json", "format.json", "binding.json")
        )
        files = build(config_path, format_path, binding_path)
        inputs = out / "inputs"
        inputs.mkdir()
        for name, content in files.items():
            (inputs / name).write_bytes(content if isinstance(content, bytes) else content.encode())
        validate_inputs(inputs)
        cfg = Config.from_dict(json.loads((inputs / "config.json").read_text()))
        if args.plant_json is None:
            require(0 <= args.plant_seed < 2**32, "plant-seed must be uint32")
            manufacturing = default_model(
                seed=args.plant_seed,
                config_overrides={"enable_sampling_noise": False, "ra_enable_noise": False},
            )
            require(
                manufacturing.nominal_decoder.to_dict()
                == json.loads((fixture / "registers_nominal.json").read_text()),
                "custom nominal fixture requires an explicit matching plant-json",
            )
            actual = manufacturing.chip
            plant_dict = {
                name: getattr(actual, name).tolist()
                for name in ["C_main", "C_sub", "C_bridge", "c_p_main", "c_p_sub"]
            }
            plant_dict["C_feedback_true"] = actual.require_feedback()
            write_json(out / "plant_replay_only.json", plant_dict)
            manifest["manufacturing"] = {
                "seed": args.plant_seed,
                "grade": "ASSUMED_MANUFACTURING_STRESS_NOT_PDK",
                "parameters": manufacturing.inference_table(),
            }
        else:
            require(args.plant_json.stat().st_size < 300_000_000, "plant file exceeds read limit")
            plant_dict = json.loads(args.plant_json.read_bytes())
            shutil.copyfile(args.plant_json, out / "plant_replay_only.json")
            manifest["manufacturing"] = {"source": "EXPLICIT_FIXED_PLANT_JSON_NOT_LEARNER_INPUT"}
        require(
            set(plant_dict)
            == {"C_main", "C_sub", "C_bridge", "c_p_main", "c_p_sub", "C_feedback_true"},
            "plant-json requires exactly the six actual chip fields",
        )
        chip = SplitChip(**plant_dict)
        write_json(
            out / "plant_metadata.json",
            {
                **plant_dict,
                "scope": "ACTUAL_FIXED_PLANT_ONLY_NOT_LEARNER_INPUT",
                "offset_v": 0.0,
                "vfs": cfg.v_fs,
                "adc2_min_v": cfg.adc2_v_min,
                "adc2_max_v": cfg.adc2_v_max,
                "adc2_n_bits": cfg.adc2_n_bits,
                "adc2_codebook": cfg.adc2_codebook,
                "adc2_endpoint_policy": cfg.adc2_endpoint_policy,
                "adc2_max_closed": int(cfg.adc2_endpoint_policy == "closed"),
                "ra_clip_v": cfg.ra_v_clip,
                "sampled_top_v": 0.0,
                "sampled_subnode_v": 0.0,
                "actual_reference_high_v": cfg.v_fs,
                "actual_reference_low_v": -cfg.v_fs,
                "nominal_reference_high_v": cfg.v_fs,
                "nominal_reference_low_v": -cfg.v_fs,
            },
        )
        beta = chip.C_bridge / (chip.C_bridge + chip.C_sub.sum(axis=1) + chip.c_p_sub)
        plant_weights = (
            np.concatenate((chip.C_main, beta[:, None] * chip.C_sub), axis=1)
            / chip.require_feedback()
        )
        require(
            plant_weights.shape == (18, 71) and np.all(plant_weights > 0),
            "explicit plant geometry differs",
        )
        plant_path = out / "plant_effective_weights_only.txt"
        plant_path.write_text("".join(f"{float(w):.17g}\n" for w in plant_weights.flat))
        nominal = FixedPointReconstructor.from_dict(
            json.loads((fixture / "registers_nominal.json").read_text())
        )
        shutil.copyfile(fixture / "registers_nominal.json", out / "registers_nominal.json")
        shared_path = args.shared_vectors or fixture / "homogeneous_code_reference.json"
        require(shared_path.stat().st_size < 300_000_000, "reference vector exceeds read limit")
        shared = json.loads(shared_path.read_text())
        require(shared["words"] == list(range(64)), "explicit shared64word fixture required")
        shared_x = float(shared["input_v"])
        require(
            math.isfinite(shared_x) and abs(shared_x) <= 2.0, "shared input exceeds Fig19 domain"
        )
        if not args.component_only:
            rng = np.random.Generator(np.random.PCG64(args.seed))
            training = [(0, float(v), 0) for v in rng.uniform(-cfg.v_fs, cfg.v_fs, args.train)]
            holdout = [
                (i % 64, float(2.0 * np.sin(2 * np.pi * 17 * i / args.holdout + 0.37)), 0)
                for i in range(args.holdout)
            ]
        manifest.update(
            plant_sha256=digest(out / "plant_replay_only.json"),
            seed=args.seed,
            shared_vector_sha256=digest(shared_path),
            execution_mode="COMPONENT_ONLY_NO_TRAINING"
            if args.component_only
            else "DUT_STATIC_LEARNING",
            cases={},
        )
        if args.component_only:
            manifest["component_domain_v"] = [shared_x, shared_x]
            manifest["learning_arguments_consumed"] = False
            manifest["unconsumed_learning_arguments"] = {
                "train": args.train,
                "holdout": args.holdout,
                "reason": "component-only always captures the explicit64word program; no training/holdout arrays",
            }
        else:
            manifest.update(
                training_domain_v=[-cfg.v_fs, cfg.v_fs],
                holdout_domain_v=[-2.0, 2.0],
                learning_arguments_consumed=True,
            )
        names = [*CORE_SOURCES, "rtl/params/rtl_error_codes.vh", "sim/tb/fig19_static_plant_tb.sv"]
        snapshot = out / "source_snapshot"
        for name in names:
            target = snapshot / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(REPO / name, target)
        source_hashes = {name: digest(snapshot / name) for name in names}
        python_hashes = {
            str(path.relative_to(REPO)): digest(path)
            for path in sorted((REPO / "src/adi_model").glob("*.py"))
        }
        tool_names = [
            "tools/run_fig19_rtl_calibration.py",
            "tools/run_open_rtl.py",
            "tools/export_rtl_params.py",
            "tools/export_fitted_rtl.py",
        ]
        tool_hashes = {name: digest(REPO / name) for name in tool_names}
        for name in tool_names:
            target = snapshot / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(REPO / name, target)
        manifest.update(
            tool_source_sha256=tool_hashes,
            source_sha256=source_hashes,
            python_source_sha256=python_hashes,
            input_manifest_sha256=digest(inputs / "rtl_input_manifest.json"),
        )
        write_json(out / "manifest.json", manifest)
        command = command_for("verilator")
        version = checked_run(
            [*command, "-V"], cwd=out, env=os.environ.copy(), log=out / "tool.log", timeout=30
        )
        with tempfile.TemporaryDirectory(prefix="sar_fig19_dut_") as temporary:
            work = Path(temporary)
            (work / "repo").symlink_to(snapshot)
            (work / "inputs").symlink_to(inputs)
            env = os.environ.copy()
            alias_runtime(work, env, version)
            argv = [
                *command,
                "--binary",
                "--timing",
                "--assert",
                "--unroll-count",
                "8192",
                "-j",
                "2",
                "-Wall",
                *verilator_compiler_args(),
                "--top-module",
                "fig19_static_plant_tb",
                f"-GP_TICK_NS={json.loads((inputs / 'binding.json').read_text())['timing']['tick_period_ns']}",
                "--Mdir",
                str(work / "obj"),
                f"-I{work}/inputs",
                f"-I{work}/repo/rtl/params",
                *[str(work / "repo" / name) for name in names if name.endswith(".sv")],
                str(work / "inputs/source_control_binding.sv"),
                str(work / "inputs/source_acquisition_binding.sv"),
            ]
            checked_run(argv, cwd=work, env=env, log=out / "compile.log", timeout=300)
            binary = work / "obj/Vfig19_static_plant_tb"

            def run_case(name: str, target: list[tuple], decoder: FixedPointReconstructor):
                folder = out / name
                folder.mkdir()
                n = len(target)
                stimulus = [*target, (0, 0.0, 0), (0, 0.0, 0), (0, 0.0, 0)]
                expected_good = sum(not row[2] for row in target)
                positive_capture = expected_good == n
                expected_counts = {
                    "samples": n + 2,
                    "good": expected_good,
                    "residues": n + 2,
                    "observations": expected_good,
                    "outputs": expected_good,
                    "capture_samples": n,
                    "program_samples": n + 3,
                    "source_accepts": n + 3,
                    "postamble_sampled": 2,
                    "unretired_postamble": 3,
                    "finite_capture": 1,
                }
                stimulus_path = folder / "stimulus.txt"
                stimulus_path.write_text(
                    "".join(f"{word} {value:.17g} {bad}\n" for word, value, bad in stimulus)
                )
                registers = folder / "registers.json"
                write_json(registers, decoder.to_dict())
                weights_path, scalars_path = folder / "weights.hex", folder / "scalars.hex"
                weights_path.write_text("".join(f"{int(w):012x}\n" for w in decoder.weights_q.flat))
                scalars_path.write_text(
                    "".join(
                        f"{v % (1<<64):016x}\n"
                        for v in [decoder.offset_q, decoder.adc2_min_q, decoder.adc2_max_q]
                    )
                )
                run = [
                    str(binary),
                    f"+weights={weights_path}",
                    f"+scalars={scalars_path}",
                    f"+plant_weights={plant_path}",
                    "+plant_offset_v=0",
                    f"+stimulus={stimulus_path}",
                    f"+expected_samples={n}",
                    f"+program_samples={len(stimulus)}",
                    f"+expected_good={expected_good}",
                    f"+vfs={cfg.v_fs:.17g}",
                    f"+adc2_min_v={cfg.adc2_v_min:.17g}",
                    f"+adc2_max_v={cfg.adc2_v_max:.17g}",
                    f"+adc2_max_closed={int(cfg.adc2_endpoint_policy=='closed')}",
                    f"+ra_clip_v={cfg.ra_v_clip:.17g}",
                    f"+trace_dir={folder}",
                ]
                payloads = [weights_path, scalars_path, stimulus_path, registers, plant_path]
                payload_before = {p.name: digest(p) for p in payloads}
                write_json(
                    folder / "case_manifest.json",
                    {
                        "schema_version": 2,
                        "capture_mode": "FINITE_WINDOW_CONTINUOUS_SOURCE",
                        "capture_samples": n,
                        "program_samples": len(stimulus),
                        "expected_good": expected_good,
                        "expected_counts": {**expected_counts, "rdac_events": 7 * (n + 2)},
                        "postamble_records": 3,
                        "sampled_postamble_records": 2,
                        "unretired_postamble_records": 3,
                        "positive_capture": positive_capture,
                        "normal_protocol_status_required_zero": positive_capture,
                        "first_sample_id": 2,
                        "sample_id_increment": 1,
                        "sample_id_bits": 32,
                        "stimulus_columns": ["word", "reference_v", "expected_reject"],
                        "plant_sha256": digest(out / "plant_replay_only.json"),
                        "input_sha256": payload_before,
                        "trace_scope": "actualDUT physicalplan/twoidealnodes, finite continuous-source window, not drained; no physicalmacro signoff",
                    },
                )
                text = checked_run(run, cwd=work, env=env, log=folder / "run.log", timeout=180)
                require(
                    all(digest(p) == payload_before[p.name] for p in payloads),
                    "input payload drift during actualDUT case",
                )
                counts = completion(
                    text,
                    "FIG19_STATIC_PLANT_COMPLETE",
                    (*expected_counts, "checks"),
                )
                require(
                    all(counts[key] == value for key, value in expected_counts.items()),
                    "completed finite program/physical/target count differs",
                )
                rejected = re.findall(r"(?:^|\s)rejects=([0-9]+)(?=\s|$)", text)
                require(
                    len(rejected) == 1 and int(rejected[0]) == n - expected_good,
                    "completed expected-prefix reject count differs",
                )
                result, observation, reference = verify_trace(
                    folder,
                    inputs,
                    registers,
                    stimulus,
                    plant_weights,
                    cfg,
                    capture_samples=n,
                )
                result["completion_counts"] = counts
                result["trace_sha256"] = {
                    trace: digest(folder / trace)
                    for trace in (
                        "controls.csv",
                        "samples.csv",
                        "residues.csv",
                        "rdac_events.csv",
                        "events.csv",
                    )
                }
                write_json(folder / "result.json", result)
                manifest["cases"][name] = result
                write_json(out / "manifest.json", manifest)
                return observation, reference

            if args.component_only:
                run_case(
                    "component_shared64",
                    [(word, shared_x, 0) for word in shared["words"]],
                    nominal,
                )
                manifest["calibration"] = {
                    "status": "NOT_RUN",
                    "reason": "Explicit bounded component capture; no training or fitted coefficients",
                }
            else:
                # These are real nonuniform-plant samples, not the homogeneous shared64 reference.
                training_observation, reference = run_case("training_word0", training, nominal)
                fitted = fit_unit_weights(training_observation, reference)
                write_json(out / "frozen_fit.json", fitted.to_dict())
                register_text, fitted_manifest = build_fitted_registers(
                    config_path, format_path, binding_path, out / "frozen_fit.json"
                )
                (out / "fitted_registers.json").write_text(register_text)
                (out / "fitted_register_manifest.json").write_text(fitted_manifest)
                corrected = FixedPointReconstructor.from_dict(json.loads(register_text))
                before_obs, hold_reference = run_case("holdout_nominal", holdout, nominal)
                after_obs, _ = run_case("holdout_fitted", holdout, corrected)
                require(
                    np.array_equal(before_obs.adc2_code, after_obs.adc2_code)
                    and np.array_equal(before_obs.slice_ids, after_obs.slice_ids)
                    and np.array_equal(before_obs.main_masks, after_obs.main_masks)
                    and np.array_equal(before_obs.sub_masks, after_obs.sub_masks)
                    and np.array_equal(
                        before_obs.known_voltage_term_v, after_obs.known_voltage_term_v
                    ),
                    "nominal/fitted holdout changed physical observations",
                )
                before_error = nominal.reconstruct(before_obs).voltage - hold_reference
                after_error = corrected.reconstruct(after_obs).voltage - hold_reference
                before_rms, after_rms = (
                    float(np.sqrt(np.mean(error**2))) for error in [before_error, after_error]
                )
                require(
                    after_rms < before_rms,
                    "frozen coefficient application did not improve same-raw holdout RMS",
                )
                manifest["calibration"] = {
                    "rank": fitted.rank,
                    "parameters": 1279,
                    "condition": fitted.condition,
                    "training_samples": args.train,
                    "holdout_samples": args.holdout,
                    "nominal_rms_v": before_rms,
                    "fitted_rms_v": after_rms,
                    "nominal_std_v": float(np.std(before_error)),
                    "fitted_std_v": float(np.std(after_error)),
                    "coefficient_source": "SVD_DUT_PACKETS_AND_KNOWN_REFERENCE_ONLY",
                    "same_physical_observations": True,
                }
                # Separate full dynamic-node negative controls use the same actual chip.
                run_case(
                    "early_prefix_negative",
                    [
                        (0, shared_x, 0),
                        (32, -2.9, 1),
                        (63, shared_x, 0),
                        (1, -2.0, 0),
                        (40, -2.9, 1),
                        (0, -2.99, 0),
                    ],
                    corrected,
                )
        require(
            all(digest(REPO / name) == expected for name, expected in source_hashes.items()),
            "source drift during the actual RTL/learning cohort",
        )
        require(
            all(digest(REPO / name) == expected for name, expected in tool_hashes.items()),
            "consumed tool source drift during the cohort",
        )
        require(
            all(digest(REPO / name) == expected for name, expected in python_hashes.items()),
            "consumed Python source drift during the cohort",
        )
        manifest.update(
            status=(
                "PASS_COMPONENT_CAPTURE_NO_TRAINING_PENDING_INDEPENDENT_PYTHON_REPLAY"
                if args.component_only
                else "PASS_DUT_LEARNING_PENDING_INDEPENDENT_SAME_CHIP_PYTHON_REPLAY"
            ),
            source_sha256=source_hashes,
            tool_version=version,
            physical_signoff="NOT_RUN",
            python_same_chip_replay="PENDING",
            capture_mode="FINITE_WINDOW_CONTINUOUS_SOURCE_NOT_DRAINED",
            unretired_postamble_per_case=3,
        )
        write_json(out / "manifest.json", manifest)
        if args.component_only:
            print("FIG19_RTL_COMPONENT_COMPLETE outputs=64 training=NOT_RUN replay=PENDING")
        else:
            print(
                f"FIG19_RTL_STATIC_LEARNING_COMPLETE rank={fitted.rank} holdout={args.holdout} same_raw=1 replay=PENDING"
            )
        return 0
    except (OSError, ValueError, RuntimeError) as error:
        if out is not None:
            manifest.update(status="FAIL", error=str(error))
            write_json(out / "manifest.json", manifest)
        print(f"FAIL: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
