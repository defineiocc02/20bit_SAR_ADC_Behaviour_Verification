"""Separate ADC2 quantization, residue headroom, and ideal subtractive dither."""

from __future__ import annotations

import argparse
import json
from dataclasses import replace
from importlib.metadata import version
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from task1.experiments.adctoolbox_calibration import (
    CalibrationConfig,
    SwitchState,
    _decode,
    _fixed_ab_flags,
    _fixed_switch_bits,
    _group_flags,
    _metrics,
    _quantize,
)
from task1.experiments.bridge_lms_comparison import ARM_LABELS, Q32, _bridge_bits
from task1.experiments.inl_dnl import (
    OUTPUT_BITS,
    PLOT_BITS,
    RampStates,
    _build_intervals,
    _fold,
    _linearity,
    _mass,
    _output_codes,
    _sha,
    _source_check,
    _states,
)
from task1.experiments.run_effects import ROOT, ExperimentConfig, _cap_pool, _codebook

DITHER_SEED = 0x44495448
DITHER_LEVELS = 16
REPEATS = (1, 4, 16)
CAPTURES = ("gain_low", "gain_high", "training", "holdout")
VIEW_CODES = slice((1 << (OUTPUT_BITS - 1)) - 48, (1 << (OUTPUT_BITS - 1)) + 48)


def _dither_words(step: float) -> np.ndarray:
    """Use the midpoints of an equiprobable discrete, one-fine-LSB distribution."""
    return ((np.arange(DITHER_LEVELS) + 0.5) / DITHER_LEVELS - 0.5) * step


def _headroom(residue: np.ndarray, cfg: CalibrationConfig, peak: float) -> dict:
    result = {}
    one_lsb20_half = peak * 2.0 ** (cfg.adc2_bits - OUTPUT_BITS)
    for label, half in (("one_lsb20_step", one_lsb20_half), ("50mV", 0.05), ("100mV", 0.1)):
        words = _dither_words(2 * half / (1 << cfg.adc2_bits))
        passing = ((residue[:, None] + words) >= -half) & ((residue[:, None] + words) < half)
        result[label] = {
            "half_window_v": half,
            "samples": len(residue),
            "residue_min_v": float(residue.min()),
            "residue_max_v": float(residue.max()),
            "overload_without_dither": int(np.count_nonzero((residue < -half) | (residue >= half))),
            "at_least_one_dither_word_overloads": int(np.count_nonzero(~passing.all(axis=1))),
            "every_dither_word_overloads": int(np.count_nonzero(~passing.any(axis=1))),
            "sample_word_pairs_overloaded": int(np.count_nonzero(~passing)),
            "sample_word_pairs": passing.size,
        }
    return result


def _tone(data, arm: str, experiment: ExperimentConfig, cfg: CalibrationConfig, words: np.ndarray):
    prefix = f"{arm}_"
    state = SwitchState(
        data[prefix + "holdout_plan"],
        data[prefix + "holdout_coarse_code"],
        data[prefix + "holdout_main_mask"],
        data[prefix + "holdout_sub_mask"],
    )
    residue = data[prefix + "holdout_residue_v"]
    reference = data["reference_holdout_v"]
    peak, half = experiment.full_scale_peak_v, cfg.adc2_half_range_v
    bridge = arm == "fig21_bridge_lms"
    flags = _group_flags(state.plan) if bridge else _fixed_ab_flags(state.plan)
    masks = _bridge_bits(state) if bridge else _fixed_switch_bits(state)
    gain = flags @ data[prefix + "frozen_gain"]
    base = (
        data["nominal_threshold_v"][state.codes]
        + peak
        + data[prefix + "holdout_correction_q32"] * (peak / Q32)
        + masks @ data[prefix + "frozen_equivalent_switch_weight_v"]
    )
    fine = _quantize(residue, cfg.adc2_bits, half)
    np.testing.assert_array_equal(fine, data[prefix + "holdout_adc2_code"])
    off = (base + _decode(fine, cfg.adc2_bits, half)) / gain - peak
    np.testing.assert_allclose(off, data[prefix + "holdout_calibrated_v"], rtol=0, atol=5e-14)
    adc2_step = 2 * half / (1 << cfg.adc2_bits)
    export_step = 2 * peak / (1 << OUTPUT_BITS)

    def export(volts: np.ndarray) -> np.ndarray:
        code = np.clip(_output_codes(volts, peak), 0, (1 << OUTPUT_BITS) - 1)
        return (code + 0.5) * export_step - peak

    # The same word sequence is replayed in both arms and both mismatch cases.
    rng = np.random.default_rng(DITHER_SEED)
    word_idx = rng.integers(len(words), size=(max(REPEATS), len(residue)))
    dither = words[word_idx]
    dithered_code = _quantize(residue[None, :] + dither, cfg.adc2_bits, half)
    demod = _decode(dithered_code, cfg.adc2_bits, half) - dither
    on = (base + demod) / gain - peak
    reports = {}
    traces = {"reference_v": reference, "residue_v": residue, "dither_word_index": word_idx}
    for count in REPEATS:
        for name, reconstructed, exported, adc2_error in (
            ("off", off, export(off), _decode(fine, cfg.adc2_bits, half) - residue),
            (
                "on",
                on[:count].mean(axis=0),
                export(on[:count]).mean(axis=0),
                demod[:count].mean(axis=0) - residue,
            ),
        ):
            key = f"{name}_repeat{count}"
            reports[key] = {
                "adc2_quantization_rms_v": float(np.sqrt(np.mean(adc2_error**2))),
                "before_export": _metrics(reconstructed, reference, experiment),
                "after_export": _metrics(exported, reference, experiment),
            }
            traces[f"{key}_pre_export_v"] = reconstructed
            traces[f"{key}_post_export_v"] = exported
    reports["expected_single_shot_quantization_rms_v"] = adc2_step / np.sqrt(12)
    return reports, traces


def _density(
    data, arm: str, experiment: ExperimentConfig, cfg: CalibrationConfig, words: np.ndarray
):
    nominal, pool = _cap_pool(experiment)
    peak, half = experiment.full_scale_peak_v, cfg.adc2_half_range_v
    thresholds = np.r_[_codebook(nominal, experiment), peak]
    np.testing.assert_array_equal(thresholds[:-1], data["nominal_threshold_v"])
    bridge = arm == "fig21_bridge_lms"
    # Four independent ramp seeds, four frozen states per seed for the bridge.
    # Use the two natural states in the fixed arm. Each state is applied to every coarse bin.
    groups = _states(experiment, 4, bridge=bridge)
    states = RampStates(
        np.concatenate([group.plan for group in groups]),
        np.concatenate([group.horizontal for group in groups]),
        np.concatenate([group.vertical for group in groups]),
    )
    gain = data[f"{arm}_frozen_gain"]
    weight = data[f"{arm}_frozen_equivalent_switch_weight_v"]
    state_count = len(states.plan)
    width = {key: np.zeros(1 << OUTPUT_BITS) for key in ("off", "on")}
    limits = {key: np.zeros(4) for key in width}
    for start in range(0, len(thresholds) - 1, 12):
        coarse = np.arange(start, min(start + 12, len(thresholds) - 1))
        interval = _build_intervals(
            pool, experiment, thresholds, states, gain, weight, coarse, bridge=bridge
        )
        for key, offsets in (("off", np.zeros(1)), ("on", words)):
            probability = 1.0 / (state_count * len(offsets))
            for offset in offsets:
                shifted = replace(
                    interval,
                    residue_low_v=interval.residue_low_v + offset,
                    residue_high_v=interval.residue_high_v + offset,
                    digital_base_v=interval.digital_base_v - offset,
                )
                mass = _mass(shifted, cfg.adc2_bits, half, peak)
                if mass.code.size:
                    first, last = int(mass.code.min()), int(mass.code.max()) + 1
                    width[key][first:last] += (
                        np.bincount(mass.code - first, weights=mass.width_v, minlength=last - first)
                        * probability
                    )
                limits[key] += probability * np.array(
                    [mass.adc2_below_v, mass.adc2_above_v, mass.output_below_v, mass.output_above_v]
                )
    reports, traces = {}, {}
    for key, histogram in width.items():
        low, high, export_low, export_high = limits[key]
        np.testing.assert_allclose(histogram.sum() + low + high, 2 * peak, rtol=0, atol=5e-11)
        if low + high > 1e-15:
            raise ValueError("uniform ramp overloads ADC2; do not publish an incomplete DNL")
        _, _, dnl20 = _linearity(histogram)
        width14 = _fold(histogram, PLOT_BITS)
        _, _, dnl14 = _linearity(width14)
        reports[key] = {
            "state_count": state_count,
            "dither_word_count": len(words) if key == "on" else 1,
            "adc2_below_input_width_v": float(low),
            "adc2_above_input_width_v": float(high),
            "export_below_input_width_v": float(export_low),
            "export_above_input_width_v": float(export_high),
            "20bit": dnl20,
            "14bit": dnl14,
        }
        traces[f"{key}_width14_v"] = width14
        traces[f"{key}_occupied20_bitpack"] = np.packbits(histogram > 1e-15)
        traces[f"{key}_central20_width_v"] = histogram[VIEW_CODES]
    return reports, traces


def _plot(out: Path, results: list[dict], traces: list[dict]) -> None:
    fig, axes = plt.subplots(2, 2, figsize=(12, 7.8), layout="constrained")
    fig.suptitle("12-bit ADC2 | Ideal subtractive dither with frozen NLMS", fontsize=16)
    first = results[0]
    axis = axes[0, 0]
    codes = np.arange(VIEW_CODES.start, VIEW_CODES.stop) - (1 << (OUTPUT_BITS - 1))
    for key, color in (("off", "#666e78"), ("on", "#1377a5")):
        widths = traces[0]["fixed_ab_lms_only"]["density"][f"{key}_central20_width_v"]
        norm = first["arms"]["fixed_ab_lms_only"]["density"][key]["20bit"][
            "mean_interior_code_width_v"
        ]
        axis.step(codes, widths / norm - 1, where="mid", color=color, label=key.capitalize())
    axis.set(
        title=f"Fixed A/B, σ={first['sigma_percent']:g}% | central codes",
        xlabel="20-bit output code relative to midscale",
        ylabel="DNL (LSB20)",
    )
    axis.legend()

    axis = axes[0, 1]
    row = 0
    labels = []
    for item in results:
        for arm, result in item["arms"].items():
            labels.append(
                f"{item['sigma_percent']:g}% {'A/B' if arm.startswith('fixed') else 'Bridge'}"
            )
            for key, dy, color in (("off", -0.17, "#666e78"), ("on", 0.17, "#1377a5")):
                missing = result["density"][key]["20bit"]["zero_width_interior_codes"]
                axis.barh(
                    row + dy,
                    missing,
                    height=0.29,
                    color=color,
                    label=key.capitalize() if row == 0 else None,
                )
                if not missing:
                    axis.plot(0, row + dy, "o", color=color, markersize=4, clip_on=False)
                axis.annotate(
                    f"{missing:,}",
                    (missing, row + dy),
                    xytext=(3, 0),
                    textcoords="offset points",
                    va="center",
                    fontsize=8,
                )
            row += 1
    axis.set(
        yticks=np.arange(len(labels)),
        yticklabels=labels,
        xlabel="20-bit zero-width inner codes",
        title="Equal-weight code density",
    )
    axis.set_xscale("symlog", linthresh=1)
    axis.set_xlim(0, 5e6)
    axis.legend(loc="upper right", fontsize=8)

    axis = axes[1, 0]
    for arm, result in first["arms"].items():
        color = "#1377a5" if arm.startswith("fig") else "#a94d1e"
        label = "Bridge" if arm.startswith("fig") else "Fixed A/B"
        for key, style in (("off", ":"), ("on", "-")):
            y = [
                1e6 * result["tone"][f"{key}_repeat{n}"]["after_export"]["input_error_rms_v"]
                for n in REPEATS
            ]
            axis.plot(REPEATS, y, style, color=color, marker="o", label=f"{label}, {key}")
    axis.set_xscale("log", base=2)
    axis.set(
        xticks=REPEATS,
        xticklabels=[str(n) for n in REPEATS],
        xlabel="Independent conversions of the same held sample",
        ylabel="20-bit output error (µV RMS)",
        title=f"Tone holdout, σ={first['sigma_percent']:g}%",
    )
    axis.legend(fontsize=8)

    axis = axes[1, 1]
    row, labels = 0, []
    for item in results:
        for arm, result in item["arms"].items():
            label = "A/B" if arm.startswith("fixed") else "Bridge"
            for capture, color in (
                ("gain_low", "#a94d1e"),
                ("gain_high", "#d39056"),
                ("training", "#1377a5"),
                ("holdout", "#76a4c4"),
            ):
                values = result["headroom"][capture]["50mV"]
                axis.plot(
                    [1000 * values["residue_min_v"], 1000 * values["residue_max_v"]],
                    [row, row],
                    color=color,
                    linewidth=3,
                )
                labels.append(f"{item['sigma_percent']:g}% {label} {capture}")
                row += 1
    for limit in (-50, 50):
        axis.axvline(limit, color="#424242", linestyle="--", linewidth=0.9)
    axis.set(
        yticks=np.arange(len(labels)),
        yticklabels=labels,
        xlabel="Residue (mV, input referred)",
        title="Modelled pre-ADC2 residue and ±50 mV window",
        xlim=(-102, 102),
    )
    axis.tick_params(axis="y", labelsize=7)
    for axis in axes.flat:
        axis.grid(alpha=0.25)
    fig.savefig(out / "adc2_dither.png", dpi=155, facecolor="white")
    plt.close(fig)


def run(training: list[Path], out: Path) -> None:
    """Use same archived physical samples, held-out stimulus, and frozen coefficients."""
    plt.switch_backend("Agg")
    out.mkdir(parents=True, exist_ok=True)
    results, traces = [], []
    for path in training:
        meta = json.loads((path / "summary.json").read_text())
        _source_check(path, meta)
        experiment = ExperimentConfig(**meta["experiment"])
        cfg = CalibrationConfig(**meta["calibration"])
        if cfg.adc2_bits != 12 or cfg.adc2_half_range_v != 0.1:
            raise ValueError("provide a completed 12-bit ±100 mV bridge LMS experiment")
        step = 2 * cfg.adc2_half_range_v / (1 << cfg.adc2_bits)
        words = _dither_words(step)
        report = {
            "sigma_percent": 100 * experiment.mismatch_sigma_unit,
            "manufacturing_seed": experiment.manufacturing_seed,
            "adc2_step_v": step,
            "dither_words_v": words.tolist(),
            "training_artifact_sha256": {
                name: _sha(path / name) for name in ("summary.json", "observations.npz")
            },
            "arms": {},
        }
        raw = {}
        with np.load(path / "observations.npz", allow_pickle=False) as data:
            for arm in ARM_LABELS:
                tone, tone_traces = _tone(data, arm, experiment, cfg, words)
                density, density_traces = _density(data, arm, experiment, cfg, words)
                report["arms"][arm] = {
                    "tone": tone,
                    "density": density,
                    "headroom": {
                        name: _headroom(
                            data[f"{arm}_{name}_residue_v"], cfg, experiment.full_scale_peak_v
                        )
                        for name in CAPTURES
                    },
                }
                raw[arm] = {"tone": tone_traces, "density": density_traces}
        sigma_key = f"{report['sigma_percent']:g}".replace(".", "p")
        np.savez_compressed(
            out / f"traces_sigma_{sigma_key}pct.npz",
            **{
                f"{arm}_{kind}_{field}": value
                for arm, by_arm in raw.items()
                for kind, by_kind in by_arm.items()
                for field, value in by_kind.items()
            },
        )
        results.append(report)
        traces.append(raw)
    source = Path(__file__).resolve()
    dependencies = (
        source.relative_to(ROOT).as_posix(),
        "task1/experiments/inl_dnl.py",
        "task1/experiments/bridge_lms_comparison.py",
        "task1/experiments/adctoolbox_calibration.py",
        "task1/experiments/run_effects.py",
    )
    info = {
        "schema": "task1_adc2_ideal_dither_study_v1",
        "definition": "Ideal quantizer at ADC2 input, exact input-referred dither subtraction before gain correction",
        "adctoolbox": version("adctoolbox"),
        "dither_seed": DITHER_SEED,
        "repeat_counts": REPEATS,
        "source_sha256": {name: _sha(ROOT / name) for name in dependencies},
        "cases": results,
    }
    (out / "summary.json").write_text(json.dumps(info, indent=2, allow_nan=False) + "\n")
    _plot(out, results, traces)


def main() -> None:
    """Run the dither diagnostic on one or more archived LMS pairs."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--training", nargs="+", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    run(args.training, args.out)


if __name__ == "__main__":
    main()
