"""experiments.py -- 对照实验、缩电容扫描、Monte Carlo。

按固定顺序逐级开启，才能知道收益来自哪里：

| 顺序 | 做什么                 | 必须通过的检查                                   |
|------|------------------------|--------------------------------------------------|
| 1    | 理想两级链路           | 无溢出时重构正确，误差符合后端量化步长           |
| 2    | 固定物理失配           | 相同芯片、相同控制状态，误差可复现               |
| 3    | DEM                    | 名义值严格守恒；检查失真下降是否伴随噪声上升     |
| 4    | Dither                 | 理想器件下去除后无明显残留；失配下再看线性化收益 |
| 5    | 采样噪声与电容缩放     | 同时改变噪声和失配，不能只改其中一个             |
| 6    | KTC                    | 理想增益匹配时消除目标采样噪声；新增观测噪声仍保留 |
| 7    | 联合测试               | 相同带宽、相同处理、相同虚拟芯片条件下比较       |
"""

from __future__ import annotations

import math
from dataclasses import replace

import numpy as np

from .chip import build_chip, rescale_chip
from .config import Config, noise_budget, resolve_ra_noise
from .mapper import Mapper, dither_transfer_code
from .metrics import inl_from_mean_error, sine_fit_metrics, static_test
from .sadc import units_per_first_stage_step
from .sampler import dc_input, sine_input
from .scheduler import Scheduler
from .sim import run_sim, run_with_calibration


def _coherent_fin(cfg: Config, n_fft: int, n_cycles: int = 1021) -> float:
    """计算相干采样频率 fin = fs·n_cycles / n_fft（Hz）。

    Args:
        cfg: 配置，提供采样率 fs（Hz）。[披露]
        n_fft: FFT/采样点数（相干、整数）。[无量纲]
        n_cycles: 窗内整数周期数，默认 1021（质数，避免频谱泄漏）。[无量纲]

    Returns:
        相干采样频率 fin（Hz）。
    """
    return cfg.fs * n_cycles / n_fft


def _clone(cfg: Config, **kw) -> Config:
    """以关键字覆盖创建一个配置副本，不改动原配置（dataclasses.replace 包装）。

    Args:
        cfg: 基准配置（Config），不会被修改。
        **kw: 要覆盖的配置字段（键为字段名，值的单位同各字段定义）。

    Returns:
        Config：应用覆盖后的新配置副本（dataclasses.replace 包装）。
        原配置保持不变，便于在同一次扫描里对不同开关做对照。
    """
    return replace(cfg, **kw)


# =====================================================================
# 失配系数标定：让 s=1、DEM 开时的 SNDR 对齐论文值
# =====================================================================
def calibrate_mismatch_for_sndr(
    cfg: Config | None = None,
    target_sndr_db: float = 93.5,
    n: int = 2**15,
    seed: int = 5,
    lo: float = 1e-6,
    hi: float = 3e-3,
    iters: int = 14,
) -> dict:
    """对数二分标定 mismatch_sigma0，使 s=1、DEM 开时 SNDR 对齐论文 93.5 dB。

    验证什么：本模型失配幅度是行为学自由参数，需标到使理想基准 SNDR 等于
        论文披露值，后续所有失配实验才在同一基准上比较（[拟合]）。
    方法：在 [lo,hi] 做 iters 次几何二分，每次用 sndr_at 在该 sigma 下跑 n 点
        正弦得 SNDR，与 target_sndr_db 比较收紧边界。
    判据：sndr_at(mid) > 目标则 lo=mid（sigma 偏大、SNDR 偏低），否则 hi=mid；
        最终取 sqrt(lo·hi) 为拟合值，并回报告实测 SNDR。
    注意：这是行为学标定，不是 PDK 值，不得用于良率结论。

    Args:
        cfg: 基础配置；None 用默认 Config。[拟合]
        target_sndr_db: 目标 SNDR（dB）。[披露]
        n: 每次评估的采样点数。[无量纲]
        seed: 芯片与噪声实现的随机种子。[无量纲]
        lo: sigma 搜索下界（相对 F）。[拟合]
        hi: sigma 搜索上界（相对 F）。[拟合]
        iters: 二分迭代次数。[无量纲]

    Returns:
        mismatch_sigma0: 标定得到的单位失配 sigma（相对 F）。
        SNDR_at_fit_dB: 该 sigma 下实测 SNDR（dB）。
        target_dB: 目标 SNDR（dB）。
        note: 标定性质说明（行为学、非 PDK）。
    """
    cfg = cfg or Config()
    fin = _coherent_fin(cfg, n)

    def sndr_at(s0: float) -> float:
        """给定单位失配 sigma0，返回 s=1、DEM 开配置下的 SNDR（dB）。

        Args:
            s0: 待评估的单位电容失配 sigma（相对 F）。[拟合]

        Returns:
            该 sigma 下正弦拟合 SNDR（dB）。
        """
        c = _clone(cfg, mismatch_sigma0=s0, dem_enable=True)
        chip = build_chip(c, mismatch_seed=seed)
        r = run_sim(c, sine_input(0.9 * c.v_fs, fin), n, chip=chip, rng=np.random.default_rng(seed))
        return float(sine_fit_metrics(r.out, c.fs, fin)["SNDR_dB"])

    for _ in range(iters):
        mid = math.sqrt(lo * hi)  # 对数二分
        if sndr_at(mid) > target_sndr_db:
            lo = mid
        else:
            hi = mid
    fitted = math.sqrt(lo * hi)
    return {
        "mismatch_sigma0": fitted,
        "SNDR_at_fit_dB": sndr_at(fitted),
        "target_dB": target_sndr_db,
        "note": "行为学标定，不是 PDK 值。用于让 s=1 的基准对上论文 SNDR=93.5 dB。",
    }


# =====================================================================
# Stage 1 -- 理想两级链路
# =====================================================================
def stage1_ideal(cfg: Config | None = None, n: int = 2**15) -> dict:
    """stage1 -- 理想两级链路：验证理想配置下重构正确、无溢出、误差约等于后端量化步长。

    验证什么：失配/采样噪声/RA 噪声/dither/KTC 全部关闭时，两级残差重构应
        无溢出且误差落在后端量化步长量级，证明数字合并链路本身正确。
    方法：相干采样 n 点正弦（0.9·V_FS），跑 run_sim 得输出，用正弦拟合得
        SNDR，并与后端量化步长 δ2/G/√12 推导的理论 SNDR 比较；统计 ADC2
        溢出率与最大重构误差。
    判据：max|err| < 1.5·δ2/G（重构正确）、溢出率=0（无溢出）、
        |实测SNDR−理论SNDR| < 1 dB（误差≈量化步长）。

    Args:
        cfg: 配置；None 时用默认 Config 并强制关闭所有误差源。[假设]
        n: 采样点数（相干，2 的幂）。[无量纲]

    Returns:
        含 sine_fit_metrics 全部键，外加：
        expect_SNDR_from_q2_dB: 由后端量化步长推导的理论 SNDR（dB）。
        q2_referred_LSB: 后端量化步长折算到输入端的 LSB 数（无量纲）。
        overflow_rate: ADC2 溢出比例（0~1）。
        err_max_LSB: 最大重构误差（LSB）。
        PASS_重构正确 / PASS_无溢出 / PASS_误差≈量化步长: 三项布尔判据。
    """
    cfg = cfg or Config()
    cfg = _clone(
        cfg,
        mismatch_enable=False,
        sadc_mismatch_enable=False,
        enable_sampling_noise=False,
        ra_enable_noise=False,
        dem_enable=False,
        dither_mode="off",
        ktc_enable=False,
    )
    fin = _coherent_fin(cfg, n)
    res = run_sim(cfg, sine_input(0.9 * cfg.v_fs, fin), n)
    m = sine_fit_metrics(res.out, cfg.fs, fin)
    q2_lsb = cfg.delta2 / cfg.g_actual / cfg.lsb_target
    expect = 20 * np.log10((cfg.v_fs / np.sqrt(2)) / (cfg.delta2 / np.sqrt(12) / cfg.g_actual))
    m.update(
        {
            "expect_SNDR_from_q2_dB": expect,
            "q2_referred_LSB": q2_lsb,
            "overflow_rate": float(res.adc2_over.mean()),
            "err_max_LSB": float(np.max(np.abs(res.err)) / cfg.lsb_target),
            "PASS_重构正确": bool(np.max(np.abs(res.err)) < 1.5 * cfg.delta2 / cfg.g_actual),
            "PASS_无溢出": bool(res.adc2_over.mean() == 0),
            "PASS_误差≈量化步长": bool(abs(m["SNDR_dB"] - expect) < 1.0),
        }
    )
    return m


# =====================================================================
# Stage 2 -- 固定物理失配：可复现性
# =====================================================================
def stage2_mismatch_reproducible(cfg: Config | None = None, n: int = 2**14) -> dict:
    """stage2 -- 固定物理失配的可复现性：相同芯片、相同控制状态误差必须一致。

    验证什么：物理失配固定在同一颗虚拟芯片（chip.C_true 只生成一次），不随
        采样点重新生成；只有采样噪声实现随 rng 改变。
    方法：用同一颗 chip(seed=7)、同输入，跑三组：r1/r2 同噪声种子(seed=1)，
        r3 不同噪声种子(seed=999)；比较输出最大差。
    判据：同种子 d12 == 0（可复现）；不同噪声种子 d13 > 0（差异只来自噪声），
        或采样噪声关闭时该判据自动放宽。

    Args:
        cfg: 配置；None 用默认并关闭噪声/dither/DEM/KTC。[假设]
        n: 采样点数。[无量纲]

    Returns:
        same_seed_same_noise_maxdiff_V: 同种子输出最大差（V）。
        diff_noise_seed_maxdiff_V: 不同噪声种子输出最大差（V）。
        PASS_同芯片同控制可复现: 同种子输出逐点相同（bool）。
        PASS_不同噪声实现才有差异: 差异只来自噪声实现（bool）。
        note: 失配固定的说明。
    """
    cfg = cfg or Config()
    cfg = _clone(
        cfg,
        enable_sampling_noise=False,
        ra_enable_noise=False,
        dem_enable=False,
        ktc_enable=False,
        dither_mode="off",
    )
    chip = build_chip(cfg, mismatch_seed=7)
    fin = _coherent_fin(cfg, n)
    inp = sine_input(0.9 * cfg.v_fs, fin)

    r1 = run_sim(cfg, inp, n, chip=chip, rng=np.random.default_rng(1))
    r2 = run_sim(cfg, inp, n, chip=chip, rng=np.random.default_rng(1))
    r3 = run_sim(cfg, inp, n, chip=chip, rng=np.random.default_rng(999))

    d12 = float(np.max(np.abs(r1.out - r2.out)))
    d13 = float(np.max(np.abs(r1.out - r3.out)))
    return {
        "same_seed_same_noise_maxdiff_V": d12,
        "diff_noise_seed_maxdiff_V": d13,
        "PASS_同芯片同控制可复现": bool(d12 == 0.0),
        "PASS_不同噪声实现才有差异": bool(d13 > 0.0 or (not cfg.enable_sampling_noise)),
        "note": "失配在 chip 里固定，不随采样点重新生成；差异只来自采样噪声实现。",
    }


# =====================================================================
# Stage 3 -- DEM：名义守恒 + 失真/噪声的此消彼长
# =====================================================================
def stage3_dem(cfg: Config | None = None, n: int = 2**15) -> dict:
    """stage3 -- DEM：验证名义值严格守恒，并检查失真下降是否伴随噪声上升。

    验证什么：等权单位置换的 DEM 必须保持 DAC_nominal 守恒；开启 DEM 后 THD
        应下降，但置换引入的活动因子涨落会使噪声 RMS 上升（此消彼长）。
    方法：对全部 2^b1 码做 nominal_conservation_check；同配置下分别关/开 DEM
        跑 n 点正弦，得 SNDR/SNR/THD/SFDR 与 noise_rms。
    判据：nominal_conservation["ok"] 为真（守恒）；dem_on THD < dem_off THD
        （失真下降）；观察噪声是否上升（dn>0）。

    Args:
        cfg: 配置；None 用默认 Config。[假设]
        n: 采样点数。[无量纲]

    Returns:
        nominal_conservation: 名义守恒检查结果（含 ok 布尔）。
        dem_off / dem_on: 关/开 DEM 下的指标 dict（SNDR/SNR/THD/SFDR/
            noise_rms_V/overflow_rate）。
        delta_THD_dB: 开减关的 THD 变化（dB）。
        delta_noise_V: 开减关的噪声 RMS 变化（V）。
        PASS_名义值严格守恒: 守恒为真（bool）。
        PASS_失真下降: THD 下降为真（bool）。
        观察_噪声是否上升: 噪声上升为真（bool）。
    """
    cfg = cfg or Config()
    fin = _coherent_fin(cfg, n)
    inp = sine_input(0.9 * cfg.v_fs, fin)

    mapper = Mapper(cfg)
    codes = np.arange(2**cfg.b1)
    bank = np.zeros(codes.size, dtype=int)
    sid = np.arange(codes.size) % 512
    cons = mapper.nominal_conservation_check(codes, bank, sid)

    out = {}
    for dem in (False, True):
        c = _clone(cfg, dem_enable=dem, dither_mode="off", ktc_enable=False)
        r = run_sim(c, inp, n, rng=np.random.default_rng(5))
        m = sine_fit_metrics(r.out, c.fs, fin)
        out[f"dem_{'on' if dem else 'off'}"] = {
            "SNDR_dB": m["SNDR_dB"],
            "SNR_dB": m["SNR_dB"],
            "THD_dB": m["THD_dB"],
            "SFDR_dB": m["SFDR_dB"],
            "noise_rms_V": m["noise_rms"],
            "overflow_rate": float(r.adc2_over.mean()),
        }
    d = out["dem_on"]["THD_dB"] - out["dem_off"]["THD_dB"]
    dn = out["dem_on"]["noise_rms_V"] - out["dem_off"]["noise_rms_V"]
    return {
        "nominal_conservation": cons,
        **out,
        "delta_THD_dB": d,
        "delta_noise_V": dn,
        "PASS_名义值严格守恒": bool(cons["ok"]),
        "PASS_失真下降": bool(d < 0),
        "观察_噪声是否上升": bool(dn > 0),
    }


# =====================================================================
# Stage 4 -- Dither
# =====================================================================
def stage4_dither(cfg: Config | None = None, n: int = 2**15) -> dict:
    """stage4 -- Dither：理想器件下去除后应无明显残留；失配下再看线性化收益。

    验证什么：理想器件（无失配/噪声）注入 analog dither 后，去除 dither 的
        残差应很小（dither 只付出可观测量程的代价）；失配器件下 dither 应
        白化 INL、降低静态误差峰值。
    方法：4a 理想器件开 analog dither，报 err_rms/err_max 与溢出率；4b 失配
        器件下对 "off"/"analog" 两种模式做静态转移曲线，比较 INL 峰值、
        可用输入范围（输入注入 dither 吃掉量程 A_D）。
    判据：ideal_with_dither err_max_LSB < 2（无明显残留）；
        analog 的 inl_max_LSB < off 的 inl_max_LSB（线性化有收益）。

    Args:
        cfg: 配置；None 用默认 Config。[假设]
        n: 采样点数。[无量纲]

    Returns:
        ideal_with_dither: 理想器件+dither 的指标（err_rms_LSB/err_max_LSB/
            overflow_rate）。
        mismatch_static: {"off","analog"} 静态指标（inl_max_LSB/
            sigma_e_mean_V/usable_input_range_Vpp）。
        PASS_理想器件下无明显残留: 理想残留小（bool）。
        PASS_失配下线性化有收益: analog INL 低于 off（bool）。
    """
    cfg = cfg or Config()
    fin = _coherent_fin(cfg, n)
    inp = sine_input(0.9 * cfg.v_fs, fin)
    out = {}

    # 4a 理想器件：去除后应无明显残留
    c = _clone(
        cfg,
        mismatch_enable=False,
        sadc_mismatch_enable=False,
        enable_sampling_noise=False,
        ra_enable_noise=False,
        dem_enable=False,
        ktc_enable=False,
        dither_mode="analog",
    )
    r = run_sim(c, inp, n)
    out["ideal_with_dither"] = {
        "err_rms_LSB": float(np.sqrt(np.mean(r.err**2)) / c.lsb_target),
        "err_max_LSB": float(np.max(np.abs(r.err)) / c.lsb_target),
        "overflow_rate": float(r.adc2_over.mean()),
    }

    # 4b 失配器件：看线性化收益
    # **输入注入 dither 要吃掉量程**：可用输入范围缩到 v_fs - A_D。这是真实代价，必须算进去。
    res = {}
    for mode in ("off", "analog"):
        c = _clone(
            cfg,
            dither_mode=mode,
            dem_enable=False,
            enable_sampling_noise=False,
            ra_enable_noise=False,
            ktc_enable=False,
        )
        a_d = c.dither_amplitude_lsb1 * c.delta1 if mode == "analog" else 0.0
        vmax = c.v_fs - a_d
        lv = np.linspace(-0.995, 0.995, 65) * vmax
        st = static_test(
            lambda lev, rp, c=c: run_sim(c, dc_input(lev), rp, rng=np.random.default_rng(3)).out,
            lv,
            64,
        )
        inl = inl_from_mean_error(st["levels"], st["mu_e"], c.lsb_target)
        res[mode] = {
            "inl_max_LSB": inl["inl_max_lsb"],
            "sigma_e_mean_V": float(st["sigma_e"].mean()),
            "usable_input_range_Vpp": float(2 * vmax),
        }
    out["mismatch_static"] = res
    out["PASS_理想器件下无明显残留"] = bool(out["ideal_with_dither"]["err_max_LSB"] < 2.0)
    out["PASS_失配下线性化有收益"] = bool(res["analog"]["inl_max_LSB"] < res["off"]["inl_max_LSB"])
    return out


# =====================================================================
# Stage 5 -- 采样噪声与电容缩放（两者必须同时变）
# =====================================================================
def stage5_cap_scaling(
    scales=(1.0, 0.5, 0.25, 0.125), cfg: Config | None = None, n: int = 2**15, seed: int = 11
) -> dict:
    """stage5 -- 采样噪声与电容缩放：缩电容必须同时放大 kT/C 与失配两项。

    验证什么：电容缩放 s 同时改变 kT/C 噪声（∝1/√s）与单位失配（∝1/√s），
        二者必须一起看，不能只改其中一项来"改善"指标。
    方法：对同一颗基准芯片按各 scale 重新缩放（rescale_chip），关 DEM/dither/
        KTC，跑 n 点正弦得 SNDR/SNR/SFDR 与噪声 RMS。
    判据：返回的 rows 同时列出 sigma_kTC 与 sigma_mismatch 随 s 的放大，
        供读者判断两项一起变化。

    Args:
        scales: 电容缩放因子序列（相对，无量纲）。
        cfg: 配置；None 用默认 Config。[假设]
        n: 采样点数。[无量纲]
        seed: 芯片与噪声种子。[无量纲]

    Returns:
        rows: 每个 scale 一行 dict，键含 cap_scale（相对）、C_total_pF（pF）、
            sigma_kTC_uV（µV）、sigma_mismatch_ppm（ppm）、SNDR_dB/SNR_dB/
            SFDR_dB（dB）、noise_rms_uV（µV）、overflow_rate（0~1）。
        note: 两项一起放大的说明。
    """
    cfg = cfg or Config()
    base_chip = build_chip(_clone(cfg, cap_scale=1.0), mismatch_seed=seed)
    rows = []
    for s in scales:
        c = _clone(cfg, cap_scale=s, dither_mode="off", ktc_enable=False, dem_enable=False)
        chip = rescale_chip(c, base_chip, s)
        fin = _coherent_fin(c, n)
        r = run_sim(c, sine_input(0.9 * c.v_fs, fin), n, chip=chip, rng=np.random.default_rng(seed))
        m = sine_fit_metrics(r.out, c.fs, fin)
        rows.append(
            {
                "cap_scale": s,
                "C_total_pF": c.c_total0 * s * 1e12,
                "sigma_kTC_uV": c.sigma_sampling(s) * 1e6,
                "sigma_mismatch_ppm": c.sigma_mismatch(s) * 1e6,
                "SNDR_dB": m["SNDR_dB"],
                "SNR_dB": m["SNR_dB"],
                "SFDR_dB": m["SFDR_dB"],
                "noise_rms_uV": m["noise_rms"] * 1e6,
                "overflow_rate": float(r.adc2_over.mean()),
            }
        )
    return {"rows": rows, "note": "缩电容同时放大 kT/C 噪声 (∝1/√s) 与失配 (∝1/√s)，两项一起看。"}


# =====================================================================
# Stage 6 -- KTC
# =====================================================================
def stage6_ktc(
    cfg: Config | None = None, n: int = 2**15, e_n_sweep=(0.0, 20e-6, 50e-6, 100e-6)
) -> dict:
    """stage6 -- KTC：理想增益匹配时消除目标采样噪声；新增观测噪声仍保留。

    验证什么：KTC 校正项在理想 beta=1 时应消除目标采样噪声（kT/C 支路），但
        观测通路自身噪声 e_n 越大，新增观测噪声越多、SNDR 越差。
    方法：对 e_n 扫描 × KTC 开/关，跑 n 点正弦记录 SNDR/噪声 RMS/ktc 饱和率/
        重构误差；另对 beta 失配扫描，看它是线性传递误差还是失真。
    判据：beta=1 时目标采样噪声被消除；e_n 越大 err_rms 越大；beta 失配主要
        压 SNDR/误差而基本不动 SFDR（线性传递函数变化，不是失真）。

    Args:
        cfg: 配置；None 用默认并关闭失配/DEM/dither。[假设]
        n: 采样点数。[无量纲]
        e_n_sweep: 观测通路自身噪声扫描（V，输入等效）。[假设]

    Returns:
        rows: 每个 (e_n, ktc) 组合一行，含 ktc(bool)/e_n_uV/SNDR_dB/
            noise_rms_uV/ktc_path_sat_rate/overflow_rate/err_rms_uV。
        beta_rows: beta 失配扫描行，含 beta_error/beta/SNDR_dB/SFDR_dB/
            THD_dB/err_rms_uV。
        note: 理想 beta 消除目标噪声、beta 失配属线性传递的说明。
    """
    cfg = cfg or Config()
    fin = _coherent_fin(cfg, n)
    inp = sine_input(0.9 * cfg.v_fs, fin)
    rows = []
    for e_n in e_n_sweep:
        for ktc in (False, True):
            c = _clone(
                cfg,
                ktc_enable=ktc,
                ktc_noise_n=e_n,
                dither_mode="off",
                dem_enable=False,
                mismatch_enable=False,
            )
            r = run_sim(c, inp, n, rng=np.random.default_rng(21))
            m = sine_fit_metrics(r.out, c.fs, fin)
            rows.append(
                {
                    "ktc": ktc,
                    "e_n_uV": e_n * 1e6,
                    "SNDR_dB": m["SNDR_dB"],
                    "noise_rms_uV": m["noise_rms"] * 1e6,
                    "ktc_path_sat_rate": float(r.ktc_sat.mean()),
                    "overflow_rate": float(r.adc2_over.mean()),
                    "err_rms_uV": float(np.sqrt(np.mean(r.err**2)) * 1e6),
                }
            )
    # beta 失配扫描：看它是线性传递误差还是失真
    beta_rows = []
    for be in (0.0, 1e-3, 1e-2):
        c = _clone(
            cfg,
            ktc_enable=True,
            ktc_beta_error=be,
            mismatch_enable=False,
            dither_mode="off",
            dem_enable=False,
        )
        r = run_sim(c, inp, n, rng=np.random.default_rng(21))
        m = sine_fit_metrics(r.out, c.fs, fin)
        beta_rows.append(
            {
                "beta_error": be,
                "beta": c.beta_eff(),
                "SNDR_dB": m["SNDR_dB"],
                "SFDR_dB": m["SFDR_dB"],
                "THD_dB": m["THD_dB"],
                "err_rms_uV": float(np.sqrt(np.mean(r.err**2)) * 1e6),
            }
        )
    return {
        "rows": rows,
        "beta_rows": beta_rows,
        "note": "理想 beta=1 时目标采样噪声被消除；e_N 越大新增观测噪声越多。"
        "beta 失配主要压 SNDR/误差而基本不动 SFDR -> 线性传递函数变化，不是失真。",
    }


# =====================================================================
# Stage 7 -- 判别性对比：原电容无 KTC  vs  缩小电容 + KTC
# =====================================================================
def stage7_headline(
    cfg: Config | None = None,
    n: int = 2**15,
    shrink: float = 0.25,
    seed: int = 31,
    e_n: float = 30e-6,
) -> dict:
    """stage7 -- 判别性对比：原电容无 KTC vs 缩小电容±KTC，只改电容与 KTC。

    验证什么：在开启相同 DEM/dither/校准的前提下，比较三种方案（原电容无
        KTC、缩电容无 KTC、缩电容有 KTC）的 SNDR/INL/噪声谱密度，确认缩小
        电容后 KTC 带来的收益是否真实、且不被 ADC2 溢出抵消。
    方法：同一颗基准芯片按 cap_scale 缩放，三种 case 各跑 n 点正弦 + 静态
        转移曲线；先按最坏情况检查 KTC 带宽守卫（越界激励会假阴性）。
    判据：缩电容+KTC 的 SNDR/INL 应优于缩电容无 KTC，且 ktc_path_sat_rate
        不显著；电容与切换电荷只作面积/驱动负担代理，不据此宣布功耗/FoM。

    Args:
        cfg: 配置；None 用默认（沿用其 DEM/dither/calibration 设置）。[假设]
        n: 采样点数。[无量纲]
        shrink: 缩电容因子（相对，默认 0.25）。[假设]
        seed: 芯片与噪声种子。[无量纲]
        e_n: KTC 观测通路噪声（V，输入等效）。[假设]

    Returns:
        cases: 三个方案各自的指标 dict（SNDR_dB/SNR_dB/SFDR_dB/THD_dB/
            noise_rms_uV/NSD_nV_rtHz/INL_max_LSB/sigma_e_mean_uV/overflow_rate/
            ktc_path_sat_rate/switch_charge_proxy/C_total_pF）。
        shrink: 所用缩电容因子。
        common: 三方案共用的 DEM/dither/calibration 设置。
        note: 电容/切换电荷仅作代理指标的说明。
    """
    cfg = cfg or Config()
    fin = _coherent_fin(cfg, n)
    # KTC 观测通路守卫：激励频率不得超过**观测通路**的摆幅上限。
    #
    # 口径修正（外部复核 2026-09-11 第三轮，见 ADR 0006）：旧写法用
    # min(adc2_v_max − G0·Δ1, −adc2_v_min) 反解上限，前提是"校正量 κ·v_N
    # 占用 ADC2 量程"。该前提不成立 —— 校正已在数字域扣除
    # （ADC2.quantize_with_correction），over 只反映 vra 本身。真正的边界是
    # 观测节点自身的摆幅 ra_v_clip。
    #
    # 默认参数下新上限 ≈ 134 MHz，远高于 fs/2 = 20 MHz，因此**当前不会
    # 触发**。保留它是为了参数退化（ra_v_clip 调小、G_N 调大、Δt 放宽）时
    # 仍然拦得住；不要把它当作"检查过了"的证据。
    if True:  # case 里会开 KTC，按最坏情况检查
        f_max = (cfg.ra_v_clip / cfg.ktc_gain_n) / (2 * np.pi * cfg.v_fs * cfg.ktc_dt())
        if fin > f_max:
            raise ValueError(
                f"stage7: 激励 {fin/1e6:.2f} MHz 超过 KTC 观测通路摆幅上限 "
                f"{f_max/1e6:.2f} MHz；在该越界条件下比较方案没有意义"
            )
    amp = 0.9 * cfg.v_fs
    inp = sine_input(amp, fin)
    lv = np.linspace(-0.98, 0.98, 65) * cfg.v_fs

    base = build_chip(_clone(cfg, cap_scale=1.0), mismatch_seed=seed)

    cases = {
        "原电容 · 无 KTC": _clone(cfg, cap_scale=1.0, ktc_enable=False),
        f"缩电容 s={shrink} · 无 KTC": _clone(cfg, cap_scale=shrink, ktc_enable=False),
        f"缩电容 s={shrink} · 有 KTC": _clone(
            cfg, cap_scale=shrink, ktc_enable=True, ktc_noise_n=e_n
        ),
    }
    common = {
        "dem_enable": cfg.dem_enable,
        "dither_mode": cfg.dither_mode,
        "calibration": cfg.calibration,
    }

    out = {}
    for name, c in cases.items():
        c = replace(c, **common)
        chip = rescale_chip(c, base, c.cap_scale)
        r = run_sim(c, inp, n, chip=chip, rng=np.random.default_rng(seed))
        m = sine_fit_metrics(r.out, c.fs, fin)
        st = static_test(
            lambda lev, rp, c=c, chip=chip: run_sim(
                c, dc_input(lev), rp, chip=chip, rng=np.random.default_rng(seed)
            ).out,
            lv,
            96,
        )
        inl = inl_from_mean_error(st["levels"], st["mu_e"], c.lsb_target)
        out[name] = {
            "SNDR_dB": m["SNDR_dB"],
            "SNR_dB": m["SNR_dB"],
            "SFDR_dB": m["SFDR_dB"],
            "THD_dB": m["THD_dB"],
            "noise_rms_uV": m["noise_rms"] * 1e6,
            "NSD_nV_rtHz": m["NSD_V_rtHz"] * 1e9,
            "INL_max_LSB": inl["inl_max_lsb"],
            "sigma_e_mean_uV": float(st["sigma_e"].mean() * 1e6),
            "overflow_rate": float(r.adc2_over.mean()),
            "ktc_path_sat_rate": float(r.ktc_sat.mean()),
            "switch_charge_proxy": c.c_total0 * c.cap_scale * 2 * c.v_fs,
            "C_total_pF": c.c_total0 * c.cap_scale * 1e12,
        }
    return {
        "cases": out,
        "shrink": shrink,
        "common": common,
        "note": "电容与切换电荷只作为面积/驱动负担的代理指标；" "不要据此宣布功耗或 FoM 已经提高。",
    }


# =====================================================================
# 缩电容 × KTC 的完整扫描（stage 5 + 6 的联合版）
# =====================================================================
def scaling_sweep(
    cfg: Config | None = None,
    scales=(1.0, 0.5, 0.25, 0.125, 0.0625),
    n: int = 2**15,
    seed: int = 31,
    e_n: float = 30e-6,
    n_levels: int = 65,
    repeats: int = 96,
) -> dict:
    """缩电容 × KTC 的完整扫描（stage5+6 联合版）：同一颗芯片下同时变 kT/C 与失配。

    验证什么：在固定虚拟芯片（同一组 z）下，把 kT/C 噪声与单位失配同时按
        cap_scale 改变，并分别开/关 KTC，观察 SNDR/INL 随二者的联合演化。
    方法：对每个 scale × {KTC 关,开} 跑 n 点正弦 + 静态转移曲线（考虑 dither
        吃掉的量程），记录 NSD 与 INL 峰值。
    判据：返回的 rows 同时含 sigma_kTC 与 sigma_mismatch，读者可判断两项
        一起变化时 KTC 开关带来的差异。

    Args:
        cfg: 配置；None 用默认 Config。[假设]
        scales: 电容缩放因子序列（相对）。[假设]
        n: 采样点数。[无量纲]
        seed: 芯片与噪声种子。[无量纲]
        e_n: KTC 观测通路噪声（V）。[假设]
        n_levels: 静态测试电平数。[无量纲]
        repeats: 每电平重复次数（覆盖 DEM 状态）。[无量纲]

    Returns:
        rows: 每个 (scale,ktc) 一行，含 cap_scale/ktc/C_total_pF/sigma_kTC_uV/
            sigma_mismatch_ppm/SNDR_dB/SNR_dB/SFDR_dB/THD_dB/noise_rms_uV/
            NSD_nV_rtHz/INL_max_LSB/sigma_e_mean_uV/overflow_rate/ktc_sat_rate/
            switch_charge_pC。
        scales: 所用缩放序列。
        e_n_uV: 观测噪声（µV）。
    """
    cfg = cfg or Config()
    base = build_chip(_clone(cfg, cap_scale=1.0), mismatch_seed=seed)
    fin = _coherent_fin(cfg, n)
    rows = []
    for s in scales:
        for ktc in (False, True):
            c = _clone(cfg, cap_scale=s, ktc_enable=ktc, ktc_noise_n=e_n)
            chip = rescale_chip(c, base, s)
            r = run_sim(
                c, sine_input(0.9 * c.v_fs, fin), n, chip=chip, rng=np.random.default_rng(seed)
            )
            m = sine_fit_metrics(r.out, c.fs, fin)

            a_d = c.dither_amplitude_lsb1 * c.delta1 if c.dither_mode != "off" else 0.0
            lv = np.linspace(-0.995, 0.995, n_levels) * (c.v_fs - a_d)
            st = static_test(
                lambda lev, rp, c=c, chip=chip: run_sim(
                    c, dc_input(lev), rp, chip=chip, rng=np.random.default_rng(seed)
                ).out,
                lv,
                repeats,
            )
            inl = inl_from_mean_error(st["levels"], st["mu_e"], c.lsb_target)
            rows.append(
                {
                    "cap_scale": s,
                    "ktc": ktc,
                    "C_total_pF": c.c_total0 * s * 1e12,
                    "sigma_kTC_uV": c.sigma_sampling(s) * 1e6,
                    "sigma_mismatch_ppm": c.sigma_mismatch(s) * 1e6,
                    "SNDR_dB": m["SNDR_dB"],
                    "SNR_dB": m["SNR_dB"],
                    "SFDR_dB": m["SFDR_dB"],
                    "THD_dB": m["THD_dB"],
                    "noise_rms_uV": m["noise_rms"] * 1e6,
                    "NSD_nV_rtHz": m["NSD_V_rtHz"] * 1e9,
                    "INL_max_LSB": inl["inl_max_lsb"],
                    "sigma_e_mean_uV": float(st["sigma_e"].mean() * 1e6),
                    "overflow_rate": float(r.adc2_over.mean()),
                    "ktc_sat_rate": float(r.ktc_sat.mean()),
                    "switch_charge_pC": c.c_total0 * s * 2 * c.v_fs * 1e12,
                }
            )
    return {"rows": rows, "scales": list(scales), "e_n_uV": e_n * 1e6}


# =====================================================================
# Monte Carlo
# =====================================================================
def monte_carlo(cfg: Config, n_chips: int = 24, n: int = 2**13, seed0: int = 1000) -> dict:
    """Monte-Carlo over freshly drawn chips, returning **the per-chip samples**.

    The audit's F10 finding was that a published "N-chip distribution" figure
    had been drawn by resampling 400 numbers from the reported ``(mean, sigma)``
    rather than from the simulated chips themselves — a figure that can then
    never support a tail or yield statement. The fix is structural: the raw
    per-chip array is part of the return value, so any histogram downstream is
    necessarily drawn from real chips.

    Args:
        cfg: Base configuration; mismatch is re-drawn per chip.
        n_chips: Number of virtual chips.
        n: Samples per chip.
        seed0: First seed; chip ``i`` uses ``seed0 + i`` throughout (chip draw
            and noise realisation share the seed so a chip reproduces exactly).

    Returns:
        Dict with the usual summary statistics plus ``sndr_per_chip`` and
        ``sfdr_per_chip`` (``np.ndarray`` of length ``n_chips``).
    """
    fin = _coherent_fin(cfg, n)
    sndr, sfdr = [], []
    for i in range(n_chips):
        chip = build_chip(cfg, mismatch_seed=seed0 + i)
        r = run_sim(
            cfg, sine_input(0.9 * cfg.v_fs, fin), n, chip=chip, rng=np.random.default_rng(seed0 + i)
        )
        m = sine_fit_metrics(r.out, cfg.fs, fin)
        sndr.append(m["SNDR_dB"])
        sfdr.append(m["SFDR_dB"])
    s = np.asarray(sndr, dtype=float)
    f = np.asarray(sfdr, dtype=float)
    return {
        "SNDR_mean": float(s.mean()),
        "SNDR_std": float(s.std()),
        "SNDR_min": float(s.min()),
        "SNDR_max": float(s.max()),
        "n_chips": n_chips,
        "sndr_per_chip": s,
        "sfdr_per_chip": f,
    }


# =====================================================================
# Stage 8 -- 失配专项压力测试（v2）
#
# v1 的失配模型把所有单位电容当成独立同分布，那是物理上的**乐观下界**：
# 真实版图里失配是空间相关的，DEM 能吃掉多少取决于相关长度。
# 本 stage 回答三个问题：
#   1. 失配的空间分解如何改变 DEM 的收益？
#   2. 在多大的 sigma 下系统会崩（PDK 估算值 vs 行为学标定值）？
#   3. DEM 到底是在帮忙还是在帮倒忙？
# =====================================================================
def stage8_mismatch_stress(
    n: int = 2**13,
    sigmas_ppm=(50, 100, 200, 400, 800, 1117, 2000),
    splits=((0, 0, 1), (0.15, 0.35, 0.50), (0.3, 0.4, 0.3), (0.5, 0.3, 0.2), (1, 0, 0)),
    draw=None,
) -> dict:
    """stage8 -- 失配专项压力测试：空间相关失配下 DEM 的收益与系统崩塌边界。

    验证什么：v1 把单位电容当独立同分布是乐观下界；真实版图失配空间相关，
        本 stage 回答：(1) 失配空间分解如何改变 DEM 收益；(2) 多大 sigma 下
        系统崩塌（PDK 估算 vs 行为学标定）；(3) DEM 帮忙还是帮倒忙。
    方法：① 量级扫描（默认分解）关/开 DEM；② 空间分解扫描（fixed/混合/单位
        主导）关/开 DEM；③ 计算 DEM 净收益 dSNDR = SNDR(on)−SNDR(off)。
    判据：数据驱动——dSNDR>0 为净收益、<0 为净损失；方向由数据决定（见
        "判据"键）。e_dac 中"与输入线性相关"部分会被增益校准吸收。

    Args:
        n: 采样点数。[无量纲]
        sigmas_ppm: 单位失配 sigma 扫描序列（ppm）。[假设]
        splits: 失配空间分解（global,slice,unit）权重元组序列。[假设]
        draw: 预生成的芯片失配抽样；None 用默认 Config 抽样。[假设]

    Returns:
        rows: 量级扫描行（sigma_ppm/dem/SNDR_dB/SFDR_dB/THD_dB/noise_rms_uV/
            err_rms_uV/e_dac_rms_uV/e_dac_linear_uV/e_dac_absorbable）。
        split_rows: 空间分解扫描行（同上键）。
        dem_delta: 每个 sigma 的 DEM 净收益（dSNDR_dB/dTHD_dB/dNoise_uV）。
        n: 采样点数。
        判据: 数据驱动判据说明（不写死结论）。
    """
    from .chip import chip_draw

    if draw is None:
        draw = chip_draw(Config())
    fin = _coherent_fin(Config(), n)

    def one(sigma_ppm, dem, split=(0.15, 0.35, 0.50)):
        """在给定失配 sigma、DEM 开关、空间分解下跑一次，返回指标与 e_dac 分解。

        Args:
            sigma_ppm: 单位失配 sigma（ppm）。[假设]
            dem: 是否开启 DEM。[假设]
            split: 失配空间分解 (global,slice,unit) 权重。[假设]

        Returns:
            含 SNDR/SFDR/THD/noise_rms/err_rms/e_dac_rms 及与输入线性相关部分
            e_dac_linear（会被增益校准吸收）占比 e_dac_absorbable 的 dict。
        """
        c = _clone(Config(), mismatch_sigma0=sigma_ppm * 1e-6, dem_enable=dem, mismatch_split=split)
        chip = build_chip(c, draw=draw)
        r = run_sim(c, sine_input(0.9 * c.v_fs, fin), n, chip=chip, rng=np.random.default_rng(5))
        m = sine_fit_metrics(r.out, c.fs, fin)
        # eDAC 里"与输入线性相关"的部分 = 会被正弦拟合 / 增益校准吸收的部分
        x = np.asarray(r.x_ref)
        e = np.asarray(r.e_dac)
        A = np.stack([np.ones(x.size), x], axis=1)
        coef, *_ = np.linalg.lstsq(A, e, rcond=None)
        lin = np.sqrt(np.mean((A @ coef) ** 2))
        tot = np.sqrt(np.mean(e**2))
        return {
            "sigma_ppm": sigma_ppm,
            "dem": dem,
            "split": list(split),
            "SNDR_dB": m["SNDR_dB"],
            "SFDR_dB": m["SFDR_dB"],
            "THD_dB": m["THD_dB"],
            "noise_rms_uV": m["noise_rms"] * 1e6,
            "err_rms_uV": float(np.sqrt(np.mean(r.err**2)) * 1e6),
            "e_dac_rms_uV": float(tot * 1e6),
            "e_dac_linear_uV": float(lin * 1e6),
            "e_dac_absorbable": float(lin / tot) if tot > 0 else 0.0,
        }

    # 1) 量级扫描（固定默认分解）
    rows = []
    for sp in sigmas_ppm:
        for dem in (False, True):
            rows.append(one(sp, dem))

    # 2) 空间分解扫描（固定 100 ppm 标定值）
    split_rows = []
    for split in splits:
        for dem in (False, True):
            split_rows.append(one(100, dem, split))

    # 3) DEM 净收益（负 = DEM 在帮倒忙）
    delta = []
    for sp in sigmas_ppm:
        a = next(r for r in rows if r["sigma_ppm"] == sp and not r["dem"])
        b = next(r for r in rows if r["sigma_ppm"] == sp and r["dem"])
        delta.append(
            {
                "sigma_ppm": sp,
                "dSNDR_dB": b["SNDR_dB"] - a["SNDR_dB"],
                "dTHD_dB": b["THD_dB"] - a["THD_dB"],
                "dNoise_uV": b["noise_rms_uV"] - a["noise_rms_uV"],
            }
        )

    return {
        "rows": rows,
        "split_rows": split_rows,
        "dem_delta": delta,
        "n": n,
        # v3 审计修正：函数只返回数据与判据，不预先写死研究结论。
        # 判据由数据计算：DEM 净收益 = DEM 开的 SNDR - DEM 关的 SNDR。
        "判据": "dSNDR_dB = SNDR(DEM on) - SNDR(DEM off)；"
        "正 = DEM 净收益为正，负 = 净损失。具体方向由数据决定",
    }


# =====================================================================
# Monte Carlo 良率（多颗虚拟芯片，看分布尾部而不只是均值）
# =====================================================================
def monte_carlo_yield(
    sigma_ppm: float,
    dem: bool = True,
    n_chips: int = 60,
    n: int = 2**13,
    seed0: int = 900,
    split=(0.15, 0.35, 0.50),
) -> dict:
    """Monte-Carlo at a given unit-mismatch sigma, returning per-chip samples.

    See :func:`monte_carlo` for why the raw arrays are returned rather than
    only their moments (audit F10: a histogram must be drawn from real chips).

    Args:
        sigma_ppm: Unit-capacitor mismatch sigma in ppm (relative).
        dem: Whether DEM is enabled.
        n_chips: Number of virtual chips.
        n: Samples per chip.
        seed0: First seed; chip ``i`` uses ``seed0 + i``.
        split: ``(global, slice, unit)`` variance fractions of the mismatch.

    Returns:
        Dict with summary statistics (mean/std/min/p5/p95) **and** the
        per-chip arrays ``sndr_per_chip``, ``sfdr_per_chip``,
        ``err_rms_per_chip_uV``.
    """
    fin = _coherent_fin(Config(), n)
    sndr, sfdr, errm = [], [], []
    for i in range(n_chips):
        c = _clone(
            Config(),
            mismatch_sigma0=sigma_ppm * 1e-6,
            dem_enable=dem,
            mismatch_split=split,
            seed=seed0 + i,
        )
        chip = build_chip(c)
        r = run_sim(
            c, sine_input(0.9 * c.v_fs, fin), n, chip=chip, rng=np.random.default_rng(seed0 + i)
        )
        m = sine_fit_metrics(r.out, c.fs, fin)
        sndr.append(m["SNDR_dB"])
        sfdr.append(m["SFDR_dB"])
        errm.append(float(np.sqrt(np.mean(r.err**2)) * 1e6))
    s = np.array(sndr)
    f = np.array(sfdr)
    e = np.array(errm)
    return {
        "sigma_ppm": sigma_ppm,
        "dem": dem,
        "n_chips": n_chips,
        "SNDR_mean": float(s.mean()),
        "SNDR_std": float(s.std()),
        "SNDR_min": float(s.min()),
        "SNDR_p5": float(np.percentile(s, 5)),
        "SNDR_p95": float(np.percentile(s, 95)),
        "SFDR_min": float(f.min()),
        "err_rms_p95_uV": float(np.percentile(e, 95)),
        # --- provenance: the samples any figure must be drawn from ---
        "sndr_per_chip": s,
        "sfdr_per_chip": f,
        "err_rms_per_chip_uV": e,
    }


def mc_pdk_sigma(cfg: Config, n_chips: int = 60, n: int = 2**13, dem: bool = True) -> dict:
    """Monte-Carlo at the assumed-PDK unit-mismatch sigma, per-chip samples kept.

    This is the study the audit's F10 finding referred to: the historical
    "60-chip SNDR distribution" was plotted from 400 Gaussian draws
    parameterised by the 60 chips' ``(mean, sigma)``. Returning
    ``sndr_per_chip`` here makes that substitution impossible to perform
    silently — a figure drawn any other way is visibly not using this data.

    Note the provenance: ``cfg.pdk_sigma_est_ppm`` is graded ``ASSUMED``
    (a Pelgrom-style area-law estimate, *not* a PDK measurement), so this study
    characterises sensitivity to an assumption; it is not a yield prediction for
    the target chip.

    Args:
        cfg: Configuration; ``pdk_sigma_est_ppm`` supplies the sigma.
        n_chips: Number of virtual chips.
        n: Samples per chip.
        dem: Whether DEM is enabled.

    Returns:
        Same dict as :func:`monte_carlo_yield`, with ``sigma_ppm`` set from the
        configuration's assumed PDK estimate.
    """
    return monte_carlo_yield(sigma_ppm=float(cfg.pdk_sigma_est_ppm), dem=dem, n_chips=n_chips, n=n)


def mismatch_budget_limit(
    target_sndr_db: float = 93.0,
    dem: bool = True,
    sigmas_ppm=(100, 150, 200, 250, 300, 400),
    n_chips: int = 40,
    n: int = 2**13,
) -> dict:
    """反推：要达到 target SNDR，单位电容失配 sigma 最多允许多大？

    验证什么：由 Monte-Carlo 良率曲线反推"均值口径"下允许的最大单位失配
        sigma（行为学预算，非良率规格）。
    方法：对每个候选 sigma 跑 monte_carlo_yield，取 SNDR_mean ≥ target 的最
        大 sigma 作为上限。
    判据：sigma_limit_ppm_mean = 满足均值口径的最大 sigma；若按最差芯片口径
        需再收紧（见 note）。

    Args:
        target_sndr_db: 目标 SNDR（dB，默认 93）。[披露]
        dem: 是否开启 DEM。[假设]
        sigmas_ppm: 候选单位失配 sigma 序列（ppm）。[假设]
        n_chips: 每点 Monte-Carlo 芯片数。[无量纲]
        n: 每芯片采样点数。[无量纲]

    Returns:
        rows: 每个 sigma 一行（sigma_ppm/SNDR_mean/SNDR_min/SFDR_min）。
        target_sndr_db: 目标 SNDR（dB）。
        sigma_limit_ppm_mean: 均值口径下允许的最大 sigma（ppm，或 None）。
        note: 均值 vs 最差芯片口径的说明。
    """
    rows = []
    for sp in sigmas_ppm:
        r = monte_carlo_yield(sp, dem=dem, n_chips=n_chips, n=n)
        rows.append(
            {
                "sigma_ppm": sp,
                "SNDR_mean": r["SNDR_mean"],
                "SNDR_min": r["SNDR_min"],
                "SFDR_min": r["SFDR_min"],
            }
        )
    ok = [r for r in rows if r["SNDR_mean"] >= target_sndr_db]
    limit = max(r["sigma_ppm"] for r in ok) if ok else None
    return {
        "rows": rows,
        "target_sndr_db": target_sndr_db,
        "sigma_limit_ppm_mean": limit,
        "note": "均值口径；若按最差芯片口径需再收紧",
    }


# =====================================================================
# Stage 9 -- 专利 [09]-[14] 机制验证
#
# 读专利的正确姿势是沿着三个问题：电荷从哪里来、数字码改变了哪些开关、
# 误差最后进入了哪条通路。本 stage 把其中三条可计算的性质固化成测试。
# =====================================================================
def stage9_patent_mechanisms(n: int = 2**13) -> dict:
    """stage9 -- 专利 [09]–[14] 机制验证：沿"电荷从哪来/数字码改了哪些开关/误差进哪条通路"三条线。

    验证什么：把专利中三条可计算性质固化成测试——
        9A [10] dither 两种物理实现（输入注入 vs 采样态衰减）的代价对比；
        9B [09] 粗码变化被残差自动修正（SADC 通路误差只改粗码，残差未溢出即补回）；
        9C [09]/[10] 小数权重（slice 异码合成）名义守恒但物理误差访问改变。
    方法：分别扫描 dither 模式（off/analog/sampling）、等幅度对照、采样响应
        失配 dtau、以及分数 k 下的 RDAC 名义/物理误差。
    判据：9A 满幅溢出率是两者分水岭；9B 粗码改变但 SNDR/误差基本不变（残差
        未溢出）；9C 名义值守恒、物理 e_dac 随状态改变。

    Args:
        n: 采样点数。[无量纲]

    Returns:
        dither_impl: 三种 dither 模式指标（alpha/SNDR_dB/THD_dB/noise_uV/
            ovf_fullscale）。
        dither_equal_cost: 等幅度 analog vs sampling 对照（dither_mV/units/
            input_SNDR/sampling_SNDR/alpha/range_loss_dB/alpha_loss_dB）。
        coarse_correction: 不同 dtau 下粗码变化率/残差漂移/SNDR/误差/溢出。
        fractional_weight: 分数 k 下的名义/物理 DAC 误差 e_dac_V。
    """
    fin = _coherent_fin(Config(), n)
    out = {}

    # ---- 9A [10] dither 两种物理实现的代价对比 ----
    # 输入注入：从 ±V_FS 扣量程（只在接近满幅时付出代价，表现为削顶）
    # 采样态  ：恒定衰减 α=(N-2D)/N（始终付出 1/α 的噪声放大，但不损失量程）
    rows = []
    for mode in ("off", "analog", "sampling"):
        c = _clone(Config(), dither_mode=mode)
        r = run_sim(c, sine_input(0.9 * c.v_fs, fin), n, rng=np.random.default_rng(5))
        m = sine_fit_metrics(r.out, c.fs, fin)
        # 满幅下的溢出率：这是两者的分水岭
        rf = run_sim(c, sine_input(0.995 * c.v_fs, fin), n, rng=np.random.default_rng(5))
        rows.append(
            {
                "mode": mode,
                "alpha": c.dither_alpha,
                "SNDR_dB": m["SNDR_dB"],
                "THD_dB": m["THD_dB"],
                "noise_uV": m["noise_rms"] * 1e6,
                "ovf_fullscale": float(rf.adc2_over.mean()),
            }
        )
    out["dither_impl"] = rows

    # 等幅度对照：把两种 dither 调到同一个电压幅度
    eq = []
    c0 = Config()
    for lsb1 in (1.0, 2.0):
        D = lsb1 * c0.delta1 / c0.rdac_step
        a = _clone(c0, dither_mode="analog", dither_amplitude_lsb1=lsb1)
        s = _clone(c0, dither_mode="sampling", dither_units_range=D)
        ra = run_sim(a, sine_input(0.9 * c0.v_fs, fin), n, rng=np.random.default_rng(5))
        rs = run_sim(s, sine_input(0.9 * c0.v_fs, fin), n, rng=np.random.default_rng(5))
        amp = lsb1 * c0.delta1
        eq.append(
            {
                "dither_mV": amp * 1e3,
                "units": D,
                "input_SNDR": sine_fit_metrics(ra.out, a.fs, fin)["SNDR_dB"],
                "sampling_SNDR": sine_fit_metrics(rs.out, s.fs, fin)["SNDR_dB"],
                "alpha": s.dither_alpha,
                "range_loss_dB": 20 * np.log10(c0.v_fs / (c0.v_fs - amp)),
                "alpha_loss_dB": -20 * np.log10(s.dither_alpha),
            }
        )
    out["dither_equal_cost"] = eq

    # ---- 9B [09] 1.3 粗码变化被残差自动修正 ----
    # SADC 通路的误差（含采样响应失配 Δτ）只改变粗码；只要残差没溢出，
    # 数字合并会把粗码变化完整补回来 —— 这是两级残差结构的根本性质。
    rows = []
    c_base = _clone(Config(), mismatch_enable=False, dem_enable=False)
    r0 = run_sim(c_base, sine_input(0.9 * c_base.v_fs, fin), n, rng=np.random.default_rng(3))
    for dtau in (0.0, 20e-12, 100e-12, 200e-12):
        c = _clone(c_base, sampling_tau_mismatch=dtau)
        r = run_sim(c, sine_input(0.9 * c.v_fs, fin), n, rng=np.random.default_rng(3))
        m = sine_fit_metrics(r.out, c.fs, fin)
        rows.append(
            {
                "dtau_ps": dtau * 1e12,
                "coarse_change_pct": float((r.coarse != r0.coarse).mean() * 100),
                "residue_shift_mV": float(np.max(np.abs(r.residue - r0.residue)) * 1e3),
                "SNDR_dB": m["SNDR_dB"],
                "err_rms_uV": float(np.sqrt(np.mean(r.err**2)) * 1e6),
                "ovf_pct": float(r.adc2_over.mean() * 100),
            }
        )
    out["coarse_correction"] = rows

    # ---- 9C [09] Fig.12 / [10] Fig.19 小数权重：slice 异码合成 ----
    # 7 个 slice 中 2 个取状态 2、5 个取状态 3 -> 合并权重 2+5/7。
    # 这里用实数 k（rdac 线性插值）验证：名义值守恒，但物理误差访问改变。
    lut_probe = []
    for frac in (0.0, 2 / 7, 0.5, 5 / 7, 1.0):
        c = _clone(Config(), dem_enable=False)
        chip = build_chip(c)
        from .rdac import RDAC
        from .scheduler import Scheduler

        sc = Scheduler(c)
        rd = RDAC(c, chip, np.stack([sc.bank_a, sc.bank_b]))
        k = 100.0 + frac
        bank = np.zeros(1, dtype=np.int64)
        sid = np.zeros(1, dtype=np.int64)
        kk = np.array([k])
        sel = rd.lut.selected_cap(bank, sid, kk)[0]
        nom = rd.evaluate_nominal(
            type("C", (), {"k": kk, "bank": bank, "sid": sid, "dither_code": np.zeros(1)})()
        )[0]
        phy = c.v_fs * (2.0 * sel / rd.lut.total[bank][0] - 1.0)
        lut_probe.append({"frac": frac, "k": k, "e_dac_V": float(phy - nom)})
    out["fractional_weight"] = lut_probe

    return out


# =====================================================================
# Stage 10 -- v3 审计验收套件
#
# 第三版审计提出的五条验收，逐条给出可复现的数值判据：
#   1. 资源与噪声一致：加备用 slice 不改变采样噪声
#   2. 物理参数确实生效：C_F 变化 -> 增益按电荷模型变化
#   3. 校准联调：dither 开/关下增益、beta 校准均收敛
#   4. 指标已知答案：远/近端 spur、纯正弦无假谐波
#   5. KTC 动态预算：观测带宽 -> 建立残差 -> beta -> 时刻精度的链条
# =====================================================================
def stage10_audit_acceptance(n: int = 2**13) -> dict:
    """stage10 -- v3 审计验收套件：五条可复现数值判据（资源/增益/校准/指标/KTC 预算）。

    验证什么：逐条回应第三版审计的五项验收——
        10.1 资源与噪声一致（加备用 slice 不改采样噪声）；
        10.2 物理参数确生效（C_F 变化→增益按电荷模型变化）；
        10.3 校准联调（dither 开/关下增益、beta 校准收敛）；
        10.4 指标已知答案（远/近端 spur、纯正弦无假谐波）；
        10.5 KTC 动态预算（观测带宽→建立残差→beta→时刻精度链条）。
    方法：每小节独立构造对照并给出显式 PASS 阈值。
    判据：见各子 dict 的 PASS 键与"判据"说明（阈值均显式写在代码里）。

    Args:
        n: 采样点数（部分小节用更短的对齐长度）。[无量纲]

    Returns:
        noise_vs_pool: 不同备用池下采样噪声一致性（含 PASS）。
        feedback_cap_to_gain: C_F×1.2→G/1.2（含 PASS）。
        calibration_e2e: 增益/beta 校准收敛（含 PASS）。
        metrics_known_answer: spur/谐波已知答案测试（含 PASS）。
        ktc_dynamic_budget: KTC 带宽预算（含判据说明：SNDR 不动因 LTI 吸收，
            err_to_x2 才是时刻一致性度量）。
    """
    fin = _coherent_fin(Config(), n)
    out = {}

    # ---- 10.1 资源与噪声一致 ----
    rows_pool = []
    for n_slices in (16, 18, 32):
        c = _clone(Config(), n_slices=n_slices)
        chip = build_chip(c)
        from .sampler import capture

        sb = capture(
            c, sine_input(1.0, 1e6), 4096, np.random.default_rng(1), chip=chip, c_active=None
        )
        rows_pool.append(
            {"n_slices": n_slices, "n_active": c.n_active, "sigma_nR_uV": float(sb.n_R.std() * 1e6)}
        )
    sigma_ref = rows_pool[0]["sigma_nR_uV"]
    out["noise_vs_pool"] = {
        "rows": rows_pool,
        "PASS": all(abs(r["sigma_nR_uV"] - sigma_ref) < 0.3 for r in rows_pool),
        "判据": "活跃 slice 固定时，改变备用池大小，采样噪声变化 < 0.3 uV",
    }

    # ---- 10.2 反馈电容 -> 增益（电荷一致模型）----
    c = _clone(Config(), mismatch_enable=False)
    r1 = run_sim(c, sine_input(0.9 * c.v_fs, fin), 2048, rng=np.random.default_rng(3))
    r2 = run_sim(
        _clone(c, c_feedback0=c.c_feedback0 * 1.2),
        sine_input(0.9 * c.v_fs, fin),
        2048,
        rng=np.random.default_rng(3),
    )
    ratio = r1.g_vec.mean() / r2.g_vec.mean()
    out["feedback_cap_to_gain"] = {
        "G_ratio_measured": float(ratio),
        "G_ratio_expected": 1.2,
        # bool(): g_vec 是 numpy 数组，mean() 返回 np.float64，直接比较得到
        # np.bool_。序列化进 results.json 时它会变成真 bool，但 in-memory 交给
        # 门禁（acceptance_records）时会因非 bool 被拒绝——在出生处就收成 bool。
        "PASS": bool(abs(ratio - 1.2) < 1e-6),
        "判据": "C_F x1.2 -> G 恰好 /1.2（电荷一致模型）",
    }

    # ---- 10.3 校准联调 ----
    rows_cal: list[dict[str, object]] = []
    cf_err = 1.0 / 1.05  # charge 模式下注入 +5% 增益误差的正确方式
    for dm in ("off", "analog", "sampling"):
        cc = _clone(
            Config(),
            dither_mode=dm,
            c_feedback0=Config().c_feedback0 * cf_err,
            mismatch_enable=False,
            dem_enable=False,
            calibration="gain",
            enable_sampling_noise=False,
            ra_enable_noise=False,
        )
        r = run_with_calibration(
            cc, sine_input(0.9 * cc.v_fs, fin), n, rng=np.random.default_rng(7)
        )
        rows_cal.append(
            {
                "dither": dm,
                "G_true": float(r.g_vec.mean()),
                "G_hat": float(r.state.estimated_gain),
                "err_ppm": float(abs(r.state.estimated_gain / r.g_vec.mean() - 1) * 1e6),
            }
        )
    # beta 校准
    cc = _clone(
        Config(),
        ktc_enable=True,
        ktc_beta_error=0.2,
        mismatch_enable=False,
        dem_enable=False,
        calibration="gain_beta",
        enable_sampling_noise=False,
        ra_enable_noise=False,
    )
    rb = run_with_calibration(cc, sine_input(0.9 * cc.v_fs, fin), n, rng=np.random.default_rng(21))
    kappa_target = (Config().g0 / Config().ktc_gain_n) / 1.2  # 名义 kappa=4，beta=1.2 -> 目标 4/1.2
    out["calibration_e2e"] = {
        "gain_rows": rows_cal,
        "gain_PASS": all(r["err_ppm"] < 50 for r in rows_cal),
        "beta_kappa_after": float(rb.state.kappa),
        "beta_kappa_target": float(kappa_target),
        "beta_err_pct": float(abs(rb.state.kappa / kappa_target - 1) * 100),
        "beta_PASS": abs(rb.state.kappa / kappa_target - 1) < 0.01,
        "判据": "增益误差 <50 ppm（三种 dither 均可）；beta 校准把 kappa 修到目标 1% 内",
    }

    # ---- 10.4 指标已知答案 ----
    fs = Config().fs
    t = np.arange(n) / fs
    fin1 = Config().fs * 1021 / n
    spur_tests = []
    for dist_bins in (1000, 10):
        x = 1.0 * np.sin(2 * np.pi * fin1 * t) + 10 ** (-80 / 20) * np.sin(
            2 * np.pi * (fin1 + dist_bins * fs / n) * t
        )
        m = sine_fit_metrics(x, fs, fin1)
        spur_tests.append(
            {
                "spur": f"-80dBc @ +{dist_bins}bin",
                "SFDR_measured": float(m["SFDR_dB"]),
                "SFDR_expected": 80.0,
                "PASS": abs(m["SFDR_dB"] - 80.0) < 0.5,
            }
        )
    fin5 = fs * max(1, round(5e6 * n / fs)) / n  # 真实 5 MHz：7/9 次谐波折叠与基波重合
    x = 2.7 * np.sin(2 * np.pi * fin5 * t)
    m5 = sine_fit_metrics(x, fs, fin5)
    spur_tests.append(
        {
            "spur": "纯 5MHz 正弦（折叠谐波去重）",
            "THD_measured": float(m5["THD_dB"]),
            "PASS": m5["THD_dB"] < -120,
            "dropped": m5["harmonics_dropped"],
        }
    )
    out["metrics_known_answer"] = {"tests": spur_tests, "PASS": all(t["PASS"] for t in spur_tests)}

    # ---- 10.5 KTC 动态预算 ----
    rows_ktc: list[dict[str, object]] = []
    for fbw in (None, 7.5e9, 5e9, 3e9):
        c = _clone(
            Config(),
            ktc_enable=True,
            ktc_observe_bw_hz=fbw,
            mismatch_enable=False,
            dem_enable=False,
        )
        r = run_sim(c, sine_input(0.9 * c.v_fs, fin), n, rng=np.random.default_rng(21))
        rows_ktc.append(
            {
                "f_bw": "ideal" if fbw is None else f"{fbw/1e9:.1f}GHz",
                "eps_settle": float(c.ktc_settle_residual()),
                "err_to_x2_rms_uV": float(np.sqrt(np.mean(r.err_to_x2**2)) * 1e6),
                "SNDR_dB": float(sine_fit_metrics(r.out, c.fs, fin)["SNDR_dB"]),
            }
        )
    out["ktc_dynamic_budget"] = {
        "rows": rows_ktc,
        "dt_ps": float(Config().ktc_dt() * 1e12),
        "bw_need_eps10_GHz": float(-np.log(0.10) / (2 * np.pi * Config().ktc_dt()) / 1e9),
        "bw_need_eps1_GHz": float(-np.log(0.01) / (2 * np.pi * Config().ktc_dt()) / 1e9),
        "判据": "v4 更正：观测通路的阶跃(n_R)与斜坡(dx)建立系数不同"
        "(eta_n != eta_x, 详见 stage11)。SNDR 不动是因为 (1-beta_x)dx "
        "对带限信号是 LTI 响应（正弦拟合吸收幅度/相位），"
        "err_to_x2 才是时刻一致性的真实度量；"
        "beta 已分解为 beta_n(噪声)/beta_x(信号)，见 stage11 ktc_dual_beta",
    }
    return out


# =====================================================================
# Stage 11 -- v4 审计验收（理论解释修正 + KTC 双建立系数 + 可观测性）
#
# 第四版审计的三个 P0/P1：
#   P0-A 全局失配的误差是 delta_G*(x - vD0)（码相关锯齿），不是整机纯增益误差
#   P0-B KTC 观测通路的阶跃(n_R)与斜坡(dx)建立系数不同：eta_n != eta_x
#   P1   校准可观测性：使用矩阵 U 的秩决定"能校准什么"
# =====================================================================
def stage11_audit_v4(n: int = 2**14) -> dict:
    """stage11 -- v4 审计验收：eta_n≠eta_x 双建立系数、全局失配锯齿结构、校准可观测性。

    验证什么：回应第四版审计三个 P0/P1——
        11.1 KTC 观测通路阶跃(n_R)与斜坡(dx)建立系数不同（eta_n≠eta_x）；
        11.2 全局失配误差是 δG·(x−vD0) 码相关锯齿，非整机增益误差；
        11.3 校准可观测性由使用矩阵 U 的秩决定（DEM 轮转群完全对称→零空间）。
    方法：数值积分 vs 解析式验证 eta；锯齿残差 vs 噪声底；构造 U 算秩。
    判据：见各子 dict 的 PASS 与"判据"键（阈值显式：11.1 阶跃/斜坡误差<1e-8、
        11.2 锯齿残差落噪声底、11.3 rank(U)=n_units/n_active）。

    Args:
        n: 采样点数（11.2/11.4/11.5 用相干采样）。[无量纲]

    Returns:
        eta_validation: eta_n/eta_x 数值积分 vs 解析（含 PASS）。
        global_mismatch_structure: 锯齿 vs 整机增益残差（含 PASS_sawtooth）。
        calibration_observability: rank(U) 与零空间维度（含 PASS）。
        ktc_dual_beta: 双 beta 权衡 e=(1-β_n)nR+(β_x-1)dx（含 PASS_model）。
        sfdr_guard_multi: 多距离 spur 保护区测试（含 PASS）。
    """
    out = {}

    # ---- 11.1 eta_n / eta_x：三激励数值积分 vs 解析 ----
    fbw = 3e9
    cc = _clone(Config(), ktc_observe_bw_hz=fbw)
    tau = cc.ktc_tau_a()
    T = cc.ktc_dt()
    Npts = 40000
    dts = T / Npts

    def integ(f_drive):
        """对驱动函数 f_drive 做一阶 RC 数值积分（梯形法），返回 T 时刻的累积值。

        Args:
            f_drive: 单变量驱动函数，输入时间 t（s）、输出驱动量（V 或 V/s）。

        Returns:
            在 T = Npts·dts 处的积分结果（与解析建立系数对比的量）。
        """
        v = 0.0
        for i in range(Npts):
            a = math.exp(-dts / tau)
            v = v * a + (1 - a) * 0.5 * (f_drive(i * dts) + f_drive((i + 1) * dts))
        return v

    eta_n_a = cc.ktc_eta_n()
    eta_x_a = cc.ktc_eta_x()
    v_step = integ(lambda t: 1.0)  # 阶跃
    v_ramp = integ(lambda t: -t / T)  # 斜坡(总变化=1)
    f5 = 5e6
    A5 = 2.7

    def xt(t):
        """返回频率 f5、幅度 A5 的正弦驱动样本，供 eta_x 斜坡/正弦片段积分使用。

        Args:
            t: 时间（s）。

        Returns:
            该时刻正弦驱动值（V）。
        """
        return A5 * math.sin(2 * math.pi * f5 * t)

    v_sin = integ(lambda t: -(xt(t) - xt(0.0)))  # 正弦片段
    sin_approx = -eta_x_a * (xt(T) - xt(0.0))
    sin_old = -eta_n_a * (xt(T) - xt(0.0))  # 旧统一系数
    out["eta_validation"] = {
        "fbw_GHz": fbw / 1e9,
        "Tw_ps": T * 1e12,
        "eta_n": eta_n_a,
        "eta_x": eta_x_a,
        "eta_x_over_eta_n": eta_x_a / eta_n_a,
        "step_num": v_step,
        "step_err": abs(v_step - eta_n_a),
        "ramp_num": v_ramp,
        "ramp_err": abs(v_ramp + eta_x_a),
        "sine_num_uV": v_sin * 1e6,
        "sine_eta_x_err_pct": abs(v_sin - sin_approx) / abs(v_sin) * 100,
        "sine_old_model_err_pct": abs(sin_old - v_sin) / abs(v_sin) * 100,
        "PASS": abs(v_step - eta_n_a) < 1e-9
        and abs(v_ramp + eta_x_a) < 1e-8
        and abs(v_sin - sin_approx) / abs(v_sin) < 0.01,
        "判据": "阶跃/斜坡与解析式一致(<1e-8)；正弦片段 eta_x 近似 <1%，"
        "旧统一系数模型偏差 ~55%（非小修，是量级错误）",
    }

    # ---- 11.2 全局失配误差结构：锯齿 vs 整机增益 ----
    fin = _coherent_fin(Config(), n)
    c0 = Config()
    from .chip import chip_draw

    draw = chip_draw(c0)
    cfg = _clone(c0, mismatch_split=(1, 0, 0), mismatch_sigma0=1e-2, dem_enable=False)
    chip = build_chip(cfg, draw=draw)
    r = run_sim(cfg, sine_input(0.9 * cfg.v_fs, fin), n, chip=chip, rng=np.random.default_rng(5))
    x = np.asarray(r.x_ref)
    vd0 = np.asarray(r.vd0)
    e = np.asarray(r.err)
    dg = float(np.mean(r.g_vec) / cfg.g0 - 1)
    e_resid_sawtooth = e - dg * (x - vd0)  # 锯齿假设的剩余
    e_resid_gain = e - dg * x  # 整机增益假设的剩余
    ri = run_sim(
        _clone(c0, mismatch_enable=False, dem_enable=False),
        sine_input(0.9 * cfg.v_fs, fin),
        n,
        rng=np.random.default_rng(5),
    )
    floor = float(np.sqrt(np.mean(np.asarray(ri.err) ** 2)))
    m_g = sine_fit_metrics(r.out, cfg.fs, fin)
    out["global_mismatch_structure"] = {
        "delta_G": dg,
        "SNDR_dB": m_g["SNDR_dB"],
        "err_rms_uV": float(np.sqrt(np.mean(e**2)) * 1e6),
        "sawtooth_pred_peak_uV": abs(dg) * cfg.delta1 / 2 * 1e6,
        "resid_if_sawtooth_uV": float(np.sqrt(np.mean(e_resid_sawtooth**2)) * 1e6),
        "resid_if_gain_uV": float(np.sqrt(np.mean(e_resid_gain**2)) * 1e6),
        "noise_floor_uV": floor * 1e6,
        "PASS_sawtooth": abs(np.sqrt(np.mean(e_resid_sawtooth**2)) - floor) / floor < 0.05,
        "判据": "剩余误差落在噪声底 => e = delta_G*(x-vD0) 逐样本成立；"
        "这是码相关锯齿（每码区间扫 |delta_G|*Delta1），不是整机增益误差。"
        "DEM 不改变它（G 偏移与 DEM 状态无关），数字增益校准才能消除",
    }

    # ---- 11.3 校准可观测性：使用矩阵 U 的秩 ----
    # 判据的解析式（**不写死 64**）：DEM 状态空间 = (n_active 个 slice 轮转)
    # x (8 个 segment 轮转) x (8 个 sub-unit 轮转)。slice 维度上的轮转群阶为
    # n_active，作用在单位上是完全对称的 —— 任何"各 slice 同位置单位同时开关"
    # 的线性组合都落在 U 的列空间里无法分辨，于是可辨识自由度恰好是
    # **slice 内的位置数** n_unit_per_slice = n_units_sig / n_active。
    # 其余 n_units_sig - n_unit_per_slice 个方向是校准的零空间（不可观测）。
    # 实测在 6 组 (b1, upl1, n_slices, main/sub) 组合上均吻合，故作为判据。
    from .mapper import N_DEM_STATES as NS
    from .mapper import unit_rank_arrays

    slice_rank, unit_idx = unit_rank_arrays(c0)
    n_u = c0.n_units_sig
    # 码范围必须由读数派生：b1=6 时 64 个码仓、b1=7 时 128 个。旧版写死
    # np.arange(0, 64)，在新读数下只扫了前一半码仓，rank 报成 320。
    codes = np.arange(0, 2**c0.b1)
    U = np.zeros((codes.size * NS, n_u), dtype=np.float32)
    row = 0
    for k_code in codes:
        ku = int(k_code * c0.units_per_lsb1)
        for sid in range(NS):
            sr = slice_rank[sid]
            ui = unit_idx[sid]
            U[row, sr[:ku] * c0.n_unit_per_slice + ui[:ku]] = 1.0
            row += 1
    rank = int(np.linalg.matrix_rank(U))
    colkey = [U[:, i].tobytes() for i in range(n_u)]
    uniq: dict[bytes, list[int]] = {}
    for i, k in enumerate(colkey):
        uniq.setdefault(k, []).append(i)
    sizes = sorted((len(v) for v in uniq.values()), reverse=True)
    rank_expected = n_u // c0.n_active
    out["calibration_observability"] = {
        "n_units": n_u,
        "n_obs": int(U.shape[0]),
        "rank": rank,
        "rank_expected": rank_expected,
        "null_space_dim": n_u - rank,
        "n_distinct_columns": len(uniq),
        "group_sizes_top5": sizes[:5],
        "groups_cross_slice": True,
        "PASS": rank == rank_expected and rank < n_u,
        "判据": "rank(U) = n_units_sig/n_active（此处 512/8 = 64）<< 512："
        "slice 轮转群完全对称，各 slice 同位置的单位无法被分别识别，"
        "校准只能识别 64 个 slice 内位置组合，另 448 个方向落在零空间。"
        "打破简并需要'各 slice 选不同数量'的映射 = [11] 的低位跨 slice 分配。"
        "（n_distinct_columns 随读数变化：b1=6/upl1=8 时 64 组 x8，"
        "b1=7/upl1=4 时 512 个单元素组 —— 但秩不变，仍是 64。）",
    }

    # ---- 11.4 KTC 双 beta 权衡：一个 kappa 无法同时优化噪声与信号 ----
    # 取最接近 5 MHz 的相干频率。（此前这里还有一个 `if "f_target" in
    # _coherent_fin.__code__.co_varnames` 的运行时守卫——该参数早已删除，
    # 条件恒为假，那个分支是永不执行的死代码，已移除。）
    fin5 = c0.fs * round(5e6 * n / c0.fs) / n
    cc = _clone(c0, ktc_enable=True, ktc_observe_bw_hz=3e9, mismatch_enable=False, dem_enable=False)
    en, ex_ = cc.ktc_eta_n(), cc.ktc_eta_x()
    kappa0 = cc.g0 / cc.ktc_gain_n
    rows = []
    for kappa, tag in ((kappa0, "nominal"), (kappa0 / ex_, "beta_x=1"), (kappa0 / en, "beta_n=1")):
        c = _clone(cc, ktc_kappa=kappa)
        r = run_sim(c, sine_input(0.9 * c.v_fs, fin5), n, rng=np.random.default_rng(21))
        m = sine_fit_metrics(r.out, c.fs, fin5)
        bn = kappa * cc.ktc_gain_n * en / cc.g0
        bx = kappa * cc.ktc_gain_n * ex_ / cc.g0
        dxr = float(np.sqrt(np.mean(r.sample.dx**2)))
        # 理论误差含三部分：n_R 残余、dx 的 LTI 时刻误差、以及与 beta 无关的
        # 噪声底（RA 噪声 + ADC2 量化 —— beta_x=1 设定下它就是主导项）
        from .config import resolve_ra_noise

        floor_v = (
            (resolve_ra_noise(cc) / cc.g0**2 * cc.g0) if False else (resolve_ra_noise(cc) / cc.g0)
        )  # RA 噪声折输入
        q2_v = cc.delta2 / math.sqrt(12.0) / cc.g0
        floor = math.sqrt(floor_v**2 + q2_v**2)
        pred = math.sqrt(((1 - bn) * c.sigma_sampling()) ** 2 + ((bx - 1) * dxr) ** 2 + floor**2)
        act = float(np.sqrt(np.mean(r.err_to_x2**2)))
        rows.append(
            {
                "setting": tag,
                "kappa": kappa,
                "beta_n": bn,
                "beta_x": bx,
                "SNDR_dB": m["SNDR_dB"],
                "err_to_x2_uV": act * 1e6,
                "err_pred_uV": pred * 1e6,
                "nR_resid_uV": (1 - bn) * c.sigma_sampling() * 1e6,
            }
        )
    out["ktc_dual_beta"] = {
        "eta_n": en,
        "eta_x": ex_,
        "rows": rows,
        "PASS_model": all(
            abs(r["err_to_x2_uV"] - r["err_pred_uV"]) / max(r["err_to_x2_uV"], 1e-9) < 0.02
            for r in rows
        ),
        "判据": "e=(1-beta_n)nR+(beta_x-1)dx 与实测吻合 <2%。"
        "信号校准(beta_x=1)令噪声过抵消(-0.55*nR)，噪声校准(beta_n=1)"
        "留 0.35*dx 的 LTI 时刻误差 —— SNDR 对两者都不敏感（LTI/小噪声），"
        "所以 kappa 的取舍必须由'时刻一致性'指标决定，不能只看 SNDR",
    }

    # ---- 11.5 SFDR 保护带多距离测试 ----
    fs = c0.fs
    t = np.arange(n) / fs
    fin1 = fs * 1021 / n
    spur_rows = []
    for dist, amp_db in ((5, -80), (6, -80), (7, -80), (10, -80), (100, -90), (1000, -80)):
        xg = np.sin(2 * np.pi * fin1 * t) + 10 ** (amp_db / 20) * np.sin(
            2 * np.pi * (fin1 + dist * fs / n) * t
        )
        m = sine_fit_metrics(xg, fs, fin1)
        spur_rows.append(
            {
                "dist_bins": dist,
                "amp_dBc": amp_db,
                "SFDR_measured": float(m["SFDR_dB"]),
                "PASS": abs(m["SFDR_dB"] - abs(amp_db)) < 0.5,
            }
        )
    out["sfdr_guard_multi"] = {
        "rows": spur_rows,
        "PASS": all(x["PASS"] for x in spur_rows),
        "判据": "保护区 ±8 bin 内 5/6/7 bin 的 -80dBc 杂散"
        "也必须被测出，不能只验证 10 bin 一个点",
    }
    return out


# ===========================================================================
# v5 -- 结构建模与设计指导
# ===========================================================================
def _split_geom(dac):
    """分段 DAC 的名义几何（设计时已知）：端点、步长、可用输入范围。"""
    v_lo, v_hi = dac._nominal_endpoints()
    step0 = (v_hi - v_lo) / (dac.levels - 1)
    v_top = v_lo + dac.levels * step0
    return {
        "v_lo": v_lo,
        "step0": step0,
        "center": (v_lo + v_top) / 2.0,
        "vfs_eff": (v_top - v_lo) / 2.0,
    }


def _sawtooth_fit(e_dac: np.ndarray, k_eq: np.ndarray, n_s: int) -> dict:
    """把 DAC 误差分解为 [常数, 增益, 子码锯齿] + 随机残差（最小二乘）。

    验证什么：桥接电容比例误差产生的误差是周期 = n_s 的子码锯齿；本拟合把
        常数/增益/锯齿系数分离，锯齿峰峰即 C_C 失配的可观测量。
    方法：对 [1, k_eq/max(k), (k_eq % n_s)/n_s] 三基做 lstsq，残差为随机项。
    判据：sawtooth_pp_uV 即锯齿幅度；DEM 开/关应基本不变（DEM 动不了 C_C）。

    Args:
        e_dac: DAC 物理误差序列（V）。[研究扩展]
        k_eq: 对应的等效码（无量纲）。[无量纲]
        n_s: 子阵列单位数（锯齿周期）。[结构]

    Returns:
        sawtooth_pp_uV: 子码锯齿峰峰（µV）。
        random_rms_uV: 随机残差 RMS（µV）。
        gain_coef: 增益基系数（无量纲）。
    """
    ks = (np.asarray(k_eq) % n_s) / float(n_s)
    X = np.stack([np.ones(len(e_dac)), np.asarray(k_eq, float) / max(k_eq.max(), 1), ks], axis=1)
    coef, *_ = np.linalg.lstsq(X, e_dac, rcond=None)
    res = e_dac - X @ coef
    return {
        "sawtooth_pp_uV": float(abs(coef[2]) * 1e6),
        "random_rms_uV": float(np.std(res) * 1e6),
        "gain_coef": float(coef[1]),
    }


def stage12_split_arch(c0: Config, n: int = 2**14) -> dict:
    """stage12 -- 分段（主/子+桥接）拓扑 vs 等权 unary：结构收益与代价。

    验证什么：同样总面积下，分段把单位数从 512 降到 72/128，单位电容大数倍→
        Pelgrom sigma 小数倍（收益）；代价是顶码亏缺的满幅收缩、C_C 比例误差
        产生的子码锯齿（DEM 无效）、与电荷口径需分面积/信号/噪声三套报告。
    方法：12.1 拓扑对比表（unary / split 64x8 / split 64x64）；12.1b 电荷口径
        闭合（闭式 vs 节点矩阵、evaluate_physical vs 独立方程、输入=输入等效
        DAC 电压时残差≈0）；12.2 桥接失配→子码锯齿；12.3 主/子边界残差。
    判据：见"判据_拓扑"/"charge_closure 判据"/"判据_锯齿"/"boundary 判据"。

    Args:
        c0: 基础配置（提供 dac_arch/dac_n_main/dac_n_sub 等）。[结构]
        n: 采样点数。[无量纲]

    Returns:
        topologies: 拓扑对比表（单位数/电平数/单位电容/sigma_eps/总采样电容/
            C_sig/C噪声等效/满幅收缩/量程代价/SNDR/THD/SFDR）。
        判据_拓扑: 结构收益与代价说明。
        charge_closure: 电荷口径闭合验收（含 PASS 式判据）。
        bridge_sweep: 桥接失配扫描行（含 DEM 开/关锯齿幅度）。
        判据_锯齿: C_C→子码锯齿说明。
        boundary: 主/子边界残差（boundary_err_uV 等）。
    """
    from .dac_arch import SplitDAC, build_split_chip
    from .sim_split import run_sim_split

    out = {}
    fin = _coherent_fin(c0, n)

    # ---- 12.1 拓扑对比表 ----
    rows = []
    topologies = [
        ("unary-512", "unary", 0, 0),
        ("split 64x8", "split", 64, 8),
        ("split 64x64", "split", 64, 64),
    ]
    chips = {}
    for name, arch, n_m, n_s in topologies:
        cc = _clone(
            c0, dac_arch=arch, dac_n_main=max(n_m, 1), dac_n_sub=max(n_s, 1), dem_enable=True
        )
        if arch == "unary":
            chip = build_chip(cc)
            c_u = cc.c_unit0
            sig = cc.sigma_mismatch()
            n_units = cc.dac_n_units
            levels = cc.dac_levels
            shrink = 0.0
            range_db = 0.0
            c_samp = chip.C_true[: cc.n_active].sum() if False else cc.c_active_nominal()
            inp_amp = 0.9 * cc.v_fs
            center = 0.0

            def run(cc=cc, chip=chip, amp=inp_amp, ctr=center):
                """Unary 拓扑下跑一次正弦仿真（center±amp），供拓扑对比表使用。"""
                return run_sim(cc, sine_input(ctr + amp, fin), n, chip=chip)
        else:
            chip = build_split_chip(cc)
            dac = SplitDAC(cc, chip)
            g = _split_geom(dac)
            c_u = chip.c_unit_nom
            sig = chip.breakdown["sigma_eps"]
            n_units = cc.dac_n_units
            levels = dac.levels
            shrink = dac.full_scale_shrinkage()
            range_db = dac.sndr_penalty_db()
            # 口径分离（v5 二轮审计 §3）：面积/负载 = A+B；
            # 信号电荷系数 = C_sig；噪声等效 = C_sig²/(A+β²B)。
            c_samp = float(chip.A + chip.B)
            c_sig_row = float(chip.A + chip.beta_true() * chip.B)
            c_noise_row = c_sig_row * c_sig_row / (chip.A + chip.beta_true() ** 2 * chip.B)
            inp_amp = 0.9 * g["vfs_eff"]
            center = g["center"]

            def run(cc=cc, chip=chip, amp=inp_amp, ctr=center):
                """Split 拓扑下跑一次正弦仿真（center±amp），供拓扑对比表使用。"""
                return run_sim_split(cc, sine_input(ctr + amp, fin), n, chip=chip)

        r = run()
        m = sine_fit_metrics(r.out, cc.fs, fin)
        chips[name] = chip
        rows.append(
            {
                "拓扑": name,
                "单位数": n_units,
                "电平数": levels,
                "单位电容_fF": c_u * 1e15,
                "sigma_eps_ppm": sig * 1e6,
                "总采样电容_pF": c_samp * 1e12,
                "C_sig_pF": (c_sig_row if arch != "unary" else c_samp) * 1e12,
                "C噪声等效_pF": (c_noise_row if arch != "unary" else c_samp) * 1e12,
                "满幅收缩_pct": shrink * 100,
                "量程代价_dB": range_db,
                "SNDR_dB": m["SNDR_dB"],
                "THD_dB": m["THD_dB"],
                "SFDR_dB": m["SFDR_dB"],
            }
        )
    out["topologies"] = rows
    out["判据_拓扑"] = (
        "同样 20.5 pF 总面积下：分段把单位数从 512 降到 72/128，"
        "单位电容大 ~4-7 倍 -> Pelgrom sigma_eps 小 ~2-2.7 倍；"
        "代价是顶码亏缺造成的满幅收缩（输入等效口径 ~1.7%/更大，"
        "两端不对称）与 C_C 比例误差（DEM 无效，见 12.2）。"
        "面积(A+B)/信号(C_sig)/噪声等效三个电容口径已分离报告。"
    )

    # ---- 12.1b 电荷口径闭合验收（v5 二轮审计 §7-2/§2.3）----
    # 独立节点方程 vs 被测实现；"输入=输入等效DAC电压 -> 残差为零"。
    from .charge_ref import ref_input_equiv_dac, ref_ra_charge, ref_ra_charge_nodal

    cc_id = _clone(
        c0,
        dac_arch="split",
        dac_n_main=64,
        dac_n_sub=8,
        dem_enable=True,
        mismatch_enable=False,
        dac_parasitic_spread=0.0,
        dac_bridge_mismatch_sigma=0.0,
        enable_sampling_noise=False,
        ra_enable_noise=False,
        dither_mode="off",
        dyn_input_settling=False,
        dyn_ref_settling=False,
        dyn_crosstalk=False,
    )
    chip_id = build_split_chip(cc_id)
    dac_id = SplitDAC(cc_id, chip_id)
    om0, os0 = dac_id.order_for_state(0)
    # (a-0) 闭式解 vs **真节点矩阵求解器**（v5.1 三轮审计 §一）：
    # charge_ref.ref_ra_charge_nodal 按电容连接组装 2x2 系统数值求解，
    # 与闭式消元解的最大偏差是"两份代码抄错同一代数式"风险的量化。
    worst_cf = 0.0
    rng_nd = np.random.default_rng(c0.seed + 12)
    for s in (0, 7, 137, 511):
        om2, os2 = dac_id.order_for_state(s)
        kk = rng_nd.integers(0, dac_id.levels, 25)
        xx = rng_nd.uniform(-2.5, 2.5, len(kk))
        q_cf = np.asarray(
            ref_ra_charge(chip_id, cc_id.v_fs, om2, os2, kk * 1.0, kk % cc_id.dac_n_sub, xx)
        )
        q_nd = np.asarray(
            ref_ra_charge_nodal(chip_id, cc_id.v_fs, om2, os2, kk * 1.0, kk % cc_id.dac_n_sub, xx)
        )
        worst_cf = max(worst_cf, float(np.max(np.abs(q_cf - q_nd))))
    # (a) 独立方程 vs evaluate_physical（Q_D/C_sig 口径，x=0 反解 Q_D）
    worst_q = 0.0
    rng_cv = np.random.default_rng(c0.seed + 11)
    for s in (0, 7, 137):
        om2, os2 = dac_id.order_for_state(s)
        kk = rng_cv.integers(0, dac_id.levels, 40)
        for k in kk:
            km_i, ks_i = (int(v) for v in dac_id.split_code(int(k)))
            q0 = float(
                np.asarray(ref_ra_charge(chip_id, cc_id.v_fs, om2, os2, km_i, ks_i, 0.0)).ravel()[0]
            )
            qd_ref = -q0 / chip_id.c_sig_true()  # q0 = -Q_D -> v_D,in = Q_D/C_sig
            worst_q = max(
                worst_q,
                abs(
                    float(
                        dac_id.evaluate_physical(
                            np.array([float(k)]), np.array([s], dtype=np.int64)
                        )[0]
                    )
                    - qd_ref
                ),
            )
    # (b) 端到端：输入=输入等效DAC电压（k 取 n_sub 整数倍并内移 1e-9 V，
    #     避免恰好压在 SADC 阈值上的浮点判决），残差应只剩这个内移量。
    worst_r = 0.0
    for k in (8, 136, 400, 504):
        x_k = (
            float(
                np.asarray(
                    ref_input_equiv_dac(chip_id, cc_id.v_fs, float(k), dac_id.levels)
                ).ravel()[0]
            )
            + 1e-9
        )
        r_z = run_sim_split(
            cc_id, lambda t, x_k=x_k: np.full_like(np.asarray(t, dtype=float), x_k), 8, chip=chip_id
        )
        worst_r = max(worst_r, abs(float(np.asarray(r_z.vra).ravel()[-1])))
    out["charge_closure"] = {
        "closedform_vs_nodalmatrix_max_V": worst_cf,
        "eval_vs_nodal_max_V": worst_q,
        "residue_zero_max_V": worst_r,
        "判据": (
            f"闭式解 vs 节点矩阵求解器最大偏差 {worst_cf*1e15:.3f} fV；"
            f"evaluate_physical 与独立节点方程最大偏差 {worst_q*1e12:.3f} pV；"
            f"输入=输入等效DAC电压(+1e-9V 内移)时主循环残差最大 {worst_r*1e9:.2f} nV "
            "（修复前 142.75 mV）—— 模拟中间节点必须符合独立电荷方程，"
            "数字自洽重构不能作为证据。"
            "注意：本验收覆盖**无 dither 采样相位**；sampling dither 改变"
            "采样电荷系数（α 从掩码推导，见 config.dither_alpha），"
            "带 dither 的相位闭合是下一版工作。"
        ),
    }

    # ---- 12.2 桥接电容比例误差 -> 子码锯齿（dither 激励子阵列）----
    # 只有 k_s != 0（sampling dither 的分数码）才走子阵列 -> 才能观察锯齿。
    cbase = _clone(
        c0,
        dac_arch="split",
        dac_n_main=64,
        dac_n_sub=8,
        dem_enable=True,
        dither_mode="sampling",
        dither_units_range=2.0,
    )
    chip12 = build_split_chip(cbase)
    dac12 = SplitDAC(cbase, chip12)
    g12 = _split_geom(dac12)
    rows_b = []
    for bsig in (1e-4, 5e-4, 2e-3):
        cc = _clone(cbase, dac_bridge_mismatch_sigma=bsig)
        # 只换桥接电容的失配（同一颗芯片其它失配不动）
        chip_b = build_split_chip(cc, draw=None)
        # 直接覆盖桥接电容，保证唯一的差异是 C_C：
        # 主/子单位电容、寄生（散布置零）、C_F 全部锁定为 chip12 的名义/实际值，
        # 这样锯齿系数随 bridge_sigma 的缩放才可归因。
        import numpy as _np

        chip_b.C_bridge = chip_b.C_bridge_nom * (
            1.0 + bsig * float(_np.random.default_rng(c0.seed + 7003).standard_normal())
        )
        chip_b.c_p_sub = chip12.c_p_sub_nom  # 寄生散布置零（隔离变量）
        chip_b.c_p_main = chip12.c_p_main_nom
        chip_b.C_main = chip12.C_main
        chip_b.C_sub = chip12.C_sub
        chip_b.C_feedback_true = chip12.C_feedback_true
        for dem in (False, True):
            r = run_sim_split(
                _clone(cc, dem_enable=dem),
                sine_input(g12["center"] + 0.85 * g12["vfs_eff"], fin),
                n,
                chip=chip_b,
            )
            m = sine_fit_metrics(r.out, cc.fs, fin)
            saw = _sawtooth_fit(np.asarray(r.e_dac), np.asarray(r.k), cc.dac_n_sub)
            rows_b.append(
                {
                    "bridge_sigma": bsig,
                    "dem": dem,
                    "SNDR_dB": m["SNDR_dB"],
                    "THD_dB": m["THD_dB"],
                    "sawtooth_pp_uV": saw["sawtooth_pp_uV"],
                    "random_rms_uV": saw["random_rms_uV"],
                }
            )
    saw_dem_off = [r for r in rows_b if not r["dem"]]
    saw_dem_on = [r for r in rows_b if r["dem"]]
    out["bridge_sweep"] = rows_b
    out["判据_锯齿"] = (
        "C_C 比例误差产生周期 = n_sub 的子码锯齿；DEM 开/关锯齿幅度必须基本不变"
        f"（DEM 只置换单位，动不了 C_C）：off {saw_dem_off[-1]['sawtooth_pp_uV']:.1f} uV vs "
        f"on {saw_dem_on[-1]['sawtooth_pp_uV']:.1f} uV @ sigma=2e-3。"
        "锯齿只有一个自由度（子阵列增益），一次校准即可消掉 —— 这正是专利 [11] "
        "主/子边界的量化版本。"
    )

    # ---- 12.3 主/子边界残差（专利 [11] 的可观测量）----
    out["boundary"] = {
        "main_LSB_uV": dac12.main_weight() * 1e6,
        "sub_LSB_uV": dac12.sub_weight() * 1e6,
        "boundary_err_uV": dac12.boundary_error() * 1e6,
        "sub_weight_rel_err_ppm": chip12.sub_weight_relative_error() * 1e6,
        "判据": (
            "boundary_err = n_sub*w_S - w_M：主/子各自 DEM 后各自逼近自己的"
            "平均值，两个平均值之差就是边界台阶；其中 C_C 比例误差部分 DEM 消不掉。"
        ),
    }
    return out


def _dem_rotation_period(cfg: Config) -> int:
    """Split 拓扑下主/子轮转组合的完整周期（v5.1 三轮审计 §3.1）。

    order_for_state 用 (sid·a) mod n 生成顺序：主阵列周期 = n_m/gcd(a_m,n_m)，
    子阵列同理，组合周期 = lcm。默认 (64,8) 时主 64、子 8 → 周期 64。
    静态测试的重复次数必须覆盖"每个 bank 的完整周期"，即 rep = 2×周期
    （A/B 两个 bank 各自从 0 独立推进，全局样本数 = 每 bank 样本数 × 2）。
    固定写 rep=96 只覆盖 48/64 组合 —— INL 是未完成平均下的确定值。

    Args:
        cfg: split 拓扑配置（提供 dac_n_main / dac_n_sub）。[结构]

    Returns:
        主/子轮转组合的完整周期（整数，单位：DEM 状态步）。
    """
    import math as _m

    a_m, a_s = 331, 173  # 与 dac_arch._A_MAIN/_A_SUB 一致
    n_m, n_s = int(cfg.dac_n_main), int(cfg.dac_n_sub)
    p_m = n_m // _m.gcd(a_m, n_m)
    p_s = n_s // _m.gcd(a_s, n_s)
    return p_m * n_s // _m.gcd(p_m, p_s) if p_s else p_m


def stage13_dynamics(c0: Config, n: int = 2**14, n_lvl: int = 129, rep: int | None = None) -> dict:
    """stage13 -- 三项动态误差（输入建立/参考建立/数字串扰）。

    [00_1] p.32 点名的三项。全部与码相关 -> 产生确定性 INL 结构；
    共模部分 DEM 无效。这是模型 INL(0.07 LSB) 与论文 2.2 LSB 之间缺口的来源。

    测量协议（v5 二轮审计 §4 / v5.1 三轮审计 §三，两套测试严格分开）：
      * **确定性测试**（本表 INL 列）：关热噪声 + 关 dither + 固定芯片，
        转移曲线是确定性的，直接报峰值/RMS —— 不做任何"去噪"换算。
        （旧版曾用 sqrt(peak²-floor²) 给峰值去噪：max|e+n| 是非线性统计量，
        方差相减不成立 —— 实测真实 INL=0 的曲线被报出 2.18 LSB 中位数。）
      * **DEM 覆盖（v5.1）**：重复次数由实际物理轮转周期反推
        （rep = 2×_dem_rotation_period，默认 128），并验证 rep→2rep 峰值
        不变（确定性协议的收敛判据）。旧 rep=96 只覆盖 48/64 组合。
      * **ρ 反推（v5.1）**：电平网格 24→257（粗码边界两侧必须被覆盖），
        跨越 2.2 LSB 的区间再做 3 次二分细化；结果标注为
        **当前网格下的条件性估计**，不是收敛后的规格 —— 输入网格加密、
        完整逐码 DNL/INL 与启动样本剔除仍是开放项。
      * **测量不确定度**：由 SNDR 行的动态仿真（噪声开）承担；
        静态均值噪声底的问题不再混进 INL 数字。

    Args:
        c0: 基础配置（split 拓扑）。[结构]
        n: 采样点数（SNDR/动态协议）。[无量纲]
        n_lvl: 静态测试电平数（确定性 INL 协议）。[无量纲]
        rep: 每 bank 重复次数；None 时取 2×_dem_rotation_period。[无量纲]

    Returns:
        rows: 各动态设置一行（SNDR_dB/THD_dB/SFDR_dB/INL_max_LSB20/INL_rms_LSB20/
            INL口径/err_rms_uV）。
        sub_weight_inl: 子阵列码调制产生的确定性 INL（含预测 vs 实测比值 PASS）。
        ron_sweep: rho 灵敏度扫描（INL_max_LSB20/INL_rms_LSB20/增益误差_ppm）。
        rep_convergence: rep→2rep 峰值收敛判据（PASS）。
        rho_max_for_2p2LSB: 当前网格下条件性估计的 rho 上界（或 None）。
        rho_max_INL_at_rho_max: 该点实测峰值 INL（LSB，或 None）。
        判据_ron / 判据 / lsb20_uV / 论文_INL_LSB / params_used: 说明与参数表。
    """
    from .dac_arch import SplitDAC, build_split_chip
    from .sim_split import run_sim_split

    if rep is None:
        rep = 2 * _dem_rotation_period(c0)

    out = {}
    cbase = _clone(c0, dac_arch="split", dac_n_main=64, dac_n_sub=8, dem_enable=True)
    chip = build_split_chip(cbase)
    dac = SplitDAC(cbase, chip)
    g = _split_geom(dac)
    fin = _coherent_fin(c0, n)
    inp = sine_input(g["center"] + 0.9 * g["vfs_eff"], fin)
    lsb20 = c0.lsb_target

    settings = [
        ("base 全关", {}),
        ("输入建立", {"dyn_input_settling": True}),
        ("输入建立+Ron码调制", {"dyn_input_settling": True, "dyn_ron_code_coeff": 0.5}),
        ("参考建立", {"dyn_ref_settling": True}),
        ("数字串扰(共模+单位)", {"dyn_crosstalk": True}),
        ("全部开启", {"dyn_input_settling": True, "dyn_ref_settling": True, "dyn_crosstalk": True}),
    ]
    rows = []
    for name, kw in settings:
        # INL：确定性协议（噪声关 + dither 关 + DEM 开，rep 次覆盖 DEM 状态）
        cc_det = _clone(
            cbase, **kw, enable_sampling_noise=False, ra_enable_noise=False, dither_mode="off"
        )
        # SNDR：动态协议（噪声开、dither 按配置），与 INL 分开报告
        cc_dyn = _clone(cbase, **kw)

        def run_fn(level, reps, cc=cc_det):
            """确定性 INL 协议：跑静态转移曲线并剔除首个启动瞬态样本。"""
            # v5.1 三轮审计 §3.2：剔除首个样本 —— v_prev[0]=0 是启动瞬态，
            # 在不同 rep 下占均值权重不同，会破坏"完整周期后 INL 不变"的
            # 收敛判据（实测 rep=128 vs 256 差 2.7e-3 LSB）。
            return run_sim_split(cc, dc_input(level), reps, chip=chip).out[1:]

        levels = np.linspace(
            g["v_lo"] + 0.02 * g["vfs_eff"], g["v_lo"] + 0.98 * 2 * g["vfs_eff"], n_lvl
        )
        st = static_test(run_fn, levels, rep)
        inl = inl_from_mean_error(levels, st["mu_e"], lsb20)
        inl_rms = float(np.sqrt(np.mean((inl["inl_lsb"]) ** 2)))
        r = run_sim_split(cc_dyn, inp, n, chip=chip)
        m = sine_fit_metrics(r.out, cc_dyn.fs, fin)
        rows.append(
            {
                "设置": name,
                "SNDR_dB": m["SNDR_dB"],
                "THD_dB": m["THD_dB"],
                "SFDR_dB": m["SFDR_dB"],
                "INL_max_LSB20": inl["inl_max_lsb"],
                "INL_rms_LSB20": inl_rms,
                "INL口径": "确定性（噪声关）",
                "err_rms_uV": float(np.std(r.err)) * 1e6,
            }
        )
    out["rows"] = rows

    # ---- 13.1b base 行的确定性 INL 来自哪里？（机制隔离 + 解析等式判据）----
    # 打开读数后 units_per_lsb1 = n_units_sig/2**b1 可能**小于** n_sub，于是
    # 一个粗码步只跨越 n_sub 个子单位的一部分，子阵列被码调制。子阵列**节点
    # 寄生** c_p_sub 与桥接电容 C_C 都是集总元件、不参与单位置换，DEM 平均
    # 不掉它们：它们同时改变桥接比 β 与 C_sig，于是"每粗码步取 k_s 个子单位"
    # 的两条支路（k_s = 0 与 k_s = n_sub/2）在扣除增益后各偏 ±Δ/2：
    #
    #     g    ≡ β·B / C_sig                    （子阵列输入等效权重因子）
    #     ΔINL = 0.5 · v_FS · |g_true − g_nom| / LSB20
    #
    # **判据是等式**：解析预测与"纯 DAC、DEM 全周期平均后扣增益"的实测值比
    # （±25%）。默认假设下 3.17 vs 3.29 LSB20（比值 1.04）。旧读数
    # units_per_lsb1 == n_sub 时子阵列完全不被码调制，此项恒为零 —— 旧版
    # base 行 0.33 LSB20 的全部来源就是这个"没被激励"的子阵列。
    #
    # 与论文的关系：这一项正是 PPT p.31「Binary-to-unary bridging」要校正的
    # 子权失配；本模型未实现该校正（见 docs/model_scope.md §4），所以采用
    # 7b+2b 读数时整链 base 行（4.2 LSB20）高于论文 2.2 LSB —— **如实入账**。
    _B = chip.C_sub.sum()
    _g_true = chip.beta_true() * _B / chip.c_sig_true()
    _g_nom = chip.beta_nom() * chip.B_nominal() / chip.c_sig_nom()
    _sub_modulated = (int(cbase.units_per_lsb1) % int(cbase.dac_n_sub)) != 0
    _codes = np.arange(0, 2 ** int(cbase.b1))
    _keq = (_codes * int(cbase.units_per_lsb1)).astype(float)
    _n_state = max(1, int(_dem_rotation_period(cbase)))
    _acc = np.zeros(len(_keq))
    for _s in range(_n_state):
        _acc += dac.evaluate_physical(_keq, np.full(len(_keq), _s))
    _err = ((_acc / _n_state) - dac.evaluate_nominal(_keq)) / lsb20
    _Amat = np.column_stack([_keq, np.ones_like(_keq)])
    _coef, *_ = np.linalg.lstsq(_Amat, _err, rcond=None)
    _resid = _err - _Amat @ _coef
    _meas_sub = float(np.max(np.abs(_resid)))
    _pred_sub = 0.5 * cbase.v_fs * abs(_g_true - _g_nom) / lsb20 if _sub_modulated else 0.0
    out["sub_weight_inl"] = {
        "units_per_lsb1": int(cbase.units_per_lsb1),
        "dac_n_sub": int(cbase.dac_n_sub),
        "sub_code_modulated": bool(_sub_modulated),
        "dem_states_averaged": _n_state,
        "g_true": float(_g_true),
        "g_nom": float(_g_nom),
        "sub_weight_rel_err": float(abs(_g_true - _g_nom) / _g_nom),
        "predicted_INL_max_LSB20": float(_pred_sub),
        "measured_dac_only_INL_max_LSB20": _meas_sub,
        "ratio": float(_meas_sub / _pred_sub) if _pred_sub > 0 else None,
        "measured_chain_base_row_LSB20": float(rows[0]["INL_max_LSB20"]),
        "PASS": bool(abs(_meas_sub / _pred_sub - 1.0) < 0.25)
        if _pred_sub > 0
        else bool(_meas_sub < 1.5),
        "判据": "子阵列被码调制（units_per_lsb1 % n_sub != 0）时，纯 DAC 的确定性 "
        "INL = 0.5·v_FS·|g_true−g_nom|/LSB20（±25%）；不被调制时 < 1.5 LSB20。"
        "该子权失配由论文的 binary-to-unary bridging 校正处理，本模型未实现，"
        "故整链 base 行高于论文 INL —— 如实入账，见 docs/model_scope.md。",
    }

    # ---- 13.2 Ron 码调制系数 rho 的灵敏度扫描 -> 反推设计边界 ----
    # v5 审计修正：**"tau 与码无关 -> 纯增益误差" 只在 epsilon<<1 时成立。**
    # 精确关系是 x[n] = xhat[n-1] + (1-eps)(x-xhat[n-1])，写成 z 域得
    #     x[n] = sum_i k^i * v_{D,0}(x_{n-i}),   k = -eps/(1-eps)*(2-phi)
    # 即使 rho=0（eps 与码无关），这一步仍对**历史 DAC 权值**有记忆，
    # 只在 |k| 远小于 1（即 eps 很小）时才退化成近似的
    # x ≈ x_{n-1} + eps(x-v_{D,0}(x_{n-1})) 那样的纯增益误差。
    # 实测边界：Ts=11.25 ns(eps=1.5e-5) 时 rho=0 的 INL 仅 0.32 LSB；
    # 但 Ts 压到 2.5 ns(eps=8.4e-2) 时会炸到 1e4 LSB 量级 ——
    # 所以这是**工作点性质**，不是可写进教科书的普适规律。
    #
    # v5.1 三轮审计 §3.2：rho 反推必须 ① 电平网格加密（24 -> 257，粗码边界
    # 两侧覆盖）② 完整 DEM 周期覆盖 ③ 跨越 2.2 LSB 的区间二分细化，
    # 且结果只能标 **条件性估计**。旧 24 电平插值给 rho=0.041，审计实测
    # 257 电平下峰值 2.402~2.474 LSB —— 不能保证 ≤2.2，已作废。
    n_lvl_rho = 257
    lv_rho = np.linspace(
        g["v_lo"] + 0.02 * g["vfs_eff"], g["v_lo"] + 0.98 * 2 * g["vfs_eff"], n_lvl_rho
    )
    # v5 二轮审计 §4.2：必须**重建理想芯片**。旧代码只把 mismatch_enable=False
    # 写进配置，却继续把带失配的 chip 传进仿真 —— 关配置不会把已生成的
    # 真实电容变回理想值，"隔离输入建立"的实验混入了失配。
    cfg_ideal = _clone(
        cbase,
        mismatch_enable=False,
        dac_parasitic_spread=0.0,
        dac_bridge_mismatch_sigma=0.0,
        enable_sampling_noise=False,
        ra_enable_noise=False,
        dither_mode="off",
    )
    chip_ideal = build_split_chip(cfg_ideal)

    def _inl_peak_at_rho(rho, levels_r, rep_r):
        """在理想芯片上测给定 rho 的确定性峰值/RMS INL 与增益误差（剔除首个样本）。

        Args:
            rho: Ron 码调制系数（无量纲）。[假设]
            levels_r: 静态测试电平数组（V）。[无量纲]
            rep_r: 每电平重复次数（覆盖 DEM 周期）。[无量纲]

        Returns:
            (INL_max_LSB20, INL_rms_LSB20, 增益误差_ppm) 三元组。
        """
        cc = _clone(cfg_ideal, dyn_input_settling=True, dyn_ron_code_coeff=rho)
        st = static_test(
            lambda lev, reps, cc=cc: run_sim_split(cc, dc_input(lev), reps, chip=chip_ideal).out[
                1:
            ],
            levels_r,
            rep_r,
        )
        inl = inl_from_mean_error(levels_r, st["mu_e"], lsb20)
        return (
            float(np.max(np.abs(inl["inl_lsb"]))),
            float(np.sqrt(np.mean(inl["inl_lsb"] ** 2))),
            float(inl["gain_err"] * 1e6),
        )

    rhos = (0.0, 0.005, 0.01, 0.02, 0.03, 0.04, 0.05, 0.1, 0.3)
    rr = []
    for rho in rhos:
        pk, rms, ge = _inl_peak_at_rho(rho, lv_rho, rep)
        rr.append({"rho": rho, "INL_max_LSB20": pk, "INL_rms_LSB20": rms, "增益误差_ppm": ge})
    # 跨越 2.2 LSB 的区间二分细化 3 次（每次一个新 rho 点）
    rho_max, rho_max_pk = None, None
    for i in range(1, len(rr)):
        if rr[i]["INL_max_LSB20"] >= 2.2 >= rr[i - 1]["INL_max_LSB20"]:
            a, b = rr[i - 1]["rho"], rr[i]["rho"]
            rho_max_pk = rr[i - 1]["INL_max_LSB20"]  # a 端实测（<2.2）
            for _ in range(3):
                mid = 0.5 * (a + b)
                pk_mid, _, _ = _inl_peak_at_rho(mid, lv_rho, rep)
                if pk_mid >= 2.2:
                    b = mid
                else:
                    a, rho_max_pk = mid, pk_mid
            rho_max = a  # 保守取区间下端
            break
    out["ron_sweep"] = rr
    # rep 收敛判据：完整 DEM 周期覆盖后，确定性峰值不应再随 rep 变化
    pk_2rep, _, _ = _inl_peak_at_rho(0.0, lv_rho, 2 * rep)
    out["rep_convergence"] = {
        "rep": rep,
        "period_per_bank": _dem_rotation_period(c0),
        "INL_at_rep": rr[0]["INL_max_LSB20"],
        "INL_at_2rep": pk_2rep,
        "PASS": abs(pk_2rep - rr[0]["INL_max_LSB20"]) < 1e-9,
    }
    out["rho_max_for_2p2LSB"] = rho_max
    out["rho_max_INL_at_rho_max"] = rho_max_pk
    out["判据_ron"] = (
        f"理想芯片+噪声关的确定性协议（{n_lvl_rho} 电平 × rep={rep} 完整 DEM 周期）下 "
        f"rho=0 的峰值 INL={rr[0]['INL_max_LSB20']:.2f} LSB、"
        f"RMS={rr[0]['INL_rms_LSB20']:.2f} LSB：默认采样相（eps≈1.5e-5）下纯建立"
        "近似为可校准的增益误差。**但这是工作点性质，不是普适规律** —— "
        "x[n] 对历史 DAC 权值有记忆（k=-eps/(1-eps)·(2-phi)），eps 增大"
        "（采样相缩短）时即使 rho=0 INL 也会炸（实测 Ts=2.5 ns 时 >1e4 LSB）。"
        + (
            f"反推（确定性峰值 vs 论文 |INL|_max=2.2）：当前网格下的**条件性估计** "
            f"rho ≤ {rho_max:.4f}（该点实测峰值 {rho_max_pk:.3f} LSB，二分细化 3 次）"
            "—— 自举开关的量化理由。**这不是收敛后的规格**：网格再加密、完整逐码"
            "DNL/INL、启动样本剔除仍可能移动它；只作灵敏度排序用。"
            if rho_max is not None
            else "扫描范围内未见 2.2 LSB 跨越，需扩大 rho 范围。"
        )
        + " 注意 rho 当前作用在 R_source+R_on 总和上；若定义为 R_on 自身的调制系数，"
        "则 tau 的等效调制要乘 R_on/(R_s+R_on)，规格要相应换算。"
    )
    out["lsb20_uV"] = lsb20 * 1e6
    out["论文_INL_LSB"] = 2.2
    out["判据"] = (
        "三项都产生码相关 INL。'输入建立+Ron码调制'与'参考建立'应看到 INL 明显"
        "抬升而 SNDR 变化较小（低频失真被正弦拟合部分吸收）；共模串扰 DEM 无效。"
        "目标：用可信的参数量级解释论文 2.2 LSB 与模型 0.07 LSB 的缺口，"
        "而不是把失配 sigma 硬调大。"
        "**口径声明：INL 列全部是确定性协议（噪声关/dither 关/固定芯片）的原始值，"
        "与论文峰值 |INL|_max 直接比对；SNDR/THD/SFDR 来自噪声开的动态协议，两套分开。**"
        "另外'三项全开 < 最大单项'是本组参数/符号/误差形状下的交叉项抵消，"
        "是实测现象而非保底设计收益 —— PVT/版图极性/码型改变后可能减弱甚至反号，"
        "预算应同时给典型组合、统计分布与保守上界（各项误差曲线线性可叠加，"
        "峰值/RMS 不可相加）。"
    )
    out["params_used"] = {
        "r_source": c0.dyn_r_source,
        "r_on": c0.dyn_r_on,
        "t_sample_ns": c0.dyn_t_sample_frac / c0.fs * 1e9,
        "tau_in_ns": (c0.dyn_r_source + c0.dyn_r_on) * 20.5e-12 * 1e9,
        "c_decouple_nF": c0.dyn_c_decouple * 1e9,
        "tau_ref_ns": c0.dyn_tau_ref * 1e9,
        "c_xtalk_common_fF": c0.dyn_c_xtalk_common * 1e15,
        "c_xtalk_unit_fF": c0.dyn_c_xtalk_unit * 1e15,
        "来源": "[假设] 全部为量级演示参数，需 PDK/版图数据替换",
    }
    return out


def stage14_observability(c0: Config, n_cal: int = 4096) -> dict:
    """stage14 -- 单位权重校准的可观测性与 rank-aware 端到端验证。

    核心问题：PDK 失配 1117 ppm 下，"估计每个单位权重并数字扣除"能做到什么程度？
    秩不足时不是数据量问题，是结构问题 —— 必须先让映射可辨识。

    Args:
        c0: 基础配置（本 stage 内部用 mismatch_sigma0=1.117e-3 的 PDK 估算）。
            [假设]
        n_cal: 校准样本数（限单个 bank）。[无量纲]

    Returns:
        maps: 三种映射（fixed/dem_rotate/permute）的可观测性 dict（秩/零空间/
            真值零空间占比）。
        note_bank: 双 bank 结构需分 bank 校准的说明。
        判据_可观测性: 映射与秩关系的说明。
        calibration: 整数码/分数码双口径残差（evalA/evalB 及改善倍数、折 LSB）。
        noise_floor_uV: 噪声底（µV）。
        required_samples_300ppm / required_samples_all: 样本量反推。
        判据_校准: 防自欺双口径与适用域说明（unary 权重估计，非 split 闭环）。
    """
    from .calib import (
        calibrate_unit_weights,
        null_space_projection_error,
        observability,
        required_samples,
        use_matrix,
    )
    from .rdac import RDAC

    out = {}
    cc = _clone(
        c0,
        mismatch_sigma0=1.117e-3,  # PDK 估算值（[假设]）
        dem_enable=True,
        enable_sampling_noise=True,
    )
    chip = build_chip(cc)
    sched = Scheduler(cc)
    alloc = sched.reserve(n_cal)
    rdac = RDAC(cc, chip, np.stack([sched.bank_a, sched.bank_b]))
    mapper = Mapper(cc)
    from .mapper import N_DEM_STATES, dem_state_sequence

    rng = np.random.default_rng(cc.seed + 41)
    # 校准激励：满幅均匀码（比正弦覆盖更充分，前台校准本来就用 ramp/DC）。
    # **必须限定单个 bank**：RDAC 是双 bank 结构（共 1024 个物理单位），
    # 使用矩阵按 bank 分开才有 U@w 的线性模型。单 bank 校准 + 单 bank 应用。
    bank0 = alloc.bank == 0
    codes = rng.integers(0, 2**cc.b1, n_cal)[bank0]
    sid_all = dem_state_sequence(n_cal, cc, alloc.bank)
    sid_seq = sid_all[bank0]
    n_cal1 = int(bank0.sum())

    def perm_factory(mode):
        """构造 DEM 置换序列工厂：把「映射模式」变成一个状态编号 -> 单位顺序的函数。

        Args:
            mode: 映射模式字符串。'fixed' 为恒等顺序（不置换）；
                'dem_rotate' 为按 DEM 状态循环旋转的置换。

        Returns:
            可调用对象 f(s) -> 单位顺序数组（无量纲物理单位编号，长度 n_units_sig）。

        Notes:
            物理单位编号 = slice 编号 × n_unit_per_slice + slice 内 unit 编号；
            dem_rotate 的顺序来自 mapper.unit_rank_arrays，与仿真主循环同源。
        """
        if mode == "fixed":
            return lambda s: np.arange(cc.n_units_sig)
        if mode == "dem_rotate":
            from .mapper import unit_rank_arrays

            sr, ui = unit_rank_arrays(cc)

            # sr, ui: (512 状态, 512 单位) —— 该状态下第 j 个位置对应的
            # 物理 slice 与 slice 内 unit。物理单位编号 = slice*64 + unit。
            def f(s):
                return sr[s] * cc.n_unit_per_slice + ui[s]

            return f
        if mode == "permute":
            prng = np.random.default_rng(cc.seed + 55)
            # 注意：Generator.permutation(2D, axis=1) 会对**所有行**施加同一个
            # 列置换（实测），必须用 argsort(random) 才能得到独立随机排列。
            perms = np.argsort(prng.random((N_DEM_STATES, cc.n_units_sig)), axis=1).astype(np.int64)
            return lambda s: perms[s % N_DEM_STATES]
        raise ValueError(mode)

    map_rows = []
    U_by_mode = {}
    for mode in ("fixed", "dem_rotate", "permute"):
        pf = perm_factory(mode)
        k = codes.astype(float) * cc.units_per_lsb1
        U = use_matrix(
            k,
            sid_seq if mode != "fixed" else np.zeros(n_cal1, int),
            pf,
            cc.n_units_sig,
            N_DEM_STATES,
        )
        ob = observability(U)
        w_true = chip.eps.reshape(-1)[: cc.n_units_sig] * cc.c_unit0
        w_true = w_true * 2.0 * cc.v_fs / (cc.c_active_nominal())
        null_frac = null_space_projection_error(U, w_true)
        U_by_mode[mode] = (U, pf)
        map_rows.append({"映射": mode, **ob.as_dict(), "真值零空间占比": null_frac})
    out["maps"] = map_rows
    out["note_bank"] = "校准与应用都限定 bank0（双 bank 结构需 1024 列 U 或分 bank 校准）"
    out["判据_可观测性"] = (
        "fixed 交织映射下秩严重不足（同位置单位永远同开同关）；DEM 轮转恢复部分；"
        "每状态独立随机置换接近满秩。真值零空间占比 = 无论给多少数据都消不掉的失配比例。"
    )

    # ---- 端到端（OUT-OF-SAMPLE + 泄漏测试）----
    # 三个重要事实（v5 实验设计的关键）：
    # (1) in-sample 会自欺：嵌套指示向量张成 = "k 的一切函数"，fixed 映射能
    #     "完美校正"却只是记住了码表。必须用全新码 + 运行态评估。
    # (2) **同一映射下，零空间分量不产生误差**：秩 64 的校准在同映射评估中
    #     近乎完美。但运行态 dither 的**分数码**让选择不再按整 8 步进，
    #     零空间分量泄漏成真实误差 —— 必须测。
    # (3) 随机置换校准要求硬件真的能随机选单位（专用校准模式）：
    #     物理 LUT 只有交织 DEM 顺序，用假想映射拟合必然失败。
    #     本实验用直接电荷求值模拟"校准模式硬件"。
    n_ev = 2048
    rng_ev = np.random.default_rng(cc.seed + 77)
    # 码避开边沿：k = code*8 + dith 必须留在 DAC 物理范围内，
    # 否则名义值外推、物理值 clip，产生与研究对象无关的假误差。
    codes_ev = rng_ev.integers(2, 2**cc.b1 - 2, n_ev)
    dith_ev = rng_ev.uniform(-2.0, 2.0, n_ev)  # 运行态 sampling dither
    sid_ev = (np.arange(n_ev, dtype=np.int64) * 2654435761) % N_DEM_STATES
    n_noise = cc.sigma_sampling()

    C_flat = chip.C_true.reshape(-1)

    def e_phys_order(order, k):
        """直接电荷求值：假想'校准模式硬件'按 order 顺序取前 k 个单位。

        Args:
            order: 单位顺序（无量纲索引数组），模拟「校准模式硬件」的取用次序。
            k: 取用的单位数（个，可为小数；会被裁剪到 [0, len(order))）。

        Returns:
            按该顺序取前 k 个单位的等效电容（F），小数部分在前缀和上线性插值。
            直接由电荷求值，绕过 DAC 栅格，用于构造可观测性分析的物理基准。
        """
        k = np.asarray(k, dtype=float)
        cum = np.concatenate([[0.0], np.cumsum(C_flat[order])])
        kc = np.clip(k, 0.0, len(order) - 1e-9)
        i0 = np.floor(kc).astype(np.int64)
        frac = kc - i0
        sel = cum[i0] + frac * (cum[i0 + 1] - cum[i0])
        tot = C_flat[order].sum()
        phys = cc.v_fs * (2.0 * sel / tot - 1.0)
        return phys - (-cc.v_fs + kc * cc.rdac_step)

    from .calib import apply_weight_correction as _awc

    e_rows = []
    for mode in ("fixed", "dem_rotate", "permute"):
        U_cal, pf = U_by_mode[mode]
        # ---- 校准数据 ----
        if mode == "permute":
            # 假想校准模式：每个校准样本用**不同的**随机选单位顺序
            # （需要硬件支持；固定顺序会退化回秩 63 的嵌套结构）
            k_cal = codes.astype(float) * cc.units_per_lsb1
            sid_perm = rng.integers(0, N_DEM_STATES, n_cal1)
            e_cal = np.empty(n_cal1)
            for sv in np.unique(sid_perm):
                m = sid_perm == sv
                e_cal[m] = e_phys_order(pf(int(sv)), k_cal[m])
            U_eff = use_matrix(k_cal, sid_perm, pf, cc.n_units_sig, N_DEM_STATES)
        else:
            sid_cal = sid_seq if mode != "fixed" else np.zeros(n_cal1, dtype=np.int64)
            cmd_cal = mapper.encode(
                codes.astype(np.int64), np.zeros(n_cal1, dtype=np.int64), sid_cal
            )
            e_cal = rdac.evaluate_physical(cmd_cal) - rdac.evaluate_nominal(cmd_cal)
            U_eff = U_cal
        err_meas = -e_cal + rng.normal(0.0, n_noise, n_cal1)
        w_hat = calibrate_unit_weights(U_eff, err_meas, lam=1e-10)

        # ---- 评估：运行映射一律用真实交织 DEM 顺序（pf_op）----
        # permute 校准出的 w 只有通过运行映射施加才有意义
        pf_op = U_by_mode["dem_rotate"][1]
        U_ev = use_matrix(
            codes_ev.astype(float) * cc.units_per_lsb1, sid_ev, pf_op, cc.n_units_sig, N_DEM_STATES
        )
        cmd_ev = mapper.encode(codes_ev.astype(np.int64), np.zeros(n_ev, dtype=np.int64), sid_ev)
        e_ev = rdac.evaluate_physical(cmd_ev) - rdac.evaluate_nominal(cmd_ev)
        resA = e_ev - _awc(U_ev, w_hat)
        # ---- 评估 B：分数码（运行态 sampling dither）——零空间泄漏测试 ----
        k_frac = codes_ev.astype(float) * cc.units_per_lsb1 + dith_ev
        U_evf = use_matrix(k_frac, sid_ev, pf_op, cc.n_units_sig, N_DEM_STATES)
        cmd_evf = mapper.encode(codes_ev.astype(np.int64), np.zeros(n_ev, dtype=np.int64), sid_ev)
        # 分数码物理值：LUT 支持实数 k（线性插值 = 部分 slice 取 m、其余 m+1）
        cmd_evf.k = k_frac.copy()  # encode 后直接覆盖为分数电平码
        e_evf = rdac.evaluate_physical(cmd_evf) - rdac.evaluate_nominal(cmd_evf)
        resB = e_evf - _awc(U_evf, w_hat)
        e_rows.append(
            {
                "映射": mode,
                "evalA_整数码残差_uV": float(np.std(resA)) * 1e6,
                "evalB_分数码残差_uV": float(np.std(resB)) * 1e6,
                "e_true_rms_uV": float(np.std(e_ev)) * 1e6,
                "改善倍数_A": float(np.std(e_ev) / max(np.std(resA), 1e-12)),
                "折_LSB20_A": float(np.std(resA) / c0.lsb_target),
                "折_LSB20_B": float(np.std(resB) / c0.lsb_target),
            }
        )
    out["calibration"] = e_rows
    out["noise_floor_uV"] = float(np.sqrt(n_noise**2 + (resolve_ra_noise(cc) / cc.g0) ** 2) * 1e6)

    # ---- 样本量反推：三种映射各算一次，暴露"秩 vs 数据量"的主次关系 ----
    rs_rows = []
    for mode in ("fixed", "dem_rotate", "permute"):
        rs_rows.append(
            {
                "映射": mode,
                **required_samples(cc, U_by_mode[mode][0], n_noise, cc.rdac_step, 300.0),
            }
        )
    out["required_samples_300ppm"] = rs_rows[-1]  # permute = 唯一可行者
    out["required_samples_all"] = rs_rows
    out["判据_校准"] = (
        "evalA(整数码)/evalB(运行态 dither 分数码) 双口径，缺一即自欺："
        "(1) fixed 校准失败 —— 假模型拟合；(2) dem_rotate（运行映射）校准在整数码下"
        "近乎完美（3 uV），但分数码把零空间失配激发出来（泄漏 ~3.6x）——"
        "'同映射零空间分量不产生误差'只在整数码上成立；"
        "(3) permute（需硬件随机选单位校准模式）估出真权重，两种评估同为 ~21 uV。"
        "**适用域（v5 审计）：这是 unary 等权 DAC 的权重估计可行性实验，"
        "不是 split ADC 的校准闭环** —— 未含主/子边界、C_C 失配、RA 测量噪声、"
        "后端量化与实际校准激励路径。139 uV -> 3~21 uV 的结论保留，"
        "但 20.7 uV 只是小于而非远小于噪声底 39.5 uV：按不相关合成 "
        "sqrt(39.5^2+20.7^2)≈44.6 uV，约损失 1 dB SNDR；若成确定性误差还需看峰值杂散。"
        "'架构成立'的最终判据应是 Null(U_cal) ⊆ Null(U_run) "
        "加 split 全链路闭环，后者是下一版工作。"
    )
    return out


def stage15_design_guide(c0: Config, s12: dict, s13: dict, s14: dict) -> dict:
    """stage15 -- 设计反推：从 SNDR/INL 目标给出各物理参数的预算。

    输入全部来自前面的实验结果，输出是"给设计者的规格行"，
    每行标注参数来源：[披露]/[拟合]/[假设]。

    Args:
        c0: 基准配置（Config）。
        s12: stage12 的结果字典（dict），提供 split 架构对比数据。
        s13: stage13 的结果字典（dict），提供动态误差数据。
        s14: stage14 的结果字典（dict），提供可观测性与秩分析数据。

    Returns:
        dict：键 'rows' 为规格行列表，每行是含 参数/需求/当前假设/来源/说明
        五个键的 dict，需求与当前假设均为带单位的字符串，来源为
        [披露]/[拟合]/[假设]/[结构] 之一。无 PASS 字段：本 stage 只做反推，
        不给通过与否的判定。
    """
    out: dict[str, list] = {"rows": []}

    def add(item, need, now, source, note=""):
        """向设计规格表追加一行（原地修改外层 out['rows']）。

        Args:
            item: 参数名（str）。
            need: 由前序 stage 反推出的需求值（str，含单位）。
            now: 当前模型假设或实测值（str，含单位）。
            source: 来源分级标签（str，[披露]/[拟合]/[假设]/[结构]）。
            note: 补充说明（str，可为空）。

        Side effects:
            向闭包外 out['rows'] 列表追加一条 dict，无返回值。
        """
        out["rows"].append(
            {"参数": item, "需求": need, "当前假设": now, "来源": source, "说明": note}
        )

    # 1. 失配（来自 v2/v4 预算结论）
    add(
        "单位失配 sigma_eps",
        "≤ 300 ppm（SNDR ≥ 93 dB 均值口径）",
        f"{c0.mismatch_sigma0*1e6:.0f} ppm [拟合] / 1117 ppm [PDK假设]",
        "[拟合]+[假设]",
        "缺口 ~3.7x -> 单位权重校准是架构可行的**待验证要求**"
        "（基于当前假设与 unary DAC 权重估计实验；split 闭环是下一版，"
        "在闭环完成前不作为正式电路规格）",
    )
    # 2. 桥接电容（stage12 锯齿）
    n_s = c0.dac_n_sub if c0.dac_arch == "split" else 8
    budget_v = 39.5e-6
    cc_max = budget_v / ((1.0 - 1.0 / n_s) * (2 * c0.v_fs / 64))
    add(
        "桥接电容 C_C 相对失配",
        f"≤ {cc_max*1e6:.0f} ppm（使锯齿 ≤ 噪声预算 39.5 µV）",
        f"{c0.dac_bridge_mismatch_sigma*1e6:.0f} ppm [假设]",
        "[假设]",
        "锯齿 DEM 无效，单参数可校准；不校准则按此匹配",
    )
    # 3. 满幅收缩（stage12）
    shr = [r for r in s12["topologies"] if r["拓扑"].startswith("split 64x8")]
    if shr:
        add(
            "桥接拓扑满幅收缩",
            f"{shr[0]['满幅收缩_pct']:.2f}% -> 量程代价 " f"{shr[0]['量程代价_dB']:.2f} dB",
            "unary 无此项",
            "[结构]",
            "收缩源于 C_C 死电容+寄生造成的电容比变化 —— **等比例放大所有电容"
            "不能恢复**（比例不变性）；对策：改 C_C 名义值/权重分配，或接受量程损失。"
            "注意此口径是码跨度差，与端点-满幅口径不完全一致",
        )
    # 4. 输入建立
    tau_in = (c0.dyn_r_source + c0.dyn_r_on) * 20.5e-12
    eps_t = 0.01
    ts_need = -tau_in * math.log(eps_t)
    add(
        "采样相时长 T_s",
        f"≥ {ts_need*1e9:.1f} ns（ε={eps_t:.0%}，τ={tau_in*1e9:.1f} ns）",
        f"{c0.dyn_t_sample_frac/c0.fs*1e9:.1f} ns [假设]",
        "[假设]",
        "一阶 RC；Ron 随码调制产生 INL（stage13）",
    )
    # 5. 参考建立
    add(
        "参考去耦/恢复",
        "使 γ·v ≤ 噪声预算的前提下确定 C_dec 与 τ_ref",
        f"C_dec={c0.dyn_c_decouple*1e9:.0f} nF, tau_ref={c0.dyn_tau_ref*1e9:.0f} ns [假设]",
        "[假设]",
        "stage13 实测该组合下的 INL/SNDR",
    )
    # 6. 数字串扰
    a_half = c0.dac_levels / 2
    cxt_max = budget_v * c_total_here(c0) / (a_half * c0.dyn_v_digital)
    add(
        "顶极板等效数字耦合",
        f"≤ {cxt_max*1e18:.1f} aF（按 A=N/2 全摆幅折算）",
        f"common {c0.dyn_c_xtalk_common*1e15:.3f} fF + unit {c0.dyn_c_xtalk_unit*1e15:.3f} fF [假设]",
        "[假设]",
        "aF 量级指向全屏蔽（**待验证要求**，非实测规格）；"
        "单位分量 DEM 可随机化。A(k)=2min(k,N-k) 是经验活动因子，"
        "非实际开关事件翻转数，不能直接反推布线耦合规格",
    )
    # 7. 开关导通电阻的码调制（stage13 rho 扫描反推的设计边界）
    rho = s13.get("rho_max_for_2p2LSB")
    if rho is not None:
        add(
            "开关导通电阻码调制 rho",
            f"≤ {rho:.3f}（**条件性估计**：257 电平 × 完整 DEM 周期的峰值口径）",
            f"{c0.dyn_ron_code_coeff:.3f} [假设]",
            "[假设]",
            f"stage13 峰值口径扫描（二分细化 3 次，该点实测峰值 "
            f"{s13.get('rho_max_INL_at_rho_max', float('nan')):.3f} LSB）："
            "默认采样相（eps≈1.5e-5）下 rho=0 的 INL 是小量，"
            "但 eps 增大时纯建立也产生码相关记忆误差（工作点性质，非普适）—— "
            "这是自举开关的量化理由。**不是收敛后的规格**：网格加密/完整逐码 "
            "DNL/INL/启动样本剔除仍可能移动它。注意 rho 定义在 R_s+R_on 总和上；"
            "若按 R_on 自身调制定义需换算 rho_tau = rho·R_on/(R_s+R_on)",
        )
    # 8. 校准观测数（stage14：先看秩，再看数据量）
    rs_all = s14.get("required_samples_all") or []
    rs = next((r for r in rs_all if r.get("feasible")), None)
    bad = [r["映射"] for r in rs_all if not r.get("feasible")]
    if rs:
        t_dedicated = rs["at_fs_40MHz_seconds"] * 1e6
        t_interleaved = t_dedicated * 2  # A/B 交织下 bank0 只见 fs/2
        add(
            "单位权重校准观测数",
            f"~{rs['n_samples']:.0f} 次 = {t_dedicated:.0f} µs"
            f"（专用校准模式口径；约束来自{rs['binding']}）",
            "stage14 实测各映射秩",
            "[结构]+[假设]",
            f"{'/'.join(bad)} 映射秩不足 → 数据再多也不可辨识（inf）；"
            f"permute 满秩，方差相对理想设计放大 {rs['cond_amp_variance']:.1f}×，"
            f"噪声界 {rs['n_noise_bound']:.0f} 次、观测冗余界 {rs['n_dof_bound']:.0f} 次"
            f"（4× 设计裕量，非物理硬下界）取大者。"
            f"若在正常 A/B 交织下只观测 bank0，时间 ×2 ≈ {t_interleaved:.0f} µs。"
            "此表基于 unary 模型的观测矩阵，非 split 闭环",
        )
    return out


def c_total_here(cfg: Config) -> float:
    """当前缩放系数下的总采样电容。

    Args:
        cfg: 模型配置（Config）。

    Returns:
        总采样电容（F），= c_total0 × cap_scale。[派生]
    """
    return cfg.c_total0 * cfg.cap_scale


# ===========================================================================
# v5.1 第三轮审计：KTC 观测尺度回归矩阵
# ===========================================================================
def ktc_scale_matrix(c0: Config, n: int = 2**14) -> dict:
    """unary/split × dither 关/开 × KTC 开/关 的 **8 组合**回归矩阵。

    背景（v5.1 三轮审计 §二）：KTC 观测尺度修复（观察 α·Δx 而非 Δx）当时只
    进了 sim_split.py，sim.py（unary 主循环）漏改 —— 两条主循环各自解释
    KTC 接口，单分支测试掩盖了遗漏。修复前 unary+dither+KTC 的
    err_to_x2 = (1/α−1)·Δx ≈ 42.7 µV @5 MHz 的确定性尺度残差（split 是
    1.0 µV）。本矩阵保证两条入口在所有组合下都回到 ADC2 量化底。

    条件：无失配 / 无热噪声 / 无动态误差 / KTC 理想带宽。

    判据分两项（均为**推导式**，不写死 µV 阈值）：
      ① ``PASS_interface``：KTC **关闭**时，两条主循环的 8→4 组合都必须回到
         ADC2 量化底 ``delta2/G/sqrt(12)`` 的 2 倍以内。这检验本 stage 的
         原始命题 —— "两入口不得各自解释 KTC 接口"。
      ② ``PASS_window``：KTC **开启**时校正项 ∝ dx 必须落在 ADC2 窗口内。
         9b 读数把残差（因而窗口）缩小 8 倍，该项在当前参数下不成立 ——
         记为真实 FAIL，给出定量理由，不调参凑 PASS。冻结基线时 KTC
         默认关闭（``Config.ktc_enable = False``），符合审计"先冻结论文
         基线、再单独评估 KTC"的推进顺序。

    Args:
        c0: 基准配置（Config）。
        n: 每组仿真的样本数（个）。

    Returns:
        dict：含 'PASS'（bool，两项判据均成立才为 True）、'判据'（str，
        中文结论）、'rows'（8 组合逐行的 err RMS，µV）等键。
        两项判据：① KTC 关闭时两入口都回到 ADC2 量化底的 2 倍以内；
        ② KTC 开启时校正项落在 ADC2 窗口内（当前参数下为真实 FAIL，不得调参凑过）。
    """
    rows = []
    for arch in ("unary", "split"):
        for dither in ("off", "sampling"):
            for ktc_on in (False, True):
                cc = _clone(
                    c0,
                    dac_arch=arch,
                    dither_mode=dither,
                    ktc_enable=ktc_on,
                    mismatch_enable=False,
                    dac_bridge_mismatch_sigma=0.0,
                    dac_parasitic_spread=0.0,
                    enable_sampling_noise=False,
                    ra_enable_noise=False,
                    dyn_input_settling=False,
                    dyn_ref_settling=False,
                    dyn_crosstalk=False,
                )
                inp = sine_input(0.7 * c0.v_fs, _coherent_fin(cc, n))
                if arch == "unary":
                    r = run_sim(cc, inp, n, rng=np.random.default_rng(c0.seed + 91))
                else:
                    from .sim_split import run_sim_split

                    r = run_sim_split(cc, inp, n, rng=np.random.default_rng(c0.seed + 91))
                rows.append(
                    {
                        "arch": arch,
                        "dither": dither,
                        "ktc": ktc_on,
                        "alpha": cc.dither_alpha,
                        "err_rms_uV": float(np.sqrt(np.mean(r.err**2))) * 1e6,
                        "overflow": float(np.mean(r.adc2_over)),
                    }
                )
    # 判据分两项，**推导式**（不再用写死的 2.0 µV —— 它曾低于 ADC2 量化底
    # 而永久 FAIL，外部审计指出后改为 delta2/G/sqrt(12) 的倍数）。
    floor_uV = c0.delta2 / c0.g_actual / math.sqrt(12.0) * 1e6
    thr = 2.0 * floor_uV
    off_rows = [r for r in rows if not r["ktc"]]
    on_rows = [r for r in rows if r["ktc"]]
    worst = max(rows, key=lambda r: r["err_rms_uV"])
    worst_off = max(off_rows, key=lambda r: r["err_rms_uV"])
    worst_on = max(on_rows, key=lambda r: r["err_rms_uV"])
    # ① 接口一致性（本 stage 的原始命题）：两条主循环在 KTC **关闭**时都必须
    #    回到量化底 —— 这检验的是"两入口是否各自解释接口"。
    pass_interface = bool(worst_off["err_rms_uV"] < thr)
    # ② 研究扩展的窗口预算：KTC **开启**时校正项 ∝ dx 必须落在 ADC2 窗口内。
    #    9b 读数把残差（因而 ADC2 窗口）缩小 8 倍，此项在当前参数下不成立 ——
    #    这是真实的设计边界，如实记为 FAIL 并给出定量理由，不调参凑 PASS。
    pass_window = bool(worst_on["overflow"] < 1e-4)
    return {
        "rows": rows,
        "阈值_uV": thr,
        "量化底_uV": floor_uV,
        "worst": worst,
        "worst_off": worst_off,
        "worst_on": worst_on,
        "PASS_interface": pass_interface,
        "PASS_window": pass_window,
        "PASS": bool(pass_interface and pass_window),
        "判据": (
            f"① 接口一致性（KTC 关）：最差 err RMS = "
            f"{worst_off['err_rms_uV']:.3f} µV < 2×量化底 {thr:.3f} µV —— "
            f"{'OK' if pass_interface else 'FAIL'}。修复前 unary+sampling+KTC "
            "为 42.683 µV，即两条主循环各自解释 KTC 接口。"
            f"② 窗口预算（KTC 开，研究扩展）：最差 err RMS = "
            f"{worst_on['err_rms_uV']:.1f} µV、ADC2 溢出率 = "
            f"{worst_on['overflow']:.1%} —— {'窗口内' if pass_window else '溢出'}。"
            "9b 读数把残差/ADC2 窗口缩小 8 倍，校正项 G_R·|dx| 相对窗口放大，"
            "该研究扩展需按新窗口重定标（缩 Δt 或加宽 ADC2），"
            "**不得调参凑 PASS**；冻结基线时 KTC 默认关闭。"
        ),
    }


# ===========================================================================
# v5.2 第四轮推进：stage16 逐相位噪声状态传递 / stage17 split 校准闭环 v1 /
#      stage18 带 dither 的采样相位电荷闭合
# ===========================================================================
def stage16_noise_transfer(c0: Config, n_mc: int = 1 << 20) -> dict:
    """stage16 -- 逐相位噪声状态传递（v5.1 三轮审计 §五）。

    把噪声保留成状态向量 q=[q_M,q_S]（方差 kT·diag(A,B)），从相位连接
    推导两条通路的组合向量：
        残差通路 a = [1, β]/C_sig（放大相节点方程直接给出）
        观测通路 b = [1, γβ]/C_sig（γ = 观测网络对子阵列噪声的覆盖比例）
    并验证三件事：
      1) Monte Carlo 协方差与解析 σ²=(a−κb)ᵀΣ(a−κb)+κ²σ_eN² 对账；
      2) γ=1 时残差回到观测通路噪声底 κ·σ_eN（a≈b，物理可观测可相消）；
      3) γ<1 时存在**结构性**残余 sqrt(aᵀΣa−(aᵀΣb)²/(bᵀΣb)) —— 调 κ 救不回来，
         只能改观测网络的连接（这是"相关随机数数值抵消"与"物理可观测抵消"
         的分界线）。
    同时确认主循环口径：sqrt(aᵀΣa) = sqrt(chi·kT/C_n,eq)（C_n,eq=C_sig²/(A+β²B)）。

    Args:
        c0: 基准配置（Config）。
        n_mc: Monte Carlo 样本数（个）。

    Returns:
        dict：'PASS'（bool）、'判据'（str）、'rows'（γ=1/0.5/0 三种观测覆盖度
        下 MC 与解析 σ_res 的对照）、'sigma_total_uV'（总采样噪声，µVrms）、
        'sigma_eN_uV'（观测通路本底，µVrms）、'beta'（桥接衰减因子，无量纲）、
        'c_sig_pF'（信号电容，pF）、'c_noise_eq_pF'（噪声等效电容，pF）。
    """
    from .dac_arch import build_split_chip
    from .noise_phase import (
        kappa_optimal,
        monte_carlo_residual,
        phase_noise_state,
        sigma_res_analytic,
        sigma_total_sampling,
        sigma_uncancellable,
    )

    out = {}
    cfg = _clone(c0, dac_arch="split")
    chip = build_split_chip(cfg)
    sigma_eN = 1e-6  # 观测通路自身噪声（输入等效，示例值 [假设]）
    st0 = phase_noise_state(chip, gamma=1.0, chi=cfg.chi)
    out["sigma_total_uV"] = sigma_total_sampling(st0) * 1e6
    from .config import K_B, TEMP_K

    out["c_noise_eq_pF"] = (cfg.chi * K_B * TEMP_K) / (sigma_total_sampling(st0) ** 2) * 1e12
    out["beta"] = st0["beta"]
    out["c_sig_pF"] = st0["c_sig"] * 1e12

    rows = []
    for gamma in (1.0, 0.5, 0.0):
        st = phase_noise_state(chip, gamma=gamma, chi=cfg.chi)
        k_opt = kappa_optimal(st)
        an = sigma_res_analytic(st, k_opt, sigma_eN)
        mc = monte_carlo_residual(
            st, k_opt, sigma_eN, n=n_mc, rng=np.random.default_rng(c0.seed + 16)
        )
        unc = sigma_uncancellable(st)
        rows.append(
            {
                "gamma": gamma,
                "kappa_opt": k_opt,
                "kappa_hat_mc": mc["kappa_hat"],
                "corr_path_obs": mc["corr_path_obs"],
                "sigma_analytic_uV": an * 1e6,
                "sigma_mc_uV": mc["sigma_mc"] * 1e6,
                "sigma_uncancellable_uV": unc * 1e6,
                "rel_err": abs(mc["sigma_mc"] - an) / an,
            }
        )
    out["rows"] = rows
    out["sigma_eN_uV"] = sigma_eN * 1e6
    worst = max(r["rel_err"] for r in rows)
    r0, r2 = rows[0], rows[-1]
    out["PASS"] = bool(
        worst < 0.03
        and abs(r0["sigma_mc_uV"] - sigma_eN * 1e6) / (sigma_eN * 1e6) < 0.05
        and r2["sigma_uncancellable_uV"] > 0.5
        and all(abs(r["kappa_hat_mc"] - r["kappa_opt"]) < 0.02 for r in rows)
    )
    out["判据"] = (
        f"三种观测覆盖度 γ=1/0.5/0 下 MC 与解析 σ_res 最大相对偏差 {worst*100:.2f}%；"
        f"γ=1 残差 = {r0['sigma_mc_uV']:.3f} µV ≈ κ·σ_eN（观测拿到正确组合，a≈b）；"
        f"γ=0 结构性残余 = {r2['sigma_uncancellable_uV']:.3f} µV"
        f"（占总采样噪声 {out['sigma_total_uV']:.1f} µV 的 "
        f"{r2['sigma_uncancellable_uV']/out['sigma_total_uV']*100:.1f}%），"
        "回归式 κ̂ 与 κ_opt 一致 —— **κ 只能消掉 b 方向上的投影**，"
        "观测网络接不全时子阵列噪声成为永久噪声底，与数据量无关。"
        "主循环 C_n,eq 口径与相位推导一致（stage16 后升级为推导值，"
        "剩余假设 = 两阵列噪声独立、顶板钳位后浮置）。"
    )
    return out


def stage17_split_calib(c0: Config, n_cal: int = 1 << 14, n_val: int = 1 << 14) -> dict:
    """stage17 -- split 校准闭环 v1：主/子**有效权重**估计（v5.1 三轮审计 §六）。

    范围（刻意收窄）：只开桥接比例误差（单位电容/寄生/C_F 全理想），
    校准算法只解决一个问题 —— 从**完整模拟链的 ADC2 输出**估计
    主/子有效权重比。物理误差 e_DAC(k_m,k_s) 在固定相位下是 (k_m,k_s)
    的仿射函数（C_sig 变化 -> 主码斜率；子权重变化 -> 子码斜率），
    所以一阶闭环 = 对 [1, k_m, k_s] 的线性回归：
        e[n] = out[n] − x_known[n] = c0 + c1·k_m[n] + c2·k_s[n] + ν[n]
    数字校正 out_cal = out − (ĉ0 + ĉ1·k_m + ĉ2·k_s)。

    防自欺的三条纪律（stage14 教训）：
      * 算法**只**使用 (out, 已知校准输入, 数字码 k) —— 真值 C_C、
        e_dac、sub_weight_relative_error 一律不进回归（只做事后对账）；
      * **out-of-sample** 验证：新频率/新幅度/新噪声实现的独立数据；
      * 整数码回归 + 锯齿幅度（k_s 结构）双口径报告。

    Args:
        c0: 基准配置（Config）。
        n_cal: 校准集样本数（个）。
        n_val: out-of-sample 验证集样本数（个）。

    Returns:
        dict：'PASS'（bool）、'判据'（str）、'theta_rel_err'（权重估计相对误差，
        无量纲）、'theta_expected_uV_per_code'（权重的理论值，µV/码）、
        'truth'（仅事后对账用的真值，不进回归）。
        纪律：算法只使用 (out, 已知输入, 数字码, dither 码)。
    """
    from .dac_arch import SplitDAC, build_split_chip
    from .sim_split import run_sim_split

    out = {}
    # 只开桥接误差：mismatch_enable=True 但 sigma0=0（单位电容全等），
    # 寄生散布=0，C_C 失配 2000 ppm（远超 stage15 预算 -> 校准是必要项）。
    # **dither 必须开（离散）**：无 dither 时 k_eq = coarse·8，k_s≡0，
    # 子码维数不被激励 —— 子权重误差被主码回归完全吸收（实测 73× 改善
    # 但 θ̂=0），既测不到也无需单独校准。sampling dither 让 k_s ∈ {−2..2}
    # 活起来，桥接误差才成为**独立可观测量** —— 这是"采样 dither 使
    # 桥接误差可校准"的架构级结论（与专利 [10] 的动机一致）。
    base = {
        "dac_arch": "split",
        "dem_enable": True,
        "mismatch_enable": True,
        "mismatch_sigma0": 0.0,
        "dac_parasitic_spread": 0.0,
        "dac_bridge_mismatch_sigma": 2.0e-3,
        "ktc_enable": False,
        "dither_mode": "sampling",
        "dither_discrete": True,
        "dyn_input_settling": False,
        "dyn_ref_settling": False,
        "dyn_crosstalk": False,
    }
    chip = build_split_chip(_clone(c0, **base))
    base_ideal = dict(base, dac_bridge_mismatch_sigma=0.0)
    chip_ideal = build_split_chip(_clone(c0, **base_ideal))
    dac = SplitDAC(_clone(c0, **base), chip)
    v_lo, v_hi = dac._nominal_endpoints()
    center, half = 0.5 * (v_lo + v_hi), 0.5 * (v_hi - v_lo)
    step0 = (v_hi - v_lo) / (dac.levels - 1)
    # 事后真值对账（不进算法）
    truth_sub_rel = chip.sub_weight_relative_error()
    truth_bridge_rel = chip.bridge_relative_error()
    theta_expected = step0 * truth_sub_rel  # V / 子码

    def _regress(err, k, dcode):
        # 基 [1, k_m, k_s, d]：k_s 斜率 = 子阵列有效权重误差；
        # d 列吸收"物理注入步长(Δ_true) vs 数字扣除步长(Δ_nom)"之差 ——
        # 它与桥接误差同源（β_t≠β_n 同时改变子权重与掩码注入步长），
        # 但 d = ks − 8·borrow 不是 (k_m,k_s) 的线性函数，必须单列。
        """对误差做 [1, k_m, k_s, d] 四列最小二乘回归，估计仿射误差模型系数。

        Args:
            err: 观测误差 out − x_known（V，形状 (n,)）。
            k: 等效 DAC 码（个，无量纲），k = k_m·n_s + k_s。
            dcode: dither 码（个，无量纲），用于吸收注入步长与扣除步长之差。

        Returns:
            三元组 (coef, km, ks)：coef 为长度 4 的回归系数向量
            [c0(V), c_{km}(V/码), c_{ks}(V/码), c_d(V/码)]；
            km、ks 为从 k 分解出的主码与子码（个，无量纲）。

        Notes:
            d 列不可省略：d = ks − 8·borrow 不是 (k_m, k_s) 的线性函数，
            但其系数与桥接误差同源，单列后才能与子权重误差解耦。
        """
        km = np.floor(k / dac.n_s)
        ks = k - km * dac.n_s
        X = np.stack([np.ones(len(k)), km, ks, np.asarray(dcode, dtype=float)], axis=1)
        coef, *_ = np.linalg.lstsq(X, err, rcond=None)
        return coef, km, ks

    def _run(chip_ref, cfg_kw, amp, fin, seed, n):
        """跑一次 split 链路仿真，用于校准/验证数据集生成。

        Args:
            chip_ref: 复用的虚拟芯片（Chip）；None 表示现场抽样。
            cfg_kw: 覆盖到基准配置上的关键字参数（dict）。
            amp: 输入正弦幅度（V，峰峰值的一半）。
            fin: 输入频率（Hz）。
            seed: 随机种子（无量纲整数）。
            n: 样本数（个）。

        Returns:
            SimResult：含 out（V）、err（V）与数字码等字段。
        """
        cc = _clone(c0, **base, **cfg_kw)
        r = run_sim_split(
            cc,
            sine_input(center + amp * half, fin),
            n,
            chip=chip_ref,
            rng=np.random.default_rng(seed),
        )
        return r

    def _sawtooth_pp(err, ks):
        """按子码分组取均值后再取极差，得到 k_s 锯齿的峰峰值。

        Args:
            err: 误差序列（V，形状 (n,)）。
            ks: 子码序列（个，无量纲，形状 (n,)）。

        Returns:
            锯齿峰峰值（V）。对每个唯一子码取误差均值，再取均值的最大最小之差。

        Notes:
            先按子码分组平均可压掉随机噪声，只留下与 k_s 结构相关的确定性锯齿。
        """
        uq = np.unique(ks)
        mu = np.array([err[ks == u].mean() for u in uq])
        return float(mu.max() - mu.min())

    results = {}
    for tag, noise_kw in (
        ("noise_off", {"enable_sampling_noise": False, "ra_enable_noise": False}),
        ("noise_on", {"enable_sampling_noise": True, "ra_enable_noise": True}),
    ):
        # ---- 校准数据（已知输入）----
        fin_cal = _coherent_fin(c0, n_cal, n_cycles=509)
        r_cal = _run(chip, noise_kw, 0.85, fin_cal, c0.seed + 171, n_cal)
        coef, km_c, ks_c = _regress(
            r_cal.err,
            np.asarray(r_cal.k, dtype=float),
            np.asarray(r_cal.sample.dither_code, dtype=float),
        )
        # 折回 DAC 口径：err = out − x ≈ −e_DAC/α -> θ_dac = −coef[ks]·α
        theta_hat = float(-coef[2] * _clone(c0, **base).dither_alpha)

        # ---- out-of-sample 验证（新频率/新幅度/新噪声实现）----
        fin_val = _coherent_fin(c0, n_val, n_cycles=331)
        r_val = _run(chip, noise_kw, 0.70, fin_val, c0.seed + 9191, n_val)
        e_before = np.asarray(r_val.err, dtype=float)
        k_val = np.asarray(r_val.k, dtype=float)
        d_val = np.asarray(r_val.sample.dither_code, dtype=float)
        km_v = np.floor(k_val / dac.n_s)
        ks_v = k_val - km_v * dac.n_s
        e_after = e_before - (coef[0] + coef[1] * km_v + coef[2] * ks_v + coef[3] * d_val)
        # 噪声地板参照：同一输入/种子下**理想芯片**（无桥接误差）的 err RMS
        r_floor = _run(chip_ideal, noise_kw, 0.70, fin_val, c0.seed + 9191, n_val)
        floor = float(np.sqrt(np.mean(np.asarray(r_floor.err) ** 2))) * 1e6
        rms_b = float(np.sqrt(np.mean(e_before**2))) * 1e6
        rms_a = float(np.sqrt(np.mean(e_after**2))) * 1e6
        saw_b = _sawtooth_pp(e_before, ks_v) * 1e6
        saw_a = _sawtooth_pp(e_after, ks_v) * 1e6
        results[tag] = {
            "theta_hat_uV_per_code": theta_hat * 1e6,
            "err_rms_before_uV": rms_b,
            "err_rms_after_uV": rms_a,
            "floor_uV": floor,
            "after_over_floor": rms_a / floor,
            "improvement": rms_b / max(rms_a, 1e-12),
            "sawtooth_pp_before_uV": saw_b,
            "sawtooth_pp_after_uV": saw_a,
            "coef": [float(c) for c in coef],
        }

    th = results["noise_off"]["theta_hat_uV_per_code"]
    th_err = abs(th - theta_expected * 1e6) / abs(theta_expected * 1e6)
    imp_off = results["noise_off"]["improvement"]
    r_on = results["noise_on"]
    out.update(results)
    out["theta_expected_uV_per_code"] = theta_expected * 1e6
    out["theta_rel_err"] = th_err
    out["truth"] = {
        "bridge_rel_err_ppm": truth_bridge_rel * 1e6,
        "sub_weight_rel_err_ppm": truth_sub_rel * 1e6,
    }
    out["PASS"] = bool(
        th_err < 0.05
        and imp_off > 5.0
        and results["noise_off"]["err_rms_after_uV"] < 2.0
        and r_on["after_over_floor"] < 1.10
    )
    out["判据"] = (
        f"桥接失配 {truth_bridge_rel*1e6:.0f} ppm -> 子权重相对误差 "
        f"{truth_sub_rel*1e6:.0f} ppm（理论 (1−1/n_s)·δ_C）。"
        f"闭环估计 θ̂ = {th:.3f} µV/子码 vs 理论 {theta_expected*1e6:.3f}"
        f"（偏差 {th_err*100:.2f}%）。out-of-sample 验证：err RMS "
        f"{results['noise_off']['err_rms_before_uV']:.1f} -> "
        f"{results['noise_off']['err_rms_after_uV']:.2f} µV（{imp_off:.0f}×，噪声关）；"
        f"噪声开 {r_on['err_rms_before_uV']:.1f} -> {r_on['err_rms_after_uV']:.2f} µV"
        f" = 理想芯片地板 {r_on['floor_uV']:.2f} µV 的 "
        f"{r_on['after_over_floor']*100:.1f}%；"
        f"k_s 锯齿峰峰 {results['noise_off']['sawtooth_pp_before_uV']:.1f} -> "
        f"{results['noise_off']['sawtooth_pp_after_uV']:.2f} µV。"
        "算法只见 (out, 已知输入, 数字码, dither 码) —— C_C 真值与 e_dac 未进回归。"
        "**架构结论**：无 dither 时 k_s≡0，子权重误差被主码校准吸收、"
        "不可独立观测；sampling dither 激励子码维数后桥接误差才可估可校。"
        "d 列吸收'物理注入步长 vs 数字扣除步长'之差（与桥接误差同源）。"
        "适用域：一阶闭环只解**有效权重**（仿射模型），单位级失配的可观测性"
        "仍是 stage14 的秩问题；DEM 置换在单位电容全等时不改变 Q_D。"
    )
    return out


def stage18_dither_charge_closure(c0: Config, n: int = 1 << 13) -> dict:
    """stage18 -- 带 dither 的采样相位电荷闭合 + 离散掩码（v5.1 三轮审计 §四）。

    第三轮审计指出：电荷闭合测试关掉了 dither，证明的是无 dither 相位；
    α 修正后还需证明 **α 本身与实际开关网络一致**。本实验补上这一层：
    把掩码（bank 末尾 2D 个单位，D+d_u 接 +V_FS、D−d_u 接 −V_FS）写进
    逐相位节点方程（charge_ref.ref_ra_charge_dither_nodal，逐单位 w/b
    组装 2x2 系统），验证恒等式
        C_F*v_R = C_sig,sample*x + w_bank*Q_mask − Q_D(k)
    与主循环逐样本一致，并确认四件事由**同一份掩码**生成：
        信号电荷（α） / dither 电荷（注入 LUT） / 输入负载 / 噪声传递（不变）。
    同时启用**离散** dither 码（整数 d_u）—— 连续插值只是数学基准。

    Args:
        c0: 基准配置（Config）。
        n: 每组仿真的样本数（个）。

    Returns:
        dict：'PASS'（bool，闭式/节点矩阵/主循环三者电荷闭合）、'判据'（str）、
        'dither_codes'（实际使用的离散 dither 码集合，个）。
        判据报告闭式 vs 节点矩阵偏差（C）、α 配置值 vs 掩码推导值、
        注入尺度（µV/码）、离散与连续 dither 的 SNDR（dB）对比。
    """
    from .charge_ref import (
        dither_mask_charge,
        ref_ra_charge_dither_closed,
        ref_ra_charge_dither_nodal,
    )
    from .dac_arch import SplitDAC, build_split_chip
    from .sim_split import run_sim_split

    out = {}
    cc = _clone(
        c0,
        dac_arch="split",
        dither_mode="sampling",
        dither_discrete=True,
        mismatch_enable=False,
        enable_sampling_noise=False,
        ra_enable_noise=False,
        ktc_enable=False,
        dem_enable=False,
        dyn_input_settling=False,
        dyn_ref_settling=False,
        dyn_crosstalk=False,
    )
    chip = build_split_chip(cc)
    dac = SplitDAC(cc, chip)
    v_lo, v_hi = dac._nominal_endpoints()
    center, half = 0.5 * (v_lo + v_hi), 0.5 * (v_hi - v_lo)
    inp = sine_input(center + 0.80 * half, _coherent_fin(cc, n))
    r = run_sim_split(cc, inp, n, chip=chip, rng=np.random.default_rng(c0.seed + 18))

    k = np.asarray(r.k, dtype=float)
    km = np.floor(k / dac.n_s)
    ks = k - km * dac.n_s
    x1 = np.asarray(r.sample.x1, dtype=float)
    d_u = np.asarray(r.sample.dither_code, dtype=float)
    order = np.arange(dac.n_m), np.arange(dac.n_s)  # DEM 关 -> 恒等顺序
    nd_mask = cc.dither_units_total
    c_sig = chip.c_sig_true()
    beta = chip.beta_true()

    # (a) 闭式 vs 节点矩阵（每个样本独立组装，不含任何共享代数式）
    v_closed = ref_ra_charge_dither_closed(
        chip,
        cc.v_fs,
        order[0],
        order[1],
        km,
        ks,
        x1,
        np.round(d_u).astype(int),
        cc.dither_split_bank,
        nd_mask,
    )
    v_nodal = ref_ra_charge_dither_nodal(
        chip,
        cc.v_fs,
        order[0],
        order[1],
        km,
        ks,
        x1,
        np.round(d_u).astype(int),
        cc.dither_split_bank,
        nd_mask,
    )
    d_cn = float(np.max(np.abs(v_closed - v_nodal)))

    # (b) 主循环 vs 节点方程（端到端：采样电荷 + dither 电荷 + DAC 电荷）
    #     主循环口径：C_sig*residue；节点口径：C_F*v_R
    d_sim = float(np.max(np.abs(c_sig * np.asarray(r.residue) - v_nodal)))

    # (c) α 与注入尺度由同一掩码生成
    Q_mask, C_mask = dither_mask_charge(chip, cc.v_fs, 0, cc.dither_split_bank, nd_mask)
    w_bank = beta if cc.dither_split_bank == "sub" else 1.0
    alpha_mask = (c_sig - w_bank * C_mask) / c_sig
    d_alpha = abs(cc.dither_alpha - alpha_mask)
    step_lut = (
        w_bank
        * cc.v_fs
        * 2.0
        * float((chip.C_sub if cc.dither_split_bank == "sub" else chip.C_main)[-nd_mask:].mean())
        / c_sig
    )

    # (d) 离散 vs 连续 dither 的动态指标（噪声开）
    def _sndr(disc):
        """在关闭全部非目标误差项的前提下测一次 SNDR，用于 dither 离散性对照。

        Args:
            disc: 是否启用离散 dither 码（bool）。

        Returns:
            SNDR（dB）。配置固定为 split + sampling dither，
            失配/KTC/DEM/建立/参考恢复/串扰全部关闭，只留 dither 机制本身。
        """
        c2 = _clone(
            c0,
            dac_arch="split",
            dither_mode="sampling",
            dither_discrete=disc,
            mismatch_enable=False,
            ktc_enable=False,
            dem_enable=False,
            dyn_input_settling=False,
            dyn_ref_settling=False,
            dyn_crosstalk=False,
        )
        rr = run_sim_split(
            c2,
            sine_input(center + 0.80 * half, _coherent_fin(c2, 1 << 14)),
            1 << 14,
            chip=build_split_chip(c2),
            rng=np.random.default_rng(c0.seed + 181),
        )
        m = sine_fit_metrics(rr.out, c2.fs, _coherent_fin(c2, 1 << 14))
        return m["SNDR_dB"], float(np.std(rr.err)) * 1e6

    sndr_disc, err_disc = _sndr(True)
    sndr_cont, err_cont = _sndr(False)

    out.update(
        {
            "closed_vs_nodal_max_C": d_cn,
            "sim_vs_nodal_max_C": d_sim,
            "alpha_cfg": cc.dither_alpha,
            "alpha_mask": alpha_mask,
            "alpha_diff": d_alpha,
            "step_lut_V": step_lut,
            "step0_V": (v_hi - v_lo) / (dac.levels - 1),
            "c_mask_fF": C_mask * 1e15,
            "c_load_pF": (chip.A + chip.B - C_mask) * 1e12,
            "c_noise_unchanged_note": (
                "掩码单位采样相接 ±V_FS 仍贡献 kT/C，且以同一 β 权重进入残差"
                " -> C_n,eq 不变，SNDR 代价 = −20·log10(α) dB"
            ),
            "sndr_discrete_dB": sndr_disc,
            "err_rms_discrete_uV": err_disc,
            "sndr_continuous_dB": sndr_cont,
            "err_rms_continuous_uV": err_cont,
            "dither_codes": sorted(set(np.round(d_u).astype(int).tolist())),
        }
    )
    out["PASS"] = bool(
        d_cn < 1e-21 and d_sim < 1e-18 and d_alpha < 1e-12 and abs(sndr_disc - sndr_cont) < 1.0
    )
    out["判据"] = (
        f"闭式 vs 节点矩阵最大偏差 {d_cn:.2e} C；主循环 vs 节点方程 "
        f"{d_sim:.2e} C（逐样本端到端：信号电荷+掩码 dither 电荷+DAC 电荷）。"
        f"α(配置) = {cc.dither_alpha:.9f} vs 掩码推导 {alpha_mask:.9f}"
        f"（差 {d_alpha:.1e}）；注入尺度 = {step_lut*1e6:.3f} µV/码 = Δ_nom。"
        f"负载口径扣除掩码电容 {C_mask*1e15:.1f} fF；噪声等效电容不变"
        f"（SNDR 代价 = −20log10(α) = {-20*np.log10(cc.dither_alpha):.3f} dB）。"
        f"离散 dither（码集 {out['dither_codes']}）SNDR {sndr_disc:.2f} dB "
        f"vs 连续基准 {sndr_cont:.2f} dB。"
        "四份量（信号/dither/负载/噪声）现在由同一份掩码生成。"
    )
    return out


# ==========================================================================
# stage19 -- v6 整体信号流（pipeline.py）：退化等价 + SADC 补偿窗口
#            + slice 带宽失配交织杂散与 8/18 洗牌
# ==========================================================================
def stage19_pipeline(c0: Config, n: int = 2**13) -> dict:
    """stage19 -- 整体 ADC 信号流参考实现（pipeline.py）的三组验收。

    (1) **退化等价**：动态/噪声/dither/失配全关时，pipeline 与 sim_split
        输出逐位一致 —— 两条主循环共享同一物理，不允许分叉。
    (2) **SADC 误差自动补偿窗口**（[09]1.3 的定量版本）：粗码误差把残差
        推出名义 bin，只要 vra 仍在 ADC2 窗口内，输出**完全**不受影响；
        窗口 = ADC2 余量/G = 0.3V/32 = 9.375 mV ≈ 0.1·Δ1 —— 这就是
        论文 "quantizer sDAC 与 RDAC >11b matching" 需求的定量出处。
    (3) **slice 带宽失配 -> 交织杂散 -> 8/18 洗牌打散**：
        slice 间 τ 失配 + 固定 A/B 交替 -> f_S/2±f_IN 固定杂散；
        8/18 随机洗牌（ShuffledScheduler）把杂散能量打散进噪声底
        （论文："shuffling of the sampling DACs to spread the residual
        interleaving tones"）。
    (4) **timing skew（交织误差四件套之 timing，逐字审计补齐）**：
        slice 间采样时刻偏移 δt_i -> 误差 = mean(δt_i)·dx/dt（与带宽失配
        正交：后者 ∝ (x−v_top)，前者 ∝ dx/dt）。固定两组 -> f_S/2−f_IN
        杂散；2×σ_t -> +6 dB；解析 err_rms = σ_t/√8·rms(dx/dt)；
        PPT p.21-22 锚点：10 ps -> 杂散高出量化底 13 dB（中心 f_S/2−fIN）。

    Args:
        c0: 基准配置（Config）。
        n: 每组仿真的样本数（个）。

    Returns:
        dict：'PASS'（bool）、'判据'（str）、'bitwise_equal'（退化等价性）、
        'sadc_window'（SADC 误差补偿窗口，V 与等效 LSB）、
        'interleave'（slice 带宽失配杂散与洗牌抑制，dB）、
        'timing_skew'（timing 误差的解析/实测对照，µVrms 与 dB）、
        'digital_conservation'（数字侧守恒校验）。
    """
    from .digital_core import DigitalCore
    from .metrics import _bh4
    from .pipeline import run_pipeline
    from .scheduler import Scheduler, ShuffledScheduler
    from .sim_split import run_sim_split

    out = {}
    base = {
        "dac_arch": "split",
        "dither_mode": "off",
        "ktc_enable": False,
        "mismatch_enable": False,
        "enable_sampling_noise": False,
        "ra_enable_noise": False,
        "dyn_ref_settling": False,
        "dyn_crosstalk": False,
        "sadc_mismatch_enable": False,
        "sadc_offset": 0.0,
        "sadc_rdac_gain_mismatch": 0.0,
    }
    fin = _coherent_fin(c0, n)
    inp = sine_input(0.7 * c0.v_fs, fin)

    # ---- (1) 退化等价（逐位）----
    cc = _clone(c0, **base, dyn_input_settling=False)
    r1 = run_sim_split(cc, inp, n, rng=np.random.default_rng(c0.seed + 19))
    r2 = run_pipeline(cc, inp, n, rng=np.random.default_rng(c0.seed + 19))
    bitwise_equal = bool(np.array_equal(r1.out, r2.out))
    out["bitwise_equal"] = bitwise_equal

    # ---- (2) SADC 补偿窗口 ----
    margin_v = float(np.mean([c0.adc2_v_max - c0.g0 * c0.delta1, -c0.adc2_v_min]))
    window_v = margin_v / c0.g0
    rows = []
    r_base = r2
    base_rms = float(np.sqrt(np.mean(r_base.err**2)))
    # 扫描点**由预测窗口推导**，不写死。历史教训（外部审计 B17）：
    # 旧实现写死 (1,5,9,9.3,9.5,12,20) mV —— 那是 b1=6（Δ1=93.75 mV，
    # 窗口 9.375 mV）时代的网格；9b 读数把窗口缩到 ~1.17 mV 后，
    # 全部扫描点都落在窗口之外，comp_max 退化成 0，判据永久 FAIL。
    # 现在按窗口的 0.25/0.5/0.75/0.95/1.05/1.5/3.0 倍取样，
    # 保证既有"窗口内全补偿"也有"窗口外溢出"的对照点，判据跨读数有效。
    w_mv = window_v * 1e3
    scan_mv = tuple(round(f * w_mv, 4) for f in (0.25, 0.5, 0.75, 0.95, 1.05, 1.5, 3.0))
    for off_mv in scan_mv:
        cc = _clone(c0, **{**base, "sadc_offset": off_mv * 1e-3}, dyn_input_settling=False)
        r = run_pipeline(cc, inp, n, rng=np.random.default_rng(c0.seed + 19))
        rms = float(np.sqrt(np.mean(r.err**2)))
        rows.append(
            {
                "offset_mV": off_mv,
                "err_rms_uV": rms * 1e6,
                "over_rate": float(np.mean(r.adc2_over)),
                "compensated": bool(rms < base_rms * 1.05 and np.mean(r.adc2_over) < 1e-4),
            }
        )
    # 窗口边界：最后一个 compensated 行的 offset
    comp_rows = [r for r in rows if r["compensated"]]
    comp_max = max(r["offset_mV"] for r in comp_rows) if comp_rows else 0.0
    # 判据：测量到的补偿边界应落在预测窗口的 [0.75, 1.05] 倍之内。
    window_ok = (0.75 * w_mv) <= comp_max <= (1.05 * w_mv)
    out["sadc_window"] = {
        "rows": rows,
        "predicted_window_mV": w_mv,
        "measured_window_mV": comp_max,
        "scan_mV": list(scan_mv),
        "window_ok": bool(window_ok),
        "note": (
            "scan grid derived from the predicted "
            "window so the test stays valid across "
            "stage-1 resolution readings."
        ),
    }

    # ---- (3) slice 带宽失配 -> 交织杂散 -> 洗牌打散 ----
    def _spur_db(err, fs, f_spur, ref_abs):
        """取误差序列在指定杂散频率处的谱线幅度（dB，相对于给定基准）。

        Args:
            err: 误差序列（V，形状 (n,)）。
            fs: 采样率（Hz）。
            f_spur: 目标杂散频率（Hz）。
            ref_abs: 参考幅度（V），用于归一化。

        Returns:
            该频点的相对杂散幅度（dB）。窗函数用 4 项 Blackman-Harris。
        """
        spec = np.abs(np.fft.rfft(err * _bh4(len(err))))
        b = int(round(f_spur / fs * len(err)))
        return float(20 * np.log10(spec[b] / ref_abs + 1e-30))

    tone_rows = []
    for spread, shuffled in ((0.0, False), (0.3, False), (0.3, True), (0.6, False), (0.6, True)):
        cc = _clone(
            c0, **base, dyn_input_settling=True, dyn_ron_code_coeff=0.0, slice_bw_spread=spread
        )
        if shuffled:
            # dem_mode 与调度器必须一致：make_scheduler 会拒绝"配置说一种
            # 调度、调用方给另一种"的组合（外部复核 2026-09-11 第三轮）。
            cc = _clone(cc, dem_mode="permute")
            sched = ShuffledScheduler(cc, np.random.default_rng(c0.seed + 555))
        else:
            sched = Scheduler(cc)
        r = run_pipeline(cc, inp, n, rng=np.random.default_rng(c0.seed + 19), scheduler=sched)
        # 基准 = 残差谱最大分量（eps*x 增益误差项），杂散相对它报 dB
        spec = np.abs(np.fft.rfft(r.err * _bh4(len(r.err))))
        tone_rows.append(
            {
                "spread": spread,
                "shuffled": shuffled,
                "err_rms_uV": float(np.sqrt(np.mean(r.err**2)) * 1e6),
                "spur_fs2_fin_dB": _spur_db(r.err, cc.fs, cc.fs / 2 - fin, float(np.max(spec))),
            }
        )
    d0 = next(t for t in tone_rows if t["spread"] == 0.0)["spur_fs2_fin_dB"]
    fix30 = next(t for t in tone_rows if t["spread"] == 0.3 and not t["shuffled"])
    shuf30 = next(t for t in tone_rows if t["spread"] == 0.3 and t["shuffled"])
    spread_db = fix30["spur_fs2_fin_dB"] - shuf30["spur_fs2_fin_dB"]
    out["interleave"] = {
        "rows": tone_rows,
        "floor_dB": d0,
        "fixed_spur_dB": fix30["spur_fs2_fin_dB"],
        "shuffled_spur_dB": shuf30["spur_fs2_fin_dB"],
        "spur_reduction_dB": spread_db,
    }

    # ---- (4) timing skew（论文交织误差四件套之 timing；PPT p.21-22 锚点）----
    # 机制：slice i 在 t_n + δt_i 采样 -> 误差 = mean(δt_i over 采集组)·dx/dt。
    # 固定 A/B 两组 -> δ̄ 逐样本 (−1)^n 调制 -> f_S/2±f_IN 杂散；
    # 解析 RMS：E[δ̄²] = σ_t²/8（8 选 18）-> err_rms ≈ σ_t/√8 · 2πf·A/√2。
    skew_rows = []
    base_skew_cfg = {"dyn_input_settling": True, "dyn_ron_code_coeff": 0.0, "slice_bw_spread": 0.0}
    # 绝对口径：杂散幅度 = 2·|X[b]|/Σw（BH 窗相干增益恢复），单位 µV。
    # 不用"相对最大谱分量"——固定组下杂散本身就是最大分量，比值恒为 0 dB，
    # 2×σ_t 标度检验会失效（实测教训）。
    _w4 = _bh4(n)
    _wsum4 = float(np.sum(_w4))
    _b_img = int(round((c0.fs / 2 - fin) / c0.fs * n))

    def _tone_uv(err):
        """取误差序列在预计算交织镜像频点处的单音幅度。

        Args:
            err: 误差序列（V，形状 (n,)）。

        Returns:
            镜像杂散幅度（µV）。窗函数与频点索引由外层闭包给定（4 项 BH）。
        """
        spec = np.fft.rfft(err * _w4)
        return float(2.0 * np.abs(spec[_b_img]) / _wsum4 * 1e6)

    for skew_ps, shuffled in (
        (0.0, False),
        (5.0, False),
        (10.0, False),
        (20.0, False),
        (10.0, True),
    ):
        cc = _clone(c0, **base, **base_skew_cfg, slice_timing_skew_s=skew_ps * 1e-12)
        if shuffled:
            cc = _clone(cc, dem_mode="permute")  # 与传入的洗牌调度器保持一致
            sched = ShuffledScheduler(cc, np.random.default_rng(c0.seed + 777))
        else:
            sched = Scheduler(cc)
        r = run_pipeline(cc, inp, n, rng=np.random.default_rng(c0.seed + 19), scheduler=sched)
        spec_abs = np.abs(np.fft.rfft(r.err * _w4))
        skew_rows.append(
            {
                "skew_ps": skew_ps,
                "shuffled": shuffled,
                "err_rms_uV": float(np.sqrt(np.mean(r.err**2)) * 1e6),
                "tone_fs2_fin_uV": _tone_uv(r.err),
                "tone_is_dominant": bool(
                    int(np.argmax(spec_abs)) == _b_img and skew_ps > 0 and not shuffled
                ),
            }
        )
    # 解析 RMS（扣除 skew=0 基线后与 (σ/√8)·rms(dx/dt) 对比）
    amp = 0.7 * c0.v_fs
    slope_rms = 2 * np.pi * fin * amp / np.sqrt(2)
    e0 = next(r for r in skew_rows if r["skew_ps"] == 0.0)["err_rms_uV"]
    e10 = next(r for r in skew_rows if r["skew_ps"] == 10.0 and not r["shuffled"])["err_rms_uV"]
    analytic10 = (10e-12 / np.sqrt(8) * slope_rms) * 1e6
    resid_quad = max(e10**2 - e0**2, 0.0)
    _t = {r["skew_ps"]: r for r in skew_rows}
    _t10f = next(r for r in skew_rows if r["skew_ps"] == 10.0 and not r["shuffled"])
    _t10s = next(r for r in skew_rows if r["skew_ps"] == 10.0 and r["shuffled"])
    _t20f = next(r for r in skew_rows if r["skew_ps"] == 20.0 and not r["shuffled"])
    out["timing_skew"] = {
        "rows": skew_rows,
        "analytic_rms_uV": analytic10,
        "measured_excess_rms_uV": float(np.sqrt(resid_quad)),
        "analytic_ratio": float(np.sqrt(resid_quad) / analytic10),
        "tone_is_dominant": bool(_t10f["tone_is_dominant"]),
        "tone_scaling_2x": float(_t20f["tone_fs2_fin_uV"] / max(_t10f["tone_fs2_fin_uV"], 1e-30)),
        "spur_above_floor_dB": float(
            20 * np.log10(max(_t10f["tone_fs2_fin_uV"], 1e-30) / max(e0, 1e-30))
        ),
        "shuffle_reduction_dB": float(
            20
            * np.log10(max(_t10f["tone_fs2_fin_uV"], 1e-30) / max(_t10s["tone_fs2_fin_uV"], 1e-30))
        ),
    }

    # ---- 数字核不变量 ----
    core = DigitalCore(c0)
    out["digital_conservation"] = core.verify_nominal_conservation()

    _sk = out["timing_skew"]
    out["PASS"] = bool(
        bitwise_equal
        and out["digital_conservation"]["ok"]
        and window_ok
        and fix30["spur_fs2_fin_dB"] - d0 > 30.0
        and spread_db > 20.0
        # timing skew：杂散位于 f_S/2−f_IN 且为最大谱分量（位置检验）；
        # 2×σ_t -> 杂散幅度 2×（±20%）；解析 RMS 偏差 <30%；洗牌抑制 >10dB
        and _sk["tone_is_dominant"]
        and abs(_sk["tone_scaling_2x"] - 2.0) < 0.4
        and abs(_sk["analytic_ratio"] - 1.0) < 0.30
        and _sk["shuffle_reduction_dB"] > 10.0
    )
    out["判据"] = (
        f"① 退化等价：pipeline 与 sim_split 输出逐位一致 = {bitwise_equal}。"
        f"② SADC 补偿窗口：实测 ≤ {comp_max:.1f} mV 全补偿、> 窗口即溢出，"
        f"与理论 (ADC2 余量)/G = {window_v*1e3:.2f} mV ≈ 0.1·Δ1 一致 —— "
        "论文 quantizer/RDAC '>11b matching' 需求的定量出处。"
        f"③ slice 带宽失配 {fix30['spread']*100:.0f}%：固定两组在 f_S/2−f_IN "
        f"产生 {fix30['spur_fs2_fin_dB']:.1f} dB 杂散（本底 {d0:.1f} dB）；"
        f"8/18 洗牌后降到 {shuf30['spur_fs2_fin_dB']:.1f} dB"
        f"（降 {spread_db:.1f} dB），err RMS "
        f"{fix30['err_rms_uV']:.1f} -> {shuf30['err_rms_uV']:.1f} µV —— "
        "论文 'shuffling ... to spread the residual interleaving tones' "
        "的定量复现。数字核名义守恒 = "
        f"{out['digital_conservation']['ok']}。"
        f"④ timing skew（交织误差四件套之 timing）：固定两组 10 ps 在 "
        f"f_S/2−f_IN 产生 {_sk['spur_above_floor_dB']:.1f} dB 高于 skew=0 "
        f"误差底的杂散且为最大谱分量"
        f"（PPT p.21 锚点：10 ps -> 高出量化底 13 dB；本模型 fin≈{fin/1e6:.1f} MHz "
        f"近 Nyquist，斜率项 ∝f_IN 故绝对值偏高，条件依赖）；"
        f"2×σ_t -> 杂散幅度 ×{_sk['tone_scaling_2x']:.2f}（理论 2.00）；"
        f"解析 err_rms = σ_t/√8·rms(dx/dt) = {_sk['analytic_rms_uV']:.1f} µV，"
        f"实测超出 {_sk['measured_excess_rms_uV']:.1f} µV"
        f"（比值 {_sk['analytic_ratio']:.2f}）；"
        f"8/18 洗牌抑制 {_sk['shuffle_reduction_dB']:.1f} dB —— "
        "论文 'timing mismatch minimized by collocating ... ~0.6ps residual' "
        "的必要性由此定量化。"
    )
    return out


# =====================================================================
# v6.1 -- 逐字审计剩余缺口闭合（stage20-24）
# =====================================================================


def _psd_logfit(fb, ps, f_lo, f_hi, groups_per_dec=12):
    """几何分箱平滑后的 log-log 斜率拟合（dB/decade）。

    矩形周期图每个 bin 是 2 自由度 chi^2 -> 10log10 涨落 std≈5.6 dB，
    直接 polyfit 的斜率不确定度 ~1 dB/dec；分箱平均把方差压到 1/组内 bin 数。

    **配对口径（首版教训）**：FFT bin 线性分布，组内低频 bin 远多于高频；
    若用 RMS 频率配对组均值 PSD，对 1/f 谱系统偏陡（精确 1/f 输入实测
    −20 dB/dec）。改用**调和平均频率** f_h = 1/mean(1/f)：mean(psd)=C/f
    的组均值 = C·mean(1/f) = C/f_h，(f_h, C/f_h) 配对对 1/f 与白谱均无偏。

    Args:
        fb: 频率轴（Hz，正频部分）。
        ps: 功率谱密度（V²/Hz，与 fb 同长）。
        f_lo: 拟合频带下界（Hz）。
        f_hi: 拟合频带上界（Hz）。
        groups_per_dec: 每十倍频程的几何分箱数（个，无量纲）。

    Returns:
        log-log 斜率（dB/decade）。分箱后用调和平均频率配对组均值 PSD，
        对 1/f 谱与白谱均无偏；直接 polyfit 或改用 RMS 频率配对会系统性偏陡。
    """
    m = (fb >= f_lo) & (fb <= f_hi)
    fb_m, ps_m = fb[m], ps[m]
    n_groups = max(int(groups_per_dec * np.log10(f_hi / f_lo)), 4)
    edges = np.logspace(np.log10(fb_m[0]), np.log10(fb_m[-1]), n_groups + 1)
    idx = np.clip(np.digitize(fb_m, edges) - 1, 0, n_groups - 1)
    fx, px = [], []
    for gi in range(n_groups):
        sel = idx == gi
        if sel.sum() >= 2:
            fx.append(1.0 / np.mean(1.0 / fb_m[sel]))
            px.append(ps_m[sel].mean())
    p = np.polyfit(np.log10(fx), 10 * np.log10(np.asarray(px)), 1)
    return float(p[0])


def stage20_interleave_offset(c0: Config, n: int = 2**13) -> dict:
    """stage20 -- 交织误差四件套收官：逐 slice 输入参考 offset（v6.1 逐字审计）。

    论文 [00] 原文："offset, gain, timing and bandwidth mismatch artefacts
    of the DACs"。gain/bandwidth/timing 三件已在 stage19 覆盖（后两者 ->
    f_S/2±f_IN 信号相关杂散）；offset 是**常数型**误差：
        x_hold = x + V_os,i  ->  样本级误差 = mean(V_os[采集组])
    固定 A/B 两组 -> (−1)^n 调制的**常数** -> 杂散在 **f_S/2** 且**不随
    f_IN 平移** —— 与另外三件（f_S/2±f_IN）构成判别性指纹。

    验收：(1) 杂散位于 f_S/2 且为最大谱分量、f_S/2±f_IN 处无峰；
    (2) 幅度 ∝ σ_os（同种子下精确线性 2×）；(3) err_rms/σ_os 落在
    解析值 σ/√8 = 0.354 的统计涨落带内；(4) f_IN 无关性（skew 则 ∝f_IN）；
    (5) 8/18 洗牌抑制 >10 dB。

    Args:
        c0: 基准配置（Config）。
        n: 每组仿真的样本数（个）。

    Returns:
        dict：'PASS'（bool，五项验收全过）、'判据'（str）、'rows'（多种子结果）、
        'fs2_dominant'（f_S/2 处是否为最大谱分量）、
        'img_not_dominant'（f_S/2±f_IN 处是否无峰）、
        'scaling_2x'（σ_os 加倍时杂散幅度倍数，无量纲，理论 2.0）、
        'err_rms_over_sigma'（误差 rms 与 σ_os 之比，无量纲，解析 0.354）、
        'fin_independence'（两个输入频率下的杂散幅度比，无量纲）、
        'shuffle_reduction_dB'（8/18 洗牌抑制量，dB）。
    """
    from .metrics import _bh4
    from .pipeline import run_pipeline
    from .scheduler import Scheduler, ShuffledScheduler

    out = {}
    base = {
        "dac_arch": "split",
        "dither_mode": "off",
        "ktc_enable": False,
        "mismatch_enable": False,
        "enable_sampling_noise": False,
        "ra_enable_noise": False,
        "dyn_input_settling": False,
        "dyn_ref_settling": False,
        "dyn_crosstalk": False,
        "sadc_offset": 0.0,
        "sadc_rdac_gain_mismatch": 0.0,
        "slice_bw_spread": 0.0,
        "slice_timing_skew_s": 0.0,
    }
    fin1 = _coherent_fin(c0, n)
    fin2 = c0.fs * 331.0 / n  # 第二个相干频率（f_IN 无关性检验）
    _w = _bh4(n)
    _wsum = float(np.sum(_w))
    _b_half = n // 2  # f_S/2 的 bin
    _b_img1 = int(round((c0.fs / 2 - fin1) / c0.fs * n))
    _b_img2 = int(round((c0.fs / 2 - fin2) / c0.fs * n))

    def _amp_at(err, b):
        """取误差序列在指定 bin 处的单音幅度。

        Args:
            err: 误差序列（V，形状 (n,)）。
            b: FFT bin 索引（个，无量纲）。

        Returns:
            该 bin 的单音幅度（µV），已按窗函数增益归一。
        """
        return float(2.0 * abs(np.fft.rfft(err * _w)[b]) / _wsum * 1e6)

    rows = []
    for sig_uv, shuffled, fin in (
        (0.0, False, fin1),
        (25.0, False, fin1),
        (50.0, False, fin1),
        (100.0, False, fin1),
        (50.0, True, fin1),
        (50.0, False, fin2),
    ):
        cc = _clone(c0, **base, slice_offset_sigma_v=sig_uv * 1e-6)
        if shuffled:
            cc = _clone(cc, dem_mode="permute")  # 与传入的洗牌调度器保持一致
            sched = ShuffledScheduler(cc, np.random.default_rng(c0.seed + 555))
        else:
            sched = Scheduler(cc)
        r = run_pipeline(
            cc,
            sine_input(0.7 * c0.v_fs, fin),
            n,
            rng=np.random.default_rng(c0.seed + 20),
            scheduler=sched,
        )
        spec = np.abs(np.fft.rfft(r.err * _w))
        spec_ex = spec.copy()
        spec_ex[0] = 0.0  # DC = 两组 bank 均值的公共分量（固定失调，
        # 物理真实但不属于交织杂散指纹）
        b_img = _b_img1 if fin == fin1 else _b_img2
        rows.append(
            {
                "sigma_os_uV": sig_uv,
                "shuffled": shuffled,
                "fin_MHz": fin / 1e6,
                "err_rms_uV": float(np.sqrt(np.mean(r.err**2)) * 1e6),
                "tone_fs2_uV": _amp_at(r.err, _b_half),
                "tone_img_uV": _amp_at(r.err, b_img),
                "fs2_dominant": bool(
                    int(np.argmax(spec_ex)) == _b_half and sig_uv > 0 and not shuffled
                ),
                "img_not_dominant": bool(
                    int(np.argmax(spec_ex)) != b_img and sig_uv > 0 and not shuffled
                ),
            }
        )
    # err_rms/σ_os 的统计稳健化：每组 bank 均值只有 2 个自由度（A/B 两组），
    # 单实现的 err_rms² 服从 χ²_2（涨落 ±100%）；3 个种子平均 -> χ²_6。
    _extra = []
    for sd in (c0.seed + 31, c0.seed + 32):
        cc = _clone(c0, **base, slice_offset_sigma_v=50e-6)
        rr = run_pipeline(
            cc,
            sine_input(0.7 * c0.v_fs, fin1),
            n,
            rng=np.random.default_rng(sd),
            scheduler=Scheduler(cc),
        )
        _extra.append(float(np.mean(rr.err**2)) * 1e12)  # V² -> µV²（×1e12，与主行同单位）
    _e50_sq = float(
        np.mean(
            [
                next(
                    r["err_rms_uV"] ** 2
                    for r in rows
                    if r["sigma_os_uV"] == 50.0
                    and not r["shuffled"]
                    and r["fin_MHz"] == fin1 / 1000000.0
                ),
                *_extra,
            ]
        )
    )
    _g = {r["sigma_os_uV"]: r for r in rows if not r["shuffled"] and r["fin_MHz"] == fin1 / 1e6}
    _sh = next(r for r in rows if r["shuffled"])
    _f2 = next(r for r in rows if r["fin_MHz"] == fin2 / 1e6)
    _f1 = _g[50.0]
    out["rows"] = rows
    out["scaling_2x"] = float(_g[100.0]["tone_fs2_uV"] / max(_g[50.0]["tone_fs2_uV"], 1e-30))
    out["err_rms_over_sigma"] = float(np.sqrt(_e50_sq) / 50.0)
    out["fin_independence"] = float(_f2["tone_fs2_uV"] / max(_f1["tone_fs2_uV"], 1e-30))
    out["shuffle_reduction_dB"] = float(
        20 * np.log10(max(_f1["tone_fs2_uV"], 1e-30) / max(_sh["tone_fs2_uV"], 1e-30))
    )
    out["fs2_dominant"] = bool(_f1["fs2_dominant"])
    out["img_not_dominant"] = bool(_f1["img_not_dominant"])
    out["PASS"] = bool(
        out["fs2_dominant"]
        and out["img_not_dominant"]
        and abs(out["scaling_2x"] - 2.0) < 0.02
        and 0.20 < out["err_rms_over_sigma"] < 0.55
        and abs(out["fin_independence"] - 1.0) < 0.25
        and out["shuffle_reduction_dB"] > 10.0
    )
    out["判据"] = (
        f"逐 slice offset（四件套之 offset）：固定两组杂散位于 f_S/2 且为最大谱分量"
        f"（f_S/2−f_IN 处无峰 —— 与 skew/带宽的 ±f_IN 指纹判别）；"
        f"2×σ_os -> tone ×{out['scaling_2x']:.3f}（精确线性）；"
        f"err_rms/σ_os = {out['err_rms_over_sigma']:.3f}"
        f"（解析 σ/√8 = 0.354，3 种子平均 χ²_6 涨落带内）；"
        f"f_IN 无关性：{fin2/1e6:.2f} MHz / {fin1/1e6:.2f} MHz tone 比 = "
        f"{out['fin_independence']:.2f}（skew 应 ∝f_IN）；"
        f"8/18 洗牌抑制 {out['shuffle_reduction_dB']:.1f} dB —— "
        "论文 'shuffling ... spread the residual interleaving tones' 对"
        "四件套统一成立。"
    )
    return out


def stage21_dither_quant(c0: Config, n: int = 2**14) -> dict:
    """stage21 -- 量化器侧 dither 与"量程 2b 增强"（逐字审计缺口 #2）。

    论文 [00]："the dither range is enhanced by 2b when the result is
    transferred from the quantizer to the RDAC"。机制载体（本模型口径）：
    dither d_Q 加在**量化器输入**，粗码在 x+d_Q 上决策；数字侧把 d_Q 以
    粒度 gran 取整后转移给 RDAC 码（d_u = −round(d_Q/gran)），残差回到
    名义 bin，余项 d_Q − round(d_Q) 经 G0 放大进 ADC2 —— 由其窗口吸收：

        gran = step0 = Δ1/8（rdac 转移）  -> 余项峰 ×G0 = 0.19 V < 窗口 0.3 V
        gran = Δ1（量化器粒度转移）       -> 余项峰 ×G0 = 1.5 V >> 窗口 -> 溢出

    "增强的位数" = log2(units_per_d1) = 3b（本栅格上界）。论文写 2b ——
    公开文本无法裁定其 2b 的参照（dither 范围/字宽/匹配余量的不同口径），
    模型如实报机制与上界，**不硬凑披露值**（方法论：禁反推）。

    验收：(1) 量化器粒度转移 -> ADC2 大面积溢出；(2) RDAC 粒度转移 ->
    零溢出且 vra 余项峰值 < 窗口；(3) dither 对 DAC 失配误差的白化
    （SFDR 改善，"dither supplements DEM"）。

    Args:
        c0: 基准配置（Config）。
        n: 每组仿真的样本数（个）。

    Returns:
        dict：'PASS'（bool）、'判据'（str）、'enhancement_bits'（本栅格上界，bit）、
        'units_per_d1'（一个 Δ1 对应的 RDAC 单位数，个）、
        'stage1_total_bits'（第一级总位数，bit）、
        'transfer_rows'（不同转移粒度的余项与溢出率对照）、
        'sfdr_gain_dB'（dither 开关的 SFDR 变化，dB）、
        'harm_fraction_off' / 'harm_fraction_on'（谐波能量占比，无量纲）、
        'sndr_change_dB'（SNDR 变化，dB）、'absorb_ratio'（窗口吸收比，无量纲）。
    """
    from .metrics import sine_fit_metrics
    from .sim_split import run_sim_split

    out = {}
    base = {
        "dac_arch": "split",
        "dither_amplitude_lsb1": 1.0,
        "ktc_enable": False,
        "dem_enable": False,
        "mismatch_enable": False,
        "enable_sampling_noise": False,
        "ra_enable_noise": False,
        "dyn_input_settling": False,
        "dyn_ref_settling": False,
        "dyn_crosstalk": False,
        "sadc_offset": 0.0,
        "sadc_rdac_gain_mismatch": 0.0,
        "sadc_mismatch_enable": False,
    }
    fin = _coherent_fin(c0, n)
    inp = sine_input(0.7 * c0.v_fs, fin)

    # ---- (1) 单位换算正确性（外部审计 F3 的**确定性**检验，不依赖仿真）----
    # dither_transfer_code 的输出必须以 **RDAC 单位步** 为单位，才能与
    # coarse*units_per_d1 直接相加。v6.1 少了这一步 ΔQ→ΔD 换算（本配置 4×），
    # 审计指出修正后溢出率 32.67%→15.97%、RMS 误差 16308→5699 µV。
    d_probe = np.array([-0.25, 0.5, -1.0, 0.0, 0.75]) * c0.delta1
    step_rdac = c0.rdac_step
    cc_range = _clone(c0, **base, dither_mode="quantizer", dither_transfer_model="range")
    code_range = dither_transfer_code(cc_range, d_probe, step_rdac=step_rdac, step_coarse=c0.delta1)
    unit_conversion_exact = bool(np.array_equal(code_range, -np.round(d_probe / step_rdac)))
    # 反事实对照：若把"以粗步长为单位"的码值直接相加，误差恰为
    # step_coarse/step_rdac 倍（审计量化的那个因子）。
    code_gran_units = -np.round(d_probe / c0.delta1)  # 粗步长单位
    factor_missing = float(c0.delta1 / step_rdac)

    # ---- (2) 量程增强能力（**推导量**，不是实验主张）----
    # "dither range enhanced by 2b" 的 b = log2(units_per_d1)，即一个第一级
    # 判决步包含多少个 RDAC 单位步。它与披露值、与"第一级 9b"必须联立自洽。
    units_per_d1 = units_per_first_stage_step(c0, None)
    enh_bits = float(np.log2(units_per_d1)) if units_per_d1 > 0 else 0.0
    stage1_total_bits = float(c0.b1) + enh_bits
    enhancement_matches = bool(abs(enh_bits - float(c0.dither_enhancement_bits)) < 1e-9)
    stage1_matches = bool(abs(stage1_total_bits - 9.0) < 1e-9)
    out["units_per_d1"] = int(units_per_d1)
    out["enhancement_bits"] = enh_bits
    out["stage1_total_bits"] = stage1_total_bits

    # ---- (3) dither 的吸收代价律（仿真）----
    # 余项 = 注入量 d 与 RDAC 栅格取整之差 ∈ ±step_rdac/2，**与 d 幅度无关**；
    # 折到 RA 输出即 ±g0·step_rdac/2。本项检验实测余项是否遵守该律
    # （这才是"转移到 RDAC"的定量代价，而不是粒度对照）。
    cc_on = _clone(c0, **base, dither_mode="quantizer", dither_transfer_model="range")
    r_on = run_sim_split(cc_on, inp, n, rng=np.random.default_rng(c0.seed + 21))
    cc_off = _clone(c0, **base, dither_mode="off")
    r_off = run_sim_split(cc_off, inp, n, rng=np.random.default_rng(c0.seed + 21))
    vra_on = np.asarray(r_on.vra, dtype=float)
    vra_nom_max = c0.g_actual * c0.delta1
    excursion_measured = float(max(vra_on.max() - vra_nom_max, -vra_on.min(), 0.0))
    excursion_predicted = c0.g0 * step_rdac / 2.0
    absorb_ratio = excursion_measured / max(excursion_predicted, 1e-30)
    over_on = float(np.mean(r_on.adc2_over))
    err_on = float(np.sqrt(np.mean(r_on.err**2)) * 1e6)
    err_off = float(np.sqrt(np.mean(r_off.err**2)) * 1e6)
    out["transfer_rows"] = [
        {
            "branch": "range(paper)",
            "over_rate": over_on,
            "err_rms_uV": err_on,
            "vra_excursion_V": excursion_measured,
        },
        {
            "branch": "off(baseline)",
            "over_rate": float(np.mean(r_off.adc2_over)),
            "err_rms_uV": err_off,
            "vra_excursion_V": 0.0,
        },
    ]
    out["predicted_excursion_rdac_V"] = excursion_predicted
    out["absorb_ratio"] = absorb_ratio
    out["counterfactual_missing_factor"] = factor_missing
    out["counterfactual_code_units"] = code_gran_units.tolist()

    # ---- (4) 白化的结构性边界（"dither supplements DEM" 的分工）----
    mis_base = dict(base, mismatch_enable=True, mismatch_sigma0=1e-3, mismatch_split=(0, 0, 1))

    def _mis(dith):
        """在相同失配实现下对比 dither 开/关的 SFDR 与谐波占比。

        Args:
            dith: 是否开启量化器侧 dither（bool）。

        Returns:
            dict：SFDR（dB）与谐波能量占比（无量纲）。
            用于判定 dither 只能白化子码尺度成分、不能替代 DEM 这一结构性结论。
        """
        kw = (
            {"dither_mode": "quantizer", "dither_transfer_model": "range"}
            if dith
            else {"dither_mode": "off"}
        )
        cc = _clone(c0, **mis_base, **kw)
        rr = run_sim_split(cc, inp, n, rng=np.random.default_rng(c0.seed + 22))
        m = sine_fit_metrics(rr.out, cc.fs, fin)
        err = np.asarray(rr.err, dtype=float)
        spec = np.abs(np.fft.rfft(err)) ** 2
        b_f = int(round(fin / cc.fs * n))
        harm = float(
            sum(
                spec[max(int(round(mq * b_f)) - 1, 0) : int(round(mq * b_f)) + 2].max()
                for mq in range(2, 11)
                if int(round(mq * b_f)) < len(spec)
            )
        )
        return {
            "sfdr_dB": m["SFDR_dB"],
            "thd_dB": m["THD_dB"],
            "sndr_dB": m["SNDR_dB"],
            "err_rms_uV": float(np.sqrt(np.mean(err**2)) * 1e6),
            "harm_fraction": float(harm / max(spec[1:].sum(), 1e-30)),
        }

    _m0 = _mis(False)
    _m1 = _mis(True)
    out["mismatch_off_dither"] = _m0
    out["mismatch_on_dither"] = _m1
    out["sfdr_gain_dB"] = _m1["sfdr_dB"] - _m0["sfdr_dB"]
    out["sndr_change_dB"] = _m1["sndr_dB"] - _m0["sndr_dB"]
    out["harm_fraction_off"] = _m0["harm_fraction"]
    out["harm_fraction_on"] = _m1["harm_fraction"]

    out["PASS"] = bool(
        unit_conversion_exact
        and enhancement_matches
        and stage1_matches
        and 0.7 < absorb_ratio < 1.3
        and over_on < 1e-2
    )
    out["判据"] = (
        f"① 单位换算（审计 F3，确定性检验）：d_code 严格以 RDAC 单位步为单位 "
        f"= {unit_conversion_exact}；若漏掉 ΔQ→ΔD 换算（反事实对照），码值会差 "
        f"{factor_missing:.0f}×（本配置 {code_gran_units.tolist()} 粗步长单位）。"
        f"② 量程增强（推导量）：units_per_d1 = {units_per_d1} -> 增强 "
        f"{enh_bits:.0f}b，与披露 {c0.dither_enhancement_bits}b "
        f"{'一致' if enhancement_matches else '不一致'}；"
        f"第一级总判决能力 = b1 + 增强 = {c0.b1} + {enh_bits:.0f} = "
        f"{stage1_total_bits:.0f}b（披露 9b，{'自洽' if stage1_matches else '不自洽'}）。"
        f"③ 吸收代价律：实测余项 {excursion_measured*1e3:.1f} mV vs 预测 "
        f"g0·ΔD/2 = {excursion_predicted*1e3:.1f} mV（比值 {absorb_ratio:.2f}，"
        f"与 dither 幅度无关），ADC2 溢出 {over_on:.2%} —— "
        f"'转移到 RDAC' 的定量代价是一个 RDAC 半步。"
        f"④ 白化的结构性边界（'dither supplements DEM'，unit 1000 ppm [假设]、"
        f"DEM 关）：SFDR {_m0['sfdr_dB']:.1f} -> {_m1['sfdr_dB']:.1f} dB"
        f"（Δ={out['sfdr_gain_dB']:+.1f} dB，谐波占比 "
        f"{_m0['harm_fraction']:.3f} -> {_m1['harm_fraction']:.3f}）—— "
        f"d_u 只在 ±{(c0.delta1/2)/c0.rdac_step:.0f} 单位内抖动，"
        "打散不了 unit 失配在 512 单位上的宏观随机游走（谐波主导保留），"
        "只白化子码尺度成分 —— 宏观失配归 DEM（stage8）/校准（stage14），"
        "dither 管量化决策与子码尺度：'supplements' 的准确分工，"
        "亦与等权阵列 dither 收益弱的结构性结论（工作记忆）一致。"
        "**本 stage 不再声称用粒度实验闭合论文的 'range enhanced' 机制** ——"
        "该机制由 ② 的推导量判定，粒度只是实现细节。"
    )
    return out


def stage22_autozero(c0: Config, n: int = 2**14) -> dict:
    """stage22 -- RA auto-zero 与 ADC2 动态采样带宽的预算归因（缺口 #3）。

    PPT p.34-35 披露两个 dB 数：auto-zero 噪声代价 −1.6 dB（存储电容
    kT/C + 噪声折叠）、ADC2 动态采样带宽 +1.3 dB（宽建立/窄噪声）。
    行为建模（ra.py）：前者 = RA 白噪声 ×10^(1.6/20)，后者 = RA 输出噪声
    ×10^(−1.3/20)。**口径要点**：PPT 的 −1.6/+1.3 dB 参照系未披露；
    本模型整机预算中 RA 占噪声功率 ~74%（kT/C 20.1 µV、RA 34.2 µV），
    折到整机 ΔSNDR 应为 −1.2 / +1.0 dB —— 本 stage 检验"预算推导 vs
    全链路实测"的一致性（<0.15 dB），不硬凑披露值。

    验收：(1) auto-zero 开 -> ΔSNDR = 预算推导值 ±0.15 dB；
    (2) ADC2 动态带宽开 -> 同上；(3) 两者叠加 = 各自之和（噪声功率线性）。

    Args:
        c0: 基准配置（Config）。
        n: 每组仿真的样本数（个）。

    Returns:
        dict：'PASS'（bool，三项验收）、'判据'（str）、'rows'（auto-zero /
        ADC2 动态带宽 / 两者叠加三行的预算推导值与实测 ΔSNDR，dB）、
        'PPT_disclosed_dB'（PPT 披露值，dB）、'ra_power_share'（RA 噪声功率占比，
        无量纲）、'sndr_baseline_dB'（基线 SNDR，dB）、
        'max_dev_dB'（预算推导与实测的最大偏差，dB，判据阈值 0.15 dB）。
    """
    from .metrics import sine_fit_metrics
    from .sim import run_sim

    out = {}
    base = {
        "dac_arch": "unary",
        "dither_mode": "off",
        "ktc_enable": False,
        "dem_enable": False,
        "mismatch_enable": False,
        "dyn_input_settling": False,
        "dyn_ref_settling": False,
        "dyn_crosstalk": False,
        "sadc_mismatch_enable": False,
    }
    fin = _coherent_fin(c0, n)
    inp = sine_input(0.7 * c0.v_fs, fin)

    def _sndr(**kw):
        """按关键字覆盖配置后跑一次 unary 链路，返回 SNDR。

        Args:
            **kw: 覆盖到基准配置上的关键字参数。

        Returns:
            SNDR（dB）。随机种子固定为 c0.seed + 23，保证多次调用可比。
        """
        cc = _clone(c0, **base, **kw)
        r = run_sim(cc, inp, n, rng=np.random.default_rng(c0.seed + 23))
        return sine_fit_metrics(r.out, cc.fs, fin)["SNDR_dB"]

    nb = noise_budget(c0)
    s_k = nb["kT/C (RDAC)"] ** 2
    s_ra = nb["RA (折输入)"] ** 2
    s_q = nb["ADC2 量化 (折输入)"] ** 2
    s0 = s_k + s_ra + s_q
    F_az = 10.0 ** (c0.ra_autozero_cost_db / 20.0)
    r_bw = 10.0 ** (-1.3 / 20.0)
    # 口径：ΔSNDR = −10·log10(N_new/N0)（噪声升 -> SNDR 降 -> 负），
    # 与 measured_dB = sndr_x − sndr0 同号比较。
    pred_az = -10 * np.log10((s_k + F_az**2 * s_ra + s_q) / s0)
    pred_bw = -10 * np.log10((s_k + r_bw**2 * s_ra + s_q) / s0)
    pred_both = -10 * np.log10((s_k + (F_az * r_bw) ** 2 * s_ra + s_q) / s0)

    sndr0 = _sndr()
    sndr_az = _sndr(ra_autozero=True)
    sndr_bw = _sndr(adc2_dyn_bw_ratio=r_bw)
    sndr_both = _sndr(ra_autozero=True, adc2_dyn_bw_ratio=r_bw)
    rows = {
        "autozero": {"measured_dB": sndr_az - sndr0, "predicted_dB": pred_az},
        "adc2_dyn_bw": {"measured_dB": sndr_bw - sndr0, "predicted_dB": pred_bw},
        "combined": {"measured_dB": sndr_both - sndr0, "predicted_dB": pred_both},
    }
    out["sndr_baseline_dB"] = sndr0
    out["ra_power_share"] = s_ra / s0
    out["rows"] = rows
    out["max_dev_dB"] = float(max(abs(r["measured_dB"] - r["predicted_dB"]) for r in rows.values()))
    out["PPT_disclosed_dB"] = {"autozero_cost": -c0.ra_autozero_cost_db, "adc2_dyn_bw_gain": 1.3}
    out["PASS"] = bool(
        out["max_dev_dB"] < 0.15
        and abs(
            rows["combined"]["measured_dB"]
            - rows["autozero"]["measured_dB"]
            - rows["adc2_dyn_bw"]["measured_dB"]
        )
        < 0.1
    )
    out["判据"] = (
        f"auto-zero 预算归因（PPT 披露 −1.6 dB，整机折算 {pred_az:+.2f} dB，"
        f"RA 占噪声功率 {s_ra/s0:.0%}）：实测 {rows['autozero']['measured_dB']:+.2f} dB；"
        f"ADC2 动态采样带宽（PPT +1.3 dB，整机折算 {pred_bw:+.2f} dB）："
        f"实测 {rows['adc2_dyn_bw']['measured_dB']:+.2f} dB；"
        f"叠加：实测 {rows['combined']['measured_dB']:+.2f} vs "
        f"预测 {pred_both:+.2f} dB（功率线性自洽）—— "
        f"最大偏差 {out['max_dev_dB']:.3f} dB。披露值与本模型整机预算的"
        "差额 = 参照系差异（PPT 未披露其 −1.6 dB 的参照系），模型做"
        "预算推导 vs 实测一致性检验，不硬凑披露值。"
    )
    return out


def stage23_flicker(c0: Config, n: int = 2**15) -> dict:
    """stage23 -- 1/f 噪声（转角 40 Hz）与 auto-zero 的交互（缺口 #4）。

    PPT：系统输入参考噪声底 8.8 nV/√Hz、1/f 转角 ~40 Hz。模型（ra.py）：
    注入源只生成 1/f 段（f<=fc），白底由系统热噪声承担；闪烁白底
    S0 = ratio²·kT/C 白底，ratio = σ_total/σ_kTC ≈ 1.97 使系统转角 = fc。

    **统计口径（首版教训）**：单实现周期图每 bin 服从 χ²₂（±5.6 dB），
    逐 bin/短带比较不可判 —— 发生器检验用 K=24 种子平均周期图（df 细，
    谱形收敛到 −10.0 dB/dec）；系统内 df=1.22 kHz 只能用**带功率比**，
    容差按 χ² 自由度定（带和的相对涨落 1/√(2·bins)）。

    验收：(1) 发生器谱形：K 平均后 −10 dB/dec（±0.5）、转角处 PSD=S0；
    (2) 系统内 fc=40 kHz：5–20 kHz 带功率超出 = 带积分预测（±2 dB）、
    低/高带比值同源预测（±3 dB）；(3) 40 Hz 转角对 AC 指标影响 <0.05 dB
    —— 论文敢引 40 Hz 的定量出处；(4) 假想无 auto-zero（转角 100 kHz）
    的 SNDR 代价 vs 预算推导（±0.15 dB）；(5) auto-zero 开 -> 带内闪烁
    移除（回到折叠白底）。

    Args:
        c0: 基准配置（Config）。
        n: 每组仿真的样本数（个）。

    Returns:
        dict：'PASS'（bool，五项验收）、'判据'（str）、'generator'（发生器谱形
        检验：斜率 dB/decade、转角频率 Hz）、'system_fc40k'（系统内带功率对照）、
        'ac_impact_40Hz_dB'（40 Hz 转角对 AC 指标的影响，dB）、
        'ac_impact_100kHz_dB'（假想无 auto-zero 时的 SNDR 代价，dB）、
        'predicted_100kHz_dB'（同项的预算推导值，dB）、
        'autozero_removal_dB'（auto-zero 移除量，dB）、
        'autozero_floor_vs_white_dB'（折叠后底噪相对白底，dB）。
    """
    from .metrics import sine_fit_metrics
    from .ra import flicker_series
    from .sim import run_sim

    out = {}
    fs = c0.fs

    # ---- (1) 发生器谱形：K 种子平均周期图（fs=1 MHz 独立验证）----
    n_fft = 1 << 21
    g_fs, g_fc = 1e6, 40.0
    K = 48
    acc = np.zeros(n_fft // 2 + 1)
    for k in range(K):
        sig_k = flicker_series(n_fft, g_fs, g_fc, 1.0, np.random.default_rng(1000 + k))
        acc += np.abs(np.fft.rfft(sig_k)) ** 2
    psd = acc * 2.0 / (g_fs * n_fft * K)
    f_bins = np.fft.rfftfreq(n_fft, d=1.0 / g_fs)
    s0 = 2.0 * 1.0**2 / g_fs
    slope_dec = _psd_logfit(f_bins, psd, 1.5, 38.0)
    b_fc = int(np.argmin(np.abs(f_bins - g_fc * 0.9)))  # 转角以下最近 bin
    corner_ratio = float(10 * np.log10(psd[b_fc] / s0))
    above = (f_bins >= 10 * g_fc) & (f_bins <= 100 * g_fc)
    floor_db = float(10 * np.log10(np.median(psd[above]) / s0 + 1e-30))
    out["generator"] = {
        "slope_dB_per_dec": slope_dec,
        "corner_psd_vs_floor_dB": corner_ratio,
        "above_corner_floor_dB": floor_db,
        "K_avg": K,
    }

    # ---- (2)-(5) 系统内 ----
    nb = noise_budget(c0)
    s_tot = math.sqrt(
        nb["kT/C (RDAC)"] ** 2 + nb["RA (折输入)"] ** 2 + nb["ADC2 量化 (折输入)"] ** 2
    )
    s_ktc = nb["kT/C (RDAC)"]
    ratio_sys = s_tot / s_ktc  # 系统转角 = f_c 的 ratio 条件
    base = {
        "dac_arch": "unary",
        "dither_mode": "off",
        "ktc_enable": False,
        "dem_enable": False,
        "mismatch_enable": False,
        "dyn_input_settling": False,
        "dyn_ref_settling": False,
        "dyn_crosstalk": False,
        "sadc_mismatch_enable": False,
        "flicker_white_ratio": ratio_sys,
    }
    fin = _coherent_fin(c0, n)
    inp = sine_input(0.7 * c0.v_fs, fin)

    def _run(**kw):
        """按关键字覆盖配置后跑一次 unary 链路。

        Args:
            **kw: 覆盖到基准配置上的关键字参数。

        Returns:
            二元组 (cfg, result)：cfg 为实际使用的配置（Config），
            result 为 SimResult。随机种子固定为 c0.seed + 24。
        """
        cc = _clone(c0, **base, **kw)
        return cc, run_sim(cc, inp, n, rng=np.random.default_rng(c0.seed + 24))

    def _psd(res):
        """由仿真结果计算误差序列的单边功率谱密度。

        Args:
            res: 仿真结果（SimResult），使用其 err 字段。

        Returns:
            二元组 (fb, ps)：fb 为频率轴（Hz，长度 n/2+1），
            ps 为单边功率谱密度（V²/Hz）。
        """
        e = np.asarray(res.err, dtype=float)
        ps = np.abs(np.fft.rfft(e)) ** 2 * 2.0 / (fs * len(e))
        fb = np.fft.rfftfreq(len(e), d=1.0 / fs)
        return fb, ps

    def _band(ps, fb, lo, hi):
        """对功率谱在指定频带内积分，得到带内功率。

        Args:
            ps: 功率谱密度（V²/Hz）。
            fb: 频率轴（Hz）。
            lo: 频带下界（Hz）。
            hi: 频带上界（Hz）。

        Returns:
            带内功率（V²）。用带功率而非逐 bin 比较，是低自由度下的可靠口径。
        """
        m = (fb >= lo) & (fb <= hi)
        return float(ps[m].sum())

    # (2) fc = 40 kHz（系统内可分辨）：带功率对照（df=1.22 kHz，逐 bin 不可判）
    cc40, r40 = _run(flicker_corner_hz=40e3)
    _, r_base = _run(flicker_corner_hz=0.0)
    fb, ps = _psd(r40)
    fb0, ps0 = _psd(r_base)
    s0_sys = 2.0 * s_tot**2 / fs
    # 带 A：5–20 kHz（12 bins）。预测：Σ(1+fc/f)·S0_sys（逐 bin 积分）
    bA = (fb >= 5e3) & (fb <= 20e3)
    pred_A = float(np.sum(1.0 + 40e3 / fb[bA]))
    meas_A = float(ps[bA].sum() / s0_sys)  # 以 S0_sys 为单位
    out["system_fc40k"] = {
        "band_5_20k_total_over_s0": meas_A,
        "band_5_20k_pred_over_s0": pred_A,
        "band_excess_dB": float(10 * np.log10(meas_A / pred_A)),
        # 低/高带比：1–3 kHz（2 bins）vs 10–30 kHz（17 bins）
        "ratio_lo_hi": float(_band(ps, fb, 1e3, 3e3) / _band(ps, fb, 10e3, 30e3)),
    }
    bB_lo = (fb >= 1e3) & (fb <= 3e3)
    bB_hi = (fb >= 10e3) & (fb <= 30e3)
    out["system_fc40k"]["ratio_lo_hi_pred"] = float(
        np.sum(1.0 + 40e3 / fb[bB_lo]) / np.sum(1.0 + 40e3 / fb[bB_hi])
    )
    # 容差**由 χ² 自由度推导**，不写死。低带只有 n_lo 个 bin、高带 n_hi 个，
    # 功率和是 n 个指数变量之和 -> 相对标准差 = 1/sqrt(n)，故比值的相对
    # 标准差 ≈ sqrt(1/n_lo + 1/n_hi)。容差取 2σ（≈95% 置信）。
    # 历史教训（外部审计）：旧判据写死 3.0 dB，而 df=1.22 kHz 时低带只有
    # 2 个 bin（相对标准差 ~75%），单次实现就可能超出 —— 这是统计容差问题，
    # 不是发生器缺陷；发生器本身由 K=48 种子平均的斜率判据独立把关。
    n_lo = int(bB_lo.sum())
    n_hi = int(bB_hi.sum())
    rel_std_lo_hi = float(np.sqrt(1.0 / max(n_lo, 1) + 1.0 / max(n_hi, 1)))
    tol_lo_hi_db = float(10.0 * np.log10(1.0 + 2.0 * rel_std_lo_hi))
    out["system_fc40k"]["n_bins_lo_hi"] = [n_lo, n_hi]
    out["system_fc40k"]["ratio_lo_hi_tol_dB"] = tol_lo_hi_db
    out["system_fc40k"]["baseline_vs_s0_dB"] = float(
        10 * np.log10(_band(ps0, fb0, 5e3, 20e3) / (s0_sys * bA.sum()))
    )

    # (3) 40 Hz 转角对 AC 指标的影响
    _, r_fl = _run(flicker_corner_hz=40.0)
    sndr_fl = sine_fit_metrics(r_fl.out, fs, fin)["SNDR_dB"]
    sndr_base = sine_fit_metrics(r_base.out, fs, fin)["SNDR_dB"]
    out["ac_impact_40Hz_dB"] = float(sndr_fl - sndr_base)

    # (4) 假想无 auto-zero：转角 100 kHz 的代价
    _, r_100k = _run(flicker_corner_hz=100e3)
    sndr_100k = sine_fit_metrics(r_100k.out, fs, fin)["SNDR_dB"]
    f1 = fs / n
    # 口径与 stage22 一致：ΔSNDR = −10·log10(N_new/N0)（噪声升为负）
    pred_100k = -10 * np.log10(1.0 + 100e3 * math.log(fs / 2 / f1) / (fs / 2))
    out["ac_impact_100kHz_dB"] = float(sndr_100k - sndr_base)
    out["predicted_100kHz_dB"] = float(pred_100k)

    # (5) auto-zero 移除闪烁（fc=100 kHz 时带内闪烁最强，对照最灵敏）
    _, r_az = _run(flicker_corner_hz=100e3, ra_autozero=True)
    fbaz, psaz = _psd(r_az)
    band_m = (fb >= 2e3) & (fb <= 20e3)
    fl_band = float(ps[band_m].sum())
    az_band = float(psaz[band_m].sum())
    base_band = float(ps0[band_m].sum())
    out["autozero_removal_dB"] = float(10 * np.log10(az_band / fl_band))
    # az 开后带内应回到"基线 × 折叠系数"：折叠只作用于 RA 白噪声份额
    # （kT/C 与 ADC2 量化不被 az 改变），fold = (1−s_ra) + s_ra·10^(1.6/10)。
    # 用 az_band/base_band 直接对照（避开 fl_band 自身的 χ² 涨落）。
    s_ra_share = nb["RA (折输入)"] ** 2 / (s_tot**2)
    fold = (1 - s_ra_share) + s_ra_share * 10 ** (c0.ra_autozero_cost_db / 10)
    out["autozero_floor_vs_white_dB"] = float(10 * np.log10(az_band / (base_band * fold)))

    out["PASS"] = bool(
        abs(slope_dec + 10.0) < 0.5
        and abs(corner_ratio) < 0.5
        and floor_db < -30.0
        and abs(out["system_fc40k"]["band_excess_dB"]) < 2.0
        and abs(
            10
            * np.log10(out["system_fc40k"]["ratio_lo_hi"] / out["system_fc40k"]["ratio_lo_hi_pred"])
        )
        < tol_lo_hi_db
        and abs(out["ac_impact_40Hz_dB"]) < 0.05
        and abs(out["ac_impact_100kHz_dB"] - pred_100k) < 0.15
        and abs(out["autozero_floor_vs_white_dB"]) < 1.5
    )
    out["判据"] = (
        f"1/f 噪声（PPT 转角 ~40 Hz）：发生器 K={K} 种子平均周期图斜率 "
        f"{slope_dec:.2f} dB/dec（理论 −10）、转角处 PSD = 自身白底"
        f"（{corner_ratio:+.2f} dB）、转角以上无功率（{floor_db:.0f} dB，"
        "白底由系统热噪声承担的口径）；系统内 fc=40 kHz：5–20 kHz 带功率 "
        f"{meas_A:.0f}·S0 vs 带积分预测 {pred_A:.0f}·S0"
        f"（{out['system_fc40k']['band_excess_dB']:+.2f} dB），"
        f"低/高带比 {out['system_fc40k']['ratio_lo_hi']:.1f} vs 预测 "
        f"{out['system_fc40k']['ratio_lo_hi_pred']:.1f}；"
        f"40 Hz 转角对 AC SNDR 影响 {out['ac_impact_40Hz_dB']:+.4f} dB"
        " —— 论文敢引 40 Hz 的定量出处；"
        f"假想无 auto-zero（fc=100 kHz）：SNDR 代价 "
        f"{out['ac_impact_100kHz_dB']:+.2f} dB（预算推导 "
        f"{pred_100k:+.2f}）—— auto-zero 存在性的量化依据；"
        f"auto-zero 开：带内功率回落到折叠白底"
        f"（{out['autozero_floor_vs_white_dB']:+.2f} dB，折叠只作用 RA 份额），"
        f"相对闪烁态移除 {out['autozero_removal_dB']:.1f} dB。"
    )
    return out


def stage24_rdac_bitwise(c0: Config, n: int = 2**14) -> dict:
    """stage24 -- RDAC 逐位装载 "loaded as they develop"（缺口 #5）。

    论文 [00]："conversion results are loaded as they develop" —— 跟随器
    逐位装载：SADC 结果分 B 位步写入 RDAC（SAR 次序，MSB 先、位权 2^-i），
    每步抽取的电荷在剩余转换时间内恢复。与两种理想化对照：

        终态单次装载（最坏）   残余因子 1.0，        峰值需求 1.0
        转换开始装载（v1-v5 隐含，乐观） exp(−T_conv/τ)，峰值 1.0
        逐位装载（论文实际）   Σ w_i·exp(−(B−i)/B·T_conv/τ)，峰值 max(w)=0.516

    静态分量（DC 负载贯穿转换相）不受装载时序影响 —— 只改动态分量
    （口径声明见 dynamics.py）。验收：(1) 关闭时与旧口径逐位一致；
    (2) 动态分量实测比值 = 解析 bitwise_eta_dyn/eta ±3%（四象限隔离：
    静态单独跑、动态用功率减法隔离）；(3) 峰值需求比 = 2^-(B-1) 归一；
    (4) 系统级 SNDR 影响如实报告（模型默认口径偏乐观 ~20% 动态项）。

    Args:
        c0: 基准配置（Config）。
        n: 每组仿真的样本数（个）。

    Returns:
        dict：'PASS'（bool，四项验收）、'判据'（str）、'scenario_table'（终态/
        开始/逐位三种装载口径的残余因子与峰值需求对照）、
        'eta_bitwise'（逐位装载残余因子，无量纲）、'eta_single'（终态装载，无量纲）、
        'dyn_ratio_measured' / 'dyn_ratio_analytic'（动态分量实测与解析比值，
        无量纲，判据 ±3%）、'peak_demand_ratio'（峰值瞬时电荷需求比，无量纲）、
        'static_unchanged_max_V'（静态分量最大变化，V）、
        'equiv_when_off'（关闭时与旧口径的等价性）、
        'sndr_off_dB' / 'sndr_on_dB'（开关前后的系统 SNDR，dB）。
    """
    from .dynamics import bitwise_eta_dyn, bitwise_peak_ratio, ref_recovery_factor
    from .sim_split import run_sim_split

    out = {}
    base = {
        "dac_arch": "split",
        "dither_mode": "off",
        "ktc_enable": False,
        "dem_enable": False,
        "mismatch_enable": False,
        "enable_sampling_noise": False,
        "ra_enable_noise": False,
        "dyn_input_settling": False,
        "dyn_crosstalk": False,
        "dyn_ref_settling": True,
        "dyn_tau_ref": 20e-9,
        "dyn_t_conv_frac": 0.40,
        "dyn_ref_dynamic_ratio": 1.0,
    }
    fin = _coherent_fin(c0, n)
    inp = sine_input(0.7 * c0.v_fs, fin)

    def _run(**kw):
        """按关键字覆盖配置后跑一次 split 链路。

        Args:
            **kw: 覆盖到基准配置上的关键字参数。

        Returns:
            SimResult：含 out（V）、err（V）等字段。
            随机种子固定为 c0.seed + 25，保证逐位装载开/关两组结果可比。
        """
        cc = _clone(c0, **{**base, **kw})
        return run_sim_split(cc, inp, n, rng=np.random.default_rng(c0.seed + 25))

    # (1) 参考建立关闭时逐位等价（无旁路效应）
    cc0 = _clone(c0, **{**base, "dyn_ref_settling": False, "rdac_bitwise_loading": False})
    cc1 = _clone(c0, **{**base, "dyn_ref_settling": False, "rdac_bitwise_loading": True})
    r0 = run_sim_split(cc0, inp, n, rng=np.random.default_rng(c0.seed + 25))
    r1 = run_sim_split(cc1, inp, n, rng=np.random.default_rng(c0.seed + 25))
    equiv_off = bool(np.array_equal(r0.out, r1.out))

    # (2) 动态分量隔离（差分法，首版教训）：功率减法 mean(e²)−mean(s²) 含
    # 2·cov(static, dynamic) 交叉项（实测偏 6.6%）。改为同种子下 ratio=1 与
    # ratio=0 两次运行**逐样本相减** —— e_ref 对 ratio 线性，差 = 精确动态
    # 分量 −v_nom·g_dy·dk/n（确定性链路，噪声/失配全关，逐样本可比）。
    e_s_off = np.asarray(
        _run(rdac_bitwise_loading=False, dyn_ref_dynamic_ratio=0.0).e_dac, dtype=float
    )
    e_s_on = np.asarray(
        _run(rdac_bitwise_loading=True, dyn_ref_dynamic_ratio=0.0).e_dac, dtype=float
    )
    static_equiv = float(np.max(np.abs(e_s_off - e_s_on)))
    e_t_off = np.asarray(_run(rdac_bitwise_loading=False).e_dac, dtype=float)
    e_t_on = np.asarray(_run(rdac_bitwise_loading=True).e_dac, dtype=float)
    e_dyn_off = e_t_off - e_s_off
    e_dyn_on = e_t_on - e_s_on
    ratio_measured = float(np.sqrt(np.mean(e_dyn_on**2) / np.mean(e_dyn_off**2)))
    eta_single = ref_recovery_factor(_clone(c0, **base))
    ratio_analytic = bitwise_eta_dyn(_clone(c0, **base)) / eta_single

    out["equiv_when_off"] = equiv_off
    out["static_unchanged_max_V"] = static_equiv
    out["dyn_ratio_measured"] = float(ratio_measured)
    out["dyn_ratio_analytic"] = float(ratio_analytic)
    out["eta_single"] = float(eta_single)
    out["eta_bitwise"] = float(bitwise_eta_dyn(_clone(c0, **base)))
    out["peak_demand_ratio"] = float(bitwise_peak_ratio(_clone(c0, **base)))
    out["scenario_table"] = [
        {"profile": "end_load(最坏)", "residual_factor": 1.0, "peak_demand": 1.0},
        {
            "profile": "start_load(v1-v5隐含)",
            "residual_factor": float(eta_single),
            "peak_demand": 1.0,
        },
        {
            "profile": "bitwise(论文实际)",
            "residual_factor": out["eta_bitwise"],
            "peak_demand": out["peak_demand_ratio"],
        },
    ]
    # (4) 系统级（噪声开）—— 如实报告，不做硬判据
    ccn = _clone(
        c0,
        **{
            **base,
            "dyn_ref_settling": True,
            "enable_sampling_noise": True,
            "ra_enable_noise": True,
        },
    )
    sn = []
    for bw in (False, True):
        ccx = _clone(ccn, rdac_bitwise_loading=bw)
        from .metrics import sine_fit_metrics

        rr = run_sim_split(ccx, inp, n, rng=np.random.default_rng(c0.seed + 26))
        sn.append(sine_fit_metrics(rr.out, ccx.fs, fin)["SNDR_dB"])
    out["sndr_off_dB"], out["sndr_on_dB"] = float(sn[0]), float(sn[1])

    out["PASS"] = bool(
        equiv_off
        and static_equiv < 1e-12
        and abs(ratio_measured - ratio_analytic) < 0.03 * ratio_analytic
        and abs(out["peak_demand_ratio"] - 2.0**-1 / (1 - 2.0**-6)) < 1e-6
    )
    out["判据"] = (
        f"RDAC 逐位装载（'loaded as they develop'）：关闭时与旧口径逐位一致"
        f" = {equiv_off}；静态分量不变（max|Δ| = {static_equiv:.1e} V）；"
        f"动态分量实测比值 {ratio_measured:.3f} vs 解析 "
        f"eta_bitwise/eta = {ratio_analytic:.3f}（B=6 位权 2^-i）；"
        f"峰值瞬时电荷需求 {out['peak_demand_ratio']:.3f}×（参考缓冲裕量"
        f"加倍）。三口径对照：终态装载 1.0 / 开始装载（v1-v5 隐含，乐观）"
        f"{eta_single:.3f} / 逐位（论文实际）{out['eta_bitwise']:.3f} —— "
        f"逐位比终态装载好 {20*np.log10(1/out['eta_bitwise']):.1f} dB，"
        f"但比旧模型隐含口径差 {20*np.log10(out['eta_bitwise']/eta_single):.1f} dB"
        f"（系统 SNDR {out['sndr_off_dB']:.2f} -> {out['sndr_on_dB']:.2f} dB，"
        "如实入账，不做乐观隐含）。"
    )
    return out
