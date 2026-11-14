"""检查实际保存的节点电荷、物理切片编号及显式因果控制记录。"""

from dataclasses import replace

import numpy as np
import pytest

from adi_model.control.timing import SlicePlan
from adi_model.simulation.config import ConfigError
from adi_model.simulation.scheduler import Scheduler
from adi_model.simulation.slice_pool import PhysicalSlicePool, check_causality
from adi_model.topology.dac_arch import charge_from_masks, charge_from_sampling_connections


def _plan():
    a = np.arange(8)
    b = np.arange(8, 16)
    spare = np.r_[np.arange(6), 16, 17]
    return SlicePlan(
        np.stack((a, b, spare)),
        np.stack((b, spare, b)),
        np.array([-1, 0, 1]),
        np.array([False, True, True]),
        "explicit-synthetic-control",
        {"bank": [0, 1, 0]},
    )


class TestCausality:
    def test_no_cross_cycle_violations(self, toy_physical_case):
        plan = Scheduler(toy_physical_case.cfg, binding=_plan()).plan(3)
        result = check_causality(plan)
        assert result["transitions"] == 2 and result["violations"] == 0
        np.testing.assert_array_equal(plan.conv[1:], plan.acq[:-1])
        assert all(not set(c) & set(a) for c, a in zip(plan.conv, plan.acq, strict=True))

    def test_mean_coverage_full(self, toy_physical_case):
        plan = Scheduler(toy_physical_case.cfg, binding=_plan()).plan(3)
        assert check_causality(plan)["mean_coverage"] == 8
        assert {16, 17} <= set(plan.conv.ravel())

    def test_invalid_mask_does_not_silently_pass(self, toy_physical_case):
        plan = _plan()
        changed = plan.conv.copy()
        changed[2] = np.arange(8)
        with pytest.raises(ConfigError, match="preceding acquisition"):
            Scheduler(toy_physical_case.cfg, binding=replace(plan, conv=changed)).plan(3)

    def test_missing_schedule_and_bank_are_not_inferred(self, toy_physical_case):
        with pytest.raises(ConfigError, match="schedule binding"):
            Scheduler(toy_physical_case.cfg, binding=None)
        plan = replace(_plan(), meta={})
        with pytest.raises(ConfigError, match="bank"):
            Scheduler(toy_physical_case.cfg, binding=plan).reserve(3)


class TestMismatchBinding:
    def test_dac_error_changes_only_for_selections_using_perturbed_physical_slice(
        self, toy_physical_case
    ):
        case = toy_physical_case
        groups = np.vstack((np.arange(8), np.arange(8, 16)))
        command = replace(
            case.command,
            main_on=np.repeat(case.command.main_on, 2, axis=0),
            sub_on=np.repeat(case.command.sub_on, 2, axis=0),
        )
        before = charge_from_masks(
            case.chip, groups, command, reference_high_v=3, reference_low_v=-1
        )
        changed = case.chip.C_main.copy()
        changed[4, 0] *= 1.02
        chip = replace(case.chip, C_main=changed)
        after = charge_from_masks(chip, groups, command, reference_high_v=3, reference_low_v=-1)
        assert after.charge_c[0] != before.charge_c[0]
        assert after.input_equivalent_v[0] != before.input_equivalent_v[0]
        np.testing.assert_array_equal(after.charge_c[1:], before.charge_c[1:])
        np.testing.assert_array_equal(
            after.signal_capacitance_f[1:], before.signal_capacitance_f[1:]
        )

    def test_actual_capacitance_and_clock_parameters_are_not_randomly_fabricated(
        self, toy_physical_case, monkeypatch
    ):
        case = toy_physical_case
        monkeypatch.setattr(
            np.random, "default_rng", lambda *args: pytest.fail("pool invented a random source")
        )
        pool = PhysicalSlicePool(case.cfg, chip=case.chip, nominal_codebook=case.book)
        np.testing.assert_array_equal(pool.chip.C_main, case.chip.C_main)
        assert not pool.chip.C_main.flags.writeable
        with pytest.raises(ValueError):
            pool.chip.C_main[0, 0] = 0
        with pytest.raises(ConfigError, match="t_skew"):
            pool.require_slice_parameter("t_skew", "s")


def test_actual_acquisition_ownership_and_release_are_required(toy_physical_case):
    case = toy_physical_case
    pool = PhysicalSlicePool(case.cfg, chip=case.chip, nominal_codebook=case.book)
    with pytest.raises(ConfigError):
        pool.held_node_charge(case.ids[0], sample_id=7)
    expected = charge_from_sampling_connections(
        case.chip,
        case.ids,
        case.connections,
        input_voltage_v=1.2,
        sampled_top_v=0.2,
        sampled_subnode_v=-0.1,
    )
    pool.acquire(
        case.ids[0],
        1.2,
        sample_id=7,
        sampled_top_v=0.2,
        sampled_subnode_v=-0.1,
        connections=case.connections,
    )
    np.testing.assert_allclose(
        pool.held_node_charge(case.ids[0], sample_id=7), expected.node_charge_c[0]
    )
    with pytest.raises(ConfigError):
        pool.held_node_charge(case.ids[0], sample_id=8)
    with pytest.raises(ConfigError):
        pool.acquire(
            case.ids[0],
            1.2,
            sample_id=8,
            sampled_top_v=0.2,
            sampled_subnode_v=-0.1,
            connections=case.connections,
        )
    pool.release(case.ids[0], sample_id=7)
    assert not pool.held_valid[case.ids[0]].any()
    assert np.all(pool.n_conversions[case.ids[0]] == 1)
    with pytest.raises(ConfigError):
        pool.held_node_charge(case.ids[0], sample_id=7)


def test_sampling_nodes_and_connections_cannot_be_guessed(toy_physical_case):
    case = toy_physical_case
    pool = PhysicalSlicePool(case.cfg, chip=case.chip)
    with pytest.raises(ConfigError):
        pool.acquire(case.ids[0], 1.2, sample_id=7, sampled_top_v=0.2, sampled_subnode_v=-0.1)
    with pytest.raises(ConfigError):
        pool.acquire(
            case.ids[0],
            1.2,
            sample_id=7,
            sampled_top_v=None,
            sampled_subnode_v=-0.1,
            connections=case.connections,
        )
