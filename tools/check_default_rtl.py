#!/usr/bin/env python3
# =============================================================================
# 文件名     : check_default_rtl.py
# 模块名     : 公共 Python 入口与生产数字 IP 的逐笔比较
# 功能       : 以输入电压及外部控制重放独立 Verilator 采集，并对比真实输出。
# 设计说明   : 比较器期望由 Python 独立计算。RTL 开关、原码和出码只在比较侧
#              读取，不能拿这些结果反推期望。缺列、重复标签或少跑场景均失败。
# =============================================================================
"""在仓库根运行 python tools/check_default_rtl.py /path/to/v21_sample_cadence/samples.csv。"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

import numpy as np

from adi_model import DefaultParameters, default_model
from adi_model.digital_conversion import LSB20, Q30, VFS, nominal_weights_q30

CASES = {
    "dither_internal": 1024,
    "dither_sweep": 192,
    "dither_mixed": 192,
    "dither_nominal_table": 192,
    "dither_corrected_table": 192,
}


# 固定输入组既比较库调用，又核对真实 CLI 发布的全部列。
# 其余组只给模型激励和配置；所有开关结果与码均来自另一个、独立执行的 RTL。
# 位图必须逐槽核对。某两个等权单元接反时，只比较正常输出仍可能完全相同。
# 少一列、截掉末尾行或相同标签被重复退休都不能计为匹配。
def compare(path: Path, default_npz: Path | None = None) -> int:
    """逐标签检查帧身份、所有物理掩码、所有已决命令和输出码。"""
    with path.open(newline="", encoding="utf-8") as src:
        trace = list(csv.DictReader(src))
    total = 0
    for name, size in CASES.items():
        rows = [row for row in trace if row["case"] == name]
        if len(rows) != size:
            raise ValueError(f"{name}: expected {size} actual RTL samples, got {len(rows)}")

        def column(key):
            return np.array([int(row[key]) for row in rows], dtype=np.int64)

        # 默认场景两边独立生字、选片和产生宏上游相位。其余场景只送入本来就由
        # 测试端提供的控制量；强制一字也必须占用片内随机序列的一笔。
        tag = np.arange(1, size + 1)
        forced = None
        phases = None
        if name != "dither_internal":
            phases = np.column_stack([column(k) for k in ("h", "v", "half", "coin")])
            forced = [
                (int(t) - 1) % 64 if name != "dither_mixed" or t % 5 == 0 else None for t in tag
            ]
        options: dict = {}
        if name != "dither_internal":
            options["scheduler_seed"] = 0x12345678
        if name == "dither_mixed":
            options["scheduler_seed"] = 0x13579BDF
        if name in ("dither_nominal_table", "dither_corrected_table"):
            # 两张加载表取完全相同的器件；在物理片 0 的子单元 18 注入已知偏差。
            words = nominal_weights_q30()
            words[0, 18] += 1 << 20
            options["device_weights"] = words / Q30
            if name == "dither_corrected_table":
                options["weights_q30"] = words
        voltage = (column("input_code") + column("input_quarters") / 4) * LSB20 - VFS
        if name == "dither_mixed" and (
            set(column("flash")) != set(range(8))
            or set(column("path")) != {0, 1, 2}
            or set(column("input_quarters")) != {0, 1, 2, 3}
            or not column("sat").any()
        ):
            raise ValueError(
                "missing Flash partition, guard path, fractional step or rail saturation"
            )
        model = default_model(parameters=DefaultParameters(**options))
        run = model.simulate(lambda _t: voltage, size, dither_words=forced, dem_controls=phases)
        if name == "dither_internal" and default_npz is not None:
            # 验证实际用户 CLI 的发布数据，防止库已切换而命令行仍走旁路。
            with np.load(default_npz, allow_pickle=False) as saved:
                wanted = run.arrays()
                if set(saved.files) != set(wanted):
                    raise ValueError("default CLI archive fields differ from the production model")
                for key, value in wanted.items():
                    if not np.array_equal(saved[key], value):
                        raise ValueError(f"default CLI archive differs in {key}")
        a = run.raw
        refs = {
            "tag": a.tag,
            "qid": a.qid,
            "word": a.word,
            "rdac_pre_eighths": a.word,
            "sadc_mask": a.sadc_mask,
            "sadc_lower_eighths": a.lower_eighths,
            "h": a.h,
            "v": a.v,
            "half": a.half,
            "coin": a.coin,
            "flash": np.minimum(a.coarse >> 6, 7),
            "k": a.k,
            "raw": a.adc2_code,
            "adc2_range": a.adc2_over,
            "code": run.codes.code,
            "err": a.out_err,
            "sat": run.codes.clipped_low | run.codes.clipped_high,
            "event_count": a.event_count,
            "ids_packed": a.acquisition_ids @ (1 << (5 * np.arange(8))),
            "pre_packed": a.pre_slots @ (1 << (4 * np.arange(8))),
        }
        # 对每一槽的实际 20 根线与每个比较事件独立检查。
        # 跨段等电荷交换通常不影响名义输出，仅靠码相同发现不了错接。
        for slot in range(8):
            refs[f"post_slot{slot}"] = a.post_slots[:, slot]
            refs[f"event_k{slot}"] = a.event_k[:, slot]
        if not np.all(a.out_valid) or not np.array_equal(column("tag"), tag):
            raise ValueError(f"{name}: missing retirement or out-of-order tag")
        for field, expected in refs.items():
            measured = column(field)
            # 首先报告实际硬件标签和字段，再列出应有值。
            # 时间对应关系错误不应被后续样本的误差平均掉。
            wrong = np.flatnonzero(measured != expected)
            if wrong.size:
                i = int(wrong[0])
                raise ValueError(
                    f"{name} tag={int(tag[i])} {field}: RTL={measured[i]} Python={expected[i]}"
                )
        print(f"{name}: {size} tags, {len(refs)} fields matched")
        total += size
    return total


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("samples_csv", type=Path)
    parser.add_argument(
        "--default-npz", type=Path, help="公开 CLI 运行固定输入 1024 笔产生的 codes.npz"
    )
    args = parser.parse_args()
    print(f"DEFAULT_PYTHON_RTL_MATCH transactions={compare(args.samples_csv, args.default_npz)}")
