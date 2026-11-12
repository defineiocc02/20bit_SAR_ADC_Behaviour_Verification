"""显式理想分段网络的独立电荷参考计算。

采样时两个顶板节点均钳位为零。放大时主节点为理想虚地，子节点浮置，桥接保持连接，
反馈电容从复位状态开始。这些是已声明的参考边界条件，不是恢复出的芯片网表。
全部电容与底板电压由调用方提供，不推断单元数、参考轨、掩码或 dither 定律；物理值只用于此验证参考。
"""

from __future__ import annotations

import math
from numbers import Real

import numpy as np


def _network_inputs(
    main_cap_f,
    sub_cap_f,
    bridge_cap_f,
    sub_parasitic_f,
    feedback_cap_f,
    sample_main_v,
    sample_sub_v,
    conversion_main_v,
    conversion_sub_v,
):
    """检查维数与单位，不生成开关模式。"""
    arrays = []
    for name, value in (("main_cap_f", main_cap_f), ("sub_cap_f", sub_cap_f)):
        if value is None or np.iscomplexobj(value):
            raise ValueError(f"charge oracle requires explicit real {name} [F]")
        cap = np.asarray(value, dtype=float)
        if cap.ndim != 1 or not cap.size or np.any(~np.isfinite(cap)) or np.any(cap <= 0):
            raise ValueError(
                f"{name} must be a finite positive one-dimensional capacitance array [F]"
            )
        arrays.append(cap)
    for name, value, allow_zero in (
        ("bridge_cap_f", bridge_cap_f, False),
        ("sub_parasitic_f", sub_parasitic_f, True),
        ("feedback_cap_f", feedback_cap_f, False),
    ):
        if (
            not isinstance(value, Real)
            or isinstance(value, bool)
            or not math.isfinite(float(value))
        ):
            raise ValueError(f"charge oracle requires explicit finite {name} [F]")
        scalar = float(value)
        if scalar < 0 or (scalar == 0 and not allow_zero):
            raise ValueError(f"{name} has an invalid capacitance [F]")
    voltages = []
    for name, value, count in (
        ("sample_main_v", sample_main_v, arrays[0].size),
        ("sample_sub_v", sample_sub_v, arrays[1].size),
        ("conversion_main_v", conversion_main_v, arrays[0].size),
        ("conversion_sub_v", conversion_sub_v, arrays[1].size),
    ):
        if value is None or np.iscomplexobj(value):
            raise ValueError(f"charge oracle requires explicit real {name} [V]")
        voltage = np.asarray(value, dtype=float)
        if voltage.ndim < 1 or voltage.shape[-1] != count or np.any(~np.isfinite(voltage)):
            raise ValueError(f"{name} must contain one finite voltage per physical unit [V]")
        voltages.append(voltage)
    leading = np.broadcast_shapes(*(voltage.shape[:-1] for voltage in voltages))
    voltages = [np.broadcast_to(voltage, (*leading, voltage.shape[-1])) for voltage in voltages]
    return *arrays, *voltages


def ref_ra_charge(
    *,
    main_cap_f,
    sub_cap_f,
    bridge_cap_f,
    sub_parasitic_f,
    feedback_cap_f,
    sample_main_v,
    sample_sub_v,
    conversion_main_v,
    conversion_sub_v,
) -> np.ndarray:
    """返回所声明网络的闭式反馈电荷 C_F*v_R [C]。

    底板电压形状为 (..., physical units) [V]，电容数组为 (physical units,) [F]。
    桥接、寄生与反馈电容是显式标量 [F]；输出为广播后的记录形状 (...)。
    """
    cm, cs, wm, ws, bm, bs = _network_inputs(
        main_cap_f,
        sub_cap_f,
        bridge_cap_f,
        sub_parasitic_f,
        feedback_cap_f,
        sample_main_v,
        sample_sub_v,
        conversion_main_v,
        conversion_sub_v,
    )
    beta = bridge_cap_f / (bridge_cap_f + cs.sum() + sub_parasitic_f)
    return np.sum(cm * (wm - bm), axis=-1) + beta * np.sum(cs * (ws - bs), axis=-1)


def ref_ra_charge_nodal(
    *,
    main_cap_f,
    sub_cap_f,
    bridge_cap_f,
    sub_parasitic_f,
    feedback_cap_f,
    sample_main_v,
    sample_sub_v,
    conversion_main_v,
    conversion_sub_v,
) -> np.ndarray:
    """独立组装并求解两节点方程，返回 C_F*v_R [C]。

    未知量为浮置子节点电压和 RA 输出电压 [V]。矩阵使用显式声明的理想边界条件，
    不使用拟合增益或闭式 beta 表达式。
    """
    cm, cs, wm, ws, bm, bs = _network_inputs(
        main_cap_f,
        sub_cap_f,
        bridge_cap_f,
        sub_parasitic_f,
        feedback_cap_f,
        sample_main_v,
        sample_sub_v,
        conversion_main_v,
        conversion_sub_v,
    )
    shape = wm.shape[:-1]
    matrix = np.zeros((*shape, 2, 2))
    matrix[..., 0, 0] = bridge_cap_f + cs.sum() + sub_parasitic_f
    matrix[..., 1, 0] = bridge_cap_f
    matrix[..., 1, 1] = feedback_cap_f
    rhs = np.stack([np.sum(cs * (bs - ws), axis=-1), np.sum(cm * (wm - bm), axis=-1)], axis=-1)
    solution = np.linalg.solve(matrix, rhs[..., None])[..., 0]
    return feedback_cap_f * solution[..., 1]
