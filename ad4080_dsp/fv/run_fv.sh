#!/usr/bin/env bash
# Explicit bounded FIFO safety check. No unbounded-proof or physical-signoff claim.
# Required: --case fifo --depth N --solver NAME --out NEW_EXTERNAL_DIR.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "${PYTHON:-python3}" - "$SCRIPT_DIR/../.." "$@" <<'PY'
"""Preserve real Yosys/solver status and reject vacuous or incomplete BMC logs."""
import argparse
import os
import re
import shlex
import shutil
import sys
import tempfile
from pathlib import Path

repo = Path(sys.argv[1]).resolve()
sys.dont_write_bytecode = True
sys.path.insert(0, str(repo / "tools"))
from run_open_rtl import checked_run, digest, new_output, write_json

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--case", choices=("fifo",), required=True)
parser.add_argument("--depth", type=int, required=True)
parser.add_argument("--solver", choices=("z3", "boolector", "yices", "cvc4", "mathsat"), required=True)
parser.add_argument("--out", type=Path, required=True)
parser.add_argument("--timeout", type=int, default=300)
args = parser.parse_args(sys.argv[2:])
if not 1 <= args.depth <= 256 or args.timeout <= 0:
    parser.error("depth must be 1..256 and timeout must be positive")

manifest = {
    "schema_version": 1, "status": "RUNNING", "case": args.case, "requested_depth": args.depth,
    "scope": "BOUNDED_DIGITAL_SAFETY", "solver": args.solver,
    "boundary": "Finite-depth symbolic safety only; no induction, liveness, CDC, timing or physical proof.",
}
out = None
try:
    out = new_output(args.out)
    write_json(out / "manifest.json", manifest)
    commands = {}
    for name, variable in (("yosys", "YOSYS"), ("yosys-smtbmc", "YOSYS_SMTBMC")):
        command = shlex.split(os.environ.get(variable, name))
        executable = shutil.which(command[0]) if command else None
        if executable is None:
            raise ValueError(f"required tool absent: {name}; configure {variable}")
        commands[name] = [executable, *command[1:]]
    solver_executable = "yices-smt2" if args.solver == "yices" else args.solver
    if shutil.which(solver_executable) is None:
        raise ValueError(f"required solver absent: {solver_executable}")
    names = ("ad4080_dsp/rtl/result_fifo.sv", "ad4080_dsp/fv/result_fifo_fv.sv")
    snapshot = out / "source_snapshot"
    snapshot.mkdir()
    hashes = {}
    for name in names:
        target = snapshot / Path(name).name
        target.write_bytes((repo / name).read_bytes())
        hashes[name] = digest(target)
    manifest.update({"commands": commands, "sources_sha256": hashes})
    write_json(out / "manifest.json", manifest)
    with tempfile.TemporaryDirectory(prefix="sar_fifo_bmc_") as temporary:
        work = Path(temporary)
        (work / "src").symlink_to(snapshot, target_is_directory=True)
        script = (
            "read_verilog -sv -DFORMAL src/result_fifo.sv src/result_fifo_fv.sv; "
            "prep -top result_fifo_fv; async2sync; memory_map; opt; dffunmap; "
            "write_smt2 -wires model.smt2"
        )
        checked_run(commands["yosys"] + ["-p", script], cwd=work, env=os.environ.copy(),
                    log=out / "elaborate.log", timeout=args.timeout)
        model = work / "model.smt2"
        if not model.is_file():
            raise ValueError("Yosys returned zero without a new SMT model")
        (out / "model.smt2").write_bytes(model.read_bytes())
        model_text = model.read_text()
        assertions = len(re.findall(r"^; yosys-smt2-assert[ \t]", model_text, re.MULTILINE))
        if assertions == 0:
            raise ValueError("SMT model contains no assertion obligations")
        try:
            output = checked_run(commands["yosys-smtbmc"] + ["-s", args.solver, "-t", str(args.depth),
                                 "--dump-vcd", "counterexample.vcd", str(model)],
                                 cwd=work, env=os.environ.copy(), log=out / "solver.log", timeout=args.timeout)
        finally:
            counterexample = work / "counterexample.vcd"
            if counterexample.is_file():
                shutil.copyfile(counterexample, out / counterexample.name)
        steps = [int(x) for x in re.findall(r"Checking assertions in step ([0-9]+)\.\.", output)]
        if steps != list(range(args.depth)):
            raise ValueError("solver did not check every requested BMC step")
        if len(re.findall(r"Status:[ \t]+PASSED[ \t]*$", output, re.MULTILINE)) != 1:
            raise ValueError("solver supplied no unique PASSED status")
        if any(digest(repo / name) != expected for name, expected in hashes.items()):
            raise ValueError("formal source changed during execution; snapshot retained")
        manifest.update({"status": "BMC_BOUNDED_PASS", "checked_steps": len(steps),
                         "assertion_obligations": assertions, "model_sha256": digest(out / "model.smt2")})
    write_json(out / "manifest.json", manifest)
    print(f"FIFO_BMC_COMPLETE depth={args.depth} checked_steps={args.depth} assertions={assertions} scope=BOUNDED_DIGITAL_SAFETY")
except (OSError, ValueError, RuntimeError) as error:
    if out is not None:
        manifest.update({"status": "FAIL", "error": str(error)})
        write_json(out / "manifest.json", manifest)
    print(f"FAIL: {error}", file=sys.stderr)
    raise SystemExit(1)
PY
