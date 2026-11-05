#!/usr/bin/env python3
"""用精确整数方程核对指定的合成源控制组件轨迹。

核对器读取显式系数镜像，独立按物理单元计算选通与电荷项，不使用 DUT 的
归约中间节点或行为模型系数。结果仅覆盖该数字组件夹具，不证明实际 ADC、
原文开关映射等价、自归零噪声抵消或时序收敛。
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def read_rows(path: Path, columns: list[str]) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        require(reader.fieldnames == columns, f"unexpected CSV fields: {path}")
        rows = list(reader)
    require(
        bool(rows) and all(None not in row and None not in row.values() for row in rows),
        f"empty/malformed trace: {path}",
    )
    return rows


def rounded_ratio(numerator: int, denominator: int) -> int:
    quotient, remainder = divmod(numerator, denominator)
    return quotient + int(
        2 * remainder > denominator or (2 * remainder == denominator and quotient & 1)
    )


def decode_packet(text: str, schema: dict) -> dict:
    value = int(text, 16)
    require(0 <= value < 1 << schema["packet_bits"], "packet exceeds declared width")
    result = {}
    for field in reversed(schema["field_order_msb_to_lsb"]):
        width = schema["field_widths"][field]
        result[field] = value & ((1 << width) - 1)
        value >>= width
    require(value == 0, "packet field widths do not cover the record")
    bits = schema["field_widths"]["known_common_q"]
    if result["known_common_q"] >= 1 << (bits - 1):
        result["known_common_q"] -= 1 << bits
    return result


def expected_masks(coarse: int, state: int, ids: list[int]) -> tuple[list, list]:
    """按显式小型绑定独立计算标量选通，不读取 DUT 内部结果。"""
    main = [
        [
            ((coarse >> ((unit + physical) % 9)) & 1) ^ ((state >> (slot % 3)) & 1)
            for unit in range(2)
        ]
        for slot, physical in enumerate(ids)
    ]
    sub = [
        [((coarse >> ((slot + 1) % 9)) & 1) ^ ((state >> ((slot + 1) % 3)) & 1)]
        for slot in range(8)
    ]
    return main, sub


def expected_code(
    packet: dict, image: dict, ids: list[int], main: list, sub: list
) -> tuple[int, int]:
    """逐单元累加量化电压项，不复用 T/G/R 归约结果。"""
    fmt, spec = image["format"], image["spec"]
    fine = image["adc2_min_q"] + rounded_ratio(
        (2 * packet["adc2_code"] + 1) * (image["adc2_max_q"] - image["adc2_min_q"]),
        1 << (spec["adc2_n_bits"] + 1),
    )
    gain = 0
    known_sum = 0
    for slot, physical in enumerate(ids):
        for unit, on in enumerate(main[slot] + sub[slot]):
            weight = image["weights_q"][physical][unit]
            gain += weight
            term = packet["known_common_q"] + (1 - 2 * on) * (1 << fmt["voltage_fraction_bits"])
            known_sum += weight * term
    numerator = (fine - image["offset_q"]) * (1 << fmt["weight_fraction_bits"]) - known_sum
    shifted = (numerator + gain * (1 << fmt["voltage_fraction_bits"])) * (1 << fmt["output_bits"])
    denominator = 2 * gain * (1 << fmt["voltage_fraction_bits"])
    limit = 1 << (fmt["accumulator_bits"] - 1)
    require(
        gain > 0 and all(-limit <= x < limit for x in (known_sum, numerator, shifted, denominator)),
        "fixture exceeded its declared integer arithmetic domain",
    )
    quotient = shifted // denominator
    flags = (8 if quotient < 0 else 0) | (16 if quotient >= 1 << 20 else 0)
    return min(max(quotient, 0), (1 << 20) - 1), flags


def verify(inputs: Path, registers: Path, traces: Path, caaz_enabled: bool) -> dict:
    schema = json.loads((inputs / "packet_schema.json").read_text())
    image = json.loads(registers.read_text())
    manifest = json.loads((inputs / "rtl_input_manifest.json").read_text())
    for name, digest in manifest["artifact_sha256"].items():
        require(
            hashlib.sha256((inputs / name).read_bytes()).hexdigest() == digest,
            f"input drift: {name}",
        )
    require(
        schema["schema_version"] == 2 and schema["packed_index_order"] == "slot0_lsb_unit0_lsb",
        "explicit v2 packet schema required",
    )
    spec = image["spec"]
    require(
        (
            spec["n_slices"],
            spec["n_active"],
            spec["dac_n_main"],
            spec["dac_n_sub"],
            spec["adc2_n_bits"],
        )
        == (18, 8, 2, 1, 6),
        "named numerical fixture required",
    )
    controls = read_rows(
        traces / "controls.csv",
        [
            "epoch",
            "owner_sample_id",
            "input_valid",
            "input_word",
            "input_state",
            "actual_valid",
            "actual_word",
            "actual_state",
            "acquiring_mask",
            "converting_mask",
            "input_chop",
        ],
    )
    residues = read_rows(
        traces / "residues.csv",
        [
            "epoch",
            "sample_id",
            "converting_mask",
            "selected_mask",
            "main_physical",
            "sub_physical",
            "sw_valid",
        ],
    )
    events = read_rows(
        traces / "events.csv",
        [
            "epoch",
            "kind",
            "sample_id",
            "code",
            "flags",
            "analog_bad",
            "packet_hex",
            "caaz_hex",
            "status_word",
            "analog_sticky",
        ],
    )
    result = {
        "scope": "COMPONENT_SYNTHETIC_DIGITAL",
        "epochs": 2,
        "acquisition_checks": 0,
        "packet_checks": 0,
        "physical_mask_checks": 0,
        "integer_compares": 0,
        "tag_checks": 0,
        "pair_compares": 0,
        "cancelled_after_launch": 0,
        "invalid_word_samples": 0,
        "caaz_enabled": caaz_enabled,
    }
    for epoch in range(2):
        group = [row for row in controls if int(row["epoch"]) == epoch]
        require(len(group) >= 24, "insufficient acquisition coverage")
        by_id = {}
        previous = None
        for index, row in enumerate(group):
            owner = int(row["owner_sample_id"])
            require(
                owner == index + 2 and owner not in by_id, "duplicate/missing acquisition owner"
            )
            valid = owner != 8
            word = owner % 4
            state = 7 if owner == 12 else owner % 7
            require(
                [int(row[k]) for k in ("input_valid", "input_word", "input_state")]
                == [int(valid), word, state],
                "explicit stimulus recipe changed",
            )
            require(
                int(row["actual_valid"]) == int(valid), "invalid/valid control ownership changed"
            )
            if valid:
                require(
                    int(row["actual_word"]) == word and int(row["actual_state"]) == state,
                    "registered acquisition word/state mismatch",
                )
            elif previous:
                require(
                    row["actual_word"] == previous["actual_word"]
                    and row["actual_state"] == previous["actual_state"],
                    "invalid acquisition silently substituted another word",
                )
            acquire, convert = int(row["acquiring_mask"], 16), int(row["converting_mask"], 16)
            require(
                acquire.bit_count() == 8 and convert.bit_count() == 8 and not acquire & convert,
                "8/8/2 resource contract failed",
            )
            if previous:
                require(
                    convert == int(previous["acquiring_mask"], 16),
                    "conversion did not own preceding acquisition",
                )
            require(
                int(row["input_chop"]) == (index % 2 if caaz_enabled else 0),
                "actual aperture chop sequence changed",
            )
            by_id[owner] = row
            previous = row
            result["acquisition_checks"] += 1
        physical = {int(row["sample_id"]): row for row in residues if int(row["epoch"]) == epoch}
        expected = {}
        expected_tags = {}
        seen_packets = set()
        selected_events = [row for row in events if int(row["epoch"]) == epoch]
        for row in selected_events:
            if row["kind"] != "OBS":
                continue
            packet = decode_packet(row["packet_hex"], schema)
            sample = packet["sample_id"]
            require(
                sample in by_id and sample not in seen_packets,
                "observation lost/duplicated sample identity",
            )
            owner = by_id[sample]
            require(
                int(owner["input_valid"]) == 1 and int(owner["input_state"]) != 7,
                "invalid control word reused as a good sample",
            )
            require(
                packet["coarse"] == 173
                and packet["adc2_code"] == 32
                and packet["control_word"] == int(owner["input_word"])
                and packet["state"] == int(owner["input_state"]),
                "live/current state replaced the previous residue context",
            )
            require(
                packet["known_common_q"] == packet["control_word"] << 8,
                "common term ownership mismatch",
            )
            ids = [(packet["slice_ids"] >> (5 * slot)) & 31 for slot in range(8)]
            require(
                len(set(ids)) == 8
                and all(value < 18 for value in ids)
                and sum(1 << value for value in ids) == int(owner["acquiring_mask"], 16),
                "physical ID ownership mismatch",
            )
            main, sub = expected_masks(173, packet["state"], ids)
            packed_main = sum(
                bit << (2 * slot + unit) for slot in range(8) for unit, bit in enumerate(main[slot])
            )
            packed_sub = sum(sub[slot][0] << slot for slot in range(8))
            require(
                packet["main_masks"] == packed_main and packet["sub_masks"] == packed_sub,
                "actual binding mask mismatch",
            )
            residue = physical[sample]
            require(
                int(residue["selected_mask"], 16) == int(owner["acquiring_mask"], 16)
                and int(residue["sw_valid"]) == 1,
                "physical RDAC did not accept the final resolved decision",
            )
            require(
                int(residue["main_physical"], 16)
                == sum(
                    bit << (2 * physical_id + unit)
                    for slot, physical_id in enumerate(ids)
                    for unit, bit in enumerate(main[slot])
                )
                and int(residue["sub_physical"], 16)
                == sum(sub[slot][0] << physical_id for slot, physical_id in enumerate(ids)),
                "packet differs from actual physical RDAC pins",
            )
            require(
                packet["analog_bad"] == int(sample == 6), "external fault sampled for wrong residue"
            )
            code, flags = expected_code(packet, image, ids, main, sub)
            require(flags == 0, "chosen integration fixture unexpectedly clips")
            side = int(row["caaz_hex"], 16)
            if caaz_enabled:
                require(
                    (side >> 33, (side >> 1) & ((1 << 32) - 1), side & 1)
                    == (int(owner["input_chop"]), (sample - 2) // 2, 1),
                    "CAAZ generation/tag context mismatch",
                )
                result["tag_checks"] += 1
                expected_tags[sample] = (side >> 33, (side >> 1) & ((1 << 32) - 1), side & 1)
            else:
                require(side == 0, "disabled optional tags changed the component")
            expected[sample] = (code, flags, packet["analog_bad"])
            seen_packets.add(sample)
            result["packet_checks"] += 1
            result["physical_mask_checks"] += 1
        require(8 not in expected and 12 not in expected, "invalid source controls retired")
        for sample in (8, 12):
            require(
                int(physical[sample]["sw_valid"]) == 0,
                "invalid source command loaded a new RDAC word",
            )
            result["invalid_word_samples"] += 1
        raw = [row for row in selected_events if row["kind"] == ("RAW" if caaz_enabled else "OUT")]
        require(bool(raw), "zero numerical comparisons")
        seen_raw = set()
        for row in raw:
            sample = int(row["sample_id"])
            require(
                sample in expected and sample not in seen_raw, "unexpected/duplicate retirement"
            )
            require(
                (int(row["code"]), int(row["flags"]), int(row["analog_bad"])) == expected[sample],
                "independent integer reconstruction mismatch",
            )
            if caaz_enabled:
                value = int(row["packet_hex"], 16)
                require(
                    (value >> 60, (value >> 40) & ((1 << 20) - 1), value & 1)
                    == (sample, int(row["code"]), int(row["analog_bad"])),
                    "raw packet/output mismatch",
                )
                require(
                    ((value >> 39) & 1, (value >> 7) & ((1 << 32) - 1), (value >> 6) & 1)
                    == expected_tags[sample],
                    "raw result substituted live CAAZ metadata",
                )
            seen_raw.add(sample)
            result["integer_compares"] += 1
        cancelled = set(expected) - seen_raw
        require(
            len(cancelled) == 1 and any(row["kind"] == "END" for row in selected_events),
            "only the explicitly cleared in-flight launch may be cancelled",
        )
        result["cancelled_after_launch"] += len(cancelled)
        if caaz_enabled:
            expected_pairs = []
            pending = None
            fault = False
            for row in raw:
                sample = int(row["sample_id"])
                code, flags, bad = expected[sample]
                chop, az_id, az_valid = expected_tags[sample]
                if fault:
                    continue
                if flags or bad:
                    fault, pending = True, None
                    continue
                if pending is None:
                    if chop != 0 or not az_valid:
                        fault = True
                    else:
                        pending = (sample, code, az_id)
                elif (
                    sample != ((pending[0] + 1) & ((1 << 32) - 1))
                    or chop != 1
                    or not az_valid
                    or az_id != pending[2]
                ):
                    fault, pending = True, None
                else:
                    expected_pairs.append((sample, (pending[1] - code + (1 << 20)) // 2))
                    pending = None
            actual_pairs = [
                (int(row["sample_id"]), int(row["code"]))
                for row in selected_events
                if row["kind"] == "OUT"
            ]
            require(
                actual_pairs == expected_pairs and bool(actual_pairs) and fault,
                "CAAZ integer pairing/fault isolation mismatch",
            )
            result["pair_compares"] += len(actual_pairs)
    result["status"] = "PASS_DIGITAL_COMPONENT_ONLY"
    result["physical_mapping_timing_AZ_noise"] = "NOT_VERIFIED"
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--inputs-dir", type=Path, required=True)
    parser.add_argument("--registers", type=Path, required=True)
    parser.add_argument("--trace-dir", type=Path, required=True)
    parser.add_argument("--caaz", type=int, choices=(0, 1), required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    require(not args.out.exists(), "refusing existing checker output")
    result = verify(args.inputs_dir, args.registers, args.trace_dir, bool(args.caaz))
    args.out.mkdir(parents=True, exist_ok=False)
    (args.out / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
