#!/usr/bin/env python3
"""以独立串行位规格逐拍核对 Python 模型与 Verilator。"""

from __future__ import annotations

import argparse
import os
import random
import re
import shutil
import sys
import tempfile
from collections.abc import Iterator
from dataclasses import asdict, dataclass, replace
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True

from task1.model.module1 import DEFAULT_DEM_SEED, advance_dem_rng, dem_snapshot  # noqa: E402
from task2.model.dither_word_gen import (  # noqa: E402
    DEFAULT_DITHER_SEED,
    DitherWordGenerator,
    DitherWordOutput,
)
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

TOP = "dither_word_gen_tb"
MARKER = "TASK2_DITHER_WORD_COMPLETE"
RTL = "task2/rtl/dither_word_gen.sv"
TB = "task2/tb/dither_word_gen_tb.sv"
STIMULUS_SEED = 0x2D17
DEFAULT_WORD_COUNT = 131_072
MAX_LAG = 32


@dataclass(frozen=True, slots=True)
class Stimulus:
    """一个上升沿前的输入。"""

    rst_n: bool = True
    clear: bool = False
    dither_enable: bool = True
    seed_load: bool = False
    seed: int = 0
    sample_accept: bool = False
    sample_id: int = 0


def _serial_word(state: int) -> tuple[int, int]:
    """按位队列从最早位开始取两个延迟位置，独立生成一个字。"""
    history = list(f"{state:031b}")
    generated = []
    for _ in range(6):
        feedback = "1" if history[0] != history[3] else "0"
        history.pop(0)
        history.append(feedback)
        generated.append(feedback)
    return int("".join(generated), 2), int("".join(history), 2)


def _reference(command: Stimulus, prior: DitherWordOutput) -> DitherWordOutput:
    """从事务规则与串行位队列计算本拍预期。"""
    if not command.rst_n or command.clear:
        return DitherWordOutput(0, False, 0, 0x5A1709C3)
    if command.seed_load:
        bad_seed = command.seed == 0 or command.sample_accept
        return DitherWordOutput(
            prior.word,
            False,
            prior.word_sample_id,
            prior.rng_state if bad_seed else command.seed,
            bad_seed,
        )
    if not command.sample_accept:
        return DitherWordOutput(prior.word, False, prior.word_sample_id, prior.rng_state)
    if not command.dither_enable:
        return DitherWordOutput(0, True, command.sample_id, prior.rng_state)
    word, state = _serial_word(prior.rng_state)
    return DitherWordOutput(word, True, command.sample_id, state)


def _commands(word_count: int) -> Iterator[tuple[Stimulus, bool]]:
    """定向、随机握手和连续样本；仅最后一段用于词频和相关性统计。"""
    accept = Stimulus(sample_accept=True, sample_id=41)
    load = Stimulus(seed_load=True, seed=1 << 30)
    yield replace(accept, rst_n=False, seed_load=True), False
    yield load, False
    yield accept, False
    yield replace(accept, sample_id=42), False
    yield Stimulus(sample_id=0xDEADBEEF), False
    yield replace(accept, dither_enable=False, sample_id=43), False
    yield replace(accept, sample_id=44), False
    yield replace(load, seed=0), False
    yield replace(accept, seed_load=True, seed=3), False
    yield replace(accept, dither_enable=False, seed_load=True, seed=3), False
    yield replace(load, seed=(1 << 31) - 1), False
    yield replace(accept, sample_id=(1 << 32) - 1), False
    yield replace(accept, clear=True, seed_load=True), False
    yield replace(accept, sample_id=0), False

    rng = random.Random(STIMULUS_SEED)
    for _ in range(512):
        yield (
            Stimulus(
                rst_n=rng.randrange(32) != 0,
                clear=rng.randrange(32) == 0,
                dither_enable=rng.randrange(4) != 0,
                seed_load=rng.randrange(8) == 0,
                seed=rng.getrandbits(31) if rng.randrange(4) else 0,
                sample_accept=rng.randrange(4) != 0,
                sample_id=rng.getrandbits(32),
            ),
            False,
        )

    yield Stimulus(clear=True), False
    for sample_id in range(word_count):
        yield Stimulus(sample_accept=True, sample_id=sample_id), True


def _packed(command: Stimulus, expected: DitherWordOutput) -> str:
    # 与 testbench 的 packed struct 同序，左端为最高位。
    fields = (
        (command.rst_n, 1),
        (command.clear, 1),
        (command.dither_enable, 1),
        (command.seed_load, 1),
        (command.seed, 31),
        (command.sample_accept, 1),
        (command.sample_id, 32),
        (expected.word, 6),
        (expected.word_valid, 1),
        (expected.word_sample_id, 32),
        (expected.rng_state, 31),
        (expected.seed_error, 1),
    )
    packed = 0
    for value, width in fields:
        packed = (packed << width) | int(value)
    return f"{packed:035x}\n"


def _vectors(path: Path, word_count: int) -> tuple[dict[str, int], list[int]]:
    model = DitherWordGenerator()
    expected = DitherWordOutput(0, False, 0, 0x5A1709C3)
    counts = {"cycles": 0, "words": 0, "seed_errors": 0}
    words = []
    with path.open("w", encoding="ascii") as stream:
        for command, steady in _commands(word_count):
            expected = _reference(command, expected)
            actual = model.step(**asdict(command))
            if actual != expected:
                raise RuntimeError(f"Python mismatch at cycle {counts['cycles']}: {actual!r}")
            if steady:
                words.append(expected.word)
            stream.write(_packed(command, expected))
            counts["cycles"] += 1
            counts["words"] += int(expected.word_valid)
            counts["seed_errors"] += int(expected.seed_error)
    return counts, words


def _lag_series(first: np.ndarray, second: np.ndarray) -> list[float]:
    coefficients = []
    for lag in range(-MAX_LAG, MAX_LAG + 1):
        start = max(0, -lag)
        stop = min(len(first), len(first) - lag)
        a = first[start:stop]
        b = second[start + lag : stop + lag]
        coefficients.append(float(np.corrcoef(a, b)[0, 1]))
    return coefficients


def _statistics(words: list[int]) -> dict:
    dither = np.asarray(words, dtype=np.float64)
    phases = np.empty((2, len(words)), dtype=np.float64)
    dem_state = DEFAULT_DEM_SEED
    for position in range(len(words)):
        snapshot = dem_snapshot(dem_state, enabled=True)
        phases[:, position] = snapshot.horizontal_phase, snapshot.vertical_phase
        dem_state = advance_dem_rng(dem_state)

    horizontal = _lag_series(dither, phases[0])
    vertical = _lag_series(dither, phases[1])
    counts = np.bincount(words, minlength=64)
    observed = int(np.count_nonzero(counts))
    if observed != 64:
        raise RuntimeError(f"continuous replay observed {observed}/64 words")
    return {
        "continuous_accepted_words": len(words),
        "word_values_observed": observed,
        "word_counts_0_to_63": counts.tolist(),
        "minimum_word_count": int(counts.min()),
        "maximum_word_count": int(counts.max()),
        "first_16_words": words[:16],
        "word_lag_1_pearson": float(np.corrcoef(dither[:-1], dither[1:])[0, 1]),
        "dem_pearson_lags": list(range(-MAX_LAG, MAX_LAG + 1)),
        "dem_horizontal_pearson": horizontal,
        "dem_vertical_pearson": vertical,
        "dem_horizontal_max_abs_pearson": max(map(abs, horizontal)),
        "dem_vertical_max_abs_pearson": max(map(abs, vertical)),
    }


def run(out: Path, command: list[str], timeout: int, word_count: int) -> dict:
    """生成向量、从零构建仿真，并封存计数和有限样本统计。"""
    env = os.environ.copy()
    with tempfile.TemporaryDirectory(prefix="task2_dither_") as temporary:
        work = Path(temporary)
        version = checked_run(
            [*command, "-V"], cwd=work, env=env, log=out / "verilator.log", timeout=timeout
        )
        alias_runtime(work, env, version)
        for name in (RTL, TB):
            shutil.copyfile(ROOT / name, work / Path(name).name)
        vectors = work / "vectors.hex"
        expected, words = _vectors(vectors, word_count)
        checked_run(
            [*command, "--lint-only", "-Wall", "--top-module", "dither_word_gen", Path(RTL).name],
            cwd=work,
            env=env,
            log=out / "lint.log",
            timeout=timeout,
        )
        checked_run(
            [
                *command,
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
            [str(work / "obj_dir" / f"V{TOP}"), f"+vectors={vectors}"],
            cwd=work,
            env=env,
            log=out / "run.log",
            timeout=timeout,
        )
        counts = completion(log, MARKER, tuple(expected))
        if counts != expected:
            raise RuntimeError(f"incomplete replay: {counts!r}; expected {expected!r}")

        reported_version = re.search(r"R e p o r t: (Verilator [^\r\n]+)", log)
        result = {
            "schema_version": 1,
            "status": "PASS",
            "scope": "DITHER_WORD_GENERATOR_DIGITAL_ONLY",
            "simulator": (
                reported_version.group(1).strip()
                if reported_version
                else version.splitlines()[0].strip()
            ),
            "python_version": sys.version.split()[0],
            "numpy_version": np.__version__,
            "delay_polynomial": "1+x^28+x^31",
            "dither_seed": DEFAULT_DITHER_SEED,
            "dem_seed": DEFAULT_DEM_SEED,
            "stimulus_seed": STIMULUS_SEED,
            "counts": counts,
            "mismatches": 0,
            "statistics": _statistics(words),
            "statistics_scope": "Finite continuous replay; DEM phases use the Task 1 Python model.",
            "source_sha256": {
                name: digest(ROOT / name)
                for name in (
                    RTL,
                    TB,
                    "task2/model/dither_word_gen.py",
                    "task2/tools/run_dither_word_checks.py",
                    "task1/model/module1.py",
                    "tools/run_open_rtl.py",
                )
            },
            "vector_sha256": digest(vectors),
            "log_sha256": {file.name: digest(file) for file in sorted(out.glob("*.log"))},
        }
        write_json(out / "result.json", result)
        return result


def main() -> int:
    """解析已安装 Verilator 和本次输出目录。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path, help="仓库外尚不存在的输出目录")
    parser.add_argument("--verilator", type=Path, help="现有 Verilator 可执行文件")
    parser.add_argument("--timeout", type=int, default=120, help="每条工具命令的超时（秒）")
    parser.add_argument("--words", type=int, default=DEFAULT_WORD_COUNT, help="连续接受样本数")
    args = parser.parse_args()
    try:
        if not 100_000 <= args.words <= 1_000_000 or args.timeout <= 0:
            raise ValueError("require 100000 to 1000000 words and a positive timeout")
        command = (
            [str(args.verilator.expanduser().resolve(strict=True))]
            if args.verilator is not None
            else command_for("verilator")
        )
        out = new_output(args.out)
        result = run(out, command, args.timeout, args.words)
    except (OSError, ValueError, RuntimeError) as error:
        print(f"TASK2_DITHER_WORD_FAIL: {error}", file=sys.stderr)
        return 1
    print(f"{MARKER}: {result['counts']}; receipt={out / 'result.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
