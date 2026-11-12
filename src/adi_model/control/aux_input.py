# =============================================================================
# 文件名     : aux_input.py
# 模块名     : adi_model.control.aux_input
# 版本       : v1.0
# 功能       : 按 US10541702B1 FIG.1/FIG.2 的节点结构核算 AUX 输入的采样电荷。
#
# 设计说明   :
#   * 依据是 [13] FIG.1 与 FIG.2（Sheet 1 of 9，已入库核对）：
#       V_IN 经电阻 2 → 节点电容 4 → 自举开关 6 的输入侧（节点 16）
#       V_IN_AUX 经电阻 2 也接到**同一节点**，两者各自有一路开关 22
#       AUX 支路上另有一对交叉耦合的辅助电容 14 与开关 18/20（自举/预充）
#     即：AUX 不是"第二条独立采样通路"，而是**与主输入在采样节点并联的第二个源**。
#   * 因此核算要点是三条，缺一条就会把 AUX 当成主信号的一部分而算错：
#       ① 主输入与 AUX 各自贡献的电荷分别记账（source separation）；
#       ② 开关 22 的切换把 AUX 电荷带进 / 带出同一个采样节点（branch charge）；
#       ③ 复位（clock reset）阶段 AUX 侧也要放电，不能算成信号。
#   * 本模块只做**核算判据与记账式**，不改主链：原件没有给出本芯片是否装 AUX、
#     装几路、AUX 采什么信号，因此默认 `enabled=False`。
# -----------------------------------------------------------------------------
# 变更记录
# -----------------------------------------------------------------------------
# 2026-11-13 初始版本：从 FIG.1/FIG.2 提取三条核算不变式。
# =============================================================================

"""AUX 输入（第二采样源）的电荷核算与判据。

单位：电荷 C，电容 F，电压 V。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from adi_model.simulation.config import ConfigError


@dataclass(frozen=True)
class AuxChargeLedger:
    """一次采样内 AUX 支路与主输入的电荷分账，单位 C。

    Attributes:
        main_source_c: 主输入 V_IN 经电阻 2 注入采样节点的电荷。
        aux_source_c: AUX 源 V_IN_AUX 经同一路径注入的电荷。
        aux_branch_c: AUX 支路辅助电容（FIG.1 的 14）被接到采样节点时搬运的电荷。
        reset_c: 复位阶段从 AUX 侧放掉的电荷（与信号无关）。
        aux_switched: 本次采样中开关 22 是否闭合（FIG.2 的 AUX 采样态）。
    """

    main_source_c: float
    aux_source_c: float
    aux_branch_c: float
    reset_c: float
    aux_switched: bool

    @property
    def node_total_c(self) -> float:
        """采样节点上的净电荷：主 + AUX 源 + AUX 支路 − 复位。

        复位项取负号是因为它在复位相把电荷放回源侧，不留在节点上。
        """
        return self.main_source_c + self.aux_source_c + self.aux_branch_c - self.reset_c

    @property
    def signal_fraction(self) -> float:
        """AUX 分量占节点净电荷的比例，用于判断"AUX 是否真的参与信号"。

        分母为零时返回 0（没有电荷搬运，比例无定义）。
        """
        total = self.node_total_c
        if total == 0.0:
            return 0.0
        return abs(self.aux_source_c + self.aux_branch_c) / abs(total)


def aux_sample_charge(
    *,
    main_v: float,
    aux_v: float,
    c_sample_f: float,
    c_aux_branch_f: float,
    aux_switched: bool,
    reset_c: float = 0.0,
) -> AuxChargeLedger:
    """按 FIG.1/FIG.2 的两源并联结构核算一笔 AUX 采样。

    Args:
        main_v: 主输入电压 [V]。
        aux_v: AUX 输入电压 [V]。
        c_sample_f: 采样节点对地电容 [F]（FIG.1 的 4 及后端阵列，>0）。
        c_aux_branch_f: AUX 支路辅助电容 [F]（FIG.1 的 14，≥0）。
        aux_switched: 开关 22 是否闭合；关闭时 AUX 完全不贡献。
        reset_c: 复位阶段放掉的电荷 [C]（≥0）。

    Returns:
        AuxChargeLedger。

    Raises:
        ConfigError: 任一参数非有限或越界。

    Side effects: 无；纯代数，可复算。

    边界：`aux_switched=False` 时 `aux_source_c` 与 `aux_branch_c` 都必须为 0，
    这是"开关决定 AUX 是否接入"的直接后果，不是可选约定。
    """
    for name, value in (("main_v", main_v), ("aux_v", aux_v)):
        if not np.isfinite(value):
            raise ConfigError(f"{name} must be finite")
    if not np.isfinite(c_sample_f) or c_sample_f <= 0:
        raise ConfigError("c_sample_f must be finite and positive [F]")
    if not np.isfinite(c_aux_branch_f) or c_aux_branch_f < 0:
        raise ConfigError("c_aux_branch_f must be finite and nonnegative [F]")
    if not np.isfinite(reset_c) or reset_c < 0:
        raise ConfigError("reset_c must be finite and nonnegative [C]")
    if not isinstance(aux_switched, bool):
        raise ConfigError("aux_switched must be an explicit bool")

    main_source = c_sample_f * main_v
    if aux_switched:
        aux_source = c_sample_f * aux_v
        aux_branch = c_aux_branch_f * (aux_v - main_v)
    else:
        aux_source = 0.0
        aux_branch = 0.0
    return AuxChargeLedger(
        main_source_c=main_source,
        aux_source_c=aux_source,
        aux_branch_c=aux_branch,
        reset_c=float(reset_c),
        aux_switched=aux_switched,
    )
