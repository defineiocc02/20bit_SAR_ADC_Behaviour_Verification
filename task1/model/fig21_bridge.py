"""Fig.21 跨 B4～B6 候选映射及已知的名义补偿。

使用 Fig.11 的十五个 2C 与两个可同控的 1C，组成十六个逻辑列。
所有主阵列采 VIN，子阵列采低参考。桥接 Cb=C 且零寄生时子
阵列有效比例为 1/17。B5/B6 搬到主节点会改变名义反馈电荷，
需在同笔数字重构中加回一次补偿。
"""

from dataclasses import dataclass

from task1.model.fig11_dem import SLOT_COUNT, Fig11Masks, _unsigned_field


@dataclass(frozen=True)
class Fig21BridgeResult:
    """物理掩码及相对 Fig.11 直接选位的正 Q32 补偿。"""

    masks: Fig11Masks
    correction_q32: int

    @property
    def main_flat(self) -> int:
        """与 RTL 相同的 136 位主掩码。"""
        return self.masks.main_flat

    @property
    def sub_flat(self) -> int:
        """与 RTL 相同的 40 位子掩码。"""
        return self.masks.sub_flat


def map_fig21_bridge(code: int, reserved_column: int, vertical_slot: int) -> Fig21BridgeResult:
    """映射同笔前级九位码并计算归一化输入补偿。

    各片从保留列的下一列起选 H 个主列。再从指定的纵向起点
    取 m 片接通保留列。主列 15 映射为同控的两个 1C。子
    阵列仅由 B7/B8 驱动，前两位及末端 dummy 固定为低参考。
    """
    code = _unsigned_field(code, 9, "code")
    reserved = _unsigned_field(reserved_column, 4, "reserved_column")
    first_slot = _unsigned_field(vertical_slot, 3, "vertical_slot")

    high_count = code >> 5
    vertical_count = (code >> 2) & 7
    regular = sum(1 << ((reserved + offset) % 16) for offset in range(1, high_count + 1))
    main = []
    for slot in range(SLOT_COUNT):
        columns = regular
        if (slot - first_slot) % SLOT_COUNT < vertical_count:
            columns |= 1 << reserved
        main.append(columns | ((columns >> 15) << 16))

    sub_mask = (((code >> 1) & 1) << 2) | ((code & 1) << 3)
    crossed_weight = (code >> 2) & 3
    correction_q32 = (crossed_weight * (1 << 26) + 8) // 17
    return Fig21BridgeResult(
        masks=Fig11Masks(tuple(main), (sub_mask,) * SLOT_COUNT, True),
        correction_q32=correction_q32,
    )
