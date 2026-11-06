"""复现实验一的时偏和电容失配对照。

运行于仓库根目录，使用 PYTHONPATH=.:src python -m task1.experiments.run_effects
--out PATH。两组实验分别开启时偏或电容失配，输出为输入等效电压。
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import platform
from dataclasses import asdict, dataclass
from pathlib import Path

import matplotlib
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.colors import BoundaryNorm, ListedColormap
from matplotlib.patches import Patch
from task1.model.fig11_dem import map_fig11
from task1.model.module1 import DEFAULT_DEM_SEED, Module1
from task1.model.scheduler import GROUP_SIZE, SLICE_COUNT

from adi_model.charge_ref import ref_ra_charge_nodal
from adi_model.metrics import inl_from_mean_error, sine_fit_metrics, spectrum_dbfs

MAIN_WEIGHTS = (2,) * 15 + (1, 1)
SUB_WEIGHTS = (8, 4, 2, 1, 1)
ROOT = Path(__file__).resolve().parents[2]
BLUE = "#0072B2"
ORANGE = "#D55E00"
GRAY = "#737B82"


@dataclass(frozen=True)
class ExperimentConfig:
    """明确指定的一次时偏实验及一个制造失配样本。"""

    fs_hz: int = 10_000_000
    samples: int = 4096
    tone_bin: int = 409
    tone_peak_v: float = 10.0 / 3.0
    full_scale_peak_v: float = 5.0
    skew_s: float = 5e-12
    unit_cap_f: float = 1e-15
    mismatch_sigma_unit: float = 0.001
    manufacturing_seed: int = 100
    schedule_seed: int = 0x243F6A88
    dem_seed: int = DEFAULT_DEM_SEED
    static_repeats: int = 128

    @property
    def fin_hz(self) -> float:
        """相干激励频率，单位 Hz。"""
        return self.tone_bin * self.fs_hz / self.samples


@dataclass(frozen=True)
class Capacitors:
    """按物理切片编号排列的主、子及桥电容，单位 F。"""

    main: np.ndarray
    sub: np.ndarray
    bridge: np.ndarray


def _schedule(
    count: int, cfg: ExperimentConfig
) -> tuple[np.ndarray, np.ndarray, tuple[np.ndarray, np.ndarray]]:
    """使用集成参考，记录各笔有序组及采集时冻结的相位。"""
    controller = Module1()
    controller.step(seed_load=True, slice_seed=cfg.schedule_seed, dem_seed=cfg.dem_seed)
    controller.step()
    controller.step(advance=True, shuffle=True, dem_enable=True)
    prime = controller.step(advance=True, shuffle=True, dem_enable=True).schedule
    if not prime.slot_accepted or not prime.acq_valid or prime.conv_valid:
        raise RuntimeError("unexpected priming state in slice scheduler")

    converting = np.empty((count, GROUP_SIZE), dtype=np.int8)
    acquiring = np.empty_like(converting)
    horizontal = np.empty(count, dtype=np.uint8)
    vertical = np.empty_like(horizontal)
    previous_acq = prime.acq_ids
    for sample_id in range(count):
        output = controller.step(advance=sample_id + 1 < count, shuffle=True, dem_enable=True)
        state = output.schedule
        if (
            not state.slot_accepted
            or not state.conv_valid
            or state.conv_sample_id != sample_id
            or state.conv_ids != previous_acq
            or len(set(state.conv_ids + state.acq_ids)) != 2 * GROUP_SIZE
            or not output.conversion_dem.enabled
        ):
            raise RuntimeError(f"invalid slice ownership at sample {sample_id}")
        converting[sample_id] = state.conv_ids
        acquiring[sample_id] = state.acq_ids
        horizontal[sample_id] = output.conversion_dem.horizontal_phase
        vertical[sample_id] = output.conversion_dem.vertical_phase
        previous_acq = state.acq_ids
    return converting, acquiring, (horizontal, vertical)


def _cap_pool(cfg: ExperimentConfig) -> tuple[Capacitors, Capacitors]:
    """构造名义网络和同一制造样本，失配为每单元独立高斯偏差。"""
    rng = np.random.Generator(np.random.PCG64(cfg.manufacturing_seed))
    main = np.tile(MAIN_WEIGHTS, (SLICE_COUNT, 1)) * cfg.unit_cap_f
    sub = np.tile(SUB_WEIGHTS, (SLICE_COUNT, 1)) * cfg.unit_cap_f
    bridge = np.full(SLICE_COUNT, cfg.unit_cap_f)

    def mismatch(cap: np.ndarray) -> np.ndarray:
        # 单元面积与名义电容成正比，独立并联单位电容的绝对失配按面积平方根增长。
        sigma_f = cfg.mismatch_sigma_unit * np.sqrt(cap * cfg.unit_cap_f)
        return cap + rng.normal(0.0, sigma_f)

    nominal = Capacitors(main, sub, bridge)
    actual = Capacitors(mismatch(main), mismatch(sub), mismatch(bridge))
    return nominal, actual


def _map_record(
    codes: np.ndarray,
    phase: tuple[np.ndarray, np.ndarray],
    *,
    dem_enable: bool,
) -> tuple[np.ndarray, np.ndarray]:
    """通过交付映射器生成实际选通位图，位序为 slot 内 unit 编号。"""
    main = np.empty((len(codes), GROUP_SIZE), dtype=np.uint32)
    sub = np.empty((len(codes), GROUP_SIZE), dtype=np.uint8)
    for sample_id, (code, h, v) in enumerate(zip(codes, *phase, strict=True)):
        mask = map_fig11(int(code), dem_enable, int(h), int(v))
        if not mask.valid:
            raise RuntimeError(f"valid stimulus rejected at sample {sample_id}")
        main[sample_id] = mask.main
        sub[sample_id] = mask.sub
    return main, sub


def _unpack(mask: np.ndarray, unit_count: int) -> np.ndarray:
    """将打包的高参考选通位转换为逐电容布尔数组。"""
    bit = np.arange(unit_count, dtype=mask.dtype)
    return ((mask[..., None] >> bit) & 1).astype(bool)


def _feedback_charge(
    pool: Capacitors,
    vin: np.ndarray,
    plan: np.ndarray,
    masks: tuple[np.ndarray, np.ndarray],
    cfg: ExperimentConfig,
) -> np.ndarray:
    """从实际物理电容和底板电压求每笔反馈电荷，单位 C。"""
    low_v = -cfg.full_scale_peak_v
    high_v = cfg.full_scale_peak_v
    main_mask, sub_mask = masks
    charge = np.zeros(len(vin))
    for physical_id in range(SLICE_COUNT):
        sample, slot = np.nonzero(plan == physical_id)
        if not sample.size:
            continue
        main_cap = pool.main[physical_id]
        sub_cap = pool.sub[physical_id]
        main_select = _unpack(main_mask[sample, slot], len(main_cap))
        sub_select = _unpack(sub_mask[sample, slot], len(sub_cap))
        # 主底板采 VIN，子底板固定低参考。逐片的独立两节点方程接收真实位图。
        charge[sample] += ref_ra_charge_nodal(
            main_cap_f=main_cap,
            sub_cap_f=sub_cap,
            bridge_cap_f=pool.bridge[physical_id],
            sub_parasitic_f=0.0,
            feedback_cap_f=cfg.unit_cap_f,
            sample_main_v=np.broadcast_to(vin[sample, None], main_select.shape),
            sample_sub_v=np.full(len(sub_cap), low_v),
            conversion_main_v=np.where(main_select, high_v, low_v),
            conversion_sub_v=np.where(sub_select, high_v, low_v),
        )
    return charge


def _voltage_equivalent(charge: np.ndarray, cfg: ExperimentConfig) -> np.ndarray:
    """按八片名义主采样电容将反馈电荷折算为输入电压。"""
    return charge / (GROUP_SIZE * sum(MAIN_WEIGHTS) * cfg.unit_cap_f)


def _codebook(nominal: Capacitors, cfg: ExperimentConfig) -> np.ndarray:
    """用独立电荷参考求 Fig.11 名义 DAC 阈值。"""
    codes = np.arange(512, dtype=np.uint16)
    zero_phase = np.zeros(len(codes), dtype=np.uint8)
    plan = np.tile(np.arange(GROUP_SIZE, dtype=np.int8), (len(codes), 1))
    vin = np.full(len(codes), -cfg.full_scale_peak_v)
    mask = _map_record(codes, (zero_phase, zero_phase), dem_enable=False)
    thresholds = vin - _voltage_equivalent(_feedback_charge(nominal, vin, plan, mask, cfg), cfg)
    if not np.all(np.diff(thresholds) > 0) or thresholds[-1] >= cfg.full_scale_peak_v:
        raise RuntimeError("invalid Fig.11 nominal thresholds")
    return thresholds


def _observe_pair(
    vin: np.ndarray,
    codes: np.ndarray,
    plan: np.ndarray,
    phase: tuple[np.ndarray, np.ndarray],
    codebook: np.ndarray,
    nominal: Capacitors,
    actual: Capacitors,
    cfg: ExperimentConfig,
) -> dict:
    """在同一输入与物理池上运行两种 DEM 设置，保留掩码和完整记录。"""
    result = {}
    for mode, enabled in (("off", False), ("on", True)):
        masks = _map_record(codes, phase, dem_enable=enabled)
        nominal_charge = _feedback_charge(nominal, vin, plan, masks, cfg)
        nominal_error = codebook[codes] + _voltage_equivalent(nominal_charge, cfg) - vin
        nominal_max_error = float(np.max(np.abs(nominal_error)))
        if nominal_max_error > 1e-12:
            raise RuntimeError(f"nominal reconstruction closure failed for DEM {mode}")
        charge = _feedback_charge(actual, vin, plan, masks, cfg)
        result[mode] = {
            "masks": masks,
            "output_v": codebook[codes] + _voltage_equivalent(charge, cfg),
            "nominal_max_error_v": nominal_max_error,
        }
    return result


def _spectral_stats(output: np.ndarray, cfg: ExperimentConfig) -> dict[str, float]:
    """提取全谱最大杂散、信噪失真比和主要杂散频率。"""
    metrics = sine_fit_metrics(output, cfg.fs_hz, cfg.fin_hz)
    return {name: float(metrics[name]) for name in ("SNDR_dB", "SFDR_dB", "spur_Hz")}


def _write_usage(
    path: Path,
    plan: np.ndarray,
    tone: dict,
) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    """统计每个物理单元开启次数，分母为所在切片的转换次数。"""
    physical_ids = plan.ravel()
    denominator = np.bincount(physical_ids, minlength=SLICE_COUNT)
    main_rates = {}
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            ["dem", "slice_id", "array", "unit", "nominal_units", "selected", "conversions"]
        )
        for mode, result in tone.items():
            for array, mask, weights in zip(
                ("main", "sub"), result["masks"], (MAIN_WEIGHTS, SUB_WEIGHTS), strict=True
            ):
                bits = _unpack(mask.ravel(), len(weights))
                count = np.zeros((SLICE_COUNT, len(weights)), dtype=int)
                np.add.at(count, physical_ids, bits)
                for physical_id in range(SLICE_COUNT):
                    for unit, weight in enumerate(weights):
                        writer.writerow(
                            [
                                mode,
                                physical_id,
                                array,
                                unit,
                                weight,
                                count[physical_id, unit],
                                denominator[physical_id],
                            ]
                        )
                if array == "main":
                    main_rates[mode] = np.divide(
                        count,
                        denominator[:, None],
                        out=np.full(count.shape, np.nan),
                        where=denominator[:, None] > 0,
                    )
    return denominator, main_rates


def _plot_interleave(
    path: Path,
    outputs: dict[str, np.ndarray],
    stats: dict,
    plan: np.ndarray,
    acquisition: np.ndarray,
    cfg: ExperimentConfig,
) -> None:
    """绘制时偏频谱及动态调度的物理资源角色。"""
    fig, (spectrum, slots) = plt.subplots(
        2, 1, figsize=(10.8, 6.6), height_ratios=(2.1, 1), layout="constrained"
    )
    fig.suptitle("Sampling skew | fixed A/B and dynamic slice scheduling", fontweight="bold")
    for key, label, color in (
        ("ideal", "Zero skew", GRAY),
        ("fixed_ab", "Fixed A/B", ORANGE),
        ("dynamic", "Dynamic scheduling", BLUE),
    ):
        f, dbfs = spectrum_dbfs(outputs[key], cfg.fs_hz, cfg.full_scale_peak_v, window="boxcar")
        if key != "ideal":
            result = stats[key]
            label += f"  (SFDR {result['SFDR_dB']:.1f} dB, SNDR {result['SNDR_dB']:.1f} dB)"
        spectrum.plot(f / 1e6, dbfs, color=color, lw=0.9, label=label)
    mirror = cfg.fs_hz / 2 - cfg.fin_hz
    spectrum.axvline(mirror / 1e6, color=GRAY, lw=0.8, ls="--", zorder=0)
    fixed_mirror = stats["fixed_ab"]["mirror_dBFS"]
    dynamic_mirror = stats["dynamic"]["mirror_dBFS"]
    spectrum.text(
        mirror / 1e6 - 0.04,
        -45,
        f"Mirror at {mirror / 1e6:.6f} MHz\n{fixed_mirror:.1f} → {dynamic_mirror:.1f} dBFS",
        ha="right",
        va="center",
        fontsize=9,
        color=GRAY,
    )
    spectrum.set(
        xlim=(0, cfg.fs_hz / 2e6),
        ylim=(-171, 3),
        xlabel="Frequency (MHz)",
        ylabel="Amplitude (dBFS/bin)",
    )
    spectrum.legend(loc="upper left", fontsize=9, frameon=True)
    spectrum.grid(alpha=0.2)

    count = 32
    roles = np.zeros((SLICE_COUNT, count), dtype=np.int8)
    for sample in range(count):
        roles[acquisition[sample], sample] = 1
        roles[plan[sample], sample] = 2
    colors = ("#E7EDF1", "#6AABDD", "#244361")
    slots.imshow(
        roles,
        cmap=ListedColormap(colors),
        norm=BoundaryNorm((-0.5, 0.5, 1.5, 2.5), 3),
        aspect="auto",
        interpolation="nearest",
        origin="lower",
    )
    slots.set(xlabel="Valid sample slot", ylabel="Physical slice ID", yticks=(0, 4, 8, 12, 17))
    slots.legend(
        handles=[
            Patch(color=color, label=label)
            for color, label in zip(colors, ("Spare", "Acquiring", "Converting"), strict=True)
        ],
        loc="lower right",
        bbox_to_anchor=(1, 1),
        ncols=3,
        frameon=False,
        fontsize=8,
    )
    fig.savefig(path, dpi=170, facecolor="white")
    plt.close(fig)


def _plot_dem(
    path: Path,
    tone: dict,
    stats: dict,
    levels: np.ndarray,
    static: dict,
    rates: dict[str, np.ndarray],
    cfg: ExperimentConfig,
) -> None:
    """绘制同池 DEM 对照、静态残差和主阵列物理单元使用率。"""
    fig = plt.figure(figsize=(10.8, 9.4), layout="constrained")
    grid = fig.add_gridspec(3, 2, height_ratios=(1.1, 1.0, 1.0))
    spectrum = fig.add_subplot(grid[0, :])
    residual = fig.add_subplot(grid[1, 0])
    sigma = fig.add_subplot(grid[1, 1], sharex=residual)
    heatmaps = [fig.add_subplot(grid[2, index]) for index in range(2)]
    fig.suptitle(
        "Fig. 11 capacitor mismatch | DEM on/off in the same virtual die", fontweight="bold"
    )

    for mode, color, heatmap in zip(("off", "on"), (ORANGE, BLUE), heatmaps, strict=True):
        name = f"DEM {mode.upper()}"
        f, dbfs = spectrum_dbfs(
            tone[mode]["output_v"], cfg.fs_hz, cfg.full_scale_peak_v, window="boxcar"
        )
        metric = stats[mode]
        label = f"{name}  (SFDR {metric['SFDR_dB']:.1f} dB, SNDR {metric['SNDR_dB']:.1f} dB)"
        spectrum.plot(f / 1e6, dbfs, label=label, lw=0.9, color=color)
        residual.plot(levels, 1e6 * static[mode]["fit_residual_v"], label=name, lw=0.9, color=color)
        sigma.plot(levels, 1e6 * static[mode]["sigma_v"], label=name, lw=0.9, color=color)
        mesh = heatmap.imshow(
            rates[mode],
            vmin=0,
            vmax=1,
            cmap="viridis",
            aspect="auto",
            interpolation="nearest",
            origin="lower",
        )
        heatmap.axvline(14.5, color="white", lw=1)
        heatmap.set(
            title=name,
            xlabel="Main unit index (0–14: 2C, 15–16: 1C)",
            ylabel="Physical slice ID",
            xticks=(0, 4, 8, 12, 15, 16),
            yticks=(0, 4, 8, 12, 17),
        )
    fig.colorbar(mesh, ax=heatmaps, label="Selections / slice conversions", shrink=0.9)
    spectrum.set(
        xlim=(0, cfg.fs_hz / 2e6),
        ylim=(-155, 3),
        xlabel="Frequency (MHz)",
        ylabel="Amplitude (dBFS/bin)",
        title="Coherent sine response",
    )
    spectrum.legend(loc="upper right", fontsize=9, frameon=True)
    residual.set(
        title="Mean error after best-fit line removal",
        xlabel="Differential input (V)",
        ylabel="Fit residual (µV)",
    )
    sigma.set(
        title="Within-level variation", xlabel="Differential input (V)", ylabel="Std. dev. (µV)"
    )
    for axis in (spectrum, residual, sigma):
        axis.grid(alpha=0.2)
    residual.legend(loc="upper left", fontsize=8, frameon=True)
    fig.savefig(path, dpi=170, facecolor="white")
    plt.close(fig)


def run(out: Path) -> dict:
    """运行两组定向实验并存储可重绘的结果。"""
    cfg = ExperimentConfig()
    plt.switch_backend("Agg")
    out.mkdir(parents=True, exist_ok=True)
    nominal, actual = _cap_pool(cfg)
    plan, acquisition, tone_phases = _schedule(cfg.samples, cfg)
    sample = np.arange(cfg.samples)
    t = sample / cfg.fs_hz
    vin = cfg.tone_peak_v * np.sin(2 * np.pi * cfg.fin_hz * t)
    delays = np.repeat((cfg.skew_s, -cfg.skew_s, 0.0), (8, 8, 2))
    fixed_ab = np.arange(GROUP_SIZE)[None, :] + (sample % 2 * GROUP_SIZE)[:, None]

    def skew_response(selection: np.ndarray) -> np.ndarray:
        phase = 2 * np.pi * cfg.fin_hz * (t[:, None] + delays[selection])
        return cfg.tone_peak_v * np.sin(phase).mean(axis=1)

    interleave = {"ideal": vin, "fixed_ab": skew_response(fixed_ab), "dynamic": skew_response(plan)}
    interleave_stats = {}
    mirror_bin = cfg.samples // 2 - cfg.tone_bin
    for mode in ("fixed_ab", "dynamic"):
        stats = _spectral_stats(interleave[mode], cfg)
        _, dbfs = spectrum_dbfs(interleave[mode], cfg.fs_hz, cfg.full_scale_peak_v, window="boxcar")
        stats["mirror_dBFS"] = float(dbfs[mirror_bin])
        interleave_stats[mode] = stats

    codebook = _codebook(nominal, cfg)
    codes = (np.searchsorted(codebook, vin, side="right") - 1).astype(np.uint16)
    tone = _observe_pair(vin, codes, plan, tone_phases, codebook, nominal, actual, cfg)
    tone_stats = {mode: _spectral_stats(record["output_v"], cfg) for mode, record in tone.items()}
    denominator, rates = _write_usage(out / "usage.csv", plan, tone)

    upper = np.r_[codebook[1:], cfg.full_scale_peak_v]
    levels = 0.5 * (codebook + upper)
    static_vin = np.repeat(levels, cfg.static_repeats)
    static_codes = np.repeat(np.arange(512, dtype=np.uint16), cfg.static_repeats)
    static_plan = np.tile(plan[: cfg.static_repeats], (len(levels), 1))
    static_phases = tuple(phase[: cfg.static_repeats] for phase in tone_phases)
    repeated_phases = tuple(np.tile(phase, len(levels)) for phase in static_phases)
    static = _observe_pair(
        static_vin, static_codes, static_plan, repeated_phases, codebook, nominal, actual, cfg
    )
    for mode, record in static.items():
        error = (record["output_v"] - static_vin).reshape(-1, cfg.static_repeats)
        mean = error.mean(axis=1)
        fit = inl_from_mean_error(levels, mean, 2 * cfg.full_scale_peak_v / len(levels))
        record.update(
            error_v=error,
            mean_error_v=mean,
            sigma_v=error.std(axis=1, ddof=1),
            fit_residual_v=fit["inl_v"],
        )
        tone_stats[mode].update(
            static_fit_residual_peak_v=float(np.abs(record["fit_residual_v"]).max()),
            static_fit_residual_rms_v=float(np.sqrt(np.mean(record["fit_residual_v"] ** 2))),
            static_median_sigma_v=float(np.median(record["sigma_v"])),
            nominal_tone_max_error_v=tone[mode]["nominal_max_error_v"],
            nominal_static_max_error_v=record["nominal_max_error_v"],
        )
    np.savetxt(
        out / "static_sweep.csv",
        np.column_stack(
            [
                levels,
                *(
                    static[mode][field]
                    for mode in ("off", "on")
                    for field in ("mean_error_v", "fit_residual_v", "sigma_v")
                ),
            ]
        ),
        delimiter=",",
        fmt="%.17g",
        comments="",
        header="input_v,off_mean_error_v,off_fit_residual_v,off_sigma_v,"
        "on_mean_error_v,on_fit_residual_v,on_sigma_v",
    )
    np.savez_compressed(
        out / "traces.npz",
        time_s=t,
        input_v=vin,
        delay_s=delays,
        fixed_ab_plan=fixed_ab,
        conv_plan=plan,
        acquisition_plan=acquisition,
        timing_fixed_ab_v=interleave["fixed_ab"],
        timing_dynamic_v=interleave["dynamic"],
        physical_main_cap_f=actual.main,
        physical_sub_cap_f=actual.sub,
        physical_bridge_cap_f=actual.bridge,
        nominal_threshold_v=codebook,
        tone_code=codes,
        tone_phase_h=tone_phases[0],
        tone_phase_v=tone_phases[1],
        dem_off_v=tone["off"]["output_v"],
        dem_on_v=tone["on"]["output_v"],
        tone_main_off=tone["off"]["masks"][0],
        tone_sub_off=tone["off"]["masks"][1],
        tone_main_on=tone["on"]["masks"][0],
        tone_sub_on=tone["on"]["masks"][1],
        static_input_v=levels,
        static_plan=plan[: cfg.static_repeats],
        static_phase_h=static_phases[0],
        static_phase_v=static_phases[1],
        static_error_off_v=static["off"]["error_v"],
        static_error_on_v=static["on"]["error_v"],
    )

    _plot_interleave(
        out / "interleave_effect.png", interleave, interleave_stats, plan, acquisition, cfg
    )
    _plot_dem(out / "dem_effect.png", tone, tone_stats, levels, static, rates, cfg)
    sources = (
        Path(__file__).resolve().relative_to(ROOT).as_posix(),
        "task1/model/scheduler.py",
        "task1/model/fig11_dem.py",
        "task1/model/module1.py",
        "src/adi_model/charge_ref.py",
        "src/adi_model/metrics.py",
    )
    summary = {
        "schema": "task1_effects_v1",
        "model_scope": "定向行为实验。时偏实验使用活动切片采样电压的均值。电容实验使用单个虚拟制造样本、理想两节点电荷求解和理想余量读出，指标以输入等效电压计。",
        "configuration": {
            **asdict(cfg),
            "tone_hz": cfg.fin_hz,
            "mirror_hz": cfg.fs_hz / 2 - cfg.fin_hz,
            "fft_window": "boxcar",
            "sfdr_residual_fft_window": "blackmanharris",
            "main_cap_weights": MAIN_WEIGHTS,
            "sub_cap_weights": SUB_WEIGHTS,
            "bridge_cap_weight": 1,
            "sub_parasitic_f": 0.0,
            "nominal_bridge_beta": 1 / 17,
            "static_levels": len(levels),
            "usage_denominator_by_slice": denominator.tolist(),
        },
        "interleave": interleave_stats,
        "dem": tone_stats,
        "environment": {
            "python": platform.python_version(),
            "numpy": np.__version__,
            "matplotlib": matplotlib.__version__,
            "platform": platform.system(),
        },
        "source_sha256": {
            source: hashlib.sha256((ROOT / source).read_bytes()).hexdigest() for source in sources
        },
    }
    (out / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8"
    )
    return summary


def main() -> None:
    """运行实验并打印便于检查的数值摘要。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True, help="实验数据和图的输出目录")
    summary = run(parser.parse_args().out)
    print(json.dumps({key: summary[key] for key in ("interleave", "dem")}, indent=2))


if __name__ == "__main__":
    main()
