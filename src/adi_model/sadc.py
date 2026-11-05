"""独立SADC的静态阈值行为模型。

在SADC采样输入节点[V]对已声明阈值数组作searchsorted，得到0..n_code-1
粗码；标准工厂n_code=2**b1，显式阈值则为len(thresholds)-1。这是flash式行为替代，不含逐位SAR时序。7bit对照、9bit候选与dither
已知端口是不同概念；实际构造统一经build_first_stage_quantizer。

采样热噪声/响应误差由sampler或生产输入网络提供，噪声和物理负载使用c_sadc；
sadc_cap_ratio是保留且不活跃的参数。粗码误差只有在后级有足够残差余量时才能
被重构吸收；越过SADC输入范围时粗码钳位，不能保证RA/ADC2仍然合法。

阈值及输入单位为SADC节点V；粗码无量纲。阈值失调/增益/非线性每芯片构造一次，
不是原芯片量化器电路的晶体管或动态验证。
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

import numpy as np

from .config import Config, ConfigError


@runtime_checkable
class SplitDacLike(Protocol):
    """The part of a segmented DAC that the first stage depends on.

    Declared structurally on purpose: ``dac_arch`` imports this module's
    neighbours, so a concrete import here would close a cycle. Anything that
    exposes ``levels`` and ``_nominal_endpoints()`` satisfies the contract,
    which is exactly what the first stage needs and nothing more.
    """

    levels: int

    def _nominal_endpoints(self) -> tuple[float, float]:
        """Return the nominal DAC output range ``(v_lo, v_hi)`` [V].

        Returns:
            元组 (v_lo, v_hi)：标称 DAC 输出下限与上限（V）。
            实现方只需暴露标称栅格，不得暴露物理真值。
        """
        ...


class SADC:
    """粗量化器：N+1 个阈值 -> N 个码仓的 flash 式转换。

    Attributes:
        thresholds: (n_code+1,)严格单调实际阈值[SADC节点V]，convert不改写。
        n_code:     码仓数 = 2^b1（显式阈值模式 = len(thresholds)-1）。
    """

    def __init__(
        self,
        cfg: Config,
        rng: np.random.Generator | None = None,
        thresholds: np.ndarray | None = None,
        gain_center: float = 0.0,
    ):
        """两种构造模式：

        Args:
            cfg:        全局配置。
            rng:        阈值失配的生成器；None 时按 cfg.seed+977 播种。
                        （失败保守化：调用方传入 rng 则必须自己保证
                        与其它失配的独立性——本模块不复用主 rng。）
            thresholds: 显式名义阈值 [V]；同样施加配置中的全部非理想性。
            gain_center: 增益失配的名义电压中心 [V]，不能来自物理真值。
        Raises:
            ConfigError: 名义或实际阈值非有限/非单调，或者增益非正。
        """
        self.cfg = cfg
        if thresholds is not None:
            thr = np.array(thresholds, dtype=float, copy=True)
        else:
            thr = -cfg.v_fs + np.arange(2**cfg.b1 + 1) * cfg.delta1
        if (
            thr.ndim != 1
            or thr.size < 2
            or not np.all(np.isfinite(thr))
            or np.any(np.diff(thr) <= 0)
        ):
            raise ConfigError("SADC nominal thresholds must be finite and strictly increasing")
        if not np.isfinite(gain_center) or 1 + cfg.sadc_rdac_gain_mismatch <= 0:
            raise ConfigError("SADC gain must be positive and its center finite")
        self.nominal_thresholds = thr.copy()
        n_code = len(thr) - 1
        c = np.arange(n_code + 1)

        if cfg.sadc_mismatch_enable and cfg.sadc_mismatch_sigma > 0:
            rng = rng or np.random.default_rng(cfg.seed + 977)
            # 每个单位步长的偏差，累积成阈值误差，再去掉端点增益（保持满幅不变）
            # —— 即把失配分解为"失调 + 增益 + 纯非线性"三项中的后一项。
            step_dev = rng.normal(0.0, cfg.sadc_mismatch_sigma, n_code)
            w = np.concatenate([[0.0], np.cumsum(step_dev)])
            w = w - (c / n_code) * w[-1]
            thr = thr + w

        # 失调 + 与 RDAC 的增益失配（增益失配会被 G 放大，直接吃掉 ADC2 余量；
        # 预算检查见 config.validate["SADC/RDAC 失配占用余量"]）
        self.thresholds = gain_center + (thr - gain_center - cfg.sadc_offset) / (
            1.0 + cfg.sadc_rdac_gain_mismatch
        )
        if not np.all(np.isfinite(self.thresholds)) or np.any(np.diff(self.thresholds) <= 0):
            raise ConfigError("SADC physical thresholds must be finite and strictly increasing")
        self.n_code = n_code

    def convert(self, x: np.ndarray) -> np.ndarray:
        """Flash 式转换：x 落入哪个码仓。

        Args:
            x: 输入电压 [V]（SADC 采样口径，含量化器通路误差时由调用方加）。
        Returns:
            与x形状相同的int64粗码，正常runner为(N,)；越界钳位到0或n_code-1。
        Side effects: 无（阈值只读）。
        """
        v = np.asarray(x, dtype=float)
        if np.any(~np.isfinite(v)):
            raise ValueError(
                "SADC input voltage must be finite"
                "（与 ADC2.convert 同口径；此前 NaN 被 searchsorted 静默排到末仓，"
                "独立审查 2026-09-25）"
            )
        code = np.searchsorted(self.thresholds, v, side="right") - 1
        return np.clip(code, 0, self.n_code - 1).astype(np.int64)


def units_per_first_stage_step(cfg: Config, dac: SplitDacLike | None = None) -> int:
    """第一级一个判决步长 = 多少个 DAC 单位步。

    这是 ``stage1_reading`` 与 DAC 拓扑之间的**唯一**换算关系，任何需要
    它的人都必须调用本函数，不得自行推导（外部审计 B16）。

    Args:
        cfg: 全局配置。
        dac: 分段拓扑的 :class:`~adi_model.dac_arch.SplitDAC`；等权拓扑传
             ``None``。

    Returns:
        单位步数（``int``，至少为 1）。
        等权拓扑下等于 ``cfg.units_per_lsb1`` = ``n_units_sig // 2**b1``；
        分段拓扑下等于 ``dac.levels // 2**b1``。两种口径在拓扑自洽时相等。
    """
    if dac is None:
        return max(int(cfg.units_per_lsb1), 1)
    return max(int(dac.levels) // (2**cfg.b1), 1)


def build_first_stage_quantizer(cfg: Config, dac: SplitDacLike | None = None) -> SADC:
    """构造与当前名义RDAC栅格对齐的独立SADC。

    Args:
        cfg: 决策位数及SADC非理想配置。
        dac: 可选split名义接口；不读取真实电容来生成数字判决基准。

    Returns:
        SADC。split阈值thr[c]=v_lo+c*(units_per_decision*ΔDAC)，
        无饱和/失配时k=coarse*units_per_decision对齐该阈值；unary使用Δ1。
        输入dither或SADC非理想可把残差推出名义bin，仍需单独检查RA/ADC2余量。
    """
    if dac is None:
        return SADC(cfg)
    v_lo, v_hi = dac._nominal_endpoints()
    levels = int(dac.levels)
    step0 = (v_hi - v_lo) / (levels - 1.0)
    unit = units_per_first_stage_step(cfg, dac)
    thr = v_lo + np.arange(2**cfg.b1 + 1) * (unit * step0)
    return SADC(cfg, thresholds=thr, gain_center=0.5 * (v_lo + v_hi))
