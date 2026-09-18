"""dem_closed_form.py -- DEM 开关译码的闭式逆映射（RTL 参考实现 + 穷举自检）。

职责：把 ``adi_model.dem.split_switch_command`` 的"逻辑位置 -> 物理地址"散播
（``main_order`` + ``put_along_axis`` 式 scatter）译码，等价地改写成"物理地址 ->
逻辑位置"的闭式，使 RTL 能用几个模加/模减逐位算出开关掩码，而不必例化 63/8 项 LUT。

来源（唯一事实来源，全部为仓库内代码，非转述）：
  * 前向译码：``src/adi_model/dem.py::split_switch_command``；
  * 消费端语义：``src/adi_model/weight_calibration.py::_terms`` 里的
    ``plus[..., order] = take``，其中 ``take[i] = clip(count - i, 0, 1)``，
    即 **物理地址 order[i] 被打开 <=> 逻辑位置 i < count**；
  * LCG 与状态数：``src/adi_model/mapper.py::_LCG_A`` / ``N_DEM_STATES``
    以及 ``dem_state_sequence``（``seq`` 是 **bank 内序号**）。
  * 显式不引用 ``src/adi_model/dac_arch.py::order_for_state`` —— 那是另一条物理
    通路（``np.roll`` + ``_A_MAIN=331`` / ``_A_SUB=173``），与本主路径无关；两条
    路径的位移常量不同，混用会得到"看起来对"的错映射。

单位契约：
  * 本模块所有地址 / 顺序 / 逻辑位置索引均为**无量纲整数**（物理单位序号）；
  * ``count`` 为整数**单位当量**（个），只作为比较阈值出现，不参与运算；
  * 不出现 [V]、[F]、[s]；不做任何模拟量计算。

参数分级：
  * ``width``/``height``/``n_main``/``n_sub``/``n_active`` 全部由 Config 派生 [—]；
  * "``height*width - n_main == 1``（被过滤掉的 cell 恰好 1 个）"是**拓扑前提**
    [假设]；``main()`` 会显式断言，不满足时本模块不适用（见下"适用域"）；
  * "``gcd(LCG_A, 512) == 1``"是**结构性事实**（可验，非假设）。

契约与适用域（越界即失效，``main()`` 逐条检查）：
  1. 主阵列闭式成立要求 ``ceil(sqrt(n_main))**2 - n_main == 1``（63 -> 8x8 满足）。
     ``n_main`` 使上式不等于 1 时（如 64 -> 8x8 差 0 个、60 -> 8x8 差 4 个），
     被过滤掉的 cell 不是 1 个，``j = cell - (cell > i*)`` 不成立。
  2. 仅当 ``cfg.dem_enable=True`` 时 ``states`` 才真正参与译码：``split_switch_command``
     在 ``dem_enable=False`` 时**强制** ``states = 0``，所有 sid 退化为恒等映射。
     注意 ``Config.paper_literal()`` 里 ``dem_enable`` 默认是 **False**
     （只有 ``dem_bridge_enable=True``），因此直接用工厂默认值验证会得到"全 PASS 的
     平凡解"——本模块显式 ``dem_enable=True``。
  3. 主阵列两个位移 ``states//width % height``、``states % width`` 与子阵列位移
     ``states//(width*height)`` 同源同 sid，必须由同一个 ``sid`` 寄存器的不同位段
     直接取用，不可各自独立计数（RTL 里禁止三条独立计数器）。

运行：
    PYTHONPATH=src <python> sim/ref/dem_closed_form.py
直接执行时脚本自行把 ``<repo>/src`` 插入 ``sys.path``。
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np

if __package__ in (None, ""):  # 直接执行时补齐 src 路径
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from adi_model.config import Config
from adi_model.dem import split_switch_command
from adi_model.mapper import _LCG_A
from adi_model.mapper import N_DEM_STATES as _N_DEM_STATES_MAPPER

# --------------------------------------------------------------------------
# 命题 1：LCG 归约
# --------------------------------------------------------------------------
N_DEM_STATES = 512
LCG_A = int(_LCG_A)
LCG_A_LOW = LCG_A % N_DEM_STATES  # RTL 里真正要乘的常量（9 位全加器）
assert N_DEM_STATES == _N_DEM_STATES_MAPPER, "状态数与 mapper.py 失同步"


def lcg_state(seq: np.ndarray | int) -> np.ndarray:
    """模型口径：``sid = (seq * LCG_A) % 512``。

    Args:
        seq: bank 内序号 [无量纲，非负整数]（可为数组）。
    Returns:
        与 seq 同形的 int64 DEM 状态索引，值域 [0, 512)。
    """
    return (np.asarray(seq, dtype=np.int64) * LCG_A) % N_DEM_STATES


def lcg_state_rtl(seq: np.ndarray | int) -> np.ndarray:
    """RTL 口径：9 位计数器 ``seq & 511`` 乘 ``LCG_A % 512``，取低 9 位。

    Args:
        seq: bank 内序号 [无量纲，非负整数]（可为数组）。
    Returns:
        与 seq 同形的 int64 状态索引；恒等于 :func:`lcg_state`。
    """
    return ((np.asarray(seq, dtype=np.int64) & (N_DEM_STATES - 1)) * LCG_A_LOW) & (N_DEM_STATES - 1)


# --------------------------------------------------------------------------
# 几何（与 split_switch_command 同源）
# --------------------------------------------------------------------------
def geometry(cfg: Config) -> tuple[int, int, int, int]:
    """返回 (n_main, n_sub, width, height)，公式与 ``split_switch_command`` 逐字相同。

    Args:
        cfg: Config；取 dac_n_main / dac_n_sub。
    Returns:
        (n_main, n_sub, width, height)，均为无量纲整数。
    """
    nm, ns = int(cfg.dac_n_main), int(cfg.dac_n_sub)
    width = math.ceil(math.sqrt(nm))
    height = math.ceil(nm / width)
    return nm, ns, width, height


def shift_row_col(sid: np.ndarray | int, width: int, height: int) -> tuple[np.ndarray, np.ndarray]:
    """主阵列的两个位移 (rh, ch) = (sid // width % height, sid % width)。

    Args:
        sid: DEM 状态 [无量纲整数]（可为数组）。
        width: 主阵列 cell 网格宽 [个]。
        height: 主阵列 cell 网格高 [个]。
    Returns:
        (rh, ch) 二元组，均为与 sid 同形的 int64 数组 [无量纲，物理单位序号位移]。
    """
    sid = np.asarray(sid, dtype=np.int64)
    return (sid // width) % height, sid % width


def excluded_cell(sid: np.ndarray | int, width: int, height: int) -> np.ndarray:
    """被 ``order < n_main`` 过滤掉的唯一 cell 索引 i*（rr==h-1 且 cc==w-1）。

    Args:
        sid: DEM 状态 [无量纲整数]（可为数组）。
        width: 网格宽 [个]；height: 网格高 [个]。
    Returns:
        与 sid 同形的 int64 数组，cell 索引 i* ∈ [0, height*width) [无量纲]。
        拓扑前提 ``height*width - n_main == 1`` 满足时该 cell 是唯一被滤掉的。
    """
    rh, ch = shift_row_col(sid, width, height)
    return ((height - 1 - rh) % height) * width + ((width - 1 - ch) % width)


# --------------------------------------------------------------------------
# 命题 2：主阵列 逻辑位置 <- 物理地址
# --------------------------------------------------------------------------
def main_logical_index(addr: np.ndarray, sid: np.ndarray, width: int, height: int) -> np.ndarray:
    """主阵列逆映射：物理地址 -> 逻辑位置。

         rr, cc = divmod(addr, width)                  # addr 为 order 值（物理地址）
         row = (rr - rh) % height, col = (cc - ch) % width
         cell = row * width + col
         j = cell - (cell > i*)                        # 去掉被过滤的那一个 cell

    Args:
        addr: (A,) 物理地址 [无量纲，物理单位序号]，取值 [0, height*width)。
        sid: (S,) DEM 状态 [无量纲整数]。
        width: 网格宽 [个]；height: 网格高 [个]。
    Returns:
        (S, A) int64 逻辑位置 j [无量纲]；``j[p] < count`` 即"物理地址 p 被打开"。
        对 ``addr == height*width - 1``（即被排除的那一格）返回 i*，见模块 docstring。
    """
    addr = np.asarray(addr, dtype=np.int64)
    sid = np.asarray(sid, dtype=np.int64)
    rr, cc = addr // width, addr % width
    rh, ch = shift_row_col(sid, width, height)
    cell = ((rr[None, :] - rh[:, None]) % height) * width + ((cc[None, :] - ch[:, None]) % width)
    i_star = excluded_cell(sid, width, height)[:, None]
    return cell - (cell > i_star)


def main_unit_on(count: np.ndarray, sid: np.ndarray, width: int, height: int) -> np.ndarray:
    """主阵列开关掩码：``on[s, p] = (j[s, p] < count[s])``。

    Args:
        count: (S,) 主阵列导通的**单位个数** [无量纲，单位当量，整数]。
        sid: (S,) DEM 状态 [无量纲整数]。
        width/height: 网格尺寸 [个]。
    Returns:
        (S, height*width) bool。列 ``p < n_main`` 是真正的主阵列物理地址；
        列 ``p == height*width - 1`` 是 RTL 里不存在的地址（该格被过滤），
        调用方应只取前 n_main 列。
    """
    addr = np.arange(height * width, dtype=np.int64)
    j = main_logical_index(addr, sid, width, height)
    return j < np.asarray(count, dtype=np.int64)[:, None]


# --------------------------------------------------------------------------
# 命题 3：子阵列 逻辑位置 <- 物理地址
# --------------------------------------------------------------------------
def sub_logical_index(
    addr: np.ndarray, sid: np.ndarray, width: int, height: int, n_sub: int
) -> np.ndarray:
    """子阵列逆映射：物理地址 q -> 逻辑位置 ``k = (q - sid // (width*height)) % n_sub``。

    Args:
        addr: (A,) 子阵列物理地址 [无量纲]，取值 [0, n_sub)。
        sid: (S,) DEM 状态 [无量纲整数]。
        width/height: 主阵列网格尺寸（用于取 ``width*height`` 这个位移除数）[个]。
        n_sub: 子阵列单位数 [个]。
    Returns:
        (S, A) int64 逻辑位置 k [无量纲]；``k < sub_count`` 即该物理地址被打开。
    """
    shift = (np.asarray(sid, dtype=np.int64) // (width * height)) % n_sub
    addr = np.asarray(addr, dtype=np.int64)
    return (addr[None, :] - shift[:, None]) % n_sub


# --------------------------------------------------------------------------
# 命题 4：桥接零和交换
# --------------------------------------------------------------------------
def bridge_rank(sid: np.ndarray, n_active: int) -> np.ndarray:
    """桥接 rank：``(i - sid % n_active) % n_active``。

    Args:
        sid: (S,) DEM 状态 [无量纲整数]。
        n_active: 活动 slice 数 [个]。
    Returns:
        (S, n_active) int64 rank [无量纲]。
    """
    sid = np.asarray(sid, dtype=np.int64)
    return (np.arange(n_active)[None, :] - (sid % n_active)[:, None]) % n_active


def bridge_signs(sid: np.ndarray, n_active: int) -> np.ndarray:
    """模型逐字口径的符号：``(rank < h) - ((rank >= h) & (rank < 2h))``，h = n_active//2。

    Args:
        sid: (S,) DEM 状态 [无量纲整数]。
        n_active: 活动 slice 数 [个]。
    Returns:
        (S, n_active) int64，取值 {-1, 0, +1} [无量纲]。
        n_active 为偶数时恒为 ±1（无 0），故逐 sid 零和。
    """
    rank = bridge_rank(sid, n_active)
    half = n_active // 2
    return (rank < half).astype(np.int64) - ((rank >= half) & (rank < 2 * half)).astype(np.int64)


def bridge_signs_simple(sid: np.ndarray, n_active: int) -> np.ndarray:
    """桥接符号的**简洁闭式**（RTL 形式）：``+1 iff rank < n_active//2 else -1``。

    Args:
        sid: (S,) DEM 状态 [无量纲整数]。
        n_active: 活动 slice 数 [个]（本模块只对**偶数** n_active 声明其等价）。
    Returns:
        (S, n_active) int64，取值 {+1, -1} [无量纲]。
        等价于"以 ``-sid`` 为起点、沿 i 递增的前一半 +1 / 后一半 -1"的循环窗。
        即：``signs[s, i] = +1 <=> ((i - sid) % n_active) < n_active/2``。

    Notes:
        n_active 为奇数时模型口径在 ``rank == n_active-1`` 处给 0（非零和），
        该简洁式不再与之等价；本模块的零和结论仅对偶数 n_active 成立。
    """
    rank = bridge_rank(sid, n_active)
    return np.where(rank < n_active // 2, 1, -1).astype(np.int64)


def bridge_slice_codes(
    code: np.ndarray, sid: np.ndarray, n_active: int, dac_levels: int
) -> np.ndarray:
    """桥接后的逐 slice 码：``clip(code) + signs * min(1, min(code, levels-1-code))``。

    Args:
        code: (N,) 逻辑 RDAC 码 [无量纲，单位当量]。
        sid: (N,) DEM 状态 [无量纲整数]。
        n_active: 活动 slice 数 [个]。
        dac_levels: 可寻址电平总数 [个]（paper_literal 下 = 512）。
    Returns:
        (N, n_active) float，逐 slice 逻辑码 [无量纲，单位当量]。
        ``amount = min(1, min(code, dac_levels-1-code))`` 保证交换后仍在 [0, levels-1]，
        且两端码（code = 0 或 levels-1）不交换，故饱和是显式的。
    """
    code = np.clip(np.asarray(code, dtype=np.int64), 0, dac_levels - 1)
    signs = bridge_signs(sid, n_active)
    amount = np.minimum(1, np.minimum(code, dac_levels - 1 - code))
    return (code[:, None] + signs * amount[:, None]).astype(float)


def bridge_split_counts(
    code: np.ndarray, sid: np.ndarray, n_active: int, n_sub: int, dac_levels: int
) -> tuple[np.ndarray, np.ndarray]:
    """桥接后的 (main_counts, sub_counts)：``floor(sc / n_sub)`` 与其余数。

    Args:
        code: (N,) 逻辑 RDAC 码 [无量纲，单位当量]。
        sid: (N,) DEM 状态 [无量纲整数]。
        n_active: 活动 slice 数 [个]；n_sub: 子阵列单位数 [个]。
        dac_levels: 可寻址电平总数 [个]。
    Returns:
        (main_counts, sub_counts) 二元组，形状均为 (N, n_active) [无量纲，单位当量]；
        ``main_counts`` 最大为 floor((levels-1)/n_sub)，故一定可实现在主阵列上。
    """
    sc = bridge_slice_codes(code, sid, n_active, dac_levels)
    mc = np.floor_divide(sc, n_sub)
    return mc, sc - n_sub * mc


# --------------------------------------------------------------------------
# 穷举自检
# --------------------------------------------------------------------------
def main() -> None:
    """穷举自检四个命题；任一 FAIL 则 SystemExit(1)。

    Side effects: 只写 stdout；不读文件、不写文件。
    """
    failures: list[str] = []

    def check(name: str, ok: bool, cases: int, note: str = "") -> None:
        print(
            f"  [{'PASS' if ok else 'FAIL'}] {name}: {cases} cases" + (f" | {note}" if note else "")
        )
        if not ok:
            failures.append(name)

    cfg = Config.paper_literal(dem_enable=True)
    nm, ns, width, height = geometry(cfg)
    n_act = int(cfg.n_active)
    levels = int(cfg.dac_levels)
    print(
        f"配置: dac_n_main={nm} dac_n_sub={ns} width={width} height={height} "
        f"n_active={n_act} dac_levels={levels} dem_enable={cfg.dem_enable} "
        f"dem_bridge_enable={cfg.dem_bridge_enable}"
    )
    sids = np.arange(N_DEM_STATES, dtype=np.int64)

    # ---- 前提 0：拓扑与开关前提 ----
    print("前提检查（适用域）")
    check("拓扑: height*width - n_main == 1", height * width - nm == 1, 1, f"{height*width}-{nm}")
    cfg_off = Config.paper_literal()  # 工厂默认 dem_enable=False
    off = split_switch_command(cfg_off, np.full(N_DEM_STATES, 10.0), sids.astype(float))
    ident = np.broadcast_to(np.arange(nm), off.main_order.shape)
    check(
        "开关: dem_enable=False 时 states 被强制为 0（全部 sid 退化为恒等）",
        bool(np.array_equal(off.main_order, ident)),
        N_DEM_STATES,
        f"Config.paper_literal() 默认 dem_enable={cfg_off.dem_enable}，必须显式覆盖",
    )

    # ---- 命题 1：LCG 归约 ----
    print("命题1: LCG 归约 sid = (seq * A) % 512")
    g = math.gcd(LCG_A, N_DEM_STATES)
    check("gcd(A, 512) == 1（512 状态全遍历的结构性前提）", g == 1, 1, f"gcd={g}")
    check(
        "512 状态双射（无重复、无遗漏）",
        int(np.unique(lcg_state(sids)).size) == N_DEM_STATES,
        N_DEM_STATES,
    )
    big = N_DEM_STATES * 1_000_000 + np.arange(4096, dtype=np.int64)
    seqs = np.concatenate([np.arange(200_000, dtype=np.int64), big])
    check(
        "闭式 == 精确: (seq*A)%512 == ((seq&511)*(A%512))&511",
        bool(np.array_equal(lcg_state(seqs), lcg_state_rtl(seqs))),
        int(seqs.size),
        f"A%512={LCG_A_LOW}，含 seq>=512e6 的 4096 点",
    )
    from adi_model.mapper import dem_state_sequence

    n_smp = 4096
    bank = (np.arange(n_smp) % 2).astype(np.int64)
    seq_bank = np.zeros(n_smp, dtype=np.int64)
    for bv in (0, 1):
        m = bank == bv
        seq_bank[m] = np.arange(int(m.sum()), dtype=np.int64)
    check(
        "模型 dem_state_sequence(bank) == 9 位计数器闭式",
        bool(np.array_equal(dem_state_sequence(n_smp, cfg, bank), lcg_state_rtl(seq_bank))),
        n_smp,
        "seq 为 bank 内序号（逐 bank 独立计数）",
    )

    # ---- 命题 2：主阵列逆映射 ----
    print("命题2: 主阵列物理地址 -> 逻辑位置")
    code_dummy = np.full(N_DEM_STATES, 10.0)
    cmd = split_switch_command(cfg, code_dummy, sids.astype(float))
    mo = cmd.main_order  # (512, 63) 模型真实输出
    check(
        "main_order 每行是 0..n_main-1 的排列",
        bool(np.all(np.sort(mo, axis=1) == np.arange(nm))),
        N_DEM_STATES,
    )

    addr_all = np.arange(height * width, dtype=np.int64)
    row, col = addr_all // width, addr_all % width
    rh, ch = shift_row_col(sids, width, height)
    order_full = ((row[None, :] + rh[:, None]) % height) * width + (
        (col[None, :] + ch[:, None]) % width
    )
    check(
        "前向重建（dem.py 三行展开）== 模型 main_order",
        bool(np.array_equal(order_full[order_full < nm].reshape(N_DEM_STATES, nm), mo)),
        N_DEM_STATES,
    )
    missing = np.setdiff1d(addr_all, np.unique(mo))
    check(
        "被 order<n_main 过滤掉的物理地址恰为 1 个且 == height*width-1",
        bool(np.array_equal(missing, np.array([height * width - 1]))),
        N_DEM_STATES,
        f"missing={missing.tolist()}",
    )
    i_star_model = np.argmax(order_full == (height * width - 1), axis=1)
    check(
        "被过滤 cell 索引 i* 闭式 == 前向重建的 argmax(order==63)",
        bool(np.array_equal(excluded_cell(sids, width, height), i_star_model)),
        N_DEM_STATES,
    )

    j_ref = np.full((N_DEM_STATES, height * width), -1, dtype=np.int64)
    np.put_along_axis(j_ref, mo, np.broadcast_to(np.arange(nm), mo.shape), axis=1)
    j_closed = main_logical_index(addr_all, sids, width, height)
    bad = np.argwhere(j_closed[:, :nm] != j_ref[:, :nm])
    check(
        "闭式 j(addr) == main_order 的逆（全部 sid × 全部主地址）",
        bad.size == 0,
        N_DEM_STATES * nm,
        (f"首个反例 sid={bad[0,0]} addr={bad[0,1]}" if bad.size else "0 mismatch"),
    )

    counts = np.arange(nm + 1, dtype=np.int64)
    take = np.clip(counts[:, None] - np.arange(nm)[None, :], 0, 1).astype(bool)
    plus = np.zeros((N_DEM_STATES, counts.size, height * width), dtype=bool)
    np.put_along_axis(
        plus,
        np.broadcast_to(mo[:, None, :], (N_DEM_STATES, counts.size, nm)),
        np.broadcast_to(take[None, :, :], (N_DEM_STATES, counts.size, nm)),
        axis=2,
    )
    expect = j_ref[:, None, :nm] < counts[None, :, None]
    check(
        "scatter 语义验证: plus[p] == (j_ref[p] < count)（_terms 的 take）",
        bool(np.array_equal(plus[:, :, :nm], expect)),
        N_DEM_STATES * counts.size * nm,
        "count 取遍 0..n_main",
    )

    # ---- 命题 3：子阵列逆映射 ----
    print("命题3: 子阵列物理地址 -> 逻辑位置")
    so = cmd.sub_order  # (512, 8) 模型真实输出
    slots = np.concatenate([mo, nm + so], axis=1)  # (512, 71)
    check(
        "物理槽位全覆盖: main_order ∪ (n_main+sub_order) == {0..70} 且无重复",
        bool(np.all(np.sort(slots, axis=1) == np.arange(nm + ns))),
        N_DEM_STATES,
        f"71 = {nm} main(0..{nm-1}) + {ns} sub({nm}..{nm+ns-1})；"
        "地址 63 属于子阵列而非主阵列，故 _terms 的 np.empty 无未写入槽位",
    )
    so_forward = (np.arange(ns)[None, :] + (sids // (width * height))[:, None]) % ns
    check(
        "子阵列前向重建 == 模型 sub_order", bool(np.array_equal(so, so_forward)), N_DEM_STATES * ns
    )
    k_ref = np.empty_like(so)
    np.put_along_axis(k_ref, so, np.broadcast_to(np.arange(ns), so.shape), axis=1)
    check(
        "闭式 (q - sid//(width*height)) % n_sub == sub_order 的逆",
        bool(np.array_equal(sub_logical_index(np.arange(ns), sids, width, height, ns), k_ref)),
        N_DEM_STATES * ns,
    )

    # ---- 命题 4：桥接零和交换 ----
    print("命题4: 桥接零和交换")
    signs = bridge_signs(sids, n_act)
    check("signs 逐 sid 零和（512 个 sid）", bool(np.all(signs.sum(axis=1) == 0)), N_DEM_STATES)
    check(
        "简洁式 signs = +1 iff ((i - sid) mod n_active) < n_active/2（偶数 n_active）",
        bool(np.array_equal(signs, bridge_signs_simple(sids, n_act))),
        N_DEM_STATES * n_act,
    )
    check(
        "signs 取值仅 ±1（偶数 n_active 下无 0）",
        bool(np.all(np.abs(signs) == 1)),
        N_DEM_STATES * n_act,
    )

    codes = np.arange(levels, dtype=np.float64)
    codes_t = np.tile(codes, N_DEM_STATES)
    sids_t = np.repeat(sids, codes.size)
    cmd2 = split_switch_command(cfg, codes_t, sids_t)
    mc_cf, sc_cf = bridge_split_counts(codes_t, sids_t, n_act, ns, levels)
    check(
        "闭式 slice_codes 复现模型 main_counts / sub_counts（全码 × 全 sid）",
        bool(np.array_equal(cmd2.main_counts, mc_cf) and np.array_equal(cmd2.sub_counts, sc_cf)),
        int(codes_t.size * n_act),
        f"{codes_t.size} 样本 = 512 sid × 512 码，逐 8 slice",
    )
    delta_slice = cmd2.main_counts * ns + cmd2.sub_counts - codes_t[:, None]
    amount_t = np.minimum(1, np.minimum(codes_t, levels - 1 - codes_t))
    check(
        "逐 slice 交换量 == signs * min(1, min(code, levels-1-code))（闭式）",
        bool(np.array_equal(delta_slice, bridge_signs(sids_t, n_act) * amount_t[:, None])),
        int(codes_t.size * n_act),
    )
    check(
        "交换量逐 sid 零和（sum_i signs_i * amount == 0，名义码不变）",
        bool(np.all(delta_slice.sum(axis=1) == 0)),
        int(codes_t.size),
        "注意：逐 slice 的码确实被改了，零和只在 slice 维求和后成立",
    )

    print()
    if failures:
        print(f"RESULT: FAIL（{len(failures)} 项）-> {failures}")
        raise SystemExit(1)
    print("RESULT: PASS（全部命题与前提）")
    i_star_demo = int(excluded_cell(np.array([1]), width, height)[0])
    print(
        f"RTL 要点: sid=(seq*{LCG_A_LOW})&511; rh=(sid>>3)&7, ch=sid&7; j=cell-(cell>i*); "
        f"k=(q-(sid>>6))&7; amount=min(1,min(code,{levels-1}-code)); sign=+1 iff ((i-sid)&7)<4"
    )
    print(f"          i* 随 sid 变（例：sid=1 -> i*={i_star_demo}）")


if __name__ == "__main__":
    main()
