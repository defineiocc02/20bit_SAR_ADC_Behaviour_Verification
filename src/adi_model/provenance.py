"""Machine-checkable source grading for every number the model produces.

Why this module exists
----------------------
Earlier versions of this model carried a documentation convention: each
parameter was tagged ``[披露]`` / ``[拟合]`` / ``[假设]`` in a comment. The
convention was honoured in the breach — an external audit of v6.1 found that
numbers produced from *fitted* and *assumed* parameters were being reported
alongside numbers derived from the published literature, with no way for a
reader to tell them apart, and no way for a test to enforce the distinction.

A comment cannot be tested. This module turns the convention into a runtime
value so that:

* every public quantity can carry its provenance with it (``Graded``);
* every ``Config`` field has a declared grade in :data:`PARAM_GRADES`, and a
  test fails if a new field is added without one;
* a report (:func:`audit_provenance`) lists exactly which headline numbers rest
  on assumptions, which is the first thing a reviewer should read.

Grades
------
``DISCLOSED``
    Transcribed from [00] (ISSCC 2024 digest) or [00_1] (slides). Quotable as
    "the paper says".
``DERIVED``
    Obtained by algebra from ``DISCLOSED`` values, with no free parameter.
    Quotable with the derivation shown.
``FITTED``
    Chosen so that the model reproduces a published figure. **A result obtained
    with a fitted parameter is not a prediction of that figure** — it is a
    consistency check that the architecture, as modelled, is compatible with
    it.
``ASSUMED``
    No published source. Sensitivity studies only; must never appear in a
    yield, area, power or performance claim about the target chip.
``RESEARCH_EXTENSION``
    An original mechanism added by this repository that is *not* disclosed in
    the literature being modelled. Must never be attributed to the original
    authors.

Example:
-------
>>> from adi_model.provenance import Graded, SourceGrade
>>> lsb = Graded(5.722e-6, SourceGrade.DERIVED, "2*v_fs/2**20", "LSB at 20 bit")
>>> lsb.grade is SourceGrade.DERIVED
True
>>> lsb.require(SourceGrade.DISCLOSED, SourceGrade.DERIVED)  # doctest: +ELLIPSIS
5.722e-06
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

__all__ = [
    "PARAM_GRADES",
    "Graded",
    "GradingError",
    "SourceGrade",
    "annotate_config",
    "audit_provenance",
    "grade_of",
]

T = TypeVar("T")


class SourceGrade(enum.Enum):
    """Provenance of a numerical value. See module docstring."""

    DISCLOSED = "disclosed"
    DERIVED = "derived"
    FITTED = "fitted"
    ASSUMED = "assumed"
    RESEARCH_EXTENSION = "research_extension"

    @property
    def quotable(self) -> bool:
        """True if a value of this grade may be quoted as fact in a paper.

        ``FITTED`` and ``ASSUMED`` are deliberately excluded: a fitted value
        restates its own target, and an assumed value has no source.

        Returns:
            True 若该等级可当事实引用（仅 DISCLOSED / DERIVED）；
            False 若 FITTED / ASSUMED / RESEARCH_EXTENSION。
        """
        return self in (SourceGrade.DISCLOSED, SourceGrade.DERIVED)

    @property
    def label_zh(self) -> str:
        """Chinese label matching the vocabulary used in earlier versions.

        Returns:
            中文来源标签字符串：DISCLOSED→"[披露]"，DERIVED→"[推导]"，
            FITTED→"[拟合]"，ASSUMED→"[假设]"，RESEARCH_EXTENSION→"[研究扩展]"。
        """
        return {
            SourceGrade.DISCLOSED: "[披露]",
            SourceGrade.DERIVED: "[推导]",
            SourceGrade.FITTED: "[拟合]",
            SourceGrade.ASSUMED: "[假设]",
            SourceGrade.RESEARCH_EXTENSION: "[研究扩展]",
        }[self]


class GradingError(RuntimeError):
    """Raised when a value is used at a grade that does not permit that use."""


@dataclass(frozen=True)
class Graded(Generic[T]):
    """A value bundled with its provenance.

    Attributes:
        value: The quantity itself, in the unit documented by ``unit``.
        grade: Where the number comes from.
        source: The reference, derivation or reason. Free text; for
            ``DISCLOSED`` this should be a document and page/slide number.
        note: Optional clarification, e.g. domain-of-applicability caveats.
            Also carries the runtime value of a sentinel field (see
            ``RESOLVED_SENTINELS``), so a ``None`` placeholder cannot look like
            a switched-off parameter.
        unit: Unit string, e.g. ``"V"``, ``"F"``, ``"LSB@20b"``, ``"dB"``.
        overridden: True when the field's *current value* no longer equals its
            stock default while ``grade``/``source`` still describe the
            original declaration. ``fs=80e6`` on a field sourced from
            "[00] abstract: 40 MS/s" is the canonical case: that sentence is
            still true *about the paper* and false about the number in use.
            :meth:`require` refuses such a value at a ``DISCLOSED`` call site.
            Without this flag the grading was attached to the field *name*, so
            a hand-edited number inherited the paper's authority (external
            review, 2026-09-11).
    """

    value: T
    grade: SourceGrade
    source: str
    note: str = ""
    unit: str = ""
    overridden: bool = False

    @property
    def effective_grade(self) -> SourceGrade:
        """The grade that governs use of the current value.

        A ``DISCLOSED``/``DERIVED`` number that has been overridden no longer
        has evidence at its declared grade, so it is demoted to ``ASSUMED`` —
        no published source covers *this* value; sensitivity studies only.
        Every other grade is returned unchanged.

        Returns:
            SourceGrade: 当前值真正可以主张的等级。
        """
        if self.overridden and self.grade in (SourceGrade.DISCLOSED, SourceGrade.DERIVED):
            return SourceGrade.ASSUMED
        return self.grade

    def require(self, *allowed: SourceGrade) -> T:
        """Return the value, or raise if it may not be used here.

        This is the enforcement hook. Call it at every point where a number is
        about to be used in a claim, so that an assumption silently
        propagating into a performance claim becomes a test failure instead of
        a correction after publication.

        The check is against :attr:`effective_grade`, not ``grade``: a value
        that has been changed away from the one its source documents cannot be
        quoted as though the source still covered it.

        Args:
            *allowed: Grades acceptable at this call site.

        Returns:
            The wrapped value.

        Raises:
            GradingError: If :attr:`effective_grade` is not in ``allowed``.
        """
        if self.effective_grade not in allowed:
            why = (
                f" (declared {self.grade.name} from {self.source!r}, but the current "
                f"value differs from the stock default so it no longer inherits that source)"
                if self.effective_grade is not self.grade
                else ""
            )
            raise GradingError(
                f"value graded {self.effective_grade.name} ({self.effective_grade.label_zh}) from "
                f"{self.source!r} is not valid here; allowed: "
                f"{[g.name for g in allowed]}{why}"
            )
        return self.value

    def map(self, fn: Any) -> Graded[Any]:
        """Apply ``fn`` to the value, preserving grade and source.

        Returns:
            新的 Graded，value=fn(self.value)，grade/source/note/unit/overridden 沿用。

        Args:
            fn: 作用于 value 的映射函数（callable）。若换算了单位，
                调用方需自行更新返回对象的 unit 字段。

        """
        return Graded(
            fn(self.value),
            self.grade,
            self.source,
            self.note,
            self.unit,
            self.overridden,
        )

    def __repr__(self) -> str:
        """Readable form including the grade, e.g. ``Graded(4.0, 披露, '论文')``.

        Returns:
            形如 "Graded(<value>, <中文等级>, <source>[, unit=...][, OVERRIDDEN])" 的字符串。
        """
        return (
            f"Graded({self.value!r}, {self.grade.label_zh}, {self.source!r}"
            + (f", unit={self.unit!r}" if self.unit else "")
            + (", OVERRIDDEN" if self.overridden else "")
            + ")"
        )


# --------------------------------------------------------------------------
# Config parameter grading table.
#
# Rule enforced by tests/unit/test_provenance.py: every field of Config must appear
# here. Adding a Config field without grading it is a test failure, which is
# the entire point — an ungraded parameter is how fitted numbers start
# masquerading as disclosed ones.
# --------------------------------------------------------------------------
_G = SourceGrade.DISCLOSED
_D = SourceGrade.DERIVED
_F = SourceGrade.FITTED
_A = SourceGrade.ASSUMED
_X = SourceGrade.RESEARCH_EXTENSION

PARAM_GRADES: dict[str, tuple[SourceGrade, str]] = {
    # ---- system / target -------------------------------------------------
    "fs": (_G, "[00] abstract: 40 MS/s"),
    "n_bits_target": (_G, "[00] abstract: 20 bit"),
    "v_fs": (_D, "2.111 Vrms * sqrt(2) from NSD 8.8nV/rtHz and -167.6dBFS/Hz [00_1]"),
    "target_dr_db": (_G, "[00_1] low-frequency performance slide (94.6 dB DR)"),
    "seed": (_A, "reproducibility only, no physical meaning"),
    # ---- first stage -----------------------------------------------------
    "b1": (_A, "ARCHITECTURAL READING, see docs/adr/0003-stage-1-resolution.md"),
    "stage1_reading": (_G, "[00]: '...resulting in 9b quantization in the first stage'"),
    "c_sadc": (_A, "quantizer slice sampling cap; not disclosed"),
    "sadc_offset": (_A, "default 0 = ideal"),
    "sadc_rdac_gain_mismatch": (_F, "set to be consistent with >11b matching [00]"),
    "sampling_tau_mismatch": (_A, "no published number; mechanism from [09]"),
    "sadc_mismatch_enable": (_A, "modelling switch"),
    "sadc_mismatch_sigma": (_A, "no published number"),
    # ---- residue amplifier -----------------------------------------------
    "g0": (_G, "[00_1] figure annotation: G0 = 32"),
    "gain_error": (_A, "default 0 = nominal"),
    "ra_gain_model": (_D, "charge-consistent definition, see charge_ref.py"),
    "ra_out_noise_rms": (_F, "None => reverse-solved from target_dr_db (ANCHOR, not prediction)"),
    "ra_v_clip": (_A, "chosen to match adc2 range; not disclosed"),
    "ra_enable_noise": (_A, "modelling switch"),
    # ---- slice pool ------------------------------------------------------
    "n_slices": (_G, "[00]: pool of 18 sDAC"),
    "n_active": (_G, "[00]: 8 converting + 8 acquiring"),
    "n_unit_per_slice": (_D, "512 RDAC units / 8 active slices; 8x8 matches 3b+3b DEM"),
    "n_units_headroom": (_A, "dither headroom; not disclosed"),
    # ---- capacitor array -------------------------------------------------
    "c_total0": (_G, "[00_1]: 20.5 pF RDAC"),
    "cap_scale": (_A, "exploration knob for the capacitor-shrinking study"),
    "chi": (_D, "2 = two independent differential halves"),
    "mismatch_enable": (_A, "modelling switch"),
    "mismatch_sigma0": (_F, "100 ppm behavioural calibration; NOT a PDK value"),
    "mismatch_split": (_A, "variance partition; not disclosed"),
    "mismatch_gradient": (_A, "no published number"),
    "pdk_sigma_est_ppm": (_A, "Pelgrom-style area-law estimate; not a PDK measurement"),
    "c_feedback0": (_D, "c_total0 / g0"),
    "split_feedback_cap_f": (_A, "explicit split feedback override [F]; None derives C_sig_nom/g0"),
    # ---- backend ADC -----------------------------------------------------
    "adc2_n_bits": (_D, "derived in Config.paper_consistent() from Delta1 and LSB20·G0"),
    "adc2_v_min": (_D, "-0.10 * G0 * Delta1 (residue span with ~10% margin)"),
    "adc2_v_max": (_D, "+1.10 * G0 * Delta1 (residue span with ~10% margin)"),
    # ---- DEM / dither ----------------------------------------------------
    "dem_enable": (_A, "modelling switch"),
    "dem_mode": (_A, "algorithm choice, not disclosed"),
    "dither_mode": (_A, "modelling switch"),
    "dither_amplitude_lsb1": (_A, "not disclosed; '2b enhancement' has no number"),
    "dither_units_range": (_A, "not disclosed"),
    "dither_split_bank": (_A, "topology choice for split DAC only"),
    "dither_discrete": (_A, "realisability study"),
    "dither_quant_transfer": (_A, "reading of 'transferred from quantizer to RDAC' [00]"),
    "dither_transfer_model": (
        _G,
        "[00]: 'range enhanced by 2b when transferred from quantizer to RDAC'",
    ),
    "dither_enhancement_bits": (_G, "[00]: '2b enhancement' on the RDAC side"),
    "dither_changes_in_window": (_A, "modelling switch"),
    # ---- KTC: original research extension --------------------------------
    "ktc_enable": (_X, "NOT in [00] or [09]-[14]; original research extension"),
    "ktc_gain_n": (_X, "observer gain, free design parameter of the extension"),
    "ktc_kappa": (_X, "observer scale, free design parameter of the extension"),
    "ktc_beta_error": (_X, "observer mismatch, free parameter of the extension"),
    "ktc_noise_n": (_X, "observer self-noise; 0 = ideal observer (unrealistic)"),
    "ktc_observe_bw_hz": (_X, "None = infinite bandwidth (unrealistic)"),
    "ktc_dt_fraction": (_X, "observation window; sets the bandwidth requirement"),
    "ktc_sub_obs_fraction": (_X, "observation coverage gamma of the extension"),
    # ---- calibration -----------------------------------------------------
    "calibration": (_A, "algorithm choice"),
    # ---- DAC topology ----------------------------------------------------
    "dac_arch": (_A, "'unary' is a modelling simplification, not the shipped DAC"),
    "dac_n_main": (_A, "segmentation study"),
    "dac_n_sub": (_A, "segmentation study"),
    "dac_parasitic_ratio": (_A, "no published number"),
    "dac_parasitic_spread": (_A, "no published number"),
    "dac_bridge_mismatch_sigma": (_A, "no published number"),
    # ---- dynamics: all assumed -------------------------------------------
    "dyn_input_settling": (_A, "modelling switch"),
    "dyn_r_source": (_A, "no published number"),
    "dyn_r_on": (_A, "no published number"),
    "dyn_t_sample_frac": (_A, "no published number"),
    "dyn_ron_code_coeff": (_A, "no published number"),
    "dyn_ref_settling": (_A, "modelling switch"),
    "dyn_c_decouple": (_A, "no published number"),
    "dyn_tau_ref": (_A, "no published number"),
    "dyn_t_conv_frac": (_A, "no published number"),
    "dyn_ref_dynamic_ratio": (_A, "no published number"),
    "dyn_crosstalk": (_A, "modelling switch"),
    "dyn_c_xtalk_common": (_A, "no published number"),
    "dyn_c_xtalk_unit": (_A, "no published number"),
    "dyn_v_digital": (_A, "no published number"),
    # ---- interleaving ----------------------------------------------------
    "slice_bw_spread": (_A, "no published number"),
    "slice_timing_skew_s": (_A, "[00] reports ~0.6 ps residual, but as a design outcome"),
    "slice_offset_sigma_v": (_A, "no published number"),
    "sadc_cap_ratio": (_A, "no published number"),
    # ---- RA / ADC2 second-order ------------------------------------------
    "ra_autozero": (_A, "modelling switch"),
    "ra_autozero_cost_db": (_G, "[00_1] p.34-35: -1.6 dB"),
    "adc2_dyn_bw_ratio": (_D, "10**(-1.3/20) from +1.3 dB [00_1] p.33-35"),
    "flicker_corner_hz": (_A, "[00_1] quotes ~40 Hz corner when enabled"),
    "flicker_white_ratio": (_A, "no published number"),
    "rdac_bitwise_loading": (_A, "modelling switch"),
    "rdac_bitwise_bits": (_A, "assumed equal to first-stage bits"),
    "enable_sampling_noise": (_A, "modelling switch"),
    "driver_noise_rms": (_A, "system-level study, not part of the ADC"),
}


def grade_of(name: str) -> tuple[SourceGrade, str]:
    """Return the declared ``(grade, source)`` of a Config field.

    Args:
        name: Field name of :class:`~adi_model.config.Config`.

    Returns:
        Tuple of grade and source string.

    Raises:
        KeyError: If the field has no declared grade. Callers should treat this
            as a build-breaking condition; it is what stops an ungraded
            parameter from silently becoming a "disclosed" one.
    """
    return PARAM_GRADES[name]


# --------------------------------------------------------------------------
# Sentinel-valued fields: ``None`` means "a resolver supplies the number", not
# "this contribution is switched off".
#
# This mapping exists because an external review (2026-09-11) found that the
# default configuration's RA noise fit — the single most load-bearing fitted
# number in the model — was invisible to :func:`audit_provenance`: the filter
# was ``grade is FITTED and value is not None``, and ``ra_out_noise_rms`` stores
# ``None`` precisely *because* the fit is active. A reviewer reading
# ``fitted_in_use`` would have concluded that no fitted parameter was driving
# results. The reviewer's suggested test — "if a headline number depends on a
# fitted parameter, the report must say so" — is what this table enforces.
# --------------------------------------------------------------------------
RESOLVED_SENTINELS: dict[str, str] = {
    "ra_out_noise_rms": "adi_model.config.resolve_ra_noise",
}


def _resolve_sentinel(name: str, cfg: Any) -> Any:
    """Evaluate the resolver that supplies a sentinel field's runtime value.

    Args:
        name: Config field name; must be a key of :data:`RESOLVED_SENTINELS`.
        cfg: The configuration the resolver is applied to.

    Returns:
        The resolved value (e.g. V for a noise amplitude), or ``None`` if the
        resolver is unavailable — a partial install must not make this module
        unimportable, and ``None`` here only means "cannot prove it is active".
    """
    if name == "ra_out_noise_rms":
        try:
            from .config import resolve_ra_noise
        except ImportError:  # pragma: no cover - partial install only
            return None
        return resolve_ra_noise(cfg)
    return None


def _value_is_active(value: Any, resolved: bool) -> bool:
    """Decide whether a parameter's *current* value can move a number.

    Args:
        value: The value stored on the Config.
        resolved: True if a sentinel was resolved to a real number at runtime.

    Returns:
        False for a disabled switch (``False``, ``0``), True for anything that
        contributes. ``None`` returns ``resolved``: a sentinel is active exactly
        when its resolver produced a value.
    """
    if value is None:
        return bool(resolved)
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return float(value) != 0.0
    return bool(value)


def _differs(a: Any, b: Any) -> bool:
    """Value inequality that tolerates tuples and non-comparable types.

    Args:
        a: First value.
        b: Second value.

    Returns:
        True if the two differ, or if they cannot be compared at all (a
        conservative default: an uncomparable value is reported, not hidden).
    """
    try:
        return bool(a != b)
    except Exception:  # pragma: no cover - exotic types only
        try:
            return list(a) != list(b)
        except Exception:  # pragma: no cover
            return True


def config_defaults() -> dict[str, Any]:
    """Field defaults of a stock :class:`~adi_model.config.Config`.

    Returns:
        Mapping field name -> default value, or an empty mapping if ``Config``
        cannot be imported (keeps this module usable without the rest of the
        package).
    """
    try:
        from dataclasses import fields

        from .config import Config
    except ImportError:  # pragma: no cover - partial install only
        return {}
    return {f.name: f.default for f in fields(Config)}


def annotate_config(cfg: Any) -> dict[str, Graded[Any]]:
    """Bundle every Config field with its declared grade.

    For fields listed in :data:`RESOLVED_SENTINELS`, the runtime-resolved value
    is recorded in ``Graded.note`` so that a sentinel cannot masquerade as an
    inactive parameter.

    For ``DISCLOSED``/``DERIVED`` fields whose current value no longer equals
    the stock :class:`~adi_model.config.Config` default, ``Graded.overridden``
    is set. The grade stays attached to the field *name*, so without this flag
    a hand-edited number would inherit the original source's authority and
    ``require(DISCLOSED)`` would accept it (external review, 2026-09-11).

    Args:
        cfg: A :class:`~adi_model.config.Config` instance.

    Returns:
        Mapping field name -> :class:`Graded` value. Fields without a declared
        grade are returned with grade ``ASSUMED`` and source ``"UNGRADED"`` so
        that they cannot be mistaken for anything else.
    """
    from dataclasses import fields  # local import: keeps this module dependency-free

    defaults = config_defaults()
    out: dict[str, Graded[Any]] = {}
    for f in fields(cfg):
        grade, source = PARAM_GRADES.get(f.name, (SourceGrade.ASSUMED, "UNGRADED"))
        stored = getattr(cfg, f.name)
        note = ""
        if f.name in RESOLVED_SENTINELS:
            resolved = _resolve_sentinel(f.name, cfg)
            if resolved is not None:
                note = f"sentinel {stored!r} resolved at runtime -> {resolved!r}"
        is_overridden = (
            f.name in defaults
            and grade in (SourceGrade.DISCLOSED, SourceGrade.DERIVED)
            and _differs(stored, defaults[f.name])
        )
        # 注意：覆盖状态放进 Graded.overridden，**不**混进 note。note 是
        # resolved_sentinels 的载体，往里面塞别的说明会让"哨兵被解析了"这个
        # 判据失去意义（`if v.note` 就不再等价于"这个哨兵解出了值"）。
        out[f.name] = Graded(stored, grade, source, note=note, unit="", overridden=is_overridden)
    return out


def audit_provenance(cfg: Any) -> dict[str, Any]:
    """Summarise how much of a configuration rests on which grade.

    Intended as the first thing a reviewer reads: if a headline number depends
    on an ``ASSUMED`` parameter, this report says so explicitly.

    Args:
        cfg: A :class:`~adi_model.config.Config` instance.

    Returns:
        Dict with keys:

        ``counts``
            grade -> number of fields.
        ``ungraded``
            Field names missing from :data:`PARAM_GRADES`.
        ``assumed_in_use``
            Assumed parameters whose current value can move a number.
        ``fitted_in_use``
            Fitted parameters whose current value can move a number. Includes
            sentinel-valued fields whose resolver is active (see
            :data:`RESOLVED_SENTINELS`) — omitting those was the defect an
            external review of 2026-09-11 found.
        ``resolved_sentinels``
            Sentinel field -> the runtime value the resolver supplied, so a
            reader can see *what* the fit produced, not merely that one is on.
        ``overridden``
            Fields graded ``DISCLOSED``/``DERIVED`` whose value no longer equals
            the stock field default. The declared source (e.g. "40 MS/s") then
            does not describe the number actually in use, which is a different
            failure mode from an ungraded parameter and must be visible.
        ``verdict``
            One-line summary of the ungraded check.
    """
    ann = annotate_config(cfg)
    counts: dict[str, int] = {}
    for g in ann.values():
        counts[g.grade.value] = counts.get(g.grade.value, 0) + 1
    ungraded = sorted(k for k, v in ann.items() if v.source == "UNGRADED")
    resolved = {k: v.note for k, v in ann.items() if v.note}

    def _active(v: Graded[Any]) -> bool:
        """True if this graded parameter currently influences results."""
        return _value_is_active(v.value, bool(v.note))

    assumed_in_use = sorted(
        k for k, v in ann.items() if v.grade is SourceGrade.ASSUMED and _active(v)
    )
    fitted_in_use = sorted(
        k for k, v in ann.items() if v.grade is SourceGrade.FITTED and _active(v)
    )

    # 覆盖状态由 annotate_config 写在 Graded.overridden 上（单一来源），
    # 这里不再自己重算一遍默认值比较。
    overridden = sorted(k for k, v in ann.items() if v.overridden)
    return {
        "counts": counts,
        "ungraded": ungraded,
        "assumed_in_use": assumed_in_use,
        "fitted_in_use": fitted_in_use,
        "resolved_sentinels": resolved,
        "overridden": overridden,
        "verdict": (
            "OK: no ungraded parameters"
            if not ungraded
            else f"FAIL: {len(ungraded)} ungraded parameters"
        ),
    }
