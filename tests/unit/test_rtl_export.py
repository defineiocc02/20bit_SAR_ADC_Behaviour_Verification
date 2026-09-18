"""P0 gate for ``tools/export_rtl_params.py`` and ``docs/rtl/RTL_ARITHMETIC_CONTRACT.md``.

This file is the executable form of the frozen RTL arithmetic contract. It has two
kinds of checks, and the distinction matters:

**Independent arithmetic checks** — the test re-derives the rule from the contract
text and compares it against something the exporter cannot influence:
``fixed_point.round_even_divide`` (the model), and ``dem.split_switch_command`` for
the DEM geometry. If the contract and the model disagree, this file fails.

**Serialization checks** — the exported ``.vh``/JSON/hex artifacts are re-parsed and
compared against a fresh model run. These prove the artifacts are current and
lossless; they are not a second implementation of the reconstruction (that is the
P2 obligation, see the contract §8).

The floor-division checks are deliberately built so that a *wrong* implementation
fails: ``test_floor_via_truncation_correction_is_not_vacuous`` asserts the naive
truncating version actually disagrees somewhere, so the correction rule cannot pass
by accident.
"""

from __future__ import annotations

import functools
import importlib.util
import json
import pathlib
import sys
from dataclasses import replace

import numpy as np
import pytest

from adi_model import Config
from adi_model.dem import split_switch_command
from adi_model.fixed_point import round_even_divide
from adi_model.sampler import sine_input

REPO = pathlib.Path(__file__).resolve().parents[2]
ARTIFACTS = {
    "vh": REPO / "rtl" / "params" / "rtl_params.vh",
    "params": REPO / "sim" / "vectors" / "params_paper_literal.json",
    "registers": REPO / "sim" / "vectors" / "registers_paper_literal.json",
    "stimulus": REPO / "sim" / "vectors" / "stimulus_paper_literal.hex",
    "expected": REPO / "sim" / "vectors" / "expected_paper_literal.hex",
}
COMMITTED_STIMULUS = 64


def _load_exporter():
    """Import ``tools/export_rtl_params.py`` by path (``tools/`` is not a package)."""
    path = REPO / "tools" / "export_rtl_params.py"
    spec = importlib.util.spec_from_file_location("export_rtl_params", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["export_rtl_params"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def exporter():
    return _load_exporter()


@functools.lru_cache(maxsize=1)
def _golden_run(n: int = COMMITTED_STIMULUS):
    """Reproduce the exact record the committed artifacts were exported from."""
    from adi_model.pipeline import run_pipeline
    from adi_model.weight_calibration import DigitalObservation

    exporter = _load_exporter()
    cfg = Config.paper_literal()
    result = run_pipeline(
        cfg,
        sine_input(
            exporter.GOLDEN_AMPLITUDE_FRAC * cfg.v_fs,
            cfg.fs * exporter.GOLDEN_TONE_NUM / exporter.GOLDEN_TONE_DEN,
        ),
        n,
    )
    return cfg, result, DigitalObservation.from_result(result), result.to_codes()


# ---------------------------------------------------------------- 契约 §3.1
def _round_half_even_from_contract(n: int, d: int) -> int:
    """Round-half-to-even as written in contract §3.1 (floor quotient + tie rule)."""
    if d <= 0:
        raise ValueError("the contract requires a positive divisor")
    q = n // d
    r = n - q * d
    return q + int(2 * r > d or (2 * r == d and q % 2 != 0))


@pytest.mark.parametrize(
    "n, expected", [(-7, -4), (-5, -2), (-3, -2), (-1, 0), (1, 0), (3, 2), (5, 2), (7, 4)]
)
def test_contract_round_half_even_reproduces_the_shipped_samples(n, expected):
    assert _round_half_even_from_contract(n, 2) == expected


def test_contract_round_half_even_agrees_with_the_model():
    rng = np.random.default_rng(20260918)
    values = list(rng.integers(-(10**12), 10**12, 4000))
    values += [0, 1, -1, 2, -2, 3, -3, 2**31, -(2**31)]
    divisors = [2, 4, 8, 16, 4096, 8192, *rng.integers(1, 10**6, 200)]
    for n in values:
        for d in divisors:
            assert _round_half_even_from_contract(int(n), int(d)) == round_even_divide(
                int(n), int(d)
            )


# ---------------------------------------------------------------- 契约 §3.5
def _trunc_div(a: int, b: int) -> int:
    """Signed division truncated toward zero — what a bare RTL ``/`` does."""
    q = abs(a) // abs(b)
    return q if (a < 0) == (b < 0) else -q


def _floor_via_truncation_correction(a: int, b: int) -> int:
    """Contract §3.5: floor division built from a truncating divider."""
    q = _trunc_div(a, b)
    remainder = a - q * b  # C semantics: sign follows the dividend
    return q - 1 if (remainder != 0 and a < 0) else q


def test_floor_via_truncation_correction_is_not_vacuous():
    """A bare truncating divider must disagree somewhere, or the rule is untested."""
    disagreements = [a for a in range(-20, 21) if _trunc_div(a, 2) != a // 2]
    assert disagreements, "truncation never differed from floor: the check is blind"
    assert -7 in disagreements


def _random_wide(rng: np.random.Generator) -> int:
    """A signed integer spread over the 96-bit accumulator domain.

    Built from two 62/32-bit draws because ``numpy`` integers cannot hold 2**94.
    """
    magnitude = int(rng.integers(0, 1 << 32)) << 62 | int(rng.integers(0, 1 << 62))
    return -magnitude if rng.integers(0, 2) else magnitude


def test_floor_via_truncation_correction_equals_floor():
    rng = np.random.default_rng(20260918)
    for _ in range(3000):
        a = _random_wide(rng)
        b = int(rng.integers(1, 1 << 40))
        assert _floor_via_truncation_correction(a, b) == a // b


def test_recommended_shift_decomposition_is_exact():
    """Contract §3.5.1: floor(A/(G*2**33)) == floor((A >> 33) / G) for G > 0."""
    rng = np.random.default_rng(20260918)
    for _ in range(3000):
        a = _random_wide(rng)
        g = int(rng.integers(1, 1 << 40))
        decomposed = (a >> 33) // g
        assert decomposed == a // (g * (1 << 33))
        assert decomposed == _floor_via_truncation_correction(a, g * (1 << 33))


def test_accumulator_limit_is_half_open():
    """Contract §4.2: the checked interval is [-2**95, 2**95), not closed."""
    limit = 1 << 95
    for value in (-limit - 1, -limit, limit - 1, limit, limit + 1):
        inside = -limit <= value < limit
        assert inside == (value in (-limit, limit - 1))


# ---------------------------------------------------------------- 参数导出
def test_localparams_match_the_config_independently(exporter):
    cfg = Config.paper_literal()
    exported = {
        p["name"]: p["value"]
        for p in exporter.rtl_localparams(cfg, exporter.FixedPointFormat(), 16)
    }
    n_main, n_sub = cfg.dac_n_main, cfg.dac_n_sub
    expected = {
        "W_FRAC": 30,
        "W_BITS": 48,
        "V_FRAC": 32,
        "V_BITS": 64,
        "ACC_BITS": 96,
        "OUT_BITS": 20,
        "N_SLICES": 18,
        "N_ACTIVE": 8,
        "N_UNIT_MAIN": 63,
        "N_UNIT_SUB": 8,
        "N_UNIT_TOTAL": 71,
        "DAC_LEVELS": 512,
        "DAC_COMPLETE_RANGE": 1,
        "B1": 9,
        "STAGE1_LEVELS": 512,
        "N_UNITS_SIG": 512,
        "UNITS_PER_LSB1": 1,
        "UNITS_PER_D1": 1,
        "N_UNITS_HEADROOM": 0,
        "K0": 0,
        "ADC2_BITS": 12,
        "DEM_STATES": 512,
        "DEM_LCG_A": 2654435761,
        "DEM_LCG_A_MOD": 2654435761 % 512,
        "DEM_ROT_WIDTH": int(np.ceil(np.sqrt(n_main))),
        "DEM_ROT_HEIGHT": int(np.ceil(n_main / np.ceil(np.sqrt(n_main)))),
        "DEM_SUB_ROLL": n_sub,
        "DEM_ENABLE": 0,
        "DEM_BRIDGE_ENABLE": 1,
        "DITHER_MODE": 0,
        "DITHER_UNITS_TOTAL": 0,
        "DITHER_SPLIT_IS_SUB": 1,
        "DITHER_DISCRETE": 1,
        "DITHER_UNITS_RANGE": 2,
        "PHASES": 16,
    }
    assert exported == expected


def test_units_per_lsb1_guard_refuses_an_ambiguous_grid(exporter):
    """ADR 0004: two derivations of the same step must not silently disagree."""
    cfg = Config.paper_literal()
    broken = replace(cfg, n_unit_per_slice=32)  # n_units_sig=256 vs dac_levels=512
    assert broken.units_per_lsb1 != broken.dac_levels // 2**broken.b1
    with pytest.raises(ValueError, match="units_per_lsb1 disagrees"):
        exporter.rtl_localparams(broken, exporter.FixedPointFormat(), 16)


def test_grade_propagation_takes_the_weakest_input(exporter):
    from adi_model.provenance import SourceGrade

    assert (
        exporter.weakest_grade([SourceGrade.DISCLOSED, SourceGrade.DERIVED]) is SourceGrade.DERIVED
    )
    assert (
        exporter.weakest_grade([SourceGrade.DISCLOSED, SourceGrade.ASSUMED]) is SourceGrade.ASSUMED
    )
    assert exporter.weakest_grade([SourceGrade.FITTED, SourceGrade.ASSUMED]) is SourceGrade.ASSUMED
    assert (
        exporter.weakest_grade([SourceGrade.DISCLOSED, SourceGrade.RESEARCH_EXTENSION])
        is SourceGrade.RESEARCH_EXTENSION
    )
    with pytest.raises(ValueError):
        exporter.weakest_grade([])


def test_analog_references_are_exact_and_never_in_the_verilog(exporter):
    cfg = Config.paper_literal()
    for entry in exporter.analog_references(cfg):
        exact = entry["exact"]
        assert exact["num"] / 2.0 ** exact["den_exp"] == exact["float"]
        # 精确表示：分母必须是 2 的幂，否则 float 会有隐藏的十进制舍入
        assert float(exact["num"] / 2 ** exact["den_exp"]) == exact["float"]
    vh = exporter.render_verilog_header(
        exporter.rtl_localparams(cfg, exporter.FixedPointFormat(), 16),
        {
            "config": "x",
            "guard": "G",
            "revision": {"describe": "d", "commit": "c", "dirty": "0"},
            "fixed_point": {
                "weight_fraction_bits": 30,
                "coefficient_bits": 48,
                "voltage_fraction_bits": 32,
                "voltage_bits": 64,
                "accumulator_bits": 96,
                "output_bits": 20,
            },
        },
        "sha",
    )
    for entry in exporter.analog_references(cfg):
        assert entry["name"] not in vh


# ---------------------------------------------------------------- DEM 几何
def test_exported_dem_geometry_reproduces_split_switch_command(exporter):
    """The exported rot width/height rebuild ``dem.py``'s orders bit for bit."""
    cfg = Config.paper_literal(dem_enable=True)
    exported = {
        p["name"]: p["value"]
        for p in exporter.rtl_localparams(cfg, exporter.FixedPointFormat(), 16)
    }
    width, height = exported["DEM_ROT_WIDTH"], exported["DEM_ROT_HEIGHT"]
    n_main, n_sub = cfg.dac_n_main, cfg.dac_n_sub

    sids = np.arange(512, dtype=np.int64)
    command = split_switch_command(cfg, np.zeros(512), sids)

    cells = np.arange(height * width)
    row, col = np.divmod(cells, width)
    for sid in sids:
        rr = (row + (int(sid) // width % height)) % height
        cc = (col + (int(sid) % width)) % width
        order = rr * width + cc
        main_order = order[order < n_main]
        sub_order = (np.arange(n_sub) + int(sid) // (width * height)) % n_sub
        np.testing.assert_array_equal(main_order, command.main_order[sid])
        np.testing.assert_array_equal(sub_order, command.sub_order[sid])


def test_dem_permutations_are_bijections(exporter):
    """INV-5: an order that repeats or drops a unit would break nominal conservation."""
    cfg = Config.paper_literal(dem_enable=True)
    command = split_switch_command(cfg, np.zeros(512), np.arange(512, dtype=np.int64))
    for sid in range(512):
        assert sorted(command.main_order[sid].tolist()) == list(range(cfg.dac_n_main))
        assert sorted(command.sub_order[sid].tolist()) == list(range(cfg.dac_n_sub))


# ---------------------------------------------------------------- 产物
def test_export_is_deterministic(exporter):
    first = exporter.build("paper_literal", phases=16, dither_mode=None, stimulus=32, n_image=32)
    second = exporter.build("paper_literal", phases=16, dither_mode=None, stimulus=32, n_image=32)
    assert first["vh"] == second["vh"]
    assert first["params_json"] == second["params_json"]
    assert first["registers_json"] == second["registers_json"]
    assert first["stimulus_text"] == second["stimulus_text"]
    assert first["expected_text"] == second["expected_text"]


def test_unary_topology_skips_the_register_image_loudly(exporter):
    built = exporter.build("paper_consistent", phases=16, dither_mode=None, stimulus=16, n_image=16)
    assert "registers_json" not in built
    assert any("split" in note for note in built["skipped"])
    assert "RTL_PARAMS_VH" in built["vh"]


def test_verilog_guard_and_payload_hash_are_present(exporter):
    built = exporter.build("paper_literal", phases=16, dither_mode=None, stimulus=8, n_image=8)
    vh = built["vh"]
    assert "`ifndef RTL_PARAMS_VH" in vh and "`endif" in vh
    assert built["meta"]["payload_sha256"] in vh
    assert "请勿手工编辑" in vh


def test_register_image_round_trips_to_identical_words(exporter):
    cfg, result, data, stream = _golden_run()
    from adi_model.fixed_point import FixedPointReconstructor

    decoder = FixedPointReconstructor.from_result(result, format=exporter.FixedPointFormat())
    restored = FixedPointReconstructor.from_dict(
        json.loads(json.dumps(decoder.to_dict(), allow_nan=False))
    )
    np.testing.assert_array_equal(restored.reconstruct(data).code, stream.code)
    assert stream.peak_accumulator_bits <= exporter.FixedPointFormat().accumulator_bits


def _parse_hex(path: pathlib.Path) -> list[list[str]]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        # 注释是 "//"；"#" 是数据起始标记 —— 两者都必须跳过，否则标记行会被当成数据。
        if not stripped or stripped.startswith(("//", "#")):
            continue
        rows.append(stripped.split())
    return rows


@pytest.mark.skipif(
    not (REPO / "sim" / "vectors" / "stimulus_paper_literal.hex").is_file(),
    reason="rtl artifacts have not been exported into this checkout",
)
def test_committed_artifacts_are_current(exporter):
    code = exporter.main(
        [
            "--config",
            "paper_literal",
            "--emit-stimulus",
            str(COMMITTED_STIMULUS),
            "--check",
            "--quiet",
        ]
    )
    assert code == 0, "committed RTL artifacts drifted from the source tree"


@pytest.mark.skipif(
    not (REPO / "sim" / "vectors" / "stimulus_paper_literal.hex").is_file(),
    reason="rtl artifacts have not been exported into this checkout",
)
def test_committed_hex_is_lossless_and_current():
    cfg, result, data, stream = _golden_run()
    tags = _parse_hex(ARTIFACTS["stimulus"])
    expected = _parse_hex(ARTIFACTS["expected"])
    assert len(tags) == len(expected) <= COMMITTED_STIMULUS
    vscale = 2**32
    for i, (tag, exp) in enumerate(zip(tags, expected, strict=True)):
        assert len(tag) == 5 + cfg.n_active
        assert int(tag[0], 16) == round(result.k[i])
        assert int(tag[1], 16) == int(result.sid[i])
        assert int(tag[2], 16) == int(result.bank[i])
        assert int(tag[3], 16) == int(result.adc2_code[i])
        raw = int(tag[4], 16)
        signed = raw - (1 << 64) if raw >= (1 << 63) else raw
        assert signed == round(data.common_injection_v[i] / cfg.v_fs * vscale)
        np.testing.assert_array_equal([int(v, 16) for v in tag[5:]], result.conv_slice_ids[i])
        assert int(exp[0], 16) == int(stream.code[i])
        assert int(exp[1]) == int(stream.clipped_low[i])
        assert int(exp[2]) == int(stream.clipped_high[i])
        assert int(exp[3]) == int(stream.analog_overflow[i])


@pytest.mark.skipif(
    not (REPO / "sim" / "vectors" / "registers_paper_literal.json").is_file(),
    reason="rtl artifacts have not been exported into this checkout",
)
def test_committed_register_image_reproduces_the_committed_codes():
    """The *file*, not an in-memory object, is what the RTL will be loaded from."""
    from adi_model.fixed_point import FixedPointReconstructor

    _, _, data, stream = _golden_run()
    restored = FixedPointReconstructor.from_dict(
        json.loads(ARTIFACTS["registers"].read_text(encoding="utf-8"))
    )
    np.testing.assert_array_equal(restored.reconstruct(data).code[:COMMITTED_STIMULUS], stream.code)
    assert sum(int(w) for w in restored.weights_q.flat) < 2**60


def test_cli_check_detects_drift(exporter, tmp_path):
    args = [
        "--config",
        "paper_literal",
        "--emit-stimulus",
        "16",
        "--image-samples",
        "16",
        "--out-rtl",
        str(tmp_path / "rtl"),
        "--out-vectors",
        str(tmp_path / "vec"),
        "--quiet",
    ]
    assert exporter.main(args) == 0
    assert exporter.main([*args, "--check"]) == 0

    vh = tmp_path / "rtl" / "rtl_params.vh"
    vh.write_text(
        vh.read_text(encoding="utf-8").replace("ACC_BITS = 8'd96", "ACC_BITS = 8'd95"),
        encoding="utf-8",
    )
    assert exporter.main([*args, "--check"]) == 1

    vh.write_text(vh.read_text(encoding="utf-8").replace("8'd95", "8'd96"), encoding="utf-8")
    stim = tmp_path / "vec" / "stimulus_paper_literal.hex"
    stim.write_text(stim.read_text(encoding="utf-8") + "dead 0 0 0 0\n", encoding="utf-8")
    assert exporter.main([*args, "--check"]) == 1


# ------------------------------------------------- 过期检测的两条独立验证
# 外部复核（2026-09-18）要求把这两件事**分开**验证，而不是笼统地声明"会变红"：
#   (a) 参数变了 -> 旧产物必须被判过期；
#   (b) 生成物被人手编辑 -> 必须被判不一致。
# 二者失败的方式不同：前者是"内容对不上来源"，后者是"内容对不上它自己"。
STALE_ARGS = ["--config", "paper_literal", "--emit-stimulus", str(COMMITTED_STIMULUS), "--quiet"]


@pytest.mark.skipif(
    not (REPO / "rtl" / "params" / "rtl_params.vh").is_file(),
    reason="rtl artifacts have not been exported into this checkout",
)
def test_a_changed_parameter_makes_the_old_artifact_stale(exporter):
    """(a) 参数改动后旧产物必须被判过期，而不是继续"检查通过"。"""
    from adi_model.provenance import SourceGrade  # noqa: F401  (仅作可读性引用)

    current = exporter.main([*STALE_ARGS, "--check"])
    assert current == 0, "committed artifacts should be current first"

    # PHASES 是导出参数之一；换成 8 后载荷变化，磁盘上的 16 版必须被判过期。
    stale = exporter.main([*STALE_ARGS, "--phases", "8", "--check"])
    assert stale == 1, "a changed parameter must invalidate the committed .vh"


def test_b_a_hand_edited_artifact_is_detected(exporter, tmp_path):
    """(b) 人手编辑生成物必须被判不一致 —— 逐目录都测一遍，不只测 .vh。"""
    args = [
        "--config",
        "paper_literal",
        "--emit-stimulus",
        "16",
        "--image-samples",
        "16",
        "--out-rtl",
        str(tmp_path / "rtl"),
        "--out-vectors",
        str(tmp_path / "vec"),
        "--quiet",
    ]
    assert exporter.main(args) == 0
    assert exporter.main([*args, "--check"]) == 0

    edits = {
        "rtl/rtl_params.vh": ("ACC_BITS = 8'd96", "ACC_BITS = 8'd95"),
        "vec/params_paper_literal.json": (
            '"weight_fraction_bits": 30',
            '"weight_fraction_bits": 29',
        ),
        "vec/registers_paper_literal.json": ('"offset_q": 0', '"offset_q": 1'),
        "vec/stimulus_paper_literal.hex": ("#DATA", "#DATA_EDITED"),
        "vec/expected_paper_literal.hex": ("#DATA", "#DATA_EDITED"),
    }
    for rel, (needle, replacement) in edits.items():
        path = tmp_path / rel
        assert path.is_file(), rel
        text = path.read_text(encoding="utf-8")
        assert needle in text, f"{rel}: anchor {needle!r} missing"
        path.write_text(text.replace(needle, replacement, 1), encoding="utf-8")
        assert exporter.main([*args, "--check"]) == 1, f"hand-edited {rel} was not detected"
        path.write_text(text, encoding="utf-8")
    assert exporter.main([*args, "--check"]) == 0, "restoring the artifacts should clear the drift"
