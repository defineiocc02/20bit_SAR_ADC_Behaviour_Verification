# =============================================================================
# 文件名     : system_cal.py
# 模块名     : system_cal
# 版本       : v1.0
# 功能       : 从已知差分电平或参考正弦估算通道仿射误差，按显式绑定生成
#              系统 OFFSET/GAIN 字节。
# 设计说明   :
#   * 测量模型为 raw = a * (VIN_DIFF / LSB) + b，修正模型为 y = g * raw + c。
#     捕获应使用中性 OFFSET=0、GAIN=512 和未经其他用户校正的 20 位结果。
#   * LSB = 2 * VREF / 2^20，以 V/code 表示。输入 VREF、差分峰值与直流
#     均为实测伏特，拟合与偏置以 ADC 码为单位。
#   * 正弦拟合调用现有 ADCToolbox；数字修正仍由现有 sys_error_corr 执行。
#     实测表按手册图 96 的乘前加法节点给出 OFFSET 初值。工程映射使用冻结
#     RTL 的乘后加法约定；增益绑定由调用者给定，结果只输出至 stdout。
# -----------------------------------------------------------------------------
# 变更记录
# -----------------------------------------------------------------------------
# 明确分离 Rev. B 图 96 的实测表路径和工程 RTL 的偏置顺序。
# =============================================================================

from __future__ import annotations

import argparse
import csv
import json
import math
import operator
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
from numpy.typing import ArrayLike, NDArray

# =============================================================================
# 1. 码制、物理量与中性参考
# =============================================================================
# [依据] OFFSET 与 GAIN 的字段宽度和地址见 AD4080 Rev. B 第 92、93 页。
# 原始位串的传输容器宽度独立于量化位宽，例如 SPI24 仍按 20 位定 LSB。
ADC_BITS = 20
SIGN_BIT = 1 << (ADC_BITS - 1)
WIRE_MASK = (1 << ADC_BITS) - 1
SPI24_MASK = (1 << 24) - 1
MIN_CODE = -SIGN_BIT
MAX_CODE = SIGN_BIT - 1
OFFSET_MIN = -(1 << 11)
OFFSET_MAX = (1 << 11) - 1
GAIN_UNITY = 1 << 9
GAIN_MAX = (1 << 10) - 1
CODE_FORMATS = ("signed20", "wire_twos20", "offset_binary", "spi24_sign_extended")


class CalibrationError(ValueError):
    """输入数据或寄存器映射不符合校准合同，调用者可据异常提示重新采集。"""


@dataclass(frozen=True)
class CalibrationEstimate:
    """正向斜率、逆斜率均无量纲，偏置和拟合残差以有符号码表示。"""

    method: str
    code_format: str
    vref_v: float
    lsb_v: float
    sample_count: int
    forward_gain: float
    forward_offset_codes: float
    correction_gain: float
    correction_offset_codes: float
    residual_rmse_codes: float
    residual_max_abs_codes: float
    details: dict[str, object]


@dataclass(frozen=True)
class RegisterPlan:
    """偏置为有符号 12 位整数，写入字节中的保留位均置零。"""

    mapping: str
    offset_code: int
    gain_code: int
    applied_gain: float
    k_gain: int | None
    shift: int | None
    writes: dict[str, int]


def _finite(value: float | str, label: str) -> float:
    if isinstance(value, (bool, np.bool_)):
        raise CalibrationError(f"{label} 必须为有限实数")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise CalibrationError(f"{label} 必须为有限实数") from exc
    if not math.isfinite(number):
        raise CalibrationError(f"{label} 必须为有限实数")
    return number


def _vector(values: ArrayLike, label: str) -> NDArray[np.float64]:
    try:
        array = np.asarray(values)
        # [边界] 只接收整数与实浮点，避免虚部或对象值被静默转换。
        if array.ndim != 1 or array.dtype.kind not in "iuf":
            raise ValueError
        with np.errstate(over="ignore", invalid="ignore"):
            vector = array.astype(np.float64, copy=False)
    except (TypeError, ValueError, OverflowError) as exc:
        raise CalibrationError(f"{label} 必须为一维实数序列") from exc
    if not np.all(np.isfinite(vector)):
        raise CalibrationError(f"{label} 包含 NaN 或无限值")
    return vector


def decode_codes(codes: ArrayLike, code_format: str = "signed20") -> NDArray[np.int64]:
    """将一维整数或整值浮点码转换为有符号 20 位码；量程错误抛出异常。

    线码以非负整数交付。SPI24 仅接受符号扩展后的 24 位容器。
    此函数保留合法轨端码，拟合入口另行拒绝无法判定截顶的采集。
    """
    if code_format not in CODE_FORMATS:
        raise CalibrationError(f"未知码制 {code_format}")
    raw = _vector(codes, "ADC 码")
    if code_format == "signed20":
        low, high = MIN_CODE, MAX_CODE
    else:
        low, high = 0, SPI24_MASK if code_format == "spi24_sign_extended" else WIRE_MASK
    # [范围] 先检查浮点是否精确代表范围内整数，再转型，避免超域截断或回绕。
    if np.any((raw < low) | (raw > high) | (raw != np.floor(raw))):
        raise CalibrationError(f"{code_format} 只接受范围 {low}…{high} 的整数码")
    packed = raw.astype(np.int64)
    if code_format == "signed20":
        return packed
    if code_format == "offset_binary":
        return packed - SIGN_BIT

    signed = ((packed & WIRE_MASK) ^ SIGN_BIT) - SIGN_BIT
    if code_format == "spi24_sign_extended" and np.any(packed != (signed & SPI24_MASK)):
        raise CalibrationError("SPI24 高四位不符合 20 位符号扩展")
    return signed


def _capture(codes: ArrayLike, code_format: str) -> NDArray[np.int64]:
    # [证据边界] 仅依靠数字轨端无法区分恰好命中与过载后饱和。保守地拒绝
    # 该批训练数据，并保留 decode_codes 对码制端点的透明转换。
    decoded = decode_codes(codes, code_format)
    if np.any((decoded == MIN_CODE) | (decoded == MAX_CODE)):
        raise CalibrationError("采集达到 20 位轨端，存在截顶可能，请降低输入后重新采集")
    return decoded


def _voltage_scale(vref_v: float) -> tuple[float, float]:
    vref = _finite(vref_v, "VREF")
    # 先除再用，避免对仍可表示的正 VREF 先乘二而溢出。
    lsb = vref / SIGN_BIT
    if lsb <= 0:
        raise CalibrationError("VREF 必须为正且可表示有效 LSB")
    return vref, lsb


# =============================================================================
# 2. 仿射估计与残差
# =============================================================================
def _estimate(
    method: str,
    code_format: str,
    vref: float,
    lsb: float,
    gain: float,
    bias: float,
    residual: NDArray[np.float64],
    details: dict[str, object],
) -> CalibrationEstimate:
    if not math.isfinite(gain) or gain <= 0:
        raise CalibrationError("通道增益必须可辨识且为正")
    # [反解] 原始零输入输出为 b，因此实际修正偏置是 -b/a。
    inverse_gain = _finite(1.0 / gain, "逆增益")
    correction_offset = _finite(-bias * inverse_gain, "逆偏置")
    # [单位] 统计的是正向模型相对于训练原码的残差，还未应用拟合的逆映射，
    # 因而它与留出校正误差、实际芯片寄存器量化误差各自有独立含义。
    return CalibrationEstimate(
        method=method,
        code_format=code_format,
        vref_v=vref,
        lsb_v=lsb,
        sample_count=int(residual.size),
        forward_gain=gain,
        forward_offset_codes=_finite(bias, "通道偏置"),
        correction_gain=inverse_gain,
        correction_offset_codes=correction_offset,
        residual_rmse_codes=_finite(np.sqrt(np.mean(np.square(residual))), "残差 RMS"),
        residual_max_abs_codes=_finite(np.max(np.abs(residual)), "最大绝对残差"),
        details=details,
    )


def fit_dc(
    vin_diff_v: ArrayLike,
    codes: ArrayLike,
    *,
    vref_v: float,
    code_format: str = "signed20",
) -> CalibrationEstimate:
    """两个已知差分电平（V）各至少重复两笔；行顺序任意。

    以同电平均值反解通道斜率和零点。残差统计包含两组的组内偏差。
    超域、截顶、两点均值反向或不可辨识时抛出 CalibrationError。
    """
    voltage = _vector(vin_diff_v, "差分参考电压")
    raw = _capture(codes, code_format)
    vref, lsb = _voltage_scale(vref_v)
    if voltage.size != raw.size:
        raise CalibrationError("参考电平与 ADC 码必须逐行配对")
    levels, counts = np.unique(voltage, return_counts=True)
    if levels.size != 2 or np.any(counts < 2):
        raise CalibrationError("两点 DC 需要恰好两个参考电平，每个至少采集两笔")
    if np.any(np.abs(levels) >= vref):
        raise CalibrationError("差分参考电压须位于开区间 (-VREF, +VREF)")

    # [单位] 参考值先转换成理想 ADC 码。以高低电压的均值判极性，和 CSV 行序无关。
    lower = voltage == levels[0]
    low_codes, high_codes = raw[lower], raw[~lower]
    mean_low, mean_high = float(np.mean(low_codes)), float(np.mean(high_codes))
    ideal_low, ideal_high = (float(level) / lsb for level in levels)
    if not ideal_high > ideal_low or not mean_high > mean_low:
        raise CalibrationError("两点 DC 的斜率为零、反向或不可辨识")
    gain = (mean_high - mean_low) / (ideal_high - ideal_low)
    bias = mean_low - gain * ideal_low
    # [统计] 每个电平恰好给斜率一项均值，组内样本数不同只改变各自均值
    # 的置信度。残差统计按样本计，用于暴露重复采样的波动。
    residual = raw - np.where(lower, mean_low, mean_high)
    level_details = [
        {
            "vin_diff_v": float(level),
            "sample_count": int(group.size),
            "mean_codes": mean,
            "std_codes": float(np.std(group, ddof=1)),
        }
        for level, group, mean in zip(levels, (low_codes, high_codes), (mean_low, mean_high))
    ]
    details = {"levels": level_details, "positive_mean_slope_verified": True}
    return _estimate("dc", code_format, vref, lsb, gain, bias, residual, details)


def fit_sine(
    codes: ArrayLike,
    *,
    vref_v: float,
    fs_hz: float,
    fin_hz: float,
    reference_peak_v: float,
    reference_dc_v: float,
    code_format: str = "signed20",
) -> CalibrationEstimate:
    """从均匀采样单音与已标定的差分峰值、DC（V）估算仿射参数。

    Fs、Fin 以 Hz 表示。相位自由，增益符号须已知为正；拟合幅值本身
    无法区分正极性与相差 π 的反相通道。无已知物理参考时只获得码域幅值。
    """
    raw = _capture(codes, code_format)
    vref, lsb = _voltage_scale(vref_v)
    fs = _finite(fs_hz, "Fs")
    fin = _finite(fin_hz, "Fin")
    peak = _finite(reference_peak_v, "参考正弦峰值")
    dc = _finite(reference_dc_v, "参考正弦 DC")
    if fs <= 0 or not 0 < fin < fs / 2:
        raise CalibrationError("频率须满足 0 < Fin < Fs/2")
    if peak <= 0 or peak >= vref - abs(dc):
        raise CalibrationError("正弦峰值须为正，且 DC±峰值必须严格处于差分输入范围")
    if raw.size < 4 or np.ptp(raw) == 0:
        raise CalibrationError("定频正弦至少需要四个非恒定样本")

    freq = fin / fs
    # [秩] 在 ADCToolbox 拟合前只检查设计矩阵的条件数。三个未知系数至少
    # 需要三列独立，最弱方向不足 sqrt(eps) 时数值结果不能可靠解释成校正。
    theta = (2 * np.pi * freq) * np.arange(raw.size, dtype=np.float64)
    design = np.column_stack((np.cos(theta), np.sin(theta), np.ones(raw.size)))
    singular = np.linalg.svd(design, compute_uv=False)
    if singular[-1] <= np.sqrt(np.finfo(np.float64).eps) * singular[0]:
        raise CalibrationError("定频设计矩阵秩退化或病态，请扩大有效相位覆盖")

    try:
        from adctoolbox import fit_sine_4param
    except ImportError as exc:
        raise CalibrationError("正弦拟合需要已安装 ADCToolbox") from exc
    # [定频] 基于已量取的 Fin/Fs，禁止频率优化拟合吸收时基误差。
    # ADCToolbox 返回的 amplitude 是峰值码数，和已测差分峰值同口径。
    fit = fit_sine_4param(raw, frequency_estimate=freq, max_iterations=0)
    amplitude = _finite(fit["amplitude"], "测得的拟合峰值")
    measured_dc = _finite(fit["dc_offset"], "测得的拟合 DC")
    if amplitude <= 0.5:
        raise CalibrationError("定频分量不足半码，无法辨识绝对增益")
    if measured_dc - amplitude <= MIN_CODE or measured_dc + amplitude >= MAX_CODE:
        raise CalibrationError("拟合正弦触及 20 位轨端，无法用该采集校准")

    # [绝对量] 拟合的 DC 含参考源本身的 DC。减掉其斜率项才能得到通道零输入偏置。
    ideal_amplitude = peak / lsb
    if ideal_amplitude <= 0:
        raise CalibrationError("参考峰值无法表示为有效的理想 ADC 码")
    gain = amplitude / ideal_amplitude
    bias = measured_dc - gain * (dc / lsb)
    details = {
        "fs_hz": fs,
        "fin_hz": fin,
        "reference_peak_v": peak,
        "reference_dc_v": dc,
        "fitted_amplitude_codes": amplitude,
        "fitted_dc_codes": measured_dc,
        "fitted_phase_rad": _finite(fit["phase"], "拟合相位"),
        "positive_gain_assumed": True,
    }
    return _estimate("sine", code_format, vref, lsb, gain, bias, fit["residuals"], details)


# =============================================================================
# 3. 显式增益绑定及寄存器字节
# =============================================================================
def _bounded_int(value: int, low: int, high: int, label: str) -> int:
    try:
        integer = operator.index(value)
    except TypeError as exc:
        raise CalibrationError(f"{label} 必须为整数") from exc
    if isinstance(value, (bool, np.bool_)) or not low <= integer <= high:
        raise CalibrationError(f"{label} 超出 {low}…{high}")
    return integer


def quantize_registers(
    estimate: CalibrationEstimate,
    *,
    k_gain: int | None = None,
    shift: int | None = None,
    gain_table: Mapping[int, float] | None = None,
) -> RegisterPlan | None:
    """以显式 K_GAIN/SHIFT 或相对中性码的实测增益表选码。

    返回 0x25～0x28 的低/高字节，不访问总线。未提供映射时返回 None。
    最近增益按实测或显式算术表选择，平局优先靠近单位码；超出可写域报错。
    工程 RTL 的偏置按已量化增益重算；实测表按图 96 的乘前节点给初值，
    假定每寄存器步为一输入 ADC 码。器件最终 OFFSET 另经选定档的灵敏度复测。
    """
    if gain_table is not None:
        if k_gain is not None or shift is not None:
            raise CalibrationError("增益表与 K_GAIN/SHIFT 必须择一")
        mapping = "measured_gain_table"
        # [实测映射] 表值为工况相同的有效倍数，相对本器件 GAIN=512 斜率
        # 归一。若当成绝对通道斜率，会把待去除的模拟增益误差重复引入。
        gains = {
            _bounded_int(code, 1, GAIN_MAX, "增益表码"): _finite(gain, "增益表系数")
            for code, gain in gain_table.items()
        }
        if gains.get(GAIN_UNITY) != 1.0 or any(gain <= 0 for gain in gains.values()):
            raise CalibrationError("增益表必须以 512:1.0 为基准且所有系数为正")
    elif k_gain is None and shift is None:
        return None
    else:
        if k_gain is None or shift is None:
            raise CalibrationError("K_GAIN 与 SHIFT 必须同时提供")
        k_gain = _bounded_int(k_gain, 1, 1023, "K_GAIN")
        shift = _bounded_int(shift, 1, 30, "SHIFT")
        mapping = "rtl_k_gain_shift"
        # [数值合同] 原 RTL 对增益增量做定点舍入；这里选系数，不再实现样本处理。
        gains = {
            code: 1.0 + (code - GAIN_UNITY) * k_gain / (1 << shift)
            for code in range(1, GAIN_MAX + 1)
        }
        gains = {code: gain for code, gain in gains.items() if gain > 0}

    target = _finite(estimate.correction_gain, "目标逆增益")
    if not min(gains.values()) <= target <= max(gains.values()):
        raise CalibrationError("目标逆增益超出绑定的可写增益范围")
    gain_code = min(
        gains,
        key=lambda code: (abs(gains[code] - target), abs(code - GAIN_UNITY), code),
    )
    applied_gain = gains[gain_code]
    # [顺序] 冻结 RTL 是 y=g*x+O，乘后 O 抵消 g*b；Rev. B 图 96 画出
    # y=g*(x+d)，乘前 d 直接抵消 b。实测增益表只覆盖 g，d 仍是名义初值。
    bias_scale = 1.0 if gain_table is not None else applied_gain
    offset = _finite(-bias_scale * estimate.forward_offset_codes, "所需 OFFSET")
    # [范围] 真实目标超出端点时拒绝，即使数学舍入碰巧会落回边界码。
    if not OFFSET_MIN <= offset <= OFFSET_MAX:
        raise CalibrationError(f"所需 OFFSET 超出有符号 12 位范围 {OFFSET_MIN}…{OFFSET_MAX}")
    # [量化] 用最邻近整数，对半码选较大整数，符号扩展到物理 OFFSET 位域
    # 内终止，字节上方保留位不随负号扩展。
    offset_code = math.floor(offset + 0.5)
    offset_word = offset_code & 0xFFF
    return RegisterPlan(
        mapping=mapping,
        offset_code=offset_code,
        gain_code=gain_code,
        applied_gain=applied_gain,
        k_gain=k_gain,
        shift=shift,
        writes={
            "0x25": offset_word & 0xFF,
            "0x26": offset_word >> 8,
            "0x27": gain_code & 0xFF,
            "0x28": gain_code >> 8,
        },
    )


# =============================================================================
# 4. CSV 和可独立运行的命令行
# =============================================================================
# [采集] 正弦输入文件的行序就是采样顺序，时间与源相位由采集者确认。
# BOM 兼容仪表导出；无名多余列通常代表行分隔错位，输入应报错。
def _read_rows(path: Path, columns: tuple[str, ...]) -> list[tuple[str, ...]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream, strict=True)
        header = reader.fieldnames or []
        if len(header) != len(set(header)) or not set(columns).issubset(header):
            raise CalibrationError(f"CSV 需要不重复的标题及列 {', '.join(columns)}")
        rows = []
        for row in reader:
            # 允许时间、序号等额外命名列，必需字段须完整。
            if None in row or any(not row.get(name) or not row[name].strip() for name in columns):
                raise CalibrationError(f"CSV 第 {reader.line_num} 行列数或数值字段不完整")
            rows.append(tuple(row[name].strip() for name in columns))
    return rows


def _code_token(token: str) -> int:
    radix = 16 if token.lstrip("+-").lower().startswith("0x") else 10
    try:
        return int(token, radix)
    except ValueError as exc:
        raise CalibrationError(f"码值 {token!r} 需要十进制整数或 0x 十六进制") from exc


def _parse_gain_table(path: Path) -> dict[int, float]:
    gains = {}
    for code_text, gain_text in _read_rows(path, ("gain_code", "effective_gain")):
        code = _code_token(code_text)
        if code in gains:
            raise CalibrationError(f"增益表重复码 {code}")
        gains[code] = _finite(gain_text, "增益表系数")
    return gains


def main(argv: Sequence[str] | None = None) -> int:
    """从文件估算后在 stdout 输出 JSON；数据或参数有误时退出码为 2。"""
    parser = argparse.ArgumentParser(description="20 位 ADC 系统 OFFSET/GAIN 主机校准")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--csv", type=Path, required=True, help="已采集样本 CSV")
    common.add_argument("--vref-v", type=float, required=True, help="实测参考电压，单位 V")
    common.add_argument("--code-format", choices=CODE_FORMATS, default="signed20")
    common.add_argument("--k-gain", type=int, help="已绑定的 sys_error_corr K_GAIN")
    common.add_argument("--shift", type=int, help="已绑定的 sys_error_corr SHIFT")
    common.add_argument("--gain-table", type=Path, help="gain_code,effective_gain 表")
    common.add_argument("--max-rmse-codes", type=float, help="可选拟合残差 RMS 上限，单位码")
    methods = parser.add_subparsers(dest="method", required=True)
    methods.add_parser("dc", parents=[common], help="vin_diff_v,code 两点差分 DC")
    sine = methods.add_parser("sine", parents=[common], help="code 列定频正弦")
    sine.add_argument("--fs-hz", type=float, required=True)
    sine.add_argument("--fin-hz", type=float, required=True)
    sine.add_argument("--reference-peak-v", type=float, required=True, help="差分峰值 V")
    sine.add_argument("--reference-dc-v", type=float, required=True, help="差分 DC 电压 V")
    args = parser.parse_args(argv)

    try:
        if args.method == "dc":
            rows = _read_rows(args.csv, ("vin_diff_v", "code"))
            estimate = fit_dc(
                [_finite(row[0], "差分参考电压") for row in rows],
                [_code_token(row[1]) for row in rows],
                vref_v=args.vref_v,
                code_format=args.code_format,
            )
        else:
            rows = _read_rows(args.csv, ("code",))
            estimate = fit_sine(
                [_code_token(row[0]) for row in rows],
                vref_v=args.vref_v,
                fs_hz=args.fs_hz,
                fin_hz=args.fin_hz,
                reference_peak_v=args.reference_peak_v,
                reference_dc_v=args.reference_dc_v,
                code_format=args.code_format,
            )
        if args.max_rmse_codes is not None:
            # [验收] RMS 门限取决于仪表和目标用法，调用方显式选择。
            # 超限退出先于生成写入计划，宿主不会收到可误用的半份 JSON。
            ceiling = _finite(args.max_rmse_codes, "拟合 RMS 上限")
            if ceiling < 0 or estimate.residual_rmse_codes > ceiling:
                raise CalibrationError("拟合 RMS 超出允许上限")
        gain_table = _parse_gain_table(args.gain_table) if args.gain_table is not None else None
        registers = quantize_registers(
            estimate, k_gain=args.k_gain, shift=args.shift, gain_table=gain_table
        )
    except (CalibrationError, OSError, UnicodeError, csv.Error, np.linalg.LinAlgError) as exc:
        parser.error(str(exc))

    # [交付] 中性采数是假设，字节为待审方案，向同一地址的 SPI 顺序由宿主负责。
    result = {
        "capture_assumed_neutral": {"offset_code": 0, "gain_code": GAIN_UNITY},
        "estimate": asdict(estimate),
        "registers": asdict(registers) if registers is not None else None,
    }
    print(json.dumps(result, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
