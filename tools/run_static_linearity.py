#!/usr/bin/env python3
# =============================================================================
# 文件名     : run_static_linearity.py
# 模块名     : v21 静态转移特性与码密度实验
# 版本       : v1.0
# 功能       : 在同一器件、数据格式与输入范围上比较扰动、DEM 和片外估权。
# 设计说明   : 复用 v21 的开关映射、片组调度、器件权重及独立定点参考。
#              在每段粗码内，ADC2 箱边界可以精确映回输入电压。按有效
#              区间累计真实 20 位出码宽度，也能逐箱计数均匀斜坡的采样点。
# 变更记录   : 首次加入全码静态线性度实验。
# =============================================================================
"""v21 电荷合同的全码行为实验。

输入是覆盖差分满幅的均匀斜坡，输出限定为同一 20 位 offset-binary 整数码。
同一个型号的若干虚拟器件由制造种子区分。制造、权重测量、留出验证各有身份。
理想比较器按独立 SADC 名义门限判决，RDAC 的制造失配由实际被选单元承担。
模拟观测仅通过 13 位 ADC2 原码进入解码器。失配真值另列作诊断上界。

输出目录含每一内部码的输入占用宽度、确定性斜坡命中数和可选的含噪期望宽度。
非单调传递的占用宽度仍可测量。是否在粗码交接处倒退另行计数。
此实验针对静态算术和开关电荷，晶体管动态误差与流水控制归相应验证层。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np
import scipy
from scipy.special import ndtr

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from adi_model.calibration.mask_calibration import fit_code_correction
from adi_model.dem_dither import bridge_swap, post_mask, pre
from adi_model.sched18_rtl import Scheduler18Rtl
from algo_chain import CF, DELTA, LSB20, N2, SPAN, VFS, YMIN, fwd_dac, make_w, sadc
from recon_oracle import Q30, oracle, rounded

CODES = 1 << 20
Q2 = SPAN / N2
MODES = ("fixed", "dem", "dither", "both")
SOURCE_FILES = (
    "tools/run_static_linearity.py",
    "tools/algo_chain.py",
    "tools/recon_oracle.py",
    "src/adi_model/dem_dither.py",
    "src/adi_model/sched18_rtl.py",
    "src/adi_model/calibration/mask_calibration.py",
)


# =============================================================================
# 1. 片组与冻结开关状态。每种方法使用相同的独立制造、训练、评估种子。
# =============================================================================
@dataclass(frozen=True)
class State:
    """同一笔的冻结控制。

    片号按调度器给出的物理顺序排列；slot 权位随数组索引读对应片的系数。
    片内轮换和扰动是两个控制维度。消融时只覆盖该维，继续沿用同一组器件。
    """

    ids: tuple[int, ...]
    horizontal: int
    vertical: int
    half: int
    coin: int
    word: int

    def select(self, mode):
        dem = mode in ("dem", "both")
        dither = mode in ("dither", "both")
        return State(
            self.ids,
            self.horizontal if dem else 0,
            self.vertical if dem else 0,
            self.half if dem else 0,
            self.coin if dem else 0,
            self.word if dither else 0,
        )


def states(n, seed):
    """每 64 个状态分别遍历 64 个相位对和 64 个字，二者独立置换。

    预热排除调度器的空转换状态，每次推进后读取已经交接的转换片。
    与输入无关的分层采样保证每个字都被观察到；相位与字的笛卡尔积远大于 64。
    更换评估种子、增加状态数可以检查相位与片组有限采样造成的变化。
    """
    rng = np.random.default_rng(seed)
    scheduler = Scheduler18Rtl(seed)
    for _ in range(18):
        scheduler.step()
    for _ in range(n // 64):
        phases = rng.permutation(64)
        words = rng.permutation(64)
        halves = rng.permutation(np.arange(64) % 2)
        coins = rng.permutation(np.arange(64) % 2)
        for phase, word, half, coin in zip(phases, words, halves, coins):
            scheduler.step()
            yield State(
                tuple(scheduler.conv),
                int(phase // 8),
                int(phase % 8),
                int(half),
                int(coin),
                int(word),
            )


@lru_cache(maxsize=256)
def switches(h, v, half, coin):
    """8 个物理槽各 20 位。复用叶子参考，避免在实验中另写 DEM/bridge 规则。"""
    return np.asarray(
        [bridge_swap(post_mask(k, h, v), h, v, half * 8, coin) for k in range(521)], dtype=np.uint8
    )


def wiring(state):
    masks = switches(state.horizontal, state.vertical, state.half, state.coin)
    preset, _, upper, lower = pre(state.word, state.vertical)
    return masks, np.asarray(preset, dtype=np.uint8), upper, lower


def sums(weights, ids, masks, preset):
    """同一组开关分别作用于模拟或数字系数。

    G 只包含采样 Vin 的主单元，Wm 包含最后一次实际 post 选中的主、子单元。
    Wp 单独取采样 pre 选中的子单元，因此分路扰动只扣回一次。
    此处保留输入 dtype，数字路径的合计仍在整数 Q30 域。
    """
    cells = weights[list(ids)]
    gain = cells[:, :16].sum()
    post = np.einsum("ksj,sj->k", masks, cells)
    before = (preset * cells[:, 16:]).sum()
    return gain, post, before


def decode(raw, g, wm, wp):
    """alpha=1、b=0 时的 v21 整数算术参考。

    原码先转换为 Q24 码箱中心，G/Wm/Wp 以装载后的 Q30 字求和。
    倒数就近舍入，乘积算术右移取 floor，最终除以 96 时恰半离零。
    溢出限幅只在最后的 20 位码域执行；原码越窗在生成观测时单列。
    本批量实现针对本实验的有界输入，乘积超出 int64 即拒绝。
    标量版 oracle 保留任意精度，独立检查一批样本的整数结果。
    """
    g = int(g)
    if g <= 0:
        raise ValueError("nonpositive loaded gain")
    inv = rounded(1 << 60, g)
    num = 64 * (-3145728 + 1536 * raw + 768) + 6 * wm - 3 * g - 6 * wp
    if max(abs(int(num.min())), abs(int(num.max()))) * inv > np.iinfo(np.int64).max:
        raise OverflowError("requested case exceeds exact vector arithmetic")
    x = (num * inv) >> 36
    quantized = np.sign(x) * ((np.abs(x) + 48) // 96) + CODES // 2
    return np.clip(quantized, 0, CODES - 1)


# =============================================================================
# 2. 独立训练。估计器只接收已知输入、片号、掩码和 ADC2 观测值。
# =============================================================================
def training_observations(device, state, x, noise):
    # 外部已知输入决定独立 SADC 的名义粗码。RDAC 的失配不能反向进入该判决。
    # 残差在节点端加噪后才转换为原码，因而训练保留后级量化误差。
    masks, preset, upper, lower = wiring(state)
    c = np.floor((x + VFS) / DELTA + lower).astype(np.int64)
    k = c + upper
    if np.any((k < 0) | (k > 520)):
        raise ValueError("training command outside the v21 guard")
    active = device[list(state.ids)]
    applied = masks[k]
    g = active[:, :16].sum()
    post = np.einsum("nsj,sj->n", applied, active)
    before = (preset * active[:, 16:]).sum()
    y = g * (x + VFS) - 6 * (post - before) + noise
    raw = np.floor((y - YMIN) / Q2).astype(np.int64)
    if np.any((raw < 0) | (raw >= N2)):
        raise ValueError("ADC2 overflow during coefficient identification")
    return c, raw, applied, preset


def fit_tables(device, nominal, seed, train_noise):
    """以独立已知输入拟合物理权重，同时训练对照用的逐粗码表。

    对每个活动主单元，线性观测系数为 x+3−6·post；子单元为 6·(pre−post)。
    训练目标为实测原码的码箱中心。模拟器的制造权重只用于产生观测。
    名义权重是拟合的起点。最小范数解在未被激励的方向上保留这个先验。
    可观测奇异值的条件数与矩阵秩同时输出，帮助判断逐单元辨识范围。
    另一张表以粗码为索引，训练目标为名义整数码的输入折算误差均值。
    它只在相同的固定阵内开关配置下评估；开扰动的留出数据不套用此表。
    """
    n_groups, per_group = 1024, 64
    n = n_groups * per_group
    rng = np.random.default_rng(seed)
    design = np.zeros((n, 18, 20))
    observed = np.empty(n)
    coarse, estimated, inputs = np.empty(n, int), np.empty(n), np.empty(n)
    known_q = np.rint(nominal * Q30).astype(np.int64)
    for i, state in enumerate(states(n_groups, seed + 1)):
        block = slice(i * per_group, (i + 1) * per_group)
        x = rng.uniform(-VFS, VFS, per_group)
        noise = rng.normal(0, train_noise, per_group)
        _, raw, applied, preset = training_observations(device, state, x, noise)
        local = -6.0 * applied.astype(float)
        local[:, :, :16] += (x + VFS)[:, None, None]
        local[:, :, 16:] += 6 * preset
        design[block][:, list(state.ids), :] = local
        observed[block] = YMIN + (raw + 0.5) * Q2

        # 同一批独立已知输入训练实验性粗码表。被评估的固定阵内开关不加扰动。
        base = state.select("fixed")
        c, raw, _, _ = training_observations(device, base, x, noise)
        masks, preset, _, _ = wiring(base)
        g, wm, wp = sums(known_q, base.ids, masks, preset)
        coarse[block], inputs[block] = c, x
        estimated[block] = (decode(raw, g, wm[c], wp) - CODES // 2) * LSB20

    # 在名义系数附近求最小范数修正。报告数值秩，不能把不可辨识方向称为已估准。
    a = design.reshape(n, -1)
    prior = nominal.ravel()
    delta, _, rank, sv = np.linalg.lstsq(a, observed - a @ prior, rcond=1e-10)
    fitted = (prior + delta).reshape(18, 20)
    if not np.all(np.isfinite(fitted)) or np.any(fitted <= 0):
        raise ValueError("nonphysical fitted weights")
    table = fit_code_correction(coarse, estimated, inputs)
    fit = dict(
        training_samples=n,
        training_states=n_groups,
        rank=int(rank),
        columns=a.shape[1],
        observable_condition=float(sv[0] / sv[rank - 1]),
        residual_rms_node_uV=float(np.sqrt(np.mean((observed - a @ fitted.ravel()) ** 2)) * 1e6),
        coarse_lut_min_observations=table.min_count,
    )
    return fitted, table, fit


# =============================================================================
# 3. 全量程积分和实际斜坡计数。所有结果均进入同一个 20 位整数输出域。
# =============================================================================
def expected_edges(edges, low, high, sigma):
    """计算饱和线性函数在高斯分布下的期望。

    low、high 是该 SADC 粗码在有限输入斜坡上占有的区间。
    对一个 ADC2 箱边界求期望，再将相邻期望相减，得到该原码的期望质量。
    sigma 以输入端 LSB20 为单位，等于 RA 节点噪声除以实际增益和 LSB20。
    靠近两轨的占用宽度包含有限激励截断。图和数据保留这部分，另报内区敏感度。
    """
    bounded = np.clip(edges, low, high)
    if sigma == 0:
        return bounded
    # E[(z+noise)_+] 与 max(z,0) 的差只靠近边界。9σ 外的修正低于浮点量级。
    for loc, sign in ((low, 1), (high, -1)):
        offset = np.abs(edges - loc) / sigma
        near = offset < 9
        a = offset[near]
        bounded[near] += sign * sigma * (np.exp(-a * a / 2) / np.sqrt(2 * np.pi) - a * ndtr(-a))
    return bounded


def accumulate(dest, codes, mass):
    """按真实整数输出码分箱。非单调跳变、重复码段或空码均保留。"""
    start = int(codes.min())
    local = np.bincount((codes - start).ravel(), weights=mass.ravel())
    dest[start : start + len(local)] += local


def histograms(device, loads, lut, state_set, mode, hits_per_code, noise_v):
    """对每个允许的粗码区间进行全跨度码密度计算。

    每个条件状态使用一条均匀输入分布，等权混合得到这一组片号/字的传递特性。
    粗码边界可直接由 SADC 的已知单调码本求出，包括正向扰动进入的 guard。
    在每个粗码内，线性残差将每条 ADC2 边界映成一个输入电压。
    物理权重决定边界；数字估计只决定这个原码会被分给哪个最终整数码。

    将同一码的所有输入区间宽度相加，可处理粗码交界的重叠、跳码和倒退。
    第二条路径在这些半开区间内计数等距采样点，为输入点总数提供独立校验。
    分块限定工作内存。所有状态之间使用互补的采样偏移，避免斜坡与码边界锁定。
    理想输入范围之外的 Gaussian 尾部单独做原码溢出核查。
    """
    names = list(loads)
    if mode == "fixed" and lut is not None:
        names.append("coarse_lut")
    masses = {n: np.zeros(CODES) for n in names}
    hits = {n: np.zeros(CODES) for n in names}
    noisy = {n: np.zeros(CODES) for n in names} if noise_v else {}
    backwards = {name: dict(total=0, reversed=0, largest_drop=0) for name in names}
    invalid_mass = 0.0
    extrema = [N2, -1]
    nstates = len(state_set)
    density = hits_per_code // nstates
    if not density or hits_per_code % nstates:
        raise ValueError("ramp density must be a multiple of the number of states")

    for si, original in enumerate(state_set):
        state = original.select(mode)
        masks, preset, upper, lower = wiring(state)
        g, wm, wp = sums(device, state.ids, masks, preset)
        digital = {n: sums(w, state.ids, masks, preset) for n, w in loads.items()}
        c = np.arange(520)
        lo = np.maximum(0.0, (c - lower) * 2048.0)
        hi = np.minimum(float(CODES), (c + 1 - lower) * 2048.0)
        keep = lo < hi
        c, lo, hi = c[keep], lo[keep], hi[keep]
        k = c + upper
        if np.any(k > 520):
            raise ValueError("valid analog input requested an illegal switch command")
        # 输入坐标 u=(Vin+3 V)/LSB20。原码 r 的左边界 a+b*r 由实际节点电荷决定。
        a = (YMIN + 6 * (wm[k] - wp)) / (g * LSB20)
        b = Q2 / (g * LSB20)
        first = np.floor((lo - a) / b).astype(int)
        stop = np.ceil((hi - a) / b).astype(int)
        if np.any(first < 0) or np.any(stop > N2):
            raise ValueError("deterministic ADC2 overflow: rejected full-range coverage")
        extrema = [min(extrema[0], int(first.min())), max(extrema[1], int(stop.max() - 1))]
        sigma = noise_v / (g * LSB20)
        if sigma:
            low_edge = expected_edges(a, lo, hi, sigma)
            high_edge = expected_edges(a + b * N2, lo, hi, sigma)
            invalid_mass += float(np.sum(low_edge - lo + hi - high_edge))
        pad = int(np.ceil(9 * noise_v / Q2)) + 1 if sigma else 1
        starts, ends = np.maximum(0, first - pad), np.minimum(N2, stop + pad)
        offset = (si + 0.5) / nstates
        for row in range(0, len(c), 16):
            ix = slice(row, row + 16)
            cmd = k[ix]
            raw_edges = np.minimum(
                starts[ix, None] + np.arange(1 + int((ends[ix] - starts[ix]).max())), ends[ix, None]
            )
            analog_edges = a[ix, None] + b * raw_edges
            bounded = np.clip(analog_edges, lo[ix, None], hi[ix, None])
            widths = np.diff(bounded, axis=1)
            # 数落在每个半开区间的网格点。所有状态合起来为均匀的全码斜坡，
            # 相邻输入点轮换状态。无需为每个点重复求解同一组开关电荷。
            counts = np.diff(np.ceil(bounded * density - offset), axis=1)
            if sigma:
                with_noise = np.diff(
                    expected_edges(analog_edges, lo[ix, None], hi[ix, None], sigma), axis=1
                )
            raw = raw_edges[:, :-1]
            for name in names:
                key = "nominal" if name == "coarse_lut" else name
                gg, mm, pp = digital[key]
                result = decode(raw, gg, mm[cmd, None], pp)
                if name == "coarse_lut":
                    # 冻结逐码电压偏置后重新量化至 20 位，避免与浮点后处理混比。
                    correction = np.rint(lut.table_v[c[ix]] / LSB20).astype(np.int64)
                    result = np.clip(result - correction[:, None], 0, CODES - 1)
                accumulate(masses[name], result, widths)
                accumulate(hits[name], result, counts)
                if sigma:
                    accumulate(noisy[name], result, with_noise)
        # 同一状态的整段传递是否在粗码边界倒退，与混合直方图缺码按不同口径统计。
        for name in names:
            gg, mm, pp = digital["nominal" if name == "coarse_lut" else name]
            left = decode(first, gg, mm[k], pp)
            right = decode(stop - 1, gg, mm[k], pp)
            if name == "coarse_lut":
                correction = np.rint(lut.table_v[c] / LSB20).astype(np.int64)
                left = np.clip(left - correction, 0, CODES - 1)
                right = np.clip(right - correction, 0, CODES - 1)
            drop = right[:-1] - left[1:]
            audit = backwards[name]
            audit["total"] += len(drop)
            audit["reversed"] += int(np.count_nonzero(drop > 0))
            audit["largest_drop"] = max(audit["largest_drop"], int(drop.max()))
    results = {}
    for name in names:
        if int(hits[name].sum()) != CODES * hits_per_code:
            raise ValueError("lost ramp samples")
        results[name] = (
            masses[name] / nstates,
            hits[name].astype(np.int64),
            noisy[name] / nstates if noise_v else None,
        )
    return results, backwards, extrema, invalid_mass / (nstates * CODES)


def linearity(widths, rail_codes=1):
    """使用端点直线归一化码宽与过渡。

    输入宽度以名义 LSB20 表示。首、末码的宽度包含到激励边界的半开区间。
    标准结果取 T1 与 T(N−1)，只从 DNL 极值中排除两端饱和码。
    保留全部输入质量以检查覆盖。噪声诊断的内区指标按其自己的端点重新定标。
    含状态混合或倒退时，累积占用定义等效过渡位置，不能视为逐状态单调证明。
    """
    if np.any(widths < -1e-9) or not np.isclose(widths.sum(), CODES, rtol=0, atol=1e-5):
        raise ValueError("invalid probability mass or incomplete transfer")
    interior = widths[rail_codes:-rail_codes]
    step = interior.mean()
    dnl = interior / step - 1
    # 逐码先减去端点斜率，再积累真实偏差。累加一百万个约为 1 的宽度
    # 后才减去码号会放大浮点舍入，尤其在校准后误差远小于一个码宽时。
    inl = np.r_[0.0, np.cumsum(interior - step)] / step
    return dnl, inl


def stats(widths, rail_codes=1):
    dnl, inl = linearity(widths, rail_codes)
    return dict(
        dnl_min=float(dnl.min()),
        dnl_max=float(dnl.max()),
        inl_min=float(inl.min()),
        inl_max=float(inl.max()),
        missing_interior=int(np.count_nonzero(widths[rail_codes:-rail_codes] == 0)),
        worst_dnl_code=rail_codes + int(np.argmax(np.abs(dnl))),
        worst_inl_transition=rail_codes + int(np.argmax(np.abs(inl))),
    )


def validate(device, fitted_q, sample_states):
    """以两种粒度复核加速统计。

    标量采样调用独立的逐次判决、电荷求和和 Python 整数 oracle。
    全码域的逐点斜坡直接算每个输入的粗码、后级原码和出码，再逐码对直方图。
    两条路径要给出完全相同的命中数，不能只比较最大值或平均误差。
    接近浮点精度极限的理想阈值允许相邻舍入，整数重构断言仍逐笔严格相等。
    """
    rng = np.random.default_rng(901)
    count = 0
    direct = np.zeros(CODES, np.int32)
    state = sample_states[0]
    for state_ in sample_states:
        masks, preset, upper, lower = wiring(state_)
        x = np.r_[rng.uniform(-VFS, VFS, 22), -VFS, np.nextafter(VFS, -np.inf)]
        c, raw, _, _ = training_observations(device, state_, x, 0.0)
        gg, mm, pp = sums(fitted_q, state_.ids, masks, preset)
        vec = decode(raw, gg, mm[c + upper], pp)
        for i in range(len(x)):
            coarse, _ = sadc(x[i] + lower * DELTA)
            # 在 +FS 的浮点邻近值，两个代数等价的加法顺序可恰落相邻 SADC 边界。
            if (
                c[i] != coarse
                and abs((x[i] + VFS) / DELTA + lower - round((x[i] + VFS) / DELTA + lower)) > 1e-10
            ):
                raise AssertionError("coarse decisions disagree")
            main, sw, pr = fwd_dac(device * CF, state_.ids, masks[c[i] + upper], preset)
            y = (main * (x[i] + VFS) - 6 * (sw - pr)) / CF
            refraw = int(np.floor((y - YMIN) / Q2))
            if refraw != raw[i] and abs((y - YMIN) / Q2 - round((y - YMIN) / Q2)) > 1e-8:
                raise AssertionError("node and plant disagree")
            ref = oracle(int(raw[i]), 0, Q30, int(gg), int(mm[c[i] + upper]), int(pp))
            if ref["code"] != vec[i]:
                raise AssertionError("v21 integer oracle mismatch")
            count += 1
    # 完整 20 位输入范围的 1,048,576 个独立前向样本，逐码与区间计数完全一致。
    masks, preset, upper, lower = wiring(state)
    gg, mm, pp = sums(fitted_q, state.ids, masks, preset)
    g, wm, wp = sums(device, state.ids, masks, preset)
    for start in range(0, CODES, 32768):
        u = np.arange(start, start + 32768) + 0.5
        c = np.floor(u / 2048 + lower).astype(int)
        raw = np.floor((g * LSB20 * u - 6 * (wm[c + upper] - wp) - YMIN) / Q2).astype(np.int64)
        if np.any((raw < 0) | (raw >= N2)):
            raise AssertionError("unexpected range flag in direct ramp")
        codes = decode(raw, gg, mm[c + upper], pp)
        np.add.at(direct, codes, 1)
    got, _, _, _ = histograms(device, {"fitted": fitted_q}, None, [state], "both", 1, 0.0)
    if not np.array_equal(direct, got["fitted"][1]):
        raise AssertionError("direct full-span ramp disagrees with bin counting")
    return count


# =============================================================================
# 4. 输出。每组完整数据独立落盘；图中每个横向像素区间保留其码段极值。
# =============================================================================
def save_figure(target, rows, sigma, result):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    colors = {"nominal": "#ad4f18", "fitted": "#005b8c"}
    with plt.rc_context(
        {
            "font.family": "DejaVu Sans",
            "font.size": 8,
            "axes.labelsize": 8,
            "xtick.labelsize": 7,
            "ytick.labelsize": 7,
            "svg.fonttype": "none",
            "svg.hashsalt": "sar-v21-linearity",
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.linewidth": 0.7,
        }
    ):
        fig, axs = plt.subplots(2, 2, figsize=(7.16, 4.66), sharex=True, sharey="row")
        fig.subplots_adjust(
            left=0.085, right=0.988, bottom=0.115, top=0.82, wspace=0.15, hspace=0.53
        )
        for column, mode in enumerate(("fixed", "both")):
            for row, metric in enumerate(("INL", "DNL")):
                ax = axs[row, column]
                for key, name in (("nominal", "Nominal weights"), ("fitted", "Fitted weights")):
                    u, lower, upper = rows[(mode, key, metric)]
                    color = colors[key]
                    ax.fill_between(u, lower, upper, color=color, alpha=0.12, linewidth=0)
                    ax.plot(
                        u,
                        lower,
                        color=color,
                        linewidth=0.65,
                        linestyle="-" if key == "fitted" else "--",
                        label=name,
                    )
                    ax.plot(
                        u,
                        upper,
                        color=color,
                        linewidth=0.65,
                        linestyle="-" if key == "fitted" else "--",
                    )
                ax.axhline(0, color="#353535", linewidth=0.4, zorder=0)
                label = "abcd"[2 * row + column]
                title = "Fixed in-slice phases" if mode == "fixed" else "DEM + split dither"
                before, after = [peak(result, mode, n, metric) for n in ("nominal", "fitted")]
                ax.set_title(
                    f"({label})  {title}", fontsize=8.5, loc="left", fontweight="bold", pad=16
                )
                ax.text(
                    0,
                    1.025,
                    f"Peak |{metric}|   {before:.3f} → {after:.3f} LSB",
                    transform=ax.transAxes,
                    ha="left",
                    va="bottom",
                    fontsize=7.2,
                    color="#374151",
                )
                ax.grid(axis="y", color="#d7dee3", linewidth=0.45)
                if column == 0:
                    ax.set_ylabel(f"{metric}  [LSB20]")
                if row == 1:
                    ax.set_xlabel("20-bit code  [×10⁶]")
                ax.set_xticks([0, 0.25, 0.5, 0.75, 1])
                ax.set_xlim(0, CODES / 1e6)
        handles, labels = axs[0, 0].get_legend_handles_labels()
        fig.legend(
            handles, labels, ncol=2, frameon=False, loc="upper center", bbox_to_anchor=(0.53, 0.997)
        )
        for ext in ("png", "svg"):
            fig.savefig(
                target / f"linearity-{sigma:g}.{ext}",
                dpi=300,
                metadata={"Date": None} if ext == "svg" else {},
            )
        plt.close(fig)


def peak(result, mode, decoder, metric):
    rec = result["modes"][mode]["decoders"][decoder]["exact"]
    key = metric.lower()
    return max(abs(rec[key + "_min"]), abs(rec[key + "_max"]))


def save_comparison(out, source, sigma, result):
    """上排逐项拆开技术效果，下排画同一段输出码的实际宽度。"""
    import matplotlib.pyplot as plt

    orange, blue, charcoal = "#aa5026", "#09688a", "#344652"
    with plt.rc_context(
        {
            "font.family": "DejaVu Sans",
            "font.size": 8,
            "axes.labelsize": 8,
            "xtick.labelsize": 7,
            "ytick.labelsize": 7,
            "axes.linewidth": 0.7,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "svg.fonttype": "none",
            "svg.hashsalt": "sar-static-effects",
        }
    ):
        fig = plt.figure(figsize=(7.16, 5.03))
        grid = fig.add_gridspec(
            2,
            2,
            height_ratios=(1, 1.08),
            hspace=0.68,
            wspace=0.43,
            left=0.16,
            right=0.965,
            top=0.88,
            bottom=0.195,
        )
        for col, metric in enumerate(("INL", "DNL")):
            ax = fig.add_subplot(grid[0, col])
            for row, mode in enumerate(MODES):
                old, new = [peak(result, mode, method, metric) for method in ("nominal", "fitted")]
                ax.plot([new, old], [row, row], color="#bdc9cf", lw=1.4, zorder=1)
                ax.scatter(
                    old,
                    row,
                    marker="o",
                    s=24,
                    facecolor="white",
                    edgecolor=orange,
                    zorder=3,
                    label="Nominal weights" if row == 0 else None,
                )
                ax.scatter(
                    new,
                    row,
                    marker="D",
                    s=23,
                    color=blue,
                    zorder=4,
                    label="Fitted weights" if row == 0 else None,
                )
            ax.set_yticks(
                range(4),
                ("Fixed phases", "DEM", "Split dither", "DEM + dither") if col == 0 else [""] * 4,
            )
            ax.set_ylim(3.5, -0.5)
            bound = max(
                peak(result, mode, method, metric)
                for mode in MODES
                for method in ("nominal", "fitted")
            )
            ax.set_xlim(-0.025 * bound, 1.06 * bound)
            ax.grid(axis="x", color="#d7dee3", lw=0.45)
            ax.set_xlabel("Peak absolute error  [LSB20]")
            ax.set_title(
                f"({'ab'[col]})  {metric} by control mode",
                loc="left",
                fontsize=8.5,
                fontweight="bold",
                pad=6,
            )
            if col == 0:
                fig.legend(
                    *ax.get_legend_handles_labels(),
                    loc="upper center",
                    ncol=2,
                    frameon=False,
                    bbox_to_anchor=(0.53, 1.005),
                )
        ax = fig.add_subplot(grid[1, :])
        worst = result["modes"]["fixed"]["decoders"]["nominal"]["exact"]["worst_dnl_code"]
        center = max(17, min(CODES - 18, worst))
        codes = np.arange(center - 16, center + 17)
        ax.axhline(1, color=charcoal, lw=0.65, ls=":")
        for mode, name, label, color, style in (
            ("fixed", "nominal", "Fixed · nominal", orange, "--"),
            ("fixed", "fitted", "Fixed · fitted", charcoal, "-."),
            ("both", "fitted", "DEM + dither · fitted", blue, "-"),
        ):
            with np.load(source / f"{mode}-{name}.npz") as data:
                width = data["input_width_lsb20_by_code"]
            ax.step(
                codes - worst, width[codes], where="mid", label=label, color=color, ls=style, lw=1.3
            )
        ax.set_xlim(codes[0] - worst - 0.5, codes[-1] - worst + 0.5)
        ax.set_xlabel(f"Output code minus {worst:,}")
        ax.set_ylabel("Code width\n[nominal LSB20]")
        ax.grid(axis="y", color="#d7dee3", lw=0.45)
        ax.set_title(
            "(c)  Individual code widths at the worst uncalibrated DNL",
            loc="left",
            fontsize=8.5,
            fontweight="bold",
            pad=6,
        )
        ax.legend(
            frameon=False, ncol=3, loc="upper center", bbox_to_anchor=(0.5, -0.36), fontsize=6.7
        )
        for ext in ("png", "svg"):
            fig.savefig(
                out / f"effects-{sigma:g}.{ext}",
                dpi=300,
                metadata={"Date": None} if ext == "svg" else {},
            )
        plt.close(fig)


def envelope(values):
    """显示用横坐标取码段中心，每段同时返回上下界以保留单码尖峰。"""
    # 仅用于显示。指标和导出的数据保留所有内部码和过渡点。
    groups = np.array_split(values, 512)
    x = (np.cumsum([len(a) for a in groups]) - np.array([len(a) for a in groups]) / 2) / 1e6
    return x, np.array([v.min() for v in groups]), np.array([v.max() for v in groups])


def run_case(out, sigma, seed, test_seed, nstates, density, noise):
    """组装一颗器件，冻结系数后依次执行消融。

    训练和留出使用不同的种子。相同制造种子下改变 sigma，保持同一组标准化偏差。
    理想器件控制用于检查半码偏置和 rail 处理，误差要降到数值零。
    nominal 与 fitted 两组始终共享同一批原码，oracle 对照只用于解释残余误差。
    数据按模式和系数来源分别输出，避免把稀疏图表当成完整逐码数据。
    """
    target = out / f"sigma-{sigma:g}"
    target.mkdir()
    nominal = np.asarray(make_w(0.0, seed)) / CF
    device = np.asarray(make_w(sigma, seed)) / CF
    fitted, table, diagnostics = fit_tables(device, nominal, seed + 2101, noise)
    loads = {
        name: np.rint(value * Q30).astype(np.int64)
        for name, value in (("nominal", nominal), ("fitted", fitted), ("oracle", device))
    }
    # oracle 只作真值上界；fitted 来自观测，两者使用相同的硬件 Q30 字格式。
    for coeff in loads.values():
        if np.any(coeff < 0) or np.any(coeff >= 1 << 40) or np.any(coeff[:, 16:] > 128 * Q30):
            raise ValueError("coefficient is outside the weight configuration contract")
    eval_states = list(states(nstates, test_seed))
    check_points = validate(device, loads["fitted"], eval_states[:8])
    # 理想名义器件、nominal 编码及当前量化窗口必须复原理想的内部码宽。
    ideal, _, _, _ = histograms(
        nominal, {"nominal": loads["nominal"]}, None, [eval_states[0]], "both", 8, 0.0
    )
    ideal_stats = stats(ideal["nominal"][0])
    if max(abs(ideal_stats[k]) for k in ("dnl_min", "dnl_max", "inl_min", "inl_max")) > 1e-6:
        raise AssertionError(f"failed ideal converter endpoint control: {ideal_stats}")
    result = {
        "relative_mismatch_sigma": sigma,
        "seed": seed,
        "training": diagnostics,
        "scalar_reference_points": check_points,
        "direct_ramp_samples": CODES,
        "ideal": ideal_stats,
        "modes": {},
    }
    np.savez_compressed(
        target / "coefficients.npz",
        fitted_q30=loads["fitted"],
        nominal_q30=loads["nominal"],
        truth_oracle_q30=loads["oracle"],
        coarse_lut_v=table.table_v,
    )
    traces = {}
    for mode in MODES:
        # 精确已知器件重构的上界只需在全开配置展示；其它配置比较可估的权重。
        active = dict(loads) if mode == "both" else {k: loads[k] for k in ("nominal", "fitted")}
        outputs, backwards, extrema, lost = histograms(
            device, active, table, eval_states, mode, density, noise
        )
        if lost > 1e-12:
            raise ValueError(f"ADC2 Gaussian overflow mass {lost}, full-range INL not qualified")
        report = {
            "adc2_raw_min_max_without_noise": extrema,
            "gaussian_adc2_overflow_fraction": lost,
            "decoders": {},
        }
        for name, (widths, hits, with_noise) in outputs.items():
            empirical = stats(hits / density)
            measured = stats(widths)
            dnl, inl = linearity(widths)
            dh, ih = linearity(hits / density)
            evidence = dict(
                exact=measured,
                counted=empirical,
                min_count_interior=int(hits[1:-1].min()),
                max_count_interior=int(hits[1:-1].max()),
                count_total=int(hits.sum()),
                count_minus_exact_max_DNL=float(np.abs(dh - dnl).max()),
                count_minus_exact_max_INL=float(np.abs(ih - inl).max()),
                coarse_boundary_checks=backwards[name],
            )
            data = {"input_width_lsb20_by_code": widths, "ramp_hits_by_code": hits}
            if with_noise is not None:
                evidence["ra_noise_expected"] = stats(with_noise)
                # 另报离开两轨各 64 码且重新定端点的值，以识别有限斜坡边缘偏差。
                # 全跨度结果始终保留，端点敏感性不用于代替主指标。
                evidence["ra_noise_excluding_64_rail_codes"] = stats(with_noise, 64)
                data["expected_width_lsb20_with_ra_noise"] = with_noise
            np.savez_compressed(target / f"{mode}-{name}.npz", **data)
            report["decoders"][name] = evidence
            if name in ("nominal", "fitted") and mode in ("fixed", "both"):
                traces[(mode, name, "INL")] = envelope(inl)
                traces[(mode, name, "DNL")] = envelope(dnl)
        result["modes"][mode] = report
        print(
            f"sigma={sigma:g} {mode}: {len(outputs)} decoders, {density*CODES:,} input points",
            flush=True,
        )
    save_figure(out, traces, sigma, result)
    save_comparison(out, target, sigma, result)
    (target / "summary.json").write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
    return result


def main():
    """运行一次可重放的实验并保存数据来源。

    输出目录须是新目录，避免不同种子的数组与摘要混写。
    多状态平均要求按完整 64 字设计取样，斜坡密度还须可整分到每一种状态。
    Gaussian 的期望结果与确定性命中计数分别保存。所有数组均可重新计算端点指标。
    摘要记录实际执行文件的哈希，允许从尚未提交的工作树复核数值。
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True, help="新目录，完整数值数组写在其内")
    parser.add_argument("--states", type=int, default=64, help="独立状态样本数，64 的正整数倍")
    parser.add_argument(
        "--hits-per-code", type=int, default=512, help="每个理想码宽的均匀斜坡样本数"
    )
    parser.add_argument(
        "--sigma", type=float, nargs="+", default=[1e-4, 1e-3], help="器件相对失配标准差"
    )
    parser.add_argument("--seed", type=int, default=20261105)
    parser.add_argument("--evaluation-seed", type=int, default=317011)
    parser.add_argument(
        "--ra-noise-v", type=float, default=0.001, help="训练及含噪声对照的 RA 输出端 RMS"
    )
    args = parser.parse_args()
    if (
        args.states < 64
        or args.states % 64
        or args.hits_per_code <= 0
        or args.hits_per_code % args.states
        or args.hits_per_code * CODES > 2**53
        or not np.isfinite(args.ra_noise_v)
        or args.ra_noise_v < 0
        or any(not np.isfinite(v) or v < 0 for v in args.sigma)
        or len(set(args.sigma)) != len(args.sigma)
    ):
        parser.error("invalid state count, ramp density, noise, or mismatch")
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    report = dict(
        scope="v21 static charge contract and D5 integer oracle; full-code behavioral linearity",
        output_bits=20,
        input_span_v=[-VFS, VFS],
        adc2_bits=13,
        adc2_window_v=[YMIN, YMIN + SPAN],
        lsb20_v=LSB20,
        states=args.states,
        evaluation_seed=args.evaluation_seed,
        input_samples_per_mode=CODES * args.hits_per_code,
        hits_per_ideal_code=args.hits_per_code,
        training_and_expected_ra_noise_rms_v=args.ra_noise_v,
        seed=args.seed,
        git_base_sha=subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        source_sha256={
            n: hashlib.sha256((ROOT / n).read_bytes()).hexdigest() for n in SOURCE_FILES
        },
        python=platform.python_version(),
        numpy=np.__version__,
        scipy=scipy.__version__,
        cases=[],
    )
    for sigma in args.sigma:
        report["cases"].append(
            run_case(
                out,
                sigma,
                args.seed,
                args.evaluation_seed,
                args.states,
                args.hits_per_code,
                args.ra_noise_v,
            )
        )
    (out / "summary.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")


if __name__ == "__main__":
    main()
