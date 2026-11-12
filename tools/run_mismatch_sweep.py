"""失配预算扫描：单位电容 σ 与 INL/DNL 的定量关系，DEM 开/关各一条。

回答的问题不是"INL 好不好"，而是"这个 INL 是不是失配本来就应该给出的值"。
三条曲线（无 DEM / 三维 DEM / 逐码校准）放在同一 σ 轴上，斜率就是机制的指纹：

* 无 DEM：INL 与 σ 成正比，比例系数由布朗桥方差决定（见文档推导）；
* DEM：斜率变小（失配被摊平），但逐笔噪声上升；
* 逐码校准：截距与斜率同时被压，剩余项与 σ 无关的部分是表项噪声。

运行：
    PYTHONPATH=src python3 tools/run_mismatch_sweep.py --out docs/reviews/20261113_p3p4/data
"""

from __future__ import annotations

import argparse
import json

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.run_p4_ablation import (  # noqa: E402  (脚本入口需先补 sys.path)
    V_FS,
    _build_model,
    _static,
    _train_calibration,
)

SIGMAS = (0.0, 1e-4, 3e-4, 1e-3, 3e-3)


def _row(model, *, dem: bool, calibration, points: int, repeats: int) -> dict:
    metrics = _static(
        model,
        dem=dem,
        uniform_plan=dem,
        points=points,
        repeats=repeats,
        calibration=calibration,
    )
    return {
        "inl_lsb20": metrics["inl_max_lsb20"],
        "inl_ppm_fs": metrics["inl_max_ppm_fs"],
        "dnl_bin_lsb20": metrics["dnl_bin_lsb20_max"],
        "sigma_e_lsb20": metrics["sigma_e_mean_lsb20"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="mismatch -> INL/DNL sweep")
    parser.add_argument("--out", default="docs/reviews/20261113_p3p4/data")
    parser.add_argument("--points", type=int, default=256)
    parser.add_argument("--repeats", type=int, default=8)
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    rows = []
    for sigma in SIGMAS:
        model = _build_model(sigma, True)
        row = {"sigma_unit": sigma, "sigma_ppm": sigma * 1e6}
        row["plain"] = _row(
            model, dem=False, calibration=None, points=args.points, repeats=args.repeats
        )
        row["dem"] = _row(
            model, dem=True, calibration=None, points=args.points, repeats=args.repeats
        )
        if sigma > 0:
            calibration, _info = _train_calibration(model)
            row["calibrated"] = _row(
                model,
                dem=False,
                calibration=calibration,
                points=args.points,
                repeats=args.repeats,
            )
        else:
            row["calibrated"] = None
        rows.append(row)
        print(
            f"sigma={sigma:7.1e}  plain={row['plain']['inl_ppm_fs']:6.2f} ppm  "
            f"dem={row['dem']['inl_ppm_fs']:6.2f} ppm  "
            f"cal={'-' if row['calibrated'] is None else format(row['calibrated']['inl_ppm_fs'], '6.2f')} ppm"
        )

    positive = [row for row in rows if row["sigma_unit"] > 0]
    slopes = {
        "plain_ppm_per_unit_sigma": float(
            np.polyfit(
                [row["sigma_unit"] for row in positive],
                [row["plain"]["inl_ppm_fs"] for row in positive],
                1,
            )[0]
        ),
        "dem_ppm_per_unit_sigma": float(
            np.polyfit(
                [row["sigma_unit"] for row in positive],
                [row["dem"]["inl_ppm_fs"] for row in positive],
                1,
            )[0]
        ),
    }
    document = {
        "profile": "adi_isscc2024_40m_6vpp",
        "fs_hz": 40e6,
        "v_fs_v": V_FS,
        "lsb20_v": 2 * V_FS / 2**20,
        "points": args.points,
        "repeats": args.repeats,
        "seed": 20261105,
        "rows": rows,
        "slopes": slopes,
        "paper_inl_ppm": 2.2,
        "implied_sigma_for_paper_inl": float(2.2 / slopes["plain_ppm_per_unit_sigma"]),
        "evidence_level": "L1 behavioral; endpoint-fit INL on a sampled ramp, not code-density INL",
        "notes": [
            "sigma 是逐 (slice, element) 的相对标准差，与 bridge sigma 取同一值，feedback sigma=0",
            "INL 为 256 点斜坡端点拟合，点数少于 2**20，数值偏乐观",
            "implied_sigma 是把论文 2.2 ppm 反解到本模型斜率上，不代表论文的器件失配",
        ],
    }
    (out / "mismatch_sweep.json").write_text(
        json.dumps(document, indent=2, ensure_ascii=False) + "\n"
    )
    header = ["sigma_unit", "plain_inl_ppm", "dem_inl_ppm", "cal_inl_ppm", "plain_sigma_e", "dem_sigma_e"]
    lines = [",".join(header)]
    for row in rows:
        calibrated = row["calibrated"]["inl_ppm_fs"] if row["calibrated"] else ""
        lines.append(
            ",".join(
                [
                    f"{row['sigma_unit']:.1e}",
                    f"{row['plain']['inl_ppm_fs']:.2f}",
                    f"{row['dem']['inl_ppm_fs']:.2f}",
                    "" if calibrated == "" else f"{calibrated:.2f}",
                    f"{row['plain']['sigma_e_lsb20']:.2f}",
                    f"{row['dem']['sigma_e_lsb20']:.2f}",
                ]
            )
        )
    (out / "mismatch_sweep.csv").write_text("\n".join(lines) + "\n")
    print(json.dumps({"slopes": slopes,
                      "implied_sigma_for_paper_inl": document["implied_sigma_for_paper_inl"]},
                     indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
