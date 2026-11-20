"""数字采样连接到静态学习项；与实际物理电荷算子分离。

只接收真实整数连接、转换mask和独立名义参考绑定。
这里没有制造电容、保持电压、实际参考压降、RA增益或输入真值参数，不能用plant状态保证校准闭合。
"""

from __future__ import annotations

import numpy as np

from adi_model._arrays import readonly
from adi_model.behavioral.sampler import finite_record
from adi_model.control.dem import (
    REF_HIGH,
    REF_LOW,
    SIGNAL,
    SamplingConnections,
    SplitSwitchCommand,
    SplitSwitchGeometry,
)
from adi_model.simulation.config import ConfigError


def digital_connection_terms(
    geometry: SplitSwitchGeometry,
    connections: SamplingConnections,
    command: SplitSwitchCommand,
    *,
    nominal_reference_high_v,
    nominal_reference_low_v,
) -> tuple[np.ndarray, np.ndarray]:
    """从数字连接形成a和term=b−VD，不读物理truth。

    Args:
        geometry: 明确数字维数，主/子单位顺序固定。
        connections: (N,A,M/S)真实采集Signal0/RefLo1/RefHi2整数连接。
        command: (N,A,M/S)真实转换bool mask；1接名义高轨、0接名义低轨。
        nominal_reference_high_v: 数字已知高参考[V]，标量或(N,A)。
        nominal_reference_low_v: 数字已知低参考[V]，标量或(N,A)。

    Returns:
        (a,term)，形状(N,A,M+S)；a为bool信号连接，term为名义节点V。

    Raises:
        ConfigError: 缺少实际被消费的名义轨或非法离散mask。

    Side effects: 无；不从旧SID/tail/D/增强位数重建缺失连接。
    """
    if not isinstance(connections, SamplingConnections) or not isinstance(
        command, SplitSwitchCommand
    ):
        raise ConfigError("digital terms require actual SamplingConnections and SplitSwitchCommand")
    if not isinstance(geometry, SplitSwitchGeometry):
        raise ConfigError("digital terms require explicit SplitSwitchGeometry")
    main_shape = np.asarray(command.main_on).shape
    if len(main_shape) != 3:
        raise ConfigError("actual conversion main mask must have shape (N,A,M)")
    n = main_shape[0]
    sampled = connections.validated(geometry, n)
    converted = command.validated(geometry, n)
    sample = np.concatenate((sampled.main, sampled.sub), axis=-1)
    mask = np.concatenate((converted.main_on, converted.sub_on), axis=-1)
    shape = (n, geometry.n_active)
    high_used = bool(np.any(sample == REF_HIGH) or np.any(mask))
    low_used = bool(np.any(sample == REF_LOW) or np.any(~mask))
    high = (
        finite_record(nominal_reference_high_v, shape, "nominal high reference [V]")
        if high_used
        else np.zeros(shape)
    )
    low = (
        finite_record(nominal_reference_low_v, shape, "nominal low reference [V]")
        if low_used
        else np.zeros(shape)
    )
    a = sample == SIGNAL
    sample_known = np.where(
        sample == REF_HIGH, high[..., None], np.where(sample == REF_LOW, low[..., None], 0.0)
    )
    converted_v = np.where(mask, high[..., None], low[..., None])
    return readonly(a, bool), readonly(sample_known - converted_v)
