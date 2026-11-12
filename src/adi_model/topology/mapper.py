"""显式粗码到RDAC整数码表，不推未知位权、prefix或DEM物理映射。"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from adi_model._arrays import readonly
from adi_model.simulation.config import ConfigError
from adi_model.topology.dac_arch import NominalCodebook
from adi_model.control.dem import _integer_array


@dataclass(frozen=True)
class MappedCodes:
    """同笔最终RDAC码及可选已声明逐事件码，均为实际整数控制。"""

    codes: np.ndarray
    event_codes: np.ndarray | None


class Mapper:
    """消费显式数字码表，不从SADC位数或电容数量猜倍数。"""

    def __init__(
        self, rdac_code_by_coarse, *, nominal_codebook: NominalCodebook, event_codes_by_coarse=None
    ):
        """冻结粗码映射，逐事件表必须以同一最终码结束。

        Args:
            rdac_code_by_coarse: (C,)显式整数RDAC码，C为粗码仓数。
            nominal_codebook: 独立数字已知的RDAC码域与电压表。
            event_codes_by_coarse: 可选(C,E)实际逐事件码；参考动态执行时必需，
                不从最终粗码按旧seed/tick/binary prefix公式产生。

        Side effects: 拷贝并冻结数字表，不读取C/G或保持电压。
        """
        if not isinstance(nominal_codebook, NominalCodebook):
            raise ConfigError("Mapper requires an explicit NominalCodebook")
        codes = _integer_array(rdac_code_by_coarse, "RDAC code by coarse", ndim=1)
        if not codes.size:
            raise ConfigError("coarse-to-RDAC mapping must be nonempty")
        nominal_codebook.evaluate(codes)
        events = None
        if event_codes_by_coarse is not None:
            events = _integer_array(event_codes_by_coarse, "RDAC event code table", ndim=2)
            if (
                events.shape[0] != len(codes)
                or events.shape[1] < 1
                or not np.array_equal(events[:, -1], codes)
            ):
                raise ConfigError(
                    "RDAC event code table must match coarse rows and end at the final code"
                )
            nominal_codebook.evaluate(events)
            events = readonly(events, np.int64)
        self.rdac_code_by_coarse = readonly(codes, np.int64)
        self.event_codes_by_coarse = events
        self.nominal_codebook = nominal_codebook

    def encode(self, coarse_code) -> MappedCodes:
        """按已声明表输出最终码/逐事件码，不夹码或添加dither。

        Args:
            coarse_code: (N,)真实SADC整数判决。

        Returns:
            MappedCodes；末事件与最终码归属同一raw。

        Side effects: 无，不选择物理slice或生成DEM状态。
        """
        coarse = _integer_array(coarse_code, "SADC coarse codes", ndim=1)
        if np.any((coarse < 0) | (coarse >= len(self.rdac_code_by_coarse))):
            raise ConfigError("SADC code is outside the explicit coarse mapping")
        events = (
            None
            if self.event_codes_by_coarse is None
            else readonly(self.event_codes_by_coarse[coarse], np.int64)
        )
        return MappedCodes(readonly(self.rdac_code_by_coarse[coarse], np.int64), events)
