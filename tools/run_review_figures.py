"""审查要求的图（F02/F03/F04/F05/F06/F08）与逐图来源记录。

每张图都自带：配置、seed、样本数、源码 SHA、重绘命令；数据另存 JSON。
图只画**已经算出来的量**，不把"看起来像"的曲线补进去。

运行：
    PYTHONPATH=src python3 tools/run_review_figures.py
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from tools.run_p4_ablation import (  # noqa: E402
    FS_HZ,
    V_FS,
    _build_model,
    _dither_max_v,
    _ramp_fn,
    _run,
    _static,
    _train_calibration,
)

from adi_model import metrics  # noqa: E402
from adi_model.behavioral.ra_az import OrdinaryAZConfig, apply_ordinary_az  # noqa: E402
from adi_model.control.dem3d import (  # noqa: E402
    Dem3State,
    round_robin_plan,
    unary_rotation_masks,
)
from adi_model.control.dither import (  # noqa: E402
    FIG19_FRACTIONAL_WEIGHTS_Q,
    Fig19DitherProgram,
    Fig19StaticSpec,
)
from adi_model.control.follower import (  # noqa: E402
    ResolvedPrefixes,
    run_direct_final,
    run_follower,
    run_trial_sar,
)
from adi_model.simulation.pipeline import run_pipeline  # noqa: E402
from adi_model.simulation.slice_pool import PhysicalSlicePool  # noqa: E402
from adi_model.topology.mapper import Mapper  # noqa: E402

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


# ---------------------------------------------------------------- F09
def figure_input_current(out: Path) -> dict:
    """输入电流 vs **直流**差分输入（论文 Fig.9.8.5 的横轴），四种取电方式。"""
    c_total_f = 20.5e-12
    x = np.linspace(-V_FS, V_FS, 121)
    full = c_total_f * np.abs(x) * FS_HZ
    fig, ax = plt.subplots(figsize=(8.4, 4.4), constrained_layout=True)
    ax.plot(x, full * 1e6, label="no TRACK: C_total x |Vin| x fs", color="#c53030")
    ax.plot(
        x,
        full * 0.14 * 1e6,
        label="AUX supplies 86% (fit to the 350 uA point)",
        color="#2b6cb0",
        ls="--",
    )
    ax.plot(x, full * 0.05 * 1e6, label="AUX supplies 95%", color="#38a169", ls=":")
    ax.plot(x, np.zeros_like(x), label="perfect TRACK at DC (residual = 0)", color="k", lw=1.2)
    ax.scatter([V_FS], [350.0], marker="*", s=140, color="#b7791f", zorder=5,
               label="paper Fig.9.8.5: 350 uA (value only)")
    ax.set_xlabel("DC differential input [V]")
    ax.set_ylabel("input current [uA]")
    ax.set_title(
        "F09  input current vs DC input: 20.5 pF x 40 MS/s gives 2.46 mA at full scale"
    )
    ax.grid(alpha=0.3)
    ax.legend(fontsize=7)
    fig.savefig(out / "F09_input_current.png", dpi=130)
    plt.close(fig)
    return {
        "figure": "F09_input_current.png",
        "c_total_f": c_total_f,
        "full_scale_current_a": float(c_total_f * V_FS * FS_HZ),
        "aux_fraction_for_paper_point": float(1 - 350e-6 / (c_total_f * V_FS * FS_HZ)),
        "note": (
            "the paper's x-axis is DC differential input; the 350 uA point is read from "
            "Fig.9.8.5 without a stated input value, so the AUX fraction is an inference, not a result"
        ),
    }


# ---------------------------------------------------------------- F07
def _dynamic_config():
    """A7 全动态的显式参数集（全部标 ASSUMED，见 10_dynamic_closure.md）。"""
    from dataclasses import replace

    from adi_model.behavioral.conversion import ConversionParameters

    conversion = ConversionParameters(
        quantizer_time_s=6e-9,
        ra_duration_s=12e-9,
        rdac_event_times_s=(0.3e-9, 0.9e-9, 1.5e-9, 2.1e-9, 2.7e-9, 3.3e-9, 3.9e-9),
        reference_accurate_start_s=5.0e-9,
        fine_reference_tau_s=1.5e-9,
        ra_response_model="single_pole",
        ra_bandwidth_hz=66e6,
        ra_dc_gain="ideal",
        ra_slew_v_s="unlimited",
        adc2_response_model="wide_narrow",
        adc2_wide_bandwidth_hz=300e6,
        adc2_narrow_bandwidth_hz=150e6,
        adc2_narrow_start_s=10e-9,
    )
    model = _build_model(1e-3, False)  # 动态路径拒绝与采样域白噪声组合
    return model, replace(
        model.cfg,
        conversion=conversion,
        dyn_ref_settling=True,
        dyn_c_decouple=1e-9,
        dyn_tau_ref=0.56e-9,
    )


def figure_dynamics(out: Path, *, n: int = 32) -> dict:
    """F07：参考建立、RA 输出、ADC2 宽/窄带切换的相位波形。"""
    from adi_model.control.dither import complete_flash3_sar6_prefix_table
    from adi_model.defaults import _all_signal

    model, cfg = _dynamic_config()
    plan = round_robin_plan(n)
    pool = PhysicalSlicePool(
        cfg,
        chip=model.chip,
        nominal_codebook=model.codebook,
        mask_binding=unary_rotation_masks,
        acquisition_binding=_all_signal,
        schedule_binding=plan,
    )
    mapper = Mapper(
        np.arange(512, dtype=np.int64),
        nominal_codebook=model.codebook,
        event_codes_by_coarse=complete_flash3_sar6_prefix_table(),
    )
    raw = run_pipeline(
        cfg,
        lambda t: 2.0 * np.sin(2 * np.pi * 1e6 * t),
        n,
        pool=pool,
        mapper=mapper,
        sample_ids=np.arange(n, dtype=np.int64),
        dem_states=np.zeros(n, dtype=np.int64),
        sampled_top_v=0.0,
        sampled_subnode_v=0.0,
        reference_high_v=V_FS,
        reference_low_v=-V_FS,
        nominal_reference_high_v=V_FS,
        nominal_reference_low_v=-V_FS,
        static_charge_domain=True,
        initial_reference_error_v=(0.0, 0.0),
        initial_ra_output_v=0.0,
        initial_adc2_input_v=0.0,
    )
    trace = raw.conversion_trace
    times = np.asarray(trace.phase_times_s) * 1e9
    names = [str(v) for v in np.asarray(trace.phase_names)]
    reference = np.asarray(trace.phase_reference_v)  # (N, phases, 2 rails)
    ra = np.asarray(trace.phase_ra_v)
    adc2 = np.asarray(trace.phase_adc2_v)
    fig, (ax0, ax1) = plt.subplots(1, 2, figsize=(12, 4.2), constrained_layout=True)
    ax0.plot(times, np.abs(reference).mean(axis=0)[:, 0] * 1e6, marker="o", label="rail 0")
    ax0.plot(times, np.abs(reference).mean(axis=0)[:, 1] * 1e6, marker="s", label="rail 1")
    for x, name in zip(times, names, strict=True):
        ax0.axvline(x, color="k", lw=0.5, alpha=0.3)
        ax0.text(x, ax0.get_ylim()[1], name, rotation=90, fontsize=6, va="top")
    ax0.set_xlabel("time from aperture [ns]")
    ax0.set_ylabel("|reference error| [uV]")
    ax0.set_title("F07a  reference re-settling after the mux switch", fontsize=10)
    ax0.grid(alpha=0.3)
    ax0.legend(fontsize=7)
    ax1.plot(times, ra.mean(axis=0) * 1e3, marker="o", label="RA output")
    ax1.plot(times, adc2.mean(axis=0) * 1e3, marker="s", label="ADC2 input")
    ax1.set_xlabel("time from aperture [ns]")
    ax1.set_ylabel("mean voltage [mV]")
    ax1.set_title("F07b  RA / ADC2 phases, wide-to-narrow switch at 10 ns", fontsize=10)
    ax1.grid(alpha=0.3)
    ax1.legend(fontsize=8)
    fig.suptitle(
        f"F07  N={n}, explicit ASSUMED dynamics (tau_ref=0.56 ns back-solved from the paper's "
        "65%/20b claim); self-consistency only, not validation"
    )
    fig.savefig(out / "F07_reference_ra_adc2.png", dpi=130)
    plt.close(fig)

    entry = int(names.index("ra_entry"))
    aperture = int(names.index("adc2_aperture"))
    e_entry = float(np.mean(np.abs(reference[:, entry, :])))
    e_final = float(np.mean(np.abs(reference[:, aperture, :])))
    bits_settled = float(np.log2(e_entry / e_final)) if e_final > 0 else float("nan")
    return {
        "figure": "F07_reference_ra_adc2.png",
        "n": n,
        "phase_times_ns": times.tolist(),
        "phase_names": names,
        "reference_error_ra_entry_uv": e_entry * 1e6,
        "reference_error_aperture_uv": e_final * 1e6,
        "extra_bits_settled_during_ra": bits_settled,
        "ra_sat": int(np.count_nonzero(np.asarray(trace.ra_sat))),
        "note": "tau_ref was back-solved from the 65%/20b statement, so the ratio is self-consistent by construction",
    }


# ---------------------------------------------------------------- F12
def figure_fixed_point(model, out: Path, *, n: int) -> dict:
    """浮点模拟输出 vs 20 位定点解码的差（**不是** RTL 对拍）。"""
    tone_bin = max(1, int(round(n * 1.0e6 / FS_HZ)))
    fin = tone_bin * FS_HZ / n
    raw, voltage = _run(
        model, lambda t: V_FS * 0.999 * np.sin(2 * np.pi * fin * t), n, dem=False, uniform_plan=False
    )
    codes = model.nominal_decoder.reconstruct(raw.digital_observation)
    delta = (np.asarray(codes.voltage) - np.asarray(raw.out)) / LSB20
    fig, (ax0, ax1) = plt.subplots(1, 2, figsize=(12, 4), constrained_layout=True)
    ax0.plot(np.arange(n), delta, lw=0.6, color="#2b6cb0")
    ax0.set_xlabel("sample index")
    ax0.set_ylabel("fixed - float [LSB20]")
    ax0.set_title("F12a  digital decode minus float reconstruction", fontsize=10)
    ax0.grid(alpha=0.3)
    ax1.hist(delta, bins=60, color="#2b6cb0")
    ax1.set_xlabel("fixed - float [LSB20]")
    ax1.set_ylabel("count")
    ax1.set_title("F12b  bounded by half an LSB20 (20-bit rounding)", fontsize=10)
    ax1.grid(alpha=0.3)
    fig.suptitle(
        f"F12  N={n}, fin={fin/1e6:.3f} MHz; float analog model vs 20-bit fixed decode "
        "(NOT RTL co-simulation)"
    )
    fig.savefig(out / "F12_fixed_vs_float.png", dpi=130)
    plt.close(fig)
    return {
        "figure": "F12_fixed_vs_float.png",
        "n": n,
        "fin_hz": fin,
        "rms_lsb20": float(np.sqrt(np.mean(delta**2))),
        "max_abs_lsb20": float(np.max(np.abs(delta))),
        "note": "digital fixed-point path only; no RTL, no event-level scoreboard",
    }


# ---------------------------------------------------------------- F11
def figure_calibration_generalization(out: Path, *, points: int, repeats: int,
                                      seeds=(20261105, 20261106, 20261107, 20261108, 20261109)) -> dict:
    """多制造种子的校准泛化：同芯片留出 vs 跨芯片套用。"""
    rows = {"seed": [], "plain": [], "dem": [], "calibrated": [], "cross_chip": []}
    chips = {}
    for seed in seeds:
        model = _build_model(1e-3, True, seed=seed)
        chips[seed] = model
    for index, seed in enumerate(seeds):
        model = chips[seed]
        calibration = _train_calibration(model)[0]
        rows["seed"].append(seed)
        rows["plain"].append(
            _static(model, dem=False, uniform_plan=False, points=points, repeats=repeats)["inl_max_lsb20"]
        )
        rows["dem"].append(
            _static(model, dem=True, uniform_plan=False, points=points, repeats=repeats)["inl_max_lsb20"]
        )
        rows["calibrated"].append(
            _static(model, dem=False, uniform_plan=False, points=points, repeats=repeats,
                    calibration=calibration)["inl_max_lsb20"]
        )
        other = chips[seeds[(index + 1) % len(seeds)]]
        rows["cross_chip"].append(
            _static(other, dem=False, uniform_plan=False, points=points, repeats=repeats,
                    calibration=calibration)["inl_max_lsb20"]
        )

    fig, ax = plt.subplots(figsize=(9, 4.4), constrained_layout=True)
    labels = ["plain", "DEM", "calibrated (own chip)", "calibration on another chip"]
    keys = ["plain", "dem", "calibrated", "cross_chip"]
    means = [float(np.mean(rows[k])) for k in keys]
    spreads = [float(np.std(rows[k], ddof=1)) for k in keys]
    colors = ["#718096", "#2b6cb0", "#38a169", "#c53030"]
    ax.bar(labels, means, yerr=spreads, capsize=5, color=colors)
    for position, (mean, spread) in enumerate(zip(means, spreads, strict=True)):
        ax.text(position, mean + spread, f"{mean:.1f}", ha="center", va="bottom", fontsize=9)
    ax.set_ylabel("max INL [LSB20]")
    ax.set_title(
        f"F11  {len(seeds)} manufacturing seeds, {points} levels x {repeats} repeats; "
        "bars = mean, whiskers = 1 sigma"
    )
    ax.grid(alpha=0.3, axis="y")
    fig.savefig(out / "F11_calibration_generalization.png", dpi=130)
    plt.close(fig)
    return {
        "figure": "F11_calibration_generalization.png",
        "seeds": list(seeds),
        "points": points,
        "repeats": repeats,
        "per_seed": rows,
        "means": dict(zip(keys, means, strict=True)),
        "spreads": dict(zip(keys, spreads, strict=True)),
    }


# ---------------------------------------------------------------- F10
def figure_interleaving(model, out: Path, *, n: int) -> dict:
    """片选策略对 SFDR/SNDR 的影响：随机 8-of-10 vs 轮转 8-of-18。"""
    freqs = (0.2e6, 1.0e6, 3.0e6)
    plans = (("random 8-of-10", False, "#718096"), ("round-robin 8-of-18", True, "#2b6cb0"))
    rows = {}
    for label, _uniform, _color in plans:
        rows[label] = {}
    sfdr = {label: [] for label, _, _ in plans}
    sndr = {label: [] for label, _, _ in plans}
    for target in freqs:
        tone_bin = max(1, int(round(n * target / FS_HZ)))
        fin = tone_bin * FS_HZ / n
        for label, uniform, _color in plans:
            _, voltage = _run(
                model,
                lambda t, f=fin: V_FS * 0.999 * np.sin(2 * np.pi * f * t),
                n,
                dem=True,
                uniform_plan=uniform,
            )
            fitted = metrics.sine_fit_metrics(voltage, FS_HZ, fin)
            sfdr[label].append(float(fitted["SFDR_dB"]))
            sndr[label].append(float(fitted["SNDR_dB"]))
            rows[label][f"{fin/1e6:.2f} MHz"] = {
                "sndr_db": float(fitted["SNDR_dB"]),
                "sfdr_db": float(fitted["SFDR_dB"]),
                "spur_hz": float(fitted["spur_Hz"]),
            }
    fig, (ax0, ax1) = plt.subplots(1, 2, figsize=(12, 4), constrained_layout=True)
    x = np.arange(len(freqs))
    width = 0.36
    for offset, (label, _uniform, color) in enumerate(plans):
        ax0.bar(x + (offset - 0.5) * width, sfdr[label], width, label=label, color=color)
        ax1.bar(x + (offset - 0.5) * width, sndr[label], width, label=label, color=color)
    for ax, values, name in ((ax0, sfdr, "SFDR"), (ax1, sndr, "SNDR")):
        ax.set_xticks(x, [f"{f/1e6:.1f} MHz" for f in freqs])
        ax.set_ylabel(f"{name} [dB]")
        ax.set_ylim(min(min(v) for v in values.values()) - 3, max(max(v) for v in values.values()) + 3)
        ax.grid(alpha=0.3, axis="y")
        ax.legend(fontsize=8)
        ax.set_title(f"F10{'a' if name == 'SFDR' else 'b'}  {name} vs slice-selection policy", fontsize=10)
    fig.suptitle(f"F10  N={n}, same pool and seed, DEM on; only the slice-selection policy changes")
    fig.savefig(out / "F10_interleaving.png", dpi=130)
    plt.close(fig)
    return {"figure": "F10_interleaving.png", "n": n, "results": rows}


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
        # A6 走独立电荷口；它占掉 T_max 的输入范围，所以扫描区间与其他行不同，
        # 图上直接画在各自的实际可用区间里，不插值补齐。
        ("A6 +dither (charge port)", dict(mismatch=1e-3, noise=True, dem=False, plan=False,
                                          cal=False, dither=True)),
    ]
    fig, (ax_inl, ax_dnl) = plt.subplots(2, 1, figsize=(9, 8), constrained_layout=True)
    rows = {}
    for label, cfg in configs:
        m = _build_model(cfg["mismatch"], cfg["noise"])
        calibration = _train_calibration(m)[0] if cfg["cal"] else None
        dither_seed = 20261113 if cfg.get("dither") else None
        headroom = _dither_max_v(m.cfg) if dither_seed is not None else 0.0
        metrics_row = _static(
            m,
            dem=cfg["dem"],
            uniform_plan=cfg["plan"],
            points=points,
            repeats=repeats,
            calibration=calibration,
            dither_seed=dither_seed,
            dither_headroom_v=headroom,
        )
        levels = np.linspace(-0.985 * V_FS + headroom, 0.985 * V_FS - headroom, points)
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
                    dither_words=(
                        None
                        if dither_seed is None
                        else np.random.default_rng(
                            np.random.SeedSequence([int(dither_seed), int(index)])
                        ).integers(0, 64, repeats, dtype=np.int64)
                    ),
                )[1].mean()
                - lv
                for index, lv in enumerate(levels)
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
    return {
        "figure": "F03_inl_dnl.png",
        "points": points,
        "repeats": repeats,
        "configs": rows,
        "offset_v": 0.0,
        "note": (
            "A5 在本图用 offset=0 的公共电平网格，因此与本矩阵里带留出偏移的 "
            "A5（10.5 LSB20）不同；两者分别是'同网格可比'与'留出集'口径，"
            "不能互相替代，也不是同一次测量。"
        ),
    }


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
    from adi_model.control.dither import complete_flash3_sar6_prefix_table

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
        figure_interleaving(model, out, n=args.dynamic_n),
        figure_calibration_generalization(out, points=args.points, repeats=args.repeats),
        figure_fixed_point(model, out, n=args.dynamic_n),
        figure_dynamics(out, n=32),
        figure_input_current(out),
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
