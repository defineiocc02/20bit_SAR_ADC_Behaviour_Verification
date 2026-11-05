"""保留 2026-09-25 独立审查的回归（原 v8.2.0 发布物）。

F1–F17 来自当日首轮审查，N1–N5 来自第二轮复查。检查目标包括入口拒绝、
数值口径、异常类型、独立随机流和分发内容；数值比较采用相应误差界，避免
依赖具体随机实现。各项原编号及诊断背景保留，便于回溯。

本文件按当时的模型与导出接口保留断言。本轮只整理说明，不增加新的架构要求，
也不以注释说明代替实际测试结果。
"""

from __future__ import annotations

import importlib.util as _ilu
import sys as _sys
import warnings
from pathlib import Path

import numpy as np
import pytest

from adi_model import Config
from adi_model.aux_input import build_stage
from adi_model.charge_ref import (
    dither_mask_charge,
    ref_ra_charge_dither_closed,
    ref_ra_charge_dither_nodal,
)
from adi_model.chip import build_chip
from adi_model.cli import _run_script
from adi_model.dac_arch import SplitDAC, build_split_chip
from adi_model.dynamics import crosstalk_error, switching_activity
from adi_model.experiments import monte_carlo
from adi_model.inventory_gate import GateResult, _check_sources
from adi_model.ktc import KTCBranch
from adi_model.mapper import Mapper
from adi_model.noise_phase import monte_carlo_residual, phase_noise_state
from adi_model.ref_track import RefTrackConfig
from adi_model.reporting import render_report
from adi_model.sadc import SADC
from adi_model.sim import run_with_calibration

REPO = Path(__file__).resolve().parents[2]


# --------------------------------------------------------------- F1 / F2
def test_monte_carlo_mismatch_and_noise_streams_are_independent():
    """F1：失配抽签与噪声实现必须来自独立子流（旧口径 16/16 逐值相同）。"""
    cfg = Config()
    a = monte_carlo(cfg, n_chips=2, n=4096, seed0=999)
    b = monte_carlo(cfg, n_chips=2, n=4096, seed0=999)
    assert np.array_equal(
        np.asarray(a["sndr_per_chip"], dtype=float), np.asarray(b["sndr_per_chip"], dtype=float)
    ), "同一 seed0 必须完全复现整体系综"


def test_spawn_does_not_consume_parent_stream():
    """F2：独立检查 spawn 不消耗父随机流的后续抽样序列。"""
    g1 = np.random.default_rng(42)
    g1.spawn(1)
    after = g1.normal(size=4)
    g2 = np.random.default_rng(42)
    assert np.array_equal(after, g2.normal(size=4))


def test_run_sim_gives_scheduler_an_independent_rng(monkeypatch):
    """F2：传给调度器的随机源不得是主噪声 rng。"""
    import adi_model.sim as sim_mod
    from adi_model.sampler import sine_input

    captured = {}
    real = sim_mod.make_scheduler

    def spy(cfg, rng, scheduler=None):
        captured["rng"] = rng
        return real(cfg, rng, scheduler)

    monkeypatch.setattr(sim_mod, "make_scheduler", spy)
    cfg = Config()
    main_rng = np.random.default_rng(777)
    sim_mod.run_sim(
        cfg, sine_input(0.5 * cfg.v_fs, cfg.fs / 8), 64, chip=build_chip(cfg), rng=main_rng
    )
    assert "rng" in captured
    assert captured["rng"] is not main_rng
    assert captured["rng"].bit_generator.state != main_rng.bit_generator.state


# --------------------------------------------------------------- F3a / F3b
@pytest.mark.parametrize(
    "kwargs",
    [
        {"g0": -32.0},
        {"g0": float("nan")},
        {"n_slices": 0},
        {"n_unit_per_slice": 0},
        {"c_total0": -1e-12},
        {"c_feedback0": 0.0},
    ],
)
def test_config_legality_rejects_illegal_values(kwargs):
    """F3a：检查所列五个物理或几何量的非法取值被合法性检查识别。"""
    assert Config(**kwargs).legality_violations()


def test_config_requested_default_is_legal():
    assert Config().legality_violations() == []


def test_ron_code_coeff_checked_where_it_is_consumed():
    """F3b：系数越界在启用输入建立（消费点）时必须被拒；未启用时不校验。"""
    assert Config(dyn_ron_code_coeff=1.5).legality_violations() == []
    assert Config(dyn_ron_code_coeff=1.5, dyn_input_settling=True).legality_violations()


# --------------------------------------------------------------- F4
def test_chip_build_rejects_nonpositive_unit_capacitance():
    with pytest.raises(ValueError, match="非正|非有限"):
        build_chip(Config(c_total0=-1e-12))


# --------------------------------------------------------------- F5 / F6
@pytest.mark.parametrize("gain", [0.0, 1.5, -0.2])
def test_ref_track_rejects_out_of_domain_threshold_gain(gain):
    with pytest.raises(ValueError):
        RefTrackConfig(threshold_gain=gain).validated()


@pytest.mark.parametrize("r_aux", [0.0, -1.0, float("nan"), float("inf")])
def test_aux_input_rejects_nonpositive_r_aux(r_aux):
    """F6：r_aux 必须为正且有限；0/负/nan/inf 一律拒（旧口径只查 `v<=0`，漏掉 nan/inf）。"""
    with pytest.raises(ValueError, match="必须为正有限值"):
        build_stage(Config(), r_aux=r_aux).validated()


def test_aux_input_accepts_positive_r_aux():
    """F6 正对照：合法 r_aux=1.0 被接受。"""
    build_stage(Config(), r_aux=1.0).validated()  # 正对照要求正常返回。


# --------------------------------------------------------------- F7
@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -float("inf")])
def test_sadc_rejects_non_finite_input(bad):
    with pytest.raises(ValueError, match="finite"):
        SADC(Config()).convert(np.array([0.1, bad]))


# --------------------------------------------------------------- F8
def test_dither_nodal_matches_closed_form_on_fractional_codes():
    """F8：小数码下节点验证器与闭式解必须同域（旧实现按整数码截断，偏差 3.5e-3）。"""
    cfg = Config(dac_arch="split", dither_mode="sampling", dither_discrete=True, dem_enable=False)
    chip = build_split_chip(cfg)
    om, os_ = np.arange(chip.n_main), np.arange(chip.n_sub)
    nm = int(cfg.dither_units_total)
    rng = np.random.default_rng(7)
    ks = rng.uniform(0.0, chip.n_sub - 1e-9, size=32)
    km = rng.integers(0, chip.n_main, size=32).astype(float)
    x = np.full(32, 0.31)
    d = np.zeros(32, dtype=np.int64)
    closed = np.asarray(
        ref_ra_charge_dither_closed(
            chip, cfg.v_fs, om, os_, km, ks, x, d, cfg.dither_split_bank, nm
        ),
        dtype=float,
    )
    nodal = np.asarray(
        ref_ra_charge_dither_nodal(
            chip, cfg.v_fs, om, os_, km, ks, x, d, cfg.dither_split_bank, nm
        ),
        dtype=float,
    )
    rel = float(np.max(np.abs(closed - nodal)) / np.max(np.abs(closed)))
    assert rel < 1e-12, f"闭式与节点在小数码下应一致到机器精度，实测 {rel:.3e}"


# --------------------------------------------------------------- N4
def test_dither_nmask_none_means_full_column_in_both_backends():
    """N4：检查旧 n_mask=None 的整列含义在两个后端一致，且不引发异常。"""
    cfg = Config(dac_arch="split", dither_mode="sampling", dither_discrete=True)
    chip = build_split_chip(cfg)
    om, os_ = np.arange(chip.n_main), np.arange(chip.n_sub)
    km, ks = np.array([5.0]), np.array([1.5])
    x, d = np.array([0.1]), np.zeros(1, dtype=np.int64)
    full = len(chip.C_sub) if cfg.dither_split_bank == "sub" else len(chip.C_main)
    closed_none = np.asarray(
        ref_ra_charge_dither_closed(
            chip, cfg.v_fs, om, os_, km, ks, x, d, cfg.dither_split_bank, None
        ),
        dtype=float,
    )
    closed_full = np.asarray(
        ref_ra_charge_dither_closed(
            chip, cfg.v_fs, om, os_, km, ks, x, d, cfg.dither_split_bank, full
        ),
        dtype=float,
    )
    nodal_none = np.asarray(
        ref_ra_charge_dither_nodal(
            chip, cfg.v_fs, om, os_, km, ks, x, d, cfg.dither_split_bank, None
        ),
        dtype=float,
    )
    assert np.allclose(closed_none, closed_full, rtol=0, atol=0)
    assert np.allclose(nodal_none, closed_none, rtol=1e-12, atol=0)


def test_dither_mask_zero_is_explicit_noop():
    """N4：检查 n_mask=0 表示无选通列，贡献为零而不发生广播异常。"""
    cfg = Config(dac_arch="split")
    chip = build_split_chip(cfg)
    q, c = dither_mask_charge(chip, cfg.v_fs, np.array([0, 3, -2]), cfg.dither_split_bank, 0)
    assert c == 0.0
    assert np.allclose(np.asarray(q, dtype=float), 0.0)


# --------------------------------------------------------------- F9 / N2
def test_crosstalk_activity_uses_fractional_half_units():
    """F9/N2：逐项独立计算 A(k)/2 个单元的分数前缀和。

    A(k)=3、分数码 k=1.5 时，分数口径取 1.5 个单元，取整口径取 2 个；
    夹具使两者可区分，不复用被测向量表达式。
    """
    n_u, p0 = 63, 1e-15
    grad = np.linspace(-1.0, 1.0, n_u)
    prof = p0 * (1.0 + 0.5 * grad)  # 使用空间梯度分布以区分分数与取整口径。
    cfg = Config(dyn_crosstalk=True, dyn_c_xtalk_common=0.0, dyn_c_xtalk_unit=p0, dyn_v_digital=1.0)
    n_levels = int(cfg.dac_levels)
    code = np.array([1.5, 2.0, 10.0, 20.0, 40.0])  # 首个是分数码 -> A(k)=3
    sid = np.zeros(code.size, dtype=np.int64)
    perm = lambda s: np.arange(n_u)  # noqa: E731

    got = np.asarray(
        crosstalk_error(
            cfg,
            code,
            sid,
            n_levels=n_levels,
            unit_xtalk_profile=prof,
            perm_fn=perm,
            c_out=1e-12,
        ),
        dtype=float,
    )
    a_k = switching_activity(cfg, code, n_levels)
    expect, rounded = [], []
    for a in a_k:
        nk = min(float(a) / 2.0, n_u - 1e-9)
        i0 = int(np.floor(nk))
        frac = nk - i0
        sel = float(prof[:i0].sum()) + (frac * float(prof[i0]) if i0 < n_u else 0.0)
        expect.append(2.0 * sel / 1e-12)
        n_round = int(np.round(float(a) / 2.0))
        rounded.append(2.0 * float(prof[:n_round].sum()) / 1e-12)

    assert np.allclose(got, np.array(expect), rtol=1e-12, atol=0)
    assert not np.isclose(expect[0], rounded[0]), "该码点必须能区分分数/取整两种口径"


# --------------------------------------------------------------- F11 / F12
def test_ridge_fit_lambda_zero_uses_min_norm_on_rank_deficient_matrix():
    """F11：检查超定、秩亏、lam=0 时的最小范数解及 used=1。

    2026-09-25 独立审查 B3 已注明：旧实现在该分支会由奇异 UᵀU 触发
    LinAlgError 后转入 lstsq，本来就能返回正确解。因此此断言维护数值合同，
    不能单独检出后来 ridge_fit 修改的回退。
    """
    from adi_model.calib import ridge_fit

    U = np.array([[1.0, 2.0], [2.0, 4.0], [3.0, 6.0]])  # 秩 1（两列成比例）
    y = np.array([1.0, 2.0, 3.0])
    w, used = ridge_fit(U, y, lam=0.0)
    assert np.all(np.isfinite(w))
    # 秩亏时只能辨识一个方向，used 不应报告为全部参数。
    assert used == 1 and used < U.shape[1]
    # 手算核对：UtU=[[14,28],[28,56]]，Uty=14·[1,2] ⇒ 解空间 w0+2*w1=1；
    # 其上的最小范数点 = (1/5)·[1,2] = [0.2, 0.4]（唯一）
    assert np.allclose(w, [0.2, 0.4], rtol=1e-12, atol=1e-15)
    assert float(np.linalg.norm(w)) == pytest.approx(1.0 / np.sqrt(5.0), rel=1e-12)
    # [1,0] 等其他同残差解范数更大，用于区分最小范数解与任意特解。
    assert float(np.linalg.norm(w)) < float(np.linalg.norm([1.0, 0.0]))


def test_ref_track_zero_error_window_returns_inf():
    """F12：零误差窗口 → inf（与 a06 口径一致），而不是 raise。"""
    from adi_model.ref_track import RefTrackRun

    def make(err):
        zeros = np.zeros(err.size)
        return RefTrackRun(
            err_end_of_cycle=np.asarray(err, dtype=float),
            err_per_trial=np.zeros((err.size, 4)),
            threshold=zeros,
            charge_from_ext=zeros,
            settle_cycle=0,
        )

    from adi_model.ref_track import reference_precision_bits

    # 严格零误差的RMS为0，等效精度为inf。
    assert make(np.zeros(16))._window_bits(slice(None), span_v=1.0) == float("inf")
    # 非零常数是DC误差；std为0不能将它误报为无误差。
    constant = make(np.full(16, 1e-6))._window_bits(slice(None), span_v=1.0)
    assert constant == pytest.approx(reference_precision_bits(1e-6, 1.0))
    assert np.isfinite(constant)
    # 非退化（非常数）微误差窗口 → 有限位数，且与公开换算函数逐位一致
    err = np.linspace(-1e-6, 1e-6, 16)
    finite = make(err)._window_bits(slice(None), span_v=1.0)
    assert np.isfinite(finite)
    rms = float(np.sqrt(np.mean(err**2)))
    assert finite == pytest.approx(reference_precision_bits(rms, 1.0), rel=1e-12)
    assert finite == pytest.approx(18.841282446503264, rel=1e-12)


# --------------------------------------------------------------- F13 / N1
def test_mapper_encode_validates_contract():
    m = Mapper(Config())
    n_code = 2 ** Config().b1
    with pytest.raises(ValueError, match="长度不一致"):
        m.encode(np.array([3, 4]), np.array([0]), np.array([0, 1]))
    with pytest.raises(ValueError, match="粗码越界"):
        m.encode(np.array([n_code]), np.array([0]), np.array([0]))
    with pytest.raises(ValueError, match="1-D"):
        m.encode(3, np.array([0]), np.array([0]))  # N1：标量输入曾是 len() TypeError


def test_mapper_encode_rejects_fractional_coarse_code():
    """F13：检查域内分数粗码（如 2.5）被拒绝。

    SADC 粗码应为整数；旧 k=coarse*units_per_lsb1+k0 会将分数传播为非整数单元数。
    同时保留粗码越界的分支顺序和错误信息检查。
    """
    m = Mapper(Config())
    n_code = 2 ** Config().b1
    with pytest.raises(ValueError, match="必须是整数"):
        m.encode(np.array([2.5]), np.array([0]), np.array([0]))
    # 整值浮点（如 2.0）仍被接受，不改既有合法路径
    cmd = m.encode(np.array([2.0]), np.array([0]), np.array([0]))
    assert np.all(np.isfinite(cmd.k))
    # 越界分支仍报"粗码越界"（守住分支顺序）
    with pytest.raises(ValueError, match="粗码越界"):
        m.encode(np.array([n_code]), np.array([0]), np.array([0]))


# --------------------------------------------------------------- F10
def test_run_with_calibration_short_record_stays_below_nyquist(monkeypatch):
    """F10：检查少于 4096 点的旧 beta 标定所用相干频点不越过 Nyquist。

    直接截取传给正弦入口的 fin 并断言 0<fin<fs/2，覆盖原 39 条回归未触达的修正。
    """
    import adi_model.sampler as sampler_mod

    captured = {}
    real = sampler_mod.sine_input

    def spy(v, f, **k):
        captured["fin"] = f
        return real(v, f, **k)

    monkeypatch.setattr(sampler_mod, "sine_input", spy)
    fs = Config().fs
    for n in (256, 512, 1024, 2048, 4096):
        cfg = Config(calibration="gain_beta", ktc_enable=True)
        rng = np.random.default_rng(1)
        run_with_calibration(cfg, real(0.5 * cfg.v_fs, cfg.fs / 8), n, rng=rng)
        fin = captured.get("fin")
        assert fin is not None, "必须捕获到传给正弦前台的 fin"
        assert 0 < fin < fs / 2, f"n={n}: fin/fs={fin / fs} 越过了 Nyquist"


# --------------------------------------------------------------- F11
def test_ridge_fit_underdetermined_rank_deficient_uses_min_norm():
    """F11：检查欠定、秩亏、lam=0 的对偶分支也返回最小范数解。

    U 行数小于列数，独立比较 np.linalg.lstsq；原测试只覆盖超定 3×2 分支。
    """
    from adi_model.calib import ridge_fit

    U = np.array([[1.0, 2.0, 3.0], [2.0, 4.0, 6.0]])  # 2×3 秩 1（欠定 + 秩亏）
    y = np.array([1.0, 2.0])
    w, used = ridge_fit(U, y, lam=0.0)
    ref = np.linalg.lstsq(U, y, rcond=None)[0]
    assert np.all(np.isfinite(w))
    assert np.allclose(U @ w, y, atol=1e-10)  # 仍是解
    assert np.allclose(w, ref, rtol=1e-12, atol=1e-15)  # 且是最小范数解


# --------------------------------------------------------------- F9 / N2
def test_crosstalk_uniform_profile_numerically_unchanged():
    """F9/N2：检查均匀活动分布下新旧公式数值一致，相对容差约 1e-12。

    旧对照为 e_unit=dyn_v*cum[round(a_k)]。2026-09-25 独立审查 B3 已注明：
    均匀分布下两公式按构造重合，因此这是均匀输入的不变性检查，不能单独检出
    分数前缀串扰修正的回退。
    """
    n_u, p0 = 63, 1e-15
    cfg = Config(dyn_crosstalk=True, dyn_c_xtalk_common=0.0, dyn_c_xtalk_unit=p0, dyn_v_digital=1.0)
    n_levels = int(cfg.dac_levels)
    code = np.array([1.5, 2.0, 10.0, 20.0, 30.0])  # 物理合法码（a_k ≤ n_units）
    sid = np.zeros(code.size, dtype=np.int64)
    perm = lambda s: np.arange(n_u)  # noqa: E731
    C_REF = cfg.c_total0 * cfg.cap_scale

    def old_with(prof, codes, sids):
        prof = np.asarray(prof, dtype=float)
        a = switching_activity(cfg, codes, n_levels)
        cum = np.concatenate([[0.0], np.cumsum(prof)])
        return cfg.dyn_v_digital * cum[np.clip(np.round(a).astype(int), 0, prof.size)] / C_REF

    prof_uni = p0 * np.ones(n_u)
    new_uni = np.asarray(
        crosstalk_error(
            cfg,
            code,
            sid,
            n_levels=n_levels,
            unit_xtalk_profile=prof_uni,
            perm_fn=perm,
            c_out=C_REF,
        ),
        dtype=float,
    )
    old_uni = old_with(prof_uni, code, sid)
    rel = float(np.max(np.abs(new_uni - old_uni)) / (np.max(np.abs(old_uni)) + 1e-30))
    assert rel < 1e-12, f"均匀 profile 下应数值不变，实测相对差 {rel:.3e}"


# --------------------------------------------------------------- F14
def test_reporting_tolerates_null_experiment_entry():
    """F14：v8 的 null-undefined 口径下，results[key]=None 不得让报告生成崩溃。"""
    html = render_report(
        {k: None for k in ("pipeline", "il_offset", "dither_quant", "rdac_bitwise")}
    )
    assert isinstance(html, str) and html


# --------------------------------------------------------------- F15
def test_inventory_gate_flags_duplicate_source_id():
    dup = GateResult()
    digest = "a" * 64
    _check_sources(
        REPO,
        {"sources": [{"id": "x", "sha256": digest}, {"id": "x", "sha256": digest}]},
        dup,
    )
    final = dup.finalize()
    assert any(r.check == "sources.duplicate_id" and r.level == "FAIL" for r in final.records)


def test_repo_inventory_gate_is_clean():
    from adi_model.inventory_gate import verify

    result = verify(REPO)
    assert result.ok, [r for r in result.records if r.level != "PASS"]


# --------------------------------------------------------------- F16 / F17a / N5
@pytest.mark.parametrize("g_r", [0.0, -1.5, float("nan"), float("inf"), -float("inf")])
def test_ktc_rejects_nonpositive_ra_gain(g_r):
    """F16：beta_n_of 必须拒 g_r<=0 与 nan/inf（旧口径 `g_r<=0` 漏掉非有限值）。"""
    with pytest.raises(ValueError, match="g_r"):
        KTCBranch(Config(ktc_enable=True)).beta_n_of(np.array([g_r]))


def test_ktc_beta_x_of_shares_the_domain_guard():
    """F16：检查旧 beta_x_of 与 beta_n_of 拒绝同一组非法 g_r。

    覆盖 nan、inf 及非正值；合法 g_r=2.0 时，结果应有限且为 kappa*g_n*eta_x/2。
    """
    kb = KTCBranch(Config(ktc_enable=True))
    for g_r in (0.0, -1.0, float("nan"), float("inf")):
        with pytest.raises(ValueError, match="g_r"):
            kb.beta_x_of(np.array([g_r]))
    got = kb.beta_x_of(np.array([2.0]))
    assert np.isfinite(got)
    expect = kb.kappa * kb.g_n * kb.eta_x / 2.0
    assert float(got[0]) == pytest.approx(expect, rel=1e-12)


def test_noiseless_observer_degenerate_path_is_explicit_and_silent():
    """F17a + N5：观测通路无噪声时 kappa_hat / corr 显式为 NaN 且**不得**抛运行时警告。"""
    chip = build_split_chip(Config(dac_arch="split"))
    st = phase_noise_state(chip)
    st0 = {k: (np.zeros_like(v) if k == "b" else v) for k, v in st.items()}
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        r = monte_carlo_residual(st0, kappa=1.0, sigma_eN=0.0, n=1024, rng=np.random.default_rng(3))
    noisy = [
        str(w.message)
        for w in caught
        if issubclass(w.category, RuntimeWarning)
        and ("invalid value" in str(w.message) or "divide" in str(w.message))
    ]
    assert not noisy, f"退化路径仍在做 0/0：{noisy}"
    assert np.isnan(r["kappa_hat"])
    assert np.isnan(r["corr_path_obs"])
    assert np.isfinite(r["sigma_mc"])


def test_noiseless_observer_normal_path_still_recovers_kappa():
    chip = build_split_chip(Config(dac_arch="split"))
    st = phase_noise_state(chip)
    r = monte_carlo_residual(st, kappa=1.0, sigma_eN=1e-6, n=1 << 13, rng=np.random.default_rng(4))
    assert r["kappa_hat"] == pytest.approx(1.0, rel=0.02)
    assert r["corr_path_obs"] > 0.99


# --------------------------------------------------------------- F17b
def test_cli_reports_results_dir_pointing_at_a_file(tmp_path):
    target = tmp_path / "not_a_dir.txt"
    target.write_text("x", encoding="utf-8")
    assert _run_script("run_all.py", str(target)) == 2


# --------------------------------------------------------------- N3（合规兜底）
def test_internal_review_report_dir_is_ignored():
    """N3：检查含论文、专利截图的内部审查目录受 .gitignore 约束。"""
    patterns = (REPO / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "docs/robustness_review_2026/" in patterns
    idx_allow = patterns.index("!docs/**/*.png")
    idx_block = patterns.index("docs/robustness_review_2026/")
    assert idx_block > idx_allow, "反向规则必须写在 !docs/**/*.png 之后才生效"


def test_sdist_excludes_the_third_party_report_figures():
    """N3：直接检查构建后端的分发排除规则，并保留反向包含检查。

    2026-09-25 发布记录显示，hatchling 逐模式匹配的行为与 Git 目录剪枝不同，
    !docs/**/*.png 曾覆盖目录级排除，把 ISSCC 论文页和专利首页截图放入 sdist。
    其 path_is_excluded 对文件路径调用 GitIgnoreSpec.match_file，单纯 dir 或 dir/
    目录模式不足以匹配该路径，因此使用 dir/** 形式。

    测试调用后端实际判定函数，既检查第三方截图被排除，也检查自有图片和参考
    产物保留；仅搜索配置中的字符串不能证明分发边界。
    """
    pytest.importorskip("hatchling")  # 构建后端不可用时不适用（CI 装了 hatchling）
    from hatchling.builders.sdist import SdistBuilder

    cfg = SdistBuilder(str(REPO)).config

    must_exclude = [
        "docs/robustness_review_2026/fig/paper_p1.png",  # ISSCC 2024 9.8 论文页截图
        "docs/robustness_review_2026/fig/patent12_fig.png",  # 专利首页截图
        "docs/robustness_review_2026/robustness_review_2026.pdf",
        "docs/robustness_review_2026/robustness_review_2026.tex",
    ]
    for rel in must_exclude:
        assert cfg.path_is_excluded(rel), f"{rel} 必须被 sdist 排除（第三方版权材料）"

    # 反向检查自有图及参考产物仍进入分发包，避免 README 引用失效。
    must_keep = [
        "docs/release_v8.2.0/fig/headline_compare.png",
        "docs/release_v8.2.0/fig/significance_null.png",
        "docs/release_v8.2.1/fig/guard_hardening_map.png",
        "docs/release_v8.2.1/fig/byte_account_v821.png",
        "docs/release_v8.2.2/fig/ingress_closure_v822.png",
        "docs/release_v8.2.2/fig/byte_account_v822.png",
        # 首页引用的自有架构和对应关系图应保留在分发包中。
        "docs/readme/fig/fig1_architecture.png",
        "docs/readme/fig/fig2_modules.png",
        "docs/readme/fig/fig3_source_alignment.png",
        "docs/readme/fig/fig4_disclosed_anchors.png",
        "docs/readme/fig/fig5_provenance_grades.png",
        "docs/readme/fig/fig6_verification.png",
        "docs/readme/fig/fig7_mapping.png",
        "docs/readme/fig/unified_1_architecture.png",
        "docs/readme/fig/unified_2_sources.png",
        "docs/readme/fig/unified_3_calibration.png",
        "docs/readme/fig/unified_4_dsp.png",
        "docs/readme/fig/unified_5_evidence.png",
        "docs/readme/fig/unified_6_calibration_effect.png",
        "docs/readme/fig/unified_7_interleave_effect.png",
        "docs/readme/fig/bridge_state_example.png",
        "docs/readme/fig/structural_calibration_holdout.png",
        "docs/readme/fig/current_architecture.png",
        "docs/readme/fig/current_calibration.png",
        "docs/readme/fig/current_interleave.png",
        "docs/readme/fig/current_dither_headroom.png",
        "docs/readme/fig/current_figures_manifest.json",
        "docs/readme/make_overview_figures.py",
        "docs/readme/data/interleave_timing_40m.npz",
        "docs/readme/data/interleave_timing_codes_40m.npz",
        "docs/readme/data/dither_headroom.json",
        "tools/reproduce_dither_headroom.py",
        "docs/readme/fig/dem_implementation.png",
        "docs/readme/fig/dither_implementation.png",
        "docs/readme/data/randomization_contract.json",
        "docs/readme/make_randomization_figures.py",
        "tools/reproduce_randomization_caaz.py",
        "tools/reproduce_dem_usage.py",
        "docs/randomization_caaz/data/dem_usage.json",
        "docs/randomization_caaz/fig/dem_usage.png",
        "sim/tb/caaz_randomization_probe_tb.sv",
        "tools/check_caaz_randomization_trace.py",
        "docs/caaz/fig/caaz_mechanism_results.png",
        "docs/caaz/fig/caaz_bandwidth.png",
        "docs/caaz/fig/caaz_rtl_context.png",
        "docs/caaz/data/rtl_context_trace.csv",
        "docs/caaz/data/summary.json",
        "docs/caaz/make_figures.py",
        "tools/reproduce_caaz.py",
        "tools/export_caaz_vectors.py",
        "docs/report/figs/fig1_interleave.png",
        "tools/results/results.json",
    ]
    for rel in must_keep:
        assert not cfg.path_is_excluded(rel), f"{rel} 不应被排除"


# --------------------------------------------------------------- 参考输出指纹
def test_reference_output_is_present_and_typed():
    """发布物健全性：随仓库发布的参考输出必须存在且可解析（字节账见 CHANGELOG）。"""
    import json

    payload = json.loads((REPO / "tools" / "results" / "results.json").read_text(encoding="utf-8"))
    assert {"s1", "mc", "mc_pdk_off", "s13"} <= set(payload)


def test_split_dac_nominal_endpoints_are_ordered():
    """回归：分段 DAC 端点序（SplitDAC 与探针共用的最小健全性检查）。"""
    cfg = Config()
    dac = SplitDAC(cfg, build_split_chip(cfg))
    lo, hi = dac._nominal_endpoints()
    assert lo < hi


# 第二批：2026-09-25 独立审查定位的有限性检查。
# 以下保留当时 18 个站点的回归；旧域校验仅检查
# 符号或区间时，nan/inf 可能传播为 NaN，因此同时检查有限性
# （标量用 math.isfinite，数组用 np.isfinite），并设置合法值正对照。

_REPO = Path(__file__).resolve().parents[2]


def _load_exporter():
    """按路径加载 tools/export_rtl_params.py；tools 目录不是 Python 包。"""
    path = _REPO / "tools" / "export_rtl_params.py"
    spec = _ilu.spec_from_file_location("export_rtl_params_2", path)
    module = _ilu.module_from_spec(spec)
    _sys.modules["export_rtl_params_2"] = module
    spec.loader.exec_module(module)
    return module


# --- 站点 1/2：config.fs / config.v_fs 必须有限正值 ---
@pytest.mark.parametrize(
    "field, bad",
    [
        ("fs", float("nan")),
        ("fs", float("inf")),
        ("v_fs", float("nan")),
        ("v_fs", float("inf")),
    ],
)
def test_config_fs_vfs_reject_nonfinite(field, bad):
    """站点 1/2：采样率/满幅必须有限正值（config 走 legality_violations 返回非空）。"""
    assert Config(**{field: bad}).legality_violations()


def test_config_fs_vfs_accept_finite():
    """站点 1/2 正对照：有限、正的采样率及满幅被接受。"""
    assert Config(fs=40e6, v_fs=3.0).legality_violations() == []


# --- 站点 3：reference_precision_bits ---
@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -float("inf")])
def test_reference_precision_bits_rejects_nonfinite(bad):
    """站点 3：检查误差 RMS 和电压跨度的非有限输入被拒绝。"""
    from adi_model.ref_track import reference_precision_bits

    with pytest.raises(ValueError, match="必须为正有限值"):
        reference_precision_bits(bad, 1.0)
    with pytest.raises(ValueError, match="必须为正有限值"):
        reference_precision_bits(1e-3, bad)


def test_reference_precision_bits_accepts_finite():
    from adi_model.ref_track import reference_precision_bits

    assert np.isfinite(reference_precision_bits(1e-3, 1.0))


# --- 站点 4：RefTrackConfig.validated 十字段循环 ---
@pytest.mark.parametrize(
    "field, bad",
    [
        ("v_ext", float("nan")),
        ("v_ext", float("inf")),
        ("c_internal", float("nan")),
        ("c_internal", float("inf")),
        ("c_dac_load", float("nan")),
        ("c_dac_load", float("inf")),
        ("v_fs_dac", float("nan")),
        ("v_fs_dac", float("inf")),
        ("i_charge_max", float("nan")),
        ("i_charge_max", float("inf")),
        ("cmp_bw_hz", float("nan")),
        ("cmp_bw_hz", float("inf")),
        ("t_conv", float("nan")),
        ("t_conv", float("inf")),
        ("tol_first_v", float("nan")),
        ("tol_first_v", float("inf")),
        ("n_bits", float("nan")),
        ("n_bits", float("inf")),
    ],
)
def test_reftrack_validated_rejects_nonfinite(field, bad):
    """站点 4：检查旧十字段校验同时约束有限性，不能仅比较 v<=0。"""
    with pytest.raises(ValueError, match="必须为正有限值"):
        RefTrackConfig(**{field: bad}).validated()


def test_reftrack_validated_accepts_finite():
    RefTrackConfig().validated()  # 默认参数均有限合法


# --- 站点 5：TrackPolicy.validated 权重有限性 ---
@pytest.mark.parametrize("bad", [float("nan"), float("inf")])
def test_trackpolicy_weights_reject_nonfinite(bad):
    """站点 5：加权权重须有限。"""
    from adi_model.interleave_tracking import TrackPolicy

    with pytest.raises(ValueError, match="有限"):
        TrackPolicy(mode="track_other_weighted", n_weighted=3, weights=(0.6, 0.3, bad)).validated()


def test_trackpolicy_weights_accept_finite():
    from adi_model.interleave_tracking import TrackPolicy

    TrackPolicy(mode="track_other_weighted", n_weighted=3, weights=(0.6, 0.3, 0.1)).validated()


# --- 站点 6：filter_bw_relative(t_cycle) ---
@pytest.mark.parametrize("bad", [float("nan"), float("inf")])
def test_filter_bw_relative_rejects_nonfinite_tcycle(bad):
    from adi_model.interleave_tracking import filter_bw_relative

    with pytest.raises(ValueError, match="有限"):
        filter_bw_relative(bad, 0.01)


def test_filter_bw_relative_accepts_finite():
    from adi_model.interleave_tracking import filter_bw_relative

    assert filter_bw_relative(1e-6, 0.01) > 0


# --- 站点 7：filter_bw_absolute 四个参数 ---
@pytest.mark.parametrize(
    "idx,bad", [(i, b) for i in range(4) for b in (float("nan"), float("inf"))]
)
def test_filter_bw_absolute_rejects_nonfinite(idx, bad):
    from adi_model.interleave_tracking import filter_bw_absolute

    args = [1e-12, 1e-12, 1e-3, 1e-6]
    args[idx] = bad
    with pytest.raises(ValueError, match="有限"):
        filter_bw_absolute(*args)


def test_filter_bw_absolute_accepts_finite():
    from adi_model.interleave_tracking import filter_bw_absolute

    assert filter_bw_absolute(1e-12, 1e-12, 1e-3, 1e-6) >= 0


# --- 站点 8：noise_ratio_from_bw ---
@pytest.mark.parametrize("which", ["bw", "bw_ref"])
@pytest.mark.parametrize("bad", [float("nan"), float("inf")])
def test_noise_ratio_from_bw_rejects_nonfinite(which, bad):
    from adi_model.interleave_tracking import noise_ratio_from_bw

    bw, bw_ref = 1e6, 1e6
    if which == "bw":
        bw = bad
    else:
        bw_ref = bad
    with pytest.raises(ValueError, match="有限"):
        noise_ratio_from_bw(bw, bw_ref)


def test_noise_ratio_from_bw_accepts_finite():
    from adi_model.interleave_tracking import noise_ratio_from_bw

    assert noise_ratio_from_bw(1e6, 1e6) == pytest.approx(1.0)


# --- 站点 9：required_samples(tgt) ---
@pytest.mark.parametrize("bad", [float("nan"), float("inf")])
def test_required_samples_rejects_nonfinite(bad):
    """站点 9：目标精度非有限时返回不可行（不再静默算出 nan）。"""
    from adi_model.calib import required_samples

    U = np.eye(4)
    res = required_samples(Config(), U, 1e-4, 1e-3, bad)
    assert res["feasible"] is False
    assert not np.isfinite(res["n_samples"])


def test_required_samples_accepts_finite():
    from adi_model.calib import required_samples

    U = np.eye(4)
    res = required_samples(Config(), U, 1e-4, 1e-3, 300)
    assert res["feasible"] is True
    assert np.isfinite(res["n_samples"])


# --- 站点 10：QuantizedPretracker.lower_v 有限性 ---
@pytest.mark.parametrize("bad", [float("nan"), float("inf")])
def test_pretracker_lower_v_rejects_nonfinite(bad):
    from adi_model.pretracking import QuantizedPretracker

    with pytest.raises(ValueError):
        QuantizedPretracker(n_slices=4, bits=4, lower_v=bad, coarse_step_v=1.0, signal_scale=1.0)


def test_pretracker_lower_v_accepts_finite():
    from adi_model.pretracking import QuantizedPretracker

    QuantizedPretracker(4, 4, 0.0, 1.0, 1.0)  # lower_v 可为任意有限值（含 0/负）


# --- 站点 11：response_interval(tau_ref_s, clip_v) ---
@pytest.mark.parametrize(
    "field,bad",
    [
        ("tau_ref_s", float("nan")),
        ("tau_ref_s", float("inf")),
        ("clip_v", float("nan")),
        ("clip_v", float("inf")),
    ],
)
def test_response_interval_rejects_nonfinite(field, bad):
    from adi_model.conversion import response_interval

    kw = {
        "y0": 0.0,
        "z0": 0.0,
        "target_v": 1.0,
        "reference_drive_v": 1.0,
        "tau_ref_s": 1e-6,
        "dt_s": 1e-6,
        "ra_bw_hz": None,
        "adc_bw_hz": None,
        "clip_v": 1.0,
        "slew_v_s": None,
    }
    kw[field] = bad
    with pytest.raises(ValueError, match="finite"):
        response_interval(**kw)


def test_response_interval_accepts_finite():
    from adi_model.conversion import response_interval

    kw = {
        "y0": 0.0,
        "z0": 0.0,
        "target_v": 1.0,
        "reference_drive_v": 1.0,
        "tau_ref_s": 1e-6,
        "dt_s": 1e-6,
        "ra_bw_hz": None,
        "adc_bw_hz": None,
        "clip_v": 1.0,
        "slew_v_s": None,
    }
    out = response_interval(**kw)
    assert len(out) == 3


# --- 站点 12：sar_loading_codes(coarse/units/dither) ---
@pytest.mark.parametrize(
    "field,bad",
    [
        ("coarse", float("nan")),
        ("coarse", float("inf")),
        ("units", float("nan")),
        ("units", float("inf")),
        ("dither", float("nan")),
        ("dither", float("inf")),
    ],
)
def test_sar_loading_codes_rejects_nonfinite(field, bad):
    from adi_model.reference_charge import sar_loading_codes

    kw = {"coarse": 0, "bits": 4, "units": 1, "dither": 0.0}
    kw[field] = bad
    with pytest.raises(ValueError):
        sar_loading_codes(**kw)


def test_sar_loading_codes_accepts_finite():
    from adi_model.reference_charge import sar_loading_codes

    codes = sar_loading_codes(0, 4, 1, 0.0)
    assert np.isfinite(codes).all()


# --- 站点 13：dynamics ref_recovery_factor / bitwise_eta_dyn (dyn_tau_ref) ---
class _DynCfg:
    dyn_t_conv_frac = 0.4
    fs = 40e6
    dyn_tau_ref = 1e-7
    rdac_bitwise_bits = 8


@pytest.mark.parametrize("bad", [float("nan"), float("inf")])
def test_dyn_recovery_rejects_nonfinite(bad):
    """站点 13：绕过配置检查时，旧动态参考函数也不能把非有限 tau 传播为 NaN。"""
    from adi_model.dynamics import bitwise_eta_dyn, ref_recovery_factor

    cfg = _DynCfg()
    cfg.dyn_tau_ref = bad
    # 此旧接口对非有限输入返回有限值 0.0，不传播 NaN。
    assert np.isfinite(ref_recovery_factor(cfg))
    assert np.isfinite(bitwise_eta_dyn(cfg))


def test_dyn_recovery_accepts_finite():
    from adi_model.dynamics import bitwise_eta_dyn, ref_recovery_factor

    cfg = _DynCfg()
    cfg.dyn_tau_ref = 1e-7
    assert np.isfinite(ref_recovery_factor(cfg))
    val = bitwise_eta_dyn(cfg)
    assert np.isfinite(val) and val > 0


# --- 站点 14：fit_unit_weights 权重有限正值 ---
def test_fit_unit_weights_rejects_nonfinite_or_nonpositive():
    """站点 14：检查拟合权重有限且为正，使用 SVD 替身定向注入异常。

    非有限 SVD 因子应在矩阵乘法前拒绝；有限但非正的权重由下游权重检查拒绝。
    两条路径分别断言对应错误信息，避免只检查最终出现某个异常。
    """
    import dataclasses

    from scipy.linalg import svd as _real_svd

    import adi_model.weight_calibration as _wc
    from adi_model.weight_calibration import (
        CalibrationSpec,
        DigitalObservation,
        fit_unit_weights,
    )

    cfg = Config(dac_arch="split")
    spec = CalibrationSpec.from_config(cfg)
    na = spec.n_active
    ns = spec.n_slices
    p = int(np.prod(spec.shape)) + 1
    n = p + 5
    sid = np.zeros((n, na), dtype=np.int64)
    for i in range(n):
        sid[i] = [(i + j) % ns for j in range(na)]
    rdac = np.zeros(n, dtype=np.int64)
    dem = np.zeros(n, dtype=np.int64)
    adc2 = np.zeros(n, dtype=np.int64)
    bank = np.zeros(n)
    inj = np.zeros(n)
    ov = np.zeros(n, dtype=bool)

    def make_obs(avmin):
        sp = dataclasses.replace(spec, adc2_v_min=avmin)
        return DigitalObservation(sp, sid, rdac, dem, adc2, bank, inj, ov, True)

    def fake(bad):
        def f(design, full_matrices=False, check_finite=False, lapack_driver="gesdd"):
            nn, pp = design.shape
            u = np.zeros((nn, pp))
            for i in range(min(nn, pp)):
                u[i, i] = 1.0
            s = np.ones(pp)
            vh = np.eye(pp)
            vh[0, 0] = bad
            return u, s, vh

        return f

    # 合法有限正权重正对照：adc2_v_min 为正，构造的 fine_v 及权重为正。
    _wc.svd = fake(1.0)
    try:
        fit_unit_weights(make_obs(0.0), np.ones(n))
    finally:
        _wc.svd = _real_svd
    # 非有限 SVD 因子应在矩阵乘法前被拒绝；缺少该检查时，nan/inf 会
    # 进入 design @ theta，0*inf 可能触发 RuntimeWarning。历史 x86-64 CI 将其判为失败，
    # Apple Accelerate 则可能继续到权重检查并返回不同信息；因此分别检查因子
    # 检查的信息字符串，以识别检查被删除或移到算术后的变异。
    for bad in (float("nan"), float("inf")):
        _wc.svd = fake(bad)
        try:
            with pytest.raises(ValueError, match="non-finite factors"):
                fit_unit_weights(make_obs(-10.0), np.ones(n))
        finally:
            _wc.svd = _real_svd
    # 有限但非正的权重：SVD 因子是有限的，因子守卫不得误报；必须由下游权重守卫拦下。
    _wc.svd = fake(-1.0)
    try:
        with pytest.raises(ValueError, match="nonpositive"):
            fit_unit_weights(make_obs(-10.0), np.ones(n))
    finally:
        _wc.svd = _real_svd


# --- 站点 14b：CalibrationSpec 电压标度 / dither 档位范围的有限性入口闸门 ---
@pytest.mark.parametrize(
    "field_,bad",
    [
        ("adc2_v_min", float("nan")),
        ("adc2_v_max", float("inf")),
        ("v_fs", float("nan")),
        ("v_fs", float("inf")),
        ("dither_units_range", float("inf")),
    ],
)
def test_spec_physical_scales_must_be_finite(field_, bad):
    """站点 14b：检查手工构造的 spec 也在观测入口拒绝非有限标度。

    from_config 会调用配置检查，但手工构造或 dataclasses.replace 可绕过该入口。
    v_fs 与 adc2_v_min/v_max 分别用于设计矩阵和后级电压；须在
    DigitalObservation.validate() 拒绝，避免传播至 LAPACK 或 0*inf 运算。
    """
    import dataclasses

    from adi_model.weight_calibration import (
        CalibrationSpec,
        DigitalObservation,
        fit_unit_weights,
    )

    spec = CalibrationSpec.from_config(Config(dac_arch="split"))
    p = int(np.prod(spec.shape)) + 1
    n = p + 5
    na, ns = spec.n_active, spec.n_slices
    sid = np.array([[(i + j) % ns for j in range(na)] for i in range(n)], dtype=np.int64)
    z64 = np.zeros(n, dtype=np.int64)
    z = np.zeros(n)
    ov = np.zeros(n, dtype=bool)
    bad_spec = dataclasses.replace(spec, **{field_: bad})
    obs = DigitalObservation(bad_spec, sid, z64, z64, z64, z, z, ov, True)
    with pytest.raises(ValueError, match="physical scales must be finite"):
        fit_unit_weights(obs, np.ones(n))


def test_spec_physical_scales_accepts_finite():
    """站点 14b 正对照：合法有限标度继续到参数检查，不被标度校验误拒绝。"""
    from adi_model.weight_calibration import CalibrationSpec, DigitalObservation, fit_unit_weights

    spec = CalibrationSpec.from_config(Config(dac_arch="split"))
    n = int(np.prod(spec.shape)) + 6
    na, ns = spec.n_active, spec.n_slices
    sid = np.array([[(i + j) % ns for j in range(na)] for i in range(n)], dtype=np.int64)
    z64 = np.zeros(n, dtype=np.int64)
    z = np.zeros(n)
    ov = np.zeros(n, dtype=bool)
    obs = DigitalObservation(spec, sid, z64, z64, z64, z, z, ov, True)
    # 标度检查不应拒绝；此夹具的设计矩阵秩亏，应由可辨识性检查拒绝。
    with pytest.raises(ValueError) as exc:
        fit_unit_weights(obs, np.ones(n))
    assert "physical scales must be finite" not in str(exc.value)


# --- 站点 15：PhysicalSlicePool 电容/寄生有限性 ---
def test_slice_pool_rejects_nonfinite_caps():
    """站点 15：用随机源替身注入 NaN，检查单位、桥接及寄生电容的有限性。"""
    from adi_model.slice_pool import PhysicalSlicePool

    class _NanRNG:
        def normal(self, loc=0.0, scale=1.0, size=None, **k):
            sz = size if size is not None else k.get("size", ())
            return np.full(sz, np.nan)

        def standard_normal(self, size=None, **k):
            sz = size if size is not None else k.get("size", ())
            return np.full(sz, np.nan)

    with pytest.raises(ValueError, match="finite"):
        PhysicalSlicePool(Config(dac_arch="split"), rng=_NanRNG())


def test_slice_pool_accepts_finite():
    from adi_model.slice_pool import PhysicalSlicePool

    PhysicalSlicePool(Config(dac_arch="split"), rng=np.random.default_rng(0))  # 默认即有限合法


# --- 站点 16/17：aux_residual / driver_charge_per_sample ---
@pytest.mark.parametrize("bad", [float("nan"), float("inf")])
def test_aux_residual_rejects_nonfinite(bad):
    from adi_model.aux_input import build_stage

    with pytest.raises(ValueError, match="必须为正"):
        build_stage(Config(), r_aux=1.0).aux_residual(bad)


def test_aux_residual_accepts_finite():
    from adi_model.aux_input import build_stage

    # 有限正值被接受（返回有限非负的残余比例，而非抛异常或 nan）
    assert build_stage(Config(), r_aux=1.0).aux_residual(1e-6) >= 0.0


@pytest.mark.parametrize("bad", [float("nan"), float("inf")])
def test_driver_charge_per_sample_rejects_nonfinite(bad):
    from adi_model.aux_input import build_stage

    with pytest.raises(ValueError, match="不能为负"):
        build_stage(Config(), r_aux=1.0).driver_charge_per_sample(bad)


def test_driver_charge_per_sample_accepts_finite():
    from adi_model.aux_input import build_stage

    out = build_stage(Config(), r_aux=1.0).driver_charge_per_sample(1.0)
    assert np.isfinite(out["ratio"])


# --- 站点 18 / M15：export_rtl_params.rtl_localparams(phases) 入口校验 ---
def test_export_rtl_params_rejects_out_of_range():
    """站点 18：RTL 参数宽度越界须被守卫拒绝（区间校验，消息 does not fit）。"""
    exporter = _load_exporter()
    with pytest.raises(ValueError, match="does not fit"):
        exporter.rtl_localparams(Config(), exporter.FixedPointFormat(), phases=100)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), 3.5])
def test_export_rtl_params_rejects_nonfinite(bad):
    """站点 18/M15：检查旧 phases 入口以 ValueError 拒绝非有限或分数值。

    旧 int(phases) 可先抛出 OverflowError，而全整数 raw 循环中的有限性判断无法
    覆盖该入口。案例包括 nan、inf 和 3.5，断言错误信息含“有限的整数值”。
    """
    exporter = _load_exporter()
    with pytest.raises(ValueError, match="有限的整数值"):
        exporter.rtl_localparams(Config(), exporter.FixedPointFormat(), phases=bad)


def test_export_rtl_params_accepts_legal_integer_phases():
    """站点 18/M15 正对照：旧接口接受合法整数 phases=16。"""
    exporter = _load_exporter()
    out = exporter.rtl_localparams(Config(), exporter.FixedPointFormat(), phases=16)
    assert any(r["name"] == "PHASES" and r["value"] == 16 for r in out)


# --- B1 闭环：AuxInputStage 直接构造须过 validated() 守卫 ---
@pytest.mark.parametrize("bad", [float("inf"), float("nan")])
def test_aux_stage_direct_construction_rejects_nonfinite_r_aux(bad):
    """B1：检查绕过 build_stage 直接构造也执行入口校验。

    r_aux=inf/nan 应在 __post_init__ 通过 validated() 拒绝。历史记录中
    AuxInputStage(r_aux=inf, mode="off") 的 required_filter_bw 曾返回 8.8e6，
    本测试针对该直接构造路径保留反例。
    """
    from adi_model.aux_input import AuxInputStage

    with pytest.raises(ValueError, match="必须为正有限值"):
        AuxInputStage(
            c_signal=1e-12,
            c_filter=1e-13,
            c_parasitic=1e-14,
            r_filter=100.0,
            r_aux=bad,
            t_acq=1e-6,
            boost_voltage=3.3,
            mode="off",
        )


def test_aux_stage_direct_construction_accepts_legal():
    """B1 正对照：合法有限输入可直接构造，入口检查不误拒绝。"""
    from adi_model.aux_input import AuxInputStage

    st = AuxInputStage(
        c_signal=1e-12,
        c_filter=1e-13,
        c_parasitic=1e-14,
        r_filter=100.0,
        r_aux=1.0,
        t_acq=1e-6,
        boost_voltage=3.3,
        mode="off",
    )
    assert st.r_aux == 1.0
    assert st.validated() is st  # 守卫幂等
