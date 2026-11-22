# =============================================================================
# 文件名     : generate_sinc5_comp.py
# 模块名     : generate_sinc5_comp
# 版本       : v1.0
# 功能       : 生成抽取 SINC5 后接 ×2 FIR 的独立工程系数和量化频响回执。
# 设计说明   :
#   * 已有 SINC5 按 N 抽取且直流归一。设计约束作用于 SINC5 与 FIR 的
#     复合幅度，频率 u=N*f/Fs 为 FIR 输入归一频率，输出采样率 Fs/(2N)。
#   * 表 28 各档取打印中心，带宽条件针对准确的 −3.000 dB。p70 的图面
#     用于划定通阻带，不提供厂商系数、测量误差或芯片的容差规范。
#   * 低频正实的线性相位对称 FIR 保留图 106 的通带纹波形状与图 107 的
#     代表阻带峰，以未参与优化的交错频点复验。量化中心补偿总和误差。
# -----------------------------------------------------------------------------
# 变更记录
# -----------------------------------------------------------------------------
# 加入原件图 106/107 联合约束和独立交错留出。
# =============================================================================

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import scipy
from scipy.optimize import brentq, linprog

# =============================================================================
# 1. 原件数值和工程约束
# =============================================================================
HERE = Path(__file__).resolve().parent
DEFAULT_TARGETS = (
    HERE.parents[1] / "ad4080_peripherals/interfaces/dsp_review/manual_filter_targets.csv"
)
DEFAULT_FIG106 = HERE / "sinc5_comp_fig106_anchors.csv"
DEFAULT_LUT = HERE.parent / "rtl/sinc5_comp_coeff_lut.svh"
DEFAULT_JSON = HERE / "sinc5_comp_coefficients.json"
MANUAL_SHA256 = "f08f2586dc82d5f583b42071b3570c06f1fe736d3053e15a559a47ed2841cbc1"
FIR_TAPS = 35
FRAC_BITS = 22
COEF_WIDTH = 24
SCALE = 1 << FRAC_BITS
PASS_END_U = 0.176130302  # 图 106 N=2 首跌 −0.1 dB 的 f/Fs 读数乘二

# [余量] 优化网格的通带边界稍窄于最终 ±0.1 dB 门限，给系数量化和
# 网格间峰值留出裕量。保持图 106 的近似单侧波纹，也固定 DC 为一。
PASS_TRAIN_DB = (-0.098, 0.030)

# 图 107 的代表值用 f/Fout，0.57～0.75 换成中间速率的 u 除以二。
FIG107_WINDOW_U = (0.57 / 2, 0.75 / 2)
FIG107_PEAK_ERROR_DB = 0.10
SOLVE_GUARD_DB = 0.02  # 为独立密扫的网格间峰及 Q22 量化预留

# [原件] 图 105 N=2，稳定阻带自 f/Fs=.13 起。图面 f≈.125167 有
# 垂直段，Nyquist 邻近作为过渡。第二至第五个窗口阈值由向量峰保守取整。
# u>=1 的额外像频窗口仅用于 N>2 的独立工程守门，图 105 未覆盖。
STOP_WINDOWS = (
    (0.25, 0.26, -33.10, "near_output_nyquist"),
    (0.26, 0.40, -42.85, "figure105_stable_1"),
    (0.40, 0.60, -48.99, "figure105_stable_2"),
    (0.60, 0.75, -64.75, "figure105_stable_3"),
    (0.75, 1.00, -51.00, "figure105_cic_image"),
    (1.00, 2.00, -45.00, "engineering_extra_images"),
)


@dataclass(frozen=True)
class Mode:
    """表 28 一行。频率和打印量化半单位都以原 ADC 的 Fs 归一。"""

    rate: int
    n: int
    target: float
    half_print_unit: float


def read_modes(path: Path) -> list[Mode]:
    """自独立的原件表格输入读取全部八档，并核槽号和总倍率。"""
    # 打印表值有四位小数，末位舍入区间只用于审计显示的一致性。
    # 源文件同时含其他滤波器和图面中心线，拒绝将其误作 COMP 目标。
    with path.open(newline="", encoding="utf-8") as stream:
        rows = [row for row in csv.DictReader(stream)
                if row["source_kind"] == "printed_table" and row["filter"] == "sinc5_comp"
                and row["metric"] == "bw_3db_over_input_fs"]
    result = []
    for row in rows:
        rate = int(row["sinc_dec_rate_bits"], 2)
        n = int(row["sinc_stage_decimation_n"])
        if n != 2 << rate or int(row["total_decimation_d"]) != 2 * n:
            raise ValueError(f"Table 28 stage rate mismatch for rate {rate}")
        result.append(Mode(rate, n, float(row["value"]), float(row["half_print_unit_assumed"])))
    result.sort(key=lambda mode: mode.rate)
    if [mode.rate for mode in result] != list(range(8)):
        raise ValueError("Table 28 must contain precisely the eight COMP profiles")
    return result


def read_fig106_anchors(path: Path) -> dict[str, np.ndarray]:
    """图 106 独立抽样。频率以 f/Fs 表示，三列幅值为中心、下界、上界 dB。"""
    result: dict[str, list[tuple[float, ...]]] = {"train": [], "holdout": []}
    with path.open(newline="", encoding="utf-8") as stream:
        for row in csv.DictReader(stream):
            result[row["split"]].append(tuple(float(row[name]) for name in
                ("f_over_fs", "center_db", "graphic_lower_db", "graphic_upper_db")))
    arrays = {split: np.asarray(sorted(rows)) for split, rows in result.items()}
    # 偶千分位用于求解，奇千分位只用于最终验收，保留跨波纹的对应关系。
    for split, offset in (("train", 2), ("holdout", 1)):
        band = arrays[split]
        if (band.shape != (46, 4) or not np.all(np.isfinite(band))
                or not np.allclose(band[:, 0], np.arange(offset, 93, 2) / 1000,
                                   atol=1e-12, rtol=0.0)
                or not np.all((band[:, 2] < band[:, 1]) & (band[:, 1] < band[:, 3]))):
            raise ValueError(f"invalid interleaved Fig. 106 subset {split}")
    return arrays


def read_fig107_peaks(path: Path) -> dict[int, float]:
    """图 107 的八档代表阻带峰。本曲线数据选定窗口的峰位均为其左边界。"""
    result = {}
    with path.open(newline="", encoding="utf-8") as stream:
        for row in csv.DictReader(stream):
            if row["source_id"] != "Fig. 107" or row["metric"] != "max_gain_db_in_band":
                continue
            n = int(row["sinc_stage_decimation_n"])
            if abs(n * float(row["x_over_fs"]) - FIG107_WINDOW_U[0]) > 1e-7 or n in result:
                raise ValueError(f"unexpected Fig. 107 peak position for N={n}")
            result[n] = float(row["value"])
    if sorted(result) != [2 << i for i in range(8)] or not np.all(
        np.isfinite(list(result.values()))
    ):
        raise ValueError("missing finite Fig. 107 reference peaks")
    return result


def db(amplitude):
    # 零点以下只用于有限显示；停止带峰值和精确 −3 dB 解不受该地板影响。
    return 20.0 * np.log10(np.maximum(np.abs(amplitude), 1e-300))


def response_basis(n: int, u, order: int) -> np.ndarray:
    """以 u=f/(Fs/N) 为频率，返回复合相位剥离的余弦基底。"""
    u = np.atleast_1d(u)
    cic = np.abs(np.sinc(u) / np.sinc(u / n)) ** 5
    return cic[:, None] * np.cos(2 * np.pi * u[:, None] * np.arange(order + 1))


def active_windows(n: int):
    # 同一 FIR 频响会跨 Nyquist 周期复制；计算继续到原始 ADC 的 Fs/2。
    for lo, hi, target, label in STOP_WINDOWS:
        if lo < n / 2:
            yield lo, min(hi, n / 2), target, label


# =============================================================================
# 2. 余弦对称解、量化与全带复验
# =============================================================================
def solve_coefficients(mode: Mode, peak107_db: float, fig106_train: np.ndarray,
                       taps: int = FIR_TAPS) -> tuple[np.ndarray, float, int]:
    """变元为中心、两倍旁瓣系数及通带重点形状的最大偏差。"""
    if taps % 2 != 1:
        raise ValueError("Type-I symmetric FIR needs odd tap count")
    half = taps // 2
    pass_matrix = response_basis(mode.n, np.linspace(0, PASS_END_U, 401), half)
    pass_rows = np.c_[pass_matrix, np.zeros(len(pass_matrix))]
    rows = [pass_rows, -pass_rows]
    limits = [np.full(len(pass_rows), 10 ** (PASS_TRAIN_DB[1] / 20)),
              np.full(len(pass_rows), -10 ** (PASS_TRAIN_DB[0] / 20))]

    # [阻带] 各峰约束幅值的正负两侧，包含图 105 的第二像频。图 107
    # 再约束实际相同频窗，并设一个明确幅值锚点，避免用更深的峰冒充同一轮廓。
    windows = list(active_windows(mode.n))
    windows.append((*FIG107_WINDOW_U, peak107_db + FIG107_PEAK_ERROR_DB, "figure107_peak"))
    for lo, hi, cap, _ in windows:
        stop_matrix = response_basis(mode.n, np.linspace(lo, hi, 601), half)
        zeros = np.zeros(len(stop_matrix))
        limit = np.full(len(stop_matrix), 10 ** ((cap - SOLVE_GUARD_DB) / 20))
        rows.extend((np.c_[stop_matrix, zeros], np.c_[-stop_matrix, zeros]))
        limits.extend((limit, limit))

    # [首交点] 限制复合响应通带之后到最终 Nyquist 之间单调下降，
    # 避免定一点 −3 dB 却在其前已穿越。频率取 Table 28 的打印中心。
    transition = response_basis(mode.n, np.linspace(PASS_END_U, 0.25, 301), half)
    differences = np.diff(transition, axis=0)
    rows.append(np.c_[differences, np.zeros(len(differences))])
    limits.append(np.zeros(len(differences)))
    # [形状] N2 用原图训练点的非对称墨宽作幅值归一，其余档没有
    # 单独细刻度通带图，故在已声明窗口内做单位增益极小极大。
    if mode.n == 2:
        shape = response_basis(mode.n, 2 * fig106_train[:, 0], half)
        center = 10 ** (fig106_train[:, 1] / 20)
        down = center - 10 ** (fig106_train[:, 2] / 20)
        up = 10 ** (fig106_train[:, 3] / 20) - center
    else:
        shape = pass_matrix
        center = up = down = np.ones(len(shape))
    rows.extend((np.c_[shape, -up], np.c_[-shape, -down]))
    limits.extend((center, -center))

    # 图只给幅度。分别探索图 107 锚点的两种实值符号并选择目标较小者，
    # 这不推定原厂相位。DC 与表 28 的 −3.000 dB 仍使用硬等式。
    anchor = np.c_[response_basis(mode.n, [0, mode.n * mode.target, FIG107_WINDOW_U[0]], half),
                   np.zeros(3)]
    attempts = []
    for sign in (1, -1):
        result = linprog(
            np.r_[np.zeros(half + 1), 1], A_ub=np.vstack(rows), b_ub=np.concatenate(limits),
            A_eq=anchor, b_eq=[1.0, 10 ** (-3.0 / 20), sign * 10 ** (peak107_db / 20)],
            bounds=[(None, None)] * (half + 1) + [(0, None)], method="highs",
        )
        if result.success:
            attempts.append((result, sign))
    # 即使幅值图一致，两个实符号也对应不同的过零关系，不能直接对两解平均。
    if not attempts:
        raise RuntimeError(f"N={mode.n}, taps={taps}: both Fig. 107 phase signs infeasible")
    result, sign = min(attempts, key=lambda pair: pair[0].fun)

    cosine = result.x[:-1]
    h = np.r_[cosine[:0:-1] / 2, cosine[0], cosine[1:] / 2]
    coefficients = np.rint(h * SCALE).astype(np.int64)
    # [DC] 只改中心，保持镜像对称。对恒定整数样本，滤波器稳态恰为一倍。
    coefficients[half] += SCALE - int(coefficients.sum())
    if (not np.array_equal(coefficients, coefficients[::-1]) or coefficients.sum() != SCALE
            or np.max(np.abs(coefficients)) >= 1 << (COEF_WIDTH - 1)):
        raise RuntimeError(f"quantized coefficients exceed declared format for N={mode.n}")
    return coefficients, float(result.x[-1]), sign


def quantized_response(n: int, coefficients: np.ndarray, u) -> np.ndarray:
    """以量化系数计算单音幅值；输入可在原 ADC Nyquist 范围内跨多像频。"""
    half = coefficients.size // 2
    cosine = np.r_[coefficients[half], 2 * coefficients[half + 1:]] / SCALE
    return np.abs(response_basis(n, u, half) @ cosine)


def first_crossing(n: int, coefficients: np.ndarray, target_db: float) -> float:
    """独立密网格找首交点，夹住后精求；返回频率单位为 f/Fs。"""
    u = np.linspace(0, 0.25, 32769)
    threshold = 10 ** (target_db / 20)
    below = np.flatnonzero(quantized_response(n, coefficients, u) <= threshold)
    if below.size == 0 or below[0] == 0:
        raise RuntimeError(f"missing {target_db:.3f} dB crossing for N={n}")
    last = below[0]
    crossed = brentq(lambda v: quantized_response(n, coefficients, v)[0] - threshold,
                     u[last - 1], u[last], xtol=1e-13)
    return float(crossed / n)


def verify_and_report(mode: Mode, coefficients: np.ndarray, minimax: float,
                      sign: int, peak107_db: float) -> dict:
    """以比求解密得多的频率列验证，不把训练等式直接记作测量值。"""
    n = mode.n
    pass_db = db(quantized_response(n, coefficients, np.linspace(0, PASS_END_U, 32001)))
    pass_min, pass_max = float(pass_db.min()), float(pass_db.max())
    if pass_min < -0.1 or pass_max > 0.1:
        raise RuntimeError(f"N={n} failed quantized passband: {pass_min}, {pass_max}")
    f3 = first_crossing(n, coefficients, -3.0)
    if abs(f3 - mode.target) >= mode.half_print_unit:
        raise RuntimeError(f"N={n} bandwidth {f3} disagrees with Table 28 {mode.target}")

    stops = []
    for lo, hi, limit, label in active_windows(n):
        u = np.linspace(lo, hi, 10001)
        gains = db(quantized_response(n, coefficients, u))
        index = int(np.argmax(gains))
        peak = float(gains[index])
        if peak > limit:
            raise RuntimeError(f"N={n} failed {label}: {peak:.4f} > {limit:.4f}")
        stops.append({"window": label, "range_u": [lo, hi], "limit_db": limit,
                      "peak_db": peak, "peak_frequency_over_fs": float(u[index] / n)})

    # [同窗复验] 不只检查衰减至少达标，同时检查代表峰没有比图面中心深很多。
    fig107_u = np.linspace(*FIG107_WINDOW_U, 12001)
    fig107_peak = float(db(quantized_response(n, coefficients, fig107_u)).max())
    if abs(fig107_peak - peak107_db) > FIG107_PEAK_ERROR_DB:
        raise RuntimeError(f"N={n} Fig. 107 corresponding window peak mismatch: {fig107_peak}")

    # [全频界] 对尚未密扫的 u>=2，|sinc(u)/sinc(u/N)| <= 1/(2u)，
    # 由截断频率及 FIR 的 L1 上界给整个 ADC Nyquist 内剩余尾段限幅。
    l1_q = int(np.abs(coefficients).sum())
    tail_db = float(db(l1_q / SCALE * (1 / 4) ** 5)) if n > 4 else None
    if tail_db is not None and tail_db > -45.0:
        raise RuntimeError(f"N={n} tail envelope exceeds -45 dB")
    # 按 SINC5 输出已限幅的 signed20 间接界定原始累加范围，含 FIR 半步。
    accumulator_max = (1 << 19) * l1_q + SCALE // 2
    return {
        "rate": mode.rate, "cic_decimation": n, "total_decimation": 2 * n,
        "coefficients_q": coefficients.tolist(),
        "half_coefficients_q": coefficients[:coefficients.size // 2 + 1].tolist(),
        "pass_min_db": pass_min, "pass_max_db": pass_max, "pass_end_over_fs": PASS_END_U / n,
        "first_minus_pt1_db_over_fs": first_crossing(n, coefficients, -0.1),
        "f3_target_over_fs": mode.target, "f3_over_fs": f3,
        "f3_error_over_fs": f3 - mode.target, "table_half_print_unit": mode.half_print_unit,
        "minimax_float": minimax, "fig107_anchor_sign": sign, "stopbands": stops,
        "fig107_same_window_peak_db": fig107_peak, "fig107_centerline_peak_db": peak107_db,
        "u_ge_2_tail_bound_db": tail_db, "dc_sum_q": int(coefficients.sum()),
        "coefficient_l1": l1_q / SCALE,
        "signed_min_accumulator_bits": accumulator_max.bit_length() + 1,
        "impulse_length_input_samples": 5 * (n - 1) + 1 + (coefficients.size - 1) * n,
    }


# =============================================================================
# 3. 可读的合成半表与手册留出点
# =============================================================================
def render_lut(modes: list[dict]) -> str:
    """输出模块作用域 include；调用者传解码后的 rate 和对称 tap 对号。"""
    lines = [
        "// =============================================================================",
        "// 文件名     : sinc5_comp_coeff_lut.svh",
        "// 模块名     : sinc5_comp 的 FIR 纯函数查表",
        "// 版本       : v1.0",
        "// 功能       : 提供已量化对称 FIR 的一半系数，另一半由消费端配对。",
        "// 设计说明   : 由 design/generate_sinc5_comp.py 生成，Q22，每档 DC 总和恰 2^22。",
        "//              rate 决定 CIC 的 N，tap 0 为两端，tap 17 是中心。",
        "//              工程重设计，源指标参见 AD4080 Rev. B 表 28、图 105～107。",
        "// 变更记录   : 初始版本，暂无变更记录。",
        "// =============================================================================",
        f"localparam int PMODE_COMP_TAPS = {FIR_TAPS};",
        f"localparam int PMODE_COMP_COEF_W = {COEF_WIDTH};",
        f"localparam int PMODE_COMP_COEF_FRAC = {FRAC_BITS};",
        "",
        "function automatic logic signed [PMODE_COMP_COEF_W-1:0] sinc5_comp_coeff(",
        "    input logic [2:0] rate,",
        "    input int tap",
        ");",
        "    case (rate)",
    ]
    for mode in modes:
        lines.append(f"        // CIC N={mode['cic_decimation']}，总抽取 D={mode['total_decimation']}")
        lines.extend((f"        3'd{mode['rate']}: begin", "            case (tap)"))
        for tap, coefficient in enumerate(mode["half_coefficients_q"]):
            literal = f"{'-' if coefficient < 0 else ''}{COEF_WIDTH}'sd{abs(coefficient)}"
            other = FIR_TAPS - 1 - tap
            samples = f"中心 x[n-{tap}]" if tap == other else f"配对 x[n-{tap}] 和 x[n-{other}]"
            value = f"{tap:2d}: sinc5_comp_coeff = {literal:>13};"
            lines.append(f"                {value}  // {samples}")
        lines.extend(("                default: sinc5_comp_coeff = '0;", "            endcase",
                      "        end"))
    lines.extend(("        default: sinc5_comp_coeff = '0;", "    endcase", "endfunction", ""))
    return "\n".join(lines)


def figure_comparison(row: dict, design: float) -> dict:
    """保留相同单位的图面中心线、工程响应、两者差及原读图误差描述。"""
    nominal = float(row["value"])
    return {"figure": row["source_id"], "metric": row["metric"], "unit": row["unit"],
            "n": int(row["sinc_stage_decimation_n"]), "figure_centerline": nominal,
            "design": design, "delta": design - nominal,
            "figure_read_uncertainty_abs": float(row["read_uncertainty_abs"]),
            "figure_read_context": row["note"]}


def manual_holdout(modes: list[dict], targets: Path) -> dict:
    """读取独立数字化原图。求解用通带端点与分段边界，留出使用点值。"""
    profiles = {record["cic_decimation"]: record for record in modes}
    profiles_q = {n: np.array(record["coefficients_q"], dtype=np.int64)
                  for n, record in profiles.items()}
    with targets.open(newline="", encoding="utf-8") as stream:
        rows = [row for row in csv.DictReader(stream)
                if row["source_kind"] == "vector_figure" and row["filter"] == "sinc5_comp"]

    pass_details = []
    fig105_reference = []
    fig107_bandwidth = []
    fig107_stop = []
    edge_observations = []
    # [比较] 图 106 此处为附加探针。独立留出用明确分组的交错频点；
    # 图 105 峰值和图 107 同窗已作为设计参考，不当作留出样本。
    for row in rows:
        n = int(row["sinc_stage_decimation_n"])
        figure, metric = row["source_id"], row["metric"]
        if figure == "Fig. 106" and metric == "gain_db_at_f":
            f = float(row["x_over_fs"])
            gain = float(db(quantized_response(n, profiles_q[n], n * f)[0]))
            pass_details.append({"f_over_fs": f, **figure_comparison(row, gain)})
        elif figure == "Fig. 106" and metric == "first_minus0p1_over_fs":
            edge = profiles[n]["first_minus_pt1_db_over_fs"]
            edge_observations.append(figure_comparison(row, edge))
        elif figure == "Fig. 105" and metric == "max_gain_db_in_band":
            peak_u = n * float(row["x_over_fs"])
            window = next(item for item in profiles[n]["stopbands"][1:5]
                          if item["range_u"][0] <= peak_u < item["range_u"][1])
            fig105_reference.append({"window_u": window["range_u"],
                                     **figure_comparison(row, window["peak_db"])})
        elif figure == "Fig. 105" and metric == "bw_3db_over_input_fs":
            edge_observations.append(figure_comparison(row, profiles[n]["f3_over_fs"]))
        elif figure == "Fig. 107" and metric == "bw_3db_over_input_fs":
            fig107_bandwidth.append(figure_comparison(row, profiles[n]["f3_over_fs"]))
        elif figure == "Fig. 107" and metric == "max_gain_db_in_band":
            fig107_stop.append(figure_comparison(row, profiles[n]["fig107_same_window_peak_db"]))

    if (len(pass_details), len(fig105_reference), len(fig107_bandwidth), len(fig107_stop),
            len(edge_observations)) != (9, 4, 8, 8, 2):
        raise ValueError("independent vector anchors for Figures 105–107 are missing")
    return {
        "scope": "vector centerlines and graphic read bounds, "
                 "neither silicon measurements nor tolerance",
        "figure106_passband_and_transition": pass_details,
        "figure105_constraint_reference_peaks": fig105_reference,
        "selected_edges": edge_observations,
        "figure107_minus_3db_holdout": fig107_bandwidth,
        "figure107_tuned_stopband_ratio_f_to_output_fs": [0.57, 0.75],
        "figure107_tuned_stopband_comparison": fig107_stop,
    }


def verify_fig106_shape(coefficients: np.ndarray, anchors: dict[str, np.ndarray]) -> dict:
    """在工程留出频点检查图墨包络，错误比例按各方向的不对称墨宽计算。"""
    result = {}
    for split, reference in anchors.items():
        actual = db(quantized_response(2, coefficients, 2 * reference[:, 0]))
        f, center, lower, upper = reference.T
        error = actual - center
        # 原图横向墨宽会经局部斜率转成不对称幅值范围，分别按实际超差方向归一。
        ratios = np.where(error > 0, error / (upper - center), -error / (center - lower))
        worst = float(ratios.max())
        if worst > 1.0:
            raise RuntimeError(f"Fig. 106 {split} exceeded its graphic envelope by ratio {worst}")
        result[split] = {
            "count": len(f), "max_abs_error_db": float(np.abs(error).max()),
            "rms_error_db": float(np.sqrt(np.mean(error**2))), "max_ink_ratio": worst,
            "points": [{"f_over_fs": float(freq), "figure_db": float(ref), "design_db": float(est),
                        "graphic_lower_db": float(lo), "graphic_upper_db": float(hi)}
                       for freq, ref, est, lo, hi in zip(f, center, actual, lower, upper)],
        }
    return result


def main() -> None:
    """按固定表格和声明的验证门槛生成回执及半表，失败时不覆写既有结果。"""
    cli = argparse.ArgumentParser(description=__doc__ or "SINC5 compensation design")
    cli.add_argument("--targets", type=Path, default=DEFAULT_TARGETS)
    cli.add_argument("--fig106-anchors", type=Path, default=DEFAULT_FIG106)
    cli.add_argument("--out-json", type=Path, default=DEFAULT_JSON)
    cli.add_argument("--out-svh", type=Path, default=DEFAULT_LUT)
    args = cli.parse_args()
    anchors = read_fig106_anchors(args.fig106_anchors)
    stop107 = read_fig107_peaks(args.targets)
    # 多档全部完成密扫及图形留出才写出表，避免上一档或部分通过覆盖已知结果。
    records = []
    for mode in read_modes(args.targets):
        coefficients, factor, sign = solve_coefficients(mode, stop107[mode.n], anchors["train"])
        measurements = verify_and_report(mode, coefficients, factor, sign, stop107[mode.n])
        records.append(measurements)
        print(f"N={mode.n:3d}: FIR Q{FRAC_BITS}, -3.000dB at {measurements['f3_over_fs']:.9g} Fs, "
              f"PB={measurements['pass_min_db']:.5f}…{measurements['pass_max_db']:.5f} dB")
    document = {
        "schema": "sinc5-comp-design-v1",
        "scope": "independent engineering FIR, quantized analytical response",
        "manual": "AD4080 Rev. B p64–70 Table 28/29 and Figures 105/106/107",
        "manual_sha256": MANUAL_SHA256,
        "target_csv_sha256": hashlib.sha256(args.targets.read_bytes()).hexdigest(),
        "fig106_anchor_csv_sha256": hashlib.sha256(args.fig106_anchors.read_bytes()).hexdigest(),
        "numpy": np.__version__, "scipy": scipy.__version__,
        "fir_taps": FIR_TAPS, "coefficient_width": COEF_WIDTH, "frac_bits": FRAC_BITS,
        "frequency_variable": "u = N*f/Fs; input Fs, CIC output Fs/N, final output Fs/(2*N)",
        "pass_end_u": PASS_END_U, "modes": records,
        "fig106_interleaved": verify_fig106_shape(np.array(records[0]["coefficients_q"]), anchors),
        "manual_vector_comparison": manual_holdout(records, args.targets),
    }
    encoded = json.dumps(document, indent=2, ensure_ascii=False, allow_nan=False)
    args.out_json.write_text(encoded + "\n", encoding="utf-8")
    args.out_svh.write_text(render_lut(records), encoding="utf-8")


if __name__ == "__main__":
    main()
