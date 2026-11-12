"""行为指纹：重构前后的逐位对照基线。

搬家（MOVE/ADAPT）必须证明"行为没变"。本脚本把几组关键配置的输出压成 SHA256，
迁移前后各跑一次，指纹相同才算搬家成功；指纹不同就是改到了行为。

覆盖：
* 静态名义链（默认模型，512 笔，dither 关闭）；
* DEM 开关两种（`dem_enable` 切换掩码绑定与状态序列）；
* 定点解码输出（`codes.voltage`）与浮点重构（`raw.out`）分开记录。

运行：
    # 写新基线（默认拒绝覆盖已有文件）
    PYTHONPATH=src python3 tools/behavior_fingerprint.py --out docs/restructure

    # 对照基线：逐字段比较，首处差异定位，非零退出码表示失败
    PYTHONPATH=src python3 tools/behavior_fingerprint.py --check docs/restructure/behavior_fingerprint.json
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

from tools.run_p4_ablation import FS_HZ, V_FS, _build_model, _run

from adi_model.control.dem3d import Dem3State, fig11_masks, round_robin_plan
from adi_model.simulation.slice_pool import PhysicalSlicePool

N_SAMPLES = 512
TONE_HZ = 1_000_000


def _digest(array: np.ndarray) -> str:
    return hashlib.sha256(np.ascontiguousarray(array).tobytes()).hexdigest()


def _compare(expected: dict, actual: dict) -> int:
    """Print the first differing digest and return a process exit code.

    The comparison walks every ``*_sha256`` field in the baseline. ``rms_v`` and
    the sample counts are deliberately *not* criteria — they are sanity checks
    that catch a crashed or truncated run, not behaviour.
    """
    failures = 0
    for group, fields in expected["entries"].items():
        if group not in actual["entries"]:
            print(f"MISSING GROUP: {group}")
            failures += 1
            continue
        for name, want in fields.items():
            if not name.endswith("_sha256"):
                continue
            got = actual["entries"][group].get(name)
            if got != want:
                if failures == 0:
                    print("First mismatch")
                    print(f"  group        : {group}")
                    print(f"  field        : {name}")
                    print(f"  baseline     : {want}")
                    print(f"  current      : {got}")
                else:
                    print(f"  also differs : {group}.{name}")
                failures += 1
    if failures:
        print(f"\nBEHAVIOR DRIFT: {failures} field(s) differ from the baseline")
        return 1
    print("behavior fingerprint matches the baseline (all *_sha256 fields equal)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="docs/restructure")
    parser.add_argument("--check", default=None,
                        help="compare against this baseline instead of writing")
    parser.add_argument("--force", action="store_true",
                        help="allow overwriting an existing baseline file")
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    # 失配 σ 不写死：取 DefaultParameters 的唯一真源（由论文两指标反解，见
    # docs/mechanisms/paper_metrics_calibration.md）。写死 1e-3 会在参数调整后
    # 让指纹基线悄悄脱离实际模型。
    from adi_model.defaults import DefaultParameters as _DefaultParameters

    mismatch_std = _DefaultParameters().unit_mismatch_std
    model = _build_model(mismatch_std, True)
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
        mask_binding=fig11_masks,
        acquisition_binding=None,
        schedule_binding=plan,
    )
    codes = np.arange(64, dtype=np.int64) * 8
    states = Dem3State.sequence(64, columns=63)
    command = fig11_masks(pool.geometry, codes, states, plan.conv)
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
        # 描述必须从**实际参数**读，不能写死：写死会在参数调整后留下虚假记录
        # （实测曾写成 "mismatch=1e-3" 而实际已是 1e-4）。
        "model": (
            "DefaultModel("
            f"mismatch={model.parameters.unit_mismatch_std:.0e}, "
            f"adc2_n_bits={model.cfg.adc2_n_bits}, "
            "noise on)"
        ),
        "entries": entry,
        "compare": "all *_sha256 fields must match; rms_v is a sanity check, not a criterion",
    }
    target = out / "behavior_fingerprint.json"
    if args.check is not None:
        baseline = json.loads(Path(args.check).read_text(encoding="utf-8"))
        return _compare(baseline, document)
    if target.exists() and not args.force:
        print(f"refusing to overwrite the existing baseline at {target}")
        print("pass --force to replace it, or --check to compare against it")
        return 2
    target.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps({k: v if k == "mask_layer" else {
        kk: (vv[:16] if isinstance(vv, str) else vv) for kk, vv in v.items()
    } for k, v in entry.items()}, indent=2))
    print(f"\nwritten to {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
