# =============================================================================
# 文件名     : characterize_registers.py
# 模块名     : 系统 GAIN 映射测量与 OFFSET 写后复测
# 版本       : v1.0
# 功能       : 从已知直流输入与寄存器回读得到实际增益码表，并测选定档的偏移步长。
# 设计说明   : 比值以同一器件的 0x200 为基准。测两种 OFFSET 可辨别该档的
#              实际偏移单位，避免将自研 DSP 的内部加乘顺序带入实物测试。
# 变更记录   : 加入按已测档选择、回读字段检查和原始采集文件保护。
# =============================================================================
"""离线主机工具。gain-table 输出可直接交给 system_cal.py --gain-table 的 CSV。"""
import argparse
import csv
import io
import json
import math
import os
from pathlib import Path
import sys
import tempfile

import numpy as np

if __package__:
    from .system_cal import CalibrationError, decode_codes, fit_dc
else:
    from system_cal import CalibrationError, decode_codes, fit_dc


# =============================================================================
# 1. 采样格式和同配置分组
# =============================================================================
def integer(value):
    """普通数字按十进制，明确写 0x 的字段按十六进制，包括有符号十进制。"""
    text = str(value).strip()
    return int(text, 16 if text.lower().lstrip("+-").startswith("0x") else 10)


def image_group(rows):
    """只接收回读过的非零增益档。0 端点的器件行为没有在本方案中绑定。"""
    value = integer(rows["gain_code"])
    if not 1 <= value <= 1023:
        raise CalibrationError("gain_code 需为实际回读的 1..1023")
    return value


def parse_csv(path, extra):
    required = {"gain_code", "vin_diff_v", "code", *extra}
    with path.open(encoding="utf-8-sig", newline="") as stream:
        data = csv.DictReader(stream)
        names = data.fieldnames or []
        if len(names) != len(set(names)) or not required <= set(names):
            raise CalibrationError(f"CSV 需含独立列 {sorted(required)}")
        rows = list(data)
    if not rows:
        raise CalibrationError("CSV 没有采样记录")
    return rows


# =============================================================================
# 2. 实测增益码表和偏移修正
# =============================================================================
def gain_table(rows, *, vref_v, code_format):
    """每个 GAIN 使用两组已知直流、相同 OFFSET=0；返回各档相对单位档的斜率。"""
    groups = {}
    for row in rows:
        # 如果采集文件登记了 offset_lsb，则可直接查出量测期间混用的寄存器设置。
        if "offset_lsb" in row and integer(row["offset_lsb"]) != 0:
            raise CalibrationError("增益扫描各组应使用 OFFSET=0")
        groups.setdefault(image_group(row), []).append(row)
    if 512 not in groups or len(groups) < 2:
        raise CalibrationError("测量必须包括单位增益 0x200 和至少一个其他档")
    slopes = {}
    for gain, samples in sorted(groups.items()):
        fit = fit_dc([float(r["vin_diff_v"]) for r in samples],
                     [integer(r["code"]) for r in samples],
                     vref_v=vref_v, code_format=code_format)
        slopes[gain] = fit.forward_gain
    # 每个档独立反推出物理增益，以单位档归一可消去板级量程误差。
    # 保留离散已测档；不通过插值冒充未测的寄存器行为。
    table = [(code, slope / slopes[512]) for code, slope in slopes.items()]
    if any(not lo[1] < hi[1] for lo, hi in zip(table, table[1:])):
        raise CalibrationError("实测增益未随码严格递增，请检查回读、激励漂移和重复测量")
    return table


def offset_trim(rows, *, vref_v, code_format):
    """固定已选 GAIN，在同一已知电压测 OFFSET=0 与一个试调值，从输出差估算 OFFSET。"""
    if not math.isfinite(vref_v) or vref_v <= 0:
        raise CalibrationError("VREF 必须是有限正电压")
    gains = {image_group(row) for row in rows}
    levels = {float(row["vin_diff_v"]) for row in rows}
    if len(gains) != 1 or len(levels) != 1:
        raise CalibrationError("偏移复测须保持同一 GAIN 和同一已知直流输入")
    level = next(iter(levels))
    if not -vref_v < level < vref_v:
        raise CalibrationError("已知直流须在可用量程内")
    groups = {}
    for row in rows:
        offset = integer(row["offset_lsb"])
        if not -2048 <= offset <= 2047:
            raise CalibrationError("offset_lsb 是 -2048..2047 的有符号整数")
        groups.setdefault(offset, []).append(integer(row["code"]))
    if 0 not in groups or len(groups) != 2 or min(map(len, groups.values())) < 2:
        raise CalibrationError("需 OFFSET=0 和一个试调值，每组至少两笔")
    means = {}
    for offset, group in groups.items():
        codes = decode_codes(group, code_format)
        if np.any((codes == -(1 << 19)) | (codes == (1 << 19) - 1)):
            raise CalibrationError("偏移复测触及输出端点，请降低激励幅度")
        means[offset] = float(np.mean(codes))
    trial = next(o for o in groups if o != 0)
    sensitivity = (means[trial] - means[0]) / trial
    if not math.isfinite(sensitivity) or sensitivity <= 0:
        raise CalibrationError("测不出正的偏移响应，请核对输入或增加试调幅度")
    target = level / (2.0 * vref_v / (1 << 20))
    # 在已选增益档闭环量 offset，所以即便内部先加后乘，这个斜率也由实测决定。
    value = round((target - means[0]) / sensitivity)
    if not -2048 <= value <= 2047:
        raise CalibrationError("所需 OFFSET 超出 12 位范围")
    field = value & 0xfff
    return {"gain_code_held": next(iter(gains)), "offset_code": value, "offset_field": field,
            "reference_code": target, "zero_offset_mean": means[0], "trial_offset": trial,
            "trial_mean": means[trial], "codes_per_offset_lsb": sensitivity,
            "writes": {"0x25": field & 0xff, "0x26": field >> 8},
            "predicted_mean_error_codes": means[0] + sensitivity * value - target,
            "verification": "write and read back both bytes; repeat on new acquisitions"}


# =============================================================================
# 3. 命令行只生成建议值；设备回读和真实寄存器编程交给板端控制接口
# =============================================================================
def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("gain-table", "offset-trim"))
    parser.add_argument("--csv", type=Path, required=True)
    parser.add_argument("--vref-v", type=float, required=True)
    parser.add_argument("--code-format", default="signed20",
                        choices=("signed20","wire_twos20","offset_binary","spi24_sign_extended"))
    parser.add_argument("--output", type=Path, help="省略时打印到标准输出")
    args = parser.parse_args(argv)
    try:
        if args.output and args.csv.resolve() == args.output.resolve():
            raise CalibrationError("输出不能覆盖原始采集 CSV")
        if args.action == "gain-table":
            samples = parse_csv(args.csv, set())
            out = io.StringIO(newline="")
            writer = csv.writer(out, lineterminator="\n")
            writer.writerow(("gain_code", "effective_gain"))
            writer.writerows(gain_table(samples, vref_v=args.vref_v, code_format=args.code_format))
            text = out.getvalue()
        else:
            samples = parse_csv(args.csv, {"offset_lsb"})
            result = offset_trim(samples, vref_v=args.vref_v, code_format=args.code_format)
            text = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        if args.output:
            # 写到同目录临时文件后一次替换，求解或校验失败时不留下半张码表。
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=args.output.parent,
                                             delete=False) as tmp:
                tmp.write(text)
            os.replace(tmp.name, args.output)
        else:
            sys.stdout.write(text)
    except (ValueError, OSError, csv.Error, ArithmeticError) as error:
        parser.exit(2, f"{error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
