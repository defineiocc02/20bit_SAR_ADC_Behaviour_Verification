"""将 R1–R18 的仍有效合同迁到当前显式模型、控制入口与引用门禁。

旧验收52项/豁免账本、7+2/6bit入口、自动DR拟合、KTC/Wiener分支和64轮转实现
已退役，不恢复这些实现作为测试需求。保留样本归属、定向物理扰动、来源降级、
入口拒绝、退出码、显式必需ID、共享Rs与“静态登记不等于执行验证”等实质负例。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from types import SimpleNamespace

import numpy as np
import pytest

from adi_model.cli import main_validate_config
from adi_model.simulation.config import Config, ConfigError
from adi_model.topology.dac_arch import SplitChip
from adi_model.defaults import DefaultParameters, default_model
from adi_model.control.dem import SplitSwitchCommand
from adi_model.behavioral.input_network import PassiveTrackingNetwork
from adi_model.inventory_gate import build_summary, verify
from adi_model.inventory_gate import main as inventory_main
from adi_model.behavioral.low_frequency_noise import BandLimitedFlicker
from adi_model.simulation.pipeline import run_pipeline
from adi_model.provenance import (
    Graded,
    GradingError,
    SourceGrade,
    annotate_config,
    audit_provenance,
)
from adi_model.behavioral.sampler import dc_input
from adi_model.simulation.scheduler import Scheduler
from adi_model.simulation.slice_pool import PhysicalSlicePool
from adi_model.control.timing import SlicePlan


@pytest.fixture
def nominal_model():
    """声明均匀、零噪工程网络，仅用于因果回归，不作为source物理映射。"""
    return default_model(
        parameters=DefaultParameters(unit_mismatch_std=0, bridge_mismatch_std=0),
        config_overrides={"enable_sampling_noise": False, "ra_enable_noise": False},
    )


def _replay(model, previous, *, chip=None, binding=None, states=None, dem=False):
    """沿同一显式计划重放固定外部DC，只替换声明的plant或数字binding。"""
    cfg = replace(previous.cfg, dem_enable=dem)
    pool = PhysicalSlicePool(
        cfg,
        chip=previous.pool.chip if chip is None else chip,
        nominal_codebook=model.codebook,
        mask_binding=previous.pool.mask_binding if binding is None else binding,
        acquisition_binding=previous.pool.acquisition_binding,
        schedule_binding=previous.pool.schedule_binding,
    )
    return run_pipeline(
        cfg,
        dc_input(0.31),
        len(previous.sample_id),
        pool=pool,
        mapper=model.mapper,
        sample_ids=previous.sample_id,
        dem_states=np.zeros(len(previous.sample_id), dtype=np.int64) if states is None else states,
        sampled_top_v=0.0,
        sampled_subnode_v=0.0,
        reference_high_v=3.0,
        reference_low_v=-3.0,
        nominal_reference_high_v=3.0,
        nominal_reference_low_v=-3.0,
        static_charge_domain=True,
    )


@pytest.fixture
def reference_gate_case(tmp_path, monkeypatch):
    """临时合成身份/引用清单，不使用论文、用户文件或真实实验PASS。

    PDF为一页空白合成文档；页数只是登记值。被引用模块故意在导入时抛异常，
    用于证明引用门禁只解析锚点，不会执行模块或将登记PASS变成行为验证。
    """
    monkeypatch.delenv("ADC_SOURCES_DIR", raising=False)
    root = tmp_path / "repo"
    for directory in ("references", "src", "tests"):
        (root / directory).mkdir(parents=True, exist_ok=True)
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 10 10] >>",
    ]
    data, offsets = b"%PDF-1.4\n% synthetic test fixture\n", [0]
    for number, obj in enumerate(objects, 1):
        offsets.append(len(data))
        data += str(number).encode() + b" 0 obj\n" + obj + b"\nendobj\n"
    xref = len(data)
    data += b"xref\n0 4\n0000000000 65535 f \n"
    for offset in offsets[1:]:
        data += f"{offset:010d} 00000 n \n".encode()
    data += f"trailer\n<< /Size 4 /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    (root / "references/fixture.pdf").write_bytes(data)
    digest = hashlib.sha256(data).hexdigest()
    references = {
        "schema_version": 1,
        "documents_count": 1,
        "pages_as_file_inventory": 1,
        "documents": [
            {
                "source_id": "SYNTHETIC_ONLY",
                "target_relative_path": "references/fixture.pdf",
                "sha256": digest,
                "pages": 1,
                "bytes": len(data),
            }
        ],
    }
    sources = {
        "sources": [{"id": "SYNTHETIC_ONLY", "file": "fixture.pdf", "sha256": digest, "pages": 1}],
        "location_rule": {"default_dir": str(root / "absent_external_sources")},
    }
    inventory = {
        "axes": {
            "implementation_degree": {"values": ["COMPONENT"]},
            "evidence_source": {"values": ["SYNTHETIC"]},
            "verification_nature": {"values": ["STATIC_ONLY"]},
        },
        "config_classes": {"TEST_COMPONENT": {}},
        "coverage_obligations": {"required_task_ids": ["case_a"], "pending_items": []},
        "mechanisms": [
            {
                "id": "M1",
                "required": True,
                "implementation_degree": "COMPONENT",
                "embodiment": "TEST_COMPONENT",
                "evidence_source": ["SYNTHETIC"],
                "verification_nature": ["STATIC_ONLY"],
                "sources": [{"id": "SYNTHETIC_ONLY", "location": "synthetic page1"}],
                "task_refs": ["case_a"],
                "code": ["src/component.py"],
                "tests": ["tests/test_component.py::test_component"],
                "experiments": [{"status": "PASS"}],
            }
        ],
    }
    (root / "src/component.py").write_text(
        "raise RuntimeError('static gate must not import code')\n"
    )
    (root / "tests/test_component.py").write_text(
        "raise RuntimeError('static gate must not run tests')\ndef test_component():\n    assert False\n"
    )
    case = SimpleNamespace(root=root, references=references, sources=sources, inventory=inventory)

    def write():
        for path, value in [
            ("sources_manifest.json", sources),
            ("references/source_manifest.json", references),
            ("mechanism_inventory.json", inventory),
        ]:
            (root / path).write_text(json.dumps(value, ensure_ascii=False))

    case.write = write
    write()
    return case


class TestR1SampleOwnership:
    """R1：周期互斥不能代替跨周期实际采样归属。"""

    def test_explicit_controller_plan_is_consumed_and_keeps_all_records(self, nominal_model):
        raw = nominal_model.simulate(dc_input(0.31), 64).raw
        plan = Scheduler(raw.cfg, binding=raw.pool.schedule_binding).plan(64)
        assert np.array_equal(plan.conv, raw.conv_slice_ids)
        assert np.array_equal(plan.acq, raw.next_acq_slice_ids)
        assert np.array_equal(np.sort(plan.conv[1:], axis=1), np.sort(plan.acq[:-1], axis=1))
        assert np.all(raw.held_sample_ids == raw.sample_id[:, None])
        assert np.array_equal(raw.sample_id, np.arange(64))

    def test_disjoint_but_noncausal_groups_are_rejected(self):
        n = 4
        plan = SlicePlan(
            np.tile(np.arange(8), (n, 1)),
            np.tile(np.arange(8, 16), (n, 1)),
            np.arange(n) - 1,
            np.ones(n, bool),
            "explicit invalid ownership",
            {},
        )
        with pytest.raises(ConfigError, match="preceding acquisition"):
            Scheduler(Config(), binding=plan).plan(n)

    def test_source_cycle_metadata_cannot_be_fabricated(self, nominal_model):
        raw = nominal_model.simulate(dc_input(0.31), 8).raw
        plan = raw.pool.plan(8)
        forged = replace(plan, conv_source_cycle=np.full(8, -1, dtype=np.int64))
        with pytest.raises(ConfigError, match="metadata contradicts"):
            Scheduler(raw.cfg, binding=forged).plan(8)


class TestR2PhysicalPoolOnMainPath:
    """R2/R9/R13：定向改变actual C，只有实际使用该物理片的记录应改变。"""

    def test_directed_capacitor_perturbation_reaches_real_charge_and_gain(self, nominal_model):
        before = nominal_model.simulate(dc_input(0.31), 128).raw
        victim, chip = int(before.conv_slice_ids[0, 0]), before.pool.chip
        changed = chip.C_main.copy()
        changed[victim, 0] *= 1.02
        perturbed = SplitChip(
            changed, chip.C_sub, chip.C_bridge, chip.c_p_sub, chip.c_p_main, chip.C_feedback_true
        )
        after = _replay(nominal_model, before, chip=perturbed)
        uses = np.any(before.conv_slice_ids == victim, axis=1)
        assert uses.any() and (~uses).any()
        assert np.all(
            before.normalization_capacitance_f[uses] != after.normalization_capacitance_f[uses]
        )
        assert np.all(before.g_vec[uses] != after.g_vec[uses])
        assert np.any(before.vra[uses] != after.vra[uses])
        assert np.array_equal(before.vra[~uses], after.vra[~uses])
        assert np.array_equal(
            before.sampled_effective_charge_c[~uses], after.sampled_effective_charge_c[~uses]
        )
        a, b = before.digital_observation, after.digital_observation
        assert np.array_equal(a.signal_mask, b.signal_mask)
        assert np.array_equal(a.known_voltage_term_v, b.known_voltage_term_v)


class TestR3ProvenanceSeesActiveAssumptions:
    """R3：当前明确噪声假设不冒充自动拟合或已披露部件值。"""

    def test_default_report_has_no_reverse_fitted_noise(self):
        model = default_model()
        report = audit_provenance(model.cfg)
        assert report["fitted_supplied"] == []
        assert "ra_out_noise_rms" in report["assumed_supplied"]
        assert "ra_out_noise_rms" in audit_provenance(Config())["unbound"]
        assert annotate_config(model.cfg)["ra_out_noise_rms"].value == 1e-3

    def test_overridden_disclosure_is_not_quotable(self):
        assert "fs" not in audit_provenance(Config())["overridden"]
        assert "fs" in audit_provenance(Config(fs=80e6))["overridden"]
        with pytest.raises(GradingError):
            annotate_config(Config(fs=80e6))["fs"].require(SourceGrade.DISCLOSED)


class TestR4ConfigTakesEffectAtTheRunner:
    """R4/R14：未知枚举/缺项在实际runner被拒绝，不要求旧双入口继续存在。"""

    @pytest.mark.parametrize(
        "changes,field",
        [
            ({"adc2_endpoint_policy": "hal_open"}, "adc2_endpoint_policy"),
            ({"sadc_thresholds_v": None}, "sadc_thresholds_v"),
            ({"g0": -1}, "g0"),
        ],
    )
    def test_entry_reports_the_illegal_field(self, explicit_pipeline_factory, changes, field):
        cfg, controls = explicit_pipeline_factory(n_samples=4)
        with pytest.raises(ConfigError, match=field):
            run_pipeline(replace(cfg, **changes), dc_input(0.1), 4, **controls)

    @pytest.mark.parametrize(
        "field,value",
        [
            ("execution_profile", "unknown"),
            ("adc2_codebook", "unknown"),
            ("sadc_endpoint_policy", "unknown"),
            ("ra_enable_noise", "False"),
        ],
    )
    def test_current_field_enums_and_types_are_enforced(self, configuration_inputs, field, value):
        with pytest.raises(ConfigError, match=field):
            replace(configuration_inputs, **{field: value}).check_legal()

    def test_removed_gain_model_cannot_select_a_hidden_runner(self):
        with pytest.raises(TypeError, match="ra_gain_model"):
            Config.from_dict({"ra_gain_model": "fixed"})


class TestR5StaticGateBindsToExitCode:
    """R5：静态/状态失败必须非零，但0也不代表执行了模型或文献语义验收。"""

    def test_valid_static_registration_does_not_execute_its_fake_pass(self, reference_gate_case):
        case = reference_gate_case
        result = verify(case.root)
        assert result.static_refs_pass and result.required_states_ready
        assert inventory_main(["--repo-root", str(case.root)]) == 0
        summary = build_summary(result, case.root)
        assert summary["model_tests_run"] is False and summary["rtl_or_eda_run"] is False
        assert summary["source_conformance"] == "SOURCE_CONFORMANCE_NOT_ASSESSED"
        assert summary["reference_page_check"] == "REGISTERED_METADATA_ONLY_NOT_PDF_READING"

    def test_control_metadata_false_is_not_an_experiment_status(self, reference_gate_case):
        case = reference_gate_case
        case.inventory["config_classes"]["TEST_COMPONENT"]["control_metadata"] = {
            "dem_enable": False,
            "uncalibrated_sndr_db": -6.0,
        }
        case.write()
        result = verify(case.root)
        assert result.static_refs_pass and result.required_states_ready
        assert inventory_main(["--repo-root", str(case.root)]) == 0

    def test_wrong_source_bytes_are_a_hard_error(self, reference_gate_case):
        case = reference_gate_case
        (case.root / "references/fixture.pdf").write_bytes(b"tampered synthetic bytes")
        result = verify(case.root)
        assert not result.static_refs_pass
        assert any(r.level == "FAIL" and "sha256" in r.check for r in result.records)
        assert inventory_main(["--repo-root", str(case.root)]) == 1

    def test_a_comment_is_not_a_declared_test_anchor(self, reference_gate_case):
        case = reference_gate_case
        (case.root / "tests/test_component.py").write_text(
            "# def test_component(): pass\ndef unrelated():\n    pass\n"
        )
        result = verify(case.root)
        assert not result.static_refs_pass
        assert any(r.level == "FAIL" and r.check.endswith(".tests") for r in result.records)

    def test_existing_code_outside_the_repository_is_not_a_valid_reference(
        self, reference_gate_case
    ):
        case = reference_gate_case
        (case.root.parent / "outside.py").write_text("def outside(): pass\n")
        case.inventory["mechanisms"][0]["code"] = ["../outside.py"]
        case.write()
        assert not verify(case.root).static_refs_pass
        assert inventory_main(["--repo-root", str(case.root)]) == 1


class TestR6FlickerPhysicalTime:
    """R6：短窗口不可分辨不等于状态不存在，不恢复伪FFT漂移补丁。"""

    def test_one_explicit_state_is_shared_across_windows(self):
        state = BandLimitedFlicker.create(
            1e-12,
            40,
            0.1,
            np.random.default_rng(8),
            high_hz=40,
            modes=16,
            reference_node="test input V",
            distribution="gaussian",
        )
        whole = state.at_adc_indices(np.arange(128), 40e6)
        tail = state.at_adc_indices(np.arange(64, 128), 40e6)
        assert np.array_equal(whole[64:], tail)
        assert np.any(whole != 0)
        assert state.high_hz < 40e6 / 128


class TestR7ConfigChecksReachControlExit:
    """R7：配置CLI的错误不能被静态或普通开关值隐藏。"""

    def test_invalid_config_exits_nonzero_with_field_diagnostic(
        self, tmp_path, capsys, configuration_inputs
    ):
        path = tmp_path / "bad.json"
        path.write_text(json.dumps(replace(configuration_inputs, adc2_v_max=None).to_dict()))
        assert main_validate_config(["--config", str(path)]) == 2
        output = json.loads(capsys.readouterr().err)
        assert output["status"] == "CONFIGURATION_REJECTED" and output["model_run"] is False
        assert "adc2_v_max" in output["error"]

    def test_valid_config_is_not_claimed_as_a_simulation(
        self, tmp_path, capsys, configuration_inputs
    ):
        path = tmp_path / "valid.json"
        path.write_text(json.dumps(configuration_inputs.to_dict()))
        assert main_validate_config(["--config", str(path)]) == 0
        output = json.loads(capsys.readouterr().out)
        assert output["status"] == "CONFIG_INPUTS_VALID"
        assert output["model_run"] is False and output["source_conformance"] == "NOT_ASSESSED"


class TestR8OverrideIsEnforced:
    """R8：来源约束随有效值及全部推导依赖传播，不因换标签恢复披露权威。"""

    def test_source_value_remains_quotable_only_without_override(self):
        base = annotate_config(Config())["fs"]
        assert base.require(SourceGrade.DISCLOSED) == 40e6
        changed = annotate_config(Config(fs=80e6))["fs"]
        assert changed.effective_grade is SourceGrade.ASSUMED
        with pytest.raises(GradingError):
            changed.require(SourceGrade.DISCLOSED)

    def test_map_propagates_override_and_assumed_dependencies(self):
        changed = annotate_config(Config(fs=80e6))["fs"]
        derived = changed.map(
            lambda value: value / 1e6, source="fs / 1e6", unit="MHz", dependencies=()
        )
        assert derived.effective_grade is SourceGrade.ASSUMED
        with pytest.raises(GradingError):
            derived.require(SourceGrade.DERIVED)
        assumed = Graded(2.0, SourceGrade.ASSUMED, "explicit engineering multiplier")
        value = annotate_config(Config())["fs"].map(
            lambda fs: fs * assumed.value,
            source="fs * explicit multiplier",
            unit="Hz",
            dependencies=(assumed,),
        )
        assert value.effective_grade is SourceGrade.ASSUMED

    def test_exact_math_does_not_promote_fitted_inputs(self):
        fitted = Graded(1e-3, SourceGrade.FITTED, "synthetic fitted RMS", unit="V")
        mapped = fitted.map(lambda value: value / 32, source="RMS / 32", unit="V", dependencies=())
        assert mapped.effective_grade is SourceGrade.FITTED
        with pytest.raises(GradingError):
            mapped.require(SourceGrade.DERIVED)


class TestR10ExplicitControlBinding:
    """R10：不再由旧mode名字猜调度或物理掩码。"""

    def test_unbound_schedule_is_rejected(self):
        with pytest.raises(ConfigError, match="binding"):
            Scheduler(Config(), binding=None)

    def test_unbound_dem_request_is_not_a_default_permutation(self, nominal_model):
        pool = PhysicalSlicePool(
            replace(nominal_model.cfg, dem_enable=True),
            chip=nominal_model.chip,
            nominal_codebook=nominal_model.codebook,
        )
        with pytest.raises(ConfigError, match="binding"):
            pool.select(np.array([10]), np.array([1]), slice_ids=np.arange(8)[None, :])

    def test_retired_dem_mode_cannot_select_another_hidden_runner(self):
        with pytest.raises(TypeError, match="dem_mode"):
            Config.from_dict({"dem_mode": "permute"})


class TestR11RequiredEvidenceStates:
    """R11：旧52项/豁免账本退役；显式必需ID及未就绪状态仍不能被隐藏。"""

    @pytest.mark.parametrize("state", ["FAIL", "NOT_RUN", "BLOCKED"])
    def test_required_unready_state_never_exits_zero(self, reference_gate_case, state):
        case = reference_gate_case
        case.inventory["mechanisms"][0]["experiments"][0]["status"] = state
        case.write()
        result = verify(case.root)
        assert result.static_refs_pass and not result.required_states_ready
        assert inventory_main(["--repo-root", str(case.root)]) == 2
        summary = build_summary(result, case.root)
        assert summary["unresolved_required_states"]
        assert summary["model_tests_run"] is False

    def test_same_record_count_cannot_replace_a_required_id(self, reference_gate_case):
        case = reference_gate_case
        case.inventory["coverage_obligations"]["required_task_ids"].append("case_b")
        duplicate_task = dict(case.inventory["mechanisms"][0], id="M2")
        case.inventory["mechanisms"].append(duplicate_task)
        case.write()
        result = verify(case.root)
        assert not result.static_refs_pass
        assert any(
            r.check == "coverage.required_task_ids" and r.level == "FAIL" for r in result.records
        )

    def test_optional_label_cannot_exempt_a_required_task(self, reference_gate_case):
        case = reference_gate_case
        case.inventory["mechanisms"][0]["required"] = False
        case.write()
        result = verify(case.root)
        assert not result.static_refs_pass
        assert inventory_main(["--repo-root", str(case.root)]) == 1

    def test_summary_refuses_overwrite(self, reference_gate_case):
        case = reference_gate_case
        path = case.root / "summary.json"
        assert inventory_main(["--repo-root", str(case.root), "--summary", str(path)]) == 0
        before = path.read_bytes()
        assert inventory_main(["--repo-root", str(case.root), "--summary", str(path)]) == 2
        assert path.read_bytes() == before


class TestR12NodeBoundaries:
    """R12：KTC删除后不恢复旧fmax；当前RA摆幅与ADC2窗口仍是不同节点约束。"""

    def test_ra_clipping_is_not_adc2_overflow(self, explicit_pipeline_factory):
        cfg, controls = explicit_pipeline_factory(n_samples=4, config_changes={"ra_v_clip": 0.1})
        raw = run_pipeline(cfg, dc_input(0.01), 4, **controls)
        assert raw.ra_sat.all() and not raw.adc2_over.any()

    def test_adc2_overflow_is_not_ra_clipping(self, explicit_pipeline_factory):
        cfg, controls = explicit_pipeline_factory(
            n_samples=4, config_changes={"adc2_v_min": -0.01, "adc2_v_max": 0.01}
        )
        raw = run_pipeline(cfg, dc_input(0.01), 4, **controls)
        assert raw.adc2_over.all() and not raw.ra_sat.any()
        assert raw.digital_observation.overflow.all()


class TestR15GateRefusesTruthiness:
    """R15：当前JSON状态/布尔schema不能以字符串或数字真值代替。"""

    @pytest.mark.parametrize("bad", [True, False, 0, 1, "False", None])
    def test_non_status_values_cannot_be_pass(self, reference_gate_case, bad):
        case = reference_gate_case
        case.inventory["mechanisms"][0]["experiments"][0]["status"] = bad
        case.write()
        assert not verify(case.root).static_refs_pass
        assert inventory_main(["--repo-root", str(case.root)]) == 1

    @pytest.mark.parametrize("bad", [0, 1, "False", None])
    def test_required_field_must_be_a_json_boolean(self, reference_gate_case, bad):
        case = reference_gate_case
        case.inventory["mechanisms"][0]["required"] = bad
        case.write()
        assert not verify(case.root).static_refs_pass

    @pytest.mark.parametrize(
        "text", ["[]", '{"sources":[],"sources":[]}', '{"sources":[],"n":NaN}']
    )
    def test_malformed_json_cannot_pass(self, reference_gate_case, text):
        path = reference_gate_case.root / "sources_manifest.json"
        path.write_text(text)
        assert inventory_main(["--repo-root", str(reference_gate_case.root)]) == 1


class TestR16ExternalDemBinding:
    """R16：任意状态标签数不是source三维DEM证据；实际binding必须改变真实mask。"""

    def test_explicit_equal_unit_rotation_changes_masks_but_conserves_nominal_charge(
        self, nominal_model
    ):
        before = nominal_model.simulate(dc_input(0.31), 32).raw
        base_binding = before.pool.mask_binding

        def declared_rotation(geometry, codes, states, slice_ids):
            base = base_binding(geometry, codes, np.zeros_like(states), slice_ids)
            main, sub = base.main_on.copy(), base.sub_on.copy()
            for index, state in enumerate(states):
                main[index] = np.roll(main[index], int(state), axis=-1)
                sub[index] = np.roll(sub[index], int(state), axis=-1)
            return SplitSwitchCommand(main, sub)

        states = np.arange(32, dtype=np.int64) % 2
        rotated = _replay(nominal_model, before, binding=declared_rotation, states=states, dem=True)
        assert np.any(before.main_masks != rotated.main_masks)
        assert np.array_equal(before.main_masks.sum(-1), rotated.main_masks.sum(-1))
        assert np.array_equal(before.sub_masks.sum(-1), rotated.sub_masks.sum(-1))
        assert np.allclose(before.vra, rotated.vra, rtol=0, atol=1e-12)
        assert not np.array_equal(
            before.digital_observation.known_voltage_term_v,
            rotated.digital_observation.known_voltage_term_v,
        )
        assert (
            "not independently proved source mapping"
            in rotated.binding_metadata["source_alignment"]
        )


class TestR17CommonModeTau:
    """R17：实际共享Rs网络的阶跃不能用全部按slice缩小的错误tau代替。"""

    def test_star_network_matches_the_independent_common_mode_solution(self):
        n, rs, ron, c_total = 8, 30.0, 20.0, 20.5e-12
        c_slice, duration = c_total / n, 0.5e-9
        network = PassiveTrackingNetwork(np.full(n, c_slice), np.full(n, ron), rs, 0.0)
        actual = network.advance(np.zeros(n), 0.0, duration, dc_input(1.0))
        correct_tau = rs * c_total + ron * c_slice
        expected = 1 - np.exp(-duration / correct_tau)
        wrong = 1 - np.exp(-duration / ((rs + ron) * c_slice))
        assert np.allclose(actual, expected, rtol=0, atol=1e-13)
        assert np.max(np.abs(actual - wrong)) > 0.2


class TestR18RemovedObserverCannotBeEnabled:
    """R18旧Wiener/KTC专属模型退役，不借测试需求恢复被删除的电路假设。"""

    @pytest.mark.parametrize("field", ["ktc_enable", "kappa", "beta_error", "ktc_gain_n"])
    def test_deleted_observer_parameters_fail_at_the_real_input_boundary(self, field):
        with pytest.raises(TypeError, match=field):
            Config.from_dict({field: 1})
