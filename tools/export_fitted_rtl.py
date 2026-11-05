#!/usr/bin/env python3
"""将外部固定拟合系数量化为显式绑定的 RTL 寄存器镜像。

本工具不训练、不读取物理真值，也不回退到名义系数。
输出训练统计来自调用方提供的拟合记录，不代表本次重新执行过训练。
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
from pathlib import Path

from adi_model.fixed_point import FixedPointReconstructor
from adi_model.weight_calibration import FrozenCalibration
from export_rtl_params import _json_text, _object, load_contract


def build_fitted_registers(
    config_path: Path, format_path: Path, binding_path: Path, frozen_path: Path
) -> tuple[str, str]:
    """校验所选接口及完整校准规格，再量化调用方提供的固定系数。

    Returns:
        寄存器镜像 JSON 文本与导出清单 JSON 文本；输入训练统计仅作为元数据保留。
    """
    data = load_contract(config_path, format_path, binding_path)
    if not frozen_path.is_file() or frozen_path.stat().st_size >= 300 * 1024 * 1024:
        raise ValueError("frozen calibration must be a readable file smaller than 300 MB")
    frozen = FrozenCalibration.from_dict(_object(frozen_path))
    decoder = FixedPointReconstructor.from_frozen_calibration(
        data["spec"], frozen, format=data["format"]
    )
    register_text = _json_text(decoder.to_dict())
    manifest = {
        "schema_version": 1,
        "scope": "FROZEN_FIT_REGISTER_EXPORT_NOT_TRAINING_OR_ANALOG_VALIDATION",
        "input_sha256": {
            **data["input_sha256"],
            "frozen_fit": hashlib.sha256(frozen_path.read_bytes()).hexdigest(),
        },
        "register_sha256": hashlib.sha256(register_text.encode("utf-8")).hexdigest(),
        "training_statistics_are_supplied_metadata": True,
        "training_digest": frozen.training_digest,
        "training_samples": frozen.training_samples,
        "rank": frozen.rank,
        "condition": frozen.condition,
        "residual_rms_v": frozen.residual_rms_v,
        "shape": list(data["spec"].shape),
        "weight_unit": "effective C/Cf",
        "voltage_register_unit": "normalized V/Vfs",
    }
    return register_text, _json_text(manifest)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    for name in ("config", "format", "binding", "frozen", "out"):
        parser.add_argument("--" + name, required=True, type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    register, manifest = build_fitted_registers(args.config, args.format, args.binding, args.frozen)
    files = {"registers.json": register, "fitted_register_manifest.json": manifest}
    if args.check:
        for name, content in files.items():
            target = args.out / name
            if not target.is_file() or target.read_bytes() != content.encode("utf-8"):
                raise SystemExit(f"fitted register drift: {target}")
    else:
        args.out.mkdir(parents=True, exist_ok=False)
        try:
            for name, content in files.items():
                (args.out / name).write_text(content, encoding="utf-8")
        except OSError:
            shutil.rmtree(args.out)
            raise
    print("FITTED_REGISTER_EXPORT_COMPLETE files=2 estimator_executed=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
