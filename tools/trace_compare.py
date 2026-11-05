#!/usr/bin/env python3
"""按字段字符串比较两份观测轨迹的公共行前缀。

本比较器转用 VeriBugBench 的输出可观测保留判据，具体约定见审计文档 §4。
按记录行序取 common=min(两份行数)，逐字段比较字符串；sample_idx 时间字段
本身也参与比较，不另行对齐行。x/z 是已观测字符串：x 与 0 不同，x 与 x 相同。
公共前缀内任一字段不同即 killed，全同即 unobserved；超出公共前缀的行不计差异。

原论文用相关测度描述基准构造及产物质量，不是调试技术性能；这里的转用结果
不能解释为测试台达到该基准的某种水平。本函数也不检查编译与完整执行，因而
unobserved 不是等价证明。truncated、n_golden 和 n_mutant 提供截断诊断，调用方
须根据完整执行判据处理，不得将未完成执行计为等价。

分类依赖比较器之外的证据。结构性等价需要预先说明理由并保留可复核的有界
证明；观测面不足则应通过更宽观测面或不同采样点检验。原审计保留了三类案例：
激励未进入差异分支、差异端口未列入轨迹，以及振荡差异在离散采样点偶然重合。

历史配对记录中，calib_regs.sv:107 的 < 改为 <= 在顶层未观测，在模块级有
41 行 cb_ready/cb_err/cb_min/cb_max 差异；calib_regs.sv:109 的 err_code 取反
改变 status_word（0x00000020 变为 0xffffffe0，err_code 为 67108863），但旧
四列轨迹没有该字段。status_regs.sv:82 的自保持表达式取反会逐拍翻转，末次
采样可能回到 0x00000020；这类情况应区分差异存在与采样恰好重合。
这些位置和计数是历史诊断记录，不是对当前源码重新执行的结果。

删除 weight_store 容量守卫只是冻结尺寸下的等价候选，仍需有界证明。
2026-09-19 的更正也保留：~offset_q=-offset_q-1，不是结构性等价；已有合法
反例为 524287→524288 和 524288→524289，见 test_rtl_review_regressions.py。
旧向量的 unobserved 仅表示当时激励未观测到差异。原文所用更宽观测面为
p2_periph_tb 的 27 列；选择观测面及运行额外实验均由调用方负责。

退出码为 0（killed）、1（unobserved）或 2（用法／I/O 错误）。
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
    """记录一行轨迹及其源文件行号。

    Attributes:
        n: 从 1 开始的源文件行号，仅用于诊断。
        fields: 各观测列的原始字符串，不作数值解析。
    """

    n: int
    fields: tuple[str, ...]


def read_trace(path: Path, fields: tuple[str, ...]) -> list[Row]:
    """读取轨迹，跳过空行及 # 注释行，按空白拆分观测字段。

    Args:
        path: UTF-8 轨迹路径。
        fields: 期望列名及列数，顺序与输入一致。

    Returns:
        保留文件行号和字符串字段的记录列表。

    Raises:
        SystemExit: 任一数据行列数不符。
    """
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
    """比较公共行前缀，分别返回可观测差异与截断诊断。

    只比较字符串，不进行数值转换或时间对齐。返回差异行数、逐字段计数及首处
    差异；轨迹长度不足单独记录，不并入 killed 判定。
    """
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
        # 截断诊断交由调用方按完整执行判据处理，不能直接解释为等价。
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
