"""检查显式样本归属、已声明事件及结构接口与电气模型的边界。"""

from dataclasses import replace

import numpy as np
import pytest

from adi_model.simulation.config import Config, ConfigError
from adi_model.control.dither import complete_flash3_sar6_prefix_table
from adi_model.topology.mapper import Mapper
from adi_model.simulation.pipeline import run_pipeline
from adi_model.behavioral.reference_charge import physical_reference_program
from adi_model.behavioral.sampler import dc_input


def test_reference_program_keeps_all_resolved_events_and_uses_actual_connections(
    toy_physical_case,
):
    case = toy_physical_case
    events = complete_flash3_sar6_prefix_table()[173]
    main = np.zeros((7, 8, 2), dtype=bool)
    sub = np.zeros((7, 8, 2), dtype=bool)
    for index, code in enumerate(events):
        main[index, :, 0] = bool(code & 0x20)
        main[index, :, 1] = bool(code & 0x08)
        sub[index, :, 0] = bool(code & 0x04)
        sub[index, :, 1] = bool(code & 0x01)
    ids = case.ids[0]
    chip = case.chip
    result = physical_reference_program(
        slice_ids=ids,
        main_caps_f=chip.C_main[ids],
        sub_caps_f=chip.C_sub[ids],
        bridge_caps_f=chip.C_bridge[ids],
        main_parasitic_f=chip.c_p_main[ids],
        sub_parasitic_f=chip.c_p_sub[ids],
        main_on=main,
        sub_on=sub,
        times_s=np.arange(7) * 1e-9,
        sampled_main_bottom_v=np.full((8, 2), 0.2),
        sampled_sub_bottom_v=np.full((8, 2), 0.2),
        sampled_top_v=np.zeros(8),
        sampled_subnode_v=np.zeros(8),
        ref_high_v=3.0,
        ref_low_v=-3.0,
    )
    assert result.charge_c.shape == (7, 2)
    np.testing.assert_array_equal(result.times_s, np.arange(7) * 1e-9)
    np.testing.assert_array_equal(result.charge_c[[2, 5]], 0)
    assert np.any(result.charge_c != 0)


def test_mapper_freezes_only_the_explicit_event_table_and_never_invents_one(
    explicit_pipeline_factory,
):
    _cfg, controls = explicit_pipeline_factory(n_samples=4)
    book = controls["pool"].nominal_codebook
    coarse = np.arange(512)
    no_events = Mapper(coarse, nominal_codebook=book)
    assert no_events.encode(np.array([173])).event_codes is None
    table = complete_flash3_sar6_prefix_table().copy()
    actual = Mapper(coarse, nominal_codebook=book, event_codes_by_coarse=table)
    table[173, 0] = 0
    np.testing.assert_array_equal(
        actual.encode(np.array([173])).event_codes[0], [128, 160, 160, 168, 172, 172, 173]
    )
    assert not actual.event_codes_by_coarse.flags.writeable


def test_structural_profile_keeps_explicit_sample_ids_and_full_acquisition_period(
    explicit_pipeline_factory,
):
    cfg, controls = explicit_pipeline_factory(n_samples=8)
    cfg = replace(cfg, execution_profile="rtl16")
    original = controls["sample_ids"].copy()
    result = run_pipeline(cfg, dc_input(0.2), 8, **controls)
    np.testing.assert_array_equal(result.sample_id, np.arange(8))
    np.testing.assert_array_equal(
        result.held_sample_ids, np.broadcast_to(np.arange(8)[:, None], (8, 8))
    )
    np.testing.assert_array_equal(controls["sample_ids"], original)
    np.testing.assert_allclose(
        result.sample.aperture_s - result.sample.acquisition_start_s,
        1 / cfg.fs,
        atol=1e-23,
        rtol=0,
    )
    assert result.conversion_trace is None
    assert result.effective_config["execution_profile"] == "rtl16"


def test_structural_interface_does_not_supply_missing_analog_or_clock_models():
    cfg = Config.rtl_structural()
    assert cfg.adc2_n_bits is None and cfg.conversion is None
    with pytest.raises(ConfigError, match="missing"):
        cfg.check_legal()
    assert Config.from_dict(cfg.to_dict()) == cfg
    with pytest.raises(TypeError):
        Config.from_dict({"rdac_bitwise_loading": True})
    with pytest.raises(TypeError):
        Config.from_dict({"dyn_t_conv_frac": 0.4})
