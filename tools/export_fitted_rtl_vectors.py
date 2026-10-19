#!/usr/bin/env python3
"""Generate a deterministic fitted-weight RTL holdout fixture (18 physical slices).

Training sees only a separate reference input and digital observations.  The
holdout retains the same simulated physical pool but changes tone and phase.
The vectors carry quantized coefficients, observed backend codes and physical
switch masks; no true capacitance or ideal residue is exported to the RTL.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

from adi_model import Config, sine_input
from adi_model.dem import split_switch_command
from adi_model.fixed_point import FixedPointReconstructor
from adi_model.pipeline import run_pipeline
from adi_model.weight_calibration import CalibrationSpec, DigitalObservation, fit_unit_weights


def _json(value: object) -> str:
    return json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n"


def build() -> dict[str, str]:
    cfg = Config.paper_literal(
        n_slices=18,
        n_active=8,
        dem_enable=True,
        dem_mode="permute",
        dither_mode="sampling",
        mismatch_sigma0=0.005,
        sadc_rdac_gain_mismatch=0,
    )
    train_fn = sine_input(2.4, cfg.fs * 103 / 4096)
    training = run_pipeline(cfg, train_fn, 2048)
    model = fit_unit_weights(DigitalObservation.from_result(training), train_fn(training.sample.t1))
    held_fn = sine_input(2.2, cfg.fs * 307 / 8192, 0.5)
    held = run_pipeline(cfg, held_fn, 128, pool=training.pool, rng=np.random.default_rng(893))
    data = DigitalObservation.from_result(held)
    spec = CalibrationSpec.from_config(cfg)
    decoder = FixedPointReconstructor.from_frozen_calibration(spec, model)
    nominal = FixedPointReconstructor.from_result(held)
    fitted_words = decoder.reconstruct(data)
    nominal_words = nominal.reconstruct(data)
    fitted_rms = float(np.std(fitted_words.voltage - held.x_ref))
    nominal_rms = float(np.std(nominal_words.voltage - held.x_ref))
    if fitted_rms >= 0.3 * nominal_rms:
        raise AssertionError("fitted coefficients did not improve independent holdout")
    if np.any(fitted_words.clipped_low | fitted_words.clipped_high | fitted_words.analog_overflow):
        raise AssertionError("holdout unexpectedly clips or overflows")

    command = split_switch_command(spec, data.rdac_code, data.dem_state)
    plus = np.empty((len(data.rdac_code), spec.n_active, spec.shape[1]), dtype=np.int8)
    for begin, size, order, counts in (
        (0, spec.dac_n_main, command.main_order, command.main_counts),
        (spec.dac_n_main, spec.dac_n_sub, command.sub_order, command.sub_counts),
    ):
        selected = np.clip(counts[..., None] - np.arange(size), 0, 1).astype(np.int8)
        np.put_along_axis(plus[:, :, begin : begin + size], order[:, None, :], selected, axis=2)
    mask_count = int(2 * spec.dither_units_range)
    if spec.dither_split_bank != "sub" or mask_count != 4:
        raise AssertionError("fixture expects the last four sub units as sampling dither")

    registers = decoder.to_dict()
    weight_lines = [f"{int(weight):012x}" for weight in decoder.weights_q.flat]
    sample_lines = []
    for i in range(len(data.adc2_code)):
        ids = sum(int(data.slice_ids[i, a]) << (5 * a) for a in range(spec.n_active))
        main = sum(
            int(plus[i, a, u]) << (spec.dac_n_main * a + u)
            for a in range(spec.n_active)
            for u in range(spec.dac_n_main)
        )
        sub = sum(
            int(plus[i, a, spec.dac_n_main + u]) << (spec.dac_n_sub * a + u)
            for a in range(spec.n_active)
            for u in range(spec.dac_n_sub)
        )
        rails = sum(
            int(u < mask_count // 2 + int(data.bank_dither[i])) << u for u in range(mask_count)
        )
        injection = int(
            np.rint(
                data.common_injection_v[i] / spec.v_fs * 2**decoder.format.voltage_fraction_bits
            )
        )
        sample_lines.append(
            f"{ids:010x} {main:0126x} {sub:016x} {rails:01x} "
            f"{int(data.adc2_code[i]):03x} {injection & ((1 << 64) - 1):016x} "
            f"{int(fitted_words.code[i]):05x} 00"
        )
    artifacts = {
        "fitted18_frozen.json": _json(model.to_dict()),
        "fitted18_registers.json": _json(registers),
        "fitted18_weights.hex": "\n".join(weight_lines) + "\n",
        "fitted18_scalars.hex": (
            f"{decoder.offset_q & ((1 << 64) - 1):016x} "
            f"{decoder.adc2_min_q & ((1 << 64) - 1):016x} "
            f"{decoder.adc2_max_q & ((1 << 64) - 1):016x}\n"
        ),
        "fitted18_holdout.hex": "\n".join(sample_lines) + "\n",
    }
    artifacts["fitted18_manifest.json"] = _json(
        {
            "schema_version": 1,
            "training_samples": model.training_samples,
            "rank": model.rank,
            "condition": model.condition,
            "training_digest": model.training_digest,
            "holdout_samples": len(sample_lines),
            "fitted_rms_v": fitted_rms,
            "nominal_rms_v": nominal_rms,
            "artifacts_sha256": {
                name: hashlib.sha256(content.encode("utf-8")).hexdigest()
                for name, content in artifacts.items()
            },
        }
    )
    return artifacts


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=Path("sim/vectors"))
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    for name, content in build().items():
        path = args.out / name
        if args.check:
            if not path.is_file() or path.read_bytes() != content.encode("utf-8"):
                raise SystemExit(f"fitted RTL vector drift: {path}")
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
