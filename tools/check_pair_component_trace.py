#!/usr/bin/env python3
"""核对显式配对组件输入与 DUT 实际输出的 25 列 CSV。

输入 JSON 与 export_pair_component_vectors 相同。每行记录 case_id、cycle、
10 个实际输入和 13 个实际输出。核对器从 JSON 输入复算参考，不使用向量中
预填的 expected 值。参考算法与导出器共享，独立于 SV DUT，并非两套独立 Python 算法。

通过仅表示所给合成数字夹具的算术及样本归属一致，不证明 CBIAS 保持、H/K、
扰动电荷、ADC 性能、工艺适用性或时序。schema 或 binding 不匹配即拒绝。
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import io
import json
from pathlib import Path


def _reference_module():
    path = Path(__file__).with_name("export_pair_component_vectors.py")
    spec = importlib.util.spec_from_file_location("_pair_component_reference", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("pair-component reference module is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def check_trace(input_path: Path, trace_path: Path) -> dict:
    """核对全部输入、输出及非零完整计数，保留失败位置与失败总数。"""
    reference = _reference_module()
    data, input_sha = reference.load_input(input_path)
    data, expected_rows = reference.evaluate_records(data)
    if not trace_path.is_file() or trace_path.stat().st_size > reference.MAX_INPUT_BYTES:
        raise ValueError("trace must be a regular CSV file no larger than 300 MB")
    failures = []
    failure_count = check_count = actual_outputs = 0
    raw_trace = trace_path.read_bytes()
    reader = csv.DictReader(io.StringIO(raw_trace.decode("utf-8")))
    if reader.fieldnames != list(reference.COLUMNS):
        raise ValueError("trace requires the exact pair-component 25-column CSV schema")
    rows = []
    for row in reader:
        if len(rows) >= data["expected"]["record_count"]:
            raise ValueError("trace has extra rows beyond the declared complete fixture")
        rows.append(row)
    if len(rows) != data["expected"]["record_count"]:
        raise ValueError("trace row count does not match the complete input fixture")
    for index, (actual_row, expected_row) in enumerate(zip(rows, expected_rows, strict=True)):
        if None in actual_row or any(value is None for value in actual_row.values()):
            raise ValueError(f"trace row {index}: missing or extra CSV fields")
        try:
            actual = {name: int(actual_row[name]) for name in reference.COLUMNS}
        except ValueError as error:
            raise ValueError(
                f"trace row {index}: every signal must be a decimal integer"
            ) from error
        expected = {
            "case_id": expected_row["case_id"],
            "cycle": expected_row["cycle"],
            **expected_row["inputs"],
            **expected_row["expected"],
        }
        actual_outputs += actual["out_valid"] == 1
        for name in reference.COLUMNS:
            check_count += 1
            if actual[name] != expected[name]:
                failure_count += 1
                if len(failures) < 100:
                    failures.append(
                        {
                            "record": index,
                            "case_id": expected_row["case_id"],
                            "cycle": expected_row["cycle"],
                            "field": name,
                            "expected": expected[name],
                            "actual": actual[name],
                        }
                    )
    if actual_outputs != data["expected"]["valid_output_count"]:
        failure_count += 1
        if len(failures) < 100:
            failures.append(
                {
                    "field": "valid_output_count",
                    "expected": data["expected"]["valid_output_count"],
                    "actual": actual_outputs,
                }
            )
    return {
        "schema": "pair-component-analysis-v1",
        "evidence_scope": "COMPONENT_SYNTHETIC_DIGITAL",
        "status": "PASS" if failure_count == 0 else "FAIL",
        "binding": data["binding"],
        **data["expected"],
        "check_count": check_count,
        "actual_valid_output_count": actual_outputs,
        "failure_count": failure_count,
        "failures": failures,
        "failure_details_limit": 100,
        "input_record_sha256": input_sha,
        "actual_trace_sha256": hashlib.sha256(raw_trace).hexdigest(),
        "reference_sha256": hashlib.sha256(Path(reference.__file__).read_bytes()).hexdigest(),
        "checker_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "scope": "Digital pair component only; no ADC/noise/circuit/timing or literature-performance conclusion",
    }


def write_analysis(input_path: Path, trace_path: Path, output_path: Path) -> dict:
    """仅向新目录写分析结果，不覆盖源输入、旧结果或符号链接。"""
    if output_path.exists() or output_path.is_symlink():
        raise ValueError("--out must be a new directory, not an existing path or symlink")
    result = check_trace(input_path, trace_path)
    output_path.mkdir(parents=True, exist_ok=False)
    with (output_path / "pair_component_analysis.json").open("xb") as output:
        output.write((json.dumps(result, indent=2) + "\n").encode("utf-8"))
    return result


def main() -> int:
    """核对实际 DUT 数字轨迹；缺项、失配或覆盖请求均以非零状态退出。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path, help="Explicit input JSON used by DUT")
    parser.add_argument("--trace", required=True, type=Path, help="Actual 25-column DUT CSV")
    parser.add_argument("--out", required=True, type=Path, help="New analysis output directory")
    args = parser.parse_args()
    try:
        result = write_analysis(args.input, args.trace, args.out)
    except (OSError, UnicodeError, ValueError) as error:
        parser.error(str(error))
    print(
        json.dumps(
            {
                name: result[name]
                for name in (
                    "evidence_scope",
                    "status",
                    "record_count",
                    "case_count",
                    "actual_valid_output_count",
                    "check_count",
                    "failure_count",
                )
            }
        )
    )
    return int(result["failure_count"] != 0)


if __name__ == "__main__":
    raise SystemExit(main())
