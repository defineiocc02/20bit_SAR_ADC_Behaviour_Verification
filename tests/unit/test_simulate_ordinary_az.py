"""普通 AZ 主链接入：N→N、零误差逐位一致、非零误差改变码。"""

from __future__ import annotations

import numpy as np

from adi_model.behavioral.ra_az import OrdinaryAZConfig
from adi_model.defaults import default_model
from adi_model.sampler import sine_input


def test_az_none_matches_baseline():
    m = default_model()
    a = m.simulate(sine_input(2.7, 1e6), 32)
    b = m.simulate(sine_input(2.7, 1e6), 32, ordinary_az=None)
    assert np.array_equal(a.codes.code, b.codes.code)


def test_az_zero_error_is_bit_identical_and_full_rate():
    m = default_model()
    base = m.simulate(sine_input(2.7, 1e6), 32)
    az = m.simulate(
        sine_input(2.7, 1e6), 32,
        ordinary_az=OrdinaryAZConfig(enabled=True, offset_v=0.0, noise_v=0.0),
    )
    assert az.codes.code.shape == base.codes.code.shape
    np.testing.assert_array_equal(az.codes.code, base.codes.code)


def test_az_offset_changes_codes_but_keeps_n_to_n():
    m = default_model()
    base = m.simulate(sine_input(2.7, 1e6), 32)
    az = m.simulate(
        sine_input(2.7, 1e6), 32,
        ordinary_az=OrdinaryAZConfig(enabled=True, offset_v=1e-3),
    )
    assert az.codes.code.shape == (32,)
    assert int(np.sum(az.codes.code != base.codes.code)) > 0
    assert az.metadata["ordinary_az"]["pairing"].startswith("none")
