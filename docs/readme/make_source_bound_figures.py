#!/usr/bin/env python3
"""从冻结账本绘制默认结构、静态校准与Fig19逐事件图。

默认只读取架构参数快照；校准图须显式提供 --calibration-inputs 及其真实
留出码数组。当前记录须匹配公共producer和全部已加载源码的SHA；不匹配时
拒绝重绘，不能把旧记录刷新为新验证。脚本不导入ADC模型、不运行仿真或
训练，不修改历史结果。结构图是原创等效示意，不是原文图像或晶体管网表。
校准图保留中幅不可辨识的反例，明确转换噪声、扰动与RTL的验证边界。
"""

from __future__ import annotations

import argparse
import hashlib
import json
from itertools import pairwise
from pathlib import Path

import matplotlib
import numpy as np

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch, Rectangle
from matplotlib.text import Text

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
BLUE, TEAL, ORANGE = "#27658e", "#218578", "#cf8535"
INK, MUTED, GRAY = "#203044", "#5a6878", "#89949e"
FIGURES = {
    "architecture": "source_bound_default_architecture_v1",
    "calibration": "source_bound_static_calibration",
    "fig19": "source_bound_fig19_mechanism",
    "rtl_calibration": "source_bound_rtl_calibration",
}


def sha256(path: Path) -> str:
    """返回原始文件字节的 SHA256，不改写输入。"""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def setup_style() -> str:
    """选择本机已有中文字体；找不到时显式拒绝，不安装字体。"""
    for name in ("PingFang SC", "Hiragino Sans GB", "STHeiti", "Arial Unicode MS"):
        try:
            font_manager.findfont(name, fallback_to_default=False)
            break
        except ValueError:
            continue
    else:
        raise RuntimeError("需要可用中文字体；此脚本不安装或修改字体环境")
    plt.rcParams.update(
        {
            "font.sans-serif": [name, "DejaVu Sans"],
            "font.size": 11.5,
            "axes.titlesize": 12,
            "axes.titleweight": 600,
            "axes.labelsize": 11,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.unicode_minus": False,
            "text.color": INK,
            "axes.labelcolor": INK,
            "svg.fonttype": "path",
            "svg.hashsalt": "sar-source-bound-preparation-v1",
        }
    )
    return name


def load_calibration_inputs(path: Path) -> tuple[dict, dict[str, np.ndarray]]:
    """核验留出数据身份，并从实际20位码中心重算误差。

    Args:
        path: 新配图账本的 JSON 路径；点数组按其所在目录解析。

    Returns:
        账本和真实留出数组，数组形状均为 (N,)。

    Raises:
        ValueError: 文件摘要、整数码、样本数、指标或记录范围不一致。
    """
    record = json.loads(path.read_text(encoding="utf-8"))
    if record.get("schema_version") != 1:
        raise ValueError("unsupported figure input schema")
    case = record["calibration"]
    point_path = path.parent / case["points_file"]
    if sha256(point_path) != case["points_sha256"]:
        raise ValueError("holdout payload SHA256 mismatch")
    with np.load(point_path, allow_pickle=False) as payload:
        arrays = {name: payload[name].copy() for name in payload.files}
    summary = case["summary"]
    if summary["rtl_executed"] or summary["source_chip_algorithm_reproduction"]:
        raise ValueError("this figure only accepts the declared static Python experiment")
    if case.get("record_kind") != "CURRENT_FROZEN_STATIC_PYTHON" or case["provisional"]:
        raise ValueError("only the current source-bound public static record is supported")
    enabled = summary["inferred_model_metadata"]["enabled"]
    if any(
        enabled[key] for key in ("source_dither", "unit_dem", "sampling_noise", "ra_output_noise")
    ):
        raise ValueError("this figure requires the recorded no-dither/no-conversion-noise case")
    n = summary["holdout_samples"]
    for name, value in arrays.items():
        if value.shape != (n,):
            raise ValueError(f"{name}: expected raw holdout shape ({n},)")
    reference = arrays["reference_v"]
    if not np.isfinite(reference).all() or arrays["analog_invalid"].any():
        raise ValueError("nonfinite reference or unexpected analog-invalid sample")
    if not np.array_equal(arrays["calibrated_code"], arrays["independent_code"]):
        raise ValueError("calibrated and independent integer words differ")
    inferences = summary["inferred_model_metadata"]["inferences"]
    vfs = inferences["input_differential_peak_v"]["value"]
    bits = inferences["output_bits"]["value"]
    for name, field in (("nominal", "nominal_error"), ("calibrated", "calibrated_error")):
        words = arrays[f"{name}_code"]
        if words.dtype.kind not in "iu" or np.any((words < 0) | (words >= 1 << bits)):
            raise ValueError(f"{name}: invalid output code")
        # 两路都按同一码仓中心解码；不拟合增益、相位或偏置，也不删掉任何样本。
        error = (words.astype(float) + 0.5) * (2 * vfs / (1 << bits)) - vfs - reference
        arrays[f"{name}_error_uv"] = error * 1e6
        statistics = {
            "mean_v": float(np.mean(error)),
            "std_v": float(np.std(error)),
            "rms_v": float(np.sqrt(np.mean(error**2))),
            "max_abs_v": float(np.max(np.abs(error))),
        }
        for key, value in statistics.items():
            if not np.isclose(value, summary[field][key], rtol=0, atol=1e-14):
                raise ValueError(f"{name}/{key}: point data disagree with the recorded summary")
        if any(summary[field][key] for key in ("analog_invalid", "clip_low", "clip_high")):
            raise ValueError("the selected zero-invalid/zero-clip case changed")
    negative = case["negative_control"]
    if not 0 < negative["rank"] < negative["required_rank"] == summary["rank"]:
        raise ValueError("the negative-control and full-span ranks are inconsistent")
    if negative["training_samples"] != summary["training_samples"]:
        raise ValueError("the two training-coverage cases must use the recorded equal count")
    if (
        negative["producer_sha256"] != summary["producer_sha256"]
        or negative["source_sha256"] != summary["source_sha256"]
        or not negative["failure_record"]["source_unchanged"]
    ):
        raise ValueError("the positive and rejected training cases use different source identities")
    return record, arrays


def require_replay_negatives(case: dict) -> None:
    """要求记录分别覆盖采集、实际总线和8文件来源损坏拒绝。"""
    negative = case["negative_checks"]
    source = case["source_binding_negative_checks"]
    if (
        negative["status"] != "PASS"
        or negative["checks"] <= 0
        or not negative["all_rejected"]
        or not negative["acquisition_mask_rejected"]
        or not negative["unselected_rows_and_excess_width_rejected"]
        or source["status"] != "PASS"
        or source["checks"] <= 0
        or not source["all_rejected"]
    ):
        raise ValueError(
            "required actual acquisition/physical-bus/source-binding negatives are missing"
        )


def load_rtl_calibration_inputs(path: Path) -> tuple[dict, dict[str, np.ndarray]]:
    """从真实RTL输出重算留出误差，保留同源重放与有限窗身份。"""
    record = json.loads(path.read_text(encoding="utf-8"))
    if record.get("schema_version") != 1:
        raise ValueError("unsupported RTL figure input schema")
    case = record["rtl_calibration"]
    if case["record_kind"] != "CURRENT_FROZEN_STATIC_RTL" or case["source_reproduction"]:
        raise ValueError("only the declared source-bound engineering RTL record is supported")
    points = path.parent / case["points_file"]
    if sha256(points) != case["points_sha256"]:
        raise ValueError("RTL holdout payload SHA256 mismatch")
    with np.load(points, allow_pickle=False) as payload:
        arrays = {name: payload[name].copy() for name in payload.files}
    n = case["summary"]["holdout_samples"]
    if any(value.shape != (n,) for value in arrays.values()):
        raise ValueError("RTL holdout points must retain all N raw samples")
    if not all(
        np.array_equal(arrays["calibrated_code"], arrays[name])
        for name in ("independent_code", "production_code")
    ):
        raise ValueError("actual RTL, production-Python and independent integer words differ")
    if any(arrays[k].any() for k in ("nominal_flags", "calibrated_flags", "analog_bad")):
        raise ValueError("the selected zero-flag RTL record changed")
    reference = arrays["reference_v"]
    if not np.isfinite(reference).all():
        raise ValueError("nonfinite RTL holdout reference")
    for name in ("nominal", "calibrated"):
        words = arrays[f"{name}_code"]
        if words.dtype.kind not in "iu" or np.any((words < 0) | (words >= 1 << 20)):
            raise ValueError("invalid actual RTL output word")
        error = (words.astype(float) + 0.5) * (6 / (1 << 20)) - 3 - reference
        arrays[f"{name}_error_uv"] = error * 1e6
        metrics = {"rms_v": float(np.sqrt(np.mean(error**2))), "std_v": float(np.std(error))}
        for key, value in metrics.items():
            if not np.isclose(value, case[f"{name}_error"][key], rtol=0, atol=1e-14):
                raise ValueError("actual RTL points disagree with the recorded error metrics")
    replay = case["replay"]
    if replay["status"] != "PASS" or replay["integer_words_matched"] != n:
        raise ValueError("the corresponding actual-production replay is missing")
    if any(
        replay[key]
        for key in (
            "normal_capture_status_errors",
            "normal_capture_analog_sticky",
            "normal_capture_conversion_fault",
        )
    ):
        raise ValueError("normal RTL replay contains a protocol fault")
    require_replay_negatives(case)
    return record, arrays


def arrow(ax, points, *, color=INK, dashed=False) -> None:
    """画功能连接；箭头不代表实际电平转换器或模拟开关。"""
    for start, end in pairwise(points[:-1]):
        ax.plot(*zip(start, end, strict=True), color=color, lw=1.3, ls="--" if dashed else "-")
    ax.add_patch(
        FancyArrowPatch(
            points[-2],
            points[-1],
            arrowstyle="-|>",
            mutation_scale=13,
            color=color,
            lw=1.3,
            linestyle="--" if dashed else "-",
        )
    )


def box(ax, x, y, width, height, title, detail, color, *, dashed=False) -> None:
    """用简短标题与自然分行画等效功能框。"""
    ax.add_patch(
        FancyBboxPatch(
            (x, y),
            width,
            height,
            boxstyle="round,pad=0.025,rounding_size=0.08",
            facecolor="white",
            edgecolor=color,
            linewidth=1.6,
            linestyle="--" if dashed else "-",
        )
    )
    ax.text(
        x + width / 2,
        y + height * 0.77,
        title,
        ha="center",
        va="center",
        fontsize=12,
        weight=600,
        color=color,
    )
    ax.text(
        x + width / 2,
        y + height * 0.37,
        detail,
        ha="center",
        va="center",
        fontsize=10.8,
        linespacing=1.35,
    )


def architecture(record: dict) -> tuple[plt.Figure, dict]:
    """画当前默认参数快照；来源锚点与推断电气值分别标明。"""
    p = record["architecture"]["profile"]
    fig19_status = record["architecture"]["implementation_status"]["fig19_rtl"]
    limited_rtl_verified = fig19_status == "RESTRICTED_STATIC_SOURCE_BOUND_VERIFIED"
    fig19_label = (
        "Python／RTL指定静态点已核" if limited_rtl_verified else "Python受限路径已实现；RTL验证中"
    )
    fig = plt.figure(figsize=(13, 8))
    ax = fig.add_axes((0.025, 0.12, 0.95, 0.70))
    ax.set(xlim=(0, 18.4), ylim=(0, 8.1))
    ax.axis("off")
    fig.text(0.035, 0.958, "默认等效结构：原文锚点、工程推断与待验证边界", fontsize=20, weight=600)
    fig.text(
        0.035,
        0.913,
        f"{p['fs'] / 1e6:g} MS/s 参数 · {p['n_bits_target']}位输出码 · 源码快照／工程推断，非原芯片电路复原",
        color=MUTED,
        fontsize=12,
    )
    for x, label, color in (
        (0.035, "原文架构锚点", BLUE),
        (0.27, "工程推断的名义网络", TEAL),
        (0.59, "受限路径／物理网络待验", ORANGE),
    ):
        fig.patches.append(
            Rectangle(
                (x, 0.861),
                0.013,
                0.016,
                transform=fig.transFigure,
                facecolor=color,
                edgecolor="none",
            )
        )
        fig.text(x + 0.02, 0.861, label, color=color, fontsize=11.5)

    box(
        ax,
        1.5,
        3.5,
        3.0,
        1.5,
        f"{p['n_slices']}片电容资源池",
        f"{p['n_active']}转换／{p['n_active']}采集／{p['n_slices'] - 2 * p['n_active']}空闲\n消费此前实际采到的电荷",
        BLUE,
    )
    box(
        ax,
        5.0,
        3.5,
        3.0,
        1.5,
        "分段 RDAC（推断）",
        f"每片 {p['n_main']}主 + {p['n_sub']}子\n桥接比例与电荷统一计算",
        TEAL,
    )
    box(
        ax,
        8.5,
        3.5,
        2.4,
        1.5,
        "共享 RA",
        f"G[n]=Cnorm[n]/Cf\n名义{p['capacitance_gain']:g}；限幅±{p['ra_clip_v']:g} V",
        TEAL,
    )
    box(
        ax,
        11.4,
        3.5,
        2.9,
        1.5,
        f"ADC2：{p['adc2_n_bits']}位（推断）",
        f"[{p['adc2_v_min'] * 1e3:g}, {p['adc2_v_max'] * 1e3:g}) mV\n半开窗口、静态量化",
        TEAL,
    )
    box(
        ax,
        14.8,
        3.5,
        3.0,
        1.5,
        "数字重构",
        f"Q30／Q32；{p['n_bits_target']}位码\n名义或冻结拟合系数",
        TEAL,
    )
    for left, right in ((4.5, 5.0), (8.0, 8.5), (10.9, 11.4), (14.3, 14.8)):
        arrow(ax, [(left, 4.25), (right, 4.25)], color=BLUE)
    arrow(ax, [(0.1, 4.25), (1.5, 4.25)], color=BLUE)
    ax.text(0.48, 4.65, f"差分输入\n±{p['v_fs']:g} V", ha="center", color=BLUE, fontsize=10.5)
    arrow(ax, [(17.8, 4.25), (18.3, 4.25)], color=TEAL)
    box(
        ax,
        2.0,
        6.4,
        3.8,
        1.3,
        f"独立 SADC：{p['b1']}位",
        f"静态阈值量化\nCs={p['sadc_cap_f'] * 1e12:g} pF（推断）",
        BLUE,
    )
    arrow(ax, [(1.15, 4.25), (1.15, 7.05), (2.0, 7.05)], color=BLUE)
    box(
        ax,
        6.5,
        6.4,
        4.8,
        1.3,
        "显式粗码 → 开关 mask",
        "默认基本计数译码\n不等同原文3D DEM或完整桥接矩阵",
        TEAL,
    )
    arrow(ax, [(5.8, 7.05), (6.5, 7.05)], color=BLUE)
    arrow(ax, [(8.9, 6.4), (8.9, 5.7), (6.5, 5.7), (6.5, 5.0)], color=TEAL)
    ax.text(7.35, 5.89, "同笔控制与物理选择", fontsize=10.3, ha="center", color=TEAL)
    box(
        ax,
        12.0,
        6.4,
        5.8,
        1.3,
        "Fig.19 lower／upper 静态等效",
        fig19_label + "\n默认关闭；完整电容／开关／噪声网络未实现",
        ORANGE,
        dashed=True,
    )

    box(
        ax,
        1.5,
        0.65,
        7.0,
        1.85,
        "单片名义网络与共享反馈（推断）",
        f"Cu={p['unit_cap_f'] * 1e15:.3f} fF；Cb={p['bridge_cap_f'] * 1e15:.3f} fF；β={p['beta_nominal']:g}\n"
        f"{p['n_active']}×({p['n_main']}Cu+β·{p['n_sub']}Cu)={p['active_Cnorm_f'] * 1e12:g} pF（Cnorm）\n"
        f"共享 Cf={p['feedback_cap_f'] * 1e12:g} pF；不是每片各放一个Cf",
        TEAL,
    )
    box(
        ax,
        9.2,
        0.65,
        8.6,
        1.85,
        "两个系数入口，不能互称",
        "名义 C/Cf：默认直接计算，不是训练\n"
        "冻结拟合：独立已知输入 + 数字观测 → SVD → 固定\n"
        + (
            "两组校准图各有静态范围；动态与真实宏仍待验"
            if limited_rtl_verified
            else "当前校准图只验证Python静态留出；没有RTL／动态结论"
        ),
        TEAL,
    )
    arrow(ax, [(13.5, 2.5), (13.5, 2.95), (16.3, 2.95), (16.3, 3.5)], color=TEAL)
    fig.text(
        0.035,
        0.079,
        f"默认压力参数：σCu=σCb={p['unit_mismatch_std'] * 1e6:g} ppm；采样噪声开启，RA输出{p['ra_noise_rms_v'] * 1e3:g} mV RMS。校准图另关闭随机噪声。",
        fontsize=10.8,
    )
    fig.text(
        0.035,
        0.035,
        "原创等效示意；20.5 pF的节点映射及63+8网络是工程假设。真实开关、动态参考／RA、H/K与工艺时序未验证。",
        fontsize=10.5,
        color=MUTED,
    )
    return fig, {
        "scope": "INFERRED_DEFAULT_ARCHITECTURE_SOURCE_SNAPSHOT",
        "provisional": False,
        "source_fig19_status": fig19_status,
    }


def calibration(record: dict, arrays: dict[str, np.ndarray]) -> tuple[plt.Figure, dict]:
    """用真实留出点和秩反例展示指定源码身份的Python静态记录。"""
    case = record["calibration"]
    s, negative = case["summary"], case["negative_control"]
    before, after = s["nominal_error"], s["calibrated_error"]
    fig = plt.figure(figsize=(13, 7.6))
    fig.text(0.035, 0.955, "Python静态校准｜独立留出", fontsize=20, weight=600)
    fig.text(
        0.50,
        0.955,
        f"留出RMS：{before['rms_v'] * 1e6:.2f} → {after['rms_v'] * 1e6:.3f} µV",
        fontsize=20,
        weight=600,
        color=TEAL,
    )
    fig.text(
        0.035,
        0.906,
        f"固定失配 seed{s['seed']} · {s['training_samples']}随机训练／{s['holdout_samples']}独立留出 · 同一批raw · 采样／RA随机噪声关闭，DEM／dither关闭",
        fontsize=11.5,
        color=MUTED,
    )
    rank_ax = fig.add_axes((0.055, 0.405, 0.23, 0.355))
    rank_ax.set_title(f"A  同为{s['training_samples']}笔，覆盖范围决定可辨识性", loc="left", pad=13)
    required = negative["required_rank"]
    rank_ax.barh(1.1, negative["rank"], height=0.37, color=ORANGE)
    rank_ax.barh(
        1.1,
        required - negative["rank"],
        left=negative["rank"],
        height=0.37,
        color="white",
        edgecolor=ORANGE,
        hatch="////",
    )
    rank_ax.barh(-0.2, s["rank"], height=0.37, color=TEAL)
    rank_ax.text(
        0,
        1.58,
        f"中幅 [-{negative['training_peak_v']:g}, +{negative['training_peak_v']:g}) V",
        fontsize=11,
    )
    rank_ax.text(
        required / 2,
        1.1,
        f"{negative['rank']} / {required}",
        color="white",
        ha="center",
        va="center",
        fontsize=12,
        weight=600,
    )
    rank_ax.text(
        0,
        0.65,
        f"缺{required - negative['rank']}个方向：拒绝拟合／冻结",
        color=ORANGE,
        fontsize=10.8,
    )
    rank_ax.text(
        0, 0.25, f"全跨度 [-{s['training_peak_v']:g}, +{s['training_peak_v']:g}) V", fontsize=11
    )
    rank_ax.text(
        required / 2,
        -0.2,
        f"{s['rank']} / {required}",
        color="white",
        ha="center",
        va="center",
        fontsize=12,
        weight=600,
    )
    rank_ax.text(0, -0.65, "满秩后固定系数，再看独立留出", color=TEAL, fontsize=10.8)
    rank_ax.set(
        xlim=(0, required * 1.04),
        ylim=(-0.90, 1.90),
        yticks=[],
        xticks=[0, required],
        xlabel="设计矩阵秩",
    )
    rank_ax.spines["left"].set_visible(False)
    rank_ax.tick_params(axis="y", length=0)
    fig.text(0.055, 0.300, "训练与留出分开", fontsize=12, weight=600)
    fig.text(0.055, 0.253, f"训练条件数：{s['condition']:.1f}", fontsize=11)
    fig.text(
        0.055,
        0.210,
        f"留出仅 [{case['holdout_reference_min_v']:g}, +{case['holdout_reference_max_v']:g}] V",
        fontsize=11,
    )
    fig.text(0.055, 0.167, "训练满跨度不等于全幅留出通过", fontsize=10.7, color=MUTED)

    ax = fig.add_axes((0.36, 0.335, 0.345, 0.425))
    zoom = fig.add_axes((0.775, 0.335, 0.195, 0.425))
    reference = arrays["reference_v"]
    for axis in (ax, zoom):
        axis.axvspan(-3, case["holdout_reference_min_v"], color="#edf0f2", zorder=0)
        axis.axvspan(case["holdout_reference_max_v"], 3, color="#edf0f2", zorder=0)
        axis.axhline(0, color=GRAY, lw=0.7)
        axis.grid(alpha=0.20, lw=0.6)
        axis.set(xlim=(-3, 3), xlabel="独立已知留出输入 (V)")
    ax.scatter(
        reference,
        arrays["nominal_error_uv"],
        s=9,
        color=ORANGE,
        alpha=0.60,
        label="未校准（名义）",
        linewidths=0,
    )
    ax.scatter(
        reference,
        arrays["calibrated_error_uv"],
        s=8,
        color=TEAL,
        alpha=0.70,
        label="已校准（冻结）",
        linewidths=0,
    )
    ax.set(
        title=f"B  同一纵轴：全部{s['holdout_samples']}笔留出",
        ylim=(-280, 280),
        yticks=[-250, -125, 0, 125, 250],
        ylabel="20位码中心 − 输入 (µV)",
    )
    ax.legend(loc="upper left", frameon=False, fontsize=10.8)
    zoom.scatter(
        reference, arrays["calibrated_error_uv"], s=12, color=TEAL, alpha=0.68, linewidths=0
    )
    zoom.set(
        title="C  残差放大（纵轴改变）", ylim=(-6, 6), yticks=[-6, -3, 0, 3, 6], ylabel="误差 (µV)"
    )
    fig.text(
        0.36,
        0.245,
        f"名义：均值 {before['mean_v'] * 1e6:.2f}；标准差 {before['std_v'] * 1e6:.2f}；RMS {before['rms_v'] * 1e6:.2f} µV",
        fontsize=10.8,
        color=ORANGE,
    )
    fig.text(
        0.36,
        0.200,
        f"冻结：均值 {after['mean_v'] * 1e6:.4f}；标准差 {after['std_v'] * 1e6:.4f}；RMS {after['rms_v'] * 1e6:.4f} µV",
        fontsize=10.8,
        color=TEAL,
    )
    fig.text(
        0.36,
        0.155,
        "灰色区：此次留出未覆盖的幅度；未平滑、未删点、未作输出拟合校正。",
        fontsize=10.5,
        color=MUTED,
    )
    fig.text(
        0.035,
        0.087,
        f"模拟异常0；最终clip0；{s['independent_integer_words_matched']}/{s['holdout_samples']}笔最终整数码与独立oracle一致。两种系数消费同一raw。",
        fontsize=11,
    )
    fig.text(
        0.035,
        0.035,
        "公共producer实跑的无dither Python工程静态记录；未验证RTL、含随机噪声工况、动态、端点、全码INL/DNL或芯片性能。",
        fontsize=10.5,
        color=MUTED,
    )
    return fig, {
        "scope": s["scope"],
        "provisional": False,
        "rtl_executed": False,
        "all_holdout_samples": s["holdout_samples"],
        "nominal_error": before,
        "calibrated_error": after,
        "negative_control_rank": negative["rank"],
        "fullspan_rank": s["rank"],
    }


def fig19(record: dict) -> tuple[plt.Figure, dict]:
    """用实际候选事件说明两采集端口、一次重构和尚缺的DEM网络。"""
    case = record["fig19"]
    require_replay_negatives(case)
    negative = case["negative"]
    prefix = np.asarray(negative["prefix_before_upper"], dtype=np.int64)
    candidates = np.asarray(negative["candidate_codes"], dtype=np.int64)
    accepted = np.asarray(negative["accepted"], dtype=bool)
    upper = negative["upper"]
    if prefix.shape != (7,) or not np.array_equal(candidates, prefix - upper):
        raise ValueError("Fig19 trace does not preserve every resolved prefix and upper word")
    domain = negative["code_domain"]
    if not np.array_equal(accepted, (candidates >= domain[0]) & (candidates < domain[1])):
        raise ValueError("Fig19 accepted events differ from the actual code-domain checks")
    fig = plt.figure(figsize=(13, 8.5))
    fig.text(0.035, 0.954, "Fig.19：两采集端口、一次重构与早事件拒绝", fontsize=20, weight=600)
    fig.text(
        0.035,
        0.909,
        "原文按位分组关系；负极性、Q32与18/8目标绑定为受限工程静态等效，完整物理网络未实现。",
        fontsize=11.6,
        color=MUTED,
    )
    mechanism = fig.add_axes((0.025, 0.515, 0.565, 0.34))
    mechanism.set(xlim=(0, 12), ylim=(0, 5.2))
    mechanism.axis("off")
    mechanism.text(0.2, 4.97, "A  同一个显式六位字，控制三条不同关系", weight=600, fontsize=12)
    box(mechanism, 0.2, 1.45, 2.2, 2.3, "D6…D1", "调用方显式给字\n逐样本记录", BLUE)
    for y, title, detail, target, operation in (
        (3.5, "lower L", "D5、D3、D2、D1", "SADC采样", "x−L → 粗码c"),
        (1.95, "total T", "T=L+U·Δ", "RDAC保存", "x−T"),
        (0.4, "upper U", "U=4D6+D4", "实际RDAC命令", "k=c−U；逐prefix减U"),
    ):
        box(mechanism, 3.1, y, 3.3, 1.05, title, detail, TEAL)
        box(mechanism, 7.4, y, 4.1, 1.05, target, operation, BLUE)
        arrow(mechanism, [(2.4, 2.6), (2.75, 2.6), (2.75, y + 0.525), (3.1, y + 0.525)], color=TEAL)
        arrow(mechanism, [(6.4, y + 0.525), (7.4, y + 0.525)], color=BLUE)
    events = np.arange(1, len(candidates) + 1)
    ax = fig.add_axes((0.675, 0.56, 0.285, 0.255))
    fig.text(
        0.675,
        0.855,
        f"B  实际反例：{negative['input_v']:g} V／word{negative['word']}",
        fontsize=12,
        weight=600,
    )
    ax.axhspan(-6, 0, color="#f8e6d5", zorder=0)
    ax.axhline(0, color=GRAY, lw=0.8)
    ax.plot(events, prefix, "o--", color=GRAY, ms=5, lw=1.1, label="原已决prefix")
    ax.plot(events, candidates, "-", color=TEAL, lw=1.4)
    ax.scatter(
        events[accepted], candidates[accepted], color=TEAL, s=49, zorder=4, label="减U后合法"
    )
    ax.scatter(
        events[~accepted],
        candidates[~accepted],
        color=ORANGE,
        marker="x",
        s=65,
        linewidths=2,
        zorder=4,
        label="减U后拒绝",
    )
    ax.set(
        xlim=(0.6, 7.4),
        ylim=(-6, 11),
        xticks=events,
        yticks=[-4, 0, 4, 8],
        xlabel="独立的已决事件（重复码也保留）",
        ylabel="RDAC整数码（只放大低端）",
    )
    ax.grid(alpha=0.20, lw=0.6)
    ax.legend(
        loc="lower left",
        bbox_to_anchor=(0, 1.015),
        fontsize=8.8,
        frameon=False,
        ncol=3,
        columnspacing=0.6,
        handlelength=1.2,
    )
    ax.text(
        2,
        -5.2,
        f"前{int((~accepted).sum())}次不加载非法mask",
        color=ORANGE,
        fontsize=10,
        ha="center",
    )
    ax.text(5.65, 5.2, f"最终k={negative['final_code']}合法", color=TEAL, fontsize=10, ha="center")
    fig.text(
        0.65, 0.483, "最终合法不能补救早prefix非法；不夹码、不删事件。", fontsize=11.1, color=ORANGE
    )
    fig.text(
        0.65,
        0.425,
        f"实际采集：SADC {negative['sadc_sample_v']:.4f} V，RDAC {negative['rdac_sample_v']:.4f} V。",
        fontsize=10.4,
        color=MUTED,
    )

    fig.text(0.045, 0.465, "实际mask＋冻结W → 一次重构", fontsize=17, weight=600, color=TEAL)
    fig.text(0.045, 0.421, "均匀名义时：VD(k)+fine/Ghat+T = VD(c)+fine/Ghat+L。", fontsize=10.7)
    fig.text(
        0.045,
        0.381,
        "失配时逐unit归约；T是已知电压项。upper已在mask中，不能再扣一次U。",
        fontsize=10.7,
        color=MUTED,
    )

    dem = fig.add_axes((0.025, 0.17, 0.95, 0.165))
    dem.set(xlim=(0, 18.4), ylim=(0, 2.05))
    dem.axis("off")
    dem.text(
        0.2, 1.88, "C  DEM应改变实际物理选择；资源调度本身不等于单元DEM", fontsize=12, weight=600
    )
    for x, width, title, detail, color, dashed in (
        (0.2, 3.8, "采集／转换资源计划", "有序物理slice IDs", BLUE, False),
        (4.65, 4.4, "实际source binding", "每slice主／子unit mask", TEAL, False),
        (9.7, 3.5, "冻结权重重构", "按实际ID与mask取值", TEAL, False),
        (13.85, 4.2, "原文3D DEM未实现", "均衡统计／精度收益待验证", ORANGE, True),
    ):
        box(dem, x, 0.15, width, 1.25, title, detail, color, dashed=dashed)
    for left, right in ((4.0, 4.65), (9.05, 9.7)):
        arrow(dem, [(left, 0.775), (right, 0.775)], color=TEAL)
    evidence = case["verified_capture"]
    fig.text(
        0.035,
        0.11,
        f"实际DUT负例{evidence['capture_targets']}目标／{evidence['retired']}退休，拒绝ID {','.join(map(str, evidence['rejected_ids']))}并触发预期fault；候选事件与Python记录一致。",
        fontsize=11,
    )
    fig.text(
        0.035,
        0.063,
        "已测±2.0 V等指定静态点；没有连续全幅、逐事件模拟建立、参考恢复、开关噪声或DEM组合通过声明。",
        fontsize=10.6,
        color=MUTED,
    )
    fig.text(
        0.035,
        0.025,
        "原创功能示意，非原文图复制。正常RTL另按N+3程序／N+2物理／N目标的有限观察窗核验，不代表停机／idle。",
        fontsize=10.2,
        color=MUTED,
    )
    return fig, {
        "scope": case["scope"],
        "provisional": False,
        "source_reproduction": False,
        "negative_candidate_codes": candidates.tolist(),
        "actual_rejected_ids": evidence["rejected_ids"],
        "source_dem_implemented": False,
    }


def rtl_calibration(record: dict, arrays: dict[str, np.ndarray]) -> tuple[plt.Figure, dict]:
    """画真实DUT留出误差；量化效果和有限观察窗分别标明。"""
    case = record["rtl_calibration"]
    s = case["summary"]
    fig = plt.figure(figsize=(13, 7.3))
    fig.text(0.035, 0.95, "RTL静态闭环｜非均匀ADC模型与同序计划", fontsize=20, weight=600)
    fig.text(
        0.035,
        0.902,
        f"{s['training_samples']}笔DUT观测供Python训练／系数回装／{s['holdout_samples']}独立留出；64字各8次，静态等效、随机噪声与源DEM关闭。",
        fontsize=11.4,
        color=MUTED,
    )
    fig.text(
        0.035,
        0.816,
        f"留出RMS：{s['nominal_rms_v'] * 1e6:.2f} → {s['fitted_rms_v'] * 1e6:.3f} µV",
        fontsize=21,
        color=TEAL,
        weight=600,
    )
    ax = fig.add_axes((0.075, 0.325, 0.55, 0.395))
    zoom = fig.add_axes((0.74, 0.325, 0.225, 0.395))
    for axis in (ax, zoom):
        axis.axvspan(-3, case["holdout_reference_min_v"], color="#edf0f2", zorder=0)
        axis.axvspan(case["holdout_reference_max_v"], 3, color="#edf0f2", zorder=0)
        axis.axhline(0, color=GRAY, lw=0.7)
        axis.grid(alpha=0.2, lw=0.6)
        axis.set(xlim=(-3, 3), xlabel="独立已知留出输入 (V)", ylabel="20位码中心 − 输入 (µV)")
    ax.scatter(
        arrays["reference_v"],
        arrays["nominal_error_uv"],
        s=10,
        color=ORANGE,
        alpha=0.62,
        linewidths=0,
        label="未校准（名义）",
    )
    ax.scatter(
        arrays["reference_v"],
        arrays["calibrated_error_uv"],
        s=9,
        color=TEAL,
        alpha=0.7,
        linewidths=0,
        label="已校准（冻结）",
    )
    ax.set(title=f"A  同一raw、全部{s['holdout_samples']}笔、共用纵轴", ylim=(-300, 300))
    ax.legend(frameon=False, loc="upper left", fontsize=10.8)
    zoom.scatter(
        arrays["reference_v"],
        arrays["calibrated_error_uv"],
        s=13,
        color=TEAL,
        alpha=0.7,
        linewidths=0,
    )
    zoom.set(title="B  冻结残差放大", ylim=(-6, 6), yticks=[-6, -3, 0, 3, 6])
    fig.text(
        0.075,
        0.235,
        f"训练rank {s['rank']}/{s['parameters']}；独立留出仅 [{case['holdout_reference_min_v']:g}, {case['holdout_reference_max_v']:g}] V。灰区未覆盖。",
        fontsize=11.2,
    )
    replay = case["replay"]
    fig.text(
        0.075,
        0.182,
        f"生产Python以同物理C、同有序ID重放：{replay['integer_words_matched']}码一致；{replay['rdac_prefix_events_matched']}个prefix；RA最大差{replay['ra_max_error_v']:.3g} V。",
        fontsize=10.9,
        color=TEAL,
    )
    fig.text(
        0.035,
        0.104,
        f"正常有限窗：{replay['program_samples']}程序／{replay['physical_samples_replayed']}物理采样／{replay['samples']}目标退休，3程序尾未退休；status／sticky／fault为0。",
        fontsize=10.9,
    )
    fig.text(
        0.035,
        0.054,
        "本图是数字DUT＋静态工程ADC模型；不证明真实停机／idle、模拟建立、含噪动态、全码INL/DNL或目标工艺STA。",
        fontsize=10.5,
        color=MUTED,
    )
    return fig, {
        "scope": case["scope"],
        "provisional": False,
        "rtl_executed": True,
        "fullspan_rank": s["rank"],
        "target_samples": s["holdout_samples"],
        "independent_production_python_replay": True,
    }


def save_checked(fig: plt.Figure, out: Path, basename: str) -> dict:
    """检查可见文字未越过画布，再导出PNG与SVG。"""
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    bounds = fig.bbox
    hidden_ticks = set()
    for axis in fig.axes:
        for coordinate in (axis.xaxis, axis.yaxis):
            if not axis.axison:
                hidden_ticks.update((coordinate.label, coordinate.get_offset_text()))
            low, high = sorted(coordinate.get_view_interval())
            for tick in (*coordinate.get_major_ticks(), *coordinate.get_minor_ticks()):
                if not axis.axison or not low <= tick.get_loc() <= high:
                    hidden_ticks.update((tick.label1, tick.label2))
    outside = []
    for artist in fig.findobj(Text):
        if artist in hidden_ticks or not artist.get_visible() or not artist.get_text().strip():
            continue
        box_ = artist.get_window_extent(renderer)
        if box_.x0 < -1 or box_.y0 < -1 or box_.x1 > bounds.x1 + 1 or box_.y1 > bounds.y1 + 1:
            outside.append(artist.get_text())
    if outside:
        raise ValueError(f"text outside figure canvas: {outside}")
    result = {}
    for suffix in ("png", "svg"):
        path = out / f"{basename}.{suffix}"
        metadata = (
            {"Software": "SAR source-bound README figures"}
            if suffix == "png"
            else {
                "Creator": "SAR source-bound README figures",
                "Date": None,
            }
        )
        fig.savefig(path, dpi=200, facecolor="white", metadata=metadata)
        result[path.name] = {"sha256": sha256(path), "bytes": path.stat().st_size}
    plt.close(fig)
    return {"text_outside_canvas": 0, "outputs": result}


def source_identity(source_sha256: dict[str, str], *, prefix="") -> dict:
    """只报告本机源码是否仍匹配记录；不把不匹配身份升级成新验证。"""
    matched, changed, missing = [], [], []
    for name, digest in source_sha256.items():
        path = REPO / prefix / name
        if not path.is_file():
            missing.append(name)
        elif sha256(path) == digest:
            matched.append(name)
        else:
            changed.append(name)
    return {
        "matched": matched,
        "changed": changed,
        "missing": missing,
        "all_match": not changed and not missing,
    }


def main() -> None:
    """重绘冻结输入；把输入、producer和源码身份写入独立清单。"""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--inputs", type=Path, default=HERE / "data/source_bound_default_architecture_v1.json"
    )
    parser.add_argument("--calibration-inputs", type=Path)
    parser.add_argument("--fig19-inputs", type=Path)
    parser.add_argument("--rtl-calibration-inputs", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--only", choices=FIGURES, nargs="+")
    args = parser.parse_args()
    paths = {
        "architecture": args.inputs,
        "calibration": args.calibration_inputs,
        "fig19": args.fig19_inputs,
        "rtl_calibration": args.rtl_calibration_inputs,
    }
    selected = args.only or [name for name, path in paths.items() if path is not None]
    for name in selected:
        if paths[name] is None:
            parser.error(f"{name}须显式提供相应冻结输入JSON")
    records, array_sets, input_sha = {}, {}, {}
    if "architecture" in selected:
        input_sha["architecture"] = sha256(args.inputs)
        records["architecture"] = json.loads(args.inputs.read_text(encoding="utf-8"))
        if records["architecture"].get("schema_version") != 1:
            raise ValueError("unsupported architecture input schema")
        if not source_identity(records["architecture"]["architecture"]["profile_source_sha256"])[
            "all_match"
        ]:
            parser.error("默认参数快照与当前源码不符；重建快照后重绘")
    if "calibration" in selected:
        input_sha["calibration"] = sha256(args.calibration_inputs)
        records["calibration"], array_sets["calibration"] = load_calibration_inputs(
            args.calibration_inputs
        )
        case = records["calibration"]["calibration"]
        summary = case["summary"]
        identity = source_identity(summary["source_sha256"], prefix="src/adi_model")
        producer_identity = source_identity({summary["producer"]: summary["producer_sha256"]})
        if not identity["all_match"] or not producer_identity["all_match"]:
            parser.error("记录源SHA与当前源码／producer不符；须重跑，不能刷新旧SHA")
    for name in ("fig19", "rtl_calibration"):
        if name not in selected:
            continue
        input_sha[name] = sha256(paths[name])
        if name == "rtl_calibration":
            records[name], array_sets[name] = load_rtl_calibration_inputs(paths[name])
        else:
            records[name] = json.loads(paths[name].read_text(encoding="utf-8"))
            if records[name].get("schema_version") != 1:
                raise ValueError("unsupported Fig19 figure input schema")
        if not source_identity(records[name][name]["repo_source_sha256"])["all_match"]:
            parser.error(f"{name}记录源SHA与当前源／工具不符，不能刷新旧身份")
    args.out.mkdir(parents=True, exist_ok=True)
    font = setup_style()
    figures = {}
    for name in selected:
        if name == "architecture":
            fig, evidence = architecture(records[name])
        elif name == "calibration":
            fig, evidence = calibration(records[name], array_sets[name])
        elif name == "fig19":
            fig, evidence = fig19(records[name])
        else:
            fig, evidence = rtl_calibration(records[name], array_sets[name])
        basename = FIGURES[name]
        figures[name] = {**evidence, **save_checked(fig, args.out, basename)}
    for name, path in paths.items():
        if name in input_sha and sha256(path) != input_sha[name]:
            raise RuntimeError("figure inputs changed during rendering")
    manifest = {
        "schema_version": 1,
        "operation": "FROZEN_INPUT_REDRAW_ONLY_NO_SIMULATION_OR_TRAINING",
        "provisional": any(item["provisional"] for item in figures.values()),
        "renderer_sha256": sha256(Path(__file__)),
        "inputs_sha256": input_sha,
        "font": font,
        "numpy": np.__version__,
        "matplotlib": matplotlib.__version__,
        "figures": figures,
    }
    if "architecture" in selected:
        manifest["architecture_source_identity"] = source_identity(
            records["architecture"]["architecture"]["profile_source_sha256"]
        )
        if not manifest["architecture_source_identity"]["all_match"]:
            raise RuntimeError("architecture source changed during rendering")
    if "calibration" in selected:
        case = records["calibration"]["calibration"]
        manifest["points_sha256"] = case["points_sha256"]
        manifest["calibration_source_identity"] = source_identity(
            case["summary"]["source_sha256"], prefix="src/adi_model"
        )
        summary = case["summary"]
        manifest["calibration_producer_identity"] = source_identity(
            {summary["producer"]: summary["producer_sha256"]}
        )
        if not all(
            manifest[name]["all_match"]
            for name in ("calibration_source_identity", "calibration_producer_identity")
        ):
            raise RuntimeError(
                "source changed during rendering; do not publish these calibration figures"
            )
    for name in ("fig19", "rtl_calibration"):
        if name in selected:
            manifest[f"{name}_source_identity"] = source_identity(
                records[name][name]["repo_source_sha256"]
            )
            if not manifest[f"{name}_source_identity"]["all_match"]:
                raise RuntimeError("source or verifier changed during rendering")
    (args.out / "source_bound_figures_manifest_v1.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "status": "FROZEN_INPUT_FIGURES_RENDERED",
                "figures": list(figures),
                "provisional": manifest["provisional"],
                "rtl_executed_by_renderer": False,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
