# =============================================================================
# 文件名     : test_independent_charge.py
# 模块名     : test_independent_charge
# 版本       : v1.0
# 功能       : 用独立位图和两节点矩阵解核验八片扰动。
#
# 设计说明   :
#   * pytest 跑全部字及两个相位与短留出记录；直接运行本模块会核验全部
#   * 128 个桥相位和完整正弦记录，并写入包含依赖 SHA-256 的 JSON 收据。
# -----------------------------------------------------------------------------
# 变更记录
# -----------------------------------------------------------------------------
# 初始版本，暂无变更记录。
# ===========================================================================

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

from adi_model.capacitors import ExperimentConfig, cap_pool
from adi_model.charge_ref import ref_ra_charge_nodal
from task1.experiments.residue_control_study import MapperTable
from task1.model.codebook import codebook
from task2.eight.model import ChargePool, coarse_decision, conversion_state, decode, words

ROOT = Path(__file__).resolve().parents[3]
STEP_V = 10.0 / 544
# 512 在此处故意写死，不从 adi_model.codes 导入：独立 oracle 若复用被测方的常量，
# 位宽被改成 10 bit 时 oracle 会跟着一起变、于是「一致」通过，正好漏掉要抓的错。
COARSE_CODES = 512
SEED = 0x5A1709C3
ADC2_HALF_V = 0.100
ADC2_CODES = 4096
ARCHIVE_PREFIX = "fig21_bridge_lms_holdout_"


# =========================================================================
# 1. 函数组：_reference_word / _reference_words / _reference_bridge
# =========================================================================
# -------------------------------------------------------------------------
# 逐位拆字并生成从有序槽 0 开始分配的采样底板电平。
# 用串行反馈生成每笔六位字，独立于向量化发生器。
# 从列相对顺序独立生成 Fig.21 转换底板，末维依物理位排列。
# ------------------------------------------------------------------------
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


# ------------------------------------------------------------------------
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


# ------------------------------------------------------------------------
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


# =========================================================================
# 2. 函数组：_check_nominal / _check_tone / _run
# =========================================================================
# -------------------------------------------------------------------------
# 核对所有字、前端拒绝边界和每个合法码字的名义电荷。
# 独立求解同一物理池的正弦留出，逐笔核对实际转换与采样底板。
# ------------------------------------------------------------------------
def _check_nominal(
    experiment: ExperimentConfig, table: MapperTable, *, full: bool
) -> tuple[dict, np.ndarray]:
    """核对所有字、前端拒绝边界和每个合法码字的名义电荷。"""
    nominal, _ = cap_pool(experiment)
    physical_nominal = ChargePool.from_caps(nominal, experiment)
    thresholds = np.r_[codebook(nominal, experiment), 5.0]
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
    # 负注入（C9）：SADC 判 x−L，粗码 c≥U 才合法，故每笔字的输入下限右移 +L。
    input_limit = ideal[upper] + (integer - upper + count / 8) * STEP_V
    for delta, expected in ((-1e-10, False), (1e-10, True)):
        _, _, valid = coarse_decision(input_limit + delta, terms, thresholds, STEP_V)
        np.testing.assert_array_equal(valid, np.full(64, expected))

    word = np.repeat(word_all, COARSE_CODES)
    coarse = np.tile(np.arange(COARSE_CODES), len(word_all))
    command = coarse - upper[word]
    legal = command >= 0
    word, command = word[legal], command[legal]
    plan = np.broadcast_to(np.arange(8), (len(command), 8))
    actual = decode(word)
    # 名义 VD(k)+T 的独立有理分子，公共分母为 8 × 544。
    # 负注入下 RDAC 存的是 x−T，所以数字侧要先补回 +T 才与 VD(k) 合成输入。
    base_num = 8 * command + 2 * (command // 4) + 8 * integer[word] + count[word]
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
        # 负注入下每个字的合法输入是 [ideal[U]+L, +peak)；下限逐字不同，
        # 全字并联能接到的绝对下限取 min，最苛刻的那个字取 max。
        "minimum_accepted_input_v": float(input_limit.min()),
        "worst_word_lower_limit_v": float(input_limit.max()),
        "full_scale_uniform_word_frontend_rejected_fraction": float(
            np.mean(input_limit - (-5.0)) / 10.0
        ),
    }, thresholds


# ------------------------------------------------------------------------
def _check_tone(
    percent: int, table: MapperTable, thresholds: np.ndarray, *, full: bool
) -> tuple[list[dict], Path]:
    """独立求解同一物理池的正弦留出，逐笔核对实际转换与采样底板。"""
    experiment = replace(ExperimentConfig(), mismatch_sigma_unit=percent / 100)
    _, pool = cap_pool(experiment)
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
            # Task1 的 B11 阈值从物理电荷生成。负注入下 SADC 判 x−L，用该表独立判定每笔粗码。
            coarse = np.digitize(x - offset, thresholds) - 1
            command = coarse - upper
            dither = decode(word)
            actual_c, actual_k, legal = coarse_decision(x, dither, thresholds, STEP_V)
            np.testing.assert_array_equal(actual_c, coarse)
            np.testing.assert_array_equal(actual_k, command)
            np.testing.assert_array_equal(legal, command >= 0)
            # c<U 的下溢样本在真实前端就被拒绝，不进入 RDAC，也不参与电荷核对。
            keep = np.nonzero(legal)[0]
            command, slot_plan, slot_h, slot_v = command[keep], plan[keep], h[keep], v[keep]
            slot_dither = decode(word[keep])

            conversion = conversion_state(
                command, slot_plan, (slot_h, slot_v), slot_dither, thresholds, STEP_V, table
            )
            main, sub = _reference_bridge(command, slot_h, slot_v)
            np.testing.assert_array_equal(conversion.switches.main, main @ (1 << np.arange(17)))
            np.testing.assert_array_equal(conversion.switches.sub, sub @ (1 << np.arange(5)))
            charge = np.zeros(len(keep))
            for physical_id in range(18):
                row, slot = np.nonzero(slot_plan == physical_id)
                if not row.size:
                    continue
                charge[row] += ref_ra_charge_nodal(
                    main_cap_f=pool.main[physical_id],
                    sub_cap_f=pool.sub[physical_id],
                    bridge_cap_f=pool.bridge[physical_id],
                    sub_parasitic_f=0.0,
                    feedback_cap_f=experiment.unit_cap_f,
                    sample_main_v=np.broadcast_to(x[keep][row, None], (len(row), 17)),
                    sample_sub_v=np.full((len(row), 5), -5.0),
                    conversion_main_v=10 * main[row, slot] - 5,
                    conversion_sub_v=10 * sub[row, slot] - 5,
                    # [模型边界] 将注入直接加入子节点 KCL。电荷幅度由实际子电容
                    # 与逻辑位图确定，是共享权重假设；不是低轨→高轨底板采样。
                    extra_sub_charge_c=10
                    * np.sum(pool.sub[physical_id] * sample[keep][row, slot], axis=-1),
                )
            nodal_residue = charge / (8 * 32 * experiment.unit_cap_f)
            gain, base = affine.affine(slot_plan, conversion.features)
            residue = gain * (x[keep] + 5) - base
            # 独立解必须直接给出同一残差，禁止求解后另减 2T 修补极性。
            # 这证明两个数学模型相符，不证明受控电荷源已有晶体管实现。
            np.testing.assert_allclose(residue, nodal_residue, rtol=0, atol=8e-15)
            overload = np.count_nonzero((residue < -ADC2_HALF_V) | (residue >= ADC2_HALF_V))
            assert not overload
            fine_step = 2 * ADC2_HALF_V / ADC2_CODES
            reference_code = np.floor((nodal_residue + ADC2_HALF_V) / fine_step)
            np.testing.assert_array_equal(
                np.floor((residue + ADC2_HALF_V) / fine_step), reference_code
            )
            if not enabled:
                np.testing.assert_allclose(
                    nodal_residue,
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
                    "front_end_rejected_samples": int(np.count_nonzero(~legal)),
                    "reference_residue_min_v": float(nodal_residue.min()),
                    "reference_residue_max_v": float(nodal_residue.max()),
                    "maximum_nodal_vs_affine_residue_difference_v": float(
                        np.max(np.abs(residue - nodal_residue))
                    ),
                    "maximum_nodal_minus_affine_residue_v": float(
                        np.max(np.abs(residue - nodal_residue))
                    ),
                    "injection_boundary": "explicit_subnode_charge_source_shared_sub_weights",
                    "adc2_overloaded_samples": int(overload),
                    "adc2_code_mismatches": 0,
                }
            )
    return reports, archive


# ------------------------------------------------------------------------
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
        "scope": "名义闭合、含显式辅助电荷源的独立两节点解（共享子权重假设）、前级及 ADC2 量程。",
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


# =========================================================================
# 3. 函数组：test_independent_charge_oracle / main
# =========================================================================
# -------------------------------------------------------------------------
# 快速核对所有字、两个相位和各臂 257 笔留出。
# 运行全量证明并保存不可覆盖的收据。
# ------------------------------------------------------------------------
def test_independent_charge_oracle() -> None:
    """快速核对所有字、两个相位和各臂 257 笔留出。"""
    _run(full=False)


# ------------------------------------------------------------------------
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
