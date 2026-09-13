"""Electrical and causal checks independent of the shared runner implementation."""

from dataclasses import replace

import numpy as np
import pytest

from adi_model import Config, sine_input
from adi_model.dem import split_switch_command
from adi_model.pipeline import run_pipeline
from adi_model.sim import run_sim
from adi_model.sim_split import run_sim_split, run_sim_split_reference
from adi_model.slice_pool import PhysicalSlicePool


def cfg_ideal(**kw):
    return replace(
        Config(
            dac_arch="split",
            mismatch_enable=False,
            enable_sampling_noise=False,
            ra_enable_noise=False,
            sadc_rdac_gain_mismatch=0,
        ),
        **kw,
    )


def test_shared_engine_matches_independent_aggregate_ideal_limit():
    cfg = cfg_ideal()
    fn = sine_input(2.7, cfg.fs * 317 / 4096)
    physical = run_pipeline(cfg, fn, 4096)
    oracle = run_sim_split_reference(cfg, fn, 4096)
    np.testing.assert_allclose(physical.vd_true, oracle.vd_true, atol=3e-15)
    np.testing.assert_allclose(physical.g_vec, oracle.g_vec, atol=1e-13)
    assert np.max(np.abs(physical.out - oracle.out)) <= cfg.delta2 / cfg.g0 + 1e-13


def test_priming_and_held_charge_are_bound_to_the_sample():
    cfg = cfg_ideal(dem_mode="permute")
    r = run_pipeline(cfg, sine_input(2, cfg.fs * 101 / 1024), 1024)
    np.testing.assert_array_equal(r.conv_slice_ids[1:], r.acq_slice_ids[:-1])
    np.testing.assert_array_equal(r.held_sample, np.broadcast_to(r.sample_id[:, None], (1024, 8)))
    coeff = r.pool.split_coefficients(r.conv_slice_ids)
    np.testing.assert_allclose(r.stored_charge, r.sample.x1 * coeff["c_signal"], atol=1e-25)
    assert r.acquisition_start[0] < 0
    assert r.pool.n_conversions.sum() == 1024 * cfg.n_active


@pytest.mark.parametrize("runner", [run_pipeline, run_sim_split])
def test_nonuniform_capacitor_perturbation_is_local_and_predictable(runner):
    cfg = cfg_ideal(dem_mode="permute")
    fn = sine_input(1.7, cfg.fs * 97 / 1024)
    before = runner(cfg, fn, 1024)
    pool = before.pool
    victim = 4
    pool.unit_caps[victim, 0] *= 1.02
    after = runner(cfg, fn, 1024, pool=pool)
    uses = np.any(before.conv_slice_ids == victim, axis=1)
    assert np.any(after.e_dac[uses] != before.e_dac[uses])
    np.testing.assert_array_equal(after.e_dac[~uses], before.e_dac[~uses])
    # Independent direct sign-charge sum for one affected conversion.
    n = np.flatnonzero(uses)[7]
    ids = after.conv_slice_ids[n]
    caps = pool.unit_caps[ids]
    a, b = caps[:, :64].sum(axis=1), caps[:, 64:].sum(axis=1)
    beta = pool.bridge_caps[ids] / (pool.bridge_caps[ids] + b + pool.sub_parasitic[ids])
    main, sub = divmod(int(after.k[n]), 8)
    qm = 2 * caps[:, :main].sum(axis=1) - a
    qs = 2 * caps[:, 64 : 64 + sub].sum(axis=1) - b
    expected = cfg.v_fs * (qm + beta * qs).sum() / (a + beta * b).sum()
    assert after.vd_true[n] == pytest.approx(expected, abs=2e-15)


@pytest.mark.parametrize("nm,complete", [(64, False), (63, True)])
def test_multidimensional_masks_are_realizable_and_preserve_nominal_charge(nm, complete):
    cfg = cfg_ideal(
        dac_n_main=nm, dac_complete_range=complete, dem_enable=True, dem_bridge_enable=True
    )
    k = np.arange(512)
    cmd = split_switch_command(cfg, k, np.arange(512))
    for order, count in [(cmd.main_order, nm), (cmd.sub_order, 8)]:
        np.testing.assert_array_equal(
            np.sort(order, axis=1), np.broadcast_to(np.arange(count), order.shape)
        )
    np.testing.assert_allclose((8 * cmd.main_counts + cmd.sub_counts).mean(axis=1), k)
    assert np.all(cmd.main_counts == np.floor(cmd.main_counts))
    assert np.all(cmd.sub_counts == np.floor(cmd.sub_counts))
    pool = PhysicalSlicePool(cfg, np.random.default_rng(1))
    ids = np.tile(np.arange(8), (512, 1))
    actual = pool.split_dac_voltage(ids, k, np.arange(512))
    np.testing.assert_allclose(actual, -cfg.v_fs + k * cfg.nominal_rdac_step, atol=3e-15)
    maps = {tuple(np.r_[a, b]) for a, b in zip(cmd.main_order, cmd.sub_order, strict=True)}
    assert len(maps) == 512


def test_unary_dac_uses_the_same_selected_slices_as_its_gain():
    cfg = replace(cfg_ideal(dem_mode="permute", mismatch_enable=True), dac_arch="unary")
    r = run_sim(cfg, sine_input(1.5, cfg.fs * 101 / 1024), 1024)
    n = 17
    caps = r.chip.C_true[r.conv_slice_ids[n]].reshape(-1)
    # DEM disabled: mapper interleaves slice rows; obtain nominal order only.
    from adi_model.mapper import unit_rank_arrays

    rows, cols = unit_rank_arrays(cfg)
    ordered = r.chip.C_true[r.conv_slice_ids[n][rows[0]], cols[0]]
    expected = cfg.v_fs * (2 * ordered[: int(r.k[n])].sum() / caps.sum() - 1)
    assert r.vd_true[n] == pytest.approx(expected, abs=2e-15)


def test_noise_switches_do_not_redraw_capacitors_or_schedule():
    cfg = cfg_ideal(mismatch_enable=True, dem_mode="permute")
    fn = sine_input(1, cfg.fs * 101 / 1024)
    a = run_pipeline(cfg, fn, 1024)
    b = run_pipeline(replace(cfg, enable_sampling_noise=True, ra_enable_noise=True), fn, 1024)
    np.testing.assert_array_equal(a.pool.unit_caps, b.pool.unit_caps)
    np.testing.assert_array_equal(a.pool.t_skew, b.pool.t_skew)
    np.testing.assert_array_equal(a.conv_slice_ids, b.conv_slice_ids)


def test_reused_chip_obeys_runtime_masks_without_changing_fabrication_controls():
    cfg = cfg_ideal()
    pool = PhysicalSlicePool(cfg, np.random.default_rng(1))
    caps = pool.unit_caps.copy()
    changed = replace(
        cfg, dither_mode="sampling", dither_discrete=True, dem_enable=True, dem_bridge_enable=True
    )
    fn = sine_input(1.3, cfg.fs * 47 / 1024)
    reused = run_pipeline(changed, fn, 1024, pool=pool)
    fresh = run_pipeline(changed, fn, 1024)
    np.testing.assert_array_equal(reused.out, fresh.out)
    np.testing.assert_array_equal(pool.unit_caps, caps)
    assert pool.cfg.dither_mode == "off"
    assert np.max(abs(reused.err)) < changed.delta2 / changed.g0 / changed.dither_alpha
