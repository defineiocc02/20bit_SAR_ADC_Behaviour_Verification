# =============================================================================
# 文件名     : module1_bridge.py
# 模块名     : module1_bridge
# 版本       : v1.0
# 功能       : 保留模块一时序的 Fig.21 桥接候选逐沿参考。
#
# 设计说明   :
#   * 承担 独立参考模型与逐沿 oracle 中的一环；输入输出的物理口径与单位以同目录 SPEC/README 为准。
# -----------------------------------------------------------------------------
# 变更记录
# -----------------------------------------------------------------------------
# 初始版本，暂无变更记录。
# =============================================================================

"""保留模块一时序的 Fig.21 桥接候选逐沿参考。"""

from dataclasses import dataclass, field, replace

from adi_model.control.fig21_bridge import map_fig21_bridge
from adi_model.control.module1 import Module1, Module1Output
from adi_model.control.scheduler import FULL_MASK


# =========================================================================
# 1. 函数组：Module1BridgeOutput / Module1Bridge
# =========================================================================
# -------------------------------------------------------------------------
# 模块一的已发布事务，以及随样本冻结的桥接模式和 Q32 正补偿。
# 按已发布转换组选择 Fig.11 或 Fig.21 的逐沿参考。
# -------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class Module1BridgeOutput:
    """模块一的已发布事务，以及随样本冻结的桥接模式和 Q32 正补偿。"""

    module1: Module1Output = field(default_factory=Module1Output)
    acquisition_bridge_enabled: bool = False
    conversion_bridge_enabled: bool = False
    correction_q32: int = 0


class Module1Bridge:
    """按已发布转换组选择 Fig.11 或 Fig.21 的逐沿参考。"""

    def __init__(self) -> None:
        """建立空的模块一和桥接配置流水。"""
        self._base = Module1()
        self._clear_bridge_mode()

    def _clear_bridge_mode(self) -> None:
        self._requested_bridge = False
        self._acquisition_bridge = False
        self._conversion_bridge = False

    def step(
        self,
        *,
        rst_n: bool = True,
        clear: bool = False,
        enable: bool = True,
        advance: bool = False,
        eligible: int = FULL_MASK,
        shuffle: bool = False,
        dem_enable: bool = False,
        bridge_enable: bool = False,
        seed_load: bool = False,
        slice_seed: int = 0,
        dem_seed: int = 0,
        code_valid: bool = False,
        code: int = 0,
        code_sample_id: int = 0,
    ) -> Module1BridgeOutput:
        """推进一个边沿，再对发布组和同笔前缀计算有效掩码。

        bridge_enable 与 advance 同沿提交。调度接受才冻结采集配置，
        下一次调度接受把该配置连同采集组晋升到转换组。旧 h/v 相位
        分别提供 Fig.21 纵向起始 slot 的低三位和保留列 R。
        """
        module1 = self._base.step(
            rst_n=rst_n,
            clear=clear,
            enable=enable,
            advance=advance,
            eligible=eligible,
            shuffle=shuffle,
            dem_enable=dem_enable,
            seed_load=seed_load,
            slice_seed=slice_seed,
            dem_seed=dem_seed,
            code_valid=code_valid,
            code=code,
            code_sample_id=code_sample_id,
        )
        if not rst_n or clear or not enable:
            self._clear_bridge_mode()
            return Module1BridgeOutput(module1)

        if module1.schedule.slot_accepted:
            self._conversion_bridge = self._acquisition_bridge
            self._acquisition_bridge = self._requested_bridge
        self._requested_bridge = bridge_enable

        correction_q32 = 0
        if self._conversion_bridge and module1.masks.valid:
            snapshot = module1.conversion_dem
            bridge = map_fig21_bridge(code, snapshot.vertical_phase, snapshot.horizontal_phase & 7)
            module1 = replace(module1, masks=bridge.masks)
            correction_q32 = bridge.correction_q32

        return Module1BridgeOutput(
            module1,
            self._acquisition_bridge,
            self._conversion_bridge,
            correction_q32,
        )
