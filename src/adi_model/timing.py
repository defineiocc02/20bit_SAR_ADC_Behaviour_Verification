"""显式物理slice计划与因果校验。

18/8资源数量可来自目标配置；
具体随机策略、种子、状态序列、启动组和无效事务必须由实际控制记录或显式binding提供。
这里不再生成旧LFSR游标、固定D2扰动、固定A/B或备用轮换作为论文实现。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np

from adi_model.config import ConfigError
from adi_model.dem import _integer_array


@dataclass(frozen=True)
class SlicePlan:
    """N个周期的实际组记录；slot是列位置，元素是物理slice ID。"""

    conv: np.ndarray
    acq: np.ndarray
    conv_source_cycle: np.ndarray
    valid: np.ndarray
    strategy: str
    meta: dict = field(default_factory=dict)

    def __len__(self) -> int:
        """返回记录的周期行数，不把它当成功转换输出数。"""
        conv = np.asarray(self.conv)
        if conv.ndim != 2:
            raise ConfigError("slice plan conversion IDs must have shape (N,n_active)")
        return int(conv.shape[0])

    def validated(self, *, n_slices: int, n_active: int) -> SlicePlan:
        """校验整数物理ID、资源互斥及前周期采集归属。"""
        for name, value in (("n_slices", n_slices), ("n_active", n_active)):
            if isinstance(value, bool | np.bool_) or not isinstance(value, int | np.integer):
                raise ConfigError(f"{name} must be an explicit integer slice dimension")
        conv = _integer_array(self.conv, "conversion slice IDs", ndim=2)
        acq = _integer_array(self.acq, "acquisition slice IDs", ndim=2)
        if conv.shape != acq.shape or conv.shape[1] != n_active:
            raise ConfigError(
                "slice plan requires matching (N,n_active) acquisition/conversion IDs"
            )
        if n_slices < 2 * n_active or n_active <= 0:
            raise ConfigError("slice pool cannot hold disjoint acquisition and conversion groups")
        for name, ids in (("conversion", conv), ("acquisition", acq)):
            if np.any((ids < 0) | (ids >= n_slices)):
                raise ConfigError(f"{name} IDs are outside the physical pool")
            if np.any(np.diff(np.sort(ids, axis=1), axis=1) == 0):
                raise ConfigError(f"{name} group contains duplicate physical IDs")
        both = np.sort(np.concatenate((conv, acq), axis=1), axis=1)
        if np.any(np.diff(both, axis=1) == 0):
            raise ConfigError("the same physical slice acquires and converts in one period")
        source = _integer_array(self.conv_source_cycle, "source acquisition cycles", ndim=1)
        valid = np.asarray(self.valid)
        if source.shape != (len(conv),) or valid.shape != (len(conv),) or valid.dtype.kind != "b":
            raise ConfigError("source cycles and bool validity must identify every plan row")
        if not isinstance(self.strategy, str) or not self.strategy.strip():
            raise ConfigError("slice plan requires the explicit controller/binding identity")
        if len(conv) > 1:
            if not np.array_equal(np.sort(conv[1:], axis=1), np.sort(acq[:-1], axis=1)):
                raise ConfigError("conversion does not consume the preceding acquisition group")
            if not np.array_equal(source[1:], np.arange(len(conv) - 1)):
                raise ConfigError(
                    "source acquisition-cycle metadata contradicts physical ownership"
                )
        if len(conv) and source[0] >= 0:
            raise ConfigError("first plan row must identify an external pre-acquisition cycle")
        for array in (conv, acq, source):
            array.flags.writeable = False
        return SlicePlan(conv, acq, source, valid.copy(), self.strategy, dict(self.meta))


ScheduleBinding = Callable[[int, int, int], SlicePlan]


def build_slice_plan(
    cfg, n_samples: int, *, binding: ScheduleBinding | SlicePlan | None
) -> SlicePlan:
    """读取显式controller计划，不按未知随机分布猜测组选择。

    callable绑定只接收样本数、池大小、活动数三个数字量。首周期有效性与预采集来源由绑定给出；
    本函数不暗中跳过两次advance或生成sample ID。
    """
    if isinstance(n_samples, bool) or not isinstance(n_samples, int | np.integer) or n_samples < 0:
        raise ConfigError("slice plan sample count must be a nonnegative integer")
    if binding is None:
        raise ConfigError("slice schedule is unbound; supply an explicit physical controller plan")
    for name in ("n_slices", "n_active"):
        value = getattr(cfg, name)
        if isinstance(value, bool | np.bool_) or not isinstance(value, int | np.integer):
            raise ConfigError(f"schedule {name} must be an explicit integer dimension")
    if isinstance(binding, SlicePlan):
        plan = binding
    elif callable(binding):
        plan = binding(int(n_samples), cfg.n_slices, cfg.n_active)
    else:
        raise ConfigError("slice schedule binding must return or contain a SlicePlan")
    if not isinstance(plan, SlicePlan) or len(plan) != n_samples:
        raise ConfigError("schedule binding returned an incompatible SlicePlan")
    return plan.validated(n_slices=cfg.n_slices, n_active=cfg.n_active)
