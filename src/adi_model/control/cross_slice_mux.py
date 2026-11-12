# =============================================================================
# 文件名     : cross_slice_mux.py
# 模块名     : adi_model.control.cross_slice_mux
# 版本       : v1.0
# 功能       : 按 US10511316B2 FIG.20 的**逐单元 2:1 MUX**结构实现跨片分配判据。
#
# 设计说明   :
#   * 依据是 [11] 的 FIG.20（Sheet 18 of 29，本轮在 2560×3300 **原生图**上逐元件核对；
#     上一轮教训：缩略图会串页，必须先按 "FIG. 20" 标题定位到 PDF 第 20 页再切图）：
#       353 SHUFFLER CONTROLLER 产出 `Shuffler Pointer` 与 `mux_sel`；
#       352 shuffler 输入 `15x2C` 并带两个标 `0` 的方框，输出经**虚线**到 MX 的上输入；
#       另一路以**实线**到 MX 的下输入；
#       MX1 / MX2 / MX3 / MX4 = **2:1 MUX**，选择端由 `mux_sel` 驱动，
#       每个 MX 的输出送到 DS1 的**一个** 2C 单元；
#       DS1 内部：`2C 2C … 2C` + 虚线框内 `C C` + 桥接 `C`。
#   * 由此得到的**关键语义**（这是本模块存在的理由）：
#       跨片分配**不是"整片交换"**，而是**逐主单元**用 2:1 MUX 选来源。
#       这决定了：同一片的 15 个 2C 单元可以分别来自不同路径，
#       因此"片"不再是分配的最小粒度，**单元**才是。
#   * 本模块只做**判据与账目**，不改主链：原件没有给 `mux_sel` 的具体序列。
# -----------------------------------------------------------------------------
# 变更记录
# -----------------------------------------------------------------------------
# 2026-11-13 初始版本：从 FIG.20 原生图提取"逐单元 2:1 MUX"语义与四条判据。
# =============================================================================

"""FIG.20 的跨片分配（逐单元 2:1 MUX）判据。

单位：计数与布尔；不引入电压或电容。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from adi_model.simulation.config import ConfigError


# =============================================================================
# 1. 原件计数常量（来自 FIG.20 图面）
# =============================================================================
#: 图中标出的 MUX 个数（MX1..MX4），每个对应 DS1 的一个 2C 单元。
FIGURE_MUX_COUNT = 4
#: 每个 MX 是 2:1（两路输入：352 那路为虚线、另一路为实线）。
MUX_INPUTS = 2
#: 352 的输入仍是 `15x2C`（与 FIG.19 一致）。
SHUFFLER_352_INPUT_2C_UNITS = 15


# =============================================================================
# 2. 逐单元 MUX 的选择模型
# =============================================================================
@dataclass(frozen=True)
class MuxSelection:
    """一次转换里逐单元的来源选择。

    Attributes:
        source: (n_units,) 整数数组，0 = 上输入（352 那路），1 = 下输入（实线那路）。
        n_units: 单元数。
    """

    source: np.ndarray
    n_units: int

    @property
    def count_upper(self) -> int:
        """选到上输入（352 路径）的单元数。"""
        return int(np.count_nonzero(self.source == 0))

    @property
    def count_lower(self) -> int:
        """选到下输入（实线路径）的单元数。"""
        return int(np.count_nonzero(self.source == 1))


def mux_selection(source) -> MuxSelection:
    """把逐单元选择码包装成可核对的记录。

    Args:
        source: 一维整数序列，取值只能是 0（上）或 1（下）。

    Returns:
        MuxSelection。

    Raises:
        ConfigError: 非一维、含 0/1 以外的值、或为空。

    Side effects: 无；拷贝并冻结。
    """
    values = np.asarray(source)
    if values.ndim != 1 or values.size == 0:
        raise ConfigError("mux selection must be a nonempty one-dimensional sequence")
    if np.any((values != 0) & (values != 1)):
        raise ConfigError("each 2:1 MUX selects exactly one of two inputs (0 or 1)")
    frozen = values.astype(np.int64).copy()
    frozen.setflags(write=False)
    return MuxSelection(frozen, int(values.size))


# =============================================================================
# 3. 四条判据
# =============================================================================
def per_unit_not_per_slice(*, per_unit_source: np.ndarray, slice_size: int) -> bool:
    """判据 ①：分配粒度是**单元**而不是**片**。

    FIG.20 里每个 MX 只接到 DS1 的一个 2C 单元，因此同片的各单元可以有不同来源。
    若某个"跨片分配"实现要求同片内所有单元来源相同，它就退化成了整片交换，
    与图面不符。

    Args:
        per_unit_source: (S, slice_size) 二维选择码，S 为片数。
        slice_size: 每片参与分配的主单元数。

    Returns:
        True 表示至少有一片的单元来源**不完全相同**（即确实是逐单元分配）。

    Raises:
        ConfigError: 形状不符或取值非法。

    Side effects: 无。

    注意：返回 False 并不必然是"实现错误"——某一笔转换里可能恰好全都选同一路。
    这条判据的用途是**在给定数据上检验"是否可能逐单元不同"**。
    """
    values = np.asarray(per_unit_source)
    if values.ndim != 2 or values.shape[1] != int(slice_size) or values.shape[0] < 1:
        raise ConfigError("per_unit_source must be (slices, slice_size)")
    if np.any((values != 0) & (values != 1)):
        raise ConfigError("selection codes must be 0 or 1")
    return bool(np.any(np.ptp(values.astype(np.int64), axis=1) > 0))


def mux_selects_from_two_inputs(*, source) -> bool:
    """判据 ②：每个 MX 只从**两路**里选一个（不是三路或多路）。

    Args:
        source: 选择码序列。

    Returns:
        True（只要构造成功就必然成立；此函数用于让调用方显式表达该约束）。

    Raises:
        ConfigError: 取值越界（见 `mux_selection`）。

    Side effects: 无。
    """
    mux_selection(source)
    return MUX_INPUTS == 2


def selection_is_complete(*, selection: MuxSelection, expected_units: int) -> bool:
    """判据 ③：选择的单元数必须等于该片参与分配的主单元数。

    FIG.20 里 DS1 有多少个 2C 单元，就要有多少个 MX（或等价的 MUX 资源）。

    Args:
        selection: 逐单元选择记录。
        expected_units: 期望的单元数（一般 = 15，即主段 15 个 2C）。

    Returns:
        True 表示数量匹配。

    Raises:
        ConfigError: expected_units 非正整数。

    Side effects: 无。
    """
    if isinstance(expected_units, bool) or not isinstance(expected_units, int | np.integer):
        raise ConfigError("expected_units must be an integer")
    if expected_units < 1:
        raise ConfigError("expected_units must be positive")
    return selection.n_units == int(expected_units)


def shuffle_pointer_advances(*, pointers) -> bool:
    """判据 ④：`Shuffler Pointer` 每笔前进（否则轮换名存实亡）。

    FIG.20 左侧 353 SHUFFLER CONTROLLER 产出 `Shuffler Pointer`，
    它的用途就是把每一次转换的起点挪一格，从而打散固定图案。

    Args:
        pointers: 连续若干笔的指针值（非负整数）。

    Returns:
        True 表示序列严格单调递增（允许跨周期回绕时由调用方分段传入）。

    Raises:
        ConfigError: 为空或含负值/非整数。

    Side effects: 无。
    """
    values = np.asarray(pointers)
    if values.ndim != 1 or values.size < 2:
        raise ConfigError("need at least two pointer values")
    if np.any(values < 0) or np.any(values != np.round(values)):
        raise ConfigError("pointers must be nonnegative integers")
    return bool(np.all(np.diff(values.astype(np.int64)) > 0))


def figure_gate_count() -> dict:
    """返回图面上的分配资源计数，供文档与测试引用。

    Returns:
        dict：MUX 个数、每 MUX 输入数、352 输入总量、以及"主段是否逐单元可选"。
    """
    return {
        "mux_count_in_figure": FIGURE_MUX_COUNT,
        "inputs_per_mux": MUX_INPUTS,
        "shuffler_352_input_c": SHUFFLER_352_INPUT_2C_UNITS * 2,
        # 图上只画了 4 个 MX 并加省略号 ⇒ 结构是"每单元一个"，4 只是画出来的样例
        "per_unit_mux": True,
    }
