# =============================================================================
# 文件名     : uncalibrated_linearity.py
# 模块名     : uncalibrated_linearity
# 版本       : v1.0
# 功能       : Measure the independent nominal-reconstruction path with DEM and LMS disabled.
#
# 设计说明   :
#   * 承担 行为级实验与图件生成 中的一环；输入输出的物理口径与单位以同目录 SPEC/README 为准。
# -----------------------------------------------------------------------------
# 变更记录
# -----------------------------------------------------------------------------
# 初始版本，暂无变更记录。
# ===========================================================================

"""Measure the independent nominal-reconstruction path with DEM and LMS disabled."""

from __future__ import annotations

import argparse
import json
from dataclasses import replace
from importlib.metadata import version
from pathlib import Path

import numpy as np

from adi_model.capacitors import Capacitors, ExperimentConfig, cap_pool
from adi_model.hashing import sha256_file
from task1.experiments.adctoolbox_calibration import (
    CalibrationConfig,
    _capture,
    _nominal_reconstruction,
    calibration_metrics,
    decode_switches,
    fixed_ab_plan,
    quantize,
    range_report,
    residue_voltage,
)
from task1.experiments.inl_dnl import (
    OUTPUT_BITS,
    Intervals,
    _output_codes,
    code_mass,
    inl_linearity,
)
from task1.experiments.run_effects import ROOT
from task1.model.codebook import codebook, map_record

ARM = "fixed_ab_no_dem_no_lms"
STATE_COUNT = 2
AUDIT_COARSE_CODES = (0, 1, 255, 256, 511)
AUDIT_SAMPLES_PER_INTERVAL = 1 << 15
SOURCE_PATHS = (
    "task1/experiments/uncalibrated_linearity.py",
    "task1/experiments/adctoolbox_calibration.py",
    "task1/experiments/inl_dnl.py",
    "task1/experiments/run_effects.py",
    "task1/model/fig11_dem.py",
    "src/adi_model/charge_ref.py",
)


# =========================================================================
# 1. 函数组：_uncalibrated_intervals / _check_direct_ramp / _tone
# =========================================================================
# -------------------------------------------------------------------------
# Solve both physical residue endpoints for every fixed A/B coarse interval.
# Compare the analytical widths with fresh two-node samples at selected coarse bins.
# ------------------------------------------------------------------------
def _uncalibrated_intervals(
    pool: Capacitors, experiment: ExperimentConfig, thresholds: np.ndarray
) -> tuple[Intervals, np.ndarray, tuple[np.ndarray, np.ndarray]]:
    """Solve both physical residue endpoints for every fixed A/B coarse interval."""
    count = STATE_COUNT * (len(thresholds) - 1)
    coarse = np.repeat(np.arange(len(thresholds) - 1, dtype=np.uint16), STATE_COUNT)
    plan = fixed_ab_plan(count)
    phase = np.zeros(count, dtype=np.uint8)
    masks = map_record(coarse, (phase, phase), dem_enable=False)
    low_v, high_v = thresholds[coarse], thresholds[coarse + 1]
    peak = experiment.full_scale_peak_v
    intervals = Intervals(
        low_v,
        high_v,
        residue_voltage(pool, low_v, plan, masks, experiment),
        residue_voltage(pool, high_v, plan, masks, experiment),
        low_v + peak,
        np.ones(count),
    )
    return intervals, plan, masks


# ------------------------------------------------------------------------
def _check_direct_ramp(
    intervals: Intervals,
    plan: np.ndarray,
    masks: tuple[np.ndarray, np.ndarray],
    pool: Capacitors,
    experiment: ExperimentConfig,
    cfg: CalibrationConfig,
) -> dict:
    """Compare the analytical widths with fresh two-node samples at selected coarse bins.

    Args:
        intervals: 输入区间与对应残差模型。
        plan: 每笔有序物理切片号。
        masks: 与片号同槽的实际转换开关。
        pool: 固定制造池，单位 F。
        experiment: 输入幅度与制造配置。
        cfg: ADC2 位数和输入折算窗口。

    直接斜坡检查与区间积分分别构造，不能只比较两个来自同一 mapper 的数组。
    ADC2 过载与最终数字输出裁剪分别记录，避免把端点限制归因于电容失配。
    """
    peak = experiment.full_scale_peak_v
    samples = AUDIT_SAMPLES_PER_INTERVAL
    ramp_codes = np.arange(samples) + 0.5
    max_residue_error = max_width_error = max_relative_bound = 0.0
    for coarse in AUDIT_COARSE_CODES:
        for state in range(STATE_COUNT):
            index = coarse * STATE_COUNT + state
            interval = Intervals(
                intervals.low_v[index : index + 1],
                intervals.high_v[index : index + 1],
                intervals.residue_low_v[index : index + 1],
                intervals.residue_high_v[index : index + 1],
                intervals.digital_base_v[index : index + 1],
                intervals.digital_gain[index : index + 1],
            )
            step = float((interval.high_v - interval.low_v)[0] / samples)
            vin = interval.low_v[0] + ramp_codes * step
            residue = residue_voltage(
                pool,
                vin,
                np.repeat(plan[index : index + 1], samples, axis=0),
                tuple(np.repeat(mask[index : index + 1], samples, axis=0) for mask in masks),
                experiment,
            )
            affine = interval.residue_low_v[0] + interval.slope[0] * (vin - interval.low_v[0])
            max_residue_error = max(max_residue_error, float(np.max(np.abs(residue - affine))))
            adc2_code = quantize(residue, cfg.adc2_bits, cfg.adc2_half_range_v)
            output = interval.low_v[0] + decode_switches(
                adc2_code, cfg.adc2_bits, cfg.adc2_half_range_v
            )
            codes = np.clip(_output_codes(output, peak), 0, (1 << OUTPUT_BITS) - 1)
            mass = code_mass(interval, cfg.adc2_bits, cfg.adc2_half_range_v, peak)
            all_codes, counts = np.unique(codes, return_counts=True)
            audit_codes = np.union1d(all_codes, mass.code)
            exact = np.bincount(
                np.searchsorted(audit_codes, mass.code), mass.width_v, minlength=len(audit_codes)
            )
            sampled = np.bincount(
                np.searchsorted(audit_codes, all_codes),
                counts * step,
                minlength=len(audit_codes),
            )
            # For one monotone affine interval, each occupied output code is one connected span.
            error = float(np.max(np.abs(exact - sampled)))
            if error > step + 1e-12:
                raise ValueError("direct two-node ramp disagrees with the 20-bit width integral")
            max_width_error = max(max_width_error, error)
            max_relative_bound = max(max_relative_bound, error / step)
    if max_residue_error > 2e-13:
        raise ValueError("fresh two-node residue samples rejected the affine interval model")
    return {
        "coarse_codes": AUDIT_COARSE_CODES,
        "states": STATE_COUNT,
        "samples_per_coarse_interval_and_state": samples,
        "total_samples": samples * len(AUDIT_COARSE_CODES) * STATE_COUNT,
        "maximum_affine_residue_error_v": max_residue_error,
        "maximum_per_code_width_error_v": max_width_error,
        "maximum_width_error_over_local_ramp_step": max_relative_bound,
    }


# ------------------------------------------------------------------------
def _tone(
    pool: Capacitors,
    experiment: ExperimentConfig,
    cfg: CalibrationConfig,
    nominal_lower: np.ndarray,
) -> tuple[dict, dict[str, np.ndarray]]:
    peak = experiment.full_scale_peak_v
    vin = experiment.tone_peak_v * np.sin(
        2 * np.pi * experiment.tone_bin / experiment.samples * np.arange(experiment.samples)
        + cfg.evaluation_tone_phase_rad
    )
    phase = np.zeros(len(vin), dtype=np.uint8)
    capture = _capture(
        pool,
        vin,
        fixed_ab_plan(len(vin)),
        (phase, phase),
        nominal_lower,
        experiment,
        dem_enable=False,
    )
    headroom = range_report(capture.residue_v, cfg.adc2_half_range_v)
    raw = {
        "tone_reference_v": vin,
        "tone_coarse_code": capture.codes,
        "tone_state_plan": capture.plan,
        "tone_main_mask": capture.main,
        "tone_sub_mask": capture.sub,
        "tone_residue_v": capture.residue_v,
    }
    report = {
        "samples": experiment.samples,
        "sample_rate_hz": experiment.fs_hz,
        "frequency_hz": experiment.fin_hz,
        "amplitude_v_peak": experiment.tone_peak_v,
        "phase_rad": cfg.evaluation_tone_phase_rad,
        "adc2_range": headroom,
    }
    if headroom["below_range"] or headroom["at_or_above_range"]:
        return {**report, "status": "adc2_range_blocked"}, raw
    observation = capture.quantized(cfg)
    output = _nominal_reconstruction(observation, nominal_lower, cfg)
    export_max = (1 << OUTPUT_BITS) - 1
    code = _output_codes(output, peak)
    exported = np.clip(code, 0, export_max)
    export_lsb = 2 * peak / (1 << OUTPUT_BITS)
    export_v = (exported + 0.5) * export_lsb - peak
    raw.update(
        tone_adc2_code=observation.adc2_code,
        tone_nominal_reconstruction_v=output,
        tone_export_code=exported,
    )
    return (
        {
            **report,
            "status": "completed",
            "encoder_clipped_samples": int(np.count_nonzero(code != exported)),
            "before_20bit_export": calibration_metrics(output, vin, experiment),
            "after_20bit_export": calibration_metrics(export_v, vin, experiment),
        },
        raw,
    )


# =========================================================================
# 2. 函数组：_measure / run / main
# =========================================================================
# -------------------------------------------------------------------------
# Evaluate fixed seed 100 for the paired 2% and 3% manufacturing levels.
# Select the ideal residue-ADC resolution and the output directory.
# ------------------------------------------------------------------------
def _measure(out: Path, sigma: float, adc2_bits: int) -> dict:
    """调用合同。

    Args:
        out: 原始记录及摘要的输出目录。
        sigma: 单位电容制造失配标准差，无量纲。
        adc2_bits: 理想二级量化器的位数。
    Returns:
        未使用 DEM／LMS 处理时的线性度与边界结果。

    本臂是独立未校准基线；不能因结果较差而用更换种子或缩窗隐藏失败。
    """
    experiment = replace(ExperimentConfig(), mismatch_sigma_unit=sigma, manufacturing_seed=100)
    cfg = replace(CalibrationConfig(), adc2_bits=adc2_bits, adc2_half_range_v=0.1)
    nominal, pool = cap_pool(experiment)
    physical = {"main": pool.main, "sub": pool.sub, "bridge": pool.bridge}
    if any(np.any(~np.isfinite(cap) | (cap <= 0)) for cap in physical.values()):
        raise ValueError("the manufacturing sample has an invalid capacitance")
    peak = experiment.full_scale_peak_v
    nominal_lower = codebook(nominal, experiment)
    if len(nominal_lower) != 512 or not np.isclose(nominal_lower[0], -peak, rtol=0, atol=1e-13):
        raise ValueError("expected 512 nominal Fig. 11 code intervals over the full input")
    thresholds = np.r_[-peak, nominal_lower[1:], peak]
    intervals, plan, masks = _uncalibrated_intervals(pool, experiment, thresholds)
    mass = code_mass(intervals, cfg.adc2_bits, cfg.adc2_half_range_v, peak)
    widths = np.bincount(mass.code, weights=mass.width_v, minlength=1 << OUTPUT_BITS) / STATE_COUNT
    adc2_out_of_range = (mass.adc2_below_v + mass.adc2_above_v) / STATE_COUNT
    accounted_v = float(widths.sum()) + adc2_out_of_range
    if not np.isclose(accounted_v, 2 * peak, rtol=0, atol=5e-11):
        raise ValueError(f"the nominal-decoder width integral accounts for {accounted_v:g} V")
    tone, tone_raw = _tone(pool, experiment, cfg, nominal_lower)
    arrays = {
        f"{ARM}_uniform_input_width_v_20b": widths,
        f"{ARM}_state_plan": plan[:STATE_COUNT],
        "nominal_coarse_threshold_v": thresholds,
        "residue_low_v_by_coarse_and_state": intervals.residue_low_v.reshape(-1, STATE_COUNT),
        "residue_high_v_by_coarse_and_state": intervals.residue_high_v.reshape(-1, STATE_COUNT),
        **{f"physical_{name}_cap_f": values for name, values in physical.items()},
        **tone_raw,
    }
    report = {
        "status": "adc2_range_blocked" if adc2_out_of_range > 1e-15 else "completed",
        "effective_states": STATE_COUNT,
        "adc2_out_of_range_input_width_v": adc2_out_of_range,
        "adc2_below_input_width_v": mass.adc2_below_v / STATE_COUNT,
        "adc2_above_input_width_v": mass.adc2_above_v / STATE_COUNT,
        "encoder_clip_input_width_v": (mass.output_below_v + mass.output_above_v) / STATE_COUNT,
        "encoder_low_clip_input_width_v": mass.output_below_v / STATE_COUNT,
        "encoder_high_clip_input_width_v": mass.output_above_v / STATE_COUNT,
        "mass_balance_error_v": accounted_v - 2 * peak,
        "residue_min_v": float(intervals.residue_low_v.min()),
        "residue_max_v": float(intervals.residue_high_v.max()),
        "tone": tone,
    }
    if report["status"] == "completed":
        _, _, stats = inl_linearity(widths)
        report["linearity"] = {"20": stats}
        report["local_direct_ramp_crosscheck"] = _check_direct_ramp(
            intervals, plan, masks, pool, experiment, cfg
        )
    np.savez_compressed(out / f"linearity_sigma_{100 * sigma:g}pct.npz", **arrays)
    return {
        "manufacturing_seed": experiment.manufacturing_seed,
        "sigma": sigma,
        "input_full_scale_v": 2 * peak,
        "sample_rate_hz": experiment.fs_hz,
        "adc2_bits": cfg.adc2_bits,
        "adc2_half_range_v": cfg.adc2_half_range_v,
        "adc2_input_equivalent_step_v": 2 * cfg.adc2_half_range_v / (1 << cfg.adc2_bits),
        "output_quantizer_bits": OUTPUT_BITS,
        "output_ideal_lsb_v": 2 * peak / (1 << OUTPUT_BITS),
        "nominal_coarse_intervals": len(thresholds) - 1,
        "state_slice_ids": plan[:STATE_COUNT].tolist(),
        "arms": {ARM: report},
    }


# ------------------------------------------------------------------------
def run(out: Path, adc2_bits: int) -> None:
    """Evaluate fixed seed 100 for the paired 2% and 3% manufacturing levels."""
    out.mkdir(parents=True, exist_ok=True)
    summary = {
        "schema": "task1_nominal_no_dem_no_lms_static_code_density_v1",
        "scope": (
            "Fixed A/B, DEM and LMS disabled, nominal coarse decisions and digital codebook, "
            "realized CDAC mismatch in the two-node residue only, ideal ADC2, no noise or skew."
        ),
        "method": (
            "Full input span, equal weight for the two fixed A/B states, affine input width "
            "integration for each ADC2 bin, 20-bit export, endpoint-code exclusion and "
            "mean interior width normalization."
        ),
        "training_samples": 0,
        "digital_reconstruction": "nominal_coarse_threshold_v[coarse_code] + ideal_adc2_midpoint_v",
        "adctoolbox_version": version("adctoolbox"),
        "source_sha256": {name: sha256_file(ROOT / name) for name in SOURCE_PATHS},
        "experiments": [_measure(out, sigma, adc2_bits) for sigma in (0.02, 0.03)],
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n")


# ------------------------------------------------------------------------
def main() -> None:
    """Select the ideal residue-ADC resolution and the output directory."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--adc2-bits", type=int, choices=(12, 16), default=12)
    args = parser.parse_args()
    run(args.out, args.adc2_bits)


if __name__ == "__main__":
    main()
