"""Evaluate pre-ADC2 RDAC recentering using frozen foreground calibration."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
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
    _range_report,
    _residue_voltage,
)
from task1.experiments.bridge_lms_comparison import ARM_LABELS, Q32, _bridge_bits, _state
from task1.experiments.inl_dnl import BRIDGE_SEEDS, OUTPUT_BITS, _sha, _source_check, _states
from task1.experiments.run_effects import ROOT, Capacitors, ExperimentConfig, _cap_pool, _codebook

COARSE_CODES = 512
RADIUS = 3
STATES_PER_SEED = 128
COARSE_BATCH = 16
DIRECT = "direct_coarse_command"
CENTERED = "frozen_weight_centered_command"


@dataclass(frozen=True)
class MapperTable:
    """Canonical mapper outputs indexed by RDAC command and frozen phase."""

    bridge: bool
    main: np.ndarray
    sub: np.ndarray
    correction_q32: np.ndarray

    @classmethod
    def build(cls, *, bridge: bool) -> MapperTable:
        """Evaluate the production mapper over all legal code and phase combinations."""
        phase_count = 128 if bridge else 1
        codes = np.repeat(np.arange(COARSE_CODES, dtype=np.uint16), phase_count)
        phase = np.tile(np.arange(phase_count, dtype=np.uint8), COARSE_CODES)
        plan = np.broadcast_to(np.arange(8), (len(codes), 8))
        state, correction = _state(codes, plan, (phase % 8, phase // 8), bridge=bridge)
        return cls(
            bridge,
            state.main.reshape(COARSE_CODES, phase_count, -1),
            state.sub.reshape(COARSE_CODES, phase_count, -1),
            correction.reshape(COARSE_CODES, phase_count),
        )

    def lookup(
        self, command: np.ndarray, plan: np.ndarray, phases: tuple[np.ndarray, np.ndarray]
    ) -> tuple[SwitchState, np.ndarray]:
        """Return the switch masks and nominal correction for a command vector."""
        h, v = phases
        phase = v * 8 + h % 8 if self.bridge else np.zeros_like(h)
        return (
            SwitchState(plan, command, self.main[command, phase], self.sub[command, phase]),
            self.correction_q32[command, phase],
        )


@dataclass(frozen=True)
class FrozenArm:
    """Digital quantities admitted by the deployed command selector."""

    table: MapperTable
    threshold_v: np.ndarray
    gain: np.ndarray
    switch_weight_v: np.ndarray

    @property
    def peak_v(self) -> float:
        """Return the positive full-scale input in volts."""
        return float(self.threshold_v[-1])

    def predicted_gain(self, plan: np.ndarray) -> np.ndarray:
        """Combine the frozen gain coefficients for the selected physical groups."""
        flags = _group_flags(plan) if self.table.bridge else _fixed_ab_flags(plan)
        result = flags @ self.gain
        if np.any(result <= 0):
            raise ValueError("frozen residual gain must be positive")
        return result

    def predicted_base(self, state: SwitchState, correction_q32: np.ndarray) -> np.ndarray:
        """Compute the frozen digital intercept for the actual RDAC command."""
        switch_bits = _bridge_bits(state) if self.table.bridge else _fixed_switch_bits(state)
        return (
            self.threshold_v[state.codes]
            + self.peak_v
            + correction_q32 * (self.peak_v / Q32)
            + switch_bits @ self.switch_weight_v
        )

    def select(
        self,
        coarse: np.ndarray,
        plan: np.ndarray,
        phases: tuple[np.ndarray, np.ndarray],
    ) -> tuple[SwitchState, np.ndarray]:
        """Find a legal local RDAC command using only the coarse interval center."""
        coarse = coarse.astype(np.int16)
        center_v = (self.threshold_v[coarse] + self.threshold_v[coarse + 1]) * 0.5
        target = self.predicted_gain(plan) * (center_v + self.peak_v)
        command = coarse.copy()
        direct, correction = self.table.lookup(coarse, plan, phases)
        error = np.abs(target - self.predicted_base(direct, correction))
        # Equal costs keep the incumbent. The direct command has first priority.
        for radius in range(1, RADIUS + 1):
            for sign in (-1, 1):
                candidate = coarse + sign * radius
                legal = (candidate >= 0) & (candidate < COARSE_CODES)
                clipped = np.clip(candidate, 0, COARSE_CODES - 1)
                state, correction = self.table.lookup(clipped, plan, phases)
                trial = np.abs(target - self.predicted_base(state, correction))
                take = legal & (trial < error)
                command[take] = candidate[take]
                error[take] = trial[take]
        return self.table.lookup(command, plan, phases)


def _spectral_evidence(
    r: np.ndarray,
    reference_v: np.ndarray,
    base_v: np.ndarray,
    gain: np.ndarray,
    experiment: ExperimentConfig,
    adc2_bits: int,
    half_window_v: float,
) -> dict:
    report = _range_report(r, half_window_v)
    clipped = np.clip(r, -half_window_v, np.nextafter(half_window_v, -np.inf))
    adc2_v = _decode(_quantize(clipped, adc2_bits, half_window_v), adc2_bits, half_window_v)
    peak = experiment.full_scale_peak_v
    decoded = (base_v + adc2_v) / gain - peak
    step_v = 2 * peak / (1 << OUTPUT_BITS)
    code = np.floor((decoded + peak) / step_v)
    exported = (np.clip(code, 0, (1 << OUTPUT_BITS) - 1) + 0.5) * step_v - peak
    metrics = {
        "adc2_quantization_and_overload_input_error_rms_v": float(
            np.sqrt(np.mean(((adc2_v - r) / gain) ** 2))
        ),
        "before_export": _metrics(decoded, reference_v, experiment),
        "after_20bit_export": _metrics(exported, reference_v, experiment),
        "export_saturated_samples": int(np.count_nonzero((code < 0) | (code >= 1 << OUTPUT_BITS))),
    }
    overloaded = bool(report["below_range"] or report["at_or_above_range"])
    return {
        **report,
        "half_window_v": half_window_v,
        "adc2_step_v": 2 * half_window_v / (1 << adc2_bits),
        "status": "adc2_overload" if overloaded else "no_adc2_overload",
        "valid_capture": None if overloaded else metrics,
        "saturating_adc2_diagnostic": metrics if overloaded else None,
    }


def _tone(
    data: np.lib.npyio.NpzFile,
    arm: str,
    pool: Capacitors,
    experiment: ExperimentConfig,
    cfg: CalibrationConfig,
    frozen: FrozenArm,
    half_window_v: float,
) -> tuple[dict, dict]:
    prefix = arm + "_holdout_"
    coarse = data[prefix + "coarse_code"]
    plan = data[prefix + "plan"]
    phases = data[prefix + "horizontal"], data[prefix + "vertical"]
    direct = frozen.table.lookup(coarse, plan, phases)
    centered = frozen.select(coarse, plan, phases)
    for field in ("main", "sub"):
        np.testing.assert_array_equal(getattr(direct[0], field), data[prefix + field + "_mask"])
    np.testing.assert_array_equal(direct[1], data[prefix + "correction_q32"])

    # Physical input is admitted only after the selector returns the actual command.
    reference_v = data["reference_holdout_v"]
    gain = frozen.predicted_gain(plan)
    traces = {"reference_v": reference_v, "input_coarse_code": coarse}
    report = {}
    for label, (state, correction) in zip((DIRECT, CENTERED), (direct, centered), strict=True):
        r = _residue_voltage(pool, reference_v, plan, (state.main, state.sub), experiment)
        base = frozen.predicted_base(state, correction)
        continuous = (base + r) / gain - frozen.peak_v
        if label == DIRECT:
            np.testing.assert_array_equal(r, data[prefix + "residue_v"])
            original = (
                base + _decode(data[prefix + "adc2_code"], cfg.adc2_bits, cfg.adc2_half_range_v)
            ) / gain - frozen.peak_v
            np.testing.assert_allclose(original, data[prefix + "calibrated_v"], rtol=0, atol=5e-14)
        traces[label + "_rdac_command"] = state.codes
        traces[label + "_residue_v"] = r
        traces[label + "_continuous_calibrated_v"] = continuous
        report[label] = {
            "changed_commands": int(np.count_nonzero(state.codes != coarse)),
            "residue_peak_abs_v": float(np.max(np.abs(r))),
            "continuous_calibration_error_rms_v": float(
                np.sqrt(np.mean((continuous - reference_v) ** 2))
            ),
            "target_narrow_adc2": _spectral_evidence(
                r, reference_v, base, gain, experiment, cfg.adc2_bits, half_window_v
            ),
            "archive_window_adc2": _spectral_evidence(
                r, reference_v, base, gain, experiment, cfg.adc2_bits, cfg.adc2_half_range_v
            ),
        }
    return report, traces


def _interval_statistics(
    extrema: np.ndarray, overloaded_width_v: np.ndarray, clipped_intervals: int, full_scale: float
) -> dict:
    minimum = float(extrema[:, 0].min())
    maximum = float(extrema[:, 1].max())
    return {
        "min_v": minimum,
        "max_v": maximum,
        "required_symmetric_half_window_v": max(-minimum, maximum),
        "required_window_with_optimized_center_v": maximum - minimum,
        "overload_input_fraction": float(overloaded_width_v.sum() / full_scale),
        "coarse_state_intervals_with_overload": clipped_intervals,
    }


def _static(
    pool: Capacitors,
    experiment: ExperimentConfig,
    frozen: FrozenArm,
    half_window_v: float,
) -> tuple[dict, dict]:
    groups = _states(experiment, STATES_PER_SEED, bridge=frozen.table.bridge)
    plan0 = np.concatenate([s.plan for s in groups])
    h0 = np.concatenate([s.horizontal for s in groups])
    v0 = np.concatenate([s.vertical for s in groups])
    nstates = len(plan0)
    modes = (DIRECT, CENTERED)
    extrema = {name: np.empty((COARSE_CODES, 2)) for name in modes}
    clipped_widths = {name: np.empty(COARSE_CODES) for name in modes}
    clipped_counts = dict.fromkeys(modes, 0)
    continuous_trim_lower_bound_v = 0.0
    maximum_frozen_model_error = 0.0
    trim_min_v = np.inf
    trim_max_v = -np.inf
    ideal_predicted_trim_peak_v = 0.0
    changes = 0
    for start in range(0, COARSE_CODES, COARSE_BATCH):
        stop = min(start + COARSE_BATCH, COARSE_CODES)
        count = stop - start
        coarse = np.repeat(np.arange(start, stop, dtype=np.uint16), nstates)
        plan = np.tile(plan0, (count, 1))
        phases = np.tile(h0, count), np.tile(v0, count)
        direct = frozen.table.lookup(coarse, plan, phases)
        centered = frozen.select(coarse, plan, phases)
        changes += int(np.count_nonzero(centered[0].codes != coarse))
        low, high = frozen.threshold_v[coarse], frozen.threshold_v[coarse + 1]
        gain = frozen.predicted_gain(plan)
        for label, (state, correction) in zip(modes, (direct, centered), strict=True):
            masks = state.main, state.sub
            r_low = _residue_voltage(pool, low, plan, masks, experiment)
            r_high = _residue_voltage(pool, high, plan, masks, experiment)
            width_v = high - low
            true_gain = (r_high - r_low) / width_v
            if np.any(true_gain <= 0):
                raise ValueError("the physical interval residue must have positive slope")
            base = frozen.predicted_base(state, correction)
            pred_low, pred_high = (
                gain * (low + frozen.peak_v) - base,
                gain * (high + frozen.peak_v) - base,
            )
            if label == CENTERED:
                predicted_trim_v = (pred_low + pred_high) * 0.5
                trim_min_v = min(trim_min_v, float(predicted_trim_v.min()))
                trim_max_v = max(trim_max_v, float(predicted_trim_v.max()))
                ideal_predicted_trim_peak_v = max(
                    ideal_predicted_trim_peak_v,
                    float(np.max(np.abs(r_low - predicted_trim_v))),
                    float(np.max(np.abs(r_high - predicted_trim_v))),
                )
            maximum_frozen_model_error = max(
                maximum_frozen_model_error,
                float(np.max(np.abs(np.r_[r_low - pred_low, r_high - pred_high]))),
            )
            continuous_trim_lower_bound_v = max(
                continuous_trim_lower_bound_v, float(np.max((r_high - r_low) / 2))
            )
            # Residue is affine within a fixed input-code interval and switch state.
            # Integrate the invalid input widths, weighting every retained state equally.
            below = np.clip((-half_window_v - r_low) / true_gain, 0, width_v)
            above = np.clip((r_high - half_window_v) / true_gain, 0, width_v)
            invalid = below + above
            extrema[label][start:stop, 0] = r_low.reshape(count, nstates).min(axis=1)
            extrema[label][start:stop, 1] = r_high.reshape(count, nstates).max(axis=1)
            clipped_widths[label][start:stop] = invalid.reshape(count, nstates).mean(axis=1)
            clipped_counts[label] += int(np.count_nonzero(invalid > 0))
    report = {
        "state_count": nstates,
        "coarse_codes": COARSE_CODES,
        "coarse_state_intervals": COARSE_CODES * nstates,
        "changed_commands": changes,
        "all_phase_seeds": BRIDGE_SEEDS if frozen.table.bridge else None,
        "symmetric_half_window_v": half_window_v,
        "nominal_max_input_coarse_bin_width_v": float(np.max(np.diff(frozen.threshold_v))),
        "half_window_lower_bound_with_ideal_continuous_center_trim_v": continuous_trim_lower_bound_v,
        "max_frozen_prediction_error_at_interval_endpoints_v": maximum_frozen_model_error,
        "additional_ideal_analog_trim_budget": {
            "predicted_trim_min_v": trim_min_v,
            "predicted_trim_max_v": trim_max_v,
            "residue_peak_after_hypothetical_ideal_trim_v": ideal_predicted_trim_peak_v,
            "remaining_ideal_half_window_margin_v": half_window_v - ideal_predicted_trim_peak_v,
        },
    }
    traces = {}
    for label in modes:
        report[label] = _interval_statistics(
            extrema[label], clipped_widths[label], clipped_counts[label], 2 * frozen.peak_v
        )
        traces[label + "_residue_extrema_v"] = extrema[label]
        traces[label + "_overloaded_input_width_v"] = clipped_widths[label]
    return report, traces


def _plot(out: Path, results: list[tuple[dict, dict]]) -> None:
    fig, axes = plt.subplots(len(results), len(ARM_LABELS), figsize=(11, 7), squeeze=False)
    for row, (result, traces) in enumerate(results):
        for col, (name, label) in enumerate(ARM_LABELS.items()):
            ax = axes[row, col]
            half_mv = 1e3 * result["adc2_half_window_for_one_export_lsb_v"]
            ax.axhspan(-half_mv, half_mv, alpha=0.15, color="tab:green", label="12-bit window")
            for mode, color, label_mode in (
                (DIRECT, "0.5", "Direct command"),
                (CENTERED, "tab:blue", "Frozen-weight recentering"),
            ):
                data = traces[f"{name}_static_{mode}_residue_extrema_v"] * 1e3
                ax.plot(data[:, 0], color=color, lw=0.8, alpha=0.8, label=label_mode)
                ax.plot(data[:, 1], color=color, lw=0.8, alpha=0.8)
            ax.set_title(f"{100 * result['unit_sigma']:.0f}% mismatch | {label}", fontsize=9)
            ax.set(xlabel="Input coarse code", ylabel="Physical residue extrema (mV)")
            ax.grid(alpha=0.2)
    axes[0, 0].legend(loc="best", fontsize=8)
    fig.suptitle("ADC2 headroom using predicted RDAC recentering", fontweight="bold")
    fig.tight_layout()
    fig.savefig(out / "residue_control.png", dpi=150)
    plt.close(fig)


def main() -> None:
    """Read the archived training evidence and write the independent comparison."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--training", type=Path, nargs="+", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    plt.switch_backend("Agg")
    tables = {bridge: MapperTable.build(bridge=bridge) for bridge in (False, True)}
    records = []
    for index, training in enumerate(args.training):
        report_path = training / "summary.json"
        frozen_path = training / "observations.npz"
        prior = json.loads(report_path.read_text())
        _source_check(training, prior)
        experiment = ExperimentConfig(**prior["experiment"])
        cfg = CalibrationConfig(**prior["calibration"])
        if cfg.adc2_bits != 12 or not np.isclose(cfg.adc2_half_range_v, 0.1):
            raise ValueError("the experiment requires the archived 12-bit ±100 mV training")
        peak = experiment.full_scale_peak_v
        half = peak * 2.0 ** (cfg.adc2_bits - OUTPUT_BITS)
        nominal, pool = _cap_pool(experiment)
        thresholds = np.r_[_codebook(nominal, experiment), peak]
        result = {
            "unit_sigma": experiment.mismatch_sigma_unit,
            "manufacturing_seed": experiment.manufacturing_seed,
            "full_scale_peak_v": peak,
            "fs_hz": experiment.fs_hz,
            "tone_hz": experiment.fin_hz,
            "adc2_bits": cfg.adc2_bits,
            "coarse_codes": COARSE_CODES,
            "adc2_half_window_for_one_export_lsb_v": half,
            "frozen_training_half_window_v": cfg.adc2_half_range_v,
            "training_record_sha256": {
                "summary.json": _sha(report_path),
                "observations.npz": _sha(frozen_path),
            },
            "arms": {},
        }
        traces = {"nominal_coarse_threshold_v": thresholds}
        with np.load(frozen_path, allow_pickle=False) as data:
            np.testing.assert_array_equal(data["nominal_threshold_v"], thresholds[:-1])
            for arm, bridge in zip(ARM_LABELS, (False, True), strict=True):
                design = prior["arms"][arm]["equivalent_weight_design"]
                if design["rank"] != design["columns"]:
                    raise ValueError(f"command extrapolation requires full observable rank: {arm}")
                frozen = FrozenArm(
                    tables[bridge],
                    thresholds,
                    data[arm + "_frozen_gain"],
                    data[arm + "_frozen_equivalent_switch_weight_v"],
                )
                tone, tone_traces = _tone(data, arm, pool, experiment, cfg, frozen, half)
                static, static_traces = _static(pool, experiment, frozen, half)
                result["arms"][arm] = {"tone": tone, "static": static}
                traces.update({f"{arm}_tone_{key}": val for key, val in tone_traces.items()})
                traces.update({f"{arm}_static_{key}": val for key, val in static_traces.items()})
        np.savez_compressed(args.out / f"capture_{index}.npz", **traces)
        records.append((result, traces))
    _plot(args.out, records)
    summary = {
        "schema": "task1_residue_control_v1",
        "scope": "工程候选，理想行为两节点与 ADC2。未接 RTL，未验证模拟。",
        "policy": "中心由输入粗码区间确定；在 c±3 之内用本笔 plan/phase 与冻结权重最小化预测中心余量。",
        "experiments": [item[0] for item in records],
        "source_sha256": {
            name: _sha(ROOT / name)
            for name in (
                Path(__file__).resolve().relative_to(ROOT).as_posix(),
                "task1/experiments/inl_dnl.py",
                "task1/experiments/bridge_lms_comparison.py",
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
    (args.out / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    )


if __name__ == "__main__":
    main()
