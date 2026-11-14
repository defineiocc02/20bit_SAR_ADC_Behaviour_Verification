"""显式数字开关绑定：不生成未披露的DEM、几何或跨slice映射。

selector仅接收数字几何、整数RDAC码、状态及实际slice ID，不接触电容、增益或保持电压。
物理mask始终是有限的0/1选择；采集连接另用Signal/RefLo/RefHi三种整数状态，
不能由mask全零推断是否连接输入。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from adi_model.simulation.config import ConfigError

SIGNAL = 0
REF_LOW = 1
REF_HIGH = 2


def _integer_array(value, name: str, *, ndim: int | None = None) -> np.ndarray:
    """保留整数语义；拒绝浮点取整、布尔ID和uint64回绕。"""
    array = np.asarray(value)
    if array.dtype.kind not in "iu":
        raise ConfigError(f"{name} must contain explicit integers")
    if array.dtype.kind == "u" and np.any(array > np.iinfo(np.int64).max):
        raise ConfigError(f"{name} exceeds the signed integer interface")
    result = array.astype(np.int64, copy=True)
    if ndim is not None and result.ndim != ndim:
        raise ConfigError(f"{name} must have {ndim} dimensions")
    return result


def _binary_mask(value, name: str, shape: tuple[int, ...]) -> np.ndarray:
    array = np.asarray(value)
    if array.shape != shape or array.dtype.kind not in "biu":
        raise ConfigError(f"{name} must be an integer/bool mask with shape {shape}")
    if np.any((array != 0) & (array != 1)):
        raise ConfigError(f"{name} contains a nonbinary physical switch value")
    result = array.astype(bool, copy=True)
    result.flags.writeable = False
    return result


@dataclass(frozen=True)
class SplitSwitchGeometry:
    """数字可见的显式尺寸与码域；码域缺失只允许检查mask/资源数学。"""

    n_main: int
    n_sub: int
    n_slices: int
    n_active: int
    code_min: int | None = None
    code_max: int | None = None

    def __post_init__(self) -> None:
        """校验显式整数尺寸及可缺失的成对码域。"""
        for name in ("n_main", "n_sub", "n_slices", "n_active"):
            value = getattr(self, name)
            if isinstance(value, bool | np.bool_) or not isinstance(value, int | np.integer):
                raise ConfigError(f"geometry.{name} must be an explicit integer")
            if value <= 0:
                raise ConfigError(f"geometry.{name} must be positive")
            object.__setattr__(self, name, int(value))
        if self.n_active > self.n_slices:
            raise ConfigError("geometry.n_active exceeds the physical slice pool")
        if (self.code_min is None) != (self.code_max is None):
            raise ConfigError("geometry.code_min and code_max must be supplied together")
        if self.code_min is not None:
            for name in ("code_min", "code_max"):
                value = getattr(self, name)
                if isinstance(value, bool | np.bool_) or not isinstance(value, int | np.integer):
                    raise ConfigError(f"geometry.{name} must be an integer RDAC code")
                object.__setattr__(self, name, int(value))
            assert self.code_max is not None
            if self.code_min > self.code_max:
                raise ConfigError("geometry.code_min exceeds code_max")

    @property
    def dac_levels(self) -> int:
        """电平数只来自显式码域，绝不从物理元件数相乘推断。"""
        if self.code_min is None or self.code_max is None:
            raise ConfigError("RDAC code selection requires geometry.code_min/code_max")
        return self.code_max - self.code_min + 1

    def validate_slice_ids(self, slice_ids, *, n_samples: int | None = None) -> np.ndarray:
        """校验每笔物理ID维度、范围与组内唯一性。"""
        ids = _integer_array(slice_ids, "physical slice IDs", ndim=2)
        if ids.shape[1] != self.n_active or (n_samples is not None and ids.shape[0] != n_samples):
            raise ConfigError("physical slice IDs do not match sample/active dimensions")
        if np.any((ids < 0) | (ids >= self.n_slices)):
            raise ConfigError("physical slice ID is outside the explicit geometry")
        if np.any(np.diff(np.sort(ids, axis=1), axis=1) == 0):
            raise ConfigError("a sample selects the same physical slice more than once")
        return ids


@dataclass(frozen=True)
class SplitSwitchCommand:
    """实际RDAC物理mask；1接显式高参考轨，0接显式低参考轨。"""

    main_on: np.ndarray
    sub_on: np.ndarray

    def validated(self, geometry: SplitSwitchGeometry, n_samples: int) -> SplitSwitchCommand:
        """返回通过形状和整数二进制检验的只读实际mask副本。"""
        main = _binary_mask(
            self.main_on, "main_on", (n_samples, geometry.n_active, geometry.n_main)
        )
        sub = _binary_mask(self.sub_on, "sub_on", (n_samples, geometry.n_active, geometry.n_sub))
        return SplitSwitchCommand(main, sub)


@dataclass(frozen=True)
class SamplingConnections:
    """实际采集连接：Signal=0、RefLo=1、RefHi=2；不推断未披露开关。"""

    main: np.ndarray
    sub: np.ndarray

    def validated(self, geometry: SplitSwitchGeometry, n_samples: int) -> SamplingConnections:
        """校验每个实际电容的有限三态采集连接。"""
        values = []
        for name, value, size in (
            ("main sampling connections", self.main, geometry.n_main),
            ("sub sampling connections", self.sub, geometry.n_sub),
        ):
            array = _integer_array(value, name, ndim=3)
            if array.shape != (n_samples, geometry.n_active, size):
                raise ConfigError(f"{name} has an incompatible physical shape")
            if np.any((array < SIGNAL) | (array > REF_HIGH)):
                raise ConfigError(f"{name} contains an unsupported connection state")
            array.flags.writeable = False
            values.append(array)
        return SamplingConnections(*values)


MaskBinding = Callable[
    [SplitSwitchGeometry, np.ndarray, np.ndarray, np.ndarray], SplitSwitchCommand
]


def split_switch_command(
    geometry: SplitSwitchGeometry,
    k,
    state,
    *,
    slice_ids,
    binding: MaskBinding | None,
) -> SplitSwitchCommand:
    """由调用者绑定产生实际mask；不提供旧SID/缺格/零和交换回退。

    k和state是(N,)整数数字量；slice_ids为(N,A)。
    binding的四个参数依次为geometry、k、state、slice_ids，不包含任何物理真值。
    结果只证明绑定输出合法，绑定是否符合指定来源仍需该来源的独立证据。
    """
    if not isinstance(geometry, SplitSwitchGeometry):
        raise ConfigError("split_switch_command requires explicit SplitSwitchGeometry")
    if binding is None:
        raise ConfigError("DEM/RDAC code-to-mask mapping is unbound; supply an explicit binding")
    if not callable(binding):
        raise ConfigError("DEM/RDAC mask binding must be callable")
    codes = _integer_array(k, "RDAC codes", ndim=1)
    states = _integer_array(state, "DEM states", ndim=1)
    if states.shape != codes.shape:
        raise ConfigError("RDAC codes and DEM states must belong to the same samples")
    ids = geometry.validate_slice_ids(slice_ids, n_samples=len(codes))
    if geometry.code_min is None or geometry.code_max is None:
        raise ConfigError("RDAC code selection requires explicit code_min/code_max")
    if np.any((codes < geometry.code_min) | (codes > geometry.code_max)):
        raise ConfigError("RDAC code is outside the explicit codebook; no implicit clipping")
    command = binding(geometry, codes.copy(), states.copy(), ids.copy())
    if not isinstance(command, SplitSwitchCommand):
        raise ConfigError("mask binding must return SplitSwitchCommand")
    return command.validated(geometry, len(codes))
