"""行为指纹：重构前后的逐位对照基线。

搬家（MOVE/ADAPT）必须证明"行为没变"。本脚本把几组关键配置的输出压成 SHA256，
迁移前后各跑一次，指纹相同才算搬家成功；指纹不同就是改到了行为。

覆盖：
* 静态名义链（默认模型，512 笔，dither 关闭）；
* DEM 开关两种（`dem_enable` 切换掩码绑定与状态序列）；
* 定点解码输出（`codes.voltage`）与浮点重构（`raw.out`）分开记录。

运行：
    PYTHONPATH=src python3 tools/behavior_fingerprint.py --out docs/restructure
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from adi_model.dem3d import Dem3State, round_robin_plan, unary_rotation_masks
from adi_model.slice_pool import PhysicalSlicePool
from tools.run_p4_ablation import FS_HZ, V_FS, _build_model, _run

N_SAMPLES = 512
TONE_HZ = 1_000_000


def _digest(array: np.ndarray) -> str:
    return hashlib.sha256(np.ascontiguousarray(array).tobytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="docs/restructure")
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    model = _build_model(1e-3, True)
    tone = lambda t: V_FS * 0.9 * np.sin(2 * np.pi * TONE_HZ * t)  # noqa: E731
    entry = {}

    for label, dem in (("static_dem_off", False), ("static_dem_on", True)):
        raw, voltage = _run(model, tone, N_SAMPLES, dem=dem, uniform_plan=dem)
        entry[label] = {
            "codes_voltage_sha256": _digest(np.asarray(voltage, dtype=float)),
            "float_out_sha256": _digest(np.asarray(raw.out, dtype=float)),
            "coarse_sha256": _digest(np.asarray(raw.coarse, dtype=np.int64)),
            "adc2_code_sha256": _digest(np.asarray(raw.adc2_code, dtype=np.int64)),
            "main_masks_sha256": _digest(np.asarray(raw.main_masks, dtype=np.int8)),
            "samples": int(N_SAMPLES),
            "rms_v": float(np.sqrt(np.mean(np.asarray(voltage, dtype=float) ** 2))),
        }

    # 掩码层单独一条：不跑管线，只压 mask，便于定位"是掩码变了还是模拟变了"
    plan = round_robin_plan(64)
    pool = PhysicalSlicePool(
        model.cfg,
        chip=model.chip,
        nominal_codebook=model.codebook,
        mask_binding=unary_rotation_masks,
        acquisition_binding=None,
        schedule_binding=plan,
    )
    codes = np.arange(64, dtype=np.int64) * 8
    states = Dem3State.sequence(64, columns=63)
    command = unary_rotation_masks(pool.geometry, codes, states, plan.conv)
    entry["mask_layer"] = {
        "main_on_sha256": _digest(np.asarray(command.main_on, dtype=np.int8)),
        "sub_on_sha256": _digest(np.asarray(command.sub_on, dtype=np.int8)),
        "samples": 64,
    }

    document = {
        "purpose": "bit-identical baseline for the directory migration; rerun before/after a MOVE",
        "fs_hz": FS_HZ,
        "v_fs_v": V_FS,
        "seed": 20261105,
        "model": "DefaultModel(mismatch=1e-3, noise on)",
        "entries": entry,
        "compare": "all *_sha256 fields must match; rms_v is a sanity check, not a criterion",
    }
    (out / "behavior_fingerprint.json").write_text(
        json.dumps(document, indent=2, ensure_ascii=False) + "\n"
    )
    print(json.dumps({k: v if k == "mask_layer" else {
        kk: (vv[:16] if isinstance(vv, str) else vv) for kk, vv in v.items()
    } for k, v in entry.items()}, indent=2))
    print(f"\nwritten to {out/'behavior_fingerprint.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
