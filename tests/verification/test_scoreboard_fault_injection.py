"""Fault injection: the scoreboard must reject wrong data, not just accept right data.

A comparator that only ever says "pass" is indistinguishable from no comparator.
Each test below takes a **real passing dump** produced by the VCS run under
`verification/reports/dumps/`, injects one specific defect, and asserts the
scoreboard reports a mismatch naming the expected field.

If a dump is absent the test skips with a message — it never fabricates one, and
a skip is not a pass.

Covered injections (acceptance G4.2):
    mask flip · rounding change · floor-vs-truncate · sample id misbinding ·
    duplicate/lost row · output word off by one · slice/slot swap
"""

from __future__ import annotations

from pathlib import Path

import pytest

from verification.cosim import scoreboard as sb

DUMPS = Path(__file__).resolve().parents[2] / "verification" / "reports" / "dumps"


def _load(module: str, name: str) -> str:
    path = DUMPS / module / name
    if not path.is_file():
        pytest.skip(f"no real dump for {module}; run `run_verification --level module` first")
    return path.read_text()


def _rewrite_row(text: str, index: int, column: int, value: str) -> str:
    """Replace one whitespace-separated field of one row."""
    lines = text.splitlines()
    fields = lines[index].split()
    fields[column] = value
    lines[index] = " ".join(fields)
    return "\n".join(lines) + "\n"


def test_clean_dumps_pass():
    """Control: the untouched dumps must pass, otherwise the injections prove nothing."""
    cases = [
        ("sadc_enc", "sadc_enc.out", sb.compare_sadc),
        ("unit_therm", "unit_therm.out", sb.compare_unit_therm),
        ("adc2_dec", "adc2_dec.out", sb.compare_adc2),
        ("div_floor", "div_floor.out", sb.compare_div_floor),
        ("rdac_drv", "rdac_drv.out", sb.compare_rdac_drv),
        ("slice_pool_ctrl", "slice_pool_ctrl.out", sb.compare_slice_pool),
        ("recon_core", "recon_core.out", sb.compare_recon_core),
    ]
    for module, name, comparator in cases:
        passed, total, mismatch = comparator(_load(module, name))
        assert mismatch is None, f"{module} clean dump did not pass: {mismatch}"
        assert passed == total


def test_mask_flip_is_rejected():
    """A single flipped thermometer bit must be caught, not averaged away."""
    text = _load("unit_therm", "unit_therm.out")
    row = text.splitlines()[0].split()
    mask = int(row[0], 16) ^ 0x1  # flip physical unit 0 of slot 0
    mutated = _rewrite_row(text, 0, 0, f"{mask:x}")
    _, _, mismatch = sb.compare_unit_therm(mutated)
    assert mismatch is not None
    assert mismatch.field.startswith("main_on[")


def test_rounding_change_is_rejected():
    """Half-to-even vs add-half-then-truncate differs by 1 LSB on tie codes."""
    text = _load("adc2_dec", "adc2_dec.out")
    value = int(text.splitlines()[0].split()[0], 16)
    mutated = _rewrite_row(text, 0, 0, f"{(value + 1) & ((1 << 64) - 1):x}")
    _, _, mismatch = sb.compare_adc2(mutated)
    assert mismatch is not None
    assert mismatch.field == "fine_q"


def test_floor_versus_truncate_is_rejected():
    """A quotient truncated toward zero differs from floor by 1 on negatives."""
    text = _load("div_floor", "div_floor.out")
    rows = text.splitlines()
    for index, row in enumerate(rows):
        q = int(row.split()[0], 16)
        if q >= (1 << 62):  # a negative quotient in two's complement
            fields = row.split()
            original = int(fields[0], 16)
            fields[0] = f"{(original + 1) & ((1 << 63) - 1):x}"
            mutated = "\n".join(rows[:index] + [" ".join(fields)] + rows[index + 1:]) + "\n"
            _, _, mismatch = sb.compare_div_floor(mutated)
            assert mismatch is not None
            assert mismatch.field == "q"
            return
    pytest.skip("no negative quotient in this vector set")


def test_latency_change_is_rejected():
    """A retimed divider must fail the timing contract, not just the value check."""
    text = _load("div_floor", "div_floor.out")
    mutated = _rewrite_row(text, 0, 2, "10")
    _, _, mismatch = sb.compare_div_floor(mutated)
    assert mismatch is not None
    assert mismatch.field == "latency_cycles"


def test_slice_id_swap_is_rejected():
    """Swapping two physical slice ids must not look like a legal fanout."""
    text = _load("rdac_drv", "rdac_drv.out")
    row = text.splitlines()[0].split()
    selector = int(row[0], 16) ^ 0x1  # move ownership of slice 0
    mutated = _rewrite_row(text, 0, 0, f"{selector:x}")
    _, _, mismatch = sb.compare_rdac_drv(mutated)
    assert mismatch is not None
    assert mismatch.field == "slice_sel"


def test_sample_id_misbinding_is_rejected():
    """A scheduler that retires the wrong sample id must fail even if masks match."""
    text = _load("slice_pool_ctrl", "slice_pool_ctrl.out")
    lines = text.splitlines()
    # sample_id is column 5; shift every id by one to emulate a mis-bound epoch.
    for index, line in enumerate(lines):
        fields = line.split()
        fields[5] = str(int(fields[5]) + 1)
        lines[index] = " ".join(fields)
    _, _, mismatch = sb.compare_slice_pool("\n".join(lines) + "\n")
    assert mismatch is not None
    assert mismatch.field == "sample_id"


def test_duplicate_or_lost_row_is_rejected():
    """Dropping a row must not silently shorten the comparison."""
    text = _load("sadc_enc", "sadc_enc.out")
    mutated = "\n".join(text.splitlines()[:-1]) + "\n"
    _, _, mismatch = sb.compare_sadc(mutated)
    assert mismatch is not None
    assert mismatch.field == "count"


def test_output_word_off_by_one_is_rejected():
    """The final 20-bit word is compared exactly; ±1 LSB is a failure."""
    text = _load("recon_core", "recon_core.out")
    row = text.splitlines()[0].split()
    word = int(row[0], 16)
    mutated = _rewrite_row(text, 0, 0, f"{word ^ 0x1:x}")
    _, _, mismatch = sb.compare_recon_core(mutated)
    assert mismatch is not None
    assert mismatch.field == "dout"
