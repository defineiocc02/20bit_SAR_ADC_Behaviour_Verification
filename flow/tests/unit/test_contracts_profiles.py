"""合同/profile 的单元测试：只验证数据身份与拒绝规则，不验证电路性能。"""

# =============================================================================
# 文件名     : test_contracts_profiles.py
# 模块名     : 运行配置和合同身份检查
# 功能       : 防止配置身份、默认模型及校准接收条件发生漂移。
# 设计说明   :
#   * 正向用例比较对外声明与实际装配的参数。
#   * 异常用例分别隔离未知目标、重复片号、电容和掩码约束。
# =============================================================================
from __future__ import annotations

from dataclasses import replace

import pytest

from adi_model.contracts import (
    ADI_40M,
    PROFILES,
    AcquisitionCommand,
    FrozenCalibration,
    SampleContext,
    SwitchCommand,
    TopologySpec,
    assert_caaz_free,
    load_profile,
)


# ---- 1. 配置登记与模型装配 --------------------------------------------------
def test_registered_profile_has_the_current_operating_point():
    """当前只有一个工作配置，按登记标识查询可取回同一个对象。"""
    assert PROFILES == {ADI_40M.profile_id: ADI_40M}
    assert load_profile(ADI_40M.profile_id) is ADI_40M
    assert ADI_40M.fs_hz == 40e6
    assert ADI_40M.v_span_v == 6.0
    assert ADI_40M.topology.n_main == 16
    assert ADI_40M.topology.n_sub == 4
    # ADC2 位宽随目标记录输出，误登记会使数值结果和接口规格失去联系。
    assert ADI_40M.adc2_bits == 13


def test_adi_profile_matches_default_parameters():
    """`ADI_40M` 声明的拓扑必须与 `DefaultParameters` 逐项一致。

    这条守卫防止"规格声明"与"实现默认值"再次脱节——本会话已多次遇到
    两处定义漂移的问题（σ、ADC2 位数、ra_clip、S4 权重）。
    """
    from adi_model.defaults import DefaultParameters

    params = DefaultParameters()
    spec = ADI_40M.topology
    assert spec.n_slices_pool == 18
    assert spec.n_slices_active == 8
    assert spec.n_main == params.n_main == 16
    assert spec.n_sub == params.n_sub == 4
    # 数字规格只绑定归一化权重，显式拒绝把未给出的绝对法拉值送至物理消费者。
    assert spec.topology_id == "adc_digital_ip_v21" and spec.normalization_only
    assert (spec.c_unit_f, spec.c_bridge_f, spec.c_feedback_f) == (None, None, None)
    with pytest.raises(ValueError):
        spec.require_physical()
    assert ADI_40M.adc2_bits == params.adc2_n_bits


# CAAZ 由独立扩展装配。隐式进入主目标会改变样本域和有效输出速率。
def test_main_profile_is_caaz_free():
    assert_caaz_free()
    assert not ADI_40M.caaz


def test_unknown_profile_is_rejected_not_defaulted():
    with pytest.raises(KeyError):
        load_profile("does_not_exist")


# ---- 2. 数据身份和硬约束 ----------------------------------------------------
# 用一个字段故意破坏输入，其余值沿用标准合同，避免误中另一条校验。
def test_topology_rejects_nonpositive_unit_cap():
    with pytest.raises(ValueError):
        TopologySpec(topology_id="x", profile_id="x", c_unit_f=0.0)


def test_sample_context_requires_unique_physical_slices():
    with pytest.raises(ValueError):
        SampleContext(
            seq=0,
            sample_id=0,
            epoch=0,
            quantizer_id=0,
            physical_slice_ids=(1, 1),
            logical_slots=(0, 1),
        )


def test_switch_command_allows_overlap_feature_two():
    cmd = SwitchCommand(
        sample_id=0,
        conversion_mask=(1, 0),
        sampling_mask=(1, 0),
        level="Signal",
        feature=2,
        binding_id="b0",
    )
    assert cmd.feature == 2
    with pytest.raises(ValueError):
        SwitchCommand(
            sample_id=0,
            conversion_mask=(0,),
            sampling_mask=(0,),
            level="Signal",
            feature=3,
            binding_id="b0",
        )


# 同一器件参数也可能来自不同的物理连接。训练记录只能按拓扑身份复用。
def test_frozen_calibration_accepts_only_matching_topology():
    cal = FrozenCalibration(
        calibration_id="c0",
        topology_id=ADI_40M.topology.topology_id,
        codebook_id=ADI_40M.topology.codebook_id,
        feature_order=("main", "sub"),
        binding_id="b0",
        v_range_v=(-3.0, 3.0),
        v_domain="input_referred",
        adc2_bin_edges=(0, (1 << ADI_40M.adc2_bits) - 1),
        coefficient_unit="V/V",
        sign_convention="subtract",
        q_format="Q30",
        rounding="floor",
        overflow="reject",
        training_set_id="t0",
        mode_id="nlms",
    )
    assert cal.accepts(ADI_40M.topology)
    # 拓扑身份不同的系数不能复用，即使底下各电容参数恰好相同。
    assert not cal.accepts(replace(ADI_40M.topology, topology_id="other-topology"))


def test_acquisition_command_rejects_non_binary_masks():
    with pytest.raises(ValueError):
        AcquisitionCommand(
            sample_id=0,
            slice_ids=(0,),
            sample_main=(2,),
            sample_sub=(0,),
            signal_connect=(0,),
            binding_id="b0",
        )
