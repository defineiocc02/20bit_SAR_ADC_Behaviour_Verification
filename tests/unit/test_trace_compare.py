"""``tools/trace_compare.py`` 的门禁：论文判据（审计文档 §4）**逐条**都要有会红的反例。

判据原文要点：以记录的时间字段为索引 → 按行序比较**公共行前缀** → 每个观测字段按
**字符串**比较 → **X/Z 计为已观测值** → **超出公共前缀的部分不构成可观测性**。

本文件对每一条都放"正例 + 反例"：只测正例等于没测（判据如果退化成"恒 killed"
或"恒 unobserved"，都能过一半的用例）。
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


def test_beyond_common_prefix_is_not_observable(tmp_path):
    """**公共前缀之外不算**：变异体少跑了几行不构成差异（但要标 truncated）。"""
    r = _cmp(tmp_path, GOLD, GOLD[:3])
    assert r["verdict"] == "unobserved"
    assert r["common_prefix"] == 3
    assert r["truncated"] is True


def test_extra_rows_in_mutant_are_not_observable(tmp_path):
    """变异体多出来的行同样不算差异（论文只比公共前缀）。"""
    r = _cmp(tmp_path, GOLD, [*GOLD, "5 0f 0 1", "6 10 0 1"])
    assert r["verdict"] == "unobserved"
    assert r["common_prefix"] == len(GOLD)
    assert r["truncated"] is False


def test_x_z_count_as_observed_values(tmp_path):
    """**X/Z 计为已观测值**：`x` 与 `0` 不同 ⇒ killed；`x` 与 `x` 相同 ⇒ 不算差异。

    这条是判据里最容易被实现成"解析成数值再比"的一处；一旦解析，`x` 会变成 NaN 或抛异常。
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


def test_empty_mutant_trace_is_unobserved_but_truncated(tmp_path):
    """变异体一行都没有（仿真胎死）：公共前缀为空 ⇒ 不构成差异，但必须标 truncated。

    这一条**故意不把 truncated 并进 verdict**：criterion ②（完整执行）是漏斗的另一条判据，
    把"没跑完"算成"等价"会得到虚低的变异分数。诊断字段就是留给 campaign 去丢弃它的。
    """
    r = _cmp(tmp_path, GOLD, [])
    assert r["verdict"] == "unobserved"
    assert r["common_prefix"] == 0
    assert r["truncated"] is True
    assert r["n_mutant"] == 0


def test_bad_column_count_is_an_error(tmp_path):
    """列数不等于 schema 时必须报错，不能静默按前缀切。"""
    g = _write(tmp_path / "g.txt", GOLD)
    m = _write(tmp_path / "m.txt", ["0 0a 0"])
    with pytest.raises(SystemExit):
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
