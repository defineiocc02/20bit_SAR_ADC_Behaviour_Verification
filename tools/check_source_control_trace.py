#!/usr/bin/env python3
# =============================================================================
# 文件名     : check_source_control_trace.py
# 模块名     : check_source_control_trace
# 版本       : v1.0
# 功能       : 用镜像系数和实际片组选通记录逐笔复算合成源控制用例。
# 设计说明   : 复算按实际样本 ID 对齐采集、残差、观测包和退休记录。
#              冻结输入由 SHA 约束，测试不读取 DUT 的定点归约中间值。
#              普通 AZ 记录与历史 CAAZ 扩展的列规则独立，接受的格式必须明确。
# 变更记录   : 支持现行基础轨迹中不存在扩展斩波字段的格式。
# =============================================================================
"""用精确整数方程核对指定的合成源控制组件轨迹。

核对器读取显式系数镜像，独立按物理单元计算选通与电荷项，不使用 DUT 的
归约中间节点或行为模型系数。结果仅覆盖该数字组件夹具，不证明实际 ADC、
原文开关映射等价、自归零噪声抵消或时序收敛。
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path


# =============================================================================
# 1. 输入列、包头与数值规则
# =============================================================================
# 遇到第一项不符即退出，不写汇总 PASS。异常原因用于查找目标轨迹而非代替它。
# CSV 列名和顺序是协议的一部分。多列、缺列及空记录均不可默认为合法。
# 确实存在的空字符串与没有该列不同，读入层保留这种区别。
def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def read_rows(
    path: Path, columns: list[str], *, optional_last: str | None = None
) -> list[dict]:
    # 明确限定两种列名及顺序。只允许指定的历史扩展列出现在末尾。
    with path.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        compatible = optional_last is not None and reader.fieldnames == [*columns, optional_last]
        require(reader.fieldnames == columns or compatible, f"unexpected CSV fields: {path}")
        rows = list(reader)
    require(
        bool(rows) and all(None not in row and None not in row.values() for row in rows),
        f"empty/malformed trace: {path}",
    )
    return rows


# 使用整数除法和 ties-to-even 实现夹具的舍入约定，避免浮点复算在边界向不同侧舍入。
# 余数与分母比的是精确整数。可在大分母的情况下判定恰好中点。
def rounded_ratio(numerator: int, denominator: int) -> int:
    quotient, remainder = divmod(numerator, denominator)
    return quotient + int(
        2 * remainder > denominator or (2 * remainder == denominator and quotient & 1)
    )


# 字节位序取自冻结 schema。先限制总体位宽，再逆序解析字段。
# 读出后不得有无法解释的高位，防止包头变动却沿旧格式解析出逻辑可用的数值。
# 公共偏移字段单独按补码解释；物理选择位必须作为无符号码解码。
def decode_packet(text: str, schema: dict) -> dict:
    value = int(text, 16)
    require(0 <= value < 1 << schema["packet_bits"], "packet exceeds declared width")
    result = {}
    for field in reversed(schema["field_order_msb_to_lsb"]):
        width = schema["field_widths"][field]
        result[field] = value & ((1 << width) - 1)
        value >>= width
    require(value == 0, "packet field widths do not cover the record")
    bits = schema["field_widths"]["known_common_q"]
    if result["known_common_q"] >= 1 << (bits - 1):
        result["known_common_q"] -= 1 << bits
    return result


# =============================================================================
# 2. 独立物理绑定和定点复算
# =============================================================================
# 掩码由已决粗码、状态和片号单元公式生成，不能从同一份 DUT 掩码反推期望。
# fixture 的主阵列两单元和子阵列一单元按相同物理槽位重建。其他几何不适用此公式。
# packet 的槽级位和残差记录的物理引脚位分别比较，以捕获片号交换错误。
def expected_masks(coarse: int, state: int, ids: list[int]) -> tuple[list, list]:
    """按显式小型绑定独立计算标量选通，不读取 DUT 内部结果。"""
    main = [
        [
            ((coarse >> ((unit + physical) % 9)) & 1) ^ ((state >> (slot % 3)) & 1)
            for unit in range(2)
        ]
        for slot, physical in enumerate(ids)
    ]
    sub = [
        [((coarse >> ((slot + 1) % 9)) & 1) ^ ((state >> ((slot + 1) % 3)) & 1)]
        for slot in range(8)
    ]
    return main, sub


# 细码仅在已声明的区间和位宽内复算到码箱中心，区间上下界来自系数镜像。
# 已知 common 是转换阶段的电荷项，符号与选通参考共同决定每单元累积项。
# 累加增益与电压项独立求和，不能借归约中间结果直接验证自身。
# 溢出先在累加域判定。缩放结果进入输出码域后才检查裁剪及对应标志。
def expected_code(
    packet: dict, image: dict, ids: list[int], main: list, sub: list
) -> tuple[int, int]:
    """逐单元累加量化电压项，不复用 T/G/R 归约结果。"""
    fmt, spec = image["format"], image["spec"]
    fine = image["adc2_min_q"] + rounded_ratio(
        (2 * packet["adc2_code"] + 1) * (image["adc2_max_q"] - image["adc2_min_q"]),
        1 << (spec["adc2_n_bits"] + 1),
    )
    gain = 0
    known_sum = 0
    for slot, physical in enumerate(ids):
        for unit, on in enumerate(main[slot] + sub[slot]):
            weight = image["weights_q"][physical][unit]
            gain += weight
            term = packet["known_common_q"] + (1 - 2 * on) * (1 << fmt["voltage_fraction_bits"])
            known_sum += weight * term
    numerator = (fine - image["offset_q"]) * (1 << fmt["weight_fraction_bits"]) - known_sum
    shifted = (numerator + gain * (1 << fmt["voltage_fraction_bits"])) * (1 << fmt["output_bits"])
    denominator = 2 * gain * (1 << fmt["voltage_fraction_bits"])
    limit = 1 << (fmt["accumulator_bits"] - 1)
    require(
        gain > 0 and all(-limit <= x < limit for x in (known_sum, numerator, shifted, denominator)),
        "fixture exceeded its declared integer arithmetic domain",
    )
    quotient = shifted // denominator
    flags = (8 if quotient < 0 else 0) | (16 if quotient >= 1 << 20 else 0)
    return min(max(quotient, 0), (1 << 20) - 1), flags


# =============================================================================
# 3. 实际轨迹的身份与运行条件
# =============================================================================
# 先验证所有输入文件的字节，再解析观测轨迹。任何系数或格式不一致都不能解释通过。
# 当前复算公式只针对指定几何和 ADC2 位宽，修改夹具须先更新独立数值公式。
# 基础轨迹无 CAAZ 时可以没有斩波字段；原记录包含该字段时其零值仍必须核对。
# 历史扩展模式不能接受省略斩波输入或推算标签，避免在观察缺失时给出配对证据。
def verify(inputs: Path, registers: Path, traces: Path, caaz_enabled: bool) -> dict:
    schema = json.loads((inputs / "packet_schema.json").read_text())
    image = json.loads(registers.read_text())
    manifest = json.loads((inputs / "rtl_input_manifest.json").read_text())
    for name, digest in manifest["artifact_sha256"].items():
        require(
            hashlib.sha256((inputs / name).read_bytes()).hexdigest() == digest,
            f"input drift: {name}",
        )
    require(
        schema["schema_version"] == 2 and schema["packed_index_order"] == "slot0_lsb_unit0_lsb",
        "explicit v2 packet schema required",
    )
    spec = image["spec"]
    require(
        (
            spec["n_slices"],
            spec["n_active"],
            spec["dac_n_main"],
            spec["dac_n_sub"],
            spec["adc2_n_bits"],
        )
        == (18, 8, 2, 1, 6),
        "named numerical fixture required",
    )
    control_columns = [
        "epoch",
        "owner_sample_id",
        "input_valid",
        "input_word",
        "input_state",
        "actual_valid",
        "actual_word",
        "actual_state",
        "acquiring_mask",
        "converting_mask",
    ]
    # 基础核现行记录没有斩波端口。历史基础记录的斩波列如存在，仍核对其零值。
    if caaz_enabled:
        control_columns.append("input_chop")
    controls = read_rows(
        traces / "controls.csv",
        control_columns,
        optional_last=None if caaz_enabled else "input_chop",
    )
    residues = read_rows(
        traces / "residues.csv",
        [
            "epoch",
            "sample_id",
            "converting_mask",
            "selected_mask",
            "main_physical",
            "sub_physical",
            "sw_valid",
        ],
    )
    events = read_rows(
        traces / "events.csv",
        [
            "epoch",
            "kind",
            "sample_id",
            "code",
            "flags",
            "analog_bad",
            "packet_hex",
            "caaz_hex",
            "status_word",
            "analog_sticky",
        ],
    )
    result = {
        "scope": "COMPONENT_SYNTHETIC_DIGITAL",
        "epochs": 2,
        "acquisition_checks": 0,
        "packet_checks": 0,
        "physical_mask_checks": 0,
        "integer_compares": 0,
        "tag_checks": 0,
        "pair_compares": 0,
        "cancelled_after_launch": 0,
        "invalid_word_samples": 0,
        "caaz_enabled": caaz_enabled,
    }
    # =============================================================================
    # 4. 采集与转换片组所有权
    # =============================================================================
    # 采用两个独立 epoch，验证清空后重新装载。按 epoch 分组以防旧流水线结果误归新一轮。
    # 采集记录建立后续比较的 ID 字典。刺激的节奏、无效样本和非法映射需已知且可重放。
    # 前一拍的采集组必须在后续时刻成为转换组。不能只验证当前两组的引脚互斥和数量。
    # 无效输入允许保持上次实际字和状态，不能让这份保持被解释为当前样本接收成功。
    for epoch in range(2):
        group = [row for row in controls if int(row["epoch"]) == epoch]
        require(len(group) >= 24, "insufficient acquisition coverage")
        by_id = {}
        previous = None
        for index, row in enumerate(group):
            owner = int(row["owner_sample_id"])
            require(
                owner == index + 2 and owner not in by_id, "duplicate/missing acquisition owner"
            )
            valid = owner != 8
            word = owner % 4
            state = 7 if owner == 12 else owner % 7
            require(
                [int(row[k]) for k in ("input_valid", "input_word", "input_state")]
                == [int(valid), word, state],
                "explicit stimulus recipe changed",
            )
            require(
                int(row["actual_valid"]) == int(valid), "invalid/valid control ownership changed"
            )
            if valid:
                require(
                    int(row["actual_word"]) == word and int(row["actual_state"]) == state,
                    "registered acquisition word/state mismatch",
                )
            elif previous:
                require(
                    row["actual_word"] == previous["actual_word"]
                    and row["actual_state"] == previous["actual_state"],
                    "invalid acquisition silently substituted another word",
                )
            acquire, convert = int(row["acquiring_mask"], 16), int(row["converting_mask"], 16)
            require(
                acquire.bit_count() == 8 and convert.bit_count() == 8 and not acquire & convert,
                "8/8/2 resource contract failed",
            )
            if previous:
                require(
                    convert == int(previous["acquiring_mask"], 16),
                    "conversion did not own preceding acquisition",
                )
            if "input_chop" in row:
                require(
                    int(row["input_chop"]) == (index % 2 if caaz_enabled else 0),
                    "actual aperture chop sequence changed",
                )
            by_id[owner] = row
            previous = row
            result["acquisition_checks"] += 1
        # =============================================================================
        # 5. 物理引脚、观察包及同笔故障
        # =============================================================================
        # 按 epoch 与样本号连接采集同残差和 OBS。即使数值碰巧相同，重复或错位身份仍失败。
        # 被观察的物理片号必须由该笔采集；按包中槽位展开成取证引脚的顺序。
        # 递交的已知 common 应与本笔控制字相符，防止观察者读取正在采集的下一笔。
        # 非法绑定的正常退出是没有新的开关写入，不能仅靠未退休指出异常处理正确。
        # 注入的外部故障应只随它所属残差传播；检查观察端和退休端使用同一身份。
        physical = {int(row["sample_id"]): row for row in residues if int(row["epoch"]) == epoch}
        expected = {}
        expected_tags = {}
        seen_packets = set()
        selected_events = [row for row in events if int(row["epoch"]) == epoch]
        for row in selected_events:
            if row["kind"] != "OBS":
                continue
            packet = decode_packet(row["packet_hex"], schema)
            sample = packet["sample_id"]
            require(
                sample in by_id and sample not in seen_packets,
                "observation lost/duplicated sample identity",
            )
            owner = by_id[sample]
            require(
                int(owner["input_valid"]) == 1 and int(owner["input_state"]) != 7,
                "invalid control word reused as a good sample",
            )
            require(
                packet["coarse"] == 173
                and packet["adc2_code"] == 32
                and packet["control_word"] == int(owner["input_word"])
                and packet["state"] == int(owner["input_state"]),
                "live/current state replaced the previous residue context",
            )
            require(
                packet["known_common_q"] == packet["control_word"] << 8,
                "common term ownership mismatch",
            )
            ids = [(packet["slice_ids"] >> (5 * slot)) & 31 for slot in range(8)]
            require(
                len(set(ids)) == 8
                and all(value < 18 for value in ids)
                and sum(1 << value for value in ids) == int(owner["acquiring_mask"], 16),
                "physical ID ownership mismatch",
            )
            main, sub = expected_masks(173, packet["state"], ids)
            packed_main = sum(
                bit << (2 * slot + unit) for slot in range(8) for unit, bit in enumerate(main[slot])
            )
            packed_sub = sum(sub[slot][0] << slot for slot in range(8))
            require(
                packet["main_masks"] == packed_main and packet["sub_masks"] == packed_sub,
                "actual binding mask mismatch",
            )
            residue = physical[sample]
            require(
                int(residue["selected_mask"], 16) == int(owner["acquiring_mask"], 16)
                and int(residue["sw_valid"]) == 1,
                "physical RDAC did not accept the final resolved decision",
            )
            require(
                int(residue["main_physical"], 16)
                == sum(
                    bit << (2 * physical_id + unit)
                    for slot, physical_id in enumerate(ids)
                    for unit, bit in enumerate(main[slot])
                )
                and int(residue["sub_physical"], 16)
                == sum(sub[slot][0] << physical_id for slot, physical_id in enumerate(ids)),
                "packet differs from actual physical RDAC pins",
            )
            require(
                packet["analog_bad"] == int(sample == 6), "external fault sampled for wrong residue"
            )
            code, flags = expected_code(packet, image, ids, main, sub)
            require(flags == 0, "chosen integration fixture unexpectedly clips")
            # 基础模式保留历史 sideband 列的零值判据，避免把误接通的扩展标签当成普通 AZ。
            # 扩展模式恢复正负相与物理 AZ 代，要求完整标签与本笔采集斩波记录对应。
            side = int(row["caaz_hex"], 16)
            if caaz_enabled:
                require(
                    (side >> 33, (side >> 1) & ((1 << 32) - 1), side & 1)
                    == (int(owner["input_chop"]), (sample - 2) // 2, 1),
                    "CAAZ generation/tag context mismatch",
                )
                result["tag_checks"] += 1
                expected_tags[sample] = (side >> 33, (side >> 1) & ((1 << 32) - 1), side & 1)
            else:
                require(side == 0, "disabled optional tags changed the component")
            expected[sample] = (code, flags, packet["analog_bad"])
            seen_packets.add(sample)
            result["packet_checks"] += 1
            result["physical_mask_checks"] += 1
        require(8 not in expected and 12 not in expected, "invalid source controls retired")
        for sample in (8, 12):
            require(
                int(physical[sample]["sw_valid"]) == 0,
                "invalid source command loaded a new RDAC word",
            )
            result["invalid_word_samples"] += 1
        # =============================================================================
        # 6. 退休值、取消和配对
        # =============================================================================
        # 基础模式从 OUT 采集第三方可见结果。扩展模式先对 RAW 做同一套整数检验。
        # 每个退休事件唯一，并须有本笔已经对齐的观测。无论数据正确与否，身份重用都失败。
        # 观测超出退休的唯一合法原因是本夹具的显式末笔取消，事件日志须记录相应终止。
        raw = [row for row in selected_events if row["kind"] == ("RAW" if caaz_enabled else "OUT")]
        require(bool(raw), "zero numerical comparisons")
        seen_raw = set()
        for row in raw:
            sample = int(row["sample_id"])
            require(
                sample in expected and sample not in seen_raw, "unexpected/duplicate retirement"
            )
            require(
                (int(row["code"]), int(row["flags"]), int(row["analog_bad"])) == expected[sample],
                "independent integer reconstruction mismatch",
            )
            if caaz_enabled:
                value = int(row["packet_hex"], 16)
                require(
                    (value >> 60, (value >> 40) & ((1 << 20) - 1), value & 1)
                    == (sample, int(row["code"]), int(row["analog_bad"])),
                    "raw packet/output mismatch",
                )
                require(
                    ((value >> 39) & 1, (value >> 7) & ((1 << 32) - 1), (value >> 6) & 1)
                    == expected_tags[sample],
                    "raw result substituted live CAAZ metadata",
                )
            seen_raw.add(sample)
            result["integer_compares"] += 1
        cancelled = set(expected) - seen_raw
        require(
            len(cancelled) == 1 and any(row["kind"] == "END" for row in selected_events),
            "only the explicitly cleared in-flight launch may be cancelled",
        )
        result["cancelled_after_launch"] += len(cancelled)
        # 历史扩展的期望值从已验证 RAW 序列独立配对，依据连续样本及同代存储条件。
        # 坏标志会按该协议终止后续有效配对，因此不能先过滤坏输入改变期望状态机。
        # 逐对比较最终结果和完整样本身份，不以均值或差分偏好代替同笔比较。
        if caaz_enabled:
            expected_pairs = []
            pending = None
            fault = False
            for row in raw:
                sample = int(row["sample_id"])
                code, flags, bad = expected[sample]
                chop, az_id, az_valid = expected_tags[sample]
                if fault:
                    continue
                if flags or bad:
                    fault, pending = True, None
                    continue
                if pending is None:
                    if chop != 0 or not az_valid:
                        fault = True
                    else:
                        pending = (sample, code, az_id)
                elif (
                    sample != ((pending[0] + 1) & ((1 << 32) - 1))
                    or chop != 1
                    or not az_valid
                    or az_id != pending[2]
                ):
                    fault, pending = True, None
                else:
                    expected_pairs.append((sample, (pending[1] - code + (1 << 20)) // 2))
                    pending = None
            actual_pairs = [
                (int(row["sample_id"]), int(row["code"]))
                for row in selected_events
                if row["kind"] == "OUT"
            ]
            require(
                actual_pairs == expected_pairs and bool(actual_pairs) and fault,
                "CAAZ integer pairing/fault isolation mismatch",
            )
            result["pair_compares"] += len(actual_pairs)
    result["status"] = "PASS_DIGITAL_COMPONENT_ONLY"
    result["physical_mapping_timing_AZ_noise"] = "NOT_VERIFIED"
    return result


# =============================================================================
# 7. 结果输出边界
# =============================================================================
# 输出路径必须全新，成功后写唯一 JSON。兼容字段并不升级结果范围。
# 失败时不覆盖调用者原先保留的证据。只要读取或比较不成功，CLI 返回非零。
def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--inputs-dir", type=Path, required=True)
    parser.add_argument("--registers", type=Path, required=True)
    parser.add_argument("--trace-dir", type=Path, required=True)
    parser.add_argument("--caaz", type=int, choices=(0, 1), required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    require(not args.out.exists(), "refusing existing checker output")
    result = verify(args.inputs_dir, args.registers, args.trace_dir, bool(args.caaz))
    args.out.mkdir(parents=True, exist_ok=False)
    (args.out / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
