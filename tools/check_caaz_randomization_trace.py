#!/usr/bin/env python3
"""Check the opt-in CAAZ randomization diagnostic using independent integer arithmetic.

Inputs are events.txt and ticks.csv from caaz_randomization_probe_tb. All eleven
bounded epochs are required. Codes are 20-bit offset-binary; weights use Q30,
injection/backend voltage use signed Q32, and time_ns records POST-NBA samples.
A passing result establishes digital ownership and arithmetic only. It does not
establish H_AZ/K matching, CBIAS storage, held-sample safety or physical timing.
The complete production model and any analog calibration oracle are not imported.
"""

from __future__ import annotations

import argparse
import collections
import csv
import hashlib
import json
from pathlib import Path

TRACE_COLUMNS = [
    "epoch",
    "mode",
    "dem",
    "tick",
    "time_ns",
    "phase",
    "current_id",
    "primed",
    "bank",
    "acq_mask",
    "conv_mask",
    "acq_chop",
    "current_chop",
    "acq_dither",
    "current_dither",
    "acq_rails",
    "qdither0",
    "qdither1",
    "sid",
    "sid_a",
    "sid_b",
    "az_pair",
    "az_valid",
    "ra_az",
    "az_capture",
    "az_hold",
    "fine_id",
    "recon_busy",
    "raw_valid",
    "pair_valid",
    "pair_fault",
    "tp_clock",
]
EVENT_FIELDS = {"AP": 12, "ADV": 10, "CAP": 17, "OBS": 10, "RAW": 6, "PAIR": 10}


def signed(value, width):
    return value - (1 << width) if value >> (width - 1) else value


def split(value, widths):
    result = []
    for width in widths:
        result.append(value & ((1 << width) - 1))
        value >>= width
    if value != 0:
        raise ValueError("packed event exceeds its declared unsigned width")
    return result


def items(value, count, width):
    return [(value >> (width * index)) & ((1 << width) - 1) for index in range(count)]


def weight(slice_id, unit_id):
    return (
        67108864 + slice_id * 262144 + unit_id * 127
        if unit_id < 63
        else 8388608 + slice_id * 32768 + unit_id * 73
    )


def lfsr_next(value):
    for _ in range(8):
        value = (value >> 1) ^ (0x80200003 if value & 1 else 0)
    return value


def random_dither(value):
    return ((value >> 24) % 8 + 1) // 2 - 2


def round_even(numerator, denominator):
    q, r = divmod(numerator, denominator)
    return q + (r * 2 > denominator or (r * 2 == denominator and q & 1))


def analyze_trace(trace_directory: Path, output_directory: Path) -> dict:
    """Validate the fixed diagnostic trace and write reproducible result artifacts."""
    for name in ("events.txt", "ticks.csv"):
        path = trace_directory / name
        if not path.is_file() or path.stat().st_size > 300_000_000:
            raise ValueError(f"{path}: a regular trace file smaller than 300 MB is required")
    checks = collections.Counter()
    failures = []

    def check(condition, label, detail):
        checks[label] += 1
        if not condition:
            failures.append({"check": label, "detail": detail})

    epochs = collections.defaultdict(
        lambda: {"ap": {}, "advance": {}, "capture": {}, "obs": {}, "raw": {}, "pairs": []}
    )
    for line_number, line in enumerate(
        (trace_directory / "events.txt").read_text(encoding="utf-8").splitlines(), 1
    ):
        fields = line.split()
        if len(fields) < 5:
            raise ValueError(f"events.txt:{line_number}: missing event prefix")
        event, epoch, mode, dem, tick = fields[:5]
        if event not in EVENT_FIELDS or len(fields) != EVENT_FIELDS[event]:
            raise ValueError(f"events.txt:{line_number}: unknown event or incorrect field count")
        epoch, mode, dem, tick = map(int, (epoch, mode, dem, tick))
        row = epochs[epoch]
        if "mode" in row and (row["mode"], row["dem"]) != (mode, dem):
            raise ValueError(f"events.txt:{line_number}: mode changed within an epoch")
        row.update(mode=mode, dem=dem)
        payload = fields[5:]
        if event == "AP":
            sid, chop, mask, dither, rails, injection, ids = payload
            sid = int(sid)
            if sid in row["ap"]:
                raise ValueError(f"events.txt:{line_number}: duplicate AP sample ID{sid}")
            row["ap"][sid] = {
                "chop": int(chop),
                "mask": int(mask, 16),
                "dither": int(dither),
                "rails": int(rails, 16),
                "injection": int(injection),
                "ids": items(int(ids, 16), 8, 5),
                "line": line_number,
                "tick": tick,
            }
        elif event == "ADV":
            sid, bank, sid_a, sid_b, dither = map(int, payload)
            if sid in row["advance"]:
                raise ValueError(f"events.txt:{line_number}: duplicate ADV sample ID{sid}")
            row["advance"][sid] = {"bank": bank, "a": sid_a, "b": sid_b, "random": dither}
        elif event == "CAP":
            sid, valid, chop, az_pair, az_valid, dither = map(int, payload[:6])
            ids, main, sub, rails = (int(value, 16) for value in payload[6:10])
            injection, dem_sid = map(int, payload[10:12])
            if sid in row["capture"]:
                raise ValueError(f"events.txt:{line_number}: duplicate CAP sample ID{sid}")
            row["capture"][sid] = {
                "valid": valid,
                "chop": chop,
                "az_pair": az_pair,
                "az_valid": az_valid,
                "dither": dither,
                "ids": items(ids, 8, 5),
                "main": main,
                "sub": sub,
                "rails": rails,
                "injection": injection,
                "sid": dem_sid,
            }
        elif event == "OBS":
            packet, caaz = (int(value, 16) for value in payload[:2])
            fine, injection, bad, sampling, rails, sub, main, ids, dither, dem_sid, coarse, sid = (
                split(packet, [12, 64, 1, 1, 4, 64, 504, 40, 8, 9, 9, 32])
            )
            az_valid, az_pair, chop = split(caaz, [1, 32, 1])
            if sid in row["obs"]:
                raise ValueError(f"events.txt:{line_number}: duplicate OBS sample ID{sid}")
            row["obs"][sid] = {
                "fine": fine,
                "injection": signed(injection, 64),
                "bad": bad,
                "sampling": sampling,
                "rails": rails,
                "sub": sub,
                "main": main,
                "ids": items(ids, 8, 5),
                "dither": signed(dither, 8),
                "sid": dem_sid,
                "coarse": coarse,
                "az_valid": az_valid,
                "az_pair": az_pair,
                "chop": chop,
                "dut_total": int(payload[2]),
                "dut_gain": int(payload[3]),
                "dut_rails": int(payload[4]),
                "line": line_number,
                "tick": tick,
            }
        elif event == "RAW":
            bad, flags, az_valid, az_pair, chop, code, sid = split(
                int(payload[0], 16), [1, 5, 1, 32, 1, 20, 32]
            )
            if sid in row["raw"]:
                raise ValueError(f"events.txt:{line_number}: duplicate RAW sample ID{sid}")
            row["raw"][sid] = {
                "code": code,
                "chop": chop,
                "az_pair": az_pair,
                "az_valid": az_valid,
                "flags": flags,
                "bad": bad,
                "line": line_number,
                "tick": tick,
            }
        elif event == "PAIR":
            first, last, az_pair, code, difference = map(int, payload)
            row["pairs"].append(
                {
                    "first": first,
                    "last": last,
                    "az_pair": az_pair,
                    "code": code,
                    "difference": difference,
                    "line": line_number,
                    "tick": tick,
                }
            )
        else:
            raise ValueError(event)

    if set(epochs) != set(range(11)):
        raise ValueError("events.txt: the complete eleven-epoch fixture is required")
    for epoch, row in epochs.items():
        expected_mode = epoch % 4 if epoch < 8 else (2 if epoch == 10 else 3)
        expected_dem = epoch // 4 if epoch < 8 else 1
        if (row["mode"], row["dem"]) != (expected_mode, expected_dem):
            raise ValueError(f"events.txt: incorrect mode/DEM fixture at epoch {epoch}")
        expected_raw = 125 if epoch < 8 or epoch == 9 else (123 if epoch == 8 else 1)
        expected_pairs = 62 if epoch < 8 else (7 if epoch < 10 else 0)
        if len(row["raw"]) != expected_raw or len(row["pairs"]) != expected_pairs:
            raise ValueError(f"events.txt: incomplete raw/pair fixture at epoch {epoch}")

    summary = []
    sample_rows = []
    for epoch, row in sorted(epochs.items()):
        mode, dem = row["mode"], row["dem"]
        states = [0, 0]
        prng = 0x13579BDF
        for sid, advance in row["advance"].items():
            check(
                [advance["a"], advance["b"]] == (states if dem else [0, 0]),
                "dem_bank_sequence",
                [epoch, sid],
            )
            expected_sid = states[advance["bank"]] if dem else 0
            advance["expected_sid"] = expected_sid
            check(advance["random"] == random_dither(prng), "independent_prng", [epoch, sid])
            prng = lfsr_next(prng)
            if dem:
                states[advance["bank"]] = (states[advance["bank"]] + 433) % 512
        adjacent_overlap = []
        last = None
        for sid, aperture in row["ap"].items():
            ids = aperture["ids"]
            check(
                len(set(ids)) == 8 and all(0 <= s < 18 for s in ids),
                "aperture_ids_legal",
                [epoch, sid],
            )
            check(sum(1 << s for s in ids) == aperture["mask"], "aperture_ids_mask", [epoch, sid])
            check(aperture["chop"] == (sid - 2) % 2, "aperture_chop_sequence", [epoch, sid])
            expected_dither = row["advance"][sid - 1]["random"] if mode in (2, 3) else 0
            check(
                aperture["dither"] == expected_dither, "acquisition_random_ownership", [epoch, sid]
            )
            expected_rails = (1 << (2 + expected_dither)) - 1 if mode == 3 else 0
            check(aperture["rails"] == expected_rails, "acquisition_rail_ownership", [epoch, sid])
            if last is not None:
                overlap = aperture["mask"] & last["mask"]
                adjacent_overlap.append(bin(overlap).count("1"))
                check(overlap == 0, "adjacent_conversion_disjoint", [epoch, sid])
            last = aperture
        for sid, capture in row["capture"].items():
            aperture = row["ap"][sid]
            check(
                all(
                    capture[name] == aperture[name]
                    for name in ("chop", "dither", "injection", "ids")
                ),
                "aperture_to_residue",
                [epoch, sid],
            )
            check(capture["sid"] == row["advance"][sid]["expected_sid"], "frozen_sid", [epoch, sid])
            check(capture["az_pair"] == (sid - 2) // 2, "az_generation", [epoch, sid])
            check(
                capture["az_valid"] == (not (epoch == 9 and sid in (16, 17))),
                "az_validity",
                [epoch, sid],
            )
        for sid, observation in row["obs"].items():
            capture = row["capture"][sid]
            check(capture["valid"], "observed_valid_residue", [epoch, sid])
            check(
                all(
                    observation[name] == capture[name]
                    for name in (
                        "chop",
                        "dither",
                        "injection",
                        "ids",
                        "main",
                        "sub",
                        "rails",
                        "sid",
                        "az_pair",
                        "az_valid",
                    )
                ),
                "residue_to_fine_packet",
                [epoch, sid],
            )
            check(observation["sampling"] == (mode == 3), "sampling_mode_context", [epoch, sid])
            nominal_units = 0
            for index in range(8):
                main_bits = (observation["main"] >> (63 * index)) & ((1 << 63) - 1)
                sub_bits = (observation["sub"] >> (8 * index)) & 255
                nominal_units += 8 * bin(main_bits).count("1") + bin(sub_bits).count("1")
            expected_rdac_adjust = (
                -observation["dither"] if mode == 2 else observation["dither"] if mode == 3 else 0
            )
            check(
                nominal_units == 8 * (observation["coarse"] + expected_rdac_adjust),
                "actual_mask_dither_rdac_adjust",
                [epoch, sid],
            )
            total = gain = on = mask_total = rail_dither = 0
            for index, slice_id in enumerate(observation["ids"]):
                physical_mask = ((observation["main"] >> (63 * index)) & ((1 << 63) - 1)) | (
                    ((observation["sub"] >> (8 * index)) & 255) << 63
                )
                for unit in range(71):
                    w = weight(slice_id, unit)
                    total += w
                    if physical_mask >> unit & 1:
                        on += w
                    if unit >= 67:
                        mask_total += w
                        rail_dither += w if observation["rails"] >> (unit - 67) & 1 else -w
            gain = total - mask_total if mode == 3 else total
            rails = total - 2 * on + (rail_dither if mode == 3 else 0)
            check(
                (total, gain, rails)
                == (observation["dut_total"], observation["dut_gain"], observation["dut_rails"]),
                "independent_physical_weight_sums",
                [epoch, sid],
            )
            fine = -53687091 + round_even(
                (2 * observation["fine"] + 1) * (590558003 + 53687091), 8192
            )
            numerator = (fine << 30) - (rails << 32) - total * observation["injection"]
            a1 = ((numerator + (gain << 32)) << 20) // (1 << 33)
            expected = min((1 << 20) - 1, max(0, a1 // gain))
            observation.update(expected_raw=expected, total=total, gain=gain, weighted_rails=rails)
            if sid in row["raw"]:
                raw = row["raw"][sid]
                check(
                    raw["code"] == expected,
                    "independent_raw_code",
                    [epoch, sid, expected, raw["code"]],
                )
                check(
                    all(raw[name] == observation[name] for name in ("chop", "az_pair", "az_valid")),
                    "raw_metadata",
                    [epoch, sid],
                )
            sample_rows.append(
                {
                    "epoch": epoch,
                    "mode": mode,
                    "dem": dem,
                    "sample_id": sid,
                    "chop": observation["chop"],
                    "az_pair": observation["az_pair"],
                    "slice_mask": hex(sum(1 << s for s in observation["ids"])),
                    "slices": " ".join(map(str, observation["ids"])),
                    "sid": observation["sid"],
                    "dither": observation["dither"],
                    "rails": observation["rails"],
                    "injection_q32": observation["injection"],
                    "total_q30": total,
                    "gain_q30": gain,
                    "expected_raw": expected,
                    "raw_code": row["raw"].get(sid, {}).get("code"),
                    "obs_event_line": observation["line"],
                }
            )
        pair_overlap = []
        sid_matches = 0
        gain_matches = 0
        first_pair = None
        for pair in row["pairs"]:
            even, odd = row["raw"][pair["first"]], row["raw"][pair["last"]]
            eo, oo = row["obs"][pair["first"]], row["obs"][pair["last"]]
            check(
                pair["last"] == pair["first"] + 1 and even["chop"] == 0 and odd["chop"] == 1,
                "pair_actual_polarity_id",
                [epoch, pair["last"]],
            )
            check(
                pair["az_pair"] == even["az_pair"] == odd["az_pair"],
                "pair_az_generation",
                [epoch, pair["last"]],
            )
            difference = even["code"] - odd["code"]
            check(
                pair["difference"] == difference and pair["code"] == ((1 << 20) + difference) // 2,
                "independent_pair_code",
                [epoch, pair["last"]],
            )
            overlap = len(set(eo["ids"]) & set(oo["ids"]))
            pair_overlap.append(overlap)
            sid_matches += eo["sid"] == oo["sid"]
            gain_matches += eo["gain"] == oo["gain"]
            if first_pair is None:
                first_pair = {
                    "even_id": pair["first"],
                    "odd_id": pair["last"],
                    "even_slices": eo["ids"],
                    "odd_slices": oo["ids"],
                    "even_sid": eo["sid"],
                    "odd_sid": oo["sid"],
                    "even_dither": eo["dither"],
                    "odd_dither": oo["dither"],
                    "even_gain_q30": eo["gain"],
                    "odd_gain_q30": oo["gain"],
                    "even_total_q30": eo["total"],
                    "odd_total_q30": oo["total"],
                    "pair_event_line": pair["line"],
                }
        summary.append(
            {
                "epoch": epoch,
                "mode": mode,
                "dem": dem,
                "apertures": len(row["ap"]),
                "captures": len(row["capture"]),
                "observations": len(row["obs"]),
                "raw_outputs": len(row["raw"]),
                "pair_outputs": len(row["pairs"]),
                "adjacent_overlap_max": max(adjacent_overlap, default=0),
                "pair_overlap_max": max(pair_overlap, default=0),
                "pair_same_sid_count": sid_matches,
                "pair_equal_gain_count": gain_matches,
                "dither_values": sorted({value["dither"] for value in row["ap"].values()}),
                "first_pair": first_pair,
            }
        )

    with (trace_directory / "ticks.csv").open(newline="", encoding="utf-8") as trace_file:
        reader = csv.DictReader(trace_file)
        if reader.fieldnames != TRACE_COLUMNS:
            raise ValueError("ticks.csv: unexpected trace column schema")
        ticks = list(reader)
    if len(ticks) != 20617:
        raise ValueError(f"ticks.csv: incomplete fixture, expected 20617 ticks, got {len(ticks)}")
    if any(None in tick or any(value is None for value in tick.values()) for tick in ticks):
        raise ValueError("ticks.csv: missing or extra fields")
    for tick in ticks:
        epoch = int(tick["epoch"])
        mode = int(tick["mode"])
        phase = int(tick["phase"])
        key = [epoch, int(tick["tick"])]
        # clear/disabled tails have cfg_ready low; phase0 all controls reset.
        if int(tick["primed"]) == 0:
            continue
        check(int(tick["tp_clock"]) == (phase != 0), "per_tick_registered_tp", key)
        current_dither = int(tick["current_dither"])
        acquiring_dither = int(tick["acq_dither"])
        expected_acquisition_rails = (1 << (2 + acquiring_dither)) - 1 if mode == 3 else 0
        check(
            int(tick["acq_rails"], 16) == expected_acquisition_rails,
            "per_tick_acquisition_rails",
            key,
        )
        expected_q = [0, 0]
        if mode == 2:
            expected_q[int(tick["bank"])] = current_dither
            expected_q[1 - int(tick["bank"])] = acquiring_dither
        check(
            [int(tick["qdither0"]), int(tick["qdither1"])] == expected_q,
            "quantizer_current_acquiring_domain",
            key,
        )
        owner = int(tick["current_id"]) + (phase == 1)
        aperture = epochs[epoch]["ap"].get(owner)
        if aperture is not None:
            check(int(tick["current_chop"]) == aperture["chop"], "per_tick_ra_owner", key)
            check(
                int(tick["az_capture"]) == (phase in range(1, 9) and aperture["chop"] == 0),
                "per_tick_even_capture",
                key,
            )
            check(int(tick["az_hold"]) == aperture["chop"], "per_tick_odd_hold", key)

    with (output_directory / "samples.csv").open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=list(sample_rows[0]))
        writer.writeheader()
        writer.writerows(sample_rows)
    result = {
        "status": "PASS_DIGITAL_DIAGNOSTIC" if not failures else "FAIL",
        "checks": dict(checks),
        "total_checks": sum(checks.values()),
        "failure_count": len(failures),
        "failures": failures[:100],
        "epochs": summary,
        "ticks": len(ticks),
        "trace_sha256": {
            p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in (trace_directory / "events.txt", trace_directory / "ticks.csv")
        },
        "limits": [
            "Ideal comparator fixture, no shared analog AZ state simulated",
            "Nonuniform effective weight fixture, not measured capacitance",
            "No a_even == a_odd check exists in production pair processor",
            "No PDK, STA, analog settling or switched-capacitor noise claim",
        ],
    }
    result["reference_source_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    result["scope"] = "DIGITAL_ONLY_NOT_ANALOG_TRANSFER_OR_MACRO_SAFETY"
    (output_directory / "analysis.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def main() -> None:
    """Run the checker without overwriting an existing result directory."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--trace-dir",
        type=Path,
        required=True,
        help="Directory containing events.txt and ticks.csv",
    )
    parser.add_argument(
        "--out",
        type=Path,
        required=True,
        help="New output directory for analysis.json and samples.csv",
    )
    args = parser.parse_args()
    if args.out.exists():
        parser.error("--out must name a new directory")
    args.out.mkdir(parents=True)
    result = analyze_trace(args.trace_dir, args.out)
    print(
        json.dumps(
            {
                name: result[name]
                for name in (
                    "status",
                    "scope",
                    "total_checks",
                    "failure_count",
                    "failures",
                    "ticks",
                )
            },
            indent=2,
        )
    )
    raise SystemExit(bool(result["failure_count"]))


if __name__ == "__main__":
    main()
