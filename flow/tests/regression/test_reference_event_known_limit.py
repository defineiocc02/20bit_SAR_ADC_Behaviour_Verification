"""检查显式三位前缀／六次已决事件表，不由最终码掩盖中途越界。

该表是当前数字控制完整 valid 序列的工程绑定，不代表论文唯一开关时序。
所有重复事件及拒绝路径均保留，不自动生成模拟建立结论。
"""

import numpy as np
import pytest

from adi_model.control.dither import (
    FIG19_FRACTIONAL_WEIGHTS_Q,
    FIG19_STEP_Q,
    Fig19DitherProgram,
    Fig19DitherRangeError,
    Fig19StaticSpec,
    complete_flash3_sar6_prefix_table,
)


def _spec():
    return Fig19StaticSpec(
        v_fs=3.0,
        step_q=FIG19_STEP_Q,
        voltage_fraction_bits=32,
        fractional_lower_weights_q=FIG19_FRACTIONAL_WEIGHTS_Q,
        equivalent_polarity="subtract",
        provenance="Explicit bounded engineering fixture; no physical switch-network claim",
    )


def test_reference_table_preserves_every_resolved_event_including_repeated_codes():
    actual = complete_flash3_sar6_prefix_table()
    expected = []
    for code in range(512):
        resolved = code & 0b111000000
        row = [resolved]
        for bit in range(5, -1, -1):
            if code & (1 << bit):
                resolved += 1 << bit
            row.append(resolved)
        expected.append(row)
    np.testing.assert_array_equal(actual, expected)
    np.testing.assert_array_equal(actual[173], [128, 160, 160, 168, 172, 172, 173])
    assert not actual.flags.writeable
    assert np.any(actual[:, 1:] == actual[:, :-1])


@pytest.mark.parametrize(
    "word,coarse,expected_events",
    [(32, 8, [-4, -4, -4, 4, 4, 4, 4]), (63, 5, [-5, -5, -5, -5, -1, -1, 0])],
)
def test_legal_final_command_cannot_hide_an_illegal_early_prefix(word, coarse, expected_events):
    program = Fig19DitherProgram.from_words([word], spec=_spec())
    with pytest.raises(Fig19DitherRangeError) as captured:
        program.commands(
            [coarse], complete_flash3_sar6_prefix_table()[[coarse]], code_min=0, code_max=511
        )
    trace = captured.value.trace
    assert not trace.final_range_overflow.any()
    assert trace.event_range_overflow.any()
    np.testing.assert_array_equal(trace.rdac_event_codes[0], expected_events)
    np.testing.assert_array_equal(trace.event_range_overflow, trace.rdac_event_codes < 0)
    assert trace.rdac_event_codes.shape == (1, 7)
    assert not trace.rdac_event_codes.flags.writeable


def test_all_words_have_legal_complete_prefixes_on_the_declared_two_volt_domain():
    x = np.linspace(-2.0, 2.0, 33)
    program = Fig19DitherProgram.from_words(np.tile(np.arange(64), len(x)), spec=_spec())
    external = np.repeat(x, 64)
    coarse = np.floor((external - program.lower_v + 3) / (6 / 512)).astype(np.int64)
    trace = program.commands(
        coarse, complete_flash3_sar6_prefix_table()[coarse], code_min=0, code_max=511
    )
    assert not trace.event_range_overflow.any()
    assert not trace.final_range_overflow.any()
    np.testing.assert_array_equal(trace.rdac_event_codes[:, -1], trace.rdac_code)
