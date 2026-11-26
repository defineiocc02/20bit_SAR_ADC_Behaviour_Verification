# ==============================================================================
# 文件名     : sine_calibration.py
# 模块名     : ad4080_otp_cal.sine_calibration
# 版本       : v1.0
# 功能       : 将 ADCToolbox 正弦波前景标定思想（fgCalSine / wcalsin）扩展至
#              AD4080 18 片（360 物理权重）+ 二维 DEM + 6-bit Dither + ADC2 架构。
# 设计说明   :
#   * [为什么用正弦前景标定] 无需 20 位超精密直流阶梯源，仅需单音相干低失真正弦波，
#     联立求解 360 个物理权重误差 Δw、直流失调 b 以及未知输入正弦幅相 (c1, c2)。
#   * [两轮迭代消解 G 耦合] 由于每笔归一化分母 G[n] 本身依赖本笔激活 8 片的主阵权重和，
#     采用 2 轮 Gauss-Newton 线性化迭代：每轮用上一轮权重更新 G[n]，再用带微小岭正则
#     (Ridge) 的 SVD 最小二乘求解 Δw，并锚定主阵平均单位电容为 1.0 消除全局缩放自由度。
#   * [防过拟合检验] 默认按 80%/20% 划分训练集与独立留出集（Holdout），显式报告矩阵秩、
#     条件数及留出集残差 RMS，确保解出的 360 权重可安全写入 OTP。
# 变更记录   :
#   2026-11-26  v1.0  初版实现。
# ==============================================================================
"""AD4080 360 物理权重正弦前景标定引擎（ADCToolbox fgCalSine 扩展版）。"""

from __future__ import annotations

from dataclasses import dataclass
import numpy as np

from .architecture import (
    MAIN_PER_SLICE,
    NUM_SLICES,
    TOTAL_WEIGHTS,
    WEIGHTS_PER_SLICE,
    CaptureDataset,
    build_physical_activity_matrix,
    nominal_weights_18x20,
    reconstruct_wsum_v21,
)


@dataclass(frozen=True)
class SineCalibrationResult:
    """360 物理权重正弦前景标定结果与诊断指标。"""

    weights_18x20: np.ndarray        # 标定后的 18×20 物理权重（单位：Cu_main）
    offset_est: float                # 估计直流失调（归一化满量程）
    sine_cos_amp: float              # 估计输入正弦 cos 分量幅值
    sine_sin_amp: float              # 估计输入正弦 sin 分量幅值
    matrix_rank: int                 # 设计矩阵有效秩（满秩 363 = 360 权重 + cos/sin/offset）
    condition_number: float          # 奇异值条件数
    train_rms_error: float           # 训练集拟合残差 RMS（归一化满量程）
    holdout_rms_error: float         # 独立留出集泛化残差 RMS（归一化满量程）
    calibrated_codes_20b: np.ndarray # 使用标定后权重重构的 20-bit 码流


def calibrate_360_weights_sine(
    dataset: CaptureDataset,
    fin_hz: float | None = None,
    ridge_lambda: float = 1e-7,
    holdout_ratio: float = 0.2,
    n_iters: int = 2,
) -> SineCalibrationResult:
    """基于单音相干正弦激励，联合辨识 AD4080 全部 360 个物理电容权重与失调。

    Args:
        dataset: CaptureDataset，包含 N 笔 (active_ids, post_mask, pre_mask, adc2_residue)。
        fin_hz: 正弦输入频率（Hz）；若为 None 则使用 dataset.fin_hz。
        ridge_lambda: 岭正则化系数（防止极少数未被充分激励的边缘列病态发散）。
        holdout_ratio: 独立留出集比例（默认 0.2，即每 5 笔抽 1 笔做泛化检验）。
        n_iters: 归一化分母 G[n] 更新迭代次数（默认 2 轮即可收敛至亚 ppm）。

    Returns:
        SineCalibrationResult，含 18×20 标定权重、诊断秩/条件数及校准后 20-bit 码流。
    """
    f_in = float(dataset.fin_hz if fin_hz is None else fin_hz)
    if f_in <= 0.0:
        raise ValueError("正弦标定必须提供大于 0 的输入频率 fin_hz。")

    n_samples = dataset.active_ids.shape[0]
    if n_samples < 1024:
        raise ValueError(f"360 参数辨识建议至少 1024 笔样本，当前仅 {n_samples} 笔。")

    # 1. 展开 N×360 物理开关设计矩阵 A
    a_mat = build_physical_activity_matrix(
        dataset.active_ids, dataset.post_mask, dataset.pre_mask
    )
    t_idx = np.arange(n_samples, dtype=np.float64)
    omega = 2.0 * np.pi * f_in / float(dataset.fs_hz)
    cos_vec = np.cos(omega * t_idx)
    sin_vec = np.sin(omega * t_idx)
    ones_vec = np.ones(n_samples, dtype=np.float64)

    # 均匀步进切分训练集与留出集（避免破坏时间上的全频段/全片组分布）
    stride = max(2, int(round(1.0 / max(holdout_ratio, 1e-3))))
    holdout_mask = (np.arange(n_samples) % stride) == (stride - 1)
    train_mask = ~holdout_mask

    w_cur = nominal_weights_18x20().reshape(TOTAL_WEIGHTS).copy()
    c_cos = 0.0
    c_sin = 0.0
    b_off = 0.0
    rank_val = 0
    cond_val = float("inf")

    # 2. 两轮 Gauss-Newton 迭代求解 Δw 与 (c_cos, c_sin, b_off)
    for _ in range(max(1, n_iters)):
        w_2d = w_cur.reshape(NUM_SLICES, WEIGHTS_PER_SLICE)
        g_row = np.sum(w_2d[:, :MAIN_PER_SLICE], axis=1)
        g_n = np.sum(g_row[dataset.active_ids], axis=1)  # (N,)

        # 当前权重下的初步估计值 y_cur = (A @ w_cur)/G + adc2_residue
        y_cur = (a_mat @ w_cur) / g_n + dataset.adc2_residue

        # 线性模型：c_cos*cos + c_sin*sin + b_off - (A / G) @ dw ≈ y_cur
        # 其中 dw 仅针对物理权重偏移，[cos, sin, 1] 不施加正则惩罚
        x_full = np.empty((n_samples, TOTAL_WEIGHTS + 3), dtype=np.float64)
        x_full[:, :TOTAL_WEIGHTS] = -(a_mat / g_n[:, None])
        x_full[:, TOTAL_WEIGHTS] = cos_vec
        x_full[:, TOTAL_WEIGHTS + 1] = sin_vec
        x_full[:, TOTAL_WEIGHTS + 2] = ones_vec

        x_tr = x_full[train_mask]
        y_tr = y_cur[train_mask]

        # 构造岭正则增广矩阵（只正则化 Δw 360 列，保持正弦基与失调无偏）
        reg_diag = np.zeros(TOTAL_WEIGHTS + 3, dtype=np.float64)
        reg_diag[:TOTAL_WEIGHTS] = np.sqrt(ridge_lambda)

        # 通过 SVD 求解正规方程（兼顾数值稳定与秩/条件数诊断）
        u_mat, s_vals, vt_mat = np.linalg.svd(x_tr, full_matrices=False)
        s_max = float(s_vals[0]) if len(s_vals) > 0 else 1.0
        rank_val = int(np.sum(s_vals > s_max * 1e-10))
        cond_val = float(s_max / max(float(s_vals[-1]), 1e-15))

        # 岭正则化求解：(X^T X + Λ) θ = X^T y
        xtx = x_tr.T @ x_tr + np.diag(reg_diag ** 2)
        xty = x_tr.T @ y_tr
        theta = np.linalg.solve(xtx, xty)

        dw = theta[:TOTAL_WEIGHTS]
        c_cos = float(theta[TOTAL_WEIGHTS])
        c_sin = float(theta[TOTAL_WEIGHTS + 1])
        b_off = float(theta[TOTAL_WEIGHTS + 2])

        # 更新权重并锚定主阵平均单位电容为 1.0（消除全局分子/分母共线漂移）
        w_next = (w_cur + dw).reshape(NUM_SLICES, WEIGHTS_PER_SLICE)
        mean_main = float(np.mean(w_next[:, :MAIN_PER_SLICE]))
        if mean_main > 0.0:
            w_next = w_next / mean_main
        w_cur = w_next.reshape(TOTAL_WEIGHTS)

    w_final_2d = w_cur.reshape(NUM_SLICES, WEIGHTS_PER_SLICE)
    x_hat_all, codes_cal = reconstruct_wsum_v21(
        dataset.active_ids,
        dataset.post_mask,
        dataset.pre_mask,
        dataset.adc2_residue,
        w_final_2d,
        offset=-b_off,
    )

    # 3. 训练集与独立留出集残差评估（对照拟合出的纯单音正弦基）
    sine_fit = c_cos * cos_vec + c_sin * sin_vec
    residual = x_hat_all - sine_fit
    train_rms = float(np.sqrt(np.mean(residual[train_mask] ** 2)))
    holdout_rms = float(np.sqrt(np.mean(residual[holdout_mask] ** 2)))

    return SineCalibrationResult(
        weights_18x20=w_final_2d,
        offset_est=b_off,
        sine_cos_amp=c_cos,
        sine_sin_amp=c_sin,
        matrix_rank=rank_val,
        condition_number=cond_val,
        train_rms_error=train_rms,
        holdout_rms_error=holdout_rms,
        calibrated_codes_20b=codes_cal,
    )
