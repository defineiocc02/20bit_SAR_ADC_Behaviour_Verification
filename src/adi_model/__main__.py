"""将配置检查和实际默认模拟接到 ``python -m adi_model``。"""

from __future__ import annotations

from .cli import _ConfigParser, main_simulate, main_validate_config


def main(argv: list[str] | None = None) -> int:
    """分发独立命令；配置检查不隐式运行模拟。"""
    parser = _ConfigParser(
        prog="python -m adi_model", description="SAR ADC配置检查与可审阅默认模拟。"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("validate-config", help="只检查显式JSON配置", add_help=False)
    sub.add_parser("simulate", help="实际运行工程默认或明确覆盖", add_help=False)
    args, rest = parser.parse_known_args(argv)
    if args.command == "simulate":
        return main_simulate(rest)
    return main_validate_config(rest)


if __name__ == "__main__":
    raise SystemExit(main())
