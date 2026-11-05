#!/usr/bin/env python3
"""根据现有生产命令译码器绘制 DEM 与扰动合同。

保留 JSON 记录名义命令，不是制造电容或 ADC 测量。图示用于解释命令单位和
排序，不证明 Python／RTL 逐位等价、SNDR 或硅片扰动范围增强。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from fractions import Fraction
from pathlib import Path

import matplotlib
import numpy as np

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.patches import Ellipse, FancyArrowPatch, FancyBboxPatch, Rectangle

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from adi_model.config import Config  # noqa: E402
from adi_model.dem import split_switch_command  # noqa: E402
from adi_model.mapper import dither_transfer_code  # noqa: E402
from adi_model.sampler import capture  # noqa: E402
from adi_model.timing import build_rtl_slice_plan  # noqa: E402
from adi_model.weight_calibration import (  # noqa: E402
    CalibrationSpec,
    DigitalObservation,
    _terms,
)

BLUE, ORANGE, TEAL, PURPLE = "#27658e", "#cf8535", "#218578", "#865a9d"
INK, MUTED, LINE, PALE = "#203044", "#5a6878", "#d1dae3", "#edf2f6"
SOURCE_FILES = (
    "src/adi_model/config.py",
    "src/adi_model/dem.py",
    "src/adi_model/timing.py",
    "src/adi_model/mapper.py",
    "src/adi_model/sampler.py",
    "src/adi_model/sampling_charge.py",
    "src/adi_model/reconstruction.py",
    "src/adi_model/weight_calibration.py",
    "src/adi_model/chopping_az.py",
    "rtl/core/dem_addr_gen.sv",
    "rtl/core/swap_decode.sv",
    "rtl/core/sar_structural_ctrl.sv",
    "rtl/core/unit_therm.sv",
    "rtl/core/cal_weight_reduce.sv",
    "mechanism_inventory.json",
    "docs/readme/make_randomization_figures.py",
)


def _masks(command, main_size: int, sub_size: int) -> tuple[np.ndarray, np.ndarray]:
    """将生产顺序及计数展开为物理单元的布尔选通掩码。"""
    result = []
    for size, order, count in (
        (main_size, command.main_order, command.main_counts),
        (sub_size, command.sub_order, command.sub_counts),
    ):
        if not np.array_equal(count, np.rint(count)):
            raise ValueError("illustrated commands must be integer physical masks")
        selected = np.arange(size)[None, None, :] < count[..., None]
        physical = np.zeros(selected.shape, dtype=bool)
        np.put_along_axis(physical, order[:, None, :], selected, axis=2)
        if not np.array_equal(physical.sum(axis=2), count):
            raise ValueError("physical mask population differs from decoder count")
        result.append(physical)
    return result[0], result[1]


def _hex_mask(bits: np.ndarray) -> str:
    """以第 p 位表示物理单元 p，避免显示顺序歧义。"""
    value = sum(int(bit) << index for index, bit in enumerate(bits))
    return f"0x{value:0{(len(bits) + 3) // 4}x}"


def _bits(hexadecimal: str, width: int) -> np.ndarray:
    value = int(hexadecimal, 16)
    return np.array([(value >> index) & 1 for index in range(width)], dtype=int)


def _sampling_connections(cfg: Config, codes: np.ndarray) -> tuple[np.ndarray, float]:
    """读取合法合成命令对应的实际校准电荷项。

    后级占位码仅补齐 DigitalObservation 接口，不表示实际 ADC 转换或实测细电压。
    只读调用生产 _terms 辅助函数，以取得其物理参考轨连接。
    """
    n = len(codes)
    ids = np.tile(np.arange(cfg.n_active), (n, 1))
    observation = DigitalObservation(
        CalibrationSpec.from_config(cfg),
        ids,
        176 + codes,
        np.zeros(n, dtype=np.int64),
        np.zeros(n, dtype=np.int64),
        codes,
        np.zeros(n),
        np.zeros(n, dtype=bool),
        True,
    )
    observation.validate()
    command = split_switch_command(cfg, observation.rdac_code, observation.dem_state)
    main, sub = _masks(command, cfg.dac_n_main, cfg.dac_n_sub)
    plus = np.concatenate((main, sub), axis=2)
    _, signal, b_minus_dac = _terms(observation, 0, n)
    injection = b_minus_dac + cfg.v_fs * (2 * plus.astype(float) - 1)
    mask_size = cfg.dither_units_total
    rail_voltage = injection[:, :, -mask_size:]
    if not np.allclose(rail_voltage, rail_voltage[:, :1, :], atol=0, rtol=0):
        raise ValueError("the selected profile does not share rails across active slices")
    if not np.all(np.isclose(np.abs(rail_voltage), cfg.v_fs)):
        raise ValueError("sampling units are not connected to the declared reference rails")
    weights = np.r_[np.ones(cfg.dac_n_main), np.ones(cfg.dac_n_sub) / cfg.dac_n_sub]
    alpha = np.sum(signal * weights, axis=(1, 2)) / (cfg.n_active * weights.sum())
    if not np.allclose(alpha, cfg.dither_alpha, atol=1e-15, rtol=0):
        raise ValueError("charge-term signal gain differs from Config.dither_alpha")
    return (rail_voltage[:, 0, :] > 0).astype(int), float(alpha[0])


def collect_contract() -> dict:
    """计算名义 DEM 选通、因果分配组及扰动单位。"""
    cfg = Config.rtl_structural(dem_enable=True, dither_mode="off")
    if (cfg.n_slices, cfg.n_active, cfg.dac_n_main, cfg.dac_n_sub) != (18, 8, 63, 8):
        raise ValueError("these figures describe the explicit structural 18/8, 63+8 profile")
    plan = build_rtl_slice_plan(8, shuffle=True, seed=cfg.conversion.rtl_shuffle_seed)
    if np.any(
        np.sort(np.c_[plan.conv, plan.acq], axis=1)[:, 1:]
        == np.sort(np.c_[plan.conv, plan.acq], axis=1)[:, :-1]
    ):
        raise ValueError("conversion and acquisition groups overlap")
    if not np.array_equal(np.sort(plan.acq[:-1]), np.sort(plan.conv[1:])):
        raise ValueError("acquisition ownership does not reach the following conversion")
    examples = []
    for code in (176, 179):
        for state in (0, 433):
            command = split_switch_command(cfg, np.array([code]), np.array([state]))
            main, sub = _masks(command, cfg.dac_n_main, cfg.dac_n_sub)
            slot_units = command.main_counts[0] * cfg.dac_n_sub + command.sub_counts[0]
            if float(slot_units.sum()) != cfg.n_active * code:
                raise ValueError("zero-sum bridge changed the mean nominal command")
            examples.append(
                {
                    "code_units": code,
                    "dem_state": state,
                    "main_counts": command.main_counts[0].astype(int).tolist(),
                    "sub_counts": command.sub_counts[0].astype(int).tolist(),
                    "slot_units": slot_units.astype(int).tolist(),
                    "main_masks_hex": [_hex_mask(row) for row in main[0]],
                    "sub_masks_hex": [_hex_mask(row) for row in sub[0]],
                    "main_order": command.main_order[0].tolist(),
                    "sub_order": command.sub_order[0].tolist(),
                    "sum_units": int(slot_units.sum()),
                    "mean_units": float(slot_units.mean()),
                }
            )
    codes = np.arange(-2, 3, dtype=np.int64)
    modes = {}
    for mode in ("off", "sampling", "quantizer"):
        current = Config.rtl_structural(
            dem_enable=True, dither_mode=mode, enable_sampling_noise=False
        )
        sample = capture(
            current,
            lambda time_s: np.zeros_like(time_s),
            len(codes),
            np.random.default_rng(42),
            dither_codes=None if mode == "off" else codes,
        )
        adjustment = dither_transfer_code(
            current,
            sample.dither,
            step_rdac=current.nominal_rdac_step,
            step_coarse=current.delta1,
            dither_code_sampling=sample.dither_code,
        )
        d_q = sample.x_sadc - sample.u1 - sample.n_S
        d_r = sample.x_rdac - sample.u1 - sample.n_R
        correction = (
            sample.dither_code * current.nominal_rdac_step
            if mode == "sampling"
            else np.asarray(sample.rdac_dither)
        )
        modes[mode] = {
            "alpha": current.dither_alpha,
            "d_q_v": d_q.tolist(),
            "d_r_v": d_r.tolist(),
            "command_adjustment_units": adjustment.astype(int).tolist(),
            "command_at_fixed_coarse_176": (176 + adjustment).astype(int).tolist(),
            "digital_correction_v": correction.tolist(),
        }
    sampling_cfg = Config.rtl_structural(dither_mode="sampling")
    rails, alpha = _sampling_connections(sampling_cfg, codes)
    modes["sampling"]["rail_positive"] = rails.tolist()
    modes["sampling"]["alpha_from_charge_terms"] = alpha
    dual = Config.paper_literal(dither_mode="quantizer", enable_sampling_noise=False)
    d_q = codes * dual.nominal_rdac_step
    dual_adjustment = dither_transfer_code(
        dual, d_q, step_rdac=dual.nominal_rdac_step, step_coarse=dual.delta1
    )
    inventory = json.loads((ROOT / "mechanism_inventory.json").read_text())
    registered = {row["id"]: row for row in inventory["mechanisms"]}
    result = {
        "artifact": "randomization_contract",
        "schema_version": 1,
        "generated_by": "docs/readme/make_randomization_figures.py",
        "git_commit_at_generation": subprocess.check_output(
            ["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True
        ).strip(),
        "source_sha256": {
            path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest() for path in SOURCE_FILES
        },
        "verification_scope": "Decoder truth tables and nominal invariants only; not an ADC measurement, Python/RTL bit-match or SNDR result.",
        "profile": {
            "name": "Config.rtl_structural(dem_enable=True)",
            "n_slices": cfg.n_slices,
            "n_active": cfg.n_active,
            "n_main": cfg.dac_n_main,
            "n_sub": cfg.dac_n_sub,
            "levels": cfg.dac_levels,
            "step_v": cfg.nominal_rdac_step,
            "nominal_beta": 1 / cfg.dac_n_sub,
            "sampling_D": sampling_cfg.dither_units_range,
            "sampling_units_per_slice": sampling_cfg.dither_units_total,
            "sampling_sub_unit_ids": list(
                range(cfg.dac_n_sub - sampling_cfg.dither_units_total, cfg.dac_n_sub)
            ),
        },
        "allocator": {
            "seed": cfg.conversion.rtl_shuffle_seed,
            "startup_invalid_advance_not_shown": True,
            "sample_ids": list(range(2, 2 + len(plan.conv))),
            "conversion_ids": plan.conv.tolist(),
            "acquisition_ids": plan.acq.tolist(),
            "same_cycle_disjoint": True,
            "acquisition_to_next_conversion": True,
        },
        "dem_examples": examples,
        "dither_codes": codes.tolist(),
        "structural_dither": modes,
        "generic_dual_port": {
            "profile": "Config.paper_literal(dither_mode='quantizer')",
            "transfer_model": dual.dither_transfer_model,
            "rdac_to_quantizer_ratio": dual.dither_rdac_ratio,
            "d_q_v": d_q.tolist(),
            "d_r_v": (dual.dither_rdac_ratio * d_q).tolist(),
            "command_adjustment_units": dual_adjustment.astype(int).tolist(),
            "scope": "Explicit ideal Python dual-port assumption, absent from the structural range profile; not proof of the paper's complete 2-bit enhancement.",
        },
        "registered_mechanisms": {
            name: {
                "evidence_source": registered[name]["evidence_source"],
                "implementation_degree": registered[name]["implementation_degree"],
            }
            for name in ("M04", "M05", "M06", "X02")
        },
        "limits": [
            "63+8 geometry, PRNG, state encoding and zero-sum bridge are project implementation contracts; this does not reconstruct a proprietary switch matrix.",
            "DEM emits switch assignments without measuring capacitor weights. Learned frozen weights are a separate calibration input.",
            "Sampling rails belong to fixed physical sub-units 4..7 in each active slice, independently of the DEM conversion masks.",
            "Dither injection is downstream of the full-input sample-domain chopper. Each raw is reconstructed with its own masks and known injection before CAAZ pairing.",
        ],
    }
    return result


def validate_contract(data: dict) -> None:
    """拒绝过期源码摘要及与新译码结果不同的保留数值。"""
    if data.get("schema_version") != 1:
        raise ValueError("unknown randomization-contract schema")
    if set(data["source_sha256"]) != set(SOURCE_FILES):
        raise ValueError("the figure source-hash inventory is incomplete or has unknown paths")
    for path, expected in data["source_sha256"].items():
        if hashlib.sha256((ROOT / path).read_bytes()).hexdigest() != expected:
            raise ValueError(f"figure data are stale relative to {path}; regenerate without --data")
    current = collect_contract()
    # A later documentation-only commit need not invalidate identical source
    # hashes. The retained commit still identifies the generation snapshot.
    changed = [
        key
        for key in current
        if key != "git_commit_at_generation" and data.get(key) != current[key]
    ]
    changed.extend(sorted(set(data) - set(current)))
    if changed:
        raise ValueError(f"retained figure values differ from current decoders: {changed}")


def _setup() -> None:
    for font in ("PingFang SC", "Hiragino Sans GB", "STHeiti", "Arial Unicode MS"):
        try:
            font_manager.findfont(font, fallback_to_default=False)
            break
        except ValueError:
            continue
    else:
        raise RuntimeError("a Chinese font is required; no font is installed by this script")
    plt.rcParams.update(
        {
            "font.sans-serif": [font, "DejaVu Sans"],
            "font.size": 10,
            "axes.labelsize": 10,
            "axes.titlesize": 12,
            "axes.unicode_minus": False,
            "text.color": INK,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "svg.fonttype": "path",
            "svg.hashsalt": "sar-adc-randomization-contract-v1",
        }
    )


def _save(fig, out: Path, name: str) -> None:
    for suffix in ("png", "svg"):
        fig.savefig(
            out / f"{name}.{suffix}",
            dpi=220,
            facecolor="white",
            metadata={"Creator": "SAR ADC project original illustration", "Date": None}
            if suffix == "svg"
            else {"Software": "SAR ADC project original illustration"},
        )
    plt.close(fig)


def _section(ax, title: str) -> None:
    ax.set_title(title, loc="left", pad=15, color=INK, fontweight=600)


def _arrow(ax, start, end, color=BLUE, width=1.4) -> None:
    ax.add_patch(
        FancyArrowPatch(start, end, arrowstyle="-|>", mutation_scale=11, color=color, lw=width)
    )


def _block(ax, x, y, width, height, title, detail, color=BLUE) -> None:
    ax.add_patch(
        FancyBboxPatch(
            (x, y),
            width,
            height,
            boxstyle=f"round,pad=0.025,rounding_size={min(0.10, height / 5)}",
            facecolor="white",
            edgecolor=color,
            lw=1.15,
        )
    )
    ax.text(
        x + width / 2,
        y + height * 0.69,
        title,
        ha="center",
        va="center",
        weight=600,
        fontsize=10,
    )
    ax.text(
        x + width / 2, y + height * 0.29, detail, ha="center", va="center", fontsize=9, color=MUTED
    )


def draw_dem(data: dict, out: Path) -> None:
    """显示物理选择、译码选通及零和槽位计数。"""
    fig = plt.figure(figsize=(14, 8.5))
    fig.text(0.045, 0.953, "DEM：改变物理分配；位权学习另行完成", fontsize=20, weight=600)
    fig.text(
        0.045,
        0.915,
        "实际 allocator + split_switch_command 的固定码真值；63 main / 8 sub 是本工程结构合同",
        color=MUTED,
        fontsize=11,
    )
    grid = fig.add_gridspec(
        2,
        2,
        left=0.08,
        right=0.965,
        top=0.845,
        bottom=0.25,
        height_ratios=(1, 1.13),
        width_ratios=(1.65, 1),
        hspace=0.55,
        wspace=0.28,
    )
    ax = fig.add_subplot(grid[0, :])
    _section(ax, "A  18 个物理 slice：8 个转换、8 个采集、2 个空闲")
    alloc = data["allocator"]
    for row, (conv, acq) in enumerate(
        zip(alloc["conversion_ids"], alloc["acquisition_ids"], strict=True)
    ):
        for physical in range(data["profile"]["n_slices"]):
            color = BLUE if physical in conv else ORANGE if physical in acq else PALE
            ax.add_patch(Rectangle((physical - 0.45, row - 0.39), 0.9, 0.78, color=color, lw=0))
    ax.set(
        xlim=(-0.6, 17.6),
        ylim=(7.6, -0.65),
        xticks=np.arange(18),
        yticks=np.arange(8),
        yticklabels=[f"raw ID {value}" for value in alloc["sample_ids"]],
    )
    ax.xaxis.tick_top()
    ax.tick_params(axis="both", length=0, pad=5, labelsize=9)
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.text(
        0.0,
        -0.14,
        "同周期：转换组与采集组无交集。下一周期：上一行采集组成为转换组；启动预充未显示。",
        transform=ax.transAxes,
        color=MUTED,
        fontsize=10,
    )
    for i, (label, color) in enumerate((("转换", BLUE), ("采集", ORANGE), ("空闲", PALE))):
        ax.add_patch(
            Rectangle(
                (0.69 + i * 0.09, 1.12),
                0.02,
                0.055,
                transform=ax.transAxes,
                facecolor=color,
                clip_on=False,
                edgecolor=LINE if i == 2 else "none",
            )
        )
        ax.text(0.715 + i * 0.09, 1.148, label, transform=ax.transAxes, va="center", fontsize=10)

    masks = fig.add_subplot(grid[1, 0])
    _section(masks, "B  固定逻辑码，改变 SID：slot 0 的实际物理 mask")
    for row, example in enumerate(data["dem_examples"]):
        y = 3 - row
        for key, width, offset, color in (
            ("main_masks_hex", 63, 0, BLUE),
            ("sub_masks_hex", 8, 65, ORANGE),
        ):
            for physical, selected in enumerate(_bits(example[key][0], width)):
                masks.add_patch(
                    Rectangle(
                        (offset + physical, y - 0.32),
                        0.92,
                        0.64,
                        facecolor=color if selected else PALE,
                        edgecolor="white",
                        lw=0.15,
                    )
                )
        masks.text(
            -2,
            y,
            f"{example['code_units']} / {example['dem_state']}",
            ha="right",
            va="center",
            fontsize=10,
        )
        masks.text(
            77,
            y,
            f"{example['main_counts'][0]} / {example['sub_counts'][0]}",
            ha="center",
            va="center",
            fontsize=10,
        )
    masks.set(xlim=(-2, 83), ylim=(-0.65, 3.80))
    masks.set_yticks([])
    masks.set_xticks([0, 16, 32, 48, 62, 65, 72], ["0", "16", "32", "48", "62", "0", "7"])
    masks.tick_params(axis="x", length=2, pad=3, labelsize=9)
    masks.text(-2, 3.66, "code / SID", ha="right", fontsize=9, color=MUTED)
    masks.text(31, 3.66, "main：物理单元 0…62", ha="center", fontsize=9, color=BLUE)
    masks.text(69, 3.66, "sub", ha="center", fontsize=9, color=ORANGE)
    masks.text(77, 3.66, "m / s", ha="center", fontsize=9, color=MUTED)
    masks.text(
        0,
        -0.62,
        "着色为接 +VFS；浅色接 −VFS。各行同一个 slot；不对应上图的时间行。",
        fontsize=9,
        color=MUTED,
    )
    for spine in masks.spines.values():
        spine.set_visible(False)

    bridge = fig.add_subplot(grid[1, 1])
    _section(bridge, "C  SID433 零和 bridge（4 个 +1、4 个 −1）")
    example = next(
        r for r in data["dem_examples"] if r["code_units"] == 176 and r["dem_state"] == 433
    )
    delta = np.array(example["slot_units"]) - example["code_units"]
    x = np.arange(len(delta))
    bridge.bar(x, delta, width=0.65, color=[BLUE if v > 0 else ORANGE for v in delta])
    bridge.axhline(0, color=INK, lw=0.8)
    for slot, value in enumerate(delta):
        bridge.text(
            slot,
            value + (0.13 if value > 0 else -0.16),
            f"{value:+d}",
            ha="center",
            va="bottom" if value > 0 else "top",
            fontsize=10,
        )
    bridge.set(
        xlim=(-0.6, 7.6),
        ylim=(-1.85, 1.85),
        xticks=x,
        yticks=[-1, 0, 1],
        xlabel="活动 slot（不是物理 slice ID）",
        ylabel="相对逻辑码的 RDAC 单位数",
    )
    bridge.tick_params(length=3, labelsize=9)
    bridge.grid(axis="y", alpha=0.25, lw=0.5)
    for row, code in enumerate((176, 179)):
        current = next(
            r for r in data["dem_examples"] if r["code_units"] == code and r["dem_state"] == 433
        )
        low, high = sorted(set(current["slot_units"]))
        fig.text(
            0.645,
            0.185 - row * 0.033,
            f"code {code}：4×{low} + 4×{high} = {current['sum_units']} = 8×{code}",
            fontsize=10,
            color=INK,
        )
    fig.add_artist(
        Rectangle(
            (0.04, 0.043), 0.93, 0.083, transform=fig.transFigure, facecolor=PALE, edgecolor="none"
        )
    )
    fig.text(
        0.055,
        0.094,
        "DEM 不测量电容权重。名义和守恒，不等于失配后的物理电荷相等；重构仍需实际 mask 与独立训练后冻结的位权。",
        fontsize=11,
        weight=600,
    )
    fig.text(
        0.055,
        0.064,
        "零和 bridge 是工程候选，不等同论文完整开关矩阵。本图由现源码计算并原创绘制，非原文图复制。",
        fontsize=10,
        color=MUTED,
    )
    _save(fig, out, "dem_implementation")


def _table(ax, columns, rows, widths, font_size=10) -> None:
    """按显式列宽绘制紧凑比较表。"""
    ax.axis("off")
    table = ax.table(
        cellText=rows,
        colLabels=columns,
        colWidths=widths,
        cellLoc="center",
        loc="center",
        bbox=[0, 0, 1, 1],
    )
    table.auto_set_font_size(False)
    table.set_fontsize(font_size)
    for (row, _), cell in table.get_celld().items():
        cell.set_edgecolor(LINE)
        cell.set_linewidth(0.5)
        cell.set_facecolor(PALE if row == 0 else "white")
        if row == 0:
            cell.set_text_props(weight=600, color=INK)


def draw_dither(data: dict, out: Path) -> None:
    """分别显示采样参考轨、量化器范围及理想通用双端口。"""
    fig = plt.figure(figsize=(14, 10.0))
    fig.text(
        0.045,
        0.955,
        "Dither：后置注入、同 raw 重构，最后才做 CAAZ pair",
        fontsize=20,
        weight=600,
    )
    fig.text(
        0.045,
        0.920,
        "sampling 与 quantizer/range 互斥；已知 dither 不等于新增 SAR 判决位",
        fontsize=11,
        color=MUTED,
    )
    flow = fig.add_axes((0.04, 0.585, 0.93, 0.285))
    flow.set(xlim=(0, 14), ylim=(-0.15, 4.5))
    flow.axis("off")
    fig.text(
        0.055,
        0.878,
        "A  先按每笔 raw 的真实注入和 mask 重构，再合并偶/奇",
        fontsize=12,
        weight=600,
    )
    flow.text(0.0, 2.58, "x", fontsize=14, va="center")
    _arrow(flow, (0.25, 2.58), (0.72, 2.58))
    _block(flow, 0.75, 2.10, 1.28, 0.98, "输入 chop", r"$s_n=+1/-1$")
    _arrow(flow, (2.05, 2.58), (2.42, 2.58))
    flow.plot([2.42, 2.42, 2.92], [2.58, 3.48, 3.48], color=BLUE, lw=1.4)
    _block(flow, 2.95, 3.0, 1.87, 0.98, "粗量化 SADC", r"$\alpha s_nx+d_Q$")
    _arrow(flow, (4.85, 3.48), (5.50, 3.48))
    flow.text(5.1, 3.65, r"$c_1$", fontsize=11)
    _block(flow, 5.53, 3.0, 2.08, 0.98, "译码 / DEM", r"$k=c_1+\Delta k$")
    _arrow(flow, (6.58, 2.98), (6.58, 1.99))
    _block(flow, 5.34, 0.99, 2.5, 0.98, "RDAC 实际 mask", r"$V_D(k)$")
    flow.plot([2.42, 2.42, 8.35], [2.58, 0.48, 0.48], color=BLUE, lw=1.4)
    _arrow(flow, (8.35, 0.48), (8.35, 1.23))
    flow.text(3.15, 0.67, r"保存电荷：$\alpha s_nx+d_R$", fontsize=11, color=BLUE)
    _arrow(flow, (3.85, 4.36), (3.85, 4.0), ORANGE)
    flow.text(4.11, 4.18, r"$d_Q$", fontsize=12, color=ORANGE)
    _arrow(flow, (4.77, -0.08), (4.77, 0.45), ORANGE)
    flow.text(2.75, -0.03, r"$d_R$ / sampling rails", fontsize=10, color=ORANGE)
    flow.add_patch(Ellipse((8.35, 1.48), 0.46, 0.69, facecolor="white", edgecolor=BLUE, lw=1.3))
    flow.text(8.05, 1.58, "−", color=BLUE, fontsize=11)
    flow.text(8.45, 1.12, "+", color=BLUE, fontsize=11)
    _arrow(flow, (7.86, 1.48), (8.1, 1.48))
    _arrow(flow, (8.59, 1.48), (8.92, 1.48))
    _block(flow, 8.95, 0.99, 1.56, 0.98, "RA + ADC2", r"$v_2$")
    _arrow(flow, (10.53, 1.48), (10.96, 1.48))
    _block(
        flow,
        10.99,
        0.73,
        2.68,
        1.50,
        "逐 raw 完整重构",
        "先减 d_corr，再除 α\n实际 mask + 冻结位权",
        TEAL,
    )
    _arrow(flow, (12.33, 2.26), (12.33, 2.97), TEAL)
    _block(
        flow,
        10.99,
        3.0,
        2.68,
        0.98,
        "CAAZ 非重叠 pair",
        r"$(\hat u_{even}-\hat u_{odd})/2$",
        PURPLE,
    )
    flow.text(
        8.9, 3.36, "dither 在 chop 后\n重构时保留原符号", color=ORANGE, ha="center", fontsize=10
    )

    mode_ax = fig.add_axes((0.055, 0.385, 0.905, 0.155))
    mode_ax.text(
        0,
        1.18,
        "B  结构模式的三份名义合同（Δ 为 RDAC 单位步长）",
        transform=mode_ax.transAxes,
        fontsize=12,
        weight=600,
    )
    alpha = data["structural_dither"]["sampling"]["alpha"]
    _table(
        mode_ax,
        ["模式", "SADC 注入 d_Q", "名义保存注入 d_R", "RDAC 命令改动 Δk", "逐 raw 名义修正"],
        [
            ["off", "0", "0", "0", "不扣；α=1"],
            [
                "sampling（D=2）",
                "0",
                "d·Δ；每 slice 固定 4 个子单元",
                "+d",
                f"减 d·Δ；α={alpha:.7f}",
            ],
            ["quantizer / range", "d·Δ", "0", "−d", "不扣 d_Q；α=1"],
        ],
        [0.18, 0.16, 0.23, 0.18, 0.25],
        10,
    )

    truth = fig.add_axes((0.055, 0.145, 0.51, 0.175))
    truth.text(
        0,
        1.19,
        "C  固定 c1=176 的五档译码真值（不是固定模拟输入）",
        transform=truth.transAxes,
        fontsize=11.5,
        weight=600,
    )
    modes = data["structural_dither"]
    rows = []
    for index, code in enumerate(data["dither_codes"]):
        rails = " ".join(
            "+" if value else "−" for value in modes["sampling"]["rail_positive"][index]
        )
        rows.append(
            [
                f"{code:+d}",
                rails,
                modes["sampling"]["command_at_fixed_coarse_176"][index],
                modes["quantizer"]["command_at_fixed_coarse_176"][index],
                f"{1000*modes['sampling']['digital_correction_v'][index]:+.5f}",
            ]
        )
    _table(
        truth,
        ["dcode", "rails（sub 4…7）", "k sampling", "k range", "d_R [mV]"],
        rows,
        [0.10, 0.27, 0.18, 0.17, 0.28],
        9.5,
    )

    dual_ax = fig.add_axes((0.60, 0.145, 0.36, 0.175))
    dual_ax.axis("off")
    dual_ax.text(
        0,
        1.19,
        "D  另一个 Python 合同：generic dual_port",
        fontsize=11.5,
        weight=600,
        transform=dual_ax.transAxes,
    )
    dual = data["generic_dual_port"]
    index = data["dither_codes"].index(1)
    dual_ax.text(0, 0.90, f"d=+1：d_Q={1000*dual['d_q_v'][index]:.5f} mV", fontsize=11)
    dual_ax.text(
        0,
        0.65,
        f"d_R={1000*dual['d_r_v'][index]:.3f} mV；Δk={dual['command_adjustment_units'][index]:+d}",
        fontsize=11,
        color=ORANGE,
    )
    dual_ax.text(
        0,
        0.38,
        "d_R=4d_Q，逐 raw 扣 d_R。\n结构 range 没有这个 4× 注入端口。",
        fontsize=10.5,
        linespacing=1.7,
    )
    dual_ax.text(
        0,
        0.01,
        "显式理想注入假设；不能据此宣布\n结构 RTL 已闭合论文的“2b 增强”。",
        fontsize=10,
        color=MUTED,
        linespacing=1.7,
    )
    step_mv = data["profile"]["step_v"] * 1000
    alpha_fraction = Fraction(alpha).limit_denominator(4096)
    fig.text(
        0.055,
        0.091,
        f"本图 Δ={step_mv:.5f} mV，sampling α={alpha_fraction}（由同一组物理采样连接计算）；名义重构式：û=(V_D+v₂/Ĝ−d_corr)/α。",
        fontsize=10.5,
        color=INK,
    )
    fig.text(
        0.055,
        0.061,
        "实际冻结位权实现按每个物理 mask/rail 求和。这里验证的是码与单位合同，不能用 SNDR 代替逐 raw 对齐或 Python/RTL 码匹配。",
        fontsize=10,
        color=MUTED,
    )
    fig.text(
        0.055,
        0.035,
        "本图由现源码计算并原创绘制，非原文图复制；CAAZ 是默认关闭的样本域扩展，不代表动态 chopper 或模拟 AZ 电路实现。",
        fontsize=10,
        color=MUTED,
    )
    _save(fig, out, "dither_implementation")


def main() -> None:
    """在指定仓库或库外根目录下写新的 data/fig 子目录。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out", required=True, type=Path, help="output root containing data/ and fig/"
    )
    parser.add_argument(
        "--data", type=Path, help="reuse a retained JSON after checking source hashes"
    )
    parser.add_argument("--only", choices=("all", "dem", "dither"), default="all")
    args = parser.parse_args()
    data = json.loads(args.data.read_text()) if args.data else collect_contract()
    validate_contract(data)
    (args.out / "data").mkdir(parents=True, exist_ok=True)
    (args.out / "fig").mkdir(parents=True, exist_ok=True)
    (args.out / "data/randomization_contract.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    )
    _setup()
    if args.only in ("all", "dem"):
        draw_dem(data, args.out / "fig")
    if args.only in ("all", "dither"):
        draw_dither(data, args.out / "fig")
    print(
        json.dumps(
            {"out": str(args.out), "figures": args.only, "scope": data["verification_scope"]},
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
