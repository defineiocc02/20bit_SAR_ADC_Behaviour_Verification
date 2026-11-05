#!/usr/bin/env python3
"""复验DEM使用偏置：固定k10/512状态与真实分配后的物理计数分开。

固定原参数N=16384、seed42；两种物理统计都运行完整Python主路径，使用实际
conv_slice_ids、sid和RDAC单位码，再调用生产译码器生成mask，不替换为理想随机组。
固定slot的状态计数不等于物理slice统计；所有物理概率都以该slice实际活动次数归一。
输出新目录的dem_usage.json、dem_usage.png/svg；不评估SNDR或校准后性能，不安装依赖。
需要工程Python依赖、Matplotlib及可用中文字体。已有--out目录直接拒绝。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import matplotlib
import numpy as np

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from adi_model import Config  # noqa: E402
from adi_model.dem import split_switch_command  # noqa: E402
from adi_model.pipeline import run_pipeline  # noqa: E402

SOURCES = (
    "config",
    "dem",
    "mapper",
    "timing",
    "scheduler",
    "slice_pool",
    "pipeline",
    "pipeline_engine",
    "dac_arch",
)
BLUE, ORANGE, TEAL, GRAY = "#27658e", "#cf8535", "#218578", "#81909c"


def _masks(cfg, code, sid):
    """按生产顺序／计数译码器解析物理单元选通。"""
    command = split_switch_command(cfg, code, sid)
    masks = []
    for size, order, count in (
        (cfg.dac_n_main, command.main_order, command.main_counts),
        (cfg.dac_n_sub, command.sub_order, command.sub_counts),
    ):
        take = np.arange(size)[None, None, :] < count[..., None]
        physical = np.zeros(take.shape, dtype=np.int64)
        np.put_along_axis(physical, order[:, None, :], take, axis=2)
        if not np.array_equal(physical.sum(axis=2), count):
            raise RuntimeError("integer mask population differs from decoder counts")
        masks.append(physical)
    return command, masks


def collect() -> dict:
    """按原先声明的样本数运行，不训练或搜索参数。"""
    cfg = Config.paper_literal(dem_enable=True, dither_mode="off")
    if (cfg.n_slices, cfg.n_active, cfg.dac_n_main, cfg.dac_n_sub) != (18, 8, 63, 8):
        raise RuntimeError("this probe requires the explicit 18/8 and 63+8 geometry")
    states = np.arange(512, dtype=np.int64)
    fixed = {}
    for bridge in (False, True):
        current = replace(cfg, dem_bridge_enable=bridge)
        command, (main, sub) = _masks(current, np.full(512, 10), states)
        if not np.all((command.main_counts * 8 + command.sub_counts).sum(axis=1) == 80):
            raise RuntimeError("the fixed-code nominal aggregate is not conserved")
        fixed[str(bridge)] = {
            "main_by_slot": main.sum(axis=0).tolist(),
            "sub_by_slot": sub.sum(axis=0).tolist(),
            "nominal_sum_every_state": 80,
        }
    physical = {}
    for name, factory in (
        ("generic_random", Config.paper_literal),
        ("rtl_allocator", Config.rtl_structural),
    ):
        current = factory(
            seed=42,
            dem_enable=True,
            dem_mode="permute",
            dither_mode="off",
            mismatch_enable=False,
            enable_sampling_noise=False,
            ra_enable_noise=False,
            sadc_rdac_gain_mismatch=0,
        )
        n = 16384
        input_v = -current.v_fs + 10.5 * current.nominal_rdac_step
        result = run_pipeline(current, lambda t, value=input_v: np.full_like(t, value), n)
        if not np.all(result.k == 10):
            raise RuntimeError("the constant stimulus no longer produces RDAC code10")
        _, (main, sub) = _masks(current, result.k, result.sid)
        counts = []
        for mask, size in ((main, 63), (sub, 8)):
            total = np.zeros((18, size), dtype=np.int64)
            for slot in range(8):
                np.add.at(total, result.conv_slice_ids[:, slot], mask[:, slot, :])
            counts.append(total)
        exposure = np.bincount(result.conv_slice_ids.ravel(), minlength=18)
        if np.any(exposure == 0):
            raise RuntimeError("a physical slice has no exposure; conditional ratio is undefined")
        sub_probability = counts[1] / exposure[:, None]
        main_probability = counts[0] / exposure[:, None]
        physical[name] = {
            "n": n,
            "seed": 42,
            "k_units": 10,
            "config": current.to_dict(),
            "scheduler_class": "ShuffledScheduler"
            if name == "generic_random"
            else "build_rtl_slice_plan(shuffle=True)",
            "allocator_path": "pipeline_engine.execute_split -> make_scheduler -> ShuffledScheduler -> build_slice_plan(shuffle_causal)"
            if name == "generic_random"
            else "pipeline_engine.execute_split -> build_rtl_slice_plan",
            "physical_exposures": exposure.tolist(),
            "physical_main_counts": counts[0].tolist(),
            "physical_sub_counts": counts[1].tolist(),
            "sub_probability_given_active": sub_probability.tolist(),
            "physical_sub_probability_range": [
                float(sub_probability.min()),
                float(sub_probability.max()),
            ],
            "main56_probability_range": [
                float(main_probability[:, 56].min()),
                float(main_probability[:, 56].max()),
            ],
            "other_main_probability_range": [
                float(np.delete(main_probability, 56, axis=1).min()),
                float(np.delete(main_probability, 56, axis=1).max()),
            ],
            "overflow_count": int(
                np.count_nonzero(result.rdac_over | result.adc2_over | result.ra_sat)
            ),
        }
    paths = [f"src/adi_model/{name}.py" for name in SOURCES] + ["tools/reproduce_dem_usage.py"]
    return {
        "source_head": subprocess.check_output(
            ["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True
        ).strip(),
        "source_worktree_dirty": bool(
            subprocess.check_output(
                ["git", "-C", str(ROOT), "status", "--porcelain"], text=True
            ).strip()
        ),
        "source_sha256": {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in paths},
        "fixed_state_sweep_config": cfg.to_dict(),
        "fixed_512_state_k10": fixed,
        "actual_allocator_fixed_k10": physical,
        "mask_source": "Production split_switch_command of actual Python k/sid; physical IDs come from the same run_pipeline record. No SV capture is claimed.",
        "scope": "Nominal conserved command and conditional physical-unit selection counts; no mismatch calibration or final performance conclusion.",
    }


def draw(data: dict, out: Path) -> None:
    """重绘四种计数视图，阴影范围不是置信区间。"""
    for font in ("PingFang SC", "Noto Sans CJK SC", "Hiragino Sans GB", "Arial Unicode MS"):
        try:
            font_manager.findfont(font, fallback_to_default=False)
            break
        except ValueError:
            continue
    else:
        raise RuntimeError("a Chinese font is required; this tool does not install fonts")
    plt.rcParams.update(
        {
            "font.sans-serif": [font, "DejaVu Sans"],
            "axes.unicode_minus": False,
            "font.size": 10,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "svg.fonttype": "path",
            "svg.hashsalt": "dem-usage-current",
        }
    )
    fig, axes = plt.subplots(2, 2, figsize=(12.8, 7.5))
    fig.subplots_adjust(left=0.075, right=0.97, bottom=0.21, top=0.84, hspace=0.5, wspace=0.28)
    fig.suptitle("DEM 使用统计：名义守恒，不保证物理单元等使用", fontsize=19, weight=600, y=0.965)
    subtitle = "固定 RDAC 码 k=10；512 状态计数与实际 allocator 的 N=16384 / seed42 分开显示"
    fig.text(0.075, 0.90, subtitle, fontsize=11, color="#5a6878")
    axis = axes[0, 0]
    main = data["fixed_512_state_k10"]["False"]["main_by_slot"][0]
    axis.bar(
        np.arange(63), main, color=[ORANGE if i == 56 else BLUE for i in range(63)], width=0.85
    )
    axis.axhline(512 / 63, color=GRAY, lw=0.8, ls="--")
    axis.annotate(
        "unit56 = 16次\n其余62个各8次",
        xy=(56, 16),
        xytext=(25, 15),
        arrowprops={"arrowstyle": "->", "color": ORANGE},
        color=ORANGE,
    )
    axis.set_title("A  固定 slot0，bridge关闭：63主单元的缺格映射")
    axis.set_xlabel("主阵列物理 unit ID")
    axis.set_ylabel("512状态内接 +VFS 的次数")
    axis.set_ylim(0, 19)
    axis = axes[0, 1]
    x = np.arange(8)
    for offset, enabled, color, label in (
        (-0.18, "False", GRAY, "bridge off"),
        (0.18, "True", ORANGE, "bridge on"),
    ):
        axis.bar(
            x + offset,
            data["fixed_512_state_k10"][enabled]["sub_by_slot"][0],
            width=0.36,
            color=color,
            label=label,
        )
    axis.set_title("B  固定 slot0：sid[8] 与子阵列旋转相关")
    axis.set_xlabel("子阵列物理 unit ID")
    axis.set_ylabel("512状态内接 +VFS 的次数")
    axis.set(xticks=x, ylim=(0, 190))
    axis.legend(frameon=False, fontsize=9)
    for unit in (1, 5):
        value = data["fixed_512_state_k10"]["True"]["sub_by_slot"][0][unit]
        axis.text(unit + 0.18, value + 4, str(value), ha="center", color=ORANGE)
    for name, color, label in (
        ("generic_random", BLUE, "generic随机"),
        ("rtl_allocator", TEAL, "RTL allocator合同"),
    ):
        row = data["actual_allocator_fixed_k10"][name]
        count = np.asarray(row["physical_main_counts"])
        axes[1, 0].plot(
            np.arange(18),
            count[:, 56] / np.delete(count, 56, axis=1).mean(axis=1),
            "o-",
            ms=3,
            lw=1,
            color=color,
            label=label,
        )
        p = np.asarray(row["sub_probability_given_active"])
        mean = np.asarray(row["physical_sub_counts"]).sum(axis=0) / sum(row["physical_exposures"])
        axes[1, 1].plot(x, mean, "o-", ms=4, color=color, label=label)
        axes[1, 1].fill_between(x, p.min(axis=0), p.max(axis=0), color=color, alpha=0.16)
    axes[1, 0].axhline(1, color=GRAY, lw=0.8, ls="--")
    axes[1, 0].set_title("C  真实分配后：主unit56偏置仍保留")
    axes[1, 0].set_xlabel("物理 slice ID")
    axes[1, 0].set_ylabel("unit56 / 其他62单元平均使用次数")
    axes[1, 0].set(ylim=(0.8, 2.35), xticks=np.arange(0, 18, 2))
    axes[1, 1].axhline(0.25, color=GRAY, lw=0.8, ls="--")
    axes[1, 1].set_title("D  真实分配后：子单元条件使用概率")
    axes[1, 1].set_xlabel("子阵列物理 unit ID")
    axes[1, 1].set_ylabel("P(接+VFS | 该slice活动)")
    axes[1, 1].set(xticks=x, ylim=(0.15, 0.35))
    for axis in axes[1]:
        axis.legend(frameon=False, fontsize=9)
    for axis in axes.flat:
        axis.grid(axis="y", alpha=0.22, lw=0.5)
    population_note = (
        "C/D 使用真实主路径的物理ID与DEM状态；D阴影为18片slice的min–max，不是置信区间。"
    )
    fig.text(0.075, 0.093, population_note, fontsize=10, color="#5a6878")
    boundary = "每个状态的8-slot名义和始终为80。此图只证明条件使用偏置；校准前后SNDR/最终失配误差须另行验证。"
    fig.text(0.075, 0.056, boundary, fontsize=10.5, weight=600)
    fig.text(
        0.075, 0.025, "本图为实际代码计数的原创绘图，非原文图复制。", fontsize=9, color="#5a6878"
    )
    for extension in ("png", "svg"):
        fig.savefig(
            out / f"dem_usage.{extension}",
            dpi=220,
            facecolor="white",
            metadata={"Date": None} if extension == "svg" else None,
        )
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True, help="New output directory")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    data = collect()
    (args.out / "dem_usage.json").write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    draw(data, args.out)
    print(f"DEM_USAGE_COMPLETE: {args.out}; counts only, no performance conclusion.")


if __name__ == "__main__":
    main()
