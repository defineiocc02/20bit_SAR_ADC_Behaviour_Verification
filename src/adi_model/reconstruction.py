"""显式电压域重构与外部提供的数字状态。

fine_voltage 是 ADC2 在 RA 输出节点解码的电压 [V]；
vd_nominal 与 dither_correction 为输入等效电压 [V]，增益与信号比例无量纲。这些算术不辨识电路参数，
也不实现片上学习器；逐单元冻结有效 C/Cf 重构见 weight_calibration。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np

from adi_model.config import Config

if TYPE_CHECKING:
    from adi_model.weight_calibration import FrozenCalibration


@dataclass
class DigitalState:
    """数字增益与可选的外部推导不可变权重集。"""

    estimated_gain: float
    weight_calibration: FrozenCalibration | None = None


def initialize_state(cfg: Config) -> DigitalState:
    """使用已声明的名义残差增益，不复制物理真值。"""
    if not np.isfinite(cfg.g0) or cfg.g0 <= 0:
        raise ValueError("digital state requires a finite positive nominal residue gain [V/V]")
    return DigitalState(estimated_gain=cfg.g0)


def reconstruct(
    vd_nominal: np.ndarray,
    fine_voltage: np.ndarray,
    g_hat: float,
    dither_correction: np.ndarray,
    alpha: float,
) -> np.ndarray:
    """返回 (vd_nominal + fine_voltage/g_hat - correction)/alpha [V]。

    调用方提供当前记录的增益、注入与信号比例。除非显式选择的独立校准已经修正，
    静态权重失配及电路动态仍保留在结果中。
    """
    if not np.isfinite(g_hat) or g_hat <= 0:
        raise ValueError("reconstruction gain must be finite and positive [V/V]")
    if not np.isfinite(alpha) or alpha <= 0:
        raise ValueError("reconstruction signal fraction must be finite and positive [unitless]")
    vd, fine, correction = np.broadcast_arrays(
        np.asarray(vd_nominal, dtype=float),
        np.asarray(fine_voltage, dtype=float),
        np.asarray(dither_correction, dtype=float),
    )
    if not all(np.all(np.isfinite(value)) for value in (vd, fine, correction)):
        raise ValueError("reconstruction voltage records must be finite [V]")
    return (vd + fine / g_hat - correction) / alpha
