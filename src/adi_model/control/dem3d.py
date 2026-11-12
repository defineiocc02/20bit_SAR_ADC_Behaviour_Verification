"""三维 DEM 掩码与守恒/均匀性审计。

论文（ISSCC 2024 §9.8 第 3 页；slides 29-31）的三个维度是：
"Selection of the slices - 8/18"、"3b horizontal segmentation"、"3b vertical segmentation"。

本模块只做**数字掩码**与**审计**，不产生失配、不评估 INL。两套具名绑定对应两种真实几何：

* `fig11_masks`：专利 US10511316B2 Fig.11/17 的 17/5 阵列（15×2C + 2×1C 主、
  8/4/2/1/1 子、桥 C、β=1/17）。横向 15 列按物理 ID 旋转。
  B4 在原件图上是一条单步（FIG.17 标 `2x`，即两个 1C 中的一个），
  早先"B4 选 8 个 1C"的写法已被原件图证伪，见 docs/mechanisms/fig19_fig17_decode.md。
  子阵列是二进制权，不能旋转（旋转即改权重）。
* （已删除）`unary_rotation_masks`：63+8 分段池的掩码绑定。用户 2026-11-12 裁决
  "63 归采样/DAC 层披露实施例、主链只跑 17/5"，本符号与 `defaults._segmented_mask`
  一并删除，不留别名、不放 legacy 目录。

**相位空间必须覆盖被旋转的列数**：早期版本把横向相位限定在 15 个值，而主阵列有 63 列，
于是固定物理片在低码下只访问 15 个单元（外部审查 A01 的最小反例）。
现在横向状态是 0..columns-1 的完整空间，`usage_uniformity` 也按
`[physical_slice_id, physical_unit_id]` 分码统计，而不是只测满码。

审计是两件不同的事，不能互相替代：`nominal_conservation` 查同名码在所有状态下的名义电荷是否
相同；`usage_uniformity` 查每个物理单元是否被等频使用。前者过了后者仍可能不过。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from adi_model.control.dem import SplitSwitchCommand, SplitSwitchGeometry, _integer_array
from adi_model.control.timing import SlicePlan
from adi_model.simulation.config import ConfigError

FIG11_COLUMNS = 15
VERTICAL_UNITS = 16
VERTICAL_ACTIVE = 8
SLICE_POOL = 18
SLICE_ACTIVE = 8
HORIZONTAL_LIMIT = 64


_MASK64 = np.uint64(0xFFFF_FFFF_FFFF_FFFF)


def _mix64(value: np.ndarray) -> np.ndarray:
    """splitmix64 的向量化版本：把相邻整数打散，用于解耦相位与计划周期。"""
    x = (value + np.uint64(0x9E3779B97F4A7C15)) & _MASK64
    x = ((x ^ (x >> np.uint64(30))) * np.uint64(0xBF58476D1CE4E5B9)) & _MASK64
    x = ((x ^ (x >> np.uint64(27))) * np.uint64(0x94D049BB133111EB)) & _MASK64
    return x ^ (x >> np.uint64(31))


@dataclass(frozen=True)
class Dem3State:
    """一次转换冻结的三维 DEM 状态。

    Attributes:
        horizontal: 横向起点，0..63；按物理 ID 叠加后对**实际列数**取模。
            取值范围必须覆盖被旋转的列数（默认池 63 列、Fig.11 15 列），
            否则低码会偏用前几列。
        vertical: 纵向起点 0..15；按组内位置叠加。
        slice_step: 片选轮转计数；只用于生成计划，不参与掩码。
    """

    horizontal: int
    vertical: int
    slice_step: int = 0

    def __post_init__(self):
        """校验三个整数域的取值范围。"""
        for name, value, limit in (
            ("horizontal", self.horizontal, HORIZONTAL_LIMIT),
            ("vertical", self.vertical, VERTICAL_UNITS),
        ):
            if type(value) is not int or not 0 <= value < limit:
                raise ConfigError(f"DEM {name} phase must be an explicit integer in [0,{limit})")
        if type(self.slice_step) is not int or self.slice_step < 0:
            raise ConfigError("DEM slice_step must be a nonnegative integer")

    def to_int(self) -> int:
        """打包成 pipeline 使用的单一整数状态（低 6b 横向、次 4b 纵向）。"""
        return self.horizontal | (self.vertical << 6)

    @classmethod
    def from_int(cls, value: int) -> Dem3State:
        """从冻结整数恢复；slice_step 不参与掩码，恢复为 0。"""
        integer = int(_integer_array(value, "DEM3 state", ndim=0))
        if integer < 0 or integer > 0x3FF:
            raise ConfigError("packed DEM3 state must fit in 10 bits")
        return cls(integer & 0x3F, (integer >> 6) & 0xF)

    @classmethod
    def sequence(
        cls,
        n_samples: int,
        *,
        columns: int = 63,
        vertical: int = 7,
        plan_period: int = 9,
    ):
        """确定性的相位序列：每个"计划周期"额外前移一个与列数互质的步长。

        为什么不能简单用 `index % columns`：18 选 8 且相邻窗互斥时，片选计划的周期是
        `18/gcd(stride,18)=9`；固定物理片只在 `index ≡ r (mod 9)` 的样本上活动，
        于是 `index % 63` 在这条子序列上只取 7 个值——低码下只用到 7 个单元。
        外部审查 A01 报的 15/63 是同一类混叠（旧实现相位空间只有 15 个值）。

        修正：`h = (index + step*(index // plan_period)) % columns`，
        沿子序列（i = r + 9k）变成 `h = r + k*(9+step)`；只要 `gcd(9+step, columns)=1`
        就能铺满全部列。默认 step=7 → 16，与 63 和 15 都互质。
        纵向用 `(11*index) % vertical`，沿子序列为 3k，与 16 互质。

        纵向**周期必须等于被旋转的子阵列单元数**（默认池 7）。若用 16 再 `%7`，
        16=2x7+2 会让前两个余数多出现一次，子单元使用比变成 3:2
        （外部审查 2 的实测：756/756/504x5）。Fig.11 的 8/16 选择仍需 16，调用方显式传。

        序列完全确定，不需要保存随机状态；实际覆盖与卡方由 `usage_uniformity` 实测上报。
        """
        if type(n_samples) is not int or n_samples < 1:
            raise ConfigError("DEM3 sequence needs a positive sample count")
        if type(columns) is not int or not 1 <= columns <= HORIZONTAL_LIMIT:
            raise ConfigError("DEM3 sequence columns must be in [1,64]")
        if type(vertical) is not int or not 1 <= vertical <= VERTICAL_UNITS:
            raise ConfigError("DEM3 sequence vertical must be in [1,16]")
        if type(plan_period) is not int or plan_period < 1:
            raise ConfigError("DEM3 sequence plan_period must be a positive integer")
        import math

        step = 7
        if math.gcd(plan_period + step, columns) != 1:
            raise ConfigError(
                "phase step does not tile the column count for this plan period; "
                "pick a step coprime with both (step+period, columns)"
            )
        index = np.arange(n_samples, dtype=np.int64)
        horizontal = (index + step * (index // plan_period)) % columns
        vertical_phase = (11 * index) % vertical
        return horizontal | (vertical_phase << 6)


def fig11_masks(
    geometry, codes, states, slice_ids, *, s4_second_ones_cell: bool = False
) -> SplitSwitchCommand:
    """Fig.11 17/5 阵列的掩码映射（专利几何，非默认池）。

    主阵列 17 位：0..14 是 15 个 2C，15/16 是两个可独立控制的 1C
    （图上分别标 B4 与 S4）。
    2C 段按 `(horizontal + 物理ID) % 15` 旋转。
    **B4 是单步**：FIG.17 把 B4 画成常量 `1`、直通、一步 `2x`，FIG.19 顶层行也是
    一个 `C`，没有"8 格排列"。因此 B4=1 时每片只选一个 1C（unit 15），
    权重是 1C 而不是 8C。早先 `(2*pos+unit+v)%16<8` 的写法是工程杜撰，已删除。
    第二个 1C（S4）的编码规则图上未标，本轮**不进入名义码本**，
    只由 `s4_second_ones_cell` 开关供压力测试（默认关闭，且不计入名义增益）。
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
            start = (state.horizontal + int(physical)) % FIG11_COLUMNS
            for offset in range(thermal):
                main[index, position, (start + offset) % FIG11_COLUMNS] = True
            if b4:
                # 单步 1C：B4 每片各选自己的 unit 15，全 8 片合计 8 个 1C。
                # （FIG.17 把 B4 标 2x、也是常量 1 直通；它与子阵列的 8x/4x/2x 同属
                #  "共享权重按片分配"这一族，口径见 docs/mechanisms/fig19_fig17_decode.md §2.1。）
                # S4（unit 16）默认不参与码本，
                # 打开 s4_second_ones_cell 只是把第二片 1C 也接上，供参数扫描用；
                # 它会让名义增益多 1C，属于**已知工艺偏差压力项**，不得用来求名义码本。
                # 注意：原件 FIG.17 里 B4 是常量 1、直通、标 2x，且**没有任何一列 1C 随片轮转**；
                # `vertical` 相位只驱动 15 列 2C 的起点，故此处不引入纵向选择——
                # 早先 `(2*pos+unit+v)%16<8` 的写法既是杜撰，也会让同码 8 片权重不等。
                main[index, position, 15] = True
                if s4_second_ones_cell:
                    main[index, position, 16] = True
            for unit in range(4):
                sub[index, position, unit] = bool((code >> (3 - unit)) & 1)
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

    离散度同时给出三个口径，避免单位混用：

    * `max_spread_c`：电荷 [C]，原判据；
    * `max_spread_v`：除以该状态的归一化电容 [V]，即输入等效；
    * `max_spread_lsb20`：再除以 LSB20 [V]，得到真正的 LSB（不是法拉）。

    Args:
        pool: 名义（零失配）池。
        binding: 具名掩码绑定。
        states: (S,) 待遍历的打包状态。
        reference_high_v/reference_low_v: 实际轨电压。
        codes: 待遍历码；None 表示全部 512 个。

    Returns:
        dict：三个口径的最大离散度、最坏码与 `conserved`。
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
    lsb20_v = abs(reference_high_v - reference_low_v) / 2**20
    per_code_c = np.zeros(values.size)
    per_code_v = np.zeros(values.size)
    per_code_lsb = np.zeros(values.size)
    scale = 0.0
    c_norm_f = None
    for index, code in enumerate(values):
        repeated = np.full(packed.size, int(code), dtype=np.int64)
        command = binding(pool.geometry, repeated, packed, ids)
        packet = pool.charge_from_masks(
            ids, command, reference_high_v=reference_high_v, reference_low_v=reference_low_v
        )
        spread_c = float(packet.charge_c.max() - packet.charge_c.min())
        # 同一码的 8 片在采集/转换上是同一批 physical slice，归一化电容逐状态相同；
        # 仍从实际池取，避免把"相同"当假设。
        if c_norm_f is None:
            coefficients = pool.split_coefficients(ids[0:1])
            c_norm_f = float(np.asarray(coefficients["c_signal"])[0])
        per_code_c[index] = spread_c
        per_code_v[index] = spread_c / c_norm_f
        per_code_lsb[index] = spread_c / c_norm_f / lsb20_v
        scale = max(scale, float(np.max(np.abs(packet.charge_c))))
    worst_c = float(per_code_c.max())
    tolerance = 512 * np.finfo(float).eps * max(scale, 1e-30)
    worst_index = int(np.argmax(per_code_lsb))
    return {
        "codes_checked": int(values.size),
        "states_checked": int(packed.size),
        "c_norm_f": float(c_norm_f) if c_norm_f is not None else float("nan"),
        "lsb20_v": float(lsb20_v),
        "max_spread_c": worst_c,
        "max_spread_v": float(per_code_v.max()),
        "max_spread_lsb20": float(per_code_lsb.max()),
        "worst_code": int(values[worst_index]),
        "conserved": bool(worst_c <= tolerance),
        "tolerance_c": float(tolerance),
        "_note": "spread is nominal-charge dispersion across DEM states, not INL",
    }


def _per_physical_slice_counts(main_on, sub_on, slice_ids, geometry):
    """Accumulate switch counts by **physical slice id**, not by slot.

    `main_on` has shape ``[sample, active_slot, unit]``. Summing axis 0 gives
    per-slot statistics; with a rotating pool a slot is not a slice, so that
    number cannot answer "is every physical unit used evenly".

    Returns ``(per_slice_main, per_slice_sub)`` with shapes
    ``[n_slices, n_main]`` and ``[n_slices, n_sub]``. Slices that are never
    selected stay as zero rows on purpose: a unit that should have been used and
    was not must not be filtered out of the report.
    """
    main = np.asarray(main_on)
    sub = np.asarray(sub_on)
    ids = np.asarray(slice_ids)
    if main.ndim != 3 or sub.ndim != 3 or ids.ndim != 2:
        raise ConfigError("switch masks and slice ids must be rank-3 / rank-2")
    if main.shape[:2] != ids.shape or sub.shape[:2] != ids.shape:
        raise ConfigError("switch masks must agree with the slice id plan")
    if main.shape[2] != geometry.n_main or sub.shape[2] != geometry.n_sub:
        raise ConfigError("switch mask unit extent disagrees with the geometry")
    per_slice_main = np.zeros((geometry.n_slices, geometry.n_main), dtype=np.int64)
    per_slice_sub = np.zeros((geometry.n_slices, geometry.n_sub), dtype=np.int64)
    for lane in range(ids.shape[1]):
        np.add.at(per_slice_main, ids[:, lane], main[:, lane, :])
        np.add.at(per_slice_sub, ids[:, lane], sub[:, lane, :])
    return per_slice_main, per_slice_sub


def usage_uniformity(
    pool,
    binding,
    plan: SlicePlan,
    states,
    *,
    codes=(8, 255, 511),
) -> dict:
    """按 `[物理片, 物理单元]` 统计选通次数，逐码判定均匀性。

    只测满码会假通过：满码把所有单元都选中，任何相位都"均匀"。必须分别测
    低码（1 个主单元）、中码、分段边界码；子阵列同理（k=code%8 决定选几个）。

    Args:
        pool: 提供几何的池。
        binding: 具名掩码绑定。
        plan: 实际转换计划。
        states: (N,) 每笔冻结状态。
        codes: 需要检查的码；默认 8（1 个主单元）、255、511。

    Returns:
        dict：每个码一份 `{main_unit_counts, main_range, covered, sub_*, uniform}`，
        以及整体 `uniform` 判定。
    """
    conv = _integer_array(plan.conv, "plan conversion IDs", ndim=2)
    packed = _integer_array(states, "usage states", ndim=1)
    if conv.shape[0] != packed.shape[0]:
        raise ConfigError("usage audit needs one DEM state per conversion record")
    report = {"samples": int(conv.shape[0]), "codes": {}}
    overall = True
    for code in codes:
        if type(code) is not int or not 0 <= code <= 511:
            raise ConfigError("usage audit codes must be explicit 9-bit integers")
        repeated = np.full(conv.shape[0], code, dtype=np.int64)
        command = binding(pool.geometry, repeated, packed, conv)
        # Per **physical slice** x unit, scattered through the real slice ids.
        # `command.main_on.sum(axis=0)` would only give per-slot statistics.
        per_slice_main, per_slice_sub = _per_physical_slice_counts(
            command.main_on, command.sub_on, conv, pool.geometry
        )
        main = per_slice_main.sum(axis=0)
        sub = per_slice_sub.sum(axis=0)
        selected_main = int(repeated[0] // 8) if pool.geometry.n_main == 63 else None
        active_main = main[main > 0]
        active_sub = sub[:-1][sub[:-1] > 0] if pool.geometry.n_sub == 8 else sub[sub > 0]
        main_range = int(active_main.max() - active_main.min()) if active_main.size else 0
        sub_range = int(active_sub.max() - active_sub.min()) if active_sub.size else 0
        covered_main = int(np.count_nonzero(main))
        expected = None
        main_chi2 = None
        if selected_main is not None and selected_main > 0:
            expected = conv.shape[0] * conv.shape[1] * selected_main / pool.geometry.n_main
            # 等频的正确统计口径是卡方，不是极差：极差在 63 个桶上天然有 ~4*sqrt(mean) 的涨落，
            # 把涨落当偏置会误报。chi2/dof ≈ 1 才是"无系统偏置"。
            dof = max(active_main.size - 1, 1)
            main_chi2 = float(np.sum((active_main - expected) ** 2) / expected / dof)
            # 判据只设上界：确定性轮转的 chi2 接近 0，是最均匀的情形，不是异常。
            uniform = main_chi2 <= 2.0
        else:
            uniform = main_range <= 1
        uniform = bool(uniform and (sub_range <= 1 or active_sub.size <= 1))
        slice_coverage = np.count_nonzero(per_slice_main, axis=1)
        report["codes"][str(code)] = {
            "per_slice_units_covered": slice_coverage.tolist(),
            "per_slice_coverage_min": int(slice_coverage.min()),
            "unused_physical_slices": np.flatnonzero(slice_coverage == 0).tolist(),
            "per_slice_sub_units_covered": np.count_nonzero(per_slice_sub, axis=1).tolist(),
            "main_unit_counts": main.tolist(),
            "main_range": main_range,
            "main_units_covered": covered_main,
            "expected_count_per_main_unit": expected,
            "main_chi2_per_dof": main_chi2,
            "sub_unit_counts": sub.tolist(),
            "sub_range": sub_range,
            "uniform": uniform,
        }
        overall = overall and uniform
    report["uniform"] = bool(overall)
    report["_note"] = "counts are per (physical slice aggregated, physical unit); not INL"
    return report


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
    "FIG11_COLUMNS",
    "HORIZONTAL_LIMIT",
    "SLICE_ACTIVE",
    "SLICE_POOL",
    "VERTICAL_ACTIVE",
    "VERTICAL_UNITS",
    "fig11_masks",
    "nominal_conservation",
    "round_robin_plan",
    "usage_uniformity",
]
