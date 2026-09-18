"""P1 向量导出器的门禁：产物可复现、覆盖账本属实、口径与模型一致。

与 ``tests/unit/test_rtl_export.py`` 的分工：那里守的是**参数包**与**算术契约**，
这里守的是**黄金向量**与它们的覆盖声明。两者都接在 ``--check`` 漂移门禁上。

这里刻意放了一条"陷阱回归"：模型工厂 ``Config.paper_literal()`` 的 ``dem_enable``
默认是 ``False``，而 ``split_switch_command`` 在该状态下会把 ``states`` 强制为 0，
全部置换退化为恒等。向量必须按 ``dem_enable=True`` 生成，且报告要**显式声明**
这个差异 —— 否则"全绿"可能只是什么都没覆盖。参见面板的同一条记录。
"""

from __future__ import annotations

import importlib.util
import itertools
import json
import pathlib
import sys
from dataclasses import replace

import numpy as np
import pytest

from adi_model import Config
from adi_model.mapper import dem_state_sequence

REPO = pathlib.Path(__file__).resolve().parents[2]
VECTORS = REPO / "sim" / "vectors"
REPORT = VECTORS / "p1_vectors_report.json"


def _load(name: str, relative: str):
    """Import a ``tools/`` script by path (``tools/`` is not a package)."""
    spec = importlib.util.spec_from_file_location(name, REPO / relative)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def exporter():
    return _load("export_rtl_vectors", "tools/export_rtl_vectors.py")


def test_export_is_deterministic(exporter):
    """P2 用了记忆化；这里的两次 build 都先清缓存，测的是真复现而不是缓存命中。"""
    exporter.clear_p2_memo()
    first = exporter.build("paper_literal")
    exporter.clear_p2_memo()
    second = exporter.build("paper_literal")
    assert first["files"] == second["files"]
    assert first["report"] == second["report"]
    assert first["p2_report"] == second["p2_report"]


def test_only_paper_literal_is_supported(exporter):
    """The M2 closed form assumes H_ROT*W_ROT == 64; refuse other topologies."""
    with pytest.raises(ValueError, match="paper_literal only"):
        exporter.build("paper_consistent")


def test_the_dem_trap_is_refused_and_recorded(exporter):
    """DEM off would make every permutation identity, so the vectors must say so."""
    report = exporter.build("paper_literal")["report"]
    assert report["effective_overrides"]["dem_enable"] is True
    assert "dem_enable" in report["header_default_note"]
    # 参数包的复位默认值确实是 0（stock 配置），与向量的假设**不同**且被显式写明
    params = _load("export_rtl_params", "tools/export_rtl_params.py")
    local = {
        p["name"]: p["value"]
        for p in params.rtl_localparams(Config.paper_literal(), params.FixedPointFormat(), 16)
    }
    assert local["DEM_ENABLE"] == 0


def test_m1_report_claims_both_banks_sweep_every_state(exporter):
    """The per-bank-counter invariant is what the historical bug broke."""
    stats = exporter.build("paper_literal")["report"]["m1"]
    assert stats["distinct_sids"] == 512
    assert stats["per_bank_all_states_seen"] is True
    assert stats["first_sid_by_bank"] == {"0": 0, "1": 0}


def test_m2_report_claims_a_bijection(exporter):
    stats = exporter.build("paper_literal")["report"]["m2"]
    assert stats["cases"] == 512 * (63 + 8)
    assert stats["bijection_verified"] is True


def test_m3_report_covers_the_whole_control_space(exporter):
    stats = exporter.build("paper_literal")["report"]["m3"]
    assert stats["exhaustive_cases"] == 512 * 2 * 8
    assert stats["directed_cases"] == 5 * 17


def test_m5_report_covers_every_state(exporter):
    stats = exporter.build("paper_literal")["report"]["m5"]
    assert stats["all_sids_boundary_cases"] == 1024
    assert stats["thermometer_structure_verified"] is True
    assert stats["uniform_counts_require_bridge_off"] is True


def test_m5_golden_is_independent_of_the_bridge_because_counts_would_differ(exporter):
    """With the bridge on, the model gives slices *different* counts on purpose.

    A uniform-count grid is therefore only meaningful with the bridge disabled —
    and the report has to say so, or a reader would think the bridge were covered
    here as well (it is covered exhaustively in M3 instead).
    """
    cfg = replace(Config.paper_literal(), dem_enable=True, dem_bridge_enable=True)
    from adi_model.dem import split_switch_command

    command = split_switch_command(cfg, np.array([1.0]), np.array([0], dtype=np.int64))
    per_slice = np.unique(command.sub_counts[0])
    assert per_slice.size > 1, "bridge must make slice counts non-uniform, else the note is stale"


def test_rdac_over_definition_matches_the_model(exporter):
    """The exporter mirrors ``SimResult.rdac_over``; prove the mirror is faithful."""
    from adi_model.pipeline import run_pipeline
    from adi_model.sampler import sine_input

    cfg = Config.paper_literal()
    result = run_pipeline(cfg, sine_input(0.8 * cfg.v_fs, cfg.fs * 73 / 2048), 32)
    k = np.rint(np.asarray(result.k)).astype(np.int64)
    mine = (k < 0) | (k > cfg.dac_levels - 1)
    np.testing.assert_array_equal(mine, np.asarray(result.rdac_over))


@pytest.mark.skipif(not REPORT.is_file(), reason="P1 vectors have not been exported")
def test_committed_vectors_are_current(exporter):
    assert exporter.main(["--config", "paper_literal", "--check", "--quiet"]) == 0
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    assert report["m2"]["cases"] == 36352


@pytest.mark.skipif(not REPORT.is_file(), reason="P1 vectors have not been exported")
def test_committed_vectors_have_a_data_marker(exporter):
    """The TB uses ``$fscanf`` after a ``#DATA`` marker; both must be present."""
    for name in ("p1_m1_sid.hex", "p1_m2_jinv.hex", "p1_m3.hex", "p1_m5_masks.hex", "p1_chain.hex"):
        text = (VECTORS / name).read_text(encoding="utf-8")
        lines = text.splitlines()
        markers = [i for i, line in enumerate(lines) if line == "#DATA"]
        assert len(markers) == 1, name
        assert lines[markers[0] + 1], f"{name}: no data line after the marker"
        assert all(line.startswith("//") for line in lines[: markers[0]]), name


def test_sid_sequence_matches_the_model_for_a_long_record(exporter):
    """Independent re-derivation of the M1 golden straight from the model."""
    cfg = replace(Config.paper_literal(), dem_enable=True)
    bank = np.arange(1024) % 2
    expected = dem_state_sequence(1024, cfg, bank)
    text = exporter.build("paper_literal")["files"]["p1_m1_sid.hex"]
    rows = [ln.split() for ln in text.splitlines() if ln and not ln.startswith(("//", "#"))]
    assert len(rows) == 1024
    for i, row in enumerate(rows):
        assert int(row[1]) == bank[i]
        assert int(row[4], 16) == int(expected[i])


def test_changed_vectors_are_detected_as_stale(exporter, tmp_path):
    """向量侧的同类过期检测：手改黄金向量必须被 `--check` 抓住。"""
    args = ["--config", "paper_literal", "--out-vectors", str(tmp_path / "vec"), "--quiet"]
    assert exporter.main(args) == 0
    assert exporter.main([*args, "--check"]) == 0

    target = tmp_path / "vec" / "p1_m2_jinv.hex"
    text = target.read_text(encoding="utf-8")
    target.write_text(text.replace("#DATA", "#DATA_TAMPERED", 1), encoding="utf-8")
    assert exporter.main([*args, "--check"]) == 1

    target.write_text(text, encoding="utf-8")
    assert exporter.main([*args, "--check"]) == 0


def test_chain_vector_covers_a_nonzero_dither_chain(exporter):
    """T7 的价值在于"链路连通"；若激励退化成琐碎情形，它就不再证明任何事。

    金标按**模型给的 sid** 计算，而 TB 用 RTL 自己的 sid —— 链路错位即红。
    """
    stats = exporter.build("paper_literal")["report"]["chain"]
    assert stats["samples"] == 256
    assert stats["dither_mode"] == "quantizer", "只有 quantizer 能让 dither 非零且掩码提取仍精确"
    assert stats["nonzero_dither_samples"] > 0, "dither 全零会让 M3 的 dither 支路退出链路"
    assert stats["bridge"] == 1, "桥接关掉会让 M3 的零和交换退出链路"
    assert stats["covers_m3_sid_low"] is True
    assert "只驱动" in stats["note"]


# ======================================================================
# P2：L0 div_floor / adc2_dec、L2 oracle / 饱和 / 真实掩码 / 斜坡、L3 链路
# ======================================================================
P2_REPORT = VECTORS / "p2_report.json"
P2_HEX_FILES = (
    "p2_oracle_spec.hex",
    "p2_oracle_cfg.hex",
    "p2_scalar.hex",
    "p2_div.hex",
    "p2_sat.hex",
    "p2_alloc.hex",
    "p2_masks.hex",
    "p2_ramp.hex",
    "p2_recon_coef.hex",
    "p2_link_weights.hex",
    "p2_link_stim.hex",
    "p2_link_expected.hex",
)
P2_VECTOR_FILES = (*P2_HEX_FILES, "p2_regs.json")
P2_LIMIT = 512 * 1024


def _data_rows(text: str) -> list[list[str]]:
    """Return the token rows that follow the single ``#DATA`` marker."""
    lines = text.splitlines()
    markers = [i for i, line in enumerate(lines) if line == "#DATA"]
    assert len(markers) == 1
    return [line.split() for line in lines[markers[0] + 1 :] if line]


def _disk_rows(path: pathlib.Path) -> list[list[str]]:
    return _data_rows(path.read_text(encoding="utf-8"))


def _unpack_bits(value: int, size: int) -> np.ndarray:
    """Inverse of the exporter's LSB-first pack."""
    return np.array([(value >> p) & 1 for p in range(size)], dtype=np.int64)


@pytest.fixture(scope="module")
def p2(exporter):
    """One P2 build for the whole module (the 2**20 oracle is seconds, not free)."""
    built = exporter.build("paper_literal")
    return built["files"], built["p2_report"]


def test_p2_vectors_all_exist_and_stay_under_the_pre_commit_limit(p2):
    """文件存在性 + 512 KB 预算：超限会在导出器里直接失败，这里再独立量一次。"""
    files, report = p2
    for name in P2_VECTOR_FILES:
        path = VECTORS / name
        assert path.is_file(), f"{name} has not been exported"
        size = path.stat().st_size
        assert size < P2_LIMIT, f"{name} is {size} B, over the pre-commit 512 KB budget"
        assert report["files"][name] == size, f"{name}: report size disagrees with disk"
        assert name in files
    assert report["largest_file_bytes"] < P2_LIMIT


def test_p2_committed_vectors_are_current(exporter):
    """`--check` 漂移门禁必须覆盖 P2 的全部产物，包括 p2_report.json。"""
    assert (VECTORS / "p2_report.json").is_file()
    assert exporter.main(["--config", "paper_literal", "--check", "--quiet"]) == 0
    report = json.loads(P2_REPORT.read_text(encoding="utf-8"))
    assert report["bank_equals_index_mod_2"] is True
    assert report["scalar_min_gt_max_tie_cases"] == 4


def test_changed_p2_vectors_are_detected_as_stale(exporter, tmp_path):
    """能变红的证据：手改一个 P2 文件的**一个 token**，`--check` 必须返回非 0。"""
    out = tmp_path / "vec"
    args = ["--config", "paper_literal", "--out-vectors", str(out), "--quiet"]
    assert exporter.main(args) == 0
    assert exporter.main([*args, "--check"]) == 0

    target = out / "p2_alloc.hex"
    text = target.read_text(encoding="utf-8")
    rows = text.splitlines()
    marker = rows.index("#DATA")
    tokens = rows[marker + 1].split()
    tokens[0] = "ff" if tokens[0] != "ff" else "00"
    rows[marker + 1] = " ".join(tokens)
    target.write_text("\n".join(rows) + "\n", encoding="utf-8")
    assert exporter.main([*args, "--check"]) == 1

    target.write_text(text, encoding="utf-8")
    assert exporter.main([*args, "--check"]) == 0


def test_p2_every_vector_has_a_marker_and_a_comment_header(p2):
    """TB 用 `$fscanf` 紧跟 `#DATA`；注释头必须全是注释行。"""
    files, _ = p2
    for name in P2_HEX_FILES:
        lines = (VECTORS / name).read_text(encoding="utf-8").splitlines()
        markers = [i for i, line in enumerate(lines) if line == "#DATA"]
        assert len(markers) == 1, name
        assert lines[markers[0] + 1], name
        assert all(line.startswith("//") for line in lines[: markers[0]]), name
    assert not any(
        line == "#DATA"
        for line in (VECTORS / "p2_regs.json").read_text(encoding="utf-8").splitlines()
    )


# ---- L2 oracle：常量与性质 -------------------------------------------------
def test_oracle_constants_really_produce_the_identity_map(p2):
    """用**文件里的**权重与寄存器重建 oracle，抽样重跑 dout == c。

    这是非循环的：模型对象由向量文件的值构造，而不是由导出器的构造函数。
    """
    from adi_model.fixed_point import FixedPointFormat, FixedPointReconstructor
    from adi_model.weight_calibration import CalibrationSpec, DigitalObservation

    files, _ = p2
    rows = _disk_rows(VECTORS / "p2_oracle_spec.hex")
    weights = [int(token, 16) for token in rows[0]]
    assert len(rows) == 3, "冻结的行数是 3 行"
    assert all(row == rows[0] for row in rows), "3 行必须给出同一组权重"
    assert len(weights) == 3 and all(w > 0 for w in weights)
    assert sum(weights) == 2**30, "oracle 的构造要求 3 个正权重之和恰为 2**30"

    cfg = _disk_rows(VECTORS / "p2_oracle_cfg.hex")[0]
    min_q = int.from_bytes(bytes.fromhex(cfg[0]), "big", signed=True)
    max_q = int.from_bytes(bytes.fromhex(cfg[1]), "big", signed=True)
    offset_q = int.from_bytes(bytes.fromhex(cfg[2]), "big", signed=True)
    n_bits, out_count, identity = int(cfg[3], 16), int(cfg[4], 16), int(cfg[5], 16)
    assert (n_bits, out_count, identity) == (20, 2**20, 1)
    assert (min_q, max_q, offset_q) == (
        0,
        2**33,
        0,
    ), "adc2_max_q 必须是 2**33：docs/rtl/P2_INTERFACE.md §12 写的 2**25 已实测让 word 恒为 0"
    spec = CalibrationSpec(
        dac_n_main=1,
        dac_n_sub=2,
        n_active=1,
        dac_complete_range=True,
        dem_enable=False,
        dem_bridge_enable=False,
        n_slices=1,
        v_fs=3.0,
        adc2_n_bits=n_bits,
        adc2_v_min=0.0,
        adc2_v_max=6.0,
        dither_mode="off",
        dither_split_bank="sub",
        dither_units_range=0,
        dither_discrete=True,
    )
    model = FixedPointReconstructor(
        spec,
        FixedPointFormat(),
        np.array(weights, dtype=np.int64).reshape(spec.shape),
        offset_q,
        min_q,
        max_q,
    )
    codes = np.unique(np.concatenate([np.arange(0, out_count, 257), [0, out_count - 1]]))
    data = DigitalObservation(
        spec,
        np.zeros((codes.size, 1), np.int64),
        np.zeros(codes.size),
        np.zeros(codes.size, np.int64),
        codes,
        np.zeros(codes.size),
        np.zeros(codes.size),
        np.zeros(codes.size, bool),
        True,
    )
    stream = model.reconstruct(data)
    np.testing.assert_array_equal(stream.code, codes)
    assert not np.any(stream.clipped_low | stream.clipped_high | stream.analog_overflow)


def test_oracle_report_declares_the_property_not_the_matrix(p2):
    """2**20 行 oracle 不落文件；报告必须写清它的性质与位宽实测。"""
    _, report = p2
    assert "dout == adc2_code" in report["oracle_property"]
    assert "2**20" in report["oracle_property"]
    assert report["coverage"]["p2_oracle_spec.hex"]["oracle_words"] == 2**20
    assert report["coverage"]["p2_oracle_spec.hex"]["identity_verified"] is True
    assert report["coverage"]["p2_oracle_spec.hex"]["weights_sum"] == 2**30


# ---- L0 adc2_dec：ties-to-even --------------------------------------------
def _scalar_fields(row: list[str]) -> tuple[int, int, int, int, int, int, int]:
    """Decode one ``p2_scalar.hex`` row into plain Python integers."""
    return (
        int(row[0], 16),
        int(row[1], 16),
        int.from_bytes(bytes.fromhex(row[2]), "big", signed=True),
        int.from_bytes(bytes.fromhex(row[3]), "big", signed=True),
        int.from_bytes(bytes.fromhex(row[4]), "big", signed=True),
        int.from_bytes(bytes.fromhex(row[5]), "big", signed=True),
        int(row[6]),
    )


def test_scalar_vector_reproduces_signed_ties_to_even(p2):
    """`test_signed_ties_to_even` 的 8 个样例必须在 RTL 侧可复现（除数 2）。

    构造是 delta = 2x、P_ADC2_BITS = 1 -> k = 2，于是
    ``round_even_divide(2x, 4) == round_even_divide(x, 2)`` 逐值相等；
    若改成 delta = 4x 就退化成整除，余数恒为 0，一个平局都测不到。
    """
    files, report = p2
    rows = _disk_rows(VECTORS / "p2_scalar.hex")
    assert len(rows) <= 512
    ties = {}
    for row in rows:
        code, n_bits, mn, mx, _, fine, _ = _scalar_fields(row)
        if n_bits == 1 and code == 0 and mn == 1000:
            ties[(mx - mn) // 2] = fine - 1000
    assert ties == {-7: -4, -5: -2, -3: -2, -1: 0, 1: 0, 3: 2, 5: 2, 7: 4}
    min_gt_max = sum(1 for row in rows if _scalar_fields(row)[2] > _scalar_fields(row)[3])
    assert min_gt_max >= 4, "前 8 行里有 4 个负 delta 的刻意用例"
    assert report["scalar_min_gt_max_cases"] == min_gt_max
    assert report["scalar_min_gt_max_tie_cases"] == 4
    assert report["coverage"]["p2_scalar.hex"]["ties_to_even_samples"] == 8
    assert 1 in report["coverage"]["p2_scalar.hex"]["adc2_n_bits_used"]


def test_scalar_expected_fine_matches_the_model_for_every_row(p2):
    """逐行独立复算 exp_fine_q（模型自己的 round_even_divide）与 exp_ovf。"""
    from adi_model.fixed_point import round_even_divide

    files, _ = p2
    rows = _disk_rows(VECTORS / "p2_scalar.hex")
    assert len(rows[0]) == 7, "adc2_n_bits 列必须在（冻结表漏了它，已在报告里登记）"
    widths = set()
    for row in rows:
        code, n_bits, mn, mx, _, fine, ovf = _scalar_fields(row)
        widths.add(n_bits)
        assert 1 <= n_bits <= 20
        assert code < 2**n_bits
        assert fine == mn + round_even_divide((2 * code + 1) * (mx - mn), 2 ** (n_bits + 1))
        assert ovf == int(not -(2**63) <= fine < 2**63)
        assert ovf == 0, "合法寄存器下 adc2_dec 的 ovf 结构上不可达（见报告 findings）"
    assert {1, 2, 3, 4, 12, 20} <= widths, "ties 用的 k=2..5、生产位宽 12 与 n_bits=20 都要覆盖到"


# ---- L0 div_floor：TB 侧定义式自检 ----------------------------------------
def test_div_vector_satisfies_the_definition_independently(p2):
    """独立判据（不是模型复刻）：q*d <= a < (q+1)*d（d > 0）。"""
    files, report = p2
    rows = _disk_rows(VECTORS / "p2_div.hex")
    assert len(rows) <= 128
    for row in rows:
        a = int.from_bytes(bytes.fromhex(row[0]), "big", signed=True)
        d = int(row[1], 16)
        q = int.from_bytes(bytes.fromhex(row[2]), "big", signed=True)
        assert d > 0 and d < 2**60
        assert -(2**62) <= a < 2**62
        assert q * d <= a < (q + 1) * d, (a, d, q)
        assert q == a // d
    assert report["coverage"]["p2_div.hex"]["cases"] == len(rows)
    assert any(int.from_bytes(bytes.fromhex(r[0]), "big", signed=True) < 0 for r in rows)


# ---- L2 饱和：剪裁与模拟饱和分开 ------------------------------------------
def test_sat_vector_reproduces_the_saturation_oracle(p2, exporter):
    """逐行用模型重算 exp_word / clip_low / clip_high，并复现既有单元测试的 16 个样例。"""
    files, _ = p2
    rows = _disk_rows(VECTORS / "p2_sat.hex")
    assert len(rows) <= 64
    clip_low = clip_high = 0
    for row in rows:
        inj_q = int.from_bytes(bytes.fromhex(row[0]), "big", signed=True)
        code = int(row[1], 16)
        scale_bits = exporter.P2_SAT_SCALE_BITS * int(row[2])
        word = int(row[3], 16)
        low, high = int(row[4]), int(row[5])
        model = exporter._p2_sat_model(scale_bits)
        exp_word, exp_low, exp_high = exporter._p2_sat_probe(model, code, inj_q)
        assert (word, low, high) == (exp_word, exp_low, exp_high), row
        clip_low += low
        clip_high += high
    assert clip_low > 0 and clip_high > 0, "剪裁的两端都必须真的出现过"


def test_sat_body_repeats_test_output_saturation_is_separate_from_analog_saturation(p2):
    """前 32 行（w_scale = 0 / 1）就是那个单元测试的 16 个样例。"""
    files, _ = p2
    rows = _disk_rows(VECTORS / "p2_sat.hex")
    base = [row for row in rows if int(row[2]) == 0][:16]
    assert len(base) == 16
    np.testing.assert_array_equal([int(r[1], 16) for r in base], np.arange(16))
    injected = [int.from_bytes(bytes.fromhex(r[0]), "big", signed=True) for r in base]
    assert injected[0] > 0 and injected[-1] < 0
    assert int(base[0][4]) == 1 and int(base[0][5]) == 0
    assert int(base[-1][5]) == 1 and int(base[-1][4]) == 0
    assert int(base[0][3], 16) == 0 and int(base[-1][3], 16) == 2**20 - 1


# ---- L0 slice_alloc：pingpong 与 INV-4a -----------------------------------
def test_alloc_vector_matches_the_model_and_inv4a(p2):
    """64 拍逐拍与 timing.build_slice_plan 相等，且 INV-4a/4b 成立。"""
    from adi_model.timing import build_slice_plan

    files, report = p2
    rows = _disk_rows(VECTORS / "p2_alloc.hex")
    assert len(rows) == 64
    cfg = Config.paper_literal()
    n_act = int(cfg.n_active)
    plan = build_slice_plan(
        cfg, 64, np.random.default_rng(cfg.seed + 4201), "pingpong", spare_period=None
    )
    for n, row in enumerate(rows):
        assert int(row[0], 16) == n
        acq = [int(v, 16) for v in row[1 : 1 + n_act]]
        conv = [int(v, 16) for v in row[1 + n_act : 1 + 2 * n_act]]
        bank, valid = int(row[1 + 2 * n_act]), int(row[2 + 2 * n_act])
        assert acq == list(plan.acq[n]), n
        assert conv == list(plan.conv[n]), n
        assert bank == n % 2
        assert valid == int(plan.valid[n]) == int(n >= 1)
        assert len(set(acq)) == n_act and len(set(conv)) == n_act, "INV-4b"
        assert max(acq + conv) < 16, "INV-4d：pingpong 只用 0..15"
        if n:
            assert conv == [int(v) for v in plan.acq[n - 1]], "INV-4a"
    assert report["coverage"]["p2_alloc.hex"]["inv_4a_conv_equals_previous_acq"] is True


# ---- L2 真实掩码 ----------------------------------------------------------
def test_mask_vector_is_the_models_own_scatter_and_a_thermometer(p2, exporter):
    """掩码必须等于 `split_switch_command` 的 scatter 结果（独立于导出器的 _terms 反演）。"""
    from adi_model.dem import split_switch_command
    from adi_model.pipeline import run_pipeline
    from adi_model.sampler import sine_input
    from adi_model.weight_calibration import CalibrationSpec

    files, _ = p2
    rows = _disk_rows(VECTORS / "p2_masks.hex")
    assert len(rows) == exporter.P2_MASKS_SAMPLES
    cfg = Config.paper_literal()
    run_cfg = replace(cfg, dem_enable=True, dither_mode="quantizer", dither_discrete=True)
    result = run_pipeline(
        run_cfg,
        sine_input(
            exporter.P2_MASKS_AMPLITUDE_FRAC * run_cfg.v_fs,
            run_cfg.fs * exporter.P2_MASKS_TONE_NUM / exporter.P2_MASKS_TONE_DEN,
        ),
        exporter.P2_MASKS_SAMPLES,
    )
    spec = CalibrationSpec.from_config(run_cfg)
    n_act, n_main, n_sub = int(cfg.n_active), int(cfg.dac_n_main), int(cfg.dac_n_sub)
    for i, row in enumerate(rows):
        slice_ids = [int(v, 16) for v in row[:n_act]]
        assert slice_ids == [int(v) for v in result.conv_slice_ids[i]]
        command = split_switch_command(
            spec,
            np.asarray([np.rint(result.k[i])], dtype=np.int64),
            np.asarray([result.sid[i]], dtype=np.int64),
        )
        for a in range(n_act):
            mains = int(row[n_act + a], 16)
            subs = int(row[2 * n_act + a], 16)
            for counts, order, size, packed in (
                (command.main_counts, command.main_order, n_main, mains),
                (command.sub_counts, command.sub_order, n_sub, subs),
            ):
                count = int(counts[0][a])
                mask = np.zeros(size, dtype=np.int64)
                mask[np.asarray(order[0])[:count]] = 1
                assert int(mask.sum()) == count, "温度计：popcount == count"
                assert packed == sum(
                    int(mask[p]) << p for p in range(size)
                ), f"row {i} slice {a}: exported mask is not the model's scatter"
        # 期望字由模型给出，这里只检查取值范围与剪裁口径的自洽
        word, low, high = int(row[-3], 16), int(row[-2]), int(row[-1])
        assert 0 <= word < 2**20
        assert (low, high) == (0, 0), "这批用例必须留在量程内，否则剪裁口径会被混淆"


def test_mask_report_claims_a_thermometer_and_real_slices(p2):
    _, report = p2
    stats = report["coverage"]["p2_masks.hex"]
    assert stats["thermometer_verified"] is True
    assert stats["cases"] == 512
    assert stats["slice_alloc_uses_only_0_to_15"] is True
    assert stats["nonzero_injection_cases"] > 0
    assert stats["distinct_sids"] > 1


# ---- L2 斜坡 --------------------------------------------------------------
def test_ramp_vector_covers_both_sides_of_every_carry(p2, exporter):
    """口径断言：每个进位都同时有 rel_step < 0 与 > 0 的行，且抽稀前后都单调。"""
    files, report = p2
    rows = _disk_rows(VECTORS / "p2_ramp.hex")
    steps = exporter.P2_RAMP_REL_STEPS
    assert len(rows) == exporter.P2_RAMP_CARRIES * len(steps)
    assert len(rows) > 1000, "抽稀后仍要覆盖全部 511 个进位"
    per_carry: dict[int, list[tuple[int, int, int]]] = {}
    for index, row in enumerate(rows):
        carry, offset = divmod(index, len(steps))
        rel = int.from_bytes(bytes.fromhex(row[-1]), "big", signed=True)
        assert rel == steps[offset]
        word = int(row[-4], 16)
        assert 0 <= word < 2**20
        per_carry.setdefault(carry, []).append((rel, word, row[-3]))
    assert len(per_carry) == exporter.P2_RAMP_CARRIES
    for carry, entries in per_carry.items():
        rels = [e[0] for e in entries]
        assert any(r < 0 for r in rels) and any(r > 0 for r in rels), carry
        words = [e[1] for e in entries]
        assert all(0 <= b - a <= 1 for a, b in itertools.pairwise(words)), carry
    assert report["coverage"]["p2_ramp.hex"]["points_per_carry"] == len(steps)
    assert report["coverage"]["p2_ramp.hex"]["full_grid_rows"] == 511 * 129


def test_ramp_masks_are_nested_within_each_carry(p2, exporter):
    """同一进位内 k 单调升 -> 主掩码逐位包含、**总单位数**非降（文件内部自洽）。

    子阵列的位掩码本身**不**嵌套：子计数 = k mod n_sub，会在每个主进位处从 7 回绕到 0。
    单调的是总单位数 ``popcount(main)*n_sub + popcount(sub)``（= k mod dac_levels）。
    """
    files, _ = p2
    rows = _disk_rows(VECTORS / "p2_ramp.hex")
    n_act, n_main, n_sub = 8, 63, 8
    steps = len(exporter.P2_RAMP_REL_STEPS)
    for carry in range(0, exporter.P2_RAMP_CARRIES, 37):
        base = carry * steps
        for a in range(n_act):
            packed = [int(rows[base + j][n_act + a], 16) for j in range(steps)]
            for prev, nxt in itertools.pairwise(packed):
                assert prev & ~nxt == 0, f"carry {carry} slice {a}: main mask is not nested"
            subs = [int(rows[base + j][2 * n_act + a], 16) for j in range(steps)]
            totals = [
                bin(packed[j]).count("1") * n_sub + bin(subs[j]).count("1") for j in range(steps)
            ]
            for prev, nxt in itertools.pairwise(totals):
                assert 0 <= nxt - prev <= 1, f"carry {carry} slice {a}: unit count jumped"
            assert all(bin(s).count("1") < n_sub for s in subs)
            assert all(p < 2**n_main for p in packed)


# ---- 生产三系数：与寄存器镜像同源 -----------------------------------------
def test_recon_coef_matches_the_production_register_image(p2):
    """1 行 3 列，且逐项等于 p2_regs.json 的 offset_q/adc2_min_q/adc2_max_q。

    两份产物必须同源：TB 用这个文件配 recon_core，用 p2_regs.json 配链路，
    两者一旦脱节就会得到"每个文件都自洽、合起来却错"的假绿。
    """
    files, _ = p2
    rows = _disk_rows(VECTORS / "p2_recon_coef.hex")
    assert len(rows) == 1, "冻结要求就是 1 行数据"
    assert len(rows[0]) == 3, "冻结要求就是 3 列"
    offset_q, adc2_min_q, adc2_max_q = (
        int.from_bytes(bytes.fromhex(token), "big", signed=True) for token in rows[0]
    )
    regs = json.loads((VECTORS / "p2_regs.json").read_text(encoding="utf-8"))
    assert (offset_q, adc2_min_q, adc2_max_q) == (
        regs["offset_q"],
        regs["adc2_min_q"],
        regs["adc2_max_q"],
    ), "p2_recon_coef.hex 与 p2_regs.json 脱节"
    assert adc2_min_q < adc2_max_q
    for value in (offset_q, adc2_min_q, adc2_max_q):
        assert -(2**63) <= value < 2**63, "Q32 系数必须装进 64 bit 有符号寄存器"


def test_recon_coef_is_the_same_instance_the_other_vectors_used(p2):
    """非循环复核：从 p2_regs.json 的 spec 按定义式重算 min/max，必须逐值相等。

    定义式 = from_weights 里的 ``rint(v / v_fs * 2**32)``（v 已归一化），
    不复用导出器的任何函数。这一条才真正排除"三份产物一起错成另一个值"。
    """
    files, _ = p2
    regs = json.loads((VECTORS / "p2_regs.json").read_text(encoding="utf-8"))
    spec = regs["spec"]
    frac = regs["format"]["voltage_fraction_bits"]
    expected = [
        int(np.rint(spec["adc2_v_min"] / spec["v_fs"] * 2**frac)),
        int(np.rint(spec["adc2_v_max"] / spec["v_fs"] * 2**frac)),
    ]
    row = _disk_rows(VECTORS / "p2_recon_coef.hex")[0]
    assert [int.from_bytes(bytes.fromhex(t), "big", signed=True) for t in row[1:]] == expected
    assert int.from_bytes(bytes.fromhex(row[0]), "big", signed=True) == regs["offset_q"]


def test_recon_coef_report_records_its_source(p2):
    """报告必须写明它来自哪一次 from_result，并把三份产物的同源判定记下来。"""
    _, report = p2
    source = report["recon_coef_source"]
    assert "from_result" in source
    assert "p2_masks.hex" in source and "p2_ramp.hex" in source and "p2_link" in source
    stats = report["coverage"]["p2_recon_coef.hex"]
    assert stats["triples_agree"] is True
    assert sorted(stats["sources"]) == ["p2_link_expected.hex", "p2_masks.hex", "p2_ramp.hex"]
    row = _disk_rows(VECTORS / "p2_recon_coef.hex")[0]
    assert [stats["offset_q"], stats["adc2_min_q"], stats["adc2_max_q"]] == [
        int.from_bytes(bytes.fromhex(t), "big", signed=True) for t in row
    ]
    assert stats["adc2_n_bits"] == 12, "生产链路的 ADC2 位宽是 12"
    assert "p2_recon_coef.hex" in " ".join(report["findings"])


# ---- L3 链路：coarse 覆盖域 ------------------------------------------------
def _link_stim_files() -> list[pathlib.Path]:
    """所有 link **激励**文件（``p2_link_stim*.hex``，留出拆文件时的后缀位）。"""
    return sorted(VECTORS.glob("p2_link_stim*.hex"))


def _link_coarse_values() -> list[int]:
    """把全部 link 激励文件的 ``coarse`` 列并起来（第 1 列，3 个 hex 字符）。"""
    names = _link_stim_files()
    assert names, "一个 link 激励文件都没有 —— 覆盖域无从谈起"
    values: list[int] = []
    for path in names:
        values.extend(int(row[1], 16) for row in _disk_rows(path))
    return values


def test_link_vectors_cover_the_whole_coarse_domain(p2):
    """链路激励的 coarse 必须覆盖 [0, 511] **全域**，两端都要有对照值。

    为什么值得单列一条：L3 只能对"向量里出现过的 coarse"给结论。窄化实验实测到
    coarse 只覆盖 [50, 462]，于是 ``coarse ≈ 0`` 附近观察到 ``clip_high = 1`` 也只能登记、
    不能下结论 —— 那里恰好是桥接守卫 ``amount = min(1, min(code, 511 - code))`` 与
    signs 零和交换的边界。洞补上了，"两端没有对照值"就不再是借口。
    """
    values = _link_coarse_values()
    domain = set(values)
    assert min(values) == 0, f"coarse 最小实测 {min(values)} —— 低端没有对照值"
    assert max(values) == 511, f"coarse 最大实测 {max(values)} —— 高端没有对照值"
    missing = sorted(set(range(512)) - domain)
    assert not missing, f"coarse 只覆盖 {len(domain)}/512 个值，缺 {missing[:12]}"
    # 报告里的统计必须是**实测**的，且与这里独立数出来的数字一致
    stats = p2[1]["coverage"]["p2_link_stim.hex"]
    assert stats["coarse_min"] == min(values)
    assert stats["coarse_max"] == max(values)
    assert stats["coarse_distinct"] == len(domain)
    assert p2[1]["link_checks"]["coarse_range"] == [min(values), max(values)]
    # 两端必须各有若干行，而不是"只有一个点碰巧落在边界上"
    low = {v for v in values if v <= 49}
    high = {v for v in values if v >= 463}
    assert len(low) == 50, f"低端只覆盖 {len(low)}/50 个 coarse"
    assert len(high) == 49, f"高端只覆盖 {len(high)}/49 个 coarse"


# ---- L3 链路 --------------------------------------------------------------
def test_link_stimulus_and_expectation_match_a_fresh_model_run(p2, exporter):
    """独立重跑一次同波形的模型，与两个文件逐样本比对（含 inj_q 的重建）。"""
    from adi_model.pipeline import run_pipeline
    from adi_model.sampler import sine_input

    files, _ = p2
    stim = _disk_rows(VECTORS / "p2_link_stim.hex")
    expected = _disk_rows(VECTORS / "p2_link_expected.hex")
    assert len(stim) == len(expected) == exporter.P2_LINK_SAMPLES
    cfg = Config.paper_literal()
    run_cfg = replace(cfg, dem_enable=True, dither_mode="quantizer", dither_discrete=True)
    result = run_pipeline(
        run_cfg,
        sine_input(
            exporter.P2_AMPLITUDE_FRAC * run_cfg.v_fs,
            run_cfg.fs * exporter.P2_TONE_NUM / exporter.P2_TONE_DEN,
            exporter.P2_LINK_PHASE,
        ),
        exporter.P2_LINK_SAMPLES,
    )
    stream = result.to_codes()
    units = int(cfg.dac_levels) // (2 ** int(cfg.b1))
    from adi_model.weight_calibration import DigitalObservation

    data = DigitalObservation.from_result(result)
    for i, (srow, erow) in enumerate(zip(stim, expected, strict=True)):
        assert len(srow) == 8, "第 6/7/8 列是模拟域标志（rdac_over / adc2_over / ra_sat）"
        assert int(srow[0]) == int(result.bank[i]) == i % 2
        assert int(srow[1], 16) == int(result.coarse[i])
        k = int(np.rint(result.k[i]))
        assert (
            int.from_bytes(bytes.fromhex(srow[2]), "big", signed=True)
            == k - int(result.coarse[i]) * units
        )
        inj_q = int(np.rint(float(data.common_injection_v[i]) / cfg.v_fs * 2**32))
        assert int.from_bytes(bytes.fromhex(srow[3]), "big", signed=True) == inj_q
        assert int(srow[4], 16) == int(result.adc2_code[i])
        assert int(erow[0], 16) == int(stream.code[i])
        assert int(erow[1]) == int(stream.clipped_low[i])
        assert int(erow[2]) == int(stream.clipped_high[i])
        assert int(erow[3]) == int(stream.analog_overflow[i])


# ---- L3 模拟域三列：数字-only TB 只能靠向量驱动 ----------------------------
def test_link_analog_flag_columns_drive_the_contract_or(p2, exporter):
    """第 6/7/8 列 = rdac_over / adc2_over / ra_sat，逐行必须与模型同源。

    为什么需要这三列：L3 的 TB 是**数字-only** 的，模拟链不在里面，所以
    ``recon_core`` 的 ``rdac_ovf`` / ``adc2_over`` / ``ra_sat`` 只能由向量驱动。
    契约（§4.6）说 ``analog_ovf = rdac_ovf | adc2_over | ra_sat``，而
    ``weight_calibration.py:113`` 正是这么定义 ``data.overflow`` 的 ——
    所以三列之或必须**逐样本**等于 ``p2_link_expected.hex`` 第 4 列。
    这条等式成立，TB 才算真的有独立判据，而不是自证。
    """
    from adi_model.pipeline import run_pipeline
    from adi_model.sampler import sine_input

    _, report = p2
    stim = _disk_rows(VECTORS / "p2_link_stim.hex")
    expected = _disk_rows(VECTORS / "p2_link_expected.hex")
    cfg = Config.paper_literal()
    run_cfg = replace(cfg, dem_enable=True, dither_mode="quantizer", dither_discrete=True)
    result = run_pipeline(
        run_cfg,
        sine_input(
            exporter.P2_AMPLITUDE_FRAC * run_cfg.v_fs,
            run_cfg.fs * exporter.P2_TONE_NUM / exporter.P2_TONE_DEN,
            exporter.P2_LINK_PHASE,
        ),
        exporter.P2_LINK_SAMPLES,
    )
    columns = ("rdac_over", "adc2_over", "ra_sat")
    model = {
        "rdac_over": np.asarray(result.rdac_over, dtype=np.int64),
        "adc2_over": np.asarray(result.adc2_over, dtype=np.int64),
        "ra_sat": np.asarray(result.ra_sat, dtype=np.int64),
    }
    counts = dict.fromkeys(columns, 0)
    asserted_or = 0
    for i, (srow, erow) in enumerate(zip(stim, expected, strict=True)):
        flags = {}
        for offset, name in enumerate(columns):
            token = srow[5 + offset]
            assert len(token) == 1, f"{name} 必须是 1 个 hex 数字（TB 取 bit[0]）"
            value = int(token, 16)
            assert value in (0, 1), f"row {i} {name} = {token} 不是 0/1"
            assert value == int(model[name][i]), (i, name)
            flags[name] = value
            counts[name] += value
        # 契约 §4.6：三者之或 == analog_ovf（第 4 列）
        or_value = flags["rdac_over"] | flags["adc2_over"] | flags["ra_sat"]
        assert or_value == int(erow[3]), f"row {i}: OR != analog_ovf"
        asserted_or += 1
    assert asserted_or == exporter.P2_LINK_SAMPLES
    assert counts["rdac_over"] > 0, "全域波形必须真的产生 rdac_over 行"

    stats = report["coverage"]["p2_link_stim.hex"]
    assert stats["analog_flag_columns"] == list(columns)
    for name in columns:
        assert stats[f"{name}_rows"] == counts[name], name
    assert stats["analog_flag_rows"] == int(
        np.count_nonzero(
            np.asarray(result.rdac_over) | np.asarray(result.adc2_over) | np.asarray(result.ra_sat)
        )
    ), "analog_flag_rows 必须是三列之或的实测计数"
    # 账本里的归属分布必须能对上（both / only_rdac / only_adc2 / neither）
    attr = stats["analog_flag_attribution"]
    rdac = model["rdac_over"].astype(bool)
    adc2 = model["adc2_over"].astype(bool)
    assert attr["both_rdac_and_adc2"] == int(np.count_nonzero(rdac & adc2))
    assert attr["only_rdac"] == int(np.count_nonzero(rdac & ~adc2))
    assert attr["only_adc2"] == int(np.count_nonzero(~rdac & adc2))
    assert attr["neither_rdac_nor_adc2"] == int(np.count_nonzero(~rdac & ~adc2))
    assert attr["only_ra_sat"] == int(
        np.count_nonzero(model["ra_sat"].astype(bool) & ~(rdac | adc2))
    )


def test_link_weights_match_the_register_image(p2):
    """`p2_link_weights.hex` 必须与 `p2_regs.json` 的 weights_q 逐项一致（18 x 71）。"""
    files, _ = p2
    rows = _disk_rows(VECTORS / "p2_link_weights.hex")
    regs = json.loads((VECTORS / "p2_regs.json").read_text(encoding="utf-8"))
    weights = regs["weights_q"]
    assert len(rows) == len(weights) * len(weights[0]) == 18 * 71
    for row in rows:
        s, u = int(row[0], 16), int(row[1], 16)
        assert int(row[2], 16) == weights[s][u], (s, u)
    assert regs["adc2_min_q"] < regs["adc2_max_q"]
    assert sum(sum(w) for w in weights) < 2**60
    assert all(0 < w < 2**47 for w in (v for row in weights for v in row))


def test_link_report_records_the_measured_facts(p2, exporter):
    """报告里那几个字段必须是**实测**的，不是抄的。"""
    _, report = p2
    assert (
        report["bank_equals_index_mod_2"] is True
    ), "P2 接口文档 §5 说若不等以模型为准；这里实测 result.bank == n % 2"
    checks = report["link_checks"]
    assert checks["dout_equals_adc2_code"] is False, "dout 是最终 20 bit 字，不是后端码"
    assert checks["max_abs_dout_minus_adc2_code"] > 0
    assert checks["distinct_adc2_codes"] > 1
    assert checks["nonzero_injection_samples"] > 0
    # 全域波形（A = 1.0*v_fs）**必须**真的顶到量程两端：剪裁行是刻意要对照的边界段，
    # 若一条都没有，说明幅值没到 ±v_fs，"全域"就是假的。
    assert checks["clip_low_cases"] >= 1 and checks["clip_high_cases"] >= 1, (
        f"clip_low={checks['clip_low_cases']} clip_high={checks['clip_high_cases']} —— "
        "全域波形没有顶到两端，coarse 覆盖声明需要重新核对"
    )
    assert checks["coarse_range"] == [0, 511]
    assert checks["distinct_sids"] == 512, "4096 拍的链路激励应当扫过 DEM 的全部状态"
    stim = report["coverage"]["p2_link_stim.hex"]
    assert stim["rows"] == exporter.P2_LINK_SAMPLES
    assert report["coverage"]["p2_link_expected.hex"]["rows"] == exporter.P2_LINK_SAMPLES


def test_p2_report_records_the_deviation_from_the_frozen_table(p2):
    """契约不符的发现必须留在报告里，不能被静默吞掉。"""
    _, report = p2
    joined = " ".join(report["findings"])
    assert "2**33" in joined and "2**25" in joined, "oracle 的 adc2_max_q 更正必须登记"
    assert "512 KB" in joined, "斜坡超限的处置必须登记"
    assert "ovf" in joined, "adc2_dec 的 ovf 不可达必须登记"
    assert "slice(1)/unit(1)" in joined, "列宽更正必须登记"
    assert "4096" in joined and "[50, 462]" in joined, "coarse 覆盖洞的补全与代价必须登记"
    peaks = report["peak_bits"]
    for group in ("oracle", "ramp", "link"):
        assert peaks[group]["shifted"] >= peaks[group]["num"] - 2
        assert peaks[f"{group}_model_peak_accumulator_bits"] == max(peaks[group].values()) + 1


# ---- 覆盖账本：逐文件逐列的 {min, max, distinct} ---------------------------
def test_coverage_audit_covers_every_vector_file_and_column(p2, exporter):
    """账本必须覆盖每个向量文件的每个输入列，且列数与行里的 token 数一致。

    这一条同时是**列定义漂移**的门禁：改了行格式却忘了改 `P2_AUDIT_COLUMNS`，
    导出期就会先抛（token 数不符），这里再从报告侧独立核对一遍。
    """
    _, report = p2
    audit = report["coverage_audit"]
    for name in exporter.P2_AUDIT_COLUMNS:
        assert name in audit, f"{name} 不在覆盖账本里"
        assert name in report["files"], f"{name} 不在产物清单里"
    assert set(audit) == set(exporter.P2_AUDIT_COLUMNS) | {"p2_regs.json"}
    for name, spec in exporter.P2_AUDIT_COLUMNS.items():
        rows = _disk_rows(VECTORS / name)
        assert audit[name]["rows"] == len(rows)
        assert set(audit[name]["columns"]) == {column for column, _ in spec}
        for row in rows:
            assert len(row) == len(spec), f"{name}: 列台账与行 token 数不符"
        for column, _ in spec:
            entry = audit[name]["columns"][column]
            assert entry["min"] <= entry["max"] and entry["distinct"] >= 1


def test_coverage_audit_numbers_are_recomputed_independently(p2, exporter):
    """独立复算账本：不信任报告里的数字，从磁盘读 token 自己重数。

    只挑几张有代表性的表（含符号列与打包掩码列），但**逐列**核对，
    这样"账本抄错了"或者"某一列悄悄退化成常量"都会被抓住。
    """
    _, report = p2
    audit = report["coverage_audit"]
    checks = 0
    for name in ("p2_scalar.hex", "p2_div.hex", "p2_link_stim.hex", "p2_masks.hex"):
        spec = exporter.P2_AUDIT_COLUMNS[name]
        rows = _disk_rows(VECTORS / name)
        for index, (column, signed) in enumerate(spec):
            values = [exporter._decode_hex_token(row[index], signed) for row in rows]
            assert audit[name]["columns"][column] == {
                "min": min(values),
                "max": max(values),
                "distinct": len(set(values)),
            }, f"{name}.{column} 的账本与独立复算不符"
            checks += 1
    assert checks >= 30, "复算的列数太少，这条门禁没有实际覆盖面"
    # 账本要能回答"某个量到底量到了哪些值"：至少这些退化风险高的列要有非平凡取值
    stim = audit["p2_link_stim.hex"]["columns"]
    assert stim["coarse"]["distinct"] == 512
    assert stim["coarse"]["min"] == 0 and stim["coarse"]["max"] == 511
    assert stim["dither"]["distinct"] > 1 and stim["dither"]["min"] < 0 < stim["dither"]["max"]
    assert audit["p2_regs.json"]["columns"]["weights_q"]["distinct"] > 1
    assert audit["p2_regs.json"]["columns"]["adc2_min_q"]["min"] == -53687091


def test_p2_row_counts_match_the_frozen_table(p2, exporter):
    """行数与冻结表对齐（斜坡是唯一被迫偏离的一项，已在报告里登记）。"""
    files, report = p2
    rows = report["file_rows"]
    assert rows["p2_oracle_spec.hex"] == 3
    assert rows["p2_oracle_cfg.hex"] == 1
    assert rows["p2_alloc.hex"] == 64
    assert rows["p2_masks.hex"] == 512
    assert rows["p2_recon_coef.hex"] == 1, "三系数文件固定 1 行 3 列"
    assert rows["p2_link_weights.hex"] == 18 * 71
    assert rows["p2_link_stim.hex"] == rows["p2_link_expected.hex"] == exporter.P2_LINK_SAMPLES
    assert exporter.P2_LINK_SAMPLES == 4096, (
        "链路行数从冻结表的 512 改到 4096 是为了 coarse 全域覆盖；"
        "再改这个数必须同时复核 clamp 覆盖与 512 KB 上限"
    )
    assert rows["p2_scalar.hex"] <= 512
    assert rows["p2_div.hex"] <= 128
    assert rows["p2_sat.hex"] <= 64
    assert rows["p2_ramp.hex"] == exporter.P2_RAMP_CARRIES * len(exporter.P2_RAMP_REL_STEPS)
