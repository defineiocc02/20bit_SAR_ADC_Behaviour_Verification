#!/usr/bin/env python3
"""Audit RTL arithmetic against independent Python integers and directed flag sequences.

Fixed seed by default; no project floating-point/model code is imported. Generated
vectors and build files live in an isolated temporary directory. VERILATOR is a
shell-like command string parsed with shlex and executed without a shell.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SOURCES = [
    "rtl/core/cal_residue_mac.sv",
    "rtl/core/adc2_dec.sv",
    "rtl/core/div_floor.sv",
    "rtl/core/cal_output_stage.sv",
    "sim/tb/arithmetic_audit_tb.sv",
]


def generate_vectors(root: Path, seed: int) -> dict[str, int]:
    rng = random.Random(seed)
    limit = 1 << 95
    cases = []
    for sign in [-1, 1]:
        for delta in [-2, -1, 0, 1, 2]:
            cases.append((sign * (1 << 45) + delta, 1 << 32, 0, 1 << 30, 1 << 30, 0))
    for k in range(40000):
        if k % 4 == 0:
            fine, offset, injection = (rng.randrange(-(1 << 63), 1 << 63) for _ in range(3))
            gain, total = (rng.getrandbits(64) for _ in range(2))
            rails = rng.randrange(-(1 << 65), 1 << 65)
        elif k % 4 == 1:
            fine, offset, injection = (rng.randrange(-(1 << 33), 1 << 33) for _ in range(3))
            gain, total = (rng.getrandbits(39) for _ in range(2))
            rails = rng.randrange(-(1 << 40), 1 << 40)
        elif k % 4 == 2:
            fine, offset, injection = (
                rng.choice(
                    [
                        -(1 << 63),
                        -(1 << 62),
                        -(1 << 32),
                        -1,
                        0,
                        1,
                        1 << 32,
                        (1 << 62) - 1,
                        (1 << 63) - 1,
                    ]
                )
                for _ in range(3)
            )
            gain, total = (
                rng.choice([0, 1, 1 << 30, 1 << 32, 1 << 62, (1 << 64) - 1]) for _ in range(2)
            )
            rails = rng.choice([-(1 << 65), -(1 << 63), -1, 0, 1, (1 << 65) - 1])
        else:
            fine, offset, injection = (rng.randrange(-(1 << 44), 1 << 44) for _ in range(3))
            gain, total = (rng.getrandbits(25) for _ in range(2))
            rails = rng.randrange(-(1 << 27), 1 << 27)
        cases.append((fine, offset, injection, gain, total, rails))

    def encode(values):
        return " ".join(format(value % (1 << width), "x") for value, width in values)

    rows = []
    nonoverflow = 0
    for fine, offset, injection, gain, total, rails in cases:
        op1 = (fine - offset) * (1 << 30)
        op2 = rails * (1 << 32)
        op3 = total * injection
        numerator = op1 - op2 - op3
        shifted = (numerator + gain * (1 << 32)) * (1 << 20)
        denominator = gain * (1 << 33)
        overflow = any(
            x < -limit or x >= limit for x in (op1, op2, op3, numerator, shifted, denominator)
        )
        nonoverflow += not overflow
        rows.append(
            encode(
                [
                    (fine, 64),
                    (offset, 64),
                    (injection, 64),
                    (gain, 64),
                    (total, 64),
                    (rails, 66),
                    (int(overflow), 1),
                    (shifted // (1 << 33), 63),
                ]
            )
        )
    (root / "mac.hex").write_text("\n".join(rows) + "\n")
    rows = []
    for k in range(40000):
        if k < 200:
            lo = rng.choice([-(1 << 63), -1, 0, 1, (1 << 63) - 1])
            hi = rng.choice([-(1 << 63), -1, 0, 1, (1 << 63) - 1])
            code = rng.choice([0, 1, 2047, 2048, 4094, 4095])
        else:
            lo = rng.randrange(-(1 << 63), 1 << 63)
            hi = rng.randrange(-(1 << 63), 1 << 63)
            code = rng.randrange(4096)
        quotient, remainder = divmod((2 * code + 1) * (hi - lo), 8192)
        fine = lo + quotient + int(remainder > 4096 or (remainder == 4096 and quotient % 2))
        rows.append(encode([(code, 12), (lo, 64), (hi, 64), (fine, 64)]))
    (root / "adc.hex").write_text("\n".join(rows) + "\n")
    rng = random.Random(seed)
    numerators = [-(1 << 62), -(1 << 62) + 1, -1, 0, 1, (1 << 62) - 1]
    denominators = [0, 1, 2, 3, (1 << 32) - 1, (1 << 62) - 1, 1 << 62, (1 << 64) - 1]
    divisions = [(a, d) for a in numerators for d in denominators] + [
        (rng.randrange(-(1 << 62), 1 << 62), rng.getrandbits(64)) for _ in range(10000)
    ]
    (root / "div.hex").write_text(
        "".join(
            encode([(a, 63), (d, 64), (a // d if d else 0, 63), (int(d == 0), 1)]) + "\n"
            for a, d in divisions
        )
    )
    return {
        "mac": len(cases),
        "mac_nonoverflow": nonoverflow,
        "adc": len(rows),
        "div": len(divisions),
        "flag_sequences": 8,
    }


def checked_run(command, *, cwd, env, timeout, log):
    """Keep diagnostics and fail on a missing tool, timeout, or nonzero result."""
    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            env=env,
            timeout=timeout,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        log.write_text(str(error) + "\n", encoding="utf-8")
        raise RuntimeError(f"command failed: {shlex.join(command)}: {error}") from error
    log.write_text(result.stdout, encoding="utf-8")
    if result.returncode:
        raise RuntimeError(
            f"command returned {result.returncode}: {shlex.join(command)}\n{result.stdout}"
        )
    return result.stdout


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=20260930)
    parser.add_argument("--timeout", type=int, default=180, help="seconds allowed for each command")
    parser.add_argument("--output-dir", type=Path, help="retain compact logs and provenance here")
    args = parser.parse_args()
    # A failed rerun must not leave a previous successful manifest behind.
    if args.output_dir:
        args.output_dir.mkdir(parents=True, exist_ok=True)
        (args.output_dir / "manifest.json").unlink(missing_ok=True)
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    command = shlex.split(os.environ.get("VERILATOR", "verilator"))
    if not command or not shutil.which(command[0]):
        parser.error("Verilator is required; set VERILATOR to its executable and optional flags")
    command[0] = str(Path(shutil.which(command[0])).absolute())
    try:
        with tempfile.TemporaryDirectory(prefix="sar-arithmetic-audit-") as tmp:
            work = Path(tmp)
            out = args.output_dir.resolve() if args.output_dir else work
            out.mkdir(parents=True, exist_ok=True)
            env = os.environ.copy()
            version = checked_run(
                [*command, "-V"],
                cwd=work,
                env=env,
                timeout=args.timeout,
                log=out / "verilator-version.log",
            )
            root = env.get("VERILATOR_ROOT")
            if not root:
                match = re.search(r"^\s*VERILATOR_ROOT\s*=\s*(.+?)\s*$", version, re.MULTILINE)
                root = match.group(1) if match else None
            if not root or not Path(root).is_dir():
                raise RuntimeError("Cannot locate Verilator runtime; set VERILATOR_ROOT explicitly")
            (work / "verilator").symlink_to(Path(root).resolve(), target_is_directory=True)
            env["VERILATOR_ROOT"] = str(work / "verilator")
            # ASCII aliases avoid make escaping a user's Unicode checkout path.
            (work / "repo").symlink_to(REPO, target_is_directory=True)
            (work / "python3").symlink_to(Path(sys.executable).resolve())
            counts = generate_vectors(work, args.seed)
            compile_command = (
                command
                + [
                    "--binary",
                    "--timing",
                    "--top-module",
                    "arithmetic_audit_tb",
                    "-Wno-fatal",
                    "-CFLAGS",
                    "-std=c++20",
                    "-MAKEFLAGS",
                    f"PYTHON3={work / 'python3'} CFG_CXXFLAGS_PCH_I=-include",
                    f"-I{work / 'repo/rtl/params'}",
                    "--Mdir",
                    str(work / "obj"),
                ]
                + [str(work / "repo" / name) for name in SOURCES]
            )
            checked_run(
                compile_command, cwd=work, env=env, timeout=args.timeout, log=out / "compile.log"
            )
            output = checked_run(
                [str(work / "obj/Varithmetic_audit_tb"), f"+VDIR={work}"],
                cwd=work,
                env=env,
                timeout=args.timeout,
                log=out / "run.log",
            )
            expected = [
                "MAC_INDEPENDENT_PASS cases=40010",
                "ADC_INDEPENDENT_PASS cases=40000",
                "DIV_INDEPENDENT_PASS cases=10048 latency=9 busy_requests_ignored",
                "OUTPUT_FLAG_SEQUENCE_PASS scenarios=8",
                "ARITHMETIC_AUDIT_COMPLETE mac=40010 adc=40000 div=10048 flag_sequences=8",
            ]
            if any(output.count(marker) != 1 for marker in expected):
                raise RuntimeError(f"Missing or duplicate completion counts:\n{output}")
            files = [*SOURCES, "rtl/params/rtl_params.vh", "tools/audit_rtl_arithmetic.py"]
            manifest = {
                "status": "PASS",
                "seed": args.seed,
                "counts": counts,
                "tool_command": command,
                "verilator_version": version,
                "compile_command": compile_command,
                "sources_sha256": {
                    name: hashlib.sha256((REPO / name).read_bytes()).hexdigest() for name in files
                },
                "vectors_sha256": {
                    name: hashlib.sha256((work / name).read_bytes()).hexdigest()
                    for name in ["mac.hex", "adc.hex", "div.hex"]
                },
                "scope": "Block arithmetic and directed output protocol; not full design, analog, STA, or formal proof.",
            }
            (out / "manifest.json").write_text(
                json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
            )
            print(output, end="")
            print(f"Independent arithmetic audit passed (seed={args.seed}); {counts}")
    except (RuntimeError, ValueError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
