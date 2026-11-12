"""合同/profile 的单元测试：只验证数据身份与拒绝规则，不验证电路性能。"""

from __future__ import annotations

import pytest

from adi_model.contracts import (
    ADI_40M,
    ENG_10M,
    PATENT_FIG19,
    AcquisitionCommand,
    FrozenCalibration,
    SampleContext,
    SwitchCommand,
    TopologySpec,
    assert_caaz_free,
    load_profile,
)


def test_profiles_have_expected_operating_points():
    assert ADI_40M.fs_hz == 40e6
    assert ADI_40M.v_span_v == 6.0
    assert ADI_40M.topology.n_main == 63
    assert ENG_10M.fs_hz == 10e6
    assert ENG_10M.v_span_v == 10.0
    assert ENG_10M.topology.n_main == 17
    assert PATENT_FIG19.topology.n_slices_pool == 7


def test_all_main_profiles_are_caaz_free():
    assert_caaz_free()
    assert all(not p.caaz for p in (ADI_40M, ENG_10M, PATENT_FIG19))


def test_unknown_profile_is_rejected_not_defaulted():
    with pytest.raises(KeyError):
        load_profile("does_not_exist")


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


def test_frozen_calibration_accepts_only_matching_topology():
    cal = FrozenCalibration(
        calibration_id="c0",
        topology_id=ADI_40M.topology.topology_id,
        codebook_id=ADI_40M.topology.codebook_id,
        feature_order=("main", "sub"),
        binding_id="b0",
        v_range_v=(-3.0, 3.0),
        v_domain="input_referred",
        adc2_bin_edges=(0, 4095),
        coefficient_unit="V/V",
        sign_convention="subtract",
        q_format="Q30",
        rounding="floor",
        overflow="reject",
        training_set_id="t0",
        mode_id="nlms",
    )
    assert cal.accepts(ADI_40M.topology)
    assert not cal.accepts(ENG_10M.topology)


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
