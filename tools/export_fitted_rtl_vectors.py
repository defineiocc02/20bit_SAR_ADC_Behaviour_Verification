#!/usr/bin/env python3
"""以调用方提供的固定拟合系数投影留出观测。

与训练数据的独立性由调用方保证。工具不创建训练波形、训练样本或物理电容池。
固定系数与观测分别输入，各自文件摘要写入输出包，便于追溯。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from adi_model.fixed_point import FixedPointReconstructor
from export_fitted_rtl import build_fitted_registers
from export_rtl_params import _digest, _json_text, load_contract
from export_rtl_vectors import build_vectors, write_bundle


def build(
    config_path: Path,
    format_path: Path,
    binding_path: Path,
    frozen_path: Path,
    observations_path: Path,
) -> dict[str, str]:
    register_text, fit_manifest = build_fitted_registers(
        config_path, format_path, binding_path, frozen_path
    )
    decoder = FixedPointReconstructor.from_dict(json.loads(register_text))
    contract = load_contract(config_path, format_path, binding_path)
    files = build_vectors(contract, decoder, observations_path)
    manifest = json.loads(files["reconstruction_manifest.json"])
    manifest["input_sha256"]["frozen_fit"] = _digest(frozen_path.read_bytes())
    manifest["fitted_register_manifest_sha256"] = _digest(fit_manifest.encode("utf-8"))
    files["reconstruction_manifest.json"] = _json_text(manifest)
    files["registers.json"] = register_text
    files["fitted_register_manifest.json"] = fit_manifest
    return files


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    for name in ("config", "format", "binding", "frozen", "observations", "out"):
        parser.add_argument("--" + name, required=True, type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    files = build(args.config, args.format, args.binding, args.frozen, args.observations)
    write_bundle(files, args.out, check=args.check)
    print("FROZEN_HOLDOUT_VECTORS_COMPLETE estimator_executed=0 physical_mapping_verified=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
