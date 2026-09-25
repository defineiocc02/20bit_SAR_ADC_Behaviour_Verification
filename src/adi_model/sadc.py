"""sadc.py -- 第一级粗量化器 SADC（小、快的独立量化器）。

============================================================================
物理模型与文献出处
============================================================================
论文 [00]："The quantizer is fully separated from the residue generating
DAC (RDAC)"；"the quantizers are built out of one slice DAC. The
matched-quantizer sDAC with the RDAC allows >11b matching"（PPT p.9 写
">12b AC matching" —— 取更严的 12b 作门限，见 config.validate）。

架构读数见 ADR 0003：历史 7b 配置只作候选/回归对照；9b 决策与 dither
范围是独立概念。`Config.paper_literal()` 保留 512 个实际决策区间，不能把
dither 码宽加到决策信息位数上。未披露的电容数量和后端参数仍为假设。

* flash 式行为模型：阈值数组 + searchsorted 向量化转换，无逐位 SAR 时序。
* **SADC 的误差不会直接叠加到输出**（[09]1.3 的机制）：粗码误差把残差
  推出名义 bin，只要 vra 仍在 ADC2 窗口内（= (ADC2 余量)/G = 4.6875 mV
  ≈ 0.1·Δ1），输出完全不受影响 —— 定量验收见 experiments.stage19 验收②。
* 量化器自身的采样 kT/C 噪声与建立误差**不在本模块**：
  噪声在 sampler.py（x_S 通路），建立在 pipeline.SlicePool.acquire_quantizer
  （独立量化器 sDAC，cfg.sadc_cap_ratio × 活跃电容）。

单位契约：thresholds / 输入 x / 输出均为 [V] 与无量纲码（int64）。

参数来源分级：
    b1                           [假设-架构读数]（PPT 未给位数；由两条披露
                                 联立取值，见 docs/adr/0003-stage-1-resolution.md）
    stage1_reading               [披露]（论文原句，见 provenance.PARAM_GRADES）
    sadc_offset                  [假设]（默认 0；stage19 验收②扫描其窗口）
    sadc_rdac_gain_mismatch=1e-4 [假设]（对应 13.3b matching，满足 >12b）

契约与不变量：
    * 阈值数组每颗虚拟芯片生成一次（sadc_mismatch_enable 时由 rng 播种
      cfg.seed+977），之后只读 —— 三条底线之"物理失配固定"的组成部分。
    * 显式 thresholds 是名义输入；所有拓扑统一施加失调、增益和阈值失配。
      Split 的增益中心取 DAC 名义端点中点。runner 不得再次施加非理想性。
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
        thresholds: (n_code+1,) 严格单调的实际阈值 [V]，构造后只读。
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
            (len(x),) int64 码，范围 [0, n_code-1]，越界钳位到端点码仓。
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
    """构造第一级量化器的**唯一入口**。

    pipeline 与 sim_split 必须都调用本函数。历史教训（外部审计 B16）：
    两条主循环各自推导阈值步长，b1=6 时 ``n_sub*step0`` 与 ``cfg.delta1``
    数值巧合相等因而长期掩盖了分歧；一旦按论文把第一级读到 9b，
    两者相差 8 倍，退化等价测试立刻失败。

    口径：
        * 提供 ``dac``（分段拓扑）时，阈值对齐 **DAC 自己的名义栅格**：
          ``thr[c] = v_lo + c * (units_per_d1 * step0)``。这样
          ``k_eq = coarse * units_per_d1`` 恰好落在阈值上，残差被限制在
          ``[0, units_per_d1*step0)`` 之内（无噪声/失配/饱和时）。
        * 不提供 ``dac``（等权拓扑）时，阈值就是理想栅格
          ``-v_fs + c * cfg.delta1``。

    Args:
        cfg: 全局配置。
        dac: 可选的分段 DAC 实例。

    Returns:
        配置好名义与实际阈值的 :class:`SADC`，全部非理想性已施加一次。
    """
    if dac is None:
        return SADC(cfg)
    v_lo, v_hi = dac._nominal_endpoints()
    levels = int(dac.levels)
    step0 = (v_hi - v_lo) / (levels - 1.0)
    unit = units_per_first_stage_step(cfg, dac)
    thr = v_lo + np.arange(2**cfg.b1 + 1) * (unit * step0)
    return SADC(cfg, thresholds=thr, gain_center=0.5 * (v_lo + v_hi))
