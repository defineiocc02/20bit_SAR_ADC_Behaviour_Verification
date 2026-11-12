"""审查要求的图（F02/F03/F04/F05/F06/F08）与逐图来源记录。

每张图都自带：配置、seed、样本数、源码 SHA、重绘命令；数据另存 JSON。
图只画**已经算出来的量**，不把"看起来像"的曲线补进去。

运行：
    PYTHONPATH=src python3 tools/run_review_figures.py
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from dataclasses import asdict
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

from adi_model import metrics  # noqa: E402
from adi_model.behavioral.ra_az import OrdinaryAZConfig, apply_ordinary_az  # noqa: E402
from adi_model.dem3d import (  # noqa: E402
    Dem3State,
    round_robin_plan,
    unary_rotation_masks,
)
from adi_model.dither import FIG19_FRACTIONAL_WEIGHTS_Q, Fig19DitherProgram, Fig19StaticSpec  # noqa: E402
from adi_model.follower import (  # noqa: E402
    ResolvedPrefixes,
    run_direct_final,
    run_follower,
    run_trial_sar,
)
from adi_model.slice_pool import PhysicalSlicePool  # noqa: E402
from adi_model.track_aux import build_track_plan, causality_audit, track_residual_charge  # noqa: E402
from tools.run_p4_ablation import (  # noqa: E402
    FS_HZ,
    V_FS,
    _build_model,
    _ramp_fn,
    _run,
    _static,
    _train_calibration,
)

LSB20 = 2 * V_FS / 2**20
SEED = 20261105


def _sha() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()
    except Exception:  # pragma: no cover - 只在无 git 的环境降级
        return "UNKNOWN"


def _pool(model, plan):
    return PhysicalSlicePool(
        model.cfg,
        chip=model.chip,
        nominal_codebook=model.codebook,
        mask_binding=unary_rotation_masks,
        acquisition_binding=None,
        schedule_binding=plan,
    )


# ---------------------------------------------------------------- F01
def figure_ownership(model, out: Path) -> dict:
    """连续样本的片所有权 + 单样本逐事件参考电荷（全部来自已记录数据）。"""
    n = 8
    plan = round_robin_plan(n)
    # 用斜坡而不是正弦：三笔的码要拉开，才能看出"事件驱动码"的形状
    ramp = np.linspace(-0.85 * V_FS, 0.85 * V_FS, n)
    codes = np.asarray(
        _run(model, _ramp_fn(ramp), n, dem=False, uniform_plan=False)[0].coarse, dtype=np.int64
    )
    pool = _pool(model, plan)
    trace = run_follower(
        pool,
        ResolvedPrefixes.from_final(codes[:3]),
        np.zeros(3, dtype=np.int64),
        slice_ids=plan.conv[:3],
        reference_high_v=V_FS,
        reference_low_v=-V_FS,
    )
    fig, (ax0, ax1) = plt.subplots(1, 2, figsize=(13, 4.6), constrained_layout=True)

    # 面板 A：18 片 × 3 个周期，C=转换，A=采集（同一周期同一片不得兼有）
    state = np.zeros((18, 3), dtype=int)
    for index in range(3):
        state[plan.conv[index], index] = 1
        state[plan.acq[index], index] = 2
        overlap = set(plan.conv[index].tolist()) & set(plan.acq[index].tolist())
        if overlap:
            raise AssertionError(f"sample {index} has slices both converting and acquiring: {overlap}")
    ax0.imshow(state, aspect="auto", cmap=matplotlib.colors.ListedColormap(["#e2e8f0", "#2b6cb0", "#f6ad55"]))
    ax0.set_yticks(np.arange(0, 18, 2))
    ax0.set_xticks(range(3), [f"sample {i}" for i in range(3)])
    ax0.set_ylabel("physical slice id")
    ax0.set_title("F01a  slice ownership per period", fontsize=10)
    handles = [
        matplotlib.patches.Patch(color="#2b6cb0", label="convert (8 of 18)"),
        matplotlib.patches.Patch(color="#f6ad55", label="acquire (disjoint 8)"),
        matplotlib.patches.Patch(color="#e2e8f0", label="idle (2 spares / not used)"),
    ]
    ax0.legend(handles=handles, fontsize=7, loc="upper center", bbox_to_anchor=(0.5, -0.12), ncol=3)

    # 面板 B：3 笔样本的已决事件与累计参考电荷
    for index in range(3):
        applied = np.asarray(trace.applied_codes)[index]
        ax1.step(
            np.arange(applied.size) + 1,
            applied,
            where="mid",
            marker="o",
            ms=4,
            lw=1.2,
            label=f"sample {index}: final code {int(codes[index])}",
        )
    ax1.set_xlabel("resolved event index (Flash3 + SAR6)")
    ax1.set_ylabel("RDAC code applied at that event")
    ax1.set_title("F01b  RDAC moves only to already-resolved codes", fontsize=10)
    ax1.grid(alpha=0.3)
    ax1.legend(fontsize=8)
    fig.savefig(out / "F01_ownership_events.png", dpi=130)
    plt.close(fig)
    return {
        "figure": "F01_ownership_events.png",
        "samples": n,
        "codes": codes[:3].tolist(),
        "overlap_violations": 0,
        "follower_final_pc": [float(v) for v in np.asarray(trace.cumulative_abs_charge_c)[:, -1] * 1e12],
    }


# ---------------------------------------------------------------- F02
def _old_phase_states(n: int) -> np.ndarray:
    """复算修复前的相位序列（h = index % 15），仅用于对照。"""
    index = np.arange(n, dtype=np.int64)
    return (index % 15) | (((index % 16)) << 6)


def figure_dem_usage(model, out: Path) -> dict:
    """按 (物理片, 单元) 的选通次数：修复前/后对照。"""
    n = 63 * 9
    plan = round_robin_plan(n)
    pool = _pool(model, plan)
    new_states = Dem3State.sequence(n, columns=63)
    old_states = _old_phase_states(n)
    codes = (8, 255, 511)
    fig = plt.figure(figsize=(14, 7.2))
    grid = fig.add_gridspec(2, 3, height_ratios=(1.0, 0.85), hspace=0.45, wspace=0.25)
    data = {}
    for column, code in enumerate(codes):
        ax = fig.add_subplot(grid[0, column])
        command = unary_rotation_masks(pool.geometry, np.full(n, code, np.int64), new_states, plan.conv)
        counts = np.zeros((18, 63), dtype=int)
        for pos in range(plan.conv.shape[1]):
            np.add.at(counts, plan.conv[:, pos], command.main_on[:, pos, :].astype(int))
        covered = int(np.count_nonzero(counts.sum(axis=0)))
        image = ax.imshow(counts, aspect="auto", cmap="viridis", vmin=0, interpolation="nearest")
        ax.set_title(f"code {code}: {covered}/63 units, count {counts.min()}-{counts.max()}")
        ax.set_xlabel("main unit index")
        ax.set_yticks(np.arange(0, 18, 2))
        if column == 0:
            ax.set_ylabel("physical slice id")
        fig.colorbar(image, ax=ax, shrink=0.85, label="selections")
        data[str(code)] = {"covered_units": covered, "counts": counts.tolist()}

    ax_bar = fig.add_subplot(grid[1, :])
    width = 0.36
    x = np.arange(len(codes))
    for offset, (states, label, color) in enumerate(
        ((old_states, "before fix (h = index % 15)", "#c53030"),
         (new_states, "after fix (tiling phase)", "#2b6cb0"))
    ):
        covered = []
        for code in codes:
            command = unary_rotation_masks(pool.geometry, np.full(n, code, np.int64), states, plan.conv)
            counts = np.zeros((18, 63), dtype=int)
            for pos in range(plan.conv.shape[1]):
                np.add.at(counts, plan.conv[:, pos], command.main_on[:, pos, :].astype(int))
            covered.append(int(np.count_nonzero(counts.sum(axis=0))))
        ax_bar.bar(x + (offset - 0.5) * width, covered, width, label=label, color=color)
        data.setdefault("coverage", {})[label] = covered
        for position, value in zip(x + (offset - 0.5) * width, covered, strict=True):
            ax_bar.text(position, value + 1, str(value), ha="center", fontsize=8)
    ax_bar.axhline(63, color="k", ls="--", lw=0.8)
    ax_bar.set_xticks(x, [f"code {code}" for code in codes])
    ax_bar.set_ylabel("main units ever selected (of 63)")
    ax_bar.set_ylim(0, 72)
    ax_bar.set_title("F02b  coverage per code: the full-code case hides the low-code defect (audit A01)")
    ax_bar.legend(fontsize=8, loc="lower right")
    ax_bar.grid(alpha=0.3, axis="y")
    fig.suptitle("F02  DEM usage per (physical slice, main unit), 567 samples = 63 columns x 9 plan periods")
    fig.savefig(out / "F02_dem_usage.png", dpi=130, bbox_inches="tight")
    plt.close(fig)
    return {"figure": "F02_dem_usage.png", "codes": list(codes), "per_code": data}


# ---------------------------------------------------------------- F03
def figure_inl(model, out: Path, *, points: int, repeats: int, dynamic_n: int) -> dict:
    """真 INL（斜坡、逐电平平均）与分箱 DNL，四条配置。"""
    # 配置必须与 P4 矩阵逐字对齐：A3 用默认随机片选，A4 才加轮转。
    configs = [
        ("A0 ideal", dict(mismatch=0.0, noise=False, dem=False, plan=False, cal=False)),
        ("A2 mismatch+noise", dict(mismatch=1e-3, noise=True, dem=False, plan=False, cal=False)),
        ("A3 +DEM", dict(mismatch=1e-3, noise=True, dem=True, plan=False, cal=False)),
        ("A4 +DEM+uniform plan", dict(mismatch=1e-3, noise=True, dem=True, plan=True, cal=False)),
        ("A5 +calibration", dict(mismatch=1e-3, noise=True, dem=False, plan=False, cal=True)),
    ]
    fig, (ax_inl, ax_dnl) = plt.subplots(2, 1, figsize=(9, 8), constrained_layout=True)
    rows = {}
    for label, cfg in configs:
        m = _build_model(cfg["mismatch"], cfg["noise"])
        calibration = _train_calibration(m)[0] if cfg["cal"] else None
        metrics_row = _static(
            m,
            dem=cfg["dem"],
            uniform_plan=cfg["plan"],
            points=points,
            repeats=repeats,
            calibration=calibration,
        )
        levels = np.linspace(-0.985 * V_FS, 0.985 * V_FS, points)
        # 重新算一遍 INL 曲线用于画图（同一配置、同一 seed，可复现）
        mu = np.array(
            [
                _run(
                    m,
                    _ramp_fn(np.full(repeats, lv)),
                    repeats,
                    dem=cfg["dem"],
                    uniform_plan=cfg["plan"],
                    calibration=calibration,
                )[1].mean()
                - lv
                for lv in levels
            ]
        )
        inl = metrics.inl_from_mean_error(levels, mu, LSB20)
        steps = np.diff(mu + levels)
        step = levels[1] - levels[0]
        ax_inl.plot(levels, inl["inl_lsb"], lw=0.9, label=f"{label} (max {metrics_row['inl_max_lsb20']:.1f})")
        ax_dnl.plot(levels[1:], (steps - step) / LSB20, lw=0.7, alpha=0.8, label=label)
        rows[label] = {
            "inl_max_lsb20": metrics_row["inl_max_lsb20"],
            "inl_max_ppm_fs": metrics_row["inl_max_ppm_fs"],
            "dnl_bin_max_lsb20": metrics_row["dnl_bin_lsb20_max"],
            "sigma_e_lsb20": metrics_row["sigma_e_mean_lsb20"],
        }
    ax_inl.set_ylabel("INL [LSB20]")
    ax_inl.set_title(f"F03  INL after best-fit line, {points} levels x {repeats} repeats")
    ax_inl.grid(alpha=0.3)
    ax_inl.legend(fontsize=7)
    ax_dnl.set_ylabel("bin DNL [LSB20]")
    ax_dnl.set_xlabel("input [V]")
    ax_dnl.set_title("F03b  differential metric at ramp-step granularity (not 20b code-density DNL)")
    ax_dnl.grid(alpha=0.3)
    ax_dnl.legend(fontsize=7)
    fig.savefig(out / "F03_inl_dnl.png", dpi=130)
    plt.close(fig)
    return {"figure": "F03_inl_dnl.png", "points": points, "repeats": repeats, "configs": rows}


# ---------------------------------------------------------------- F04
def figure_psd(model, out: Path, *, n: int) -> dict:
    """同条件 FFT/PSD：DEM 与校准对噪声底与杂散的影响。"""
    tone_bin = 205  # 与 8192 点相干
    fin = tone_bin * FS_HZ / n
    amplitude = V_FS * 0.999
    configs = [
        ("A2 mismatch+noise", dict(dem=False, plan=False, cal=False)),
        ("A3 +DEM", dict(dem=True, plan=False, cal=False)),
        ("A5 +calibration", dict(dem=False, plan=False, cal=True)),
    ]
    fig, ax = plt.subplots(figsize=(9, 4.6), constrained_layout=True)
    rows = {}
    for label, cfg in configs:
        calibration = _train_calibration(model)[0] if cfg["cal"] else None
        _, voltage = _run(
            model,
            lambda t: amplitude * np.sin(2 * np.pi * fin * t),
            n,
            dem=cfg["dem"],
            uniform_plan=cfg["plan"],
            calibration=calibration,
        )
        spec = metrics.power_spectral_density(voltage, FS_HZ)
        freq = np.asarray(spec["frequency_hz"])
        psd = np.asarray(spec["density_v2_hz"])
        # 每条曲线各自归一化到自己的基波：三者的有效增益不同（校准后重构增益略有差异），
        # 不归一化会把"增益差"误读成"噪声差"。归一化后纵轴是 dBc/Hz。
        reference = float(np.max(psd[1:]))  # 排除 DC bin
        ax.semilogx(freq[1:], 10 * np.log10(psd[1:] / reference), lw=0.7, label=label)
        fitted = metrics.sine_fit_metrics(voltage, FS_HZ, fin)
        rows[label] = {
            "sndr_db": float(fitted["SNDR_dB"]),
            "sfdr_db": float(fitted["SFDR_dB"]),
            "nsd_nv": float(fitted["NSD_V_rtHz"]) * 1e9,
        }
    ax.set_xlabel("frequency [Hz]")
    ax.set_ylabel("PSD [dBc/Hz], normalized to each fundamental")
    ax.set_title(f"F04  coherent sine {fin/1e6:.3f} MHz, N={n}, same pool and seed")
    ax.grid(alpha=0.3, which="both")
    ax.legend(fontsize=8)
    fig.savefig(out / "F04_psd.png", dpi=130)
    plt.close(fig)
    return {"figure": "F04_psd.png", "fin_hz": fin, "n": n, "configs": rows}


# ---------------------------------------------------------------- F05
def figure_dither_margin(out: Path) -> dict:
    """逐字的输入域缺口与早期候选码越界计数（解析，不跑流水线）。"""
    spec = Fig19StaticSpec(
        v_fs=V_FS,
        step_q=2**24,
        voltage_fraction_bits=32,
        fractional_lower_weights_q=FIG19_FRACTIONAL_WEIGHTS_Q,
        equivalent_polarity="subtract",
        provenance="tools/run_review_figures.py explicit binding",
    )
    words = np.arange(64)
    program = Fig19DitherProgram.from_words(words, spec=spec)
    lower = program.lower_v
    upper = program.upper_code
    lower_bound = -V_FS + lower
    # 早期候选码越界（每个字、512 码、7 事件）
    from adi_model.dither import complete_flash3_sar6_prefix_table

    table = complete_flash3_sar6_prefix_table()
    shifted = table[None, :, :] - upper[:, None, None]
    illegal = ((shifted < 0) | (shifted > 511)).sum(axis=(1, 2))
    fig, (ax0, ax1) = plt.subplots(1, 2, figsize=(12, 4), constrained_layout=True)
    ax0.bar(words, (lower_bound + V_FS) * 1e3, color="#2b6cb0")
    ax0.set_ylim(0, 40)
    ax0.set_xlabel("6-bit dither word")
    ax0.set_ylabel("deficit above -Vfs [mV]")
    ax0.set_title("F05a  input-range deficit per word (0 = nominal -Vfs)")
    ax1.bar(words, illegal, color="#c53030")
    ax1.set_xlabel("6-bit dither word")
    ax1.set_ylabel("illegal (code,event) pairs")
    ax1.set_title("F05b  early RDAC candidates outside 0..511 (no clipping)")
    fig.savefig(out / "F05_dither_margin.png", dpi=130)
    plt.close(fig)
    return {
        "figure": "F05_dither_margin.png",
        "worst_deficit_v": float((lower_bound + V_FS).max()),
        "worst_lower_bound_v": float(lower_bound.max()),
        "max_illegal_events": int(illegal.max()),
        "illegal_by_word": illegal.tolist(),
    }


# ---------------------------------------------------------------- F06
def figure_follower(model, out: Path, *, n_codes: int) -> dict:
    """三条路径的累计参考电荷：follower / 一次加载 / 试探 SAR。"""
    codes = np.unique(np.linspace(0, 511, n_codes).astype(np.int64))
    plan = round_robin_plan(codes.size)
    pool = _pool(model, plan)
    states = np.zeros(codes.size, dtype=np.int64)
    prefixes = ResolvedPrefixes.from_final(codes)
    follower = run_follower(
        pool, prefixes, states, slice_ids=plan.conv,
        reference_high_v=V_FS, reference_low_v=-V_FS,
    )
    direct = run_direct_final(
        pool, codes, states, slice_ids=plan.conv,
        reference_high_v=V_FS, reference_low_v=-V_FS,
    )
    trial = run_trial_sar(
        pool, codes, states, slice_ids=plan.conv,
        reference_high_v=V_FS, reference_low_v=-V_FS,
    )
    fig, (ax0, ax1) = plt.subplots(1, 2, figsize=(12, 4), constrained_layout=True)
    for trace, label, color in (
        (follower, "follower (resolved, causal)", "#2b6cb0"),
        (direct, "direct final load (non-causal)", "#718096"),
        (trial, "trial SAR (set + undo)", "#c53030"),
    ):
        cumulative = np.asarray(trace.cumulative_abs_charge_c).mean(axis=0) * 1e12
        ax0.plot(np.arange(cumulative.size), cumulative, marker="o", ms=3, label=label, color=color)
    ax0.set_xlabel("event index")
    ax0.set_ylabel("mean cumulative |dQ| [pC]")
    ax0.set_title("F06a  reference charge accumulation")
    ax0.grid(alpha=0.3)
    ax0.legend(fontsize=8)
    totals = [
        follower.total_abs_charge_c().mean() * 1e12,
        direct.total_abs_charge_c().mean() * 1e12,
        trial.total_abs_charge_c().mean() * 1e12,
    ]
    ax1.bar(["follower", "direct", "trial"], totals, color=["#2b6cb0", "#718096", "#c53030"])
    ax1.set_ylabel("mean total |dQ| [pC]")
    ax1.set_title(f"F06b  totals: trial/follower = {totals[2]/totals[0]:.2f}x")
    for index, value in enumerate(totals):
        ax1.text(index, value, f"{value:.1f}", ha="center", va="bottom", fontsize=9)
    fig.savefig(out / "F06_follower_charge.png", dpi=130)
    plt.close(fig)
    return {
        "figure": "F06_follower_charge.png",
        "codes": int(codes.size),
        "totals_pc": {"follower": totals[0], "direct": totals[1], "trial": totals[2]},
        "trial_over_follower": totals[2] / totals[0],
    }


# ---------------------------------------------------------------- F08
def figure_az(model, out: Path, *, n: int) -> dict:
    """普通 AZ 开/关：输出偏移与噪声底。"""
    tone_bin = 205
    fin = tone_bin * FS_HZ / n
    cfg = OrdinaryAZConfig(enabled=True, offset_v=1e-3, noise_v=5e-5, seed=SEED)
    fig, (ax0, ax1) = plt.subplots(1, 2, figsize=(12, 4), constrained_layout=True)
    rows = {}
    for label, az in (("AZ off", None), ("AZ on (1 mV residual)", cfg)):
        run = model.simulate(lambda t: 0.5 * np.sin(2 * np.pi * fin * t), n, ordinary_az=az)
        voltage = np.asarray(run.raw.out, dtype=float)
        spec = metrics.power_spectral_density(voltage, FS_HZ)
        ax0.semilogx(
            np.asarray(spec["frequency_hz"])[1:],
            10 * np.log10(np.asarray(spec["density_v2_hz"])[1:] / (V_FS**2 / 2)),
            lw=0.7,
            label=label,
        )
        mean_error = float(np.mean(voltage - np.asarray(run.raw.x_ref)))
        rows[label] = {
            "mean_error_uv": mean_error * 1e6,
            "nsd_nv": float(metrics.sine_fit_metrics(voltage, FS_HZ, fin)["NSD_V_rtHz"]) * 1e9,
        }
    ax0.set_xlabel("frequency [Hz]")
    ax0.set_ylabel("PSD [dBFS/Hz]")
    ax0.set_title("F08a  ordinary AZ on/off, same pool and seed")
    ax0.grid(alpha=0.3, which="both")
    ax0.legend(fontsize=8)
    labels = list(rows)
    # 保持时间扫描（无噪声，只看 droop 的线性响应）
    # 1 kV/s 是示例斜率：1 V/s 在 100 ns 内只有 0.1 uV，看不出来；数值写进图注
    droop_cfg = OrdinaryAZConfig(enabled=True, droop_v_per_s=1e3)
    holds = np.array([0.0, 1e-9, 5e-9, 20e-9, 100e-9])
    ids = np.arange(64, dtype=np.int64)
    sweep = [
        float(np.mean(apply_ordinary_az(np.zeros(64), ids, droop_cfg, hold_time_s=np.full(64, h))
                      .residual_v))
        for h in holds
    ]
    ax1.plot(holds * 1e9, np.array(sweep) * 1e6, marker="o", color="#2b6cb0")
    ax1.set_xlabel("hold time since AZ refresh [ns]")
    ax1.set_ylabel("residual droop [uV]")
    ax1.set_title("F08b  1 kV/s droop vs hold time (linear by construction)", fontsize=10)
    ax1.grid(alpha=0.3)
    fig.savefig(out / "F08_az.png", dpi=130)
    plt.close(fig)
    return {
        "figure": "F08_az.png",
        "n": n,
        "results": rows,
        "hold_sweep": {"hold_ns": (holds * 1e9).tolist(), "residual_uv": (np.array(sweep) * 1e6).tolist()},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="review figures F02/F03/F04/F05/F06/F08")
    parser.add_argument("--out", default="docs/reviews/20261113_p3p4/figures")
    parser.add_argument("--points", type=int, default=512)
    parser.add_argument("--repeats", type=int, default=8)
    parser.add_argument("--dynamic-n", type=int, default=8192)
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    model = _build_model(1e-3, True)
    sha = _sha()
    produced = [
        figure_ownership(model, out),
        figure_dem_usage(model, out),
        figure_inl(model, out, points=args.points, repeats=args.repeats, dynamic_n=args.dynamic_n),
        figure_psd(model, out, n=args.dynamic_n),
        figure_dither_margin(out),
        figure_follower(model, out, n_codes=128),
        figure_az(model, out, n=args.dynamic_n),
    ]
    document = {
        "source_sha": sha,
        "seed": SEED,
        "fs_hz": FS_HZ,
        "v_fs_v": V_FS,
        "lsb20_v": LSB20,
        "points": args.points,
        "repeats": args.repeats,
        "dynamic_n": args.dynamic_n,
        "evidence_level": "L1 behavioral (float); no RTL/post-layout/silicon",
        "figures": produced,
        "redraw": (
            "PYTHONPATH=src python3 tools/run_review_figures.py "
            f"--points {args.points} --repeats {args.repeats} --dynamic-n {args.dynamic_n}"
        ),
    }
    (out / "figures.json").write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n")
    for row in produced:
        print(f"{row['figure']}")
    print(f"written to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
