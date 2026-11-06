"""从八片实验证据独立重积分逐码宽、重算 FFT 和校验源数据哈希。"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
from task2.eight.tests.test_independent_charge import ROOT, _reference_bridge, _reference_word

EVIDENCE = ROOT / "task2/evidence/eight_native"
ORACLE = ROOT / "task2/eight/evidence/independent_oracle/receipt.json"


def _sha(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _check_hashes(root: Path, entries: dict[str, str]) -> int:
    for filename, expected in entries.items():
        if _sha(root / filename) != expected:
            raise ValueError(f"SHA-256 differs from the archive: {filename}")
    return len(entries)


def _integrate(data, report: dict, *, enabled: bool) -> dict:
    """独立计算 Fig.21 加冻结 LMS 的每个 ADC2 码仓输入交叠。"""
    experiment, calibration = report["experiment"], report["calibration"]
    peak = experiment["full_scale_peak_v"]
    fine_half = calibration["adc2_half_range_v"]
    fine_step = 2 * fine_half / (1 << calibration["adc2_bits"])
    static_window = report["static_half_window_v"]
    output_guard = report["static_output_guard_v"]
    output_codes = 1 << 20
    span = 2 * peak

    plan0 = data["static_plan"]
    horizontal, reserved = data["static_horizontal"], data["static_vertical"]
    word_on = data["static_on_word"]
    np.testing.assert_array_equal(np.bincount(word_on), np.full(64, len(plan0) // 64))
    word = word_on if enabled else np.zeros_like(word_on)
    upper, fraction, integer, _, sample_sub = _reference_word(word)
    step = report["native_dither_step_v"]
    lower = (integer - upper + fraction / 8) * step

    # 手工组装每个底板的电荷系数。固定片的桥衰减由其自身实际电容决定。
    cap_main, cap_sub = data["actual_main_cap_f"], data["actual_sub_cap_f"]
    cap_bridge = data["actual_bridge_cap_f"]
    beta = cap_bridge / (cap_bridge + cap_sub.sum(axis=1))
    normalizer = 8 * 32 * experiment["unit_cap_f"]
    actual_weights = np.c_[cap_main, beta[:, None] * cap_sub[:, :4]] * span / normalizer
    actual_gain = cap_main.sum(axis=1) / normalizer
    weight_fit, gain_fit = data["fig21_dem_shared_frozen_weight_v"], data["frozen_gain"]
    threshold = data["nominal_threshold_v"]

    n = len(plan0)
    width = np.zeros(output_codes)
    absolute_error_bound = np.zeros(2)
    accepted_input_width = 0.0
    intervals = fine_bins = 0
    for start in range(0, len(threshold) - 1, 8):
        coarse_block = np.arange(start, min(start + 8, len(threshold) - 1))
        coarse = np.repeat(coarse_block, n)
        state = np.tile(np.arange(n), len(coarse_block))
        cmd = coarse + upper[state]
        left = np.maximum(threshold[coarse] - lower[state], -peak)
        right = np.minimum(threshold[coarse + 1] - lower[state], peak)
        valid = (cmd < len(threshold) - 1) & (right > left)
        state, cmd, left, right = (a[valid] for a in (state, cmd, left, right))
        accepted_input_width += float(np.sum(right - left) / n)

        main, sub = _reference_bridge(cmd, horizontal[state], reserved[state])
        bottom = np.concatenate((main, sub[:, :, :4] - sample_sub[state, :, :4]), axis=2)
        ids = plan0[state]
        gain = actual_gain[ids].sum(axis=1)
        fitted_gain = gain_fit[ids].sum(axis=1)
        b_physical = np.sum(bottom * actual_weights[ids], axis=(1, 2))
        q32 = (((cmd // 4) % 4) * (1 << 26) + 8) // 17
        b_nominal = threshold[cmd] + peak + q32 * (peak / (1 << 32)) - word[state] * (step / 8)
        b_fitted = b_nominal + np.sum(bottom * weight_fit[ids], axis=(1, 2))

        # 每区间误差在两个输入端点取极值。再加上半个 ADC2 量化步和半个输出步。
        for endpoint in (left, right):
            residual = gain * (endpoint + peak) - b_physical
            nominal_error = np.abs(b_nominal + residual - peak - endpoint) + fine_step / 2
            lms_error = np.abs(
                (b_fitted + residual) / fitted_gain - peak - endpoint
            ) + fine_step / (2 * fitted_gain)
            absolute_error_bound = np.maximum(
                absolute_error_bound, [nominal_error.max(), lms_error.max()]
            )

        left = np.maximum(left, -static_window)
        right = np.minimum(right, static_window)
        inside = right > left
        if not np.any(inside):
            continue
        left, right, gain, fitted_gain, b_physical, b_fitted = (
            a[inside] for a in (left, right, gain, fitted_gain, b_physical, b_fitted)
        )
        r_left = gain * (left + peak) - b_physical
        r_right = gain * (right + peak) - b_physical
        assert np.all((r_left >= -fine_half) & (r_right <= fine_half))

        first = np.floor((r_left + fine_half) / fine_step).astype(int)
        stop = np.ceil((r_right + fine_half) / fine_step).astype(int)
        count = stop - first
        row = np.repeat(np.arange(len(first)), count)
        bin_index = (
            np.arange(len(row)) + first[row] - np.repeat(np.r_[0, np.cumsum(count[:-1])], count)
        )
        overlap = np.maximum(
            0,
            np.minimum(r_right[row], (bin_index + 1) * fine_step - fine_half)
            - np.maximum(r_left[row], bin_index * fine_step - fine_half),
        )
        mass = overlap / (gain[row] * n)
        adc2_midpoint = (bin_index + 0.5) * fine_step - fine_half
        reconstructed = (b_fitted[row] + adc2_midpoint) / fitted_gain[row]
        code = np.floor(reconstructed * (output_codes / span)).astype(int)
        assert np.all((code >= 0) & (code < output_codes))
        width += np.bincount(code, weights=mass, minlength=output_codes)
        intervals += len(first)
        fine_bins += len(row)

    absolute_error_bound += 0.5 * span / output_codes
    assert np.all(absolute_error_bound < output_guard)
    arm = "on" if enabled else "off"
    archived = data[f"fig21_dem_{arm}_lms_width_v"]
    np.testing.assert_allclose(width, archived, rtol=0, atol=2e-15)
    rail_rejected = span - accepted_input_width
    np.testing.assert_allclose(
        rail_rejected,
        report["groups"]["fig21_dem"]["linearity"][arm]["lms"][
            "full_scale_front_end_rejected_width_v"
        ],
        rtol=0,
        atol=1e-12,
    )
    return {
        "dither_enabled": enabled,
        "input_state_intervals": intervals,
        "adc2_bin_intersections": fine_bins,
        "code_width_max_difference_v": float(np.max(np.abs(width - archived))),
        "full_scale_front_end_rejected_width_v": rail_rejected,
        "full_scale_pre_export_error_plus_half_lsb20_bound_v": absolute_error_bound.tolist(),
    }


def _check_metrics(data, summary: dict) -> dict:
    """从所有已导出的整码重算频谱和从每个码宽重算线性度。"""
    cfg = summary["experiment"]
    step, peak, tone_bin = summary["output_lsb20_v"], cfg["full_scale_peak_v"], cfg["tone_bin"]
    for name in ("tone_reference_v", "tone_plan", "train_reference_v", "train_plan"):
        np.testing.assert_array_equal(data[f"no_dem_{name}"], data[f"fig21_dem_{name}"])
    width_sets = tone_sets = 0
    for group, results in summary["groups"].items():
        training_word = data[group + "_shared_train_word"]
        training_enable = data[group + "_train_dither_enable"]
        assert np.count_nonzero(training_enable) * 2 == len(training_word)
        assert not training_word[~training_enable].any()
        assert not data[group + "_old_off_train_word"].any()
        for arm in ("off", "on"):
            for mode in ("nominal", "lms", "old_off"):
                prefix = f"{group}_{arm}_{mode}_"
                codes = data[prefix + "output_code"]
                power = np.abs(np.fft.fft((codes + 0.5) * step - peak)) ** 2
                fundamental = power[tone_bin] + power[-tone_bin]
                power[[0, tone_bin, -tone_bin]] = 0
                actual = (
                    10 * np.log10(fundamental / power.sum()),
                    10 * np.log10(fundamental / (2 * power.max())),
                )
                expected = results["tone"][arm][mode]
                np.testing.assert_allclose(
                    actual, [expected["sndr_db"], expected["sfdr_dbc"]], atol=1e-8, rtol=0
                )
                tone_sets += 1
                if mode == "old_off":
                    continue
                linearity = results["linearity"][arm][mode]
                width = data[prefix + "width_v"]
                first, stop = (
                    linearity[key]
                    for key in ("evaluated_first_unsigned_code", "evaluated_stop_unsigned_code")
                )
                actual_width = width[first:stop]
                dnl = actual_width / step - 1
                inl = np.r_[0, np.cumsum(dnl)]
                inl -= np.linspace(0, inl[-1], len(inl))
                measured = [
                    np.sqrt(np.mean(dnl**2)),
                    dnl.min(),
                    dnl.max(),
                    np.max(np.abs(inl)),
                    np.count_nonzero(actual_width <= 1e-15),
                ]
                expect = [
                    linearity[key]
                    for key in (
                        "dnl_rms_lsb20",
                        "dnl_min_lsb20",
                        "dnl_max_lsb20",
                        "endpoint_inl_peak_lsb20",
                        "zero_width_codes",
                    )
                ]
                np.testing.assert_allclose(measured, expect, atol=1e-9, rtol=0)
                width_sets += 1
    return {"tone_sets": tone_sets, "code_width_sets": width_sets}


def main() -> None:
    """验证两档交付件并保存独立收据。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    oracle = json.loads(ORACLE.read_text(encoding="utf-8"))
    oracle_count = _check_hashes(ROOT, oracle["sha256"])
    manifests = (("figures.json", "script_sha256"), ("no_lms_figures.json", "source_sha256"))
    figure_counts = {}
    hash_paths = [Path(__file__), ORACLE]
    for filename, script_key in manifests:
        path = EVIDENCE / filename
        manifest = json.loads(path.read_text(encoding="utf-8"))
        figure_counts[filename] = sum(
            _check_hashes(base, manifest[key])
            for key, base in (
                ("input_sha256", EVIDENCE),
                (script_key, ROOT),
                ("artifact_sha256", EVIDENCE),
            )
        )
        hash_paths.append(path)
    results = {}
    for percent in (2, 3):
        case = EVIDENCE / f"sigma_{percent}pct"
        summary_path, data_path = case / "summary.json", case / "data.npz"
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        source_count = _check_hashes(ROOT, summary["source_sha256"])
        data_count = _check_hashes(case, summary["artifact_sha256"])
        with np.load(data_path) as data:
            results[f"sigma_{percent}pct"] = {
                "source_hashes": source_count,
                "data_hashes": data_count,
                "reported_metrics_check": _check_metrics(data, summary),
                "independent_static_reintegration": [
                    _integrate(data, summary, enabled=e) for e in (False, True)
                ],
            }
        hash_paths.extend((summary_path, data_path))
    report = {
        "schema": "task2_native_eight_slice_archive_audit_v1",
        "status": "PASS",
        "figure_and_input_hashes": sum(figure_counts.values()),
        "hash_entries_by_figure_manifest": figure_counts,
        "oracle_dependency_hashes": oracle_count,
        "results": results,
        "sha256": {path.relative_to(ROOT).as_posix(): _sha(path) for path in hash_paths},
    }
    with args.out.open("x", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


if __name__ == "__main__":
    main()
