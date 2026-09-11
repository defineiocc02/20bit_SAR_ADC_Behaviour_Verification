"""Unit tests for the machine-checkable provenance layer.

These tests exercise the runtime enforcement of the
[披露]/[拟合]/[假设]/[推导] convention: a value tagged ``ASSUMED`` is
rejected when used in a quote-only context, and a value tagged
``FITTED`` is rejected when used as a prediction. The audit
demonstrated that this convention was being honoured in the breach;
the only fix is a structural one.
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
    """Every Config field must carry a declared grade; missing is a test
    failure rather than a silent drift.
    """

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
    """`annotate_config` is the workhorse for downstream verification."""

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
        """A Config field missing from PARAM_GRADES must surface as ASSUMED,
        not be silently dropped or treated as DISCLOSED.
        """
        ann = annotate_config(cfg)
        ungraded = {k: v for k, v in ann.items() if v.source == "UNGRADED"}
        for v in ungraded.values():
            assert v.grade is SourceGrade.ASSUMED


class TestAuditProvenance:
    """`audit_provenance` is the first thing a reviewer reads. It must
    surface anything that the headline number depends on.
    """

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
        """The default config has at least fs / g0 / c_total0 / n_slices / n_active
        / n_bits_target / target_dr_db declared as DISCLOSED. Anything below
        that is a regression in the grade table.
        """
        audit = audit_provenance(cfg)
        assert audit["counts"][SourceGrade.DISCLOSED.value] >= 7

    def test_research_extension_is_opt_in(self, cfg):
        """KTC must remain a research extension. Its fields must be graded
        RESEARCH_EXTENSION, not DERIVED or DISCLOSED — that is the whole
        reason we have a separate grade for it.
        """
        ann = annotate_config(cfg)
        for field in ("ktc_enable", "ktc_gain_n", "ktc_kappa"):
            assert ann[field].grade is SourceGrade.RESEARCH_EXTENSION, (
                f"{field} must be RESEARCH_EXTENSION, not "
                f"{ann[field].grade.name}; the audit specifically flagged "
                f"that KTC had been quietly allowed to look like a "
                f"disclosed capability."
            )

    def test_assumed_in_use_contains_pdk_sigma(self, cfg):
        """pdk_sigma_est_ppm has a non-zero default — it must appear in
        `assumed_in_use`, which is the set of assumptions that can move a
        headline number.
        """
        audit = audit_provenance(cfg)
        assert "pdk_sigma_est_ppm" in audit["assumed_in_use"]

    def test_assumed_in_use_excludes_zero_defaults(self, cfg):
        """A modelling switch that defaults to False (sadc_mismatch_enable)
        is not 'in use'; it should not appear in assumed_in_use.
        """
        audit = audit_provenance(cfg)
        assert "sadc_mismatch_enable" not in audit["assumed_in_use"]

    def test_ungraded_is_empty_by_construction(self, cfg):
        """PARAM_GRADES is supposed to cover every Config field. If this
        fails, someone added a field and forgot to grade it.
        """
        audit = audit_provenance(cfg)
        assert audit["ungraded"] == [], (
            f"Ungraded fields: {audit['ungraded']}. "
            f"Add a grade entry to PARAM_GRADES in provenance.py."
        )

    def test_fitted_in_use_lists_active_values(self, cfg):
        """A fitted parameter is 'in use' when it currently moves a number.

        ``None`` is **not** the same as "switched off": for a sentinel field it
        means a resolver supplies the value at runtime. Filtering on
        ``value is not None`` therefore hid ``ra_out_noise_rms`` — the model's
        most load-bearing fit, active in the default configuration — from the
        one report a reviewer is told to read first (external review,
        2026-09-11). A sentinel is in use exactly when it reports a resolved
        value; a plain ``None`` or ``False`` is still not in use.
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
        """A changed value must not keep quoting its original source.

        ``fs`` is graded DISCLOSED with source "[00] abstract: 40 MS/s"; setting
        ``fs=80e6`` leaves that string untouched, so the override has to be
        reported separately. Otherwise a reader sees a disclosed value whose
        stated provenance no longer describes it.
        """
        assert "fs" not in audit_provenance(cfg)["overridden"]
        report = audit_provenance(Config(fs=80e6))
        assert "fs" in report["overridden"]
