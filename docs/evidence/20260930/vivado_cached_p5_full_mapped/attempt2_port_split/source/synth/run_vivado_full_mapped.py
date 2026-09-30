#!/usr/bin/env python3
"""Full-top DCP functional simulation; run locally or inside an isolated SSH directory.

Default is prepare-only. --execute starts the installed Vivado/XSim tools.
No SSH transport, resynthesis, implementation, SDF, or timing claim is included.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import re
import shutil
import signal
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCOPE = "FULL_TOP_POST_SYNTH_FUNCTIONAL_NO_SDF_NOT_TIMING"
COLUMNS = [
    "mode",
    "frame",
    "clock",
    "sample_id",
    "code",
    "expected_code",
    "flags",
    "expected_flags",
    "status",
]


def sha256(path: Path) -> str:
    """Digest one exact file byte stream."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_origin(manifest: dict, dcp: Path, stages: int, expected_hash: str | None) -> dict:
    """Bind the TB label to two manifest fields and an explicitly trusted DCP hash."""
    fields = manifest.get("vivado_status", {})
    args = manifest.get("arguments", {})
    if (
        stages not in (5, 7)
        or args.get("stages") != stages
        or fields.get("P_RECON_STAGES") != str(stages)
    ):
        raise ValueError("TB profile differs from synthesis manifest parameters")
    if manifest.get("status") not in {"SYNTH_COMPLETE_TIMING_MET", "SYNTH_COMPLETE_TIMING_NOT_MET"}:
        raise ValueError(
            "Only an accepted synthesis manifest is usable; FAILED history is not promoted"
        )
    if manifest.get("failure_reason") or manifest.get("undriven_diagnostics"):
        raise ValueError("Invalid/undriven synthesis cannot be used as a positive witness")
    wns = float(fields.get("WNS_NS", "nan"))
    part = args.get("part")
    if (
        manifest["status"]
        != ("SYNTH_COMPLETE_TIMING_MET" if wns >= 0 else "SYNTH_COMPLETE_TIMING_NOT_MET")
        or fields.get("STATUS") != "SYNTH_COMPLETE"
        or not math.isfinite(wns)
        or fields.get("TIMING_MET") != str(int(wns >= 0))
        or not isinstance(part, str)
        or not re.fullmatch(r"[A-Za-z0-9_.+\-]+", part)
        or fields.get("PART") != part
        or fields.get("SCOPE") != "FPGA_POST_SYNTH_OOC_VECTORLESS_NOT_ASIC"
    ):
        raise ValueError("Incomplete/inconsistent synthesis status")
    recorded_hash = manifest.get("remote_dcp_identity", {}).get("sha256")
    if expected_hash and recorded_hash and expected_hash.lower() != recorded_hash.lower():
        raise ValueError("Explicit DCP hash disagrees with manifest DCP identity")
    wanted = recorded_hash or expected_hash
    if not isinstance(wanted, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", wanted):
        raise ValueError("Manifest DCP identity or explicit --dcp-sha256 is required")
    if not dcp.is_file() or dcp.suffix.lower() != ".dcp" or dcp.stat().st_size == 0:
        raise ValueError("Nonempty .dcp file required")
    actual = sha256(dcp)
    if actual != wanted.lower():
        raise ValueError("DCP content hash differs from the trusted synthesis output")
    return {
        "part": part,
        "stages": stages,
        "dcp_sha256": actual,
        "binding": "manifest.remote_dcp_identity"
        if recorded_hash
        else "explicit_caller_expected_sha256",
        "synthesis_status": manifest["status"],
        "synthesis_wns_ns": wns,
    }


def audit_trace(trace: Path, log: Path, stages: int) -> dict:
    """Independent ID schedule + strict row/log consistency, not a second code oracle."""
    text = log.read_text(encoding="utf-8", errors="backslashreplace")
    if re.search(r"(?im)(?:^\s*(?:ERROR:|FATAL_ERROR\b)|\bFatal:|\bFATAL:)", text):
        raise ValueError("Simulator reported an error/fatal diagnostic")
    marker = (
        f"STRUCTURAL_MAPPED_COMPLETE modes=3 outputs=438 checks=7680 physical_slices=18 "
        f"recon_stages={stages} config_reads=3900 cancelled=3 expected=441 latency_checked=0"
    )
    if text.count("STRUCTURAL_MAPPED_COMPLETE") != 1 or marker not in text:
        raise ValueError("Missing, duplicate, or inconsistent completion marker")
    scope = f"MAPPED_SCOPE functional_only public_ports_only recon_stages={stages} latency_checked=0 reset_release_ns=220"
    if text.count(scope) != 1:
        raise ValueError("Scope/profile/reset declaration missing or inconsistent")
    with trace.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != COLUMNS:
            raise ValueError("Unexpected CSV schema")
        rows = list(reader)
    if len(rows) != 438:
        raise ValueError("Expected exactly 438 output rows")
    numeric = []
    for row in rows:
        if set(row) != set(COLUMNS) or any(
            not re.fullmatch(r"[0-9]+", row[k] or "") for k in COLUMNS
        ):
            raise ValueError("Nondecimal/unknown/malformed CSV value")
        value = {k: int(row[k]) for k in COLUMNS}
        if value["code"] != value["expected_code"] or value["flags"] != value["expected_flags"]:
            raise ValueError("Code/flag mismatch in trace")
        if (
            not 0 <= value["code"] < (1 << 20)
            or value["flags"] != 0
            or value["status"] >= (1 << 32)
        ):
            raise ValueError("Unexpected output/flag/status range for this bounded fixture")
        numeric.append(value)
    expected_ids = [i for i in range(2, 159) if (i - 1) % 37 not in (11, 12) and i % 41 != 13]
    if [r["mode"] for r in numeric] != [m for m in range(3) for _ in expected_ids]:
        raise ValueError("Unexpected epoch/mode order")
    for mode in range(3):
        selected = [r for r in numeric if r["mode"] == mode]
        if [r["sample_id"] for r in selected] != expected_ids:
            raise ValueError("Sample IDs differ from the independent validity schedule")
        if any(not 0 <= r["frame"] < 160 for r in selected):
            raise ValueError("Output outside the bounded stimulus frames")
    if any(numeric[i - 1]["clock"] >= numeric[i]["clock"] for i in range(1, len(numeric))):
        raise ValueError("Nonmonotonic output observation clocks")
    printed = re.findall(r"^MAPPED_ROW,([^\r\n]+)", text, flags=re.MULTILINE)
    if printed != [",".join(row[k] for k in COLUMNS) for row in rows]:
        raise ValueError("CSV and simulator output rows disagree")
    cancelled = re.findall(
        r"^MAPPED_CANCEL,mode=(\d+),sample_id=(\d+),frame=(\d+)", text, flags=re.MULTILINE
    )
    if cancelled != [(str(mode), "159", "159") for mode in range(3)]:
        raise ValueError("Unexpected/missing epoch cancellation records")
    return {
        "status": "PASS",
        "scope": "TB_TRACE_CONSISTENCY_NOT_NETLIST_IDENTITY_OR_TIMING",
        "recon_stages": stages,
        "rows": 438,
        "rows_per_mode": [146, 146, 146],
        "config_readbacks": 3900,
        "protocol_checks": 7680,
        "physical_slices": 18,
        "cancelled": 3,
        "code_mismatches": 0,
        "flag_mismatches": 0,
        "internal_launch_latency_checked": False,
        "nonzero_flags_exercised": False,
        "trace_sha256": sha256(trace),
        "log_sha256": sha256(log),
    }


def windows_batch(argv: list[str]) -> str:
    """Quote literal batch arguments without percent or delayed expansion."""
    if any(any(c in arg for c in '%"\r\n\x00') for arg in argv):
        raise ValueError("Windows command arguments contain unsupported quoting characters")
    return (
        "@echo off\r\nsetlocal DisableDelayedExpansion\r\nchcp 65001 >nul\r\ncall "
        + " ".join('"' + arg + '"' for arg in argv)
        + "\r\nexit /b %ERRORLEVEL%\r\n"
    )


def run_step(name: str, argv: list[str], out: Path, timeout: int) -> dict:
    """Keep raw return codes/logs; timeout terminates only the process tree we start."""
    command = argv
    if os.name == "nt":
        batch = out / f"{name}.cmd"
        batch.write_bytes(windows_batch(argv).encode("utf-8"))
        command = [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/v:off", "/c", batch.name]
    with (out / f"{name}.console.log").open("wb") as log:
        process = subprocess.Popen(
            command,
            cwd=out,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=os.name != "nt",
        )
        try:
            code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill.exe", "/PID", str(process.pid), "/T", "/F"],
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    check=False,
                    timeout=30,
                )
            else:
                os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=30)
            raise RuntimeError(f"{name} timed out after {timeout}s") from None
    return {
        "step": name,
        "argv": argv,
        "raw_returncode": code,
        "console_sha256": sha256(out / f"{name}.console.log"),
    }


def main() -> int:
    """Capture inputs, optionally execute tools, then audit immutable results."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dcp", type=Path, required=True)
    parser.add_argument("--synthesis-manifest", type=Path, required=True)
    parser.add_argument("--dcp-sha256")
    parser.add_argument("--stages", type=int, choices=(5, 7), required=True)
    parser.add_argument(
        "--vivado", type=Path, required=True, help="Full path to this installation's vivado(.bat)"
    )
    parser.add_argument(
        "--out", type=Path, required=True, help="New directory; existing results never reused"
    )
    parser.add_argument(
        "--timeout", type=int, default=3600, help="Per tool process-tree deadline in seconds"
    )
    parser.add_argument(
        "--execute", action="store_true", help="Without this flag prepare only; no EDA starts"
    )
    args = parser.parse_args()
    if args.timeout < 1:
        parser.error("timeout must be positive")
    origin = validate_origin(
        json.loads(args.synthesis_manifest.read_text(encoding="utf-8")),
        args.dcp,
        args.stages,
        args.dcp_sha256,
    )
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    inputs = out / "input"
    inputs.mkdir()
    originals = {
        "post_synth.dcp": args.dcp,
        "synthesis_manifest.json": args.synthesis_manifest,
        "structural_mapped_tb.sv": ROOT / "sim/tb/structural_mapped_tb.sv",
        "run_vivado_full_mapped.tcl": ROOT / "synth/run_vivado_full_mapped.tcl",
        "run_vivado_full_mapped.py": Path(__file__),
        "audit_full_mapped.py": ROOT / "synth/audit_full_mapped.py",
    }
    manifest = {
        "status": "PREPARED",
        "scope": SCOPE,
        "origin": origin,
        "steps": [],
        "helper_sha256": sha256(Path(__file__)),
        "input_sha256": {},
    }

    def save() -> None:
        (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    try:
        for name, source in originals.items():
            shutil.copyfile(source, inputs / name)
            manifest["input_sha256"][name] = sha256(inputs / name)
        if manifest["input_sha256"]["post_synth.dcp"] != origin["dcp_sha256"]:
            raise ValueError("DCP changed during input capture")
        vivado = args.vivado.resolve()
        suffix = ".bat" if vivado.suffix.lower() == ".bat" else ""
        tools = {name: vivado.parent / (name + suffix) for name in ("xvlog", "xelab", "xsim")}
        glbl = vivado.parent.parent / "data/verilog/src/glbl.v"
        snapshot = f"full_mapped_p{args.stages}"
        commands = [
            (
                "export",
                [
                    str(vivado),
                    "-mode",
                    "batch",
                    "-nojournal",
                    "-log",
                    "export.vivado.log",
                    "-source",
                    "input/run_vivado_full_mapped.tcl",
                    "-tclargs",
                    "input/post_synth.dcp",
                    "export",
                    origin["part"],
                    str(args.stages),
                ],
            ),
            (
                "xvlog",
                [
                    str(tools["xvlog"]),
                    "--sv",
                    "export/full_top_funcsim.v",
                    "input/structural_mapped_tb.sv",
                    str(glbl),
                ],
            ),
            (
                "xelab",
                [
                    str(tools["xelab"]),
                    "structural_mapped_tb",
                    "glbl",
                    "-L",
                    "unisims_ver",
                    "-s",
                    snapshot,
                    "-mt",
                    "2",
                    "-generic_top",
                    f"P_RECON_STAGES={args.stages}",
                ],
            ),
            (
                "xsim",
                [
                    str(tools["xsim"]),
                    snapshot,
                    "-R",
                    "-testplusarg",
                    "mapped_trace=trace.csv",
                    "-log",
                    "xsim.log",
                ],
            ),
        ]
        manifest["commands"] = commands
        save()
        if not args.execute:
            print(f"PREPARED (no EDA): {out}")
            return 0
        for path in [vivado, glbl, *tools.values()]:
            if not path.is_file():
                raise ValueError(f"Installed tool/library missing: {path}")
        manifest["tool_sha256"] = {str(p): sha256(p) for p in [vivado, glbl, *tools.values()]}
        manifest["status"] = "RUNNING"
        save()
        for name, command in commands:
            record = run_step(name, command, out, args.timeout)
            manifest["steps"].append(record)
            save()
            if record["raw_returncode"] != 0:
                raise RuntimeError(f"{name} returned {record['raw_returncode']}")
            console = (out / f"{name}.console.log").read_text(
                encoding="utf-8", errors="backslashreplace"
            )
            if re.search(r"(?im)^\s*ERROR:", console):
                raise RuntimeError(f"{name} emitted ERROR despite raw return code 0")
            if name == "export":
                fields = {}
                for line in (out / "export/status.txt").read_text().splitlines():
                    key, sep, value = line.partition("=")
                    if not sep or key in fields:
                        raise ValueError("Malformed export status")
                    fields[key] = value
                expected = {
                    "STATUS": "FULL_MAPPED_EXPORT_COMPLETE",
                    "TOP": "sar20_digital_core",
                    "PART": origin["part"],
                    "P_RECON_STAGES": str(args.stages),
                    "SCOPE": SCOPE,
                }
                if any(fields.get(k) != v for k, v in expected.items()) or not fields.get(
                    "VIVADO_VERSION"
                ):
                    raise ValueError("DCP export did not complete with matching identity")
                manifest["export_status"] = fields
                manifest["netlist_sha256"] = sha256(out / "export/full_top_funcsim.v")
        manifest["audit"] = audit_trace(out / "trace.csv", out / "xsim.log", args.stages)
        if any(sha256(inputs / name) != value for name, value in manifest["input_sha256"].items()):
            raise RuntimeError("Input snapshot changed during simulation")
        manifest["status"] = "FULL_MAPPED_FUNCTIONAL_PASS"
        save()
        print(f"FULL_MAPPED_FUNCTIONAL_PASS: {out}")
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        manifest["status"] = "FAILED"
        manifest["error"] = str(error)
        save()
        print(f"FAILED: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
