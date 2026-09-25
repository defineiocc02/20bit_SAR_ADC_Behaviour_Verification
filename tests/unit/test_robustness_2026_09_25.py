"""2026-09-25 独立审查修复的回归测试（v8.2.0 发布物的一部分）。

每条测试对应一项已被独立复核的发现（F1–F17 来自 2026-09-25 首轮审查，
N1–N5 来自同日的第二轮复查）。修复前的行为要么产生**静默垃圾数值**，
要么抛出难以定位的裸异常；这里的断言把"入口必须拒绝 / 口径必须一致"
钉死，防止后续改动把这些防护悄悄拿掉。

数值口径类断言（对拍、独立性）不写死具体数字，只写**界**，避免与随机
实现细节耦合。
"""

from __future__ import annotations

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
    """F2 的依据：sim.py 注释声称 spawn 不影响主流——把该假设钉成测试。"""
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
    """F3a：这五个量任何非法取值都必须在合法检查里报出来（旧实现静默放行）。"""
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
    """F6 姊妹断言：合法正值（1.0）不被守卫误伤。"""
    build_stage(Config(), r_aux=1.0).validated()  # 不抛异常即过关


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
    """N4：n_mask=None 的口径（文档：整列长度）在两个后端必须一致且不崩。"""
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
    """N4：n_mask=0 表示无掩码列，必须是零贡献而不是广播异常。"""
    cfg = Config(dac_arch="split")
    chip = build_split_chip(cfg)
    q, c = dither_mask_charge(chip, cfg.v_fs, np.array([0, 3, -2]), cfg.dither_split_bank, 0)
    assert c == 0.0
    assert np.allclose(np.asarray(q, dtype=float), 0.0)


# --------------------------------------------------------------- F9 / N2
def test_crosstalk_activity_uses_fractional_half_units():
    """F9/N2：单位串扰按 A(k)/2 个单位的**分数**前缀和计（独立逐项复算，不共用向量式）。

    判别力：A(k)=3（分数码 k=1.5）时分数口径取 1.5 个单位、取整口径取 2 个，
    两者必然不同 —— 断言实测值等于分数口径即证明用的是分数插值。
    """
    n_u, p0 = 63, 1e-15
    grad = np.linspace(-1.0, 1.0, n_u)
    prof = p0 * (1.0 + 0.5 * grad)  # 梯度空间分布：本机制存在的意义所在
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
    from adi_model.calib import ridge_fit

    U = np.array([[1.0, 2.0], [2.0, 4.0], [3.0, 6.0]])  # 秩 1（两列成比例）
    y = np.array([1.0, 2.0, 3.0])
    w, used = ridge_fit(U, y, lam=0.0)
    assert np.all(np.isfinite(w))
    # 秩亏：只能辨识 1 个方向，不得报告成"全部估出"
    assert used == 1 and used < U.shape[1]
    # 手算核对：UtU=[[14,28],[28,56]]，Uty=14·[1,2] ⇒ 解空间 w0+2*w1=1；
    # 其上的最小范数点 = (1/5)·[1,2] = [0.2, 0.4]（唯一）
    assert np.allclose(w, [0.2, 0.4], rtol=1e-12, atol=1e-15)
    assert float(np.linalg.norm(w)) == pytest.approx(1.0 / np.sqrt(5.0), rel=1e-12)
    # 同一残差的其他解（如 [1,0]）范数更大 ⇒ 证明取的是最小范数解而非任意特解
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

    # 严格零误差窗口（std=0）→ inf，与 a06 口径一致，且不抛异常
    assert make(np.zeros(16))._window_bits(slice(None), span_v=1.0) == float("inf")
    # 常数窗口同样 std=0：是"零窗口"而非"小误差窗口"，必须也是 inf
    assert make(np.full(16, 1e-6))._window_bits(slice(None), span_v=1.0) == float("inf")
    # 非退化（非常数）微误差窗口 → 有限位数，且与公开换算函数逐位一致
    err = np.linspace(-1e-6, 1e-6, 16)
    finite = make(err)._window_bits(slice(None), span_v=1.0)
    assert np.isfinite(finite)
    assert finite == pytest.approx(reference_precision_bits(float(np.std(err)), 1.0), rel=1e-12)
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
    """F13（次要漏网）：域内分数粗码（如 2.5）此前被放行，会经
    `k=coarse*units_per_lsb1+k0` 产生非整数单位数 k。SADC 只输出整数码。
    同时守住既有"粗码越界"分支顺序与消息不回归。
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
    """F10：短记录（<4096）beta 相干 bin 不得越 Nyquist。作者 39 条回归里**没有**
    任何一条触达该修正——这里 runtime 捕获真正传给正弦前台的 `fin`，断言 0<fin<fs/2。
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
    """F11：lam=0 的"欠定+秩亏"分支（U 行<列，走 U@U.T 对偶式）也必须给出
    最小范数解，与独立 np.linalg.lstsq 一致。作者原测试只覆盖超定（3×2）分支。
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
    """F9/N2：均匀开关活动 profile 下，修正后的 crosstalk 公式相对旧公式
    `e_unit = dyn_v * cum[round(a_k)]` 数值不变（相对容差 ~1e-12）。
    这条"均匀不变"承诺此前只存在于文字里，无回归断言。
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
    """F16（对称缺口）：beta_x_of 此前**完全没有**域守卫，与 beta_n_of 同源的
    nan/inf/<=0 漏网。修复后两函数必须拒绝同一组非法 g_r，且合法值有限且等于
    kappa*g_n*eta_x/2（g_r=2.0 时）。
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
    """N3：含论文/专利截图的内部审查目录必须被 .gitignore 结构性拦住。"""
    patterns = (REPO / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "docs/robustness_review_2026/" in patterns
    idx_allow = patterns.index("!docs/**/*.png")
    idx_block = patterns.index("docs/robustness_review_2026/")
    assert idx_block > idx_allow, "反向规则必须写在 !docs/**/*.png 之后才生效"


def test_sdist_excludes_the_third_party_report_figures():
    """N3（真闸门）：**发行通道不由 .gitignore 管辖**，且目录型模式在构建后端失效。

    2026-09-25 发版实测两连击：
      1) hatchling 对 .gitignore 的模拟与 git 语义不一致 —— git 规定“父目录被排除
         时无法再反包含其中文件”，hatchling 却按模式顺序判定，于是 `!docs/**/*.png`
         盖过目录级排除，把报告里的 ISSCC 论文页截图与专利首页截图打进 sdist；
      2) 更隐蔽的是：hatchling 的 ``path_is_excluded`` 对**文件路径**调用
         ``GitIgnoreSpec.match_file``，而 gitignore 的“目录型模式”（``dir`` / ``dir/``）
         在文件路径上恒为 False（git 靠遍历剪枝目录而非匹配文件）。因此只写
         ``docs/robustness_review_2026`` 时**一条文件都挡不住**，必须写成 ``dir/**``。

    这条测试直接调用构建后端的判定函数（真实入口），并做双向断言 —— 只查
    “配置里有没有这个字符串”会给出已合规的假象。
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

    # 反向断言：自有图与参考产物必须**仍在**包里，否则 README 的图会 404
    must_keep = [
        "docs/release_v8.2.0/fig/headline_compare.png",
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
