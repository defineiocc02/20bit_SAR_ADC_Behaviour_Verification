"""在共同的物理失配样本上独立训练无 DEM 与 Fig.21 候选。"""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict, replace
from importlib.metadata import version
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from task1.experiments.adctoolbox_calibration import (
    CalibrationConfig,
    SwitchState,
    _condition_report,
    _decode,
    _fit_gain_from_flags,
    _fit_nlms,
    _fixed_ab_flags,
    _fixed_ab_plan,
    _fixed_switch_bits,
    _group_flags,
    _metrics,
    _quantize,
    _range_report,
    _residue_voltage,
    _spectrum,
    _write_summary,
)
from task1.experiments.run_effects import (
    BLUE,
    GRAY,
    MAIN_WEIGHTS,
    ROOT,
    SUB_WEIGHTS,
    Capacitors,
    ExperimentConfig,
    _cap_pool,
    _codebook,
    _map_record,
    _schedule,
    _unpack,
)
from task1.model.fig21_bridge import map_fig21_bridge
from task1.model.scheduler import SLICE_COUNT

ARM_LABELS = {
    "fixed_ab_lms_only": "Fixed A/B, no DEM + NLMS",
    "fig21_bridge_lms": "Dynamic Fig. 21 bridge + NLMS",
}
Q32 = 1 << 32


def _state(
    codes: np.ndarray,
    plan: np.ndarray,
    phases: tuple[np.ndarray, np.ndarray],
    *,
    bridge: bool,
) -> tuple[SwitchState, np.ndarray]:
    if not bridge:
        main, sub = _map_record(codes, phases, dem_enable=False)
        return SwitchState(plan, codes, main, sub), np.zeros(len(plan), dtype=np.int64)

    main = np.empty(plan.shape, dtype=np.uint32)
    sub = np.empty(plan.shape, dtype=np.uint8)
    correction = np.empty(len(plan), dtype=np.int64)
    for row, (code, horizontal, vertical) in enumerate(zip(codes, *phases, strict=True)):
        # 与 Module1Bridge 的冻结相位约定一致。
        result = map_fig21_bridge(int(code), int(vertical), int(horizontal) % 8)
        main[row], sub[row] = result.masks.main, result.masks.sub
        correction[row] = result.correction_q32
    return SwitchState(plan, codes, main, sub), correction


def _plan(
    samples: int, experiment: ExperimentConfig, *, bridge: bool
) -> tuple[np.ndarray, tuple[np.ndarray, np.ndarray]]:
    if bridge:
        plan, _, phases = _schedule(samples, experiment)
        return plan, phases
    zero = np.zeros(samples, dtype=np.uint8)
    return _fixed_ab_plan(samples), (zero, zero)


def _bridge_bits(state: SwitchState) -> np.ndarray:
    main = _unpack(state.main, len(MAIN_WEIGHTS))
    sub = _unpack(state.sub, len(SUB_WEIGHTS))
    if np.any(main[..., 15] != main[..., 16]) or sub[..., (0, 1, 4)].any():
        raise ValueError("Fig. 21 aggregate features require paired 1C and inactive B5/B6/dummy")
    active = np.concatenate((main[..., :16], sub[..., 2:4]), axis=-1)
    physical = np.zeros((len(state.plan), SLICE_COUNT, active.shape[-1]), dtype=np.uint8)
    physical[np.arange(len(state.plan))[:, None], state.plan] = active
    return physical.reshape(len(state.plan), -1)


def _fit_observable(
    fit: np.ndarray, holdout: np.ndarray, target: np.ndarray, cfg: CalibrationConfig
) -> tuple[np.ndarray, dict, list[dict]]:
    # 只从训练位图识别列空间。若留出出现训练空间外分量，拒绝发布结果。
    _, singular, right = np.linalg.svd(fit.astype(float), full_matrices=False)
    design = _condition_report(singular, fit.shape)
    rank = design["rank"]
    if rank < 1:
        raise ValueError("empty calibration feature subspace")
    if rank == fit.shape[1]:
        weights, _, learning = _fit_nlms(fit, target, cfg)
        holdout_unobserved_norm = 0.0
    else:
        basis = right[:rank].T
        holdout_in_basis = holdout @ basis
        holdout_unobserved_norm = float(
            np.max(np.linalg.norm(holdout - holdout_in_basis @ basis.T, axis=1))
        )
        if holdout_unobserved_norm > 1e-8:
            raise ValueError("holdout activates a switch combination outside the training subspace")
        projected, _, learning = _fit_nlms(fit @ basis, target, cfg)
        weights = basis @ projected
    design.update(
        svd_discarded_columns=fit.shape[1] - rank,
        retained_condition_number=float(singular[0] / singular[rank - 1]),
        max_holdout_unobserved_l2=holdout_unobserved_norm,
    )
    return weights, design, learning


def _arm(
    pool: Capacitors,
    nominal: Capacitors,
    threshold_v: np.ndarray,
    references: dict[str, np.ndarray],
    experiment: ExperimentConfig,
    cfg: CalibrationConfig,
    *,
    bridge: bool,
) -> tuple[dict, dict[str, np.ndarray]]:
    peak = experiment.full_scale_peak_v
    fits = {
        "gain": replace(experiment, schedule_seed=cfg.gain_schedule_seed),
        "training": replace(
            experiment, schedule_seed=cfg.training_schedule_seed, dem_seed=cfg.training_dem_seed
        ),
        "holdout": experiment,
    }
    raw = {}
    states = {}
    corrections = {}
    nominal_error_v = {}
    for name, run_cfg in fits.items():
        reference = references["gain_low" if name == "gain" else name]
        plan, phases = _plan(len(reference), run_cfg, bridge=bridge)
        if name == "gain":
            codes = np.full(len(plan), cfg.forced_gain_code, dtype=np.uint16)
        else:
            codes = (np.searchsorted(threshold_v, reference, side="right") - 1).astype(np.uint16)
        state, correction_q32 = _state(codes, plan, phases, bridge=bridge)
        states[name] = state
        corrections[name] = correction_q32.astype(float) * (peak / Q32)
        masks = state.main, state.sub
        raw.update(
            {
                f"{name}_plan": state.plan,
                f"{name}_coarse_code": state.codes,
                f"{name}_main_mask": state.main,
                f"{name}_sub_mask": state.sub,
                f"{name}_horizontal": phases[0],
                f"{name}_vertical": phases[1],
                f"{name}_correction_q32": correction_q32,
            }
        )
        nominal_r = _residue_voltage(nominal, reference, plan, masks, experiment)
        closure = nominal_r + threshold_v[codes] + corrections[name] - reference
        nominal_error_v[name] = float(np.max(np.abs(closure)))
        if nominal_error_v[name] > 0.5 * peak / Q32 + 1e-12:
            raise ValueError(f"nominal two-node closure exceeds half of Q32 LSB in {name}")
        for key in ("gain_low", "gain_high") if name == "gain" else (name,):
            raw[f"{key}_residue_v"] = _residue_voltage(
                pool, references[key], plan, masks, experiment
            )

    capture_names = ("gain_low", "gain_high", "training", "holdout")
    headroom = {
        name: _range_report(raw[f"{name}_residue_v"], cfg.adc2_half_range_v)
        for name in capture_names
    }
    blocked = [
        key for key, value in headroom.items() if value["below_range"] or value["at_or_above_range"]
    ]
    hold_state = states["holdout"]
    raw["holdout_unquantized_nominal_v"] = (
        threshold_v[hold_state.codes] + corrections["holdout"] + raw["holdout_residue_v"]
    )
    report = {
        "bridge_enabled": bridge,
        "selection": "dynamic" if bridge else "fixed_ab",
        "gain_reference_pairs": cfg.gain_pairs,
        "training_samples_per_pass": cfg.training_samples,
        "passes": cfg.lms_passes,
        "update_count_if_trained": cfg.training_samples * cfg.lms_passes,
        "nominal_two_node_q32_closure_max_error_v": nominal_error_v,
        "adc2_range_checks": headroom,
        "blocked_stages": blocked,
        "continuous_pre_adc2_diagnostic": _metrics(
            raw["holdout_unquantized_nominal_v"], references["holdout"], experiment
        ),
    }
    if blocked:
        return {**report, "status": "adc2_range_blocked", "holdout": None}, raw

    for name in capture_names:
        raw[f"{name}_adc2_code"] = _quantize(
            raw[f"{name}_residue_v"], cfg.adc2_bits, cfg.adc2_half_range_v
        )
    gain_flags = _group_flags if bridge else _fixed_ab_flags
    switch_bits = _bridge_bits if bridge else _fixed_switch_bits
    gain, gain_report = _fit_gain_from_flags(
        gain_flags(states["gain"].plan),
        references["gain_low"],
        references["gain_high"],
        raw["gain_low_adc2_code"],
        raw["gain_high_adc2_code"],
        cfg,
    )

    def nominal_reconstruction(name: str) -> np.ndarray:
        # 数字路径加回名义 Q32 差值一次。ADC2 码来自未补偿模拟余量。
        return (
            threshold_v[states[name].codes]
            + corrections[name]
            + _decode(raw[f"{name}_adc2_code"], cfg.adc2_bits, cfg.adc2_half_range_v)
        )

    fit_nominal = nominal_reconstruction("training")
    train_gain = gain_flags(states["training"].plan) @ gain
    target = train_gain * (references["training"] + peak) - (fit_nominal + peak)
    fit_bits = switch_bits(states["training"])
    hold_bits = switch_bits(hold_state)
    weight, design, learning = _fit_observable(fit_bits, hold_bits, target, cfg)
    hold_nominal = nominal_reconstruction("holdout")
    hold_gain = gain_flags(hold_state.plan) @ gain
    calibrated = (hold_nominal + peak + hold_bits @ weight) / hold_gain - peak
    raw.update(
        frozen_gain=gain,
        frozen_equivalent_switch_weight_v=weight,
        holdout_nominal_v=hold_nominal,
        holdout_calibrated_v=calibrated,
    )
    return {
        **report,
        "status": "completed",
        "gain_design": gain_report,
        "equivalent_weight_design": design,
        "learning": learning,
        "holdout": _metrics(calibrated, references["holdout"], experiment),
    }, raw


def _plot(
    out: Path, report: dict, records: dict, experiment: ExperimentConfig, cfg: CalibrationConfig
) -> None:
    arms = list(ARM_LABELS)
    colors = (GRAY, BLUE)
    fig, axes = plt.subplot_mosaic(
        [["spectrum", "spectrum"], ["result", "learning"]],
        figsize=(11, 7),
        layout="constrained",
    )
    completed = report["status"] == "completed"
    fig.suptitle(
        f"Independent NLMS | unit-cap σ {100 * experiment.mismatch_sigma_unit:g}% | "
        f"ideal ADC2 ±{1000 * cfg.adc2_half_range_v:g} mV",
        fontsize=15,
        fontweight="bold",
    )
    spectrum = axes["spectrum"]
    uncalibrated_labels = {
        "fixed_ab_lms_only": "Fixed A/B, nominal coarse + residue",
        "fig21_bridge_lms": "Fig. 21, nominal coarse + residue + Q32",
    }
    for name, color in zip(arms, colors, strict=True):
        key = "holdout_calibrated_v" if completed else "holdout_unquantized_nominal_v"
        data = _spectrum(records[name][key], experiment)["plot_data"]
        spectrum.plot(
            data["freq"] / 1e6,
            data["power_spectrum_db_plot"],
            lw=0.75,
            color=color,
            label=(ARM_LABELS if completed else uncalibrated_labels)[name],
        )
    spectrum.set(
        xlabel="Frequency (MHz)",
        ylabel="Amplitude (dBFS/bin)",
        title="Independent calibrated holdout"
        if completed
        else "Continuous diagnostic before ADC2",
        ylim=(-160 if completed else -115, 1),
    )
    spectrum.legend(loc="upper right", fontsize=8)
    result = axes["result"]
    learning_ax = axes["learning"]
    if completed:
        x = np.arange(2)
        for name, color, offset in zip(arms, colors, (-0.19, 0.19), strict=True):
            stats = report["arms"][name]["holdout"]
            bars = result.bar(
                x + offset, [stats["sndr_db"], stats["sfdr_db"]], width=0.35, color=color
            )
            result.bar_label(bars, fmt="%.2f", padding=2, fontsize=8)
            learning = report["arms"][name]["learning"]
            learning_ax.semilogy(
                [row["epoch"] for row in learning],
                [row["training_residual_rms_v"] * 1e6 for row in learning],
                color=color,
                marker="o",
            )
        highest = max(
            arm["holdout"][metric]
            for arm in report["arms"].values()
            for metric in ("sndr_db", "sfdr_db")
        )
        result.set(xticks=x, xticklabels=("SNDR", "SFDR"), ylabel="dB", ylim=(0, highest + 13))
        learning_ax.set(xlabel="Training passes", ylabel="Switch-fit RMS error (µV)")
    else:
        stages = ("gain_low", "gain_high", "training", "holdout")
        max_mv = 1000 * cfg.adc2_half_range_v
        for name, color, offset in zip(arms, colors, (-0.18, 0.18), strict=True):
            checks = report["arms"][name]["adc2_range_checks"]
            x = np.arange(len(stages)) + offset
            values = [
                checks[key]["below_range"] + checks[key]["at_or_above_range"] for key in stages
            ]
            bars = result.bar(x, values, width=0.35, color=color)
            result.bar_label(bars, fontsize=8, padding=2)
            peaks = [
                1000 * max(abs(checks[key]["min_v"]), abs(checks[key]["max_v"])) for key in stages
            ]
            learning_ax.scatter(x, peaks, color=color, s=15)
        labels = tuple(key.replace("_", " ") for key in stages)
        result.set(xticks=np.arange(len(stages)), xticklabels=labels, ylabel="Overrange samples")
        learning_ax.axhline(max_mv, color="black", ls=":", lw=1)
        learning_ax.set(
            xticks=np.arange(len(stages)), xticklabels=labels, ylabel="Peak absolute residue (mV)"
        )
    for ax in axes.values():
        ax.grid(alpha=0.22)
    fig.savefig(out / "bridge_lms_comparison.png", dpi=165, facecolor="white")
    plt.close(fig)


def run(
    out: Path,
    sigma: float,
    half_range_v: float,
    manufacturing_seed: int,
    *,
    summary_only: bool = False,
) -> dict:
    """复跑同一失配池、相同训练预算的两条独立链路。"""
    if not np.isfinite(sigma) or sigma < 0 or not np.isfinite(half_range_v) or half_range_v <= 0:
        raise ValueError("sigma and ADC2 half-range must be finite, with sigma >= 0 and range > 0")
    experiment = replace(
        ExperimentConfig(), mismatch_sigma_unit=sigma, manufacturing_seed=manufacturing_seed
    )
    cfg = replace(CalibrationConfig(), adc2_half_range_v=half_range_v)
    nominal, pool = _cap_pool(experiment)
    cap_minimum = {name: float(np.min(getattr(pool, name))) for name in ("main", "sub", "bridge")}
    if any(value <= 0 or not np.isfinite(value) for value in cap_minimum.values()):
        raise ValueError("invalid physical capacitance from the specified mismatch seed")

    threshold_v = _codebook(nominal, experiment)
    gain_rng = np.random.default_rng(cfg.gain_reference_seed)
    gain_mid = cfg.gain_reference_midpoint_span_v * gain_rng.uniform(-0.5, 0.5, cfg.gain_pairs)
    gain_halfspan = cfg.gain_reference_span_v / 2
    references = {
        "gain_low": gain_mid - gain_halfspan,
        "gain_high": gain_mid + gain_halfspan,
        "training": cfg.training_tone_peak_v
        * np.sin(
            2
            * np.pi
            * cfg.training_tone_bin
            / cfg.training_samples
            * np.arange(cfg.training_samples)
            + cfg.training_tone_phase_rad
        ),
        "holdout": experiment.tone_peak_v
        * np.sin(
            2 * np.pi * experiment.fin_hz / experiment.fs_hz * np.arange(experiment.samples)
            + cfg.evaluation_tone_phase_rad
        ),
    }

    arms = {}
    records = {}
    for name, bridge in zip(ARM_LABELS, (False, True), strict=True):
        arms[name], records[name] = _arm(
            pool, nominal, threshold_v, references, experiment, cfg, bridge=bridge
        )
    completed = all(report["status"] == "completed" for report in arms.values())
    comparison = None
    if completed:
        baseline = arms["fixed_ab_lms_only"]["holdout"]
        candidate = arms["fig21_bridge_lms"]["holdout"]
        comparison = {key: candidate[key] - baseline[key] for key in ("sndr_db", "sfdr_db")}
        comparison["joint_improvement_this_die"] = all(value > 0 for value in comparison.values())
    summary = {
        "schema": "task1_fig21_bridge_nlms_comparison_v1",
        "status": "completed" if completed else "adc2_range_blocked",
        "model_scope": "单个虚拟制造样本、名义粗判决、理想两节点和理想 ADC2；时偏和热噪声关闭。",
        "experiment": {
            key: value
            for key, value in asdict(experiment).items()
            if key not in ("skew_s", "static_repeats")
        },
        "calibration": asdict(cfg),
        "adc2_window_basis": "behavioral_default" if half_range_v == 0.05 else "candidate",
        "adc2_step_v": 2 * half_range_v / (1 << cfg.adc2_bits),
        "minimum_physical_cap_f": cap_minimum,
        "training_frequency_hz": experiment.fs_hz * cfg.training_tone_bin / cfg.training_samples,
        "evaluation_frequency_hz": experiment.fin_hz,
        "phase_contract": "reserved_column=v, vertical_slot=h%8; phases freeze per sample",
        "digital_nominal_correction": "adc2 decoded + nominal coarse threshold + Q32 * VFS_peak / 2**32",
        "fig21_feature_contract": {
            "original_physical_switch_columns": SLICE_COUNT
            * (len(MAIN_WEIGHTS) + len(SUB_WEIGHTS)),
            "always_off_subcolumns_per_slice": ["B5", "B6", "dummy"],
            "always_tied_main_columns_per_slice": ["B4", "S4"],
            "combined_feature_columns": SLICE_COUNT * 18,
        },
        "arms": arms,
        "bridge_minus_fixed": comparison,
        "analysis": {"library": "ADCToolbox", "version": version("adctoolbox")},
        "source_sha256": {
            path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
            for path in (
                Path(__file__).resolve().relative_to(ROOT).as_posix(),
                "task1/experiments/adctoolbox_calibration.py",
                "task1/experiments/run_effects.py",
                "task1/model/fig21_bridge.py",
                "task1/model/fig11_dem.py",
                "task1/model/module1.py",
                "task1/model/scheduler.py",
                "src/adi_model/charge_ref.py",
            )
        },
    }
    out.mkdir(parents=True, exist_ok=True)
    if not summary_only:
        _plot(out, summary, records, experiment, cfg)
        np.savez_compressed(
            out / "observations.npz",
            nominal_threshold_v=threshold_v,
            **{f"reference_{key}_v": trace for key, trace in references.items()},
            **{
                f"{arm}_{field}": trace
                for arm, fields in records.items()
                for field, trace in fields.items()
            },
        )
    return _write_summary(out, summary)


def main() -> None:
    """执行两条链路，并以非零退出码标记量程阻断。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--sigma", required=True, type=float)
    parser.add_argument("--adc2-half-range-mv", default=100.0, type=float)
    parser.add_argument("--seed", default=100, type=int)
    parser.add_argument("--summary-only", action="store_true")
    args = parser.parse_args()
    summary = run(
        args.out,
        args.sigma,
        args.adc2_half_range_mv / 1000,
        args.seed,
        summary_only=args.summary_only,
    )
    print(
        json.dumps(
            {
                "status": summary["status"],
                "bridge_minus_fixed": summary["bridge_minus_fixed"],
                "arms": {
                    key: {"holdout": val["holdout"], "blocked_stages": val["blocked_stages"]}
                    for key, val in summary["arms"].items()
                },
            },
            indent=2,
        )
    )
    if summary["status"] != "completed":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
