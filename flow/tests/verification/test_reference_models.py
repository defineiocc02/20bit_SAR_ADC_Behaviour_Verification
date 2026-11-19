# =============================================================================
# 文件名     : test_reference_models.py
# 模块名     : test_reference_models
# 版本       : v1.0
# 功能       : 验证舍入、码仓中心和定点重构的整数参考实现。
# 设计说明   : 并行计算与流水线分解使用同一组整数输入，以精确等式比对。
#              测试使用手算例、构造量程或显式极限，复算独立于 RTL 仿真。
#              跨单元重构用四个理想单位，单独检查选通与信号连接的作用。
# 变更记录   : 保留整数算术用例，清除与退役转换链向量相关的比对。
# =============================================================================
"""运行纯 Python 定点参考用例。

    python -m pytest flow/tests/verification/test_reference_models.py -q
"""

from __future__ import annotations

import random

from subsys.experiments.fig11 import Fig11Parameters
from verification.reference.datapath_ref import (
    OUTPUT_COUNT,
    ReconTerms,
    adc2_bin_center,
    recon_terms,
    reconstruct,
    sadc_code,
)
from verification.reference.fixed_point import (
    floor_div,
    round_half_even_shift,
    truncate_div,
)


# =============================================================================
# 1. 纯整数舍入和负数除法
# =============================================================================
def test_ties_to_even_matches_the_eight_frozen_examples():
    """RTL_ARITHMETIC_CONTRACT.md §3.1 lists exactly these eight results."""
    values = [-7, -5, -3, -1, 1, 3, 5, 7]
    expected = [-4, -2, -2, 0, 0, 2, 2, 4]
    assert [round_half_even_shift(v, 1) for v in values] == expected


def test_floor_and_truncate_differ_only_on_negative_non_integers():
    """This is the seam the whole fixed-point oracle exists to catch."""
    for numerator in range(-40, 41):
        for denominator in (1, 2, 3, 5, 7):
            floor = floor_div(numerator, denominator)
            trunc = truncate_div(numerator, denominator)
            if numerator % denominator == 0:
                assert floor == trunc, (numerator, denominator)
            elif numerator < 0:
                assert floor == trunc - 1, (numerator, denominator)
            else:
                assert floor == trunc, (numerator, denominator)


def test_pipelined_division_identity_holds_on_random_inputs():
    """floor((x >>> 33) / g) must equal floor(x / (g * 2**33)) exactly."""
    rng = random.Random(20261112)
    for _ in range(400):
        gain = rng.randrange(1 << 20, 1 << 45)
        shifted = rng.randrange(-(1 << 60), 1 << 60)
        assert shifted // (gain << 33) == (shifted >> 33) // gain


# =============================================================================
# 2. 码仓和比较器判决编码
# =============================================================================
def test_adc2_bin_center_is_monotone_and_bounded():
    """遍历当前位宽的所有码仓，使用易于复算的对称测试区间。"""
    bits = Fig11Parameters().adc2_n_bits
    min_q, max_q = -(1 << 32), 1 << 32
    previous = None
    for code in range(1 << bits):
        value, _ = adc2_bin_center(code, min_q, max_q, bits)
        assert min_q <= value <= max_q
        if previous is not None:
            assert value >= previous
        previous = value


def test_sadc_code_counts_bits_and_tolerates_bubbles():
    assert sadc_code(0) == 0
    assert sadc_code((1 << 511) - 1) == 511
    # 气泡码保留为逐位计数，避免连续前缀计数吞掉不相邻的判决。
    assert sadc_code(0b1011) == 3


# =============================================================================
# 3. 信号权重与 20 位输出范围
# =============================================================================
def test_gain_uses_signal_connection_not_the_switch_mask():
    """Contract §3.2: ordinary units have a=1 even when their switch is off.

    This pins the exact misreading that was made once in this repo: using the
    conversion mask as `a` makes gain look mask-dependent and wrongly suggests
    the RTL contradicts the contract.
    """
    weights = [1 << 30] * 4
    on = [1, 0, 1, 0]
    sign = [1 - 2 * bit for bit in on]
    terms = recon_terms(weights, on, sign)
    assert terms.total_q30 == 4 << 30
    assert terms.gain_q30 == 4 << 30, "gain must count every connected unit, not just the on ones"
    assert terms.rails_q30 == 0

    # 关闭两路信号连接后增益减半，参考轨选通保持和上一个输入相同。
    terms_dither = recon_terms(weights, on, sign, signal_connected=[1, 1, 0, 0])
    assert terms_dither.gain_q30 == 2 << 30


def test_reconstruct_clips_low_and_reports_the_flag():
    """Below the negative rail the word saturates at 0 and the flag goes high.

    The threshold is set by gain, so the stimulus has to be far enough below
    -1.0 V/Vfs to leave no doubt: with gain = 4 units the clip point is -4 V/Vfs.
    """
    terms = recon_terms([1 << 30] * 4, [1, 1, 0, 0], [1, 1, -1, -1])
    result = reconstruct(fine_q=-(1 << 40), offset_q=0, injection_q=0, terms=terms)
    assert result.word == 0
    assert result.clip_low
    # 对称的正极限应夹到最大码，并在高端状态位留下可观测记录。
    high = reconstruct(fine_q=1 << 40, offset_q=0, injection_q=0, terms=terms)
    assert high.word == 1048575
    assert high.clip_high


def test_reconstruct_is_inside_the_output_range_by_construction():
    terms = ReconTerms(rails_q30=0, gain_q30=1 << 30, total_q30=1 << 30)
    for fine in (-(1 << 32), 0, (1 << 32) - 1):
        result = reconstruct(fine_q=fine, offset_q=0, injection_q=0, terms=terms)
        assert 0 <= result.word < OUTPUT_COUNT
