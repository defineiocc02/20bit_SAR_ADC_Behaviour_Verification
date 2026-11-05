"""粗细码名义重构与已知输入的前台标量校准。

x_hat=(vD0+v2/Ghat-d_corr)/alpha：v2为ADC2解码后的RA输出节点V，
vD0/d_corr为输入等效V，Ghat/alpha无量纲。先扣注入再补偿信号衰减。
名义alpha不能自动消除实际mask失配；逐物理unit的冻结C/Cf重构在weight_calibration。

数字端不得读取真实电容/增益。这里的标量gain/beta学习不等于逐slice时偏/带宽学习，
也不是原芯片内部校准电路或KTC/CAAZ晶体管实现。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np

from .config import Config

if TYPE_CHECKING:
    from .weight_calibration import FrozenCalibration


@dataclass
class DigitalState:
    """数字域系数状态。

    estimated_gain/kappa/beta无量纲；W_nominal是名义RDAC步长[V/单位码]，
    W_estimated为保留接口；有效unit权重另存weight_calibration的冻结C/Cf矩阵。
    history仅供调用方记录，不参与物理plant。
    """

    estimated_gain: float
    kappa: float
    beta: float
    W_nominal: np.ndarray | None = None  # 名义权重（DAC 单位步长）
    W_estimated: np.ndarray | None = None  # 保留的标量接口；unit有效权重见weight_calibration
    history: list | None = None
    weight_calibration: FrozenCalibration | None = None


def initialize_state(cfg: Config) -> DigitalState:
    """按名义参数初始化数字状态，不复制物理芯片真值。

    Args:
        cfg: 配置；g0、kappa_eff、beta_eff和名义RDAC步长提供初值。

    Returns:
        DigitalState，history为空，weight_calibration尚未装载。
    """
    return DigitalState(
        estimated_gain=cfg.g0,
        kappa=cfg.kappa_eff(),
        beta=cfg.beta_eff(),
        W_nominal=np.array([cfg.rdac_step]),
        history=[],
    )


def reconstruct(
    vd_nominal: np.ndarray,
    fine_voltage: np.ndarray,
    g_hat: float,
    dither_correction: np.ndarray,
    alpha: float = 1.0,
) -> np.ndarray:
    """名义电压域重构x_hat=(vD0+v2/Ghat-d_corr)/alpha。

    Args:
        vd_nominal: (N,)名义RDAC输出，输入等效V。
        fine_voltage: (N,)ADC2解码值，RA输出节点V。
        g_hat: 数字端标量增益估计，无量纲，不能默认等于每笔真实G。
        dither_correction: (N,)已知名义注入扣除量，输入等效V。
        alpha: 名义采样信号衰减，无量纲；非sampling通常为1。

    Returns:
        (N,)输入等效V。只补名义衰减，后端噪声/静态DAC误差亦按1/alpha放大；
        没有在此对波形重新量化或校正真实电容失配。
    """
    return (vd_nominal + fine_voltage / g_hat - dither_correction) / alpha


class Calibrator:
    """使用已知外部输入的前台回归；不声明参考芯片的内部学习实现。"""

    def __init__(self, cfg: Config, state: DigitalState):
        """绑定配置与数字状态（前台校准用）。

        Args:
            cfg:   模型配置（Config），提供 dither 相关口径与校准开关。
            state: 共享的 DigitalState；校准结果写回其 estimated_gain /
                   beta / kappa。
        Side effects: 无（仅绑定引用）。
        """
        self.cfg = cfg
        self.state = state

    def update_gain(
        self,
        x_known: np.ndarray,
        vd0: np.ndarray,
        fine: np.ndarray,
        d_known: np.ndarray | None = None,
        alpha: float | None = None,
    ) -> float:
        """用已知输入的残差回归拟合一个有效标量增益。

        模型为fine≈G*(alpha*x+d_nom-vD0)。不能对已重构out与x直接拟合，
        因为vD0的阶梯信号项会掩盖级间增益偏差。该模型不辨识每个unit的失配。

        Args:
            x_known: (N,)已知外部校准输入[V]。
            vd0: (N,)名义RDAC输入等效输出[V]。
            fine: (N,)ADC2解码值[RA输出V]。
            d_known: 可选(N,)名义注入[输入等效V]；有注入时调用方必须提供。
            alpha: 可选名义衰减；None表示信号倍率1，只乘x，不乘d或vD0。

        Returns:
            当前正增益估计；回归能量为0或拟合值非正时保留旧值。

        Side effects:
            只更新state.estimated_gain；不读取真实G/C，不改采样或噪声模型。
        """
        x = np.asarray(x_known, dtype=float)
        # 只有输入信号乘alpha；注入d与名义DAC输出不经过该信号衰减。
        r = alpha * x if alpha is not None else x
        r = r - np.asarray(vd0, dtype=float)
        if d_known is not None:
            r = r + np.asarray(d_known, dtype=float)
        f = np.asarray(fine, dtype=float)
        denom = float(np.dot(r, r))
        if denom <= 0:
            return self.state.estimated_gain
        g = float(np.dot(f, r) / denom)
        if g > 0:
            self.state.estimated_gain = g
        return self.state.estimated_gain

    def update_beta(self, out: np.ndarray, x1: np.ndarray, dx: np.ndarray) -> float:
        """以固定外部目标x1估计KTC有效beta并比例更新kappa。

        beta_hat=<out-x1,dx>/<dx,dx>。目标x1不能预先含未知beta，
        也不读取cfg.g_actual来替代估计。

        Args:
            out: (N,)含噪重构结果[输入V]。
            x1: (N,)固定外部目标x(t1)[输入V]。
            dx: (N,)已知窗口变化x2-x1[输入V]。

        Returns:
            beta_hat，无量纲。

        Side effects:
            写state.beta；beta_hat>0时按kappa_old/beta_hat更新state.kappa。
        """
        from .ktc import KTCBranch

        b = KTCBranch.estimate_beta(np.asarray(out) - np.asarray(x1), dx)
        self.state.beta = b  # 估计值（注意：这是"当前实际 beta"的估计）
        if b > 0:
            self.state.kappa = self.state.kappa / b
        return b
