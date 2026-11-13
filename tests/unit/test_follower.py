"""RDAC follower 因果语义与参考代价的单元测试。

夹具是声明过的小型合成池（9 主单元/1 子单元），不是原芯片几何；
本组测试只验证"事件码只依赖已决前缀"与"最终电荷一致、过程代价更大"这两件事。
"""

from __future__ import annotations

import numpy as np
import pytest

from adi_model.control.dither import complete_flash3_sar6_prefix_table
from adi_model.control.follower import (
    FOLLOWER_EVENTS,
    ResolvedPrefixes,
    audit_causality,
    compare_reference_cost,
    reference_current_a,
    run_direct_final,
    run_follower,
    run_trial_sar,
    switching_cost,
)
from adi_model.simulation.config import ConfigError


def _pool_and_ids(factory, n_samples: int):
    _cfg, controls = factory(n_samples=n_samples)
    pool = controls["pool"]
    ids = pool.plan(n_samples).conv
    return pool, ids


def _peeking(values: np.ndarray) -> np.ndarray:
    """故意偷看最终码的错误实现，用于证明因果门禁不是恒真。"""
    return np.repeat(values[:, -1:], values.shape[1], axis=1)


def test_prefix_table_ends_at_final_code():
    final = np.arange(512, dtype=np.int64)
    prefixes = ResolvedPrefixes.from_final(final)
    assert prefixes.codes.shape == (512, FOLLOWER_EVENTS)
    assert np.array_equal(prefixes.codes[:, -1], final)
    assert np.array_equal(prefixes.codes[:, 0], final & ~63)


def test_prefix_table_rejects_wrong_length():
    with pytest.raises(ConfigError):
        ResolvedPrefixes(np.zeros((4, 6), dtype=np.int64), np.zeros(4, dtype=np.int64))


def test_prefix_table_rejects_inconsistent_final():
    codes = complete_flash3_sar6_prefix_table()[:3].copy()
    bad = codes[:, -1] + 1
    with pytest.raises(ConfigError):
        ResolvedPrefixes(codes, bad)


def test_truncated_fill_only_touches_unresolved_columns():
    final = np.array([0, 17, 511], dtype=np.int64)
    prefixes = ResolvedPrefixes.from_final(final)
    cut = prefixes.truncated(2, 511)
    assert np.array_equal(cut[:, :3], prefixes.codes[:, :3])
    assert np.all(cut[:, 3:] == 511)


def test_production_event_codes_are_causal(explicit_pipeline_factory):
    pool, ids = _pool_and_ids(explicit_pipeline_factory, 32)
    final = np.arange(32, dtype=np.int64) * 16
    prefixes = ResolvedPrefixes.from_final(final)
    audit = audit_causality(prefixes, lambda values: values)
    assert audit.causal
    assert audit.checked == prefixes.codes.size
    # 施加码不能越过该事件的已决前缀
    trace = run_follower(
        pool, prefixes, np.zeros(32, np.int64), slice_ids=ids,
        reference_high_v=3.0, reference_low_v=-3.0,
    )
    assert np.array_equal(trace.applied_codes, prefixes.codes)


def test_peeking_implementation_is_caught(explicit_pipeline_factory):
    pool, ids = _pool_and_ids(explicit_pipeline_factory, 8)
    prefixes = ResolvedPrefixes.from_final(np.array([3, 9, 120, 255, 511, 64, 200, 17]))
    audit = audit_causality(prefixes, _peeking)
    assert not audit.causal
    assert audit.violations > 0
    assert audit.first is not None


def test_follower_final_charge_equals_direct_load(explicit_pipeline_factory):
    pool, ids = _pool_and_ids(explicit_pipeline_factory, 16)
    final = np.arange(16, dtype=np.int64) * 32
    prefixes = ResolvedPrefixes.from_final(final)
    states = np.zeros(16, np.int64)
    follower = run_follower(
        pool, prefixes, states, slice_ids=ids, reference_high_v=3.0, reference_low_v=-3.0
    )
    direct = run_direct_final(
        pool, final, states, slice_ids=ids, reference_high_v=3.0, reference_low_v=-3.0
    )
    trial = run_trial_sar(
        pool, final, states, slice_ids=ids, reference_high_v=3.0, reference_low_v=-3.0
    )
    comparison = compare_reference_cost(follower, direct, trial, fs_hz=40e6)
    assert comparison["statically_equivalent"]
    assert comparison["final_charge_mismatch_c"] == 0.0
    # 拆事件不增加电荷：follower 与一次加载等电荷
    assert comparison["follower_over_direct_charge_ratio"] == pytest.approx(1.0, rel=1e-12)
    # 传统试探 SAR 多付的是试探-撤销的往返电荷
    assert comparison["trial_over_follower_charge_ratio"] > 1.0
    assert comparison["events"] == {"follower": 7, "direct": 1, "trial": 18}


def test_trial_sar_converges_and_rejects_wide_codes(explicit_pipeline_factory):
    pool, ids = _pool_and_ids(explicit_pipeline_factory, 4)
    final = np.array([0, 1, 255, 511], dtype=np.int64)
    trace = run_trial_sar(
        pool, final, np.zeros(4, np.int64), slice_ids=ids,
        reference_high_v=3.0, reference_low_v=-3.0,
    )
    assert np.array_equal(trace.applied_codes[:, -1], final)
    with pytest.raises(ConfigError):
        run_trial_sar(
            pool, np.array([512, 0, 0, 0], dtype=np.int64), np.zeros(4, np.int64),
            slice_ids=ids, bits=9, reference_high_v=3.0, reference_low_v=-3.0,
        )


def test_follower_cost_is_monotone_in_event_count(explicit_pipeline_factory):
    pool, ids = _pool_and_ids(explicit_pipeline_factory, 8)
    final = np.full(8, 300, dtype=np.int64)
    states = np.zeros(8, np.int64)
    prefixes = ResolvedPrefixes.from_final(final)
    follower = run_follower(
        pool, prefixes, states, slice_ids=ids, reference_high_v=3.0, reference_low_v=-3.0
    )
    # 累计 |dQ| 单调不减，且末值等于总和
    cumulative = follower.cumulative_abs_charge_c
    assert np.all(np.diff(cumulative, axis=1) >= 0)
    assert np.allclose(cumulative[:, -1], follower.total_abs_charge_c())


def test_reference_current_scales_with_sample_rate(explicit_pipeline_factory):
    pool, ids = _pool_and_ids(explicit_pipeline_factory, 4)
    final = np.array([100, 200, 300, 400], dtype=np.int64)
    trace = run_direct_final(
        pool, final, np.zeros(4, np.int64), slice_ids=ids,
        reference_high_v=3.0, reference_low_v=-3.0,
    )
    low = reference_current_a(trace, 10e6)
    high = reference_current_a(trace, 40e6)
    assert high == pytest.approx(4 * low, rel=1e-12)
    with pytest.raises(ConfigError):
        reference_current_a(trace, 0.0)


def test_out_of_domain_event_code_is_rejected(explicit_pipeline_factory):
    pool, ids = _pool_and_ids(explicit_pipeline_factory, 4)
    prefixes = ResolvedPrefixes.from_final(np.array([1, 2, 3, 4], dtype=np.int64))
    with pytest.raises(ConfigError, match="outside the explicit domain"):
        run_follower(
            pool, prefixes, np.zeros(4, np.int64), slice_ids=ids,
            event_code_fn=lambda values: np.full_like(values, 512),
            reference_high_v=3.0, reference_low_v=-3.0,
        )


def test_switching_cost_reports_counts_not_timing(explicit_pipeline_factory):
    pool, ids = _pool_and_ids(explicit_pipeline_factory, 4)
    trace = run_follower(
        pool,
        ResolvedPrefixes.from_final(np.array([511, 400, 256, 7], dtype=np.int64)),
        np.zeros(4, np.int64),
        slice_ids=ids,
        reference_high_v=3.0,
        reference_low_v=-3.0,
    )
    report = switching_cost(trace)
    assert report["events"] == FOLLOWER_EVENTS
    assert report["mean_flips_per_sample"] >= 1.0
    assert report["peak_cumulative_fraction"] >= 1.0


def _committed_only(values: np.ndarray) -> np.ndarray:
    """正确实现：第 i 个事件只能用前 i 个已决位。"""
    n_events = values.shape[1]
    out = np.zeros_like(values)
    for i in range(n_events):
        out[:, i] = values[:, : i + 1].sum(axis=1)
    return out


def test_every_event_emits_one_command_and_ignores_future_bits():
    """D2：逐事件命令存在，且只依赖已决前缀。

    这条替代"用 import 计数判断是否实现"：它直接检查事件数与因果性。
    """
    n = 16
    prefixes = ResolvedPrefixes.from_final(np.arange(n, dtype=np.int64))
    good = audit_causality(prefixes, _committed_only)
    assert good.checked == n * FOLLOWER_EVENTS, "每笔每条事件都应产生一条命令"
    assert good.violations == 0, f"正确实现被判违规: {good.first}"

    bad = audit_causality(prefixes, _peeking)
    assert bad.violations > 0, "偷看最终码的实现必须被抓到（否则门禁恒真）"


def test_follower_reaches_the_same_final_code_by_a_different_event_path(explicit_pipeline_factory):
    """D2：follower 与"一次加载"必须**逐事件不同、最终相同**。

    只证明最终电荷一致，无法区分"逐事件跟随"和"算完再一次性写"；
    只证明逐事件不同，又可能是错的码。两条一起才构成机制证据。
    """
    pool, ids = _pool_and_ids(explicit_pipeline_factory, 6)
    final = np.arange(6, dtype=np.int64) * 32
    prefixes = ResolvedPrefixes.from_final(final)
    states = np.zeros(6, np.int64)
    follower = run_follower(
        pool, prefixes, states, slice_ids=ids, reference_high_v=3.0, reference_low_v=-3.0
    )
    direct = run_direct_final(
        pool, final, states, slice_ids=ids, reference_high_v=3.0, reference_low_v=-3.0
    )
    a = np.asarray(follower.applied_codes)
    b = np.asarray(direct.applied_codes)
    # 中间事件必须不同：否则 follower 只是"算完再写"的别名
    assert not np.array_equal(a[:, :-1], b[:, :-1]), "follower 的中间事件与直接加载相同，机制未生效"
    # 末事件必须相同：否则码是错的
    assert np.array_equal(a[:, -1], b[:, -1]), "最后一拍应与最终码一致"
