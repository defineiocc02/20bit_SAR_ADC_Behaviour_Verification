# =============================================================================
# 文件名     : adctoolbox_calibration.py
# 模块名     : adctoolbox_calibration
# 版本       : v1.0
# 功能       : 比较模块一的电容失配、DEM 与前台 NLMS 校准。
#
# 设计说明   :
#   * 在仓库根目录使用 PYTHONPATH=.:src python -m task1.experiments.adctoolbox_calibration
#   * --out PATH。校准数据为物理片 ID、实际开关位图、理想 ADC2 码及已知前台参考。
# -----------------------------------------------------------------------------
# 变更记录
# -----------------------------------------------------------------------------
# 初始版本，暂无变更记录。
# ===========================================================================

"""比较模块一的电容失配、DEM 与前台 NLMS 校准。

在仓库根目录使用 PYTHONPATH=.:src python -m task1.experiments.adctoolbox_calibration
--out PATH。校准数据为物理片 ID、实际开关位图、理想 ADC2 码及已知前台参考。
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import platform
from dataclasses import asdict, dataclass, replace
from importlib.metadata import version
from pathlib import Path

import matplotlib
import matplotlib.pyplot as plt
import numpy as np

from adi_model.capacitors import (
    MAIN_WEIGHTS,
    SUB_WEIGHTS,
    Capacitors,
    ExperimentConfig,
    cap_pool,
    feedback_charge,
    unpack,
    voltage_equivalent,
)
from adi_model.noise import FrontEndNoise
from task1.experiments.run_effects import BLUE, GRAY, ORANGE, ROOT, schedule_sequence
from task1.model.codebook import codebook, map_record
from task1.model.scheduler import GROUP_SLICES, POOL_SLICES

UNIT_COUNT = len(MAIN_WEIGHTS) + len(SUB_WEIGHTS) - 1
SWITCH_COLUMNS = POOL_SLICES * UNIT_COUNT
MODE_LABELS = {
    "no_dem_no_cal": "DEM off / uncalibrated",
    "dem_only": "DEM on / uncalibrated",
    "lms_only": "DEM off / NLMS",
    "dem_and_lms": "DEM on / NLMS",
}


# =========================================================================
# 1. 函数组：CalibrationConfig / SwitchState / Observation
# =========================================================================
# -------------------------------------------------------------------------
# 前台采集、理想 ADC2 和数字重编码的固定参数。
# 按有序物理切片保存粗码和开关位图。
# 校准器可以读取的原始数字观测。
# ------------------------------------------------------------------------
@dataclass(frozen=True)
class CalibrationConfig:
    """前台采集、理想 ADC2 和数字重编码的固定参数。"""

    adc2_bits: int = 12
    adc2_half_range_v: float = 0.05
    residue_feature_bits: int = 16
    gain_pairs: int = 32768
    gain_reference_span_v: float = 0.060
    gain_reference_midpoint_span_v: float = 0.020
    gain_reference_seed: int = 3904
    gain_schedule_seed: int = 0x464A1D3F
    forced_gain_code: int = 256
    training_samples: int = 8192
    training_tone_bin: int = 613
    training_tone_peak_v: float = 4.98
    training_tone_phase_rad: float = 0.37
    training_schedule_seed: int = 0x733D25CA
    training_dem_seed: int = 0x56724CB5
    evaluation_tone_phase_rad: float = 0.11
    training_dem_off_period: int = 4
    lms_passes: int = 6
    lms_initial_step: float = 0.25
    lms_norm_floor: float = 1e-9
    covariance_floor: float = 1e-10


# ------------------------------------------------------------------------
@dataclass(frozen=True)
class SwitchState:
    """按有序物理切片保存粗码和开关位图。"""

    plan: np.ndarray
    codes: np.ndarray
    main: np.ndarray
    sub: np.ndarray


# ------------------------------------------------------------------------
@dataclass(frozen=True)
class Observation(SwitchState):
    """校准器可以读取的原始数字观测。"""

    adc2_code: np.ndarray


# =========================================================================
# 2. 函数组：Capture / FrozenCalibration / quantize
# =========================================================================
# -------------------------------------------------------------------------
# 量化前的模拟观察，用于检查 ADC2 输入余量。
# 片增益及物理开关权重修正都在独立留出前冻结。
# 用理想均匀量化器编码，越界即停止实验。
# ------------------------------------------------------------------------
@dataclass(frozen=True)
class Capture(SwitchState):
    """量化前的模拟观察，用于检查 ADC2 输入余量。"""

    residue_v: np.ndarray

    def quantized(self, cfg: CalibrationConfig) -> Observation:
        """把量程内的记录转成数字观测，越界即拒绝。"""
        return Observation(
            self.plan,
            self.codes,
            self.main,
            self.sub,
            quantize(self.residue_v, cfg.adc2_bits, cfg.adc2_half_range_v),
        )


# ------------------------------------------------------------------------
@dataclass(frozen=True)
class FrozenCalibration:
    """片增益及物理开关权重修正都在独立留出前冻结。"""

    gain_per_slice: np.ndarray
    switch_correction_v: np.ndarray


# ------------------------------------------------------------------------
def quantize(voltage: np.ndarray, bits: int, half_range_v: float) -> np.ndarray:
    """用理想均匀量化器编码，越界即停止实验。

    Args:
        voltage: 残差输入折算电压，单位 V，允许批量数组。
        bits: 理想 ADC2 的输出位数。
        half_range_v: 半量程，单位 V，合法范围为 [-half,+half)。
    Returns:
        与输入同形的 offset-binary 整数码。
    Raises:
        ValueError: 输入非有限或超出半开窗口。

    不能依赖末行 minimum 掩盖量程错误；越界检查先执行。
    这个理想模型没有模拟比较器噪声、建立时间或过载恢复。
    """
    invalid = ~np.isfinite(voltage) | (voltage < -half_range_v) | (voltage >= half_range_v)
    if np.any(invalid):
        raise ValueError(f"residue input out of range: {np.count_nonzero(invalid)} sample(s)")
    step_v = 2 * half_range_v / (1 << bits)
    return np.minimum(np.floor((voltage + half_range_v) / step_v), (1 << bits) - 1).astype(
        np.uint16
    )


# =========================================================================
# 3. 函数组：decode_switches / residue_voltage / _capture
# =========================================================================
# -------------------------------------------------------------------------
# 解出码仓中点，单位 V。
# ------------------------------------------------------------------------
def decode_switches(code: np.ndarray, bits: int, half_range_v: float) -> np.ndarray:
    """解出码仓中点，单位 V。"""
    step_v = 2 * half_range_v / (1 << bits)
    return (np.asarray(code, dtype=float) + 0.5) * step_v - half_range_v


# ------------------------------------------------------------------------
def residue_voltage(
    pool: Capacitors,
    vin: np.ndarray,
    plan: np.ndarray,
    masks: tuple[np.ndarray, np.ndarray],
    experiment: ExperimentConfig,
) -> np.ndarray:
    """把本笔的积分参考电荷折算成输入电压（V），不含量化与阈值项。"""
    return voltage_equivalent(feedback_charge(pool, vin, plan, masks, experiment), experiment)


# ------------------------------------------------------------------------
def _capture(
    pool: Capacitors,
    vin: np.ndarray,
    plan: np.ndarray,
    phases: tuple[np.ndarray, np.ndarray],
    threshold_v: np.ndarray,
    experiment: ExperimentConfig,
    *,
    dem_enable: bool,
    noise: FrontEndNoise | None = None,
) -> Capture:
    """把一笔输入跑完采样与粗判决，返回开关状态与残差。

    ``noise`` 的三个分量与 task2/eight 的同名函数同序注入：采样噪声同时进
    粗码判决与残差（采样电容上的电荷被后续所有操作继承），比较器噪声只进
    判决输入不进残差（否则会被后级重构吸收），转换噪声只进残差。
    ``noise`` 为 None 或全零时整条噪声路径不执行，结果与无噪声版本逐位相同。
    """
    peak = experiment.full_scale_peak_v
    if np.any(~np.isfinite(vin) | (vin < -peak) | (vin >= peak)):
        raise ValueError("input exceeds the nominal Fig. 11 range")
    active = noise is not None and not noise.is_quiet
    sampled_v = vin + noise.sampling_v if active else vin
    decision_v = sampled_v + noise.comparator_v if active else sampled_v
    codes = (np.searchsorted(threshold_v, decision_v, side="right") - 1).astype(np.uint16)
    if active and np.any(codes >= len(threshold_v) - 1):
        raise ValueError("comparator noise pushed a coarse code out of range")
    main, sub = map_record(codes, phases, dem_enable=dem_enable)
    residue = residue_voltage(pool, sampled_v, plan, (main, sub), experiment)
    if active:
        residue = residue + noise.offset_v + noise.conversion_v
    return Capture(plan, codes, main, sub, residue)


# =========================================================================
# 4. 函数组：_physical_bits / _mix_training_modes / _nominal_reconstruction
# =========================================================================
# ------------------------------------------------------------------------
def _physical_bits(obs: SwitchState) -> np.ndarray:
    main = unpack(obs.main, len(MAIN_WEIGHTS))
    sub = unpack(obs.sub, len(SUB_WEIGHTS))[..., :-1]
    bits = np.zeros((len(obs.plan), POOL_SLICES, UNIT_COUNT), dtype=np.uint8)
    bits[np.arange(len(obs.plan))[:, None], obs.plan] = np.concatenate((main, sub), axis=-1)
    return bits.reshape(len(obs.plan), SWITCH_COLUMNS)


# ------------------------------------------------------------------------
def _mix_training_modes(on: Capture, off: Capture, choose_on: np.ndarray) -> Capture:
    return Capture(
        on.plan,
        on.codes,
        np.where(choose_on[:, None], on.main, off.main),
        np.where(choose_on[:, None], on.sub, off.sub),
        np.where(choose_on, on.residue_v, off.residue_v),
    )


# ------------------------------------------------------------------------
def _nominal_reconstruction(
    obs: Observation, threshold_v: np.ndarray, cfg: CalibrationConfig
) -> np.ndarray:
    return threshold_v[obs.codes] + decode_switches(
        obs.adc2_code, cfg.adc2_bits, cfg.adc2_half_range_v
    )


# =========================================================================
# 5. 函数组：_features / _condition_report / _fit_gain
# =========================================================================
# ------------------------------------------------------------------------
def _features(
    obs: Observation,
    gain_per_slice: np.ndarray,
    threshold_v: np.ndarray,
    experiment: ExperimentConfig,
    cfg: CalibrationConfig,
) -> np.ndarray:
    residue = decode_switches(obs.adc2_code, cfg.adc2_bits, cfg.adc2_half_range_v)
    # r=G·(Vin+VFS)-W。先用未校准电压估计 G-1 项，保留的交叉误差为二阶。
    nominal_output = threshold_v[obs.codes] + residue
    aggregate_gain = gain_per_slice[obs.plan].sum(axis=1)
    corrected = residue - (aggregate_gain - 1) * (nominal_output + experiment.full_scale_peak_v)
    code = quantize(corrected, cfg.residue_feature_bits, cfg.adc2_half_range_v)
    fine_bits = unpack(code, cfg.residue_feature_bits)[:, ::-1]
    return np.column_stack((_physical_bits(obs), fine_bits)).astype(np.uint8)


# ------------------------------------------------------------------------
def _condition_report(singular: np.ndarray, shape: tuple[int, int]) -> dict:
    threshold = singular[0] * max(shape) * np.finfo(float).eps
    rank = int(np.count_nonzero(singular > threshold))
    return {
        "rows": shape[0],
        "columns": shape[1],
        "rank": rank,
        "condition_number": float(singular[0] / singular[-1]) if rank == shape[1] else None,
    }


# ------------------------------------------------------------------------
def _fit_gain(
    plan: np.ndarray,
    low_v: np.ndarray,
    high_v: np.ndarray,
    low_code: np.ndarray,
    high_code: np.ndarray,
    cfg: CalibrationConfig,
) -> tuple[np.ndarray, dict]:
    return _fit_gain_from_flags(group_flags(plan), low_v, high_v, low_code, high_code, cfg)


# =========================================================================
# 6. 函数组：_fit_gain_from_flags / group_flags / _schedule_report
# =========================================================================
# ------------------------------------------------------------------------
def _fit_gain_from_flags(
    flags: np.ndarray,
    low_v: np.ndarray,
    high_v: np.ndarray,
    low_code: np.ndarray,
    high_code: np.ndarray,
    cfg: CalibrationConfig,
) -> tuple[np.ndarray, dict]:
    adc2_low_v = decode_switches(low_code, cfg.adc2_bits, cfg.adc2_half_range_v)
    adc2_high_v = decode_switches(high_code, cfg.adc2_bits, cfg.adc2_half_range_v)
    slopes = (adc2_high_v - adc2_low_v) / (high_v - low_v)
    gain, _, _, singular = np.linalg.lstsq(flags, slopes, rcond=None)
    report = _condition_report(singular, flags.shape)
    if report["rank"] != flags.shape[1] or np.any(gain <= 0.0):
        raise ValueError(
            f"gain reference capture does not identify {flags.shape[1]} positive gains"
        )
    return gain, report


# ------------------------------------------------------------------------
def group_flags(plan: np.ndarray) -> np.ndarray:
    """把逐样本的物理片号展开成 one-hot 分组矩阵，供最小二乘按片解增益。

    Args:
        plan: (N,8) 有序物理片号，片号取自同一 18 片池。
    Returns:
        (N,18) 的组成员特征，用于组合每片增益。

    组成员特征用于输入增益，不包含每个电容开关的位权信息。
    不能用这份矩阵替代完整开关特征去训练所有物理单元失配。
    同笔重复物理片号的合法性由调度合同和下游检查保证。
    """
    flags = np.zeros((len(plan), POOL_SLICES))
    flags[np.arange(len(plan))[:, None], plan] = 1.0
    return flags


# ------------------------------------------------------------------------
def _schedule_report(plan: np.ndarray) -> dict:
    flags = group_flags(plan)
    return {
        "observed_slice_ids": np.flatnonzero(flags.sum(axis=0)).tolist(),
        "gain_observability_from_plan_alone": _condition_report(
            np.linalg.svd(flags, compute_uv=False), flags.shape
        ),
    }


# =========================================================================
# 7. 函数组：fixed_ab_plan / fixed_ab_flags / fixed_switch_bits
# =========================================================================
# ------------------------------------------------------------------------
def fixed_ab_plan(samples: int) -> np.ndarray:
    """生成固定 A/B 交替计划：偶数样本用 0–7 号片，奇数样本用 8–15 号片。"""
    return np.arange(GROUP_SLICES)[None, :] + (np.arange(samples) % 2)[:, None] * GROUP_SLICES


# ------------------------------------------------------------------------
def fixed_ab_flags(plan: np.ndarray) -> np.ndarray:
    """固定 A/B 模式下按 8 片聚合的增益设计矩阵；计划不是交替形态时直接报错。"""
    if not np.array_equal(plan, fixed_ab_plan(len(plan))):
        raise ValueError("aggregate calibration requires the fixed A/B plan")
    group = plan[:, 0] // GROUP_SLICES
    return group[:, None] == np.arange(2)


# ------------------------------------------------------------------------
def fixed_switch_bits(obs: SwitchState) -> np.ndarray:
    """把两拍位图摊平成同一组特征位；要求八片共用一张掩码且两个末位恒关。"""
    flags = fixed_ab_flags(obs.plan)
    if np.any(obs.main != obs.main[:, :1]) or np.any(obs.sub != obs.sub[:, :1]):
        raise ValueError("aggregate calibration requires all eight slices to share the same mask")
    # 第 17 个主单元 S4 和第 5 个子单元在无 DEM 路径均固定关闭。
    main = unpack(obs.main[:, 0], len(MAIN_WEIGHTS))
    sub = unpack(obs.sub[:, 0], len(SUB_WEIGHTS))
    if main[:, -1].any() or sub[:, -1].any():
        raise ValueError("aggregate calibration requires the DEM-off mask")
    active_bits = np.column_stack((main[:, :-1], sub[:, :-1]))
    return (flags[:, :, None] & active_bits[:, None, :]).reshape(len(flags), -1)


# =========================================================================
# 8. 函数组：_design_report / _nominal_weights / _fit_weights
# =========================================================================
# ------------------------------------------------------------------------
def _design_report(bits: np.ndarray, frequency: float) -> dict:
    # 工具箱拟合直流项和一个待估相位分量；同时检查正弦和余弦两种基底。
    phase = 2 * np.pi * frequency * np.arange(len(bits))
    base = np.column_stack((bits, np.ones(len(bits))))
    reports = []
    for name, harmonic in (("cosine", np.cos(phase)), ("sine", np.sin(phase))):
        matrix = np.column_stack((base, harmonic))
        report = _condition_report(np.linalg.svd(matrix, compute_uv=False), matrix.shape)
        reports.append({"fitted_component": name, **report})
    return {
        "basis_cases": reports,
        "constant_feature_count": int((np.ptp(bits, axis=0) == 0).sum()),
    }


# ------------------------------------------------------------------------
def _nominal_weights(experiment: ExperimentConfig, cfg: CalibrationConfig) -> np.ndarray:
    beta = 1.0 / (1.0 + sum(SUB_WEIGHTS))
    per_slice = np.r_[MAIN_WEIGHTS, np.asarray(SUB_WEIGHTS[:-1]) * beta]
    scale = 2 * experiment.full_scale_peak_v / (GROUP_SLICES * sum(MAIN_WEIGHTS))
    switch_weights = np.tile(per_slice * scale, POOL_SLICES)
    code_step = 2 * cfg.adc2_half_range_v / (1 << cfg.residue_feature_bits)
    residue_weights = code_step * np.exp2(np.arange(cfg.residue_feature_bits - 1, -1, -1))
    return np.r_[switch_weights, residue_weights]


# ------------------------------------------------------------------------
def _fit_weights(
    bits: np.ndarray, nominal: np.ndarray, cfg: CalibrationConfig, report: dict
) -> tuple[np.ndarray, float, dict]:
    from adctoolbox import calibrate_weight_sine, scale_calibration_output

    result = calibrate_weight_sine(
        bits,
        freq=cfg.training_tone_bin / cfg.training_samples,
        force_search=False,
        harmonic_order=1,
        nominal_weights=nominal,
    )
    scaled = scale_calibration_output(result, target_sine_peak=cfg.training_tone_peak_v)
    if scaled["rank_patch"]["applied"]:
        raise ValueError("ADCToolbox patched training rank; refusing to freeze the weight solution")
    return (
        np.asarray(scaled["weight"]),
        float(scaled["offset"]),
        {
            **report,
            "toolbox_rank_patch_applied": bool(scaled["rank_patch"]["applied"]),
            "scale_convention": scaled["scale_convention"],
            "scale_factor_v": float(scaled["scale_factor"]),
        },
    )


# =========================================================================
# 9. 函数组：_switch_design_report / _fit_nlms / _apply_nlms
# =========================================================================
# -------------------------------------------------------------------------
# 逐笔更新有效权重，预条件矩阵只由训练位图确定。
# ------------------------------------------------------------------------
def _switch_design_report(bits: np.ndarray) -> dict:
    singular = np.linalg.svd(bits.astype(float), compute_uv=False)
    return {
        **_condition_report(singular, bits.shape),
        "constant_feature_count": int(np.count_nonzero(np.ptp(bits, axis=0) == 0)),
    }


# ------------------------------------------------------------------------
def _fit_nlms(
    switch_bits: np.ndarray, target_v: np.ndarray, cfg: CalibrationConfig
) -> tuple[np.ndarray, dict, list[dict]]:
    """逐笔更新有效权重，预条件矩阵只由训练位图确定。

    Args:
        switch_bits: 训练特征矩阵，每行一笔、每列一个待识别有效权重。
        target_v: 同笔独立参考形成的训练目标，单位 V。
        cfg: 协方差正则、归一化下限、初始步长和训练遍数。
    Returns:
        原特征坐标的冻结权重、矩阵秩报告及各遍训练残差。
    Raises:
        ValueError: 特征矩阵不能识别所有传入权重列。

    预条件矩阵只由训练特征构造，不能使用留出输出寻找更优方向。
    第 e 遍步长按 cfg.lms_initial_step/(e+1) 递减。
    返回的学习误差只针对训练集，泛化必须另用独立留出。
    制造电容值只用于生成模拟观测，不能直接作为训练目标权重。
    前台参考的独立性由调用方提供，本函数不测量参考源的精度。
    训练收敛不自动证明全幅量程和未激励状态正确。
    系数装载与定点量化需要在后续整数重构链另行验证。
    欠秩问题由调用方先构造可观测子空间，本函数不会静默丢弃列。
    """
    features = switch_bits.astype(float)
    _, singular, right = np.linalg.svd(features, full_matrices=False)
    report = _condition_report(singular, features.shape)
    if report["rank"] != features.shape[1]:
        raise ValueError("NLMS capture does not identify all physical switch weights")
    # P = V diag((sigma^2/N + floor)^(-1/2))，在该坐标做 NLMS 后映回物理权重。
    preconditioner = right.T / np.sqrt(singular**2 / len(features) + cfg.covariance_floor)
    conditioned = features @ preconditioner
    normalization = np.einsum("ij,ij->i", conditioned, conditioned) + cfg.lms_norm_floor
    update_direction = conditioned / normalization[:, None]
    coefficient = np.zeros(features.shape[1])

    def record(epoch: int, step: float) -> dict:
        error = target_v - conditioned @ coefficient
        return {
            "epoch": epoch,
            "step": step,
            "training_residual_rms_v": float(np.sqrt(np.mean(error**2))),
        }

    learning = [record(0, 0.0)]
    for epoch in range(cfg.lms_passes):
        step = cfg.lms_initial_step / (epoch + 1)
        for feature, direction, target in zip(conditioned, update_direction, target_v, strict=True):
            coefficient += step * (target - feature @ coefficient) * direction
        learning.append(record(epoch + 1, step))
    return preconditioner @ coefficient, report, learning


# ------------------------------------------------------------------------
def _apply_nlms(
    obs: Observation,
    frozen: FrozenCalibration,
    threshold_v: np.ndarray,
    experiment: ExperimentConfig,
    cfg: CalibrationConfig,
) -> np.ndarray:
    nominal_v = _nominal_reconstruction(obs, threshold_v, cfg)
    gain = frozen.gain_per_slice[obs.plan].sum(axis=1)
    correction_v = _physical_bits(obs) @ frozen.switch_correction_v
    peak = experiment.full_scale_peak_v
    return (nominal_v + peak + correction_v) / gain - peak


# =========================================================================
# 10. 函数组：_four_modes / _spectrum / calibration_metrics
# =========================================================================
# ------------------------------------------------------------------------
def _four_modes(
    off: Observation,
    on: Observation,
    frozen: FrozenCalibration,
    threshold_v: np.ndarray,
    experiment: ExperimentConfig,
    cfg: CalibrationConfig,
) -> dict[str, np.ndarray]:
    return {
        "no_dem_no_cal": _nominal_reconstruction(off, threshold_v, cfg),
        "dem_only": _nominal_reconstruction(on, threshold_v, cfg),
        "lms_only": _apply_nlms(off, frozen, threshold_v, experiment, cfg),
        "dem_and_lms": _apply_nlms(on, frozen, threshold_v, experiment, cfg),
    }


# ------------------------------------------------------------------------
def _spectrum(output_v: np.ndarray, experiment: ExperimentConfig) -> dict:
    from adctoolbox.spectrum import compute_spectrum

    return compute_spectrum(
        output_v,
        fs=experiment.fs_hz,
        max_scale_range=(-experiment.full_scale_peak_v, experiment.full_scale_peak_v),
        win_type="rectangular",
        side_bin=0,
    )


# ------------------------------------------------------------------------
def calibration_metrics(
    output_v: np.ndarray, reference_v: np.ndarray, experiment: ExperimentConfig
) -> dict:
    """取 adctoolbox 的 SNDR/SFDR/ENOB，并附输出相对参考的 RMS 误差。"""
    metrics = _spectrum(output_v, experiment)["metrics"]
    return {
        "sndr_db": float(metrics["sndr_dbc"]),
        "sfdr_db": float(metrics["sfdr_dbc"]),
        "enob": float(metrics["enob"]),
        "input_error_rms_v": float(np.sqrt(np.mean((output_v - reference_v) ** 2))),
    }


# =========================================================================
# 11. 函数组：_plot / range_report / _plot_headroom
# =========================================================================
# ------------------------------------------------------------------------
def _plot(
    path: Path,
    outputs: dict[str, np.ndarray],
    stats: dict[str, dict],
    learning: list[dict],
    experiment: ExperimentConfig,
    cfg: CalibrationConfig,
) -> None:
    colors = (GRAY, ORANGE, "#549868", BLUE)
    fig, panels = plt.subplot_mosaic(
        [["spectrum", "spectrum"], ["bars", "learning"]],
        figsize=(11.1, 7.2),
        layout="constrained",
    )
    spectral = panels["spectrum"]
    bars = panels["bars"]
    fig.suptitle(
        f"Fig. 11 | holdout with 12-bit ADC2 ±{1000 * cfg.adc2_half_range_v:g} mV, "
        f"unit-cap σ = {100 * experiment.mismatch_sigma_unit:g}%",
        fontsize=14,
        fontweight="bold",
    )
    for (mode, output), color in zip(outputs.items(), colors, strict=True):
        plot_data = _spectrum(output, experiment)["plot_data"]
        spectral.plot(
            plot_data["freq"] / 1e6,
            plot_data["power_spectrum_db_plot"],
            label=MODE_LABELS[mode],
            color=color,
            lw=0.8,
        )
    spectral.set(
        xlabel="Frequency (MHz)",
        ylabel="Amplitude (dBFS/bin)",
        xlim=(0, experiment.fs_hz / 2e6),
        ylim=(-160, 1),
    )
    spectral.legend(loc="upper right", fontsize=8)
    spectral.grid(alpha=0.25)
    x = np.arange(len(outputs))
    for key, label, offset, color in (
        ("sndr_db", "SNDR", -0.18, BLUE),
        ("sfdr_db", "SFDR", 0.18, ORANGE),
    ):
        values = [stats[mode][key] for mode in outputs]
        plotted = bars.bar(x + offset, values, width=0.34, label=label, color=color)
        bars.bar_label(plotted, fmt="%.1f", padding=2, fontsize=9)
    bars.set(
        ylabel="dB",
        xticks=x,
        xticklabels=["Raw", "DEM", "NLMS", "DEM + NLMS"],
        ylim=(0, max(max(m["sndr_db"], m["sfdr_db"]) for m in stats.values()) + 12),
    )
    bars.legend(loc="upper left", fontsize=8)
    bars.axhline(100, color=GRAY, ls=":", lw=0.8, zorder=0)
    bars.grid(axis="y", alpha=0.25)
    panels["learning"].semilogy(
        [point["epoch"] for point in learning],
        [point["training_residual_rms_v"] * 1e6 for point in learning],
        marker="o",
        color=BLUE,
    )
    panels["learning"].set(
        xlabel=f"Training passes ({cfg.training_samples} samples per pass)",
        ylabel="Switch-fit RMS error (µV)",
    )
    panels["learning"].grid(alpha=0.25)
    fig.savefig(path, dpi=165, facecolor="white")
    plt.close(fig)


# ------------------------------------------------------------------------
def range_report(residue_v: np.ndarray, half_range_v: float) -> dict:
    """统计余量越出 ADC2 半量程的样本数与极值，供裕度图判断是否削顶。

    Args:
        residue_v: 被观察内部节点的残差数组，单位 V。
        half_range_v: 该节点窗口的半宽，单位 V。
    Returns:
        残差极值和超窗数量等诊断信息。

    统计对象必须标明节点；ADC2 输入超窗与最终数字码饱和不是同一事件。
    范围报告不会修改、限幅或修复传入数据。
    显示通过仅说明本次激励未越过该窗口，不保证其他输入和状态也安全。
    """
    return {
        "samples": int(residue_v.size),
        "min_v": float(residue_v.min()),
        "max_v": float(residue_v.max()),
        "below_range": int(np.count_nonzero(residue_v < -half_range_v)),
        "at_or_above_range": int(np.count_nonzero(residue_v >= half_range_v)),
    }


# ------------------------------------------------------------------------
def _plot_headroom(
    path: Path,
    headroom: dict,
    traces: dict[str, np.ndarray],
    stats: dict,
    experiment: ExperimentConfig,
    cfg: CalibrationConfig,
) -> None:
    fig, (ranges, spectrum) = plt.subplots(2, 1, figsize=(10.5, 7.5), layout="constrained")
    fig.suptitle(
        f"Fig. 11 | ADC2 range blocked, unit-cap σ = {100 * experiment.mismatch_sigma_unit:g}%",
        fontsize=14,
        fontweight="bold",
    )
    limit_mv = 1000 * cfg.adc2_half_range_v
    ranges.axvspan(-limit_mv, limit_mv, color="#DDEDDC", label="ADC2 nominal window")
    labels = []
    for row, (name, report) in enumerate(headroom.items()):
        low, high = 1000 * report["min_v"], 1000 * report["max_v"]
        overloads = report["below_range"] + report["at_or_above_range"]
        color = ORANGE if overloads else BLUE
        ranges.hlines(row, low, high, color=color, lw=2)
        ranges.plot((low, high), (row, row), ".", color=color)
        ranges.annotate(
            f"{overloads}/{report['samples']} out",
            (high, row),
            (5, 0),
            textcoords="offset points",
            va="center",
            fontsize=9,
        )
        labels.append(name.replace("_", " "))
    ranges.set(
        xlim=(
            min(-limit_mv, *(1000 * v["min_v"] for v in headroom.values())) - 5,
            max(limit_mv, *(1000 * v["max_v"] for v in headroom.values())) + 20,
        ),
        yticks=np.arange(len(labels)),
        yticklabels=labels,
        xlabel="Input-equivalent residue range (mV)",
    )
    ranges.legend(loc="lower left", fontsize=8)
    ranges.invert_yaxis()
    for (mode, trace), color in zip(traces.items(), (GRAY, ORANGE), strict=True):
        data = _spectrum(trace, experiment)["plot_data"]
        metric = stats[mode]
        spectrum.plot(
            data["freq"] / 1e6,
            data["power_spectrum_db_plot"],
            label=f"{mode.replace('_', ' ')}  SNDR {metric['sndr_db']:.2f}, "
            f"SFDR {metric['sfdr_db']:.2f} dB",
            color=color,
            lw=0.75,
        )
    spectrum.set(
        title="Nominal coarse + unquantized residue | diagnostic only",
        xlabel="Frequency (MHz)",
        ylabel="Amplitude (dBFS/bin)",
        ylim=(-115, 1),
    )
    spectrum.legend(loc="upper right", fontsize=8)
    for axis in (ranges, spectrum):
        axis.grid(alpha=0.22)
    fig.savefig(path, dpi=165, facecolor="white")
    plt.close(fig)


# =========================================================================
# 12. 函数组：_plot_schedule_comparison / _strict_fixed_training / _plot_strict_comparison
# =========================================================================
# -------------------------------------------------------------------------
# 只用固定 A/B、DEM 关闭的采集，拟合可辨识的组增益和组合权重。
# ------------------------------------------------------------------------
def _plot_schedule_comparison(
    path: Path, dynamic: dict, fixed: dict, experiment: ExperimentConfig, cfg: CalibrationConfig
) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.4), layout="constrained")
    fig.suptitle(
        f"Fixed A/B vs dynamic slices | ADC2 ±{1000 * cfg.adc2_half_range_v:g} mV, "
        f"unit-cap σ = {100 * experiment.mismatch_sigma_unit:g}%",
        fontsize=14,
        fontweight="bold",
    )
    x = np.arange(len(MODE_LABELS))
    for ax, metric, label in zip(axes, ("sndr_db", "sfdr_db"), ("SNDR", "SFDR"), strict=True):
        for stats, name, offset, color in (
            (fixed, "Fixed A/B", -0.18, GRAY),
            (dynamic, "Dynamic slices", 0.18, BLUE),
        ):
            values = [stats[mode][metric] for mode in MODE_LABELS]
            bars = ax.bar(x + offset, values, width=0.35, color=color, label=name)
            ax.bar_label(bars, fmt="%.1f", padding=2, fontsize=8)
        ax.set(ylabel=f"{label} (dB)", xticks=x, xticklabels=["Raw", "DEM", "NLMS", "DEM + NLMS"])
        ax.set_ylim(
            0, max(item[metric] for stats in (fixed, dynamic) for item in stats.values()) + 15
        )
        ax.grid(axis="y", alpha=0.2)
    axes[0].legend(loc="upper left", fontsize=8)
    fig.savefig(path, dpi=165, facecolor="white")
    plt.close(fig)


# ------------------------------------------------------------------------
def _strict_fixed_training(
    pool: Capacitors,
    gain_references: tuple[np.ndarray, np.ndarray],
    gain_masks: tuple[np.ndarray, np.ndarray],
    fit_v: np.ndarray,
    fit_switches: SwitchState,
    hold_v: np.ndarray,
    hold_capture: Capture,
    threshold_v: np.ndarray,
    experiment: ExperimentConfig,
    cfg: CalibrationConfig,
) -> tuple[dict, dict]:
    """只用固定 A/B、DEM 关闭的采集，拟合可辨识的组增益和组合权重。"""
    gain_plan = fixed_ab_plan(cfg.gain_pairs)
    gain_low_v, gain_high_v = gain_references
    low_residue_v = residue_voltage(pool, gain_low_v, gain_plan, gain_masks, experiment)
    high_residue_v = residue_voltage(pool, gain_high_v, gain_plan, gain_masks, experiment)
    fit_plan = fixed_ab_plan(cfg.training_samples)
    fit_masks = fit_switches.main, fit_switches.sub
    fit_capture = Capture(
        fit_plan,
        fit_switches.codes,
        *fit_masks,
        residue_voltage(pool, fit_v, fit_plan, fit_masks, experiment),
    )
    headroom = {
        name: range_report(voltage, cfg.adc2_half_range_v)
        for name, voltage in (
            ("gain_low", low_residue_v),
            ("gain_high", high_residue_v),
            ("train_dem_off", fit_capture.residue_v),
            ("holdout_dem_off", hold_capture.residue_v),
        )
    }
    report = {
        "training_dem_enabled": False,
        "holdout_dem_enabled": False,
        "adc2_range_checks": headroom,
        "gain_reference_pairs": cfg.gain_pairs,
        "training_samples_per_pass": cfg.training_samples,
        "passes": cfg.lms_passes,
        "update_count": cfg.training_samples * cfg.lms_passes,
    }
    if any(row["below_range"] or row["at_or_above_range"] for row in headroom.values()):
        return {**report, "status": "adc2_range_blocked", "holdout": None}, {}

    low_code = quantize(low_residue_v, cfg.adc2_bits, cfg.adc2_half_range_v)
    high_code = quantize(high_residue_v, cfg.adc2_bits, cfg.adc2_half_range_v)
    gain, gain_report = _fit_gain_from_flags(
        fixed_ab_flags(gain_plan), gain_low_v, gain_high_v, low_code, high_code, cfg
    )
    fit = fit_capture.quantized(cfg)
    fit_gain = fixed_ab_flags(fit.plan) @ gain
    peak = experiment.full_scale_peak_v
    nominal_fit = _nominal_reconstruction(fit, threshold_v, cfg)
    target = fit_gain * (fit_v + peak) - (nominal_fit + peak)
    weight, design, learning = _fit_nlms(fixed_switch_bits(fit), target, cfg)

    hold = hold_capture.quantized(cfg)
    nominal_hold = _nominal_reconstruction(hold, threshold_v, cfg)
    hold_gain = fixed_ab_flags(hold.plan) @ gain
    calibrated = (nominal_hold + peak + fixed_switch_bits(hold) @ weight) / hold_gain - peak
    return {
        **report,
        "status": "completed",
        "gain_design": gain_report,
        "aggregate_weight_design": design,
        "learning": learning,
        "holdout": calibration_metrics(calibrated, hold_v, experiment),
    }, {
        "strict_fixed_gain_plan": gain_plan,
        "strict_fixed_gain_low_residue_v": low_residue_v,
        "strict_fixed_gain_high_residue_v": high_residue_v,
        "strict_fixed_gain_low_adc2_code": low_code,
        "strict_fixed_gain_high_adc2_code": high_code,
        "strict_fixed_fit_plan": fit.plan,
        "strict_fixed_fit_coarse_code": fit.codes,
        "strict_fixed_fit_main_mask": fit.main,
        "strict_fixed_fit_sub_mask": fit.sub,
        "strict_fixed_fit_adc2_code": fit.adc2_code,
        "strict_fixed_fit_residue_v": fit_capture.residue_v,
        "strict_fixed_holdout_adc2_code": hold.adc2_code,
        "strict_fixed_group_gain": gain,
        "strict_fixed_group_switch_correction_v": weight,
        "strict_fixed_calibrated_v": calibrated,
    }


# ------------------------------------------------------------------------
def _plot_strict_comparison(
    path: Path, fixed: dict, dynamic: dict, experiment: ExperimentConfig, cfg: CalibrationConfig
) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(9.2, 4.3), layout="constrained")
    fig.suptitle(
        f"Independent training paths | ADC2 ±{1000 * cfg.adc2_half_range_v:g} mV, "
        f"unit-cap σ = {100 * experiment.mismatch_sigma_unit:g}%",
        fontsize=14,
        fontweight="bold",
    )
    labels = (
        "Fixed A/B, DEM off\nNLMS trained without DEM",
        "Dynamic + DEM\nMixed-DEM NLMS training",
    )
    for ax, key, label in zip(axes, ("sndr_db", "sfdr_db"), ("SNDR", "SFDR"), strict=True):
        values = [fixed[key], dynamic[key]]
        bars = ax.bar((0, 1), values, color=[GRAY, BLUE], width=0.57)
        ax.bar_label(bars, fmt="%.2f", padding=2)
        ax.set(
            ylabel=f"{label} (dB)", xticks=(0, 1), xticklabels=labels, ylim=(0, max(values) + 14)
        )
        ax.grid(axis="y", alpha=0.22)
    fig.savefig(path, dpi=165, facecolor="white")
    plt.close(fig)


# =========================================================================
# 13. 函数组：_write_summary / run / main
# =========================================================================
# -------------------------------------------------------------------------
# 在相同虚拟制造样本上完成前台训练和留出评估，保存可复现结果。
# 执行可复现实验并打印留出指标。
# ------------------------------------------------------------------------
def _write_summary(out: Path, summary: dict) -> dict:
    (out / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    return summary


# ------------------------------------------------------------------------
def run(out: Path, mismatch_sigma_unit: float = 0.001, adc2_half_range_v: float = 0.05) -> dict:
    """在相同虚拟制造样本上完成前台训练和留出评估，保存可复现结果。"""
    if not np.isfinite(mismatch_sigma_unit) or mismatch_sigma_unit < 0:
        raise ValueError("unit-capacitor mismatch sigma must be finite and non-negative")
    if not np.isfinite(adc2_half_range_v) or adc2_half_range_v <= 0:
        raise ValueError("ADC2 half range must be finite and positive")
    experiment = replace(ExperimentConfig(), mismatch_sigma_unit=mismatch_sigma_unit)
    cfg = replace(CalibrationConfig(), adc2_half_range_v=adc2_half_range_v)
    plt.switch_backend("Agg")
    out.mkdir(parents=True, exist_ok=True)

    nominal, pool = cap_pool(experiment)
    minimum_cap_f = {name: float(np.min(getattr(pool, name))) for name in ("main", "sub", "bridge")}
    if any(value <= 0 or not np.isfinite(value) for value in minimum_cap_f.values()):
        raise ValueError(f"manufacturing seed generates non-positive capacitance: {minimum_cap_f}")
    threshold_v = codebook(nominal, experiment)
    gain_plan, _, gain_phases = schedule_sequence(
        cfg.gain_pairs, replace(experiment, schedule_seed=cfg.gain_schedule_seed)
    )
    gain_rng = np.random.default_rng(cfg.gain_reference_seed)
    midpoint_v = cfg.gain_reference_midpoint_span_v * gain_rng.uniform(-0.5, 0.5, cfg.gain_pairs)
    gain_low_v = midpoint_v - cfg.gain_reference_span_v / 2
    gain_high_v = midpoint_v + cfg.gain_reference_span_v / 2
    gain_codes = np.full(cfg.gain_pairs, cfg.forced_gain_code, dtype=np.uint16)
    gain_masks = map_record(gain_codes, gain_phases, dem_enable=False)
    gain_low_residue_v = residue_voltage(pool, gain_low_v, gain_plan, gain_masks, experiment)
    gain_high_residue_v = residue_voltage(pool, gain_high_v, gain_plan, gain_masks, experiment)

    fit_plan, _, fit_phases = schedule_sequence(
        cfg.training_samples,
        replace(
            experiment, schedule_seed=cfg.training_schedule_seed, dem_seed=cfg.training_dem_seed
        ),
    )
    training_frequency = cfg.training_tone_bin / cfg.training_samples
    fit_v = cfg.training_tone_peak_v * np.sin(
        2 * np.pi * training_frequency * np.arange(cfg.training_samples)
        + cfg.training_tone_phase_rad
    )
    fit_on = _capture(pool, fit_v, fit_plan, fit_phases, threshold_v, experiment, dem_enable=True)
    fit_off = _capture(pool, fit_v, fit_plan, fit_phases, threshold_v, experiment, dem_enable=False)
    choose_on = np.arange(cfg.training_samples) % cfg.training_dem_off_period != 0
    fit_capture = _mix_training_modes(fit_on, fit_off, choose_on)
    hold_plan, _, hold_phases = schedule_sequence(experiment.samples, experiment)
    hold_v = experiment.tone_peak_v * np.sin(
        2 * np.pi * experiment.fin_hz / experiment.fs_hz * np.arange(experiment.samples)
        + cfg.evaluation_tone_phase_rad
    )
    hold_capture_off, hold_capture_on = (
        _capture(pool, hold_v, hold_plan, hold_phases, threshold_v, experiment, dem_enable=enabled)
        for enabled in (False, True)
    )
    fixed_plan = fixed_ab_plan(experiment.samples)
    fixed_capture_off, fixed_capture_on = (
        _capture(pool, hold_v, fixed_plan, hold_phases, threshold_v, experiment, dem_enable=enabled)
        for enabled in (False, True)
    )
    captures = {
        "gain_low": gain_low_residue_v,
        "gain_high": gain_high_residue_v,
        "train_dem_off": fit_capture.residue_v[~choose_on],
        "train_dem_on": fit_capture.residue_v[choose_on],
        "holdout_dem_off": hold_capture_off.residue_v,
        "holdout_dem_on": hold_capture_on.residue_v,
    }
    headroom = {
        name: range_report(residue_v, cfg.adc2_half_range_v) for name, residue_v in captures.items()
    }
    fixed_captures = {
        "holdout_dem_off": fixed_capture_off.residue_v,
        "holdout_dem_on": fixed_capture_on.residue_v,
    }
    fixed_headroom = {
        name: range_report(residue_v, cfg.adc2_half_range_v)
        for name, residue_v in fixed_captures.items()
    }
    summary = {
        "schema": "task1_nlms_adctoolbox_calibration_v2",
        "model_scope": "理想两节点电容失配与固定理想 ADC2，时偏和其余动态误差源关闭，训练至留出电容池和温度不变。",
        "adc2_scope": f"输入等效 ±{1000 * cfg.adc2_half_range_v:g} mV、12 位均匀码仓的理想细级，16 位重编码仅用于工具箱接口适配。",
        "adc2_window_basis": "behavioral_default" if adc2_half_range_v == 0.05 else "candidate",
        "adc2_step_v": 2 * cfg.adc2_half_range_v / (1 << cfg.adc2_bits),
        "minimum_physical_cap_f": minimum_cap_f,
        "adc2_range_checks": headroom,
        "schedule_comparison": {
            "shared_training": "all eight modes use the same mixed-DEM dynamic training and frozen NLMS weights",
            "shared_phases": True,
            "dynamic": _schedule_report(hold_plan),
            "fixed_ab": {
                **_schedule_report(fixed_plan),
                "adc2_range_checks": fixed_headroom,
            },
        },
        "experiment": {
            key: value
            for key, value in asdict(experiment).items()
            if key not in ("skew_s", "static_repeats")
        },
        "calibration": asdict(cfg),
        "training_frequency_hz": experiment.fs_hz * training_frequency,
        "evaluation_frequency_hz": experiment.fin_hz,
        "source_sha256": {
            path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
            for path in (
                Path(__file__).resolve().relative_to(ROOT).as_posix(),
                "task1/experiments/run_effects.py",
                "task1/model/scheduler.py",
                "task1/model/fig11_dem.py",
                "task1/model/module1.py",
                "src/adi_model/charge_ref.py",
            )
        },
        "versions": {
            "adctoolbox": version("adctoolbox"),
            "python": platform.python_version(),
            "numpy": np.__version__,
            "matplotlib": matplotlib.__version__,
        },
    }
    blocked = [
        name
        for name, result in headroom.items()
        if result["below_range"] or result["at_or_above_range"]
    ]
    if blocked:
        summary["schedule_comparison"]["dynamic"]["status"] = "adc2_range_blocked"
        summary["schedule_comparison"]["fixed_ab"]["status"] = "shared_training_unavailable"
        summary["strict_training_comparison"] = {"status": "dynamic_adc2_range_blocked"}
        proxy = {
            name: threshold_v[record.codes] + record.residue_v
            for name, record in (("DEM_off", hold_capture_off), ("DEM_on", hold_capture_on))
        }
        stats = {
            mode: calibration_metrics(output, hold_v, experiment) for mode, output in proxy.items()
        }
        _plot_headroom(out / "adc2_headroom.png", headroom, proxy, stats, experiment, cfg)
        np.savez_compressed(
            out / "residue_before_adc2.npz",
            gain_reference_low_v=gain_low_v,
            gain_reference_high_v=gain_high_v,
            training_reference_v=fit_v,
            training_dem_enabled=choose_on,
            evaluation_reference_v=hold_v,
            evaluation_fixed_plan=fixed_plan,
            **{f"{name}_residue_v": trace for name, trace in captures.items()},
            **{f"fixed_{name}_residue_v": trace for name, trace in fixed_captures.items()},
            **{f"{name}_continuous_output_v": trace for name, trace in proxy.items()},
        )
        return _write_summary(
            out,
            {
                **summary,
                "status": "adc2_range_blocked",
                "blocked_stages": blocked,
                "first_failed_stage": blocked[0],
                "holdout": None,
                "continuous_pre_adc2_diagnostic": stats,
            },
        )

    gain_low_code = quantize(gain_low_residue_v, cfg.adc2_bits, cfg.adc2_half_range_v)
    gain_high_code = quantize(gain_high_residue_v, cfg.adc2_bits, cfg.adc2_half_range_v)
    gain, gain_report = _fit_gain(
        gain_plan, gain_low_v, gain_high_v, gain_low_code, gain_high_code, cfg
    )
    fit_obs = fit_capture.quantized(cfg)
    physical_bits = _physical_bits(fit_obs)
    nominal_output = _nominal_reconstruction(fit_obs, threshold_v, cfg)
    peak = experiment.full_scale_peak_v
    target = gain[fit_plan].sum(axis=1) * (fit_v + peak) - (nominal_output + peak)
    correction, lms_report, learning = _fit_nlms(physical_bits, target, cfg)
    frozen = FrozenCalibration(gain, correction)

    # 同样的训练输入和选片计划，只固定 DEM 模式，用于证明两个秩反例。
    counterexamples = {
        "dem_on_only": _switch_design_report(_physical_bits(fit_on)),
        "dem_off_only": _switch_design_report(_physical_bits(fit_off)),
    }
    fit_bits = _features(fit_obs, gain, threshold_v, experiment, cfg)
    toolbox_report = _design_report(fit_bits, training_frequency)
    toolbox_coefficients = None
    if all(case["rank"] == case["columns"] for case in toolbox_report["basis_cases"]):
        weights, dc_offset, toolbox_report = _fit_weights(
            fit_bits, _nominal_weights(experiment, cfg), cfg, toolbox_report
        )
        toolbox_coefficients = (weights, dc_offset)
        toolbox_report["status"] = "trained"
    else:
        toolbox_report["status"] = "rank_deficient"

    hold_off = hold_capture_off.quantized(cfg)
    hold_on = hold_capture_on.quantized(cfg)
    outputs = _four_modes(hold_off, hold_on, frozen, threshold_v, experiment, cfg)
    stats = {
        mode: calibration_metrics(output, hold_v, experiment) for mode, output in outputs.items()
    }
    _plot(out / "adctoolbox_calibration.png", outputs, stats, learning, experiment, cfg)
    schedule_comparison = summary["schedule_comparison"]
    schedule_comparison["dynamic"].update(status="completed", holdout=stats)
    fixed_report = schedule_comparison["fixed_ab"]
    fixed_outputs = {}
    if any(row["below_range"] or row["at_or_above_range"] for row in fixed_headroom.values()):
        fixed_report["status"] = "adc2_range_blocked"
        fixed_report["holdout"] = None
    else:
        fixed_off = fixed_capture_off.quantized(cfg)
        fixed_on = fixed_capture_on.quantized(cfg)
        fixed_outputs = _four_modes(fixed_off, fixed_on, frozen, threshold_v, experiment, cfg)
        fixed_stats = {
            mode: calibration_metrics(output, hold_v, experiment)
            for mode, output in fixed_outputs.items()
        }
        fixed_report.update(status="completed", holdout=fixed_stats)
        _plot_schedule_comparison(out / "schedule_dem_lms.png", stats, fixed_stats, experiment, cfg)
        fixed_outputs.update(adc2_code_off=fixed_off.adc2_code, adc2_code_on=fixed_on.adc2_code)

    strict_fixed, strict_arrays = _strict_fixed_training(
        pool,
        (gain_low_v, gain_high_v),
        gain_masks,
        fit_v,
        fit_off,
        hold_v,
        fixed_capture_off,
        threshold_v,
        experiment,
        cfg,
    )
    strict_dynamic = {
        "status": "completed",
        "training_dem_on_samples_per_pass": int(choose_on.sum()),
        "training_dem_off_samples_per_pass": int((~choose_on).sum()),
        "holdout_dem_enabled": True,
        "gain_reference_pairs": cfg.gain_pairs,
        "training_samples_per_pass": cfg.training_samples,
        "passes": cfg.lms_passes,
        "update_count": cfg.training_samples * cfg.lms_passes,
        "gain_design": gain_report,
        "physical_weight_design": lms_report,
        "learning": learning,
        "holdout": stats["dem_and_lms"],
    }
    summary["strict_training_comparison"] = {
        "status": strict_fixed["status"],
        "fixed_ab_lms_only": strict_fixed,
        "dynamic_dem_and_lms": strict_dynamic,
    }
    if strict_fixed["status"] == "completed":
        _plot_strict_comparison(
            out / "strict_lms_comparison.png",
            strict_fixed["holdout"],
            strict_dynamic["holdout"],
            experiment,
            cfg,
        )
    with (out / "learning.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(learning[0]))
        writer.writeheader()
        writer.writerows(learning)

    toolbox_arrays = {}
    if toolbox_coefficients is not None:
        weights, dc_offset = toolbox_coefficients
        toolbox_report["holdout"] = {}
        for name, obs in (("dem_off", hold_off), ("dem_on", hold_on)):
            output_v = _features(obs, gain, threshold_v, experiment, cfg) @ weights - dc_offset
            toolbox_report["holdout"][name] = calibration_metrics(output_v, hold_v, experiment)
            toolbox_arrays[f"toolbox_{name}_v"] = output_v
        toolbox_arrays.update(toolbox_weights_v=weights, toolbox_offset_v=dc_offset)
    np.savez_compressed(
        out / "observations.npz",
        nominal_threshold_v=threshold_v,
        gain_plan=gain_plan,
        gain_low_v=gain_low_v,
        gain_high_v=gain_high_v,
        gain_adc2_low=gain_low_code,
        gain_adc2_high=gain_high_code,
        gain_per_slice=gain,
        fit_plan=fit_obs.plan,
        fit_reference_v=fit_v,
        fit_dem_enabled=choose_on,
        fit_coarse_code=fit_obs.codes,
        fit_main_mask=fit_obs.main,
        fit_sub_mask=fit_obs.sub,
        fit_adc2_code=fit_obs.adc2_code,
        frozen_switch_correction_v=correction,
        evaluation_reference_v=hold_v,
        evaluation_plan=hold_plan,
        evaluation_coarse_code=hold_off.codes,
        evaluation_main_mask_off=hold_off.main,
        evaluation_sub_mask_off=hold_off.sub,
        evaluation_adc2_code_off=hold_off.adc2_code,
        evaluation_main_mask_on=hold_on.main,
        evaluation_sub_mask_on=hold_on.sub,
        evaluation_adc2_code_on=hold_on.adc2_code,
        evaluation_fixed_plan=fixed_plan,
        **{f"{name}_residue_v": trace for name, trace in captures.items()},
        **{f"fixed_{name}_residue_v": trace for name, trace in fixed_captures.items()},
        **{f"fixed_{name}": trace for name, trace in fixed_outputs.items()},
        **strict_arrays,
        **outputs,
        **toolbox_arrays,
    )

    return _write_summary(
        out,
        {
            **summary,
            "status": "completed",
            "gain_design": gain_report,
            "nlms_weight_design": lms_report,
            "training_rank_counterexamples": counterexamples,
            "learning": learning,
            "toolbox_sine_weight_reference": toolbox_report,
            "holdout": stats,
        },
    )


# ------------------------------------------------------------------------
def main() -> None:
    """执行可复现实验并打印留出指标。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--sigma", type=float, default=0.001, help="单位电容相对失配标准差")
    parser.add_argument(
        "--adc2-half-range-mv", type=float, default=50.0, help="ADC2 输入等效半量程，单位 mV"
    )
    args = parser.parse_args()
    summary = run(args.out, args.sigma, args.adc2_half_range_mv / 1000)
    if summary["status"] != "completed":
        print(
            json.dumps(
                {key: summary[key] for key in ("status", "blocked_stages", "adc2_range_checks")},
                indent=2,
            )
        )
        raise SystemExit(2)
    print(json.dumps(summary["holdout"], indent=2))


if __name__ == "__main__":
    main()
