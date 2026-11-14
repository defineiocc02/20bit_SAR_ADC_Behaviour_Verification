"""检查数值来源权威性、显式未知项及依赖项的来源分级。"""

from dataclasses import dataclass, fields, replace

import pytest

from adi_model.provenance import (
    PARAM_GRADES,
    PARAM_UNITS,
    SOURCE_VALUES,
    Graded,
    GradingError,
    SourceGrade,
    annotate_config,
    audit_provenance,
    grade_of,
)
from adi_model.simulation.config import Config


class TestSourceGrade:
    def test_quotable_property_matches_docstring(self):
        assert {g for g in SourceGrade if g.quotable} == {
            SourceGrade.DISCLOSED,
            SourceGrade.DERIVED,
        }

    def test_label_zh_round_trip(self):
        assert [g.label_zh for g in SourceGrade] == [
            "[披露]",
            "[推导]",
            "[拟合]",
            "[假设]",
            "[研究扩展]",
        ]


class TestGraded:
    def test_require_accepts_bound_allowed_value(self):
        value = Graded(1.23e-6, SourceGrade.DISCLOSED, "explicit source fixture", unit="V")
        assert value.require(SourceGrade.DISCLOSED) == 1.23e-6

    def test_require_rejects_unlisted_effective_grade(self):
        value = Graded(3.0, SourceGrade.ASSUMED, "explicit engineering fixture", unit="F")
        with pytest.raises(GradingError, match="ASSUMED"):
            value.require(SourceGrade.DISCLOSED, SourceGrade.DERIVED)

    @pytest.mark.parametrize("grade", list(SourceGrade))
    def test_unbound_value_never_becomes_quotable(self, grade):
        with pytest.raises(GradingError, match="unbound"):
            Graded(None, grade, "no component value supplied").require(grade)

    @pytest.mark.parametrize(
        "grade", [SourceGrade.ASSUMED, SourceGrade.FITTED, SourceGrade.RESEARCH_EXTENSION]
    )
    def test_map_preserves_dependency_authority(self, grade):
        source = Graded(2.0, SourceGrade.DISCLOSED, "explicit voltage", unit="V")
        dependency = Graded(3.0, grade, "explicit ratio", unit="ratio")
        product = source.map(
            lambda x: x * dependency.value,
            source="V times ratio",
            unit="V",
            dependencies=(dependency,),
        )
        assert product.value == 6.0 and product.unit == "V"
        assert product.grade is SourceGrade.DERIVED
        assert product.effective_grade is grade
        with pytest.raises(GradingError):
            product.require(SourceGrade.DISCLOSED, SourceGrade.DERIVED)

    def test_map_requires_explicit_formula_units_and_dependencies(self):
        value = Graded(2.0, SourceGrade.DISCLOSED, "input", unit="V")
        with pytest.raises(TypeError):
            value.map(lambda x: 2 * x)
        mapped = value.map(
            lambda x: 2 * x, source="multiply by exact two", unit="V", dependencies=()
        )
        assert mapped.value == 4.0 and mapped.effective_grade is SourceGrade.DERIVED

    def test_map_refuses_an_unbound_dependency(self):
        value = Graded(2.0, SourceGrade.DISCLOSED, "input", unit="V")
        dependency = Graded(None, SourceGrade.ASSUMED, "missing ratio")
        with pytest.raises(GradingError, match="unbound"):
            value.map(
                lambda x: x, source="incomplete calculation", unit="V", dependencies=(dependency,)
            )


class TestParamGradesCoverage:
    def test_registered_fields_and_units_equal_the_actual_schema(self):
        expected = {f.name for f in fields(Config)}
        assert set(PARAM_GRADES) == set(PARAM_UNITS) == expected
        assert all(origin.strip() for _, origin in PARAM_GRADES.values())
        assert all(unit.strip() for unit in PARAM_UNITS.values())

    def test_grade_of_unknown_raises(self):
        with pytest.raises(KeyError):
            grade_of("undeclared_component_value")


class TestAnnotateConfig:
    def test_literal_source_architecture_is_quotable(self):
        annotated = annotate_config(Config())
        for name, value in SOURCE_VALUES.items():
            assert annotated[name].require(SourceGrade.DISCLOSED) == value
        assert annotated["b1"].value == 9

    def test_unpublished_supplied_values_remain_engineering_inputs(self, configuration_inputs):
        annotated = annotate_config(configuration_inputs)
        for name in ("dac_n_main", "adc2_n_bits", "adc2_v_min", "ra_v_clip"):
            assert annotated[name].effective_grade is SourceGrade.ASSUMED
            with pytest.raises(GradingError):
                annotated[name].require(SourceGrade.DISCLOSED)

    def test_override_of_a_source_value_is_demoted(self):
        item = annotate_config(Config(fs=80e6))["fs"]
        assert item.overridden and item.grade is SourceGrade.DISCLOSED
        assert item.effective_grade is SourceGrade.ASSUMED
        with pytest.raises(GradingError):
            item.require(SourceGrade.DISCLOSED)

    def test_unknown_dataclass_field_is_visible_not_source_backed(self):
        @dataclass
        class Unknown:
            component: float = 3.0

        item = annotate_config(Unknown())["component"]
        assert item.source == "UNREGISTERED" and item.effective_grade is SourceGrade.ASSUMED
        assert audit_provenance(Unknown())["ungraded"] == ["component"]
        assert audit_provenance(Unknown())["verdict"].startswith("FAIL")


class TestAuditProvenance:
    def test_incomplete_configuration_keeps_unbound_inputs(self):
        cfg = Config()
        before = cfg.to_dict()
        audit = audit_provenance(cfg)
        assert cfg.to_dict() == before
        assert {"ra_out_noise_rms", "adc2_n_bits", "sadc_thresholds_v"} <= set(audit["unbound"])
        assert not audit["fitted_supplied"]
        assert cfg.ra_out_noise_rms is None

    def test_counts_cover_schema_without_legacy_fields(self):
        audit = audit_provenance(Config())
        assert sum(audit["counts"].values()) == len(fields(Config))
        assert not audit["ungraded"]
        assert "ktc_enable" not in PARAM_GRADES and "target_dr_db" not in PARAM_GRADES

    def test_supplied_noise_is_not_reverse_fitted(self, configuration_inputs):
        cfg = replace(
            configuration_inputs,
            ra_enable_noise=True,
            ra_out_noise_rms=0.0032,
            ra_noise_distribution="gaussian_white",
            seed=9,
        )
        audit = audit_provenance(cfg)
        assert "ra_out_noise_rms" in audit["assumed_supplied"]
        assert not audit["fitted_supplied"]
        assert cfg.ra_out_noise_rms == 0.0032

    def test_registered_origin_report_is_not_a_consumer_validity_gate(self):
        cfg = Config()
        report = audit_provenance(cfg)
        assert "consumer validity checked separately" in report["verdict"]
        assert cfg.legality_violations()
