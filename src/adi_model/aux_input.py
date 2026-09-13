"""aux_input.py -- 专利 [13] US 10,541,702 B1 的辅助输入与电荷核算。

============================================================================
专利披露的机制（机制级转述，原文不入库；见 NOTICE 引用原则）
============================================================================
问题：输入开关是双栅 FET，栅/背栅存在非线性寄生电容（[13] FIG.1 的
12/14/16）。采集相位合上开关时这些寄生电容也要充/放电——传统上这份
电荷只能经过输入 RC 滤波器（R_f, C_f）从驱动器取，于是滤波器带宽必须
做宽。专利背景给出的量级（[13] p.12 左栏背景段）：LTC2387-18 采
50 kHz 低频信号也要求 **77 MHz** 滤波带宽——低频输入仍可能要求很宽的
前级滤波带宽（否则寄生电荷建立不到位），宽带宽直接抬高采样噪声与
驱动功耗。

三个对策（本模块各对应一种模式）：
* **dedicated_pin**（FIG.2）：片外辅助输入引脚 V_IN_AUX 经辅助开关
  （采集相通、转换相接地开关）直接给背栅寄生充电——寄生电荷**完全不
  经过 R_f**，滤波器只需为信号电荷服务。
* **opamp_midpoint**（FIG.3）：运算放大器（理想高输入阻抗）接在 R_f
  与 C_f 之间的节点上，从滤波器内部低阻节点取电给开关寄生充电，
  "不打扰滤波器"。
* **gate_boost**（FIG.4/5）：栅自举电路在采集相把 V_GS 固定在
  C_boost 充到的电压差上（披露例 3.3 V）-> r_on 与信号无关 -> 开关
  导通电阻的码调制（本仓库 ``dyn_ron_code_coeff`` 刻画的那一项）归零。
  **本模块把它实现为"理想自举对照"**：只关闭 V_GS 调制项，忽略阈值
  漂移、体效应、有限 V_DS、自举电压建立误差等其余信号相关因素——
  零是理想化假设的结果，不是已验证的器件结论（第八份复核口径）。

============================================================================
本实现的口径（勿夸大）
============================================================================
* 电荷核算 + 一阶建立标度律，不是晶体管级模型：
    - 无辅助：寄生电荷与信号电荷都走 R_f，寄生支路时间常数
      τ_off = R_f·(C_f + C_pg)。
    - dedicated_pin / opamp_midpoint：寄生支路由低阻辅助通路供电，
      τ_aux = R_aux·C_pg（R_aux 为辅助通路等效电阻，[假设] 小电阻），
      滤波器支路只剩 τ_on = R_f·C_f。
  由此：相同建立目标 ε 下，R_f 允许放大 (C_f+C_pg)/C_f 倍，
  f_3dB 同比例下降，驱动器噪声 en·sqrt(π/2·f_3dB) 降 sqrt 倍，
  驱动器每样本电荷减少 C_pg·ΔV 的份额。
* C_pg（背栅寄生总量）无披露值 -> 全部数值结论 [假设] 比例 +
  [推导] 标度律；机制结构 [披露]（[13]）。
* 本模块保留独立标度律。主链路的实际状态实现见
  input_network.InputNetworkParameters 的 auxiliary_* 参数和 ADR 0012；
  不使用这里的电荷比例直接推算主链路噪声收益。

Examples:
-------
>>> from adi_model.config import Config
>>> from adi_model.aux_input import AuxInputStage, build_stage
>>> cfg = Config()
>>> st = build_stage(cfg, c_parasitic_ratio=0.02)
>>> st.required_filter_bw("off", eps=0.01) > st.required_filter_bw("dedicated_pin", eps=0.01)
True
>>> round(st.r_filter_max_ratio, 6) == round((st.c_filter + st.c_parasitic) / st.c_filter, 6)
True
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .config import Config
from .provenance import SourceGrade

__all__ = [
    "AUX_MODES",
    "AuxInputStage",
    "build_stage",
    "AUX_GRADES",
]

AUX_MODES = ("off", "dedicated_pin", "opamp_midpoint", "gate_boost")

AUX_GRADES = {
    "mechanism": (SourceGrade.DISCLOSED, "[13] US 10,541,702 B1: auxiliary input charge"),
    "modes": (
        SourceGrade.DISCLOSED,
        "[13] FIG.2 (dedicated pin) / FIG.3 (op-amp midpoint) / FIG.4-5 (gate boost)",
    ),
    "ltc2387_anchor": (
        SourceGrade.DISCLOSED,
        "[13] p.12 background: 77 MHz filter BW required even for a 50 kHz"
        " signal (LTC2387-18); page per patent PDF",
    ),
    "c_signal": (SourceGrade.DERIVED, "c_active_nominal() of this repository"),
    "c_parasitic_ratio": (SourceGrade.ASSUMED, "back-gate parasitics; no published number"),
    "r_aux": (SourceGrade.ASSUMED, "auxiliary path resistance; no published number"),
    "boost_voltage": (SourceGrade.DISCLOSED, "[13] FIG.4: 3.3 V first plate of C_boost"),
}


@dataclass(frozen=True)
class AuxInputStage:
    """输入级电荷核算参数（[13] 的机制旋钮）。

    Attributes:
        c_signal: 采样网络信号电容 [F]（[推导] 锚到本仓库活跃采样电容）。
        c_filter: RC 滤波器电容 [F]。
        c_parasitic: 输入开关背栅寄生电容总量 [F]（[假设] 比例）。
        r_filter: RC 滤波器电阻 [Ω]。
        r_aux: 辅助通路等效电阻 [Ω]（dedicated_pin/opamp 模式生效）。
        t_acq: 采集窗口时长 [s]（= ``dyn_t_sample_frac/fs`` 的口径）。
        boost_voltage: 栅自举提供的恒定 V_GS 电压差 [V]（[披露] 例 3.3 V）。
        mode: 当前核算的模式（AUX_MODES 之一）。
    """

    c_signal: float
    c_filter: float
    c_parasitic: float
    r_filter: float
    r_aux: float
    t_acq: float
    boost_voltage: float
    mode: str = "off"

    def validated(self) -> AuxInputStage:
        """拒绝非法模式与非正参数（入口显式拒绝）。

        Returns:
            AuxInputStage: 原样返回。

        Raises:
            ValueError: mode 非法或存在非正电容/电阻/时间。
        """
        if self.mode not in AUX_MODES:
            raise ValueError(f"mode={self.mode!r} 不是已实现取值（可选：{', '.join(AUX_MODES)}）")
        for name, v in (
            ("c_signal", self.c_signal),
            ("c_filter", self.c_filter),
            ("c_parasitic", self.c_parasitic),
            ("r_filter", self.r_filter),
            ("t_acq", self.t_acq),
        ):
            if v <= 0:
                raise ValueError(f"{name}={v!r} 必须为正")
        return self

    # ------------------------------------------------------------ 建立标度律
    def parasitic_tau(self, mode: str | None = None) -> float:
        """寄生电荷支路的一阶时间常数 [s]。

        ``off``    ：寄生电荷只能经 R_f -> τ = R_f·(C_f + C_pg)。
        ``dedicated_pin`` / ``opamp_midpoint``：低阻辅助通路 -> τ = R_aux·C_pg。
        ``gate_boost``：自举不改变寄生支路，返回与 off 相同的 τ
            （它的收益在 r_on 调制，见 :meth:`ron_modulation_factor`）。

        Args:
            mode: 覆盖 self.mode（None = 用 self.mode）。

        Returns:
            float: 时间常数 [s]。
        """
        m = mode or self.mode
        if m in ("dedicated_pin", "opamp_midpoint"):
            return self.r_aux * self.c_parasitic
        return self.r_filter * (self.c_filter + self.c_parasitic)

    def signal_tau(self) -> float:
        """信号电荷支路的一阶时间常数 [s] = R_f·C_f（三种模式相同）。

        Returns:
            float: 时间常数 [s]。
        """
        return self.r_filter * self.c_filter

    def aux_residual(self, t_aux: float) -> float:
        """辅助通路在 t_aux 后的残余比例 exp(-t_aux/τ_aux)（[推导]）。

        Args:
            t_aux: 辅助通路可用建立时间 [s]（> 0）。

        Returns:
            float: 残余比例 [无量纲]（1 = 完全没建立）。

        Raises:
            ValueError: t_aux 非正。
        """
        if t_aux <= 0:
            raise ValueError(f"t_aux={t_aux} 必须为正")
        tau = self.parasitic_tau("dedicated_pin")
        return float(math.exp(-t_aux / tau))

    def aux_ready(self, eps_aux: float = 0.01) -> bool:
        """辅助通路能否在采集窗口内建立到 eps_aux（[推导] 可行性条件）。

        约束：τ_aux = R_aux·C_pg ≤ t_acq/ln(1/ε_aux)。**带宽收益的
        成立前提**（第八份外部复核补齐）：辅助通路若来不及建立，
        "寄生电荷由低阻辅助通路供给"的前提不成立，
        :meth:`required_filter_bw` 会拒绝报告收益而不是静默给出。

        Args:
            eps_aux: 辅助支路建立残差目标 [无量纲]。

        Returns:
            bool: 可行 = True。

        Raises:
            ValueError: eps_aux 不在 (0,1)。
        """
        if not 0 < eps_aux < 1:
            raise ValueError(f"eps_aux={eps_aux} 必须在 (0,1)")
        tau_aux = self.parasitic_tau("dedicated_pin")
        return tau_aux <= self.t_acq / math.log(1.0 / eps_aux)

    def required_filter_bw(
        self,
        mode: str | None = None,
        eps: float = 0.01,
        eps_aux: float = 0.01,
    ) -> float:
        """给定建立目标 ε，采集窗口内建立到位所需的**最低** 3dB 带宽 [Hz]。

        建立约束：支路时间常数 τ = R_f·C_branch ≤ t_acq/ln(1/ε)
        -> R_f 有上限 -> f_3dB = 1/(2π·R_f·C_f) 有**下界**（带宽低于它
        就建立不到位）。辅助供电接管寄生支路后 C_branch 从 C_f+C_pg
        降为 C_f，R_f 上限放大 (C_f+C_pg)/C_f 倍，带宽下界同比例下降——
        这正是 [13] "允许更低滤波器带宽"的定量化。

        **可行性前提**：辅助模式下先检查辅助支路自身能在窗口内建立
        （τ_aux ≤ t_acq/ln(1/ε_aux)）；不满足时抛 ValueError——
        慢辅助通路不得仍无条件报告带宽收益（第八份外部复核）。

        Args:
            mode: 覆盖 self.mode。
            eps: 信号支路建立残差目标 [无量纲]。
            eps_aux: 辅助支路建立残差目标 [无量纲]（仅辅助模式检查）。

        Returns:
            float: 最低要求 3dB 带宽 [Hz]。

        Raises:
            ValueError: eps 不在 (0,1)；辅助模式下辅助支路建立不可行。
        """
        if not 0 < eps < 1:
            raise ValueError(f"eps={eps} 必须在 (0,1)")
        if (mode or self.mode) in ("dedicated_pin", "opamp_midpoint"):
            if not self.aux_ready(eps_aux):
                tau_aux = self.parasitic_tau("dedicated_pin")
                raise ValueError(
                    "辅助通路在采集窗口内建立不可行："
                    f"τ_aux=R_aux·C_pg={tau_aux:.3e}s 超过 "
                    f"t_acq/ln(1/eps_aux)={self.t_acq / math.log(1.0 / eps_aux):.3e}s"
                    "（带宽收益前提不成立；减小 R_aux 或放宽 eps_aux）"
                )
            r_f_max = self.t_acq / (math.log(1.0 / eps) * self.c_filter)
        else:
            r_f_max = self.t_acq / (math.log(1.0 / eps) * (self.c_filter + self.c_parasitic))
        return 1.0 / (2.0 * math.pi * r_f_max * self.c_filter)

    @property
    def r_filter_max_ratio(self) -> float:
        """辅助供电允许 R_f 放大的倍数 = (C_f + C_pg)/C_f（[推导]）。

        Returns:
            float: 倍数（> 1）。
        """
        return (self.c_filter + self.c_parasitic) / self.c_filter

    @property
    def bw_ratio(self) -> float:
        """允许带宽的压缩比 = C_f/(C_f + C_pg) = 1/r_filter_max_ratio（[推导]）。

        Returns:
            float: < 1。
        """
        return 1.0 / self.r_filter_max_ratio

    def driver_noise_ratio(self, en_density: float = 1.0) -> float:
        """驱动器噪声比值（辅助 vs 无辅助）= sqrt(bw_ratio)（[推导]）。

        一阶 RC + 平坦输入噪声 en 的输出噪声 = en·sqrt(π/2·f_3dB)，
        因此比值只依赖带宽比。en_density 入参只为量纲提示，不参与比值。

        Args:
            en_density: 驱动器噪声密度 [V/√Hz]（比值中约掉；默认 1）。

        Returns:
            float: 噪声 rms 比（< 1）。
        """
        return math.sqrt(self.bw_ratio)

    def driver_charge_per_sample(self, dv: float) -> dict[str, float]:
        """每样本驱动器必须提供的电荷（无辅助 vs 有辅助）。

        无辅助：Q = (C_f + C_pg)·ΔV（寄生电荷也经驱动器）。
        有辅助（dedicated_pin/opamp）：Q = C_f·ΔV（寄生由辅助通路供给）。
        gate_boost 与 off 相同（自举省的是 r_on 调制，不是电荷）。

        Args:
            dv: 采集瞬间节点电压摆幅 [V]。

        Returns:
            dict: ``off_coulomb`` / ``aux_coulomb`` / ``ratio``。

        Raises:
            ValueError: dv 为负。
        """
        if dv < 0:
            raise ValueError(f"dv={dv} 不能为负")
        q_off = (self.c_filter + self.c_parasitic) * dv
        q_aux = self.c_filter * dv
        return {
            "off_coulomb": q_off,
            "aux_coulomb": q_aux,
            "ratio": (q_aux / q_off) if q_off > 0 else float("nan"),
        }

    def ron_modulation_factor(self) -> float:
        """理想自举对照下的 r_on 信号调制因子（gate_boost = 0，其余 = 1）。

        **口径（第八份外部复核）**：0 是"理想自举对照"的结果——固定
        V_GS 只关闭 V_GS 调制这一项；阈值漂移、体效应、有限 V_DS、
        自举电压建立误差等其余信号相关因素本模型一律未保留。因此
        这是理想化假设下的对照值，**不是**"真实开关 r_on 完全不随
        信号变化"的器件结论。

        Returns:
            float: 0（理想自举对照）或 1（调制保留）。
        """
        return 0.0 if self.mode == "gate_boost" else 1.0


def build_stage(
    cfg: Config,
    c_parasitic_ratio: float = 0.02,
    r_filter: float = 100.0,
    c_filter: float | None = None,
    r_aux: float = 1.0,
    boost_voltage: float = 3.3,
    mode: str = "off",
) -> AuxInputStage:
    """从仓库配置构造一个辅助输入核算级。

    Args:
        cfg: 模型配置；c_signal 锚到 ``c_active_nominal()``（[推导]），
            t_acq 取 ``dyn_t_sample_frac/fs`` 口径。
        c_parasitic_ratio: 背栅寄生相对信号电容的比例 [假设]。
        r_filter: 滤波器电阻 [Ω] [假设]。
        c_filter: 滤波器电容 [F]；None = 信号电容的 1/10 [假设]。
        r_aux: 辅助通路电阻 [Ω] [假设]。
        boost_voltage: 栅自举电压差 [V]（[披露] 例 3.3 V）。
        mode: 初始模式（AUX_MODES 之一）。

    Returns:
        AuxInputStage: 已校验的核算级。

    Raises:
        ValueError: 参数非法。
    """
    c_sig = cfg.c_active_nominal()
    return AuxInputStage(
        c_signal=c_sig,
        c_filter=c_filter if c_filter is not None else 0.1 * c_sig,
        c_parasitic=c_parasitic_ratio * c_sig,
        r_filter=r_filter,
        r_aux=r_aux,
        t_acq=cfg.dyn_t_sample_frac / cfg.fs,
        boost_voltage=boost_voltage,
        mode=mode,
    ).validated()
