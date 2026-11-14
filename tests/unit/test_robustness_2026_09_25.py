"""迁移2026-09-25稳健性审查的实际失败保护，历史编号保留于库外对照表。

生产接口只使用当前显式物理/数字合同；旧experiments、unary、KTC、ref_track、
AUX比例和按码距离推串扰均已退役。这里保留独立随机流、有限性、真实电荷/RC、
可辨识性、文件写入和真实构建后端的反例，不以历史报告PASS证明当前芯片性能。
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
import warnings
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from adi_model.behavioral.charge_ref import ref_ra_charge, ref_ra_charge_nodal
from adi_model.behavioral.conversion import response_interval
from adi_model.behavioral.dynamics import crosstalk_error, switching_activity
from adi_model.behavioral.input_network import (
    InputNetworkParameters,
    PassiveTrackingNetwork,
    track_interval,
)
from adi_model.behavioral.low_frequency_noise import BandLimitedFlicker
from adi_model.behavioral.pretracking import QuantizedPretracker
from adi_model.behavioral.ra import ResidueAmplifier
from adi_model.behavioral.reference_charge import physical_reference_program
from adi_model.behavioral.sadc import build_first_stage_quantizer
from adi_model.behavioral.sampler import dc_input, sine_input
from adi_model.calib import observability, required_samples
from adi_model.calibration.weight_calibration import (
    CalibrationUnidentifiableError,
    fit_unit_weights,
)
from adi_model.cli import main_simulate
from adi_model.defaults import DefaultParameters, default_model
from adi_model.inventory_gate import GateResult, _check_sources, build_summary, verify
from adi_model.metrics.metrics import reference_precision_bits
from adi_model.simulation.config import Config, ConfigError
from adi_model.simulation.serialization import result_document, write_results
from adi_model.simulation.slice_pool import PhysicalSlicePool
from adi_model.topology.dac_arch import NominalCodebook, SplitDAC
from adi_model.topology.mapper import Mapper

REPO = Path(__file__).resolve().parents[2]


def _quiet_model(seed=999):
    return default_model(
        seed=seed,
        parameters=DefaultParameters(unit_mismatch_std=0, bridge_mismatch_std=0),
        config_overrides={"enable_sampling_noise": False, "ra_enable_noise": False},
    )


def _network():
    return PassiveTrackingNetwork([1e-12], [100.0], 0.0, 0.0)


def _charge_inputs():
    rng = np.random.default_rng(7)
    return {
        "main_cap_f": np.array([1.0, 1.3, 0.8]) * 1e-12,
        "sub_cap_f": np.array([0.7, 1.1]) * 1e-12,
        "bridge_cap_f": 1.4e-12,
        "sub_parasitic_f": 0.3e-12,
        "feedback_cap_f": 0.4e-12,
        "sample_main_v": rng.uniform(-1, 1, (32, 3)),
        "sample_sub_v": rng.uniform(-1, 1, (32, 2)),
        "conversion_main_v": rng.uniform(-3, 3, (32, 3)),
        "conversion_sub_v": rng.uniform(-3, 3, (32, 2)),
    }


def _mapper():
    book = NominalCodebook(0, 3, np.array([-1.0, 0.0, 1.0, 2.0]))
    return Mapper(np.arange(4, dtype=np.int64), nominal_codebook=book)


def _rows(data, count):
    names = (
        "slice_ids",
        "adc2_code",
        "main_masks",
        "sub_masks",
        "signal_mask",
        "known_voltage_term_v",
        "overflow",
        "sample_ids",
    )
    return replace(data, **{name: getattr(data, name)[:count] for name in names})


def _response_inputs():
    return {
        "y0": 0.0,
        "z0": 0.0,
        "target_v": 0.2,
        "reference_drive_v": 0.1,
        "tau_ref_s": 1e-6,
        "dt_s": 1e-6,
        "ra_bw_hz": None,
        "adc_bw_hz": None,
        "clip_v": 1.0,
        "slew_v_s": None,
    }


def _reference_inputs():
    return {
        "slice_ids": np.array([0]),
        "main_caps_f": np.array([[1e-12, 2e-12]]),
        "sub_caps_f": np.array([[1e-12]]),
        "bridge_caps_f": np.array([1e-12]),
        "main_parasitic_f": np.array([0.25e-12]),
        "sub_parasitic_f": np.array([0.5e-12]),
        "main_on": np.array([[[False, False]], [[True, False]], [[True, False]]]),
        "sub_on": np.zeros((3, 1, 1), bool),
        "times_s": np.array([0.0, 1e-9, 2e-9]),
        "sampled_main_bottom_v": np.full((1, 2), 0.3),
        "sampled_sub_bottom_v": np.full((1, 1), 0.3),
        "sampled_top_v": np.zeros(1),
        "sampled_subnode_v": np.zeros(1),
        "ref_high_v": 3.0,
        "ref_low_v": -3.0,
    }


def _aux_parameters(**changes):
    return replace(
        InputNetworkParameters(
            filter_cap_f=0.0,
            auxiliary_cap_f=1e-14,
            auxiliary_bypass=True,
            auxiliary_source_ohm=1.0,
            auxiliary_resistance_ohm=1.0,
        ),
        **changes,
    )


def _load_exporter():
    path = REPO / "tools/export_rtl_params.py"
    spec = importlib.util.spec_from_file_location("robustness_current_exporter", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def exporter_case(tmp_path):
    """复制明确的数字组件夹具；不创建未知物理绑定或改原文件。"""
    path = tmp_path / "explicit_component"
    shutil.copytree(REPO / "sim/fixtures/source_control_component", path)
    return _load_exporter(), path


def test_monte_carlo_mismatch_and_noise_streams_are_independent():
    """F1：实际默认层的制造/计划/噪声分流可重放，不恢复旧MC生产器。"""
    a, b = default_model(seed=999), default_model(seed=999)
    first = a.simulate(dc_input(0.31), 64)
    second = b.simulate(dc_input(0.31), 64)
    assert np.array_equal(a.chip.C_main, b.chip.C_main)
    assert np.array_equal(first.codes.code, second.codes.code)
    assert np.array_equal(first.codes.analog_overflow, second.codes.analog_overflow)
    partition = first.metadata["seed_partition"]
    assert partition["fabrication_spawn_key"] == [0]
    assert partition["schedule_spawn_key"] == [1, 0]
    assert partition["noise_spawn_key"] == [2, 0]
    assert partition["schedule_seed"] != partition["noise_seed"]


def test_spawn_does_not_consume_parent_stream():
    """F2：NumPy SeedSequence分子流不消耗已建立的父Generator。"""
    sequence = np.random.SeedSequence(42)
    parent = np.random.Generator(np.random.PCG64(sequence))
    sequence.spawn(1)
    assert np.array_equal(parent.normal(size=4), np.random.default_rng(42).normal(size=4))


def test_noise_switch_does_not_change_physical_schedule_or_chip():
    """F2：实际打开噪声不能消耗调度或制造随机流。"""
    noisy = default_model(seed=777)
    quiet = default_model(
        seed=777, config_overrides={"enable_sampling_noise": False, "ra_enable_noise": False}
    )
    a, b = noisy.simulate(dc_input(0.31), 64), quiet.simulate(dc_input(0.31), 64)
    assert np.array_equal(noisy.chip.C_main, quiet.chip.C_main)
    assert np.array_equal(a.raw.conv_slice_ids, b.raw.conv_slice_ids)
    assert np.array_equal(a.raw.acq_slice_ids, b.raw.acq_slice_ids)
    assert np.any(a.raw.sample.sadc_noise_v != 0)
    assert np.array_equal(b.raw.sample.sadc_noise_v, np.zeros(64))


@pytest.mark.parametrize(
    "kwargs",
    [
        {"g0": -32.0},
        {"g0": float("nan")},
        {"n_slices": 0},
        {"dac_n_main": 0},
        {"dac_n_sub": 0},
        {"ra_v_clip": 0.0},
    ],
)
def test_config_legality_rejects_illegal_values(configuration_inputs, kwargs):
    assert replace(configuration_inputs, **kwargs).legality_violations()


def test_config_requested_default_is_legal():
    model = default_model()
    assert model.cfg.legality_violations(geometry=model.geometry) == []
    assert Config().legality_violations()  # 底层不完整对象没有隐式物理默认值。


@pytest.mark.parametrize("bad", [-1.0, float("nan"), float("inf")])
def test_active_ron_law_rejects_illegal_coefficients(bad):
    with pytest.raises(ValueError, match="Ron law"):
        track_interval(
            [1e-12],
            [100.0],
            0.0,
            [0.0],
            0.0,
            1e-9,
            dc_input(1.0),
            parameters=InputNetworkParameters(filter_cap_f=0.0),
            ron_coefficient=bad,
        )


def test_chip_build_rejects_nonpositive_unit_capacitance(toy_physical_case):
    bad = toy_physical_case.chip.C_main.copy()
    bad[0, 0] = -1e-12
    with pytest.raises(ValueError, match="positive"):
        replace(toy_physical_case.chip, C_main=bad)


@pytest.mark.parametrize("gain", [0.0, -0.2, float("nan"), float("inf")])
def test_pretracker_rejects_invalid_signal_scale(gain):
    with pytest.raises(ValueError):
        QuantizedPretracker(4, codebook_v=[-1.0, 1.0], signal_scale=gain)


@pytest.mark.parametrize("r_aux", [0.0, -1.0, float("nan"), float("inf")])
def test_auxiliary_branch_rejects_nonpositive_resistance(r_aux):
    with pytest.raises(ValueError, match="auxiliary_resistance_ohm"):
        _aux_parameters(auxiliary_resistance_ohm=r_aux).require_auxiliary_source(dc_input(1.0))


def test_auxiliary_branch_requires_an_independent_positive_source():
    parameters = _aux_parameters()
    assert parameters.require_auxiliary_source(dc_input(1.0)) is None
    with pytest.raises(ValueError, match="auxiliary_input_fn"):
        parameters.require_auxiliary_source(None)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -float("inf")])
def test_sadc_rejects_non_finite_input(configuration_inputs, bad):
    with pytest.raises(ValueError, match="finite"):
        build_first_stage_quantizer(configuration_inputs).convert_codes(np.array([0.1, bad]))


def test_explicit_charge_nodal_matches_closed_on_fractional_bottom_voltages():
    values = _charge_inputs()
    closed, nodal = ref_ra_charge(**values), ref_ra_charge_nodal(**values)
    assert np.max(np.abs(closed - nodal)) / np.max(np.abs(closed)) < 1e-12


def test_unknown_sampling_connections_are_not_an_implicit_full_column(toy_physical_case):
    case = toy_physical_case
    pool = PhysicalSlicePool(case.cfg, chip=case.chip, nominal_codebook=case.book)
    with pytest.raises(ValueError, match="unbound"):
        pool.acquire(
            np.arange(8), np.full(8, 0.1), sample_id=0, sampled_top_v=0.0, sampled_subnode_v=0.0
        )
    for function in (ref_ra_charge, ref_ra_charge_nodal):
        values = _charge_inputs()
        values["conversion_main_v"] = None
        with pytest.raises(ValueError, match="explicit"):
            function(**values)


def test_explicit_zero_voltage_change_has_zero_feedback_charge():
    values = _charge_inputs()
    values["conversion_main_v"] = values["sample_main_v"].copy()
    values["conversion_sub_v"] = values["sample_sub_v"].copy()
    assert np.array_equal(ref_ra_charge(**values), np.zeros(32))
    assert np.array_equal(ref_ra_charge_nodal(**values), np.zeros(32))


def test_crosstalk_uses_explicit_fractional_signed_driver_changes():
    caps = np.array([0.5, 1.0, 2.0]) * 1e-15
    changes = np.array([[0.25, -0.5, 1.5], [0.0, 1.0, -1.0]])
    got = crosstalk_error(
        coupling_cap_f=caps, digital_voltage_change_v=changes, output_capacitance_f=1e-12
    )
    expected = [
        sum(float(c) * float(v) for c, v in zip(caps, row, strict=True)) / 1e-12 for row in changes
    ]
    assert np.allclose(got, expected, rtol=0, atol=1e-18)
    rounded = (caps * np.rint(changes)).sum(-1) / 1e-12
    assert not np.allclose(got, rounded, rtol=1e-12, atol=0)


def test_rank_deficient_overdetermined_system_exposes_null_directions():
    matrix = np.array([[1.0, 2.0], [2.0, 4.0], [3.0, 6.0]])
    info = observability(matrix)
    assert info.rank == 1 and info.n_parameters == 2
    assert np.isinf(info.cond)
    assert np.array_equal(matrix @ np.array([-2.0, 1.0]), np.zeros(3))


def test_reference_precision_keeps_bias_and_rejects_undefined_zero_rms():
    with pytest.raises(ValueError, match="positive"):
        reference_precision_bits(0.0, 1.0)
    constant = np.full(16, 1e-6)
    rms = float(np.sqrt(np.mean(constant**2)))
    assert rms > constant.std()
    assert reference_precision_bits(rms, 1.0) == pytest.approx(np.log2(1 / (np.sqrt(12) * rms)))
    error = np.linspace(-1e-6, 1e-6, 16)
    assert reference_precision_bits(float(np.sqrt(np.mean(error**2))), 1.0) == pytest.approx(
        18.841282446503264
    )


def test_mapper_encode_validates_contract():
    mapper = _mapper()
    with pytest.raises(ValueError, match="outside"):
        mapper.encode(np.array([4]))
    with pytest.raises(ValueError, match="one-dimensional|1-D|dimensions"):
        mapper.encode(3)
    with pytest.raises(ValueError, match="match coarse rows"):
        Mapper(
            np.arange(4),
            nominal_codebook=mapper.nominal_codebook,
            event_codes_by_coarse=np.zeros((2, 1), int),
        )


def test_mapper_encode_rejects_noninteger_dtype_and_accepts_integer_control():
    mapper = _mapper()
    for value in (np.array([2.5]), np.array([2.0]), np.array([True])):
        with pytest.raises(ValueError, match="integer"):
            mapper.encode(value)
    assert np.array_equal(mapper.encode(np.array([2], np.int64)).codes, [2])
    with pytest.raises(ValueError, match="outside"):
        mapper.encode(np.array([4]))


def test_short_forward_record_keeps_the_explicit_input_frequency():
    model = _quiet_model()
    for n in (16, 32, 64, 128, 256):
        run = model.simulate(sine_input(1.5, 1e6), n)
        times = np.arange(n) / model.cfg.fs
        expected = 1.5 * np.sin(2 * np.pi * 1e6 * times)
        assert np.array_equal(run.raw.sample.input_clean_v, expected)
        assert len(run.codes.code) == n
    with pytest.raises(TypeError, match="calibration"):
        Config.from_dict({"calibration": "gain_beta"})


def test_underdetermined_fit_rejects_instead_of_inventing_weights(synthetic_linear_data):
    data = _rows(synthetic_linear_data.data, 3)
    with pytest.raises(CalibrationUnidentifiableError, match="cannot identify"):
        fit_unit_weights(data, synthetic_linear_data.known[:3])


def test_crosstalk_uniform_explicit_coupling_and_activity():
    changes = np.array([[1.0, 0.0, -1.0], [0.5, 0.5, 0.5]])
    actual = crosstalk_error(
        coupling_cap_f=np.full(3, 1e-15),
        digital_voltage_change_v=changes,
        output_capacitance_f=1e-12,
    )
    assert np.allclose(actual, [0.0, 1.5e-3], rtol=0, atol=1e-18)
    before = np.array([[False, True, False], [True, True, False]])
    after = np.array([[True, True, False], [False, False, True]])
    assert np.array_equal(switching_activity(before, after), [1, 3])


def test_result_serialization_tolerates_null_and_preserves_failure():
    results = {"not_run": None, "status": "FAIL", "bad": np.nan}
    document = result_document(results)
    assert document["not_run"] is None and document["status"] == "FAIL"
    assert document["bad"] is None and document["undefined_numeric_values"][0]["path"] == "/bad"
    assert np.isnan(results["bad"])
    json.dumps(document, allow_nan=False)


def test_inventory_gate_flags_duplicate_source_id(tmp_path):
    import hashlib

    data = b"synthetic identity bytes"
    external = tmp_path / "source_inputs"
    external.mkdir()
    (external / "fixture.pdf").write_bytes(data)
    source = {
        "id": "x",
        "file": "fixture.pdf",
        "sha256": hashlib.sha256(data).hexdigest(),
        "pages": 1,
    }
    result = GateResult()
    _check_sources(
        tmp_path,
        {"location_rule": {"default_dir": str(external)}, "sources": [source, dict(source)]},
        result,
    )
    assert any(
        r.check == "sources.duplicate_id" and r.level == "FAIL" for r in result.finalize().records
    )


def test_repo_inventory_gate_checks_static_identity_without_device_pass():
    result = verify(REPO)
    assert result.static_refs_pass, [r for r in result.records if r.level == "FAIL"]
    summary = build_summary(result, REPO)
    assert summary["model_tests_run"] is False and summary["rtl_or_eda_run"] is False
    assert summary["source_conformance"] == "SOURCE_CONFORMANCE_NOT_ASSESSED"
    if not result.required_states_ready:
        assert summary["unresolved_required_states"]


@pytest.mark.parametrize("gain", [0.0, -1.5, float("nan"), float("inf"), -float("inf")])
def test_ra_rejects_nonpositive_or_nonfinite_actual_gain(gain):
    ra = ResidueAmplifier(Config(ra_v_clip=1.0, ra_enable_noise=False))
    with pytest.raises(ValueError, match="gain|positive|finite"):
        ra.evaluate(np.array([0.1]), None, np.array([gain]))


def test_ra_error_input_uses_the_same_domain_guards():
    ra = ResidueAmplifier(Config(ra_v_clip=1.0, ra_enable_noise=False))
    for bad in (float("nan"), float("inf")):
        with pytest.raises(ValueError, match="finite"):
            ra.evaluate(np.array([0.1]), None, np.array([2.0]), input_error_v=np.array([bad]))
    out, sat = ra.evaluate(np.array([0.1]), None, np.array([2.0]), input_error_v=np.array([0.005]))
    assert out[0] == pytest.approx(0.21) and not sat.any()


def test_degenerate_estimator_is_explicit_and_silent(synthetic_linear_data):
    data = synthetic_linear_data.data
    bad = replace(
        data,
        slice_ids=np.zeros_like(data.slice_ids),
        signal_mask=np.ones_like(data.signal_mask),
        known_voltage_term_v=np.zeros_like(data.known_voltage_term_v),
    )
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        with pytest.raises(CalibrationUnidentifiableError, match="rank"):
            fit_unit_weights(bad, np.ones(len(data.sample_ids)))
    assert not [w for w in caught if issubclass(w.category, RuntimeWarning)]


def test_full_rank_estimator_recovers_the_declared_positive_weights(synthetic_linear_data):
    case = synthetic_linear_data
    frozen = fit_unit_weights(case.data, case.known)
    data = case.data
    units = case.weights.shape[1]
    design = np.zeros((len(case.known), case.weights.size + 1))
    for row, selected in enumerate(data.slice_ids):
        for slot, physical in enumerate(selected):
            design[row, physical * units : (physical + 1) * units] = (
                data.signal_mask[row, slot] * case.known[row] + data.known_voltage_term_v[row, slot]
            )
    design[:, -1] = 1.0
    truth = np.r_[case.weights.ravel(), case.offset]
    half_step = data.spec.adc2_step_v / 2
    assert np.max(np.abs(data.fine_v - design @ truth)) <= half_step + 1e-15
    expected, _, rank, _ = np.linalg.lstsq(design, data.fine_v, rcond=None)
    estimated = np.r_[frozen.weights.ravel(), frozen.offset_v]
    assert np.allclose(estimated, expected, rtol=1e-11, atol=1e-13)
    # 每笔箱中心误差<=半个ADC2 LSB；pinv每行的绝对和给出对应参数的确定上界。
    bound = np.abs(np.linalg.pinv(design)).sum(axis=1) * half_step
    assert np.all(np.abs(estimated - truth) <= bound + 1e-13)
    assert frozen.rank == rank == case.weights.size + 1


def test_cli_reports_output_pointing_at_a_file(tmp_path, monkeypatch, capsys):
    import adi_model.cli as cli

    target = tmp_path / "not_a_dir.txt"
    target.write_text("preserve")

    def forbidden(**kwargs):
        raise AssertionError("existing target must be rejected before model construction")

    monkeypatch.setattr(cli, "default_model", forbidden)
    assert main_simulate(["--samples", "4", "--output", str(target)]) == 2
    assert target.read_text() == "preserve"
    assert json.loads(capsys.readouterr().err)["model_run"] is False


def test_internal_review_report_dir_is_ignored():
    patterns = (REPO / ".gitignore").read_text().splitlines()
    assert "docs/robustness_review_2026/" in patterns
    assert patterns.index("docs/robustness_review_2026/") > patterns.index("!docs/**/*.png")


def test_sdist_excludes_third_party_originals_and_keeps_real_public_assets():
    """真实hatchling路径判定；后端是dev依赖，缺失必须失败而非skip。"""
    from hatchling.builders.sdist import SdistBuilder

    cfg = SdistBuilder(str(REPO)).config
    original_documents = json.loads((REPO / "references/source_manifest.json").read_text())[
        "documents"
    ]
    excluded = [
        "docs/robustness_review_2026/fig/paper_p1.png",
        "docs/robustness_review_2026/fig/patent12_fig.png",
        "docs/robustness_review_2026/robustness_review_2026.pdf",
        "docs/robustness_review_2026/robustness_review_2026.tex",
        *(row["target_relative_path"] for row in original_documents),
    ]
    assert original_documents
    for relative in excluded:
        assert cfg.path_is_excluded(relative), relative
    kept = [
        "README.md",
        "pyproject.toml",
        "references/README.md",
        "references/source_manifest.json",
            "src/adi_model/defaults.py",
            "src/adi_model/control/dither.py",
            "src/adi_model/calibration/weight_calibration.py",
            "src/adi_model/rtl_observation.py",
            "src/adi_model/simulation/serialization.py",
            "src/adi_model/cli.py",
            "src/adi_model/topology/mapper.py",
            "src/adi_model/behavioral/conversion.py",
            "src/adi_model/metrics/metrics_lsb20.py",
        "docs/readme/make_source_bound_figures.py",
        "docs/readme/fig/source_bound_default_architecture_v1.png",
        "docs/readme/make_overview_figures.py",
        "docs/caaz/make_figures.py",
        "sim/fixtures/source_control_component/config.json",
        "sim/fixtures/source_control_component/format.json",
        "sim/fixtures/source_control_component/binding.json",
        "sim/fixtures/source_control_component/source_control_binding.sv",
        "sim/fixtures/source_control_component/source_acquisition_binding.sv",
        "tools/export_rtl_params.py",
        "tools/run_open_rtl.py",
    ]
    for relative in kept:
        assert (REPO / relative).is_file(), relative
        assert not cfg.path_is_excluded(relative), relative


def test_historical_reference_output_is_present_and_typed():
    """只保留历史归档存在性；不检查或重盖旧stage结果的PASS。"""
    payload = json.loads((REPO / "tools/results/results.json").read_text())
    assert isinstance(payload, dict)
    assert {"s1", "mc", "mc_pdk_off", "s13"} <= set(payload)


def test_split_dac_explicit_nominal_endpoints_are_ordered(toy_physical_case):
    case = toy_physical_case
    book = NominalCodebook(0, 2, np.array([-1.0, 0.0, 1.0]))
    dac = SplitDAC(case.cfg, case.chip, nominal_codebook=book)
    assert np.array_equal(dac.evaluate_nominal(np.array([0, 2])), [-1.0, 1.0])


@pytest.mark.parametrize(
    "field,bad", [(f, b) for f in ("fs", "v_fs") for b in (float("nan"), float("inf"))]
)
def test_config_fs_vfs_reject_nonfinite(configuration_inputs, field, bad):
    assert replace(configuration_inputs, **{field: bad}).legality_violations()


def test_config_fs_vfs_accept_finite(configuration_inputs):
    assert replace(configuration_inputs, fs=40e6, v_fs=3.0).legality_violations() == []


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -float("inf")])
def test_reference_precision_bits_rejects_nonfinite(bad):
    with pytest.raises(ValueError, match="finite"):
        reference_precision_bits(bad, 1.0)
    with pytest.raises(ValueError, match="finite"):
        reference_precision_bits(1e-3, bad)


def test_reference_precision_bits_accepts_finite():
    assert reference_precision_bits(1e-3, 1.0) == pytest.approx(np.log2(1 / (np.sqrt(12) * 1e-3)))


@pytest.mark.parametrize(
    "field,bad",
    [
        (f, b)
        for f in (
            "filter_cap_f",
            "auxiliary_cap_f",
            "auxiliary_source_ohm",
            "auxiliary_resistance_ohm",
            "pretrack_time_s",
            "pretrack_source_ohm",
        )
        for b in (float("nan"), float("inf"))
    ],
)
def test_input_network_declared_parameters_reject_nonfinite(field, bad):
    with pytest.raises(ValueError, match=field):
        replace(InputNetworkParameters(filter_cap_f=0.0), **{field: bad}).require_tracking()


def test_input_network_declared_parameters_accept_finite():
    parameters = InputNetworkParameters(filter_cap_f=0.0)
    assert parameters.violations() == []
    assert parameters.require_tracking() is None


@pytest.mark.parametrize("bad", [float("nan"), float("inf")])
def test_pretracker_weights_reject_nonfinite(bad):
    with pytest.raises(ValueError, match="weights"):
        QuantizedPretracker(
            4, codebook_v=[-1.0, 0.0, 1.0], signal_scale=1.0, weights=(0.6, 0.3, bad)
        )


def test_pretracker_weights_accept_finite():
    tracker = QuantizedPretracker(
        4, codebook_v=[-1.0, 0.0, 1.0], signal_scale=1.0, weights=(0.6, 0.3, 0.1)
    )
    assert tracker.history_depth == 3


@pytest.mark.parametrize("bad", [float("nan"), float("inf")])
def test_tracking_interval_rejects_nonfinite_time(bad):
    with pytest.raises(ValueError, match="finite"):
        _network().advance([0.0], 0.0, bad, dc_input(1.0))


def test_tracking_interval_accepts_finite_and_matches_single_pole():
    actual = _network().advance([0.0], 0.0, 1e-9, dc_input(1.0))
    assert actual[0] == pytest.approx(1 - np.exp(-10), abs=1e-13)


@pytest.mark.parametrize(
    "field,bad",
    [
        (f, b)
        for f in ("caps_f", "switch_ohm", "source_ohm", "filter_cap_f")
        for b in (float("nan"), float("inf"))
    ],
)
def test_tracking_components_reject_nonfinite(field, bad):
    inputs = {"caps_f": [1e-12], "switch_ohm": [100.0], "source_ohm": 30.0, "filter_cap_f": 1e-13}
    inputs[field] = [bad] if field in ("caps_f", "switch_ohm") else bad
    with pytest.raises(ValueError, match="finite"):
        PassiveTrackingNetwork(**inputs)


def test_tracking_components_accept_finite():
    network = PassiveTrackingNetwork([1e-12], [100.0], 30.0, 1e-13)
    after = network.advance(np.zeros(2), 0.0, 1e-9, dc_input(1.0))
    assert np.all(np.isfinite(after)) and np.all((after >= 0) & (after <= 1))


@pytest.mark.parametrize("which", ["white_density_v2_hz", "high_hz"])
@pytest.mark.parametrize("bad", [float("nan"), float("inf")])
def test_declared_noise_band_rejects_nonfinite(which, bad):
    values = {
        "white_density_v2_hz": 1e-12,
        "corner_hz": 40.0,
        "low_hz": 0.1,
        "rng": np.random.default_rng(5),
        "high_hz": 40.0,
        "modes": 8,
        "reference_node": "test",
        "distribution": "gaussian",
    }
    values[which] = bad
    with pytest.raises(ValueError):
        BandLimitedFlicker.create(**values)


def test_declared_noise_band_has_explicit_ensemble_variance():
    state = BandLimitedFlicker.create(
        1e-12,
        40.0,
        0.1,
        np.random.default_rng(5),
        high_hz=40.0,
        modes=8,
        reference_node="test",
        distribution="gaussian",
    )
    assert state.expected_variance_v2 == pytest.approx(1e-12 * 40 * np.log(400))


@pytest.mark.parametrize("bad", [float("nan"), float("inf")])
def test_required_samples_rejects_nonfinite(bad):
    with pytest.raises(ValueError, match="target parameter"):
        required_samples(np.eye(4), 1e-4, bad, oversample=2.0, sample_rate_hz=40e6)


def test_required_samples_accepts_finite():
    res = required_samples(np.eye(4), 1e-4, 1e-3, oversample=2.0, sample_rate_hz=40e6)
    assert res["feasible"] is True
    assert res["recommended_samples"] == pytest.approx(8.0)
    assert res["duration_s"] == pytest.approx(8 / 40e6)


@pytest.mark.parametrize("bad", [float("nan"), float("inf")])
def test_pretracker_codebook_rejects_nonfinite_lower_endpoint(bad):
    with pytest.raises(ValueError, match="finite"):
        QuantizedPretracker(4, codebook_v=[bad, 1.0], signal_scale=1.0)


def test_pretracker_codebook_accepts_finite_negative_endpoint():
    tracker = QuantizedPretracker(4, codebook_v=[-2.0, -1.0, 0.0, 1.0], signal_scale=1.0)
    assert np.array_equal(tracker.codebook_v, [-2.0, -1.0, 0.0, 1.0])


@pytest.mark.parametrize(
    "field,bad", [(f, b) for f in ("tau_ref_s", "clip_v") for b in (float("nan"), float("inf"))]
)
def test_response_interval_rejects_nonfinite(field, bad):
    kw = _response_inputs()
    kw[field] = bad
    with pytest.raises(ValueError, match="finite"):
        response_interval(**kw)


def test_response_interval_accepts_finite():
    y, z, sat = response_interval(**_response_inputs())
    assert y == pytest.approx(0.2 + 0.1 / np.e)
    assert y == z and sat is False


@pytest.mark.parametrize(
    "field,bad",
    [
        (f, b)
        for f in ("times_s", "main_caps_f", "ref_high_v")
        for b in (float("nan"), float("inf"))
    ],
)
def test_physical_reference_program_rejects_nonfinite_events(field, bad):
    values = _reference_inputs()
    if field == "times_s":
        values[field] = np.array([0.0, bad, 2e-9])
    elif field == "main_caps_f":
        values[field] = np.array([[bad, 2e-12]])
    else:
        values[field] = bad
    with pytest.raises(ValueError):
        physical_reference_program(**values)


def test_physical_reference_program_preserves_repeated_real_events():
    result = physical_reference_program(**_reference_inputs())
    assert len(result.times_s) == 3 and np.all(np.isfinite(result.charge_c))
    assert np.array_equal(result.charge_c[-1], [0.0, 0.0])


@pytest.mark.parametrize("bad", [float("nan"), float("inf")])
def test_active_reference_recovery_rejects_nonfinite_tau(bad):
    values = _response_inputs()
    values["tau_ref_s"] = bad
    with pytest.raises(ValueError, match="finite"):
        response_interval(**values)


def test_active_reference_recovery_matches_its_declared_exponential():
    values = _response_inputs()
    values.update(target_v=0.0, reference_drive_v=1.0, clip_v=2.0)
    y, z, saturated = response_interval(**values)
    assert y == pytest.approx(np.exp(-1)) and z == pytest.approx(y)
    assert not saturated


def test_fit_unit_weights_rejects_nonfinite_or_nonpositive(synthetic_linear_data, monkeypatch):
    """因子非finite必须在乘法前拒绝；有限的非正权重由对应守卫拒绝。"""
    import adi_model.calibration.weight_calibration as wc

    data, known = synthetic_linear_data.data, synthetic_linear_data.known
    assert np.all(fit_unit_weights(data, known).weights > 0)
    count = np.prod(data.spec.shape) + 1

    def factors(bad):
        def fake(design, **kwargs):
            u = np.zeros((len(known), count))
            indices = np.arange(count)
            u[indices, indices] = -np.sign(data.fine_v[:count])
            v = np.eye(count)
            v[0, 0] = bad
            return u, np.ones(count), v

        return fake

    with monkeypatch.context() as patch:
        patch.setattr(wc, "svd", factors(1.0))
        with pytest.raises(ValueError, match="nonpositive"):
            fit_unit_weights(data, known)
    for bad in (float("nan"), float("inf")):
        with monkeypatch.context() as patch:
            patch.setattr(wc, "svd", factors(bad))
            with pytest.raises(ValueError, match="non-finite factors"):
                fit_unit_weights(data, known)


@pytest.mark.parametrize(
    "field,bad",
    [
        ("adc2_v_min", float("nan")),
        ("adc2_v_max", float("inf")),
        ("v_fs", float("nan")),
        ("v_fs", float("inf")),
    ],
)
def test_spec_physical_scales_must_be_finite(synthetic_linear_data, field, bad):
    with pytest.raises(ValueError, match="finite"):
        replace(synthetic_linear_data.data.spec, **{field: bad})


def test_spec_physical_scales_accepts_finite(synthetic_linear_data):
    data = synthetic_linear_data.data
    assert data.validate() is None
    assert fit_unit_weights(data, synthetic_linear_data.known).rank == 7


def test_slice_pool_rejects_nonfinite_caps(toy_physical_case):
    bad = toy_physical_case.chip.C_main.copy()
    bad[0, 0] = np.nan
    with pytest.raises(ValueError, match="finite"):
        replace(toy_physical_case.chip, C_main=bad)


def test_slice_pool_accepts_finite(toy_physical_case):
    case = toy_physical_case
    pool = PhysicalSlicePool(case.cfg, chip=case.chip, nominal_codebook=case.book)
    assert pool.chip is case.chip and not pool.held_valid.any()
    assert np.array_equal(pool.chip.C_main, case.chip.C_main)


@pytest.mark.parametrize("bad", [float("nan"), float("inf")])
def test_tracking_residual_rejects_nonfinite_interval(bad):
    with pytest.raises(ValueError, match="finite"):
        _network().advance([0.0], 0.0, bad, dc_input(1.0))


def test_tracking_residual_accepts_finite():
    residual = 1 - _network().advance([0.0], 0.0, 1e-9, dc_input(1.0))[0]
    assert residual == pytest.approx(np.exp(-10), abs=1e-13)


@pytest.mark.parametrize("bad", [float("nan"), float("inf")])
def test_driver_charge_rejects_nonfinite_node_voltage(bad):
    with pytest.raises(ValueError, match="finite"):
        _network().charge_change(np.array([0.0]), np.array([bad]))


def test_driver_charge_from_actual_capacitor_state_is_finite():
    assert _network().charge_change(np.array([0.0]), np.array([1.0])) == pytest.approx(1e-12)


def test_export_rtl_params_rejects_out_of_range(exporter_case):
    exporter, path = exporter_case
    binding = json.loads((path / "binding.json").read_text())
    binding["timing"]["phases"] = 100
    (path / "binding.json").write_text(json.dumps(binding))
    with pytest.raises(ValueError, match="schedule"):
        exporter.load_contract(path / "config.json", path / "format.json", path / "binding.json")


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), 3.5])
def test_export_rtl_params_rejects_nonfinite(exporter_case, bad):
    exporter, path = exporter_case
    binding = json.loads((path / "binding.json").read_text())
    binding["timing"]["phases"] = bad
    (path / "binding.json").write_text(json.dumps(binding))
    with pytest.raises(ValueError, match="nonfinite|schedule"):
        exporter.load_contract(path / "config.json", path / "format.json", path / "binding.json")


def test_export_rtl_params_accepts_explicit_integer_phases(exporter_case):
    exporter, path = exporter_case
    files = exporter.build(path / "config.json", path / "format.json", path / "binding.json")
    manifest = json.loads(files["rtl_input_manifest.json"])
    assert manifest["parameters"]["PHASES"] == 16
    assert manifest["binding_interface"] == "RTL_BINDING_ABI_2_COMMON_ACQUISITION"
    assert "source_control_binding.sv" in files and "source_acquisition_binding.sv" in files
    assert set(files) == {
        "rtl_params.vh",
        "source_control_binding.sv",
        "source_acquisition_binding.sv",
        "packet_schema.json",
        "config.json",
        "format.json",
        "binding.json",
        "rtl_input_manifest.json",
    }


@pytest.mark.parametrize("bad", [float("inf"), float("nan")])
def test_direct_auxiliary_request_checks_nonfinite_branch_resistance(bad):
    parameters = _aux_parameters(auxiliary_resistance_ohm=bad)
    with pytest.raises(ValueError, match="auxiliary_resistance_ohm"):
        parameters.require_auxiliary_source(dc_input(1.0))


def test_direct_auxiliary_request_accepts_explicit_finite_source():
    parameters = _aux_parameters()
    original = replace(parameters)
    assert parameters.require_auxiliary_source(dc_input(1.0)) is None
    assert parameters.require_auxiliary_source(dc_input(1.0)) is None
    assert parameters == original


def test_atomic_result_write_failure_preserves_target_and_removes_temp(tmp_path, monkeypatch):
    import adi_model.simulation.serialization as serialization

    target = tmp_path / "result.json"
    target.write_text("original")

    def denied(*args):
        raise PermissionError("directed atomic publish failure")

    monkeypatch.setattr(serialization.os, "replace", denied)
    with pytest.raises(PermissionError, match="publish failure"):
        write_results(target, {"value": 1.0})
    assert target.read_text() == "original"
    assert set(tmp_path.iterdir()) == {target}


def test_atomic_result_write_preserves_null_failure_and_numpy_data(tmp_path):
    target = tmp_path / "result.json"
    original = {"未运行": None, "status": "FAIL", "data": np.array([1.0, np.nan])}
    result = write_results(target, original)
    assert json.loads(target.read_text()) == result
    assert result["status"] == "FAIL" and result["未运行"] is None
    assert result["data"] == [1.0, None] and result["undefined_numeric_values"]
    assert np.isnan(original["data"][1])


@pytest.mark.parametrize(
    "field", ["dither_units_range", "dither_split_bank", "dither_units_total", "dither_discrete"]
)
def test_retired_dither_mask_range_is_not_implicitly_accepted(field):
    with pytest.raises(TypeError, match=field):
        Config.from_dict({field: 4})


def test_active_aux_request_cannot_create_an_ideal_pipeline():
    model = default_model(config_overrides={"input_network": _aux_parameters()})

    def forbidden(_times):
        raise AssertionError("unbound active AUX must reject before sampling a waveform")

    with pytest.raises(ConfigError, match="complete active acquisition-state binding"):
        model.simulate(forbidden, 4)
