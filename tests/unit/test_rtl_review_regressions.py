"""独立整数反例及变异执行完整性，解除对旧P3向量和LFSR入口的依赖。

旧dither_gen已退役，不能继续要求其PRNG周期作为源Fig19性质；显式控制字不
生成随机序列。保留offset补码反例、完整/失败/空轨迹和真正源位点的约束。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, REPO / relative)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


harness = load("review_twoneck", "tests/integration/test_mutation_twoneck.py")
mirror = load("review_mirror", "sim/ref/recon_rtl_mirror.py")
runner = load("review_runner", "tools/run_open_rtl.py")
mutator = load("review_mutator", "tools/mutate_rtl.py")


@pytest.mark.parametrize(
    "offset,min_q,expected", [(4096, 1, (524287, 524288)), (0, 8191, (524288, 524289))]
)
def test_offset_complement_is_observable(offset, min_q, expected):
    arguments = {
        "adc2_bits": 12,
        "known_common_q": 0,
        "total": 1 << 30,
        "gain": 1 << 30,
        "rails": 0,
        "weight_fraction_bits": 30,
        "voltage_fraction_bits": 32,
        "voltage_bits": 64,
        "accumulator_bits": 96,
        "output_bits": 20,
        "sum_bits": 64,
        "divider_stages": 7,
        "code": 0,
        "min_q": min_q,
        "max_q": min_q + 4096,
    }
    normal = mirror.reconstruct(offset_q=offset, **arguments)
    mutant = mirror.reconstruct(offset_q=~offset, **arguments)
    assert (normal[0], mutant[0]) == expected
    assert not any(normal[1:]) and not any(mutant[1:])


def trace(tmp_path, text):
    path = tmp_path / "trace.txt"
    path.write_text(text, encoding="utf-8")
    return path


GOOD = "0 0a 0 0\n1 0b 0 0\n2 0c 0 0\n# COMPONENT_TRACE_COMPLETE rows=3\n"


def test_complete_zero_returncode_record_is_accepted(tmp_path):
    harness._complete_trace(trace(tmp_path, GOOD), 0, expected_rows=3)


@pytest.mark.parametrize("rc", [1, 2, 3, 4, 5, 255])
def test_completion_marker_never_overrides_failed_execution(tmp_path, rc):
    with pytest.raises(AssertionError, match="process failed"):
        harness._complete_trace(trace(tmp_path, GOOD), rc, expected_rows=3)


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
        GOOD + "# COMPONENT_TRACE_COMPLETE rows=3\n",
    ],
)
def test_incomplete_run_is_never_a_mutation_verdict(tmp_path, text):
    with pytest.raises(AssertionError):
        harness._complete_trace(trace(tmp_path, text), 0, expected_rows=3)


def test_stale_output_is_rejected_without_destroying_previous_evidence(tmp_path):
    existing = tmp_path / "old_run"
    existing.mkdir()
    stale = existing / "trace.txt"
    stale.write_text(GOOD)
    with pytest.raises(ValueError, match="already exists"):
        runner.new_output(existing)
    assert stale.read_text() == GOOD


def test_empty_mutant_cannot_pass_negative_control(tmp_path):
    golden = tmp_path / "golden.txt"
    golden.write_text(GOOD, encoding="utf-8")
    with pytest.raises(AssertionError):
        harness._verdict(golden, trace(tmp_path, ""), expected_rows=3)


@pytest.mark.parametrize("mutated,verdict", [(False, "unobserved"), (True, "killed")])
def test_complete_pair_preserves_comparator_semantics(tmp_path, mutated, verdict):
    golden = tmp_path / "golden.txt"
    golden.write_text(GOOD, encoding="utf-8")
    mutant = trace(tmp_path, GOOD.replace("0b", "ff") if mutated else GOOD)
    result = harness._verdict(golden, mutant, expected_rows=3)
    assert result["verdict"] == verdict
    assert result["equivalence_proof"] is False
    assert result["hdl_execution_verified"] is False


def test_live_divider_mutation_target_is_found_from_source_not_a_frozen_line_number():
    sites = mutator.enumerate_sites(REPO / "rtl")
    assert any(
        s.rel == "rtl/core/div_floor.sv" and s.op == "ExprUpdate" and s.span == "<<" for s in sites
    )
