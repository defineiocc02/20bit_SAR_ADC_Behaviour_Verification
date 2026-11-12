"""Dither must act on two different places, not one.

Patent FIG.19 splits the word: D5/D3/D2/D1 reach the quantiser input, D6/D4
reach the SAR/DAC code path. A model that subtracts the *whole* word from both
the SADC and the RDAC has lost that split, and its "correct" final output only
proves the digital de-dithering, not the internal connection.

The probes below freeze everything that could mask the defect:
zero mismatch, zero noise, DC input. Then only one bit of the dither word moves.

Observed on the current code (D6 = bit 5 = upper, D5 = bit 4 = lower):

    word= 0   coarse=290  out=594193
    word=32   coarse=290  out=594193   <- upper only: SADC decision unchanged
    word=16   coarse=288  out=594193   <- lower only: SADC decision moves
    word=48   coarse=288  out=594193

So on this path the split is real. The final output is identical in all four
cases, which is the de-dithering working; the point of the test is that the
*intermediate* decision differs between upper and lower.

NOTE: this covers the Fig19 program path (v1). The independent charge-port path
(v2) is a different connection and is NOT covered here.
"""

from __future__ import annotations

import numpy as np
import pytest

from verification.vectors.generate import PROFILE  # noqa: F401  (keeps package import warm)


def _model():
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(root / "tools"))
    import run_p4_ablation as runner

    return runner._build_model(0.0, False)


DC = 0.4
UPPER_BIT = 5  # D6
LOWER_BIT = 4  # D5


def _run(word: int):
    model = _model()
    x = lambda t: np.full_like(np.asarray(t, dtype=float), DC)  # noqa: E731
    return model.simulate(x, 4, dither_words=np.full(4, word, dtype=np.int64))


def test_upper_bit_does_not_move_the_sadc_decision():
    base = _run(0)
    upper = _run(1 << UPPER_BIT)
    assert np.array_equal(np.asarray(base.raw.coarse), np.asarray(upper.raw.coarse)), (
        "setting only the upper dither bit changed the SADC decision; "
        "the split has collapsed into a single injection point"
    )


def test_lower_bit_does_move_the_sadc_decision():
    """Control: if nothing moved, the test above would pass for the wrong reason."""
    base = _run(0)
    lower = _run(1 << LOWER_BIT)
    assert not np.array_equal(np.asarray(base.raw.coarse), np.asarray(lower.raw.coarse)), (
        "the lower bit did not reach the quantiser; it is not the lower branch"
    )


def test_final_output_is_restored_for_every_word():
    """De-dithering: the final code must not depend on the injected word."""
    outputs = {int(np.asarray(_run(w).codes.code)[0]) for w in
               (0, 1 << UPPER_BIT, 1 << LOWER_BIT, (1 << UPPER_BIT) | (1 << LOWER_BIT))}
    assert len(outputs) == 1, f"final code depends on the dither word: {outputs}"
