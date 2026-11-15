"""用独立电容支路 KCL 检查显式浮置网络的参考轨电荷。

电容、节点和参考轨均是小型数学夹具，不是原芯片参数。参考事件由调用方提供，
不从粗码、DEM 状态或数字相位表推断；浮置 top 也不作为输入等效残差。
"""

from dataclasses import FrozenInstanceError

import numpy as np
import pytest

from adi_model.behavioral.reference_charge import (
    physical_reference_program,
    sampled_bottom_voltages,
)


@pytest.fixture
def reference_case():
    """声明两个切片、两个主单元和一个子单元的完整连接图。"""
    return {
        "slice_ids": np.array([3, 14]),
        "main_caps_f": 1e-12 * np.array([[1.0, 1.7], [0.8, 1.3]]),
        "sub_caps_f": 1e-12 * np.array([[0.9], [1.4]]),
        "bridge_caps_f": 1e-12 * np.array([1.2, 1.6]),
        "main_parasitic_f": 1e-12 * np.array([0.2, 0.1]),
        "sub_parasitic_f": 1e-12 * np.array([0.3, 0.4]),
        "main_on": np.array(
            [[[1, 0], [0, 1]], [[1, 1], [0, 1]], [[0, 0], [1, 0]], [[0, 0], [1, 0]]], dtype=bool
        ),
        "sub_on": np.array([[[0], [1]], [[1], [0]], [[1], [1]], [[1], [1]]], dtype=bool),
        "times_s": np.array([0.0, 4e-10, 1.7e-09, 1.7e-09]),
        "sampled_main_bottom_v": np.array([[0.2, -2.7], [2.9, -0.4]]),
        "sampled_sub_bottom_v": np.array([[2.9], [0.1]]),
        "sampled_top_v": np.array([0.03, -0.01]),
        "sampled_subnode_v": np.array([0.08, -0.02]),
        "ref_high_v": 3.1,
        "ref_low_v": -2.8,
    }


def _branch_oracle(case):
    """按独立支路装配 KCL，不调用电荷或 DEM 辅助函数。"""
    cm, cs = case["main_caps_f"], case["sub_caps_f"]
    count = len(case["slice_ids"])
    edges = []
    for i in range(count):
        for unit, cap in enumerate(cm[i]):
            edges.append(
                (
                    0,
                    None,
                    cap,
                    case["sampled_top_v"][i],
                    case["sampled_main_bottom_v"][i, unit],
                    (i, unit),
                )
            )
        for unit, cap in enumerate(cs[i]):
            edges.append(
                (
                    i + 1,
                    None,
                    cap,
                    case["sampled_subnode_v"][i],
                    case["sampled_sub_bottom_v"][i, unit],
                    (i, cm.shape[1] + unit),
                )
            )
        edges.extend(
            [
                (
                    0,
                    i + 1,
                    case["bridge_caps_f"][i],
                    case["sampled_top_v"][i],
                    case["sampled_subnode_v"][i],
                    None,
                ),
                (0, None, case["main_parasitic_f"][i], case["sampled_top_v"][i], 0.0, None),
                (i + 1, None, case["sub_parasitic_f"][i], case["sampled_subnode_v"][i], 0.0, None),
            ]
        )
    matrix = np.zeros((count + 1, count + 1))
    initial = np.zeros(count + 1)
    bottom_before = np.empty((count, cm.shape[1] + cs.shape[1]))
    for a, b, cap, va, vb, port in edges:
        initial[a] += cap * (va - vb)
        matrix[a, a] += cap
        if b is not None:
            initial[b] += cap * (vb - va)
            matrix[b, b] += cap
            matrix[a, b] -= cap
            matrix[b, a] -= cap
        if port is not None:
            bottom_before[port] = cap * (vb - va)
    nodes, charges = [], []
    for main, sub in zip(case["main_on"], case["sub_on"], strict=True):
        mask = np.concatenate((main, sub), axis=1)
        rhs = initial.copy()
        for a, _b, cap, _va, _vb, port in edges:
            if port is not None:
                rhs[a] += cap * (case["ref_high_v"] if mask[port] else case["ref_low_v"])
        voltage = np.linalg.solve(matrix, rhs)
        bottom_after = np.empty_like(bottom_before)
        for a, _b, cap, _va, _vb, port in edges:
            if port is not None:
                rail = case["ref_high_v"] if mask[port] else case["ref_low_v"]
                bottom_after[port] = cap * (rail - voltage[a])
        change = bottom_after - bottom_before
        charges.append([change[mask].sum(), change[~mask].sum()])
        nodes.append(voltage)
        bottom_before = bottom_after
    return np.array(nodes), np.array(charges), bottom_before, matrix


def test_reference_charge_matches_independent_branch_equations(reference_case):
    expected_nodes, expected_charge, expected_bottom, matrix = _branch_oracle(reference_case)
    actual = physical_reference_program(**reference_case)
    np.testing.assert_allclose(actual.floating_node_voltage_v, expected_nodes, atol=2e-15, rtol=0)
    np.testing.assert_allclose(actual.charge_c, expected_charge, atol=8e-27, rtol=0)
    np.testing.assert_allclose(actual.bottom_charge_c, expected_bottom, atol=8e-27, rtol=0)
    assert actual.normalization_capacitance_f == pytest.approx(
        1 / np.linalg.solve(matrix, [1.0, 0.0, 0.0])[0], rel=3e-16
    )
    assert actual.node_names == (
        "shared_floating_top",
        "slice_3_floating_sub",
        "slice_14_floating_sub",
    )


def test_repeated_mask_preserves_event_and_has_zero_new_load(reference_case):
    actual = physical_reference_program(**reference_case)
    np.testing.assert_array_equal(actual.times_s, reference_case["times_s"])
    np.testing.assert_array_equal(actual.charge_c[-1], [0.0, 0.0])
    assert len(actual.times_s) == 4
    assert actual.times_s[-1] == actual.times_s[-2]


def test_unchanged_rail_connection_still_draws_charge_when_nodes_move(reference_case):
    case = {
        **reference_case,
        "main_on": reference_case["main_on"][:2],
        "sub_on": reference_case["sub_on"][:2],
        "times_s": [0.0, 1e-9],
    }
    actual = physical_reference_program(**case)
    first = actual.floating_node_voltage_v[0]
    second = actual.floating_node_voltage_v[1]
    assert case["main_on"][0, 0, 0] and case["main_on"][1, 0, 0]
    unchanged_port_change = case["main_caps_f"][0, 0] * (first[0] - second[0])
    assert abs(unchanged_port_change) > 1e-14
    _, expected, _, _ = _branch_oracle(case)
    np.testing.assert_allclose(actual.charge_c, expected, atol=8e-27, rtol=0)
    assert actual.charge_c[1, 1] < 0


def test_actual_sample_charge_and_explicit_rail_values_affect_load(reference_case):
    before = physical_reference_program(**reference_case)
    altered_case = {
        **reference_case,
        "sampled_main_bottom_v": reference_case["sampled_main_bottom_v"] + 0.2,
    }
    changed = physical_reference_program(**altered_case)
    expected_change = _branch_oracle(altered_case)[1] - _branch_oracle(reference_case)[1]
    np.testing.assert_allclose(
        changed.charge_c - before.charge_c, expected_change, atol=8e-27, rtol=0
    )
    scale = sum(reference_case["main_caps_f"].flat) * abs(reference_case["ref_high_v"])
    assert np.max(abs(expected_change)) > 100 * np.finfo(float).eps * scale
    rails = physical_reference_program(**{**reference_case, "ref_high_v": 2.7})
    assert np.max(abs(rails.charge_c - before.charge_c)) > 1e-13


def test_sensitivity_is_floating_top_derivative_and_ground_parasitics_remain(reference_case):
    actual = physical_reference_program(**reference_case)
    # 该网络严格线性；用可分辨的 1/8 V 对照，避免微小差分的相消误差。
    epsilon = 0.125
    for column, name in enumerate(("ref_high_v", "ref_low_v")):
        plus = physical_reference_program(
            **{**reference_case, name: reference_case[name] + epsilon}
        )
        minus = physical_reference_program(
            **{**reference_case, name: reference_case[name] - epsilon}
        )
        numerical = (plus.floating_node_voltage_v[:, 0] - minus.floating_node_voltage_v[:, 0]) / (
            2 * epsilon
        )
        np.testing.assert_allclose(actual.sensitivity[:, column], numerical, atol=2e-12, rtol=0)
    assert np.all(actual.sensitivity.sum(axis=1) < 1)
    assert np.all(actual.sensitivity >= 0)


def test_result_is_owned_readonly_data(reference_case):
    actual = physical_reference_program(**reference_case)
    reference_case["times_s"][0] = 8.0
    reference_case["slice_ids"][0] = 17
    assert actual.times_s[0] == 0
    assert actual.slice_ids[0] == 3
    for array in (
        actual.times_s,
        actual.charge_c,
        actual.sensitivity,
        actual.bottom_charge_c,
        actual.floating_node_voltage_v,
        actual.slice_ids,
    ):
        assert not array.flags.writeable
        with pytest.raises(ValueError):
            array.flat[0] = 0
    with pytest.raises(FrozenInstanceError):
        actual.node_names = ()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("slice_ids", [3, 3]),
        ("slice_ids", [3.0, 14.0]),
        ("slice_ids", [-1, 14]),
        ("main_caps_f", [[0.0, 1e-12], [1e-12, 1e-12]]),
        ("sub_caps_f", [[float("nan")], [1e-12]]),
        ("bridge_caps_f", [0.0, 1e-12]),
        ("main_parasitic_f", [-1e-12, 0.0]),
        ("sub_parasitic_f", [0.0, float("inf")]),
        ("main_on", np.ones((4, 2, 2), dtype=int)),
        ("sub_on", np.ones((4, 2, 2), dtype=bool)),
        ("times_s", [-1e-9, 0.0, 1e-9, 2e-9]),
        ("times_s", [0.0, 2e-9, 1e-9, 3e-9]),
        ("sampled_main_bottom_v", None),
        ("sampled_top_v", None),
        ("sampled_subnode_v", [0.0]),
        ("ref_high_v", None),
        ("ref_low_v", None),
    ],
)
def test_illegal_or_missing_physical_input_is_rejected(reference_case, field, value):
    with pytest.raises(ValueError):
        physical_reference_program(**{**reference_case, field: value})


def test_sampling_connection_voltages_use_only_actual_consumed_nodes():
    main = np.array([[0, 1, 2], [2, 0, 1]])
    sub = np.array([[1], [2]])
    m, s = sampled_bottom_voltages(main, sub, [0.2, -0.4], ref_high_v=3.1, ref_low_v=-2.8)
    np.testing.assert_array_equal(m, [[0.2, -2.8, 3.1], [3.1, -0.4, -2.8]])
    np.testing.assert_array_equal(s, [[-2.8], [3.1]])
    only_high = np.full((2, 1), 2, dtype=int)
    high, _ = sampled_bottom_voltages(only_high, only_high, None, ref_high_v=3.1, ref_low_v=None)
    np.testing.assert_array_equal(high, np.full((2, 1), 3.1))
    assert not m.flags.writeable and not s.flags.writeable


@pytest.mark.parametrize("bad", [np.array([[3]]), np.array([[0.0]]), np.array([0])])
def test_sampling_connection_domain_is_explicit(bad):
    with pytest.raises(ValueError):
        sampled_bottom_voltages(bad, np.zeros((1, 1), int), 0.0, ref_high_v=3, ref_low_v=-3)
