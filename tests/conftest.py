"""Shared pytest fixtures for the adi_model test suite."""

from __future__ import annotations

import os

# --------------------------------------------------------------------- paths
# Ensure `import adi_model` works without setting PYTHONPATH manually.
import sys

import numpy as np
import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, os.path.join(ROOT, "src"))


# --------------------------------------------------------------------- rng
@pytest.fixture
def rng() -> np.random.Generator:
    """A default_rng seeded for deterministic tests."""
    return np.random.default_rng(20260910)


@pytest.fixture
def cfg():
    """A default Config() instance."""
    from adi_model import Config

    return Config()


# --------------------------------------------------------------------- inputs
@pytest.fixture
def sine_input_fn(cfg):
    """A single-tone sine input at a coherent bin for N = 2**14."""
    from adi_model import sine_input

    n = 2**14
    return sine_input(0.7 * cfg.v_fs, cfg.fs * 1021 / n)


@pytest.fixture
def small_n() -> int:
    """Small N for fast unit tests (must be coherent for the chosen fin)."""
    return 2**14
