"""三维 DEM 掩码与守恒/均匀性审计。

论文（ISSCC 2024 §9.8 第 3 页；slides 29-31）的三个维度是：
"Selection of the slices - 8/18"、"3b horizontal segmentation"、"3b vertical segmentation"，
并明确"the overall DEM range becomes two-dimensional, with 6b total range"。

本模块只做**数字掩码**与**审计**，不产生失配、不评估 INL。两套具名绑定对应两种真实几何，
不能混用：

* `fig11_masks`：专利 US10511316B2 Fig.11 的 17/5 阵列（15×2C + 2×1C 主、8/4/2/1/1 子、
  桥 C、β=1/17）。横向 15 列按**物理 ID** 旋转；B4 的 8/16 个 1C 必须**恰好选 8 个**，
  按物理 ID 取模会在 18 片池上选出 9~10 个并直接破坏名义权重，所以纵向按**组内位置**选，
  均匀性靠组旋转保证；子阵列是二进制权，不能旋转（旋转即改权重），这是模型边界。
* `unary_rotation_masks`：默认 63+8 分段池（主 63 温度计、子 7 温度计 + 1 位恒低）。
  两段都是 unary，可以整段旋转，按物理 ID 对齐。

审计是两件不同的事，不能互相替代：`nominal_conservation` 查同名码在所有状态下的名义电荷是否
相同；`usage_uniformity` 查每个物理单元是否被等频使用。前者过了后者仍可能不过。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .config import ConfigError
from .dem import SplitSwitchCommand, SplitSwitchGeometry, _integer_array
from .timing import SlicePlan

HORIZONTAL_COLUMNS = 15
VERTICAL_UNITS = 16
VERTICAL_ACTIVE = 8
SLICE_POOL = 18
SLICE_ACTIVE = 8


@dataclass(frozen=True)
class Dem3State:
    """一次转换冻结的三维 DEM 状态。

    Attributes:
        horizontal: 横向起点；按物理 ID 叠加。
        vertical: 纵向起点；按组内位置叠加。
        slice_step: 片选轮转计数；只用于生成计划，不参与掩码。
    """

    horizontal: int
    vertical: int
    slice_step: int = 0

    def __post_init__(self):
        """校验三个整数域的取值范围。"""
        for name, value, limit in (
            ("horizontal", self.horizontal, 16),
            ("vertical", self.vertical, VERTICAL_UNITS),
        ):
            if type(value) is not int or not 0 <= value < limit:
                raise ConfigError(f"DEM {name} phase must be an explicit integer in [0,{limit})")
        if type(self.slice_step) is not int or self.slice_step < 0:
            raise ConfigError("DEM slice_step must be a nonnegative integer")

    def to_int(self) -> int:
        """打包成 pipeline 使用的单一整数状态（低 4b 横向、次 4b 纵向）。"""
        return self.horizontal | (self.vertical << 4)

    @classmethod
    def from_int(cls, value: int) -> Dem3State:
        """从冻结整数恢复；slice_step 不参与掩码，恢复为 0。"""
        integer = int(_integer_array(value, "DEM3 state", ndim=0))
        if integer < 0 or integer > 0xFF:
            raise ConfigError("packed DEM3 state must fit in 8 bits")
        return cls(integer & 0xF, (integer >> 4) & 0xF)

    @classmethod
    def sequence(cls, n_samples: int) -> np.ndarray:
        """按样本推进的默认状态序列：横向 +1（模 15）、纵向 +1（模 16）。

        两个相位互质推进，240 笔覆盖全部 (h,v) 组合。这是可复算的确定性铺满，
        不是原芯片的随机序列；片选由计划单独负责。
        """
        if type(n_samples) is not int or n_samples < 1:
            raise ConfigError("DEM3 sequence needs a positive sample count")
        index = np.arange(n_samples, dtype=np.int64)
        return (index % HORIZONTAL_COLUMNS) | ((index % VERTICAL_UNITS) << 4)


def fig11_masks(geometry, codes, states, slice_ids) -> SplitSwitchCommand:
    """Fig.11 17/5 阵列的掩码映射（专利几何，非默认池）。

    主阵列 17 位：0..14 是 15 个 2C，15/16 是两个可独立控制的 1C。
    2C 段按 `(horizontal + 物理ID) % 15` 旋转；1C 段在 B4=1 时按
    `(2*组内位置 + unit + vertical) % 16 < 8` 恰好选 8 个。
    子阵列 5 位：B5..B8 为二进制权，末位恒低。

    Args:
        geometry: 必须恰为 17 主 / 5 子。
        codes: (N,) 9 位码。
        states: (N,) 打包状态。
        slice_ids: (N,8) 物理 ID。

    Returns:
        SplitSwitchCommand，已校验并冻结。

    Raises:
        ConfigError: 几何不是 Fig.11 形状、码越界或维度不符。
    """
    if not isinstance(geometry, SplitSwitchGeometry):
        raise ConfigError("fig11_masks requires an explicit SplitSwitchGeometry")
    if geometry.n_main != 17 or geometry.n_sub != 5:
        raise ConfigError("fig11_masks implements the Fig.11 17/5 split array only")
    values = _integer_array(codes, "fig11 codes", ndim=1)
    packed = _integer_array(states, "fig11 states", ndim=1)
    if values.shape != packed.shape:
        raise ConfigError("fig11 codes and states must belong to the same samples")
    if np.any((values < 0) | (values > 511)):
        raise ConfigError("fig11 codes must be actual 9-bit codes")
    ids = geometry.validate_slice_ids(slice_ids, n_samples=len(values))

    main = np.zeros((len(values), ids.shape[1], 17), dtype=bool)
    sub = np.zeros((len(values), ids.shape[1], 5), dtype=bool)
    for index in range(len(values)):
        state = Dem3State.from_int(int(packed[index]))
        code = int(values[index])
        thermal = code >> 5
        b4 = (code >> 4) & 1
        for position, physical in enumerate(ids[index]):
            start = (state.horizontal + int(physical)) % HORIZONTAL_COLUMNS
            for offset in range(thermal):
                main[index, position, (start + offset) % HORIZONTAL_COLUMNS] = True
            if b4:
                for unit in range(2):
                    column = 2 * int(position) + unit
                    if (column + state.vertical) % VERTICAL_UNITS < VERTICAL_ACTIVE:
                        main[index, position, 15 + unit] = True
            for unit in range(4):
                sub[index, position, unit] = bool((code >> (3 - unit)) & 1)
    return SplitSwitchCommand(main, sub).validated(geometry, len(values))


def unary_rotation_masks(geometry, codes, states, slice_ids) -> SplitSwitchCommand:
    """默认 63+8 分段池的两段 unary 旋转（主 63、子 7，子末位恒低）。

    与 `defaults._segmented_mask` 的码分解完全相同（main=code//8、sub=code%8），
    只把温度计起点按物理 ID 旋转：主段 `(horizontal + pid) % 63`、子段 `(vertical + pid) % 7`。
    两段都是 unary，旋转不改变选通数量，所以名义权重逐位不变。

    Args:
        geometry: 必须恰为 63 主 / 8 子（第 8 位恒低）。
        codes/state/slice_ids: 同上。

    Returns:
        SplitSwitchCommand。

    Raises:
        ConfigError: 几何或码域不符。
    """
    if not isinstance(geometry, SplitSwitchGeometry):
        raise ConfigError("unary_rotation_masks requires an explicit SplitSwitchGeometry")
    if geometry.n_main != 63 or geometry.n_sub != 8:
        raise ConfigError("unary rotation implements the default 63/8 segmented pool only")
    values = _integer_array(codes, "rotation codes", ndim=1)
    packed = _integer_array(states, "rotation states", ndim=1)
    if values.shape != packed.shape:
        raise ConfigError("rotation codes and states must belong to the same samples")
    if np.any((values < 0) | (values > 511)):
        raise ConfigError("rotation codes must be actual 9-bit codes")
    ids = geometry.validate_slice_ids(slice_ids, n_samples=len(values))

    main = np.zeros((len(values), ids.shape[1], 63), dtype=bool)
    sub = np.zeros((len(values), ids.shape[1], 8), dtype=bool)
    columns = np.arange(63)
    sub_columns = np.arange(7)
    for index in range(len(values)):
        state = Dem3State.from_int(int(packed[index]))
        code = int(values[index])
        main_count = code // 8
        sub_count = code % 8
        for position, physical in enumerate(ids[index]):
            start = (state.horizontal + int(physical)) % 63
            main[index, position, (start + columns[:main_count]) % 63] = True
            if sub_count:
                sub_start = (state.vertical + int(physical)) % 7
                sub[index, position, (sub_start + sub_columns[:sub_count]) % 7] = True
    return SplitSwitchCommand(main, sub).validated(geometry, len(values))


def nominal_conservation(
    pool,
    binding,
    states,
    *,
    reference_high_v: float,
    reference_low_v: float,
    codes=None,
) -> dict:
    """检查同一码在所有 DEM 状态下的名义参考电荷是否相同。

    这直接检验"DEM 只重排、不改名义权重"。若 Fig.11 的 8/16 个 1C 按物理 ID 取模选，
    这里会立刻出现按码分布的非零离散度——这正是本审计存在的理由。

    Args:
        pool: 名义（零失配）池。
        binding: 具名掩码绑定。
        states: (S,) 待遍历的打包状态。
        reference_high_v/reference_low_v: 实际轨电压。
        codes: 待遍历码；None 表示全部 512 个。

    Returns:
        dict：最大离散度 [C]、最坏码、折合 LSB20、以及 `conserved`。
    """
    packed = _integer_array(states, "conservation states", ndim=1)
    if packed.size == 0:
        raise ConfigError("conservation audit needs at least one state")
    values = (
        np.arange(512, dtype=np.int64)
        if codes is None
        else _integer_array(codes, "conservation codes", ndim=1)
    )
    ids = np.broadcast_to(np.arange(SLICE_ACTIVE, dtype=np.int64), (packed.size, SLICE_ACTIVE))
    per_code = np.empty(values.size)
    scale = 0.0
    for index, code in enumerate(values):
        repeated = np.full(packed.size, int(code), dtype=np.int64)
        command = binding(pool.geometry, repeated, packed, ids)
        packet = pool.charge_from_masks(
            ids, command, reference_high_v=reference_high_v, reference_low_v=reference_low_v
        )
        per_code[index] = float(packet.charge_c.max() - packet.charge_c.min())
        scale = max(scale, float(np.max(np.abs(packet.charge_c))))
    worst = float(per_code.max())
    # 不同单元求和顺序会带来末位舍入差；判据是"小于浮点求和误差"，不是"恰好 0"。
    tolerance = 512 * np.finfo(float).eps * max(scale, 1e-30)
    return {
        "codes_checked": int(values.size),
        "states_checked": int(packed.size),
        "max_spread_c": worst,
        "worst_code": int(values[int(np.argmax(per_code))]),
        "conserved": bool(worst <= tolerance),
        "tolerance_c": float(tolerance),
        "max_spread_lsb20": float(
            worst / (abs(reference_high_v - reference_low_v) / 2**20)
        ),
        "_note": "spread is nominal-charge dispersion across DEM states, not INL",
    }


def usage_uniformity(pool, binding, plan: SlicePlan, states) -> dict:
    """统计每个物理片与每个单元的选通次数，检查是否等频。

    固定用满码 511 遍历，使所有可旋转单元都参与统计；这只衡量使用分布，
    不代表失配已被平均（见 docs 里的口径说明）。

    Args:
        pool: 提供几何的池。
        binding: 具名掩码绑定。
        plan: 实际转换计划。
        states: (N,) 每笔冻结状态。

    Returns:
        dict：片使用极差、主单元使用极差、子单元使用极差与 `uniform` 判定。
    """
    conv = _integer_array(plan.conv, "plan conversion IDs", ndim=2)
    packed = _integer_array(states, "usage states", ndim=1)
    if conv.shape[0] != packed.shape[0]:
        raise ConfigError("usage audit needs one DEM state per conversion record")
    codes = np.full(conv.shape[0], 511, dtype=np.int64)
    command = binding(pool.geometry, codes, packed, conv)
    slice_counts = np.bincount(conv.ravel(), minlength=pool.geometry.n_slices)
    main_counts = command.main_on.sum(axis=(0, 1))[:HORIZONTAL_COLUMNS]
    sub_counts = command.sub_on.sum(axis=(0, 1))
    # 子阵列末位恒接低轨（见 capacitors.SUB_WEIGHTS），永远不选通，不参与均匀性判据。
    switchable_sub = sub_counts[:-1]
    result = {
        "samples": int(conv.shape[0]),
        "slice_counts": slice_counts.tolist(),
        "slice_range": int(slice_counts.max() - slice_counts.min()),
        "main_unit_counts": main_counts.tolist(),
        "main_unit_range": int(main_counts.max() - main_counts.min()),
        "sub_unit_counts": sub_counts.tolist(),
        "sub_unit_range": int(switchable_sub.max() - switchable_sub.min()),
        "sub_last_element_always_low": bool(sub_counts[-1] == 0),
    }
    result["uniform"] = bool(
        result["slice_range"] <= 1
        and result["main_unit_range"] <= 1
        and result["sub_unit_range"] <= 1
    )
    return result


def round_robin_plan(
    n_samples: int,
    *,
    n_slices: int = SLICE_POOL,
    n_active: int = SLICE_ACTIVE,
    stride: int | None = None,
) -> SlicePlan:
    """18 选 8 的连续窗轮转计划：相邻窗口互斥，长期使用次数差不超过 1。

    约束来自硬件而不是审美：同一周期里一片不能既采集又转换，所以相邻窗口必须不重叠，
    这要求 `2*n_active <= n_slices`；窗口起点至少前进 n_active。
    默认 stride = n_active。stride 与池大小不必互质——18/8 时 gcd=2，
    9 个窗口构成一个周期，每片恰被用 4 次；均匀性由 `usage_uniformity` 实测，不靠假设。

    Args:
        n_samples: 记录笔数。
        n_slices: 物理池大小。
        n_active: 每笔活动片数。
        stride: 窗口起点步长；None 表示 n_active。

    Returns:
        SlicePlan；第 i 笔转换窗与第 i+1 笔采集窗相邻且互斥。

    Raises:
        ConfigError: 维度非法、无空闲片或步长导致相邻窗口重叠。
    """
    if type(n_samples) is not int or n_samples < 1:
        raise ConfigError("round robin plan needs a positive sample count")
    if 2 * n_active > n_slices:
        raise ConfigError(
            "the pool cannot give every period a disjoint acquire/convert pair; "
            "add spare slices instead of overlapping windows"
        )
    step = n_active if stride is None else stride
    if type(step) is not int or step < n_active:
        raise ConfigError("round robin stride must be an integer not smaller than the group")

    def window(index: int) -> np.ndarray:
        start = (index * step) % n_slices
        return (start + np.arange(n_active)) % n_slices

    conv = np.stack([window(index) for index in range(n_samples)])
    acq = np.stack([window(index + 1) for index in range(n_samples)])
    overlap = np.array(
        [len(np.intersect1d(conv[index], acq[index])) for index in range(n_samples)]
    )
    if np.any(overlap != 0):
        raise ConfigError(
            "round robin stride makes a slice acquire and convert in the same period"
        )
    return SlicePlan(
        conv,
        acq,
        np.arange(n_samples, dtype=np.int64) - 1,
        np.ones(n_samples, dtype=bool),
        f"DEM3D disjoint window stride={step} over {n_slices}; uniform checked by audit",
        {},
    )


__all__ = [
    "Dem3State",
    "HORIZONTAL_COLUMNS",
    "SLICE_ACTIVE",
    "SLICE_POOL",
    "VERTICAL_ACTIVE",
    "VERTICAL_UNITS",
    "fig11_masks",
    "nominal_conservation",
    "round_robin_plan",
    "unary_rotation_masks",
    "usage_uniformity",
]
