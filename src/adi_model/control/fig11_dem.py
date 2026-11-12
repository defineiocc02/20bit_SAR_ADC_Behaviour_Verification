# =============================================================================
# 文件名     : fig11_dem.py
# 模块名     : fig11_dem
# 版本       : v1.0
# 功能       : 把 9 位 RDAC 码翻成 Fig.11 分段电容网络的开关掩码，
#              供 DEM 映射、RTL 逐沿对拍与独立电荷参考共用。
#
# 设计说明   :
#   * 这是 RDAC 侧的 binary -> unary 映射，不是第一级量化器的电容结构；
#     量化器电容阵列、3b flash + SAR、比较器噪声、kT/C、参考建立均不在本模型内。
#   * MAIN_CAP_UNITS / SUB_CAP_UNITS / Cbridge=C / 零寄生 / 子阵列不采 VIN /
#     末端 1C 固定低轨，都是工程假设；β=1/17 由这组假设推出，不是原件给出的数值。
#   * 输出掩码 slot 0 在最低位，置位表示对应底板选高参考；
#     main 136 位、sub 40 位，与 RTL 逐位对齐，位宽改动会同时破坏对拍。
#   * DEM 关闭时忽略两个相位；横相位 15 在 DEM 打开时无保留列可用，返回全零掩码。
# -----------------------------------------------------------------------------
# 变更记录
# -----------------------------------------------------------------------------
# 初始版本，暂无变更记录。
# [待核实] 两个 1C 单元是否都应参与 B4~B6 / dither，以及 sub 阵列 8/4/2/1/1
#          相对专利二进制子阵列的取舍，需与模拟组确认后再改常量。
# =============================================================================

"""生成 Fig.11 分段电容网络的开关掩码。

每个主阵列含十五个 2C 和两个可独立控制的 1C。子阵列为
8C、4C、2C、1C、1C，末端 1C 固定到低参考。物理参考以 C
为桥接电容、零寄生以及子阵列不采 VIN 为工程假设。映射保留
B5～B8 在子节点，使主、子节点各自的名义选通电容与 DEM 关闭时相同。
"""

from dataclasses import dataclass
from operator import index

from adi_model.capacitors import GROUP_SLICES, MAIN_WEIGHTS, SUB_WEIGHTS

# =========================================================================
# 1. 电容几何与常量
# =========================================================================
# -------------------------------------------------------------------------
# 以下三个常量原在此处各写一份字面量，与 adi_model.capacitors 的同名事实重复。
# 现在一律从这里向上游取，只保留 Fig.11 侧的命名（CAP_UNITS / SLOT_COUNT）
# 以免打断 fig21_bridge 等调用方；数值不再有第二个定义点。
# 桥接电容 C 也归上游的 BRIDGE_CAP_UNITS 管，本模块不再重复声明。
# 改动任何一个都必须同步重跑 262144 组 DEM 对拍。
# -------------------------------------------------------------------------
MAIN_CAP_UNITS = MAIN_WEIGHTS  # 15 个 2C + 2 个 1C，共 32C；两个 1C 假定可独立切换
SUB_CAP_UNITS = SUB_WEIGHTS  # 8C/4C/2C/1C/1C；末个 1C 恒接低轨（工程改动，待核实）
SLOT_COUNT = GROUP_SLICES  # 每笔采样的活动 slice 数，对应 18 片池中的 8 片
_EMPTY_SLOTS = (0,) * SLOT_COUNT  # 非法相位时返回的全零掩码，valid=False


# =========================================================================
# 2. 数据结构
# =========================================================================
# -------------------------------------------------------------------------
# frozen=True 是刻意的：掩码一旦生成就不允许就地改写，避免同笔样本的
# 采样快照与转换快照被后续 DEM 步骤意外串改。
# -------------------------------------------------------------------------
@dataclass(frozen=True)
class Fig11Masks:  # 每片一组 main/sub 掩码 + 本次映射是否有效
    """每个活动 slot 的主、子掩码，置位选择高参考。"""

    main: tuple[int, ...]  # 每片 17 位主阵列选通位图
    sub: tuple[int, ...]  # 每片 5 位子阵列选通位图，bit4 dummy 恒低
    valid: bool  # False 表示该次请求被拒绝，调用方不得使用掩码
    reserved_column_hit: bool = False  # 相位落在第 16 个"保留列"上（该列无开关位）

    @property
    def main_flat(self) -> int:
        """与 RTL 相同的 136 位打包，slot 0 在低位。"""
        return sum(mask << (len(MAIN_CAP_UNITS) * slot) for slot, mask in enumerate(self.main))

    @property
    def sub_flat(self) -> int:
        """与 RTL 相同的 40 位打包，slot 0 在低位。"""
        return sum(mask << (len(SUB_CAP_UNITS) * slot) for slot, mask in enumerate(self.sub))


# =========================================================================
# 3. 输入字段校验
# =========================================================================
# -------------------------------------------------------------------------
# 软件侧的 int 可以无限宽，RTL 字段有限；这里在映射入口就把越界挡住，
# 否则高位会被静默截断，逐沿对拍时表现为「偶发不一致」而很难定位。
# -------------------------------------------------------------------------
def _unsigned_field(value: int, bits: int, name: str) -> int:
    """验证软件输入可无损表示为指定的 RTL 无符号字段。"""
    integer = index(value)  # 用 operator.index 拒绝 float 等隐式数值
    if not 0 <= integer < (1 << bits):
        raise ValueError(f"{name} exceeds its {bits}-bit unsigned field")
    return integer


# =========================================================================
# 4. 码到掩码的映射
# =========================================================================
# -------------------------------------------------------------------------
# B0 为最高位。therm_count 把高 4 位翻成 15 个 2C 的温度计掩码，
# b4 控制两个 1C，低 4 位直接作子阵列二进制掩码；DEM 打开时按
# (horizontal_phase + slot) % 15 旋转起点，使八片各自选不同的物理列。
# -------------------------------------------------------------------------
def map_fig11(
    code: int,
    dem_enable: bool,
    horizontal_phase: int,
    vertical_phase: int,
) -> Fig11Masks:
    """映射同一活动组的已决九位码，B0 为最高位。

    DEM 打开时，物理列是 **16 位环**：0..14 是 15 个可切换的 2C，
    第 15 列是**保留列**（哑列，恒接低轨，没有开关位）。横向相位 0..15
    指向这个环；相位 15 落在保留列上，窗口跳过它，因此有源集合与相位 0 相同，
    只多一个 `reserved_column_hit` 标记。早期版本把相位 15 判为非法并返回全零掩码，
    是因为当时没有建模保留列——这与 `dem3d.fig11_masks`（接受任意相位）不一致。

    B4 为 1 时从 vertical_phase 起循环选八个 1C。位置 2*s 与
    2*s+1 分别对应主阵列的 unit 15 与 unit 16。DEM 关闭时忽略两个相位。

    **已知不一致（记录在案，不假装统一）**：
    * 本函数按 **slot** 旋转（`(phase + slot) % 15`），`dem3d.fig11_masks` 按**物理 ID**；
    * 本函数的纵向是 `(2*slot + unit - v) % 16`，`dem3d` 是 `(+v)`。
    两者只在 slot==物理 ID 且 v==0 时逐位相同；漂移由
    `tests/unit/test_fig11_vs_dem3d.py` 钉住。
    """
    code = _unsigned_field(code, 9, "code")  # 9 位无符号粗码，越界直接抛异常
    enabled = _unsigned_field(dem_enable, 1, "dem_enable")  # DEM 开关按 1 位字段规范化
    horizontal_phase = _unsigned_field(horizontal_phase, 4, "horizontal_phase")
    vertical_phase = _unsigned_field(vertical_phase, 4, "vertical_phase")
    reserved_hit = bool(enabled and horizontal_phase == 15)
    if reserved_hit:
        # 相位 15 命中保留列：窗口从哑列之后开始，等价于相位 0 的有源集合。
        horizontal_phase = 0

    therm_count = code >> 5  # 高 4 位 -> 15 个 2C 的温度计计数
    b4 = (code >> 4) & 1  # B4 决定两个 1C 单元的选通
    sub_mask = sum(
        ((code >> (3 - unit)) & 1) << unit for unit in range(4)
    )  # B5~B8 翻成 4 位子阵列掩码
    if not enabled:
        main_mask = ((1 << therm_count) - 1) | (b4 << 15)
        return Fig11Masks((main_mask,) * SLOT_COUNT, (sub_mask,) * SLOT_COUNT, True, False)

    main = []
    for slot in range(SLOT_COUNT):
        start = (horizontal_phase + slot) % 15  # 每片旋转 2C 起点，实现跨片 DEM
        therm_mask = sum(
            1 << ((start + offset) % 15) for offset in range(therm_count)
        )  # 温度计掩码按 15 取模环绕，不越界到 1C 区
        ones_mask = sum(
            1 << (15 + unit)
            for unit in range(2)
            if b4 and (2 * slot + unit - vertical_phase) % 16 < 8
        )
        main.append(therm_mask | ones_mask)  # 2C 段与 1C 段拼成本片 17 位主掩码
    return Fig11Masks(tuple(main), (sub_mask,) * SLOT_COUNT, True, reserved_hit)
