#!/usr/bin/env python3
"""复现可选 CAAZ 样本域实验，不拟合硅片性能。

自归零及普通误差 RMS 是显式 ASSUMED 的 RA 输出电压，进入原有 RA 后先限幅、
再经过 ADC2，最终码重构后配对。C32-1 图 6 提供保持噪声机制，不提供这些幅度、
目标架构或 40/20 MS/s 速率。产物写入新建库外目录，不覆盖已有证据。
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

from adi_model import Config
from adi_model.chopping_az import ChoppingAZParameters, run_caaz_pipeline


def _statistics(error_v: np.ndarray) -> dict[str, float]:
    return {
        "rms_v": float(np.sqrt(np.mean(error_v**2))),
        "std_v": float(np.std(error_v)),
        "mean_v": float(np.mean(error_v)),
    }


def reproduce(destination: Path) -> None:
    """运行匹配的完整 ADC 案例和保持噪声敏感性实验。

    向尚不存在的 destination 独占创建目录，保存摘要及原始数值记录。
    各 ADC 记录独立预置，不覆盖已有证据。

    Args:
        destination: 新输出目录。

    Returns:
        None；数值记录写入 destination。
    """
    destination.mkdir(parents=True, exist_ok=False)
    cfg = Config.rtl_structural(
        mismatch_enable=False,
        sadc_rdac_gain_mismatch=0.0,
        enable_sampling_noise=False,
        ra_enable_noise=False,
        dither_mode="off",
        dem_enable=False,
        seed=2025,
    )
    count = 8192
    input_frequency_hz = cfg.fs * 205 / count
    az_rms_v = ordinary_rms_v = 1.0e-3
    ordinary_error = np.random.default_rng(774).normal(0, ordinary_rms_v, count)

    def source(time_s):
        return 2.0 * np.sin(2 * np.pi * input_frequency_hz * np.asarray(time_s))

    summary: dict = {
        "schema_version": 1,
        "evidence_level": "SAMPLE_DOMAIN_BEHAVIORAL_SIMULATION",
        "source_mechanism": "PKU_VLSI2025_C32_1_PAPER Fig.6; PKU_VLSI2025_C32_1_SLIDES PDF34-35",
        "assumptions": {
            "raw_sample_rate_hz": cfg.fs,
            "pair_sample_rate_hz": cfg.fs / 2,
            "raw_samples": count,
            "input_peak_v": 2.0,
            "input_frequency_hz": input_frequency_hz,
            "az_ra_output_rms_v": az_rms_v,
            "ordinary_ra_output_rms_v": ordinary_rms_v,
            "nominal_ra_gain": cfg.g0,
            "seed": cfg.seed,
            "ordinary_error_seed": 774,
            "capacitor_mismatch": False,
            "calibration": "nominal frozen weights; no coefficient fitting",
            "unsupported": "dynamic chopper/FGA/CP-FIA/circuit flicker and PDK performance",
        },
        "cases": {},
    }
    arrays: dict[str, np.ndarray] = {}
    for mode in ("baseline", "conventional_az", "chopping_assisted"):
        result = run_caaz_pipeline(
            cfg,
            source,
            count,
            az=ChoppingAZParameters(
                mode=mode, az_noise_rms_v=0 if mode == "baseline" else az_rms_v
            ),
            post_az_error_v=ordinary_error,
        )
        time_s = result.raw.sample.t1
        ideal = source(time_s)
        pair_reference = (ideal[::2] + ideal[1::2]) / 2
        raw_error = result.chop_sign * result.raw_words.voltage - ideal
        pair_error = result.output_v - pair_reference
        if np.any(result.invalid) or len(result.output_code) != count // 2:
            raise RuntimeError(f"{mode}: invalid or missing pair in nominal experiment")
        expected_raw_variance = ordinary_rms_v**2 + (0 if mode == "baseline" else az_rms_v**2)
        expected_pair_variance = ordinary_rms_v**2 / 2 + (
            az_rms_v**2 / 2 if mode == "conventional_az" else 0
        )
        summary["cases"][mode] = {
            "raw": _statistics(raw_error),
            "pair": _statistics(pair_error),
            "pair_count": len(result.output_code),
            "invalid_pair_count": int(np.count_nonzero(result.invalid)),
            "continuous_linear_prediction_raw_std_v": float(
                np.sqrt(expected_raw_variance) / cfg.g0
            ),
            "continuous_linear_prediction_pair_std_v": float(
                np.sqrt(expected_pair_variance) / cfg.g0
            ),
            "first_sample_ids": result.source_sample_ids[:8].tolist(),
            "first_pair_time_s": float(result.output_time_s[0]),
            "display_pair_error_uv": (pair_error[:160] * 1e6).tolist(),
            "display_az_error_mv": (result.az_sample_error_v[:16] * 1e3).tolist(),
        }
        arrays[f"{mode}_raw_code"] = result.raw_words.code
        arrays[f"{mode}_pair_code"] = result.output_code
        arrays[f"{mode}_pair_error_v"] = pair_error
        arrays[f"{mode}_sample_ids"] = result.source_sample_ids

    hold_rows = []
    for retention in (0.0, 0.5, 0.8, 0.95, 1.0):
        result = run_caaz_pipeline(
            cfg,
            source,
            count,
            az=ChoppingAZParameters(
                mode="chopping_assisted",
                az_noise_rms_v=az_rms_v,
                odd_hold_retention=retention,
            ),
        )
        if np.any(result.invalid):
            raise RuntimeError("hold sensitivity experiment generated invalid pairs")
        ideal = source(result.raw.sample.t1)
        pair_reference = (ideal[::2] + ideal[1::2]) / 2
        hold_rows.append(
            {
                "retention": retention,
                **_statistics(result.output_v - pair_reference),
                "linear_az_only_std_v": az_rms_v * (1 - retention) / (2 * cfg.g0),
            }
        )
    summary["hold_sensitivity"] = hold_rows
    conventional = summary["cases"]["conventional_az"]
    caaz = summary["cases"]["chopping_assisted"]
    summary["measured_gain_db"] = {
        "ordinary_pair_vs_raw": float(
            20 * np.log10(conventional["raw"]["std_v"] / conventional["pair"]["std_v"])
        ),
        "caaz_vs_matched_conventional_pair": float(
            20 * np.log10(conventional["pair"]["std_v"] / caaz["pair"]["std_v"])
        ),
    }
    root = Path(__file__).resolve().parents[1]
    files = [*sorted((root / "src/adi_model").glob("*.py")), Path(__file__).resolve()]
    summary["source_sha256"] = {
        file.relative_to(root).as_posix(): hashlib.sha256(file.read_bytes()).hexdigest()
        for file in files
    }
    np.savez_compressed(destination / "raw_capture.npz", **arrays)
    (destination / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary["measured_gain_db"], indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True, help="new external result directory")
    reproduce(parser.parse_args().out)
