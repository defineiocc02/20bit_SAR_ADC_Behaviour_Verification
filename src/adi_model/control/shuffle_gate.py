# =============================================================================
# 文件名     : shuffle_gate.py
# 模块名     : adi_model.control.shuffle_gate
# 版本       : v1.0
# 功能       : 按 US10511316B2 FIG.19 的 362 门与两级 shuffler 结构实现可核对判据。
#
# 设计说明   :
#   * 依据是 [11] 的 FIG.19（Sheet 17 of 29，本轮在 2560×3300 原生图上逐元件核对）：
#       306 UNARY ENCODER: 输入 8x / 4x / 2x（即 B5/B6/B7 的每片权重）
#       352 左 shuffler : 输入 "15x2C"（= B0-B3 段的 30C，定值）+ 一个标 `0` 的方框
#       350 右 shuffler : 输入 8x / 4x / 2x + 一个标 `0` 的方框
#       362 门          : 把 352/350 的分配结果**选通**到下方各片 DS1..DS8
#       360             : 指向 DS1 内部电容行的引线
#       305             : 包住整个 shuffler 域的虚线框
#     DS1 内部图面：`2C 2C … 2C` + **虚线框内的 `C C`** + 桥接 `C`。
#     虚线框内的两个 C 与 FIG.11 的 S4/子阵列占位对应 —— 两图互相印证。
#   * 由此得到的三条语义（不是猜的数值）：
#       ① **两级分配**：B0-B3 段（30C）走 352，B5-B7 段走 306→350；
#          两条路径**汇聚**在 362，即一组码的电容来自两个不同的 shuffler。
#       ② **362 是选通门**，不是加权：它把分配结果送到切片，本身不改变权重。
#          因此各切片拿到的权重和必须与码本律一致（可核对）。
#       ③ 两个 shuffler 各有一个 `0` 空位 ⇒ 与 `reserved_column` 的"16 选 15"同源。
#   * 本模块只做**判据**，不改主链：原件没有给 362 的具体开关表。
# -----------------------------------------------------------------------------
# 变更记录
# -----------------------------------------------------------------------------
# 2026-11-13 初始版本：从 FIG.19 原生图提取两级分配 + 选通门语义。
# =============================================================================

"""FIG.19 的 362 选通门与两级 shuffler 判据。

单位：权重为"每片 1x 单元"的倍数（无量纲）；不引入电压。
"""

from __future__ import annotations

import numpy as np

from adi_model.simulation.config import ConfigError


# =============================================================================
# 1. 原件计数常量（来自 FIG.19 图面）
# =============================================================================
#: 352 的输入是固定量的 B0-B3 段：15 个 2C = 30C，图上标 `15x2C`。
SHUFFLER_352_INPUT_2C_UNITS = 15
#: 306 → 350 的三路输入，对应 B5/B6/B7 的每片权重（图上标 8x/4x/2x）。
SHUFFLER_350_INPUTS = {"B5": 8, "B6": 4, "B7": 2}
#: 两个 shuffler 各有一个标 `0` 的空位（与 reserved_column 同源）。
RESERVED_SLOTS_PER_SHUFFLER = 1


# =============================================================================
# 2. 两级分配的总量核对
# =============================================================================
def verify_two_stage_allocation() -> dict:
    """核对两级 shuffler 的输入总量能否拼出主阵列的 30C + 子阵列段。

    FIG.19 把主段的 30C 交给 352（定值），把 B5/B6/B7 交给 306→350。
    两路在 362 汇聚 ⇒ 一次转换里被分配的单元总量必须等于
    "主段 30C + 子阵列可切换 15C" 的口径。

    Returns:
        dict：352 总量、350 总量、两者之和、以及与图面标注的对照。

    Side effects: 无；纯整数运算。
    """
    l352 = SHUFFLER_352_INPUT_2C_UNITS * 2  # 15 个 2C = 30C
    l350 = int(sum(SHUFFLER_350_INPUTS.values()))  # 8+4+2 = 14
    return {
        "shuffler_352_total_c": l352,
        "shuffler_350_total_c": l350,
        "combined_c": l352 + l350,
        # 主段 15×2C = 30C；子阵列可切换 8+4+2+1 = 15C（B8 不经过 350，见下）
        "main_segment_c": 30,
        "sub_switchable_c": 8 + 4 + 2 + 1,
        # 306 只喂 B5/B6/B7（8/4/2），B8（1C）不经过 unary encoder
        "unary_encoder_feed_c": l350,
    }


def unary_encoder_excludes_lsb() -> bool:
    """B8 是否**不经过** unary encoder（图面上 306 只有 8x/4x/2x 三路）。

    这条判据的用途：如果某实现把 B8 也做了 unary/DEM 旋转，就与 FIG.19 不符。
    B8 是最低位，逐片权重 1C，走不加扰的直通更合理，也与图面一致。

    Returns:
        True 表示 B8 不经 encoder（图面如此）。
    """
    return "B8" not in SHUFFLER_350_INPUTS


# =============================================================================
# 3. 362 是"选通"而不是"加权"
# =============================================================================
def gate_preserves_weight_sum(*, weights_before: np.ndarray, weights_after: np.ndarray) -> bool:
    """362 门只改**连接关系**，不改被分配的总权重。

    图面上 362 画成一组开关（对角交叉符号），输入来自 352/350、输出到各片 DS1..DS8。
    开关阵列的语义是"把同一批单元送到不同片"，因此：

        Σ(所有片的权重) 在门前后必须不变

    Args:
        weights_before: 门前的逐片权重（任意形状，按总和比较）。
        weights_after: 门后的逐片权重，元素总数必须与 before 相同。

    Returns:
        True 表示总和不变（即只做了置换/选通，没有增删权重）。

    Raises:
        ConfigError: 形状元素数不同，或含非有限值。

    Side effects: 无。
    """
    before = np.asarray(weights_before, dtype=float)
    after = np.asarray(weights_after, dtype=float)
    if before.size != after.size:
        raise ConfigError("gate cannot add or drop weight slots; sizes must match")
    if np.any(~np.isfinite(before)) or np.any(~np.isfinite(after)):
        raise ConfigError("weights must be finite")
    return bool(np.isclose(before.sum(), after.sum(), rtol=0, atol=1e-12))


def gate_is_permutation(*, mapping: np.ndarray, n_slots: int) -> bool:
    """核对一组 362 开关连接是否构成**置换**（同一切片不重复拿到同一单元）。

    Args:
        mapping: (S,) 整数数组，第 i 个输出槽来自哪个输入槽。
        n_slots: 输入槽数 == 输出槽数。

    Returns:
        True 表示 mapping 是 n_slots 个位置上的置换。

    Raises:
        ConfigError: n_slots 非正，或 mapping 形状/取值越界。

    Side effects: 无。
    """
    if isinstance(n_slots, bool) or not isinstance(n_slots, int | np.integer) or n_slots < 1:
        raise ConfigError("n_slots must be a positive integer")
    values = np.asarray(mapping)
    if values.ndim != 1 or values.size != int(n_slots):
        raise ConfigError("mapping must be a one-dimensional array of length n_slots")
    if np.any(values != np.round(values)) or np.any(values < 0) or np.any(values >= n_slots):
        raise ConfigError("mapping entries must be integers within [0, n_slots)")
    return bool(np.array_equal(np.sort(values.astype(np.int64)), np.arange(int(n_slots))))


def reserved_slots_per_cycle() -> int:
    """每个 shuffler 每周期留出的空位数（图面上的标 `0` 方框）。

    FIG.19 里 352 与 350 各有一个 `0` 方框 ⇒ 两条路径各自留 1 个空位。
    这与 `control/reserved_column.py` 的"16 选 15"是同一机制的两种画法。

    Returns:
        空位数（每条 shuffler 各 1）。
    """
    return RESERVED_SLOTS_PER_SHUFFLER
