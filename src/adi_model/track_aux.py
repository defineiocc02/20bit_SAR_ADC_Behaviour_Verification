"""TRACK 预置与 AUX 辅助电荷路径：只做电荷记账与因果审计。

两种机制解决的是同一件事——**不要把满摆幅信号电荷每次都从驱动源抽走**：

* TRACK（US10797889 的预跟踪思想）：转换组之外的空闲片，在本次孔径之前把底板预置到
  "上一次可用判决"对应的状态，于是采样时输入源只需补 (x[n] - x_pred)。
* AUX（US10541702）：一条独立的辅助输入副本承担大部分电荷，主驱动只承担 1/k。

本模块不产生运放、不做 RC 积分，也不声称建立时间。它给三件事：

1. `build_track_plan`：目标片、来源样本、来源**就绪时刻**、目标**预置时刻**；
2. `input_charge`：用实际逐片电容算 |dQ|，失配会自然出现在预测残差里；
3. `causality_audit`：来源被否决的判据是**时间**（ready < precharge）与前缀，
   不是"预测值是否恰好等于目标值"——恒定直流输入合法地满足后者（外部审查 A03）。

`values_v` 必须由**已可见的数字码**加名义/冻结码本重建，不能传 plant 真值；
本模块只检查形状，来源由调用方在文档与测试里证明。

量纲（外部审查 A05）：正弦相邻样本之差是 `2A·sin(pi f/fs)`，这是**峰值**；
峰峰值是它的两倍。`abs(Q)*fs` 是电荷吞吐代理，不是瞬态峰值电流，也不是电源功耗。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from adi_model.config import ConfigError
from adi_model.dem import _integer_array


@dataclass(frozen=True)
class TrackPlan:
    """每笔的预置目标、来源、来源就绪时刻与预置时刻。

    Attributes:
        target_slices: (N,A) 目标采集组物理 ID。
        source_sample: (N,) 来源样本索引；-1 表示无合法来源。
        source_codes: (N,) 来源样本的已决码；无效处为 -1。
        predicted_v: (N,) 由来源码重建的预测输入 [V]；无效处为 NaN。
        source_ready_s: (N,) 来源判决可用时刻 [s]；无效处为 NaN。
        precharge_s: (N,) 目标预置开始时刻 [s]；无效处为 NaN。
        valid: (N,) 是否真的做了预置（无来源或时间不满足时不得假装预置了 0 V）。
    """

    target_slices: np.ndarray
    source_sample: np.ndarray
    source_codes: np.ndarray
    predicted_v: np.ndarray
    source_ready_s: np.ndarray
    precharge_s: np.ndarray
    valid: np.ndarray


def build_track_plan(
    plan,
    codes,
    *,
    values_v,
    fs_hz: float,
    track_lead_s: float,
    lookback: int = 1,
) -> TrackPlan:
    """用第 n-lookback 笔的已决结果预置第 n 笔的采集组。

    时间合同：来源判决在第 `(source+1)/fs` 时刻可用（转换结束），目标预置在
    `n/fs - track_lead_s` 开始。只有 `ready < precharge` 才允许预置；
    否则 `valid=False`，预测值留 NaN——不允许用 0 V 冒充一次真实预置。

    Args:
        plan: SlicePlan；使用其 acq 组作为预置目标。
        codes: (N,) 每笔已决粗码，仅用于记录来源。
        values_v: (N,) 由数字码重建的输入电压 [V]；不得传 plant 真值。
        fs_hz: 采样率 [Hz]，用于把样本序号换成时刻。
        track_lead_s: 预置提前量 [s]，必须非负且小于一个周期。
        lookback: 回看笔数，>=1。

    Returns:
        TrackPlan。

    Raises:
        ConfigError: 维度不符、lookback 非正或时间参数非法。
    """
    acq = _integer_array(plan.acq, "track target slices", ndim=2)
    resolved = _integer_array(codes, "track source codes", ndim=1)
    values = np.asarray(values_v, dtype=float)
    if resolved.shape != (acq.shape[0],) or values.shape != resolved.shape:
        raise ConfigError("track plan needs one code and one value per sample")
    if type(lookback) is not int or lookback < 1:
        raise ConfigError("track lookback must be a positive integer")
    if not np.isfinite(fs_hz) or fs_hz <= 0:
        raise ConfigError("track plan needs a positive sample rate")
    if not np.isfinite(track_lead_s) or not 0 <= track_lead_s < 1 / fs_hz:
        raise ConfigError("track lead must be in [0, one sample period)")
    n = acq.shape[0]
    index = np.arange(n, dtype=np.int64)
    source = index - lookback
    source_ok = source >= 0
    safe_source = np.where(source_ok, source, 0)
    ready = np.where(source_ok, (safe_source + 1) / fs_hz, np.nan)
    precharge = index / fs_hz - track_lead_s
    time_ok = source_ok & (ready < precharge)
    return TrackPlan(
        target_slices=acq.copy(),
        source_sample=np.where(source_ok, source, -1).astype(np.int64),
        source_codes=np.where(source_ok, resolved[safe_source], -1).astype(np.int64),
        predicted_v=np.where(source_ok, values[safe_source], np.nan),
        source_ready_s=ready,
        precharge_s=precharge,
        valid=time_ok,
    )


def causality_audit(track: TrackPlan) -> dict:
    """检查来源严格早于目标，且来源判决在预置开始前已就绪。

    不使用"预测值等于目标值"作判据：恒定直流输入下合法预测恰好相等，
    那会把正常行为误判为偷看（外部审查 A03 的最小反例）。

    Args:
        track: 预置计划。

    Returns:
        dict：`future_sources`、`self_sources`、`late_sources`（就绪晚于预置）
        三个违约计数必须全为 0，`causal` 才为 True。
    """
    source = np.asarray(track.source_sample, dtype=np.int64)
    index = np.arange(source.size, dtype=np.int64)
    valid = np.asarray(track.valid, dtype=bool)
    ready = np.asarray(track.source_ready_s, dtype=float)
    precharge = np.asarray(track.precharge_s, dtype=float)
    future = int(np.count_nonzero(valid & (source >= index)))
    self_sources = int(np.count_nonzero(source == index))
    # "晚了"要按**所有**有来源的样本统计：即使调用方把 valid 关掉，
    # 一个 ready>=precharge 的来源也说明这份计划本身不因果。
    has_source = source >= 0
    late = int(np.count_nonzero(has_source & ~(ready < precharge)))
    return {
        "samples": int(source.size),
        "valid_sources": int(np.count_nonzero(valid)),
        "blocked_by_lookback": int(np.count_nonzero(source < 0)),
        "future_sources": future,
        "self_sources": self_sources,
        "late_sources": late,
        "causal": future == 0 and self_sources == 0 and late == 0,
    }


def track_residual_charge(
    pool,
    track: TrackPlan,
    target_values_v,
    *,
    capacity_f=None,
) -> dict:
    """用实际逐片电容算"预置后仍需输入源补的电荷"。

    预置使每片的起始电荷等于 `C_i * x_pred`；采样结束时为 `C_i * x_now`。
    因此输入源只需补 `C_i * (x_now - x_pred)`。

    Args:
        pool: 提供逐片 `C_main/C_sub` 的池（几何只需 n_active）。
        track: 预置计划。
        target_values_v: (N,) 目标输入 [V]。
        capacity_f: 可选 (N,A) 覆盖电容；None 用池的 Signal 归一化电容。

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
    """把每笔电荷换成**平均**电流 [A]；这是电荷吞吐代理，不是瞬态峰值电流。"""
    if not np.isfinite(fs_hz) or fs_hz <= 0:
        raise ConfigError("input current needs a positive sample rate")
    if not np.isfinite(charge_c) or charge_c < 0:
        raise ConfigError("input current needs a nonnegative finite charge")
    return float(charge_c * fs_hz)


def consecutive_sample_charge(c_total_f: float, amplitude_v: float, fin_hz: float, fs_hz: float) -> dict:
    """正弦相邻样本差分的电荷与电流（峰峰值与峰值分开给）。

    相邻样本电压差 `x[n]-x[n-1] = 2A·sin(pi f/fs)·cos(...)`：

    * `delta_v_peak` = `2A·sin(pi f/fs)`，这是**峰值**；
    * `delta_v_pp`   = 2 倍，**峰峰值**。

    早期版本把峰值标成 pp，导致电流与"反解频率"都差 2 倍（外部审查 A05）。

    Args:
        c_total_f: 采样总电容 [F]。
        amplitude_v: 正弦峰值 [V]。
        fin_hz: 输入频率 [Hz]。
        fs_hz: 采样率 [Hz]。

    Returns:
        dict：峰值/峰峰值的电压、电荷与电流，另给正弦平均电流。
    """
    for name, value in (("c_total_f", c_total_f), ("amplitude_v", amplitude_v),
                        ("fin_hz", fin_hz), ("fs_hz", fs_hz)):
        if not np.isfinite(value) or value <= 0:
            raise ConfigError(f"{name} must be finite positive")
    if fin_hz >= fs_hz / 2:
        raise ConfigError("this formula assumes fin below Nyquist")
    delta_peak = 2 * amplitude_v * np.sin(np.pi * fin_hz / fs_hz)
    delta_pp = 2 * delta_peak
    return {
        "fin_hz": float(fin_hz),
        "delta_v_peak": float(delta_peak),
        "delta_v_pp": float(delta_pp),
        "charge_c_peak": float(c_total_f * delta_peak),
        "charge_c_pp": float(c_total_f * delta_pp),
        "current_a_peak": float(c_total_f * delta_peak * fs_hz),
        "current_a_pp": float(c_total_f * delta_pp * fs_hz),
        "mean_current_a": float(c_total_f * delta_pp * 2 / np.pi * fs_hz / 2),
    }


def peak_current_for_dc_step(c_total_f: float, step_v: float, fs_hz: float) -> float:
    """直流电平切换时的电荷吞吐峰值电流上界 [A]（不是论文那条曲线的口径）。

    论文 Fig.9.8.5 的输入电流横轴是**直流差分输入电压**，不是正弦频率；
    用正弦差分去反解"多少 Hz 对应 350 uA"是不成立的（见 06 文档的更正）。
    本函数只给"电平跳变时每周期搬运 C*ΔV"的吞吐上界，供驱动预算参考。
    """
    for name, value in (("c_total_f", c_total_f), ("step_v", step_v), ("fs_hz", fs_hz)):
        if not np.isfinite(value) or value <= 0:
            raise ConfigError(f"{name} must be finite positive")
    return float(c_total_f * step_v * fs_hz)


__all__ = [
    "TrackPlan",
    "build_track_plan",
    "causality_audit",
    "consecutive_sample_charge",
    "input_current_a",
    "peak_current_for_dc_step",
    "track_residual_charge",
]
