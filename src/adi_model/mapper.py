"""mapper.py -- 分段编码、DEM、dither 码修改。

DEM 接口：

    map_code(coarse_code, selected_slices, dem_state) -> (switch_commands, next_dem_state)

**最重要的验收条件**：

    DAC_nominal(M(c, state)) == V_target(c)     对任意 state 成立

本实现里，DEM 只做**等权单位的位置置换**（横向 3b：slice 轮转；纵向 3b：slice 内
segment / sub-unit 轮转），因此"选中的单位个数 == 逻辑码"恒成立，
名义值严格守恒是**结构性保证**，而不是靠调参调出来的。

反面例子：直接打乱 1C/2C/4C 的位控制再自称 DEM —— 那样名义值会变，不是 DEM。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .config import Config

# 3 个 DEM 维度各 8 个状态（3b）：slice 轮转 x 横向 segmentation x 纵向 segmentation
N_DEM_STATES = 8 * 8 * 8
_LCG_A = 2_654_435_761  # 奇数，与 512 互质 -> n*a mod 512 遍历全部状态


def dem_state_sequence(n_samples: int, cfg: Config, bank: np.ndarray | None = None) -> np.ndarray:
    """返回每个样本的 DEM 状态索引（int64，值域 [0, 512)）。DEM 关闭时恒为 0。

    **状态必须按 bank 内的序号推进，不能按全局样本序号。**
    全局推进时 bank A 只落在偶数 n 上，而 gcd(2A, 512) = 2，
    于是每个 bank 只遍历 256/512 个状态 —— 另一半 LUT 永远用不到。
    按 bank 内序号推进后，两个 bank 各自完整遍历全部 512 个状态。

    Args:
        n_samples: 样本数 N。
        cfg:       dem_enable=False 时直接返回全 0（DEM 关 = 固定顺序）。
        bank:      (N,) 逐样本 bank 标签；None = 无 bank 概念（全局推进，
                   仅用于无需交织的实验）。
    Returns:
        (N,) int64 状态索引。Side effects: 无（LCG 是确定性映射）。
    """
    if not cfg.dem_enable:
        return np.zeros(n_samples, dtype=np.int64)
    n = np.arange(n_samples, dtype=np.int64)
    if bank is None:
        seq = n
    else:
        b = np.asarray(bank, dtype=np.int64)
        # 每个 bank 内部独立计数
        seq = np.zeros(n_samples, dtype=np.int64)
        for bv in np.unique(b):
            m = b == bv
            seq[m] = np.arange(int(m.sum()), dtype=np.int64)
    return (seq * _LCG_A) % N_DEM_STATES


def unit_rank_arrays(cfg: Config):
    """返回 (slice_rank, unit_idx)，形状均为 (N_DEM_STATES, n_units_sig)。

    * slice_rank：8 个激活 slice 的轮转（横向 / slice 选择维度）
    * unit_idx  ：slice 内 64 个单位的顺序（3b 横向 segment x 3b 纵向 sub-unit）

    单位在整片激活阵列中**交错**排列：码值 +1 先给下一个 slice 加一个单位，
    这样任意码值 >= 8 时所有 slice 都参与，DEM 平均效果最好。
    """
    n_u = cfg.n_units_sig
    n_act = cfg.n_active
    n_up = cfg.n_unit_per_slice
    # headroom 情况下只使用中间连续的一段作为信号量程
    if n_u > n_act * n_up:
        raise ValueError("n_units_sig 超过物理单位数")

    j = np.arange(n_u)
    sid = np.arange(N_DEM_STATES)
    s = (sid // 64)[:, None]
    h = ((sid // 8) % 8)[:, None]
    v = (sid % 8)[:, None]

    slice_rank = (j[None, :] % n_act + s) % n_act  # (S, n_u)
    seg = (j[None, :] // n_act) // 8  # slice 内 segment
    sub = (j[None, :] // n_act) % 8  # segment 内 sub-unit
    n_seg = n_up // 8
    unit_idx = 8 * ((seg + h) % n_seg) + ((sub + v) % 8)  # (S, n_u)
    return slice_rank.astype(np.int64), unit_idx.astype(np.int64)


@dataclass
class SwitchCommand:
    """数字侧发给物理 RDAC 的指令。"""

    k: np.ndarray  # (N,) 需要导通的**单位个数**（名义值由此唯一确定）
    bank: np.ndarray  # (N,) 使用哪一组 slice
    sid: np.ndarray  # (N,) DEM 状态
    dither_code: np.ndarray  # (N,) 码域 dither（v1 输入注入模式下为 0）


class Mapper:
    """unary 编码器：逻辑粗码 -> 开关指令（不含任何物理信息）。

    单位契约：coarse_code [码仓号]；k / k0 / dither_code [单位当量]。
    Side effects: 无。
    """

    def __init__(self, cfg: Config):
        """保存配置；码表在需要时按 cfg 生成。

        Args:
            cfg: Config；取 n_units_sig / units_per_lsb1 / n_units_headroom
                等（[推导]）。本类只做数字映射，不含任何物理信息。
        """
        self.cfg = cfg

    def encode(
        self,
        coarse_code: np.ndarray,
        bank: np.ndarray,
        sid: np.ndarray,
        dither_code: np.ndarray | None = None,
    ) -> SwitchCommand:
        """粗码 -> SwitchCommand（k 为信号码，dither 单列）。

        Args:
            coarse_code: (N,) SADC 粗码 [码仓号]。
            bank: (N,) bank 标签（透传，不参与编码）。
            sid: (N,) DEM 状态（透传，不参与编码）。
            dither_code: (N,) [单位当量]；None = 全 0。
        Returns:
            SwitchCommand。k = coarse·units_per_lsb1 + k0。
        历史教训（勿回退）：SwitchCommand.k 是**信号码**；dither_code 单独
        存放，由 rdac 在两个求值里统一加上。（曾在 encode 里先加一次、
        rdac 再加一次，导致 dither 计入两遍、SNDR 崩到 50 dB。）
        """
        coarse_code = np.asarray(coarse_code)
        bank = np.asarray(bank)
        sid = np.asarray(sid)
        if coarse_code.ndim == 0 or bank.ndim == 0 or sid.ndim == 0:
            # 标量输入会让下游的 len()/广播以难懂形式爆掉（旧行为：
            # `TypeError: len() of unsized object`），这里显式给出口径
            # （独立审查 2026-09-25 第二轮）。
            raise ValueError(
                "coarse_code/bank/sid 必须是 1-D 数组；标量输入请用 np.atleast_1d 包装"
            )
        if not (len(coarse_code) == len(bank) == len(sid)):
            raise ValueError(
                f"coarse/bank/sid 长度不一致：{len(coarse_code)}/{len(bank)}/{len(sid)}"
                "——长度错位会在下游产生静默错配（独立审查 2026-09-25）"
            )
        n_code = 2**self.cfg.b1
        if len(coarse_code) and (
            not np.all(np.isfinite(coarse_code))
            or coarse_code.min() < 0
            or coarse_code.max() >= n_code
        ):
            raise ValueError(f"粗码越界 [0, {n_code})：[{coarse_code.min()}, {coarse_code.max()}]")
        k0 = self.cfg.n_units_headroom // 2  # 低端 dither 余量
        k = (coarse_code * self.cfg.units_per_lsb1 + k0).astype(float)
        if dither_code is None:
            dither_code = np.zeros_like(k, dtype=float)
        return SwitchCommand(
            k=k, bank=bank, sid=sid, dither_code=np.asarray(dither_code, dtype=float)
        )

    # ---------------- 验收用 ----------------
    def nominal_conservation_check(
        self, coarse_code: np.ndarray, bank: np.ndarray, sid: np.ndarray
    ) -> dict:
        """同一逻辑码、不同 DEM 状态下，选中的单位个数必须恒定。

        Args:
        coarse_code: 逻辑粗码数组 [码仓号]。
        bank: 逐样本 bank 标签数组（透传）。
        sid: 逐样本 DEM 状态数组 [int]。

        Returns:
        字典：ok [bool]（所有码的单位个数是否跨 DEM 状态恒定）；
        k_per_code [dict: int 码 -> int 单位个数]，任一状态不一致则为 None。
        Side effects: 无（纯函数，只读 cfg 与输入）。

        Notes:
        数字侧守恒是**结构性保证**：DEM 只做等权单位位置置换，选中单位
        个数恒等于逻辑码，故名义 DAC 值对任意 DEM 状态不变（勿回退）。
        """
        cmd = self.encode(coarse_code, bank, sid)
        ok = True
        detail = {}
        for c in np.unique(coarse_code):
            m = coarse_code == c
            ks = np.unique(cmd.k[m])
            detail[int(c)] = int(ks[0]) if ks.size == 1 else None
            ok &= ks.size == 1
        return {"ok": bool(ok), "k_per_code": detail}


# ---------------- dither ----------------
@dataclass
class DitherState:
    """模拟注入 / 码修改 / 数字扣除必须成对存在。"""

    analog_injection: np.ndarray  # 加在模拟输入上的已知量
    code_modification: np.ndarray  # 码域修改（v1 为 0）
    digital_correction: np.ndarray  # 最终扣除量


def make_dither_state(cfg: Config, analog: np.ndarray) -> DitherState:
    """按工作模式构造 dither 状态（模拟注入量、码域量与扣除量）。

    Args:
        cfg: Config；取 dither_mode（off / analog / sampling）。
        analog: 模拟注入量数组 [V]（dither 已知量）。

    Returns:
        DitherState：analog_injection / code_modification / digital_correction
        三个数组 [V]。off 模式三者全 0；analog 模式注入量 == 扣除量
        （理想可减基准，码域为 0）。三者必须成对存在、符号相反相消。
    Side effects: 无（纯函数）。
    """
    if cfg.dither_mode == "off":
        return DitherState(np.zeros_like(analog), np.zeros_like(analog), np.zeros_like(analog))
    # 理想可减 dither 基准：注入量 == 扣除量（这是基准，不是 ADI 的完整实现）
    return DitherState(
        analog_injection=analog, code_modification=np.zeros_like(analog), digital_correction=analog
    )


def dither_transfer_code(
    cfg: Config,
    dither_volts: np.ndarray,
    *,
    step_rdac: float,
    step_coarse: float,
    dither_code_sampling: np.ndarray | None = None,
) -> np.ndarray:
    """Convert a dither quantity into an RDAC **unit-step** code offset.

    Single source of truth for the quantizer-dither bookkeeping. Both main
    loops (``sim_split`` and ``pipeline``) must call this rather than each
    interpreting the interface; a divergence between them produced a 42.7 uV
    error in v6.1 that took an external audit to find.

    History (audit finding F3)
    --------------------------
    v6.1 computed ``d_code = -round(d / gran)`` and added it directly to
    ``coarse * units_per_lsb1``, which is expressed in **RDAC unit steps**.
    With ``gran = step_coarse`` the result was in **coarse-step** units, so the
    sum mixed two unit systems and was wrong by the factor
    ``step_coarse / step_rdac`` (8 in the default configuration). Correcting
    only this conversion moved the stage-21 coarse-transfer branch from
    32.7% to 16.0% ADC2 overflow and from 16308 uV to 5699 uV RMS error, and
    flipped that check from PASS to FAIL.

    Args:
        cfg: Configuration; uses ``dither_mode`` and ``dither_quant_transfer``.
        dither_volts: ``(N,)`` float, the dither quantity in volts.
        step_rdac: RDAC unit step [V] (the unit system of the returned code).
        step_coarse: First-stage quantiser step [V] (``delta1``).
        dither_code_sampling: For ``dither_mode == "sampling"``, the mask code
            already produced by the sampler, in RDAC unit steps.

    Returns:
        ``(N,)`` float, dither offset in **RDAC unit steps**, ready to be added
        to ``coarse * cfg.units_per_lsb1``.

    Raises:
        ValueError: If a step is non-positive (which would make the conversion
            factor undefined) or if ``sampling`` mode is requested without a
            mask code.
    """
    if step_rdac <= 0.0:
        raise ValueError(f"step_rdac must be > 0, got {step_rdac!r}")
    d = np.asarray(dither_volts, dtype=float)

    if cfg.dither_mode == "sampling":
        if dither_code_sampling is None:
            raise ValueError("sampling dither requires dither_code_sampling")
        return np.asarray(dither_code_sampling, dtype=float)
    if cfg.dither_mode != "quantizer":
        return np.zeros_like(d)

    if cfg.dither_transfer_model == "dual_port":
        # Both physical ports are included in capture. Coarse decisions contain
        # d_Q, stored RDAC charge contains d_R; command offset is (d_R-d_Q)/step.
        # Digital reconstruction separately subtracts d_R. Omitting either port
        # would turn amplitude enhancement into an incorrect code multiplier.
        return np.round((cfg.dither_rdac_ratio - 1.0) * d / step_rdac)

    if cfg.dither_transfer_model == "range":
        # "range" model (paper-literal). The disclosure is: "the dither range
        # is enhanced by 2b when the result is transferred from the quantizer
        # to the RDAC".
        #
        # Modelling note (corrected): the 2^b is a *headroom* statement, NOT an
        # amplitude gain. Physically the dither is injected ahead of the
        # quantiser and then *cancelled* by the RDAC, so the code offset is the
        # exact unit conversion -round(d / step_rdac). What the disclosure says
        # is that the RDAC can represent a dither range 2^b times wider than one
        # quantiser step, i.e.
        #     log2(units_per_d1) == cfg.dither_enhancement_bits,
        # with units_per_d1 = DAC levels / 2**b1. An earlier revision of this
        # branch multiplied the dither amplitude by 2**enh, which made the
        # cancellation wrong by that factor and pushed the residue out of the
        # ADC2 window; that was a modelling error, not the paper's mechanism.
        # The capability itself is *reported* (see experiments.stage21) rather
        # than claimed to be experimentally closed -- the audit explicitly warns
        # against using a granularity experiment to prove a range statement.
        return -np.round(d / step_rdac)

    # "granularity" model (v6.1 legacy): choose the grid at which the transfer
    # is quantised ...
    gran = step_rdac if cfg.dither_quant_transfer == "rdac" else step_coarse
    if gran <= 0.0:
        raise ValueError(f"granularity must be > 0, got {gran!r}")
    code_in_gran_units = -np.round(d / gran)
    # ... and convert that count into RDAC unit steps. This factor is the fix.
    return code_in_gran_units * (gran / step_rdac)
