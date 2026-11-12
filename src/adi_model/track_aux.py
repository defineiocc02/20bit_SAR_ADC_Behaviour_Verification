"""TRACK 预置与 AUX 辅助电荷路径：只做电荷记账与因果审计。

两种机制解决的是同一件事——**不要把满摆幅信号电荷每次都从驱动源抽走**：

* TRACK（US10797889 的预跟踪思想）：转换组之外的空闲片，在本次孔径之前把底板预置到
  "上一次可用判决"对应的状态，于是采样时输入源只需补 (x[n] - x_pred)，而不是 x[n]。
  预置电荷来自参考轨/预充缓冲，不来自输入源。
* AUX（US10541702）：一条独立的辅助输入副本承担大部分电荷，主驱动只承担 1/k。

本模块不产生运放、不做 RC 积分，也不声称建立时间。它给三件事：

1. `build_track_plan`：目标片、来源样本、预测码，且来源必须**严格早于**目标；
2. `input_charge`：用实际逐片电容算 |dQ|，失配会自然出现在预测残差里；
3. `causality_audit`：来源样本必须早于目标样本，且不得等于目标本身。

关键量化口径：`input_current_a` 是"每笔 |dQ| × fs"，不是平均值滤波后的读数。
对正弦、相邻样本差分，精确式是 `2*A*sin(pi*f/fs)`，不是 `2*pi*f/fs*A` —— 后者在
f/fs 接近 0.5 时高估 57%，不能用来对论文的 350 uA 做结论。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .config import ConfigError
from .dem import _integer_array


@dataclass(frozen=True)
class TrackPlan:
    """每笔的预置目标、来源与预测值。

    Attributes:
        target_slices: (N,A) 目标采集组物理 ID。
        source_sample: (N,) 来源样本索引；-1 表示无合法来源。
        source_codes: (N,) 来源样本的已决码；无效处为 -1。
        predicted_v: (N,) 预测输入 [V]；无效处为 NaN。
        valid: (N,) 是否真的做了预置（无来源时不得假装预置了 0 V）。
    """

    target_slices: np.ndarray
    source_sample: np.ndarray
    source_codes: np.ndarray
    predicted_v: np.ndarray
    valid: np.ndarray


def build_track_plan(
    plan,
    codes,
    *,
    values_v,
    lookback: int = 1,
) -> TrackPlan:
    """用第 n-lookback 笔的已决结果预置第 n 笔的采集组。

    来源索引严格小于目标索引；前 lookback 笔没有合法来源，`valid=False`，
    预测值留 NaN——不允许用 0 V 冒充一次真实预置（那会把电荷算小）。

    Args:
        plan: SlicePlan；使用其 acq 组作为预置目标。
        codes: (N,) 每笔已决粗码，仅用于记录来源。
        values_v: (N,) 每笔对应的输入电压 [V]，由调用方从已决码重建，不是模拟真值。
        lookback: 回看笔数，>=1。

    Returns:
        TrackPlan。

    Raises:
        ConfigError: 维度不符或 lookback 非正。
    """
    acq = _integer_array(plan.acq, "track target slices", ndim=2)
    resolved = _integer_array(codes, "track source codes", ndim=1)
    values = np.asarray(values_v, dtype=float)
    if resolved.shape != (acq.shape[0],) or values.shape != resolved.shape:
        raise ConfigError("track plan needs one code and one value per sample")
    if type(lookback) is not int or lookback < 1:
        raise ConfigError("track lookback must be a positive integer")
    n = acq.shape[0]
    source = np.arange(n, dtype=np.int64) - lookback
    valid = source >= 0
    safe_source = np.where(valid, source, 0)
    return TrackPlan(
        target_slices=acq.copy(),
        source_sample=np.where(valid, source, -1).astype(np.int64),
        source_codes=np.where(valid, resolved[safe_source], -1).astype(np.int64),
        predicted_v=np.where(valid, values[safe_source], np.nan),
        valid=valid,
    )


def causality_audit(track: TrackPlan, *, target_values_v=None) -> dict:
    """检查预置来源严格早于目标，且没有把目标自身的值当成预测。

    Args:
        track: 预置计划。
        target_values_v: 可选 (N,) 目标真实输入；给出时额外检查
            "预测值不等于目标值"（等于说明偷看了目标）。

    Returns:
        dict：`valid_sources`、`future_sources`（必须为 0）、`self_sources`（必须为 0）、
        `cheating`（预测与目标逐位相同则为 True）。
    """
    source = np.asarray(track.source_sample, dtype=np.int64)
    index = np.arange(source.size, dtype=np.int64)
    valid = np.asarray(track.valid, dtype=bool)
    future = int(np.count_nonzero(valid & (source >= index)))
    negative = int(np.count_nonzero(~valid & (source >= 0)))
    result = {
        "samples": int(source.size),
        "valid_sources": int(np.count_nonzero(valid)),
        "future_sources": future,
        "invalid_with_index": negative,
        "self_sources": int(np.count_nonzero(valid & (source == index))),
        "causal": future == 0 and negative == 0,
    }
    if target_values_v is not None:
        target = np.asarray(target_values_v, dtype=float)
        if target.shape != source.shape:
            raise ConfigError("target values must match the track plan length")
        predicted = np.asarray(track.predicted_v, dtype=float)
        compared = valid & np.isfinite(predicted) & np.isfinite(target)
        result["cheating"] = bool(
            np.any(compared & (np.abs(predicted - target) <= 0.0))
        )
        result["causal"] = result["causal"] and not result["cheating"]
    return result


def track_residual_charge(
    pool,
    track: TrackPlan,
    target_values_v,
    *,
    capacity_f=None,
) -> dict:
    """用实际逐片电容算"预置后仍需输入源补的电荷"。

    预置使每片的起始电荷等于 `C_i * x_pred`；采样结束时为 `C_i * x_now`。
    因此输入源只需补 `C_i * (x_now - x_pred)`。失配让每片 C_i 不同，
    但同一片的差值仍按自身电容缩放，于是残差里保留真实电容信息。

    Args:
        pool: 提供逐片 `C_main/C_sub` 的池（几何只需 n_active）。
        track: 预置计划。
        target_values_v: (N,) 目标输入 [V]。
        capacity_f: 可选 (n_slices,A) 覆盖电容；None 用池的 Signal 归一化电容。

    Returns:
        dict：无预置与有预置的平均 |dQ|、改善倍数、以及无来源样本数。
    """
    target = np.asarray(target_values_v, dtype=float)
    valid = np.asarray(track.valid, dtype=bool)
    if target.shape != valid.shape:
        raise ConfigError("target values must match the track plan")
    if capacity_f is None:
        coefficients = pool.split_coefficients(track.target_slices)
        per_slice = np.asarray(coefficients["c_slice"], dtype=float)
    else:
        per_slice = np.asarray(capacity_f, dtype=float)
        if per_slice.shape != track.target_slices.shape:
            raise ConfigError("capacity override must match (N,A)")
    predicted = np.asarray(track.predicted_v, dtype=float)
    raw = np.abs(per_slice * target[:, None]).sum(axis=1)
    residual = np.abs(per_slice * (target - np.where(valid, predicted, 0.0))[:, None]).sum(axis=1)
    residual = np.where(valid, residual, raw)
    return {
        "samples": int(target.size),
        "samples_without_source": int(np.count_nonzero(~valid)),
        "mean_abs_charge_no_track_c": float(raw.mean()),
        "mean_abs_charge_with_track_c": float(residual.mean()),
        "charge_reduction": float(raw.mean() / residual.mean()) if residual.mean() else float("inf"),
        "max_residual_c": float(residual.max()),
    }


def input_current_a(charge_c: float, fs_hz: float) -> float:
    """把每笔电荷换成平均电流 [A]；这是记账推论，不是驱动能力结论。"""
    if not np.isfinite(fs_hz) or fs_hz <= 0:
        raise ConfigError("input current needs a positive sample rate")
    if not np.isfinite(charge_c) or charge_c < 0:
        raise ConfigError("input current needs a nonnegative finite charge")
    return float(charge_c * fs_hz)


def consecutive_sample_charge(c_total_f: float, amplitude_v: float, fin_hz: float, fs_hz: float) -> dict:
    """正弦相邻样本差分的精确电荷与电流（用于对照论文的输入电流）。

    相邻样本电压差为 `2*A*sin(pi*f/fs)`（不是 `2*pi*f*A/fs`）。
    返回每笔 |dQ| 的峰值与对应平均电流，并给出反解频率。

    Args:
        c_total_f: 采样总电容 [F]。
        amplitude_v: 正弦峰值 [V]。
        fin_hz: 输入频率 [Hz]。
        fs_hz: 采样率 [Hz]。

    Returns:
        dict：`delta_v_pp`、`charge_c_pp`、`current_a_pp`。
    """
    for name, value in (("c_total_f", c_total_f), ("amplitude_v", amplitude_v),
                        ("fin_hz", fin_hz), ("fs_hz", fs_hz)):
        if not np.isfinite(value) or value <= 0:
            raise ConfigError(f"{name} must be finite positive")
    if fin_hz >= fs_hz / 2:
        raise ConfigError("this formula assumes fin below Nyquist")
    delta = 2 * amplitude_v * np.sin(np.pi * fin_hz / fs_hz)
    charge = c_total_f * delta
    return {
        "fin_hz": float(fin_hz),
        "delta_v_pp": float(delta),
        "charge_c_pp": float(charge),
        "current_a_pp": float(charge * fs_hz),
        "charge_per_sample_mean_c": float(charge * 2 / np.pi),
        "mean_current_a": float(charge * 2 / np.pi * fs_hz),
    }


def solve_frequency_for_current(
    target_a_pp: float, c_total_f: float, amplitude_v: float, fs_hz: float
) -> float:
    """反解"多少输入频率才会给出这个峰值电流"，用于核对论文数值的口径。"""
    if not np.isfinite(target_a_pp) or target_a_pp <= 0:
        raise ConfigError("target current must be finite positive")
    delta = target_a_pp / (c_total_f * fs_hz)
    ratio = delta / (2 * amplitude_v)
    if not 0 < ratio < 1:
        raise ConfigError("target current is outside what this capacitance can deliver")
    return float(np.arcsin(ratio) * fs_hz / np.pi)


__all__ = [
    "TrackPlan",
    "build_track_plan",
    "causality_audit",
    "consecutive_sample_charge",
    "input_current_a",
    "solve_frequency_for_current",
    "track_residual_charge",
]
