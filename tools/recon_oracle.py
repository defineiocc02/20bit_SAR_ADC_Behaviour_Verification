#!/usr/bin/env python3
"""重构流水连续输入测试的独立整数参考（oracle）。

设计说明
--------
* 为什么需要它：原 tb_i4_recon 对每组输入**保持相同总线 7 拍**再检查一次，
  这恰好让流水里的"实时总线读取"看起来正确，掩盖了取数点错误。
  本 oracle 配 tb_recon_stream.sv 用**连续受理**激励（相邻拍不同笔），
  并用独立整数模型逐笔、逐级比对 Z/N/I/X/code。

* 来自独立复核（isscc9p8_rework_decision_20261116）：
  修复前 10 笔中 8 笔码错；修复后（fe7535f）0 笔。

* py3.6 兼容：远端 Python 3.6 不支持 zip(strict=True)，已去掉。

用法:
    python3 recon_oracle.py generate <vec_out>
    python3 recon_oracle.py examine <trace_csv> <report_json>
退出码: 0 = MATCH，1 = MISMATCH
"""
from collections import Counter
import csv
import json
from pathlib import Path
import sys


Q30 = 1 << 30
CASE_NAMES = {
    1: "stable_one_pulse",
    2: "back_to_back",
    3: "bubbles_change_bus",
    4: "reciprocal_join_constant_n",
    5: "alpha_change_after_valid",
}
INPUT = ("scenario", "tick", "reset_n", "in_valid", "in_tag", "raw", "b", "alpha",
         "G", "Wm", "Wp")


# ---------------------------------------------------------------------------- 1. 独立的整数参考
def rounded(n, d):
    """与方案的恰半离零规则对应，分母取明确正整数。"""
    mag, rem = divmod(abs(n), d)
    result = mag + int(2 * rem >= d)
    return -result if n < 0 else result


def oracle(raw, offset, alpha, gain, post, pre):
    """返回按接受沿输入计算的 Z/N/I/X 和末级整数码。"""
    y = -3145728 + 1536 * raw + 768
    z = rounded((y - offset) * Q30, alpha)
    n = 64 * z + 6 * post - 3 * gain - 6 * pre
    i = rounded(1 << 60, gain)
    x = (n * i) // (1 << 36)
    ideal = (1 << 19) + rounded(x, 96)
    code = min((1 << 20) - 1, max(0, ideal))
    return dict(Z=z, N=n, I=i, X=x, code=code, sat=int(ideal != code), err=0)


# ---------------------------------------------------------------------------- 2. 只在受理沿定义事务
def word(gain, post, *, raw=2048, alpha=Q30):
    """所有场景落在拟议合法权重、增益与 alpha 范围。"""
    return (raw, 768, alpha, gain * Q30, post * Q30, 0)


def schedule():
    a, b, c, d = word(32, 16), word(16, 8), word(64, 33), word(32, 17)
    cases = {
        1: (a, [(101, a)], a),
        2: (a, [(201, a), (202, b), (203, c)], a),
        3: (a, [(301, a), (0, b), (0, c), (302, d)], a),
        # N 恒为 6·2**30，变化的 G 只影响倒数，单独检验汇合沿。
        4: (d, [(401, d), (402, word(16, 9)), (403, c)], d),
        # 只改变 S1a 接受后的 alpha。该用例不改变 G 或权重。
        5: (a, [(501, word(32, 16, raw=2050, alpha=Q30 // 2))], a),
    }
    rows = []
    for case, (quiet, core, pad) in cases.items():
        frame = [(0, 0, quiet)] * 3 + [(1, 0, quiet)] * 2
        frame += [(1, tag, bus) for tag, bus in core]
        frame += [(1, 0, pad)] * 12
        for tick, (reset_n, tag, bus) in enumerate(frame, 1):
            rows.append((case, tick, reset_n, int(tag != 0), tag, *bus))
    return rows


# ---------------------------------------------------------------------------- 3. 按标签审计接受到退休的完整窗口
def by_phase(rows, valid_name, tag_name, tag):
    return [r for r in rows if r[valid_name] == "1" and int(r[tag_name]) == tag]


def integer_subset(row, names):
    """有效事件的未知值直接作为验证错误，不补零。"""
    return {name: int(row[name]) for name in names}


def examine(trace, report):
    expected_rows = schedule()
    with trace.open(newline="") as source:
        actual_rows = list(csv.DictReader(source))
    if len(expected_rows) != len(actual_rows):
        raise ValueError(f"short or long capture: {len(actual_rows)}")
    observed_inputs = [tuple(int(r[f]) for f in INPUT) for r in actual_rows]
    if observed_inputs != expected_rows:
        raise ValueError("trace did not execute the declared input schedule")
    cases = {}
    for sid, name in CASE_NAMES.items():
        selected = [r for r in actual_rows if int(r["scenario"]) == sid]
        if any(r["out_valid"] not in ("0", "1") for r in selected):
            raise ValueError(f"unknown out_valid in {name}")
        outs = [r for r in selected if r["out_valid"] == "1"]
        expected = [x for x in expected_rows if x[0] == sid and x[3]]
        tags = [x[4] for x in expected]
        retired = [int(r["out_tag"]) for r in outs]
        transactions = []
        for _, accepted, _, _, tag, *bus in expected:
            ref = oracle(*bus)
            xb = by_phase(selected, "s1b_valid", "s1b_tag", tag)
            np = by_phase(selected, "s2a_valid", "s2a_tag", tag)
            jn = by_phase(selected, "join_valid", "join_tag", tag)
            st = by_phase(selected, "s3_valid", "s3_tag", tag)
            out = by_phase(selected, "out_valid", "out_tag", tag)
            if not all(len(records) == 1 for records in (xb, np, jn, st, out)):
                raise ValueError(f"missing or duplicate trace records for {name} tag {tag}")
            output = integer_subset(out[0], ("tick", "out_code", "out_sat", "out_err"))
            z = integer_subset(xb[0], ("tick", "alpha", "Z"))
            n = integer_subset(np[0], ("tick", "G", "Wm", "Wp", "N"))
            i = integer_subset(jn[0], ("tick", "join_I", "join_N", "ri_valid_pre",
                                       "ri_valid_post", "ri_I_pre", "ri_I_post"))
            x = integer_subset(st[0], ("tick", "X"))
            diffs = [f for f, val in zip(("Z", "N", "I", "X", "code", "sat", "err"),
                (z["Z"], n["N"], i["join_I"], x["X"], output["out_code"],
                 output["out_sat"], output["out_err"])) if val != ref[f]]
            if output["tick"] != accepted + 6:
                diffs.append("latency")
            transactions.append(dict(tag=tag, accept_tick=accepted, expected=ref,
                stage_z=z, stage_n=n, join=i, stage_x=x, output=output, mismatches=diffs))
        cases[name] = dict(accepted=tags, retired=retired, retire_count=Counter(retired),
            tags_in_order=(tags == retired), transactions=transactions)
    failed = [name for name, case in cases.items() if not case["tags_in_order"] or
              any(tx["mismatches"] for tx in case["transactions"])]
    report.write_text(json.dumps(dict(captured_rows=len(actual_rows), cases=cases,
                                      cases_with_mismatches=failed), indent=2) + "\n")
    for name, case in cases.items():
        first = next((t for t in case["transactions"] if t["mismatches"]), None)
        print(f"{name} accepted={case['accepted']} retired={case['retired']}"
              f" mismatches={sum(bool(t['mismatches']) for t in case['transactions'])}")
        if first:
            print(f"  tag={first['tag']} accept={first['accept_tick']} "
                  f"retire={first['output']['tick']} "
                  f"expected=0x{first['expected']['code']:05X} "
                  f"actual=0x{first['output']['out_code']:05X} "
                  f"fields={','.join(first['mismatches'])}")
    print("RESULT:", "RECON_STREAM_MISMATCH" if failed else "RECON_STREAM_MATCH")
    return int(bool(failed))


if __name__ == "__main__":
    if sys.argv[1] == "generate":
        Path(sys.argv[2]).write_text("".join(" ".join(map(str, row)) + "\n"
                                             for row in schedule()))
    else:
        sys.exit(examine(Path(sys.argv[2]), Path(sys.argv[3])))
