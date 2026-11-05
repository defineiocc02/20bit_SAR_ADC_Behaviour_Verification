#!/usr/bin/env python3
"""Reproduce conditional sample-domain DEM/dither/CAAZ compatibility controls.

One command writes the fixed 42-cell matrix and five follow-up controls into a
new directory. The model is a behavioral plant. The two AZ error-transfer
hypotheses are explicit assumptions, not measurements of CBIAS/FGA noise.
The source revision is recorded; no historical Git HEAD is required.

Assumed stress: slice sigma=0.5%, unit sigma=0.02%, bridge/parasitic sigma=0.02%.
These are experiment overrides, not default process mismatch or paper values.
RA-output-equivalent 1mV RMS is an assumed added source; the same input-referred
innovations also drive H_AZ=G_n as a distinct conditional transfer hypothesis.

Sampling-dither training fits effective per-unit C/Cf weights from whitelisted
observations and a separately known input. Those fixed physical parameters can
be reused at the same geometry/Vfs when DEM, scheduling and other dither modes
change known masks/voltage terms. This is not an online learner and it does not
calibrate dynamic settling/reference errors. Physical G/alpha are used only by
the plant injection and the diagnostic oracle, never by the digital estimator.

Example from the checkout root (the output directory must not exist)::

    PYTHONPATH=src OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 \
        python tools/reproduce_randomization_caaz.py --out /tmp/new-caaz-matrix

Outputs include light summary.json, detailed matrix_results.json,
followup_controls.json, source_lineage.json and full raw_data/ NPZ/fit records.
Every output is retained; invalid flags are counted rather than hidden.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import time
from dataclasses import fields
from pathlib import Path

import numpy as np
import scipy

from adi_model import Config, dc_input, sine_input
from adi_model.chopping_az import ChoppingAZParameters, pair_readout, run_caaz_pipeline
from adi_model.fixed_point import CodeStream, FixedPointReconstructor
from adi_model.pipeline import run_pipeline
from adi_model.slice_pool import PhysicalSlicePool
from adi_model.weight_calibration import DigitalObservation, FrozenCalibration, fit_unit_weights

REPO = Path(__file__).resolve().parents[1]
REFERENCE_RUN_HEAD = "8238cc350de0c09b1071ca446bf00fde4711cc4c"
N = 2048
NCAL = 8192
SEED = 2501
POOL_SEED = 889
NOISE_SEED = 61002
ERROR_SEED = 6421


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def source_hashes() -> dict[str, str]:
    return {
        "src/adi_model/" + path.name: sha(path.read_bytes())
        for path in sorted((REPO / "src/adi_model").glob("*.py"))
    }


def revision_info() -> dict:
    try:
        head = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=REPO, text=True, stderr=subprocess.DEVNULL
        ).strip()
        dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=REPO, text=True))
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        return {
            "git_head": None,
            "git_worktree_dirty": None,
            "status": "UNAVAILABLE",
            "reason": type(exc).__name__,
        }
    return {"git_head": head, "git_worktree_dirty": dirty, "status": "RECORDED"}


def stats(value):
    a = np.asarray(value, float)
    if not a.size:
        return {"n": 0, "mean_v": None, "std_v": None, "rms_v": None, "maxabs_v": None}
    return {
        "n": a.size,
        "mean_v": float(a.mean()),
        "std_v": float(a.std()),
        "rms_v": float(np.sqrt(np.mean(a * a))),
        "maxabs_v": float(abs(a).max()),
    }


def extrema(a):
    a = np.asarray(a, float)
    return {"min": float(a.min()), "max": float(a.max()), "std": float(a.std())}


def base_config(plant, structural=False, **kw):
    factory = Config.rtl_structural if structural else Config.paper_literal
    args = {
        "mismatch_enable": plant == "mismatch",
        "mismatch_sigma0": 0,
        "dac_bridge_mismatch_sigma": 0.0002 if plant == "mismatch" else 0,
        "dac_parasitic_spread": 0.0002 if plant == "mismatch" else 0,
        "enable_sampling_noise": False,
        "ra_enable_noise": False,
        "sadc_rdac_gain_mismatch": 0,
        "seed": SEED,
    }
    args.update(kw)
    return factory(**args)


def run_experiment(OUT: Path) -> dict:
    """Run the fixed matrix and controls without mutating repository sources."""
    source_sha = source_hashes()
    version = revision_info()
    head = version["git_head"]
    lineage = {
        "current_source_version": version,
        "reference_run_source_head": REFERENCE_RUN_HEAD,
        "reference_note": "Historical numeric-equivalence reference; current revision is recorded, not blocked.",
        "source_sha256": source_sha,
        "script_sha256": sha(Path(__file__).read_bytes()),
        "runtime": {
            "python": platform.python_version(),
            "numpy": np.__version__,
            "scipy": scipy.__version__,
        },
        "thread_environment": {
            key: os.environ.get(key)
            for key in ("VECLIB_MAXIMUM_THREADS", "OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS")
        },
        "evidence": "Conditional sample-domain behavioral experiment; no claim of measured device-noise transfer or whole RTL performance signoff.",
        "generation_command": "PYTHONPATH=src OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 python tools/reproduce_randomization_caaz.py --out NEW_DIRECTORY",
    }
    (OUT / "source_lineage.json").write_text(json.dumps(lineage, indent=2) + "\n")
    rows = []
    training_reports = {}

    def persist():
        result = {
            "schema": "dem-dither-caaz-compatibility-v1",
            "source_head": head,
            "N_raw": N,
            "raw_fs_hz": 40e6,
            "pair_fs_hz": 20e6,
            "rate_grade": "ENGINEERING_CHOICE",
            "matrix_cells": len(rows),
            "training": training_reports,
            "cells": rows,
            "error_plane_note": "A: constant RA-output H=Gnom; B: RA/FGA-input equivalent H=G_n. e[k] is shared in each pair; no claim that physical CBIAS noise transfer has been measured.",
            "signal_truth": "(input(t_even)+input(t_odd))/2, never the latest endpoint or unfiltered midpoint",
            "no_filtering": "all arrays/flags retained; valid-intersection statistics explicitly labeled separately",
        }
        (OUT / "matrix_results.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n"
        )

    def train(plant):
        cfg = base_config(
            plant,
            dem_enable=True,
            dem_mode="permute",
            dem_bridge_enable=True,
            dither_mode="sampling",
            dither_split_bank="sub",
            dither_units_range=2,
        )
        pool = PhysicalSlicePool(
            cfg,
            np.random.default_rng(POOL_SEED),
            slice_sigma=0.005 if plant == "mismatch" else 0,
            unit_sigma=0.0002 if plant == "mismatch" else 0,
        )
        caps_before = sha(
            pool.unit_caps.tobytes() + pool.bridge_caps.tobytes() + pool.sub_parasitic.tobytes()
        )
        fn = sine_input(2.0, cfg.fs * 103 / NCAL, 0.19)
        begun = time.monotonic()
        raw = run_pipeline(cfg, fn, NCAL, pool=pool, rng=np.random.default_rng(NOISE_SEED + 1))
        digital = DigitalObservation.from_result(raw)
        known = np.asarray(fn(raw.sample.t1))
        report = {
            "cfg": cfg.to_dict(),
            "n_training": NCAL,
            "known_reference": "independent controlled sine 2.0V, 103 cycles, phase0.19rad",
            "noise_seed": NOISE_SEED + 1,
            "pool_seed": POOL_SEED,
            "slice_sigma_override": 0.005 if plant == "mismatch" else 0,
            "unit_sigma_override": 0.0002 if plant == "mismatch" else 0,
            "bad_raw": int(digital.overflow.sum()),
            "physical_array_sha256": caps_before,
            "estimator_fields": [f.name for f in fields(digital)],
            "training_data_source": "only DigitalObservation + separately evaluated known reference; no G/C/alpha/oracle passed to fit",
        }
        np.savez_compressed(
            OUT / "raw_data" / f"{plant}_training_digital.npz",
            known_input_v=known,
            slice_ids=digital.slice_ids,
            rdac_code=digital.rdac_code,
            dem_state=digital.dem_state,
            adc2_code=digital.adc2_code,
            bank_dither=digital.bank_dither,
            overflow=digital.overflow,
        )
        training_reports[plant] = report
        persist()
        if report["bad_raw"]:
            raise ValueError(f'{plant} training overflow: {report["bad_raw"]}; not filtering it')
        fitted = fit_unit_weights(digital, known)
        report.update(
            rank=fitted.rank,
            coefficients=fitted.weights.size + 1,
            condition=fitted.condition,
            residual_rms_raout_v=fitted.residual_rms_v,
            training_digest=fitted.training_digest,
            wall_s=time.monotonic() - begun,
        )
        (OUT / "raw_data" / f"{plant}_frozen_fit.json").write_text(
            json.dumps(fitted.to_dict(), indent=2) + "\n"
        )
        report["frozen_weights_bytes_sha256"] = sha(fitted.weights.tobytes())
        report["frozen_fit_sha256"] = sha(
            (OUT / "raw_data" / f"{plant}_frozen_fit.json").read_bytes()
        )
        print("TRAIN", plant, report["rank"], report["condition"], report["wall_s"], flush=True)
        persist()
        return pool, fitted, caps_before

    def digital_gain(decoder, ids, cfg):
        w = decoder.weights_q.astype(float) / 2**decoder.format.weight_fraction_bits
        if cfg.dither_mode == "sampling":
            count = int(2 * cfg.dither_units_range)
            end = cfg.dac_n_main if cfg.dither_split_bank == "main" else w.shape[1]
            if count:
                w[:, end - count : end] = 0
        return w[ids].sum(axis=(1, 2))

    def bad_flags(raw, words):
        return {
            "rdac_over": int(raw.rdac_over.sum()),
            "ra_sat": int(raw.ra_sat.sum()),
            "adc2_over": int(raw.adc2_over.sum()),
            "digital_low": int(words.clipped_low.sum()),
            "digital_high": int(words.clipped_high.sum()),
            "raw_analog_over": int(words.analog_overflow.sum()),
        }

    def run_cell(plant, pool, fitted, cfg, label, *, stress=False):
        count = N
        fn = dc_input(0.3) if stress else sine_input(1.6, cfg.fs * 31 / count, 0.31)
        started = time.monotonic()

        def capture(mode, error=None):
            return run_caaz_pipeline(
                cfg,
                fn,
                count,
                pool=pool,
                az=ChoppingAZParameters(mode=mode),
                post_az_error_v=error,
                rng=np.random.default_rng(NOISE_SEED),
            )

        quiet = capture("chopping_assisted")
        ordinary = capture("baseline")
        ids = quiet.raw.conv_slice_ids
        coeff = pool.split_coefficients(ids, cfg=cfg)
        gain = np.asarray(quiet.raw.g_vec)
        alpha = np.asarray(coeff["alpha"]) if cfg.dither_mode == "sampling" else np.ones(count)
        k_true = gain * alpha
        rng = np.random.default_rng(ERROR_SEED)
        e = np.repeat(
            rng.uniform(-0.0025, 0.0025, count // 2)
            if stress
            else rng.normal(0, 0.001 / cfg.g0, count // 2),
            2,
        )
        h = {"A_constant_RA_output": np.full(count, cfg.g0), "B_RA_input_times_G": gain}
        noisy = {name: capture("chopping_assisted", transfer * e) for name, transfer in h.items()}
        reference = (fn(quiet.raw.sample.t1[::2]) + fn(quiet.raw.sample.t1[1::2])) / 2
        case = {
            "id": label,
            "plant": plant,
            "cfg": cfg.to_dict(),
            "profile": "rtl16"
            if cfg.conversion.rdac_event_timing == "rtl16"
            else "generic paper_literal",
            "scheduler_requested": cfg.dem_mode,
            "scheduler_actual": (
                "rtl16 allocator, shuffle=dem_enable"
                if cfg.conversion.rdac_event_timing == "rtl16"
                else ("causal shuffle" if cfg.dem_mode == "permute" else "fixed A/B pingpong")
            ),
            "dem_enable": cfg.dem_enable,
            "dem_bridge_enable": cfg.dem_bridge_enable,
            "dither": cfg.dither_mode,
            "seed": SEED,
            "pool_seed": POOL_SEED,
            "noise_seed": NOISE_SEED,
            "error_seed": ERROR_SEED,
            "N_raw": count,
            "N_pair": count // 2,
            "input": "DC0.3V stress" if stress else "sine1.6V,31 cycles,phase0.31rad",
            "error_source": "pair-held uniform input-eq[-2.5mV,+2.5mV], constant-H output[-80mV,+80mV]"
            if stress
            else "pair-held Gaussian input-eq31.25uV RMS; constant-H output1mV RMS",
            "training_fit_sha256": training_reports[plant]["frozen_fit_sha256"],
            "G": extrema(gain),
            "alpha": extrema(alpha),
            "K_true": extrema(k_true),
            "same_group_pairs": int(np.all(np.sort(ids[::2]) == np.sort(ids[1::2]), axis=1).sum()),
            "pair_group_intersection_max": max(
                len(set(a) & set(b)) for a, b in zip(ids[::2], ids[1::2], strict=False)
            ),
            "unique_selected_groups": len(np.unique(np.sort(ids, axis=1), axis=0)),
            "unique_sid": len(np.unique(quiet.raw.sid)),
            "sid_equal_within_pair_fraction": float(
                np.mean(quiet.raw.sid[::2] == quiet.raw.sid[1::2])
            ),
            "frozen_readout_same_raw": True,
            "results": {},
        }
        arrays = {
            "pair_reference_v": reference,
            "raw_id": quiet.raw.sample_id,
            "selected_slice_ids": ids,
            "dem_sid": quiet.raw.sid,
            "dither_bank_code": np.zeros(count)
            if quiet.raw.sample.dither_bank_code is None
            else quiet.raw.sample.dither_bank_code,
            "dither_v": quiet.raw.sample.dither,
            "G": gain,
            "alpha": alpha,
            "K_true": k_true,
            "pair_error_source_input_v": e[::2],
        }
        for name, result in noisy.items():
            for field in ("k", "coarse", "sid", "adc2_code"):
                if field != "adc2_code" and not np.array_equal(
                    getattr(quiet.raw, field), getattr(result.raw, field)
                ):
                    raise ValueError("injection changed upstream digital controls")
            if not np.array_equal(ids, result.raw.conv_slice_ids):
                raise ValueError("error run rerolled schedule")
            good = ~(quiet.raw.ra_sat | result.raw.ra_sat)
            case.setdefault("physical_hook_identity_max_v", {})[name] = (
                float(abs((result.raw.vra - quiet.raw.vra - h[name] * e)[good]).max())
                if good.any()
                else None
            )
        for decoder_name in ("nominal", "frozen"):

            def decode(result, decoder_name=decoder_name):
                digital = DigitalObservation.from_result(result.raw)
                decoder = (
                    FixedPointReconstructor.from_result(result.raw)
                    if decoder_name == "nominal"
                    else FixedPointReconstructor.from_frozen_calibration(digital.spec, fitted)
                )
                words = decoder.reconstruct(digital)
                pair = pair_readout(words, difference=result.mode == "chopping_assisted")
                return decoder, words, pair

            decoder, qwords, qpair = decode(quiet)
            _, owords, opair = decode(ordinary)
            khat = digital_gain(decoder, ids, cfg)
            decoded = {
                "Khat": extrema(khat),
                "Khat_vs_K_relative_error": extrema(khat / k_true - 1),
                "quiet_pair_error": stats(qpair[1] - reference),
                "ordinary_pair_error": stats(opair[1] - reference),
                "quiet_invalid_pairs": int(qpair[3].sum()),
                "ordinary_invalid_pairs": int(opair[3].sum()),
                "quiet_raw_flags": bad_flags(quiet.raw, qwords),
                "ordinary_raw_flags": bad_flags(ordinary.raw, owords),
                "noise": {},
            }
            arrays[f"{decoder_name}_Khat"] = khat
            arrays[f"{decoder_name}_quiet_pair_v"] = qpair[1]
            arrays[f"{decoder_name}_ordinary_pair_v"] = opair[1]
            arrays[f"{decoder_name}_quiet_invalid"] = qpair[3]
            for name, result in noisy.items():
                _, words, pair = decode(result)
                observed = pair[1] - qpair[1]
                a = h[name] / khat
                physical_a = h[name] / k_true
                prediction = (a[::2] - a[1::2]) * e[::2] / 2
                physical_prediction = (physical_a[::2] - physical_a[1::2]) * e[::2] / 2
                valid = ~(qpair[3] | pair[3])
                bound = 1.5 * cfg.lsb_target + (
                    (cfg.adc2_v_max - cfg.adc2_v_min) / 2**cfg.adc2_n_bits
                ) / float(khat.min())
                nr = {
                    "H_domain": name,
                    "H_extrema": extrema(h[name]),
                    "a_H_over_Khat": extrema(a),
                    "a_pair_difference": extrema(a[::2] - a[1::2]),
                    "total_pair_error": stats(pair[1] - reference),
                    "delta_noise_all": stats(observed),
                    "delta_noise_valid_intersection": stats(observed[valid]),
                    "linear_prediction_Khat": stats(prediction),
                    "linear_prediction_physical_K": stats(physical_prediction),
                    "observed_minus_prediction_valid": stats((observed - prediction)[valid]),
                    "quantization_bound_v": bound,
                    "invalid_pairs": int(pair[3].sum()),
                    "valid_intersection_pairs": int(valid.sum()),
                    "raw_flags": bad_flags(result.raw, words),
                }
                decoded["noise"][name] = nr
                arrays[f"{decoder_name}_{name}_observed_leak_v"] = observed
                arrays[f"{decoder_name}_{name}_linear_prediction_v"] = prediction
                arrays[f"{decoder_name}_{name}_a_difference"] = a[::2] - a[1::2]
                arrays[f"{decoder_name}_{name}_invalid"] = pair[3]
            case["results"][decoder_name] = decoded
        # Same-group counterfactual is an algebra-only nonpipeline control, not a
        # scheduler mutation. Both conversions reuse the real even group's K/H.
        case["same_group_counterfactual"] = {
            "status": "NONPIPELINED_ALGEBRA_ONLY",
            "same_G_alpha_H_for_pair": True,
            "predicted_pair_leak_both_H_domains_v": 0.0,
            "reason": "current simultaneous acquisition/conversion forbids same group on consecutive ticks",
        }
        for key, result in [("quiet", quiet), ("ordinary", ordinary), *noisy.items()]:
            arrays[key + "_raw_adc2_code"] = result.raw.adc2_code
            arrays[key + "_raw_ra_sat"] = result.raw.ra_sat
            arrays[key + "_raw_adc2_over"] = result.raw.adc2_over
            arrays[key + "_raw_rdac_over"] = result.raw.rdac_over
        arrayfile = OUT / "raw_data" / f"{label}.npz"
        np.savez_compressed(arrayfile, **arrays)
        case["arrays_file"] = str(arrayfile.relative_to(OUT))
        case["arrays_sha256"] = sha(arrayfile.read_bytes())
        case["wall_s"] = time.monotonic() - started
        rows.append(case)
        persist()
        print(
            "CELL",
            label,
            "bad",
            case["results"]["frozen"]["quiet_invalid_pairs"],
            "wall",
            case["wall_s"],
            flush=True,
        )

    for plant in ("mismatch", "ideal"):
        pool, fitted, physical_sha = train(plant)
        # Structural production allocator cells first, so this milestone can be
        # reviewed before the separate generic-factor decomposition is complete.
        for dem in (False, True):
            for dither in ("off", "sampling", "quantizer"):
                cfg = base_config(
                    plant,
                    True,
                    dem_enable=dem,
                    dither_mode=dither,
                    dither_split_bank="sub",
                    dither_units_range=2,
                )
                run_cell(plant, pool, fitted, cfg, f"{plant}_rtl16_dem{int(dem)}_{dither}")
        for schedule in ("rotate", "permute"):
            for dem in (False, True):
                for dither in ("off", "sampling", "quantizer"):
                    cfg = base_config(
                        plant,
                        dem_enable=dem,
                        dem_bridge_enable=False,
                        dem_mode=schedule,
                        dither_mode=dither,
                        dither_split_bank="sub",
                        dither_units_range=2,
                    )
                    run_cell(
                        plant,
                        pool,
                        fitted,
                        cfg,
                        f"{plant}_generic_{schedule}_dem{int(dem)}_{dither}",
                    )
        if plant == "mismatch":
            for dither in ("off", "sampling", "quantizer"):
                cfg = base_config(
                    plant,
                    dem_enable=True,
                    dem_bridge_enable=True,
                    dem_mode="permute",
                    dither_mode=dither,
                    dither_split_bank="sub",
                    dither_units_range=2,
                )
                run_cell(plant, pool, fitted, cfg, f"{plant}_generic_permute_bridge1_{dither}")
            for dither in ("off", "sampling", "quantizer"):
                cfg = base_config(
                    plant,
                    dem_enable=True,
                    dem_bridge_enable=False,
                    dem_mode="permute",
                    dither_mode=dither,
                    dither_split_bank="sub",
                    dither_units_range=2,
                )
                run_cell(
                    plant,
                    pool,
                    fitted,
                    cfg,
                    f"{plant}_stress_generic_permute_{dither}",
                    stress=True,
                )
        after = sha(
            pool.unit_caps.tobytes() + pool.bridge_caps.tobytes() + pool.sub_parasitic.tobytes()
        )
        if after != physical_sha:
            raise ValueError("fixed fabricated pool changed during matrix")
        if sha(fitted.weights.tobytes()) != training_reports[plant]["frozen_weights_bytes_sha256"]:
            raise ValueError("frozen coefficients changed during the matrix")

    def cfg_for(plant, dither, white=False):
        return Config.paper_literal(
            mismatch_enable=plant == "mismatch",
            mismatch_sigma0=0,
            dac_bridge_mismatch_sigma=0.0002 if plant == "mismatch" else 0,
            dac_parasitic_spread=0.0002 if plant == "mismatch" else 0,
            enable_sampling_noise=False,
            ra_enable_noise=white,
            ra_out_noise_rms=0.001 if white else None,
            sadc_rdac_gain_mismatch=0,
            dem_enable=True,
            dem_bridge_enable=False,
            dem_mode="permute",
            dither_mode=dither,
            dither_split_bank="sub",
            dither_units_range=2,
            seed=2501,
        )

    def pool_for(cfg, plant):
        return PhysicalSlicePool(
            cfg,
            np.random.default_rng(889),
            slice_sigma=0.005 if plant == "mismatch" else 0,
            unit_sigma=0.0002 if plant == "mismatch" else 0,
        )

    def word(raw, fit):
        data = DigitalObservation.from_result(raw)
        return FixedPointReconstructor.from_frozen_calibration(data.spec, fit).reconstruct(data)

    result = {"independent_white_controls": [], "same_group_controls": []}
    for plant in ("ideal", "mismatch"):
        cfg = cfg_for(plant, "sampling", True)
        pool = pool_for(cfg, plant)
        fit = FrozenCalibration.from_dict(
            json.loads((OUT / "raw_data" / f"{plant}_frozen_fit.json").read_text())
        )
        n = 2048
        fn = sine_input(1.6, cfg.fs * 31 / n, 0.31)
        e = np.repeat(np.random.default_rng(6421).normal(0, 0.001 / cfg.g0, n // 2), 2)
        q = run_caaz_pipeline(
            cfg,
            fn,
            n,
            pool=pool,
            az=ChoppingAZParameters(mode="chopping_assisted"),
            rng=np.random.default_rng(61002),
        )
        basewhite = q.raw.vra - q.raw.g_vec * q.raw.residue
        qw = word(q.raw, fit)
        qp = pair_readout(qw, difference=True)
        reference = (fn(q.raw.sample.t1[::2]) + fn(q.raw.sample.t1[1::2])) / 2
        out = {
            "plant": plant,
            "cfg": cfg.to_dict(),
            "same_chip_seed": 889,
            "noise_seed": 61002,
            "error_seed": 6421,
            "noise": {},
            "quiet_pair_signal_error": stats(qp[1] - reference),
            "raw_ra_white_rms_measured_v": float(np.sqrt(np.mean(basewhite**2))),
        }
        arrays = {"reference": reference, "quiet_pair": qp[1], "base_ra_white_v": basewhite}
        for name, H in [
            ("A_constant_RA_output", np.full(n, cfg.g0)),
            ("B_RA_input_times_G", q.raw.g_vec),
        ]:
            noisy = run_caaz_pipeline(
                cfg,
                fn,
                n,
                pool=pool,
                az=ChoppingAZParameters(mode="chopping_assisted"),
                post_az_error_v=H * e,
                rng=np.random.default_rng(61002),
            )
            nw = word(noisy.raw, fit)
            pair = pair_readout(nw, difference=True)
            white = noisy.raw.vra - noisy.raw.g_vec * noisy.raw.residue - H * e
            out["noise"][name] = {
                "white_realization_match_max_v": float(abs(white - basewhite).max()),
                "noisy_minus_quiet_pair": stats(pair[1] - qp[1]),
                "invalid_pairs": int(pair[3].sum()),
                "raw_ra_sat": int(noisy.raw.ra_sat.sum()),
                "raw_adc2_over": int(noisy.raw.adc2_over.sum()),
            }
            arrays[name + "_pair_delta"] = pair[1] - qp[1]
        result["independent_white_controls"].append(out)
        np.savez_compressed(OUT / "raw_data" / f"{plant}_white_controls.npz", **arrays)

    # These are re-primed one-sample conversions, not consecutive valid pipeline
    # epochs. The arithmetic-only pair does NOT make them RTL-ready 40M/20M data.
    plant = "mismatch"
    fit = FrozenCalibration.from_dict(
        json.loads((OUT / "raw_data" / f"{plant}_frozen_fit.json").read_text())
    )
    for dither in ("off", "sampling", "quantizer"):
        cfg = cfg_for(plant, dither)
        pool = pool_for(cfg, plant)
        n_pair = 64
        e = np.random.default_rng(6421).uniform(-0.0025, 0.0025, n_pair)
        collected = {k: [] for k in ("quiet", "A_constant_RA_output", "B_RA_input_times_G")}
        ids = []
        gains = []
        for k in range(n_pair):
            for sign in (1, -1):
                raw = run_pipeline(
                    cfg, dc_input(sign * 0.3), 1, pool=pool, rng=np.random.default_rng(61002)
                )
                g = float(raw.g_vec[0])
                ids.append(raw.conv_slice_ids[0])
                gains.append(g)
                collected["quiet"].append(word(raw, fit))
                for name, H in [("A_constant_RA_output", cfg.g0), ("B_RA_input_times_G", g)]:
                    noisy = run_pipeline(
                        cfg,
                        dc_input(sign * 0.3),
                        1,
                        pool=pool,
                        rng=np.random.default_rng(61002),
                        ra_output_error_v=np.array([H * e[k]]),
                    )
                    collected[name].append(word(noisy, fit))

        def combine(parts, v_fs=cfg.v_fs):
            return CodeStream(
                np.concatenate([w.code for w in parts]),
                np.concatenate([w.clipped_low for w in parts]),
                np.concatenate([w.clipped_high for w in parts]),
                np.concatenate([w.analog_overflow for w in parts]),
                v_fs,
                20,
                max(w.peak_accumulator_bits for w in parts),
            )

        qp = pair_readout(combine(collected["quiet"]), difference=True)
        out = {
            "plant": plant,
            "dither": dither,
            "cfg": cfg.to_dict(),
            "status": "NONPIPELINED_REPRIMED_CONVERSIONS",
            "N_pair": n_pair,
            "effective_rate_hz": None,
            "raw_epoch_ids": "every one-sample run has sample_id0; not one continuous capture or a valid RTL input record",
            "all_groups_identical": bool(np.all(np.asarray(ids) == ids[0])),
            "group": np.asarray(ids[0]).tolist(),
            "G_min": min(gains),
            "G_max": max(gains),
            "predicted_continuous_shared_noise_pair_leak_v": 0.0,
            "noise": {},
        }
        arrays = {"pair_error_source_input_v": e, "quiet_pair": qp[1]}
        for name in ("A_constant_RA_output", "B_RA_input_times_G"):
            pair = pair_readout(combine(collected[name]), difference=True)
            out["noise"][name] = {
                "noisy_minus_quiet_pair": stats(pair[1] - qp[1]),
                "invalid_pairs": int(pair[3].sum()),
            }
            arrays[name + "_pair_delta"] = pair[1] - qp[1]
        result["same_group_controls"].append(out)
        np.savez_compressed(OUT / "raw_data" / f"nonpipeline_same_group_{dither}.npz", **arrays)
    (OUT / "followup_controls.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    )
    print(
        json.dumps(
            {
                "white_controls": len(result["independent_white_controls"]),
                "same_group_controls": len(result["same_group_controls"]),
            }
        )
    )
    if source_hashes() != source_sha:
        raise RuntimeError(
            "production source changed during the experiment; output provenance is invalid"
        )
    definitions = {
        "pair_signal_reference": "(x_even+x_odd)/2 from known source aperture times; not latest endpoint or unfiltered midpoint.",
        "mean_v": "Arithmetic mean signed error in V.",
        "std_v": "Population standard deviation about the error mean in V; removes DC error.",
        "rms_v": "sqrt(mean(error**2)) in V; retains DC error and is not interchangeable with std.",
        "quiet_pair_error": "CAAZ with zero added AZ error, minus the two-aperture signal reference; ordinary mismatch/quantization remains.",
        "ordinary_pair_error": "No input chopping, ordinary pair average, minus the same two-aperture reference.",
        "delta_noise_all": "Same chip/controls/RNG: noisy paired20bit output minus quiet paired20bit output, retaining all raw flags.",
        "linear_prediction_Khat": "e_pair*(H_even/Khat_even-H_odd/Khat_odd)/2; Khat derives only from frozen digital coefficients/masks.",
        "linear_prediction_physical_K": "Same formula with diagnostic physical K=G*alpha. Truth is plant/oracle-only, never a calibration feature.",
        "quantization_bound_v": "1.5 LSB20 + ADC2step/min(Khat), for the difference of two pair outputs in the unsaturated static regime.",
        "quantization_difference_floor": "Nonzero noisy-minus-quiet code RMS can remain even when continuous shared AZ leakage is zero; ADC2 and final word quantization are nonlinear. It is not evidence of a device-noise conflict.",
        "valid_intersection": "Statistics with this suffix explicitly use pairs valid in both captures; all-pair statistics and arrays are separately retained.",
        "G": "Residue gain C_signal/Cf, dimensionless. Sampling dither signal gain is K=G*alpha, not G alone.",
        "H_AZ": "A uses constant Gnom and B uses physical G_n; both multiply the same assumed input-equivalent e innovations. The paper does not supply the transfer of this project18-slice amplifier.",
    }
    summary_cells = []
    for cell in rows:
        record = {
            key: cell[key]
            for key in (
                "id",
                "plant",
                "profile",
                "scheduler_requested",
                "scheduler_actual",
                "dem_enable",
                "dem_bridge_enable",
                "dither",
                "N_raw",
                "N_pair",
                "input",
                "error_source",
                "training_fit_sha256",
                "G",
                "alpha",
                "K_true",
                "same_group_pairs",
                "pair_group_intersection_max",
                "unique_selected_groups",
                "unique_sid",
                "sid_equal_within_pair_fraction",
                "arrays_file",
            )
        }
        record["readout"] = {}
        for name, data in cell["results"].items():
            record["readout"][name] = {
                key: data[key]
                for key in (
                    "Khat",
                    "Khat_vs_K_relative_error",
                    "quiet_pair_error",
                    "ordinary_pair_error",
                    "quiet_invalid_pairs",
                    "ordinary_invalid_pairs",
                )
            }
            record["readout"][name]["noise"] = {}
            for domain, noise in data["noise"].items():
                record["readout"][name]["noise"][domain] = {
                    key: noise[key]
                    for key in (
                        "H_domain",
                        "delta_noise_all",
                        "delta_noise_valid_intersection",
                        "linear_prediction_Khat",
                        "linear_prediction_physical_K",
                        "observed_minus_prediction_valid",
                        "quantization_bound_v",
                        "invalid_pairs",
                        "valid_intersection_pairs",
                    )
                }
        summary_cells.append(record)
    summary = {
        "schema": "randomization-caaz-summary-v1",
        "source": lineage,
        "assumptions": {
            "slice_relative_sigma": 0.005,
            "unit_relative_sigma": 0.0002,
            "bridge_relative_sigma": 0.0002,
            "parasitic_relative_sigma": 0.0002,
            "grade": "ASSUMED experiment stress; not process data or the default mismatch sigma",
            "geometry": "18 slices,8 active,63 main+8 sub units",
            "raw_hz": 40e6,
            "pair_hz": 20e6,
            "rate_grade": "ENGINEERING_CHOICE; source prototype raw/effective rate remains unresolved",
        },
        "metric_definitions": definitions,
        "training": {
            plant: {
                key: data[key]
                for key in (
                    "n_training",
                    "bad_raw",
                    "rank",
                    "coefficients",
                    "condition",
                    "residual_rms_raout_v",
                    "training_digest",
                    "frozen_fit_sha256",
                    "frozen_weights_bytes_sha256",
                    "training_data_source",
                )
            }
            for plant, data in training_reports.items()
        },
        "matrix_cells": len(rows),
        "cells": summary_cells,
        "followup_controls": result,
        "production_source_unchanged": True,
    }
    (OUT / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    )
    (OUT / "COMPLETE.json").write_text(
        json.dumps(
            {
                "matrix_cells": len(rows),
                "independent_white_controls": len(result["independent_white_controls"]),
                "nonpipeline_same_group_controls": len(result["same_group_controls"]),
                "production_source_unchanged": True,
                "source_version": version,
            },
            indent=2,
        )
        + "\n"
    )
    return summary


def main(argv: list[str] | None = None) -> int:
    """Write one reproducible bounded experiment to a new output directory."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--out", type=Path, required=True, help="new output directory, must not already exist"
    )
    args = parser.parse_args(argv)
    if args.out.exists() or args.out.is_symlink():
        parser.error("--out must be a new directory; refusing to overwrite an existing path")
    try:
        args.out.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        parser.error("--out was created concurrently; no existing path was overwritten")
    (args.out / "raw_data").mkdir()
    summary = run_experiment(args.out)
    print(
        json.dumps(
            {
                "matrix_cells": summary["matrix_cells"],
                "white_controls": len(summary["followup_controls"]["independent_white_controls"]),
                "same_group_controls": len(summary["followup_controls"]["same_group_controls"]),
                "out": str(args.out),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
