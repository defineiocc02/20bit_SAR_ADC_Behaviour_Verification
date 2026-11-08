#!/usr/bin/env python3
# =============================================================================
# 文件名     : run_joint_noise_rtl.py
# 模块名     : run_joint_noise_rtl
# 版本       : v1.0
# 功能       : 用四组带噪 ADC2 归档对拍整体数字顶层与外部对照。
#
# 设计说明   :
#   * 承担 集成顶层向量与对拍入口 中的一环；输入输出的物理口径与单位以同目录 SPEC/README 为准。
# -----------------------------------------------------------------------------
# 变更记录
# -----------------------------------------------------------------------------
# 初始版本，暂无变更记录。
# ===========================================================================

"""用四组带噪 ADC2 归档对拍整体数字顶层与外部对照。"""

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

from task1.tools.run_rtl_checks import STRICT_LINT  # noqa: E402
from task_integration.tools.run_lms_checks import LMS_RTL, TOP, pack, slots  # noqa: E402
from task_reconstruction.fixed import from_training  # noqa: E402

ARCHIVE = Path("task3/evidence/negative/joint_noise")
DATA = ARCHIVE / "caaz_joint_noise_data.npz"
META = ARCHIVE / "summary.json"
TRAINING = Path("task2/evidence/eight_negative/sigma_3pct/data.npz")
BENCH = "sar20_joint_noise_tb"
SOURCES = (*LMS_RTL, f"task_integration/tb/{BENCH}.sv")
MARKER = "SAR20_JOINT_NOISE_COMPLETE"
COUNTS = ("cycles", "checks", "adc2_captures", "reconstructed", "outputs", "peak_inflight")
CASE_MODES = {
    "az_independent": "external_average",
    "chop_independent": "external_chop_and_difference",
    "shared_ideal": "rtl_caaz",
    "shared_hold_error": "rtl_caaz",
}
RAW_COUNT = 4096
PAIR_COUNT = RAW_COUNT // 2


# =========================================================================
# 1. 函数组：write_fixture / read_trace / compare_observations
# =========================================================================
# -------------------------------------------------------------------------
# 重建摘要中已冻结的向量，不重新生成模拟噪声。
# 检查记录类型、字段宽度和完整性。
# 用真正握手的 ADC2、重构码和对外输出逐笔核对独立归档。
# ------------------------------------------------------------------------
# write_fixture(archive, case, path) -> None：含显式异常拒绝、含迭代/循环、numpy 向量化、产出归档/图件；本模块 的口径以同目录 SPEC/README 为准，越界与失效路径须显式处理
# ------------------------------------------------------------------------
def write_fixture(archive: np.lib.npyio.NpzFile, case: str, path: Path) -> None:
    """重建摘要中已冻结的向量，不重新生成模拟噪声。"""
    fields = (
        archive["dither_word"],
        archive["sample_plan"],
        archive[f"{case}_coarse"],
        archive[f"{case}_command"],
        archive[f"{case}_conv_main"],
        archive[f"{case}_conv_sub"],
        archive[f"{case}_sample_sub_high"],
        archive[f"{case}_adc2_code"],
        archive[f"{case}_raw_q28_lms_signed_code"],
    )
    if any(len(field) != RAW_COUNT for field in fields):
        raise ValueError(f"incomplete fixture: {case}")
    with path.open("w", encoding="ascii", newline="\n") as stream:
        for word, ids, coarse, command, main, sub, sampled, adc2, raw in zip(*fields, strict=True):
            stream.write(
                pack(
                    (
                        (word, 6),
                        (slots(ids, 5), 40),
                        (coarse, 9),
                        (command, 9),
                        (slots(main, 17), 136),
                        (slots(sub, 5), 40),
                        (slots(sampled, 5), 40),
                        (adc2, 12),
                        (raw, 20),
                    )
                )
            )


# ------------------------------------------------------------------------
# read_trace(path, case, rtl_pairs) -> dict[str, np.ndarray]：含显式异常拒绝、含迭代/循环、numpy 向量化；本模块 的口径以同目录 SPEC/README 为准，越界与失效路径须显式处理
# ------------------------------------------------------------------------
def read_trace(path: Path, case: str, rtl_pairs: bool) -> dict[str, np.ndarray]:
    """检查记录类型、字段宽度和完整性。"""
    widths = {"A": 3, "R": 4, "O": 7}
    rows: dict[str, list[list[int]]] = {name: [] for name in widths}
    for line_no, line in enumerate(path.read_text(encoding="ascii").splitlines(), 1):
        name, *fields = line.split()
        if name not in widths or len(fields) != widths[name]:
            raise ValueError(f"invalid observation at {case}:{line_no}")
        rows[name].append([int(field) for field in fields])
    required = {"A": RAW_COUNT, "R": RAW_COUNT, "O": PAIR_COUNT if rtl_pairs else RAW_COUNT}
    for tag, count in required.items():
        if len(rows[tag]) != count:
            raise ValueError(f"{case}: observed {len(rows[tag])} {tag} records, expected {count}")
    return {tag: np.asarray(data, dtype=np.int64) for tag, data in rows.items()}


# ------------------------------------------------------------------------
# compare_observations(archive, case, mode, trace) -> dict[str, np.ndarray]：numpy 向量化、含断言；本模块 的口径以同目录 SPEC/README 为准，越界与失效路径须显式处理
# ------------------------------------------------------------------------
def compare_observations(
    archive: np.lib.npyio.NpzFile,
    case: str,
    mode: str,
    trace: dict[str, np.ndarray],
) -> dict[str, np.ndarray]:
    """用真正握手的 ADC2、重构码和对外输出逐笔核对独立归档。"""
    acquired, raw, output = trace["A"], trace["R"], trace["O"]
    ids = np.arange(RAW_COUNT, dtype=np.int64)
    np.testing.assert_array_equal(acquired[:, 1], ids, err_msg=f"{case}: ADC2 IDs")
    np.testing.assert_array_equal(acquired[:, 2], archive[f"{case}_adc2_code"], err_msg=case)
    np.testing.assert_array_equal(np.diff(acquired[:, 0]), 16, err_msg=f"{case}: ADC2 cadence")
    np.testing.assert_array_equal(raw[:, 0] - acquired[:, 0], 23, err_msg=f"{case}: raw latency")
    np.testing.assert_array_equal(raw[:, 1], ids, err_msg=f"{case}: raw sample IDs")
    np.testing.assert_array_equal(
        raw[:, 2], archive[f"{case}_raw_q28_lms_signed_code"], err_msg=case
    )
    np.testing.assert_array_equal(raw[:, 3], 0, err_msg=f"{case}: raw bad status")
    if mode == "rtl_caaz":
        np.testing.assert_array_equal(output[:, 0], raw[1::2, 0], err_msg=f"{case}: pair clock")
        np.testing.assert_array_equal(output[:, 1], ids[1::2], err_msg=f"{case}: pair last ID")
        np.testing.assert_array_equal(output[:, 2], ids[::2], err_msg=f"{case}: pair first ID")
        np.testing.assert_array_equal(output[:, 3], ids[::2] // 2, err_msg=f"{case}: AZ generation")
        np.testing.assert_array_equal(output[:, 4], 1, err_msg=f"{case}: pair validity")
        pair_code, pair_difference = output[:, 5], output[:, 6]
    else:
        np.testing.assert_array_equal(output[:, 0], raw[:, 0], err_msg=f"{case}: bypass clock")
        np.testing.assert_array_equal(output[:, 1], ids, err_msg=f"{case}: bypass last ID")
        np.testing.assert_array_equal(output[:, 2], ids, err_msg=f"{case}: bypass first ID")
        np.testing.assert_array_equal(
            output[:, 3:5], 0, err_msg=f"{case}: bypass AZ and pair flags"
        )
        np.testing.assert_array_equal(output[:, 5], raw[:, 2], err_msg=f"{case}: bypass output")
        np.testing.assert_array_equal(output[:, 6], 2 * output[:, 5], err_msg=case)
        even, odd = output[:, 5].reshape(-1, 2).T
        # 平均保留 offset-binary 码中心合同；外接斩波路径使用实际旁路码的偶减奇。
        pair_difference = even + odd + 1 if mode == "external_average" else even - odd
        pair_code = pair_difference // 2
    np.testing.assert_array_equal(
        pair_difference,
        archive[f"{case}_pair_exact_half_lsb"],
        err_msg=f"{case}: half-LSB difference",
    )
    np.testing.assert_array_equal(pair_code, archive[f"{case}_pair_signed_code"], err_msg=case)
    return {
        f"{case}_adc2_accept_cycle": acquired[:, 0].astype(np.int32),
        f"{case}_raw_accept_cycle": raw[:, 0].astype(np.int32),
        f"{case}_raw_signed_code": raw[:, 2].astype(np.int32),
        f"{case}_top_output_trace": output.astype(np.int32),
        f"{case}_pair_exact_half_lsb": pair_difference.astype(np.int32),
        f"{case}_pair_signed_code": pair_code.astype(np.int32),
    }


# =========================================================================
# 2. 函数组：build_and_run / main
# =========================================================================
# -------------------------------------------------------------------------
# 单次编译原顶层，为每组数据重新复位和加载同一套权重。
# 运行带噪归档对拍，保存逐笔实测代码和证据哈希。
# ------------------------------------------------------------------------
# build_and_run(out, meta) -> tuple[dict, dict]：含显式异常拒绝、含迭代/循环、numpy 向量化、产出归档/图件、含断言；本模块 的口径以同目录 SPEC/README 为准，越界与失效路径须显式处理
# ------------------------------------------------------------------------
def build_and_run(out: Path, meta: dict) -> tuple[dict, dict]:
    """单次编译原顶层，为每组数据重新复位和加载同一套权重。"""
    command = command_for("verilator")
    env = dict(os.environ)
    version = checked_run(
        [*command, "-V"], cwd=ROOT, env=env, log=out / "verilator.log", timeout=30
    )
    report, observed = {}, {}
    with tempfile.TemporaryDirectory(prefix="sar20_joint_noise_") as scratch:
        work = Path(scratch)
        alias_runtime(work, env, version)
        coeff_path = work / "coeff.hex"
        coefficient = from_training(ROOT / TRAINING)
        coeff_path.write_text(
            "".join(f"{word:07x}\n" for word in coefficient.words()), encoding="ascii"
        )
        if digest(coeff_path) != meta["archived_fixture_identity"]["config_sha256"]:
            raise ValueError("loaded coefficient bank differs from the archived noise model")
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
        with np.load(ROOT / DATA, allow_pickle=False) as archive:
            for case, mode in CASE_MODES.items():
                vectors = work / f"{case}.hex"
                path = work / f"{case}.trace"
                write_fixture(archive, case, vectors)
                vector_hash = digest(vectors)
                if vector_hash != meta["groups"][case]["vectors_sha256"]:
                    raise ValueError(f"replay vector identity changed: {case}")
                rtl_pairs = mode == "rtl_caaz"
                run = checked_run(
                    [
                        str(work / "obj" / f"V{BENCH}"),
                        f"+coeff={coeff_path}",
                        f"+vectors={vectors}",
                        f"+trace={path}",
                        f"+caaz={int(rtl_pairs)}",
                        f"+slice_seed={meta['schedule_seed']:08x}",
                        f"+dem_seed={meta['dem_seed']:08x}",
                        f"+dither_seed={meta['dither_word_seed']:08x}",
                    ],
                    cwd=work,
                    env=env,
                    log=out / f"{case}.log",
                    timeout=60,
                )
                counts = completion(run, MARKER, COUNTS)
                expected = {
                    "adc2_captures": RAW_COUNT,
                    "reconstructed": RAW_COUNT,
                    "outputs": PAIR_COUNT if rtl_pairs else RAW_COUNT,
                    "peak_inflight": 2,
                }
                if any(counts[key] != value for key, value in expected.items()):
                    raise ValueError(f"unexpected replay counts: {case} {counts}")
                observed.update(
                    compare_observations(archive, case, mode, read_trace(path, case, rtl_pairs))
                )
                report[case] = {
                    **counts,
                    "pairing_path": mode,
                    "pair_codes_compared": PAIR_COUNT,
                    "rtl_caaz_enabled": rtl_pairs,
                    "vectors_sha256": vector_hash,
                    "raw_code_mismatches": 0,
                    "pair_code_mismatches": 0,
                    "half_lsb_mismatches": 0,
                    "observed_handshake_trace_sha256": digest(path),
                }
    return report, observed


# ------------------------------------------------------------------------
# main() -> None：含显式异常拒绝、含迭代/循环、numpy 向量化、产出归档/图件；本模块 的口径以同目录 SPEC/README 为准，越界与失效路径须显式处理
# ------------------------------------------------------------------------
def main() -> None:
    """运行带噪归档对拍，保存逐笔实测代码和证据哈希。

    读取负注入训练与噪声归档，核对二者内容摘要后才生成测试向量。
    四组工况必须使用同一硬件配置，分别报告原码重构和配对输出是否逐码一致。
    输出时记录配对由 RTL 生成还是由外部算法生成，二者不能合并为 RTL 证据。

    命令行 --out 为仓库外新目录；目录已存在或输入身份不符时立即拒绝。
    成功结果是给定样本的数字对拍，不是模拟噪声或 CAAZ 电路的后仿验收。
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    out = new_output(args.out)
    meta = json.loads((ROOT / META).read_text(encoding="utf-8"))
    if (
        meta["raw_samples"] != RAW_COUNT
        or meta["pair_outputs"] != PAIR_COUNT
        or digest(ROOT / DATA) != meta["artifact_sha256"][DATA.name]
        or digest(ROOT / TRAINING) != meta["archived_fixture_identity"]["input_data_sha256"]
    ):
        raise ValueError("frozen noise or training archive identity changed")
    report, observed = build_and_run(out, meta)
    observations = out / "observed_codes.npz"
    np.savez_compressed(observations, **observed)
    sources = (
        *SOURCES,
        "task_integration/tools/run_joint_noise_rtl.py",
        "task_integration/tools/run_lms_checks.py",
        "task_integration/tools/run_checks.py",
        "task1/tools/run_rtl_checks.py",
        "tools/run_open_rtl.py",
        "task_reconstruction/fixed.py",
        "task_integration/SPEC.md",
        "task_integration/LMS_PIPELINE.md",
        str(META),
        str(DATA),
        str(TRAINING),
    )
    write_json(
        out / "result.json",
        {
            "status": "PASS",
            "top": TOP,
            "scope": "behavioral_RA_noisy_ADC2_archive_replayed_on_RTL",
            "physical_noise_measurement": False,
            "raw_codes_compared": sum(row["reconstructed"] for row in report.values()),
            "pair_codes_compared": sum(row["pair_codes_compared"] for row in report.values()),
            "pairs_published_by_rtl_caaz": sum(
                row["outputs"] for row in report.values() if row["rtl_caaz_enabled"]
            ),
            "groups": report,
            "observation_columns": [
                "cycle",
                "last_id",
                "first_id",
                "az_generation",
                "is_pair",
                "signed_code",
                "exact_half_lsb",
            ],
            "source_sha256": {name: digest(ROOT / name) for name in sources},
            "artifact_sha256": {
                name: digest(out / name)
                for name in (
                    "observed_codes.npz",
                    "verilator.log",
                    "lint.log",
                    "compile.log",
                    *(f"{case}.log" for case in CASE_MODES),
                )
            },
        },
    )
    for case, row in report.items():
        print(
            f"{case}: {row['reconstructed']} raw codes, {row['pair_codes_compared']} pairs, {row['pairing_path']}: bit exact"
        )


if __name__ == "__main__":
    main()
