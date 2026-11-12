"""RDAC follower 的因果逐事件语义与参考开关代价。

论文原文（ISSCC 2024 §9.8 第 2 页）："The RDAC works as a follower of the quantizer
with the conversion results loaded as they develop." 本模块把这句话变成可检查的合同，
而不是一句注释：

* 第 e 个事件施加到池上的整数码，只能由第 0..e 个**已决**前缀决定；
  不得读取最终码，也不得使用尚未产生的比较结果。
* 检查方式不是"读一遍代码确认"，而是**前缀截断不变性**：
  把第 e 事件之后所有未决位分别补成全 0 与全 1，事件 e 的实际施加码必须不变。
  一个偷看最终码的实现会在这两个补全下给出不同结果，从而被判定 `lookahead>0`。
* 采集态（全部底板接 Signal）是第 -1 个事件；参考电荷从 0 起算。

参考开关代价只做电荷记账（`|dQ|` 是参考轨实际付出的电荷），三点必须分清：

* follower（已决结果逐事件加载）与"最终码一次加载"的**最终电荷完全相同**。二者只差因果性：
  后者要求转换开始前就知道最终码，物理上不可实现，静态实验里却常被当成等价替代。
* 传统单 DAC SAR 每比特先试探再撤销（置位→比较→可能清零），同一最终码的 `|dQ|` 明显更大。
  follower 省参考电流的来源是**取消了试探态**（论文："the DAC is always set directly to the
  right state"），不是把事件拆细。
* 若把试探态也记进 trace，事件数与电荷都会翻倍；本模块用 `run_trial_sar` 显式给出这个对照，
  而不是靠文字断言。

本模块不含：比较器时序/亚稳态、逐事件 RC 建立、参考 droop、开关非线性电容。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np

from ._arrays import readonly
from .config import ConfigError
from .dem import SplitSwitchCommand, _integer_array
from .dither import complete_flash3_sar6_prefix_table

# 当前 core 的已决事件数：3b flash + 6b SAR = 7 次已决前缀消费（见 complete_flash3_sar6_prefix_table）。
FOLLOWER_EVENTS = 7

EventCodeFn = Callable[[np.ndarray], np.ndarray]


@dataclass(frozen=True)
class ResolvedPrefixes:
    """(N,E) 已决前缀码，行尾必须等于该笔最终粗码。

    Attributes:
        codes: int64 (N,E)，列 e 为第 e 次比较结束后的已决码。
        final: int64 (N,)，等于 codes[:, -1]；单独保存以免调用方各写一遍。

    Side effects: 拷贝并冻结输入；不修改、不推断缺失事件。
    """

    codes: np.ndarray
    final: np.ndarray

    def __post_init__(self):
        """校验事件维、整数语义与行尾一致性，不补造事件。"""
        codes = _integer_array(self.codes, "resolved prefixes", ndim=2)
        if codes.shape[1] != FOLLOWER_EVENTS:
            raise ConfigError(
                f"resolved prefixes must carry exactly {FOLLOWER_EVENTS} decision events"
            )
        if codes.shape[0] == 0:
            raise ConfigError("resolved prefixes must cover at least one sample")
        final = _integer_array(self.final, "final codes", ndim=1)
        if final.shape != (codes.shape[0],):
            raise ConfigError("final codes must match the prefix rows")
        if not np.array_equal(codes[:, -1], final):
            raise ConfigError("the last resolved prefix must equal the final coarse code")
        object.__setattr__(self, "codes", readonly(codes, np.int64))
        object.__setattr__(self, "final", readonly(final, np.int64))

    @classmethod
    def from_final(cls, final, *, table: np.ndarray | None = None) -> ResolvedPrefixes:
        """由最终粗码与明确前缀表生成；不猜前缀，不生成随机事件。

        Args:
            final: (N,) 实际 9 位粗码。
            table: (512,E) 明确前缀表；None 使用当前 core 的 Flash3/SAR6 完整表。

        Returns:
            ResolvedPrefixes，行尾严格等于 final。

        Raises:
            ConfigError: 码域越界或表形状不符。
        """
        codes = _integer_array(final, "final codes", ndim=1)
        if np.any((codes < 0) | (codes > 511)):
            raise ConfigError("final codes must be actual 9-bit resolved codes")
        source = complete_flash3_sar6_prefix_table() if table is None else np.asarray(table)
        if source.shape != (512, FOLLOWER_EVENTS):
            raise ConfigError("prefix table must be (512, 7)")
        return cls(source[codes], codes)

    def truncated(self, event: int, fill: int) -> np.ndarray:
        """返回"只决到 event、其余补 fill"的前缀副本，供因果审计使用。

        Args:
            event: 0..E-1，保留列 0..event。
            fill: 未决列填入的整数码（审计取码域两端）。

        Returns:
            (N,E) int64 新数组；不改写本对象。
        """
        if type(event) is not int or not 0 <= event < self.codes.shape[1]:
            raise ConfigError("event index is outside the resolved event range")
        codes = self.codes.copy()
        if event + 1 < codes.shape[1]:
            codes[:, event + 1 :] = int(fill)
        return codes


@dataclass(frozen=True)
class FollowerTrace:
    """逐事件施加码、掩码与参考电荷记账。

    Attributes:
        applied_codes: (N,E) 实际施加的整数码（不是前缀副本，是经过 event_code_fn 的结果）。
        main_on/sub_on: (N,E,A,n) 与施加码对应的实际物理 mask。
        flips: (N,E) 本事件相对上一事件翻转的底板个数（采集态记为全 0）。
        charge_c: (N,E) 事件末 DAC 参考电荷 Q_D [C]。
        delta_charge_c: (N,E) 相对上一事件的 Q_D 增量 [C]，有符号。
        cumulative_abs_charge_c: (N,E) 累计 |dQ| [C]，即参考轨已付出的电荷。
    """

    applied_codes: np.ndarray
    main_on: np.ndarray
    sub_on: np.ndarray
    flips: np.ndarray
    charge_c: np.ndarray
    delta_charge_c: np.ndarray
    cumulative_abs_charge_c: np.ndarray

    def __post_init__(self):
        """冻结全部数组；不做任何物理推断。"""
        for name in self.__dataclass_fields__:
            value = np.asarray(getattr(self, name))
            object.__setattr__(self, name, readonly(value, value.dtype))

    @property
    def n_events(self) -> int:
        """实际事件数；直接最终态路径为 1。"""
        return int(self.applied_codes.shape[1])

    def total_abs_charge_c(self) -> np.ndarray:
        """每笔参考轨总付出电荷 [C]。"""
        return self.cumulative_abs_charge_c[:, -1]

    def final_charge_c(self) -> np.ndarray:
        """每笔最终稳态 Q_D [C]；follower 与直接最终态必须逐位相同。"""
        return self.charge_c[:, -1]


@dataclass(frozen=True)
class CausalityAudit:
    """前缀截断不变性审计结果。

    Attributes:
        checked: 实际检查的 (样本,事件) 组合数。
        violations: 施加码随未决位补全而改变的事件数；>0 即存在 lookahead。
        first: 首个违约位置 [sample, event]，无违约为 None。
    """

    checked: int
    violations: int
    first: list[int] | None

    @property
    def causal(self) -> bool:
        """只有零违约才允许声明因果。"""
        return self.violations == 0


def _identity_event_codes(prefixes: np.ndarray) -> np.ndarray:
    """生产实现：事件 e 直接用已决前缀 e，不看后续列。"""
    return np.asarray(prefixes)


def run_follower(
    pool,
    prefixes: ResolvedPrefixes,
    state,
    *,
    slice_ids,
    event_code_fn: EventCodeFn | None = None,
    reference_high_v=None,
    reference_low_v=None,
) -> FollowerTrace:
    """按已决前缀逐事件推进 RDAC，并记账每次参考电荷变化。

    Args:
        pool: 显式 PhysicalSlicePool（需要实际电容与 mask 绑定）。
        prefixes: 已决前缀，行尾即最终码。
        state: (N,) DEM 状态；关闭 DEM 时保持同一值。
        slice_ids: (N,A) 每笔实际转换的物理 ID。
        event_code_fn: 事件码生成器；默认恒等。审计时传入被测实现。
        reference_high_v/reference_low_v: 实际轨电压；只在用到该轨时必填。

    Returns:
        FollowerTrace，含逐事件 mask 与电荷记账。

    Raises:
        ConfigError: 维度、码域或 mask 绑定非法。

    Side effects: 不修改 pool 的采集资格、不消费 RNG；只读取电容与 mask 绑定。
    """
    codes = prefixes.codes
    n_samples, n_resolved = codes.shape
    states = _integer_array(state, "DEM states", ndim=1)
    if states.shape != (n_samples,):
        raise ConfigError("DEM states must identify every sample")
    ids = pool.geometry.validate_slice_ids(slice_ids, n_samples=n_samples)
    generator = _identity_event_codes if event_code_fn is None else event_code_fn
    applied = _integer_array(generator(codes.copy()), "event codes", ndim=2)
    if applied.shape[0] != n_samples or applied.shape[1] < 1:
        raise ConfigError(
            "event_code_fn must return at least one event per sample; "
            "collapsing the event sequence has to be an explicit named path"
        )
    n_events = applied.shape[1]
    code_min, code_max = pool.geometry.code_min, pool.geometry.code_max
    if code_min is None or code_max is None:
        raise ConfigError("follower requires an explicit RDAC code domain")
    if np.any((applied < code_min) | (applied > code_max)):
        raise ConfigError(
            "follower produced an RDAC code outside the explicit domain; "
            "no clipping and no event dropping is allowed"
        )

    shape = (n_samples, n_events)
    charge = np.empty(shape)
    delta = np.empty(shape)
    cumulative = np.empty(shape)
    flips = np.empty(shape, dtype=np.int64)
    main_all = np.empty((n_samples, n_events, ids.shape[1], pool.geometry.n_main), dtype=bool)
    sub_all = np.empty((n_samples, n_events, ids.shape[1], pool.geometry.n_sub), dtype=bool)
    # 电荷零点取"全部底板接低轨"（码域下端点）：采样态底板接 Signal，其到低轨的电荷由输入
    # 网络承担，不属于参考轨在转换期付出的电荷。这样 follower 与一次加载的 |dQ| 可直接比。
    baseline_command = pool.select(np.full(n_samples, code_min, dtype=np.int64), states, slice_ids=ids)
    baseline = pool.charge_from_masks(
        ids, baseline_command, reference_high_v=reference_high_v, reference_low_v=reference_low_v
    )
    previous = baseline.charge_c
    previous_mask = (baseline_command.main_on, baseline_command.sub_on)
    for event in range(n_events):
        command = pool.select(applied[:, event], states, slice_ids=ids)
        packet = pool.charge_from_masks(
            ids, command, reference_high_v=reference_high_v, reference_low_v=reference_low_v
        )
        main_all[:, event] = command.main_on
        sub_all[:, event] = command.sub_on
        charge[:, event] = packet.charge_c
        delta[:, event] = charge[:, event] - previous
        cumulative[:, event] = (
            np.abs(delta[:, event]) if event == 0 else cumulative[:, event - 1] + np.abs(delta[:, event])
        )
        flips[:, event] = np.count_nonzero(
            command.main_on != previous_mask[0], axis=(1, 2)
        ) + np.count_nonzero(command.sub_on != previous_mask[1], axis=(1, 2))
        previous = charge[:, event]
        previous_mask = (command.main_on, command.sub_on)
    return FollowerTrace(applied, main_all, sub_all, flips, charge, delta, cumulative)


def run_direct_final(
    pool,
    final,
    state,
    *,
    slice_ids,
    reference_high_v=None,
    reference_low_v=None,
) -> FollowerTrace:
    """把最终码一次加载（单事件）；仅用于对照，不是论文的 follower 行为。

    Returns:
        单事件 FollowerTrace；其最终电荷必须与 run_follower 逐位相同。
    """
    codes = _integer_array(final, "final codes", ndim=1)
    single = ResolvedPrefixes(
        np.repeat(codes[:, None], FOLLOWER_EVENTS, axis=1), codes
    )
    trace = run_follower(
        pool,
        single,
        state,
        slice_ids=slice_ids,
        event_code_fn=lambda values: values[:, -1:],
        reference_high_v=reference_high_v,
        reference_low_v=reference_low_v,
    )
    return trace


def run_trial_sar(
    pool,
    final,
    state,
    *,
    slice_ids,
    bits: int = 9,
    reference_high_v=None,
    reference_low_v=None,
) -> FollowerTrace:
    """传统单 DAC SAR 的试探序列：每比特先置位、比较、必要时清零。

    事件数固定为 2*bits：偶数下标为试探态，奇数下标为判决后的稳定态。
    同一最终码下它的 |dQ| 必然不小于 follower（多出试探-撤销的往返电荷），
    这是论文"RDAC 直接置于正确状态"所消除的那部分参考电流。

    Args:
        final: (N,) 目标 9 位码。
        bits: 决策位数，默认 9（与 SADC 码宽一致）。
        slice_ids/state/reference_*: 同 run_follower。

    Returns:
        FollowerTrace，n_events = 2*bits。

    Raises:
        ConfigError: 码域越界或比特数与池码域不符。
    """
    codes = _integer_array(final, "final codes", ndim=1)
    if type(bits) is not int or not 1 <= bits <= 12:
        raise ConfigError("trial SAR bit count must be an explicit small positive integer")
    if np.any(codes < 0) or np.any(codes >= (1 << bits)):
        raise ConfigError("trial SAR needs final codes inside the declared bit width")
    events = []
    current = np.zeros(codes.size, dtype=np.int64)
    for bit in range(bits - 1, -1, -1):
        weight = 1 << bit
        trial = current | weight
        events.append(trial)
        keep = trial <= codes
        events.append(np.where(keep, trial, current))
        current = np.where(keep, trial, current)
    applied = np.stack(events, axis=1)
    if not np.array_equal(applied[:, -1], codes):
        raise ConfigError("trial sequence did not converge to the requested final code")
    return run_follower(
        pool,
        ResolvedPrefixes(np.repeat(codes[:, None], FOLLOWER_EVENTS, axis=1), codes),
        state,
        slice_ids=slice_ids,
        event_code_fn=lambda _values: applied,
        reference_high_v=reference_high_v,
        reference_low_v=reference_low_v,
    )


def audit_causality(
    prefixes: ResolvedPrefixes,
    event_code_fn: EventCodeFn,
    *,
    code_min: int = 0,
    code_max: int = 511,
) -> CausalityAudit:
    """用前缀截断不变性检查事件码是否偷看未决位。

    对每个事件 e，构造两份只在 e 之后不同的前缀（分别补 code_min 与 code_max），
    要求 event_code_fn 在事件 e 上给出相同结果。生产实现必然通过；
    任何读取 codes[:, -1] 的实现会在两端补全下不同，被判 lookahead。

    Args:
        prefixes: 已决前缀。
        event_code_fn: 被测的事件码生成器。
        code_min/code_max: 补全用的码域端点。

    Returns:
        CausalityAudit；violations 为违约的 (样本,事件) 计数。

    Side effects: 无；不改写 prefixes。
    """
    if not callable(event_code_fn):
        raise ConfigError("causality audit requires a callable event_code_fn")
    n_events = prefixes.codes.shape[1]
    violations = 0
    first = None
    for event in range(n_events):
        low = np.asarray(event_code_fn(prefixes.truncated(event, code_min)))[:, event]
        high = np.asarray(event_code_fn(prefixes.truncated(event, code_max)))[:, event]
        bad = np.flatnonzero(low != high)
        if bad.size:
            violations += int(bad.size)
            if first is None:
                first = [int(bad[0]), event]
    return CausalityAudit(prefixes.codes.size, violations, first)


def switching_cost(trace: FollowerTrace) -> dict:
    """汇总单条 trace 的参考开关代价；只报电荷与翻转计数，不报建立时间或失真。

    Returns:
        dict，含每笔翻转数、平均/最大 |dQ| 与峰值累计占比（>1 表示中途电荷折返）。
    """
    total = trace.total_abs_charge_c()
    return {
        "events": trace.n_events,
        "mean_flips_per_sample": float(trace.flips.sum(axis=1).mean()),
        "max_flips_per_sample": int(trace.flips.sum(axis=1).max()),
        "mean_abs_charge_c": float(total.mean()),
        "max_abs_charge_c": float(total.max()),
        "mean_final_charge_c": float(trace.final_charge_c().mean()),
        "peak_cumulative_fraction": float(
            (trace.cumulative_abs_charge_c.max(axis=1) / np.maximum(np.abs(total), 1e-300)).max()
        ),
    }


def reference_current_a(trace: FollowerTrace, fs_hz: float) -> float:
    """由平均 |dQ| 与采样率给出参考轨平均电流 [A]。

    这是电荷记账的推论，不是版图/驱动能力结论；正负轨各承担一半的量级判断需分别积分，
    此处给出两轨合计的上界估计（|dQ| 已取绝对值）。

    Args:
        trace: 单事件或 follower trace。
        fs_hz: 采样率 [Hz]。

    Returns:
        平均电流 [A]。
    """
    if not np.isfinite(fs_hz) or fs_hz <= 0:
        raise ConfigError("reference current requires a positive sample rate")
    return float(trace.total_abs_charge_c().mean() * fs_hz)


def compare_reference_cost(
    follower: FollowerTrace,
    direct: FollowerTrace,
    trial: FollowerTrace,
    *,
    fs_hz: float,
    paper_reference_current_a: float | None = None,
) -> dict:
    """三方对比参考电荷：follower / 直接最终态 / 传统试探 SAR。

    结论本身要能反驳：

    * `final_charge_mismatch_c` 必须为 0——三条路径的**最终稳态电荷**相同，
      follower 不改善静态精度；
    * `follower_over_direct_charge_ratio` 应为 1——只拆事件不会多花电荷，
      所以"follower 比直接加载更费电"是错的；
    * `trial_over_follower_charge_ratio` > 1——真正被消除的是试探-撤销的往返电荷。

    Args:
        follower: 已决前缀逐事件路径。
        direct: 单事件最终码路径（不可实现的对照）。
        trial: 传统试探路径。
        fs_hz: 采样率 [Hz]，只用于把电荷换成平均电流。
        paper_reference_current_a: 论文给的参考电流 [A]，可选，仅作量级对照。

    Returns:
        dict；含三条路径的 |dQ|、电流、倍数与论文比值。
    """
    if follower.n_events < 2 or direct.n_events != 1 or trial.n_events < 2:
        raise ConfigError("comparison needs follower, single-event direct load and trial trace")
    if not np.isfinite(fs_hz) or fs_hz <= 0:
        raise ConfigError("reference cost comparison needs a positive sample rate")
    final_follower = follower.final_charge_c()
    final_direct = direct.final_charge_c()
    final_trial = trial.final_charge_c()
    mismatch = float(
        max(
            np.max(np.abs(final_follower - final_direct)),
            np.max(np.abs(final_follower - final_trial)),
        )
    )
    scale = max(float(np.max(np.abs(final_follower))), 1e-30)
    q_follower = float(follower.total_abs_charge_c().mean())
    q_direct = float(direct.total_abs_charge_c().mean())
    q_trial = float(trial.total_abs_charge_c().mean())
    result = {
        "final_charge_mismatch_c": mismatch,
        "statically_equivalent": mismatch <= 64 * np.finfo(float).eps * scale,
        "follower_mean_abs_charge_c": q_follower,
        "direct_mean_abs_charge_c": q_direct,
        "trial_mean_abs_charge_c": q_trial,
        "follower_over_direct_charge_ratio": q_follower / q_direct if q_direct else float("inf"),
        "trial_over_follower_charge_ratio": q_trial / q_follower if q_follower else float("inf"),
        "follower_reference_current_a": q_follower * fs_hz,
        "direct_reference_current_a": q_direct * fs_hz,
        "trial_reference_current_a": q_trial * fs_hz,
        "mean_flips_follower": float(follower.flips.sum(axis=1).mean()),
        "mean_flips_direct": float(direct.flips.sum(axis=1).mean()),
        "mean_flips_trial": float(trial.flips.sum(axis=1).mean()),
        "events": {
            "follower": follower.n_events,
            "direct": direct.n_events,
            "trial": trial.n_events,
        },
    }
    if paper_reference_current_a is not None:
        if not np.isfinite(paper_reference_current_a) or paper_reference_current_a <= 0:
            raise ConfigError("paper reference current must be finite positive")
        result["paper_reference_current_a"] = float(paper_reference_current_a)
        result["follower_over_paper_current"] = (
            q_follower * fs_hz / paper_reference_current_a
        )
        result["trial_over_paper_current"] = q_trial * fs_hz / paper_reference_current_a
    return result


__all__ = [
    "FOLLOWER_EVENTS",
    "CausalityAudit",
    "FollowerTrace",
    "ResolvedPrefixes",
    "audit_causality",
    "compare_reference_cost",
    "reference_current_a",
    "run_direct_final",
    "run_follower",
    "run_trial_sar",
    "switching_cost",
]
