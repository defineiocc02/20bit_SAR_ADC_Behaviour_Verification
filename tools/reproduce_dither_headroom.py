#!/usr/bin/env python3
"""复跑静态理想 rtl16 profile 的有限幅度扫描，逐节点检查 dither 余量。

固定18/8 slice、9bit SADC、12bit ADC2、40MS/s、2048点相干正弦，
fin=251*Fs/2048、phase=0、DEM关闭。同一理想物理pool和独立RNG设置贯穿
8个输入峰值与off/sampling/quantizer三种模式；不做校准、不改变任何量程。

输出summary.json、producer.json及24份raw_data/*.npz。SADC超范围、端点码、
RDAC命令越界、RA饱和、ADC2越界、最终20bit裁剪分别报告；失败点和输出均保留。
这是项目工程profile的行为余量实验，不是原芯片满量程或动态电路性能证明。

从repo根目录执行（--out必须不存在）：
    PYTHONPATH=src python tools/reproduce_dither_headroom.py --out NEW_DIRECTORY
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
from dataclasses import replace
from pathlib import Path

import numpy as np

from adi_model import Config, sine_input
from adi_model.dac_arch import SplitDAC
from adi_model.pipeline import run_pipeline
from adi_model.sadc import build_first_stage_quantizer
from adi_model.slice_pool import PhysicalSlicePool

REPO = Path(__file__).resolve().parents[1]
AMPLITUDES_V = (2.70, 2.80, 2.90, 2.95, 2.97, 2.98, 2.99, 3.00)
MODES = ("off", "sampling", "quantizer")
N_SAMPLES = 2048


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _source_hashes() -> dict[str, str]:
    return {
        str(path.relative_to(REPO)): _sha(path.read_bytes())
        for path in sorted((REPO / "src/adi_model").glob("*.py"))
    }


def _stats(value: np.ndarray) -> dict:
    value = np.asarray(value, float)
    return {
        "mean_v": float(value.mean()),
        "std_v": float(value.std()),
        "rms_v": float(np.sqrt(np.mean(value * value))),
        "maxabs_v": float(np.max(np.abs(value))),
    }


def run(out: Path) -> dict:
    """运行24个固定工作点；所有产物只写入新建输出目录。"""
    sources = _source_hashes()
    try:
        revision = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=REPO, text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (FileNotFoundError, subprocess.CalledProcessError):
        revision = None
    try:
        dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=REPO, text=True))
    except (FileNotFoundError, subprocess.CalledProcessError):
        dirty = None
    cfg0 = Config.rtl_structural(
        mismatch_enable=False,
        enable_sampling_noise=False,
        ra_enable_noise=False,
        sadc_rdac_gain_mismatch=0,
        dem_enable=False,
        dither_mode="off",
        seed=42,
    )
    pool = PhysicalSlicePool(cfg0, np.random.default_rng(4242))
    pool_hash = _sha(pool.unit_caps.tobytes() + pool.bridge_caps.tobytes())
    fin_hz = 251 * cfg0.fs / N_SAMPLES
    producer = {
        "tool": "tools/reproduce_dither_headroom.py",
        "tool_sha256": _sha(Path(__file__).read_bytes()),
        "source_commit": revision,
        "source_worktree_dirty": dirty,
        "source_sha256": sources,
        "python": platform.python_version(),
        "numpy": np.__version__,
        "command": "PYTHONPATH=src python tools/reproduce_dither_headroom.py --out NEW_DIRECTORY",
        "measurement_scope": "static ideal behavioral rtl16 profile; no transistor or full-scale silicon claim",
    }
    cells = []
    for mode in MODES:
        cfg = replace(cfg0, dither_mode=mode)
        for amplitude in AMPLITUDES_V:
            fn = sine_input(amplitude, fin_hz, 0)
            raw = run_pipeline(cfg, fn, N_SAMPLES, pool=pool, rng=np.random.default_rng(42))
            sadc = build_first_stage_quantizer(raw.cfg, SplitDAC(raw.cfg, raw.chip))
            words = raw.to_codes()
            # No RA noise/dynamics in this experiment: G*r is the pre-clip
            # RA-output node. ADC2 sees raw.adc2_input_voltage after RA clipping.
            preclip = raw.g_vec * raw.residue
            low = raw.sample.x_sadc < sadc.thresholds[0]
            high = raw.sample.x_sadc >= sadc.thresholds[-1]
            bad = raw.rdac_over | raw.ra_sat | raw.adc2_over
            reference = fn(raw.sample.t1)
            name = f"{mode}_{amplitude:.2f}V"
            arrays = {
                "time_s": raw.sample.t1,
                "input_v": reference,
                "sadc_input_v": raw.sample.x_sadc,
                "sadc_low_outside": low,
                "sadc_high_outside": high,
                "coarse_code": raw.coarse,
                "rdac_requested_code": raw.k,
                "rdac_over": raw.rdac_over,
                "dither_diagnostic_v": raw.sample.dither,
                "sadc_dither_dq_v": raw.sample.dither
                if mode == "quantizer"
                else np.zeros(N_SAMPLES),
                "rdac_mask_injection_v": raw.sample.dither
                if mode == "sampling"
                else np.zeros(N_SAMPLES),
                "rdac_dither_dr_v": raw.sample.rdac_dither,
                "ra_preclip_v": preclip,
                "ra_output_v": raw.vra,
                "ra_sat": raw.ra_sat,
                "adc2_input_v": raw.adc2_input_voltage,
                "adc2_raw_code": raw.adc2_code,
                "adc2_over": raw.adc2_over,
                "output20_code": words.code,
                "output20_v": words.voltage,
                "digital_clipped_low": words.clipped_low,
                "digital_clipped_high": words.clipped_high,
                "sample_id": raw.sample_id,
            }
            datafile = out / "raw_data" / f"{name}.npz"
            np.savez_compressed(datafile, **arrays)
            cells.append(
                {
                    "id": name,
                    "dither_mode": mode,
                    "input_peak_v": amplitude,
                    "N": N_SAMPLES,
                    "sadc_input_min_v": float(raw.sample.x_sadc.min()),
                    "sadc_input_max_v": float(raw.sample.x_sadc.max()),
                    "sadc_threshold_min_v": float(sadc.thresholds[0]),
                    "sadc_threshold_max_v": float(sadc.thresholds[-1]),
                    "sadc_low_outside_count": int(low.sum()),
                    "sadc_high_outside_count": int(high.sum()),
                    "coarse_low_endpoint_count": int(np.count_nonzero(raw.coarse == 0)),
                    "coarse_high_endpoint_count": int(
                        np.count_nonzero(raw.coarse == sadc.n_code - 1)
                    ),
                    "rdac_requested_min": float(raw.k.min()),
                    "rdac_requested_max": float(raw.k.max()),
                    "rdac_legal_min": 0,
                    "rdac_legal_max": cfg.dac_levels - 1,
                    "rdac_over_count": int(raw.rdac_over.sum()),
                    "ra_preclip_min_v": float(preclip.min()),
                    "ra_preclip_max_v": float(preclip.max()),
                    "ra_clip_abs_v": cfg.ra_v_clip,
                    "ra_sat_count": int(raw.ra_sat.sum()),
                    "adc2_input_min_v": float(raw.adc2_input_voltage.min()),
                    "adc2_input_max_v": float(raw.adc2_input_voltage.max()),
                    "adc2_window_min_v": cfg.adc2_v_min,
                    "adc2_window_max_v": cfg.adc2_v_max,
                    "adc2_over_count": int(raw.adc2_over.sum()),
                    "digital_clipped_low_count": int(words.clipped_low.sum()),
                    "digital_clipped_high_count": int(words.clipped_high.sum()),
                    "any_analog_invalid_count": int(bad.sum()),
                    "sadc_outside_and_adc2_over_count": int(((low | high) & raw.adc2_over).sum()),
                    "signal_error_all": _stats(words.voltage - reference),
                    "retained_samples": len(words.code),
                    "dither_sequence_metadata": raw.effective_config["dither_sequence"],
                    "arrays_file": str(datafile.relative_to(out)),
                    "arrays_sha256": _sha(datafile.read_bytes()),
                }
            )
    if _sha(pool.unit_caps.tobytes() + pool.bridge_caps.tobytes()) != pool_hash:
        raise RuntimeError("fixed ideal pool changed during the experiment")
    if _source_hashes() != sources:
        raise RuntimeError("production source changed during the experiment")
    result = {
        "schema": "dither-headroom-v1",
        "producer": producer,
        "profile": "rtl_structural",
        "common_config": cfg0.to_dict(),
        "amplitudes_v": list(AMPLITUDES_V),
        "dither_modes": list(MODES),
        "N": N_SAMPLES,
        "fin_hz": fin_hz,
        "phase_rad": 0,
        "same_fixed_ideal_pool_sha256": pool_hash,
        "output_format": "20-bit offset binary; decoded input-referred bin centers [V]",
        "metric_definitions": {
            "sadc_outside": "x_SADC<threshold_min or x_SADC>=threshold_max; endpoint code counts alone do not prove saturation",
            "ra_preclip": "G*r before original RA clamp, valid here because RA noise/dynamics are disabled",
            "adc2_input": "actual RA-output node after RA clipping; compared to the unchanged ADC2 window",
            "rms_v": "sqrt(mean(error**2)), retaining mean error",
            "std_v": "population standard deviation about the mean; not interchangeable with RMS",
            "retention": "all2048 outputs retained; no invalid workpoint or sample is filtered",
            "scope": "finite coherent amplitude grid; not a full-range guarantee or INL/DNL sweep",
        },
        "cells": cells,
        "completed_cells": len(cells),
    }
    (out / "summary.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    )
    (out / "producer.json").write_text(
        json.dumps(producer, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    )
    return result


def main(argv: list[str] | None = None) -> int:
    """只向全新目录写入一组可追溯的有限幅度扫描。"""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.out.exists() or args.out.is_symlink():
        parser.error("--out must be a new directory; refusing to overwrite an existing path")
    args.out.mkdir(parents=True, exist_ok=False)
    (args.out / "raw_data").mkdir()
    result = run(args.out)
    print(
        json.dumps({"cells": result["completed_cells"], "out": str(args.out)}, ensure_ascii=False)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
