"""明确数字驱动跳变的线性电荷耦合；不从ADC码猜测串扰或参考误差。

源文献点名串扰不等于公开了耦合电容、驱动边沿或物理路由。
此处只计算调用方明确给出的C*ΔV及节点电容归一化。没有旧三角活动率、预设空间梯度、DEM必然有效的假定，
也不把代数自洽当作器件串扰验证。

输入建立由input_network的共享源KCL承担；参考—RA—ADC2由conversion承担。
旧输入/参考aggregate代理和通用三项合成入口已移除。
"""

from __future__ import annotations

import numpy as np


def switching_activity(previous, current) -> np.ndarray:
    """统计实际布尔驱动mask在末轴上的变化数，不推断物理电荷。

    Args:
        previous: (...,D)前一实际布尔连接/逻辑状态。
        current: 同形当前布尔状态；驱动轴D必须由调用方绑定。

    Returns:
        (...)整数变化次数。相同逻辑数目不保证相同电荷或相同模拟误差。

    Raises:
        ValueError: 非布尔、空驱动轴或shape不匹配。
    """
    before, after = np.asarray(previous), np.asarray(current)
    if (
        before.ndim < 1
        or before.shape[-1] == 0
        or before.shape != after.shape
        or before.dtype != np.dtype(bool)
        or after.dtype != np.dtype(bool)
    ):
        raise ValueError("switching activity requires matching actual Boolean driver masks")
    return np.count_nonzero(before ^ after, axis=-1)


def crosstalk_error(
    *, coupling_cap_f, digital_voltage_change_v, output_capacitance_f
) -> np.ndarray:
    """给定真实驱动跳变，求指定节点的线性耦合电压误差。

    Args:
        coupling_cap_f: 可广播到(...,D)的实际非负耦合电容[F]。
        digital_voltage_change_v: (...,D)实际有符号驱动电压变化[V]。
            正跳变通过正电容向该节点注入正电荷；差分极性由真实节点绑定。
        output_capacitance_f: 可广播到(...)的正节点等效电容[F]；
            不能默认用整池电容、C_signal或任意预算值替代。

    Returns:
        (...)节点电压[V]=sum(C_coupling*ΔV)/C_out。不自动换算为外部输入误差，
        也不证明驱动时间波形、版图寄生或带内噪声。

    Raises:
        ValueError: 缺失物理绑定、非实数/非有限量、负电容或非法维度。
    """
    if any(
        value is None for value in (coupling_cap_f, digital_voltage_change_v, output_capacitance_f)
    ):
        raise ValueError(
            "coupling caps [F], actual driver voltage changes [V] and node capacitance [F] are required"
        )
    raw = [
        np.asarray(value)
        for value in (coupling_cap_f, digital_voltage_change_v, output_capacitance_f)
    ]
    if any(not np.issubdtype(value.dtype, np.number) or np.iscomplexobj(value) for value in raw):
        raise ValueError("crosstalk inputs must be finite real numeric arrays")
    caps, dv, c_out = (np.asarray(value, dtype=float) for value in raw)
    if (
        dv.ndim < 1
        or dv.shape[-1] == 0
        or any(np.any(~np.isfinite(value)) for value in (caps, dv, c_out))
        or np.any(caps < 0)
        or np.any(c_out <= 0)
    ):
        raise ValueError(
            "crosstalk requires actual driver axes, nonnegative coupling and positive node capacitance"
        )
    try:
        coupling = np.broadcast_to(caps, dv.shape)
        output = np.broadcast_to(c_out, dv.shape[:-1])
    except ValueError as error:
        raise ValueError(
            "crosstalk capacitance shapes must match the explicit driver/node axes"
        ) from error
    return np.sum(coupling * dv, axis=-1) / output
