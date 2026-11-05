"""检查可机器执行的参数来源分级。

披露、推导、拟合及假设等级参与运行时 require：ASSUMED 不能用于仅允许引用
披露值的场合，FITTED 不能冒充独立预测。测试检查约束实际执行，而不只检查标签。
"""

from __future__ import annotations

import pytest

from adi_model.config import Config
from adi_model.provenance import (
    PARAM_GRADES,
    Graded,
    GradingError,
    SourceGrade,
    annotate_config,
    audit_provenance,
    grade_of,
)


class TestSourceGrade:
    def test_quotable_property_matches_docstring(self):
        assert SourceGrade.DISCLOSED.quotable is True
        assert SourceGrade.DERIVED.quotable is True
        assert SourceGrade.FITTED.quotable is False
        assert SourceGrade.ASSUMED.quotable is False
        assert SourceGrade.RESEARCH_EXTENSION.quotable is False

    def test_label_zh_round_trip(self):
        # The Chinese label vocabulary must remain stable across versions
        # because downstream docs and papers cite it.
        assert SourceGrade.DISCLOSED.label_zh == "[披露]"
        assert SourceGrade.DERIVED.label_zh == "[推导]"
        assert SourceGrade.FITTED.label_zh == "[拟合]"
        assert SourceGrade.ASSUMED.label_zh == "[假设]"
        assert SourceGrade.RESEARCH_EXTENSION.label_zh == "[研究扩展]"


class TestGraded:
    def test_require_accepts_listed(self):
        g = Graded(1.23e-6, SourceGrade.DERIVED, "2*v_fs/2**20")
        assert g.require(SourceGrade.DISCLOSED, SourceGrade.DERIVED) == pytest.approx(1.23e-6)

    def test_require_rejects_unlisted(self):
        g = Graded(1117e-6, SourceGrade.ASSUMED, "Pelgrom area law")
        with pytest.raises(GradingError) as ei:
            g.require(SourceGrade.DISCLOSED, SourceGrade.DERIVED)
        # The error message must contain the grade label and the source so
        # that a reviewer can locate it.
        msg = str(ei.value)
        assert "ASSUMED" in msg or "[假设]" in msg
        assert "Pelgrom" in msg

    def test_map_preserves_grade_and_source(self):
        g = Graded(1.0, SourceGrade.DERIVED, "identity")
        doubled = g.map(lambda v: 2.0 * v)
        assert doubled.value == pytest.approx(2.0)
        assert doubled.grade is SourceGrade.DERIVED
        assert doubled.source == "identity"

    def test_require_disclosed_versus_assumed(self):
        # Audit's central mistake: ASSUMED numbers quoted as if DISCLOSED.
        lsb = Graded(5.722e-6, SourceGrade.DERIVED, "2*v_fs/2**20")
        sigma_pdk = Graded(1117e-6, SourceGrade.ASSUMED, "Pelgrom, area-aware")
        # Both can be carried through the simulation; only the first is
        # quotable as a number-about-the-target-chip.
        assert lsb.require(SourceGrade.DISCLOSED, SourceGrade.DERIVED)
        with pytest.raises(GradingError):
            sigma_pdk.require(SourceGrade.DISCLOSED, SourceGrade.DERIVED)


class TestParamGradesCoverage:
    """检查每个 Config 字段都有来源等级，避免缺项静默通过。"""

    def test_param_grades_nonempty(self):
        assert len(PARAM_GRADES) > 60  # current is 84

    def test_param_grades_key_matches_field_name(self):
        import dataclasses

        from adi_model import Config

        field_names = {f.name for f in dataclasses.fields(Config())}
        graded = set(PARAM_GRADES.keys())
        # Allow Config fields to exist without a grade (we want to *detect*
        # additions via test failure), so check the inverse: every graded
        # key must correspond to a real field.
        assert (
            graded <= field_names
        ), f"PARAM_GRADES references unknown fields: {graded - field_names}"

    def test_grade_of_unknown_raises(self):
        with pytest.raises(KeyError):
            grade_of("__no_such_field__")


class TestAnnotateConfig:
    """检查 annotate_config 向下游提供的分级及引用约束。"""

    def test_returns_field_grade_mapping(self, cfg):
        ann = annotate_config(cfg)
        assert "fs" in ann
        assert "n_bits_target" in ann
        assert "v_fs" in ann
        assert "ktc_enable" in ann
        # Every entry is a Graded instance.
        for k, v in ann.items():
            assert isinstance(v, Graded), f"{k} returned {type(v).__name__}"

    def test_known_disclosed_field_is_quotable(self, cfg):
        ann = annotate_config(cfg)
        # `fs` is graded DISCLOSED — must accept it as a quotable number.
        ann["fs"].require(SourceGrade.DISCLOSED, SourceGrade.DERIVED)

    def test_assumed_field_is_not_quotable(self, cfg):
        ann = annotate_config(cfg)
        # pdk_sigma_est_ppm is ASSUMED.
        with pytest.raises(GradingError):
            ann["pdk_sigma_est_ppm"].require(SourceGrade.DISCLOSED, SourceGrade.DERIVED)

    def test_ungraded_field_defaults_to_assumed(self, cfg):
        """检查未登记字段显式归入 ASSUMED，不丢弃或按 DISCLOSED 处理。"""
        ann = annotate_config(cfg)
        ungraded = {k: v for k, v in ann.items() if v.source == "UNGRADED"}
        for v in ungraded.values():
            assert v.grade is SourceGrade.ASSUMED


class TestAuditProvenance:
    """检查来源报告显示影响主要结果的拟合、假设与覆盖项。"""

    def test_returns_known_keys(self, cfg):
        audit = audit_provenance(cfg)
        # Structural keys (current API):
        for k in ("counts", "ungraded", "assumed_in_use", "fitted_in_use", "verdict"):
            assert k in audit, f"audit_provenance() must expose '{k}'"

    def test_counts_breakdown_includes_all_known_grades(self, cfg):
        audit = audit_provenance(cfg)
        c = audit["counts"]
        # All SourceGrade values must appear in the counts (zero is fine).
        for g in SourceGrade:
            assert g.value in c, f"counts missing grade {g.value}"

    def test_disclosed_is_nonempty(self, cfg):
        """检查默认 fs、g0、c_total0、n_slices、n_active、n_bits_target、target_dr_db 的披露声明覆盖。"""
        audit = audit_provenance(cfg)
        assert audit["counts"][SourceGrade.DISCLOSED.value] >= 7

    def test_research_extension_is_opt_in(self, cfg):
        """检查 KTC 字段保持 RESEARCH_EXTENSION，不被标为推导或披露。"""
        ann = annotate_config(cfg)
        for field in ("ktc_enable", "ktc_gain_n", "ktc_kappa"):
            assert ann[field].grade is SourceGrade.RESEARCH_EXTENSION, (
                f"{field} must be RESEARCH_EXTENSION, not "
                f"{ann[field].grade.name}; the audit specifically flagged "
                f"that KTC had been quietly allowed to look like a "
                f"disclosed capability."
            )

    def test_assumed_in_use_contains_pdk_sigma(self, cfg):
        """检查非零默认 pdk_sigma_est_ppm 出现在实际使用的假设集合。"""
        audit = audit_provenance(cfg)
        assert "pdk_sigma_est_ppm" in audit["assumed_in_use"]

    def test_assumed_in_use_excludes_zero_defaults(self, cfg):
        """检查默认关闭的模型开关不被列为正在使用的假设。"""
        audit = audit_provenance(cfg)
        assert "sadc_mismatch_enable" not in audit["assumed_in_use"]

    def test_ungraded_is_empty_by_construction(self, cfg):
        """检查 PARAM_GRADES 覆盖全部 Config 字段。"""
        audit = audit_provenance(cfg)
        assert audit["ungraded"] == [], (
            f"Ungraded fields: {audit['ungraded']}. "
            f"Add a grade entry to PARAM_GRADES in provenance.py."
        )

    def test_fitted_in_use_lists_active_values(self, cfg):
        """检查运行时解析的拟合值出现在实际使用的拟合集合。

        None 哨兵不等于关闭：ra_out_noise_rms 会由解析器提供数值，旧 value is not None
        过滤曾隐藏该默认拟合。普通未解析 None 或 False 仍不视为使用中。
        """
        audit = audit_provenance(cfg)
        ann = annotate_config(cfg)
        for name in audit["fitted_in_use"]:
            entry = ann[name]
            if entry.value is None:
                assert (
                    entry.note
                ), f"{name} is a sentinel listed as in use but reports no resolved value"
            else:
                assert entry.value is not False

        # The default RA-noise fit is raised on sentinel resolution and must show.
        assert "ra_out_noise_rms" in audit["fitted_in_use"]
        assert audit["resolved_sentinels"], "no resolved sentinel was reported"

    def test_overridden_disclosed_values_are_flagged(self, cfg):
        """检查 fs 从 [00] 披露的 40 MS/s 覆盖为 80e6 时，报告单独识别覆盖，不能沿用原值权威性。"""
        assert "fs" not in audit_provenance(cfg)["overridden"]
        report = audit_provenance(Config(fs=80e6))
        assert "fs" in report["overridden"]
