"""数字命令与dither单位换算；不读取制造电容或估计器的隐藏真值。

Mapper用于coarse code到RDAC单位信号码的兼容映射；split阵列的实际mask由dem.split_switch_command生成。
dem_state_sequence是按bank分别推进的512状态确定性遍历，unit_rank_arrays是64等权unary几何助手。
512状态覆盖不证明当前63+8 split单元等使用；物理slice选择还由独立allocator决定。

dither_transfer_code只返回RDAC单位的改码量；
采样注入和最终数字扣除在sampler/pipeline/reconstruction配对处理。quantizer/range的d_R为0，
不能把所有模式都写成最终减d_Q。具体双端口、range与granularity按各自单位合同解释。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .config import Config

# 512状态：unary助手按3×3b解码；split助手按行/列/子阵列解码。
N_DEM_STATES = 8 * 8 * 8
_LCG_A = 2_654_435_761  # 奇数，与 512 互质 -> n*a mod 512 遍历全部状态


def dem_state_sequence(n_samples: int, cfg: Config, bank: np.ndarray | None = None) -> np.ndarray:
    """生成每笔样本的9位DEM状态，范围0..511；关闭时全0。

    Args:
        n_samples: raw样本数N。
        cfg: 读取dem_enable。
        bank: (N,) 相位/组标签；None表示按全局序号推进，不含固定物理ID含义。

    Returns:
        (N,) int64。每个bank以自己的序号乘互质步长再mod512，足够长时各自覆盖
        全部状态。若错误使用全局偶数序号，则某bank只能走一半状态。
        状态覆盖与物理单元等使用是不同判据，尤其63单元缺格与bridge门控存在相关性。

    Side effects:
        无RNG或持久状态；每次调用从各bank的序号0开始。
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
    """生成64等权unary兼容几何的逻辑slot与单元顺序表。

    Returns:
        (slice_rank, unit_idx)，均为(512, n_units_sig) int64；slice_rank是活动组内
        逻辑slot，unit_idx是该slice内的单元编号。相邻单位先分配给不同slot。

    Raises:
        ValueError: n_units_sig超过声明的活动物理单位数。

    Notes:
        该助手不是63+8 split译码器，也不负责18-slice物理调度或验证统计白化。
    """
    n_u = cfg.n_units_sig
    n_act = cfg.n_active
    n_up = cfg.n_unit_per_slice
    # 信号单位数量不能超过所声明的活动阵列容量。
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
    """兼容RDAC命令；k是信号码，dither_code单列，不能在上下游各加一次。"""

    k: np.ndarray  # (N,) 需要导通的**单位个数**（名义值由此唯一确定）
    bank: np.ndarray  # (N,) 相位/组标签；实际物理slice由allocator给出
    sid: np.ndarray  # (N,) DEM 状态
    dither_code: np.ndarray  # (N,) RDAC单位改码量，与信号码k独立


class Mapper:
    """coarse码仓号到名义RDAC单位信号码的编码器。

    只进行数字映射：k=coarse_code*units_per_lsb1+k0。bank/sid透传，不选择物理slice，
    也不学习实际权重。
    """

    def __init__(self, cfg: Config):
        """保存名义Config；不抽取制造失配或读取物理plant。"""
        self.cfg = cfg

    def encode(
        self,
        coarse_code: np.ndarray,
        bank: np.ndarray,
        sid: np.ndarray,
        dither_code: np.ndarray | None = None,
    ) -> SwitchCommand:
        """将粗量化码转为独立信号码与dither改码字段。

        Args:
            coarse_code: (N,) SADC整数码仓号，范围[0, 2**b1)。
            bank: (N,) 相位/组标签；透传，不参与单位换算。
            sid: (N,) DEM状态；透传，由后级据此生成具体mask。
            dither_code: (N,) RDAC单位改码量；None表示0。

        Returns:
            SwitchCommand。k不含dither；后级只加一次dither_code。
            这里的单位数守恒不等于split阵列每个物理unit等使用。

        Raises:
            ValueError: 标量、向量长度不匹配、非有限/越界/非整数coarse code。

        Side effects:
            无；数组按一维接口使用。
        """
        coarse_code = np.asarray(coarse_code)
        bank = np.asarray(bank)
        sid = np.asarray(sid)
        if coarse_code.ndim == 0 or bank.ndim == 0 or sid.ndim == 0:
            # 标量没有逐raw长度，先在数字接口边界拒绝。
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
        # 域内分数粗码也非法：它会产生不可实现的分数信号码。
        if len(coarse_code) and bool(
            np.any(
                np.asarray(coarse_code, dtype=float)
                != np.round(np.asarray(coarse_code, dtype=float))
            )
        ):
            raise ValueError(
                "粗码必须是整数（SADC 只输出整数码）；分数粗码（如 2.5）会产生非整数"
                "单位数 k（独立审查 2026-09-25）"
            )
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
        """检查同一coarse code映射出的名义信号单位数不随状态改变。

        Args:
            coarse_code: (N,) SADC整数码仓号。
            bank: (N,) 透传相位标签。
            sid: (N,) DEM状态。

        Returns:
            ok与逐coarse码的k_per_code；发现同码产生多个单位数时该项为None。

        Notes:
            仅检查Mapper的信号码单位合同，不计算split mask频次、物理电荷或最终性能。
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
    """已知注入与数字扣除的诊断字段；实际RDAC改码由dither_transfer_code给出。

    analog_injection/digital_correction为输入等效电压[V]；重构器显式减后者，字段本身不预先取负。
    code_modification在本构造器中是兼容占位0，不代替真实改码。
    """

    analog_injection: np.ndarray  # 加在模拟输入上的已知量
    code_modification: np.ndarray  # 兼容诊断占位0；实际RDAC改码由转移函数给出
    digital_correction: np.ndarray  # 最终扣除量


def make_dither_state(cfg: Config, analog: np.ndarray) -> DitherState:
    """构造dither诊断状态，最终模式相关扣除仍由runner设置。

    Args:
        cfg: 读取dither_mode。
        analog: (N,) 已知的输入等效注入量[V]。

    Returns:
        off三字段全0；非off以相同正号保存注入和初始扣除量。
        pipeline对sampling覆盖成名义采样mask电荷，对quantizer覆盖成d_R；
        结构range的d_R=0，不能沿用初始d_Q作为最终扣除。

    Side effects:
        无；不产生模拟注入，也不修改物理电容。
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
    """将已知dither换算成可与信号码相加的RDAC单位改码量。

    Args:
        cfg: 模式及转移合同；range/dual_port/granularity含义不同。
        dither_volts: (N,) 输入等效d_Q或已知扰动[V]。
        step_rdac: ΔDAC[V/RDAC单位]；返回值始终使用该单位。
        step_coarse: Δ1[V/粗码仓]，仅granularity的coarse网格分支使用。
        dither_code_sampling: sampling已由采样mask生成的RDAC单位码。

    Returns:
        (N,) 浮点改码量。sampling透传mask码；quantizer/range为
        -round(d_Q/ΔDAC)，其保存通路d_R=0；dual_port为
        round((d_R-d_Q)/ΔDAC)，随后数字重构另减d_R。
        granularity先在所选电压网格取整，再换算到RDAC单位，避免混合Δ1和ΔDAC。
        off/analog不在这里改变RDAC码。该换算不证明论文完整2b范围增强。

    Raises:
        ValueError: ΔDAC<=0、sampling缺少mask码，或选用的granularity网格<=0。

    Side effects:
        无；调用方负责同一raw的注入、改码、实际mask与扣除项对齐。
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
        # range只有量化器端d_Q，保存通路d_R=0；用相同RDAC单位消去决策扰动。
        # 不乘2**enhancement_bits，也不以此换算宣称完整范围增强已经验证。
        return -np.round(d / step_rdac)

    # granularity先在RDAC或粗码电压网格取整，再换算为RDAC单位。
    gran = step_rdac if cfg.dither_quant_transfer == "rdac" else step_coarse
    if gran <= 0.0:
        raise ValueError(f"granularity must be > 0, got {gran!r}")
    code_in_gran_units = -np.round(d / gran)
    # 取整结果乘电压网格/ΔDAC，才能与RDAC信号码使用同一单位。
    return code_in_gran_units * (gran / step_rdac)
