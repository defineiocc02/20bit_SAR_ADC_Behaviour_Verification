"""Self-checks for the verification reference models.

These are not DUT tests. They pin the reference against the *contract text* so
that a later edit cannot quietly turn the oracle into something that agrees with
a buggy RTL. Run with:

    python -m pytest tests/verification -q
"""

from __future__ import annotations

import random

import pytest
import yaml

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


def test_adc2_bin_center_is_monotone_and_bounded():
    min_q, max_q = -(1 << 32), 1 << 32
    previous = None
    for code in range(0, 1 << 12, 7):
        value, _ = adc2_bin_center(code, min_q, max_q, 12)
        assert min_q <= value <= max_q
        if previous is not None:
            assert value >= previous
        previous = value


def test_sadc_code_counts_bits_and_tolerates_bubbles():
    assert sadc_code(0) == 0
    assert sadc_code((1 << 511) - 1) == 511
    # A bubble is not rejected; the popcount simply reflects it.
    assert sadc_code(0b1011) == 3


def test_reconstruct_clips_low_and_reports_the_flag():
    """Below the negative rail the word saturates at 0 and the flag goes high.

    The threshold is set by gain, so the stimulus has to be far enough below
    -1.0 V/Vfs to leave no doubt: with gain = 4 units the clip point is -4 V/Vfs.
    """
    terms = recon_terms([1 << 30] * 4, [1, 1, 0, 0], [1, 1, -1, -1], gain_mode="all_signal")
    result = reconstruct(fine_q=-(1 << 40), offset_q=0, injection_q=0, terms=terms)
    assert result.word == 0
    assert result.clip_low
    # And the symmetric positive extreme saturates at the top code.
    high = reconstruct(fine_q=1 << 40, offset_q=0, injection_q=0, terms=terms)
    assert high.word == 1048575
    assert high.clip_high


def test_reconstruct_is_inside_the_output_range_by_construction():
    terms = ReconTerms(rails_q30=0, gain_q30=1 << 30, total_q30=1 << 30)
    for fine in (-(1 << 32), 0, (1 << 32) - 1):
        result = reconstruct(fine_q=fine, offset_q=0, injection_q=0, terms=terms)
        assert 0 <= result.word < OUTPUT_COUNT


def test_contract_params_match_the_frozen_rtl_header():
    """The YAML contract and rtl_params.vh must not drift apart."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    params = {}
    for line in (root / "verification/contracts/rtl_params.vh").read_text().splitlines():
        if "localparam int unsigned" in line and "=" in line:
            name = line.split("localparam int unsigned")[1].split("=")[0].strip()
            value = line.split("=")[1].strip().rstrip(";").replace("32'd", "")
            params[name] = int(value)
    spec = yaml.safe_load((root / "verification/contracts/digital_interfaces.yaml").read_text())
    for key, value in spec["params"].items():
        assert params[key] == value, key


@pytest.mark.parametrize("module", ["sadc_enc", "adc2_dec", "div_floor", "recon_core"])
def test_vector_sets_are_reproducible(module):
    """Same seed must regenerate byte-identical vectors."""
    from verification.vectors.generate import GENERATORS

    first = GENERATORS[module](20261112)
    second = GENERATORS[module](20261112)
    assert first.fields == second.fields
