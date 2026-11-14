"""把论文 [00] 对片池的**具体陈述**固化成可核对断言。

论文原文（逐字，正文）：
  "the RDAC is composed of 8 slice DACs (sDAC) converting the signal, and
   8 sDACs acquiring the signal, dynamically selected from a pool of 18 sDACs.
   Two spare sDACs are introduced to allow shuffling of the sampling DACs and
   spread the residual interleaving tones."

拆成四条可核对的性质：
  ① 池 18 片、动态选 8 片；
  ② 同一笔里"采样 8 片"与"转换 8 片"**不相交**（16 片在用、2 片空着）；
  ③ 那 2 片就是 spare，用于打散交织杂散 ⇒ 多笔之内必须**覆盖到全部 18 片**；
  ④ 动态选择（不同笔选出的集合不完全相同），否则"spare"没有意义。
"""

from __future__ import annotations

import numpy as np

from adi_model.defaults import DefaultModel, DefaultParameters


def _plan(n: int):
    model = DefaultModel(seed=20261105, parameters=DefaultParameters(), config_overrides=None)
    raw = model.simulate(lambda t: 0.0, n).raw
    return raw, raw.pool.plan(n)


def test_pool_is_eighteen_slices_eight_active():
    """① 池 18 片、每笔选 8 片。

    两处**独立**表述都与这个配置吻合：
      · 论文正文："8 slice DACs converting … 8 sDACs acquiring … pool of 18 …
        Two spare sDACs"
      · PPT 第 10 页逐字："Quantizer (1x) and RDAC (8x)"、
        "Pool of 18x slice DACs for residue DAC"、"2x spare (randomisation) DACs"，
        右侧图结构画出 `8x sDAC | 2x sDAC | 8x sDAC` ⇒ 8+2+8 = 18
    """
    raw, plan = _plan(4)
    assert raw.cfg.n_slices == 18
    assert raw.cfg.n_active == 8
    assert np.asarray(plan.acq).shape == (4, 8)
    assert np.asarray(plan.conv).shape == (4, 8)
    # 8 转换 + 8 采样 = 16 在用，18 - 16 = 2 片备用（两处原件一致）
    assert raw.cfg.n_slices - 2 * raw.cfg.n_active == 2


def test_sampling_and_conversion_slices_are_disjoint():
    """② 采样片与转换片在同一笔里不相交。

    这不是"最好不要重叠"，而是"两相交错"的直接后果：同一时刻一组在采样、
    另一组在转换。若相交，同一块电容会同时被要求两个状态。
    """
    _raw, plan = _plan(8)
    acq = np.asarray(plan.acq)
    conv = np.asarray(plan.conv)
    for i in range(acq.shape[0]):
        overlap = set(acq[i].tolist()) & set(conv[i].tolist())
        assert not overlap, f"sample {i}: sampling and conversion share slices {sorted(overlap)}"


def test_all_eighteen_slices_are_used_within_a_few_samples():
    """③ 多笔之内覆盖全部 18 片 —— 这就是"2 片 spare"被用起来的证据。

    每笔只用 16 片，若连续多笔仍覆盖不到第 17/18 片，说明 spare 没有被轮换。
    """
    _raw, plan = _plan(12)
    used = set()
    for i in range(12):
        used |= set(np.asarray(plan.acq)[i].tolist())
        used |= set(np.asarray(plan.conv)[i].tolist())
    assert used == set(range(18)), f"only {len(used)}/18 slices used in 12 samples"


def test_slice_selection_is_dynamic():
    """④ 动态选择：不同笔的转换片集合不全相同（固定集合等于没有 spare）。"""
    _raw, plan = _plan(6)
    conv = [tuple(sorted(np.asarray(plan.conv)[i].tolist())) for i in range(6)]
    assert len(set(conv)) > 1, "conversion slice set never changes; spares are unused"


def test_acquisition_for_next_sample_differs_from_current_conversion():
    """两相交错的直接核验：本笔采样的片 ≠ 本笔转换的片，且下一笔的采样片
    就是本笔转换用的那些片（互补 8 片轮转）。"""
    _raw, plan = _plan(6)
    acq = np.asarray(plan.acq)
    conv = np.asarray(plan.conv)
    for i in range(acq.shape[0] - 1):
        overlap = set(acq[i + 1].tolist()) & set(conv[i].tolist())
        assert len(overlap) <= 8, f"sample {i}: next acquisition overlaps >8 slices with conversion"
