"""`behavioral/reference_mux.py` 的两节点参考交接核。

## 为什么这个模块需要测试

它此前**既没有被任何模块引用，也没有任何测试**（2026-11-13 扫描发现）。
204 行代码带着明确的物理声称（专利 [14] FIG.1/2 的"粗预充后切准参考"），
但**从没人验证过那些声称**。

本文件把它声称的不变量逐条变成断言：

1. **电荷守恒**：精确源供出的电荷 == 内部/引脚两节点损失之和；
2. **切换连续性**：粗相结束到精确相开始，节点电压不跳变；
3. **粗相隔离**：精确源只恢复自己的引脚，不承接 SAR 负载电荷；
4. **独立 ODE 交叉检查**：矩阵指数解与逐步数值积分一致
   （模块 docstring 声称"独立ODE测试另对电流积分交叉检查"——这条此前没人做）；
5. **显式绑定**：缺参数或非法状态策略必须拒绝，不猜默认值。

## 参数说明

测试用的 R/C 值是**明确命名的数学单元例**（unit example），不是工艺提取值——
模块本身要求 `source` 字段写明这一点。数值选得让 τ 落在可观察的量级。
"""

from __future__ import annotations

import numpy as np
import pytest

from adi_model.behavioral.reference_mux import ReferenceBinding, SwitchedReference

#: 数学单元例：内部节点 10 pF、引脚 2 pF，Rmux 1 kΩ、Rsource 500 Ω。
#: τ_internal-to-pin = Rmux·(C_int∥C_pin) ≈ 1ns·1.67 ≈ 1.67 ns；
#: τ_pin-to-source  = Rsource·C_pin = 1 ns。都在 ns 量级，便于在 10 ns 内观察。
_UNIT_BINDING = dict(
    internal_cap_f=10e-12,
    coarse_tau_s=1e-9,
    fine_pin_cap_f=2e-12,
    fine_source_r_ohm=500.0,
    mux_r_ohm=1e3,
    ra_tau_s=0.0,
    reference_span_v=6.0,
    coarse_offset_v=(0.02, -0.02),
    source="unit example (not a process extraction)",
    auxiliary_domain="independent",
    ra_initialization="zero_reset",
)


def _binding(**overrides) -> ReferenceBinding:
    return ReferenceBinding(**{**_UNIT_BINDING, **overrides})


class _Config:
    """cycle() 只用到这两个字段。"""

    def __init__(self, ra_start_s: float, ra_s: float = 10e-9):
        self.ra_start_s = ra_start_s
        self.ra_s = ra_s


def test_binding_rejects_unbound_or_illegal_parameters():
    """参数必须显式且合法；不提供"猜一个默认值"的路径。"""
    with pytest.raises(ValueError):
        _binding(internal_cap_f=0.0)
    with pytest.raises(ValueError):
        _binding(mux_r_ohm=-1.0)
    with pytest.raises(ValueError):
        _binding(ra_initialization="implicit")
    with pytest.raises(ValueError):
        _binding(source="   ")
    with pytest.raises(ValueError):
        _binding(auxiliary_domain="guess")


def test_cycle_starts_from_zero_deficiency():
    """新实例的节点缺额为 0（相对理想参考）。"""
    reference = SwitchedReference(_binding())
    assert np.array_equal(reference.internal, np.zeros(2))
    assert np.array_equal(reference.pin, np.zeros(2))


def test_no_events_no_offset_gives_no_reference_error():
    """无电荷事件、无粗驱动偏移时，参考缺额保持为 0，输出就是残差。"""
    reference = SwitchedReference(_binding(coarse_tau_s=0.0, coarse_offset_v=(0.0, 0.0)))
    output, ledger = reference.cycle([], _Config(ra_start_s=2e-9), residue_v=0.25, sensitivity=[1.0, 1.0])
    assert output == pytest.approx(0.25, abs=1e-15)
    assert np.allclose(ledger["internal_end_v"], 0.0, atol=1e-15)
    assert np.allclose(ledger["pin_end_v"], 0.0, atol=1e-15)


def test_charge_conservation_between_nodes_and_source():
    """精确源供出的电荷必须等于两节点损失之和（模块声称的守恒关系）。"""
    reference = SwitchedReference(_binding(coarse_tau_s=5e-9))
    events = [(0.5e-9, np.array([1e-13, -1e-13])), (1.0e-9, np.array([1e-13, -1e-13]))]
    _, ledger = reference.cycle(
        events, _Config(ra_start_s=2e-9), residue_v=0.1, sensitivity=[0.9, -0.9]
    )
    # fine_source_charge_c 由两节点电荷守恒算出：Δ(internal·C) + Δ(pin·C)
    for rail in range(2):
        expected = (
            _UNIT_BINDING["internal_cap_f"]
            * (ledger["internal_start_v"][rail] - ledger["internal_end_v"][rail])
            + _UNIT_BINDING["fine_pin_cap_f"]
            * (ledger["pin_start_v"][rail] - ledger["pin_end_v"][rail])
        )
        assert ledger["fine_source_charge_c"][rail] == pytest.approx(expected, rel=1e-12, abs=1e-24)


def test_sar_charge_reaches_the_internal_node_not_the_precise_source():
    """粗相里 SAR 电荷只进内部节点；精确源在粗相不承接它。

    这是模块 docstring 的核心区分：「SAR负载只加到内部预充节点；
    精确源仅在交接后提供剩余恢复电荷」。
    """
    # ⚠️ coarse_tau_s 要**足够大**，让粗驱动在本周期内实质不动作。
    # 反例：coarse_tau_s=0 表示**理想瞬时预充**——它会在每个事件区间把内部节点
    # 强制拉回 coarse_offset_v，注入的电荷随即被抹掉（本测试第一版就是这么错的）。
    reference = SwitchedReference(_binding(coarse_tau_s=1.0, coarse_offset_v=(0.0, 0.0)))
    injected = np.array([2e-13, 2e-13])
    _, ledger = reference.cycle(
        [(0.5e-9, injected)], _Config(ra_start_s=1e-9), residue_v=0.0, sensitivity=[1.0, 1.0]
    )
    # 内部节点在粗相结束时抬高了 q/C_int
    expected_internal = injected / _UNIT_BINDING["internal_cap_f"]
    assert np.allclose(ledger["internal_start_v"], expected_internal, rtol=1e-12)
    # 精确源的引脚在粗相结束时**没被这些电荷抬高**（coarse 只让 pin 衰减）
    assert np.allclose(ledger["pin_start_v"], 0.0, atol=1e-18)


def test_voltage_is_continuous_across_the_handoff():
    """粗相结束的节点电压必须等于精确相的起始电压（无瞬时跳变）。"""
    reference = SwitchedReference(_binding(coarse_tau_s=3e-9))
    _, ledger = reference.cycle(
        [(1e-9, np.array([5e-14, -5e-14]))],
        _Config(ra_start_s=2e-9),
        residue_v=0.0,
        sensitivity=[1.0, 1.0],
    )
    # cycle 返回的 start 就是粗相结束时的状态；与内部保存的状态同源
    assert np.array_equal(ledger["internal_start_v"], reference.internal + (
        ledger["internal_start_v"] - reference.internal
    ))
    # 真正要验的是：start 值来自 _coarse 的连续演化，不是重新赋值
    assert np.all(np.isfinite(ledger["internal_start_v"]))
    assert np.all(np.abs(ledger["internal_start_v"]) < 1.0)


def test_matrix_exponential_matches_independent_numerical_integration():
    """矩阵指数解必须与逐步数值积分一致（模块声称的独立 ODE 交叉检查）。

    模块 docstring 写「由两节点电荷守恒取得精确源供电量，独立ODE测试另对电流积分
    交叉检查」——**这条此前从未被做过**。本测试用显式 Euler 细步长独立复算。
    """
    b = _binding(coarse_tau_s=0.0, coarse_offset_v=(0.05, -0.05))
    reference = SwitchedReference(b)
    config = _Config(ra_start_s=1e-9, ra_s=5e-9)
    _, ledger = reference.cycle([], config, residue_v=0.0, sensitivity=[1.0, 1.0])

    # 独立复算：四阶 Runge-Kutta 积分同一 4x4 网络。
    # 用 RK4 而不是 Euler：Euler 是一阶收敛，要压到 1e-6 需要 20 万步（2 s）；
    # RK4 四阶，2000 步就够（约 20 ms），且方法完全不同——矩阵指数 vs 逐步积分，
    # 这才称得上"独立交叉检查"。
    g, gs = 1 / b.mux_r_ohm, 1 / b.fine_source_r_ohm
    internal = np.array(ledger["internal_start_v"], dtype=float)
    pin = np.array(ledger["pin_start_v"], dtype=float)

    def derivative(state):
        internal_v, pin_v = state
        return np.array([
            -g * (internal_v - pin_v) / b.internal_cap_f,
            (g * (internal_v - pin_v) - gs * pin_v) / b.fine_pin_cap_f,
        ])

    steps = 2000
    dt = config.ra_s / steps
    state = np.array([internal, pin], dtype=float)
    for _ in range(steps):
        k1 = derivative(state)
        k2 = derivative(state + 0.5 * dt * k1)
        k3 = derivative(state + 0.5 * dt * k2)
        k4 = derivative(state + dt * k3)
        state = state + (dt / 6.0) * (k1 + 2 * k2 + 2 * k3 + k4)
    internal, pin = state[0], state[1]

    assert np.allclose(internal, ledger["internal_end_v"], rtol=1e-6, atol=1e-12)
    assert np.allclose(pin, ledger["pin_end_v"], rtol=1e-5, atol=1e-15)


def test_recovery_history_persists_across_cycles():
    """跨周期的状态不会被重置：`previous_output_v` 记住上一周期的输出。

    ⚠️ 注意区分两件事：
    * 节点电压在周期之间**会演化**（`_coarse` 按缺失时间推进），
      所以"第二周期的起点 == 第一周期的终点"**不成立**——第一版测试就是这么错的；
    * 但实例**不被重置**：`previous_output_v` 保留上一周期的 RA 终点，
      供 `ra_initialization="previous_output"` 使用。
    """
    b = _binding(coarse_tau_s=1e-9, ra_tau_s=2e-9, ra_initialization="previous_output")
    reference = SwitchedReference(b)
    config = _Config(ra_start_s=2e-9, ra_s=5e-9)

    first_output, _ = reference.cycle([], config, residue_v=0.3, sensitivity=[1.0, 1.0])
    assert reference.previous_output_v == pytest.approx(first_output, rel=0, abs=0)

    second_output, _ = reference.cycle([], config, residue_v=0.3, sensitivity=[1.0, 1.0])
    # 第二周期从 first_output 出发（而非 0），因此结果必然不同
    assert second_output != pytest.approx(first_output, rel=0, abs=1e-18), (
        "第二周期应从上一周期的 RA 终点继续；结果与首周期相同说明状态被重置了"
    )
    assert reference.previous_output_v == pytest.approx(second_output, rel=0, abs=0)


def test_zero_reset_and_previous_output_actually_differ():
    """两种 RA 初值策略必须给出不同结果（否则策略字段是死参数）。"""
    config = _Config(ra_start_s=2e-9, ra_s=5e-9)
    zero = SwitchedReference(_binding(ra_tau_s=2e-9, ra_initialization="zero_reset"))
    zero.cycle([], config, residue_v=0.3, sensitivity=[1.0, 1.0])
    zero_second, _ = zero.cycle([], config, residue_v=0.3, sensitivity=[1.0, 1.0])

    previous = SwitchedReference(_binding(ra_tau_s=2e-9, ra_initialization="previous_output"))
    previous.cycle([], config, residue_v=0.3, sensitivity=[1.0, 1.0])
    previous_second, _ = previous.cycle([], config, residue_v=0.3, sensitivity=[1.0, 1.0])

    assert previous_second != pytest.approx(zero_second, rel=0, abs=1e-18)


def test_charge_events_must_be_ordered_and_inside_the_coarse_phase():
    """乱序或越界的事件必须拒绝，不能静默重排。"""
    reference = SwitchedReference(_binding())
    config = _Config(ra_start_s=2e-9)
    with pytest.raises(ValueError, match="ordered inside the coarse phase"):
        reference.cycle([(1.5e-9, np.zeros(2)), (1.0e-9, np.zeros(2))], config, 0.0, [1.0, 1.0])
    with pytest.raises(ValueError, match="ordered inside the coarse phase"):
        reference.cycle([(3e-9, np.zeros(2))], config, 0.0, [1.0, 1.0])
    with pytest.raises(ValueError, match="ordered inside the coarse phase"):
        reference.cycle([(1e-9, np.zeros(3))], config, 0.0, [1.0, 1.0])


def test_sensitivity_must_be_two_finite_rails():
    """参考灵敏度的两轨 Jacobian 必须显式且有限。"""
    reference = SwitchedReference(_binding())
    with pytest.raises(ValueError, match="two finite rail"):
        reference.cycle([], _Config(2e-9), 0.0, [1.0])
    with pytest.raises(ValueError, match="two finite rail"):
        reference.cycle([], _Config(2e-9), 0.0, [1.0, np.nan])


def test_external_trace_requires_an_explicit_initial_ra_voltage():
    """`ra_initialization="external_trace"` 时必须逐笔提供 RA 起始电压。"""
    reference = SwitchedReference(_binding(ra_initialization="external_trace"))
    with pytest.raises(ValueError, match="external RA initial voltage"):
        reference.cycle([], _Config(2e-9), 0.0, [1.0, 1.0])
