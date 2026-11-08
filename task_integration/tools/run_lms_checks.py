#!/usr/bin/env python3
# =============================================================================
# 文件名     : run_lms_checks.py
# 模块名     : run_lms_checks
# 版本       : v1.0
# 功能       : 核对片上重构整体顶层与真实失配模型导出的有符号定点结果。
#
# 设计说明   :
#   * 承担 集成顶层向量与对拍入口 中的一环；输入输出的物理口径与单位以同目录 SPEC/README 为准。
# -----------------------------------------------------------------------------
# 变更记录
# -----------------------------------------------------------------------------
# 初始版本，暂无变更记录。
# ===========================================================================

"""核对片上重构整体顶层与真实失配模型导出的有符号定点结果。"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True

from tools.run_open_rtl import (  # noqa: E402
    alias_runtime,
    checked_run,
    command_for,
    completion,
    digest,
    new_output,
    verilator_compiler_args,
    write_json,
)

from adi_model.capacitors import Capacitors, ExperimentConfig  # noqa: E402
from task1.experiments.adctoolbox_calibration import CalibrationConfig  # noqa: E402
from task1.experiments.residue_control_study import MapperTable  # noqa: E402
from task1.tools.run_rtl_checks import STRICT_LINT  # noqa: E402
from task2.eight.experiment import _capture  # noqa: E402
from task2.eight.model import ChargePool  # noqa: E402
from task_integration.tools.run_checks import RTL  # noqa: E402
from task_reconstruction.fixed import from_training, reconstruct  # noqa: E402

TOP = "sar20_digital_lms_top"
BENCH = "sar20_lms_integration_tb"
LMS_RTL = (
    *RTL,
    "task_reconstruction/rtl/sar20_lms_recon.sv",
    f"task_integration/rtl/{TOP}.sv",
)
SOURCES = (*LMS_RTL, f"task_integration/tb/{BENCH}.sv")
FIXTURES = tuple(Path(f"task2/evidence/eight_negative/sigma_{pct}pct") for pct in (2, 3))
FIELDS = (
    "cycles",
    "checks",
    "adc2_captures",
    "bypass",
    "pairs",
    "peak_inflight",
    "signed_negative_pairs",
    "mixed_dither_pairs",
    "rejects",
    "macro_faults",
)


# =========================================================================
# 1. 函数组：pack / slots / make_vectors
# =========================================================================
# -------------------------------------------------------------------------
# 按 bench 结构体的最高位起依次打包。
# 物理 slot 0 位于最低位。
# 旁路重放存档；CAAZ 组用同一物理池模拟输入换相，附已知共模偏移。
# ------------------------------------------------------------------------
# pack(fields) -> str：含迭代/循环；本模块 的口径以同目录 SPEC/README 为准，越界与失效路径须显式处理
# ------------------------------------------------------------------------
def pack(fields: tuple[tuple[int, int], ...]) -> str:
    """按 bench 结构体的最高位起依次打包。"""
    value = width = 0
    for field, bits in fields:
        value = (value << bits) | (int(field) & ((1 << bits) - 1))
        width += bits
    return f"{value:0{(width + 3) // 4}x}\n"


# ------------------------------------------------------------------------
# slots(values, width) -> int：numpy 向量化；本模块 的口径以同目录 SPEC/README 为准，越界与失效路径须显式处理
# ------------------------------------------------------------------------
def slots(values: np.ndarray, width: int) -> int:
    """物理 slot 0 位于最低位。"""
    return sum(int(value) << (slot * width) for slot, value in enumerate(values))


# ------------------------------------------------------------------------
# make_vectors(folder, out) -> tuple[Path, Path, dict]：含显式异常拒绝、含迭代/循环、numpy 向量化、产出归档/图件；本模块 的口径以同目录 SPEC/README 为准，越界与失效路径须显式处理
# ------------------------------------------------------------------------
def make_vectors(folder: Path, out: Path, *, chopped: bool) -> tuple[Path, Path, dict]:
    """旁路重放存档；CAAZ 组用同一物理池模拟输入换相，附已知共模偏移。"""
    meta = json.loads((ROOT / folder / "summary.json").read_text())
    data_path = ROOT / folder / "data.npz"
    coeff = from_training(data_path)
    tag = "pair" if chopped else "bypass"
    config_file, vectors_file = out / f"{tag}_coeff.hex", out / f"{tag}_data.hex"
    config_file.write_text("".join(f"{word:07x}\n" for word in coeff.words()), encoding="ascii")
    prefix = "fig21_dem_on_tone_"
    with np.load(data_path) as data:
        plan = data["fig21_dem_tone_plan"]
        word = data[prefix + "word"]
        if chopped:
            experiment = ExperimentConfig(**meta["experiment"])
            calibration = CalibrationConfig(**meta["calibration"])
            physical = ChargePool.from_caps(
                Capacitors(
                    data["actual_main_cap_f"], data["actual_sub_cap_f"], data["actual_bridge_cap_f"]
                ),
                experiment,
            )
            phase = (data["fig21_dem_tone_horizontal"], data["fig21_dem_tone_vertical"])
            # 共享的 80 uV 偏移仅作为同笔数字路径激励；真实 AZ 噪声独立验收。
            polarity = 1 - 2 * (np.arange(len(plan)) % 2)
            voltage = polarity * data["fig21_dem_tone_reference_v"] + 80e-6
            state, coarse, _, adc2 = _capture(
                voltage,
                word,
                plan,
                phase,
                data["nominal_threshold_v"],
                meta["native_dither_step_v"],
                MapperTable.build(bridge=True),
                physical,
                calibration,
            )
            command = state.switches.codes
            main, sub, sampled = (
                state.switches.main,
                state.switches.sub,
                state.dither.sample_sub_high,
            )
        else:
            coarse, command = data[prefix + "coarse"], data[prefix + "command"]
            main, sub, sampled = (
                data[prefix + "main"],
                data[prefix + "sub"],
                data[prefix + "sample_sub"],
            )
            adc2 = data[prefix + "adc2_code"]
    with vectors_file.open("w", encoding="ascii") as stream:
        for idx, (ids, m, c, s, fine, dither) in enumerate(
            zip(plan, main, sub, sampled, adc2, word, strict=True)
        ):
            signed_code, bad = reconstruct(
                coeff,
                tuple(map(int, ids)),
                tuple(map(int, m)),
                tuple(map(int, c)),
                tuple(map(int, s)),
                int(fine),
            )
            if bad:
                raise RuntimeError(f"fixture unexpectedly rejects {tag} sample {idx}")
            stream.write(
                pack(
                    (
                        (dither, 6),
                        (slots(ids, 5), 40),
                        (coarse[idx], 9),
                        (command[idx], 9),
                        (slots(m, 17), 136),
                        (slots(c, 5), 40),
                        (slots(s, 5), 40),
                        (fine, 12),
                        (signed_code, 20),
                    )
                )
            )
    return (
        config_file,
        vectors_file,
        {
            "samples": len(plan),
            "chopped": chopped,
            "common_offset_v": 80e-6 if chopped else 0,
            "coefficient_words": len(coeff.words()),
            "input_data_sha256": digest(data_path),
            "config_sha256": digest(config_file),
            "vectors_sha256": digest(vectors_file),
        },
    )


# =========================================================================
# 2. 函数组：main
# =========================================================================
# -------------------------------------------------------------------------
# 生成失配重放向量，验收同一个片上重构 RTL 设计包。
# ------------------------------------------------------------------------
# main() -> None：含显式异常拒绝、含断言；本模块 的口径以同目录 SPEC/README 为准，越界与失效路径须显式处理
# ------------------------------------------------------------------------
def main() -> None:
    """生成失配重放向量，验收同一个片上重构 RTL 设计包。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    out = new_output(args.out)
    bypass_cfg, bypass_vectors, bypass_report = make_vectors(FIXTURES[0], out, chopped=False)
    pair_cfg, pair_vectors, pair_report = make_vectors(FIXTURES[1], out, chopped=True)
    command = command_for("verilator")
    env = dict(os.environ)
    version = checked_run(
        [*command, "-V"], cwd=ROOT, env=env, log=out / "verilator.log", timeout=30
    )
    with tempfile.TemporaryDirectory(prefix="sar20_lms_top_") as work_dir:
        work = Path(work_dir)
        alias_runtime(work, env, version)
        checked_run(
            [*command, "--lint-only", "--top-module", TOP, *STRICT_LINT, *LMS_RTL],
            cwd=ROOT,
            env=env,
            log=out / "lint.log",
            timeout=60,
        )
        checked_run(
            [
                *command,
                "--binary",
                "--timing",
                "--assert",
                "-j",
                "2",
                "--top-module",
                BENCH,
                "--Mdir",
                str(work / "obj"),
                *verilator_compiler_args(),
                *STRICT_LINT,
                *SOURCES,
            ],
            cwd=ROOT,
            env=env,
            log=out / "compile.log",
            timeout=480,
        )
        run = checked_run(
            [
                str(work / "obj" / f"V{BENCH}"),
                f"+bypass_coeff={bypass_cfg}",
                f"+bypass_data={bypass_vectors}",
                f"+pair_coeff={pair_cfg}",
                f"+pair_data={pair_vectors}",
            ],
            cwd=work,
            env=env,
            log=out / "run.log",
            timeout=60,
        )
    counts = completion(run, "SAR20_LMS_INTEGRATION_COMPLETE", FIELDS)
    required = {"adc2_captures": 8192, "bypass": 4096, "pairs": 2048, "peak_inflight": 2}
    if any(counts[key] != expected for key, expected in required.items()):
        raise RuntimeError(f"unexpected pipeline counts: {counts}")
    inputs = (
        *SOURCES,
        "tools/run_open_rtl.py",
        "task_integration/tools/run_checks.py",
        "task_integration/tools/run_lms_checks.py",
        "task_reconstruction/fixed.py",
        "task2/eight/model.py",
        "task2/eight/experiment.py",
        "task_integration/SPEC.md",
        "task_integration/LMS_PIPELINE.md",
    )
    write_json(
        out / "result.json",
        {
            "scope": "digital_top_on_chip_frozen_lms_inference_and_caaz",
            "status": "PASS",
            "counts": counts,
            "bypass_2pct_fixture": bypass_report,
            "paired_3pct_fixture": pair_report,
            "source_sha256": {name: digest(ROOT / name) for name in inputs},
            "log_sha256": {
                name: digest(out / name)
                for name in ("verilator.log", "lint.log", "compile.log", "run.log")
            },
        },
    )
    print(run.splitlines()[0])


if __name__ == "__main__":
    main()
