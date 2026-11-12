# =============================================================================
# 文件名     : module1.py
# 模块名     : module1
# 版本       : v1.0
# 功能       : 模块一的逐沿参考，连接调度结果、同笔 DEM 配置和掩码。
#
# 设计说明   :
#   * 承担 独立参考模型与逐沿 oracle 中的一环；输入输出的物理口径与单位以同目录 SPEC/README 为准。
# -----------------------------------------------------------------------------
# 变更记录
# -----------------------------------------------------------------------------
# 初始版本，暂无变更记录。
# =============================================================================

"""模块一的逐沿参考，连接调度结果、同笔 DEM 配置和掩码。"""

from dataclasses import dataclass, field, replace
from operator import index

from adi_model.capacitors import DEFAULT_DEM_SEED, GROUP_SLICES, POOL_SLICES

from adi_model.control.fig11_dem import Fig11Masks, map_fig11
from adi_model.control.scheduler import FULL_MASK, SchedulerOutput, SliceScheduler

# DEFAULT_DEM_SEED 从 adi_model.capacitors 导入后在此重导出：
# 本模块与 ExperimentConfig 必须用同一个种子，各写一份字面量时改一处就会静默分叉。
_UINT32_MASK = (1 << 32) - 1
_EMPTY_MASKS = Fig11Masks((0,) * GROUP_SLICES, (0,) * GROUP_SLICES, False)


# =========================================================================
# 1. 函数组：DemSnapshot / Module1Output / advance_dem_rng
# =========================================================================
# -------------------------------------------------------------------------
# 一笔转换从采集阶段继承的 DEM 使能和两个轮转相位。
# 上升沿后发布的组、随机状态及当前已决码的掩码。
# 将一个非零的 32 位 xorshift 状态推进一步。
# -------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class DemSnapshot:
    """一笔转换从采集阶段继承的 DEM 使能和两个轮转相位。"""

    enabled: bool = False
    horizontal_phase: int = 0
    vertical_phase: int = 0


@dataclass(frozen=True, slots=True)
class Module1Output:
    """上升沿后发布的组、随机状态及当前已决码的掩码。"""

    schedule: SchedulerOutput = field(default_factory=SchedulerOutput)
    acquisition_dem: DemSnapshot = field(default_factory=DemSnapshot)
    conversion_dem: DemSnapshot = field(default_factory=DemSnapshot)
    dem_rng_state: int = DEFAULT_DEM_SEED
    masks: Fig11Masks = _EMPTY_MASKS
    code_error: bool = False


def advance_dem_rng(state: int) -> int:
    """将一个非零的 32 位 xorshift 状态推进一步。"""
    state ^= (state << 13) & _UINT32_MASK
    state ^= state >> 17
    return state ^ ((state << 5) & _UINT32_MASK)


# =========================================================================
# 2. 函数组：dem_snapshot / Module1
# =========================================================================
# -------------------------------------------------------------------------
# 从本笔分配前的随机状态提取两个相位。
# 同一时钟域内的模块一，调度请求晚一拍发布。
# -------------------------------------------------------------------------
def dem_snapshot(state: int, enabled: bool) -> DemSnapshot:
    """从本笔分配前的随机状态提取两个相位。"""
    return DemSnapshot(enabled, (state >> 24) % 15, (state >> 16) & 15)


class Module1:
    """同一时钟域内的模块一，调度请求晚一拍发布。"""

    def __init__(self) -> None:
        """建立空的两级调度和 DEM 状态。"""
        self.scheduler = SliceScheduler()
        self._clear_pipeline()

    def _clear_pipeline(self) -> None:
        self._dem_enabled = False
        self._pending_dem_seed: int | None = None
        self._seed_error = False
        self._acquisition_dem = DemSnapshot()
        self._conversion_dem = DemSnapshot()
        self._dem_rng_state = DEFAULT_DEM_SEED

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
        seed_load: bool = False,
        slice_seed: int = 0,
        dem_seed: int = 0,
        code_valid: bool = False,
        code: int = 0,
        code_sample_id: int = 0,
    ) -> Module1Output:
        """推进一个边沿，并评估边沿后的组合掩码。

        配置随本沿请求保存。下一沿发布调度结果，然后对已发布的转换组
        检查 code_sample_id。转换组可在多个时钟提交同一笔的已决前缀。
        """
        slice_seed = index(slice_seed)
        dem_seed = index(dem_seed)
        code = index(code)
        code_sample_id = index(code_sample_id)
        if not (0 <= slice_seed <= _UINT32_MASK and 0 <= dem_seed <= _UINT32_MASK):
            raise ValueError("both seeds must fit in 32 bits")
        if not (0 <= code < (1 << 9) and 0 <= code_sample_id <= _UINT32_MASK):
            raise ValueError("code and its sample ID must fit in 9 and 32 bits")

        accept_seed = seed_load and not advance and slice_seed != 0 and dem_seed != 0
        previous = self.scheduler.state
        self.scheduler.step(
            rst_n=rst_n,
            clear=clear,
            enable=enable,
            advance=advance and not seed_load,
            eligible=eligible,
            shuffle=shuffle,
            seed_load=accept_seed,
            seed=slice_seed,
        )
        if not rst_n or clear or not enable:
            self._clear_pipeline()
            return Module1Output()

        if previous.slot_accepted:
            self._conversion_dem = self._acquisition_dem
            self._acquisition_dem = dem_snapshot(self._dem_rng_state, self._dem_enabled)
        if self._pending_dem_seed is not None:
            self._dem_rng_state = self._pending_dem_seed
        elif previous.slot_accepted:
            self._dem_rng_state = advance_dem_rng(self._dem_rng_state)

        ids = set(previous.conv_ids)
        legal_group = len(ids) == GROUP_SLICES and all(0 <= item < POOL_SLICES for item in ids)
        if legal_group:
            legal_group = sum(1 << item for item in ids) == previous.conv_mask
        schedule = replace(
            previous,
            conv_valid=previous.conv_valid and legal_group,
            seed_error=self._seed_error or previous.seed_error,
        )
        self._dem_enabled = dem_enable
        self._pending_dem_seed = dem_seed if accept_seed else None
        self._seed_error = seed_load and not accept_seed

        masks = _EMPTY_MASKS
        if code_valid and schedule.conv_valid and code_sample_id == schedule.conv_sample_id:
            snapshot = self._conversion_dem
            masks = map_fig11(
                code, snapshot.enabled, snapshot.horizontal_phase, snapshot.vertical_phase
            )
        return Module1Output(
            schedule,
            self._acquisition_dem,
            self._conversion_dem,
            self._dem_rng_state,
            masks,
            code_valid and not masks.valid,
        )
