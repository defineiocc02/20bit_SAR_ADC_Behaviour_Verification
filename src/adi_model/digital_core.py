"""digital_core.py -- 数字核：数字侧算法的唯一入口与数据流边界。

============================================================================
数据流边界（工业级约束，验收强制检查）
============================================================================

    ┌─────────────── 模拟域（物理真值，chip.*）───────────────┐
    │  18-slice 池 · SplitDAC · RA · KTC · ADC2 · 动态误差    │
    └──────────────┬──────────────────────┬──────────────────┘
                   │ SADC 粗码 c[n]       │ ADC2 细码 fine[n]
                   ▼                      ▼
    ┌──────────────────── 数字核 DigitalCore ─────────────────┐
    │ 1. switch_commands:                                     │
    │      (c, dither 码, DEM 状态) -> SwitchCommand(k, sid)  │
    │      不变量：DAC_nominal(M(c,state)) = V_target(c)       │
    │      （等权单位置换，结构性保证，verify_nominal）        │
    │ 2. reconstruct:                                         │
    │      x̂ = ( vD0 + fine/Ĝ − d_corr ) / α                  │
    │      Ĝ/κ/α 全部来自数字状态，禁止读 chip.C_true          │
    │ 3. calibrate（慢时标，温度量级）：                        │
    │      gain LMS / beta 回归 / 权重校正系数                 │
    │      输入只能是 (out, 已知校准输入, 数字码, dither 码)   │
    └─────────────────────────────────────────────────────────┘

设计规则（全部来自 v1-v5 审计教训，违反任何一条都在历史里翻过车）：
  * DEM 只做**等权单位位置置换** -> 名义值守恒是结构性质，不靠调参；
  * dither 的模拟注入 / 码修改 / 数字扣除三者**成对出现**（DitherState）；
  * 校准回归量必须含 dither 名义项（v3：漏 d_once -> G_hat 崩到 6.68）；
  * 增益校准禁止用 out 对 x 回归（vD0 是 x 的阶梯函数，斜率恒 ~1）；
  * 数字侧永不读物理真值（chip.C_true / e_dac），真值只做事后评分。
单位契约：k / dither_code [单位当量]；sid / bank [无量纲索引]；
estimated_gain / kappa / alpha [无量纲]。
契约与不变量（验收强制）：① 数字侧禁读 chip 真值（C_true/e_dac）；
② 名义守恒 DAC(M(c)) 与 DEM 状态无关；③ 校准回归量必须含 dither 名义项。

"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .config import Config
from .mapper import Mapper, SwitchCommand
from .reconstruction import DigitalState, reconstruct


@dataclass
class PipelineState:
    """跨样本持久的数字状态（对应芯片里的校准寄存器 + DEM 计数器）。"""

    digital: DigitalState
    dem_counter_a: int = 0  # bank A 的 DEM 状态推进计数（按 bank 内序号）
    dem_counter_b: int = 0
    gain_updates: int = 0  # 校准事件计数（可观测性/时序建模用）
    calib_history: list = field(default_factory=list)


class DigitalCore:
    """数字侧算法集合：粗细码合并、DEM、dither、校准。

    pipeline.py 的转换相按以下顺序调用（与论文 Fig.9.8.1 的数据流一致）：
        1. sadc.convert()            -> 粗码（量化器是独立 sDAC）
        2. switch_commands()         -> (k_eq, sid, dither 码)
        3. （物理 RDAC 置位、RA、ADC2 —— 模拟域）
        4. reconstruct(vd0, fine)    -> 输出
        5. （慢时标）calibrate_gain / calibrate_beta
    """

    def __init__(self, cfg: Config):
        """按配置装配数字核心：映射器、流水线状态与校准器。

        Args:
            cfg: 模型配置（Config）。g0 / kappa_eff / beta_eff /
                 rdac_step 用于初始化数字状态初值。
        Side effects: 构造 self.mapper 与 self.state（含 DigitalState 初值）。
        """
        self.cfg = cfg
        self.mapper = Mapper(cfg)
        self.state = PipelineState(
            digital=DigitalState(
                estimated_gain=cfg.g0,
                kappa=cfg.kappa_eff(),
                beta=cfg.beta_eff(),
                W_nominal=np.array([cfg.rdac_step]),
                history=[],
            )
        )

    # ------------------------------------------------------------------
    # 1) 粗码 + DEM + dither -> 开关指令
    # ------------------------------------------------------------------
    def switch_commands(
        self, coarse: np.ndarray, bank: np.ndarray, dither_code: np.ndarray | None = None
    ) -> SwitchCommand:
        """生成物理 RDAC 的开关指令。

        DEM 状态按 **bank 内序号**推进（全局序号会让每个 bank 只走一半
        状态空间 —— gcd(2a,512)=2 的历史坑）。dither 码单独存放，
        由 RDAC 在标称/物理两次求值里统一使用（不能在 encode 里预加）。

        Args:
            coarse:       粗码 c[n] [单位当量]（SADC 粗量化结果）。
            bank:         每样本所属 bank [无量纲索引]（0=A，1=B）。
            dither_code:  可选 dither 码 [单位当量]；None = 不加 dither。
        Returns:
            SwitchCommand，含 k（单位当量数组）、sid（DEM 状态号）、
            dither 码；不变量 DAC_nominal(M(c,state))=V_target(c) 成立。
        Side effects: 推进 state.dem_counter_a / dem_counter_b（按 bank）。
        """
        n = len(coarse)
        if self.cfg.dem_enable:
            st = self.state
            sid = np.empty(n, dtype=np.int64)
            for bv, cnt_name in ((0, "dem_counter_a"), (1, "dem_counter_b")):
                m = bank == bv
                cnt = getattr(st, cnt_name)
                idx = cnt + np.arange(int(m.sum()), dtype=np.int64)
                sid[m] = (idx * 2_654_435_761) % 512
                setattr(st, cnt_name, cnt + int(m.sum()))
        else:
            sid = np.zeros(n, dtype=np.int64)
        return self.mapper.encode(coarse, bank, sid, dither_code)

    # ------------------------------------------------------------------
    # 2) 粗细码合并
    # ------------------------------------------------------------------
    def reconstruct(
        self, vd0: np.ndarray, fine: np.ndarray, dither_correction: np.ndarray, alpha: float
    ) -> np.ndarray:
        """数字域重建 x̂ = (vd0 + fine/Ĝ − d_corr)/α。

        Ĝ/κ/α 全部来自数字状态（self.state.digital），禁止读 chip.C_true。

        Args:
            vd0:              粗码对应标称电压 vD0 [V]。
            fine:             ADC2 输出等效电压 v2 [V]（RA 输出口径）。
            dither_correction: dither 名义扣除项 d_corr [V]。
            alpha:            采样态 dither 恒定衰减 [无量纲]（注入模式 ≡1）。
        Returns:
            x̂ [V]，已折回输入口径（fine/Ĝ + vd0 − d_corr 后除以 α）。
        Side effects: 无（只读数字状态）。
        """
        return reconstruct(vd0, fine, self.state.digital.estimated_gain, dither_correction, alpha)

    # ------------------------------------------------------------------
    # 3) 慢时标校准（接口与 sim_split 的 Calibrator 一致，状态共享）
    # ------------------------------------------------------------------
    def calibrate_gain(
        self,
        x_known: np.ndarray,
        vd0: np.ndarray,
        fine: np.ndarray,
        d_known: np.ndarray | None = None,
        alpha: float | None = None,
    ) -> float:
        """G_hat 最小二乘更新。回归量 = α·x + d_nom − vD0（顺序与口径见 reconstruction.Calibrator.update_gain 的审计注释）。

        Args:
            x_known:   已知校准输入 x [V]（可直接获取，非待测信号）。
            vd0:       粗码标称电压 vD0 [V]。
            fine:      ADC2 输出 v2 [V]（RA 输出口径）。
            d_known:   可选 dither 名义项 d_nom [V]；缺失时退化为无 dither。
            alpha:     可选衰减 α [无量纲]；None = 1。
        Returns:
            G_hat [无量纲]，更新后的增益估计（写入 state.digital.estimated_gain）。
        Side effects: 写 state.digital.estimated_gain；state.gain_updates +1；
            向 state.calib_history 追加 ("gain", g)。
        """
        from .reconstruction import Calibrator

        g = Calibrator(self.cfg, self.state.digital).update_gain(x_known, vd0, fine, d_known, alpha)
        self.state.gain_updates += 1
        self.state.calib_history.append(("gain", g))
        return g

    # ------------------------------------------------------------------
    # 验收：名义守恒（DEM 任意状态下同一逻辑码选中同样多的单位）
    # ------------------------------------------------------------------
    def verify_nominal_conservation(self) -> dict:
        """校验名义通路上的电荷守恒，返回逐码残差。

        Returns:
            字典：
                "ok": 布尔，守恒且无重号/漏号（相邻码差恒等于 units_per_lsb1）。
                "k_by_code": 逐逻辑码 -> 名义单位数 k [单位当量]。
                "units_per_lsb1": 每 LSB1 单位数 [单位当量]。
        Side effects: 无（仅读 cfg 与 mapper，不修改状态）。
        """
        cfg = self.cfg
        coarse = np.arange(2**cfg.b1)
        banks = np.zeros_like(coarse)
        sids = np.arange(0, 512, max(512 // len(coarse), 1))[: len(coarse)]
        cmd = self.mapper.encode(coarse, banks, sids)
        k_by_code = {}
        ok = True
        for c in coarse:
            ks = cmd.k[coarse == c]
            ok &= ks.size == 1
            k_by_code[int(c)] = float(ks[0]) if ks.size == 1 else float("nan")
        # 相邻码差必须严格等于 units_per_lsb1（等权 + 无重号/漏号）
        kv = np.array([k_by_code[int(c)] for c in coarse])
        diffs = np.diff(kv)
        mono = bool(np.all(diffs == cfg.units_per_lsb1))
        return {
            "ok": bool(ok and mono),
            "k_by_code": k_by_code,
            "units_per_lsb1": cfg.units_per_lsb1,
        }
