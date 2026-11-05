#!/usr/bin/env python3
"""Run raw structural-DUT observation -> external fit -> loaded holdout.

The simulated analog plant has fixed mismatch per chip. Only external known
input values and the DUT's digital observation packet reach the fitter. This is
functional behavioral/RTL evidence, not an extracted or silicon measurement.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path

import numpy as np

from adi_model import Config
from adi_model.fixed_point import FixedPointReconstructor
from adi_model.pipeline import run_pipeline
from adi_model.rtl_observation import observations_from_rtl
from adi_model.weight_calibration import CalibrationSpec, fit_unit_weights

ROOT = Path(__file__).resolve().parents[1]


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def _read_capture(path: Path, spec: CalibrationSpec):
    packets = path.joinpath("observations.hex").read_text(encoding="utf-8").splitlines()
    data = observations_from_rtl(packets, spec, static_charge_domain=True)
    reference = _csv(path / "reference.csv")
    output = _csv(path / "outputs.csv")
    if len(reference) != len(packets) or len(output) != len(packets):
        raise ValueError("reference, observation and retirement counts differ")
    ref_ids = np.array([int(row["sample_id"]) for row in reference], dtype=np.int64)
    out_ids = np.array([int(row["sample_id"]) for row in output], dtype=np.int64)
    if not np.array_equal(data.sample_ids, ref_ids) or not np.array_equal(data.sample_ids, out_ids):
        raise ValueError("reference, observation and output IDs are not aligned")
    x = np.array([float(row["input_v"]) for row in reference])
    codes = np.array([int(row["code"]) for row in output], dtype=np.int64)
    flags = np.array([int(row["flags"]) for row in output], dtype=np.int64)
    analog_bad = np.array([int(row["analog_bad"]) for row in output], dtype=bool)
    if not np.array_equal(analog_bad, data.overflow):
        raise ValueError("per-result analog flags do not match the observation packet")
    return data, x, codes, flags


def _check_model_contract(cfg: Config, data, x: np.ndarray) -> None:
    """Compare the structural Python profile with the real observed schedule."""

    def known_input(time_s):
        index = np.rint(np.asarray(time_s) * cfg.fs).astype(np.int64)
        return x[np.clip(index, 0, len(x) - 1)]

    model = run_pipeline(cfg, known_input, len(x))
    for name, actual, observed in (
        ("sample ID", model.sample_id, data.sample_ids),
        ("slice order", model.conv_slice_ids, data.slice_ids),
        ("DEM state", model.sid, data.dem_state),
        ("coarse decision", model.coarse, data.rdac_code - data.bank_dither),
        ("sampling dither", model.sample.dither_bank_code, data.bank_dither),
        ("RDAC command", model.k, data.rdac_code),
    ):
        if not np.array_equal(actual, observed):
            raise AssertionError(f"structural Python/RTL {name} mismatch")


def _run_capture(
    binary: Path,
    temp_root: Path,
    output: Path,
    name: str,
    count: int,
    chip_seed: int,
    *,
    training: bool,
    weights: Path | None = None,
    offset: Path | None = None,
    fault_id: int | None = None,
    check_clear: bool = False,
    noise_rms_uv: float = 0.0,
) -> Path:
    """Run the same real RTL with a selected plant, data set and coefficient image."""
    destination = output / name
    destination.mkdir()
    alias = temp_root / name
    alias.symlink_to(destination, target_is_directory=True)
    cmd = [
        str(binary),
        f"+outdir={alias}",
        f"+samples={count}",
        f"+train={int(training)}",
        f"+chip_seed={chip_seed}",
        f"+noise_rms_uv={noise_rms_uv:.12g}",
    ]
    if weights is not None and offset is not None:
        weight_alias = temp_root / f"{name}_weights.hex"
        offset_alias = temp_root / f"{name}_offset.hex"
        weight_alias.symlink_to(weights)
        offset_alias.symlink_to(offset)
        cmd += [f"+weights={weight_alias}", f"+offset={offset_alias}"]
    if fault_id is not None:
        cmd.append(f"+fault_id={fault_id}")
    if check_clear:
        cmd.append("+check_clear=1")
    with (destination / "simulation.log").open("w", encoding="utf-8") as log:
        subprocess.run(
            cmd, cwd=temp_root, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=600
        )
    if (
        "STRUCTURAL_CALIBRATION_OBSERVE_COMPLETE"
        not in (destination / "simulation.log").read_text()
    ):
        raise RuntimeError(f"{name}: missing RTL completion marker")
    if (
        check_clear
        and "CAL_OBSERVER_CLEAR_PASS" not in (destination / "simulation.log").read_text()
    ):
        raise RuntimeError(f"{name}: missing observer clear marker")
    return destination


def run(
    output: Path, train_samples: int, held_samples: int, chip_seeds: list[int], noise_rms_uv: float
) -> dict:
    """Compile once and evaluate independent fitted holdouts for each chip seed."""
    if (
        not 1279 < train_samples <= 20000
        or not 16 <= held_samples <= 20000
        or not chip_seeds
        or any(not 0 <= seed <= 0xFFFF_FFFF for seed in chip_seeds)
        or not np.isfinite(noise_rms_uv)
        or noise_rms_uv < 0
    ):
        raise ValueError("invalid sample counts, chip seeds or input-referred noise")
    if output.exists():
        raise ValueError("output directory must not already exist")
    output.mkdir(parents=True)
    cfg = Config.rtl_structural(
        dem_enable=True,
        dem_mode="permute",
        dither_mode="sampling",
        dither_split_bank="sub",
        dither_discrete=True,
        enable_sampling_noise=False,
        ra_enable_noise=False,
        mismatch_enable=False,
        sadc_rdac_gain_mismatch=0,
    )
    spec = CalibrationSpec.from_config(cfg)
    nominal_w = np.full(spec.shape, 0.0625)
    nominal_w[:, 63:] = 0.0078125
    nominal_decoder = FixedPointReconstructor.from_weights(spec, nominal_w)
    cmd = shlex.split(os.environ.get("VERILATOR", "verilator"))
    if not cmd or shutil.which(cmd[0]) is None:
        raise RuntimeError("Verilator is required; set VERILATOR to a working command")
    inputs = [ROOT / line for line in (ROOT / "rtl/rtl_sources.f").read_text().splitlines() if line]
    inputs += [ROOT / "sim/tb/structural_calibration_observe_tb.sv"]
    hash_inputs = inputs + sorted((ROOT / "rtl/params").glob("*.vh"))
    hash_inputs.extend(sorted((ROOT / "src/adi_model").glob("*.py")))
    hash_inputs.append(Path(__file__).resolve())
    source_hashes_before = {str(p.relative_to(ROOT)): _sha(p) for p in hash_inputs}
    results = []
    with tempfile.TemporaryDirectory(prefix="sar_calibration_") as temp:
        t = Path(temp)
        stage = t / "repo"
        stage.symlink_to(ROOT, target_is_directory=True)
        build = t / "build"
        args = [
            *cmd,
            "--binary",
            "--timing",
            "--assert",
            "--unroll-count",
            "8192",
            "-j",
            "2",
            "-Wno-fatal",
            "-Werror-PINMISSING",
            "-Werror-SELRANGE",
            "--top-module",
            "structural_calibration_observe_tb",
            "-Irtl/params",
            "--Mdir",
            str(build),
            *[str(p.relative_to(ROOT)) for p in inputs],
        ]
        with (output / "build.log").open("w", encoding="utf-8") as log:
            subprocess.run(
                args, cwd=stage, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=600
            )
        binary = build / "Vstructural_calibration_observe_tb"
        for seed in chip_seeds:
            train = _run_capture(
                binary,
                t,
                output,
                f"chip{seed}_train",
                train_samples,
                seed,
                training=True,
                noise_rms_uv=noise_rms_uv,
            )
            train_data, train_x, _, train_flags = _read_capture(train, spec)
            if np.any(train_data.overflow) or np.any(train_flags):
                raise ValueError("training contains analog or digital errors")
            _check_model_contract(cfg, train_data, train_x)
            fitted = fit_unit_weights(train_data, train_x)
            decoder = FixedPointReconstructor.from_frozen_calibration(spec, fitted)
            chip_dir = output / f"chip{seed}_coefficients"
            chip_dir.mkdir()
            weights = chip_dir / "weights.hex"
            weights.write_text("".join(f"{int(w):012x}\n" for w in decoder.weights_q.flat))
            offset = chip_dir / "offset.hex"
            offset.write_text(f"{decoder.offset_q & ((1 << 64) - 1):016x}\n")
            (chip_dir / "frozen.json").write_text(json.dumps(fitted.to_dict(), indent=2) + "\n")
            (chip_dir / "registers.json").write_text(json.dumps(decoder.to_dict(), indent=2) + "\n")
            baseline = _run_capture(
                binary,
                t,
                output,
                f"chip{seed}_holdout_nominal",
                held_samples,
                seed,
                training=False,
                noise_rms_uv=noise_rms_uv,
            )
            corrected = _run_capture(
                binary,
                t,
                output,
                f"chip{seed}_holdout_fitted",
                held_samples,
                seed,
                training=False,
                weights=weights,
                offset=offset,
                noise_rms_uv=noise_rms_uv,
            )
            for filename in ("observations.hex", "reference.csv"):
                if (
                    baseline.joinpath(filename).read_bytes()
                    != corrected.joinpath(filename).read_bytes()
                ):
                    raise ValueError(
                        "nominal and fitted holdouts did not use identical observations"
                    )
            held, x, before, before_flags = _read_capture(baseline, spec)
            held_after, x_after, after, after_flags = _read_capture(corrected, spec)
            _check_model_contract(cfg, held, x)
            if (
                not np.array_equal(x, x_after)
                or np.any(held.overflow)
                or np.any(held_after.overflow)
            ):
                raise ValueError("held-out inputs or analog error flags differ")
            expected_before = nominal_decoder.reconstruct(held)
            expected_after = decoder.reconstruct(held)
            if (
                np.any(before_flags)
                or np.any(after_flags)
                or not np.array_equal(before, expected_before.code)
                or not np.array_equal(after, expected_after.code)
            ):
                raise AssertionError("RTL holdout differs from independent integer reconstruction")
            lsb = 2 * spec.v_fs / 2**20
            before_error = expected_before.voltage - x
            after_error = expected_after.voltage - x
            before_std = float(np.std(before_error))
            after_std = float(np.std(after_error))
            limit = 0.5 if noise_rms_uv == 0 else 1.0
            if not after_std < limit * before_std:
                raise AssertionError(
                    "frozen coefficients did not improve independent holdout error"
                )
            results.append(
                {
                    "chip_seed": seed,
                    "training_samples": len(train_x),
                    "holdout_samples": len(x),
                    "matrix_rank": fitted.rank,
                    "matrix_condition": fitted.condition,
                    "noise_rms_uv": noise_rms_uv,
                    "before_error_std_uv": before_std * 1e6,
                    "after_error_std_uv": after_std * 1e6,
                    "before_error_mean_uv": float(np.mean(before_error)) * 1e6,
                    "after_error_mean_uv": float(np.mean(after_error)) * 1e6,
                    "before_error_rms_uv": float(np.sqrt(np.mean(before_error**2))) * 1e6,
                    "after_error_rms_uv": float(np.sqrt(np.mean(after_error**2))) * 1e6,
                    "before_error_std_lsb": before_std / lsb,
                    "after_error_std_lsb": after_std / lsb,
                    "rtl_matches_independent_integer": True,
                    "python_rtl_sample_switch_contract": True,
                    "holdout_observation_sha256": _sha(baseline / "observations.hex"),
                    "training_observation_sha256": _sha(train / "observations.hex"),
                    "training_reference_sha256": _sha(train / "reference.csv"),
                    "frozen_sha256": _sha(chip_dir / "frozen.json"),
                    "register_sha256": _sha(chip_dir / "registers.json"),
                }
            )
            print(
                f"chip {seed}: rank={fitted.rank}, error std {before_std*1e6:.3f} -> {after_std*1e6:.3f} uV",
                flush=True,
            )
        fault = _run_capture(
            binary,
            t,
            output,
            "fault_probe",
            32,
            chip_seeds[0],
            training=False,
            fault_id=4,
            check_clear=True,
            noise_rms_uv=noise_rms_uv,
        )
        fault_data, fault_x, _, _ = _read_capture(fault, spec)
        if fault_data.sample_ids is None or np.count_nonzero(fault_data.overflow) != 1:
            raise AssertionError("analog fault did not remain attached to one observed sample")
        if int(fault_data.sample_ids[np.flatnonzero(fault_data.overflow)[0]]) != 4:
            raise AssertionError("analog fault is attached to the wrong sample ID")
        try:
            fit_unit_weights(fault_data, fault_x)
        except ValueError as exc:
            if "clipped conversions" not in str(exc):
                raise
        else:
            raise AssertionError("training accepted an analog-fault observation")
    source_hashes_after = {str(p.relative_to(ROOT)): _sha(p) for p in hash_inputs}
    if source_hashes_before != source_hashes_after:
        raise RuntimeError("RTL or testbench source changed during the run")
    summary = {
        "scope": "structural digital RTL with a static simulated mismatch plant; not transistor, PDK or silicon",
        "input_contract": "digital RTL packets and separately known input only; no physical unit weights in fitter",
        "training_holdout": "distinct stimulus streams, same plant seed; nominal and fitted holdouts use identical observations",
        "results": results,
        "fault_sample_id": 4,
        "fault_rejected_by_fitter": True,
        "same_edge_clear_cancels_observer": True,
        "rtl_clock_hz": 640_000_000,
        "sample_rate_hz": 40_000_000,
        "source_sha256": source_hashes_before,
    }
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary


def main() -> None:
    """Parse paths and bounded experiment settings."""
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--train", type=int, default=4096)
    ap.add_argument("--holdout", type=int, default=512)
    ap.add_argument("--chip-seeds", type=int, nargs="+", default=[42, 91])
    ap.add_argument("--noise-rms-uv", type=float, default=0.0)
    args = ap.parse_args()
    run(args.output.resolve(), args.train, args.holdout, args.chip_seeds, args.noise_rms_uv)
    print("STRUCTURAL_CALIBRATION_CLOSURE_COMPLETE", flush=True)


if __name__ == "__main__":
    main()
