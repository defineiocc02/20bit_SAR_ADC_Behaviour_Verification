"""显式物理采集计划的接口，不再自动选择未披露的随机策略。"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from adi_model.simulation.config import ConfigError
from adi_model.control.dem import _integer_array
from adi_model.control.timing import ScheduleBinding, SlicePlan, build_slice_plan


@dataclass(frozen=True)
class Allocation:
    """bank来自实际数字记录；slice_ids元素是物理编号，不由bank反推。"""

    bank: np.ndarray
    slice_ids: np.ndarray


class Scheduler:
    """只消费显式计划binding；不读取电容真值，也不估计时偏或带宽。"""

    def __init__(self, cfg, *, binding: ScheduleBinding | SlicePlan | None):
        """绑定显式控制记录或提供者，不选择默认算法或种子。"""
        if binding is None:
            raise ConfigError("Scheduler requires an explicit source/controller schedule binding")
        self.cfg = cfg
        self.binding = binding

    def plan(self, n_samples: int) -> SlicePlan:
        """返回经物理资源和因果规则校验的实际计划。"""
        return build_slice_plan(self.cfg, n_samples, binding=self.binding)

    def reserve_dual(self, n_samples: int) -> tuple[np.ndarray, np.ndarray]:
        """返回同周期不交叉且跨周期因果相接的(conv,acq)。"""
        plan = self.plan(n_samples)
        return plan.conv, plan.acq

    def reserve(self, n_samples: int) -> Allocation:
        """返回显式bank与物理组；缺bank记录时不按奇偶数制造它。"""
        plan = self.plan(n_samples)
        if "bank" not in plan.meta:
            raise ConfigError("bank allocation requires actual plan.meta['bank'] metadata")
        bank = _integer_array(plan.meta["bank"], "controller bank IDs", ndim=1)
        if bank.shape != (n_samples,) or np.any(bank < 0):
            raise ConfigError("controller bank metadata does not match the plan")
        return Allocation(bank, plan.conv)


def make_scheduler(cfg, *, binding: ScheduleBinding | SlicePlan | None) -> Scheduler:
    """构造显式计划消费方；没有rotate/permute类型推断或旧对象回退。"""
    return Scheduler(cfg, binding=binding)
