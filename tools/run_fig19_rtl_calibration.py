#!/usr/bin/env python3
# =============================================================================
# 文件名     : run_fig19_rtl_calibration.py
# 版本       : v1.0
# 功能       : 运行独立 Fig.19 静态组件，核对物理轨迹和整数输出。
# 设计说明   :
#   * 该试验使用显式 63+8 和 12 位码制。模型默认值或数字主线的规格改动
#     不会替换已登记的夹具参数。实际物理电容和名义整数权重分别追踪。
#   * 静态模式只跑明确给出的字序列。训练与留出分别执行，实际制造数据
#     只供物理参考，不能传给拟合器生成自己会通过的答案。
#   * 每个工具从本次目录运行，源、输入包和轨迹的 SHA-256 随工件存档。
#     参数不合法、工具失败、协议检查失败均保留失败状态和输入身份。
# 变更记录   : 恢复使用固定 Fig.19 夹具的名义捕获，并与组件编译规则对齐。
# =============================================================================
"""运行显式Fig19理想双节点的真实RTL、静态训练及同raw留出。

制造数据只供独立plant；训练器仅接收真实DUT packet与已知输入。该夹具不证明
原芯片注入电容、模拟保持/建立、DEM、噪声、动态校准或目标工艺时序。
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True

import numpy as np  # noqa: E402

from adi_model.calibration.weight_calibration import fit_unit_weights  # noqa: E402
from adi_model.contracts.fixed_point import FixedPointReconstructor  # noqa: E402
from adi_model.defaults import default_model  # noqa: E402
from adi_model.rtl_observation import observations_from_rtl  # noqa: E402
from adi_model.simulation.config import Config  # noqa: E402
from adi_model.topology.dac_arch import SplitChip  # noqa: E402
from export_fitted_rtl import build_fitted_registers  # noqa: E402
from export_rtl_params import build  # noqa: E402
from run_open_rtl import (  # noqa: E402
    CORE_SOURCES,
    REPO,
    alias_runtime,
    checked_run,
    command_for,
    completion,
    digest,
    new_output,
    validate_inputs,
    verilator_compiler_args,
    write_json,
)


# =============================================================================
# 1. 数据判据与独立参考
# =============================================================================
# 固定 Fig.19 的参考以已登记码域为准。修改默认模型不能改变这个夹具的输出解释。
# 比较使用严格的独立期望，不允许被查文件定义自己的 PASS。
# 拒绝非法引用、模拟域错误和输出裁剪采用不同字段，不能以同一个等值码相互抵消。
# 所有调用从实测或可重算的输入形成期望，出了错误要把原因传回入口。
def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def dither_terms(word: int) -> tuple[int, int, int]:
    """按独立源合同逐位求整数 L/T/U，不调用 SV 译码或实际电容。

    ⚠️ **本函数里的权重是 63+8 时代的显式合同值**（`step_q = 2**24`，
    低位权 9586981/4793490/2396745），与 `flow/sim/fixtures/fig19_ideal_ports/`
    那份遗留 fixture 配套。主链已切到 17/5（`FIG19_STEP_Q = 16810048`），
    但**这里不能跟着改**：
      * 它服务于对该 fixture 的 RTL 重放，fixture 本身就是按 63/8 构造的；
      * 改了会让"合同值"与"fixture 实际数据"不一致，反而产生假通过/假失败。

    要用当前模型跑这条路径，必须先把 fixture 整套重建
    （见 `flow/sim/fixtures/fig19_ideal_ports/README.md` 的三步说明）。
    """
    require(type(word) is int and 0 <= word < 64, "six-bit Fig19 word required")
    upper = 4 * ((word >> 5) & 1) + ((word >> 3) & 1)
    lower = 33554432 * ((word >> 4) & 1)
    lower += 9586981 * ((word >> 2) & 1)
    lower += 4793490 * ((word >> 1) & 1) + 2396745 * (word & 1)
    return lower, lower + upper * 16777216, upper


# =============================================================================
# 2. 轨迹解析
# =============================================================================
# CSV 必须来自同一次运行。控制、物理引脚、残差码和最终出码各有独立的记录顺序。
# 不要先按标签重排再检查样本归属，否则一些串笔或重复可以被排序掩盖。
# 保留完整尾段直到验证物理捕获计数。训练只在切出有效内段后继续。
# 此处读取证据不回写。如果轨迹不足，调用方报告这一次没有形成有效产物。
def rows(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        data = list(reader)
    require(
        bool(data) and all(None not in row and None not in row.values() for row in data),
        f"empty or malformed trace: {path}",
    )
    return data


# =============================================================================
# 3. 片序和绑定
# =============================================================================
# 物理片身份同时用于读权重和定位导线。槽位置相同不代表物理片相同。
# 除集合交叠外，还要按槽位检查片号顺序，避免两个相同权重的片把错误隐藏。
# 采样时的已知电压由声明的绑定提供，不以制造真值或本次输出做校正输入。
# 在初始采集位与进行中的转换位之间，标签和片号都必须随记录一起传递。
def unpack_ids(value: str) -> list[int]:
    packed = int(value, 16)
    require(0 <= packed < 1 << 40, "ordered physical IDs exceed eight5bit slots")
    ids = [(packed >> (5 * slot)) & 31 for slot in range(8)]
    require(len(set(ids)) == 8 and all(i < 18 for i in ids), "invalid physical IDs")
    return ids


def binding_features(spec, controls):
    """只读数字word/actual masks及名义参考；不接收制造值或系数。"""
    words = controls["control_word"]
    total_q = np.asarray([dither_terms(int(word))[1] for word in words], dtype=np.int64)
    require(
        np.array_equal(controls["known_common_q"], -total_q),
        "actual frozen common term differs from observed word Fig19 T",
    )
    masks = np.concatenate((controls["main_masks"], controls["sub_masks"]), axis=2)
    known = spec.v_fs * (-total_q[:, None, None] / 2**32 + 1 - 2 * masks)
    return np.ones_like(masks, dtype=np.int8), known


# =============================================================================
# 4. 整数参考
# =============================================================================
# 独立计算使用显式二进制尺度、符号与取整。浮点近似不能代替边界检查。
# 本层读取冻结寄存器，不从实时模拟状态重新估算系数。
# 实物等效电容仅供模拟余差参考。软件定点输出使用同次实际装载的整数表。
# 最终输出含码、裁剪和错误状态，数值相等仍需点对点核查其它标志。
def round_even(numerator: int, denominator: int) -> int:
    quotient, remainder = divmod(numerator, denominator)
    return quotient + int(
        2 * remainder > denominator or (2 * remainder == denominator and quotient % 2)
    )


def scalar_word(image: FixedPointReconstructor, obs, index: int, total_q: int) -> tuple[int, int]:
    """逐unit任意精度整数参考，不使用DUT归约树或生产重构中间量。"""
    fmt = image.format
    fine = image.adc2_min_q + round_even(
        (2 * int(obs.adc2_code[index]) + 1) * (image.adc2_max_q - image.adc2_min_q),
        1 << (image.spec.adc2_n_bits + 1),
    )
    gain, known = 0, 0
    masks = np.concatenate((obs.main_masks[index], obs.sub_masks[index]), axis=1)
    for slot, physical in enumerate(obs.slice_ids[index]):
        for unit, on in enumerate(masks[slot]):
            weight = int(image.weights_q[int(physical), unit])
            gain += weight
            known += weight * (-total_q + (1 - 2 * int(on)) * (1 << fmt.voltage_fraction_bits))
    numerator = (fine - image.offset_q) * (1 << fmt.weight_fraction_bits) - known
    shifted = (numerator + gain * (1 << fmt.voltage_fraction_bits)) * (1 << fmt.output_bits)
    quotient = shifted // (2 * gain * (1 << fmt.voltage_fraction_bits))
    flags = (8 if quotient < 0 else 0) | (16 if quotient >= 1 << fmt.output_bits else 0)
    return min(max(quotient, 0), (1 << fmt.output_bits) - 1), flags


# =============================================================================
# Fig.19 静态夹具的固定名义电容
# =============================================================================
def fixture_nominal_plant(fixture: Path, nominal: FixedPointReconstructor) -> dict:
    """从绑定夹具的公开名义值建立均匀电容阵列，电容单位为 F。

    仅用于 component-only 捕获。真实制造数据仍从显式 plant 输入，
    或使用与名义装载映像严格一致的同一模型产生。
    """
    manifest = json.loads((fixture / "fixture_manifest.json").read_text(encoding="utf-8"))
    require(
        manifest["scope"].startswith("SOURCE_FAMILY_FIG19_IDEAL_PORTS"),
        "component-only requires declared Fig19 fixture capacitances",
    )
    parameters = manifest["parameter_inference"]
    count = nominal.spec.n_slices
    cu = float(parameters["unit_cap_f"]["value"])
    bridge = float(parameters["bridge_cap_f"]["value"])
    cf = float(parameters["feedback_cap_f"]["value"])
    # 主／子物理电容都是名义 Cu。有效权重里的 beta 在 SplitChip 侧按桥比计算一次。
    # 固定夹具没有寄生。不能随当前默认 17/5 几何或 13 位 ADC2 一起迁移。
    require(
        all(float(parameters[key]["value"]) == 0.0 for key in (
            "main_parasitic_f", "sub_parasitic_f",
            "unit_mismatch_std", "bridge_mismatch_std", "feedback_mismatch_std",
        )),
        "default Fig19 component plant requires an explicit homogeneous, parasitic-free fixture",
    )
    return {
        "C_main": np.full((count, nominal.spec.dac_n_main), cu).tolist(),
        "C_sub": np.full((count, nominal.spec.dac_n_sub), cu).tolist(),
        "C_bridge": np.full(count, bridge).tolist(),
        "c_p_main": np.zeros(count).tolist(),
        "c_p_sub": np.zeros(count).tolist(),
        "C_feedback_true": cf,
    }


# =============================================================================
# 6. 本次捕获的交叉核查
# =============================================================================
# 先确定夹具声明的宽度，再分别核对采样记录的身份、数量与字段。
# 只有已接受的合法前缀能使模拟引脚变化。被拒绝的事件要保留旧片号和开关状态。
# 采样端保持量、参考贡献和转换命令在同一笔上重算，避免只验证最终数字码。
# 整条录制的每一类记录有自身边界。尾部补拍属于证据的一部分，不是训练输出。
# 用独立物理记录解释捕获 packet，而不是根据观测矩阵反推出真实引脚。
def verify_trace(
    folder: Path,
    inputs: Path,
    register_path: Path,
    stimulus: list[tuple],
    plant_weights: np.ndarray,
    config: Config,
    *,
    capture_samples: int,
    plan_output: Path | None = None,
) -> tuple[dict, object, np.ndarray]:
    """核完整合法程序和物理尾部；仅前 N 目标参与学习或退休比较。"""
    image = FixedPointReconstructor.from_dict(json.loads(register_path.read_text()))
    spec = image.spec
    # 这条对拍读取独立 Fig.19 的固定前缀表和 12 位 ADC2 输出。
    # 图内的位宽由装载映像确认；与当前默认模型的 ADC2 设置相互独立。
    n_main_bits, n_sub_bits = spec.dac_n_main, spec.dac_n_sub
    require(
        (spec.n_slices, spec.n_active, n_main_bits, n_sub_bits, spec.adc2_n_bits)
        == (18, 8, 63, 8, 12)
        and plant_weights.shape == spec.shape,
        "Fig19 trace requires matching 63+8 masks and a 12-bit ADC2 image",
    )
    # 检查器读取执行目录里这次保存的原件。各 CSV 的作用不可互换。
    # 控制序列验证本次程序何时受理。采样和余差反映物理层推进，出码验证退休边界。
    # 出错时仍保留这些记录和原输入。任何单一 CSV 均不足以声称完整转换已经验证。
    controls = rows(folder / "controls.csv")
    samples = rows(folder / "samples.csv")
    residues = rows(folder / "residues.csv")
    events = rows(folder / "rdac_events.csv")
    results = rows(folder / "events.csv")
    n = capture_samples
    require(
        type(n) is int
        and 1 <= n <= 8192
        and len(stimulus) == n + 3
        and stimulus[n:] == [(0, 0.0, 0)] * 3,
        "finite capture requires N target records and three explicit legal postamble records",
    )
    # N 是目标比较笔数。固定后缀让前 N 笔完整出码。
    # 后缀中的部分样本已经混入物理捕获。先用实际各表长度验证这一事实，
    # 随后只把目标域的合格记录交给模型重放和学习。
    physical_count = n + 2
    require(
        len(controls) == len(stimulus)
        and len(samples) == len(residues) == physical_count
        and len(events) == 7 * physical_count,
        "actual source/sample/residue/event coverage differs from the finite program",
    )
    # 该夹具每次从复位运行，最初可观察样本编号按受理语义从 2 起。
    # 连续编号不仅给数量，也精确指定每张表中哪些记录能够配对。
    # 如果某张表编号乱序，不能取两表交集后悄悄删除未匹配记录。
    expected_ids = list(range(2, physical_count + 2))
    require(
        [int(row["sample_id"]) for row in samples] == expected_ids
        and [int(row["sample_id"]) for row in residues] == expected_ids,
        "sample/residue identity missing or reordered",
    )
    require(
        all(row["epoch"] == "0" and row["kind"] in ("OBS", "OUT") for row in results),
        "only epoch0 OBS/OUT records are legal in this reset-bounded capture",
    )
    # 正常场景要求转换无协议错误。负例只允许事先设计的前缀绑定拒绝。
    # 模拟非法、捕获不足和绑定拒绝是三个不同现象，需要独立指出原因。
    positive_capture = not any(row[2] for row in stimulus[:n])
    for row in results:
        status = int(row["status_word"], 16)
        fault = int(row["conversion_fault"])
        require(int(row["analog_sticky"]) == 0, "capture has a sticky analog error")
        if positive_capture:
            require(status == fault == 0, "positive capture has a protocol/conversion fault")
        else:
            require(
                status in (0, 13 << 6) and fault == int(status != 0),
                "early-prefix negative has an unexpected error rather than source binding13",
            )
    # 每笔从同一个程序条目取得 word、输入电压和两路偏置。
    # 控制寄存器的选择和真值预设都验证来源，不能只查看命令是否发过。
    control_ids = []
    for index, (control, (word, reference, _)) in enumerate(zip(controls, stimulus, strict=True)):
        lower_q, total_q, _ = dither_terms(word)
        require(
            (
                int(control["sample_id"]),
                int(control["word"]),
                float(control["reference_v"]),
                int(control["actual_control_word"]),
                int(control["control_valid"]),
                int(control["sadc_shift_q"]),
                int(control["rdac_shift_q"]),
            )
            == (index + 2, word, reference, word, 1, -lower_q, -total_q),
            "actual source handshake/registered two-node control differs from its program owner",
        )
        control_ids.append(unpack_ids(control["acquiring_ids"]))
    # 到这里控制记录的顺序已独立校验，映射只用于后续按真实编号对齐。
    # 逐事件仍沿记录文件顺序前进，以保留拒绝前一拍的引脚和状态。
    by_id = {int(row["sample_id"]): row for row in residues}
    plans = []
    early_invalid = []
    physical_compares = 0
    previous_pins = (0, 0, 0)
    for index, (sample, residue, (word, reference, rejected)) in enumerate(
        zip(samples, residues, stimulus[:physical_count], strict=True)
    ):
        sid = index + 2
        lower_q, total_q, upper = dither_terms(word)
        require(
            int(sample["word"]) == int(residue["word"]) == word
            and float(sample["reference_v"]) == float(residue["reference_v"]) == reference,
            "accepted word/reference differs from source stimulus",
        )
        require(
            (int(sample["sadc_shift_q"]), int(sample["rdac_shift_q"])) == (-lower_q, -total_q),
            "actual acquisition ports do not implement the source two-node contract",
        )
        # SADC 使用 lower，RDAC 采样使用 total。两路电压均从同一次 stimulus 重算。
        # 保持值来自记录的原节点，不用最终粗码倒推初始输入，以免把错误状态合法化。
        sadc_v, rdac_v = (
            reference - lower_q / 2**32 * config.v_fs,
            reference - total_q / 2**32 * config.v_fs,
        )
        require(
            abs(float(sample["sadc_sample_v"]) - sadc_v) <= 1e-13
            and abs(float(sample["rdac_sample_v"]) - rdac_v) <= 1e-13,
            "lower/total did not enter the two actual held inputs",
        )
        # 正在转换的片组必须是该笔采样的同一个物理组。
        # 下一采集组不能与当前转换组占相同片。两种片组角色同时记录才可判断独占状态。
        ids = unpack_ids(residue["converting_ids"])
        acq = unpack_ids(residue["next_acquiring_ids"])
        require(
            ids == unpack_ids(sample["acquiring_ids"])
            and ids == control_ids[index]
            and acq == control_ids[index + 1]
            and not set(ids) & set(acq),
            "actual conversion did not own preceding acquisition",
        )
        require(
            int(sample["acquiring_mask"], 16) == sum(1 << i for i in ids),
            "ordered IDs differ from actual acquisition mask",
        )
        if plans:
            require(ids == plans[-1]["acq"], "conv[n+1] differs from actual acq[n]")
        plans.append(
            {
                "sample_id": sid,
                "conv": ids,
                "acq": acq,
                "word": word,
                "reference_v": reference,
                "actual_reference_high_v": config.v_fs,
                "actual_reference_low_v": -config.v_fs,
                "nominal_reference_high_v": config.v_fs,
                "nominal_reference_low_v": -config.v_fs,
            }
        )
        coarse = min(max(math.floor((sadc_v + config.v_fs) * 512 / (2 * config.v_fs)), 0), 511)
        require(
            int(residue["coarse_final"]) == coarse and int(residue["rdac_final"]) == coarse - upper,
            "actual final c/k differs from sampled SADC and source upper code",
        )
        # 每组七笔是本夹具的显式前缀事件。尚未判出的低位在事件计算时保持未用。
        # 对负例的合法性检查仍基于当前前缀，直到终码同时合法也不能省略早期故障。
        group = events[7 * index : 7 * index + 7]
        invalid_prefix = False
        for j, event in enumerate(group):
            prefix = coarse & ~((1 << (6 - j)) - 1)
            k = prefix - upper
            accepted = 0 <= k <= 511
            require(
                (
                    int(event["sample_id"]),
                    int(event["event_index"]),
                    int(event["phase"]),
                    int(event["coarse_prefix"]),
                    int(event["rdac_code"]),
                    int(event["word"]),
                )
                == (sid, j, j + 2, prefix, k, word),
                "actual prefix sequence or owner differs",
            )
            require(
                int(event["common_q"]) == -total_q
                and (int(event["accepted"]), int(event["bound_valid"]), int(event["bound_error"]))
                == (int(accepted), int(accepted), 0 if accepted else 13),
                "prefix overflow was clamped/omitted or lost its binding error",
            )
            pins = tuple(
                int(event[name], 16) for name in ("selected_mask", "main_physical", "sub_physical")
            )
            if accepted:
                main = sum(
                    int(unit < k // 8) << (physical * 63 + unit)
                    for physical in ids
                    for unit in range(63)
                )
                sub = sum(
                    int(unit < k % 8) << (physical * 8 + unit)
                    for physical in ids
                    for unit in range(8)
                )
                require(
                    pins == (sum(1 << p for p in ids), main, sub),
                    "accepted event differs from real registered physical pins",
                )
            else:
                require(pins == previous_pins, "invalid prefix drove new pins instead of holding")
                invalid_prefix = True
            previous_pins = pins
            physical_compares += 1
        require(
            invalid_prefix == bool(rejected),
            "explicit rejected-sample policy differs from all7events",
        )
        require(
            tuple(
                int(residue[name], 16)
                for name in ("selected_mask", "main_physical", "sub_physical")
            )
            == previous_pins,
            "actual residue pins differ from the last registered prefix event",
        )
        if invalid_prefix:
            require(
                coarse - upper >= 0,
                "negative control must isolate early-prefix from final legality",
            )
            early_invalid.append(sid)
        # 只对已选物理片读取寄存的实际主／子引脚。偏移以物理片为单位进行打包。
        # 这一步以实际电容复算余差，其值不传给拟合器作为数字参考。
        # 即便终码吻合，也要验证本笔独立余差和 ADC2 量化均处在声明范围。
        f = 0.0
        for physical in range(18):
            if physical not in ids:
                continue
            for unit in range(n_main_bits + n_sub_bits):
                packed = int(
                    residue["main_physical"] if unit < n_main_bits else residue["sub_physical"], 16
                )
                bit = (
                    packed
                    >> (
                        physical
                        * (n_main_bits if unit < n_main_bits else n_sub_bits)
                        + (unit if unit < n_main_bits else unit - n_main_bits)
                    )
                ) & 1
                f += float(plant_weights[physical, unit]) * (rdac_v + config.v_fs * (1 - 2 * bit))
        require(
            abs(f - float(residue["ra_v"])) <= 2e-12,
            "independent physical charge operator differs from real held-input/pin plant",
        )
        raw = min(
            max(
                math.floor(
                    (f - config.adc2_v_min) * 4096 / (config.adc2_v_max - config.adc2_v_min)
                ),
                0,
            ),
            4095,
        )
        require(
            int(residue["adc2_code"]) == raw and int(residue["analog_bad"]) == 0,
            "raw ADC2 or analog validity differs from independent static plant",
        )
    # 合法目标样本才允许进入重构。观察和输出两个事件流按独立编号顺序对照。
    # 拒绝的目标和后缀只有约定的物理记录。漏出码、多出码或串笔都会因此暴露。
    obs_rows = [row for row in results if row["kind"] == "OBS"]
    out_rows = [row for row in results if row["kind"] == "OUT"]
    good_ids = [index + 2 for index, row in enumerate(stimulus[:n]) if not row[2]]
    require(
        [int(row["sample_id"]) for row in obs_rows] == good_ids
        and [int(row["sample_id"]) for row in out_rows] == good_ids
        and bool(good_ids),
        "DUT lost/duplicated accepted sample or emitted rejected prefix",
    )
    # schema 来自本次输入包，字段宽度是生成时定版的协议。
    # 以独立记录的引脚和片序逐字段比较。不允许从已解码的整数码猜测拍次身份。
    schema = json.loads((inputs / "packet_schema.json").read_text())
    # Numeric equality cannot authenticate identity: equal weights or a slot
    # permutation can preserve every code while assigning the wrong physical row.
    # Bind every packet field directly to independently recorded physical trace.
    expected_ordered_ids = []
    expected_main_arrays = []
    expected_sub_arrays = []
    for row in obs_rows:
        sid = int(row["sample_id"])
        residue = by_id[sid]
        ordered = unpack_ids(residue["converting_ids"])
        expected_ordered_ids.append(ordered)
        main_physical = int(residue["main_physical"], 16)
        sub_physical = int(residue["sub_physical"], 16)
        main_slots = [(main_physical >> (p * 63)) & ((1 << 63) - 1) for p in ordered]
        sub_slots = [(sub_physical >> (p * 8)) & 255 for p in ordered]
        expected_main_arrays.append([[(value >> u) & 1 for u in range(63)] for value in main_slots])
        expected_sub_arrays.append([[(value >> u) & 1 for u in range(8)] for value in sub_slots])
        value = int(row["packet_hex"], 16)
        require(0 <= value < 1 << schema["packet_bits"], "packet exceeds declared width")
        fields = {}
        for name in reversed(schema["field_order_msb_to_lsb"]):
            width = schema["field_widths"][name]
            fields[name] = value & ((1 << width) - 1)
            value >>= width
        total_q = dither_terms(stimulus[sid - 2][0])[1]
        expected_fields = {
            "sample_id": sid,
            "coarse": int(residue["coarse_final"]),
            "state": 0,
            "control_word": stimulus[sid - 2][0],
            "adc2_code": int(residue["adc2_code"]),
            "known_common_q": (-total_q) % (1 << schema["field_widths"]["known_common_q"]),
            "slice_ids": int(residue["converting_ids"], 16),
            "main_masks": sum(value << (63 * slot) for slot, value in enumerate(main_slots)),
            "sub_masks": sum(value << (8 * slot) for slot, value in enumerate(sub_slots)),
            "analog_bad": int(residue["analog_bad"]),
        }
        require(
            fields == expected_fields,
            f"packet identity/control/mask differs from actual physical trace at sample{sid}",
        )
    packets = [row["packet_hex"] for row in obs_rows]
    observation = observations_from_rtl(
        packets,
        image.spec,
        packet_schema=schema,
        binding_document=(inputs / "binding.json").read_bytes(),
        connection_binding=binding_features,
        static_charge_domain=True,
        fixed_configuration_epoch=True,
        require_consecutive=not early_invalid,
    )
    require(
        np.array_equal(observation.sample_ids, np.asarray(good_ids))
        and np.array_equal(observation.slice_ids, np.asarray(expected_ordered_ids))
        and np.array_equal(observation.main_masks, np.asarray(expected_main_arrays))
        and np.array_equal(observation.sub_masks, np.asarray(expected_sub_arrays)),
        "production observation identity differs from physical row/pin trace",
    )
    # 用冻结系数解释已确认身份的观测。保持身份核查先于数值比较。
    # 逐笔检查实际 out code、flags 和 analog_bad，避免裁剪后碰巧数值相同而漏报。
    reconstructed = image.reconstruct(observation)
    for i, row in enumerate(out_rows):
        sid = int(row["sample_id"])
        scalar, flags = scalar_word(image, observation, i, dither_terms(stimulus[sid - 2][0])[1])
        require(
            (int(row["code"]), int(row["flags"]), int(row["analog_bad"])) == (scalar, flags, 0),
            "real retired word/flags differ from independent arbitrary-precision oracle",
        )
        require(
            int(reconstructed.code[i]) == scalar
            and int(observation.adc2_code[i]) == int(by_id[sid]["adc2_code"]),
            "production parser/integer decoder differs from actual raw/independent code",
        )
    if plan_output is not None:
        require(not plan_output.exists(), "refusing to overwrite a physical plan record")
        write_json(
            plan_output,
            {
                "rows": plans,
                "capture_samples": n,
                "program_samples": len(stimulus),
                "unretired_postamble": 3,
                "scope": "ACTUAL_SV_ORDERED_IDS_REFERENCES_WORDS_FINITE_WINDOW_NOT_DRAINED",
            },
        )
    return (
        {
            "capture_samples": n,
            "program_samples": len(stimulus),
            "source_accepts": len(controls),
            "samples": physical_count,
            "residues": physical_count,
            "good": len(good_ids),
            "early_prefix_rejected_ids": early_invalid,
            "actual_pin_event_compares": physical_compares,
            "integer_compares": len(good_ids),
            "two_node_compares": physical_count,
            "positive_capture": positive_capture,
            "normal_protocol_status_zero": positive_capture,
            "postamble_sampled": 2,
            "unretired_postamble": 3,
            "finite_capture": True,
            "capture_mode": "FINITE_WINDOW_CONTINUOUS_SOURCE_NOT_DRAINED",
            "status": "PASS",
        },
        observation,
        np.asarray([stimulus[sid - 2][1] for sid in good_ids]),
    )


# =============================================================================
# 7. 输入准备与执行边界
# =============================================================================
# 定点测试的入口从具体终端参数选择 component-only 或学习模式，输出明确标记范围。
# 组件模式固定的是程序，不是整个系统的吞吐率。训练参数不改变该模式的采样次数。
# 学习模式同时跟踪真实 plant、名义装载与实际拟合，三者必须分离。
# 临时编译输出、案例目录、回放工件均由本次运行独占。
# 前一步失败不得复用旧输入或旧二进制。入口保留 FAIL 与执行经过的阶段。
# 实际 HDL 运行与 Python 静态复算分别记录；工具返回成功不是证据已全部检查。
def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--fixture-dir", type=Path, default=REPO / "flow/sim/fixtures/fig19_ideal_ports")
    parser.add_argument(
        "--plant-json",
        type=Path,
        help="Optional explicit fixed actual Cmain/Csub/Cbridge/Cp/Cf; plant-only",
    )
    parser.add_argument("--shared-vectors", type=Path)
    parser.add_argument("--plant-seed", type=int, default=42)
    parser.add_argument(
        "--train", type=int, default=4096, help="Learning-mode samples; unused by component-only"
    )
    parser.add_argument(
        "--holdout", type=int, default=512, help="Learning-mode samples; unused by component-only"
    )
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--component-only",
        action="store_true",
        help="Only capture the explicit64word component fixture with nominal coefficients; no SVD/training",
    )
    args = parser.parse_args()
    out = None
    manifest = {
        "status": "RUNNING",
        "scope": "FIG19_IDEAL_TWO_NODE_STATIC_DUT_NOT_PHYSICAL_SIGNOFF",
    }
    try:
        require(
            1 <= args.train <= 8192 and 1 <= args.holdout <= 8192 and 0 <= args.seed < 2**32,
            "train/holdout must be1..8192 even in component-only; seed must be uint32",
        )
        require(
            args.component_only or (args.train > 1279 and args.holdout >= 64),
            "learning mode requires train>1279 and holdout>=64",
        )
        out = new_output(args.out)
        fixture = args.fixture_dir.resolve()
        config_path, format_path, binding_path = (
            fixture / name for name in ("config.json", "format.json", "binding.json")
        )
        # 输入包由明确指定的配置、格式和绑定生成。当前运行复制真实绑定源码。
        # 再校验输入清单，保证编译器看到的电气命令和复算使用的数学约定是同一套。
        files = build(config_path, format_path, binding_path)
        inputs = out / "inputs"
        inputs.mkdir()
        for name, content in files.items():
            (inputs / name).write_bytes(content if isinstance(content, bytes) else content.encode())
        validate_inputs(inputs)
        cfg = Config.from_dict(json.loads((inputs / "config.json").read_text()))
        # 先解名义寄存器确定期望物理布局。这样不必借用默认模型的后端位数或权重顺序。
        # 组件默认 plant 的元数据来自此夹具版本。运行中不自动更换成主链 ADC2 的新设置。
        nominal = FixedPointReconstructor.from_dict(
            json.loads((fixture / "registers_nominal.json").read_text())
        )
        use_fixture_plant = args.component_only and args.plant_json is None
        if use_fixture_plant:
            # 静态理想组件用夹具的固定 capacitance。这里不会借用当前主模型的默认几何。
            plant_dict = fixture_nominal_plant(fixture, nominal)
            write_json(out / "plant_replay_only.json", plant_dict)
            manifest["manufacturing"] = {
                "source": "DECLARED_FIG19_FIXTURE_NOMINAL_CAPACITANCE",
                "fixture_manifest_sha256": digest(fixture / "fixture_manifest.json"),
            }
        elif args.plant_json is None:
            require(0 <= args.plant_seed < 2**32, "plant-seed must be uint32")
            manufacturing = default_model(
                seed=args.plant_seed,
                config_overrides={"enable_sampling_noise": False, "ra_enable_noise": False},
            )
            require(
                manufacturing.nominal_decoder.to_dict() == nominal.to_dict(),
                "custom nominal fixture requires an explicit matching plant-json",
            )
            actual = manufacturing.chip
            plant_dict = {
                name: getattr(actual, name).tolist()
                for name in ["C_main", "C_sub", "C_bridge", "c_p_main", "c_p_sub"]
            }
            plant_dict["C_feedback_true"] = actual.require_feedback()
            write_json(out / "plant_replay_only.json", plant_dict)
            manifest["manufacturing"] = {
                "seed": args.plant_seed,
                "grade": "ASSUMED_MANUFACTURING_STRESS_NOT_PDK",
                "parameters": manufacturing.inference_table(),
            }
        else:
            require(args.plant_json.stat().st_size < 300_000_000, "plant file exceeds read limit")
            plant_dict = json.loads(args.plant_json.read_bytes())
            shutil.copyfile(args.plant_json, out / "plant_replay_only.json")
            manifest["manufacturing"] = {"source": "EXPLICIT_FIXED_PLANT_JSON_NOT_LEARNER_INPUT"}
        require(
            set(plant_dict)
            == {"C_main", "C_sub", "C_bridge", "c_p_main", "c_p_sub", "C_feedback_true"},
            "plant-json requires exactly the six actual chip fields",
        )
        chip = SplitChip(**plant_dict)
        write_json(
            out / "plant_metadata.json",
            {
                **plant_dict,
                "scope": "ACTUAL_FIXED_PLANT_ONLY_NOT_LEARNER_INPUT",
                "offset_v": 0.0,
                "vfs": cfg.v_fs,
                "adc2_min_v": cfg.adc2_v_min,
                "adc2_max_v": cfg.adc2_v_max,
                "adc2_n_bits": cfg.adc2_n_bits,
                "adc2_codebook": cfg.adc2_codebook,
                "adc2_endpoint_policy": cfg.adc2_endpoint_policy,
                "adc2_max_closed": int(cfg.adc2_endpoint_policy == "closed"),
                "ra_clip_v": cfg.ra_v_clip,
                "sampled_top_v": 0.0,
                "sampled_subnode_v": 0.0,
                "actual_reference_high_v": cfg.v_fs,
                "actual_reference_low_v": -cfg.v_fs,
                "nominal_reference_high_v": cfg.v_fs,
                "nominal_reference_low_v": -cfg.v_fs,
            },
        )
        # 子阵列只按声明桥比折算一次，与生成权重数组的排列保持一致。
        # 矩阵必须覆盖所有物理片。形状不对停在 Python 输入边界，避免仿真只读到部分权重。
        beta = chip.C_bridge / (chip.C_bridge + chip.C_sub.sum(axis=1) + chip.c_p_sub)
        plant_weights = (
            np.concatenate((chip.C_main, beta[:, None] * chip.C_sub), axis=1)
            / chip.require_feedback()
        )
        # 数组由 RTL 按映像声明的布局读取。尺寸不一致时在启动工具前报告。
        require(
            plant_weights.shape == nominal.spec.shape and np.all(plant_weights > 0),
            "explicit plant geometry differs from nominal register image",
        )
        if use_fixture_plant:
            # 同一组名义电容和量化映像都需独立自洽，不能用当前默认模型绕过。
            expected_weights = np.rint(
                plant_weights * (1 << nominal.format.weight_fraction_bits)
            ).astype(np.int64)
            require(np.array_equal(expected_weights, nominal.weights_q),
                    "declared Fig19 fixture capacitances differ from nominal registers")
        plant_path = out / "plant_effective_weights_only.txt"
        plant_path.write_text("".join(f"{float(w):.17g}\n" for w in plant_weights.flat))
        shutil.copyfile(fixture / "registers_nominal.json", out / "registers_nominal.json")
        # 固定源程序的已知目标用于同质模型参考。参考轨迹的来源另做摘要记录。
        # 换了输入文件就得对应新身份。禁止悄悄借另一轮训练的输出比较本轮捕获。
        shared_path = args.shared_vectors or fixture / "homogeneous_code_reference.json"
        require(shared_path.stat().st_size < 300_000_000, "reference vector exceeds read limit")
        shared = json.loads(shared_path.read_text())
        require(shared["words"] == list(range(64)), "explicit shared64word fixture required")
        shared_x = float(shared["input_v"])
        require(
            math.isfinite(shared_x) and abs(shared_x) <= 2.0, "shared input exceeds Fig19 domain"
        )
        if not args.component_only:
            rng = np.random.Generator(np.random.PCG64(args.seed))
            training = [(0, float(v), 0) for v in rng.uniform(-cfg.v_fs, cfg.v_fs, args.train)]
            holdout = [
                (i % 64, float(2.0 * np.sin(2 * np.pi * 17 * i / args.holdout + 0.37)), 0)
                for i in range(args.holdout)
            ]
        manifest.update(
            plant_sha256=digest(out / "plant_replay_only.json"),
            seed=args.seed,
            shared_vector_sha256=digest(shared_path),
            execution_mode="COMPONENT_ONLY_NO_TRAINING"
            if args.component_only
            else "DUT_STATIC_LEARNING",
            cases={},
        )
        if args.component_only:
            manifest["component_domain_v"] = [shared_x, shared_x]
            manifest["learning_arguments_consumed"] = False
            manifest["unconsumed_learning_arguments"] = {
                "train": args.train,
                "holdout": args.holdout,
                "reason": "component-only always captures the explicit64word program; no training/holdout arrays",
            }
        else:
            manifest.update(
                training_domain_v=[-cfg.v_fs, cfg.v_fs],
                holdout_domain_v=[-2.0, 2.0],
                learning_arguments_consumed=True,
            )
        # 冻结传给编译器的源码集合和生成输入。归档中保留足量字段复查实际命令所用身份。
        # 不使用上次生成的构建缓存。临时目录只指向这一轮确定的输入与快照。
        names = [*CORE_SOURCES, "rtl/params/rtl_error_codes.vh", "flow/sim/tb/fig19_static_plant_tb.sv"]
        snapshot = out / "source_snapshot"
        for name in names:
            target = snapshot / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(REPO / name, target)
        source_hashes = {name: digest(snapshot / name) for name in names}
        python_hashes = {
            str(path.relative_to(REPO)): digest(path)
            for path in sorted((REPO / "src/adi_model").glob("*.py"))
        }
        tool_names = [
            "tools/run_fig19_rtl_calibration.py",
            "tools/run_open_rtl.py",
            "tools/export_rtl_params.py",
            "tools/export_fitted_rtl.py",
        ]
        tool_hashes = {name: digest(REPO / name) for name in tool_names}
        for name in tool_names:
            target = snapshot / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(REPO / name, target)
        manifest.update(
            tool_source_sha256=tool_hashes,
            source_sha256=source_hashes,
            python_source_sha256=python_hashes,
            input_manifest_sha256=digest(inputs / "rtl_input_manifest.json"),
        )
        write_json(out / "manifest.json", manifest)
        # 先核对实际执行的仿真器。既不代装工具，也不把工具找不到当作跳过成功。
        # 版本查询在案例专用目录。编译和执行各有独立日志和超时。
        command = command_for("verilator")
        version = checked_run(
            [*command, "-V"], cwd=out, env=os.environ.copy(), log=out / "tool.log", timeout=30
        )
        with tempfile.TemporaryDirectory(prefix="sar_fig19_dut_") as temporary:
            work = Path(temporary)
            (work / "repo").symlink_to(snapshot)
            (work / "inputs").symlink_to(inputs)
            env = os.environ.copy()
            alias_runtime(work, env, version)
            argv = [
                *command,
                "--binary",
                "--timing",
                "--assert",
                "--unroll-count",
                "8192",
                "-j",
                "2",
                # 组件 TB 的告警留在编译日志。漏接管脚和越界选择仍作为错误。
                "-Wall",
                "-Wno-fatal",
                "-Werror-PINMISSING",
                "-Werror-SELRANGE",
                *verilator_compiler_args(),
                "--top-module",
                "fig19_static_plant_tb",
                f"-GP_TICK_NS={json.loads((inputs / 'binding.json').read_text())['timing']['tick_period_ns']}",
                "--Mdir",
                str(work / "obj"),
                f"-I{work}/inputs",
                f"-I{work}/repo/rtl/params",
                *[str(work / "repo" / name) for name in names if name.endswith(".sv")],
                str(work / "inputs/source_control_binding.sv"),
                str(work / "inputs/source_acquisition_binding.sv"),
            ]
            checked_run(argv, cwd=work, env=env, log=out / "compile.log", timeout=300)
            binary = work / "obj/Vfig19_static_plant_tb"

            # 每个案例建立独占工件。寄存器、激励、工具返回与实际对拍结论分别写入。
            # 按事先声明的计数验证完成标记和每个目标是否退休，退出码良好仍需过独立比较。
            def run_case(name: str, target: list[tuple], decoder: FixedPointReconstructor):
                folder = out / name
                folder.mkdir()
                n = len(target)
                stimulus = [*target, (0, 0.0, 0), (0, 0.0, 0), (0, 0.0, 0)]
                expected_good = sum(not row[2] for row in target)
                positive_capture = expected_good == n
                expected_counts = {
                    "samples": n + 2,
                    "good": expected_good,
                    "residues": n + 2,
                    "observations": expected_good,
                    "outputs": expected_good,
                    "capture_samples": n,
                    "program_samples": n + 3,
                    "source_accepts": n + 3,
                    "postamble_sampled": 2,
                    "unretired_postamble": 3,
                    "finite_capture": 1,
                }
                stimulus_path = folder / "stimulus.txt"
                stimulus_path.write_text(
                    "".join(f"{word} {value:.17g} {bad}\n" for word, value, bad in stimulus)
                )
                # 保留 JSON 映像与编译器读的 hex 文件。观测解码选择同一次容器的宽度、顺序和尺度。
                # 未使用的学习参数也单列，避免把一组固定组件的数据描述成一次校准结果。
                registers = folder / "registers.json"
                write_json(registers, decoder.to_dict())
                weights_path, scalars_path = folder / "weights.hex", folder / "scalars.hex"
                weights_path.write_text("".join(f"{int(w):012x}\n" for w in decoder.weights_q.flat))
                scalars_path.write_text(
                    "".join(
                        f"{v % (1<<64):016x}\n"
                        for v in [decoder.offset_q, decoder.adc2_min_q, decoder.adc2_max_q]
                    )
                )
                run = [
                    str(binary),
                    f"+weights={weights_path}",
                    f"+scalars={scalars_path}",
                    f"+plant_weights={plant_path}",
                    "+plant_offset_v=0",
                    f"+stimulus={stimulus_path}",
                    f"+expected_samples={n}",
                    f"+program_samples={len(stimulus)}",
                    f"+expected_good={expected_good}",
                    f"+vfs={cfg.v_fs:.17g}",
                    f"+adc2_min_v={cfg.adc2_v_min:.17g}",
                    f"+adc2_max_v={cfg.adc2_v_max:.17g}",
                    f"+adc2_max_closed={int(cfg.adc2_endpoint_policy=='closed')}",
                    f"+ra_clip_v={cfg.ra_v_clip:.17g}",
                    f"+trace_dir={folder}",
                ]
                payloads = [weights_path, scalars_path, stimulus_path, registers, plant_path]
                payload_before = {p.name: digest(p) for p in payloads}
                write_json(
                    folder / "case_manifest.json",
                    {
                        "schema_version": 2,
                        "capture_mode": "FINITE_WINDOW_CONTINUOUS_SOURCE",
                        "capture_samples": n,
                        "program_samples": len(stimulus),
                        "expected_good": expected_good,
                        "expected_counts": {**expected_counts, "rdac_events": 7 * (n + 2)},
                        "postamble_records": 3,
                        "sampled_postamble_records": 2,
                        "unretired_postamble_records": 3,
                        "positive_capture": positive_capture,
                        "normal_protocol_status_required_zero": positive_capture,
                        "first_sample_id": 2,
                        "sample_id_increment": 1,
                        "sample_id_bits": 32,
                        "stimulus_columns": ["word", "reference_v", "expected_reject"],
                        "plant_sha256": digest(out / "plant_replay_only.json"),
                        "input_sha256": payload_before,
                        "trace_scope": "actualDUT physicalplan/twoidealnodes, finite continuous-source window, not drained; no physicalmacro signoff",
                    },
                )
                text = checked_run(run, cwd=work, env=env, log=folder / "run.log", timeout=180)
                require(
                    all(digest(p) == payload_before[p.name] for p in payloads),
                    "input payload drift during actualDUT case",
                )
                counts = completion(
                    text,
                    "FIG19_STATIC_PLANT_COMPLETE",
                    (*expected_counts, "checks"),
                )
                require(
                    all(counts[key] == value for key, value in expected_counts.items()),
                    "completed finite program/physical/target count differs",
                )
                rejected = re.findall(r"(?:^|\s)rejects=([0-9]+)(?=\s|$)", text)
                require(
                    len(rejected) == 1 and int(rejected[0]) == n - expected_good,
                    "completed expected-prefix reject count differs",
                )
                result, observation, reference = verify_trace(
                    folder,
                    inputs,
                    registers,
                    stimulus,
                    plant_weights,
                    cfg,
                    capture_samples=n,
                )
                result["completion_counts"] = counts
                result["trace_sha256"] = {
                    trace: digest(folder / trace)
                    for trace in (
                        "controls.csv",
                        "samples.csv",
                        "residues.csv",
                        "rdac_events.csv",
                        "events.csv",
                    )
                }
                write_json(folder / "result.json", result)
                manifest["cases"][name] = result
                write_json(out / "manifest.json", manifest)
                return observation, reference

            if args.component_only:
                run_case(
                    "component_shared64",
                    [(word, shared_x, 0) for word in shared["words"]],
                    nominal,
                )
                manifest["calibration"] = {
                    "status": "NOT_RUN",
                    "reason": "Explicit bounded component capture; no training or fitted coefficients",
                }
            else:
                # These are real nonuniform-plant samples, not the homogeneous shared64 reference.
                training_observation, reference = run_case("training_word0", training, nominal)
                fitted = fit_unit_weights(training_observation, reference)
                write_json(out / "frozen_fit.json", fitted.to_dict())
                register_text, fitted_manifest = build_fitted_registers(
                    config_path, format_path, binding_path, out / "frozen_fit.json"
                )
                (out / "fitted_registers.json").write_text(register_text)
                (out / "fitted_register_manifest.json").write_text(fitted_manifest)
                corrected = FixedPointReconstructor.from_dict(json.loads(register_text))
                before_obs, hold_reference = run_case("holdout_nominal", holdout, nominal)
                after_obs, _ = run_case("holdout_fitted", holdout, corrected)
                require(
                    np.array_equal(before_obs.adc2_code, after_obs.adc2_code)
                    and np.array_equal(before_obs.slice_ids, after_obs.slice_ids)
                    and np.array_equal(before_obs.main_masks, after_obs.main_masks)
                    and np.array_equal(before_obs.sub_masks, after_obs.sub_masks)
                    and np.array_equal(
                        before_obs.known_voltage_term_v, after_obs.known_voltage_term_v
                    ),
                    "nominal/fitted holdout changed physical observations",
                )
                before_error = nominal.reconstruct(before_obs).voltage - hold_reference
                after_error = corrected.reconstruct(after_obs).voltage - hold_reference
                before_rms, after_rms = (
                    float(np.sqrt(np.mean(error**2))) for error in [before_error, after_error]
                )
                require(
                    after_rms < before_rms,
                    "frozen coefficient application did not improve same-raw holdout RMS",
                )
                manifest["calibration"] = {
                    "rank": fitted.rank,
                    "parameters": 1279,
                    "condition": fitted.condition,
                    "training_samples": args.train,
                    "holdout_samples": args.holdout,
                    "nominal_rms_v": before_rms,
                    "fitted_rms_v": after_rms,
                    "nominal_std_v": float(np.std(before_error)),
                    "fitted_std_v": float(np.std(after_error)),
                    "coefficient_source": "SVD_DUT_PACKETS_AND_KNOWN_REFERENCE_ONLY",
                    "same_physical_observations": True,
                }
                # Separate full dynamic-node negative controls use the same actual chip.
                run_case(
                    "early_prefix_negative",
                    [
                        (0, shared_x, 0),
                        (32, -2.9, 1),
                        (63, shared_x, 0),
                        (1, -2.0, 0),
                        (40, -2.9, 1),
                        (0, -2.99, 0),
                    ],
                    corrected,
                )
        require(
            all(digest(REPO / name) == expected for name, expected in source_hashes.items()),
            "source drift during the actual RTL/learning cohort",
        )
        require(
            all(digest(REPO / name) == expected for name, expected in tool_hashes.items()),
            "consumed tool source drift during the cohort",
        )
        require(
            all(digest(REPO / name) == expected for name, expected in python_hashes.items()),
            "consumed Python source drift during the cohort",
        )
        manifest.update(
            status=(
                "PASS_COMPONENT_CAPTURE_NO_TRAINING_PENDING_INDEPENDENT_PYTHON_REPLAY"
                if args.component_only
                else "PASS_DUT_LEARNING_PENDING_INDEPENDENT_SAME_CHIP_PYTHON_REPLAY"
            ),
            source_sha256=source_hashes,
            tool_version=version,
            physical_signoff="NOT_RUN",
            python_same_chip_replay="PENDING",
            capture_mode="FINITE_WINDOW_CONTINUOUS_SOURCE_NOT_DRAINED",
            unretired_postamble_per_case=3,
        )
        write_json(out / "manifest.json", manifest)
        if args.component_only:
            print("FIG19_RTL_COMPONENT_COMPLETE outputs=64 training=NOT_RUN replay=PENDING")
        else:
            print(
                f"FIG19_RTL_STATIC_LEARNING_COMPLETE rank={fitted.rank} holdout={args.holdout} same_raw=1 replay=PENDING"
            )
        return 0
    except (OSError, ValueError, RuntimeError) as error:
        if out is not None:
            manifest.update(status="FAIL", error=str(error))
            write_json(out / "manifest.json", manifest)
        print(f"FAIL: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
