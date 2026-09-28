#!/usr/bin/env python3
"""Export an externally fitted physical-unit calibration as RTL registers.

The training JSON is produced by FrozenCalibration.to_dict().  This command
does not estimate coefficients or inspect simulated physical truth.  It only
checks the frozen fit against the selected nominal interface, quantizes it
once, and records which training file supplied the register image.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import replace
from pathlib import Path

from adi_model.fixed_point import FixedPointReconstructor
from adi_model.weight_calibration import CalibrationSpec, FrozenCalibration
from export_rtl_params import _CONFIG_FACTORIES, _json_text


def build_fitted_registers(
    config_name: str,
    frozen_json: bytes,
    *,
    dither_mode: str | None = None,
    dem_enable: bool | None = None,
    dem_mode: str | None = None,
) -> tuple[str, str]:
    """Return a canonical register image and a provenance sidecar."""
    cfg = _CONFIG_FACTORIES[config_name]()
    if dither_mode is not None:
        cfg = replace(cfg, dither_mode=dither_mode)
    if dem_enable is not None:
        cfg = replace(cfg, dem_enable=dem_enable)
    if dem_mode is not None:
        cfg = replace(cfg, dem_mode=dem_mode)
    spec = CalibrationSpec.from_config(cfg)
    frozen = FrozenCalibration.from_dict(json.loads(frozen_json))
    decoder = FixedPointReconstructor.from_frozen_calibration(spec, frozen)
    register_text = _json_text(decoder.to_dict())
    register_sha = hashlib.sha256(register_text.encode("utf-8")).hexdigest()
    manifest = {
        "schema_version": 1,
        "config": config_name,
        "dither_mode": cfg.dither_mode,
        "dem_enable": cfg.dem_enable,
        "dem_mode": cfg.dem_mode,
        "register_sha256": register_sha,
        "frozen_file_sha256": hashlib.sha256(frozen_json).hexdigest(),
        "training_digest": frozen.training_digest,
        "training_samples": frozen.training_samples,
        "rank": frozen.rank,
        "condition": frozen.condition,
        "residual_rms_v": frozen.residual_rms_v,
        "weight_unit": "effective C/Cf",
        "weight_fraction_bits": decoder.format.weight_fraction_bits,
        "voltage_fraction_bits": decoder.format.voltage_fraction_bits,
        "shape": list(spec.shape),
        "v_fs": spec.v_fs,
    }
    return register_text, _json_text(manifest)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--config", required=True, choices=sorted(_CONFIG_FACTORIES))
    ap.add_argument("--dither-mode", choices=("off", "analog", "quantizer", "sampling"))
    ap.add_argument("--dem-enable", action="store_true")
    ap.add_argument("--dem-mode", choices=("rotate", "permute"))
    ap.add_argument("--frozen", required=True, type=Path)
    ap.add_argument("--out-registers", required=True, type=Path)
    ap.add_argument("--out-manifest", required=True, type=Path)
    ap.add_argument("--check", action="store_true", help="verify existing outputs byte for byte")
    args = ap.parse_args(argv)
    if args.out_registers == args.out_manifest or args.frozen in (
        args.out_registers,
        args.out_manifest,
    ):
        ap.error("input and output paths must be distinct")
    register_text, manifest_text = build_fitted_registers(
        args.config,
        args.frozen.read_bytes(),
        dither_mode=args.dither_mode,
        dem_enable=True if args.dem_enable else None,
        dem_mode=args.dem_mode,
    )
    for path, content in (
        (args.out_registers, register_text),
        (args.out_manifest, manifest_text),
    ):
        if args.check:
            if not path.is_file() or path.read_bytes() != content.encode("utf-8"):
                raise SystemExit(f"calibration export drift: {path}")
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
