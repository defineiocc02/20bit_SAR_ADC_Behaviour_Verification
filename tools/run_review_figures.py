#!/usr/bin/env python3
# =============================================================================
# 文件名     : run_review_figures.py
# 版本       : v1.0
# 功能       : 重绘 F01–F12，并记录源基线、运行设置和逐图指标。
# 设计说明   :
#   * 合同整数枚举、行为级信号实验和显式解析估计使用各自输入。图注指出
#     物理/数字边界，避免把不同 ADC2 或扰动口径拼成一条 RTL 证据。
#   * 静态扫保留越界计数；频谱图明确功率归一和输出域。JSON 记录可复算
#     的指标与口径，完整生成只依赖仓内模型和 NumPy/SciPy/Matplotlib。
#   * 本脚本只负责制图。出错抛给调用方，批量更新文档图前先在另一个
#     目录完整生成并检验。
# 变更记录   : 2026-11-17，修订 F01–F12 的图法、实验边界和证据记录。
# =============================================================================
from __future__ import annotations
import argparse
import hashlib
import json
import tempfile
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import matplotlib

# 指定非交互后端，使无桌面的干净检出也能生成同样的 PNG。
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import scipy  # noqa: E402

from adi_model import metrics  # noqa: E402
from adi_model.behavioral.ra_az import OrdinaryAZConfig, apply_ordinary_az  # noqa: E402
from adi_model.control.dem3d import fig11_masks, round_robin_plan  # noqa: E402
from adi_model.control.dither import (  # noqa: E402
    FIG19_FRACTIONAL_WEIGHTS_Q,
    FIG19_STEP_Q,
    Fig19DitherProgram,
    Fig19StaticSpec,
)
from adi_model.control.follower import (  # noqa: E402
    ResolvedPrefixes,
    run_direct_final,
    run_follower,
    run_trial_sar,
)
from experiments.fig11 import Fig11Parameters  # noqa: E402
from adi_model.dem_dither import bridge_swap, post_mask  # noqa: E402
from adi_model.sched18_rtl import Scheduler18Rtl  # noqa: E402
from adi_model.simulation.pipeline import run_pipeline  # noqa: E402
from adi_model.simulation.slice_pool import PhysicalSlicePool  # noqa: E402
from adi_model.topology.mapper import Mapper  # noqa: E402
from tools.run_p4_ablation import (  # noqa: E402
    FS_HZ,
    V_FS,
    _build_model,
    _dither_max_v,
    _ramp_fn,
    _run,
    _train_calibration,
)

LSB20 = 2 * V_FS / 2**20
SEED = 20261105


# =============================================================================
# 0. 画布、输入范围和可复现记录
# =============================================================================
COLORS = {
    "blue": "#15548d",
    "teal": "#007769",
    "orange": "#ac5409",
    "purple": "#7653a0",
    "rose": "#ab3954",
    "ink": "#192a3c",
    "muted": "#4d5e6e",
    "grid": "#d8e1e8",
    "paper": "#f4f7fa",
    "white": "#ffffff",
}

# 参照 ISSCC 2024 Fig. 9.8.1/3–5 的图内排版：白纸、细坐标框、
# 紧凑无衬线标签和仅用于区分技术状态的颜色。数学符号使用 STIX。
# 图注放在图框外，导出文件保留可编辑文字。
FIGURE_STYLE = {
    "figure.facecolor": "white",
    "savefig.facecolor": "white",
    "axes.facecolor": "white",
    "axes.edgecolor": "#232323",
    "axes.labelcolor": "#151515",
    "axes.titlecolor": "#151515",
    "axes.titlelocation": "left",
    "axes.titlesize": 8,
    "axes.titleweight": "bold",
    "axes.titlepad": 4,
    "axes.linewidth": 0.65,
    "axes.axisbelow": True,
    "font.family": "sans-serif",
    "font.sans-serif": ["Arial", "Liberation Sans", "DejaVu Sans"],
    "font.size": 8,
    "mathtext.fontset": "stix",
    "text.color": "#151515",
    "xtick.color": "#232323",
    "ytick.color": "#232323",
    "xtick.labelsize": 7,
    "ytick.labelsize": 7,
    "xtick.major.size": 2.6,
    "ytick.major.size": 2.6,
    "xtick.major.width": 0.6,
    "ytick.major.width": 0.6,
    "grid.color": "#dedede",
    "grid.linewidth": 0.45,
    "legend.fontsize": 7,
    "legend.frameon": False,
    "lines.linewidth": 1.05,
    "svg.fonttype": "none",
}


def _figure_title(fig, key: str, title: str, subtitle: str) -> None:
    """把整图标题和实验条件放进导出描述，图面由分图及正文图注解释。"""
    fig.set_label(f"{key}. {title}. {subtitle}")


def _save_figure(fig, out: Path, name: str) -> None:
    """输出双栏宽的可编辑矢量图与 300 dpi PNG，保存后释放画布。"""
    if name.startswith("F"):
        # 较窄图仍为横轴及图例留出台阶。机理图按各自的双栏画布定尺寸。
        width, height = fig.get_size_inches()
        fig.set_size_inches(7.16, max(2.75, height * 7.16 / width))
    description = fig.get_label()
    fig.savefig(out / name, dpi=300, pil_kwargs={"optimize": True},
                metadata={"Description": description})
    fig.savefig(out / Path(name).with_suffix(".svg"),
                metadata={"Description": description, "Date": None})
    plt.close(fig)


def _sha() -> str | None:
    """记录 Git 基线。未提交的制图工具另有文件哈希，不能只靠 HEAD 追踪。"""
    try:
        return subprocess.run(
            ["git", "-C", str(ROOT), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def _pool(model, plan):
    """为 follower 账目显式绑定行为级片池、几何和片选计划。"""
    return PhysicalSlicePool(
        model.cfg,
        chip=model.chip,
        nominal_codebook=model.codebook,
        mask_binding=fig11_masks,
        acquisition_binding=None,
        schedule_binding=plan,
    )


# =============================================================================
# 1. D1 片归属与 D2 已决前缀
# =============================================================================
def figure_ownership(model, out: Path) -> dict:
    """核对 D1 冻结向量并绘制片归属，另画独立 D2 前缀激励。"""
    # 冻结表就是 tb_sched18 的逐拍参考输入。相同比对只能证明匹配该向量，
    # 截图标题不将本轮计算写成 RTL 仿真。复位后的第一拍视为第 1 个有效推进。
    golden = np.loadtxt(ROOT / "tb" / "vec_r18_sched.txt", dtype=np.int64)
    scheduler = Scheduler18Rtl()
    n_display = 8
    state = np.zeros((18, n_display), dtype=np.int64)
    mismatches = 0
    for index, expected in enumerate(golden):
        result = scheduler.step()
        idle_mask = sum(1 << slot for slot in result["idle"])
        row = [index, *result["acq"], scheduler.acq_mask, scheduler.conv_mask, idle_mask]
        mismatches += int(not np.array_equal(row, expected))
        if index < n_display:
            state[result["conv"], index] = 1
            state[result["acq"], index] = 2
    if mismatches or scheduler.violations:
        raise RuntimeError("D1 参考调度与冻结向量或 8/8/2 不变量不一致")

    # D2 用指定末码查固定表生成合法前缀。不靠最终码推断实测比较器序列，也不把两面板
    # 拼作同一笔 v21 事务。电荷路径仍沿用模型的片池和名义参考轨。
    codes = np.array([96, 277, 459], dtype=np.int64)
    plan = round_robin_plan(len(codes))
    trace = run_follower(
        _pool(model, plan),
        ResolvedPrefixes.from_final(codes),
        np.zeros(len(codes), dtype=np.int64),
        slice_ids=plan.conv,
        reference_high_v=V_FS,
        reference_low_v=-V_FS,
    )
    fig, (ownership, follower) = plt.subplots(
        1, 2, figsize=(12.1, 5.25), gridspec_kw={"width_ratios": (0.95, 1.05)},
        layout="constrained",
    )
    handles = []
    palette = (
        (COLORS["grid"], "Idle  ·  2"),
        (COLORS["blue"], "Convert  ·  8"),
        (COLORS["teal"], "Acquire  ·  8"),
    )
    # 每个离散片状态用矢量网格绘制。横轴中心是推进编号，而不是物理时间。
    ownership.pcolormesh(
        np.arange(n_display + 1) - 0.5, np.arange(19) - 0.5, state,
        cmap=matplotlib.colors.ListedColormap([color for color, _ in palette]), vmin=0, vmax=2,
    )
    for color, label in palette:
        handles.append(matplotlib.patches.Patch(color=color, label=label))
    ownership.set_xticks(range(n_display), range(1, n_display + 1))
    ownership.set_yticks(np.arange(0, 18, 2))
    ownership.set_xlabel("Accepted conversion period")
    ownership.set_ylabel("Physical slice ID")
    ownership.set_title("(a) Slice allocation")
    ownership.legend(handles=handles, loc="upper center", ncol=3, bbox_to_anchor=(0.5, -0.13))

    # 最后一拍在曲线末端直接标码，便于从灰度版追踪每一条阶梯。
    for index, (row, color, marker) in enumerate(
        zip(trace.applied_codes, (COLORS["teal"], COLORS["blue"], COLORS["purple"]),
            ("o", "s", "D"), strict=True)
    ):
        events = np.arange(len(row))
        follower.step(events, row, where="post", color=color)
        follower.scatter(events, row, marker=marker, s=24, color=color, zorder=3)
        follower.annotate(str(codes[index]), (events[-1], row[-1]), xytext=(7, 0),
                          textcoords="offset points", color=color, va="center")
    follower.set_xlim(-0.25, 6.7)
    follower.set_ylim(0, 512)
    follower.set_xticks(range(7), ["Flash", "SAR 1", "SAR 2", "SAR 3", "SAR 4", "SAR 5", "SAR 6"])
    follower.set_xlabel("Resolved event")
    follower.set_ylabel("Applied RDAC code")
    follower.set_title("(b) Follower response to given prefixes")
    follower.grid(axis="y")
    _figure_title(fig, "F01", "Slice ownership and event following",
                  f"D1 matches {len(golden):,} frozen vectors  •  D2 shows three independent prefix examples")
    _save_figure(fig, out, "F01_ownership_events.png")
    return {
        "figure": "F01_ownership_events.png",
        "scheduler_seed": f"{scheduler.seed:#010x}",
        "checked_scheduler_cycles": len(golden),
        "scheduler_vector_mismatches": mismatches,
        "overlap_violations": len(scheduler.violations),
        "displayed_slice_state": state.tolist(),
        "slice_state_encoding": {"idle": 0, "convert": 1, "acquire": 2},
        "codes": codes.tolist(),
        "applied_prefix_codes": trace.applied_codes.tolist(),
        "follower_final_pc": (trace.cumulative_abs_charge_c[:, -1] * 1e12).tolist(),
    }


# =============================================================================
# 2. D3 掩码覆盖与桥接电荷守恒
# =============================================================================
def figure_dem_usage(out: Path) -> dict:
    """枚举 v2.1 整数掩码，展示外供相位如何移动等权选通单元。

    输出 PNG 和可复算的整数计数。热图是 8 个逻辑转换槽，和 F01 的 18 个
    物理片 ID 不同。覆盖统计把外部的 64 对相位等权枚举，不涉及生成器速率。
    """
    # 以 Cu/8 为权重单位核对名义电荷。桥接交换一个 4Cu 半列与八片合计
    # 4Cu 的子阵列支路。用整数表示，任何一位权重差都不能被容差掩盖。
    weights = np.array([32] * 16 + [4, 2, 1, 1], dtype=np.int64)
    example = 155
    codes = (8, example, 255, 511)
    counts = {code: np.zeros((8, 16), dtype=np.int64) for code in codes}
    mask_cases = 0
    mismatches = 0
    for h in range(8):
        for v in range(8):
            for k in range(521):
                mask = post_mask(k, h, v)
                if k in counts:
                    counts[k] += np.asarray(mask)[:, :16]
                # 即使 coin=0 时两种 half 同结果，也保留完整输入域的计数，
                # 使它可与旧 D3 逐位验证覆盖规格对照。
                for coin in (0, 1):
                    for half in (0, 1):
                        selected = bridge_swap(mask, h, v, half << 3, coin)
                        actual = int((np.asarray(selected) * weights).sum())
                        mismatches += int(actual != 8 * k)
                        mask_cases += 1
    if mismatches:
        raise RuntimeError(f"D3 名义掩码不变量失配 {mismatches}/{mask_cases}")

    fig, axes = plt.subplots(
        1, 3, figsize=(12.5, 4.4), layout="constrained",
        gridspec_kw={"width_ratios": [1, 1, 1.12]},
    )
    phases = ((0, 0), (2, 3))
    masks = []
    palette = matplotlib.colors.ListedColormap([COLORS["white"], COLORS["blue"]])
    for panel, ax, (h, v) in zip(("a", "b"), axes[:2], phases, strict=True):
        mask = np.asarray(post_mask(example, h, v))[:, :16]
        masks.append(mask.tolist())
        # 方块尺寸与掩码坐标一一对应，SVG 中逐单元保留矢量边界。
        ax.pcolormesh(
            np.arange(17) - 0.5, np.arange(9) - 0.5, mask,
            cmap=palette, vmin=0, vmax=1, edgecolors=COLORS["grid"], linewidth=0.25,
        )
        ax.set_title(f"({panel}) h={h}, v={v}; k={example}")
        ax.set_xlabel("Main half-cell index")
        ax.set_xticks([0, 3, 7, 11, 15])
        ax.set_yticks(range(8))
        ax.set_xticks(np.arange(-0.5, 16, 1), minor=True)
        ax.set_yticks(np.arange(-0.5, 8, 1), minor=True)
        ax.grid(which="minor", color=COLORS["grid"], linewidth=0.42)
        ax.tick_params(which="minor", length=0)
        # 两个半单元共一宏列。框出随 h 移动的保留列，竖向图样由 v 决定。
        reserved = (7 + h) % 8
        ax.add_patch(matplotlib.patches.Rectangle(
            (2 * reserved - 0.5, -0.5), 2, 8, fill=False,
            edgecolor=COLORS["orange"], lw=1.5,
        ))
    axes[0].set_ylabel("Conversion slot")
    axes[1].tick_params(labelleft=False)

    summary = axes[2]
    rows = {}
    # 不用四张单色热图重复同一性质。点是 128 个单元的均值，横线覆盖
    # 单元间最小到最大。零散布由数据得到，不在绘图时预设。
    for y, (code, mask_counts) in enumerate(counts.items()):
        percent = 100 * mask_counts / 64
        lo, mean, hi = map(float, (percent.min(), percent.mean(), percent.max()))
        summary.hlines(y, lo, hi, color=COLORS["teal"], lw=3)
        summary.scatter(mean, y, color=COLORS["teal"], s=38, zorder=3)
        summary.annotate(f"{mean:.1f}%", (mean, y), xytext=(0, 8),
                         textcoords="offset points", ha="center", color=COLORS["teal"])
        rows[str(code)] = {
            "counts_out_of_64": mask_counts.tolist(), "mean_selection_pct": mean,
            "min_selection_pct": lo, "max_selection_pct": hi,
        }
    summary.set_title("(c) Uniform phase sweep")
    summary.set_yticks(range(len(codes)), [f"k = {k}" for k in codes])
    summary.set_ylim(len(codes) - 0.67, -0.6)
    summary.set_xlim(-7, 107)
    summary.set_xticks([0, 25, 50, 75, 100])
    summary.set_xlabel("Selected phase pairs (%)")
    summary.grid(axis="x")
    _figure_title(
        fig, "F02", "DEM rotates the selected cells",
        f"Blue = selected  •  Orange outline = reserved column  •  "
        f"64 phase pairs per code  •  {mask_cases:,} nominal mask checks including bridge",
    )
    _save_figure(fig, out, "F02_dem_usage.png")
    return {
        "figure": "F02_dem_usage.png",
        "contract": "v2.1 integer post_mask and bridge_swap reference",
        "phase_pairs": 64,
        "snapshot_code": example,
        "snapshot_phases_hv": [list(pair) for pair in phases],
        "snapshot_main_masks": masks,
        "codes": list(codes),
        "per_code": rows,
        "nominal_weight_checks": mask_cases,
        "nominal_weight_mismatches": mismatches,
    }


# =============================================================================
# 9. 等效输入电流
# =============================================================================
def figure_input_current(out: Path) -> dict:
    """计算显式等效电容在直流输入上的残余充电电流。"""
    # ΔQ = C·ΔV 仅描述从预置电平到直流输入的电荷差。两档补偿比例是
    # 等效模型输入，不能推出真实环路跟踪、参考供电或差分测量定义。
    capacitance_f = 20.5e-12
    dc = np.linspace(-V_FS, V_FS, 121)
    full = capacitance_f * np.abs(dc) * FS_HZ
    scenarios = ((0.0, COLORS["ink"], "o"), (0.85, COLORS["blue"], "s"),
                 (0.95, COLORS["teal"], "D"))
    fig, (curve, detail) = plt.subplots(
        1, 2, figsize=(10.8, 4.3), gridspec_kw={"width_ratios": (1.55, 1)},
        layout="constrained",
    )
    endpoint_ua = []
    for supplied, color, marker in scenarios:
        current = full * (1 - supplied)
        endpoint_ua.append(float(current[-1] * 1e6))
        curve.plot(dc, current * 1e3, color=color, marker=marker, markevery=20,
                   label=f"{supplied:.0%} precharged")
    curve.set_title("(a) DC input sweep")
    curve.set_xlabel("Differential input (V)")
    curve.set_ylabel("Mean charge per cycle × fs (mA)")
    curve.set_xlim(-V_FS, V_FS)
    curve.set_ylim(0, 2.65)
    curve.grid(axis="y")
    curve.legend(loc="upper center")
    positions = [2, 1, 0]
    detail.hlines(positions, 0, endpoint_ua, colors=[row[1] for row in scenarios], alpha=0.55)
    for y, value, (_, color, marker) in zip(positions, endpoint_ua, scenarios, strict=True):
        detail.scatter(value, y, color=color, marker=marker, s=47, zorder=3)
        detail.annotate(f"{value:,.0f}", (value, y), xytext=(6, 0), textcoords="offset points",
                        ha="left", va="center", color=color)
    detail.set_yticks(positions, ["0%", "85%", "95%"])
    detail.set_xlim(0, max(endpoint_ua) * 1.23)
    detail.set_ylim(-0.6, 2.6)
    detail.set_title("(b) At |Vin| = 3 V")
    detail.set_xlabel("Equivalent current (µA)")
    detail.grid(axis="x")
    _figure_title(fig, "F09", "Input charging current under assumed precharge",
                  "20.5 pF equivalent load  •  40 MS/s  •  Current estimated by C · |ΔV| · fs")
    _save_figure(fig, out, "F09_input_current.png")
    return {
        "figure": "F09_input_current.png",
        "equivalent_capacitance_f": capacitance_f,
        "full_scale_current_a": float(full[-1]),
        "precharge_fraction": [row[0] for row in scenarios],
        "full_scale_current_ua": endpoint_ua,
        "input_v": dc.tolist(),
        "unprecharged_current_a": full.tolist(),
    }


# =============================================================================
# F03. 静态电平扫描与支路越界
# =============================================================================
def figure_inl(model, out: Path, *, points: int, repeats: int, dynamic_n: int) -> dict:
    """按 P4 配置输出分面残差图及原始支路越界计数。

    points 是不同的输入电平数（至少 2）；repeats 是每电平的转换笔数（至少 1）。
    误差以 6 V / 2**20 归一；去最佳拟合直线后得到稀疏网格残差。
    点间输出步长偏差只作为这次网格的诊断，不能推断逐码 DNL。
    model、dynamic_n 保留以兼容生成入口；静态配置自行建立同一制造种子下的芯片。
    """
    from matplotlib.ticker import MaxNLocator

    from experiments.configs.matrix import ablation_configs

    if points < 2 or repeats < 1:
        raise ValueError("the level scan requires at least two levels and one repeat")

    # 配置开关来自 P4 定义，以免图和矩阵的 DEM、片选、失配口径各自漂移。
    cases = {
        "A0_ideal": "A0  Ideal",
        "A2_mismatch_noise": "A2  Mismatch + noise",
        "A3_dem_rotation": "A3  + DEM",
        "A4_dem_uniform_plan": "A4  + DEM + rotation",
        "A5_calibration": "A5  + LUT",
        "C1_dem_cal": "C1  DEM + LUT",
        "A6_dither_in_loop": "A6  + Dither",
        "C2_dem_dither": "C2  DEM + dither",
        "C3_all": "C3  DEM+Dith+LUT",
    }
    configs = {cfg["id"]: cfg for cfg in ablation_configs()}
    models = {}
    trained = {}
    rows = {}
    traces = {}
    sample_count = points * repeats
    dither_seed = 20261113

    # 每个制造/DEM 模式复用一次冻结 LUT。C3 的 dither 分支沿用 DEM 打开、
    # dither 关闭的 LUT，仅用于看现有解码路径受越界的影响。
    for case in cases:
        cfg = configs[case]
        model_key = (cfg["mismatch_std"], cfg["noise"])
        if model_key not in models:
            models[model_key] = _build_model(*model_key, seed=SEED)
        case_model = models[model_key]
        dem = cfg["dem"]
        use_dither = cfg.get("dither", False)
        calibration = None
        training = None
        if cfg.get("calibration", False):
            train_key = (*model_key, dem)
            if train_key not in trained:
                trained[train_key] = _train_calibration(case_model, dem=dem)
            calibration, training = trained[train_key]

        # 复用 P4 的对称 headroom。缩小输入区间规避一阶端点，本身不保证 ADC2 窗口足够。
        headroom = _dither_max_v(case_model.cfg) if use_dither else 0.0
        span = 0.985 * V_FS - headroom
        levels = np.linspace(-span, span, points)
        mean_error = np.empty(points)
        sigma = np.empty(points)
        invalid_levels = np.zeros(points, dtype=bool)
        counts = dict.fromkeys(("sadc_over", "ra_sat", "adc2_over", "analog_overflow"), 0)
        for index, level in enumerate(levels):
            words = None
            if use_dither:
                # 字流独立于噪声和片选，A6/C2/C3 对同一电平使用完全相同的 dither 字。
                generator = np.random.default_rng(np.random.SeedSequence([dither_seed, index]))
                words = generator.integers(0, 64, repeats, dtype=np.int64)
            raw, voltage = _run(
                case_model,
                lambda times, value=level: np.full_like(times, value, dtype=float),
                repeats,
                dem=dem,
                uniform_plan=cfg["uniform_plan"],
                calibration=calibration,
                dither_words=words,
            )
            # 保留整段记录。筛掉饱和笔再拟合会掩盖模型工作区间的缺口。
            mean_error[index] = np.mean(voltage) - level
            sigma[index] = np.std(voltage, ddof=1) if repeats > 1 else 0.0
            invalid_levels[index] = np.any(raw.analog_overflow)
            for name in counts:
                counts[name] += int(np.count_nonzero(getattr(raw, name)))
        fit = metrics.inl_from_mean_error(levels, mean_error, LSB20)
        peak = fit["inl_max_lsb"]
        local_step = float(np.max(np.abs(np.diff(mean_error))) / LSB20)
        valid = counts["analog_overflow"] == 0
        traces[case] = (levels, fit["inl_lsb"], invalid_levels)
        rows[case] = {
            "label": cases[case],
            "status": "behavioral_diagnostic" if valid else "analog_overrange",
            "fit_residual_peak_lsb20": peak,
            "inl_max_lsb20": peak if valid else None,
            "inl_max_ppm_fs": peak / 2**20 * 1e6 if valid else None,
            "local_step_error_peak_lsb20": local_step,
            "sigma_e_lsb20": float(np.mean(sigma) / LSB20),
            "input_min_v": float(levels[0]),
            "input_max_v": float(levels[-1]),
            "input_step_v": float(levels[1] - levels[0]),
            "dither_enabled": use_dither,
            "dither_seed": dither_seed if use_dither else None,
            "dither_headroom_v": headroom,
            "dem_enabled": dem,
            "uniform_plan": cfg["uniform_plan"],
            "noise_enabled": cfg["noise"],
            "mismatch_std": cfg["mismatch_std"],
            "sample_count": sample_count,
            "flag_counts": counts,
            "invalid_level_count": int(np.count_nonzero(invalid_levels)),
            "output_domain": (
                "post-decode voltage with frozen coarse-code offset"
                if calibration is not None else "nominal integer code, converted to volts"
            ),
            "lut_training": None if training is None else {**training, "dither_enabled": False},
        }

    fig, axes = plt.subplots(3, 3, figsize=(11.8, 8.3), sharex=True, layout="constrained")
    # 固定噪声组和越界组各自的同组刻度；理想对照单列小量程。
    # 每个面板标明实际刻度，以免读者通过曲线视觉高度直接比较不同量级的场景。
    ordered = list(cases)
    noisy_extent = max(rows[key]["fit_residual_peak_lsb20"] for key in ordered[1:6])
    dither_extent = max(rows[key]["fit_residual_peak_lsb20"] for key in ordered[6:])
    for index, (ax, key) in enumerate(zip(axes.flat, ordered, strict=True)):
        row = rows[key]
        levels, error, invalid_levels = traces[key]
        if index == 0:
            extent = row["fit_residual_peak_lsb20"]
            color = COLORS["blue"]
        elif index < 6:
            extent = noisy_extent
            color = COLORS["teal"]
        else:
            extent = dither_extent
            color = COLORS["rose"]
        ax.axhline(0, color=COLORS["muted"], linewidth=0.65, alpha=0.55)
        ax.plot(levels, error, color=color, linewidth=0.88)
        if np.any(invalid_levels):
            ax.scatter(
                levels[invalid_levels], error[invalid_levels],
                color=color, s=5, alpha=0.7, edgecolors="none",
            )
        ax.set_xlim(-V_FS, V_FS)
        ax.set_ylim(-max(1.1 * extent, 1e-3), max(1.1 * extent, 1e-3))
        ax.set_xticks([-3, 0, 3])
        ax.yaxis.set_major_locator(MaxNLocator(5, symmetric=True))
        ax.set_title(cases[key], loc="left")
        flagged = row["flag_counts"]["analog_overflow"]
        label = (
            f"{flagged / sample_count:.1%} flagged"
            if flagged else f"Peak {row['fit_residual_peak_lsb20']:.2f}"
        )
        ax.set_title(label, loc="right", fontsize=7.5, color=color, fontweight="normal")
    fig.supylabel("Best-fit residual  [LSB20]")
    axes[-1, 1].set_xlabel("Differential input voltage [V]")
    _figure_title(
        fig, "F03", "Static transfer residuals",
        f"{points} levels × {repeats} repeats   |   "
        "Shared scale within each group   |   Dither overrange retained",
    )
    filename = "F03_inl_dnl.png"
    _save_figure(fig, out, filename)
    return {
        "figure": filename,
        "points": points,
        "repeats": repeats,
        "lsb20_v": LSB20,
        "offset_v": 0.0,
        "configs": rows,
        "model_adc2_bits": case_model.cfg.adc2_n_bits,
        "model_adc2_window_v": [case_model.cfg.adc2_v_min, case_model.cfg.adc2_v_max],
        "note": (
            "逐电平重复并重置控制/噪声序列，采用最小二乘直线与零偏移网格。"
            "点间步长误差只服务稀疏网格诊断。模型支路越界时保留数值曲线，且不登记 INL 指标。"
        ),
    }


# =============================================================================
# F04. 相干正弦功率谱与残余能量闭合
# =============================================================================
def figure_psd(model, out: Path, *, n: int) -> dict:
    """在同一制造 realization 上对比三条相干记录。

    n 是输出样本数（至少 8）。输入幅度是差分峰值 [V]。
    返回实际基波、窗、频率间隔及整段模型的 SNDR、SFDR、等效 NSD 和越界计数。
    """
    if n < 8:
        raise ValueError("the spectrum comparison requires at least eight samples")
    tone_bin = max(1, min((n - 1) // 2, round(1e6 * n / FS_HZ)))
    fin = tone_bin * FS_HZ / n
    amplitude = V_FS * 0.999
    cases = (
        ("A2", "Mismatch + noise", False, False, COLORS["blue"]),
        ("A3", "DEM enabled", True, False, COLORS["teal"]),
        ("A5", "Frozen LUT", False, True, COLORS["purple"]),
    )
    fig, axes = plt.subplots(3, 1, figsize=(10.5, 7.3), sharex=True, sharey=True,
                             layout="constrained")
    rows = {}
    for ax, (case, title, dem, use_lut, color) in zip(axes, cases, strict=True):
        calibration, training = _train_calibration(model, dem=dem) if use_lut else (None, None)
        raw, voltage = _run(
            model,
            lambda t: amplitude * np.sin(2 * np.pi * fin * t),
            n,
            dem=dem,
            uniform_plan=False,
            calibration=calibration,
        )
        fitted = metrics.sine_fit_metrics(voltage, FS_HZ, fin)

        # 相干采样下矩形窗让基波落在一条 bin；用该 bin 的积分功率作分母。
        # 此处单位是每 Hz 的功率比。删去单条基波和 DC 后，其余积分和时域 SNDR 应闭合。
        spec = metrics.power_spectral_density(voltage, FS_HZ, window="boxcar")
        freq = np.asarray(spec["frequency_hz"])
        density = np.asarray(spec["density_v2_hz"])
        df = float(spec["df_hz"])
        base_power = float(density[tone_bin] * df)
        retained = np.ones(freq.size, dtype=bool)
        retained[[0, tone_bin]] = False
        residual_power = float(np.sum(density[retained]) * df)
        spectral_sndr = float(10 * np.log10(base_power / residual_power))
        sndr = float(fitted["SNDR_dB"])
        if not np.isclose(spectral_sndr, sndr, atol=1e-4, rtol=0.0):
            raise ValueError(f"coherent PSD residual disagrees with sine fit for {case}")
        relative = np.full(freq.size, np.nan)
        relative[retained] = 10 * np.log10(
            np.maximum(density[retained] / base_power, np.finfo(float).tiny)
        )
        ax.plot(freq / 1e6, relative, linewidth=0.73, color=color, alpha=0.9)
        ax.axvline(fin / 1e6, linewidth=0.72, linestyle=":", color=COLORS["muted"])
        ratios = f"SNDR {sndr:.2f} dB     SFDR {fitted['SFDR_dB']:.2f} dB"
        counts = {
            name: int(np.count_nonzero(getattr(raw, name)))
            for name in ("sadc_over", "ra_sat", "adc2_over", "analog_overflow")
        }
        if counts["analog_overflow"]:
            ratios = f"{counts['analog_overflow']}/{n} overrange · " + ratios
        ax.set_xlim(0, FS_HZ / 2e6)
        ax.set_title(f"{case}  {title}", loc="left")
        ax.set_title(ratios, loc="right", fontsize=9.5, color=color, fontweight="normal")
        rows[case] = {
            "label": title,
            "sndr_db": sndr,
            "spectral_sndr_db": spectral_sndr,
            "sfdr_db": float(fitted["SFDR_dB"]),
            "spur_hz": float(fitted["spur_Hz"]),
            "nsd_nv": float(fitted["NSD_V_rtHz"]) * 1e9,
            "fit_noise_reliable": bool(fitted["noise_fit_reliable"]),
            "fundamental_power_v2": base_power,
            "residual_power_v2": residual_power,
            "flag_counts": counts,
            "output_domain": (
                "post-decode voltage with frozen coarse-code offset"
                if use_lut else "nominal integer code, converted to volts"
            ),
            "lut_training": training,
        }

    axes[-1].set_xlabel("Frequency  [MHz]")
    fig.supylabel("Residual power spectral density  [dBc/Hz]")
    _figure_title(
        fig, "F04", "Coherent tone residual spectrum",
        f"{fin / 1e6:.6f} MHz / {FS_HZ / 1e6:g} MS/s   |   N = {n:,}   |   "
        "DC and fundamental bin omitted",
    )
    filename = "F04_psd.png"
    _save_figure(fig, out, filename)
    return {
        "figure": filename,
        "n": n,
        "fin_hz": fin,
        "tone_bin": tone_bin,
        "sample_rate_hz": FS_HZ,
        "stimulus_peak_v": amplitude,
        "frequency_bin_width_hz": FS_HZ / n,
        "window": "boxcar",
        "psd_reference": "integrated fundamental bin power; DC and fundamental omitted",
        "mismatch_std": model.parameters.unit_mismatch_std,
        "configs": rows,
    }


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


# =============================================================================
# 文件名     : run_review_figures.py（F05–F08 函数替换稿）
# 模块名     : Dither、follower、动态节点、普通 AZ 图
# 版本       : v1.0
# 功能       : 将现有行为模型输出和独立合同算术画成可复核的诊断图。
#
# 设计说明   :
#   * 只在生成图片时写指定输出目录，所有参数、单位和判定范围进入摘要。
#   * 两套 dither 极性分开，follower 横轴使用各路径自身的事件，RA 横轴用真实相位。
#   * AZ 模型接收残余误差，配对模拟观测它的传递，保持时间扫描另走独立内核。
# =============================================================================


# =============================================================================
# 5. Dither 幅度和命令边界
# =============================================================================
def figure_dither_margin(out: Path) -> dict:
    """枚举六位字、普通码及 guard 端点，保存 F05 并返回 V／命令统计。

    out 为既有输出目录。Fig.19 等效 subtract 绑定来自模型，add 绑定来自合同
    v2.1 §1、§3、§6。枚举域是笛卡尔积，未要求每个码与字组合由模拟量化产生。
    """
    from matplotlib.colors import ListedColormap, to_rgba

    from adi_model.control.dither import complete_flash3_sar6_prefix_table

    # --- 旧 Fig.19 等效分支 ---
    spec = Fig19StaticSpec(
        v_fs=V_FS,
        step_q=FIG19_STEP_Q,
        voltage_fraction_bits=32,
        fractional_lower_weights_q=FIG19_FRACTIONAL_WEIGHTS_Q,
        equivalent_polarity="subtract",
        provenance="tools/run_review_figures.py explicit Fig.19 binding",
    )
    words = np.arange(64)
    program = Fig19DitherProgram.from_words(words, spec=spec)
    legacy_lower = program.lower_v
    upper = program.upper_code
    table = complete_flash3_sar6_prefix_table()
    shifted = table[None, :, :] - upper[:, None, None]
    legacy_invalid = ((shifted < 0) | (shifted > 511)).sum(axis=(1, 2))

    # --- 合同 v2.1 分支 ---
    # [口径] 合同 §1.3 的 lower 子单位是 /8，旧模型的 4:2:1 权重和为一个完整粗阶。
    # 两种模式的扰动方向相反，左图只比较幅值，右侧网格才按各自符号算命令。
    contract_step_v = (2 * V_FS) / 512
    d5 = (words >> 4) & 1
    lower_fraction = words & 7
    contract_lower = (2 * d5 + lower_fraction / 8) * contract_step_v
    contract_coarse = np.arange(520)
    contract_commands = contract_coarse[None, :] + upper[:, None]
    contract_invalid = contract_commands > 520
    contract_guard = (contract_commands >= 512) & ~contract_invalid
    upper_choices = np.unique(upper)

    # --- 对照图只给决定取值的位 ---
    # 左图对每个 lower 组合取一份，U 位不参与 lower 幅度。命令网格反过来只依赖 U。
    fig, (ax0, ax1, ax2) = plt.subplots(
        1, 3, figsize=(12.2, 4.25), layout="constrained",
        gridspec_kw={"width_ratios": (1.30, 1.0, 1.0)},
    )
    lower_set = upper == 0
    lower_code = np.arange(np.count_nonzero(lower_set))
    ax0.plot(
        lower_code, legacy_lower[lower_set] * 1e3, color=COLORS["orange"],
        lw=1.8, marker="o", ms=4, label="Fig. 19 equivalent (subtract)",
    )
    ax0.plot(
        lower_code, contract_lower[lower_set] * 1e3, color=COLORS["blue"],
        lw=1.5, marker="D", ms=3, linestyle="--", label="Contract v2.1 (add)",
    )
    ax0.set(
        title="(a) Lower injection magnitude",
        xlabel="Lower pattern (D5, D3:D1)", ylabel="Input magnitude (mV)",
        xticks=[0, 3, 7, 8, 11, 15], ylim=(0, 40),
    )
    ax0.legend(loc="upper left", fontsize="small")

    color_map = ListedColormap([
        to_rgba(COLORS["muted"], 0.14),
        to_rgba(COLORS["teal"], 0.26),
        to_rgba(COLORS["rose"], 0.25),
    ])
    for ax, coarse, sign, max_k in (
        (ax1, np.arange(8), -1, 511),
        (ax2, np.arange(512, 520), 1, 520),
    ):
        k = coarse[None, :] + sign * upper_choices[:, None]
        rejected = (k < 0) | (k > max_k)
        category = np.where(rejected, 2, (k >= 512).astype(int))
        # 上方第一行是 U=0，彩色格及数字共同编码范围，避免依赖红绿色觉。
        ax.pcolormesh(
            np.arange(coarse.size + 1) - 0.5,
            np.arange(upper_choices.size + 1) - 0.5,
            category, cmap=color_map, vmin=-0.5, vmax=2.5,
        )
        ax.invert_yaxis()
        for i, row in enumerate(k):
            for j, value in enumerate(row):
                ax.text(
                    j, i, str(value).replace("-", "−"), ha="center", va="center", fontsize=7.2,
                    color=COLORS["rose"] if rejected[i, j] else COLORS["ink"],
                    fontweight="bold" if rejected[i, j] else "normal",
                )
        ax.set(
            xlabel="Coarse code c", ylabel="Upper code U",
            xticks=np.arange(coarse.size), xticklabels=coarse,
            yticks=np.arange(upper_choices.size), yticklabels=upper_choices,
        )
        ax.tick_params(length=0, pad=4)
        ax.grid(False)

    ax1.set_title("(b) Fig. 19 lower edge   k = c − U", loc="left", pad=9)
    ax2.set_title("(c) v2.1 upper edge   k = c + U", loc="left", pad=9)
    _figure_title(
        fig, "F05", "Dither magnitude and command limits",
        "Nominal arithmetic across six-bit words. Red cells are out of range; green cells use guard codes.",
    )
    _save_figure(fig, out, "F05_dither_margin.png")
    return {
        "figure": "F05_dither_margin.png",
        "model_scope": "Fig19 subtract equivalent and v2.1 command-domain arithmetic",
        "worst_deficit_v": float(legacy_lower.max()),
        "worst_lower_bound_v": float(-V_FS + legacy_lower.max()),
        "max_illegal_events": int(legacy_invalid.max()),
        "illegal_by_word": legacy_invalid.tolist(),
        "fig19_subtract": {
            "coarse_step_v": spec.coarse_step_v, "coarse_domain": [0, 511],
            "rdac_domain": [0, 511], "lower_magnitude_v_by_word": legacy_lower.tolist(),
            "prefix_events_per_code": int(table.shape[1]),
            "invalid_word_code_event_pairs": int(legacy_invalid.sum()),
        },
        "contract_v21_add": {
            "coarse_step_v": contract_step_v, "coarse_domain": [0, 519],
            "rdac_domain": [0, 520], "lower_magnitude_v_by_word": contract_lower.tolist(),
            "upper_codes_by_word": upper.tolist(), "guard_domain": [512, 520],
            "word_code_combinations": int(contract_commands.size),
            "invalid_word_code_combinations": int(contract_invalid.sum()),
            "legal_guard_word_code_combinations": int(contract_guard.sum()),
            "maximum_legal_coarse_by_upper": {
                str(value): min(contract_coarse[-1].item(), 520 - value.item())
                for value in upper_choices
            },
        },
    }


# =============================================================================
# 6. Follower 电荷记账与最终状态
# =============================================================================
def figure_follower(model, out: Path, *, n_codes: int) -> dict:
    """比较三种 RDAC 状态序列，保存 F06 并返回 pC 统计。

    n_codes 为抽取的整数粗码点数。各路径读取相同片组、掩码状态和 ±V_FS
    理想参考，从低轨基线累计每次净电荷变动绝对值。该度量不积分瞬态电流。
    """
    from adi_model.control.follower import compare_reference_cost

    if n_codes < 2:
        raise ValueError("F06 reference charge comparison requires at least two coarse codes")
    codes = np.unique(np.linspace(0, 511, n_codes).astype(np.int64))
    plan = round_robin_plan(codes.size)
    pool = _pool(model, plan)
    states = np.zeros(codes.size, dtype=np.int64)
    prefixes = ResolvedPrefixes.from_final(codes)
    same_binding = {
        "slice_ids": plan.conv,
        "reference_high_v": V_FS,
        "reference_low_v": -V_FS,
    }
    follower = run_follower(pool, prefixes, states, **same_binding)
    direct = run_direct_final(pool, codes, states, **same_binding)
    trial = run_trial_sar(pool, codes, states, **same_binding)
    comparison = compare_reference_cost(follower, direct, trial, fs_hz=FS_HZ)
    paths = [
        ("follower", "Resolved follower", follower, COLORS["blue"]),
        ("direct", "Direct final load", direct, COLORS["muted"]),
        ("trial", "Trial and undo", trial, COLORS["orange"]),
    ]
    fig, (ax0, ax1) = plt.subplots(
        1, 2, figsize=(10.6, 4.25), layout="constrained",
        gridspec_kw={"width_ratios": (1.20, 1.0)},
    )

    # [时序] 每条线到自身最后一个更新截止，防止把单事件对照读成七拍因果路径。
    # 从预设低轨开始取 0，不在同一横轴上假设不同机制的事件具有相同纳秒预算。
    totals = {}
    curve_by_path = {}
    for key, label, trace, color in paths:
        cumulative = np.r_[0.0, np.asarray(trace.cumulative_abs_charge_c).mean(axis=0) * 1e12]
        totals[key] = float(cumulative[-1])
        curve_by_path[key] = cumulative.tolist()
        if key == "direct":
            ax0.scatter(
                trace.n_events, cumulative[-1], marker="D", color=color, s=41,
                label=f"{label} (1)", zorder=7,
            )
        else:
            ax0.step(np.arange(cumulative.size), cumulative, where="post", color=color, lw=1.9)
            ax0.scatter(
                np.arange(1, cumulative.size), cumulative[1:], color=color, s=14,
                label=f"{label} ({trace.n_events})", zorder=5,
            )
    ax0.set(
        title="(a) Charge over the update sequence", xlabel="Update index within each path",
        ylabel="Mean cumulative |ΔQ| (pC)", xticks=[0, 1, 4, 7, 10, 14, 18],
        xlim=(-0.15, trial.n_events + 0.5), ylim=(0, totals["trial"] * 1.13),
    )
    ax0.legend(loc="upper left", fontsize="small")

    labels = [label for _, label, _, _ in paths]
    values = [totals[key] for key, _, _, _ in paths]
    bars = ax1.barh(labels, values, height=0.52, color=[color for _, _, _, color in paths])
    ax1.bar_label(bars, labels=[f"{value:.1f}" for value in values], padding=6)
    ax1.invert_yaxis()
    ax1.set(
        title="(b) Charge per conversion", xlabel="Mean total |ΔQ| (pC)",
        xlim=(0, totals["trial"] * 1.19),
    )
    ax1.grid(False, axis="y")
    ratio = comparison["trial_over_follower_charge_ratio"]
    final_delta_pc = comparison["final_charge_mismatch_c"] * 1e12
    _figure_title(
        fig, "F06", "Reference charge in three RDAC paths",
        f"{codes.size} matched coarse codes. Trial / follower = {ratio:.2f}×. "
        f"Max final charge difference = {final_delta_pc:.3g} pC.",
    )
    _save_figure(fig, out, "F06_follower_charge.png")
    return {
        "figure": "F06_follower_charge.png",
        "codes": int(codes.size),
        "sampled_coarse_codes": codes.tolist(),
        "model_scope": "ideal-rail static charge accounting from code-0 baseline",
        "totals_pc": totals,
        "mean_cumulative_pc_by_path": curve_by_path,
        "event_counts": comparison["events"],
        "trial_over_follower": ratio,
        "max_final_charge_difference_pc": final_delta_pc,
        "statically_equivalent": bool(comparison["statically_equivalent"]),
        "reference_rails_v": [-V_FS, V_FS],
    }


# =============================================================================
# 7. 参考建立与 ADC2 相位
# =============================================================================
def figure_dynamics(out: Path, *, n: int = 32) -> dict:
    """重算 N 笔动态记录并保存 F07，返回相位、参考误差和跟踪误差。

    n 为转换笔数，时刻用相对采样孔径 ns，参考误差用 μV，跟踪差用 mV。
    所有时间和带宽来自 _dynamic_config 的工程参数，参考两阶段指数衰减来自同一内核。
    """
    from adi_model.control.dither import complete_flash3_sar6_prefix_table
    from experiments.fig11 import _all_signal

    # 状态内核在样本间保留节点值；只在记录开头显式置零，所以不另称稳态测试。
    # 这里不用默认随机 DEM，回转片组和已决前缀从两个明确绑定输入。
    model, cfg = _dynamic_config()
    plan = round_robin_plan(n)
    pool = PhysicalSlicePool(
        cfg,
        chip=model.chip,
        nominal_codebook=model.codebook,
        mask_binding=fig11_masks,
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
    names = list(trace.phase_names)
    event_times = np.asarray(trace.event_times_s) * 1e9
    reference = np.asarray(trace.phase_reference_v)
    reference_at_update = np.asarray(trace.reference_event_error_v)
    ra = np.asarray(trace.phase_ra_v)
    adc2 = np.asarray(trace.phase_adc2_v)
    entry = names.index("ra_entry")
    accurate = names.index("reference_accurate")
    narrow = names.index("adc2_narrow")
    aperture = names.index("adc2_aperture")

    # [口径] 编程时间是相对 RA 起点的 10 ns。图统一用采样孔径作为零点，
    # 内核返回的 16 ns 才是可与 RDAC 事件、5 ns 参考切换同轴比较的时刻。
    timeline = np.r_[event_times, times]
    timeline_order = np.argsort(timeline)
    ref_uv = np.max(np.abs(np.concatenate([reference_at_update, reference], axis=1)), axis=2) * 1e6
    ref_q10, ref_median, ref_q90 = np.percentile(ref_uv[:, timeline_order], [10, 50, 90], axis=0)
    ref_time_ns = timeline[timeline_order]
    # 旧摘要使用双轨平均绝对值，图使用逐笔较大轨的分位数，两种统计不能互换。
    # 摘要中的 bits 只表示该比值的 log2，既未折算到 RA 输入也不是有效分辨率。
    e_entry = float(np.mean(np.abs(reference[:, entry, :])))
    e_final = float(np.mean(np.abs(reference[:, aperture, :])))
    tracking_mv = (adc2 - ra) * 1e3
    tracking_q10, tracking_median, tracking_q90 = np.percentile(tracking_mv, [10, 50, 90], axis=0)

    fig, (ax0, ax1) = plt.subplots(
        1, 2, figsize=(10.7, 4.45), layout="constrained",
        gridspec_kw={"width_ratios": (1.18, 1.0)},
    )
    # [观测] 只着色内核记录的区间点，中间连线为读图导引。零误差在对数轴无定义，
    # 勿用虚构噪声地板垫高；把零遮掉后数据仍在 JSON 摘要里保留。
    positive_low = np.ma.masked_less_equal(ref_q10, 0)
    positive_high = np.ma.masked_less_equal(ref_q90, 0)
    ax0.fill_between(
        ref_time_ns, positive_low, positive_high, color=COLORS["blue"], alpha=0.15,
        label="10th–90th percentile",
    )
    ax0.plot(
        ref_time_ns, np.ma.masked_less_equal(ref_median, 0), color=COLORS["blue"],
        marker="o", ms=4, linestyle=":", lw=1.5, label="Median",
    )
    ax0.axvline(times[accurate], color=COLORS["muted"], alpha=0.55, lw=1, linestyle="--")
    ax0.axvline(times[entry], color=COLORS["ink"], alpha=0.50, lw=1, linestyle=":")
    ax0.set(
        title="(a) Reference rail error", xlabel="Time after sampling (ns)",
        ylabel="Larger rail |error| (µV)", yscale="log", xlim=(-0.25, times[aperture] + 0.55),
        xticks=[0, 3, 6, 10, 14, 18],
    )
    ax0.legend(loc="upper right", fontsize="small", title=f"Across {n} conversions")

    # [观测] 同笔相减避免 RA 与 ADC2 两条近似电压曲线重叠，也不会被整体均值抵消。
    # 每个相位的散点都用其实际时刻，x 轴标签直接说明带宽切换和最后读取位置。
    # 保留每笔有符号差值和 10–90 百分位，便于查出平均误差无法代表的离群转换。
    for point in (entry, narrow, aperture):
        x_t = times[point]
        ax1.scatter(
            np.full(n, x_t), tracking_mv[:, point], color=COLORS["teal"], alpha=0.25, s=13,
        )
        center = tracking_median[point]
        ax1.errorbar(
            x_t, center, yerr=[[center - tracking_q10[point]], [tracking_q90[point] - center]],
            fmt="o", ms=5, color=COLORS["ink"], lw=1.35, capsize=4, zorder=5,
        )
    ax1.axhline(0.0, color=COLORS["muted"], lw=0.9, alpha=0.75)
    ax1.set(
        title="(b) ADC2 tracking at phase boundaries", ylabel="ADC2 input − RA output (mV)",
        xlabel="Time after sampling (ns)", xticks=times[[entry, narrow, aperture]],
        xticklabels=[
            f"RA start\n{times[entry]:g} ns", f"BW ↓\n{times[narrow]:g} ns",
            f"Read\n{times[aperture]:g} ns",
        ],
        xlim=(times[entry] - 1.0, times[aperture] + 1.0),
    )
    ax1.grid(False, axis="x")
    _figure_title(
        fig, "F07", "Reference settling and ADC2 tracking",
        "Assumed poles. Reference switches at 5 ns. ADC2 bandwidth falls from 300 to 150 MHz at 16 ns.",
    )
    _save_figure(fig, out, "F07_reference_ra_adc2.png")
    return {
        "figure": "F07_reference_ra_adc2.png",
        "n": n,
        "input": {"peak_v": 2.0, "fin_hz": 1e6},
        "model_scope": "assumed response model; finite sample phase observations",
        "assumptions": {
            "reference_tau_fast_ns": cfg.dyn_tau_ref * 1e9,
            "reference_tau_accurate_ns": cfg.conversion.fine_reference_tau_s * 1e9,
            "reference_decoupling_f": cfg.dyn_c_decouple,
            "ra_bandwidth_hz": cfg.conversion.ra_bandwidth_hz,
            "adc2_wide_bandwidth_hz": cfg.conversion.adc2_wide_bandwidth_hz,
            "adc2_narrow_bandwidth_hz": cfg.conversion.adc2_narrow_bandwidth_hz,
        },
        "phase_times_ns": times.tolist(),
        "phase_names": names,
        "reference_error_ra_entry_uv": e_entry * 1e6,
        "reference_error_aperture_uv": e_final * 1e6,
        "extra_bits_settled_during_ra": float(np.log2(e_entry / e_final))
        if e_entry > 0 and e_final > 0 else None,
        "reference_larger_rail_error_uv": {
            "time_ns": ref_time_ns.tolist(), "p10": ref_q10.tolist(),
            "median": ref_median.tolist(), "p90": ref_q90.tolist(),
        },
        "adc2_minus_ra_mv": {
            "p10": tracking_q10.tolist(), "median": tracking_median.tolist(),
            "p90": tracking_q90.tolist(),
            "aperture_abs_max": float(np.max(np.abs(tracking_mv[:, aperture]))),
        },
        "ra_sat": int(np.count_nonzero(np.asarray(trace.ra_sat))),
    }


# =============================================================================
# 8. 普通 AZ 残余注入与保持漂移
# =============================================================================
def figure_az(model, out: Path, *, n: int) -> dict:
    """保存 F08 并报告普通 AZ 残余端口的配对响应和保持漂移。

    n 为同一采样点数，两次模拟共用器件、输入、时序和 seed。AZ 参数的 offset/noise 是
    输入等效的残余 [V]，由模型加到转换路径；独立保持扫描显式将 ns 换算成 s。
    """
    if n < 8:
        raise ValueError("F08 paired coherent sine requires at least 8 samples")
    # 默认保留 N=8192 时的 205 bin，小记录取最接近同一频率的整 bin。
    tone_bin = max(1, round(n * 205 / 8192))
    fin = tone_bin * FS_HZ / n
    amplitude_v = 0.5
    cfg = OrdinaryAZConfig(enabled=True, offset_v=1e-3, noise_v=5e-5, seed=SEED)

    # [入口] 默认模型中的普通 AZ 是误差端口，关闭相当于不注入这个端口。
    # 采用同一 run_id，使其他噪声、时序和片组选通一一对应。
    def input_tone(t):
        return amplitude_v * np.sin(2 * np.pi * fin * t)

    baseline = model.simulate(input_tone, n, run_id=0)
    injected = model.simulate(input_tone, n, run_id=0, ordinary_az=cfg)
    reference = np.asarray(baseline.raw.x_ref)
    # 配对条件若破坏则退出，随机输入差会直接污染输出相减后的均值和标准差。
    if not np.array_equal(reference, injected.raw.x_ref):
        raise RuntimeError("F08 paired experiments have different reference samples")
    baseline_v = np.asarray(baseline.raw.out, dtype=float)
    injected_v = np.asarray(injected.raw.out, dtype=float)
    # 保留非裁剪差值。端到端量化、限幅和扰动可能让个别样本偏离设定的高斯残余。
    delta_uv = (injected_v - baseline_v) * 1e6

    # [口径] 端到端模型每笔 AZ 刷新时 hold≈0。另调用误差内核给出已知保持时间，
    # 这样 100 ns 的参数扫点不会被误读成 40 MS/s 工作时的真实保持区间。
    holds_ns = np.array([0, 1, 5, 20, 50, 100], dtype=float)
    droop_cfg = OrdinaryAZConfig(enabled=True, droop_v_per_s=1e3)
    hold_response = apply_ordinary_az(
        np.zeros(holds_ns.size), np.arange(holds_ns.size), droop_cfg, hold_time_s=holds_ns * 1e-9,
    )
    droop_uv = hold_response.residual_v * 1e6
    delta_mean = float(np.mean(delta_uv))
    delta_std = float(np.std(delta_uv))

    fig, (ax0, ax1) = plt.subplots(1, 2, figsize=(10.5, 4.25), layout="constrained")
    bins = max(9, min(50, int(np.sqrt(n))))
    ax0.hist(
        delta_uv, bins=bins, color=COLORS["blue"], edgecolor=COLORS["white"], linewidth=0.6,
    )
    ax0.axvline(
        cfg.offset_v * 1e6, color=COLORS["orange"], linestyle="--", lw=1.7,
        label=f"Injected DC = {cfg.offset_v * 1e6:.0f} µV",
    )
    ax0.set(
        title="(a) Output response to residual injection",
        ylabel="Samples per bin", xlabel="Paired output difference (µV)",
    )
    ax0.legend(loc="upper left", fontsize="small")

    ax1.plot(holds_ns, droop_uv, color=COLORS["teal"], marker="o", ms=5, lw=1.7)
    ax1.set(
        title="(b) Specified hold times", xlabel="Hold time since AZ refresh (ns)",
        ylabel="Modeled droop (µV)", xlim=(-2, 105), ylim=(0, 112),
    )
    ax1.annotate(
        f"{droop_uv[-1]:.0f} µV at {holds_ns[-1]:g} ns",
        xy=(holds_ns[-1], droop_uv[-1]), xytext=(-12, 4), textcoords="offset points",
        ha="right", va="bottom", color=COLORS["ink"],
    )
    _figure_title(
        fig, "F08", "Ordinary auto-zero residual model",
        f"{n:,} matched samples. Paired shift {delta_mean:.1f} µV, σ {delta_std:.1f} µV. "
        f"Hold sweep uses an assumed {droop_cfg.droop_v_per_s / 1e3:g} kV/s.",
    )
    _save_figure(fig, out, "F08_az.png")

    # 保留两次运行的原始统计用途，配对差用于隔离本图新增的残余项。
    # NSD 包含其他模型误差，仍保留在数据里以便审读，不在画布上当作 AZ 噪声结果。
    rows = {}
    for label, signal in (("No residual injection", baseline_v), ("Injected residual", injected_v)):
        sine_fit = metrics.sine_fit_metrics(signal, FS_HZ, fin)
        rows[label] = {
            "mean_error_uv": float(np.mean(signal - reference) * 1e6),
            "nsd_nv": float(sine_fit["NSD_V_rtHz"]) * 1e9,
        }
    return {
        "figure": "F08_az.png",
        "n": n,
        "fin_hz": fin,
        "input_peak_v": amplitude_v,
        "model_scope": "paired sensitivity to residual AZ input, plus explicit hold-time kernel",
        "results": rows,
        "assumed_residual": {"offset_v": cfg.offset_v, "noise_rms_v": cfg.noise_v, "seed": cfg.seed},
        "paired_difference_uv": {
            "mean": delta_mean, "std": delta_std,
            "p10": float(np.percentile(delta_uv, 10)), "p90": float(np.percentile(delta_uv, 90)),
        },
        "hold_sweep": {
            "slope_v_per_s": droop_cfg.droop_v_per_s,
            "hold_ns": holds_ns.tolist(), "residual_uv": droop_uv.tolist(),
        },
    }


# =============================================================================
# F11 · 逐码修正表在同芯片和不同芯片上的表现
# =============================================================================
def figure_calibration_generalization(
    out: Path,
    *,
    points: int,
    repeats: int,
    seeds=(20261105, 20261106, 20261107, 20261108, 20261109),
) -> dict:
    """比较采样 INL 峰值，图写入 out 并返回每颗接收芯片的记录。

    points 是电平数，repeats 是每电平记录数。训练为逐粗码输出电压修正，
    每颗芯片独立拟合；测试对该芯片换用独立噪声和片选流。
    """
    if points < 2 or repeats < 1 or len(set(seeds)) != len(seeds) or len(seeds) < 2:
        raise ValueError("F11 needs at least two levels and two distinct chip seeds")
    # 留出记录的电平和次序对各颗芯片相同。乱序读出后再复位到电平顺序，
    # 避免特定输入跨越与片选、噪声序列的位置相关。
    # ±0.985 满量程与原评估范围一致，训练仍遍历整个粗码域。
    # 留出样本只用于评估，不会反向更新逐码 LUT。
    span = 0.985 * V_FS
    levels = np.linspace(-span, span, points)
    shuffled_seed = 20261117
    test_run_id = 1
    order = np.random.default_rng(shuffled_seed).permutation(points * repeats)
    stimulus = _ramp_fn(np.repeat(levels, repeats)[order])

    mismatch_std = 1e-3
    chips = {seed: _build_model(mismatch_std, True, seed=seed) for seed in seeds}
    # 训练沿用 P4 校准训流 run_id=0，其内部覆盖检查遇到缺码直接报错。
    # 评估 run_id=1 只更换同一制造实现的噪声和片选，保留器件失配。
    fits = {seed: _train_calibration(chip) for seed, chip in chips.items()}
    keys = ("plain", "dem", "calibrated", "cross_chip")
    rows = {key: [] for key in ("seed", "other_lut_seed", *keys)}

    def peak_inl(voltage) -> float:
        """把乱序记录按电平聚合，去除各自的最佳拟合直线后取最大绝对偏差。"""
        # order 是原网格下标的置换，用散写恢复每组电平的独立重复。
        # 去除增益和偏置后的采样峰值只覆盖当前网格，不能替代逐码密度。
        ordered = np.empty_like(voltage)
        ordered[order] = voltage
        mean_error = ordered.reshape(points, repeats).mean(axis=1) - levels
        return float(metrics.inl_from_mean_error(levels, mean_error, LSB20)["inl_max_lsb"])

    analog_overflows = {"plain": [], "dem": []}
    for index, (seed, chip) in enumerate(chips.items()):
        # 环形指定上一颗为异芯片 LUT 来源，每颗芯片都作为接收者和提供者
        # 恰好一次。原始/自校/跨片复用同一条无 DEM 电压记录，隔离系数影响。
        donor = seeds[index - 1]
        own_lut = fits[seed][0]
        other_lut = fits[donor][0]
        raw, base = _run(
            chip, stimulus, order.size, dem=False, uniform_plan=False, run_id=test_run_id
        )
        dem_raw, dem_output = _run(
            chip, stimulus, order.size, dem=True, uniform_plan=False, run_id=test_run_id
        )
        coarse = np.asarray(raw.coarse, dtype=np.int64)
        rows["seed"].append(seed)
        rows["other_lut_seed"].append(donor)
        rows["plain"].append(peak_inl(base))
        rows["dem"].append(peak_inl(dem_output))
        rows["calibrated"].append(peak_inl(own_lut.apply(coarse, base)))
        rows["cross_chip"].append(peak_inl(other_lut.apply(coarse, base)))
        # 越界样本照常参与统计，额外报告标志，避免筛掉困难样本。
        analog_overflows["plain"].append(int(np.count_nonzero(raw.digital_observation.overflow)))
        analog_overflows["dem"].append(int(np.count_nonzero(dem_raw.digital_observation.overflow)))

    medians = {key: float(np.median(rows[key])) for key in keys}
    filename = "F11_calibration_generalization.png"
    _plot_calibration(out, filename, rows, medians)
    return {
        "figure": filename,
        "seeds": list(seeds),
        "points": points,
        "repeats": repeats,
        "unit_and_bridge_mismatch_std": mismatch_std,
        "validation_input_v": [-span, span],
        "validation_run_id": test_run_id,
        "validation_permutation_seed": shuffled_seed,
        "analog_overflow_count": analog_overflows,
        "training": {str(seed): fits[seed][1] for seed in seeds},
        "per_seed": rows,
        "means": {key: float(np.mean(rows[key])) for key in keys},
        "medians": medians,
        "spreads": {key: float(np.std(rows[key], ddof=1)) for key in keys},
        "note": "Per-coarse-code output correction on the held-out grid. "
                "Each same-chip and other-chip pair shares its uncorrected input record.",
    }


def _plot_calibration(out: Path, filename: str, rows: dict, medians: dict) -> None:
    """依据 F11 的逐片统计出图，印刷尺寸复核可以复用训练结果。"""
    seeds = rows["seed"]
    keys = ("plain", "dem", "calibrated", "cross_chip")
    # 左图保留逐颗芯片结果及中位数，右图固定接收芯片、一对一比较两个 LUT。
    # 不给五个制造抽样画置信区间，也不跨芯片错配未校准与校准结果。
    # DEM 有自己的转换流，只作独立策略对照。跨片转移的因果归属
    # 由右图同一记录切换 LUT 来源检验。
    colors = (COLORS["muted"], COLORS["blue"], COLORS["teal"], COLORS["rose"])
    labels = ("Uncorrected", "DEM only", "Same-chip LUT", "Other-chip LUT")
    fig, (overview, paired) = plt.subplots(1, 2, figsize=(12.4, 5.8))
    fig.subplots_adjust(left=0.15, right=0.97, bottom=0.24, top=0.91, wspace=0.38)
    # 纵向错列仅为分开重合散点，读数只取横坐标。
    sample_offsets = np.linspace(-0.13, 0.13, len(seeds))
    for level, (key, color) in enumerate(zip(keys, colors, strict=True)):
        overview.scatter(rows[key], level + sample_offsets, color=color, s=35, zorder=3)
        overview.vlines(medians[key], level - 0.24, level + 0.24, color=color, lw=2.4)
    overview.set_yticks(range(len(labels)), labels)
    overview.set_ylim(len(labels) - 0.5, -0.5)
    overview.set_title("(a) Model chips", loc="left", pad=10)

    for y, (own, other) in enumerate(zip(rows["calibrated"], rows["cross_chip"], strict=True)):
        paired.plot([own, other], [y, y], color=COLORS["grid"], lw=2.6, zorder=1)
    positions = np.arange(len(seeds))
    other_marker = paired.scatter(rows["cross_chip"], positions, marker="D", color=colors[3],
                                  s=30, zorder=3, label="Other-chip LUT")
    own_marker = paired.scatter(rows["calibrated"], positions, marker="o", color=colors[2],
                                s=30, zorder=4, label="Same-chip LUT")
    paired.set_yticks(positions, [str(seed) for seed in seeds])
    paired.set_ylim(len(seeds) - 0.5, -0.5)
    paired.set_title("(b) Paired on the receiving chip", loc="left", pad=10)
    for ax in (overview, paired):
        ax.set_xlabel("Peak sampled |INL| [LSB20]")
        ax.set_xlim(left=0)
        ax.grid(axis="x")
        ax.tick_params(axis="y", length=0)
    # 同一指标共用刻度。左图竖线为中位数，右图以形状区分 LUT 来源。
    paired.set_xlim(overview.get_xlim())
    paired.legend(handles=[own_marker, other_marker], ncol=2, loc="upper center",
                  bbox_to_anchor=(0.50, -0.25), frameon=False, columnspacing=0.9)
    _figure_title(fig, "F11", "Calibration transfer", "Point per model chip; vertical lines denote medians.")
    _save_figure(fig, out, filename)


# =============================================================================
# F10 · 片选策略和谱指标
# =============================================================================
def figure_interleaving(model, out: Path, *, n: int) -> dict:
    """在同一制造抽样上比较两种片选，返回逐频点、逐策略的频谱量。

    n 是每条相干记录的长度，离散频点按 FS_HZ/n 对齐；out 为写图目录。
    所有结果来自默认行为噪声与 20 位 Python 定点输出。
    """
    frequencies = (0.2e6, 1e6, 3e6)
    plans = (
        ("random 8-of-10", False, COLORS["blue"], "o"),
        ("round-robin 8-of-18", True, COLORS["teal"], "D"),
    )
    rows = {name: {} for name, _, _, _ in plans}
    # 短记录的目标频率可能落在同一个 FFT bin，只计算并绘制一次。
    bins = sorted({max(1, round(n * target / FS_HZ)) for target in frequencies})
    actual = [tone_bin * FS_HZ / n for tone_bin in bins]
    # 每对曲线共用制造抽样和同一份 DEM 状态次序。片选改变有效噪声电容与
    # 各片权重参与情况，两者都是该模型中变更片选后的响应。
    for fin in actual:
        for name, rotation, _, _ in plans:
            raw, voltage = _run(
                model,
                lambda t, f=fin: V_FS * 0.999 * np.sin(2 * np.pi * f * t),
                n, dem=True, uniform_plan=rotation,
            )
            # 输入基波由激励给定，而非从最大的 FFT bin 反推；
            # 最大杂散用扣除基波后的全频段搜索。
            result = metrics.sine_fit_metrics(voltage, FS_HZ, fin)
            rows[name][f"{fin / 1e6:.3f} MHz"] = {
                "fin_hz": fin,
                "sndr_db": float(result["SNDR_dB"]),
                "sfdr_db": float(result["SFDR_dB"]),
                "spur_hz": float(result["spur_Hz"]),
                "analog_overflow_count": int(np.count_nonzero(raw.digital_observation.overflow)),
            }

    fig, axes = plt.subplots(1, 2, figsize=(12.0, 5.35), sharey=True)
    fig.subplots_adjust(left=0.15, right=0.96, bottom=0.24, top=0.91, wspace=0.27)
    y = np.arange(len(actual))
    for ax, metric in zip(axes, ("SFDR", "SNDR"), strict=True):
        values = [
            [row[f"{metric.lower()}_db"] for row in rows[name].values()]
            for name, _, _, _ in plans
        ]
        for yi, (random_value, rotating_value) in enumerate(zip(*values, strict=True)):
            ax.plot(
                [random_value, rotating_value], [yi, yi],
                color=COLORS["grid"], lw=2.6, zorder=1,
            )
        # 纵轴文本给出实际相干频点。标签上下错开，近似相等的点也保留可读数值。
        for index, ((_, _, color, marker), vector) in enumerate(zip(plans, values, strict=True)):
            ax.scatter(vector, y, color=color, marker=marker, zorder=3 + index,
                       s=52, edgecolors=COLORS["white"], linewidths=0.65)
            # 点值是绝对 dB，不以截断轴的柱高暗示提升倍率。
            for yi, value in enumerate(vector):
                ax.annotate(
                    f"{value:.2f}", (value, yi), xytext=(0, 10 if index == 0 else -11),
                    textcoords="offset points", ha="center",
                    va="bottom" if index == 0 else "top", color=color, fontsize=9,
                )
        panel = "(a)" if metric == "SFDR" else "(b)"
        ax.set_title(f"{panel} {metric}", loc="left", pad=6)
        ax.set_xlabel(f"{metric} [dB]")
        ax.grid(axis="x")
        ax.margins(x=0.22)
        ax.tick_params(axis="y", length=0)
    axes[0].set_yticks(y, [f"{value / 1e6:.3f} MHz" for value in actual])
    axes[0].set_ylim(len(actual) - 0.50, -0.50)
    # 图例用实际绘制符号，避免打印时字体缺少几何字符。
    from matplotlib.lines import Line2D
    legend_labels = ("Random: 8 of remaining 10", "Rotating: 8-slice window")
    legend_handles = [Line2D([], [], ls="", marker=marker, color=color, label=label)
                      for (_, _, color, marker), label in zip(plans, legend_labels, strict=True)]
    fig.legend(handles=legend_handles, loc="lower center", bbox_to_anchor=(0.52, 0.035), ncol=2)
    _figure_title(
        fig, "F10", "Slice selection and spectral performance",
        f"{FS_HZ / 1e6:g} MS/s · {n:,} samples per tone · DEM on · same chip"
        f" · mismatch σ = {model.parameters.unit_mismatch_std:.1%}",
    )
    filename = "F10_interleaving.png"
    _save_figure(fig, out, filename)
    return {
        "figure": filename,
        "n": n,
        "seed": model.seed,
        "unit_and_bridge_mismatch_std": model.parameters.unit_mismatch_std,
        "tone_amplitude_v_peak": V_FS * 0.999,
        "results": rows,
    }


# =============================================================================
# F12 · 同笔浮点与定点输出之差
# =============================================================================
def figure_fixed_point(model, out: Path, *, n: int) -> dict:
    """用同一份记录的浮点诊断输出和 20 位定点解码求差。

    n 是每条正弦记录长度，写图到 out；返回峰值、均方根与越限样本数。
    定点分母换算由 _run 传入同一名义物理增益处理。
    """
    tone_bin = max(1, int(round(n * 1e6 / FS_HZ)))
    fin = tone_bin * FS_HZ / n
    # _run 将 physical_gain 传到解码器，使两路共享采样增益口径。
    raw, fixed_voltage = _run(
        model, lambda t: V_FS * 0.999 * np.sin(2 * np.pi * fin * t),
        n, dem=False, uniform_plan=False,
    )
    # 两个结果共用同一份 ADC2 量化和采样误差；差值衡量数值重构，
    # 不能拿来代替对已知模拟输入的误差。半 LSB 是只含最终取整时的基准。
    delta = (fixed_voltage - np.asarray(raw.out)) / LSB20
    abs_delta = np.abs(delta)
    peak = float(abs_delta.max())
    rms = float(np.sqrt(np.mean(delta**2)))
    over_half = int(np.count_nonzero(abs_delta > 0.5))
    analog_overflow = int(np.count_nonzero(raw.digital_observation.overflow))

    fig, (trace, dist) = plt.subplots(1, 2, figsize=(12.0, 5.4))
    fig.subplots_adjust(left=0.11, right=0.96, bottom=0.23, top=0.91, wspace=0.32)
    # 刻度覆盖实测极值和两条参考线，防止轻微超限被固定轴裁掉。
    limit = max(0.58, 1.06 * peak)
    # 左图展示连续起始样本，看密集时间结构；右图和全部指标保留
    # 整条记录，包含可能不在局部窗口内的尾部误差。
    extent = min(n, 256)
    trace.scatter(np.arange(extent), delta[:extent], s=11, color=COLORS["blue"], alpha=0.76)
    dist.hist(delta, bins=np.linspace(-limit, limit, 41), color=COLORS["teal"],
              edgecolor=COLORS["white"], linewidth=0.5)
    trace.set_ylim(-limit, limit)
    dist.set_xlim(-limit, limit)
    # 参考线只对应最终中点量化。上游定点舍入允许数据略越界，
    # 由 over_half 实际计数，不能用参考线补一条结论。
    for edge in (-0.5, 0.5):
        trace.axhline(edge, color=COLORS["muted"], lw=1.1, ls=(0, (4, 3)))
        dist.axvline(edge, color=COLORS["muted"], lw=1.1, ls=(0, (4, 3)))
    trace.set(
        xlabel="Sample index", ylabel="Fixed − float [LSB20]",
        xlim=(-1, extent),
    )
    trace.set_title(f"(a) First {extent} samples", loc="left", pad=6)
    dist.set(xlabel="Fixed − float [LSB20]", ylabel="Samples")
    dist.set_title("(b) Full-record distribution", loc="left", pad=6)
    _figure_title(
        fig, "F12", "Fixed-point reconstruction error",
        f"Python decoder vs. floating-point reconstruction · {fin / 1e6:.3f} MHz"
        f" · {n:,} shared samples · DEM off",
    )
    filename = "F12_fixed_vs_float.png"
    _save_figure(fig, out, filename)
    return {
        "figure": filename,
        "n": n,
        "fin_hz": fin,
        "seed": model.seed,
        "unit_and_bridge_mismatch_std": model.parameters.unit_mismatch_std,
        "rms_lsb20": rms,
        "max_abs_lsb20": peak,
        "signed_range_lsb20": [float(delta.min()), float(delta.max())],
        "outside_half_lsb20_count": over_half,
        "analog_overflow_count": analog_overflow,
        "note": "Same Python record and ADC2 quantization on both outputs. "
                "±0.5 LSB20 is the final quantizer reference.",
    }


# =============================================================================
# M00. 模拟采样边界与数字转换链
# =============================================================================
def figure_system_mechanism(out: Path) -> dict:
    """绘制公开架构与 v21 接口，返回图名、来源和边界说明。

    分两幅展示物理主链与数字依赖。图中的 MUX 和增益块只说明信号由来，
    具体管级开关、时序和 PVT 参数均未由此确定。
    """
    from matplotlib.patches import FancyArrowPatch, Polygon, Rectangle

    fig, (plant, logic) = plt.subplots(
        2, 1, figsize=(7.16, 3.75), layout="constrained",
        gridspec_kw={"height_ratios": (1.15, 1.0)},
    )
    colors = ("#192226", "#265d9b", "#a75c19", "#246d59")
    ink, data, analog, owner = colors
    for ax in (plant, logic):
        ax.set(xlim=(0, 12), ylim=(0, 3.2))
        ax.set_axis_off()

    def wire(ax, points, *, arrow=False, color=ink, ls="-"):
        """信号末端才标箭头，交越在独立图板中表达。"""
        xx, yy = zip(*points, strict=True)
        if not arrow:
            ax.plot(xx, yy, color=color, ls=ls, lw=0.76)
            return
        if len(points) > 2:
            ax.plot(xx[:-1], yy[:-1], color=color, ls=ls, lw=0.76)
        ax.add_patch(FancyArrowPatch(
            points[-2], points[-1], arrowstyle="-|>", mutation_scale=7,
            color=color, ls=ls, lw=0.73, shrinkA=0, shrinkB=0,
        ))

    def text(ax, x, y, value, *, color=ink, ha="center", weight="normal", size=8):
        ax.text(x, y, value, color=color, ha=ha, va="center",
                fontweight=weight, fontsize=size)

    def rectangle(ax, x, y, w, h, value, *, color=ink, fill="white", linewidth=0.78):
        ax.add_patch(Rectangle((x, y), w, h, ec=color, fc=fill, lw=linewidth))
        text(ax, x + w / 2, y + h / 2, value, color=ink)

    def dac(ax, x, y, width, value, *, fill="white"):
        """用带输入尖端的 DAC 符号与方形逻辑框区分。"""
        points = [(x, y), (x + .29, y + .24), (x + width, y + .24),
                  (x + width, y - .24), (x + .29, y - .24)]
        ax.add_patch(Polygon(points, fc=fill, ec=ink, lw=.78))
        text(ax, x + width * .56, y, value)

    def amplifier(ax, x, y, value):
        """放大或判决符号只表达功能。增益是合同给出的名义值。"""
        ax.add_patch(Polygon([(x, y-.32), (x, y+.32), (x+.84, y)],
                             ec=ink, fc="white", lw=.8))
        text(ax, x + .25, y, value, size=7.2)

    text(plant, .03, 2.98, "(a) Analog front end", ha="left", weight="bold")
    text(plant, .12, 1.83, r"$V_{IN}$", ha="left")
    wire(plant, [(.67, 1.83), (1.23, 1.83), (1.23, 2.45), (1.61, 2.45)])
    wire(plant, [(1.23, 1.83), (1.23, .84), (1.62, .84)])
    plant.scatter([1.23], [1.83], s=7, color=ink, zorder=3)
    # 开关为概念符号；并行采集组沿公共孔径换手。原文还有 Flash 共享等
    # 电路细节，数字工程仅接收外部已决事件。
    for yy in (.84, 2.45):
        plant.scatter([1.65, 2.08], [yy, yy], s=8, fc="white", ec=ink, lw=.65, zorder=4)
        wire(plant, [(1.66, yy+.03), (2.04, yy+.17)])
    dac(plant, 2.20, 2.45, 1.42, "SADC DAC")
    wire(plant, [(2.08, 2.45), (2.20, 2.45)])
    wire(plant, [(3.62, 2.45), (4.16, 2.45)])
    amplifier(plant, 4.16, 2.45, "CMP")
    rectangle(plant, 5.37, 2.19, 1.25, .52, "Flash / SAR")
    wire(plant, [(5.00, 2.45), (5.37, 2.45)], arrow=True)
    wire(plant, [(6.62, 2.45), (7.65, 2.45)], arrow=True, color=data)
    text(plant, 7.81, 2.45, "Resolved decision events", ha="left", color=data)
    wire(plant, [(5.64, 2.19), (5.64, 1.95), (2.81, 1.95), (2.81, 2.21)], arrow=True)

    plant.add_patch(Rectangle((2.15, .11), 3.72, 1.62, ec="#606060", fc="none",
                              lw=.7, ls=(0, (3, 2))))
    text(plant, 2.26, 1.53, "RDAC pool (18 slices)", ha="left", weight="bold")
    dac(plant, 2.45, 1.11, 2.35, "8 acquire", fill="#e8f2e3")
    dac(plant, 2.45, .57, 2.35, "8 convert", fill="#f6e9d9")
    text(plant, 5.27, 1.12, "2 idle", color="#4c4c4c", size=7.6)
    wire(plant, [(2.08, .84), (2.26, .84), (2.26, 1.11), (2.45, 1.11)])
    wire(plant, [(4.8, .57), (6.14, .57), (6.14, .85), (6.95, .85)], arrow=True)
    text(plant, 6.29, 1.17, r"$x-D(k)$", color=analog)
    amplifier(plant, 6.95, .85, "RA")
    text(plant, 7.36, .33, r"$G=32$ (nom.)", size=7.5)
    wire(plant, [(7.79, .85), (8.54, .85)], arrow=True)
    rectangle(plant, 8.54, .58, 1.25, .54, "ADC2")
    wire(plant, [(9.79, .85), (10.62, .85)], arrow=True, color=owner)
    text(plant, 10.05, 1.18, "Raw word", ha="left", color=owner)

    text(logic, .03, 2.98, "(b) v21 digital data flow", ha="left", weight="bold")
    # 标出内部 D2 接收的事件、D3/4 导出的实际掩码和 D6 配对结果。
    # 权重由主机装载；图上只画输入向重构的接口，RAM 提交细节见 M03。
    rectangle(logic, .78, 1.63, 1.61, .58, "D2 Follower")
    rectangle(logic, 3.22, 1.63, 1.64, .58, "D3 DEM / bridge")
    rectangle(logic, .78, .54, 1.61, .58, "D4 Split dither")
    rectangle(logic, 6.23, .54, 1.84, .58, "D6 Capture")
    rectangle(logic, 9.28, 1.63, 1.60, .58, "D5 Reconstruct")
    wire(logic, [(.17, 1.92), (.78, 1.92)], arrow=True, color=data)
    text(logic, .19, 2.23, "Events", color=data, ha="left", size=7.7)
    wire(logic, [(2.39, 1.92), (3.22, 1.92)], arrow=True, color=data)
    text(logic, 2.78, 2.14, r"$k_e$", color=data)
    wire(logic, [(1.54, 1.12), (1.54, 1.63)], arrow=True, color=data)
    text(logic, 1.83, 1.34, r"$U$", color=data)
    wire(logic, [(4.86, 1.92), (9.28, 1.92)], arrow=True)
    text(logic, 6.64, 2.16, "Final post-mask + weight sums", size=7.7)
    wire(logic, [(2.39, .84), (2.98, .84)], arrow=True, color=data)
    text(logic, 3.08, .84, r"Pre-mask to RDAC / $W_p$", ha="left", color=data, size=7.6)
    wire(logic, [(5.5, .83), (6.23, .83)], arrow=True, color=owner)
    text(logic, 5.49, .54, "Raw", ha="left", color=owner)
    wire(logic, [(8.07, .83), (10.08, .83), (10.08, 1.63)], arrow=True, color=owner)
    text(logic, 9.04, .62, "Same tag", color=owner, size=7.7)
    wire(logic, [(10.88, 1.92), (11.73, 1.92)], arrow=True)
    text(logic, 11.55, 2.16, r"$D[19{:}0]$", size=7.7)
    text(logic, 4.15, 2.58, "D1/D6: allocate ownership and freeze sample descriptor",
         color=owner, size=7.8)
    _figure_title(fig, "M00", "Front end and v21 data flow",
                  "Functional schematic. Analog circuit internals and handshake timing are abstracted.")
    _save_figure(fig, out, "M00_signal_flow.png")
    return {
        "figure": "M00_signal_flow.png",
        "evidence_type": "architecture drawing from v21 RTL and declared analog boundary",
        "style_reference": "ISSCC 2024 Fig. 9.8.1 and Fig. 9.8.3",
        "rtl_source": "rtl/sar_core/adc_digital_ip_v21.sv",
    }


# =============================================================================
# M01. 技术机理与算例（论文图面）
# =============================================================================
def figure_timing_mechanism(out: Path) -> dict:
    """在双栏宽度绘制 v21 接口拓扑、普通路径时序及一笔前缀示例。

    图 (a) 区分独立量化和残差 DAC，图 (b) 表示正常推进时的因果次序。
    片号取 Python 调度参考的前两次推进。时序图以逻辑间隔为单位，
    不使用时钟个数、模拟建立时间或绝对纳秒刻度。
    """
    scheduler = Scheduler18Rtl()
    intervals = [scheduler.step() for _ in range(2)]
    if intervals[0]["acq"] != intervals[1]["conv"]:
        raise RuntimeError("M01 needs the same slice group in adjacent ACQ and CONV intervals")
    coarse = 365
    flash = coarse >> 6
    decisions = [(coarse >> bit) & 1 for bit in range(5, -1, -1)]
    prefix = [flash << 6]
    for bit, keep in zip(range(5, -1, -1), decisions, strict=True):
        prefix.append(prefix[-1] + (keep << bit))

    # 180 mm 画布按实际合刊宽度设置字级。状态用符号和薄线保证灰度
    # 印刷仍可读，颜色只标注物理相位，不承担连线方向或信号类型。
    fig = plt.figure(figsize=(180 / 25.4, 74 / 25.4), facecolor="white")
    left = fig.add_axes([0.022, 0.07, 0.443, 0.86])
    right = fig.add_axes([0.504, 0.07, 0.472, 0.86])
    for ax in (left, right):
        ax.set(xlim=(0, 9), ylim=(0, 7))
        ax.set_axis_off()
    ink = "#151515"
    edge_color = "#a0441c"

    def key(ax, x, y, label, *, size=7.3, ha="center", weight="normal", color=ink):
        """文字按实际印刷点数指定，并沿信号中心对齐。"""
        ax.text(x, y, label, fontsize=size, ha=ha, va="center", color=color,
                fontweight=weight, linespacing=1.15)

    def wire(ax, points, *, style="solid", width=0.75, color=ink):
        """正交连线。虚线表示时序关联或采样边沿，实线表示信号路径。"""
        x, y = zip(*points, strict=True)
        ax.plot(x, y, ls=style, lw=width, solid_capstyle="butt", color=color)

    def arrow(ax, start, end, *, color=ink):
        """只在信号目标端标记方向。"""
        ax.annotate("", xy=end, xytext=start, arrowprops={
            "arrowstyle": "-|>", "color": color, "lw": 0.75,
            "shrinkA": 0, "shrinkB": 0, "mutation_scale": 7,
        })

    def box(ax, x, y, w, h, label):
        """功能节点以无填充薄边和短标签表示。"""
        ax.add_patch(matplotlib.patches.Rectangle(
            (x, y), w, h, facecolor="white", edgecolor=ink, lw=0.75
        ))
        key(ax, x + w / 2, y + h / 2, label)

    # =========================================================================
    # a. 独立量化、活动片组和残差通路
    # =========================================================================
    key(left, 0.02, 6.85, "(a)  Signal and control paths", size=8.3, ha="left", weight="bold")
    key(left, 0.04, 3.63, r"$V_\mathrm{IN}$", size=8.1, ha="left")
    wire(left, [(0.10, 3.24), (1.16, 3.24), (1.16, 5.40)])
    left.add_patch(matplotlib.patches.Circle((1.16, 3.24), 0.058, color=ink))
    arrow(left, (1.16, 5.40), (1.61, 5.40))
    arrow(left, (1.16, 3.24), (1.61, 3.24))
    box(left, 1.61, 4.89, 2.36, 1.02, "SADC pair\nFlash / SAR")
    box(left, 5.89, 4.89, 2.82, 1.02, "D2 prefix + D3 mask\napply to CONV IDs")
    arrow(left, (3.97, 5.40), (5.89, 5.40))
    key(left, 4.93, 5.72, "resolved bits", size=6.7)
    wire(left, [(7.30, 4.89), (7.30, 4.25), (3.49, 4.25)])
    arrow(left, (3.49, 4.25), (3.49, 3.94))
    key(left, 5.47, 4.49, r"post-mask for $k_e$", size=6.8)

    box(left, 1.61, 2.55, 2.95, 1.39, "18-slice RDAC\n8 ACQ / 8 CONV\n2 idle")
    # RA 和 ADC2 是外部模拟节点。三角形给出增益方向，ADC2 的
    # 压缩端口轮廓表示采样量化；两者无具体电路或物理增益假设。
    arrow(left, (4.56, 3.24), (5.16, 3.24))
    left.add_patch(matplotlib.patches.Polygon(
        [(5.16, 2.70), (5.16, 3.78), (6.15, 3.24)],
        closed=True, facecolor="white", edgecolor=ink, lw=0.75
    ))
    key(left, 5.49, 3.24, "RA", size=7.0)
    wire(left, [(6.15, 3.24), (6.51, 3.24)])
    left.add_patch(matplotlib.patches.Polygon(
        [(6.51, 3.24), (6.87, 3.75), (8.20, 3.75), (8.20, 2.73), (6.87, 2.73)],
        closed=True, facecolor="white", edgecolor=ink, lw=0.75
    ))
    key(left, 7.48, 3.24, "ADC2", size=7.5)
    arrow(left, (8.2, 3.24), (8.84, 3.24))

    box(left, 1.61, 0.64, 2.95, 0.85, "D1 slice scheduler")
    arrow(left, (3.09, 1.49), (3.09, 2.55))
    key(left, 3.24, 2.03, "slice IDs", size=6.8, ha="left")
    key(left, 6.48, 1.82, "Capture residue at the\ncommon aperture", size=7.0)
    wire(left, [(6.48, 2.19), (7.48, 2.58)], style=(0, (2, 2)), color=edge_color)

    # =========================================================================
    # b. 采集、转换和 RA 交错的逻辑时序
    # =========================================================================
    key(right, 0.02, 6.85, "(b)  Interleaving and resolved-code example",
        size=8.3, ha="left", weight="bold")
    start, aperture, end = 1.33, 5.0, 8.86
    # 同一条竖线说明公共捕获，不把 drawn CONV/RA 宽度解释为定量时序。
    wire(right, [(aperture, 3.23), (aperture, 6.12)],
         style=(0, (2.2, 2.0)), color=edge_color, width=0.7)
    key(right, aperture, 6.36, "sample B / capture A residue", size=7.0, color=edge_color)
    arrow(right, (aperture, 6.13), (aperture, 5.80), color=edge_color)
    key(right, 0.02, 5.91, "CLK", ha="left")
    wire(right, [(0.95, 5.76), (start, 5.76), (start, 6.03), (2.62, 6.03),
                 (2.62, 5.76), (aperture, 5.76), (aperture, 6.03),
                 (6.49, 6.03), (6.49, 5.76), (end, 5.76)])

    def phase(x0, x1, y, label, fill):
        """斜边表明相位切换，保持窗宽可读且不暗示实际占空比。"""
        d = 0.095
        right.add_patch(matplotlib.patches.Polygon(
            [(x0, y), (x0+d, y+0.23), (x1-d, y+0.23), (x1, y),
             (x1-d, y-0.23), (x0+d, y-0.23)], closed=True,
            facecolor=fill, edgecolor=ink, lw=0.66, zorder=3
        ))
        key(right, (x0+x1) / 2, y, label, size=7.0)

    pale_acq, pale_conv, pale_ra = "#e7eedc", "#f5e6d8", "#dce9ef"
    for y, label in ((5.12, "Sample A"), (4.43, "Sample B"), (3.74, "Sample C")):
        key(right, 0.02, y, label, size=7.0, ha="left")
    phase(start, 3.23, 5.12, "CONV", pale_conv)
    phase(3.23, aperture, 5.12, "RA", pale_ra)
    wire(right, [(aperture, 5.12), (end, 5.12)], style=(0, (2, 2)), color="#646464")
    phase(start, aperture, 4.43, "ACQ", pale_acq)
    phase(aperture, 7.08, 4.43, "CONV", pale_conv)
    phase(7.08, end, 4.43, "RA", pale_ra)
    phase(aperture, end, 3.74, "ACQ", pale_acq)
    key(right, start, 3.06, r"Slice carryover for B   $\mathrm{CONV}_{n+1}=\mathrm{ACQ}_{n}$",
        size=7.15, ha="left")

    # 具体普通区事件与相位图错开。保留重复前缀，免得把收到一个
    # 有效的零判决误判为该事务跳拍。guard 分支由独立测试覆盖。
    y_rows = (2.36, 1.77, 1.16)
    for y, label in zip(y_rows, ("Event", "Decision", r"$c_e$"), strict=True):
        key(right, 0.02, y, label, ha="left", size=7.1)
    for i, (decision, value) in enumerate(zip([flash, *decisions], prefix, strict=True)):
        x = 1.40 + 1.18 * i
        key(right, x, y_rows[0], "Flash" if not i else f"SAR {6-i}", size=7.05)
        key(right, x, y_rows[1], str(decision), size=7.15)
        key(right, x, y_rows[2], str(value), size=7.45, weight="bold")
    for y in (2.04, 0.89):
        wire(right, [(0.02, y), (end, y)], color="#888888", width=0.48)
    key(right, 0.02, 0.32, r"Ordinary path ($F<7$), $k_e=c_e+U$, example $U=0$.",
        size=7.2, ha="left")
    name = "M01_interleaving_follower.png"
    _save_figure(fig, out, name)
    return {
        "figure": name,
        "evidence_level": "v21 contract diagram with Python scheduler and arithmetic example",
        "scheduler_seed": f"{scheduler.seed:#010x}",
        "scheduler_group_rows": intervals,
        "example_coarse_code": coarse,
        "example_upper_code": 0,
        "flash": flash,
        "sar_decisions_msb_first": decisions,
        "resolved_prefixes": prefix,
    }


# =============================================================================
# M02. 技术机理与算例（论文图面）
# =============================================================================
def figure_dem_dither_mechanism(out: Path) -> dict:
    """由 v2.1 的 Python 开关参考模型绘制一笔转换的原理图。

    用相同的字、码、水平相位和垂直相位贯穿三幅子图。电容采用合同的名义
    权重，Cu 为电容单位，扰动 Ld/U/T 为输入折算的粗码步进数。该示例用给定
    的 SADC 判决 c 来演示数字和采样开关联动，未计算比较器的模拟判决过程。
    """
    from matplotlib.collections import PatchCollection
    from matplotlib.patches import FancyArrowPatch, Rectangle

    from adi_model.dem_dither import bridge_swap, dither_split, post_mask, pre

    # 选 155 的目的在于保留主阵列的横/纵分段，同时保证目标半单元与子 4Cu
    # 的 bit 不同，打开 coin 后可看见九个真实开关位改变。
    word, coarse = 53, 151
    h, v, half = 2, 3, 0
    upper, lower, total, fraction = dither_split(word)
    k = coarse + upper
    horizontal, vertical, low = k >> 6, (k >> 3) & 7, k & 7
    reserved = (7 + h) % 8
    target = 2 * reserved + half
    base = np.asarray(post_mask(k, h, v), dtype=int)
    bridged = np.asarray(bridge_swap(base.tolist(), h, v, half << 3, 1), dtype=int)
    pre_bits = np.asarray(pre(word, v)[0], dtype=int)
    physical_sub = np.array([4, 2, 1, 1])
    # 8×20 post 中前十六项每项 4Cu，后四项是过桥的等效权重。先按 Cu/8
    # 精确比较整数，再在面板给出 Cu。只数 bit=1 会误判本例的桥接守恒。
    weights_per_eighth = np.r_[np.full(16, 32), physical_sub]
    cap_before = int((base * weights_per_eighth).sum())
    cap_after = int((bridged * weights_per_eighth).sum())
    changed = np.argwhere(base != bridged)
    pre_slot_values = pre_bits @ physical_sub
    if cap_before != 8 * k or cap_after != cap_before or len(changed) != 9:
        raise AssertionError("M02 example no longer matches the v2.1 mask and bridge contract")

    # 7.16 in 是出图的双栏版心宽度。字号按原始印刷尺寸选择，面板标题不随
    # 栅格画布放大。面板使用 Arial，公式使用 STIX 数学字形。
    fig = plt.figure(figsize=(7.16, 4.22), facecolor="white")
    grid = fig.add_gridspec(
        2, 2, height_ratios=(0.95, 1),
        left=0.067, right=0.984, bottom=0.154, top=0.945, hspace=0.26, wspace=0.175,
    )
    split = fig.add_subplot(grid[0, :], facecolor="white")
    before_ax = fig.add_subplot(grid[1, 0], facecolor="white")
    after_ax = fig.add_subplot(grid[1, 1], facecolor="white")
    ink = "#222222"
    gray = "#838383"
    line = "#b5b5b5"
    blue = "#254e70"
    teal = "#215b50"
    orange = "#995022"
    violet = "#704971"
    serif = {"fontfamily": "Arial", "math_fontfamily": "stix", "color": ink}

    # -------------------------------------------------------------------------
    # a. 跟踪同一个扰动字和粗码
    # -------------------------------------------------------------------------
    split.set(xlim=(0, 1), ylim=(0, 1))
    split.set_axis_off()
    split.text(0, 1.01, "(a)  Split dither and form the RDAC command", fontsize=8.5,
               va="bottom", weight="bold", **serif)

    def arrow(start, end, *, color=ink):
        split.add_patch(FancyArrowPatch(
            start, end, arrowstyle="-|>", mutation_scale=6.6,
            color=color, linewidth=0.62, shrinkA=0, shrinkB=0,
        ))

    def text_at(x, y, text, *, size=7.7, color=ink, ha="left", weight="normal"):
        split.text(x, y, text, fontsize=size, color=color, ha=ha, va="center",
                   weight=weight, fontfamily="Arial", math_fontfamily="stix")

    def block(x, y, width, height, title, body, *, accent):
        split.add_patch(Rectangle(
            (x, y), width, height, facecolor="white", edgecolor=accent, linewidth=0.66,
        ))
        # 标题与物理量分行，保留印刷版的引线与文字间距。
        text_at(x + 0.011, y + height * 0.79, title, size=7.7, weight="bold", color=accent)
        text_at(x + width / 2, y + height * 0.35, body, size=7.75, ha="center")

    # MSB 在左。D6/D4 蓝框为 U，其他四位绿框为 Ld，避免将 D5 算入 U。
    text_at(0.0, 0.85, f"Dither word  $w={word}$", weight="bold")
    for i, bit_index in enumerate(range(5, -1, -1)):
        x = 0.0 + 0.031 * i
        color = blue if bit_index in (5, 3) else teal
        split.add_patch(Rectangle((x, 0.51), 0.028, 0.27, fill=False,
                                  edgecolor=color, linewidth=0.68))
        text_at(x + 0.014, 0.715, rf"$D_{{{bit_index + 1}}}$", size=6.5, ha="center", color=color)
        text_at(x + 0.014, 0.58, str((word >> bit_index) & 1), size=7.9, ha="center")
    text_at(0.0, 0.421, "Upper", size=6.9, color=blue)
    text_at(0.080, 0.421, "Lower", size=6.9, color=teal)
    arrow((0.186, 0.65), (0.222, 0.65))

    # SADC 只使用 Ld。RDAC 采样叠加 T，随后用解析粗码 c 形成 post 命令。
    # k=c+U 的箭头表示当前示例的最终命令，逐事件时用已决前缀代替 c。
    split.add_patch(Rectangle((0.222, 0.29), 0.243, 0.63, fill=False,
                              edgecolor=gray, linewidth=0.62))
    text_at(0.233, 0.822, rf"$U=4D_6+D_4={upper}$", size=8.1, color=blue)
    text_at(0.233, 0.682, rf"$f=4D_3+2D_2+D_1={fraction}$", size=7.4, color=teal)
    text_at(0.233, 0.541, rf"$L_d=2D_5+f/8={lower:g}$", size=7.7, color=teal)
    text_at(0.233, 0.4, rf"$T=U+L_d={total:g}$", size=8.0)
    arrow((0.465, 0.755), (0.503, 0.755), color=teal)
    arrow((0.465, 0.35), (0.503, 0.35), color=blue)
    block(0.503, 0.60, 0.208, 0.32, "SADC · resolve",
          rf"$x+L_d\Delta\ \rightarrow\ c={coarse}$", accent=teal)
    arrow((0.606, 0.599), (0.606, 0.507))
    text_at(0.621, 0.554, r"$c$", size=7)
    block(0.503, 0.175, 0.208, 0.331, "RDAC · follow",
          r"$x+T\Delta$" "\n" rf"$k=c+U={k}\ \rightarrow$ (b)", accent=blue)

    # ADC2 量化后的等效残差写作 yhat；此式选择名义 alpha=1/b=0。校准实现
    # 使用实际 Wp 做加权扣回，和 −TΔ 是同一贡献的两种表示（不能叠加）。
    arrow((0.711, 0.336), (0.746, 0.336))
    block(0.746, 0.175, 0.253, 0.331, "Digital reconstruction",
          r"$\hat{x}=D_R(k)+\hat{y}/G-T\Delta$", accent=violet)
    text_at(0.754, 0.761, r"$\hat{y}$ is the quantized ADC2 residue", size=7.25)
    text_at(0.754, 0.656, r"Nominally $-6W_p/G=-T\Delta$", size=7.25)
    # 顺序与真实逻辑槽索引一致。5 个额外单位分给从 v=3 起的五个槽，
    # 其余三槽保留 J=6。格内值是每片子阵列等效 4/2/1/1 权重的和。
    text_at(0, 0.08, r"RDAC preset, slots $0\ldots7$", size=7.2)
    for slot, value in enumerate(pre_slot_values):
        x = 0.189 + 0.026 * slot
        split.add_patch(Rectangle((x, 0.016), 0.024, 0.127, facecolor="white",
                                  edgecolor=(blue if value > pre_slot_values.min() else line),
                                  linewidth=0.63))
        text_at(x + 0.012, 0.08, str(value), size=7.1, ha="center")
    text_at(0.414, 0.08, r"Mean $=T$;  $\Delta=6/512\,\mathrm{V}$", size=7.1)

    # -------------------------------------------------------------------------
    # b–c. 对照同一转换槽和主/子阵列的位置
    # -------------------------------------------------------------------------
    def mask_panel(ax, mask, heading):
        # 全部采用矢量矩形。浅填充标示接到选定参考端的单元，可由灰度印刷辨认。
        cells = [Rectangle((j - 0.5, slot - 0.5), 1, 1)
                 for slot in range(8) for j in range(20)]
        fills = ["#c7d8e7" if bit else "white" for row in mask for bit in row]
        ax.add_collection(PatchCollection(
            cells, facecolors=fills, edgecolors=[line], linewidths=0.39,
        ))
        ax.add_patch(Rectangle(
            (2 * reserved - 0.5, -0.52), 2, 8.04, fill=False, edgecolor=orange, linewidth=0.85,
        ))
        ax.axvline(15.5, color=ink, linewidth=0.6)
        # 虚线框只标识发生翻转的位，两图位置一致，颜色不是区分交换态的唯一线索。
        for slot, j in changed:
            ax.add_patch(Rectangle(
                (j - 0.38, slot - 0.37), 0.76, 0.74, fill=False,
                edgecolor=violet, linewidth=0.8, linestyle=(0, (2.5, 1.4)),
            ))
        ax.set(xlim=(-0.53, 19.53), ylim=(7.58, -0.58))
        xtick_locs = [i + 0.5 for i in range(0, 16, 2)] + [16, 17, 18, 19]
        ax.set_xticks(xtick_locs, [str(u) for u in range(8)] + ["4", "2", "1", "bal"])
        ax.set_yticks(range(8))
        ax.tick_params(length=0, pad=2.4, labelcolor=ink, labelsize=7.1)
        for label in ax.get_xticklabels() + ax.get_yticklabels():
            label.set_fontfamily("Arial")
        ax.set_title(heading, loc="left", pad=4, fontsize=8.5, fontweight="bold", **serif)
        ax.spines[:].set_visible(False)

    mask_panel(before_ax, base, "(b)  Base post-mask, coin = 0")
    mask_panel(after_ax, bridged, "(c)  Bridge exchange, coin = 1")
    before_ax.set_ylabel("Conversion slot", fontsize=7.8, labelpad=2.2, **serif)
    # 相同坐标从 (b) 到 (c)。矩阵边框区分保留列，右四列标的是物理子权重，
    # 总量则要除以桥比的分母 8，二者不可直接相加。
    axis_caption = r"Main macro column $u$    |    Physical sub weight ($C_u$)"
    for ax in (before_ax, after_ax):
        ax.set_xlabel(axis_caption, fontsize=7.6, labelpad=2, **serif)
    before_ax.text(
        0, -0.21,
        rf"$k=64H+8V+L={k}$;  $(H,V,L)=({horizontal},{vertical},{low})$" "\n"
        rf"$h={h}$, $v={v}$;  $r=(7+h)\ \mathrm{{mod}}\ 8={reserved}$"
        " (outlined column)",
        transform=before_ax.transAxes, fontsize=7.35, va="top", **serif,
    )
    after_ax.text(
        0, -0.21,
        r"One main half off ($-4C_u$), eight sub bits on ($+8\cdot4C_u/8$)." "\n"
        rf"Selected capacitance: $ {cap_before/8:g}C_u$ in (b) and (c).",
        transform=after_ax.transAxes, fontsize=7.35, va="top", **serif,
    )
    # 图内只留物理变量和读图所需的编码；来源和适用范围由正文图注交代。
    name = "M02_dem_dither_mechanism.png"
    _save_figure(fig, out, name)
    return {
        "figure": name,
        "binding": "v2.1 nominal engineering contract, ordinary code",
        "word": word,
        "coarse_code": coarse,
        "rdac_code": k,
        "upper_lsb": upper,
        "lower_lsb": lower,
        "total_lsb": total,
        "preset_extra_slots": fraction,
        "preset_values_by_slot": pre_slot_values.tolist(),
        "h_phase": h,
        "v_phase": v,
        "half": half,
        "reserved_macro_column": reserved,
        "bridge_main_slot_cell": [v, target],
        "bridge_changed_bits": changed.tolist(),
        "nominal_selected_cu_before": cap_before / 8,
        "nominal_selected_cu_after": cap_after / 8,
    }


# =============================================================================
# M03. 技术机理与算例（论文图面）
# =============================================================================
def figure_reconstruction_mechanism(out: Path) -> dict:
    """画约 180 mm 双栏宽的 v21 机制图，输出图文件和名义算例。

    坐标表达依赖关系，事件位置无时间单位。宏 y 保留 V，Y 和 b 为
    Q24 整数，G/Wm/Wp 为 Q30 整数。名义算例用 M02 的掩码，不运行 EDA。
    """
    from fractions import Fraction
    from matplotlib.patches import FancyArrowPatch, Rectangle
    from tools.recon_oracle import oracle

    # =========================================================================
    # 1. 串接 M02 名义算例
    # =========================================================================
    # Lower 扰动后位于 c=151 的粗码箱中点。桥接在名义模型中保持 Wm，
    # 取有效权重 G=32、Wm=k/16、Wp=T/16；用分数避免量化边界误差。
    c, k, word = 151, 155, 53
    lower, total = Fraction(21, 8), Fraction(53, 8)
    vin = -3 + (c + Fraction(1, 2) - lower) * Fraction(6, 512)
    gain, post, pre = 32, Fraction(k, 16), total / 16
    resid = gain * vin + 3 * gain - 6 * post + 6 * pre
    raw = int((resid + Fraction(3, 16)) * Fraction(8192 * 4, 3))
    q30 = 1 << 30
    fixed = oracle(raw, 0, q30, gain * q30, int(post * q30), int(pre * q30))
    filename = "M03_capture_reconstruction.png"
    blue = "#20597b"
    ink = "#151515"

    # ISSCC Fig. 9.8.1–3 用直角框/细线区分物理边界。这里固定纸面尺寸，
    # 避免在手稿中缩图以后，时序标签和公式的字号一起掉到不可读的范围。
    with plt.rc_context({
        "figure.facecolor": "white", "savefig.facecolor": "white", "text.color": ink,
        "font.family": "sans-serif", "font.sans-serif": ["Arial", "DejaVu Sans"],
        "mathtext.fontset": "stix", "font.size": 8.5,
    }):
        fig = plt.figure(figsize=(7.12, 3.96), facecolor="white")
        control = fig.add_axes((0.016, 0.560, 0.968, 0.415))
        calc = fig.add_axes((0.016, 0.022, 0.968, 0.510))
        for ax in (control, calc):
            ax.set(xlim=(0, 1), ylim=(0, 1))
            ax.set_axis_off()

        def arrow(ax, start, end, color=ink):
            """箭头指向先决项的消费者；轴坐标没有 ns 或时钟刻度。"""
            ax.add_patch(FancyArrowPatch(
                start, end, arrowstyle="-|>", mutation_scale=6,
                color=color, lw=0.65, shrinkA=0, shrinkB=0,
            ))

        # =====================================================================
        # 2. 捕获先于返回，所有权跨流水保留
        # =====================================================================
        control.text(0.010, 0.977, r"(a)   ADC2 capture and owner hand-off",
                     fontsize=9.2, fontweight="bold", va="top")
        positions = [0.127, 0.376, 0.625, 0.874]
        width = 0.208
        steps = (
            "Latch final\nRDAC mask",
            "Arm ADC2\nwhen sums valid",
            "Capture A\ntag, epoch, sums",
            "Raw valid & ready\nrecover owner A",
        )
        for x, text in zip(positions, steps, strict=True):
            control.add_patch(Rectangle((x - width / 2, 0.610), width, 0.241,
                                        ec=ink, fc="none", lw=0.65))
            control.text(x, 0.731, text, ha="center", va="center", linespacing=0.97)
        for left, right in zip(positions[:-1], positions[1:], strict=True):
            arrow(control, (left + width / 2, 0.731), (right - width / 2, 0.731))

        # 虚线按顶层接口切分：arm 属于数字输出，captured/raw 来自宏。
        # hold 决定片组释放，raw 只决定重构受理，图中不连这两条路径。
        control.plot((0.01, 0.99), (0.492, 0.492), color="#606060",
                     lw=0.55, ls=(0, (3.3, 2.5)))
        control.text(0.024, 0.554, "Digital", fontsize=7.6, va="center")
        control.text(0.024, 0.429, "RA / ADC2", fontsize=7.6, va="center")
        control.text(0.024, 0.278, "Nominal residue", fontsize=8, va="center")
        control.text(0.024, 0.185, r"$y=Gx+3G-6W_m+6W_p+b$",
                     fontsize=8.3, va="center")
        for x, label in zip(positions[1:], ("arm", "captured", "raw[12:0], range"), strict=True):
            control.text(x, 0.263, label, ha="center", va="center", color=blue)
        arrow(control, (positions[1], 0.606), (positions[1], 0.333), blue)
        arrow(control, (positions[2], 0.333), (positions[2], 0.606), blue)
        arrow(control, (positions[3], 0.333), (positions[3], 0.606), blue)
        arrow(control, (0.428, 0.263), (0.556, 0.263))
        arrow(control, (0.694, 0.263), (0.755, 0.263))
        control.text(
            0.022, 0.032, "Same edge: raw A uses the previous snapshot; capture B installs the next one.",
            fontsize=8.4, va="center",
        )

        # =====================================================================
        # 3. 同笔掩码权重与 ADC2 码共同恢复输入
        # =====================================================================
        calc.text(0.010, 0.967, "(b)   Fixed-point reconstruction", fontsize=9.2,
                  fontweight="bold", va="top")
        calc.text(0.024, 0.835, r"13-bit ADC2 window $[-0.1875,\,+0.5625)$ V",
                  fontsize=8.4, va="top")
        calc.text(0.024, 0.735, r"Bin center $Y=-3145728+1536r+768$ (Q24)",
                  fontsize=8.4, va="top")
        calc.text(0.533, 0.835, r"$G$ all main; $W_m$ final post; $W_p$ sampled pre",
                  fontsize=8.4, va="top")
        calc.text(0.533, 0.735, "Q30 weights indexed by physical slice", fontsize=8.4, va="top")

        # N 在 Q30 权重口径，64 将 Q24 的 Z 提升六位。I 的倒数保持与
        # N 同笔后才相乘；clip20 只约束正常数据路径的输出码。
        equations = (
            ("Offset / gain", r"$Z=\mathrm{RND}((Y-b)2^{30},\alpha_q)$"),
            ("Undo switching", r"$N=64Z+6W_m-3G-6W_p$"),
            (r"Normalize by $G$", r"$I=\mathrm{RND}(2^{60},G)$" + "\n"
             + r"$X=\lfloor NI/2^{36}\rfloor$"),
            ("Output code", r"$q=\mathrm{RND}(X,96)$" + "\n"
             + r"$D=\mathrm{clip}_{20}(q+2^{19})$"),
        )
        for x, (title, equations_) in zip(positions, equations, strict=True):
            calc.add_patch(Rectangle((x - width / 2, 0.303), width, 0.302,
                                     ec=ink, fc="none", lw=0.65))
            calc.text(x, 0.535, title, ha="center", va="center",
                      fontsize=8.25, fontweight="bold")
            calc.text(x, 0.408, equations_, ha="center", va="center",
                      linespacing=1.08, fontsize=8.3)
        for left, right in zip(positions[:-1], positions[1:], strict=True):
            arrow(calc, (left + width / 2, 0.474), (right - width / 2, 0.474))
        arrow(calc, (positions[0], 0.670), (positions[0], 0.609), blue)
        calc.plot([positions[1], positions[2]], [0.652, 0.652], color=blue, lw=0.55)
        arrow(calc, (positions[1], 0.652), (positions[1], 0.609), blue)
        arrow(calc, (positions[2], 0.675), (positions[2], 0.609), blue)

        # 负 X 的示例来自数学 oracle。实际例程的 err3_q 限制由
        # STATUS.md 记录，这里只说明正确重构应得到哪个码。
        calc.text(
            0.024, 0.199,
            r"M02 example: $w=53$, $k=155$, $G=32$, $W_m=155/16$, $W_p=6.625/16$; $\alpha_q=2^{30}$, $b=0$.",
            fontsize=8.3, va="center",
        )
        calc.text(
            0.024, 0.100, r"$x=" + f"{float(vin):.6f}" + r"$ V"
            + r" $\longrightarrow$ $y=$" + f"{float(resid) * 1000:.1f} mV"
            + r" $\longrightarrow$ " + f"raw {raw}"
            + r" $\longrightarrow$ " + f"{fixed['code']:,} (0x{fixed['code']:05X})",
            color=blue, fontsize=8.7, va="center",
        )
        calc.text(0.024, 0.002, "RND: round to nearest, with ties away from zero.",
                  fontsize=7.7, va="bottom")
        _save_figure(fig, out, filename)

    return {
        "figure": filename,
        "evidence": "v21 signal connections and nominal integer reference calculation",
        "sources": [
            "rtl/sar_core/adc_digital_ip_v21.sv", "rtl/sar_core/adc2_capture.sv",
            "rtl/sar_core/wsum_v21.sv", "rtl/sar_core/recon_v21.sv",
            "docs/v21/PHYSICAL_INTERFACE.md#8", "tools/recon_oracle.py", "docs/STATUS.md",
        ],
        "adc2_bits": 13, "adc2_input_window_v": [-0.1875, 0.5625],
        "worked_example": {
            "word": word, "sadc_code": c, "rdac_command": k,
            "input_v": float(vin), "residue_v": float(resid), "raw": raw,
            "G": gain, "Wm": float(post), "Wp": float(pre), "X_q24": fixed["X"],
            "code": fixed["code"],
        },
        "scope": "Event spacing is schematic. The negative-X example is contract arithmetic.",
    }


# =============================================================================
# 13. 批量重绘和逐图证据记录
# =============================================================================
def main() -> int:
    """运行冻结参数下的行为诊断并保存图片及其指标。"""
    parser = argparse.ArgumentParser(description="Render four SAR ADC mechanism plates and twelve evidence figures")
    parser.add_argument("--out", type=Path, help="结果目录；省略时在系统临时目录中新建")
    parser.add_argument("--points", type=int, default=512)
    parser.add_argument("--repeats", type=int, default=8)
    parser.add_argument("--dynamic-n", type=int, default=8192)
    parser.add_argument("--mechanisms-only", action="store_true")
    args = parser.parse_args()
    if args.points < 16 or args.repeats < 1 or args.dynamic_n < 128:
        parser.error("need --points >= 16, --repeats >= 1 and --dynamic-n >= 128")
    # 无参数重跑时使用独立目录，避免半次或并发运行覆盖已发布的图和测量记录。
    out = args.out if args.out is not None else Path(tempfile.mkdtemp(prefix="sar-behavior-figures-"))
    out.mkdir(parents=True, exist_ok=True)

    with plt.rc_context(FIGURE_STYLE):
        produced = [
            figure_system_mechanism(out),
            figure_timing_mechanism(out),
            figure_dem_dither_mechanism(out),
            figure_reconstruction_mechanism(out),
        ]
        if not args.mechanisms_only:
            # 频谱采用 P4 低失配条件，压力试验保留较高失配。模型和数值条件
            # 单独写入清单，避免把跨设置的差值解读成某一模块的收益。
            baseline = _build_model(1e-4, True)
            stress = _build_model(1e-3, True)
            produced.extend([
                figure_ownership(stress, out),
                figure_dem_usage(out),
                figure_inl(baseline, out, points=args.points, repeats=args.repeats,
                           dynamic_n=args.dynamic_n),
                figure_psd(baseline, out, n=args.dynamic_n),
                figure_dither_margin(out),
                figure_follower(stress, out, n_codes=128),
                figure_dynamics(out, n=32),
                figure_az(stress, out, n=args.dynamic_n),
                figure_input_current(out),
                figure_interleaving(stress, out, n=args.dynamic_n),
                figure_calibration_generalization(out, points=args.points, repeats=args.repeats),
                figure_fixed_point(stress, out, n=args.dynamic_n),
            ])
    _write_manifest(out, produced, points=args.points, repeats=args.repeats,
                    dynamic_n=args.dynamic_n, mechanisms_only=args.mechanisms_only)
    for row in produced:
        print(row["figure"])
    print(f"written to {out}")
    return 0


def _write_manifest(out: Path, produced: list[dict], *, points: int, repeats: int,
                    dynamic_n: int, mechanisms_only: bool = False) -> None:
    """将图组与相应的输入、输出摘要绑定，允许独立复画后汇合。"""
    # 报告和图片一起保存。strict JSON 遇到无效指标会报错，不能让 NaN 变成
    # 看似成功的最终清单。基线提交和两个入口哈希用于定位本轮的未提交绘图修订。
    # 输入身份同时覆盖模型的 ADC2 默认值及实际码域实现，防止并行修订后
    # 图与合同行表面使用同一提交，实际却来自不同工作树 profile。
    source_paths = (
        "tools/run_review_figures.py", "tools/run_p4_ablation.py", "tb/vec_r18_sched.txt",
        "experiments/fig11.py", "src/adi_model/contracts/profiles.py",
        "src/adi_model/control/dem3d.py",
        "src/adi_model/simulation/pipeline_engine.py",
    )
    document = {
        "git_base_sha": _sha(),
        "source_files_sha256": {
            name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in source_paths
        },
        "runtime": {
            "python": sys.version.split()[0],
            "numpy": np.__version__,
            "matplotlib": matplotlib.__version__,
            "scipy": scipy.__version__,
        },
        "seed": SEED,
        "fs_hz": FS_HZ,
        "v_fs_v": V_FS,
        "lsb20_v": LSB20,
        "default_behavior_adc2": {
            "bits": Fig11Parameters().adc2_n_bits,
            "input_window_v": [Fig11Parameters().adc2_v_min, Fig11Parameters().adc2_v_max],
            "ra_clip_v": Fig11Parameters().ra_clip_v,
        },
        "figure_profiles": {
            "baseline_mismatch_std": 1e-4,
            "stress_mismatch_std": 1e-3,
            "points": points,
            "repeats": repeats,
            "dynamic_n": dynamic_n,
        },
        "evidence_level": "Mechanism diagrams, Python reference checks, model experiments and analytical estimates",
        "figure_style": {"paper": "ISSCC 2024 9.8, figure page", "width_inches": 7.16, "png_dpi": 300},
        "figures": produced,
        "figure_files_sha256": {
            file: hashlib.sha256((out / file).read_bytes()).hexdigest()
            for row in produced
            for file in (row["figure"], str(Path(row["figure"]).with_suffix(".svg")))
        },
        "redraw": (
            "bash tools/pyenv.sh --run tools/run_review_figures.py --out /tmp/sar-review-redraw "
            f"--points {points} --repeats {repeats} --dynamic-n {dynamic_n}"
            + (" --mechanisms-only" if mechanisms_only else "")
        ),
    }
    (out / "figures.json").write_text(
        json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    raise SystemExit(main())
