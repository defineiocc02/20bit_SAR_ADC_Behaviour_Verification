# =============================================================================
# 文件名     : test_digital_transactions.py
# 功能       : 从公开 Python 入口检查生产复位字序、事件、采样预置和定点出码。
# 设计说明   : 端口采集在 CI 中独立比较；这里用可手算的值和无效输入验证边界。
# =============================================================================
"""名义电荷、分数码步及异常标志的定向回归。"""

import numpy as np
import pytest

from adi_model import DefaultParameters, default_model, simulate
from adi_model.behavioral.sampler import dc_input
from adi_model.digital_conversion import (
    LSB20,
    Q30,
    nominal_weights_q30,
    reconstruct,
    sar_conversion,
)

VIN = -0.6522216796875


def test_native_sequence_and_all_forced_words_are_transparent_on_the_public_api():
    # 固定物理输入使正确的最终码对字不敏感。只有分别检查两端开关与 ADC2
    # 原码，才可分辨“正确注入并扣回”和根本没有注入。
    run = simulate(dc_input(VIN), 1024)
    assert run.raw.word[:8].tolist() == [45, 7, 17, 40, 26, 24, 2, 32]
    assert set(run.raw.word) == set(range(64))
    assert set(run.codes.code) == {410304} and run.raw.out_valid.all()
    assert not run.raw.adc2_over.any() and not run.raw.out_err.any()

    forced = default_model().simulate(dc_input(VIN), 64, dither_words=range(64))
    row = 53
    assert (
        forced.raw.sadc_mask[row],
        forced.raw.coarse[row],
        forced.raw.k[row],
        forced.raw.adc2_code[row],
        forced.codes.code[row],
    ) == (0x2A, 202, 206, 6016, 410304)
    assert forced.raw.wp_q30[row] == 53 * (Q30 // 128)
    assert len(set(forced.raw.adc2_code)) > 1
    assert np.array_equal(forced.codes.code, run.codes.code[:64])


def test_upper_threshold_has_two_distinct_write_sequences():
    # 保护试探被拒时没有物理写门；下一位仍从 bit5 开始。接受时只剩低三位。
    reject_c, reject = sar_conversion(-3 + (500.25 * 6 / 512), 0, 4)
    accept_c, accept = sar_conversion(-3 + (514.25 * 6 / 512), 0, 4)
    assert reject_c == 500 and len(reject) == 8 and reject[:3] == (452, -1, 484)
    assert reject[-1] == 504 and sum(k >= 0 for k in reject) == 7
    assert accept_c == 514 and accept == (452, 516, 516, 518, 518)


def test_fractional_voltages_are_kept_until_adc2_and_rail_flags_survive():
    # 相差半个最终码步的两个输入应相差一个 ADC2 原码。若先把 Vin 取整，
    # 两笔将会共用原码；超过正端的输出舍入应另有数字限幅标志。
    n = np.array([410304.25, 410304.75, (1 << 20) - 0.25])
    run = simulate(lambda _: -3 + n * LSB20, 3, dither_words=[0, 0, 0])
    assert run.raw.adc2_code[1] == run.raw.adc2_code[0] + 1
    assert run.codes.code.tolist() == [410304, 410305, (1 << 20) - 1]
    assert run.codes.clipped_high.tolist() == [False, False, True]
    assert not run.raw.adc2_over.any()


def test_digital_coefficients_do_not_change_the_forward_plant():
    weights = nominal_weights_q30()
    weights[0, 18] += 1 << 20
    acquisition = DefaultParameters(device_weights=weights / Q30)
    corrected = DefaultParameters(device_weights=weights / Q30, weights_q30=weights)
    # 只改变片内加载表。芯片和扰动参数相同，保持同一组物理开关与 raw。
    bad = simulate(dc_input(VIN), 256, parameters=acquisition)
    good = simulate(dc_input(VIN), 256, parameters=corrected)
    assert np.array_equal(bad.raw.adc2_code, good.raw.adc2_code)
    assert np.array_equal(bad.raw.post_slots, good.raw.post_slots)
    assert set(bad.codes.code.astype(int) - 410304) == {-32, 0, 32}
    assert set(good.codes.code) == {410304}


def test_reconstruction_error_is_distinct_from_adc2_range_and_missing_retirement():
    # 一个合法写入的零表仍可能具有非法的总增益。重构在原标签上返回错误。
    bad_table = simulate(
        dc_input(VIN),
        2,
        parameters=DefaultParameters(weights_q30=np.zeros((18, 20), dtype=np.int64)),
    )
    assert bad_table.raw.out_valid.all() and bad_table.raw.out_err.all()
    assert not bad_table.raw.adc2_over.any() and not bad_table.codes.code.any()
    # 当一级命令超过 520，最后一次 write 被抑制，本笔没有有效输出快照。
    high = simulate(dc_input(4), 1, dither_words=[63])
    assert high.raw.k[0] == 524 and high.raw.rdac_over[0] and not high.raw.out_valid[0]
    assert high.raw.event_k[0, high.raw.event_count[0] - 1] == -1
    # 合法粗码但余差越窗的宏仍回送 raw=0 与 range 位，重构 err 单独观察。
    low = simulate(dc_input(-4), 1, dither_words=[0])
    assert low.raw.adc2_over[0] and low.raw.adc2_code[0] == 0
    assert low.raw.out_valid[0] and not low.raw.out_err[0]


def test_published_signed_pair_uses_the_production_rounding_contract():
    # 这两份 Wm 使 X 跨过零。返回标志必须属于当前的乘积，
    # 不能沿用前一笔负值的符号扩展位。
    for weight, wanted in ((15, 0x78000), (17, 0x88000)):
        assert reconstruct(2048, 32 * Q30, weight * Q30, 0, 768) == (wanted, False, False, False)


@pytest.mark.parametrize(
    "words,phases",
    [
        ([64], None),
        ([-1], None),
        ([True], None),
        ([0.5], None),
        ([1, 2], None),
        (None, [[8, 0, 0, 0]]),
        (None, [[0, 0, 2, 0]]),
    ],
)
def test_malformed_controls_are_rejected_without_running_another_model(words, phases):
    with pytest.raises((TypeError, ValueError)):
        simulate(dc_input(0), 1, dither_words=words, dem_controls=phases)
