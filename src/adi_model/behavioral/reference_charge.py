"""由实际采样连接和参考开关计算有符号端口电荷，不推断dither或位试时序。

求解图明确为一个共享浮置top、每slice一个浮置sub、主/子单位及桥接和到电压原点的寄生电容。
采样节点和底板电压全部由调用方给出。它不是RA虚地钳位图，也不是未公开芯片网表；
不能把本图浮置top直接当输入等效残差。

参考rail负载由各底板端电荷变化得到。即使一个连接保持不变，其他开关改变浮置节点也会从该rail取电；
不能仅用码差或正的二进制载荷替代。
本模块不读coarse/sid、Config.dither_mode、tail掩码或数字16tick窗口。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from adi_model._arrays import readonly


def _real_array(value, name: str) -> np.ndarray:
    if value is None:
        raise ValueError(f"{name} is required by explicit reference connectivity")
    array = np.asarray(value)
    if not np.issubdtype(array.dtype, np.number) or np.iscomplexobj(array):
        raise ValueError(f"{name} must be a finite real numeric array")
    result = np.asarray(array, dtype=float)
    if np.any(~np.isfinite(result)):
        raise ValueError(f"{name} must be finite")
    return result


def _broadcast(value, shape, name: str, *, required=True) -> np.ndarray:
    if value is None and not required:
        # 无单元连接到此轨；该占位不赋给任何实际底板。
        return np.zeros(shape)
    data = _real_array(value, name)
    try:
        return np.broadcast_to(data, shape)
    except ValueError as error:
        raise ValueError(f"{name} cannot broadcast to explicit node shape {shape}") from error


def sampled_bottom_voltages(
    main_connections,
    sub_connections,
    signal_v,
    *,
    ref_high_v,
    ref_low_v,
) -> tuple[np.ndarray, np.ndarray]:
    """把明确的采样连接译为底板电压；不选择任何保留单元。

    Args:
        main_connections: (...,M)整型连接，0=Signal、1=RefLo、2=RefHi。
        sub_connections: (...,S)同一批物理片的整型连接，前导维度必须相同。
        signal_v: 可广播到前导维度的实际孔径输入[V]；无Signal连接可显式None。
        ref_high_v: 采样时高参考节点[V]；未使用此轨可显式None。
        ref_low_v: 采样时低参考节点[V]；未使用此轨可显式None。

    Returns:
        两个只读底板电压数组，shape分别与main/sub连接相同。

    Raises:
        ValueError: 非整数/非法连接、维度不一致、缺失实际连接的节点电压。
    """
    main, sub = np.asarray(main_connections), np.asarray(sub_connections)
    if (
        main.ndim < 2
        or sub.ndim != main.ndim
        or main.shape[:-1] != sub.shape[:-1]
        or min(main.shape[-1], sub.shape[-1]) < 1
        or not np.issubdtype(main.dtype, np.integer)
        or not np.issubdtype(sub.dtype, np.integer)
        or np.any((main < 0) | (main > 2))
        or np.any((sub < 0) | (sub > 2))
    ):
        raise ValueError(
            "sampling connections require matching integer physical arrays with codes 0/1/2"
        )
    connections = (main, sub)
    values = (
        _broadcast(
            signal_v,
            main.shape[:-1],
            "signal_v [V]",
            required=any(np.any(c == 0) for c in connections),
        ),
        _broadcast(
            ref_low_v,
            main.shape[:-1],
            "ref_low_v [V]",
            required=any(np.any(c == 1) for c in connections),
        ),
        _broadcast(
            ref_high_v,
            main.shape[:-1],
            "ref_high_v [V]",
            required=any(np.any(c == 2) for c in connections),
        ),
    )
    result = []
    for connection in connections:
        voltage = np.full(connection.shape, np.nan)
        for code, value in enumerate(values):
            voltage = np.where(connection == code, value[..., None], voltage)
        result.append(readonly(voltage))
    return result[0], result[1]


@dataclass(frozen=True)
class ReferenceProgram:
    """一个明确浮节点连接图的事件结果，不是数字观测或芯片节点真值。

    times_s为(E,)，charge_c/sensitivity为(E,2)，两列按high/low轨排列。电荷为轨供给底板的有符号C；
    sensitivity是共享浮置top对同轨共同电压扰动的导数，不是输入残差的导数。
    bottom_charge_c为(A,M+S)末端电荷。floating_node_voltage_v为(E,A+1)，
    node_names指出top及各物理sub节点。normalization_capacitance_f是消元后的C_eff，
    不自动等同采样C_signal。
    """

    times_s: np.ndarray
    charge_c: np.ndarray
    sensitivity: np.ndarray
    bottom_charge_c: np.ndarray
    floating_node_voltage_v: np.ndarray
    slice_ids: np.ndarray
    node_names: tuple[str, ...]
    normalization_capacitance_f: float


def physical_reference_program(
    *,
    slice_ids,
    main_caps_f,
    sub_caps_f,
    bridge_caps_f,
    main_parasitic_f,
    sub_parasitic_f,
    main_on,
    sub_on,
    times_s,
    sampled_main_bottom_v,
    sampled_sub_bottom_v,
    sampled_top_v,
    sampled_subnode_v,
    ref_high_v,
    ref_low_v,
) -> ReferenceProgram:
    """独立求解实际参考mask和明确初始节点电荷的浮置网络。

    Args:
        slice_ids: (A,)唯一非负物理ID，匹配以下电容和mask的片次序。
        main_caps_f: (A,M)实际主单位电容[F]，有限且正。
        sub_caps_f: (A,S)实际子单位电容[F]，有限且正。
        bridge_caps_f: (A,)或标量桥接电容[F]，有限且正。
        main_parasitic_f: (A,)或标量top到电压原点寄生[F]；零须显式给出。
        sub_parasitic_f: (A,)或标量sub到电压原点寄生[F]；零须显式给出。
        main_on: (E,A,M)真实布尔mask，True接RefHi、False接RefLo。
        sub_on: (E,A,S)真实布尔mask，含义同main_on。
        times_s: (E,)从孔径起的非负非递减实际事件时刻[s]。
        sampled_main_bottom_v: (A,M)采样结束时实际底板电压[V]。
        sampled_sub_bottom_v: (A,S)采样结束时实际底板电压[V]。
        sampled_top_v: (A,)采样结束时各片top电压[V]，不默认为零。
        sampled_subnode_v: (A,)各片sub节点初值[V]，不默认为零。
        ref_high_v: 可广播至(E,A)的高参考电压[V]，未使用可显式None。
        ref_low_v: 可广播至(E,A)的低参考电压[V]，未使用可显式None。

    Returns:
        只读ReferenceProgram。每个事件合并top自由电荷，保留各片独立sub自由电荷；
        没有电荷传给未连接的“理想部分电容”。重复mask在轨电压也不变时产生零新负载，
        不去掉真实事件时间点。

    Raises:
        ValueError: 缺失物理输入、非布尔mask、维度/时间非法或非正电容图。
    """
    ids = np.asarray(slice_ids)
    cm = _real_array(main_caps_f, "main_caps_f [F]")
    cs = _real_array(sub_caps_f, "sub_caps_f [F]")
    if (
        ids.ndim != 1
        or ids.size == 0
        or not np.issubdtype(ids.dtype, np.integer)
        or np.any(ids < 0)
        or len(np.unique(ids)) != len(ids)
        or cm.ndim != 2
        or cs.ndim != 2
        or cm.shape[0] != len(ids)
        or cs.shape[0] != len(ids)
        or min(cm.shape[1], cs.shape[1]) < 1
        or np.any(cm <= 0)
        or np.any(cs <= 0)
    ):
        raise ValueError(
            "reference program needs unique physical IDs and positive matching main/sub caps [F]"
        )
    active = len(ids)
    bridge = _broadcast(bridge_caps_f, (active,), "bridge_caps_f [F]")
    cp = _broadcast(main_parasitic_f, (active,), "main_parasitic_f [F]")
    cq = _broadcast(sub_parasitic_f, (active,), "sub_parasitic_f [F]")
    if np.any(bridge <= 0) or np.any(cp < 0) or np.any(cq < 0):
        raise ValueError("bridges must be positive and parasitics nonnegative [F]")
    high_m, high_s = np.asarray(main_on), np.asarray(sub_on)
    times = _real_array(times_s, "times_s [s]")
    if (
        times.ndim != 1
        or times.size == 0
        or times[0] < 0
        or np.any(np.diff(times) < 0)
        or high_m.dtype != np.dtype(bool)
        or high_s.dtype != np.dtype(bool)
        or high_m.shape != (len(times), *cm.shape)
        or high_s.shape != (len(times), *cs.shape)
    ):
        raise ValueError("reference program needs actual Boolean masks and ordered event times [s]")
    sm = _real_array(sampled_main_bottom_v, "sampled_main_bottom_v [V]")
    ss = _real_array(sampled_sub_bottom_v, "sampled_sub_bottom_v [V]")
    top = _real_array(sampled_top_v, "sampled_top_v [V]")
    sub = _real_array(sampled_subnode_v, "sampled_subnode_v [V]")
    if (
        sm.shape != cm.shape
        or ss.shape != cs.shape
        or top.shape != (active,)
        or sub.shape != (active,)
    ):
        raise ValueError(
            "sampled node/bottom voltages must match the explicit physical slice arrays [V]"
        )
    hi = _broadcast(
        ref_high_v,
        (len(times), active),
        "ref_high_v [V]",
        required=bool(high_m.any() or high_s.any()),
    )
    lo = _broadcast(
        ref_low_v,
        (len(times), active),
        "ref_low_v [V]",
        required=bool((~high_m).any() or (~high_s).any()),
    )
    vm = np.where(high_m, hi[..., None], lo[..., None])
    vs = np.where(high_s, hi[..., None], lo[..., None])

    # 初始自由电荷由实际采样节点定义；之后只合并top，不能短接各sub。
    q_top = ((cm * (top[:, None] - sm)).sum(axis=1) + bridge * (top - sub) + cp * top).sum()
    q_sub = (cs * (sub[:, None] - ss)).sum(axis=1) + bridge * (sub - top) + cq * sub
    matrix = np.zeros((active + 1, active + 1))
    matrix[0, 0] = cm.sum() + bridge.sum() + cp.sum()
    matrix[0, 1:] = matrix[1:, 0] = -bridge
    denominator = cs.sum(axis=1) + bridge + cq
    matrix[np.arange(1, active + 1), np.arange(1, active + 1)] = denominator
    c_eff = float(matrix[0, 0] - np.sum(bridge**2 / denominator))
    if not np.isfinite(c_eff) or c_eff <= 0:
        raise ValueError("explicit floating reference topology has no positive C_eff [F]")
    rhs = np.column_stack((q_top + (cm * vm).sum(axis=(1, 2)), q_sub + (cs * vs).sum(axis=2)))
    nodes = np.linalg.solve(matrix, rhs.T).T
    bottom = np.concatenate(
        (cm * (vm - nodes[:, 0, None, None]), cs * (vs - nodes[:, 1:, None])), axis=2
    )
    initial_bottom = np.concatenate((cm * (sm - top[:, None]), cs * (ss - sub[:, None])), axis=1)
    change = np.diff(np.concatenate((initial_bottom[None, ...], bottom), axis=0), axis=0)
    mask = np.concatenate((high_m, high_s), axis=2)
    charge = np.column_stack(
        (np.where(mask, change, 0).sum(axis=(1, 2)), np.where(mask, 0, change).sum(axis=(1, 2)))
    )
    sensitivity = np.empty((len(times), 2))
    for column, (mm, ms) in enumerate(((high_m, high_s), (~high_m, ~high_s))):
        forcing = np.column_stack(((cm * mm).sum(axis=(1, 2)), (cs * ms).sum(axis=2)))
        sensitivity[:, column] = np.linalg.solve(matrix, forcing.T)[0]
    if (
        np.any(~np.isfinite(nodes))
        or np.any(~np.isfinite(charge))
        or np.any(~np.isfinite(sensitivity))
    ):
        raise ValueError("explicit reference node solve produced nonfinite values")
    return ReferenceProgram(
        readonly(times),
        readonly(charge),
        readonly(sensitivity),
        readonly(bottom[-1]),
        readonly(nodes),
        readonly(ids, np.int64),
        ("shared_floating_top", *(f"slice_{int(i)}_floating_sub" for i in ids)),
        c_eff,
    )
