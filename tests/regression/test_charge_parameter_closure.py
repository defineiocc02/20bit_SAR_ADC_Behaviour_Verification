"""用显式阈值、反馈电容及采样连接检查参数确实作用于电荷路径。

小型电容阵列与阈值属于声明的合成夹具，不继承旧 64+8、固定 4× 扰动或
从 PDK 随机制造的默认假设。数字连接项只消费名义参考，不读取物理电容真值。
"""

from dataclasses import replace

import numpy as np
import pytest

from adi_model.topology.dac_arch import charge_from_sampling_connections
from adi_model.control.dem import SamplingConnections, SplitSwitchGeometry
from adi_model.simulation.pipeline import run_pipeline
from adi_model.behavioral.sadc import SADC
from adi_model.behavioral.sampler import dc_input
from adi_model.behavioral.sampling_charge import digital_connection_terms
from adi_model.simulation.slice_pool import PhysicalSlicePool


def test_explicit_threshold_offset_changes_decisions_and_preserves_in_range_reconstruction(
    explicit_pipeline_factory,
):
    n = 32
    cfg, controls = explicit_pipeline_factory(n_samples=n)

    def near_boundaries(t):
        return -3 + (128 + np.asarray(t) * cfg.fs) * (6 / 512) + 0.0002

    before = run_pipeline(cfg, near_boundaries, n, **controls)
    shifted = replace(cfg, sadc_thresholds_v=tuple(np.asarray(cfg.sadc_thresholds_v) + 0.001))
    after = run_pipeline(shifted, near_boundaries, n, **controls)
    np.testing.assert_array_equal(after.coarse, before.coarse - 1)
    np.testing.assert_array_equal(after.sample.input_v, before.sample.input_v)
    np.testing.assert_allclose(after.vra - before.vra, 32 * 6 / 512, atol=2e-12, rtol=0)
    assert not before.analog_overflow.any() and not after.analog_overflow.any()
    assert np.max(abs(after.err)) <= cfg.delta2 / (2 * 32) + 2e-12


def test_actual_feedback_capacitance_changes_residue_gain_without_relabeling_nominal_config(
    explicit_pipeline_factory,
):
    cfg, controls = explicit_pipeline_factory(n_samples=16)
    original = controls["pool"]
    modified = PhysicalSlicePool(
        cfg,
        chip=replace(original.chip, C_feedback_true=2 * original.chip.C_feedback_true),
        nominal_codebook=original.nominal_codebook,
        mask_binding=original.mask_binding,
        acquisition_binding=original.acquisition_binding,
        schedule_binding=original.schedule_binding,
    )
    before = run_pipeline(cfg, dc_input(0.201), 16, **controls)
    after = run_pipeline(cfg, dc_input(0.201), 16, **{**controls, "pool": modified})
    np.testing.assert_array_equal(after.coarse, before.coarse)
    np.testing.assert_array_equal(after.main_masks, before.main_masks)
    np.testing.assert_allclose(after.g_vec, before.g_vec / 2, atol=1e-14, rtol=0)
    np.testing.assert_allclose(after.vra, before.vra / 2, atol=1e-13, rtol=0)
    assert after.cfg.g0 == before.cfg.g0 == 32
    assert not after.calibration_applied
    assert not before.analog_overflow.any() and not after.analog_overflow.any()


def test_reference_connected_units_remove_actual_signal_capacitance_not_a_global_alpha(
    toy_physical_case,
):
    case = toy_physical_case
    shape = (1, 8, 2)
    connections = SamplingConnections(
        np.broadcast_to([0, 2], shape).copy(), np.broadcast_to([1, 0], shape).copy()
    )
    args = {
        "sampled_top_v": np.zeros((1, 8)),
        "sampled_subnode_v": np.zeros((1, 8)),
        "reference_high_v": 3.1,
        "reference_low_v": -2.8,
    }
    low = charge_from_sampling_connections(
        case.chip, case.ids, connections, input_voltage_v=0.1, **args
    )
    high = charge_from_sampling_connections(
        case.chip, case.ids, connections, input_voltage_v=0.4, **args
    )
    ids = case.ids
    beta = case.chip.C_bridge[ids] / (
        case.chip.C_bridge[ids] + case.chip.C_sub[ids].sum(axis=-1) + case.chip.c_p_sub[ids]
    )
    expected_signal_cap = (case.chip.C_main[ids, 0] + beta * case.chip.C_sub[ids, 1]).sum(axis=1)
    np.testing.assert_allclose(low.signal_capacitance_f, expected_signal_cap, atol=1e-26)
    np.testing.assert_allclose(
        high.effective_node_charge_c - low.effective_node_charge_c,
        -expected_signal_cap * 0.3,
        atol=1e-26,
    )
    assert np.all(low.signal_capacitance_f < low.normalization_capacitance_f)
    np.testing.assert_array_equal(low.normalization_capacitance_f, high.normalization_capacitance_f)


def test_digital_connection_conversion_is_repeatable_and_uses_only_nominal_rails(
    toy_physical_case,
):
    case = toy_physical_case
    geometry = SplitSwitchGeometry(2, 2, 18, 8)
    connections = SamplingConnections(
        np.broadcast_to([0, 2], (1, 8, 2)).copy(),
        np.broadcast_to([1, 0], (1, 8, 2)).copy(),
    )
    old_main = connections.main.copy()
    old_mask = case.command.main_on.copy()
    arguments = {"nominal_reference_high_v": 3.0, "nominal_reference_low_v": -3.0}
    first = digital_connection_terms(geometry, connections, case.command, **arguments)
    second = digital_connection_terms(geometry, connections, case.command, **arguments)
    np.testing.assert_array_equal(first[0], np.broadcast_to([True, False, False, True], (1, 8, 4)))
    np.testing.assert_array_equal(first[1], np.broadcast_to([-3.0, 6.0, -6.0, 3.0], (1, 8, 4)))
    for a, b in zip(first, second, strict=True):
        np.testing.assert_array_equal(a, b)
        assert not a.flags.writeable
    np.testing.assert_array_equal(connections.main, old_main)
    np.testing.assert_array_equal(case.command.main_on, old_mask)


@pytest.mark.parametrize("thresholds", [[0, 1, 0.5], [0, np.nan, 1], [0, 0, 1]])
def test_invalid_threshold_arrays_cannot_reach_searchsorted(thresholds):
    with pytest.raises(ValueError, match="thresholds"):
        SADC(np.asarray(thresholds), endpoint_policy="half_open")


def test_thresholds_are_owned_immutable_inputs_without_hidden_random_fabrication():
    thresholds = np.linspace(-3, 3, 513)
    original = thresholds.copy()
    first = SADC(thresholds, endpoint_policy="half_open")
    second = SADC(thresholds, endpoint_policy="half_open")
    thresholds[256] += 0.001
    np.testing.assert_array_equal(first.thresholds, original)
    np.testing.assert_array_equal(second.thresholds, original)
    assert not first.thresholds.flags.writeable
