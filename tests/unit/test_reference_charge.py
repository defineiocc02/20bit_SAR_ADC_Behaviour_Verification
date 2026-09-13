"""Rail-charge checks using direct capacitor/node equations."""

import numpy as np
import pytest

from adi_model import Config
from adi_model.reference_charge import (
    physical_reference_program,
    sampled_bottom_voltages,
    sar_loading_codes,
)
from adi_model.slice_pool import PhysicalSlicePool


def test_sar_program_contains_rejected_trials_and_actual_final_command():
    actual = sar_loading_codes(5, 3, 4, -2)
    np.testing.assert_array_equal(actual, [-2, 14, 14, 22, 14, 18, 18])
    assert actual[-1] == 5 * 4 - 2


def test_rail_charge_includes_sampled_charge_and_floating_node_redistribution():
    cfg = Config(dac_arch="split", mismatch_enable=False)
    pool = PhysicalSlicePool(cfg, np.random.default_rng(11))
    ids = np.arange(8)
    sampled = sampled_bottom_voltages(cfg, np.linspace(-0.7, 0.6, 8), 0)
    codes = [17, 143, 17, 17]
    result = physical_reference_program(cfg, pool, ids, codes, np.arange(4) * 1e-9, 0, sampled)
    # Independent capacitor-by-capacitor node equations, with no DEM helper.
    previous = pool.unit_caps[ids] * sampled
    expected = []
    for code in codes:
        km, ks = divmod(code, 8)
        charge = np.zeros(2)
        for j, physical in enumerate(ids):
            caps = pool.unit_caps[physical]
            positive = np.r_[np.arange(64) < km, np.arange(8) < ks]
            voltage = np.where(positive, 3.0, -3.0)
            rhs = sum(caps[64:] * (voltage[64:] - sampled[j, 64:]))
            vsub = np.linalg.solve(
                [[pool.bridge_caps[physical] + sum(caps[64:]) + pool.sub_parasitic[physical]]],
                [rhs],
            )[0]
            after = caps * (voltage - np.r_[np.zeros(64), np.full(8, vsub)])
            for unit in range(72):
                charge[0 if positive[unit] else 1] += after[unit] - previous[j, unit]
            previous[j] = after
        expected.append(charge)
    np.testing.assert_allclose(result.charge_c, expected, atol=2e-25, rtol=0)
    np.testing.assert_allclose(result.bottom_charge_c, previous, atol=1e-26)
    np.testing.assert_array_equal(result.charge_c[-1], [0, 0])
    assert result.charge_c[1, 0] > 0 and result.charge_c[2, 1] < 0


@pytest.mark.parametrize("bank", ["main", "sub"])
def test_dither_connections_and_dem_reference_sensitivity_are_physical(bank):
    cfg = Config.paper_literal(
        mismatch_enable=True,
        dem_enable=True,
        dither_mode="sampling",
        dither_split_bank=bank,
    )
    pool = PhysicalSlicePool(cfg, np.random.default_rng(11))
    ids = np.arange(8)
    sampled = sampled_bottom_voltages(cfg, np.full(8, 0.4), 1)
    stop = cfg.dac_n_main if bank == "main" else cfg.dac_n_units
    np.testing.assert_array_equal(sampled[0, stop - 4 : stop], [3, 3, 3, -3])
    codes = np.array([7, 8, 223, 511])
    program = physical_reference_program(cfg, pool, ids, codes, np.arange(4) * 1e-9, 201, sampled)
    np.testing.assert_allclose(program.sensitivity.sum(axis=1), 1, atol=3e-16)
    voltage = pool.split_dac_voltage(np.tile(ids, (4, 1)), codes, np.full(4, 201))
    np.testing.assert_allclose(program.sensitivity @ [3, -3], voltage, atol=3e-15)


def test_reference_load_is_not_a_fixed_pattern_independent_of_input():
    cfg = Config(dac_arch="split", mismatch_enable=False)
    pool = PhysicalSlicePool(cfg, np.random.default_rng(11))
    charges = []
    for vin in (-1.0, 1.0):
        program = physical_reference_program(
            cfg,
            pool,
            np.arange(8),
            [200],
            [0.0],
            0,
            sampled_bottom_voltages(cfg, np.full(8, vin), 0),
        )
        charges.append(program.charge_c[0])
    assert np.max(np.abs(charges[0] - charges[1])) > 1e-12
