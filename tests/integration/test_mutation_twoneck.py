"""用显式组件数值夹具验证真实算术负控与执行完整性。

不再调用旧 P3／远程 VCS 或 sim/vectors 默认包。完整轨迹辅助函数只描述
测试声明的执行索引；实际 RTL 正负控由公开整数审计入口新编译、执行并留痕。
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
FIELDS = ("sample_idx", "dout", "clip", "analog_ovf")


def _complete_trace(path: Path, returncode: int, *, expected_rows: int) -> None:
    """核对声明的合成执行索引及完整结束记录，非零进程状态不能判为完成。"""
    assert returncode == 0, f"process failed: rc={returncode}"
    assert type(expected_rows) is int and expected_rows > 0
    lines = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    marker = f"# COMPONENT_TRACE_COMPLETE rows={expected_rows}"
    assert lines and lines[-1] == marker, "missing final completion marker"
    assert lines.count(marker) == 1, "duplicate completion marker"
    rows = [line.split() for line in lines if not line.startswith("#")]
    assert len(rows) == expected_rows, "missing or extra trace rows"
    assert all(len(row) == len(FIELDS) for row in rows), "malformed trace"
    assert [row[0] for row in rows] == [
        str(i) for i in range(expected_rows)
    ], "invalid execution indices"


def test_valid_completion_marker_cannot_replace_missing_observations(tmp_path):
    path = tmp_path / "short.trace"
    path.write_text("0 word 0 0\n# COMPONENT_TRACE_COMPLETE rows=3\n")
    with pytest.raises(AssertionError, match="missing or extra"):
        _complete_trace(path, 0, expected_rows=3)


@pytest.mark.slow
@pytest.mark.integration
def test_actual_rtl_integer_math_detects_both_declared_arithmetic_mutants(tmp_path):
    """真实编译四个数字算术子模块；失败必须是指定失配，不接受编译失败代替。"""
    fixture = {
        "schema_version": 1,
        "scope": "COMPONENT_SYNTHETIC_DIGITAL",
        "description": "Explicit raw integer arithmetic fixture, not ADC physical parameters",
        "voltage_bits": 64,
        "voltage_fraction_bits": 32,
        "weight_fraction_bits": 30,
        "accumulator_bits": 96,
        "output_bits": 20,
        "adc2_bits": 12,
        "sum_bits": 64,
        "divisor_bits": 64,
        "divider_stages": 7,
        "random_cases": 32,
        "seed": 971,
    }
    source = tmp_path / "math_format.json"
    source.write_text(json.dumps(fixture) + "\n")
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    out = tmp_path / "actual_math"
    command = [
        sys.executable,
        str(REPO / "tools/audit_rtl_arithmetic.py"),
        "--out",
        str(out),
        "--format-json",
        str(source),
        "--negative-controls",
        "--timeout",
        "180",
    ]
    result = subprocess.run(command, cwd=REPO, env=env, text=True, capture_output=True, timeout=240)
    assert result.returncode == 0, result.stdout + result.stderr
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["status"] == "PASS"
    assert manifest["scope"] == "COMPONENT_SYNTHETIC_DIGITAL"
    assert manifest["positive"]["status"] == "PASS"
    assert set(manifest["negative_controls"]) == {"wide_denominator_guard", "negative_floor"}
    assert all(count > 0 for count in manifest["positive"]["counts"].values())
    for name, record in manifest["negative_controls"].items():
        assert record["status"] == "EXPECTED_FAIL_DETECTED"
        assert record["raw_returncode"] != 0
        assert "DIV mismatch" in record["failure"]
        compile_record = json.loads((out / name / "compile.log.command.json").read_text())
        assert compile_record["raw_returncode"] == 0
    assert json.loads(source.read_text()) == fixture


def _verdict(golden: Path, mutant: Path, *, expected_rows: int) -> dict:
    """比较已声明完整的合成轨迹；不把文件比较当成 HDL 执行证据。"""
    _complete_trace(golden, 0, expected_rows=expected_rows)
    _complete_trace(mutant, 0, expected_rows=expected_rows)
    spec = importlib.util.spec_from_file_location(
        "_strict_mutation_trace", REPO / "tools/trace_compare.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.compare(
        module.read_trace(golden, FIELDS),
        module.read_trace(mutant, FIELDS),
        FIELDS,
        expected_rows=expected_rows,
    )
