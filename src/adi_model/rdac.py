"""rdac.py -- 名义 DAC 与真实 DAC 的计算（unary 拓扑；分段拓扑见 dac_arch.py）。

============================================================================
物理模型与文献出处
============================================================================
对同一组指令分别计算两个值——**两套求值的分离是本平台的方法论底线**：

    vD0    = DAC_nominal (b)       数字可见，用于重构（数字侧唯一可见的口径）
    vDtrue = DAC_physical(b, chip) 物理真值，只用于模拟电路与事后评分，
                                   **不得进入任何数字算法**（digital_core 边界）

传输函数（差分 CDAC，等权单位）：

    vD0(k)    = -v_fs + (k - k0) * step                      (名义)
    vDtrue(k) =  v_fs * ( 2 * sum_{sel} C_true / sum_{act} C_true - 1 )

分母里 sum_{act} C_true 的偏差是**整片总电容偏差**，表现为满幅增益误差：
DEM 只能打散码相关的部分，消不掉这一项，必须靠增益校准
（v3 电荷一致模型的分水岭结论，工作记忆"关键结论"节）。

单位契约：k / k0 [单位当量]；step / vD0 / vDtrue [V]；C_true [F]。

参数来源分级：
    step = rdac_step = 2·v_fs/512     [披露-推断]（20.5 pF / 512 单位，PPT p.23）
    k0（dither 量程偏置）             [假设]（n_units_headroom 默认 0）

契约与不变量：
    * LUT 由 chip.C_true 一次构建；C_true 每颗芯片只生成一次（三条底线①），
      LUT 构建后**只读**——任何"边仿真边改电容"都是破坏物理固定的 bug。
    * evaluate_nominal 不得依赖 chip / DEM 状态（名义守恒，digital_core
      verify_nominal_conservation 的验收对象是同构口径）。
    * dac_error **只许**用于事后评分与报告，出现在任何算法路径里即为越界。

适用域：unary 等权阵列（sim.py 主循环口径）。分段主/子 + 桥接拓扑的
两套求值在 dac_arch.SplitDAC（sim_split / pipeline 用），两者共用本文件
"名义/物理分离"的方法论，但物理方程不同，勿混用接口。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .chip import Chip
from .config import Config
from .mapper import N_DEM_STATES, SwitchCommand, unit_rank_arrays


@dataclass
class DACLut:
    """前缀和查表：DEM 状态 × bank × 单位序号 -> 实际电容。

    Attributes:
        cum:   (n_banks, N_DEM_STATES, n_units+1) 累积和 [F]；
               cum[b, s, m] = 按 DEM 状态 s 排列的前 m 个单位电容之和。
        total: (n_banks,) 该组 slice 的实际总电容 [F]（与 DEM 状态无关，
               因为置换不改变集合——这是"等权置换守恒"的结构性保证）。
        k0:    dither 量程偏置 [单位当量]。
    """

    cum: np.ndarray  # (n_banks, N_DEM_STATES, n_units + 1)
    total: np.ndarray  # (n_banks,)  该组 slice 的实际总电容
    k0: int

    def selected_cap(self, bank: np.ndarray, sid: np.ndarray, k: np.ndarray) -> np.ndarray:
        """选中 k 个单位的实际电容。**k 可以是实数**——线性插值。

        实数 k 的物理依据（专利 [10] Fig.19 / [09] Fig.12）：
        各 slice 可以接收**不同的整数码**，合并后得到小数权重。例如 7 个 slice 中
        2 个处于状态 2、5 个处于状态 3，合并权重 = (2·2+5·3)/7 = 2+5/7。
        这不是时间平均，而是**这一次转换里**多个 slice 的电荷共同形成的小数权重。
        所以小数 k 由"部分 slice 取 m、其余取 m+1"实现，其物理误差也随之改变 ——
        这正是 dither 与 bridging 共享同一物理机制的地方。

        Args:
            bank: (N,) int64 bank 编号。
            sid: (N,) DEM 状态。
            k: (N,) 单位当量（可为实数，按 slice 混合实现小数权重）。
        Returns:
            (N,) 选中电容 [F]。k 超界钳位到 [0, n_units-ε]（端点保护）。
        Side effects: 无（LUT 只读）。
        """
        k = np.asarray(k, dtype=float)
        kc = np.clip(k, 0, self.cum.shape[-1] - 1.001)
        i0 = np.floor(kc).astype(np.int64)
        frac = kc - i0
        c0 = self.cum[bank, sid, i0]
        c1 = self.cum[bank, sid, i0 + 1]
        return c0 + frac * (c1 - c0)


class RDAC:
    """unary 差分 CDAC 的两套求值器（名义 / 物理）。

    Attributes:
        lut:   DACLut（构建后只读）。
        step:  名义单位步长 [V]。
        k0:    零码偏置 [单位当量]。
    """

    def __init__(self, cfg: Config, chip: Chip, bank_slices: np.ndarray):
        """绑定配置与芯片，按各 bank 的 slice 构成构建 LUT。

        Args:
            cfg: 模型配置。
            chip: 物理芯片；LUT 构建时读一次 C_true，此后失配固定。
            bank_slices: (n_banks, n_active) 各 bank 参与的物理 slice 编号。
        Side effects: 构建 LUT（读 chip.C_true 一次；此后 chip 失配固定）。
        """
        self.cfg = cfg
        self.chip = chip
        self.bank_slices = np.asarray(bank_slices)  # (n_banks, n_active)
        self.step = cfg.rdac_step
        self.k0 = cfg.n_units_headroom // 2
        self.lut = self._build_lut()

    def _build_lut(self) -> DACLut:
        """按 DEM 状态的置换顺序构建电容前缀和（只应在 __init__ 调用一次）。

        Returns:
            DACLut：cum 为 (n_banks, N_DEM_STATES, n_u+1) 的电容前缀和（F），
            total 为 (n_banks,) 的该 bank 选中电容总量（F，与 DEM 状态无关），
            k0 为码字零点偏移（无量纲）。只在 __init__ 调用一次。
        """
        cfg = self.cfg
        slice_rank, unit_idx = unit_rank_arrays(cfg)  # (S, n_u)
        n_b, n_u = self.bank_slices.shape[0], slice_rank.shape[1]

        cum = np.zeros((n_b, N_DEM_STATES, n_u + 1))
        total = np.zeros(n_b)
        for b in range(n_b):
            sl = self.bank_slices[b][slice_rank]  # (S, n_u) 物理 slice 编号
            C = self.chip.C_true[sl, unit_idx]  # (S, n_u)
            cum[b] = np.concatenate([np.zeros((N_DEM_STATES, 1)), np.cumsum(C, axis=1)], axis=1)
            total[b] = C[0].sum()  # 与状态无关（同一组 slice）
        return DACLut(cum=cum, total=total, k0=self.k0)

    # ---------------- 两个求值 ----------------
    def evaluate_nominal(self, cmd: SwitchCommand) -> np.ndarray:
        """名义求值 vD0(k+d) [V]。只依赖数字可见栅格——数字算法的合法输入。

        Args:
            cmd: 开关命令（SwitchCommand），含 k（主码，无量纲）、dither_code
            （dither 码，无量纲）、bank（bank 索引）、sid（DEM 状态编号）。

        Returns:
            名义 DAC 输出 vD0(k+d)（V，可为数组）。[派生]
            只依赖数字可见栅格，是数字算法的合法输入。
        """
        k = cmd.k + cmd.dither_code
        return -self.cfg.v_fs + (k - self.k0) * self.step

    def evaluate_physical(
        self, cmd: SwitchCommand, slice_ids: np.ndarray | None = None
    ) -> np.ndarray:
        """物理求值 vDtrue(k+d) [V]。读 chip 真值 + DEM 置换 LUT——**只许**进模拟通路（residue）与事后评分，禁入数字算法。

        Args:
            cmd: 开关命令（SwitchCommand），含 k、dither_code、bank、sid。
            slice_ids: 本次采样实际持有的 slice 集合；None 仅用于固定 bank 对照。

        Returns:
            物理 DAC 输出 vDtrue(k+d)（V，可为数组），
            = V_FS·(2·C_sel/C_total − 1)。含电容失配与 DEM 置换。[派生]
            边界约束：只许进模拟通路（残差）与事后评分，禁入数字算法。
        """
        k = cmd.k + cmd.dither_code
        if slice_ids is not None:
            ids = np.asarray(slice_ids, dtype=np.int64)
            ranks, columns = unit_rank_arrays(self.cfg)
            output = np.empty(len(k))
            for start in range(0, len(k), 512):
                sl = slice(start, start + 512)
                states = np.asarray(cmd.sid[sl], dtype=np.int64)
                physical = np.take_along_axis(ids[sl], ranks[states], axis=1)
                caps = self.chip.C_true[physical, columns[states]]
                fraction = np.clip(k[sl, None] - np.arange(caps.shape[1]), 0, 1)
                output[sl] = self.cfg.v_fs * (
                    2 * (caps * fraction).sum(axis=1) / caps.sum(axis=1) - 1
                )
            return output
        sel = self.lut.selected_cap(cmd.bank, cmd.sid, k)
        tot = self.lut.total[cmd.bank]
        return self.cfg.v_fs * (2.0 * sel / tot - 1.0)

    # ---------------- 事后评分用（不得进入算法） ----------------
    def dac_error(self, cmd: SwitchCommand) -> np.ndarray:
        """DAC 误差 e_dac = vDtrue - vD0 [V]。评分/归因专用，同 evaluate_physical 的边界约束：出现在校准/重构路径里即为数字侧越界读真值。

        Args:
            cmd: 开关命令（SwitchCommand），含 k、dither_code、bank、sid。

        Returns:
            DAC 误差 e_dac = vDtrue − vD0（V）。[派生]
            评分/归因专用；出现在校准或重构路径即为数字侧越界读真值。
        """
        return self.evaluate_physical(cmd) - self.evaluate_nominal(cmd)
