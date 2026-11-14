"""以独立数值方程检查显式 RTL 算术镜像。

三个正权重和 1 至 20 位后级宽度是数学夹具，不是备选器件配置。
测试不运行 ADC 流水线或物理选通映射器。
"""

from __future__ import annotations

import importlib.util
import random
import sys
from dataclasses import replace

import numpy as np
import pytest
from _rtl_test_support import ROOT

from adi_model.calibration.weight_calibration import CalibrationSpec, DigitalObservation
from adi_model.contracts.fixed_point import FixedPointFormat, FixedPointReconstructor


@pytest.fixture
def mirror(monkeypatch):
    path = ROOT / "sim/ref/recon_rtl_mirror.py"
    spec = importlib.util.spec_from_file_location("explicit_recon_rtl_mirror", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    return module


def _format(*, accumulator_bits=96):
    return FixedPointFormat(
        weight_fraction_bits=30,
        voltage_fraction_bits=32,
        voltage_bits=64,
        coefficient_bits=48,
        accumulator_bits=accumulator_bits,
        output_bits=20,
    )


def _three_weight_record(bits):
    count = 1 << bits
    codes = np.unique([0, 1, count // 2, max(0, count - 2), count - 1])
    spec = CalibrationSpec(1, 1, 1, 2, 3.0, bits, 0.0, 6.0, "uniform_midrise", "half_open")
    data = DigitalObservation(
        spec=spec,
        slice_ids=np.zeros((len(codes), 1), np.int64),
        adc2_code=codes,
        main_masks=np.zeros((len(codes), 1, 1), bool),
        sub_masks=np.zeros((len(codes), 1, 2), bool),
        signal_mask=np.ones((len(codes), 1, 3), bool),
        known_voltage_term_v=np.full((len(codes), 1, 3), 3.0),
        overflow=np.arange(len(codes)) == len(codes) - 1,
        sample_ids=np.arange(len(codes)),
        static_charge_domain=True,
    )
    decoder = FixedPointReconstructor.from_weights(spec, [[0.25, 0.25, 0.5]], 0.0, format=_format())
    return decoder, data


@pytest.mark.parametrize("bits", range(1, 21))
def test_three_positive_weights_decode_backend_bin_centers_at_every_width(bits):
    decoder, data = _three_weight_record(bits)
    assert list(decoder.weights_q[0]) == [1 << 28, 1 << 28, 1 << 29]
    assert sum(map(int, decoder.weights_q.flat)) == 1 << 30
    stream = decoder.reconstruct(data)
    expected = np.array(
        [((2 * int(code) + 1) * (1 << 20)) // (1 << (bits + 1)) for code in data.adc2_code]
    )
    np.testing.assert_array_equal(stream.code, expected)
    np.testing.assert_array_equal(stream.analog_overflow, data.overflow)
    assert not np.any(stream.clipped_low | stream.clipped_high)
    assert stream.peak_accumulator_bits <= 96
    if bits == 20:
        np.testing.assert_array_equal(stream.code, data.adc2_code)


def test_three_weight_register_image_round_trips_without_model_state():
    decoder, data = _three_weight_record(20)
    restored = FixedPointReconstructor.from_dict(decoder.to_dict())
    np.testing.assert_array_equal(restored.reconstruct(data).code, decoder.reconstruct(data).code)
    assert restored.spec == data.spec
    assert not restored.weights_q.flags.writeable


def test_narrow_accumulator_is_actually_rejected_instead_of_wraparound():
    decoder, data = _three_weight_record(2)
    narrow = replace(decoder, format=_format(accumulator_bits=83))
    with pytest.raises(OverflowError, match="accumulator"):
        narrow.reconstruct(data)
    assert decoder.reconstruct(data).peak_accumulator_bits > 83


def test_zero_signal_gain_is_rejected():
    decoder, data = _three_weight_record(6)
    no_signal = replace(data, signal_mask=np.zeros_like(data.signal_mask))
    with pytest.raises(ValueError, match="gain"):
        decoder.reconstruct(no_signal)


def test_known_term_voltage_register_overflow_is_rejected_before_integer_cast():
    decoder, data = _three_weight_record(2)
    term = data.known_voltage_term_v.copy()
    term[0, 0, 0] = 3.0 * (1 << 31)
    with pytest.raises(OverflowError, match="voltage register"):
        decoder.reconstruct(replace(data, known_voltage_term_v=term))


def test_digital_clipping_and_observed_analog_flag_are_separate():
    decoder, data = _three_weight_record(6)
    for term, field in ((0.0, "clipped_high"), (6.0, "clipped_low")):
        changed = replace(data, known_voltage_term_v=np.full_like(data.known_voltage_term_v, term))
        result = decoder.reconstruct(changed)
        assert np.any(getattr(result, field))
        np.testing.assert_array_equal(result.analog_overflow, data.overflow)
        assert np.all((result.code >= 0) & (result.code < 1 << 20))


def _signed(value, width):
    raw = value % (1 << width)
    return raw - (1 << width) if raw >= 1 << (width - 1) else raw


def _independent_half_even(numerator, denominator):
    quotient, remainder = divmod(numerator, denominator)
    return quotient + int(
        2 * remainder > denominator or (2 * remainder == denominator and quotient % 2)
    )


@pytest.mark.parametrize("shift", [1, 2, 7, 33])
def test_mirror_signed_rte_matches_independent_quotient_rule(mirror, shift):
    values = [*range(-17, 18), -(1 << 100) + 3, (1 << 100) - 3]
    for value in values:
        assert mirror.round_half_even_shift(value, shift) == _independent_half_even(
            value, 1 << shift
        )


@pytest.mark.parametrize("bits", range(1, 21))
@pytest.mark.parametrize("voltage_bits", [8, 24, 64])
def test_mirror_adc2_dec_matches_integer_bin_center_rule(mirror, bits, voltage_bits):
    lo, hi = -(1 << (voltage_bits - 2)) + 3, (1 << (voltage_bits - 2)) - 2
    for low, high in ((lo, hi), (hi, lo)):
        for code in sorted({0, 1, (1 << bits) // 2, (1 << bits) - 1}):
            expected = low + _independent_half_even((2 * code + 1) * (high - low), 1 << (bits + 1))
            assert mirror.adc2_dec(code, low, high, bits, voltage_bits) == (expected, False)


def test_mirror_adc2_ties_include_negative_deltas(mirror):
    for numerator in (-7, -5, -3, -1, 1, 3, 5, 7):
        expected = 1000 + _independent_half_even(numerator, 2)
        assert mirror.adc2_dec(0, 1000, 1000 + 2 * numerator, 1, 16) == (expected, False)


@pytest.mark.parametrize("stages", [1, 3, 5])
def test_bitwise_divider_exhaustive_small_signed_and_wide_denominator(mirror, stages):
    checked = 0
    for numerator in range(-8, 8):
        for denominator in range(32):
            expected = (numerator // denominator, False) if denominator else (0, True)
            assert mirror.div_floor_bitwise(numerator, denominator, 4, 5, stages) == expected
            checked += 1
    assert checked == 16 * 32


@pytest.mark.parametrize(
    ("wa", "wd", "stages"), [(2, 1, 1), (32, 40, 3), (63, 64, 7), (64, 96, 11), (127, 128, 11)]
)
def test_bitwise_divider_wide_ports_and_padded_groups(mirror, wa, wd, stages):
    generator = random.Random(wa + wd + stages)
    pairs = [
        (a, d)
        for a in (-(1 << (wa - 1)), -1, 0, 1, (1 << (wa - 1)) - 1)
        for d in (0, 1, (1 << wd) - 1)
    ]
    pairs += [(generator.getrandbits(wa), generator.getrandbits(wd)) for _ in range(64)]
    for raw_a, raw_d in pairs:
        numerator, denominator = _signed(raw_a, wa), raw_d % (1 << wd)
        expected = (numerator // denominator, False) if denominator else (0, True)
        assert mirror.div_floor_bitwise(raw_a, raw_d, wa, wd, stages) == expected
        assert mirror.div_floor(raw_a, raw_d, wa, wd, stages) == expected


def test_reversing_grouped_quotient_bits_is_detected(mirror):
    raw = 0x4000000000000001
    expected = _signed(raw, 63)
    assert mirror.div_floor_bitwise(raw, 1, 63, 64, 7) == (expected, False)
    bad, error = mirror.div_floor_bitwise(raw, 1, 63, 64, 7, grouped_bit_order=False)
    assert error is False and bad != expected
    assert mirror.div_floor_bitwise(raw, 1, 63, 64, 1, grouped_bit_order=False) == (expected, False)
    mismatches = 0
    for numerator in range(-127, 128):
        correct = mirror.div_floor_bitwise(numerator, 3, 9, 12, 5)
        reversed_bits = mirror.div_floor_bitwise(numerator, 3, 9, 12, 5, grouped_bit_order=False)
        assert correct == (numerator // 3, False)
        mismatches += reversed_bits != correct
    assert mismatches > 100


def _mac_case(**changes):
    """声明原始数学格式，与器件默认参数无关。"""
    fields = {
        "code": 32,
        "adc2_bits": 6,
        "min_q": -4096,
        "max_q": 4096,
        "offset_q": 0,
        "known_common_q": 0,
        "total": 100,
        "gain": 100,
        "rails": 0,
        "weight_fraction_bits": 6,
        "voltage_fraction_bits": 7,
        "voltage_bits": 24,
        "accumulator_bits": 40,
        "output_bits": 12,
        "sum_bits": 32,
        "divider_stages": 3,
    }
    fields.update(changes)
    return fields


def _mac_oracle(values):
    """使用无限精度整数，只按端口解释及显式范围施加约束。"""
    vb, vf, wf, sb = (
        values[k]
        for k in ("voltage_bits", "voltage_fraction_bits", "weight_fraction_bits", "sum_bits")
    )
    code = values["code"] % (1 << values["adc2_bits"])
    lo, hi = _signed(values["min_q"], vb), _signed(values["max_q"], vb)
    fine = lo + _independent_half_even((2 * code + 1) * (hi - lo), 1 << (values["adc2_bits"] + 1))
    total, gain = values["total"] % (1 << sb), values["gain"] % (1 << sb)
    op1 = (fine - _signed(values["offset_q"], vb)) << wf
    op2 = _signed(values["rails"], sb + 2) << vf
    op3 = total * _signed(values["known_common_q"], vb)
    numerator = op1 - op2 - op3
    shifted = (numerator + (gain << vf)) << values["output_bits"]
    denominator = gain << (vf + 1)
    limit = 1 << (values["accumulator_bits"] - 1)
    overflow = any(
        not -limit <= operand < limit
        for operand in (op1, op2, op3, numerator, shifted, denominator)
    )
    return shifted // denominator if denominator else 0, overflow, gain == 0


def test_explicit_tgr_mac_matches_big_integer_oracle_and_all_error_classes(mirror):
    generator = random.Random(71)
    cases = [_mac_case(gain=0), _mac_case(rails=1 << 30), _mac_case(gain=1 << 31)]
    for index in range(180):
        values = _mac_case(
            code=generator.randrange(64),
            offset_q=generator.randrange(-512, 513),
            known_common_q=generator.randrange(-512, 513),
            gain=generator.randrange(1, 4096),
            total=generator.randrange(1, 4096),
            rails=generator.randrange(-4096, 4097),
        )
        if index % 3 == 0:
            values.update(
                offset_q=generator.randrange(-(1 << 23), 1 << 23),
                known_common_q=generator.randrange(-(1 << 23), 1 << 23),
                total=generator.getrandbits(32),
                gain=generator.getrandbits(32),
            )
        cases.append(values)
    successful = overflowed = gain_errors = 0
    for values in cases:
        expected = _mac_oracle(values)
        actual = mirror.reconstruct(**values)
        assert actual[1:] == expected[1:]
        if not any(expected[1:]):
            assert actual[0] == expected[0]
            successful += 1
        overflowed += expected[1]
        gain_errors += expected[2]
    assert successful > 50 and overflowed > 40 and gain_errors == 1


@pytest.mark.parametrize(
    ("sign", "expected"), [(1, [False, True, True]), (-1, [True, False, False])]
)
def test_mac_overflow_check_has_a_half_open_accumulator_limit(mirror, sign, expected):
    boundary = 1 << (40 - 1 - 12 - 6)
    cases = [
        _mac_case(
            code=0,
            min_q=sign * boundary + step,
            max_q=sign * boundary + step,
            offset_q=0,
            gain=1,
            total=0,
            rails=1,
        )
        for step in (-1, 0, 1)
    ]
    results = [mirror.reconstruct(**case) for case in cases]
    assert [result[1] for result in results] == expected
    for case, actual in zip(cases, results, strict=True):
        assert actual[1:] == _mac_oracle(case)[1:]


@pytest.mark.parametrize("output_bits", [12, 20])
def test_output_clip_uses_unsigned_half_open_range(mirror, output_bits):
    count = 1 << output_bits
    for word in (-(1 << 95), -1, 0, 1, count - 1, count, 1 << 95):
        assert mirror.clip_word(word, output_bits) == (
            min(max(word, 0), count - 1),
            word < 0,
            word >= count,
        )


@pytest.mark.parametrize("widths", [(1, 2, 1), (2, 0, 1), (2, 2, 0), (True, 2, 1)])
def test_invalid_divider_width_contract_is_rejected(mirror, widths):
    with pytest.raises(ValueError):
        mirror.div_widths(*widths)


@pytest.mark.parametrize(
    "changes",
    [{"accumulator_bits": 9}, {"sum_bits": 100}, {"accumulator_bits": 96}, {"voltage_bits": True}],
)
def test_unsupported_mac_width_relations_are_not_silently_coerced(mirror, changes):
    with pytest.raises(ValueError):
        mirror.reconstruct(**_mac_case(**changes))


def _rtl_int_widths(path, wanted, known):
    """只求值显式整数位宽表达式，遇到未知形式即报错。"""
    import re

    table = dict(re.findall(r"localparam\s+int\s+(\w+)\s*=\s*([^;]+);", path.read_text()))
    values = dict(known)

    def resolve(name):
        if name in values:
            return values[name]
        expression = re.sub(r"\bint\s*'\s*\(", "sv_int(", table[name])
        for dependency in re.findall(r"[A-Za-z_]\w*", expression):
            if dependency != "sv_int" and dependency not in values:
                resolve(dependency)
        expression = re.sub(r"(?<!/)/(?!/)", "//", expression)
        values[name] = int(
            eval(expression, {"__builtins__": {}, "sv_int": lambda n: _signed(n, 32)}, values)
        )
        return values[name]

    return {name: resolve(name) for name in wanted}


def _assert_divider_widths(mirror, table_path):
    for wa, wd, stages in ((2, 1, 1), (6, 8, 7), (32, 40, 3), (63, 64, 7), (64, 96, 11)):
        result = _rtl_int_widths(
            table_path, ["W_R", "N_CYC", "W_PAD"], {"P_W_A": wa, "P_W_D": wd, "P_STAGES": stages}
        )
        assert tuple(result.values()) == mirror.div_widths(wa, wd, stages)


def test_mirror_width_functions_match_actual_rtl_expressions(mirror):
    _assert_divider_widths(mirror, ROOT / "rtl/core/div_floor.sv")
    for bits in (1, 2, 6, 12, 20):
        for vb in (8, 24, 64):
            actual = _rtl_int_widths(
                ROOT / "rtl/core/adc2_dec.sv",
                ["K", "W_MUL", "W_SUM"],
                {"P_ADC2_BITS": bits, "V_BITS": vb},
            )
            assert tuple(actual.values()) == mirror.adc2_widths(bits, vb)


def test_reconstruction_and_extracted_mac_share_declared_width_formulas():
    import re

    names = ["W_RAIL", "W_OP3", "W_WIDE", "W_S1", "W_SHIFT", "W_A", "W_TOP"]
    known = {"SUM_BITS": 64, "V_BITS": 64, "ACC_BITS": 96, "OUT_BITS": 20, "V_FRAC": 32}
    expected = {
        "W_RAIL": 66,
        "W_OP3": 130,
        "W_WIDE": 132,
        "W_S1": 97,
        "W_SHIFT": 117,
        "W_A": 63,
        "W_TOP": 22,
    }
    mac_path = ROOT / "rtl/core/cal_residue_mac.sv"
    core_path = ROOT / "rtl/core/recon_core.sv"
    assert _rtl_int_widths(mac_path, names, known) == expected
    # core只持有实际跨模块边界；MAC内部宽度不要求复制为无消费者的localparam。
    core = _rtl_int_widths(
        core_path,
        ["SUM_BITS", "W_RAIL", "W_A"],
        {name: value for name, value in known.items() if name != "SUM_BITS"},
    )
    assert core == {"SUM_BITS": 64, "W_RAIL": expected["W_RAIL"], "W_A": expected["W_A"]}
    default_sum = re.search(r"parameter\s+int\s+SUM_BITS\s*=\s*([0-9]+)", mac_path.read_text())
    assert default_sum is not None and int(default_sum[1]) == core["SUM_BITS"]
    assert re.search(r"wire\s*\[W_A\s*-\s*1:0\]\s*a1", core_path.read_text())
    assert ".a1(a1)" in core_path.read_text()


def test_width_drift_guard_rejects_both_mirror_and_rtl_mutations(mirror, tmp_path, monkeypatch):
    source = ROOT / "rtl/core/div_floor.sv"
    altered = tmp_path / "div_floor_wrong.sv"
    text = source.read_text()
    original = "(P_W_A + P_STAGES - 1) / P_STAGES"
    assert text.count(original) == 1
    altered.write_text(text.replace(original, "P_W_A / P_STAGES"))
    with pytest.raises(AssertionError):
        _assert_divider_widths(mirror, altered)
    original_function = mirror.div_widths

    def wrong_widths(wa, wd, stages):
        wr, _, padded = original_function(wa, wd, stages)
        return wr, wa // stages, padded

    with monkeypatch.context() as context:
        context.setattr(mirror, "div_widths", wrong_widths)
        with pytest.raises(AssertionError):
            _assert_divider_widths(mirror, source)
    _assert_divider_widths(mirror, source)


def test_mirror_has_no_implicit_profile_or_width_defaults(mirror):
    assert not hasattr(mirror, "Config")
    assert not hasattr(mirror, "run_pipeline")
    for function in (
        mirror.div_widths,
        mirror.adc2_widths,
        mirror.adc2_dec,
        mirror.div_floor,
        mirror.reconstruct,
    ):
        with pytest.raises(TypeError):
            function()
