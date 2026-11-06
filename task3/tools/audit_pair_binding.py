#!/usr/bin/env python3
"""从冻结夹具复核 CAAZ 配对前的逐笔 dither 掩码绑定。"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True

from task1.experiments.adctoolbox_calibration import SwitchState, _quantize  # noqa: E402
from task1.experiments.residue_control_study import MapperTable  # noqa: E402
from task2.eight.model import coarse_decision, decode  # noqa: E402
from task_integration.tools.run_lms_checks import pack, slots  # noqa: E402
from task_reconstruction.fixed import Coefficients, from_training, reconstruct  # noqa: E402
from tools.run_open_rtl import digest, new_output, write_json  # noqa: E402

CAPS = ROOT / "task2/evidence/eight_native/sigma_3pct"
JOINT = ROOT / "task3/evidence/integrated"
RTL_RECEIPT = ROOT / "task_integration/evidence/on_chip/result.json"
GROUP = "dither_1_caaz_1"


def _replay(
    coef: Coefficients,
    state: SwitchState,
    sample_sub: np.ndarray,
    adc2: np.ndarray,
    words: np.ndarray,
    coarse: np.ndarray,
) -> tuple[np.ndarray, int, str]:
    codes = np.empty(len(words), dtype=np.int32)
    rejected = 0
    vectors = hashlib.sha256()
    for index, (ids, main, conv, sample, fine) in enumerate(
        zip(state.plan, state.main, state.sub, sample_sub, adc2, strict=True)
    ):
        code, bad = reconstruct(
            coef,
            tuple(map(int, ids)),
            tuple(map(int, main)),
            tuple(map(int, conv)),
            tuple(map(int, sample)),
            int(fine),
        )
        codes[index] = code
        rejected += int(bad)
        row = pack(
            (
                (words[index], 6),
                (slots(ids, 5), 40),
                (coarse[index], 9),
                (state.codes[index], 9),
                (slots(main, 17), 136),
                (slots(conv, 5), 40),
                (slots(sample, 5), 40),
                (fine, 12),
                (code, 20),
            )
        )
        vectors.update(row.encode("ascii"))
    return codes, rejected, vectors.hexdigest()


def _pair_codes(codes: np.ndarray) -> np.ndarray:
    return (codes[::2] - codes[1::2]) // 2


def _pair_error(pair_codes: np.ndarray, reference: np.ndarray, lsb: float) -> dict:
    error_uv = ((pair_codes + 0.5) * lsb - reference) * 1e6
    return {
        "mean_uv": float(error_uv.mean()),
        "ac_rms_uv": float(error_uv.std()),
        "absolute_peak_uv": float(np.max(abs(error_uv))),
    }


def audit() -> dict:
    """核对正确向量逐字节身份，并故意给奇笔传入前一偶笔的采样子掩码。"""
    cap_path = CAPS / "data.npz"
    joint_path = JOINT / "joint_dither_caaz_data.npz"
    cap_meta_path, joint_meta_path = CAPS / "summary.json", JOINT / "summary.json"
    cap_meta = json.loads(cap_meta_path.read_text())
    joint_meta = json.loads(joint_meta_path.read_text())
    fixture = json.loads(RTL_RECEIPT.read_text())["paired_3pct_fixture"]
    cap_sha, joint_sha = digest(cap_path), digest(joint_path)
    if not cap_sha == fixture["input_data_sha256"] == cap_meta["artifact_sha256"]["data.npz"]:
        raise ValueError("3% capacitance and training data do not match the RTL receipt")
    if joint_sha != joint_meta["artifact_sha256"][joint_path.name]:
        raise ValueError("joint data do not match the archived plot receipt")

    with np.load(cap_path, allow_pickle=False) as data:
        thresholds = data["nominal_threshold_v"]
    coef = from_training(cap_path)
    config_hex = "".join(f"{word:07x}\n" for word in coef.words())
    if hashlib.sha256(config_hex.encode("ascii")).hexdigest() != fixture["config_sha256"]:
        raise ValueError("Q28 coefficient bytes do not match the RTL receipt")

    with np.load(joint_path, allow_pickle=False) as data:
        plan = data["plan"]
        words = data["dither_word_on"]
        phase = (data["phase_horizontal"], data["phase_vertical"])
        reference = data["pair_reference_v"]
        adc2 = data[f"{GROUP}_adc2_code"]
        residue = data[f"{GROUP}_adc2_residue_v"]
        adc2_cfg = cap_meta["calibration"]
        np.testing.assert_array_equal(
            _quantize(residue, adc2_cfg["adc2_bits"], adc2_cfg["adc2_half_range_v"]), adc2
        )
        chopped_input = data["raw_reference_v"].copy()
        chopped_input[1::2] *= -1
        offset_v = fixture["common_offset_v"]
        if len(plan) != fixture["samples"] or len(plan) != 2 * len(reference):
            raise ValueError("sample count or even/odd pairing changed")
        np.testing.assert_array_equal(chopped_input + offset_v, data[f"{GROUP}_raw_input_v"])

        dither = decode(words)
        step = cap_meta["native_dither_step_v"]
        clean_coarse, _, clean_valid = coarse_decision(chopped_input, dither, thresholds, step)
        coarse, command, valid = coarse_decision(chopped_input + offset_v, dither, thresholds, step)
        if not (valid.all() and clean_valid.all()):
            raise ValueError("the positive fixture must remain inside the coarse input window")
        np.testing.assert_array_equal(coarse, data[f"{GROUP}_raw_coarse"])
        np.testing.assert_array_equal(command, data[f"{GROUP}_command"])

        state, _ = MapperTable.build(bridge=True).lookup(command, plan, phase)
        correct, correct_rejects, correct_sha = _replay(
            coef, state, dither.sample_sub_high, adc2, words, coarse
        )
        np.testing.assert_array_equal(correct, data[f"{GROUP}_raw_signed_code"])
        np.testing.assert_array_equal(_pair_codes(correct), data[f"{GROUP}_pair_signed_code"])
        if (
            not correct_sha
            == fixture["vectors_sha256"]
            == joint_meta["groups"][GROUP]["vectors_sha256"]
        ):
            raise ValueError("repacked positive transactions differ from the RTL replay fixture")

    wrong_sub = dither.sample_sub_high.copy()
    wrong_sub[1::2] = wrong_sub[::2]
    wrong, wrong_rejects, wrong_sha = _replay(coef, state, wrong_sub, adc2, words, coarse)
    if correct_rejects or wrong_rejects:
        raise ValueError(f"unexpected rejected raw results: {correct_rejects=}, {wrong_rejects=}")
    lsb = cap_meta["output_lsb20_v"]
    correct_error = _pair_error(_pair_codes(correct), reference, lsb)
    wrong_error = _pair_error(_pair_codes(wrong), reference, lsb)
    if wrong_error["ac_rms_uv"] <= correct_error["ac_rms_uv"]:
        raise ValueError("the wrong-mask control no longer distinguishes the pair binding error")
    tracked = (
        Path(__file__).resolve(),
        ROOT / "task1/experiments/adctoolbox_calibration.py",
        ROOT / "task1/experiments/residue_control_study.py",
        ROOT / "task2/eight/model.py",
        ROOT / "task3/tools/run_joint_effect.py",
        ROOT / "task_reconstruction/fixed.py",
        ROOT / "task_integration/tools/run_lms_checks.py",
    )
    return {
        "status": "PASS",
        "scope": "DIGITAL_REPLAY_WITH_INTENTIONALLY_MISBOUND_ODD_SAMPLE_MASK",
        "negative_control": "故意替换奇笔的去扰采样子掩码，当前 RTL 同笔绑定不发生此替换。",
        "raw_samples": len(words),
        "paired_outputs": len(reference),
        "pairs_with_different_dither_words": int(np.count_nonzero(words[::2] != words[1::2])),
        "pairs_with_overlapping_slice_groups": int(
            np.count_nonzero(np.any(plan[::2, :, None] == plan[1::2, None, :], axis=(1, 2)))
        ),
        "raw_adc2_rejected_samples": 0,
        "pairs_with_changed_output_code": int(
            np.count_nonzero(_pair_codes(wrong) != _pair_codes(correct))
        ),
        "correct": {
            **correct_error,
            "raw_rejected_samples": correct_rejects,
            "vectors_sha256": correct_sha,
            "identical_to_compiled_rtl_fixture": True,
        },
        "odd_uses_previous_even_sample_sub": {
            **wrong_error,
            "raw_rejected_samples": wrong_rejects,
            "vectors_sha256": wrong_sha,
        },
        "ac_rms_increase_uv": wrong_error["ac_rms_uv"] - correct_error["ac_rms_uv"],
        "front_end_offset": {
            "domain": "CAAZ 换相后、SADC 粗量化前的人工差分电压偏移",
            "offset_v": offset_v,
            "changed_coarse_samples": int(np.count_nonzero(coarse != clean_coarse)),
            "max_abs_coarse_code_change": int(np.max(abs(coarse - clean_coarse))),
        },
        "rtl_fixture": {
            "receipt": str(RTL_RECEIPT.relative_to(ROOT)),
            "field": "paired_3pct_fixture",
            "canonical_sha256": hashlib.sha256(
                json.dumps(fixture, sort_keys=True, separators=(",", ":")).encode("ascii")
            ).hexdigest(),
            "config_sha256": fixture["config_sha256"],
        },
        "source_sha256": {str(p.relative_to(ROOT)): digest(p) for p in tracked},
        "input_sha256": {
            str(p.relative_to(ROOT)): digest(p)
            for p in (cap_path, cap_meta_path, joint_path, joint_meta_path)
        },
    }


def main() -> None:
    """从冻结输入复算并保存机器收据。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    result = audit()
    write_json(new_output(args.out) / "result.json", result)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
