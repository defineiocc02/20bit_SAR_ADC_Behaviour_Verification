#!/usr/bin/env python3
"""比较两份完整、非空观测轨迹的逐字段字符串。

调用者必须明确字段；CLI还要求预期行数和新库外输出目录。少行、多行、空文件、
列数错误或非法编码均拒绝，不按公共前缀忽略尾部。时间字段若在字段表中，也
作为普通字符串比较；x/z不被转换为数值。

这是声明轨迹内的差异检查，不验证编译、DUT完整执行或形式等价。退出码0表示
已观测差异，1表示声明记录全同，2表示输入或输出合同错误。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Row:
    """One observed record and its original one-based file line number."""

    n: int
    fields: tuple[str, ...]


def _fields(fields: tuple[str, ...]) -> None:
    if not fields or any(
        not isinstance(f, str) or not f or f.strip() != f or any(c.isspace() for c in f)
        for f in fields
    ):
        raise ValueError("an explicit nonempty list of single-token field names is required")
    if len(set(fields)) != len(fields):
        raise ValueError("field names must be unique")


def _capture(path: Path, fields: tuple[str, ...]) -> tuple[list[Row], bytes]:
    """Bind parsed records and hashes to the same captured input bytes."""
    _fields(fields)
    if not path.is_file() or path.stat().st_size >= 300 * 1024**2:
        raise ValueError("trace must be a regular file smaller than 300 MiB")
    payload = path.read_bytes()
    if len(payload) >= 300 * 1024**2:
        raise ValueError("trace grew beyond the supported file limit")
    rows = []
    for number, raw in enumerate(payload.decode("utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        values = tuple(line.split())
        if len(values) != len(fields):
            raise ValueError(f"{path}:{number}: column count {len(values)} != {len(fields)}")
        rows.append(Row(number, values))
    if not rows:
        raise ValueError(f"trace contains no observed records: {path}")
    return rows, payload


def read_trace(path: Path, fields: tuple[str, ...]) -> list[Row]:
    """Read strict UTF-8 records, retaining X/Z and rejecting empty or partial rows."""
    return _capture(path, fields)[0]


def compare(
    golden: list[Row],
    mutant: list[Row],
    fields: tuple[str, ...],
    *,
    expected_rows: int | None = None,
) -> dict:
    """Compare every declared row; record equality is not an HDL-equivalence proof."""
    _fields(fields)
    if not golden or not mutant:
        raise ValueError("both traces must contain observed records")
    if len(golden) != len(mutant):
        raise ValueError(f"trace lengths differ: golden={len(golden)} mutant={len(mutant)}")
    if expected_rows is not None:
        if type(expected_rows) is not int or expected_rows <= 0:
            raise ValueError("expected_rows must be a positive integer")
        if len(golden) != expected_rows:
            raise ValueError(f"observed {len(golden)} rows, expected exactly {expected_rows}")
    if any(
        len(row.fields) != len(fields)
        or any(not isinstance(value, str) or not value for value in row.fields)
        for row in [*golden, *mutant]
    ):
        raise ValueError("every observation must supply all declared string fields")
    per_field = dict.fromkeys(fields, 0)
    different = 0
    first = None
    for index, (gold, changed) in enumerate(zip(golden, mutant, strict=True)):
        bad = [
            name for name, a, b in zip(fields, gold.fields, changed.fields, strict=True) if a != b
        ]
        if bad:
            different += 1
            for name in bad:
                per_field[name] += 1
            if first is None:
                first = {
                    "row": index,
                    "golden_file_line": gold.n,
                    "mutant_file_line": changed.n,
                    "fields": bad,
                    "golden": " ".join(gold.fields),
                    "mutant": " ".join(changed.fields),
                }
    return {
        "schema_version": 1,
        "status": "OBSERVATION_COMPARISON_COMPLETE",
        "scope": "DECLARED_TRACE_RECORDS_NOT_COMPILATION_OR_FORMAL_PROOF",
        "killed": different > 0,
        "verdict": "killed" if different else "unobserved",
        "n_golden": len(golden),
        "n_mutant": len(mutant),
        "compared_rows": len(golden),
        "expected_rows": expected_rows,
        "diff_rows": different,
        "per_field": per_field,
        "first_diff": first,
        "fields": list(fields),
        "equivalence_proof": False,
        "hdl_execution_verified": False,
    }


def _output(path: Path) -> Path:
    if path.expanduser().exists() or path.expanduser().is_symlink():
        raise ValueError("output path already exists")
    out = path.expanduser().resolve()
    if (
        out.exists()
        or out == REPO
        or out.is_relative_to(REPO)
        or any((p / ".git").exists() for p in (out, *out.parents))
    ):
        raise ValueError("output must be a new directory outside Git working trees")
    out.mkdir(parents=True, exist_ok=False)
    return out


def main(argv: list[str] | None = None) -> int:
    """Require an explicit schema/count and refuse to overwrite any prior result."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--golden", type=Path, required=True)
    parser.add_argument("--mutant", type=Path, required=True)
    parser.add_argument(
        "--fields", required=True, help="Comma-separated field names, no implicit schema"
    )
    parser.add_argument("--expected-rows", type=int, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)
    fields = tuple(args.fields.split(","))
    try:
        _fields(fields)
        if args.expected_rows <= 0:
            raise ValueError("--expected-rows must be positive")
        golden = args.golden.expanduser().resolve()
        mutant = args.mutant.expanduser().resolve()
        golden_rows, golden_bytes = _capture(golden, fields)
        mutant_rows, mutant_bytes = _capture(mutant, fields)
        result = compare(golden_rows, mutant_rows, fields, expected_rows=args.expected_rows)
        out = _output(args.out)
        (out / "golden.trace").write_bytes(golden_bytes)
        (out / "mutant.trace").write_bytes(mutant_bytes)
        result["input_sha256"] = {
            "golden": hashlib.sha256(golden_bytes).hexdigest(),
            "mutant": hashlib.sha256(mutant_bytes).hexdigest(),
        }
        (out / "trace_comparison.json").write_text(
            json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
            encoding="utf-8",
        )
    except (OSError, UnicodeError, ValueError) as error:
        print(f"trace_compare: {error}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    elif args.quiet:
        print(result["verdict"])
    else:
        print(
            f"TRACE_COMPARISON_COMPLETE rows={result['compared_rows']} diff_rows={result['diff_rows']} verdict={result['verdict']} equivalence=NOT_ASSESSED"
        )
    return 0 if result["killed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
