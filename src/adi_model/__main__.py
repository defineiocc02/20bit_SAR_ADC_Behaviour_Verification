"""Wire the :mod:`adi_model.cli` entry points to ``python -m adi_model``.

Kept deliberately tiny: the sub-commands live in :mod:`adi_model.cli`, and the
only job here is to expose the same two commands through the module runner so
that ``python -m adi_model run-all`` and ``adi-run-all`` cannot diverge.
"""

from __future__ import annotations

import argparse
import sys

from .cli import main_make_report, main_run_all


def main(argv: list[str] | None = None) -> int:
    """Dispatch ``python -m adi_model <command>``.

    Args:
        argv: Argument list; defaults to ``sys.argv[1:]``.

    Returns:
        Process exit code.
    """
    parser = argparse.ArgumentParser(
        prog="python -m adi_model",
        description="Behavioural model of a two-stage residual SAR ADC.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("run-all", help="run every acceptance stage")
    sub.add_parser("make-report", help="build the HTML report from results.json")
    args = parser.parse_args(argv)
    rest = sys.argv[2:] if argv is None else []
    if args.command == "run-all":
        return main_run_all(rest)
    return main_make_report(rest)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
