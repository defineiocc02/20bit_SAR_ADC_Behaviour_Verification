# =============================================================================
# 文件名     : snapshot_baseline.py
# 模块名     : tools.snapshot_baseline
# 版本       : v1.0
# 功能       : 生成/校验行为级模型的数值基线指纹，把「模型没被我改坏」变成可执行断言
#
# 设计说明   :
#   * 指纹只存**指标**，不存配置。理由：M2 起要给 ExperimentConfig 加 noise 字段，
#     存 asdict(experiment) 会让指纹因为「多了一个默认关闭的字段」而误报，
#     那种红是噪音，会训练人忽略红灯。
#   * 也不存 source_sha256：本轮每个里程碑都要改模型源码，存哈希等于让指纹必红。
#     指纹要守的是**数值**，代码改动是否合法由测试与台账各自负责。
#   * 八项指标覆盖四个层面，缺一层都会漏掉一类回归：
#       1-2 单位/标度（native_dither_step_v、output_lsb20_v）
#       3-4 动态校准后（fig21_dem 的 sndr/sfdr）
#       5   DEM 差异（no_dem 的 sndr，与 3 配对看 DEM 是否还在起作用）
#       6-8 静态（未校准 INL 地板、校准后 INL、校准后 DNL 峰值）
#   * 默认 strict 精确比较。跨平台 CI 显式用 portable：只有频谱和线性度标量
#     允许绝对 1e-9 包络；电压标度、版本、种子和整数对拍仍精确比较。
#   * 指纹是**环境相关**的。归档 summary.json 记录的环境是
#     python 3.12.14 / numpy 2.5.3 / adctoolbox 0.9.1，本脚本默认在该环境取值。
#     换 numpy 大版本可能改变 FFT 末位，此时--check 会红，先确认是版本漂移
#     而不是模型改动，再决定是否重生成。
#   * 不写 data.npz 到仓库：run() 的产物落在临时目录，取完指标即删。
# -----------------------------------------------------------------------------
# 变更记录
# -----------------------------------------------------------------------------
# 初始版本。
# =============================================================================

"""生成或校验行为级模型的数值基线指纹。"""

from __future__ import annotations

import argparse
import json
import math
import platform
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

FINGERPRINT = ROOT / "task2" / "evidence" / "baseline_fingerprint" / "fingerprint.json"
SCHEMA = "adi_behavior_baseline_fingerprint_v1"

# 固定的实验参数。改这两个数就是换基线，必须重生成指纹并在提交信息里说明。
SIGMA = 0.02
SEED = 100

# 指标名 -> summary.json 里的点分路径。顺序即报告顺序。
METRICS: tuple[tuple[str, str], ...] = (
    ("native_dither_step_v", "native_dither_step_v"),
    ("output_lsb20_v", "output_lsb20_v"),
    ("fig21_dem_sndr_lms_db", "groups.fig21_dem.tone.on.lms.sndr_db"),
    ("fig21_dem_sfdr_lms_dbc", "groups.fig21_dem.tone.on.lms.sfdr_dbc"),
    ("no_dem_sndr_lms_db", "groups.no_dem.tone.on.lms.sndr_db"),
    (
        "fig21_dem_inl_off_nominal_lsb20",
        "groups.fig21_dem.linearity.off.nominal.endpoint_inl_peak_lsb20",
    ),
    ("fig21_dem_inl_on_lms_lsb20", "groups.fig21_dem.linearity.on.lms.endpoint_inl_peak_lsb20"),
    ("fig21_dem_dnl_on_lms_lsb20", "groups.fig21_dem.linearity.on.lms.dnl_max_lsb20"),
)

PINNED_COMMAND = (
    "uv run --python 3.12 --with numpy==2.5.3 --with matplotlib "
    "--with adctoolbox==0.9.1 python tools/snapshot_baseline.py"
)


def dig(summary: dict, dotted: str) -> float:
    """按点分路径取一个标量，取不到就报错而不是返回 None。"""
    node: object = summary
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            raise KeyError(f"summary 里没有 {dotted}")
        node = node[part]
    value = float(node)
    if value != value or value in (float("inf"), float("-inf")):
        raise ValueError(f"{dotted} 不是有限数: {value!r}")
    return value


def measure() -> dict:
    """跑一次实验并抽出八项指标，返回含环境信息的完整指纹。"""
    from task2.eight.experiment import run

    workdir = Path(tempfile.mkdtemp(prefix="baseline_fp_"))
    try:
        summary = run(workdir / "out", SIGMA, SEED)
        metrics = {name: dig(summary, path) for name, path in METRICS}
        return {
            "schema": SCHEMA,
            "params": {"sigma": SIGMA, "seed": SEED},
            "environment": {
                "python": platform.python_version(),
                "numpy": summary["dependencies"]["numpy"],
                "adctoolbox": summary["dependencies"]["adctoolbox"],
            },
            "metrics": metrics,
        }
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def compare(expected: dict, actual: dict, *, portable: bool = False) -> list[str]:
    """比较冻结标量；默认逐位严格，显式 portable 才允许浮点尾数包络。

    Args:
        expected: 冻结的环境、种子和八项标量，不在比较时更新。
        actual: 本次运行的同结构标量。
        portable: 对频谱允许绝对 1e-9 dB、线性度允许绝对 1e-9 LSB20。
            单位步长、输出 LSB、软件版本及实验参数仍精确比较。
    Returns:
        所有失败项目；NaN、Inf 与超出包络的变化均不能通过。

    包络是跨平台数值回归的工程选择，覆盖已观察的 Linux／macOS 尾数差，
    不是 ADC 性能容限。该函数不参与任何输出码、RTL 或物理验收比较。
    """
    problems: list[str] = []
    env_exp = expected.get("environment", {})
    env_act = actual.get("environment", {})
    if env_exp != env_act:
        problems.append(
            f"环境不同: 指纹 {env_exp} / 当前 {env_act}；"
            "先用指纹记录的环境重跑（见 PINNED_COMMAND），确认不是版本漂移"
        )
    if expected.get("params") != actual.get("params"):
        problems.append(
            f"实验参数不同: 指纹 {expected.get('params')} / 当前 {actual.get('params')}"
        )

    exp_metrics = expected.get("metrics", {})
    act_metrics = actual.get("metrics", {})
    if set(exp_metrics) != set(act_metrics):
        problems.append(
            f"指标集合不同: 缺 {set(exp_metrics) - set(act_metrics)}，多 {set(act_metrics) - set(exp_metrics)}"
        )
    for name, _ in METRICS:
        if name not in exp_metrics or name not in act_metrics:
            continue
        want, got = exp_metrics[name], act_metrics[name]
        tolerance = (
            1e-9 if portable and name not in {"native_dither_step_v", "output_lsb20_v"} else 0.0
        )
        if not math.isfinite(want) or not math.isfinite(got) or abs(got - want) > tolerance:
            problems.append(f"{name}: 指纹 {want!r} -> 当前 {got!r}（差 {got - want:+.6e}）")
    return problems


def main() -> int:
    """按 --check 决定是生成指纹还是校验指纹。"""
    parser = argparse.ArgumentParser(description="行为模型数值基线指纹")
    parser.add_argument(
        "--check",
        action="store_true",
        help="重算并与已存指纹逐字段比较，不一致则非零退出",
    )
    parser.add_argument(
        "--show",
        action="store_true",
        help="把测得的八项指标打到标准输出，便于人工比对",
    )
    parser.add_argument(
        "--portable",
        action="store_true",
        help="跨平台浮点标量比较；频谱／线性度绝对容限 1e-9，须配合 --check",
    )
    args = parser.parse_args()
    if args.portable and not args.check:
        parser.error("--portable requires --check; frozen data are never rewritten in this mode")

    actual = measure()
    if args.show or not args.check:
        for name, _ in METRICS:
            print(f"  {name:38s} {actual['metrics'][name]!r}")
        print(f"  环境: {actual['environment']}")

    if not args.check:
        FINGERPRINT.parent.mkdir(parents=True, exist_ok=True)
        FINGERPRINT.write_text(
            json.dumps(actual, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"已写入 {FINGERPRINT.relative_to(ROOT)}")
        print(f"复现命令: {PINNED_COMMAND}")
        return 0

    if not FINGERPRINT.is_file():
        print(f"错误: 指纹不存在 {FINGERPRINT}，先不带 --check 跑一次", file=sys.stderr)
        return 2
    expected = json.loads(FINGERPRINT.read_text(encoding="utf-8"))
    problems = compare(expected, actual, portable=args.portable)
    if problems:
        print(f"基线指纹不一致（{len(problems)} 项）：", file=sys.stderr)
        for item in problems:
            print(f"  {item}", file=sys.stderr)
        return 1
    print(f"基线指纹通过（{len(METRICS)} 项；模式：{'portable' if args.portable else 'strict'}）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
