#!/usr/bin/env python3
"""Freeze and reproduce the small-geometry RTL FSM traces in a fresh directory.

Usage: VERILATOR='verilator' python tools/run_engineering_fsm.py --out /new/path
Requires Verilator 5.x with timing support and a C++20 toolchain. This runner
compiles only engineering_fsm_tb. Production lint/full regressions remain in
run_open_rtl.py. CSV observations occur 1 ps after NBA; this is functional RTL
simulation and does not prove routed timing, analog behavior, or ASIC frequency.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import tempfile
from decimal import Decimal
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BENCH = "engineering_fsm_tb"
MARKER = "ENGINEERING_FSM_COMPLETE"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def audit_csv(trace_dir: Path) -> dict:
    """Recompute transitions and expected external results from real CSV bytes.

    No PASS-marker parsing is used here. The fixture uses exact positive Q30
    weights summing to 2^30, no on-cells/dither, ADC2 voltage span 2^33 and a
    20-bit backend, so the independent integer equation reduces to word=code.
    An invalid physical slice must instead retain the last legal word.
    """
    traces = {}
    for name, columns in (("sar", 20), ("recon", 31), ("phase", 18)):
        path = trace_dir / f"{name}_trace.csv"
        with path.open(encoding="utf-8", newline="") as stream:
            rows = list(csv.DictReader(stream))
        require(len(rows) == 175, f"{name}: expected 175 real edge records")
        require(len(rows[0]) == columns, f"{name}: CSV column count mismatch")
        require(
            all(
                None not in row and all(value is not None for value in row.values()) for row in rows
            ),
            f"{name}: malformed CSV record",
        )
        require(
            [int(row["edge"]) for row in rows] == list(range(1, 176)),
            f"{name}: missing/duplicate edge",
        )
        require(
            [Decimal(row["time_ns"]) for row in rows]
            == [Decimal("5.001") + 10 * i for i in range(175)],
            f"{name}: observation must occur 1 ps after each 10 ns rising edge",
        )
        traces[name] = rows

    recon = traces["recon"]
    accepted, results, cancelled = [], [], []
    pending = None
    last_word = 0
    sticky_gain = 0
    overlap_edge = None
    for row in recon:
        value = {key: int(item) for key, item in row.items() if key not in ("time_ns", "scenario")}
        edge = value["edge"]
        accept = value["rst_n"] and value["cfg_ready"] and value["start"] and not value["pre_busy"]
        if not value["rst_n"]:
            pending, last_word, sticky_gain = None, 0, 0
        else:
            if value["clear"]:
                sticky_gain = 0
            if not value["cfg_ready"]:
                if pending is not None:
                    cancelled.append(pending["id"])
                pending, last_word = None, 0
                require(
                    all(
                        value[key] == 0
                        for key in ("post_valid", "post_word", "post_id", "post_flags", "post_busy")
                    ),
                    f"recon edge {edge}: cancelled output/state leaked",
                )
            else:
                if pending is not None and edge == pending["due"]:
                    require(
                        value["post_valid"] == 1, f"recon edge {edge}: missing fixed-latency output"
                    )
                    require(
                        value["post_id"] == pending["id"], f"recon edge {edge}: wrong sample ID"
                    )
                    require(
                        value["post_word"] == pending["word"],
                        f"recon edge {edge}: wrong word/hold behavior",
                    )
                    require(
                        value["post_flags"] == pending["flags"],
                        f"recon edge {edge}: wrong per-sample flags",
                    )
                    results.append([edge, pending["id"], pending["word"], pending["flags"]])
                    if pending["flags"]:
                        sticky_gain = 1
                    else:
                        last_word = pending["word"]
                    pending = None
                    if accept:
                        require(
                            overlap_edge is None, "recon: unexpected second commit/start overlap"
                        )
                        overlap_edge = edge
                else:
                    require(
                        value["post_valid"] == 0,
                        f"recon edge {edge}: early/late/spurious completion",
                    )
                if accept:
                    require(
                        pending is None,
                        f"recon edge {edge}: accepted while an older request was pending",
                    )
                    error = value["slice_id"] != 0
                    pending = {
                        "accepted": edge,
                        "due": edge + 11,
                        "id": value["id"],
                        "word": last_word if error else value["code"],
                        "flags": 2 if error else 0,
                    }
                    accepted.append([edge, value["id"], value["code"], row["scenario"]])
                require(
                    value["post_word"] == last_word,
                    f"recon edge {edge}: last word changed outside a legal commit",
                )
                age = edge - pending["accepted"] if pending is not None else None
                require(
                    value["post_busy"] == int(age is not None and age < 10),
                    f"recon edge {edge}: busy interval mismatch",
                )
                require(
                    value["post_stage_b"] == int(bool(accept)),
                    f"recon edge {edge}: capture/launch stage mismatch",
                )
                require(
                    value["post_div_busy"] == int(age is not None and 1 <= age < 10),
                    f"recon edge {edge}: divider run mismatch",
                )
                require(
                    value["post_div_done"] == int(age == 10),
                    f"recon edge {edge}: divider done pulse mismatch",
                )
                if age is not None and age >= 1:
                    require(
                        value["post_div_cnt"] == min(age - 1, 8),
                        f"recon edge {edge}: divider iteration count mismatch",
                    )
            require(
                value["post_gain_sticky"] == sticky_gain,
                f"recon edge {edge}: sticky gain clear/error mismatch",
            )
    require(pending is None, "recon: unfinished request")
    expected_requests = [
        [101, 73],
        [201, 111],
        [202, 203],
        [301, 99],
        [302, 150],
        [401, 500],
        [501, 304],
        [601, 500],
        [701, 409],
    ]
    require(
        [item[1:3] for item in accepted] == expected_requests,
        "recon: fixture request accounting changed",
    )
    require(
        len(results) == 7 and cancelled == [301, 601],
        "recon: result/cancellation accounting changed",
    )
    require(sum(item[3] != 0 for item in results) == 1, "recon: expected one invalid-slice error")
    require(overlap_edge == 62, "recon: commit/new-start overlap missing")

    sar_counts = {"accepted": 0, "completed": 0, "cancelled": 0, "stalls": 0, "busy_starts": 0}
    sar_results = []
    targets = {
        "normal_stall_complete": 345,
        "cancel_during_compare": 123,
        "recovery_stall_complete": 201,
    }
    for row in traces["sar"]:
        value = {key: int(item) for key, item in row.items() if key not in ("time_ns", "scenario")}
        edge = value["edge"]
        if not value["rst_n"] or not value["enable"]:
            require(
                all(
                    value[key] == 0
                    for key in (
                        "post_trial",
                        "post_resolved",
                        "post_busy",
                        "post_done",
                        "post_resolved_valid",
                    )
                ),
                f"SAR edge {edge}: reset/disable failed",
            )
            continue
        held = all(
            value["post_" + key] == value["pre_" + key]
            for key in ("trial", "resolved", "bit_index")
        )
        if value["cancel"]:
            require(
                held
                and not value["post_busy"]
                and not value["post_done"]
                and not value["post_resolved_valid"],
                f"SAR edge {edge}: cancellation changed code or leaked event",
            )
            sar_counts["cancelled"] += value["pre_busy"]
        elif value["start"] and not value["pre_busy"]:
            sar_counts["accepted"] += 1
            seed = targets[row["scenario"]] // 64 * 64
            require(
                value["post_resolved"] == seed
                and value["post_trial"] == seed + 32
                and value["post_bit_index"] == 5
                and value["post_busy"]
                and value["post_resolved_valid"]
                and not value["post_done"],
                f"SAR edge {edge}: incorrect seeded start",
            )
        elif value["pre_busy"] and value["cmp_valid"]:
            bit = value["pre_bit_index"]
            target = targets[row["scenario"]]
            require(
                value["cmp_ge"] == int(target >= value["pre_trial"]),
                f"SAR edge {edge}: comparator convention mismatch",
            )
            resolved = (value["pre_resolved"] & ~(1 << bit)) | (value["cmp_ge"] << bit)
            require(
                value["post_resolved"] == resolved and value["post_resolved_valid"],
                f"SAR edge {edge}: wrong resolved decision",
            )
            if bit == 0:
                require(
                    not value["post_busy"]
                    and value["post_done"]
                    and value["post_trial"] == resolved == target,
                    f"SAR edge {edge}: incorrect final code",
                )
                sar_counts["completed"] += 1
                sar_results.append([edge, resolved])
            else:
                require(
                    value["post_busy"]
                    and not value["post_done"]
                    and value["post_bit_index"] == bit - 1
                    and value["post_trial"] == resolved | (1 << (bit - 1)),
                    f"SAR edge {edge}: incorrect pending next bit",
                )
        else:
            require(
                held
                and value["post_busy"] == value["pre_busy"]
                and not value["post_done"]
                and not value["post_resolved_valid"],
                f"SAR edge {edge}: stalled/idle state changed",
            )
            sar_counts["stalls"] += value["pre_busy"] and not value["cmp_valid"]
        require(
            value["post_compare_enable"]
            == int(bool(value["enable"] and value["post_busy"] and not value["cancel"])),
            f"SAR edge {edge}: comparator enable mismatch",
        )
        if value["pre_busy"] and value["start"] and not value["cancel"]:
            sar_counts["busy_starts"] += 1
    require(
        sar_counts
        == {"accepted": 3, "completed": 2, "cancelled": 1, "stalls": 9, "busy_starts": 2},
        "SAR: scenario accounting changed",
    )
    require(
        [item[1] for item in sar_results] == [345, 201], "SAR: expected two completed target words"
    )

    phase_wraps = 0
    for row in traces["phase"]:
        value = {key: int(item) for key, item in row.items() if key not in ("time_ns", "scenario")}
        if value["rst_n"] and value["enable"]:
            pre, n = value["pre_phase"], value["post_phase"]
            require(n == (pre + 1) % 16, "phase: counter transition mismatch")
            expected = (n != 0, 1 <= n < 9, n >= 10 or n == 0, 2 <= n < 9, n >= 10 or n == 0)
            actual = tuple(
                bool(value[key])
                for key in ("post_tp", "post_az", "post_amplify", "post_precharge", "post_accurate")
            )
            require(actual == expected, "phase: registered analog boundary schedule mismatch")
            require(
                tuple(bool(value[key]) for key in ("pre_quiet", "pre_advance", "pre_capture"))
                == (pre == 0, pre == 1, pre == 9),
                "phase: pre-edge digital handshake mismatch",
            )
            phase_wraps += pre == 15 and n == 0
        else:
            require(
                all(
                    value[key] == 0
                    for key in (
                        "post_phase",
                        "post_tp",
                        "post_az",
                        "post_amplify",
                        "post_precharge",
                        "post_accurate",
                    )
                ),
                "phase: reset/disable did not clear controls",
            )
    require(phase_wraps == 10, "phase: expected ten complete steady-state wraps")
    return {
        "scope": "zero-delay small-geometry RTL; no STA/analog/ASIC evidence",
        "csv_rows_each": 175,
        "recon_accepted": accepted,
        "recon_results": results,
        "recon_cancelled_ids": cancelled,
        "recon_latency_ticks": 11,
        "recon_commit_overlap_edge": overlap_edge,
        "sar_counts": sar_counts,
        "sar_completed": sar_results,
        "phase_wraps": phase_wraps,
        "csv_sha256": {path.name: sha256(path) for path in sorted(trace_dir.glob("*_trace.csv"))},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True, help="New, nonexistent trace directory")
    output = parser.parse_args().out.expanduser().resolve()
    command = shlex.split(os.environ.get("VERILATOR", "verilator"))
    if not command or shutil.which(command[0]) is None:
        raise SystemExit("Verilator is required; install it or set VERILATOR")
    if output.exists():
        raise SystemExit(f"Refusing to overwrite existing output: {output}")
    sources = (REPO / "rtl/rtl_sources.f").read_text(encoding="utf-8").splitlines()
    files = [
        *sources,
        "rtl/params/rtl_params.vh",
        "rtl/params/rtl_error_codes.vh",
        "rtl/rtl_sources.f",
        f"sim/tb/{BENCH}.sv",
    ]
    for name in files:
        path = Path(name)
        require(
            not path.is_absolute() and ".." not in path.parts,
            f"Unsafe source-manifest path: {name}",
        )
        require((REPO / path).is_file(), f"Missing source: {name}")
    require(
        len(sources) == 24 and len(set(files)) == 28,
        "FSM source bundle expects 24 SV + 2 headers + manifest + bench",
    )
    output.mkdir(parents=True)
    stage = "freeze source"
    try:
        snapshot = output / "source_snapshot"
        for name in files:
            destination = snapshot / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes((REPO / name).read_bytes())
        write_json(output / "source_sha256.json", {name: sha256(snapshot / name) for name in files})
        stage = "read Verilator version"
        with (output / "version.log").open("w", encoding="utf-8") as log:
            subprocess.run(
                [*command, "--version"],
                stdout=log,
                stderr=subprocess.STDOUT,
                check=True,
                timeout=30,
            )
        with tempfile.TemporaryDirectory(prefix="sar_engineering_fsm_") as build:
            args = [
                *command,
                "--binary",
                "--unroll-count",
                "8192",
                "--timing",
                "--assert",
                "-j",
                "2",
                "-Wno-fatal",
                "-Werror-PINMISSING",
                "-Werror-SELRANGE",
                "--top-module",
                BENCH,
                "-Irtl/params",
                "--Mdir",
                build,
                *sources,
                f"sim/tb/{BENCH}.sv",
            ]
            run_args = [str(Path(build) / f"V{BENCH}"), f"+trace_dir={output}"]
            write_json(
                output / "command.json",
                {
                    "build_command": args,
                    "build_cwd": str(snapshot),
                    "run_command": run_args,
                    "run_cwd": str(snapshot),
                },
            )
            stage = "compile FSM bench"
            with (output / "engineering_fsm_tb.build.log").open("w", encoding="utf-8") as log:
                subprocess.run(
                    args,
                    cwd=snapshot,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    check=True,
                    timeout=300,
                )
            stage = "execute FSM bench"
            with (output / "engineering_fsm_tb.run.log").open("w", encoding="utf-8") as log:
                subprocess.run(
                    run_args,
                    cwd=snapshot,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    check=True,
                    timeout=30,
                )
        require(
            MARKER in (output / "engineering_fsm_tb.run.log").read_text(encoding="utf-8"),
            "FSM completion marker missing",
        )
        stage = "audit real CSV transitions"
        result = audit_csv(output)
        result["status"] = "PASS"
        result["artifact_sha256"] = {
            name: sha256(output / name)
            for name in (
                "version.log",
                "source_sha256.json",
                "command.json",
                "engineering_fsm_tb.build.log",
                "engineering_fsm_tb.run.log",
            )
        }
        write_json(output / "result.json", result)
        print(
            f"ENGINEERING_FSM_TRACE_PASS rows=175 recon_results=7 cancelled=2 overlap=1 out={output}"
        )
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        write_json(output / "result.json", {"status": "FAIL", "stage": stage, "error": str(error)})
        raise SystemExit(
            f"FSM trace failed during {stage}: {error}\nEvidence retained at {output}"
        ) from error


if __name__ == "__main__":
    main()
