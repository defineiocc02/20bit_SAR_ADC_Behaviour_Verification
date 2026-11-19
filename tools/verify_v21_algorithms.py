#!/usr/bin/env python3
# =============================================================================
# 文件名     : verify_v21_algorithms.py
# 模块名     : v21 数字 IP 算法对照与制图
# 功能       : 用归档叶子输出、冻结向量和整数参考检验开关选择与加权出码。
# 设计说明   : 物理片 ID、掩码、整数码和标签保持原单位；输出区别向量复核、
#              合同穷举和 RTL 源级反例。图不声称记录了新的 HDL 仿真波形。
# =============================================================================
"""在仓库根运行；--out 指定保存摘要及出版图的新目录。

输入为 v21 生产清单、当前工程的数字算法参考、测试台期望数据及叶子输出节选。
生成的数据对应四种可复核对象：片号、逐事件命令、开关掩码、带标志的整数码。
时序图是接口约定的构造例，向量复算和源级诊断分别标注。
存储在数字证据清单里的哈希可用来定位出图时实际使用的文件。
"""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.colors import LinearSegmentedColormap, ListedColormap
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from adi_model.dem_dither import SUB, bridge_swap, post_mask, pre
from adi_model.control.dither_word import DEFAULT_DITHER_SEED, DitherWordGenerator
from adi_model.recip_q60 import recip_q60
from adi_model.sched18_rtl import Scheduler18Rtl
from cmp_dem_dither import hb, hb4
from contract_v2 import apply_bridge, base_post, charge_q8
from recon_oracle import Q30, oracle, schedule

INK, MODEL, ACCENT = "#20384a", "#116d78", "#ac552d"
STYLE = {
    "font.family": "DejaVu Sans",
    "font.size": 8,
    "axes.labelsize": 8,
    "xtick.labelsize": 7,
    "ytick.labelsize": 7,
    "axes.linewidth": 0.7,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "svg.fonttype": "none",
    "pdf.fonttype": 42,
    "svg.hashsalt": "v21-digital-ip",
}


# -----------------------------------------------------------------------------
# 1. 独立的数字检查。不得用重新生成的参考数据覆盖被核对的冻结向量。
# -----------------------------------------------------------------------------
def scheduling():
    """复位起点与向量一致，逐拍检查片号、三个 mask 和 8/8/2 互斥。

    冻结向量验证每拍选谁，更长的统计验证所有空闲片对有没有被选到。
    统计单位为 accepted advance，不假定其间隔是一拍或一个采样周期。
    """
    gold = np.loadtxt(ROOT / "tb/vec_r18_sched.txt", dtype=np.int64)
    scheduler = Scheduler18Rtl()
    # 选中次数是 ACQ 角色的计数。转换组是前笔的采集组，不能在同一个计数中重复计算。
    counts = np.zeros(18, dtype=int)
    idle_pairs = Counter()
    roles = np.zeros((18, 12), dtype=int)
    # 冻结的 mask 是整数字段，ID 是展开的八列；两者要分别检查。
    # 只比较数量会漏掉同一拍错选物理片的情况。
    mismatch = 0
    for i in range(20000):
        row = scheduler.step()
        counts[row["acq"]] += 1
        idle_pairs[tuple(row["idle"])] += 1
        if i < len(gold):
            observed = [
                i,
                *row["acq"],
                scheduler.acq_mask,
                scheduler.conv_mask,
                sum(1 << p for p in row["idle"]),
            ]
            mismatch += int(not np.array_equal(observed, gold[i]))
        if i < roles.shape[1]:
            roles[row["acq"], i] = 1
            roles[row["conv"], i] = 2
    if mismatch or scheduler.violations or len(idle_pairs) != 153:
        raise AssertionError("scheduler vector or partition mismatch")
    return dict(
        cycles=20000,
        matched_rows=len(gold),
        violations=len(scheduler.violations),
        counts=counts.tolist(),
        idle_pairs=len(idle_pairs),
        roles=roles.tolist(),
    )


def sar_cases():
    """从定向测试读取位决定和期望命令，按已决前缀独立复算。

    A 的第二次事件为保护码门限；B 的普通位按 MSB 至 LSB 提交。
    这些点是测试台激励的复算，时间轴使用事件序号。
    """
    text = (ROOT / "tb/v21/tb_i2_events.sv").read_text()

    def array(symbol):
        """取测试台中按事件顺序声明的整数数组；缺项直接作为输入错误。"""
        found = re.search(r"\b" + symbol + r"\s*\[[^\]]+\]\s*=\s*'\{([^}]+)\}", text)
        if not found:
            raise ValueError("benchmark array missing: " + symbol)
        return [int(v.strip()) for v in found[1].split(",")]

    # 位值为零仍可能是一次已决提交，需与完全不提交的 guard 回退区分。
    rows = {}
    for name, start, upper in (("A", 448, 5), ("B", 256, 0)):
        bits, decisions, expected = (array(name + suffix) for suffix in ("_BIT", "_BV", "_K"))
        if not (len(bits) == len(expected) == len(decisions)):
            raise AssertionError("event length mismatch")
        prefix = start
        values = []
        # 第一项对应已经装载的 Flash 前缀；后续只允许当前已决位改变前缀。
        for i, (bit, decision) in enumerate(zip(bits, decisions)):
            if i and decision:
                prefix = 512 if bit == 9 else prefix | (1 << bit)
            values.append(prefix + upper)
        if values != expected:
            raise AssertionError("SAR command mismatch")
        rows[name] = dict(upper=upper, bits=bits, decisions=decisions, commands=values)
    return rows


def masks_and_pre():
    """枚举完整合法命令域，对比两份实现的每一位和名义权重。

    只比较权重和会漏掉相同名义权重的错位。bridge 前后也可相同，
    所以另冻结一个已知会翻转九位的最小例子。
    """
    # 合法范围的两个端点和 guard 尾项都枚举，不能只检查九位普通码。
    # seq 的低三位与本叶选择无关，取 0 与 8 分别覆盖两个 half。
    cases = 0
    for k in range(521):
        for h in range(8):
            for v in range(8):
                first = post_mask(k, h, v)
                other, _ = base_post(k, h, v)
                for seq in (0, 8):
                    for coin in (0, 1):
                        actual = bridge_swap(first, h, v, seq, coin)
                        expected, _, _ = apply_bridge(other, h, v, seq, coin)
                        if actual != expected or charge_q8(actual) != 8 * k:
                            raise AssertionError((k, h, v, seq, coin))
                        cases += 1
    for word in range(64):
        for v in range(8):
            # 槽间的分数由整数分配得到，八槽平均应等于 T。
            bits, total, upper, lower = pre(word, v)
            slot_values = [sum(x * y for x, y in zip(row, SUB)) for row in bits]
            if sum(slot_values) != 8 * total or total != upper + lower:
                raise AssertionError((word, v, slot_values))
    # 选 k=8 可使保留列主半单元与子 4Cu 开关初值相反，便于观察离散交换。
    base = post_mask(8, 0, 0)
    exchanged = bridge_swap(base, 0, 0, 0, 1)
    # 逐位异或能定位交换影响的 1 个主位和 8 个子位，不能只比较置位数。
    changed = np.argwhere(np.asarray(base) != np.asarray(exchanged))
    if len(changed) != 9:
        raise AssertionError("bridge example changed")
    sample, total, upper, low = pre(53, 3)
    # 图中的受理序号对应软件独立重放；实际引脚和返回码由顶层 Verilator 回归核对。
    # 用已知状态序列和全字覆盖同时检查，防止误换种子、推进时机或输出位段。
    word_source = DitherWordGenerator()
    words = [word_source.step(sample_accept=True, sample_id=i).word for i in range(1024)]
    if words[:4] != [45, 7, 17, 40] or len(set(words)) != 64:
        raise AssertionError("on-chip dither word reference mismatch")
    return dict(
        post_checks=cases,
        pre_checks=64 * 8,
        base=base,
        bridge=exchanged,
        changed=changed.tolist(),
        preset_word=53,
        dither_reset_seed=DEFAULT_DITHER_SEED,
        first_dither_words=words[:8],
        distinct_in_1024=len(set(words)),
        sadc_pre_word53=0x2A,
        phase=3,
        U=upper,
        Ld=low,
        T=total,
        pre_values=[sum(x * y for x, y in zip(r, SUB)) for r in sample],
    )


def arithmetic():
    """验证近似倒数、修正倒数和既有 D5 期望码，保留无效向量。

    修正按 RTL 的一次下调、一次上调与余数判定执行。独立基准来自任意精度整除。
    有符号流水的期望输出直接取自同一份回归激励。实测退休结果由 Verilator 轨迹单独核对。
    """
    path = ROOT / "tb/vec_r18_recip.txt"
    # 第一份向量用于近似核自身。精确舍入由第二份向量和独立数学判据检查。
    # 不能把近似值与真商差一个单位误报成前一级向量失配。
    approximate = 0
    for line in path.read_text().splitlines():
        gain, expected = map(int, line.split())
        if recip_q60(gain) != expected:
            raise AssertionError("approximate reciprocal mismatch")
        approximate += 1
    histogram = Counter()
    flagged = 0
    total = 0
    two60 = 1 << 60
    for line in (ROOT / "tb/v21/vec_recip_rnd.txt").read_text().splitlines():
        gain, expected, err = map(int, line.split())
        total += 1
        if not 1 << 34 <= gain <= 1 << 36:
            if (expected, err) != (0, 1):
                raise AssertionError("out-of-range vector")
            flagged += 1
            continue
        # 比较时先保留近似值分布，随后再判定修正后的精确输出。
        # 合法 gain 一律非零，数学参考不会把域外零除误当正常结果。
        value = recip_q60(gain)
        q = value - int(value * gain > two60)
        q += int((q + 1) * gain <= two60)
        rem = two60 - q * gain
        corrected = q + int(2 * rem >= gain)
        if (
            not 0 <= rem < gain
            or err
            or corrected != expected
            or corrected != (2 * two60 + gain) // (2 * gain)
        ):
            raise AssertionError("reciprocal correction mismatch")
        histogram[value - expected] += 1
    # 每行除最终码外还有饱和与输入错误。三项一起比对才能排除错误零码碰巧相同。
    # 不合法的 G 或 alpha 先走错误支路，不能将其送进除法。
    recon_count = 0
    sat_count = 0
    err_count = 0
    for line in (ROOT / "tb/v21/vec_i4_recon.txt").read_text().splitlines():
        raw, wm, g, wp, a, b, code, sat, err = map(int, line.split()[:9])
        if not (
            16 * Q30 <= g <= 64 * Q30
            and Q30 // 2 <= a <= 2 * Q30
            and wp <= 128 * Q30
            and wm <= g + 128 * Q30
        ):
            fields = (0, 0, 1)
        else:
            result = oracle(raw, b, a, g, wm, wp)
            fields = tuple(result[k] for k in ("code", "sat", "err"))
        if fields != (code, sat, err):
            raise AssertionError("reconstruction vector mismatch")
        recon_count += 1
        sat_count += sat
        err_count += err
    # 同笔出码依赖事务顺序。保留受理沿和 tag 以便与实际报告逐行对照，
    # 这里生成的数字属于独立期望值，不将 Python 结果登记为硬件输出。
    signed_targets = []
    for case, tick, _, valid, tag, *bus in schedule():
        if valid and case in (6, 7):
            ref = oracle(*bus)
            signed_targets.append(
                dict(
                    case=case,
                    tick=tick,
                    tag=tag,
                    raw=bus[0],
                    x=ref["X"],
                    expected_code=ref["code"],
                    expected_err=ref["err"],
                )
            )
    return dict(
        approximate=approximate,
        rounded=total,
        flagged=flagged,
        delta_counts=dict(sorted(histogram.items())),
        reconstruction=recon_count,
        saturations=sat_count,
        errors=err_count,
        signed_stream_targets=signed_targets,
    )


# =============================================================================
# 2. 同一命令和单一系数的对照。掩码来自既有叶子记录，出码来自整数参考。
# =============================================================================
def switch_coverage():
    """从归档的叶子输出统计同一个命令下各等权位置被使用的次数。

    归档中每个相位只有一行。固定相位对照重复同一行 64 次，
    轮换组对 64 个不同相位各计一次；两组的分母与名义命令完全相同。
    输入是 tb_dem_dither 的文本格式，M 行有八个 20 位十六进制字，
    P 行有八个 4 位字。两类字的打包方向不同，须按各自接口拆位。
    返回每组的 8×8 计数表，以及同一相位下用于重构的 post 与 pre。
    横轴是宏列，纵轴是槽号。将行绑定到物理片号是下一项试验的输入。
    重复记录或不完整的相位空间会改变计数权重，必须在作图前报错。
    在原始输出节选里可以直接定位固定组的行头 M 283 2 3 0；
    D5 所用的预置行头是 P 53 3 4 5。两行来自分别驱动的叶子输出，
    它们组成一个定向的合法输入组合，没有据此重建一次顶层捕获的时间戳。
    """
    # 283 = 64×4 + 8×3 + 3。四个完整宏列加保留列里的三个槽共 35 个位置；
    # 数字余量 3 落在子支路，不能解释成另外三个主位置。
    command, fixed = 283, (2, 3)
    masks = {}
    preset = None
    fixture = ROOT / "tb/vec_dem_dither_effects.txt"
    for line in fixture.read_text().splitlines():
        if not line or line.startswith("#"):
            continue
        kind, *fields = line.split()
        controls = tuple(map(int, fields[:4]))
        slots = fields[4:]
        if len(slots) != 8:
            raise ValueError("capture must contain eight slots")
        if kind == "M":
            # 选择字在原始行中编码 coin×2+half。这里只接受两者均为零的记录，
            # 因而可直接与桥交换前的掩码核对。十六进制最低位对应主单元 0。
            code, h, v, selector = controls
            if any(not 0 <= int(x, 16) < 1 << 20 for x in slots):
                raise ValueError("out-of-range post mask")
            mask = [hb(x, 20) for x in slots]
            if (code, selector) != (command, 0) or (h, v) in masks or mask != post_mask(code, h, v):
                raise ValueError(f"unexpected DEM capture: {line}")
            if charge_q8(mask) != 8 * command:
                raise AssertionError("nominal command changed")
            masks[h, v] = np.array(mask, dtype=int)
        elif kind == "P":
            # 八个槽分别打包；预置字的最高位对应子单元 16 的采样开关。
            # 将它当成 post 的低位优先读法，会颠倒同一权重对采样、转换的作用。
            word, v, upper, fraction = controls
            if any(not 0 <= int(x, 16) < 16 for x in slots):
                raise ValueError("out-of-range preset nibble")
            bits = [hb4(x) for x in slots]
            if (
                preset is not None
                or (word, v, upper, fraction) != (53, 3, 4, 5)
                or bits != pre(word, v)[0]
            ):
                raise ValueError(f"unexpected preset capture: {line}")
            preset = np.array(bits, dtype=int)
        else:
            raise ValueError(f"unexpected capture record: {kind}")
    phases = {(h, v) for h in range(8) for v in range(8)}
    if set(masks) != phases or preset is None:
        raise ValueError("missing archived phase or preset")
    # 关闭桥交换时每个宏单元的两个主半单元同时开关，每对只计一次。
    # 子支路仍参与上面的完整名义命令检查，不将其混为等权主单元。
    stack = np.stack([masks[phase] for phase in sorted(phases)])
    macro = stack[:, :, :16:2]
    if not np.array_equal(macro, stack[:, :, 1:16:2]) or np.any(macro.sum(axis=(1, 2)) != 35):
        raise AssertionError("unexpected macro selection")
    n = len(phases)
    # 归档没有记录相位发生器在实机上的驻留时间。各相位等权是本实验的输入。
    # 用同一行乘 n 构造固定组，保持总事件数，不能当作 n 个独立硬件测量值。
    held = masks[fixed][:, :16:2] * n
    rotated = macro.sum(axis=0)
    # 每一相位都必须保住命令，累计总数也须相同。
    # 单凭右图的均匀性无法排除选择单元总数被改动，例如全零矩阵同样均匀。
    if np.count_nonzero(held) != 35 or not np.all(rotated == 35) or held.sum() != rotated.sum():
        raise AssertionError("phase coverage mismatch")
    return (
        dict(
            command=command,
            phases=n,
            fixed_hv=list(fixed),
            bridge_coin=0,
            selected_per_command=35,
            fixed_counts=held.tolist(),
            rotated_counts=rotated.tolist(),
            position_selections_per_group=int(held.sum()),
        ),
        masks[fixed],
        preset,
    )


def weight_effect(post, preset):
    """固定槽与实际开关，用已知单元系数检查 20 位输出的符号和改正量。

    先固定 raw 检查写入系数的作用方向。再按定点合同在 raw 端注入
    同一输入对应的已知残差；默认表和校正表接收完全相同的残差码。
    post 是槽序的 8×20 个转换位；preset 是槽序的 8×4 个采样位。
    13 位 raw 的单位是 ADC2 码，误差的单位是最终输出的 20 位码。
    每行返回固定 raw 的权重响应、注入后的 raw 与两张表各自的出码。
    oracle 内部依次执行定点乘加、倒数和舍入，图使用末级输出的整数差。
    名义 raw 为 4096。字 53 的粗码偏移 U=4，对应的前级粗码是 279。
    后续只使用已形成的命令 283，整个试验保持这份开关记录不变。
    改动权重代表独立提交的完整配置；每次装表的总线周期不在本图横轴上。
    原码位移是为已知系数构造的输入，不估计实际器件偏差的分布。
    """
    sid = 0
    ids = list(range(8))
    # 将槽 0 绑定到片 0，四次写入都针对同一物理片的不同子单元。
    # 其他七片的真实开关保留在求和中，否则每个系数对最终码的折算会变。
    raw, step = 4096, 1 << 20
    # 每个对照是一份完整可提交的配置；选不同子单元时从同一张名义表开始。
    # 主半单元在这里取 1/4，四个子支路分别取 1/32、1/64、1/128、1/128。
    # 八个槽的全部主权重之和是 32。只改子权重，归一化分母就始终相同。
    nominal = np.full((18, 20), Q30 // 4, dtype=np.int64)
    nominal[:, 16:] = [Q30 // 32, Q30 // 64, Q30 // 128, Q30 // 128]

    def evaluate(table, sample):
        """按 wsum 的物理地址与使能位取权重，返回该笔完整定点中间量。

        乘法在槽和单元两个方向逐位进行。G 的定义包含所有活动槽的主单元，
        不受 post 开关影响。偏置零、α=1 固定数字接口的其余自由度。
        """
        rows = table[ids]
        gain = int(rows[:, :16].sum())
        wm = int((rows * post).sum())
        wp = int((rows[:, 16:] * preset).sum())
        digital = oracle(sample, 0, Q30, gain, wm, wp)
        # 饱和钳位同样会抹掉码差。权重对照只使用未饱和的正常输出；
        # 正负交易交替时的 RTL 标志与标签由 tb_recon_stream 单独验收。
        if digital["sat"] or digital["err"]:
            raise AssertionError("the selected case must be in-range and unsaturated")
        return dict(gain=gain, wm=wm, wp=wp, **digital)

    base = evaluate(nominal, raw)
    # 图注引用这个公共基准。表或码箱中心变化时，先核对新物理口径再修改图注。
    if (base["gain"], base["code"]) != (32 * Q30, 567040):
        raise AssertionError("reference gain or published baseline code changed")
    # 对照权重表独立于四个试验。片 8 不在这笔的八个片号中，
    # 写入相同单位号与同一幅度也应保持 G、Wm、Wp、N 和末级码全部不变。
    # 如果实现误按槽序寻址，只有核对这种未参与转换的片号才有机会暴露它。
    outside = nominal.copy()
    outside[8, 18] += step
    if evaluate(outside, raw) != base:
        raise AssertionError("an inactive slice affected the transaction")
    cases = []
    # 四种掩码组合来自同一条归档记录，单位号是物理地址。每次只修改其中一个。
    # 同时修改四个单元会令正负项部分抵消，无法逐项辨认各个开关的贡献。
    for unit in (18, 16, 17, 19):
        before, after = int(preset[0, unit - 16]), int(post[0, unit])
        delta = after - before
        table = nominal.copy()
        table[sid, unit] += step
        held = evaluate(table, raw)
        # 对 α=1、b=0，一个 raw 步使 N 改变 64×1536。
        # 权重给 N 加 6×(post−pre)×δw。同一理想输入的两项必须相抵。
        # 本试验采用恰好整除的系数步长，不用输出码反推误差或挑选舍入结果。
        shift, remainder = divmod(-6 * delta * step, 64 * 1536)
        displaced = raw + shift
        if remainder or not 0 <= displaced < 8192:
            raise AssertionError("chosen injection is not exactly representable at ADC2 input")
        default = evaluate(nominal, displaced)
        corrected = evaluate(table, displaced)
        # 刻意少校一半可辨认输出是否真正依赖加载的系数。测试仍然用同一笔细码，
        # 只把写入值从已知偏差的全量改成一半，非零的两个残差应各余 16 码。
        half_table = nominal.copy()
        half_table[sid, unit] += step // 2
        half_error = evaluate(half_table, displaced)["code"] - base["code"]
        # 在 G=32 时，增量 2⁻¹⁰ 折到 20 位输出为 2²⁰×2⁻¹⁰/32 = 32 码。
        # 固定 raw 的试验先验证作用方向；相同注入后的 raw 再验证误差是否消失。
        # 逐项对 Wm、Wp 及 N 防止不同路径的错误恰在末次舍入时给出同一码。
        if (
            held["gain"] != base["gain"]
            or held["wm"] - base["wm"] != after * step
            or held["wp"] - base["wp"] != before * step
            or held["code"] - base["code"] != 32 * delta
            or default["code"] - base["code"] != -32 * delta
            or half_error != -16 * delta
            or corrected["N"] != base["N"]
            or corrected["code"] != base["code"]
        ):
            raise AssertionError(f"weight response mismatch for sub-unit {unit}")
        cases.append(
            dict(
                unit=unit,
                pre=before,
                post=after,
                held_raw_code=held["code"],
                raw_after_injection=displaced,
                nominal_code=default["code"],
                corrected_code=corrected["code"],
                nominal_error=default["code"] - base["code"],
                half_weight_error=half_error,
                corrected_error=corrected["code"] - base["code"],
            )
        )
    if {(r["pre"], r["post"]) for r in cases} != {(0, 0), (0, 1), (1, 0), (1, 1)}:
        raise AssertionError("both control states must be covered on each side")
    return dict(
        physical_ids=ids,
        changed_sid=sid,
        nominal_raw=raw,
        gain_q30=base["gain"],
        wm_q30=base["wm"],
        wp_q30=base["wp"],
        nominal_code=base["code"],
        offset_q24=0,
        alpha_q30=Q30,
        weight_step_q30=step,
        inactive_slice_test={"slice": 8, "unit": 18, "code": base["code"]},
        preset_word=53,
        rows=cases,
    )


# -----------------------------------------------------------------------------
# 图片都使用相同名义单位，图例区与数据坐标分开，确保缩小版心后仍可辨认。
# 选择性示例会给出字、相位或标签，数值总表中同时保存整组覆盖统计。
# 颜色之外再使用轮廓或文本标记，让灰度印刷也可区分前后状态。
# 3. 出版图。所有轴均为数字量；不能从一次逻辑推进推断实际纳秒时序。
# -----------------------------------------------------------------------------
def save(fig, out, name):
    """向量 PDF 适合论文排版，SVG 可编辑，PNG 用于本地和 GitHub 预览。"""
    fig.savefig(out / (name + ".png"), dpi=300, facecolor="white")
    fig.savefig(out / (name + ".svg"), metadata={"Date": None})
    # 输出身份只由输入与绘图环境决定。删除导出时钟字段，使同环境的 PDF 可逐字节复核。
    fig.savefig(
        out / (name + ".pdf"),
        metadata={"Creator": "v21 digital IP review", "CreationDate": None, "ModDate": None},
    )
    plt.close(fig)


def draw_schedule(result, out):
    """左图逐拍显示物理片归属，右图为每片相对均匀期望的偏离。

    调度公平性图用零中心的百分比轴，避免截短计数柱的基线夸大差异。
    热图的横轴为接受次数，不赋予这组离散事务额外的纳秒定时含义。
    """
    fig = plt.figure(figsize=(7.16, 2.65))
    left = fig.add_axes((0.073, 0.27, 0.37, 0.53))
    right = fig.add_axes((0.565, 0.27, 0.40, 0.53))
    colors = ["#e9eceb", "#c2dde0", "#246e7b"]
    left.imshow(
        result["roles"],
        origin="lower",
        aspect="auto",
        interpolation="nearest",
        cmap=ListedColormap(colors),
    )
    # 首列是复位后的第一次有效推进，沿前复位状态不混入稳态占用统计。
    left.set_xticks([0, 3, 7, 11], [1, 4, 8, 12])
    left.set_yticks([0, 5, 11, 17])
    left.set_xlabel("Accepted advance")
    left.set_ylabel("Physical slice")
    left.set_title("(a)  Slice ownership", weight="bold", loc="left", fontsize=9)
    counts = np.asarray(result["counts"])
    ideal = result["cycles"] * 8 / 18
    dev = 100 * (counts / ideal - 1)
    right.axhline(0, lw=0.7, color="#8d9ca1")
    right.vlines(np.arange(18), 0, dev, color="#8fb4bb", lw=1)
    right.scatter(np.arange(18), dev, color=MODEL, s=14, zorder=3)
    right.set_xlim(-0.7, 17.7)
    right.set_ylim(-0.65, 0.65)
    right.set_yticks([-0.5, 0, 0.5])
    right.set_xticks([0, 5, 11, 17])
    right.set_xlabel("Physical slice")
    right.set_ylabel("Selection deviation (%)")
    right.set_title("(b)  Allocation over 20,000 advances", weight="bold", loc="left", fontsize=8.6)
    fig.legend(
        handles=[
            Patch(color=c, label=l) for c, l in zip(colors[::-1], ("Convert", "Acquire", "Idle"))
        ],
        frameon=False,
        ncol=3,
        loc="lower center",
        bbox_to_anchor=(0.26, 0.005),
        fontsize=7,
    )
    fig.text(0.765, 0.065, "153 / 153 idle pairs observed", ha="center", fontsize=7.2, color=INK)
    save(fig, out, "slice-allocation")


def draw_events(rows, out):
    """显示两个定向激励下的已决命令。

    同一码可以连续出现在多个合法事件中。标记保留每次提交，
    只在数值改变处标数字，读者可以分别数出普通路径与 guard 路径的长度。
    """
    fig, axes = plt.subplots(1, 2, figsize=(7.16, 2.65))
    fig.subplots_adjust(left=0.095, right=0.98, top=0.77, bottom=0.25, wspace=0.37)
    for j, (name, title) in enumerate((("B", "Normal path"), ("A", "Guard path"))):
        ax = axes[j]
        data = rows[name]
        codes = data["commands"]
        x = np.arange(len(codes))
        steps = ["F"] + ["512?" if b == 9 else f"b{b}" for b in data["bits"][1:]]
        ax.step(x, codes, where="post", color=MODEL, lw=1.4)
        ax.scatter(x, codes, color=MODEL, s=17, zorder=3)
        # 阶梯纵轴是整数开关命令，正常与保护路径的原点和值域不同。
        # 每次有效提交均保留一个标记，即使拒绝某个 SAR 位后 k 保持不变。
        for i, (xi, k) in enumerate(zip(x, codes)):
            if i == 0 or codes[i - 1] != k:
                ax.annotate(
                    str(k),
                    (xi, k),
                    xytext=(2, 6),
                    textcoords="offset points",
                    fontsize=7,
                    color=INK,
                )
        ax.set_xticks(x, steps)
        ax.set_xlabel("Accepted decision")
        ax.set_ylabel("RDAC command")
        rng = max(codes) - min(codes)
        ax.set_ylim(min(codes) - 0.35 * rng, max(codes) + 0.44 * rng)
        ax.set_yticks([256, 257, 258] if name == "B" else [448, 480, 512])
        ax.grid(axis="y", lw=0.4, color="#d6dfe1")
        ax.set_title(f"({'ab'[j]})  {title}", loc="left", weight="bold", fontsize=9)
        ax.text(
            1,
            1.045,
            f"U = {data['upper']}",
            transform=ax.transAxes,
            ha="right",
            fontsize=7.2,
            color="#556975",
        )
    save(fig, out, "sar-events")


def effect_language():
    """返回 Matplotlib 字体族与标签选择器。

    嵌入式 PDF 需要可用的中文轮廓，默认的 DejaVu 缺少这些字形。
    缺少中日韩字体时改用英文标签，两个语言版本共享数字、图例和坐标。
    """
    installed = {font.name for font in font_manager.fontManager.ttflist}
    # SVG 保留可编辑文字，PDF 内嵌字形，PNG 固化预览。
    # Type 42 使用 TrueType 轮廓，部分 PDF 阅读器会拒绝按此类型封装的 CFF 字库。
    # 字母与数字用 DejaVu，汉字按顺序回退到可嵌入的本机字族。
    for face in (
        "Songti SC",
        "Microsoft YaHei",
        "Heiti SC",
        "WenQuanYi Zen Hei",
        "Arial Unicode MS",
    ):
        if face in installed:
            return ["DejaVu Sans", face], lambda zh, en: zh
    return ["DejaVu Sans"], lambda zh, en: en


def draw_switches(result, out, t):
    """用相同坐标和计数色标画前后对照。每格是等权位置跨 64 笔的使用次数。

    每行代表一个槽，每列代表一对同时开关的主半单元。颜色用绝对次数标定，
    右图是一个中间计数的均匀分布。若用单幅自动缩放，均匀分布会显示成端点色。
    """
    fig = plt.figure(figsize=(7.16, 4.28))
    muted = "#4b6268"
    low, mid, high = "#edf1ef", "#73b1a8", "#193f50"
    n = result["phases"]
    k = result["command"]
    selected = result["selected_per_command"]
    colors = LinearSegmentedColormap.from_list(
        "count", [(0, low), (selected / n, mid), (1, high)], N=257
    )
    fig.text(
        0.06,
        0.926,
        t(
            "同一条开关命令，轮换相位后谁被选中？",
            "Which units carry the same command after phase rotation?",
        ),
        size=15,
        weight="semibold",
        color=INK,
    )
    fig.text(
        0.06,
        0.854,
        t(
            f"命令 k = {k}   ·   每笔选 {selected} 个等权位置   ·   桥交换关闭",
            f"Command k = {k}   ·   {selected} equal-weight positions per conversion   ·   bridge off",
        ),
        size=9.4,
        color=muted,
    )
    # 输入相位对只决定位置。两幅图用同一归一化，右图的中间色不能被自动拉满。
    for group, (left, field, label) in enumerate(
        (
            (0.135, "fixed_counts", t("固定相位", "Fixed phase")),
            (0.587, "rotated_counts", t("遍历横纵相位", "All phase pairs")),
        )
    ):
        counts = np.asarray(result[field])
        ax = fig.add_axes((left, 0.275, 0.292, 0.49))
        ax.imshow(counts, origin="upper", vmin=0, vmax=n, cmap=colors, interpolation="nearest")
        ax.set_xticks(range(8))
        ax.set_yticks(range(8))
        ax.tick_params(length=0, pad=2, labelsize=7.5, colors=muted)
        ax.set_xticks(np.arange(-0.5, 8, 1), minor=True)
        ax.set_yticks(np.arange(-0.5, 8, 1), minor=True)
        ax.tick_params(which="minor", length=0)
        ax.grid(which="minor", color="white", lw=0.65)
        for spine in ax.spines.values():
            spine.set_visible(False)
        ax.set_xlabel(t("等权列", "Equal-weight column"), labelpad=2, color=muted)
        ax.set_ylabel(t("槽位", "Slot"), labelpad=3, color=muted)
        ax.set_title(
            ("(a)   ", "(b)   ")[group] + label,
            loc="left",
            color=INK,
            fontsize=10.5,
            weight="semibold",
            pad=6,
        )
        # 只在代表格上印数字，离散刻度仍可供灰度印刷读取全部单元。
        for value in np.unique(counts):
            row, col = np.argwhere(counts == value)[0]
            ax.text(
                col,
                row,
                str(value),
                ha="center",
                va="center",
                size=7.5,
                weight="semibold",
                color="white" if value == n else INK,
            )
        used = np.count_nonzero(counts)
        subtitle = (
            t(f"{used} / 64 个位置被用到", f"{used} / 64 positions used")
            if not group
            else t(f"64 个位置各用 {selected} 次", f"All 64 used {selected} times each")
        )
        fig.text(left + 0.146, 0.139, subtitle, ha="center", size=11, weight="semibold", color=INK)
    handles = [
        Patch(facecolor=c, edgecolor="#98a9ab", lw=0.5, label=t(f"{v} 次", f"{v}"))
        for c, v in ((low, 0), (mid, selected), (high, n))
    ]
    fig.legend(
        handles=handles,
        title=t("每格为 64 次命令中的选中次数", "Selections per position over 64 commands"),
        loc="lower center",
        bbox_to_anchor=(0.5, 0.018),
        ncol=3,
        frameon=False,
        fontsize=8,
        title_fontsize=8,
        columnspacing=2.0,
        handlelength=1.25,
    )
    save(fig, out, "physical-masks")


def draw_handover(out):
    """将 owner_valid_q 的两个实际状态与同沿旧 raw / 新 capture 画在同一张图。

    上半图读寄存器状态，下半图给出三个上升沿取样值。返回标签来自沿前的 owner。
    上电复位释放后开始例子，略去两次事务相同的相位和权重字段。
    """
    from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

    ink, accent, muted = "#20384a", "#087b83", "#52636d"
    fig = plt.figure(figsize=(7.16, 3.82))
    top = fig.add_axes((0.055, 0.59, 0.90, 0.34))
    bottom = fig.add_axes((0.055, 0.075, 0.90, 0.445))
    for ax, h in ((top, 2.2), (bottom, 4.1)):
        ax.set_xlim(0, 10)
        ax.set_ylim(0, h)
        ax.axis("off")

    def arrow(ax, start, end, curve=0, color=ink):
        ax.add_patch(
            FancyArrowPatch(
                start,
                end,
                arrowstyle="-|>",
                mutation_scale=8,
                lw=0.95,
                color=color,
                connectionstyle=f"arc3,rad={curve}",
                shrinkA=0,
                shrinkB=0,
            )
        )

    def state(x, name, flag):
        top.add_patch(
            FancyBboxPatch(
                (x, 0.72),
                1.72,
                0.64,
                boxstyle="round,pad=.06,rounding_size=.11",
                lw=1,
                edgecolor=ink,
                facecolor="#edf4f5",
            )
        )
        top.text(
            x + 0.86, 1.09, name, ha="center", va="center", color=ink, weight="bold", fontsize=9
        )
        top.text(x + 0.86, 0.84, flag, ha="center", va="center", color=muted, fontsize=7)

    top.text(0, 2.07, "(a)  Capture ownership", color=ink, weight="bold", fontsize=9)
    state(1.20, "Free", "valid = 0")
    state(6.58, "Owned", "valid = 1")
    arrow(top, (0.24, 1.04), (1.10, 1.04))
    top.text(0.61, 1.2, "Reset", ha="center", color=muted, fontsize=7)
    arrow(top, (3.0, 1.21), (6.48, 1.21), -0.14)
    top.text(4.73, 1.58, "Capture A", ha="center", color=ink, fontsize=8)
    arrow(top, (6.48, 0.83), (3.0, 0.83), -0.16)
    top.text(4.73, 0.24, "Return A, no new capture", ha="center", color=ink, fontsize=7.5)
    # 同状态的换主操作用一个右侧回路表示。文字符号放在环外，避免穿过箭头。
    arrow(top, (8.38, 0.90), (8.38, 1.21), 1.9, accent)
    top.text(8.66, 1.48, "Return A +", color=accent, fontsize=7.3)
    top.text(8.66, 1.23, "capture B", color=accent, fontsize=7.3)

    bottom.text(0, 3.89, "(b)  Same-edge handover", color=ink, weight="bold", fontsize=9)
    centers = [4.06, 6.55, 9.04]
    middle = centers[1]
    bottom.add_patch(plt.Rectangle((middle - 1.13, 0.17), 2.26, 3.22, color="#e7f3f2", ec="none"))
    for j, cx in enumerate(centers):
        bottom.text(
            cx,
            3.47,
            f"Edge {j+1}",
            ha="center",
            color=accent if j == 1 else ink,
            fontsize=8,
            weight="bold" if j == 1 else "normal",
        )
    rows = [
        ("Owner before edge", ("Free", "A", "B")),
        ("Incoming raw", ("—", "A", "B")),
        ("New capture", ("A", "B", "—")),
        ("Response tag", ("—", "A", "B")),
        ("Owner after edge", ("A", "B", "Free")),
    ]
    for i, (label, values) in enumerate(rows):
        y = 2.94 - 0.59 * i
        bottom.text(0.05, y, label, va="center", fontsize=7.8, color=ink)
        for j, (cx, v) in enumerate(zip(centers, values)):
            bottom.text(
                cx,
                y,
                v,
                ha="center",
                va="center",
                fontsize=8.3,
                weight="bold" if j == 1 and i in (3, 4) else "normal",
                color=accent if j == 1 and i in (3, 4) else muted,
            )
    bottom.plot([0, 9.98], [3.23, 3.23], color="#81939d", lw=0.6)
    bottom.plot([0, 9.98], [1.43, 1.43], color="#a9b9be", lw=0.55)
    bottom.plot([0, 9.98], [0.24, 0.24], color="#81939d", lw=0.6)
    save(fig, out, "capture-handover")


def draw_arithmetic(result, out, t):
    """同一行使用完全相同的原码，两种符号区分默认表与写入已知系数的表。

    两侧都选与两侧都不选是两个不同的零响应对照，不能合并为一次成功。
    正负号取实算输出减公共参考；raw 列保留 13 位整数，避免把两种码宽混在一起。
    """
    fig = plt.figure(figsize=(7.16, 3.90))
    muted, off, on = "#4b6268", "#9c542e", "#147878"
    reference = result["nominal_code"]
    fig.text(
        0.06,
        0.923,
        t(
            "单个权重有偏差，数字输出能修正几码？",
            "How much output error can one known weight correct?",
        ),
        size=15,
        weight="semibold",
        color=INK,
    )
    fig.text(
        0.06,
        0.842,
        t(
            f"无偏差时的参考输出 {reference:,} 码   ·   物理片 0   ·   每次只改变一个子权重",
            f"Reference output {reference:,} codes   ·   physical slice 0   ·   one subunit changed per run",
        ),
        size=9.1,
        color=muted,
    )
    left = fig.add_axes((0.060, 0.258, 0.467, 0.430))
    ax = fig.add_axes((0.580, 0.258, 0.347, 0.430))
    # 开关和 raw 的行与右图共用相同 y 坐标，避免需要箭头和额外图例来对号。
    for panel in (left, ax):
        panel.set_ylim(3.5, -0.5)
        for spine in panel.spines.values():
            spine.set_visible(False)
        panel.set_yticks([])
    left.set_xlim(0, 1)
    left.axis("off")
    columns = (0.10, 0.35, 0.55, 0.86)
    headers = (t("子单元", "Unit"), t("采样", "Pre"), t("转换", "Post"), t("ADC2 原码", "ADC2 raw"))
    for pos, label in zip(columns, headers):
        left.text(
            pos,
            1.055,
            label,
            transform=left.transAxes,
            ha="center",
            color=muted,
            fontsize=8.5,
            weight="semibold",
        )
    ax.set_title(
        t("20 位输出相对参考的误差（码）", "20-bit output error (codes)"),
        color=muted,
        fontsize=8.5,
        weight="semibold",
        pad=8,
    )
    ax.set_xlim(-42, 42)
    ax.set_xticks([-32, 0, 32], ["−32", "0", "+32"])
    ax.tick_params(axis="x", length=0, pad=4, labelsize=9, colors=INK)
    for pos in (-32, 0, 32):
        ax.axvline(
            pos, color="#b1bfc1" if pos == 0 else "#e0e6e6", lw=0.9 if pos == 0 else 0.6, zorder=0
        )
    for i, row in enumerate(result["rows"]):
        for pos, field in zip(columns, ("unit", "pre", "post", "raw_after_injection")):
            left.text(
                pos,
                i,
                str(row[field]),
                ha="center",
                va="center",
                fontsize=10,
                color=INK,
                weight="semibold" if field == "raw_after_injection" else "normal",
            )
        d, corrected = row["nominal_error"], row["corrected_error"]
        if d != corrected:
            ax.annotate(
                "",
                (corrected, i),
                (d, i),
                arrowprops=dict(
                    arrowstyle="-|>",
                    lw=1.25,
                    color="#7b9092",
                    shrinkA=6,
                    shrinkB=6,
                    mutation_scale=8,
                ),
            )
        # 零差异时两个标记同心。空心圆和实心菱形在黑白印刷中也可分辨。
        ax.scatter(
            d, i, s=104, marker="o", facecolors="white", edgecolors=off, linewidths=1.5, zorder=3
        )
        ax.scatter(
            corrected, i, s=30, marker="D", facecolors=on, edgecolors=on, linewidths=0.5, zorder=4
        )
    for y in (-0.5, 0.5, 1.5, 2.5, 3.5):
        left.axhline(y, color="#d8e1e1", lw=0.55)
        ax.axhline(y, color="#d8e1e1", lw=0.55, zorder=0)
    handles = [
        Line2D(
            [],
            [],
            ls="none",
            marker="o",
            ms=7,
            mfc="white",
            mec=off,
            mew=1.3,
            label=t("默认权重", "Nominal weights"),
        ),
        Line2D(
            [],
            [],
            ls="none",
            marker="D",
            ms=5,
            mfc=on,
            mec=on,
            label=t("写入已知权重", "Known weight applied"),
        ),
    ]
    fig.legend(
        handles=handles,
        loc="lower center",
        bbox_to_anchor=(0.5, 0.113),
        ncol=2,
        frameon=False,
        fontsize=9,
        handlelength=1.15,
        columnspacing=2.5,
    )
    fig.text(
        0.5,
        0.055,
        t(
            "数字注入   ·   子权重增量 2⁻¹⁰   ·   每行的两种配置接收同一原码",
            "Digital injection   ·   weight step 2⁻¹⁰   ·   both configurations use the same raw input in each row",
        ),
        ha="center",
        size=8.2,
        color=muted,
    )
    save(fig, out, "integer-arithmetic")


# =============================================================================
# 4. 复核与发布。证据清单在数值断言及全部图片导出成功后才生成。
# =============================================================================
def main():
    """检查成功后才保存证据摘要。

    所有数字结果均可从同一套输入重新生成。清单保存生产 RTL 及
    用于复算的参考和向量身份；源文件存在本身不代表完成了 RTL 运行。
    输出目录采用独占创建，使失败或并发的尝试不会覆盖已发布的图。
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True, help="新目录，保存数字证据与图")
    out = parser.parse_args().out
    out.mkdir(parents=True, exist_ok=False)
    # 四类检查独立完成。任何一个有反例都不登记整组成功。
    # 真正的硬件编译或综合不能通过改变本脚本的统计口径来代替。
    results = dict(
        scheduler=scheduling(), events=sar_cases(), switches=masks_and_pre(), integer=arithmetic()
    )
    coverage, captured_post, captured_pre = switch_coverage()
    # 不从绘图用的累计矩阵重建一次开关。D5 取固定相位那一行完整的 160 个 post 位，
    # 同时取与它具有相同纵向相位的八个 4 位采样字，保留子支路的实际开关方向。
    results["phase_coverage"] = coverage
    results["weight_response"] = weight_effect(captured_post, captured_pre)
    with plt.rc_context(STYLE):
        draw_schedule(results["scheduler"], out)
        draw_events(results["events"], out)
        draw_handover(out)
        fonts, label = effect_language()
        with plt.rc_context({"font.family": fonts}):
            draw_switches(coverage, out, label)
            draw_arithmetic(results["weight_response"], out, label)
    # 绑定整核的真实 RTL 输入以定位图对应的版本，不从报告文件名推断版本。
    production = ROOT / "flow/build/filelists/files_adc_v21.f"
    sources = [line for line in production.read_text().splitlines() if line.startswith("rtl/")]
    files = [
        *sources,
        "flow/build/filelists/files_adc_v21.f",
        "tools/verify_v21_algorithms.py",
        "tools/contract_v2.py",
        "tools/cmp_dem_dither.py",
        "tools/recon_oracle.py",
        # 事务模型和独立顶层对照分别登记；数值图的重新计算本身不运行 HDL。
        "src/adi_model/defaults.py",
        "src/adi_model/digital_conversion.py",
        "tools/check_default_rtl.py",
        "src/adi_model/control/dither_word.py",
        "tb/v21/sample_cadence.cpp",
        "src/adi_model/dem_dither.py",
        "src/adi_model/sched18_rtl.py",
        "src/adi_model/recip_q60.py",
        "tb/vec_r18_sched.txt",
        "tb/vec_r18_recip.txt",
        "tb/vec_dem_dither_effects.txt",
        "tb/tb_dem_dither.sv",
        "tb/v21/vec_recip_rnd.txt",
        "tb/v21/vec_i4_recon.txt",
        "tb/v21/tb_i2_events.sv",
    ]
    # 叶子全日志的哈希在数值节选头内。这里绑定节选原文和读取脚本，
    # 使离开原始仿真机器的读者也能从这 65 行复算两幅图。
    # 整数参考的代码身份和 RTL 的文件身份同时保存，两者的执行证据各不相同。
    with (out / "digital-evidence.json").open("w", encoding="utf-8") as f:
        json.dump(
            dict(
                scope="v21 digital IP, archived D3/D4 leaf outputs, frozen vectors and integer reference",
                results=results,
                source_sha256={
                    n: hashlib.sha256((ROOT / n).read_bytes()).hexdigest() for n in files
                },
                figures={
                    p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in sorted(out.iterdir())
                    if p.suffix in (".svg", ".png", ".pdf")
                },
            ),
            f,
            indent=2,
            allow_nan=False,
        )
        f.write("\n")
    print(
        "D1",
        results["scheduler"]["matched_rows"],
        "rows; D3",
        results["switches"]["post_checks"],
        "masks; D4",
        results["switches"]["pre_checks"],
        "presets; D5",
        results["integer"]["reconstruction"],
        "vectors",
    )


if __name__ == "__main__":
    main()
