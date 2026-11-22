# =============================================================================
# 文件名     : digital_conversion.py
# 模块名     : 生产数字 IP 的逐笔算术
# 功能       : 从已决粗码生成写命令，用实际 pre/post 和加载的 Q30 权重重构。
# 设计说明   : SADC 和 ADC2 的电压量化是外部理想宏的输入模型。协议错误、
#              ADC2 越窗和最终出码饱和有各自的含义，调用者分别记录。
# =============================================================================
"""单位为差分伏特、粗码步及 Q30 权重字。物理位号按生产 RTL 打包。"""

from __future__ import annotations

from math import floor, isfinite
from operator import index

import numpy as np

from adi_model.dem_dither import SUB, bridge_swap, post_mask
from adi_model.recip_q60 import recip_exact_round

Q30 = 1 << 30
VFS = 3.0
COARSE_STEP = 6.0 / 512
LSB20 = 6.0 / (1 << 20)
ADC2_MIN = -0.1875
ADC2_SPAN = 0.75


# 1. SADC 给出独立判决，只有已决内容才进入 RDAC。
def sar_conversion(voltage: float, lower: float, upper: int) -> tuple[int, tuple[int, ...]]:
    """用当前试探门限与 SADC 输入比较，返回最终 c 与各次实际写命令。

    lower 的单位是粗码步。Flash 在普通 64 码区间选前缀。最高区先试 512；
    拒绝时保留 448，返回 bit5，本比较以 −1 表示写门关闭。
    SADC 前缀最多为 519。加上 U 后的 RDAC 命令恰为 520 时仍是有效保护码，
    更大的命令由事务层按拒绝记录，不能改写成一个合法的写门事件。
    """
    # 外部量化器只看到 Vin 和低段。每一次 RDAC 提交只含当时已决的位。
    # 把极端越量程输入约束到可比较的物理门限，不把 519 截成九位。
    if not isfinite(voltage) or not isfinite(lower) or upper not in (0, 1, 4, 5):
        raise ValueError("invalid SADC voltage or split-dither input")
    level = min(519.0, max(0.0, (voltage + VFS) / COARSE_STEP + lower))
    flash = min(floor(level / 64), 7)
    prefix = flash << 6
    commands = [prefix + upper]
    bit = 5
    if flash == 7:
        if level >= 512:
            prefix = 512
            bit = 2
            commands.append(prefix + upper)
        else:
            commands.append(-1)
    for j in range(bit, -1, -1):
        trial = prefix | (1 << j)
        if level >= trial:
            prefix = trial
        commands.append(prefix + upper)
    return prefix, tuple(commands)


# 2. 数字装载表与物理器件分开。八槽按冻结片号寻址，每槽十六主位和四个子位。
def nominal_weights_q30() -> np.ndarray:
    """返回独立的 18×20 名义配置，单字单位 Ceff/Cf × 2^30。"""
    row = [Q30 // 4] * 16 + [value * (Q30 // 128) for value in SUB]
    return np.tile(np.array(row, dtype=np.int64), (18, 1))


def conversion_mask(k: int, h: int, v: int, half: int, coin: int) -> np.ndarray:
    """DEM 与桥交换复用同一开关参考；列 j 对应物理开关位 j。"""
    return np.asarray(bridge_swap(post_mask(k, h, v), h, v, half << 3, coin), dtype=np.uint8)


def weight_sums(rows: np.ndarray, post: np.ndarray, pre: np.ndarray):
    """rows 已按本笔八个物理片号取出。整数表求数字权，浮点表求物理权。"""
    # pre 的四个元素按 b2、b1、b0、bal 排列，依次选中物理子单元 16…19。
    return rows[:, :16].sum(), (rows * post).sum(), (rows[:, 16:] * pre).sum()


def reconstruct(
    raw: int, gain: int, wm: int, wp: int, b_q24: int = 0
) -> tuple[int, bool, bool, bool]:
    """返回 code、低端限幅、高端限幅、out_err。生产顶层固定 alpha=1、同笔 epoch。"""
    # 配置装载的单字合法，仍可能组成一个不合规的总增益。RTL 在该 tag 上
    # 退休一个显式错误，不能改用名义增益或把这笔偷偷丢掉。
    raw, gain, wm, wp, b_q24 = map(index, (raw, gain, wm, wp, b_q24))
    if (
        not 0 <= raw < 8192
        or not -(1 << 26) <= b_q24 < 1 << 26
        or any(not 0 <= w < 1 << 64 for w in (gain, wm, wp))
    ):
        raise ValueError("input cannot be represented on the production reconstruction pins")
    if not 16 * Q30 <= gain <= 64 * Q30 or wp > 128 * Q30 or wm > gain + 128 * Q30:
        return 0, False, False, True
    z = -3145728 + 1536 * raw + 768 - b_q24
    n = 64 * z + 6 * wm - 3 * gain - 6 * wp
    # recip_rnd_v21 在两次 Newton 后校正为精确最近商；数值参考直接求该商。
    # 有符号算术右移取 floor，最后除 96 在半点离零，与 Python round 的规则不同。
    x = (n * recip_exact_round(gain)) >> 36
    if not -(1 << 31) <= x < 1 << 31:
        return 0, False, False, True
    magnitude = (abs(x) + 48) // 96
    code = (1 << 19) + (-magnitude if x < 0 else magnitude)
    return min((1 << 20) - 1, max(0, code)), code < 0, code >= 1 << 20, False
