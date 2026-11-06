"""模块二的六位 dither 字参考，状态和字均可按样本重放。"""

from dataclasses import dataclass, replace
from operator import index

DEFAULT_DITHER_SEED = 0x5A1709C3
_STATE_MASK = (1 << 31) - 1
_WORD_MASK = (1 << 6) - 1
_ID_MASK = (1 << 32) - 1


@dataclass(frozen=True, slots=True)
class DitherWordOutput:
    """上升沿后的字、样本归属、随机状态和当拍事件。"""

    word: int = 0
    word_valid: bool = False
    word_sample_id: int = 0
    rng_state: int = DEFAULT_DITHER_SEED
    seed_error: bool = False


class DitherWordGenerator:
    """一个同步采样接口，每笔接受最多发一个六位字。"""

    def __init__(self) -> None:
        """建立复位后的发生器。"""
        self._state = DitherWordOutput()

    @property
    def state(self) -> DitherWordOutput:
        """读取最近一个时钟沿的输出。"""
        return self._state

    def step(
        self,
        *,
        rst_n: bool = True,
        clear: bool = False,
        dither_enable: bool = True,
        seed_load: bool = False,
        seed: int = 0,
        sample_accept: bool = False,
        sample_id: int = 0,
    ) -> DitherWordOutput:
        """以同步复位、写种子、接受采样的顺序评估一个上升沿。"""
        seed = index(seed)
        sample_id = index(sample_id)
        if not (0 <= seed <= _STATE_MASK and 0 <= sample_id <= _ID_MASK):
            raise ValueError("seed and sample ID must fit in 31 and 32 bits")

        current = replace(self._state, word_valid=False, seed_error=False)
        if not rst_n or clear:
            current = DitherWordOutput()
        elif seed_load:
            if seed == 0 or sample_accept:
                current = replace(current, seed_error=True)
            else:
                current = replace(current, rng_state=seed)
        elif sample_accept:
            state = current.rng_state
            word = 0
            if dither_enable:
                # 连续六次反馈都只读取旧状态中尚未移出的位。
                word = ((state >> 25) ^ (state >> 22)) & _WORD_MASK
                state = ((state << 6) & _STATE_MASK) | word
            current = DitherWordOutput(word, True, sample_id, state)
        self._state = current
        return current
