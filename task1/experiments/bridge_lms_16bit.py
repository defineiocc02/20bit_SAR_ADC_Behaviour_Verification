# =============================================================================
# 文件名     : bridge_lms_16bit.py
# 模块名     : bridge_lms_16bit
# 版本       : v1.0
# 功能       : 只更换理想 ADC2 位数，重跑严格无 DEM 与 Fig.21 独立训练对照。
#
# 设计说明   :
#   * 承担 行为级实验与图件生成 中的一环；输入输出的物理口径与单位以同目录 SPEC/README 为准。
# -----------------------------------------------------------------------------
# 变更记录
# -----------------------------------------------------------------------------
# 初始版本，暂无变更记录。
# ===========================================================================

"""只更换理想 ADC2 位数，重跑严格无 DEM 与 Fig.21 独立训练对照。"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, replace
from importlib.metadata import version
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

from adi_model.capacitors import ExperimentConfig, cap_pool
from adi_model.codes import OUTPUT_BITS
from adi_model.hashing import sha256_file
from task1.experiments.adctoolbox_calibration import CalibrationConfig, _spectrum, _write_summary
from task1.experiments.bridge_lms_comparison import ARM_LABELS, _arm
from task1.experiments.run_effects import BLUE, GRAY, ROOT
from task1.model.codebook import codebook

CANDIDATE_ADC2_BITS = 16
REFERENCE_NAMES = ("gain_low", "gain_high", "training", "holdout")


# =========================================================================
# 1. 函数组：_check_same_analog_capture / _plot
# =========================================================================
# ------------------------------------------------------------------------
# ------------------------------------------------------------------------
def _check_same_analog_capture(previous: np.lib.npyio.NpzFile, current: dict) -> int:
    # 物理轨迹必须完全相同。旧 ADC2 码和旧校准系数不进入新训练。
    """比较二级位数时必须保持输入和物理失配池相同。

    比较二级位数时必须保持输入和物理失配池相同。
    前级片选、DEM 状态和转换掩码属于模拟捕获身份，不能随量化位数重新抽取。
    否则观察到的变化同时包含制造或调度差异，不能归因于 ADC2 位数。

    Args:
        previous: 作为捕获基线的原始归档。
        current: 当前实验返回的观测字段。
    Returns:
        已检查的同源字段数量。

    该检查只约束所列捕获字段，不证明电容或模拟宏模型完全准确。
    新增影响残差的字段时须扩展比较清单，不能只保留旧计数。
    二级提高位数后的数字改善也不能代替真实 ADC2 的噪声和非线性预算。
    """
    analog_suffixes = (
        "_plan",
        "_coarse_code",
        "_main_mask",
        "_sub_mask",
        "_horizontal",
        "_vertical",
        "_correction_q32",
        "_residue_v",
    )
    checked = 0
    for arm, fields in current.items():
        for field, values in fields.items():
            if field.endswith(analog_suffixes):
                np.testing.assert_array_equal(previous[f"{arm}_{field}"], values)
                checked += 1
    return checked


# ------------------------------------------------------------------------
def _plot(
    path: Path,
    old: dict,
    summary: dict,
    records: dict,
    experiment: ExperimentConfig,
) -> None:
    fig, axes = plt.subplot_mosaic(
        [["spectrum", "spectrum"], ["sndr", "sfdr"]],
        figsize=(10.9, 6.5),
        layout="constrained",
    )
    fig.suptitle(
        f"ADC2 resolution sensitivity | ±100 mV | unit-cap σ "
        f"{100 * experiment.mismatch_sigma_unit:g}%",
        fontsize=15,
        fontweight="bold",
    )
    x = np.arange(2)
    for name, color, offset in zip(ARM_LABELS, (GRAY, BLUE), (-0.18, 0.18), strict=True):
        data = _spectrum(records[name]["holdout_calibrated_v"], experiment)["plot_data"]
        axes["spectrum"].plot(
            data["freq"] / 1e6,
            data["power_spectrum_db_plot"],
            lw=0.75,
            color=color,
            label=ARM_LABELS[name],
        )
        for axis, key, label in (("sndr", "sndr_db", "SNDR"), ("sfdr", "sfdr_db", "SFDR")):
            values = [run["arms"][name]["holdout"][key] for run in (old, summary)]
            bars = axes[axis].bar(x + offset, values, width=0.34, color=color)
            axes[axis].bar_label(bars, fmt="%.2f", padding=2, fontsize=8)
            axes[axis].set(
                xticks=x,
                xticklabels=("12-bit reference", "16-bit candidate"),
                ylabel=f"{label} (dB)",
            )
    axes["spectrum"].set(
        title="16-bit candidate: independently calibrated holdout",
        xlabel="Frequency (MHz)",
        ylabel="Amplitude (dBFS/bin)",
        ylim=(-190, 2),
    )
    axes["spectrum"].legend(loc="upper right", fontsize=8)
    for ax in axes.values():
        ax.grid(alpha=0.23)
    for axis, metric in (("sndr", "sndr_db"), ("sfdr", "sfdr_db")):
        peak = max(
            run["arms"][name]["holdout"][metric] for run in (old, summary) for name in ARM_LABELS
        )
        axes[axis].set_ylim(0, peak + 13)
    fig.savefig(path, dpi=165, facecolor="white")
    plt.close(fig)


# =========================================================================
# 2. 函数组：run / main
# =========================================================================
# -------------------------------------------------------------------------
# 用 12 位回执验证模拟轨迹相同，再独立训练两条 16 位链路。
# 运行候选后级位数敏感性实验。
# ------------------------------------------------------------------------
def run(reference_dir: Path, out: Path) -> dict:
    """用 12 位回执验证模拟轨迹相同，再独立训练两条 16 位链路。"""
    if reference_dir.resolve() == out.resolve():
        raise ValueError("out must be separate from the 12-bit reference directory")
    reference_json = reference_dir / "summary.json"
    reference_npz = reference_dir / "observations.npz"
    old = json.loads(reference_json.read_text(encoding="utf-8"))
    if (
        old["schema"] != "task1_fig21_bridge_nlms_comparison_v1"
        or old["status"] != "completed"
        or old["calibration"]["adc2_bits"] != 12
        or old["calibration"]["adc2_half_range_v"] != 0.1
    ):
        raise ValueError("reference must be a completed Fig. 21 comparison at 12-bit ADC2 ±100 mV")
    for path, sha in old["source_sha256"].items():
        if sha256_file(ROOT / path) != sha:
            raise ValueError(f"reference source hash changed: {path}")

    experiment = ExperimentConfig(**old["experiment"])
    cfg = replace(CalibrationConfig(**old["calibration"]), adc2_bits=CANDIDATE_ADC2_BITS)
    nominal, pool = cap_pool(experiment)
    minimum = {name: float(np.min(getattr(pool, name))) for name in ("main", "sub", "bridge")}
    if minimum != old["minimum_physical_cap_f"]:
        raise ValueError("recreated physical mismatch pool does not match the 12-bit receipt")
    threshold_v = codebook(nominal, experiment)

    arms = {}
    records = {}
    with np.load(reference_npz, allow_pickle=False) as previous:
        np.testing.assert_array_equal(previous["nominal_threshold_v"], threshold_v)
        references = {name: previous[f"reference_{name}_v"] for name in REFERENCE_NAMES}
        for name, bridge in zip(ARM_LABELS, (False, True), strict=True):
            arms[name], records[name] = _arm(
                pool, nominal, threshold_v, references, experiment, cfg, bridge=bridge
            )
        compared_analog_arrays = _check_same_analog_capture(previous, records)

    complete = all(arm["status"] == "completed" for arm in arms.values())
    comparison = None
    width_improvement = None
    if complete:
        baseline = arms["fixed_ab_lms_only"]["holdout"]
        bridge = arms["fig21_bridge_lms"]["holdout"]
        comparison = {key: bridge[key] - baseline[key] for key in ("sndr_db", "sfdr_db")}
        comparison["joint_improvement_this_die"] = all(value > 0 for value in comparison.values())
        width_improvement = {
            arm: {
                key: result["holdout"][key] - old["arms"][arm]["holdout"][key]
                for key in ("sndr_db", "sfdr_db")
            }
            for arm, result in arms.items()
        }

    step_v = 2 * cfg.adc2_half_range_v / (1 << cfg.adc2_bits)
    output_step_v = 2 * experiment.full_scale_peak_v / (1 << OUTPUT_BITS)
    reference_step_v = old["adc2_step_v"]
    summary = {
        "schema": "task1_fig21_bridge_nlms_16bit_sensitivity_v1",
        "status": "completed" if complete else "adc2_range_blocked",
        "adc2_window_basis": "explicit_ideal_16bit_candidate",
        "model_scope": "后级量化位数敏感性，输入等效 ±100 mV 理想 ADC2，时偏及热噪声关闭。",
        "experiment": old["experiment"],
        "calibration": asdict(cfg),
        "minimum_physical_cap_f": minimum,
        "adc2_step_v": step_v,
        "output_bits": OUTPUT_BITS,
        "output_lsb_v": output_step_v,
        "adc2_step_in_output_lsbs": step_v / output_step_v,
        "reference_adc2_step_v": reference_step_v,
        "reference_adc2_step_in_output_lsbs": reference_step_v / output_step_v,
        "ideal_adc2_quantization_rms_v": step_v / np.sqrt(12),
        "ideal_adc2_quantization_only_sndr_db": float(
            20 * np.log10((experiment.tone_peak_v / np.sqrt(2)) / (step_v / np.sqrt(12)))
        ),
        "training_frequency_hz": old["training_frequency_hz"],
        "evaluation_frequency_hz": old["evaluation_frequency_hz"],
        "checked_equal_analog_arrays": compared_analog_arrays,
        "reference_artifact_sha256": {
            "summary.json": sha256_file(reference_json),
            "observations.npz": sha256_file(reference_npz),
        },
        "phase_contract": old["phase_contract"],
        "digital_nominal_correction": old["digital_nominal_correction"],
        "fig21_feature_contract": old["fig21_feature_contract"],
        "arms": arms,
        "bridge_minus_fixed": comparison,
        "candidate_16bit_minus_reference_12bit": width_improvement,
        "analysis": {"library": "ADCToolbox", "version": version("adctoolbox")},
        "source_sha256": {
            **old["source_sha256"],
            "task1/experiments/bridge_lms_16bit.py": sha256_file(Path(__file__)),
        },
    }
    out.mkdir(parents=True, exist_ok=True)
    if complete:
        plt.switch_backend("Agg")
        _plot(out / "bridge_lms_16bit.png", old, summary, records, experiment)
    np.savez_compressed(
        out / "observations.npz",
        nominal_threshold_v=threshold_v,
        **{f"reference_{key}_v": trace for key, trace in references.items()},
        **{
            f"{arm}_{field}": trace
            for arm, fields in records.items()
            for field, trace in fields.items()
        },
    )
    return _write_summary(out, summary)


# ------------------------------------------------------------------------
def main() -> None:
    """运行候选后级位数敏感性实验。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference-dir", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    summary = run(args.reference_dir, args.out)
    print(
        json.dumps(
            {
                "status": summary["status"],
                "bridge_minus_fixed": summary["bridge_minus_fixed"],
                "holdout": {key: arm["holdout"] for key, arm in summary["arms"].items()},
            },
            indent=2,
        )
    )
    if summary["status"] != "completed":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
