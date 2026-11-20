# =============================================================================
# 文件名     : __main__.py
# 功能       : 模块调用与安装命令进入同一套验证、模拟函数。
# 设计说明   : 生产参数、组件 Config 在子命令内区分，模块分派不产生隐式采样。
# =============================================================================
"""将配置检查和实际默认模拟接到 ``python -m adi_model``。"""

from __future__ import annotations

from adi_model.cli import _ConfigParser, main_simulate, main_validate_config


def main(argv: list[str] | None = None) -> int:
    """分发独立命令；配置检查不隐式运行模拟。"""
    parser = _ConfigParser(
        prog="python -m adi_model", description="SAR ADC 配置检查与生产数字 IP 事务验证。"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("validate-config", help="检查生产参数或显式的组件 Config", add_help=False)
    sub.add_parser("simulate", help="运行 adc_digital_ip_v21 的 Python 事务参考", add_help=False)
    args, rest = parser.parse_known_args(argv)
    # 模块形式与安装的控制台入口使用同一函数，防止参数验证或默认算法发生分叉。
    if args.command == "simulate":
        return main_simulate(rest)
    return main_validate_config(rest)


if __name__ == "__main__":
    raise SystemExit(main())
