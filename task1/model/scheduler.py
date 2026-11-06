"""18 片资源池的逐沿调度模型。"""

from dataclasses import dataclass, replace
from operator import index

SLICE_COUNT = 18
GROUP_SIZE = 8
FULL_MASK = (1 << SLICE_COUNT) - 1
DEFAULT_SEED = 0x243F6A88
_UINT32_MASK = (1 << 32) - 1
_LFSR_TAPS = 0x80200003
_EMPTY_IDS = (0,) * GROUP_SIZE


@dataclass(frozen=True, slots=True)
class SchedulerOutput:
    """一个上升沿后的调度端口值。

    ID 元组按 slot 顺序排列。组资格在空拍保持，三个事件标志仅持续一拍。
    """

    acq_ids: tuple[int, ...] = _EMPTY_IDS
    conv_ids: tuple[int, ...] = _EMPTY_IDS
    acq_mask: int = 0
    conv_mask: int = 0
    acq_valid: bool = False
    conv_valid: bool = False
    acq_sample_id: int = 0
    conv_sample_id: int = 0
    slot_accepted: bool = False
    stall: bool = False
    seed_error: bool = False
    lfsr_state: int = DEFAULT_SEED


def _advance_lfsr(state: int) -> int:
    """推进八步右移 Galois LFSR。"""
    for _ in range(8):
        state = (state >> 1) ^ (_LFSR_TAPS if state & 1 else 0)
    return state


class SliceScheduler:
    """有状态的调度参考，从复位状态开始逐沿运行。"""

    def __init__(self) -> None:
        """建立同步复位后的初始状态。"""
        self.state = SchedulerOutput()

    def step(
        self,
        *,
        rst_n: bool = True,
        clear: bool = False,
        enable: bool = True,
        advance: bool = False,
        eligible: int = FULL_MASK,
        shuffle: bool = False,
        seed_load: bool = False,
        seed: int = 0,
    ) -> SchedulerOutput:
        """按复位、种子写入、分配的优先级推进一拍。

        Args:
            rst_n: 同步低有效复位。
            clear: 同步清空调度状态。
            enable: 关闭时清空调度状态。
            advance: 请求一组新采集资源。
            eligible: 可采集切片的 18 位掩码，位序为物理编号。
            shuffle: 从旧随机状态派生扫描起点。
            seed_load: 在独立控制沿载入种子。
            seed: 32 位无符号种子，零值会在载入时报告错误。

        Returns:
            上升沿后的完整输出。

        Raises:
            ValueError: 整数输入超出端口位宽。
        """
        eligible = index(eligible)
        seed = index(seed)
        if not 0 <= eligible <= FULL_MASK:
            raise ValueError("eligible must fit in 18 bits")
        if not 0 <= seed <= _UINT32_MASK:
            raise ValueError("seed must fit in 32 bits")

        if not rst_n or clear or not enable:
            self.state = SchedulerOutput()
            return self.state

        previous = replace(self.state, slot_accepted=False, stall=False, seed_error=False)
        if seed_load:
            self.state = (
                replace(previous, seed_error=True)
                if advance or seed == 0
                else replace(previous, lfsr_state=seed)
            )
        elif advance:
            self.state = self._allocate(previous, eligible, shuffle)
        else:
            self.state = previous
        return self.state

    @staticmethod
    def _allocate(previous: SchedulerOutput, eligible: int, shuffle: bool) -> SchedulerOutput:
        """选择满足释放资格的切片，并保存上一组的完整样本身份。"""
        start = (previous.lfsr_state >> 24) % SLICE_COUNT if shuffle else 0
        candidates = eligible & ~previous.acq_mask
        scan_order = (*range(start, SLICE_COUNT), *range(start))
        ids = tuple(slice_id for slice_id in scan_order if candidates & (1 << slice_id))
        if len(ids) < GROUP_SIZE:
            return replace(previous, stall=True)

        selected = ids[:GROUP_SIZE]
        return SchedulerOutput(
            acq_ids=selected,
            conv_ids=previous.acq_ids,
            acq_mask=sum(1 << slice_id for slice_id in selected),
            conv_mask=previous.acq_mask,
            acq_valid=True,
            conv_valid=previous.acq_valid,
            acq_sample_id=(previous.acq_sample_id + 1) & _UINT32_MASK if previous.acq_valid else 0,
            conv_sample_id=previous.acq_sample_id,
            slot_accepted=True,
            lfsr_state=_advance_lfsr(previous.lfsr_state) if shuffle else previous.lfsr_state,
        )
