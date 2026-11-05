#!/usr/bin/env python3
"""在新目录运行并核对小几何 RTL 状态机轨迹。

入口要求 --out 和 --fixture-json；使用现有支持时序的 Verilator 5.x 及 C++20
工具链。只编译 engineering_fsm_tb 的九文件数值组件，生产 RTL 检查仍由
run_open_rtl.py 管理。CSV 在非阻塞赋值后的 1 ps 采样；该功能仿真不证明
布线后时序、模拟建立或 ASIC 工作频率。
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import subprocess
import sys
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
    """从实际 CSV 字节独立复算状态转移和外部输出。

    不以 PASS 标记代替逐行核对。显式夹具使用总和为 2^30 的正 Q30 权重、全零
    转换选通、2^33 的 ADC2 电压数值跨度和 20 位后级，因此整数方程简化为
    word=code。无效数值行索引应保持上一次合法输出。
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


# 以下字段明确声明该数值夹具，不作为器件默认参数。
FIXTURE_VALUES = {
    "voltage_bits": 64,
    "voltage_fraction_bits": 32,
    "weight_bits": 48,
    "weight_fraction_bits": 30,
    "accumulator_bits": 96,
    "output_bits": 20,
    "adc2_bits": 20,
    "n_slices": 1,
    "n_active": 1,
    "n_main": 2,
    "n_sub": 1,
    "recon_stages": 7,
}
SOURCES = (
    "rtl/core/analog_phase_ctrl.sv",
    "rtl/core/sar_trial_ctrl.sv",
    "rtl/core/cal_weight_reduce.sv",
    "rtl/core/adc2_dec.sv",
    "rtl/core/cal_residue_mac.sv",
    "rtl/core/div_floor.sv",
    "rtl/core/cal_output_stage.sv",
    "rtl/core/recon_core.sv",
    "sim/tb/engineering_fsm_tb.sv",
)


def load_fixture(path: Path) -> dict:
    """只接受此处显式声明的整数协议夹具，不隐式补充几何参数。"""
    value = json.loads(path.read_text(encoding="utf-8"))
    require(isinstance(value, dict), "fixture-json must be an object")
    require(
        set(value) == {"schema_version", "scope", "description", *FIXTURE_VALUES},
        "fixture-json must explicitly state every numerical field",
    )
    require(
        value["schema_version"] == 1 and value["scope"] == "COMPONENT_SYNTHETIC_DIGITAL",
        "fixture-json is not a synthetic component fixture",
    )
    require(
        isinstance(value["description"], str) and bool(value["description"].strip()),
        "fixture requires its numerical-only scope description",
    )
    require(
        all(type(value[k]) is int and value[k] == wanted for k, wanted in FIXTURE_VALUES.items()),
        "fixture does not match the explicitly supported engineering identity format",
    )
    return value


def write_fixture_header(path: Path, value: dict) -> None:
    """根据显式夹具写隔离头文件，不使用仓库默认参数。"""
    names = {
        "V_BITS": "voltage_bits",
        "V_FRAC": "voltage_fraction_bits",
        "W_BITS": "weight_bits",
        "W_FRAC": "weight_fraction_bits",
        "ACC_BITS": "accumulator_bits",
        "OUT_BITS": "output_bits",
        "ADC2_BITS": "adc2_bits",
        "N_SLICES": "n_slices",
        "N_ACTIVE": "n_active",
        "N_UNIT_MAIN": "n_main",
        "N_UNIT_SUB": "n_sub",
    }
    path.write_text(
        "// COMPONENT_SYNTHETIC_DIGITAL: exact integer fixture only; no device mapping.\n"
        "`ifndef ENGINEERING_PROTOCOL_NUMERIC_VH\n`define ENGINEERING_PROTOCOL_NUMERIC_VH\n"
        + "".join(f"localparam int {name} = {value[field]};\n" for name, field in names.items())
        + f"localparam int N_UNIT_TOTAL = {value['n_main'] + value['n_sub']};\n"
        + "`endif\n",
        encoding="utf-8",
    )


def main(argv=None) -> int:
    from run_open_rtl import (
        alias_runtime,
        checked_run,
        command_for,
        completion,
        new_output,
        verilator_compiler_args,
    )

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out", type=Path, required=True, help="New directory outside all checkouts"
    )
    parser.add_argument(
        "--fixture-json",
        type=Path,
        required=True,
        help="Explicit engineering_protocol/format.json numerical fixture",
    )
    parser.add_argument("--timeout", type=int, default=180)
    args = parser.parse_args(argv)
    if args.timeout <= 0:
        parser.error("timeout must be positive")
    fixture_path = args.fixture_json.expanduser().resolve()
    output = None
    stage = "validate explicit fixture"
    try:
        fixture = load_fixture(fixture_path)
        output = new_output(args.out)
        command = command_for("verilator")
        snapshot = output / "source_snapshot"
        for name in SOURCES:
            source = REPO / name
            require(source.is_file(), f"missing selected dependency: {name}")
            target = snapshot / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read_bytes())
        source_hashes = {name: sha256(snapshot / name) for name in SOURCES}
        write_json(output / "source_sha256.json", source_hashes)
        write_json(output / "fixture.json", fixture)
        write_fixture_header(output / "rtl_params.vh", fixture)
        stage = "read existing simulator identity"
        env = os.environ.copy()
        version = checked_run(
            [*command, "-V"], cwd=output, env=env, log=output / "version.log", timeout=30
        )
        with tempfile.TemporaryDirectory(prefix="engineering_numeric_") as temporary:
            work = Path(temporary)
            alias_runtime(work, env, version)
            (work / "repo").symlink_to(snapshot, target_is_directory=True)
            (work / "output").symlink_to(output, target_is_directory=True)
            include = work / "output"
            build_command = [
                *command,
                "--binary",
                "--unroll-count",
                "8192",
                "--timing",
                "--assert",
                "-j",
                "1",
                "-Wno-fatal",
                "-Werror-PINMISSING",
                "-Werror-SELRANGE",
                *verilator_compiler_args(),
                "--top-module",
                BENCH,
                f"-I{include}",
                "--Mdir",
                str(work / "obj"),
                *[str(work / "repo" / name) for name in SOURCES],
            ]
            stage = "compile numerical FSM component"
            checked_run(
                build_command,
                cwd=work,
                env=env,
                log=output / "engineering_fsm_tb.build.log",
                timeout=args.timeout,
            )
            run_command = [str(work / "obj" / f"V{BENCH}"), f"+trace_dir={work / 'output'}"]
            write_json(
                output / "command.json",
                {
                    "build_command": build_command,
                    "run_command": run_command,
                    "selected_sources": list(SOURCES),
                    "fixture_sha256": sha256(fixture_path),
                    "scope": "COMPONENT_SYNTHETIC_DIGITAL",
                },
            )
            stage = "execute numerical FSM component"
            text = checked_run(
                run_command,
                cwd=work,
                env=env,
                log=output / "engineering_fsm_tb.run.log",
                timeout=30,
            )
        completion(text, MARKER, ("recon_accepts", "recon_results", "recon_cancels", "edges"))
        stage = "independent real CSV audit"
        result = audit_csv(output)
        require(
            all(sha256(REPO / name) == expected for name, expected in source_hashes.items()),
            "selected sources changed during execution",
        )
        result.update(
            status="PASS",
            source_sha256=source_hashes,
            fixture_sha256=sha256(fixture_path),
            scope="COMPONENT_SYNTHETIC_DIGITAL",
        )
        write_json(output / "result.json", result)
        print(
            f"ENGINEERING_FSM_TRACE_PASS rows=175 recon_results=7 cancelled=2 overlap=1 out={output}"
        )
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        if output is not None:
            write_json(
                output / "result.json", {"status": "FAIL", "stage": stage, "error": str(error)}
            )
        print(f"FSM trace failed during {stage}: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
