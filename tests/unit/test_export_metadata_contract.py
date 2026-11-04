"""Independent provenance and repository-identity contracts for parameter export."""

import importlib.util
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from adi_model import Config, FixedPointFormat
from adi_model.provenance import PARAM_GRADES, SourceGrade, annotate_config

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def exporter():
    spec = importlib.util.spec_from_file_location(
        "export_metadata_contract_target", ROOT / "tools/export_rtl_params.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _params(exporter, cfg):
    return {
        entry["name"]: entry
        for entry in exporter.rtl_localparams(cfg, FixedPointFormat(), phases=16)
    }


def test_successful_empty_git_status_is_clean_and_untracked_status_is_dirty(exporter, tmp_path):
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", str(repo)], check=True, capture_output=True)
    assert subprocess.check_output(["git", "status", "--porcelain"], cwd=repo) == b""
    assert exporter.repo_identity(repo)["dirty"] == "0"
    (repo / "untracked.txt").write_text("audit fixture\n", encoding="utf-8")
    assert exporter.repo_identity(repo)["dirty"] == "1"


def test_non_git_directory_reports_unknown_instead_of_clean(exporter, tmp_path, monkeypatch):
    root = tmp_path / "no_git"
    root.mkdir()
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    assert exporter.repo_identity(root) == {
        "commit": "unknown",
        "describe": "unknown",
        "dirty": "unknown",
    }


def test_unavailable_git_reports_unknown_without_inventing_identity(
    exporter, tmp_path, monkeypatch
):
    def unavailable(*args, **kwargs):
        raise FileNotFoundError("test fixture has no git executable")

    monkeypatch.setattr(exporter.subprocess, "run", unavailable)
    assert set(exporter.repo_identity(tmp_path).values()) == {"unknown"}


@pytest.mark.parametrize("factory", [Config, Config.paper_literal])
def test_direct_localparams_inherit_current_value_authority(exporter, factory):
    cfg = factory()
    annotated = annotate_config(cfg)
    entries = _params(exporter, cfg)
    direct = {
        "N_SLICES": "n_slices",
        "N_ACTIVE": "n_active",
        "N_UNIT_MAIN": "dac_n_main",
        "N_UNIT_SUB": "dac_n_sub",
        "DAC_COMPLETE_RANGE": "dac_complete_range",
        "B1": "b1",
        "N_UNITS_HEADROOM": "n_units_headroom",
        "ADC2_BITS": "adc2_n_bits",
        "DEM_SUB_ROLL": "dac_n_sub",
        "DEM_ENABLE": "dem_enable",
        "DEM_BRIDGE_ENABLE": "dem_bridge_enable",
        "DITHER_DISCRETE": "dither_discrete",
    }
    for name, field in direct.items():
        assert entries[name]["input_fields"] == [field]
        assert entries[name]["grade"] == annotated[field].effective_grade.value


def test_overridden_published_count_is_not_still_disclosed(exporter):
    entry = _params(exporter, Config(n_slices=19))["N_SLICES"]
    assert entry["value"] == 19
    assert entry["grade"] == "assumed"


def test_arithmetic_dependencies_are_explicit_and_do_not_promote_assumptions(exporter):
    entries = _params(exporter, Config.paper_literal())
    expected = {
        "N_UNIT_TOTAL": ["dac_n_main", "dac_n_sub"],
        "DAC_LEVELS": ["dac_arch", "dac_n_main", "dac_complete_range", "dac_n_sub"],
        "STAGE1_LEVELS": ["b1"],
        "N_UNITS_SIG": ["n_active", "n_unit_per_slice", "n_units_headroom"],
        "UNITS_PER_LSB1": ["n_active", "n_unit_per_slice", "n_units_headroom", "b1"],
        "UNITS_PER_D1": ["n_active", "n_unit_per_slice", "n_units_headroom", "b1"],
        "K0": ["n_units_headroom"],
        "DEM_ROT_WIDTH": ["dac_n_main"],
        "DEM_ROT_HEIGHT": ["dac_n_main"],
        "DITHER_UNITS_TOTAL": ["dither_mode", "dither_units_range"],
        "DITHER_UNITS_RANGE": ["dither_units_range"],
    }
    for name, fields in expected.items():
        assert entries[name]["input_fields"] == fields
        assert entries[name]["grade"] == "assumed"
    assert _params(exporter, Config())["DAC_LEVELS"]["input_fields"] == [
        "dac_arch",
        "n_active",
        "n_unit_per_slice",
    ]


@pytest.mark.parametrize(
    ("input_grade", "expected"),
    [
        (SourceGrade.DISCLOSED, "derived"),
        (SourceGrade.FITTED, "fitted"),
        (SourceGrade.ASSUMED, "assumed"),
        (SourceGrade.RESEARCH_EXTENSION, "research_extension"),
    ],
)
def test_arithmetic_grade_cannot_be_stronger_than_any_input(
    exporter, monkeypatch, input_grade, expected
):
    # Change only the authority fixture, not production numerical behavior.
    monkeypatch.setitem(PARAM_GRADES, "b1", (input_grade, "test authority fixture"))
    assert _params(exporter, Config())["STAGE1_LEVELS"]["grade"] == expected


def test_format_prng_and_encoding_choices_have_no_published_authority(exporter):
    entries = _params(exporter, Config.paper_literal())
    choices = {
        "W_FRAC",
        "W_BITS",
        "V_FRAC",
        "V_BITS",
        "ACC_BITS",
        "OUT_BITS",
        "DEM_STATES",
        "DEM_LCG_A",
        "DEM_LCG_A_MOD",
        "DITHER_MODE",
        "DITHER_SPLIT_IS_SUB",
        "PHASES",
    }
    assert all(entries[name]["grade"] == "assumed" for name in choices)
    assert all(entry["input_fields"] for entry in entries.values())
    assert entries["W_FRAC"]["input_fields"] == ["format.weight_fraction_bits"]
    assert entries["DEM_LCG_A_MOD"]["input_fields"] == [
        "mapper._LCG_A",
        "mapper.N_DEM_STATES",
    ]


def test_analog_overrides_lose_original_publication_authority(exporter):
    cfg = replace(Config(), fs=80e6, v_fs=2.5, adc2_n_bits=13)
    entries = {entry["name"]: entry for entry in exporter.analog_references(cfg)}
    for name in ("FS", "V_FS", "LSB_TARGET", "DELTA1", "ADC2_STEP"):
        assert entries[name]["grade"] == "assumed"
    assert entries["G0"]["grade"] == "disclosed"
    assert entries["FS"]["exact"]["float"] == 80e6


def test_analog_dependency_lists_follow_actual_config_formulas(exporter):
    cfg = Config.paper_literal(dither_mode="sampling")
    entries = {entry["name"]: entry for entry in exporter.analog_references(cfg)}
    assert entries["C_ACTIVE_NOMINAL"]["input_fields"] == [
        "c_total0",
        "cap_scale",
        "n_active",
        "n_unit_per_slice",
        "n_units_headroom",
    ]
    assert entries["NOMINAL_RDAC_STEP"]["input_fields"] == [
        "dac_arch",
        "v_fs",
        "dac_n_main",
        "dac_n_sub",
    ]
    assert entries["DITHER_ALPHA"]["input_fields"] == [
        "dither_mode",
        "dither_units_range",
        "dac_arch",
        "dither_split_bank",
        "dac_n_main",
        "dac_n_sub",
        "dac_parasitic_ratio",
        "c_total0",
        "cap_scale",
    ]
    for entry in entries.values():
        assert entry["inputs"] == entry["input_fields"]
        assert entry["input_fields"]


def test_config_field_serialization_exposes_effective_and_declared_grades(exporter):
    fields = exporter.config_field_entries(Config(fs=80e6))
    assert fields["fs"]["grade"] == "assumed"
    assert fields["fs"]["declared_grade"] == "disclosed"
    assert fields["fs"]["overridden"]
    assert not fields["fs"]["quotable"]


@pytest.mark.parametrize("enabled, kappa", [(False, None), (True, None), (True, 2.0)])
def test_ktc_analog_dependencies_include_the_enable_and_sentinel_branches(exporter, enabled, kappa):
    cfg = Config(ktc_enable=enabled, ktc_kappa=kappa)
    entries = {entry["name"]: entry for entry in exporter.analog_references(cfg)}
    if not enabled:
        assert entries["KAPPA_EFF"]["input_fields"] == ["ktc_enable"]
        assert entries["BETA_EFF"]["input_fields"] == ["ktc_enable"]
    else:
        expected = ["ktc_enable", "ktc_kappa"]
        if kappa is None:
            expected += ["ktc_gain_n", "g0"]
        assert entries["KAPPA_EFF"]["input_fields"] == expected
        assert set(entries["BETA_EFF"]["input_fields"]) == {
            "ktc_enable",
            "ktc_beta_error",
            "ktc_kappa",
            "ktc_gain_n",
            "g0",
        }


def test_unary_and_disabled_analog_branches_have_their_own_dependencies(exporter):
    off = {entry["name"]: entry for entry in exporter.analog_references(Config())}
    assert off["DITHER_ALPHA"]["input_fields"] == ["dither_mode"]
    assert off["NOMINAL_RDAC_STEP"]["input_fields"] == [
        "dac_arch",
        "v_fs",
        "n_active",
        "n_unit_per_slice",
        "n_units_headroom",
    ]
    sampling = {
        entry["name"]: entry for entry in exporter.analog_references(Config(dither_mode="sampling"))
    }
    assert sampling["DITHER_ALPHA"]["input_fields"] == [
        "dither_mode",
        "dither_units_range",
        "dac_arch",
        "n_active",
        "n_unit_per_slice",
    ]
