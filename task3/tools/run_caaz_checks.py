#!/usr/bin/env python3
"""按独立队列模型核对 CAAZ 的每拍控制、整数算术和故障处理。"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import shutil
import sys
import tempfile
from collections.abc import Iterator
from dataclasses import astuple, replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True

from task3.model.caaz20 import CaazOracle, Input, Output  # noqa: E402
from tools.run_open_rtl import (  # noqa: E402
    alias_runtime,
    checked_run,
    command_for,
    completion,
    digest,
    new_output,
    verilator_compiler_args,
    write_json,
)

RTL = "task3/rtl/caaz20_pair.sv"
TB = "task3/tb/caaz20_pair_tb.sv"
TOP = "caaz20_pair_tb"
MARKER = "CAAZ20_COMPLETE"
SEED = 0xCA2020
MIN_CODE, MAX_CODE = -(1 << 19), (1 << 19) - 1
IN_WIDTHS = (1, 1, 1, 1, 1, 32, 1, 32, 20, 1, 1, 31, 1)
OUT_WIDTHS = (1, 32, 1, 1, 1, 31, 1, 20, 32, 32, 31, 1, 21, 1, 1, 3)


def _acq(sample_id: int, mode: bool = True) -> Input:
    return Input(caaz_enable=mode, acq_valid=True, acq_id=sample_id)


def _raw(sample_id: int, code: int = 0, mode: bool = True) -> Input:
    return Input(
        caaz_enable=mode,
        raw_valid=True,
        raw_id=sample_id,
        raw_code=code,
        raw_chop_neg=bool(sample_id % 2) if mode else True,
        raw_az_id=sample_id // 2 if mode else 0x7FFFFFFF,
        raw_az_ok=mode,
    )


def _flow(mode: bool, count: int, *, cadence: int, rng: random.Random) -> Iterator[Input]:
    """流水、正负码极值及不同采样节拍。"""
    yield Input(clear=True, caaz_enable=mode)
    edge_codes = (MIN_CODE, MAX_CODE, MAX_CODE, MIN_CODE, -1, 0, 1, 0, 0, 0)
    pending = []
    cycle = issued = returned = 0
    while returned < count:
        event = Input(caaz_enable=mode)
        if pending and (rng.randrange(3) != 0 if cadence == 0 else cycle % cadence == 1):
            sample_id = pending.pop(0)
            code = (
                edge_codes[sample_id]
                if sample_id < len(edge_codes)
                else rng.randint(MIN_CODE, MAX_CODE)
            )
            event = _raw(sample_id, code, mode)
            returned += 1
        if issued < count and (rng.randrange(4) != 0 if cadence == 0 else cycle % cadence == 0):
            event = replace(event, acq_valid=True, acq_id=issued)
            pending.append(issued)
            issued += 1
        yield event
        cycle += 1


def _events() -> Iterator[Input]:
    yield Input(rst_n=False, acq_valid=True, raw_valid=True, raw_bad=True)
    rng = random.Random(SEED)
    for mode in (True, False):
        yield from _flow(mode, 256, cadence=0, rng=rng)
    # 16 个 160 MHz tick 启动一笔。结果等待 1 个 tick 后回报仅用于接口对拍。
    yield from _flow(True, 64, cadence=16, rng=rng)
    yield Input(clear=True)
    yield _acq(0)
    for sample_id in range(1, 1024):
        yield replace(
            _raw(sample_id - 1, rng.randint(MIN_CODE, MAX_CODE)), acq_valid=True, acq_id=sample_id
        )
    yield _raw(1023)

    faults = (
        ([_acq(0)], replace(_raw(0, mode=False), acq_valid=True, acq_id=5)),
        ([], replace(_raw(9), acq_valid=True, acq_id=5)),
        ([], replace(_raw(0), acq_valid=True, acq_id=0)),
        ([_acq(0), _acq(1), _raw(0)], _raw(0)),
        ([_acq(0), _acq(1), _acq(2), _raw(0)], _raw(2)),
        ([_acq(0)], replace(_raw(0), raw_bad=True, raw_chop_neg=True)),
        ([_acq(0, mode=False)], replace(_raw(0, mode=False), raw_bad=True)),
        ([_acq(0)], replace(_raw(0), raw_chop_neg=True, raw_az_id=1)),
        ([_acq(0), _acq(1), _raw(0)], replace(_raw(1), raw_az_id=1)),
        ([_acq(0), _acq(1), _raw(0)], replace(_raw(1), raw_az_ok=False)),
    )
    for setup, error in faults:
        yield Input(clear=True)
        yield from setup
        yield error
        yield _raw(0)
        yield _acq(0)

    for cancel in ({"clear": True}, {"rst_n": False}, {"enable": False}):
        yield Input(clear=True)
        yield _acq(0)
        yield _acq(1)
        yield _raw(0, -1)
        yield Input()
        yield replace(_raw(1), acq_valid=True, acq_id=2, **cancel)
        yield _acq(0)
        yield _acq(1)
        yield _raw(0, -1)
        yield _raw(1, 0)


def _packed(event: Input, expected: Output) -> str:
    values = astuple(event) + astuple(expected)
    widths = IN_WIDTHS + OUT_WIDTHS
    result = 0
    for value, width in zip(values, widths, strict=True):
        result = (result << width) | (int(value) & ((1 << width) - 1))
    return f"{result:0{(sum(widths) + 3) // 4}x}\n"


def _vectors(path: Path) -> tuple[dict, dict]:
    oracle = CaazOracle()
    counts = dict.fromkeys(("cycles", "controls", "pairs", "bypass", "faults"), 0)
    fault_coverage = dict.fromkeys(map(str, range(1, 7)), 0)
    with path.open("w", encoding="ascii") as stream:
        for event in _events():
            expected = oracle.step(event)
            stream.write(_packed(event, expected))
            counts["cycles"] += 1
            counts["controls"] += int(expected.acq_valid)
            counts["pairs"] += int(expected.out_valid and expected.out_pair)
            counts["bypass"] += int(expected.out_valid and not expected.out_pair)
            counts["faults"] += int(expected.fault_pulse)
            if expected.fault_pulse:
                fault_coverage[str(expected.fault_code)] += 1
    if not all(fault_coverage.values()):
        raise RuntimeError("missing fault coverage")
    counts["id_boundary_checks"] = 4
    return counts, fault_coverage


def _public_logs(out: Path, work: Path, simulator: str) -> None:
    """仅净化日志中的本机目录，保留工具返回内容。"""
    replacements = [(str(work), "<run-dir>"), (str(ROOT), "<repo>")]
    if Path(simulator).is_absolute() and Path(simulator).parent.parent != Path("/"):
        replacements.append((str(Path(simulator).parent.parent), "<verilator-install>"))

    def redact(value: str) -> str:
        for prefix, label in replacements:
            value = value.replace(prefix, label)
        return value

    for log in out.glob("*.log"):
        private_sha256 = digest(log)
        log.write_text(redact(log.read_text()))
        receipt_path = log.with_suffix(log.suffix + ".command.json")
        receipt = json.loads(receipt_path.read_text())
        receipt.update(
            argv=[redact(argument) for argument in receipt["argv"]],
            original_log_sha256=private_sha256,
            log_sha256=digest(log),
        )
        write_json(receipt_path, receipt)


def run(out: Path, simulator: list[str], timeout: int) -> dict:
    """以严格 lint、断言与 Python 队列参考逐拍对拍。"""
    env = os.environ.copy()
    with tempfile.TemporaryDirectory(prefix="caaz20_") as temporary:
        work = Path(temporary)
        version = checked_run(
            [*simulator, "-V"], cwd=work, env=env, log=out / "verilator.log", timeout=timeout
        )
        alias_runtime(work, env, version)
        for source in (RTL, TB):
            shutil.copyfile(ROOT / source, work / Path(source).name)
        vectors = work / "vectors.hex"
        expected, fault_coverage = _vectors(vectors)
        checked_run(
            [*simulator, "--lint-only", "-Wall", "--top-module", "caaz20_pair", Path(RTL).name],
            cwd=work,
            env=env,
            log=out / "lint.log",
            timeout=timeout,
        )
        checked_run(
            [
                *simulator,
                "--binary",
                "--timing",
                "--assert",
                "-Wall",
                "-j",
                "1",
                *verilator_compiler_args(),
                "--top-module",
                TOP,
                Path(RTL).name,
                Path(TB).name,
            ],
            cwd=work,
            env=env,
            log=out / "compile.log",
            timeout=timeout,
        )
        log = checked_run(
            [str(work / "obj_dir" / f"V{TOP}"), "+vectors=vectors.hex"],
            cwd=work,
            env=env,
            log=out / "run.log",
            timeout=timeout,
        )
        counts = completion(log, MARKER, tuple(expected))
        if counts != expected:
            raise RuntimeError(f"incomplete replay: {counts!r}; expected {expected!r}")
        version_banner = re.search(r"(?:R e p o r t|Report): (Verilator [^\r\n]+)", log)
        _public_logs(out, work, simulator[0])
        result = {
            "schema_version": 1,
            "status": "PASS",
            "scope": "CAAZ20_SINGLE_CLOCK_DIGITAL_PROTOCOL_AND_INTEGER_ARITHMETIC",
            "simulator": version_banner.group(1).strip()
            if version_banner
            else version.splitlines()[0],
            "stimulus_seed": SEED,
            "counts": counts,
            "fault_code_coverage": fault_coverage,
            "mismatches": 0,
            "source_sha256": {
                name: digest(ROOT / name)
                for name in (
                    RTL,
                    TB,
                    "task3/model/caaz20.py",
                    "task3/tools/run_caaz_checks.py",
                    "tools/run_open_rtl.py",
                )
            },
            "vector_sha256": digest(vectors),
            "log_sha256": {path.name: digest(path) for path in sorted(out.glob("*.log"))},
        }
        write_json(out / "result.json", result)
        return result


def main() -> int:
    """选择现有 Verilator 与仓库外新输出目录。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--verilator", type=Path)
    parser.add_argument("--timeout", type=int, default=120)
    args = parser.parse_args()
    try:
        simulator = (
            [str(args.verilator.expanduser().resolve(strict=True))]
            if args.verilator
            else command_for("verilator")
        )
        out = new_output(args.out)
        result = run(out, simulator, args.timeout)
    except (OSError, RuntimeError, ValueError) as error:
        print(f"CAAZ20_FAIL: {error}", file=sys.stderr)
        return 1
    print(f"{MARKER}: {result['counts']}; receipt={out / 'result.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
