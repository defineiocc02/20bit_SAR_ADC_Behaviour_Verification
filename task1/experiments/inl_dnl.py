# =============================================================================
# 文件名     : inl_dnl.py
# 模块名     : inl_dnl
# 版本       : v1.0
# 功能       : Measure the held-out, uniformly weighted code density of frozen NLMS paths.
#
# 设计说明   :
#   * Within a nominal coarse-code interval the two-node residue is affine in the
#   * input for any fixed slice/DEM state. Integrating each intersecting ADC2 bin
#   * avoids confusing finite uniform-ramp counts with missing 20-bit output codes.
# -----------------------------------------------------------------------------
# 变更记录
# -----------------------------------------------------------------------------
# 初始版本，暂无变更记录。
# ===========================================================================

"""Measure the held-out, uniformly weighted code density of frozen NLMS paths.

Within a nominal coarse-code interval the two-node residue is affine in the
input for any fixed slice/DEM state. Integrating each intersecting ADC2 bin
avoids confusing finite uniform-ramp counts with missing 20-bit output codes.
"""

from __future__ import annotations

import argparse
import csv
import json
from dataclasses import dataclass, replace
from importlib.metadata import version
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

from adi_model.capacitors import Capacitors, ExperimentConfig, cap_pool
from adi_model.codes import OUTPUT_BITS, to_unsigned
from adi_model.hashing import sha256_file
from task1.experiments.adctoolbox_calibration import (
    CalibrationConfig,
    decode_switches,
    fixed_ab_flags,
    fixed_switch_bits,
    group_flags,
    residue_voltage,
)
from task1.experiments.bridge_lms_comparison import ARM_LABELS, Q32, _bridge_bits, _plan, _state
from task1.experiments.run_effects import BLUE, GRAY, ROOT
from task1.model.codebook import codebook

# 位宽从 adi_model.codes 导入后在此重导出：下游 adc2_dither_study 与
# task2/eight/experiment 历史上都从这里取 OUTPUT_BITS，改名成本高于收益。
PLOT_BITS = 14
BRIDGE_SEEDS = (
    (0x4C494E4C, 0x444E4C31),
    (0x4C494E4D, 0x444E4C32),
    (0x4C494E4E, 0x444E4C33),
    (0x4C494E4F, 0x444E4C34),
)
CHUNK_COARSE = 12


# =========================================================================
# 1. 函数组：RampStates / Intervals / BinMass
# =========================================================================
# -------------------------------------------------------------------------
# Plan and frozen horizontal/vertical phases independent of the ramp input.
# A batch of affine residues and frozen digital reconstruction parameters.
# Individual output-code contributions and residue/encoder range accounting.
# ------------------------------------------------------------------------
@dataclass(frozen=True)
class RampStates:
    """Plan and frozen horizontal/vertical phases independent of the ramp input."""

    plan: np.ndarray
    horizontal: np.ndarray
    vertical: np.ndarray


# ------------------------------------------------------------------------
@dataclass(frozen=True)
class Intervals:
    """A batch of affine residues and frozen digital reconstruction parameters."""

    low_v: np.ndarray
    high_v: np.ndarray
    residue_low_v: np.ndarray
    residue_high_v: np.ndarray
    digital_base_v: np.ndarray
    digital_gain: np.ndarray

    @property
    def slope(self) -> np.ndarray:
        """Input gain of the physical affine residue within each interval."""
        return (self.residue_high_v - self.residue_low_v) / (self.high_v - self.low_v)


# ------------------------------------------------------------------------
@dataclass(frozen=True)
class BinMass:
    """Individual output-code contributions and residue/encoder range accounting."""

    code: np.ndarray
    width_v: np.ndarray
    adc2_below_v: float
    adc2_above_v: float
    output_below_v: float
    output_above_v: float


# =========================================================================
# 2. 函数组：_span_indices / _output_codes
# =========================================================================
# ------------------------------------------------------------------------
def _span_indices(start: np.ndarray, stop: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    count = np.maximum(stop - start, 0)
    row, offset = np.nonzero(np.arange(count.max(initial=0))[None, :] < count[:, None])
    return row, start[row] + offset


# ------------------------------------------------------------------------
def _output_codes(output_v: np.ndarray, peak_v: float) -> np.ndarray:
    """把输出电压换成无符号 20 位码，向下取整。

    实现收在 ``adi_model.codes.to_unsigned``；这里保留函数名是因为本模块与
    下游多处按这个名字引用。默认不抛越界，调用方自己 clip——旧行为如此。
    """
    return to_unsigned(output_v, peak_v, bits=OUTPUT_BITS)


# =========================================================================
# 3. 函数组：_first_fine_code / code_mass / inl_linearity
# =========================================================================
# -------------------------------------------------------------------------
# Invert a reconstruction code boundary using the same floating-point tie rule.
# ------------------------------------------------------------------------
def _first_fine_code(
    export_code: np.ndarray,
    base_v: np.ndarray,
    gain: np.ndarray,
    adc2_bits: int,
    half_range_v: float,
    peak_v: float,
) -> np.ndarray:
    """Invert a reconstruction code boundary using the same floating-point tie rule."""
    export_step = 2 * peak_v / (1 << OUTPUT_BITS)
    adc2_step = 2 * half_range_v / (1 << adc2_bits)
    fine = np.ceil(
        (export_code * export_step * gain - base_v + half_range_v) / adc2_step - 0.5
    ).astype(int)
    for offset in (-1, 0):
        recon = (base_v + decode_switches(fine + offset, adc2_bits, half_range_v)) / gain - peak_v
        decoded = _output_codes(recon, peak_v)
        if offset == -1:
            fine -= decoded >= export_code
        else:
            fine += decoded < export_code
    return fine


# ------------------------------------------------------------------------
def code_mass(
    intervals: Intervals,
    adc2_bits: int,
    half_range_v: float,
    peak_v: float,
    *,
    aggregate: bool | None = None,
) -> BinMass:
    """把每笔仿射余量区间摊到 ADC2 码仓，返回各仓落点数与两侧越界量。"""
    slope = intervals.slope
    r_low, r_high = intervals.residue_low_v, intervals.residue_high_v
    if np.any(slope <= 0) or np.any(intervals.digital_gain <= 0):
        raise ValueError("each affine residue and reconstruction must have positive gain")
    below = np.maximum(0.0, np.minimum(r_high, -half_range_v) - r_low) / slope
    above = np.maximum(0.0, r_high - np.maximum(r_low, half_range_v)) / slope
    low = np.maximum(r_low, -half_range_v)
    high = np.minimum(r_high, half_range_v)
    adc2_count = 1 << adc2_bits
    adc2_step = 2 * half_range_v / adc2_count
    start = np.clip(np.floor((low + half_range_v) / adc2_step), 0, adc2_count - 1).astype(int)
    stop = np.clip(np.ceil((high + half_range_v) / adc2_step), 0, adc2_count).astype(int)
    stop = np.where(high > low, stop, start)
    if aggregate is None:
        aggregate = adc2_step / intervals.digital_gain.min() < 2 * peak_v / (1 << OUTPUT_BITS)

    if aggregate:
        # When ADC2 is finer than the export grid, integrate all adjacent ADC2
        # bins mapping to one export code as a single connected input interval.
        base, gain = intervals.digital_base_v, intervals.digital_gain
        first_out = _output_codes(
            (base + decode_switches(start, adc2_bits, half_range_v)) / gain - peak_v, peak_v
        )
        stop_out = (
            _output_codes(
                (base + decode_switches(stop - 1, adc2_bits, half_range_v)) / gain - peak_v, peak_v
            )
            + 1
        )
        stop_out = np.where(stop > start, stop_out, first_out)
        edge_row, edge_code = _span_indices(first_out, stop_out + (stop > start))
        fine_edge = _first_fine_code(
            edge_code, base[edge_row], gain[edge_row], adc2_bits, half_range_v, peak_v
        )
        use_edge = edge_code < stop_out[edge_row]
        row, raw_code = edge_row[use_edge], edge_code[use_edge]
        first_fine = np.maximum(start[row], fine_edge[use_edge])
        stop_fine = np.minimum(stop[row], fine_edge[1:][use_edge[:-1]])
    else:
        row, first_fine = _span_indices(start, stop)
        stop_fine = first_fine + 1
        out_v = (
            intervals.digital_base_v[row] + decode_switches(first_fine, adc2_bits, half_range_v)
        ) / intervals.digital_gain[row] - peak_v
        raw_code = _output_codes(out_v, peak_v)

    bin_low = -half_range_v + first_fine * adc2_step
    bin_high = -half_range_v + stop_fine * adc2_step
    overlap = np.minimum(high[row], bin_high) - np.maximum(low[row], bin_low)
    width = np.maximum(overlap, 0.0) / slope[row]
    use = width > 0
    width, raw_code = width[use], raw_code[use]
    output_count = 1 << OUTPUT_BITS
    return BinMass(
        np.clip(raw_code, 0, output_count - 1),
        width,
        float(below.sum()),
        float(above.sum()),
        float(width[raw_code < 0].sum()),
        float(width[raw_code >= output_count].sum()),
    )


# ------------------------------------------------------------------------
def inl_linearity(width: np.ndarray) -> tuple[np.ndarray, np.ndarray, dict]:
    """由码宽序列算 DNL 与 INL（首末码不入均值），并给出端点拟合残差等统计量。"""
    interior = width[1:-1]
    mean_width = float(interior.mean())
    if mean_width <= 0:
        raise ValueError("ramp has no interior code mass")
    dnl = interior / mean_width - 1
    inl = np.r_[0.0, np.cumsum(dnl)]
    transition = np.arange(inl.size)
    fit = inl - np.polyval(np.polyfit(transition, inl, 1), transition)
    missing = int(np.count_nonzero(interior <= 1e-15))
    return (
        dnl,
        inl,
        {
            "interior_codes": int(interior.size),
            "zero_width_interior_codes": missing,
            "zero_width_interior_percent": 100 * missing / interior.size,
            "mean_interior_code_width_v": mean_width,
            "dnl_min_lsb": float(dnl.min()),
            "dnl_max_lsb": float(dnl.max()),
            "endpoint_inl_min_lsb": float(inl.min()),
            "endpoint_inl_max_lsb": float(inl.max()),
            "endpoint_inl_peak_lsb": float(np.abs(inl).max()),
            "best_fit_inl_peak_lsb": float(np.abs(fit).max()),
        },
    )


# =========================================================================
# 4. 函数组：_fold / _build_intervals / _values
# =========================================================================
# ------------------------------------------------------------------------
def _fold(width: np.ndarray, bits: int) -> np.ndarray:
    return width.reshape((1 << bits, -1)).sum(axis=1)


# ------------------------------------------------------------------------
def _build_intervals(
    pool: Capacitors,
    experiment: ExperimentConfig,
    thresholds: np.ndarray,
    states: RampStates,
    frozen_gain: np.ndarray,
    frozen_weight: np.ndarray,
    coarse: np.ndarray,
    *,
    bridge: bool,
) -> Intervals:
    peak = experiment.full_scale_peak_v
    n = len(states.plan)
    codes = np.repeat(coarse, n).astype(np.uint16)
    plan = np.tile(states.plan, (len(coarse), 1))
    phase = np.tile(states.horizontal, len(coarse)), np.tile(states.vertical, len(coarse))
    state, q32 = _state(codes, plan, phase, bridge=bridge)
    lower, upper = thresholds[codes], thresholds[codes + 1]
    masks = state.main, state.sub
    r_low = residue_voltage(pool, lower, plan, masks, experiment)
    r_high = residue_voltage(pool, upper, plan, masks, experiment)
    # Check a direct two-node evaluation at each coarse-bin center for one state.
    k = slice(None, None, n)
    midpoint = (lower[k] + upper[k]) * 0.5
    r_mid = residue_voltage(pool, midpoint, plan[k], (masks[0][k], masks[1][k]), experiment)
    if np.max(np.abs(r_mid - (r_low[k] + r_high[k]) * 0.5)) > 2e-13:
        raise ValueError("the independent two-node midpoint check rejected affine interpolation")
    switch_bits = _bridge_bits(state) if bridge else fixed_switch_bits(state)
    gain_flags = group_flags(state.plan) if bridge else fixed_ab_flags(state.plan)
    return Intervals(
        lower,
        upper,
        r_low,
        r_high,
        lower + peak + q32 * (peak / Q32) + switch_bits @ frozen_weight,
        gain_flags @ frozen_gain,
    )


# ------------------------------------------------------------------------
def _values(
    intervals: Intervals, adc2_code: np.ndarray, cfg: CalibrationConfig, peak: float
) -> np.ndarray:
    return (
        intervals.digital_base_v + decode_switches(adc2_code, cfg.adc2_bits, cfg.adc2_half_range_v)
    ) / intervals.digital_gain - peak


# =========================================================================
# 5. 函数组：_ramp_check / _measure / _states
# =========================================================================
# ------------------------------------------------------------------------
def _ramp_check(
    intervals: Intervals, cfg: CalibrationConfig, peak: float, states: int, samples_per_state: int
) -> tuple[np.ndarray, np.ndarray]:
    step = 2 * peak / samples_per_state
    first = np.ceil((intervals.low_v + peak) / step - 0.5).astype(int)
    stop = np.ceil((intervals.high_v + peak) / step - 0.5).astype(int)
    row, column = np.nonzero(
        np.arange((stop - first).max(initial=0))[None, :] < (stop - first)[:, None]
    )
    vin = (first[row] + column + 0.5) * step - peak
    residue = intervals.residue_low_v[row] + intervals.slope[row] * (vin - intervals.low_v[row])
    if np.any(residue < -cfg.adc2_half_range_v) or np.any(residue >= cfg.adc2_half_range_v):
        raise ValueError("sampled ramp exceeds ADC2 range")
    adc2 = np.floor(
        (residue + cfg.adc2_half_range_v) / (2 * cfg.adc2_half_range_v / (1 << cfg.adc2_bits))
    )
    output = (
        intervals.digital_base_v[row] + decode_switches(adc2, cfg.adc2_bits, cfg.adc2_half_range_v)
    ) / intervals.digital_gain[row] - peak
    codes14 = np.clip(_output_codes(output, peak), 0, (1 << OUTPUT_BITS) - 1) >> (
        OUTPUT_BITS - PLOT_BITS
    )
    # ADCToolbox sees a monotonic ramp in Vin, with each voltage repeated in each state.
    order = np.argsort(vin, kind="stable")
    return codes14[order], np.bincount(codes14, minlength=1 << PLOT_BITS) * step / states


# ------------------------------------------------------------------------
def _measure(
    pool: Capacitors,
    experiment: ExperimentConfig,
    cfg: CalibrationConfig,
    thresholds: np.ndarray,
    states: RampStates,
    frozen_gain: np.ndarray,
    frozen_weight: np.ndarray,
    *,
    bridge: bool,
    ramp_samples: int = 0,
) -> tuple[np.ndarray, dict, np.ndarray]:
    peak = experiment.full_scale_peak_v
    adc2_span = cfg.adc2_half_range_v
    adc2_step = 2 * adc2_span / (1 << cfg.adc2_bits)
    n = len(states.plan)
    widths = np.zeros(1 << OUTPUT_BITS)
    limits = {
        "adc2_below_v": 0.0,
        "adc2_above_v": 0.0,
        "output_below_v": 0.0,
        "output_above_v": 0.0,
    }
    residue_min, residue_max = float("inf"), -float("inf")
    left_edges, right_edges, middle_errors = [], [], []
    ramp_codes: list[np.ndarray] = []
    ramp_widths = np.zeros(1 << PLOT_BITS)
    ramp_segments = np.zeros(1 << PLOT_BITS)
    chunk_size = CHUNK_COARSE if adc2_step > 2 * peak / (1 << OUTPUT_BITS) else 6
    for start in range(0, len(thresholds) - 1, chunk_size):
        coarse = np.arange(start, min(start + chunk_size, len(thresholds) - 1))
        iv = _build_intervals(
            pool, experiment, thresholds, states, frozen_gain, frozen_weight, coarse, bridge=bridge
        )
        m = code_mass(iv, cfg.adc2_bits, adc2_span, peak)
        widths += np.bincount(m.code, weights=m.width_v, minlength=widths.size) / n
        for key in limits:
            limits[key] += getattr(m, key) / n
        residue_min = min(residue_min, float(iv.residue_low_v.min()))
        residue_max = max(residue_max, float(iv.residue_high_v.max()))
        lo_q = np.floor((iv.residue_low_v + adc2_span) / adc2_step)
        hi_q = np.floor(np.nextafter((iv.residue_high_v + adc2_span) / adc2_step, -np.inf))
        mid_q = np.floor(((iv.residue_low_v + iv.residue_high_v) * 0.5 + adc2_span) / adc2_step)
        left_edges.append(_values(iv, lo_q, cfg, peak).reshape(-1, n))
        right_edges.append(_values(iv, hi_q, cfg, peak).reshape(-1, n))
        mid_error = _values(iv, mid_q, cfg, peak) - (iv.low_v + iv.high_v) * 0.5
        middle_errors.append(mid_error.reshape(-1, n))
        if ramp_samples:
            rc, rw = _ramp_check(iv, cfg, peak, n, ramp_samples)
            ramp_codes.append(rc)
            ramp_widths += rw
            ramp_segments += np.bincount(
                m.code >> (OUTPUT_BITS - PLOT_BITS), minlength=ramp_segments.size
            )
    accounted_v = widths.sum() + limits["adc2_below_v"] + limits["adc2_above_v"]
    if not np.isclose(accounted_v, 2 * peak, rtol=0, atol=5e-11):
        raise ValueError(f"uniform ramp probability is not conserved: {accounted_v:.12g} V")
    report = {
        "state_count": n,
        "residue_min_v": residue_min,
        "residue_max_v": residue_max,
        "mass_balance_error_v": float(accounted_v - 2 * peak),
        **limits,
    }
    if limits["adc2_below_v"] + limits["adc2_above_v"] > 1e-15:
        report["status"] = "adc2_range_blocked"
        return widths, report, np.asarray(middle_errors[0])[:0]
    report["status"] = "completed"
    left = np.vstack(left_edges)[1:]
    right = np.vstack(right_edges)[:-1]
    jumps = left - right
    export_max = (1 << OUTPUT_BITS) - 1
    code_jumps = np.clip(_output_codes(left, peak), 0, export_max) - np.clip(
        _output_codes(right, peak), 0, export_max
    )
    report["coarse_boundaries_times_states"] = int(jumps.size)
    report["negative_coarse_boundary_jumps"] = int(np.count_nonzero(jumps < -1e-12))
    report["minimum_coarse_boundary_jump_v"] = float(jumps.min())
    report["negative_export_code_boundary_jumps"] = int(np.count_nonzero(code_jumps < 0))
    report["minimum_export_code_boundary_jump_codes"] = int(code_jumps.min())
    midpoint_error = np.vstack(middle_errors)
    x = (thresholds[:-1] + thresholds[1:]) * 0.5
    mean_error = midpoint_error.mean(axis=1)
    residual = mean_error - np.polyval(np.polyfit(x, mean_error, 1), x)
    report["midpoint_mean_fit_residual_peak_v"] = float(np.abs(residual).max())
    report["midpoint_mean_fit_residual_rms_v"] = float(np.sqrt(np.mean(residual**2)))
    report["midpoint_state_spread_median_rms_v"] = float(np.median(midpoint_error.std(axis=1)))
    if ramp_samples:
        from adctoolbox import analyze_inl_from_ramp

        integral = _fold(widths, PLOT_BITS)
        error = np.abs(integral - ramp_widths)
        # A midpoint grid miscounts a connected input interval by at most one step.
        bound = (2 * peak / ramp_samples / n) * ramp_segments
        if np.any(error > bound + 2e-13):
            raise ValueError("direct uniform-ramp code density exceeds interval integration bound")
        sampled_codes = np.concatenate(ramp_codes)
        if sampled_codes.size != ramp_samples * n:
            raise ValueError("sampled ramp lost or duplicated input points")
        check = analyze_inl_from_ramp(sampled_codes, num_bits=PLOT_BITS, create_plot=False)
        ramp_dnl, ramp_inl, _ = inl_linearity(ramp_widths)
        np.testing.assert_allclose(check["dnl"], ramp_dnl, rtol=0, atol=2e-11)
        np.testing.assert_allclose(check["inl"], ramp_inl, rtol=0, atol=2e-11)
        report["uniform_ramp_crosscheck"] = {
            "samples_per_state": ramp_samples,
            "samples": ramp_samples * n,
            "max_integrated_vs_sampled_code_width_difference_v": float(error.max()),
            "max_difference_over_per_code_ramp_step_bound": float(
                np.max(error / np.maximum(bound, 2e-13))
            ),
            "adctoolbox_version": version("adctoolbox"),
            "adctoolbox_dnl_array_matches_direct_sampled_histogram": True,
            "adctoolbox_endpoint_inl_array_matches_direct_sampled_histogram": True,
        }
    return widths, report, midpoint_error


# ------------------------------------------------------------------------
def _states(experiment: ExperimentConfig, count: int, *, bridge: bool) -> list[RampStates]:
    if not bridge:
        plan, phase = _plan(2, experiment, bridge=False)
        return [RampStates(plan, *phase)]
    sets = []
    for schedule_seed, dem_seed in BRIDGE_SEEDS:
        plan, phase = _plan(
            count, replace(experiment, schedule_seed=schedule_seed, dem_seed=dem_seed), bridge=True
        )
        sets.append(RampStates(plan, *phase))
    return sets


# =========================================================================
# 6. 函数组：_source_check / _run_pair / _plot
# =========================================================================
# ------------------------------------------------------------------------
def _source_check(training: Path, training_report: dict) -> None:
    if training_report["status"] != "completed":
        raise ValueError(f"training did not complete: {training}")
    for name, digest in training_report["source_sha256"].items():
        if sha256_file(ROOT / name) != digest:
            raise ValueError(f"training source digest changed: {name}")


# ------------------------------------------------------------------------
def _run_pair(
    training: Path, out: Path, count: int, *, archive_trace: bool = True
) -> tuple[dict, dict[str, np.ndarray]]:
    report_path = training / "summary.json"
    frozen_path = training / "observations.npz"
    training_report = json.loads(report_path.read_text())
    _source_check(training, training_report)
    experiment = ExperimentConfig(**training_report["experiment"])
    cfg = CalibrationConfig(**training_report["calibration"])
    nominal, pool = cap_pool(experiment)
    if any(np.min(getattr(pool, key)) <= 0 for key in ("main", "sub", "bridge")):
        raise ValueError("the fixed physical mismatch pool contains nonpositive capacitance")
    nominal_lower = codebook(nominal, experiment)
    peak = experiment.full_scale_peak_v
    if not np.isclose(nominal_lower[0], -peak, rtol=0, atol=1e-13):
        raise ValueError("the nominal coarse codebook does not begin at negative full scale")
    thresholds = np.r_[-peak, nominal_lower[1:], peak]
    arrays = {"nominal_coarse_threshold_v": thresholds}
    arms: dict = {}
    with np.load(frozen_path, allow_pickle=False) as frozen:
        np.testing.assert_allclose(frozen["nominal_threshold_v"], nominal_lower, rtol=0, atol=1e-13)
        for name, bridge in zip(ARM_LABELS, (False, True), strict=True):
            gain = frozen[f"{name}_frozen_gain"]
            weight = frozen[f"{name}_frozen_equivalent_switch_weight_v"]
            sets = _states(experiment, count, bridge=bridge)
            results = [
                _measure(pool, experiment, cfg, thresholds, states, gain, weight, bridge=bridge)
                for states in sets
            ]
            widths = np.mean([value[0] for value in results], axis=0)
            arms[name] = arm = {
                "groups": [stats for _, stats, _ in results],
                "adc2_out_of_range_input_width_v": float(
                    np.mean(
                        [stats["adc2_below_v"] + stats["adc2_above_v"] for _, stats, _ in results]
                    )
                ),
                "encoder_clip_input_width_v": float(
                    np.mean(
                        [
                            stats["output_below_v"] + stats["output_above_v"]
                            for _, stats, _ in results
                        ]
                    )
                ),
                "residue_min_v": min(stats["residue_min_v"] for _, stats, _ in results),
                "residue_max_v": max(stats["residue_max_v"] for _, stats, _ in results),
                "effective_states": sum(len(group.plan) for group in sets),
                "status": "completed"
                if all(stats["status"] == "completed" for _, stats, _ in results)
                else "adc2_range_blocked",
            }
            arrays[f"{name}_uniform_input_width_v_20b"] = widths
            arrays[f"{name}_state_plan"] = np.concatenate([s.plan for s in sets])
            arrays[f"{name}_state_horizontal"] = np.concatenate([s.horizontal for s in sets])
            arrays[f"{name}_state_vertical"] = np.concatenate([s.vertical for s in sets])
            if arm["status"] == "completed":
                arm["linearity"] = {}
                for bits in (PLOT_BITS, 16, 18, OUTPUT_BITS):
                    dnl, inl, stats = inl_linearity(_fold(widths, bits))
                    arm["linearity"][str(bits)] = stats
                    if bits == PLOT_BITS:
                        arrays[f"{name}_dnl_{bits}b"] = dnl
                        arrays[f"{name}_endpoint_inl_{bits}b"] = inl
                arm["independent_groups_14b"] = [
                    inl_linearity(_fold(w, PLOT_BITS))[2] for w, _, _ in results
                ]
                arrays[f"{name}_coarse_midpoint_error_v"] = np.concatenate(
                    [err for _, _, err in results], axis=1
                )
                if archive_trace:
                    s = sets[0]
                    audit_states = RampStates(s.plan[:2], s.horizontal[:2], s.vertical[:2])
                    _, audit, _ = _measure(
                        pool,
                        experiment,
                        cfg,
                        thresholds,
                        audit_states,
                        gain,
                        weight,
                        bridge=bridge,
                        ramp_samples=1 << 18,
                    )
                    arm["uniform_ramp_crosscheck"] = audit["uniform_ramp_crosscheck"]
    metadata = {
        "manufacturing_seed": experiment.manufacturing_seed,
        "sigma": experiment.mismatch_sigma_unit,
        "input_full_scale_v": 2 * peak,
        "sample_rate_hz": experiment.fs_hz,
        "adc2_bits": cfg.adc2_bits,
        "adc2_half_range_v": cfg.adc2_half_range_v,
        "adc2_input_equivalent_step_v": 2 * cfg.adc2_half_range_v / (1 << cfg.adc2_bits),
        "output_quantizer_bits": OUTPUT_BITS,
        "output_ideal_lsb_v": 2 * peak / (1 << OUTPUT_BITS),
        "figure_aggregation_bits": PLOT_BITS,
        "training_summary": str(report_path.resolve().relative_to(ROOT))
        if report_path.resolve().is_relative_to(ROOT)
        else report_path.name,
        "frozen_weight_artifact_sha256": sha256_file(frozen_path),
        "independent_bridge_state_seeds": BRIDGE_SEEDS,
        "bridge_states_per_group": count,
        "arms": arms,
    }
    if archive_trace:
        np.savez_compressed(
            out / f"linearity_sigma_{100 * experiment.mismatch_sigma_unit:g}pct.npz", **arrays
        )
    return metadata, arrays


# ------------------------------------------------------------------------
def _plot(out: Path, experiments: list[tuple[dict, dict[str, np.ndarray]]]) -> None:
    fig, axs = plt.subplots(
        len(experiments), 2, figsize=(11, 7.2), squeeze=False, layout="constrained"
    )
    adc2_bits = experiments[0][0]["adc2_bits"]
    fig.suptitle(
        f"Static linearity | ADC2 {adc2_bits}-bit, frozen NLMS, 14-bit grouped output",
        fontweight="bold",
    )
    for row, (meta, traces) in enumerate(experiments):
        for index, (name, color) in enumerate(zip(ARM_LABELS, (GRAY, BLUE), strict=True)):
            if meta["arms"][name]["status"] != "completed":
                continue
            dnl = traces[f"{name}_dnl_{PLOT_BITS}b"]
            inl = traces[f"{name}_endpoint_inl_{PLOT_BITS}b"]
            axs[row, 0].plot(
                np.arange(1, len(dnl) + 1), dnl, lw=0.65, color=color, alpha=0.85, zorder=index
            )
            axs[row, 1].plot(
                np.arange(1, len(inl) + 1), inl, lw=0.85, color=color, label=ARM_LABELS[name]
            )
        sigma = f"{100 * meta['sigma']:g}% fixed mismatch"
        axs[row, 0].set(
            title=f"{sigma} · DNL", ylabel="DNL (14-bit LSB)", xlabel="Grouped output code"
        )
        axs[row, 1].set(
            title=f"{sigma} · endpoint INL",
            ylabel="INL (14-bit LSB)",
            xlabel="Grouped output transition",
        )
        axs[row, 1].legend(fontsize=8, loc="best")
        for ax in axs[row]:
            ax.grid(alpha=0.22)
            ax.axhline(0, color="black", lw=0.55, alpha=0.55)
            ax.set_xlim(1, (1 << PLOT_BITS) - 1)
    fig.savefig(out / "linearity.png", dpi=165, facecolor="white")
    plt.close(fig)


# =========================================================================
# 7. 函数组：_envelope / _plot_full_codes / _seed_row
# =========================================================================
# ------------------------------------------------------------------------
def _envelope(values: np.ndarray, block: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    samples = np.pad(values, (0, (-len(values)) % block), constant_values=np.nan)
    chunks = samples.reshape(-1, block)
    center = (np.arange(len(chunks)) * block + (block + 1) * 0.5) / 1e6
    return center, np.nanmin(chunks, axis=1), np.nanmax(chunks, axis=1)


# ------------------------------------------------------------------------
def _plot_full_codes(out: Path, experiments: list[tuple[dict, dict[str, np.ndarray]]]) -> None:
    fig, axs = plt.subplots(
        len(experiments), 2, figsize=(11, 7.2), squeeze=False, layout="constrained"
    )
    adc2_bits = experiments[0][0]["adc2_bits"]
    fig.suptitle(
        f"20-bit export density | ideal ADC2 {adc2_bits}-bit, frozen NLMS",
        fontweight="bold",
    )
    for row, (meta, traces) in enumerate(experiments):
        for name, color in zip(ARM_LABELS, (GRAY, BLUE), strict=True):
            if meta["arms"][name]["status"] != "completed":
                continue
            dnl, inl, _ = inl_linearity(traces[f"{name}_uniform_input_width_v_20b"])
            for ax, values in zip(axs[row], (dnl, inl), strict=True):
                x, low, high = _envelope(values, 512)
                ax.fill_between(x, low, high, color=color, alpha=0.55, label=ARM_LABELS[name])
        sigma = f"{100 * meta['sigma']:g}% fixed mismatch"
        for ax, metric in zip(axs[row], ("DNL", "Endpoint INL"), strict=True):
            ax.set(
                title=f"{sigma} · {metric}",
                ylabel=f"{metric} (20-bit LSB)",
                xlabel="Export code / transition (millions)",
                xlim=(0, (1 << OUTPUT_BITS) / 1e6),
            )
            ax.grid(alpha=0.22)
        axs[row, 1].legend(fontsize=8, loc="best")
    fig.savefig(out / "linearity_20bit.png", dpi=165, facecolor="white")
    plt.close(fig)


# ------------------------------------------------------------------------
def _seed_row(meta: dict) -> dict:
    row = {
        "unit_sigma_pct": 100 * meta["sigma"],
        "manufacturing_seed": meta["manufacturing_seed"],
        "adc2_bits": meta["adc2_bits"],
    }
    for mode, arm in zip(("fixed", "bridge"), meta["arms"].values(), strict=True):
        d = arm.get("linearity", {}).get(str(PLOT_BITS), {})
        d20 = arm.get("linearity", {}).get(str(OUTPUT_BITS), {})
        row.update(
            {
                f"{mode}_status": arm["status"],
                f"{mode}_inl_peak_lsb14": d.get("endpoint_inl_peak_lsb"),
                f"{mode}_dnl_peak_lsb14": max(abs(d["dnl_min_lsb"]), abs(d["dnl_max_lsb"]))
                if d
                else None,
                f"{mode}_unoccupied_20b": d20.get("zero_width_interior_codes"),
                f"{mode}_adc2_out_of_range_width_v": arm["adc2_out_of_range_input_width_v"],
            }
        )
    return row


# =========================================================================
# 8. 函数组：_seed_counts / main
# =========================================================================
# -------------------------------------------------------------------------
# Produce code-density metrics, plots and exact integrated histograms.
# ------------------------------------------------------------------------
def _seed_counts(rows: list[dict]) -> list[dict]:
    counts = []
    for sigma, bits in sorted({(x["unit_sigma_pct"], x["adc2_bits"]) for x in rows}):
        group = [x for x in rows if (x["unit_sigma_pct"], x["adc2_bits"]) == (sigma, bits)]
        pairs = [x for x in group if x["fixed_status"] == x["bridge_status"] == "completed"]
        counts.append(
            {
                "unit_sigma_pct": sigma,
                "adc2_bits": bits,
                "total_pairs": len(group),
                "valid_pairs": len(pairs),
                "inl_improved_pairs": sum(
                    x["bridge_inl_peak_lsb14"] < x["fixed_inl_peak_lsb14"] for x in pairs
                ),
                "dnl_improved_pairs": sum(
                    x["bridge_dnl_peak_lsb14"] < x["fixed_dnl_peak_lsb14"] for x in pairs
                ),
                "unoccupied_20b_reduced_pairs": sum(
                    x["bridge_unoccupied_20b"] < x["fixed_unoccupied_20b"] for x in pairs
                ),
            }
        )
    return counts


# ------------------------------------------------------------------------
def main() -> None:
    """Produce code-density metrics, plots and exact integrated histograms."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--training", nargs="+", required=True, type=Path)
    parser.add_argument("--states-per-group", type=int, default=128)
    parser.add_argument(
        "--seed-sweep",
        action="store_true",
        help="write scalar paired results without large per-seed traces",
    )
    args = parser.parse_args()
    if args.states_per_group < 2 or args.states_per_group % 2:
        parser.error("--states-per-group must be an even integer >= 2")
    args.out.mkdir(parents=True, exist_ok=True)
    results = []
    for training in args.training:
        meta, traces = _run_pair(
            training, args.out, args.states_per_group, archive_trace=not args.seed_sweep
        )
        for arm, values in meta["arms"].items():
            print(
                f"{meta['sigma']:.0%} seed {meta['manufacturing_seed']} {arm}: {values['status']} {values.get('linearity', {}).get(str(PLOT_BITS), {})}",
                flush=True,
            )
        results.append((meta, {} if args.seed_sweep else traces))
    if not args.seed_sweep:
        _plot(args.out, results)
        if all(
            meta["adc2_input_equivalent_step_v"] < meta["output_ideal_lsb_v"] for meta, _ in results
        ):
            _plot_full_codes(args.out, results)
    summary = {
        "schema": "task1_frozen_nlms_static_code_density_v1",
        "method": "exact affine interval integration over uniform Vin and equally weighted frozen states; endpoint bins excluded, interior mean width; 20-bit export quantized from calibrated voltage",
        "scope": "nominal coarse decisions, fixed physical CDAC pool, ideal residue and ideal ADC2, independent ramp and state seeds, frozen per-arm NLMS; no thermal noise, no timing skew",
        "source_sha256": {
            str(Path(__file__).resolve().relative_to(ROOT)): sha256_file(Path(__file__)),
            "task1/experiments/bridge_lms_comparison.py": sha256_file(
                ROOT / "task1/experiments/bridge_lms_comparison.py"
            ),
            "task1/experiments/adctoolbox_calibration.py": sha256_file(
                ROOT / "task1/experiments/adctoolbox_calibration.py"
            ),
            "task1/experiments/run_effects.py": sha256_file(
                ROOT / "task1/experiments/run_effects.py"
            ),
            "task1/model/fig21_bridge.py": sha256_file(ROOT / "task1/model/fig21_bridge.py"),
        },
        "experiments": [meta for meta, _ in results],
    }
    if args.seed_sweep:
        rows = [_seed_row(meta) for meta, _ in results]
        summary["seed_counts"] = _seed_counts(rows)
        with (args.out / "seed_sweep.csv").open("w", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=list(rows[0]), lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)
        report_name = "seed_sweep.json"
    else:
        report_name = "summary.json"
    (args.out / report_name).write_text(json.dumps(summary, indent=2, allow_nan=False) + "\n")


if __name__ == "__main__":
    main()
