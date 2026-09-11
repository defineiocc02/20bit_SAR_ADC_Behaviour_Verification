"""运行全部验收实验，生成图表与自包含 HTML 报告。"""

from __future__ import annotations

import json
import os
import time
from dataclasses import replace

import matplotlib
import numpy as np

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from adi_model import (
    Config,
    dc_input,
    mismatch_from_pdk,
    noise_budget,
    resolve_ra_noise,
    run_sim,
    sine_input,
    spectrum_dbfs,
    static_test,
)
from adi_model import experiments as ex
from adi_model.dac_arch import SplitDAC, build_split_chip

OUT = os.environ.get("ADI_MODEL_RESULTS_DIR") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "results"
)
FIG = os.path.join(OUT, "fig")
os.makedirs(FIG, exist_ok=True)

N = 2**16  # 9b 第一级使 ADC2 范围变窄、KTC 带宽守卫收紧，fin 必须 ≤ 0.64 MHz
FIN_NCYC = 1021
FIN = 40e6 * FIN_NCYC / N

# ---------------------------------------------------------------- 画图风格
BG = "#1D1F27"
FG = "#E6E8EE"
GRID = "#33384A"
C1 = "#FF6B6B"
C2 = "#4ECDC4"
C3 = "#FFD93D"
C4 = "#A78BFA"

plt.rcParams.update(
    {
        "figure.facecolor": BG,
        "axes.facecolor": BG,
        "savefig.facecolor": BG,
        "text.color": FG,
        "axes.labelcolor": FG,
        "xtick.color": FG,
        "ytick.color": FG,
        "axes.edgecolor": GRID,
        "grid.color": GRID,
        "grid.alpha": 0.5,
        "font.size": 10,
        "axes.titlesize": 11,
        "legend.frameon": False,
        "font.sans-serif": ["PingFang SC", "Microsoft YaHei", "Helvetica Neue", "Arial"],
        "axes.unicode_minus": False,
    }
)


def _save(fig, name):
    p = os.path.join(FIG, name)
    fig.tight_layout()
    fig.savefig(p, dpi=150)
    plt.close(fig)
    return os.path.relpath(p, OUT)


R = {}
t_all = time.time()

# ================================================================ 配置自检
cfg0 = Config()
R["validate"] = {
    k: {
        "实际": float(v[0]) if v[0] is not None else None,
        "门限": float(v[1]) if v[1] is not None else None,
        "PASS": bool(v[2]),
        "单位": v[3],
    }
    for k, v in cfg0.validate(verbose=False).items()
}
R["validate_ktc"] = {
    k: {
        "实际": float(v[0]) if v[0] is not None else None,
        "门限": float(v[1]) if v[1] is not None else None,
        "PASS": bool(v[2]),
        "单位": v[3],
    }
    for k, v in replace(Config(), ktc_enable=True).validate(verbose=False).items()
    if "KTC" in k
}
R["derived"] = {
    "LSB20_uV": cfg0.lsb_target * 1e6,
    "Delta1_mV": cfg0.delta1 * 1e3,
    "RDAC_step_uV": cfg0.rdac_step * 1e6,
    "units_per_Delta1": cfg0.units_per_lsb1,
    "Delta2_uV": cfg0.delta2 * 1e6,
    "Delta2_over_G_LSB20": cfg0.delta2 / cfg0.g0 / cfg0.lsb_target,
    "C_unit_fF": cfg0.c_unit0 * 1e15,
    "n_units_active": cfg0.n_active * cfg0.n_unit_per_slice,
    "budget_uV": {k: v * 1e6 for k, v in noise_budget(cfg0).items()},
    "pdk_sigma_est": mismatch_from_pdk(cfg0),
    "pdk_sigma_est_ppm": mismatch_from_pdk(cfg0) * 1e6,
}

# ================================================================ Stage 1-4
print("[1/7] 理想两级链路 ...")
R["s1"] = ex.stage1_ideal(n=N)
print("[2/7] 固定物理失配可复现性 ...")
R["s2"] = ex.stage2_mismatch_reproducible()
print("[3/7] DEM ...")
R["s3"] = ex.stage3_dem(n=N)
print("[4/7] Dither ...")
R["s4"] = ex.stage4_dither(n=N)

# ================================================================ 图 1：DEM 频谱
print("      画图: DEM 频谱对比")
fig, ax = plt.subplots(figsize=(9, 4))
for dem, col, lab in ((False, C1, "DEM 关"), (True, C2, "DEM 开")):
    c = replace(Config(), dem_enable=dem, mismatch_sigma0=3e-4)
    r = run_sim(c, sine_input(0.9 * c.v_fs, FIN), N, rng=np.random.default_rng(5))
    f, db = spectrum_dbfs(r.err, c.fs, c.v_fs)
    ax.semilogx(f[1:] / 1e6, db[1:], color=col, lw=0.9, alpha=0.9, label=lab)
ax.set_xlabel("频率 [MHz]")
ax.set_ylabel("dBFS")
ax.set_title("DEM：把码相关的确定性失真打散成本底噪声（峰值杂散消失，噪声抬高）")
ax.set_ylim(-180, -60)
ax.grid(True, which="both", alpha=0.3)
ax.legend()
R["fig_dem_spectrum"] = _save(fig, "dem_spectrum.png")

# ================================================================ 图 2：静态曲线
print("      画图: 静态均值误差 / 条件标准差")
fig, axes = plt.subplots(1, 2, figsize=(11, 4))
lv = np.linspace(-0.995, 0.995, 129) * cfg0.v_fs
for dem, col, lab in ((False, C1, "DEM 关"), (True, C2, "DEM 开")):
    c = replace(Config(), dem_enable=dem, enable_sampling_noise=False, ra_enable_noise=False)
    st = static_test(
        lambda lev, rp, c=c: run_sim(c, dc_input(lev), rp, rng=np.random.default_rng(3)).out, lv, 96
    )
    axes[0].plot(st["levels"], st["mu_e"] / cfg0.lsb_target, color=col, lw=1.2, label=lab)
    axes[1].plot(st["levels"], st["sigma_e"] * 1e6, color=col, lw=1.2, label=lab)
axes[0].set_title(r"平均误差 $\mu_e(x)$：DEM 把转移曲线拉直")
axes[0].set_ylabel("LSB@20b")
axes[1].set_title(r"条件标准差 $\sigma_e(x)$：单次输出更分散")
axes[1].set_ylabel("µV")
for a in axes:
    a.set_xlabel("输入 [V]")
    a.grid(True, alpha=0.3)
    a.legend()
R["fig_static"] = _save(fig, "static_curves.png")

# ================================================================ Stage 5/6
print("[5/7] 采样噪声与电容缩放 ...")
R["s5"] = ex.stage5_cap_scaling(n=N)
print("[6/7] KTC ...")
R["s6"] = ex.stage6_ktc(n=N)

# ================================================================ 图 3：KTC
print("      画图: KTC 观测噪声与 beta 失配")
fig, axes = plt.subplots(1, 2, figsize=(11, 4))
rows = R["s6"]["rows"]
en = sorted({r["e_n_uV"] for r in rows})
for ktc, col, lab in ((False, C1, "KTC 关"), (True, C2, "KTC 开")):
    y = [r["SNDR_dB"] for r in rows if r["ktc"] == ktc]
    axes[0].plot(en, y, "o-", color=col, label=lab)
axes[0].set_xlabel(r"观测通路噪声 $e_N$ [µV]")
axes[0].set_ylabel("SNDR [dB]")
axes[0].set_title("KTC 净收益随观测通路噪声衰减")
axes[0].grid(True, alpha=0.3)
axes[0].legend()

br = R["s6"]["beta_rows"]
be = [r["beta_error"] for r in br]
axes[1].plot(be, [r["SFDR_dB"] for r in br], "o-", color=C2, label="SFDR")
axes[1].plot(be, [r["SNDR_dB"] for r in br], "s-", color=C1, label="SNDR")
axes[1].plot(be, [r["THD_dB"] for r in br], "^--", color=C3, label="THD")
axes[1].set_xscale("symlog", linthresh=1e-5)
axes[1].set_xlabel(r"$\beta$ 失配")
axes[1].set_ylabel("dB")
axes[1].set_title(r"$\beta$ 失配不改变频谱 → 它是采样时刻插值权重，非失真")
axes[1].grid(True, alpha=0.3)
axes[1].legend()
R["fig_ktc"] = _save(fig, "ktc.png")

# ================================================================ Stage 7
print("[7/7] 缩电容 × KTC 扫描 + 判别性对比 ...")
SWEEP_CFG = replace(Config(), dem_enable=True)
R["sweep"] = ex.scaling_sweep(SWEEP_CFG, n=N, seed=31, e_n=30e-6)
R["s7_dem_on"] = ex.stage7_headline(replace(Config(), dem_enable=True), n=N, shrink=0.25, e_n=30e-6)
R["s7_dem_off"] = ex.stage7_headline(
    replace(Config(), dem_enable=False), n=N, shrink=0.25, e_n=30e-6
)
R["mc"] = ex.monte_carlo(replace(Config(), dem_enable=True), n_chips=16, n=2**13)

# ================================================================ Stage 8：失配压力测试
print("[8/8] 失配专项压力测试 + 良率 ...")
R["s8"] = ex.stage8_mismatch_stress()
R["mc_pdk_off"] = ex.monte_carlo_yield(1117, dem=False, n_chips=60)
R["mc_pdk_on"] = ex.monte_carlo_yield(1117, dem=True, n_chips=60)
R["mc_cal_off"] = ex.monte_carlo_yield(100, dem=False, n_chips=60)
R["mc_cal_on"] = ex.monte_carlo_yield(100, dem=True, n_chips=60)
R["budget"] = ex.mismatch_budget_limit(target_sndr_db=93.0, dem=True)
R["s9"] = ex.stage9_patent_mechanisms(n=2**13)
R["s10"] = ex.stage10_audit_acceptance(n=2**13)
# v3 审计验收一览
_s10_pass = {
    "资源与噪声一致": R["s10"]["noise_vs_pool"]["PASS"],
    "C_F->增益(电荷一致)": R["s10"]["feedback_cap_to_gain"]["PASS"],
    "增益校准联调": R["s10"]["calibration_e2e"]["gain_PASS"],
    "beta校准": R["s10"]["calibration_e2e"]["beta_PASS"],
    "指标已知答案": R["s10"]["metrics_known_answer"]["PASS"],
}
R["s10_summary"] = _s10_pass
print("== Stage 10 审计验收 ==")
for k, v in _s10_pass.items():
    print(f"  {k}: {'PASS' if v else 'FAIL'}")
R["s11"] = ex.stage11_audit_v4(n=2**14)
_s11_pass = {
    "eta_n/eta_x 三激励验证": R["s11"]["eta_validation"]["PASS"],
    "全局失配=码相关锯齿": bool(R["s11"]["global_mismatch_structure"]["PASS_sawtooth"]),
    "校准可观测性 rank(U)=64": R["s11"]["calibration_observability"]["PASS"],
    "KTC 双 beta 模型吻合": R["s11"]["ktc_dual_beta"]["PASS_model"],
    "SFDR 保护带多距离": R["s11"]["sfdr_guard_multi"]["PASS"],
}
R["s11_summary"] = _s11_pass
print("== Stage 11 v4 审计验收 ==")
for k, v in _s11_pass.items():
    print(f"  {k}: {'PASS' if v else 'FAIL'}")

# ================================================================ v5 结构建模
print("\n===== stage12: 分段 DAC 拓扑（主/子 + 桥接电容） =====")
R["s12"] = ex.stage12_split_arch(Config(), n=2**14)
_bs_off = [r for r in R["s12"]["bridge_sweep"] if not r["dem"]][-1]["sawtooth_pp_uV"]
_bs_on = [r for r in R["s12"]["bridge_sweep"] if r["dem"]][-1]["sawtooth_pp_uV"]
_s12_pass = {
    "DEM 对 C_C 锯齿无效（结构性质，DEM 开/关偏差 ≤5%）": abs(_bs_off - _bs_on)
    / max(_bs_off, _bs_on, 1e-9)
    < 0.05,
    "分段拓扑 SNDR 与 unary 同量级": all(85 < r["SNDR_dB"] < 100 for r in R["s12"]["topologies"]),
    "电荷口径闭合：闭式解 = 节点矩阵求解器（fV 级）": R["s12"]["charge_closure"][
        "closedform_vs_nodalmatrix_max_V"
    ]
    < 1e-15,
    "电荷口径闭合：evaluate_physical = 独立节点方程（pV 级）": R["s12"]["charge_closure"][
        "eval_vs_nodal_max_V"
    ]
    < 1e-12,
    "电荷口径闭合：输入=输入等效DAC电压 -> 残差为零（nV 级）": R["s12"]["charge_closure"][
        "residue_zero_max_V"
    ]
    < 1e-6,
}
R["s12_summary"] = _s12_pass
for k, v in _s12_pass.items():
    print(f"  {k}: {'PASS' if v else 'FAIL'}")

print("\n===== stage13: 三项动态误差（输入建立/参考建立/串扰） =====")
R["s13"] = ex.stage13_dynamics(Config(), n=2**14)
_inl_all = next(r for r in R["s13"]["rows"] if r["设置"] == "全部开启")
_s13_pass = {
    # 判据由解析式给出（experiments.stage13.sub_weight_inl），不再写死 1.5：
    # 子阵列被码调制时是等式（±25%），不被调制时残余应是小量。
    "base 行确定性 INL 与其解析预测吻合（子阵列节点寄生）": R["s13"]["sub_weight_inl"]["PASS"],
    "三项动态误差把确定性峰值 INL 抬到论文 2.2 LSB 同量级": (
        1.0 <= _inl_all["INL_max_LSB20"] <= 12.0
    ),
    "理想芯片确定性协议下 rho=0 峰值 INL ≈ 结构性基准且随 rho 单调放大": (
        R["s13"]["ron_sweep"][0]["INL_max_LSB20"] < 1.5
        and R["s13"]["ron_sweep"][-1]["INL_max_LSB20"]
        > 10 * max(R["s13"]["ron_sweep"][0]["INL_max_LSB20"], 0.1)
    ),
    "完整 DEM 周期覆盖后 INL 不随 rep 变化（确定性协议收敛）": R["s13"]["rep_convergence"]["PASS"],
    "确定性峰值口径反推出 rho 设计边界（条件性估计）": R["s13"]["rho_max_for_2p2LSB"] is not None
    and R["s13"]["rho_max_INL_at_rho_max"] <= 2.2,
}
R["s13_summary"] = _s13_pass
for k, v in _s13_pass.items():
    print(f"  {k}: {'PASS' if v else 'FAIL'}")

print("\n===== stage14: 校准可观测性 + rank-aware 估计 =====")
R["s14"] = ex.stage14_observability(Config(), n_cal=4096)
_maps = {r["映射"]: r for r in R["s14"]["maps"]}
_cals = {r["映射"]: r for r in R["s14"]["calibration"]}
_s14_pass = {
    "fixed 交织映射秩不足": _maps["fixed"]["rank"] < _maps["fixed"]["n_units"] / 2,
    "随机置换映射接近满秩": _maps["permute"]["rank"] >= 0.9 * _maps["permute"]["n_units"],
    "permute 校准显著改善（整数码，改善 ≥3 倍）": _cals["permute"]["改善倍数_A"] > 3.0,
    "dither 分数码泄漏仍低于噪声底": _cals["dem_rotate"]["evalB_分数码残差_uV"]
    < R["s14"]["noise_floor_uV"],
    "permute 校准对分数码鲁棒": abs(
        _cals["permute"]["evalB_分数码残差_uV"] - _cals["permute"]["evalA_整数码残差_uV"]
    )
    / _cals["permute"]["evalA_整数码残差_uV"]
    < 0.3,
}
R["s14_summary"] = _s14_pass
for k, v in _s14_pass.items():
    print(f"  {k}: {'PASS' if v else 'FAIL'}")

print("\n===== stage15: 设计反推规格表 =====")
R["s15"] = ex.stage15_design_guide(Config(), R["s12"], R["s13"], R["s14"])
print(f"  共 {len(R['s15']['rows'])} 条规格行（参数来源分级见报告）")

print("\n===== v5.1: KTC 观测尺度回归矩阵（unary/split × dither × KTC） =====")
R["s16_ktc_scale"] = ex.ktc_scale_matrix(Config())
print(f"  {R['s16_ktc_scale']['判据']}: " f"{'PASS' if R['s16_ktc_scale']['PASS'] else 'FAIL'}")

# ================================================================ v5.2
print("\n===== stage16: 逐相位噪声状态传递（a≈κb 可观测性） =====")
R["noise_transfer"] = ex.stage16_noise_transfer(Config())
print(f"  {R['noise_transfer']['判据']}")
print(f"  -> {'PASS' if R['noise_transfer']['PASS'] else 'FAIL'}")

print("\n===== stage17: split 校准闭环 v1（主/子有效权重） =====")
R["split_calib"] = ex.stage17_split_calib(Config())
print(f"  {R['split_calib']['判据']}")
print(f"  -> {'PASS' if R['split_calib']['PASS'] else 'FAIL'}")

print("\n===== stage18: 带 dither 的采样相位电荷闭合 + 离散掩码 =====")
R["dither_closure"] = ex.stage18_dither_charge_closure(Config())
print(f"  {R['dither_closure']['判据']}")
print(f"  -> {'PASS' if R['dither_closure']['PASS'] else 'FAIL'}")

print("\n===== stage19: v6 整体信号流（逐相位状态机参考实现） =====")
R["pipeline"] = ex.stage19_pipeline(Config(), n=2**13)
_s19 = R["pipeline"]
print(f"  {R['pipeline']['判据']}")
print(f"  -> {'PASS' if R['pipeline']['PASS'] else 'FAIL'}")

# ================================================================ v6.1 逐字审计缺口闭合
print("\n===== stage20: 交织误差四件套收官（逐 slice offset） =====")
R["il_offset"] = ex.stage20_interleave_offset(Config(), n=2**13)
print(f"  {R['il_offset']['判据']}")
print(f"  -> {'PASS' if R['il_offset']['PASS'] else 'FAIL'}")

print("\n===== stage21: 量化器侧 dither 与 2b 增强 =====")
R["dither_quant"] = ex.stage21_dither_quant(Config(), n=2**14)
print(f"  {R['dither_quant']['判据']}")
print(f"  -> {'PASS' if R['dither_quant']['PASS'] else 'FAIL'}")

print("\n===== stage22: RA auto-zero / ADC2 动态采样带宽（−1.6/+1.3 dB 归因） =====")
R["autozero"] = ex.stage22_autozero(Config(), n=2**14)
print(f"  {R['autozero']['判据']}")
print(f"  -> {'PASS' if R['autozero']['PASS'] else 'FAIL'}")

print("\n===== stage23: 1/f 噪声（转角 40 Hz）与 auto-zero 交互 =====")
R["flicker"] = ex.stage23_flicker(Config(), n=2**15)
print(f"  {R['flicker']['判据']}")
print(f"  -> {'PASS' if R['flicker']['PASS'] else 'FAIL'}")

print("\n===== stage24: RDAC 逐位装载（loaded as they develop） =====")
R["rdac_bitwise"] = ex.stage24_rdac_bitwise(Config(), n=2**14)
print(f"  {R['rdac_bitwise']['判据']}")
print(f"  -> {'PASS' if R['rdac_bitwise']['PASS'] else 'FAIL'}")

# ---- 图 7：v5 结构图组 ----
print("      画图: v5 拓扑对比 / 动态误差 / 可观测性")
_c12 = replace(Config(), dac_arch="split", dac_n_main=64, dac_n_sub=8)
_ch12 = build_split_chip(_c12)
_d12 = SplitDAC(_c12, _ch12)
_p12 = _d12.inl_profile()
fig, axes = plt.subplots(1, 3, figsize=(15, 4))
axes[0].plot(_p12["code"], _p12["error_LSB_sub"], color=C2, lw=0.6)
axes[0].set_xlabel("电平码")
axes[0].set_ylabel("DAC 误差 [LSB_sub]")
axes[0].set_title("分段 DAC 静态误差（DEM 关）\n含 C_C 锯齿与单位失配")
axes[0].grid(True, alpha=0.3)

names = [r["拓扑"] for r in R["s12"]["topologies"]]
xx = np.arange(len(names))
axes[1].bar(
    xx - 0.2,
    [r["sigma_eps_ppm"] for r in R["s12"]["topologies"]],
    0.4,
    color=C1,
    label="单位失配 σ_ε [ppm]",
)
axes[1].bar(
    xx + 0.2, [r["单位数"] for r in R["s12"]["topologies"]], 0.4, color=C4, label="单位数（面积）"
)
axes[1].set_yscale("log")
axes[1].set_xticks(xx)
axes[1].set_xticklabels(names, fontsize=8)
axes[1].legend(fontsize=8)
axes[1].set_title("同样总面积：分段 → 单位更大 → 失配更小")
axes[1].grid(True, axis="y", alpha=0.3)

rows13 = R["s13"]["rows"]
yy = np.arange(len(rows13))
axes[2].barh(yy, [r["INL_max_LSB20"] for r in rows13], color=C3)
axes[2].set_yticks(yy)
axes[2].set_yticklabels([r["设置"] for r in rows13], fontsize=8)
axes[2].axvline(2.2, color=C1, ls=":", label="论文 INL 2.2 LSB")
axes[2].invert_yaxis()
axes[2].set_xlabel("|INL| [LSB@20b]")
axes[2].legend(fontsize=8)
axes[2].set_title("动态误差贡献的 INL")
axes[2].grid(True, axis="x", alpha=0.3)
R["fig_v5"] = _save(fig, "v5_structure.png")

# ---- 图 6：DEM 净收益 vs 失配量级 ----
print("      画图: DEM 净收益 与 失配良率")
fig, axes = plt.subplots(1, 3, figsize=(15, 4))
d = R["s8"]["dem_delta"]
sp = [r["sigma_ppm"] for r in d]
axes[0].axhline(0, color=FG, lw=0.8, ls=":")
axes[0].plot(sp, [r["dSNDR_dB"] for r in d], "o-", color=C1, label="ΔSNDR")
axes[0].plot(sp, [r["dTHD_dB"] for r in d], "s-", color=C2, label="ΔTHD")
axes[0].set_xscale("log")
axes[0].set_xlabel(r"单位电容失配 $\sigma$ [ppm]")
axes[0].set_ylabel("dB（DEM 开 − DEM 关）")
axes[0].set_title("DEM 净收益：失配越大越负")
axes[0].grid(True, alpha=0.3)
axes[0].legend(fontsize=8)

rows8 = R["s8"]["rows"]
for dem, col, lab in ((False, C1, "DEM 关"), (True, C2, "DEM 开")):
    sub = [r for r in rows8 if r["dem"] == dem]
    xs = [r["sigma_ppm"] for r in sub]
    axes[1].plot(xs, [r["SNDR_dB"] for r in sub], "o-", color=col, label=lab)
axes[1].axhline(93.5, color=FG, lw=0.8, ls=":", label="论文 93.5dB")
axes[1].axvline(1117, color=C3, lw=1.0, ls="--", label="PDK 估算 1117ppm")
axes[1].set_xscale("log")
axes[1].set_xlabel(r"单位电容失配 $\sigma$ [ppm]")
axes[1].set_ylabel("SNDR [dB]")
axes[1].set_title("失配是硬预算：PDK 量级下崩 5.5 dB")
axes[1].grid(True, alpha=0.3)
axes[1].legend(fontsize=8)

# 直方图必须画**真实的 per-chip 样本**。v6.1 在这里用
#   samp = rng.normal(m["SNDR_mean"], m["SNDR_std"], 400)
# 把 60 颗芯片的分布"重采样"成 400 个高斯点 —— 图形是拟合示意，不是数据，
# 尾部/良率判断会被它系统性误导（外部审计 A07.4 / F10）。
# 现在 monte_carlo_yield 返回 sndr_per_chip，直接用原始点。
for key, col, lab in (("mc_cal_on", C2, "σ=100ppm 标定值"), ("mc_pdk_on", C1, "σ=1117ppm PDK")):
    m = R[key]
    samp = np.asarray(m["sndr_per_chip"], dtype=float)
    assert samp.size == int(m["n_chips"]), (
        f"{key}: histogram must be drawn from the {m['n_chips']} real chips, "
        f"got {samp.size} samples (audit A07.4)"
    )
    axes[2].hist(samp, bins=12, alpha=0.55, color=col, density=True, label=lab)
axes[2].axvline(93.5, color=FG, lw=0.8, ls=":", label="论文 93.5dB")
axes[2].set_xlabel("SNDR [dB]")
axes[2].set_ylabel("概率密度")
axes[2].set_title("60 颗虚拟芯片的 SNDR 分布（原始 MC 点，无重采样）")
axes[2].grid(True, alpha=0.3)
axes[2].legend(fontsize=8)
R["fig_mismatch"] = _save(fig, "mismatch_stress.png")

# ================================================================ 图 4：缩放扫描
print("      画图: 缩电容 × KTC 扫描")
rows = R["sweep"]["rows"]
fig, axes = plt.subplots(1, 3, figsize=(14, 4))
for ktc, col, lab in ((False, C1, "KTC 关"), (True, C2, "KTC 开")):
    sub = [r for r in rows if r["ktc"] == ktc]
    xs = [r["C_total_pF"] for r in sub]
    axes[0].plot(xs, [r["SNDR_dB"] for r in sub], "o-", color=col, label=lab)
    axes[1].plot(xs, [r["NSD_nV_rtHz"] for r in sub], "o-", color=col, label=lab)
    axes[2].plot(xs, [r["INL_max_LSB"] for r in sub], "o-", color=col, label=lab)
axes[0].axhline(93.5, color=FG, lw=0.8, ls=":", label="论文 SNDR 93.5dB")
axes[1].axhline(8.8, color=FG, lw=0.8, ls=":", label="论文 NSD 8.8nV/rtHz")
axes[2].axhline(2.2, color=C3, lw=0.8, ls=":", label="论文 INL 2.2LSB")
axes[0].set_ylabel("SNDR [dB]")
axes[1].set_ylabel("NSD [nV/√Hz]")
axes[2].set_ylabel("|INL| [LSB@20b]")
for a in axes:
    a.set_xscale("log")
    a.set_xlabel("RDAC 总电容 [pF]")
    a.grid(True, alpha=0.3)
    a.legend(fontsize=8)
axes[0].set_title("KTC 把 SNDR 的电容敏感度拉平")
axes[2].set_title("但 INL 只由失配决定，KTC 帮不上")
R["fig_sweep"] = _save(fig, "sweep.png")

# ================================================================ 图 5：噪声预算
fig, axes = plt.subplots(1, 2, figsize=(11, 4))
scales = np.array([1.0, 0.5, 0.25, 0.125, 0.0625])
ktc_list, ra_list, tot_off, tot_on = [], [], [], []
ra_fixed = resolve_ra_noise(Config()) / Config().g_actual
for s in scales:
    c = replace(Config(), cap_scale=s)
    sn = c.sigma_sampling(s)
    ktc_list.append(sn * 1e6)
    ra_list.append(ra_fixed * 1e6)
    tot_off.append(np.hypot(sn, ra_fixed) * 1e6)
    tot_on.append(ra_fixed * 1e6)
axes[0].plot(scales * 20.5, ktc_list, "o-", color=C1, label="kT/C 采样噪声")
axes[0].plot(scales * 20.5, ra_list, "s-", color=C2, label="残差放大器（锁定不变）")
axes[0].plot(scales * 20.5, tot_off, "^-", color=C3, label="合计（无 KTC）")
axes[0].plot(scales * 20.5, tot_on, "d--", color=C4, label="合计（KTC 全消除）")
axes[0].set_xscale("log")
axes[0].set_xlabel("RDAC 总电容 [pF]")
axes[0].set_ylabel("输入折算噪声 [µV rms]")
axes[0].set_title("噪声预算：缩电容只放大 kT/C，RA 那一份不动")
axes[0].grid(True, alpha=0.3)
axes[0].legend(fontsize=8)

ax2 = axes[1]
w = 0.35
idx = np.arange(len(scales))
sub_off = [r for r in rows if not r["ktc"]]
sub_on = [r for r in rows if r["ktc"]]
ax2.bar(idx - w / 2, [r["SNDR_dB"] for r in sub_off], w, color=C1, label="KTC 关")
ax2.bar(idx + w / 2, [r["SNDR_dB"] for r in sub_on], w, color=C2, label="KTC 开")
ax2.set_xticks(idx)
ax2.set_xticklabels([f"{r['C_total_pF']:.2f}" for r in sub_off])
ax2.set_xlabel("RDAC 总电容 [pF]")
ax2.set_ylabel("SNDR [dB]")
ax2.set_title("同一颗虚拟芯片下的判别性对比")
ax2.grid(True, axis="y", alpha=0.3)
ax2.legend()
R["fig_budget"] = _save(fig, "budget.png")

print(f"总耗时 {time.time() - t_all:.1f}s")
print(
    f"验收汇总: v5 结构 {sum(_s12_pass.values())}/{len(_s12_pass)} + "
    f"动态 {sum(_s13_pass.values())}/{len(_s13_pass)} + "
    f"可观测性 {sum(_s14_pass.values())}/{len(_s14_pass)} + "
    f"KTC矩阵 {'PASS' if R['s16_ktc_scale']['PASS'] else 'FAIL'} + "
    f"噪声传递 {'PASS' if R['noise_transfer']['PASS'] else 'FAIL'} + "
    f"校准闭环 {'PASS' if R['split_calib']['PASS'] else 'FAIL'} + "
    f"dither闭合 {'PASS' if R['dither_closure']['PASS'] else 'FAIL'} + "
    f"v6信号流 {'PASS' if R['pipeline']['PASS'] else 'FAIL'} + "
    f"审计缺口闭合(offset/dither量化/auto-zero/1-f/逐位装载) "
    f"{sum(bool(R[k]['PASS']) for k in ('il_offset', 'dither_quant', 'autozero', 'flicker', 'rdac_bitwise'))}/5"
)


def _jsonable(o):
    if isinstance(o, np.ndarray):
        return o.tolist() if o.ndim else o.item()
    if isinstance(o, np.floating | np.integer | np.bool_):
        return o.item()
    if isinstance(o, list | tuple):
        return [_jsonable(v) for v in o]
    if isinstance(o, dict):
        return {k: _jsonable(v) for k, v in o.items()}
    return float(o) if isinstance(o, float) else o


with open(os.path.join(OUT, "results.json"), "w") as f:
    json.dump(_jsonable(R), f, ensure_ascii=False, indent=1)
print("结果已写入", os.path.join(OUT, "results.json"))
