"""AUX 输入核算判据的单元测试（依据 US10541702B1 FIG.1/FIG.2）。

原件结构：V_IN 与 V_IN_AUX 经各自电阻接到**同一个采样节点**，各有一路开关（22）。
因此 AUX 是"与主输入并联的第二源"，不是独立通路。
"""

from __future__ import annotations

import numpy as np
import pytest

from adi_model.control.aux_input import aux_sample_charge
from adi_model.simulation.config import ConfigError

C_SAMPLE = 20e-12
C_BRANCH = 1e-12


def test_aux_off_contributes_exactly_zero():
    """开关 22 断开时 AUX 完全不接入：源电荷与支路电荷都必须为 0。"""
    ledger = aux_sample_charge(
        main_v=1.0, aux_v=0.9, c_sample_f=C_SAMPLE, c_aux_branch_f=C_BRANCH, aux_switched=False
    )
    assert ledger.aux_source_c == 0.0
    assert ledger.aux_branch_c == 0.0
    assert ledger.node_total_c == pytest.approx(C_SAMPLE * 1.0)


def test_aux_on_shares_the_same_sampling_node():
    """AUX 闭合时两个源都进同一节点：节点总电荷 = 主 + AUX + 支路。"""
    ledger = aux_sample_charge(
        main_v=1.0, aux_v=0.5, c_sample_f=C_SAMPLE, c_aux_branch_f=C_BRANCH, aux_switched=True
    )
    assert ledger.main_source_c == pytest.approx(C_SAMPLE * 1.0)
    assert ledger.aux_source_c == pytest.approx(C_SAMPLE * 0.5)
    assert ledger.aux_branch_c == pytest.approx(C_BRANCH * (0.5 - 1.0))
    assert ledger.node_total_c == pytest.approx(C_SAMPLE * 1.0 + C_SAMPLE * 0.5 - C_BRANCH * 0.5)


def test_aux_equal_to_main_leaves_no_branch_charge():
    """AUX 与主输入同电位时，辅助电容两端无压差 ⇒ 支路无可搬运电荷。"""
    ledger = aux_sample_charge(
        main_v=0.7, aux_v=0.7, c_sample_f=C_SAMPLE, c_aux_branch_f=C_BRANCH, aux_switched=True
    )
    assert ledger.aux_branch_c == pytest.approx(0.0)


def test_reset_charge_is_subtracted_from_the_node():
    """复位阶段放掉的电荷不进信号：节点净电荷要减去它。"""
    base = aux_sample_charge(
        main_v=1.0, aux_v=0.5, c_sample_f=C_SAMPLE, c_aux_branch_f=C_BRANCH, aux_switched=True
    )
    with_reset = aux_sample_charge(
        main_v=1.0,
        aux_v=0.5,
        c_sample_f=C_SAMPLE,
        c_aux_branch_f=C_BRANCH,
        aux_switched=True,
        reset_c=1e-12,
    )
    assert with_reset.node_total_c == pytest.approx(base.node_total_c - 1e-12)


def test_signal_fraction_quantifies_aux_contribution():
    """AUX 占比可算：AUX 越大，分数越高；AUX 关闭时为 0。"""
    off = aux_sample_charge(
        main_v=1.0, aux_v=1.0, c_sample_f=C_SAMPLE, c_aux_branch_f=0.0, aux_switched=False
    )
    on = aux_sample_charge(
        main_v=1.0, aux_v=1.0, c_sample_f=C_SAMPLE, c_aux_branch_f=0.0, aux_switched=True
    )
    assert off.signal_fraction == 0.0
    assert on.signal_fraction == pytest.approx(0.5)


def test_rejects_non_finite_or_out_of_range_arguments():
    """非有限值与非法电容/电荷必须报错，不能静默计算。"""
    with pytest.raises(ConfigError):
        aux_sample_charge(
            main_v=float("nan"), aux_v=0.0, c_sample_f=C_SAMPLE, c_aux_branch_f=0.0, aux_switched=True
        )
    with pytest.raises(ConfigError):
        aux_sample_charge(
            main_v=0.0, aux_v=0.0, c_sample_f=0.0, c_aux_branch_f=0.0, aux_switched=True
        )
    with pytest.raises(ConfigError):
        aux_sample_charge(
            main_v=0.0, aux_v=0.0, c_sample_f=C_SAMPLE, c_aux_branch_f=-1e-12, aux_switched=True
        )
    with pytest.raises(ConfigError):
        aux_sample_charge(
            main_v=0.0, aux_v=0.0, c_sample_f=C_SAMPLE, c_aux_branch_f=0.0, aux_switched=True, reset_c=-1.0
        )
    with pytest.raises(ConfigError):
        aux_sample_charge(
            main_v=0.0, aux_v=0.0, c_sample_f=C_SAMPLE, c_aux_branch_f=0.0, aux_switched=1
        )
