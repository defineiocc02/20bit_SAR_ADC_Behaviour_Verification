# =============================================================================
# 文件名     : dither_word.py
# 模块名     : DitherWordGenerator
# 版本       : v1.0
# 功能       : 按受理顺序复现 v21 的六位采样扰动字。
#
# 设计说明   :
#   * B8 选定独立的 xorshift32 状态。首笔使用复位种子推进一次后的低六位。
#   * 外部定向字和零字都占用一个已接受样本。暂停不发字、不消耗状态。
# -----------------------------------------------------------------------------
# 变更记录
# -----------------------------------------------------------------------------
# 接受沿选字与 v21 的独立 xorshift32 共用工程合同。
# =============================================================================

"""工程定义的可重放字源；片选与扰动共用变换公式，各自持有独立状态。"""

from dataclasses import dataclass, replace
from operator import index

from adi_model.sched18_rtl import prng_step

DEFAULT_DITHER_SEED = 0xC001D00D
_STATE_MASK = (1 << 32) - 1
_WORD_MASK = (1 << 6) - 1
_ID_MASK = (1 << 32) - 1


# =========================================================================
# 1. 函数组：DitherWordOutput / DitherWordGenerator
# =========================================================================
# -------------------------------------------------------------------------
# 上升沿后的字、样本归属、随机状态和当拍事件。
# 一个同步采样接口，每笔接受最多发一个六位字。
# -------------------------------------------------------------------------
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
        force_word: int | None = None,
        seed_load: bool = False,
        seed: int = 0,
        sample_accept: bool = False,
        sample_id: int = 0,
    ) -> DitherWordOutput:
        """受理一个 prepare 产生一字；force_word 为 0…63，关闭扰动等效强制零。

        seed_load 仅供独立软件重放；生产 RTL 从固定种子复位。
        测试台的空拍、受理后排空或错误返回都不能额外推进字源。
        """
        seed = index(seed)
        sample_id = index(sample_id)
        if not (0 <= seed <= _STATE_MASK and 0 <= sample_id <= _ID_MASK):
            raise ValueError("seed and sample ID must fit in 32 bits")
        if force_word is not None and not 0 <= index(force_word) <= _WORD_MASK:
            raise ValueError("force_word must fit in 6 bits")

        current = replace(self._state, word_valid=False, seed_error=False)
        if not rst_n or clear:
            current = DitherWordOutput()
        elif seed_load:
            if seed == 0 or sample_accept:
                current = replace(current, seed_error=True)
            else:
                current = replace(current, rng_state=seed)
        elif sample_accept:
            state = prng_step(current.rng_state)
            word = state & _WORD_MASK if force_word is None else index(force_word)
            if not dither_enable:
                word = 0
            current = DitherWordOutput(word, True, sample_id, state)
        self._state = current
        return current
