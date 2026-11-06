"""扫 Fig.11 单位电容失配，保留同制造样本的 DEM 开关对照。"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import platform
from dataclasses import asdict, dataclass, replace
from pathlib import Path

import matplotlib
import matplotlib.pyplot as plt
import numpy as np

from adi_model.metrics import sine_fit_metrics

from . import run_effects as effects

SIGMA_PCT = (0.0, 0.003, 0.01, 0.03, 0.1)
MANUFACTURING_SEEDS = tuple(range(100, 112))
TARGET_SNDR_DB = 100.0


@dataclass(frozen=True)
class DieMeasurement:
    """固定激励和选片计划下，一个制造样本的单次 DEM 设置。"""

    sigma_pct: float
    seed: int
    dem: str
    sndr_db: float | None
    sfdr_db: float | None
    residual_rms_uv: float | None
    spur_hz: float | None
    nominal_error_max_v: float | None = None


def _statistics(values: list[float]) -> dict[str, float]:
    """用样本最小值、中位数和最大值表示有限种子的离散程度。"""
    return {
        "min": min(values),
        "median": float(np.median(values)),
        "max": max(values),
    }


def _measurements(
    cfg: effects.ExperimentConfig,
) -> tuple[list[DieMeasurement], dict[str, float]]:
    """同一输入、粗码、物理计划及位图贯穿每个失配扫点。"""
    plan, _, phases = effects._schedule(cfg.samples, cfg)
    time_s = np.arange(cfg.samples) / cfg.fs_hz
    vin = cfg.tone_peak_v * np.sin(2 * np.pi * cfg.fin_hz * time_s)
    nominal, _ = effects._cap_pool(cfg)
    codebook = effects._codebook(nominal, cfg)
    codes = (np.searchsorted(codebook, vin, side="right") - 1).astype(np.uint16)
    masks = {
        "off": effects._map_record(codes, phases, dem_enable=False),
        "on": effects._map_record(codes, phases, dem_enable=True),
    }

    def observe(pool: effects.Capacitors, mode: str) -> np.ndarray:
        charge = effects._feedback_charge(pool, vin, plan, masks[mode], cfg)
        return codebook[codes] + effects._voltage_equivalent(charge, cfg)

    closure = {mode: float(np.max(np.abs(observe(nominal, mode) - vin))) for mode in masks}
    if max(closure.values()) > 1e-12:
        raise RuntimeError("zero-mismatch check failed the nominal charge contract")
    rows = [
        DieMeasurement(0.0, seed, mode, None, None, None, None, error)
        for seed in MANUFACTURING_SEEDS
        for mode, error in closure.items()
    ]

    for sigma in SIGMA_PCT[1:]:
        for seed in MANUFACTURING_SEEDS:
            _, die = effects._cap_pool(
                replace(cfg, mismatch_sigma_unit=sigma / 100, manufacturing_seed=seed)
            )
            for mode in masks:
                metrics = sine_fit_metrics(observe(die, mode), cfg.fs_hz, cfg.fin_hz)
                rows.append(
                    DieMeasurement(
                        sigma,
                        seed,
                        mode,
                        float(metrics["SNDR_dB"]),
                        float(metrics["SFDR_dB"]),
                        1e6 * float(metrics["nd_rms"]),
                        float(metrics["spur_Hz"]),
                    )
                )
    return rows, closure


def _summarize(rows: list[DieMeasurement]) -> list[dict]:
    """逐失配水平汇总实测 dB 值和达到观察线的种子个数。"""
    summary = []
    for sigma in SIGMA_PCT[1:]:
        for mode in ("off", "on"):
            samples = [row for row in rows if row.sigma_pct == sigma and row.dem == mode]
            sndr = [row.sndr_db for row in samples if row.sndr_db is not None]
            sfdr = [row.sfdr_db for row in samples if row.sfdr_db is not None]
            residual = [row.residual_rms_uv for row in samples if row.residual_rms_uv is not None]
            summary.append(
                {
                    "sigma_pct": sigma,
                    "dem": mode,
                    "samples": len(samples),
                    "sndr_db": _statistics(sndr),
                    "sfdr_db": _statistics(sfdr),
                    "residual_rms_uv": _statistics(residual),
                    "sndr_at_least_100_db": sum(value >= TARGET_SNDR_DB for value in sndr),
                }
            )
    return summary


def _plot(path: Path, rows: list[DieMeasurement], summary: list[dict]) -> None:
    """同时绘制 SNDR、SFDR 的种子分布和中位数趋势。"""
    plt.switch_backend("Agg")
    fig, axes = plt.subplots(1, 2, figsize=(11.6, 4.9))
    fig.suptitle("Fig. 11 mismatch sweep | paired virtual dies", fontweight="bold", y=0.985)
    fig.subplots_adjust(left=0.075, right=0.98, bottom=0.21, top=0.84, wspace=0.28)
    fig.text(
        0.5,
        0.905,
        "10 MS/s, 4096 coherent points, 998.535 kHz, 12 seeds per level",
        ha="center",
        color=effects.GRAY,
        fontsize=9,
    )

    for ax, field, ylabel in zip(
        axes, ("sndr_db", "sfdr_db"), ("SNDR (dB)", "SFDR (dB)"), strict=True
    ):
        for mode, color, x_scale in (("off", effects.ORANGE, 0.96), ("on", effects.BLUE, 1.04)):
            points = [row for row in rows if row.dem == mode and row.sigma_pct > 0]
            series = [row for row in summary if row["dem"] == mode]
            ax.scatter(
                [x_scale * row.sigma_pct for row in points],
                [getattr(row, field) for row in points],
                color=color,
                alpha=0.45,
                s=17,
                linewidths=0,
            )
            ax.plot(
                [x_scale * row["sigma_pct"] for row in series],
                [row[field]["median"] for row in series],
                color=color,
                marker="D",
                markersize=4,
                label=f"DEM {mode.upper()} median",
            )
        ax.set_xscale("log")
        ax.set_xticks(SIGMA_PCT[1:], [f"{sigma:g}" for sigma in SIGMA_PCT[1:]])
        ax.set(xlabel="Unit-capacitor mismatch sigma (%)", ylabel=ylabel)
        ax.set_xlim(0.0025, 0.12)
        ax.grid(alpha=0.23)
        ax.legend(fontsize=8)
    axes[0].axhline(TARGET_SNDR_DB, color=effects.GRAY, ls="--", lw=0.9)
    axes[0].text(0.105, TARGET_SNDR_DB + 0.7, "100 dB", ha="right", color=effects.GRAY, fontsize=8)
    fig.text(
        0.5,
        0.075,
        "Each dot is one seed; horizontal offsets separate the modes. The 0% case is a numerical closure check.\n"
        "Ideal residue readout isolates capacitor mismatch. Thermal noise, actual ADC2 and CAAZ are outside this sweep.",
        ha="center",
        color=effects.GRAY,
        fontsize=8,
    )
    fig.savefig(path, dpi=170, facecolor="white")
    plt.close(fig)


def run(out: Path) -> dict:
    """写出每个样本的指标、统计摘要和可审阅图。"""
    cfg = effects.ExperimentConfig()
    rows, closure = _measurements(cfg)
    series = _summarize(rows)
    out.mkdir(parents=True, exist_ok=True)
    with (out / "sndr_sigma_samples.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(asdict(rows[0])))
        writer.writeheader()
        writer.writerows(asdict(row) for row in rows)
    _plot(out / "sndr_sigma_sweep.png", rows, series)

    root = effects.ROOT
    sources = (
        Path(__file__).resolve().relative_to(root).as_posix(),
        "task1/experiments/run_effects.py",
        "task1/model/scheduler.py",
        "task1/model/fig11_dem.py",
        "task1/model/module1.py",
        "src/adi_model/charge_ref.py",
        "src/adi_model/metrics.py",
    )
    summary = {
        "schema": "task1_sndr_sigma_sweep_v1",
        "model_scope": "理想两节点电荷参考和理想余量读出，单独量化电容失配。热噪声、真实 ADC2 与 CAAZ 留待后续联合验证。",
        "configuration": {
            "fs_hz": cfg.fs_hz,
            "samples": cfg.samples,
            "tone_bin": cfg.tone_bin,
            "tone_hz": cfg.fin_hz,
            "tone_peak_v": cfg.tone_peak_v,
            "full_scale_peak_v": cfg.full_scale_peak_v,
            "unit_cap_f": cfg.unit_cap_f,
            "schedule_seed": cfg.schedule_seed,
            "dem_seed": cfg.dem_seed,
            "sigma_pct": SIGMA_PCT,
            "manufacturing_seeds": MANUFACTURING_SEEDS,
            "sfdr_residual_fft_window": "blackmanharris",
        },
        "nominal_closure_max_v": closure,
        "nominal_db_metrics": "数值误差参考，CSV 中 dB 字段留空。",
        "target_sndr_db": TARGET_SNDR_DB,
        "target_max_residual_rms_uv": 1e6
        * cfg.tone_peak_v
        / np.sqrt(2)
        * 10 ** (-TARGET_SNDR_DB / 20),
        "series": series,
        "environment": {
            "python": platform.python_version(),
            "numpy": np.__version__,
            "matplotlib": matplotlib.__version__,
            "platform": platform.system(),
        },
        "source_sha256": {
            source: hashlib.sha256((root / source).read_bytes()).hexdigest() for source in sources
        },
    }
    (out / "sndr_sigma_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8"
    )
    return summary


def main() -> None:
    """运行扫点，并按失配和开关模式打印 SNDR 中位数及范围。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True, help="实验数据和图的输出目录")
    summary = run(parser.parse_args().out)
    for row in summary["series"]:
        stats = row["sndr_db"]
        print(
            f"{row['sigma_pct']:g}% DEM {row['dem']}: "
            f"median {stats['median']:.2f} dB, {stats['min']:.2f}–{stats['max']:.2f} dB"
        )


if __name__ == "__main__":
    main()
