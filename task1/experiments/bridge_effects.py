"""测量 Fig.21 跨段候选对桥失配和 ADC2 输入余量的影响。"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import platform
from dataclasses import asdict, replace
from pathlib import Path

import matplotlib
import matplotlib.pyplot as plt
import numpy as np
from task1.model.fig21_bridge import map_fig21_bridge

from . import run_effects as effects

SIGMA_PCT = (2.0, 3.0)
MANUFACTURING_SEEDS = tuple(range(100, 106))
SCOPES = ("bridge_only", "main_sub_bridge")
MODES = ("fig11_local", "fig21_bridge")
TONE_PHASE_RAD = 0.11


def _bridge_masks(
    codes: np.ndarray, reserved: np.ndarray, vertical: np.ndarray
) -> tuple[tuple[np.ndarray, np.ndarray], np.ndarray]:
    """取得正式映射器的八片物理位图和同笔归一化 Q32 偏移。"""
    main = np.empty((len(codes), effects.GROUP_SIZE), dtype=np.uint32)
    sub = np.empty((len(codes), effects.GROUP_SIZE), dtype=np.uint8)
    q32 = np.empty(len(codes), dtype=np.int64)
    for index, (code, column, slot) in enumerate(zip(codes, reserved, vertical, strict=True)):
        result = map_fig21_bridge(int(code), int(column), int(slot))
        main[index], sub[index] = result.masks.main, result.masks.sub
        q32[index] = result.correction_q32
    return (main, sub), q32


def _residue(
    pool: effects.Capacitors,
    vin: np.ndarray,
    plan: np.ndarray,
    masks: tuple[np.ndarray, np.ndarray],
    cfg: effects.ExperimentConfig,
) -> np.ndarray:
    charge = effects._feedback_charge(pool, vin, plan, masks, cfg)
    return effects._voltage_equivalent(charge, cfg)


def _check_nominal(
    nominal: effects.Capacitors, cfg: effects.ExperimentConfig
) -> dict[str, int | float]:
    """独立两节点求解穷举全部码及保留列和纵向起点。"""
    codes = np.arange(512, dtype=np.uint16)
    vin = np.linspace(-cfg.full_scale_peak_v, cfg.full_scale_peak_v, len(codes))
    plan = np.broadcast_to(np.arange(effects.GROUP_SIZE), (len(codes), effects.GROUP_SIZE))
    zero = np.zeros_like(codes)
    bypass = effects._map_record(codes, (zero, zero), dem_enable=False)
    baseline = _residue(nominal, vin, plan, bypass, cfg)
    exact_delta_v = (2 * cfg.full_scale_peak_v / 2176) * ((codes >> 2) & 3)
    max_exact_error_v = 0.0
    max_q32_error_v = 0.0
    for reserved_column in range(16):
        for vertical_slot in range(8):
            masks, q32 = _bridge_masks(
                codes, np.full_like(codes, reserved_column), np.full_like(codes, vertical_slot)
            )
            residue_diff = _residue(nominal, vin, plan, masks, cfg) - baseline
            exact_error = np.max(np.abs(residue_diff + exact_delta_v))
            fixed_error = np.max(np.abs(residue_diff + q32 * (cfg.full_scale_peak_v / 2**32)))
            max_exact_error_v = max(max_exact_error_v, float(exact_error))
            max_q32_error_v = max(max_q32_error_v, float(fixed_error))
    q32_half_lsb_v = cfg.full_scale_peak_v / 2**33
    if max_exact_error_v > 1e-12 or max_q32_error_v > q32_half_lsb_v + 1e-12:
        raise RuntimeError("Fig.21 nominal reconstruction exceeds its charge or Q32 bound")
    return {
        "cases": 512 * 16 * 8,
        "exact_max_closure_error_v": max_exact_error_v,
        "q32_max_closure_error_v": max_q32_error_v,
        "q32_half_lsb_v": q32_half_lsb_v,
    }


def _statistics(values: list[float]) -> dict[str, float]:
    return {"min": min(values), "median": float(np.median(values)), "max": max(values)}


def _summarize(rows: list[dict]) -> list[dict]:
    """保留每颗芯片配对变化和原始两种模式的离散区间。"""
    summary = []
    metrics = ("sndr_db", "sfdr_db", "peak_abs_residue_mv")
    for scope in SCOPES:
        for sigma in SIGMA_PCT:
            pair = {
                mode: {
                    row["manufacturing_seed"]: row
                    for row in rows
                    if (row["mismatch_scope"], row["sigma_pct"], row["mode"])
                    == (scope, sigma, mode)
                }
                for mode in MODES
            }
            base, candidate = (pair[mode] for mode in MODES)
            summary.append(
                {
                    "mismatch_scope": scope,
                    "sigma_pct": sigma,
                    "mode_statistics": {
                        mode: {
                            metric: _statistics([row[metric] for row in measurements.values()])
                            for metric in metrics
                        }
                        for mode, measurements in pair.items()
                    },
                    "candidate_minus_baseline_paired": {
                        metric: _statistics(
                            [
                                candidate[seed][metric] - base[seed][metric]
                                for seed in MANUFACTURING_SEEDS
                            ]
                        )
                        for metric in metrics
                    },
                    "overrange_total_samples": {
                        mode: {
                            f"{window}_mv": sum(
                                row[f"overrange_{window}mv_count"] for row in measurements.values()
                            )
                            for window in (50, 100)
                        }
                        for mode, measurements in pair.items()
                    },
                }
            )
    return summary


def _plot(path: Path, rows: list[dict]) -> None:
    """显示配对改善及两模式实际 ADC2 输入余量。"""
    plt.switch_backend("Agg")
    groups = [(scope, sigma) for scope in SCOPES for sigma in SIGMA_PCT]
    labels = ["Bridge\n2%", "Bridge\n3%", "All caps\n2%", "All caps\n3%"]
    x = np.arange(len(groups))
    fig, axes = plt.subplots(1, 3, figsize=(12.3, 4.5))
    fig.suptitle("Fig. 21 candidate | Bridge mismatch and ADC2 headroom", fontweight="bold")
    fig.subplots_adjust(left=0.07, right=0.99, top=0.82, bottom=0.23, wspace=0.32)
    fig.text(
        0.5,
        0.875,
        "10 MS/s, 4096 coherent samples, same six physical seeds per condition",
        ha="center",
        color=effects.GRAY,
        fontsize=9,
    )
    for index, (scope, sigma) in enumerate(groups):
        pair = {
            mode: sorted(
                (
                    row
                    for row in rows
                    if (row["mismatch_scope"], row["sigma_pct"], row["mode"])
                    == (scope, sigma, mode)
                ),
                key=lambda row: row["manufacturing_seed"],
            )
            for mode in MODES
        }
        for ax, metric in zip(axes[:2], ("sndr_db", "sfdr_db"), strict=True):
            points = [
                right[metric] - left[metric] for left, right in zip(*pair.values(), strict=True)
            ]
            color = effects.BLUE if scope == "bridge_only" else effects.ORANGE
            ax.scatter(
                index + np.linspace(-0.11, 0.11, len(points)), points, s=20, color=color, alpha=0.5
            )
            ax.scatter(index, np.median(points), marker="D", s=29, color=color, zorder=3)

        for mode, offset, color, label in (
            ("fig11_local", -0.13, effects.ORANGE, "Fig. 11 local"),
            ("fig21_bridge", 0.13, effects.BLUE, "Fig. 21 candidate"),
        ):
            peaks = [row["peak_abs_residue_mv"] for row in pair[mode]]
            axes[2].scatter(
                index + offset + np.linspace(-0.06, 0.06, len(peaks)),
                peaks,
                color=color,
                alpha=0.45,
                s=17,
            )
            axes[2].scatter(
                index + offset,
                np.median(peaks),
                marker="D",
                color=color,
                s=26,
                label=label if index == 0 else None,
                zorder=3,
            )

    for ax, label in zip(axes, ("ΔSNDR (dB)", "ΔSFDR (dB)", "Peak |residue| (mV)"), strict=True):
        ax.set_xticks(x, labels)
        ax.set_ylabel(label)
        ax.grid(axis="y", alpha=0.22)
    for ax in axes[:2]:
        ax.axhline(0, color=effects.GRAY, ls="--", lw=0.8)
        ax.set_title("Candidate − local baseline", fontsize=10)
    axes[2].axhline(50, color=effects.GRAY, ls="--", lw=0.9, label="50 mV window")
    axes[2].legend(loc="upper left", fontsize=8)
    axes[2].set_ylim(16, max(75, axes[2].get_ylim()[1] + 8))
    fig.text(
        0.5,
        0.065,
        "Each point is one fixed die, diamonds are medians. Ideal continuous residue readout isolates the mapping.\n"
        "The known Q32 offset is included in reconstruction. Window counts use the residue before correction.",
        ha="center",
        color=effects.GRAY,
        fontsize=8,
    )
    fig.savefig(path, dpi=170, facecolor="white")
    plt.close(fig)


def run(out: Path) -> dict:
    """执行两种失配隔离对照并保存实际输入、位图、余量和指标。"""
    cfg = effects.ExperimentConfig()
    nominal, _ = effects._cap_pool(cfg)
    closure = _check_nominal(nominal, cfg)
    plan, _, phases = effects._schedule(cfg.samples, cfg)
    horizontal_phase, vertical_phase = phases
    reserved_column = vertical_phase
    vertical_slot = horizontal_phase % effects.GROUP_SIZE
    vin = cfg.tone_peak_v * np.sin(
        2 * np.pi * cfg.tone_bin / cfg.samples * np.arange(cfg.samples) + TONE_PHASE_RAD
    )
    thresholds = effects._codebook(nominal, cfg)
    codes = (np.searchsorted(thresholds, vin, side="right") - 1).astype(np.uint16)
    baseline = effects._map_record(codes, phases, dem_enable=True)
    candidate, correction_q32 = _bridge_masks(codes, reserved_column, vertical_slot)
    offsets_v = (np.zeros(len(codes)), correction_q32 * (cfg.full_scale_peak_v / 2**32))
    residues = np.empty(
        (len(SCOPES), len(SIGMA_PCT), len(MANUFACTURING_SEEDS), len(MODES), cfg.samples)
    )
    rows = []
    for sigma_index, sigma in enumerate(SIGMA_PCT):
        for seed_index, seed in enumerate(MANUFACTURING_SEEDS):
            _, die = effects._cap_pool(
                replace(cfg, mismatch_sigma_unit=sigma / 100, manufacturing_seed=seed)
            )
            for scope_index, (scope, pool) in enumerate(
                zip(SCOPES, (replace(nominal, bridge=die.bridge), die), strict=True)
            ):
                for mode_index, (mode, masks, offset_v) in enumerate(
                    zip(MODES, (baseline, candidate), offsets_v, strict=True)
                ):
                    residue = _residue(pool, vin, plan, masks, cfg)
                    residues[scope_index, sigma_index, seed_index, mode_index] = residue
                    metrics = effects._spectral_stats(thresholds[codes] + offset_v + residue, cfg)
                    rows.append(
                        {
                            "mismatch_scope": scope,
                            "sigma_pct": sigma,
                            "manufacturing_seed": seed,
                            "mode": mode,
                            "sndr_db": metrics["SNDR_dB"],
                            "sfdr_db": metrics["SFDR_dB"],
                            "spur_hz": metrics["spur_Hz"],
                            "min_residue_mv": 1000 * float(np.min(residue)),
                            "max_residue_mv": 1000 * float(np.max(residue)),
                            "peak_abs_residue_mv": 1000 * float(np.max(np.abs(residue))),
                            "overrange_50mv_count": int(
                                np.count_nonzero((residue < -0.05) | (residue >= 0.05))
                            ),
                            "overrange_100mv_count": int(
                                np.count_nonzero((residue < -0.1) | (residue >= 0.1))
                            ),
                        }
                    )
    out.mkdir(parents=True, exist_ok=True)
    with (out / "bridge_samples.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    np.savez_compressed(
        out / "bridge_traces.npz",
        mismatch_scopes=SCOPES,
        sigma_pct=SIGMA_PCT,
        manufacturing_seeds=MANUFACTURING_SEEDS,
        modes=MODES,
        input_v=vin,
        coarse_code=codes,
        ordered_slices=plan,
        horizontal_phase=horizontal_phase,
        vertical_phase=vertical_phase,
        reserved_column=reserved_column,
        vertical_slot=vertical_slot,
        baseline_main_mask=baseline[0],
        baseline_sub_mask=baseline[1],
        candidate_main_mask=candidate[0],
        candidate_sub_mask=candidate[1],
        candidate_correction_q32=correction_q32,
        baseline_threshold_v=thresholds,
        residue_v=residues,
    )
    _plot(out / "bridge_effects.png", rows)
    sources = (
        Path(__file__).resolve().relative_to(effects.ROOT).as_posix(),
        "task1/experiments/run_effects.py",
        "task1/model/fig11_dem.py",
        "task1/model/fig21_bridge.py",
        "task1/model/module1.py",
        "task1/model/scheduler.py",
        "src/adi_model/charge_ref.py",
        "src/adi_model/metrics.py",
    )
    summary = {
        "schema": "task1_bridge_effects_v1",
        "model_scope": "理想两节点电荷和连续余量观察。只隔离跨段映射，完整 DEM/LMS 对照另见校准实验。",
        "configuration": {
            **{
                key: value
                for key, value in asdict(cfg).items()
                if key
                not in ("skew_s", "mismatch_sigma_unit", "manufacturing_seed", "static_repeats")
            },
            "tone_hz": cfg.fin_hz,
            "tone_phase_rad": TONE_PHASE_RAD,
            "sampling_skew_s": 0.0,
            "sigma_pct": SIGMA_PCT,
            "manufacturing_seeds": MANUFACTURING_SEEDS,
            "mismatch_scopes": SCOPES,
            "baseline_mode": "Fig.11 主阵列片内轮转和跨片 B4，子阵列 B5–B8 保留",
            "phase_binding": "保留列 R = 同笔冻结纵相位，纵向起点 = 同笔冻结横相位 mod 8",
            "coarse_threshold": "Fig.11 名义旧阈值，两个模式共用",
            "candidate_correction": "归一化 V/Vfs 的 Q32 偏移在重构加一次，实际 ADC2 余量不加偏移",
            "headroom_windows": "[-50,+50) mV 和 [-100,+100) mV，逐样本检查",
        },
        "nominal_closure": closure,
        "cases": _summarize(rows),
        "environment": {
            "python": platform.python_version(),
            "numpy": np.__version__,
            "matplotlib": matplotlib.__version__,
        },
        "source_sha256": {
            name: hashlib.sha256((effects.ROOT / name).read_bytes()).hexdigest() for name in sources
        },
    }
    (out / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    return summary


def main() -> None:
    """从仓库根目录运行本实验，指定独立输出目录。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    summary = run(args.out)
    print(json.dumps(summary["nominal_closure"], indent=2))


if __name__ == "__main__":
    main()
