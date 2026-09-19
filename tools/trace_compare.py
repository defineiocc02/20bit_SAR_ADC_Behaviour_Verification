#!/usr/bin/env python3
"""论文判据比较器：两份观测 trace 的**字段级**可观测性判定（VeriBugBench §4 的转用）。

## 判据（逐条照抄审计文档 §4，不增不减）

1. 以记录的时间字段为索引 —— 本项目的 trace 里该字段是 ``sample_idx``；
2. 按行序比较**公共行前缀**（``common = min(行数)``）；
3. **每个观测字段按字符串比较**（不做数值解析，故 ``x``/``z`` 与数字同等对待）；
4. **X/Z 计为已观测值**（它们就是字符串 ``x``/``z``，不特殊处理 —— 这正是"计为观测"的落地）；
5. **超出公共前缀的部分不构成可观测性**（多出来的行、缺失的行都不算差异）。

任一行任一字段不同 ⇒ ``killed``；公共前缀内全同 ⇒ ``unobserved``。

## 转用声明（与审计文档 §0 同句搬运，不得删）

本比较器实现的是该论文"保留判据"里"输出可观测"这一步的**转用**版本；论文用它刻画
基准构造与产物质量，**而非**某个调试技术的性能。**不得**用它声称"我们的 TB 达到了该基准的某水平"。

## 与"完整执行"的关系（如实登记，别把它藏进 verdict 里）

论文漏斗还有前两条判据（① 编译通过、② 完整执行）。本比较器**只管第 ③④⑤ 条**。
若变异体在仿真中途死掉，它的 trace 会更短甚至为空 —— 那时公共前缀也会跟着变短，
``killed`` 可能因"差异落在公共前缀之外"而变成 ``unobserved``。**这不是"等价"**，
而是"没跑完"。故本比较器额外输出 ``truncated`` 与 ``n_golden``/``n_mutant`` 三个诊断字段，
**由调用方（campaign）据 criterion ② 决定丢弃**。把这三项藏起来会得到虚低的变异分数。

## 退出码

    0  比较完成，verdict = killed
    1  比较完成，verdict = unobserved
    2  用法 / IO 错误
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

DEFAULT_FIELDS = ("sample_idx", "dout", "clip", "analog_ovf")


@dataclass(frozen=True)
class Row:
    """trace 里的一行：``n`` 是文件行号（仅诊断用），``fields`` 是各观测列的**字符串**。"""

    n: int
    fields: tuple[str, ...]


def read_trace(path: Path, fields: tuple[str, ...]) -> list[Row]:
    """读一个 trace：跳过空行与 ``#`` 注释行；每行按空白切分后要求列数与 ``fields`` 一致。"""
    rows: list[Row] = []
    for n, raw in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        toks = tuple(line.split())
        if len(toks) != len(fields):
            raise SystemExit(f"{path}:{n} 列数 {len(toks)} != 期望 {len(fields)}（{line!r}）")
        rows.append(Row(n=n, fields=toks))
    return rows


def compare(golden: list[Row], mutant: list[Row], fields: tuple[str, ...]) -> dict:
    """按论文判据比较两份 trace，返回结构化结论（不改判据、不加判据）。"""
    common = min(len(golden), len(mutant))
    per_field = {f: 0 for f in fields}
    diff_rows = 0
    first: dict | None = None
    for i in range(common):
        g, m = golden[i], mutant[i]
        bad = [f for f, a, b in zip(fields, g.fields, m.fields, strict=False) if a != b]
        if bad:
            diff_rows += 1
            for f in bad:
                per_field[f] += 1
            if first is None:
                first = {
                    "row": i,
                    "fields": bad,
                    "golden": " ".join(g.fields),
                    "mutant": " ".join(m.fields),
                }
    return {
        "killed": diff_rows > 0,
        "verdict": "killed" if diff_rows > 0 else "unobserved",
        "n_golden": len(golden),
        "n_mutant": len(mutant),
        "common_prefix": common,
        # 诊断：变异体比金标短就意味着它没跑完（criterion ②），必须由 campaign 丢弃而不是当等价
        "truncated": len(mutant) < len(golden),
        "diff_rows": diff_rows,
        "per_field": per_field,
        "first_diff": first,
        "fields": list(fields),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--golden", required=True, help="干净设计的观测 trace")
    ap.add_argument("--mutant", required=True, help="变异体的观测 trace")
    ap.add_argument(
        "--fields",
        default=",".join(DEFAULT_FIELDS),
        help=f"列名（逗号分隔），默认 {','.join(DEFAULT_FIELDS)}",
    )
    ap.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    ap.add_argument("--quiet", action="store_true", help="只输出 verdict 一行")
    args = ap.parse_args(argv)

    fields = tuple(x.strip() for x in args.fields.split(",") if x.strip())
    try:
        g = read_trace(Path(args.golden), fields)
        m = read_trace(Path(args.mutant), fields)
    except OSError as exc:
        print(f"trace_compare: {exc}", file=sys.stderr)
        return 2

    res = compare(g, m, fields)
    if args.json:
        print(json.dumps(res, indent=2, ensure_ascii=False))
    elif args.quiet:
        print(res["verdict"])
    else:
        print(f"golden = {args.golden}  ({res['n_golden']} 行)")
        print(f"mutant = {args.mutant}  ({res['n_mutant']} 行)")
        print(f"公共前缀 = {res['common_prefix']} 行；truncated = {res['truncated']}")
        print(f"差异行数 = {res['diff_rows']}；逐字段计数 = {res['per_field']}")
        if res["first_diff"]:
            fd = res["first_diff"]
            print(f"首处差异 @ 行 {fd['row']}，字段 {fd['fields']}")
            print(f"  golden: {fd['golden']}")
            print(f"  mutant: {fd['mutant']}")
        print(f"VERDICT: {res['verdict']}")
    return 0 if res["killed"] else 1


if __name__ == "__main__":
    sys.exit(main())
