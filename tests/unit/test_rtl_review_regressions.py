"""Independent algebraic counterexamples and mutation execution-integrity gates."""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import numpy as np
import pytest

REPO = Path(__file__).resolve().parents[2]


def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, REPO / relative)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


harness = load("review_twoneck", "tests/integration/test_mutation_twoneck.py")
mirror = load("review_mirror", "sim/ref/recon_rtl_mirror.py")


def test_lfsr_has_maximal_nonzero_period():
    source = (REPO / "rtl/core/dither_gen.sv").read_text(encoding="utf-8")
    match = re.search(r"TAPS\s*=\s*32'h([0-9a-fA-F_]+)", source)
    assert match
    taps = int(match[1].replace("_", ""), 16)

    def step(x):
        return (x >> 1) ^ (taps if x & 1 else 0)

    def apply(matrix, x):
        value = 0
        for k, column in enumerate(matrix):
            if x & (1 << k):
                value ^= column
        return value

    def advance(n):
        matrix = [step(1 << k) for k in range(32)]
        value = 1
        while n:
            if n & 1:
                value = apply(matrix, value)
            matrix = [apply(matrix, column) for column in matrix]
            n >>= 1
        return value

    period = (1 << 32) - 1
    assert advance(period) == 1
    for prime_factor in (3, 5, 17, 257, 65537):
        assert advance(period // prime_factor) != 1


@pytest.mark.parametrize(
    "offset,min_q,expected", [(4096, 1, (524287, 524288)), (0, 8191, (524288, 524289))]
)
def test_offset_complement_is_observable(offset, min_q, expected):
    kw = {
        "nbits": 12,
        "plus_row": np.array([0, 1]),
        "a_bit_row": np.array([1, 1]),
        "rail_add_row": np.array([0, 0]),
        "inj_v": 0.0,
        "v_fs": 3.0,
        "weights_row": np.array([1 << 29, 1 << 29], dtype=np.int64),
        "code": 0,
        "min_q": min_q,
        "max_q": min_q + 4096,
    }
    normal = mirror.reconstruct(offset_q=offset, **kw)
    mutant = mirror.reconstruct(offset_q=~offset, **kw)
    assert (normal[0], mutant[0]) == expected
    assert not any(normal[1:]) and not any(mutant[1:])


def trace(tmp_path, text):
    path = tmp_path / "trace.txt"
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture
def short_run(monkeypatch):
    monkeypatch.setattr(harness, "_expected_rows", lambda: 3)


GOOD = "0 0a 0 0\n1 0b 0 0\n2 0c 0 0\n# P3_TRACE_COMPLETE rows=3\n"


@pytest.mark.parametrize("rc", [0, 3, 4])
def test_complete_run_can_report_dut_mismatches(tmp_path, short_run, rc):
    harness._complete_trace(trace(tmp_path, GOOD), rc)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "0 0a 0 0\n",
        GOOD.replace("1 0b 0 0\n", ""),
        GOOD.replace("1 0b", "2 0b"),
        GOOD.replace("rows=3", "rows=2"),
        GOOD + "3 0d 0 0\n",
        GOOD.replace("0a 0 0", "0a 0"),
    ],
)
def test_incomplete_run_is_never_a_mutation_verdict(tmp_path, short_run, text):
    with pytest.raises(AssertionError):
        harness._complete_trace(trace(tmp_path, text))


@pytest.mark.parametrize("rc", [1, 2, 5, 255])
def test_environment_compile_timeout_transport_failures_rejected(tmp_path, short_run, rc):
    with pytest.raises(AssertionError):
        harness._complete_trace(trace(tmp_path, GOOD), rc)


def test_old_trace_removed_before_launch(tmp_path, monkeypatch):
    monkeypatch.setattr(harness, "REPO", tmp_path)
    stale = tmp_path / "sim/artifacts/fresh/p3_trace.txt"
    stale.parent.mkdir(parents=True)
    stale.write_text(GOOD, encoding="utf-8")

    def failed_run(*args, **kwargs):
        assert not stale.exists()
        raise AssertionError("compile failed")

    monkeypatch.setattr(harness, "_run", failed_run)
    with pytest.raises(AssertionError, match="compile failed"):
        harness._sim("fresh", None)


def test_empty_mutant_cannot_pass_negative_control(tmp_path, short_run):
    golden = tmp_path / "golden.txt"
    golden.write_text(GOOD, encoding="utf-8")
    with pytest.raises(AssertionError):
        harness._verdict(golden, trace(tmp_path, ""))


@pytest.mark.parametrize("mutated,verdict", [(False, "unobserved"), (True, "killed")])
def test_complete_pair_preserves_comparator_semantics(tmp_path, short_run, mutated, verdict):
    golden = tmp_path / "golden.txt"
    golden.write_text(GOOD, encoding="utf-8")
    mutant = trace(tmp_path, GOOD.replace("0b", "ff") if mutated else GOOD)
    assert harness._verdict(golden, mutant)[0] == verdict


def test_mutation_sites_are_still_resolvable():
    assert harness._site("rtl/core/div_floor.sv", "q_next = (quo <<") > 0
    assert harness._site("rtl/core/weight_store.sv", "assign accept") > 0
