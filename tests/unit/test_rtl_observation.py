"""Independent checks of the physical RTL observation packet contract."""

from dataclasses import replace

import numpy as np
import pytest

from adi_model import Config
from adi_model.rtl_observation import observations_from_rtl
from adi_model.weight_calibration import CalibrationSpec, fit_unit_weights


def _packet(sample_id=2, sid=256, coarse=173, dither=1, sampling=True, bad=False, inj=-13):
    """Construct one packet from an independent forward enumeration of cells."""
    active = list(range(8))
    rank = [(slot - sid % 8) % 8 for slot in range(8)]
    main_bits = 0
    sub_bits = 0
    rdac_code = coarse + dither if sampling else coarse
    for slot in range(8):
        code = (
            rdac_code + (1 if rank[slot] < 4 else -1)
            if sid & 256 and 0 < rdac_code < 511
            else rdac_code
        )
        count_main, count_sub = divmod(code, 8)
        order = []
        for row in range(8):
            for col in range(8):
                p = ((row + (sid // 8) % 8) % 8) * 8 + (col + sid % 8) % 8
                if p < 63:
                    order.append(p)
        sub_order = [(j + sid // 64) % 8 for j in range(8)]
        for unit in order[:count_main]:
            main_bits |= 1 << (63 * slot + unit)
        for unit in sub_order[:count_sub]:
            sub_bits |= 1 << (8 * slot + unit)
    rails = sum(1 << j for j in range(2 + dither))
    packed_ids = sum(v << (5 * slot) for slot, v in enumerate(active))
    return (
        2048
        | ((inj & ((1 << 64) - 1)) << 12)
        | (int(bad) << 76)
        | (int(sampling) << 77)
        | (rails << 78)
        | (sub_bits << 82)
        | (main_bits << 146)
        | (packed_ids << 650)
        | ((dither & 255) << 690)
        | (sid << 698)
        | (coarse << 707)
        | (sample_id << 716)
    )


def _spec():
    return CalibrationSpec.from_config(
        Config.paper_literal(dem_enable=True, dem_bridge_enable=True, dither_mode="sampling")
    )


def test_structural_packet_decodes_actual_masks_and_signed_fields():
    words = [_packet(2, 0), _packet(3, 256)]
    data = observations_from_rtl(words, _spec(), static_charge_domain=True)
    np.testing.assert_array_equal(data.sample_ids, [2, 3])
    np.testing.assert_array_equal(data.slice_ids, np.tile(np.arange(8), (2, 1)))
    np.testing.assert_array_equal(data.bank_dither, [1, 1])
    np.testing.assert_array_equal(data.dither_rails, [[1, 1, 1, 0], [1, 1, 1, 0]])
    np.testing.assert_allclose(data.common_injection_v, [-13 * 3 / 2**32] * 2)
    assert data.main_masks is not None and data.sub_masks is not None
    nominal = data.main_masks.sum(axis=2) * 8 + data.sub_masks.sum(axis=2)
    np.testing.assert_array_equal(nominal[0], np.full(8, 174))
    np.testing.assert_array_equal(nominal[1], [175] * 4 + [173] * 4)


def test_observation_rejects_wrong_masks_modes_and_ids():
    a = _packet(2)
    with pytest.raises(ValueError, match="physical masks"):
        observations_from_rtl([a ^ (1 << 146)], _spec(), static_charge_domain=True)
    with pytest.raises(ValueError, match="sampling mode"):
        observations_from_rtl([_packet(sampling=False)], _spec(), static_charge_domain=True)
    with pytest.raises(ValueError, match="missing sample IDs"):
        observations_from_rtl([a, _packet(4)], _spec(), static_charge_domain=True)
    with pytest.raises(ValueError, match="duplicated or reordered"):
        observations_from_rtl([a, a], _spec(), static_charge_domain=True)
    with pytest.raises(ValueError, match="dither rails"):
        observations_from_rtl([a ^ (1 << 78)], _spec(), static_charge_domain=True)


def test_observation_wrap_and_bad_samples_are_not_hidden():
    data = observations_from_rtl(
        [_packet(0xFFFF_FFFF), _packet(0, bad=True)], _spec(), static_charge_domain=True
    )
    np.testing.assert_array_equal(data.sample_ids, [0xFFFF_FFFF, 0])
    np.testing.assert_array_equal(data.overflow, [False, True])
    with pytest.raises(ValueError, match="clipped conversions"):
        fit_unit_weights(data, [0.0, 0.1])
    dynamic = replace(data, overflow=np.zeros(2, dtype=bool), static_charge_domain=False)
    with pytest.raises(ValueError, match="static charge-gain"):
        fit_unit_weights(dynamic, [0.0, 0.1])


def test_live_structural_capture_matches_python_sample_and_switch_contract():
    """Replay a frozen, independently captured structural-DUT acquisition."""
    import csv
    from pathlib import Path

    from adi_model.pipeline import run_pipeline

    root = Path(__file__).resolve().parents[2]
    words = (root / "sim/vectors/rtl_structural_observations.hex").read_text().splitlines()
    with (root / "sim/vectors/rtl_structural_reference.csv").open(newline="") as stream:
        reference = list(csv.DictReader(stream))
    cfg = Config.rtl_structural(
        dem_enable=True,
        dither_mode="sampling",
        mismatch_enable=False,
        enable_sampling_noise=False,
        ra_enable_noise=False,
        sadc_rdac_gain_mismatch=0,
    )
    data = observations_from_rtl(words, CalibrationSpec.from_config(cfg), static_charge_domain=True)
    x = np.array([float(row["input_v"]) for row in reference])
    np.testing.assert_array_equal(data.sample_ids, [int(row["sample_id"]) for row in reference])

    def source(time_s):
        index = np.rint(np.asarray(time_s) * cfg.fs).astype(int)
        return x[np.clip(index, 0, len(x) - 1)]

    model = run_pipeline(cfg, source, len(x))
    np.testing.assert_array_equal(model.sample_id, data.sample_ids)
    np.testing.assert_array_equal(model.conv_slice_ids, data.slice_ids)
    np.testing.assert_array_equal(model.sid, data.dem_state)
    np.testing.assert_array_equal(model.sample.dither_bank_code, data.bank_dither)
    np.testing.assert_array_equal(model.k, data.rdac_code)
    np.testing.assert_array_equal(model.coarse + data.bank_dither, data.rdac_code)
