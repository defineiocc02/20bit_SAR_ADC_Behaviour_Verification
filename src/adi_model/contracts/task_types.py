"""从任务实验层下沉的稳定类型：SwitchState / Q32 / MapperTable。

这些类型原来在 `task1/experiments/` 内定义，被 `task2/eight/model.py` 反向 import。
本模块只保留**数据结构**，不依赖任何 `task*` 目录；实验层改为 import 本模块。

`MapperTable.build()` 仍留在实验层（它依赖任务私有 mapper）；本模块提供
`lookup()` 的稳定接口，迁移时把已构建的表作为数据传入。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

Q32 = 1 << 32


@dataclass(frozen=True)
class SwitchState:
    """按有序物理切片保存粗码和开关位图。"""

    plan: np.ndarray
    codes: np.ndarray
    main: np.ndarray
    sub: np.ndarray

    def __post_init__(self) -> None:
        if self.plan.shape[0] != self.codes.shape[0]:
            raise ValueError("plan/codes must have the same length")
        if self.main.shape[0] != self.codes.shape[0] or self.sub.shape[0] != self.codes.shape[0]:
            raise ValueError("main/sub mask rows must match codes")


@dataclass(frozen=True)
class MapperTable:
    """已构建的 canonical mapper 表；只做查表，不做映射生成。"""

    bridge: bool
    main: np.ndarray
    sub: np.ndarray
    correction_q32: np.ndarray
    command_count: int = 512
    phase_count: int = 1

    def __post_init__(self) -> None:
        if self.main.shape[0] != self.command_count:
            raise ValueError("main table command dimension mismatch")
        if self.sub.shape[0] != self.command_count:
            raise ValueError("sub table command dimension mismatch")
        if self.correction_q32.shape[0] != self.command_count:
            raise ValueError("correction table command dimension mismatch")

    def lookup(self, command, plan, phases):
        """返回 (SwitchState, correction_q32)；调用方保证 command/phase 合法。"""
        command = np.asarray(command, dtype=int)
        plan = np.asarray(plan)
        h, v = phases
        phase = (np.asarray(v, dtype=int) * 8 + (np.asarray(h, dtype=int) % 8)
                 if self.bridge else np.zeros(np.asarray(h).shape, dtype=int))
        if np.any(command < 0) or np.any(command >= self.command_count):
            raise ValueError("command outside mapper table")
        if np.any(phase < 0) or np.any(phase >= self.main.shape[1]):
            raise ValueError("phase outside mapper table")
        state = SwitchState(plan, command, self.main[command, phase], self.sub[command, phase])
        corr = (self.correction_q32[command, phase]
                if self.correction_q32.ndim > 1 else self.correction_q32[command])
        return state, corr
