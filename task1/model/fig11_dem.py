"""生成 Fig.11 分段电容网络的开关掩码。

每个主阵列含十五个 2C 和两个可独立控制的 1C。子阵列为
8C、4C、2C、1C、1C，末端 1C 固定到低参考。物理参考以 C
为桥接电容、零寄生以及子阵列不采 VIN 为工程假设。映射保留
B5～B8 在子节点，使主、子节点各自的名义选通电容与 DEM 关闭时相同。
"""

from dataclasses import dataclass
from operator import index

MAIN_CAP_UNITS = (2,) * 15 + (1, 1)
SUB_CAP_UNITS = (8, 4, 2, 1, 1)
BRIDGE_CAP_UNITS = 1
SLOT_COUNT = 8
_EMPTY_SLOTS = (0,) * SLOT_COUNT


@dataclass(frozen=True)
class Fig11Masks:
    """每个活动 slot 的主、子掩码，置位选择高参考。"""

    main: tuple[int, ...]
    sub: tuple[int, ...]
    valid: bool

    @property
    def main_flat(self) -> int:
        """与 RTL 相同的 136 位打包，slot 0 在低位。"""
        return sum(mask << (len(MAIN_CAP_UNITS) * slot) for slot, mask in enumerate(self.main))

    @property
    def sub_flat(self) -> int:
        """与 RTL 相同的 40 位打包，slot 0 在低位。"""
        return sum(mask << (len(SUB_CAP_UNITS) * slot) for slot, mask in enumerate(self.sub))


def _unsigned_field(value: int, bits: int, name: str) -> int:
    """验证软件输入可无损表示为指定的 RTL 无符号字段。"""
    integer = index(value)
    if not 0 <= integer < (1 << bits):
        raise ValueError(f"{name} exceeds its {bits}-bit unsigned field")
    return integer


def map_fig11(
    code: int,
    dem_enable: bool,
    horizontal_phase: int,
    vertical_phase: int,
) -> Fig11Masks:
    """映射同一活动组的已决九位码，B0 为最高位。

    DEM 打开时，slot s 的 2C 起点为 (horizontal_phase + s) % 15。
    B4 为 1 时从 vertical_phase 起循环选八个 1C。位置 2*s 与
    2*s+1 分别对应主阵列的 unit 15 与 unit 16。横相位 15
    在 DEM 打开时无效，返回全零掩码。DEM 关闭时忽略两个相位。
    """
    code = _unsigned_field(code, 9, "code")
    enabled = _unsigned_field(dem_enable, 1, "dem_enable")
    horizontal_phase = _unsigned_field(horizontal_phase, 4, "horizontal_phase")
    vertical_phase = _unsigned_field(vertical_phase, 4, "vertical_phase")
    if enabled and horizontal_phase == 15:
        return Fig11Masks(_EMPTY_SLOTS, _EMPTY_SLOTS, False)

    therm_count = code >> 5
    b4 = (code >> 4) & 1
    sub_mask = sum(((code >> (3 - unit)) & 1) << unit for unit in range(4))
    if not enabled:
        main_mask = ((1 << therm_count) - 1) | (b4 << 15)
        return Fig11Masks((main_mask,) * SLOT_COUNT, (sub_mask,) * SLOT_COUNT, True)

    main = []
    for slot in range(SLOT_COUNT):
        start = (horizontal_phase + slot) % 15
        therm_mask = sum(1 << ((start + offset) % 15) for offset in range(therm_count))
        ones_mask = sum(
            1 << (15 + unit)
            for unit in range(2)
            if b4 and (2 * slot + unit - vertical_phase) % 16 < 8
        )
        main.append(therm_mask | ones_mask)
    return Fig11Masks(tuple(main), (sub_mask,) * SLOT_COUNT, True)
