"""将 adi_model.cli 的两个命令接到 python -m adi_model。

子命令的参数解析由 cli 模块负责；此入口只分发 run-all 与 make-report，
使模块启动方式与对应的 console script 使用相同实现。
"""

from __future__ import annotations

import argparse

from .cli import main_make_report, main_run_all


def main(argv: list[str] | None = None) -> int:
    """分发 python -m adi_model <command>。

    Args:
        argv: 参数列表；None 使用 sys.argv[1:]。

    Returns:
        进程退出码。
    """
    parser = argparse.ArgumentParser(
        prog="python -m adi_model",
        description="Behavioural model of a two-stage residual SAR ADC.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    # Command options, including --help, belong to the existing CLI parsers.
    # Parsing only the dispatch command keeps both invocation forms aligned.
    sub.add_parser("run-all", help="run every acceptance stage", add_help=False)
    sub.add_parser("make-report", help="build the HTML report from results.json", add_help=False)
    args, rest = parser.parse_known_args(argv)
    if args.command == "run-all":
        return main_run_all(rest)
    return main_make_report(rest)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
