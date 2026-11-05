"""检查显式 RTL 导出与独立整数规则，不执行 ADC 实验。"""

from __future__ import annotations

import random
import re

import pytest
from _rtl_test_support import (
    BUNDLE_FILES,
    blob,
    copy_inputs,
    input_args,
    invoke,
    load_tool,
    read_json,
    snapshot_inputs,
    write_json,
)

from adi_model.fixed_point import round_even_divide


@pytest.fixture
def inputs(tmp_path):
    return copy_inputs(tmp_path)


@pytest.fixture
def exporter(monkeypatch):
    return load_tool("export_rtl_params", monkeypatch)


def _round_ratio(numerator, denominator):
    quotient, remainder = divmod(numerator, denominator)
    return quotient + int(
        remainder * 2 > denominator or (remainder * 2 == denominator and quotient % 2 != 0)
    )


@pytest.mark.parametrize(
    ("numerator", "expected"),
    [(-7, -4), (-5, -2), (-3, -2), (-1, 0), (1, 0), (3, 2), (5, 2), (7, 4)],
)
def test_signed_half_even_ties(numerator, expected):
    assert _round_ratio(numerator, 2) == expected
    assert round_even_divide(numerator, 2) == expected


def test_big_integer_rounding_includes_nontrivial_signed_remainders():
    generator = random.Random(419)
    differences = 0
    for _ in range(300):
        n = generator.randrange(-(1 << 110), 1 << 110)
        d = generator.randrange(1, 1 << 45)
        assert round_even_divide(n, d) == _round_ratio(n, d)
        differences += _round_ratio(n, d) != n // d
    assert differences > 50


@pytest.mark.parametrize("denominator", [0, -1, -4])
def test_nonpositive_rounding_denominator_is_rejected(denominator):
    with pytest.raises(ValueError, match="positive"):
        round_even_divide(7, denominator)


@pytest.mark.parametrize("width", [8, 33, 96])
def test_signed_floor_correction_and_shift_decomposition(width):
    generator = random.Random(width)
    differences = 0
    for _ in range(200):
        n = generator.randrange(-(1 << (width - 1)), 1 << (width - 1))
        gain = generator.randrange(1, 1 << min(width, 40))
        trunc = abs(n) // gain
        trunc = -trunc if n < 0 else trunc
        corrected = trunc - int(n < 0 and n - trunc * gain != 0)
        assert corrected == n // gain
        differences += corrected != trunc
        for shift in (1, 7, 33):
            assert (n >> shift) // gain == n // (gain << shift)
    assert differences > 20


def test_build_is_deterministic_and_returns_exactly_eight_files(exporter, inputs):
    first = exporter.build(inputs.config, inputs.format, inputs.binding)
    second = exporter.build(inputs.config, inputs.format, inputs.binding)
    assert set(first) == BUNDLE_FILES
    assert {name: blob(value) for name, value in first.items()} == {
        name: blob(value) for name, value in second.items()
    }


def test_localparams_follow_explicit_inputs_without_inventing_a_dem_mapper(exporter, inputs):
    built = exporter.build(inputs.config, inputs.format, inputs.binding)
    params = {
        name: int(value)
        for name, value in re.findall(
            r"localparam int unsigned (\w+) = 32'd([0-9]+);", built["rtl_params.vh"]
        )
    }
    assert params == {
        "W_FRAC": 30,
        "W_BITS": 48,
        "V_FRAC": 32,
        "V_BITS": 64,
        "ACC_BITS": 96,
        "OUT_BITS": 20,
        "N_SLICES": 18,
        "N_ACTIVE": 8,
        "N_UNIT_MAIN": 2,
        "N_UNIT_SUB": 1,
        "N_UNIT_TOTAL": 3,
        "B1": 9,
        "ADC2_BITS": 6,
        "PHASES": 16,
        "CONTROL_WORD_BITS": 2,
        "STATE_BITS": 3,
        "RECON_UNROLL": 7,
        "POOL_SHUFFLE_SEED": 1831565813,
        "POOL_SHUFFLE_ENABLE": 1,
        "SOURCE_DEM_REQUEST": 1,
        "SOURCE_DITHER_REQUEST": 0,
        "CAL_OBS_BITS": 32 + 9 + 3 + 2 + 6 + 64 + 40 + 16 + 8 + 1,
    }
    assert not any(name.startswith(("DEM_LCG", "DEM_ROT", "DITHER_")) for name in params)


@pytest.mark.parametrize("missing", ["--config", "--format", "--binding", "--out"])
def test_cli_has_no_profile_or_output_default(exporter, inputs, missing, tmp_path, monkeypatch):
    args = [*input_args(inputs), "--out", str(tmp_path / "new_output")]
    index = args.index(missing)
    del args[index : index + 2]
    with pytest.raises(SystemExit) as error:
        invoke(exporter, args, monkeypatch)
    assert error.value.code == 2
    assert not (tmp_path / "new_output").exists()


@pytest.mark.parametrize(
    "field",
    [
        "weight_fraction_bits",
        "voltage_fraction_bits",
        "voltage_bits",
        "coefficient_bits",
        "accumulator_bits",
        "output_bits",
    ],
)
def test_missing_integer_format_is_not_repaired(exporter, inputs, field):
    fmt = read_json(inputs.format)
    del fmt[field]
    write_json(inputs.format, fmt)
    with pytest.raises(ValueError, match="missing"):
        exporter.build(inputs.config, inputs.format, inputs.binding)


@pytest.mark.parametrize(
    ("section", "field", "value"),
    [
        ("config", "dither_enable", True),
        ("config", "adc2_n_bits", 13),
        ("config", "dem_enable", None),
        ("format", "accumulator_bits", 95),
        ("binding", "sample_id_bits", 16),
        ("binding", "divider_stages", 1),
    ],
)
def test_unsupported_requests_are_rejected_without_writes(exporter, inputs, section, field, value):
    path = getattr(inputs, section)
    payload = read_json(path)
    payload[field] = value
    write_json(path, payload)
    before = snapshot_inputs(inputs)
    with pytest.raises(ValueError):
        exporter.build(inputs.config, inputs.format, inputs.binding)
    assert snapshot_inputs(inputs) == before


@pytest.mark.parametrize(
    "field", ["adc2_n_bits", "adc2_v_min", "adc2_v_max", "adc2_codebook", "adc2_endpoint_policy"]
)
def test_adc2_binding_requires_every_consumed_field(exporter, inputs, field):
    cfg = read_json(inputs.config)
    del cfg[field]
    write_json(inputs.config, cfg)
    with pytest.raises(ValueError):
        exporter.build(inputs.config, inputs.format, inputs.binding)
