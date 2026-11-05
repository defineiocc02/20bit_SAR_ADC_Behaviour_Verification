"""共享输入源的显式RC状态与电荷守恒，不替代专利AUX/自举电路。

所有支路共享同一个源阻抗，不能把它除以slice数。滤波电容若存在，其bus电压作为末状态保留；
源阻抗为零时电压由源给定，但初始电荷仍显式记账。模态积分只解决给定拓扑的数值方程，
不证明器件带宽或噪声。

目标RDAC的完整采样周期由主引擎按1/fs绑定；本kernel只积分调用方明确给出的[t0,t1]。
它不生成0.45T采样窗、预跟踪空闲相或AUX输入副本。独立AUX必须有自己的实际波形、源阻抗和支路参数，
不能把理想源称为Cboost。
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from numbers import Real
from typing import cast

import numpy as np

from ._arrays import readonly


def _finite_scalar(value) -> bool:
    # cast 补全 NumPy 的标量存根类型，ufunc 仍接收原对象。
    return (
        isinstance(value, Real)
        and not isinstance(value, bool | np.bool_)
        and bool(np.isfinite(cast(float, value)))
    )


@dataclass(frozen=True)
class InputNetworkParameters:
    """可不完整的工程网络约定；启用消费者前要求相应电参。

    Attributes:
        filter_cap_f: 共享输入 bus 电容 [F]；显式零表示移除该电容。
        integration_steps: 非线性 Ron 或任意波形的积分子区间数；线性 DC/正弦输入精确求解，
            只需一个区间。
        auxiliary_cap_f: 活跃时钟寄生支路的聚合输入电容 [F]。
        auxiliary_bypass: True 表示将该寄生支路连接到独立输入源路径。
        auxiliary_source_ohm: 独立 AUX 源阻抗 [ohm]，不默认为理想源。
        auxiliary_resistance_ohm: 寄生支路电阻 [ohm]。
        pretrack_mode: off、latest 或 weighted，不保留理想自身历史替代路径。
        pretrack_time_s: 主采集前的预跟踪时长 [s]。
        pretrack_source_ohm: 独立预充驱动器电阻 [ohm]。
        pretrack_weights: 显式最新到更早数字权重，不自动采用原文示例。
    """

    filter_cap_f: float | None = None
    integration_steps: int = 8
    auxiliary_cap_f: float | None = None
    auxiliary_bypass: bool = False
    auxiliary_source_ohm: float | None = None
    auxiliary_resistance_ohm: float | None = None
    pretrack_mode: str = "off"
    pretrack_time_s: float | None = None
    pretrack_source_ohm: float | None = None
    pretrack_weights: tuple[float, ...] | None = None

    def violations(self) -> list[str]:
        """返回非法物理或数值设置，不修改参数对象。"""
        bad = []
        for name, unit, nonnegative in (
            ("filter_cap_f", "F", True),
            ("auxiliary_cap_f", "F", True),
            ("auxiliary_source_ohm", "ohm", True),
            ("auxiliary_resistance_ohm", "ohm", False),
            ("pretrack_time_s", "s", False),
            ("pretrack_source_ohm", "ohm", False),
        ):
            value = getattr(self, name)
            if value is not None and (
                not isinstance(value, Real)
                or isinstance(value, bool | np.bool_)
                or not np.isfinite(cast(float, value))
                or (value < 0 if nonnegative else value <= 0)
            ):
                bad.append(
                    f"input_network.{name} [{unit}] must be finite and "
                    + ("nonnegative" if nonnegative else "positive")
                )
        if (
            isinstance(self.integration_steps, bool)
            or not isinstance(self.integration_steps, int)
            or not 1 <= self.integration_steps <= 4096
        ):
            bad.append("input_network.integration_steps must be an integer in [1, 4096]")
        if type(self.auxiliary_bypass) is not bool:
            bad.append("input_network.auxiliary_bypass must be a boolean")
        if self.pretrack_mode not in ("off", "latest", "weighted"):
            bad.append("input_network.pretrack_mode is not an implemented policy")
        if self.pretrack_mode != "off":
            for name, unit in (("pretrack_time_s", "s"), ("pretrack_source_ohm", "ohm")):
                if getattr(self, name) is None:
                    bad.append(f"input_network.{name} [{unit}] is required by pretracking")
        if self.pretrack_weights is not None:
            weights = np.asarray(self.pretrack_weights)
            if (
                weights.ndim != 1
                or weights.size < 2
                or not np.issubdtype(weights.dtype, np.number)
                or np.iscomplexobj(weights)
                or np.any(~np.isfinite(weights))
                or np.any(weights < 0)
                or weights[0] <= 0
                or not np.isfinite(weights.sum())
                or weights.sum() <= 0
            ):
                bad.append("input_network.pretrack_weights requires finite nonnegative weights")
        elif self.pretrack_mode == "weighted":
            bad.append(
                "input_network.pretrack_weights [unitless] is required by weighted pretracking"
            )
        auxiliary_active = isinstance(self.auxiliary_cap_f, Real) and self.auxiliary_cap_f > 0
        if auxiliary_active and self.auxiliary_resistance_ohm is None:
            bad.append(
                "input_network.auxiliary_resistance_ohm [ohm] is required by auxiliary branch"
            )
        if self.auxiliary_bypass:
            if not auxiliary_active:
                bad.append("input_network.auxiliary_cap_f [F] must select a real auxiliary branch")
            if self.auxiliary_source_ohm is None:
                bad.append(
                    "input_network.auxiliary_source_ohm [ohm] is required by independent AUX"
                )
        return bad

    def require_tracking(self) -> None:
        """跟踪实际运行前要求明确bus电容；0是主动选择无该电容。"""
        bad = self.violations()
        if self.filter_cap_f is None:
            bad.append("input_network.filter_cap_f [F] is required by track_interval")
        if bad:
            raise ValueError("; ".join(bad))

    def require_auxiliary_source(self, auxiliary_input_fn) -> None:
        """旁路启用前核对独立AUX输入；不自动复制主输入波形。"""
        bad = self.violations()
        if not self.auxiliary_bypass:
            bad.append("independent AUX requires explicit auxiliary_bypass=True")
        if not callable(auxiliary_input_fn):
            bad.append("auxiliary_input_fn [AUX-node V versus s] is required by independent AUX")
        if bad:
            raise ValueError("; ".join(bad))


class OffsetWaveform:
    """在连续输入源上叠加区间内恒定的模拟注入 [V]。"""

    def __init__(self, source, offset_v: float):
        """原输入源带精确谐波描述时保留该描述。"""
        self.source = source
        self.offset_v = offset_v
        terms = getattr(source, "harmonics", None)
        self.harmonics = (*terms, (0.0, complex(offset_v))) if terms is not None else None

    def __call__(self, t):
        """返回时刻 t [s] 的源电压与保持注入之和。"""
        return self.source(t) + self.offset_v


class PassiveTrackingNetwork:
    """共享源线性 RC 网络；每个状态为一个电容电压。

    状态数组先放输入支路；存在滤波电容时，其电压为末状态。求解器不保留可变电气状态。
    """

    def __init__(self, caps_f, switch_ohm, source_ohm: float, filter_cap_f: float):
        """由显式支路 C/R 与共享 R 构造正的无源网络。

        Args:
            caps_f: 一维正支路电容 [F]。
            switch_ohm: 同形正支路电阻 [ohm]。
            source_ohm: 非负共享源电阻 [ohm]。
            filter_cap_f: 非负 bus 电容 [F]；源电阻为零时，bus 电压由理想源指定。

        Raises:
            ValueError: 电路数值的形状、符号或有限性非法。
        """
        if np.iscomplexobj(caps_f) or np.iscomplexobj(switch_ohm):
            raise ValueError("tracking capacitances/resistances must be real [F/ohm]")
        caps = np.asarray(caps_f, dtype=float)
        ron = np.asarray(switch_ohm, dtype=float)
        if (
            caps.ndim != 1
            or caps.size == 0
            or ron.shape != caps.shape
            or np.any(~np.isfinite(caps))
            or np.any(caps <= 0)
            or np.any(~np.isfinite(ron))
            or np.any(ron <= 0)
            or not _finite_scalar(source_ohm)
            or source_ohm < 0
            or not _finite_scalar(filter_cap_f)
            or filter_cap_f < 0
        ):
            raise ValueError("tracking network requires finite positive C/R and nonnegative Rs/Cf")
        self.has_bus_state = bool(filter_cap_f > 0)
        self._prescribed_bus = bool(filter_cap_f > 0 and source_ohm == 0)
        g = 1.0 / ron
        if source_ohm == 0:
            conductance = np.diag(g)
        elif self.has_bus_state:
            conductance = np.diag(np.r_[g, g.sum() + 1.0 / source_ohm])
            conductance[:-1, -1] = -g
            conductance[-1, :-1] = -g
            caps = np.r_[caps, filter_cap_f]
        else:
            conductance = np.diag(g) - np.outer(g, g) / (g.sum() + 1.0 / source_ohm)
        self.caps_f = readonly(np.r_[caps, filter_cap_f] if self._prescribed_bus else caps)
        self._sqrt_c = np.sqrt(caps)
        normalized = conductance / self._sqrt_c[:, None] / self._sqrt_c[None, :]
        self.rates, self._u = np.linalg.eigh(normalized)
        if np.any(self.rates <= 0):
            raise ValueError("RC network is numerically singular; rescale its component values")
        self._drive = self._u.T @ self._sqrt_c

    def advance(self, previous, t0: float, t1: float, input_fn) -> np.ndarray:
        """推进连续激励区间内的电容电压。

        Args:
            previous: 按声明状态顺序给出的电容电压 [V]。
            t0: 区间起点 [s]。
            t1: 区间终点 [s]，严格晚于 t0。
            input_fn: 电压函数；harmonics 可提供实相量 (frequency_hz, complex_phasor_v)。
                普通函数在当前区间内按端点线性插值。

        Returns:
            新电容电压 [V]，不修改输入或原状态。

        Raises:
            ValueError: 状态、时间非有限，或区间非法。
        """
        if np.iscomplexobj(previous):
            raise ValueError("tracking capacitor voltages must be real [V]")
        previous = np.asarray(previous, dtype=float)
        if not _finite_scalar(t0) or not _finite_scalar(t1):
            raise ValueError("tracking interval endpoints must be explicit finite times [s]")
        dt = t1 - t0
        if (
            previous.shape != self.caps_f.shape
            or np.any(~np.isfinite(previous))
            or not np.isfinite(t0)
            or not np.isfinite(t1)
            or dt <= 0
        ):
            raise ValueError(
                "state must match network and time interval must be finite and positive"
            )
        decay = np.exp(-self.rates * dt)
        dynamic_previous = previous[:-1] if self._prescribed_bus else previous
        modal = self._u.T @ (self._sqrt_c * dynamic_previous)
        harmonics = getattr(input_fn, "harmonics", None)
        if harmonics is not None:
            forced = np.zeros_like(modal)
            for frequency, phasor in harmonics:
                omega = 2 * np.pi * frequency
                h = self.rates / (self.rates + 1j * omega)
                forced += np.real(
                    h * phasor * (np.exp(1j * omega * t1) - decay * np.exp(1j * omega * t0))
                )
        else:
            u0, u1 = float(input_fn(t0)), float(input_fn(t1))
            if not np.isfinite(u0) or not np.isfinite(u1):
                raise ValueError("input waveform must be finite")
            # 稳定计算一阶保持积分，涵盖 dt << tau；很小 z 时用展开式避免
            # 1 - (1-exp(-z))/z 的相减精度损失。
            z = self.rates * dt
            one_minus_decay = -np.expm1(-z)
            slope_weight = np.where(z < 1e-5, z / 2 - z**2 / 6 + z**3 / 24, 1 - one_minus_decay / z)
            forced = one_minus_decay * u0 + slope_weight * (u1 - u0)
        modal = decay * modal + self._drive * forced
        result = (self._u @ modal) / self._sqrt_c
        if self._prescribed_bus:
            final_bus = float(input_fn(t1))
            if not np.isfinite(final_bus):
                raise ValueError("prescribed input bus must be finite [V]")
            result = np.r_[result, final_bus]
        if np.any(~np.isfinite(result)):
            raise ValueError("tracking response produced a nonfinite capacitor voltage [V]")
        return result

    def charge_change(self, before, after) -> float:
        """按电容电荷守恒返回源提供的电荷 [C]。"""
        before, after = np.asarray(before), np.asarray(after)
        if (
            before.shape != self.caps_f.shape
            or after.shape != before.shape
            or np.iscomplexobj(before)
            or np.iscomplexobj(after)
            or not np.issubdtype(before.dtype, np.number)
            or not np.issubdtype(after.dtype, np.number)
            or np.any(~np.isfinite(before))
            or np.any(~np.isfinite(after))
        ):
            raise ValueError(
                "source charge requires finite capacitor voltages in the explicit state order"
            )
        return float(np.dot(self.caps_f, after - before))


@lru_cache(maxsize=256)
def _linear_network(caps: tuple, ron: tuple, source: float, filter_cap: float):
    # 缓存键仅含不可变元件 tuple；求解器不保存电气状态，原节点电压由每次调用提供。
    return PassiveTrackingNetwork(caps, ron, source, filter_cap)


def track_interval(
    caps_f,
    ron_ohm,
    source_ohm: float,
    previous,
    t0: float,
    t1: float,
    input_fn,
    *,
    parameters: InputNetworkParameters,
    ron_coefficient: float = 0.0,
    voltage_scale: float = 1.0,
) -> tuple[np.ndarray, float]:
    """积分一次采集，返回状态 [V] 与源电荷 [C]。

    Ron = Ron0 * (1 + ron_coefficient * (Vin/voltage_scale)**2)。对非线性 Ron，
    应将 integration_steps 加倍作比较后，再据此讨论精度。存在滤波 bus 时，
    其状态位于 previous 的末尾。
    """
    parameters.require_tracking()
    if (
        not _finite_scalar(t0)
        or not _finite_scalar(t1)
        or t1 <= t0
        or not _finite_scalar(ron_coefficient)
        or ron_coefficient < 0
        or (ron_coefficient != 0 and (not _finite_scalar(voltage_scale) or voltage_scale <= 0))
        or not callable(input_fn)
    ):
        raise ValueError(
            "track_interval requires explicit time endpoints, source callable and legal Ron law"
        )
    steps = parameters.integration_steps
    harmonics = getattr(input_fn, "harmonics", None)
    if harmonics is not None and (ron_coefficient == 0 or all(f == 0 for f, _ in harmonics)):
        steps = 1
    state = np.asarray(previous, dtype=float).copy()
    initial = state.copy()
    network = None
    for j in range(steps):
        a, b = t0 + (t1 - t0) * j / steps, t0 + (t1 - t0) * (j + 1) / steps
        if network is None or ron_coefficient != 0:
            factor = 1 + ron_coefficient * (float(input_fn((a + b) / 2)) / voltage_scale) ** 2
            network = _linear_network(
                tuple(caps_f),
                tuple(np.asarray(ron_ohm) * factor),
                source_ohm,
                parameters.filter_cap_f,
            )
        state = network.advance(state, a, b, input_fn)
    assert network is not None
    return state, network.charge_change(initial, state)
