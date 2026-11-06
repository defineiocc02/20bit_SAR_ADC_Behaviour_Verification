"""用独立位图和两节点矩阵解核验八片扰动。

pytest 跑全部字及两个相位与短留出记录；直接运行本模块会核验全部
128 个桥相位和完整正弦记录，并写入包含依赖 SHA-256 的 JSON 收据。
"""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import replace
from itertools import product
from pathlib import Path
from platform import python_version

import numpy as np
from task1.experiments.residue_control_study import MapperTable
from task1.experiments.run_effects import ExperimentConfig, _cap_pool, _codebook
from task2.eight.model import ChargePool, coarse_decision, conversion_state, decode, words

from adi_model.charge_ref import ref_ra_charge_nodal

ROOT = Path(__file__).resolve().parents[3]
STEP_V = 10.0 / 544
COARSE_CODES = 512
SEED = 0x5A1709C3
ADC2_HALF_V = 0.100
ADC2_CODES = 4096
ARCHIVE_PREFIX = "fig21_bridge_lms_holdout_"


def _reference_word(word: np.ndarray) -> tuple[np.ndarray, ...]:
    """逐位拆字并生成从有序槽 0 开始分配的采样底板电平。"""
    bit = (word[:, None] >> np.arange(6)) & 1
    upper = 4 * bit[:, 5] + bit[:, 3]
    fraction_count = 4 * bit[:, 2] + 2 * bit[:, 1] + bit[:, 0]
    integer = upper + 2 * bit[:, 4]
    level = integer[:, None] + (np.arange(8) < fraction_count[:, None])
    sampled_sub = np.zeros((len(word), 8, 5), dtype=np.int8)
    sampled_sub[:, :, :4] = (level[:, :, None] >> np.array([3, 2, 1, 0])) & 1
    return upper, fraction_count, integer, level, sampled_sub


def _reference_words(count: int, enabled: bool) -> np.ndarray:
    """用串行反馈生成每笔六位字，独立于向量化发生器。"""
    output = np.zeros(count, dtype=np.uint8)
    if enabled:
        state = SEED
        for row in range(count):
            for _ in range(6):
                feedback = ((state >> 30) ^ (state >> 27)) & 1
                state = ((state << 1) & ((1 << 31) - 1)) | feedback
                output[row] = (int(output[row]) << 1) | feedback
    return output


def _reference_bridge(
    command: np.ndarray, horizontal: np.ndarray, reserved: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """从列相对顺序独立生成 Fig.21 转换底板，末维依物理位排列。"""
    column = np.arange(16)[None, None, :]
    relative = (column - reserved[:, None, None]) % 16
    regular = (relative != 0) & (relative <= (command // 32)[:, None, None])
    slot_distance = (np.arange(8) - horizontal[:, None]) % 8
    vertical = (relative == 0) & (slot_distance < ((command // 4) % 8)[:, None])[..., None]
    columns = regular | vertical
    main = np.concatenate((columns, columns[:, :, -1:]), axis=2)
    sub = np.zeros((len(command), 8, 5), dtype=np.int8)
    sub[:, :, 2] = ((command // 2) % 2)[:, None]
    sub[:, :, 3] = (command % 2)[:, None]
    return main, sub


def _check_nominal(
    experiment: ExperimentConfig, table: MapperTable, *, full: bool
) -> tuple[dict, np.ndarray]:
    """核对所有字、前端拒绝边界和每个合法码字的名义电荷。"""
    nominal, _ = _cap_pool(experiment)
    physical_nominal = ChargePool.from_caps(nominal, experiment)
    thresholds = np.r_[_codebook(nominal, experiment), 5.0]
    index = np.arange(COARSE_CODES + 1)
    ideal = -5 + STEP_V * (index + index // 16)
    np.testing.assert_allclose(thresholds, ideal, rtol=0, atol=4e-15)

    word_all = np.arange(64)
    upper, count, integer, level, sampled = _reference_word(word_all)
    terms = decode(word_all)
    np.testing.assert_array_equal(terms.setting, level)
    np.testing.assert_array_equal(terms.sample_sub_high, sampled @ (1 << np.arange(5)))
    np.testing.assert_array_equal(terms.lower_eighth, 8 * (integer - upper) + count)
    np.testing.assert_array_equal(terms.total_eighth, 8 * integer + count)
    input_limit = ideal[COARSE_CODES - upper] - (integer - upper + count / 8) * STEP_V
    for delta, expected in ((-1e-10, True), (1e-10, False)):
        _, _, valid = coarse_decision(input_limit + delta, terms, thresholds, STEP_V)
        np.testing.assert_array_equal(valid, np.full(64, expected))

    word = np.repeat(word_all, COARSE_CODES)
    coarse = np.tile(np.arange(COARSE_CODES), len(word_all))
    command = coarse + upper[word]
    legal = command < COARSE_CODES
    word, command = word[legal], command[legal]
    plan = np.broadcast_to(np.arange(8), (len(command), 8))
    actual = decode(word)
    # 名义 B21−Vlow−T 的独立有理分子，公共分母为 8 × 544。
    base_num = 8 * command + 2 * (command // 4) - 8 * integer[word] - count[word]
    base_ref = 10.0 * base_num / (8 * 544)
    phase_pairs = list(product(range(8), range(16))) if full else [(0, 0), (7, 15)]
    error_q32 = error_charge = 0.0
    for h, v in phase_pairs:
        phases = np.full(len(command), h), np.full(len(command), v)
        conversion = conversion_state(command, plan, phases, actual, thresholds, STEP_V, table)
        gain, charge_base = physical_nominal.affine(plan, conversion.features)
        np.testing.assert_allclose(gain, 1.0, rtol=0, atol=3e-16)
        error_q32 = max(error_q32, float(np.max(np.abs(conversion.nominal_base_v - base_ref))))
        error_charge = max(error_charge, float(np.max(np.abs(charge_base - base_ref))))
    assert error_q32 <= 0.5 * 5 / (1 << 32) + 4e-15
    assert error_charge < 6e-15
    return {
        "words": len(word_all),
        "legal_coarse_code_word_pairs": len(command),
        "phase_pairs": len(phase_pairs),
        "total_combinations": len(command) * len(phase_pairs),
        "maximum_nominal_q32_difference_v": error_q32,
        "maximum_nominal_charge_difference_v": error_charge,
        "all_word_common_upper_limit_v_exclusive": float(input_limit.min()),
        "full_scale_uniform_word_frontend_rejected_fraction": float(
            np.mean(5.0 - input_limit) / 10.0
        ),
    }, thresholds


def _check_tone(
    percent: int, table: MapperTable, thresholds: np.ndarray, *, full: bool
) -> tuple[list[dict], Path]:
    """独立求解同一物理池的正弦留出，逐笔核对实际转换与采样底板。"""
    experiment = replace(ExperimentConfig(), mismatch_sigma_unit=percent / 100)
    _, pool = _cap_pool(experiment)
    affine = ChargePool.from_caps(pool, experiment)
    archive = ROOT / f"task1/evidence/bridge_lms/sigma_{percent}pct_adc2_100mv/observations.npz"
    chosen = slice(None) if full else slice(0, 257)
    reports = []
    with np.load(archive) as data:
        plan = data[ARCHIVE_PREFIX + "plan"][chosen]
        h = data[ARCHIVE_PREFIX + "horizontal"][chosen]
        v = data[ARCHIVE_PREFIX + "vertical"][chosen]
        x = data["reference_holdout_v"][chosen]
        for enabled in (False, True):
            word = _reference_words(len(x), enabled)
            np.testing.assert_array_equal(words(np.full(len(x), enabled), SEED), word)
            upper, count, integer, _, sample = _reference_word(word)
            offset = (integer - upper + count / 8) * STEP_V
            # Task1 的 B11 阈值从物理电荷生成。使用该表独立判定每笔粗码。
            coarse = np.digitize(x + offset, thresholds) - 1
            command = coarse + upper
            dither = decode(word)
            actual_c, actual_k, legal = coarse_decision(x, dither, thresholds, STEP_V)
            np.testing.assert_array_equal(actual_c, coarse)
            np.testing.assert_array_equal(actual_k, command)
            assert np.all(legal)

            conversion = conversion_state(command, plan, (h, v), dither, thresholds, STEP_V, table)
            main, sub = _reference_bridge(command, h, v)
            np.testing.assert_array_equal(conversion.switches.main, main @ (1 << np.arange(17)))
            np.testing.assert_array_equal(conversion.switches.sub, sub @ (1 << np.arange(5)))
            charge = np.zeros(len(x))
            for physical_id in range(18):
                row, slot = np.nonzero(plan == physical_id)
                if not row.size:
                    continue
                charge[row] += ref_ra_charge_nodal(
                    main_cap_f=pool.main[physical_id],
                    sub_cap_f=pool.sub[physical_id],
                    bridge_cap_f=pool.bridge[physical_id],
                    sub_parasitic_f=0.0,
                    feedback_cap_f=experiment.unit_cap_f,
                    sample_main_v=np.broadcast_to(x[row, None], (len(row), 17)),
                    sample_sub_v=10 * sample[row, slot] - 5,
                    conversion_main_v=10 * main[row, slot] - 5,
                    conversion_sub_v=10 * sub[row, slot] - 5,
                )
            reference_residue = charge / (8 * 32 * experiment.unit_cap_f)
            gain, base = affine.affine(plan, conversion.features)
            residue = gain * (x + 5) - base
            np.testing.assert_allclose(residue, reference_residue, rtol=0, atol=8e-15)
            overload = np.count_nonzero((residue < -ADC2_HALF_V) | (residue >= ADC2_HALF_V))
            assert not overload
            fine_step = 2 * ADC2_HALF_V / ADC2_CODES
            reference_code = np.floor((reference_residue + ADC2_HALF_V) / fine_step)
            np.testing.assert_array_equal(
                np.floor((residue + ADC2_HALF_V) / fine_step), reference_code
            )
            if not enabled:
                np.testing.assert_allclose(
                    reference_residue,
                    data[ARCHIVE_PREFIX + "residue_v"][chosen],
                    rtol=0,
                    atol=8e-15,
                )
                np.testing.assert_array_equal(
                    reference_code, data[ARCHIVE_PREFIX + "adc2_code"][chosen]
                )
            reports.append(
                {
                    "unit_mismatch_sigma_percent": percent,
                    "manufacturing_seed": experiment.manufacturing_seed,
                    "dither_enabled": enabled,
                    "samples": len(x),
                    "reference_residue_min_v": float(reference_residue.min()),
                    "reference_residue_max_v": float(reference_residue.max()),
                    "maximum_nodal_vs_affine_residue_difference_v": float(
                        np.max(np.abs(residue - reference_residue))
                    ),
                    "adc2_overloaded_samples": int(overload),
                    "adc2_code_mismatches": 0,
                }
            )
    return reports, archive


def _run(*, full: bool) -> tuple[dict, list[Path]]:
    table = MapperTable.build(bridge=True)
    nominal, thresholds = _check_nominal(ExperimentConfig(), table, full=full)
    tone, archives = [], []
    for sigma in (2, 3):
        reports, archive = _check_tone(sigma, table, thresholds, full=full)
        tone.extend(reports)
        archives.append(archive)
    return {
        "schema": "task2_native_eight_slice_independent_charge_v1",
        "status": "PASS",
        "scope": "名义闭合、实际底板电压的独立两节点解、前级及 ADC2 量程。",
        "python": python_version(),
        "numpy": np.__version__,
        "nominal": nominal,
        "tone_settings": {
            "adc2_half_range_v": ADC2_HALF_V,
            "adc2_bits": 12,
            "dither_seed_hex": f"0x{SEED:08X}",
            "input_and_scheduler": "Task1 archived holdout, 10 MS/s, bin 409, 4096 samples",
        },
        "tone": tone,
    }, archives


def test_independent_charge_oracle() -> None:
    """快速核对所有字、两个相位和各臂 257 笔留出。"""
    _run(full=False)


def main() -> None:
    """运行全量证明并保存不可覆盖的收据。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    report, archives = _run(full=True)
    paths = [
        Path(__file__),
        ROOT / "src/adi_model/charge_ref.py",
        ROOT / "task1/experiments/residue_control_study.py",
        ROOT / "task1/experiments/run_effects.py",
        ROOT / "task1/experiments/bridge_lms_comparison.py",
        ROOT / "task1/model/fig11_dem.py",
        ROOT / "task1/model/fig21_bridge.py",
        ROOT / "task2/eight/model.py",
        ROOT / "task2/model/dither_word_gen.py",
        *archives,
    ]
    report["sha256"] = {
        path.relative_to(ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in paths
    }
    with args.out.open("x", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


if __name__ == "__main__":
    main()
