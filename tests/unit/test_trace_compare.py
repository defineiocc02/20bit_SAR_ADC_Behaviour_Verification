"""检查完整显式轨迹的逐字段字符串比较与失败关闭边界。

保留 X/Z、全部字段及正反对照；空轨迹、缺行、多行或缺列均拒绝，不能由公共
前缀相同推断执行完整或 HDL 等价。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
FIELDS = ("sample_idx", "dout", "clip", "analog_ovf")


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


tc = _load("trace_compare_under_test", REPO / "tools" / "trace_compare.py")


def _write(p: Path, rows: list[str]) -> Path:
    p.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return p


def _cmp(tmp_path: Path, golden: list[str], mutant: list[str]) -> dict:
    g = _write(tmp_path / "g.txt", golden)
    m = _write(tmp_path / "m.txt", mutant)
    return tc.compare(tc.read_trace(g, FIELDS), tc.read_trace(m, FIELDS), FIELDS)


GOLD = [
    "0 0a 0 0",
    "1 0b 0 0",
    "2 0c 2 0",
    "3 0d 1 1",
    "4 0e 0 1",
]


def test_identical_traces_are_unobserved(tmp_path):
    """判据不能退化成"恒 killed"：同一份 trace 必须 unobserved。"""
    r = _cmp(tmp_path, GOLD, list(GOLD))
    assert r["verdict"] == "unobserved"
    assert r["killed"] is False
    assert r["diff_rows"] == 0
    assert r["per_field"] == {f: 0 for f in FIELDS}


def test_single_field_difference_in_prefix_is_killed(tmp_path):
    """任一字段字符串不同 ⇒ output-observable（这里只动 dout）。"""
    mut = list(GOLD)
    mut[3] = "3 0e 1 1"
    r = _cmp(tmp_path, GOLD, mut)
    assert r["killed"] is True
    assert r["diff_rows"] == 1
    assert r["per_field"]["dout"] == 1
    assert r["per_field"]["clip"] == 0
    assert r["first_diff"]["row"] == 3
    assert r["first_diff"]["fields"] == ["dout"]


def test_clip_field_alone_is_enough(tmp_path):
    """多字段时不能只看 dout：只动 clip 也必须 killed。"""
    mut = list(GOLD)
    mut[2] = "2 0c 0 0"
    r = _cmp(tmp_path, GOLD, mut)
    assert r["killed"] is True
    assert r["per_field"]["clip"] == 1
    assert r["per_field"]["dout"] == 0


def test_index_field_difference_is_killed(tmp_path):
    """时间字段本身就是被观测字段：索引错位即差异。"""
    mut = list(GOLD)
    mut[1] = "7 0b 0 0"
    r = _cmp(tmp_path, GOLD, mut)
    assert r["killed"] is True
    assert r["per_field"]["sample_idx"] == 1


def test_shortened_trace_is_rejected_without_an_unobserved_verdict(tmp_path):
    with pytest.raises(ValueError, match="lengths"):
        _cmp(tmp_path, GOLD, GOLD[:3])


def test_extra_rows_are_rejected_instead_of_ignored(tmp_path):
    with pytest.raises(ValueError, match="lengths"):
        _cmp(tmp_path, GOLD, [*GOLD, "5 0f 0 1", "6 10 0 1"])


def test_x_z_count_as_observed_values(tmp_path):
    """检查 x 与 0 不同、x 与 x 相同，以及 z 字符串的差异。

    不将 X/Z 转为数值或 NaN，避免改变原字符串观测语义。
    """
    gold = ["0 bd8a2 0 0", "1 xxxxx 0 x", "2 0c 1 1"]
    same = list(gold)
    r_same = _cmp(tmp_path, gold, same)
    assert r_same["verdict"] == "unobserved"

    mut = list(gold)
    mut[1] = "1 xxxxx 0 0"  # 把 x 换成 0
    r_diff = _cmp(tmp_path, gold, mut)
    assert r_diff["killed"] is True
    assert r_diff["per_field"]["analog_ovf"] == 1

    mut2 = list(gold)
    mut2[0] = "0 zzzzz 0 0"
    r_z = _cmp(tmp_path, gold, mut2)
    assert r_z["killed"] is True
    assert r_z["per_field"]["dout"] == 1


def test_comment_and_blank_lines_are_skipped(tmp_path):
    """`#` 注释与空行不参与比较（否则任何带表头的 trace 都会被判差异）。"""
    gold = ["# cols: sample_idx dout clip analog_ovf", "", *GOLD]
    mut = ["# cols: sample_idx dout clip analog_ovf", "", *GOLD]
    r = _cmp(tmp_path, gold, mut)
    assert r["verdict"] == "unobserved"
    assert r["n_golden"] == len(GOLD)


def test_empty_trace_is_rejected_before_any_comparison(tmp_path):
    with pytest.raises(ValueError, match="no observed"):
        _cmp(tmp_path, GOLD, [])


def test_bad_column_count_is_an_error(tmp_path):
    """列数不等于 schema 时必须报错，不能静默按前缀切。"""
    g = _write(tmp_path / "g.txt", GOLD)
    m = _write(tmp_path / "m.txt", ["0 0a 0"])
    with pytest.raises(ValueError):
        tc.read_trace(m, FIELDS)
    assert tc.read_trace(g, FIELDS)[0].fields == ("0", "0a", "0", "0")


@pytest.mark.parametrize(
    ("mutant_row", "expect_field"),
    [
        ("3 0e 1 1", "dout"),
        ("3 0d 3 1", "clip"),
        ("3 0d 1 0", "analog_ovf"),
        ("9 0d 1 1", "sample_idx"),
    ],
)
def test_each_field_can_independently_kill(tmp_path, mutant_row, expect_field):
    """四个字段各自单独都能把 verdict 打成 killed（不能有"只有 dout 算数"这种实现）。"""
    mut = list(GOLD)
    mut[3] = mutant_row
    r = _cmp(tmp_path, GOLD, mut)
    assert r["killed"] is True
    assert r["per_field"][expect_field] == 1
    assert sum(r["per_field"].values()) == 1


@pytest.mark.parametrize("fields", [(), ("a", "a"), ("a b",), ("",)])
def test_field_schema_must_be_explicit_unique_single_tokens(fields):
    with pytest.raises(ValueError):
        tc.compare([tc.Row(1, ("0",))], [tc.Row(1, ("0",))], fields)


@pytest.mark.parametrize("expected", [0, True, 4, 6])
def test_declared_row_count_cannot_be_filled_or_coerced(tmp_path, expected):
    path = _write(tmp_path / "exact.txt", GOLD)
    records = tc.read_trace(path, FIELDS)
    with pytest.raises(ValueError):
        tc.compare(records, records, FIELDS, expected_rows=expected)


def test_cli_records_exact_input_bytes_without_claiming_hdl_execution(tmp_path, capsys):
    import hashlib
    import json

    golden = _write(tmp_path / "gold.trace", GOLD)
    mutant = _write(tmp_path / "mutant.trace", [*GOLD[:-1], "4 changed 0 1"])
    output = tmp_path / "analysis"
    rc = tc.main(
        [
            "--golden",
            str(golden),
            "--mutant",
            str(mutant),
            "--fields",
            ",".join(FIELDS),
            "--expected-rows",
            "5",
            "--out",
            str(output),
            "--json",
        ]
    )
    assert rc == 0
    result = json.loads(capsys.readouterr().out)
    assert result["compared_rows"] == 5 and result["diff_rows"] == 1
    assert result["equivalence_proof"] is False and result["hdl_execution_verified"] is False
    assert result["input_sha256"]["golden"] == hashlib.sha256(golden.read_bytes()).hexdigest()
    assert (output / "golden.trace").read_bytes() == golden.read_bytes()
    assert (output / "mutant.trace").read_bytes() == mutant.read_bytes()
    original = (output / "trace_comparison.json").read_bytes()
    assert (
        tc.main(
            [
                "--golden",
                str(golden),
                "--mutant",
                str(mutant),
                "--fields",
                ",".join(FIELDS),
                "--expected-rows",
                "5",
                "--out",
                str(output),
            ]
        )
        == 2
    )
    assert (output / "trace_comparison.json").read_bytes() == original


def test_malformed_rows_are_rejected_even_when_both_lists_have_equal_length():
    incomplete = [tc.Row(1, ("0", "word", "0"))]
    with pytest.raises(ValueError, match="all declared"):
        tc.compare(incomplete, incomplete, FIELDS, expected_rows=1)
