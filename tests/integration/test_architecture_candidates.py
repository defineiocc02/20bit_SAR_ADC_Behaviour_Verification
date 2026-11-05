"""检查显式绑定的物理组件；不执行完整目标 ADC 仿真。"""

from dataclasses import replace

import numpy as np
import pytest

from adi_model.config import Config, ConfigError
from adi_model.dac_arch import (
    SplitChip,
    SplitDAC,
    charge_from_masks,
    topplate_voltage_from_effective_charge,
)
from adi_model.dem import SplitSwitchCommand
from adi_model.sadc import build_first_stage_quantizer
from adi_model.slice_pool import PhysicalSlicePool


def test_nine_unknown_input_bits_do_not_come_from_dither_or_rdac_truth(
    synthetic_sadc_thresholds, toy_physical_case
):
    cfg = Config(sadc_thresholds_v=synthetic_sadc_thresholds, sadc_endpoint_policy="half_open")
    adc = build_first_stage_quantizer(cfg)
    assert adc.n_code == 512
    with pytest.raises(TypeError):
        build_first_stage_quantizer(cfg, toy_physical_case.chip)
    with pytest.raises(TypeError):
        Config(dither_enhancement_bits=2)


def test_explicit_capacitor_charge_agrees_with_independent_two_node_kcl(toy_physical_case):
    case = toy_physical_case
    charge = charge_from_masks(
        case.chip, case.ids, case.command, reference_high_v=3, reference_low_v=-1
    )
    c = case.capacitance
    matrix = c * np.array([[4.25, -2], [-2, 4.5]])
    expected = np.linalg.solve(matrix, c * np.array([2.0, 2.0]))
    np.testing.assert_allclose(charge.charge_c, [8 * (2 + 4 / 9 * 2) * c], rtol=1e-14)
    np.testing.assert_allclose(charge.input_equivalent_v, [1.0], rtol=1e-14)
    top = topplate_voltage_from_effective_charge(case.chip, case.ids, charge.charge_c)
    np.testing.assert_allclose(top, [expected[0]], rtol=1e-14)
    assert not np.allclose(top, charge.input_equivalent_v)


def test_mapping_receives_digital_controls_not_capacitor_truth(toy_physical_case):
    case = toy_physical_case
    seen = []

    def binding(geometry, code, state, actual_ids):
        assert not hasattr(geometry, "C_main") and not hasattr(geometry, "gain")
        seen.append((code.copy(), state.copy(), actual_ids.copy()))
        return case.command

    pool = PhysicalSlicePool(
        case.cfg, chip=case.chip, nominal_codebook=case.book, mask_binding=binding
    )
    selected = pool.select(np.array([0]), np.array([913]), slice_ids=case.ids)
    np.testing.assert_array_equal(selected.main_on, case.command.main_on)
    np.testing.assert_array_equal(seen[0][2], case.ids)
    assert seen[0][1][0] == 913


def test_missing_mapping_is_not_replaced_by_a_legacy_off_or_rotator(toy_physical_case):
    case = toy_physical_case
    pool = PhysicalSlicePool(case.cfg, chip=case.chip, nominal_codebook=case.book)
    with pytest.raises(ConfigError, match="unbound"):
        pool.select([0], [0], slice_ids=case.ids)


@pytest.mark.parametrize("code", [[0.5], [-1], [1]])
def test_command_must_be_in_the_explicit_integer_codebook(toy_physical_case, code):
    case = toy_physical_case
    pool = PhysicalSlicePool(
        case.cfg,
        chip=case.chip,
        nominal_codebook=case.book,
        mask_binding=lambda *args: case.command,
    )
    with pytest.raises(ConfigError):
        pool.select(code, [0], slice_ids=case.ids)


def test_fractional_capacitor_masks_are_not_physical_switches(toy_physical_case):
    case = toy_physical_case
    invalid = SplitSwitchCommand(case.command.main_on.astype(float), case.command.sub_on)
    with pytest.raises(ConfigError):
        charge_from_masks(case.chip, case.ids, invalid, reference_high_v=3, reference_low_v=-1)


def test_unknown_feedback_and_parasitic_are_required_only_when_consumed(toy_physical_case):
    case = toy_physical_case
    without_feedback = SplitChip(
        case.chip.C_main, case.chip.C_sub, case.chip.C_bridge, case.chip.c_p_sub, case.chip.c_p_main
    )
    charge = charge_from_masks(
        without_feedback, case.ids, case.command, reference_high_v=3, reference_low_v=-1
    )
    assert np.isfinite(charge.charge_c).all()
    with pytest.raises(ConfigError, match="feedback"):
        SplitDAC(case.cfg, without_feedback).static_ra_output_from_charge([case.capacitance])
    without_main_parasitic = SplitChip(
        case.chip.C_main, case.chip.C_sub, case.chip.C_bridge, case.chip.c_p_sub
    )
    with pytest.raises(ConfigError):
        topplate_voltage_from_effective_charge(without_main_parasitic, case.ids, charge.charge_c)
    np.testing.assert_allclose(
        SplitDAC(case.cfg, case.chip).static_ra_output_from_charge([case.capacitance]), [2.5]
    )


def test_given_physical_arrays_are_immutable_and_never_uniformly_redistributed(toy_physical_case):
    case = toy_physical_case
    values = case.chip.C_main.copy()
    values[7, 1] *= 1.03
    chip = replace(case.chip, C_main=values)
    pool = PhysicalSlicePool(case.cfg, chip=chip)
    np.testing.assert_array_equal(pool.chip.C_main, values)
    assert pool.chip.C_main[0, 1] != pool.chip.C_main[7, 1]
    assert not pool.chip.C_main.flags.writeable
