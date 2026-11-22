# =============================================================================
# 文件名     : verify_rtl.py
# 模块名     : verify_rtl
# 版本       : v1.0
# 功能       : 估算固定演示数据的系统校正码，逐周期复核同仓 DSP RTL。
#
# 设计说明   :
#   * 两点和正弦的估算、寄存器量化均走 system_cal.py 的公开入口。
#     独立留出样本不送入估算器。真值参考为量化后有符号 20 位 ADC 码。
#   * DSP 源码从 --repo 读取并以摘要绑定。仿真副本及采集文件写入系统临时
#     目录。对照同仓 sys_corr 的输出、拒绝和两个饱和标志，不另写校正公式。
#   * 工程增益映射为 K=1、SHIFT=15，回执只用于此映射和可重复的合成仿真。
#
# 变更记录   : 2026-11-20 复核 Rev. B 图 96 的 OFFSET 和 GAIN 运算顺序，
#              对修订前后均直接按 RTL 实测决定严格模式的退出状态。
# =============================================================================
"""生成合成输入并复核工程增益映射下 DSP 的整数功能。"""

import argparse
import csv
import hashlib
import json
import math
import re
import runpy
import shutil
import statistics
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from fractions import Fraction
from importlib.metadata import version
from pathlib import Path
from random import Random

# =============================================================================
# 1. 已绑定的演示条件与测量数据
# =============================================================================
VREF_V = 3.0
GAIN_INJECTED = 1.005
OFFSET_INJECTED_LSB20 = 80
MAP_K, MAP_SHIFT = 1, 15
CODE_ZERO = 1 << 19
MAX_CODE = CODE_ZERO - 1
MIN_CODE = -CODE_ZERO
UNITY_GAIN_CODE = 512
LSB_V = VREF_V / CODE_ZERO  # 差分满输入范围为 ±VREF
HOLDOUT_SEED, HOLDOUT_COUNT = 20261120, 1024
DSP_RTL = Path("rtl/ad4080_dsp/sys_error_corr.sv")
DSP_MODEL = Path("rtl/ad4080_dsp/model/ad4080_cal_model.py")
CAL_SOURCE = Path("rtl/ad4080_periph/calibration")


@dataclass(frozen=True)
class Sample:
    ideal: int
    measured: int


@dataclass(frozen=True)
class Cycle:
    sample: int = 0
    offset: int = 0
    gain: int = UNITY_GAIN_CODE
    valid: bool = True
    cancel: bool = False
    rst_n: bool = True
    group: tuple[str, str, str] | None = None
    ideal: int = 0


def injected(ideal_code):
    """以 ADC 码为单位输入真值，返回量化参考和带仿射误差的量化样本。

    样本留在未饱和区间，使校准残差可归因于增益、偏移和整数化。
    合成采样执行最近整数舍入，冻结 DSP 的乘积舍入另由 sys_corr 对照。
    """
    return Sample(round(ideal_code), round(GAIN_INJECTED * ideal_code + OFFSET_INJECTED_LSB20))


def holdout_samples(training_samples):
    """采样未参与估算的有符号 20 位码，保留输入范围余量。"""
    training_codes = {sample.ideal for sample in training_samples}
    generator = Random(HOLDOUT_SEED)
    selected = set()
    result = []
    # 留出序列独立生成，并排除训练集中的真值码与此前留出的重复码。
    while len(result) < HOLDOUT_COUNT:
        code = generator.randint(-480_000, 480_000)
        if code in training_codes or code in selected:
            continue
        selected.add(code)
        result.append(injected(code))
    return result


def run_checked(command, cwd):
    """在独立临时目录执行有界命令，返回其标准输出。"""
    completed = subprocess.run(
        command, cwd=cwd, text=True, capture_output=True, timeout=120, check=False
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"{Path(command[0]).name} exited {completed.returncode}\n"
            f"{completed.stdout}{completed.stderr}"
        )
    return completed.stdout


def estimate_training(directory, mode, rows, columns, mode_flags):
    """用公开校准 CLI 估计和量化，返回其拟合、寄存器输出和训练 CSV 摘要。"""
    capture = directory / f"training_{mode}.csv"
    with capture.open("w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        writer.writerow(columns)
        writer.writerows(rows)
    estimator = Path(__file__).with_name("system_cal.py").resolve()
    output = run_checked(
        [sys.executable, "-B", str(estimator), mode, "--csv", capture.name,
         "--vref-v", str(VREF_V), "--code-format", "signed20",
         "--k-gain", str(MAP_K), "--shift", str(MAP_SHIFT), *mode_flags],
        directory,
    )
    result = json.loads(output)
    regs = result["registers"]
    if not isinstance(regs, dict) or (regs["k_gain"], regs["shift"]) != (MAP_K, MAP_SHIFT):
        raise ValueError(f"{mode}: register mapping missing or differs from the requested binding")
    # 确认负 OFFSET 的 12 位补码可无损折返，防止估计值在回放时被静默截断。
    if type(regs["offset_code"]) is not int or not -2048 <= regs["offset_code"] <= 2047:
        raise ValueError(f"{mode}: OFFSET is outside signed 12-bit range")
    if type(regs["gain_code"]) is not int or not 1 <= regs["gain_code"] <= 1023:
        raise ValueError(f"{mode}: GAIN is outside the bound 10-bit range")
    return result, hashlib.sha256(capture.read_bytes()).hexdigest()


def training_data(directory):
    """生成已知参考的两点与相干正弦，返回估算输入及真实训练样本。"""
    # 两点各重复采数以满足 CLI 的组内统计合同。理想演示同电平的码完全一致。
    dc_levels_v, points_per_level = (-1.2, 1.4), 8
    dc_voltages = [level for level in dc_levels_v for _ in range(points_per_level)]
    dc = [injected(level / LSB_V) for level in dc_voltages]
    dc_fit, dc_hash = estimate_training(
        directory, "dc", zip(dc_voltages, (sample.measured for sample in dc), strict=True),
        ("vin_diff_v", "code"), [],
    )

    # 非零 DC 验证正弦拟合要扣除信号源本身的偏置。相干频点可直接固定拟合频率。
    fs_hz, count, bin_index = 40_000_000, 2048, 21
    fin_hz = fs_hz * bin_index / count
    peak_v, dc_v, phase_rad = 1.1, 0.2, 0.37
    sine = [
        injected((dc_v + peak_v * math.sin(math.tau * bin_index * i / count + phase_rad)) / LSB_V)
        for i in range(count)
    ]
    sine_fit, sine_hash = estimate_training(
        directory, "sine", ((sample.measured,) for sample in sine), ("code",),
        ["--fs-hz", str(fs_hz), "--fin-hz", str(fin_hz), "--reference-peak-v", str(peak_v),
         "--reference-dc-v", str(dc_v)],
    )

    return {
        "dc": {
            "samples": dc,
            "fit": dc_fit,
            "capture_sha256": dc_hash,
            "stimulus": {"vin_diff_v": list(dc_levels_v), "sample_count_per_level": points_per_level},
        },
        "sine": {
            "samples": sine,
            "fit": sine_fit,
            "capture_sha256": sine_hash,
            "stimulus": {"fs_hz": fs_hz, "fin_hz": fin_hz, "bin": bin_index, "sample_count": count,
                         "reference_peak_v": peak_v, "reference_dc_v": dc_v, "phase_rad": phase_rad},
        },
    }


# =============================================================================
# 2. 周期激励、冻结 RTL 编译与输出采集
# =============================================================================
def exercise_cycles(cases, holdout):
    """将训练、独立留出与路径边界组织成一列逐拍刺激。"""
    # 首帧 valid 与冷复位同拍，用输出计分验证复位不会错误接收。
    cycles = [Cycle(valid=True, rst_n=False)]
    for method, case in cases.items():
        registers = case["fit"]["registers"]
        for split, samples in (("train", case["samples"]), ("holdout", holdout)):
            for phase, offset, gain in (
                ("before", 0, UNITY_GAIN_CODE),
                ("after", registers["offset_code"], registers["gain_code"]),
            ):
                for index, sample in enumerate(samples):
                    # 组间连续发数，系数随样本同沿锁存。每组插一空拍检查无效标志。
                    if index == 7:
                        cycles.append(Cycle(valid=False))
                    cycles.append(
                        Cycle(sample.measured, offset, gain, group=(method, split, phase), ideal=sample.ideal)
                    )

    cycles.extend([
        Cycle(MIN_CODE), Cycle(MAX_CODE), Cycle(-1), Cycle(0),  # 码制两端及跨零
        Cycle(MIN_CODE, -2048, 1023), Cycle(MAX_CODE, 2047, 1023),  # 双侧饱和
        Cycle(-16_384, 0, 513), Cycle(16_384, 0, 513),  # 半步在正负方向的不同整数结果
        Cycle(17, 0, 0), Cycle(-17, -2048),  # 零增益拒绝与正常次帧并行
        Cycle(88), Cycle(99), Cycle(321, 0, 0, cancel=True),  # 丢弃在途两笔和同沿请求
        Cycle(valid=False), Cycle(valid=False),
        Cycle(-77), Cycle(-55), Cycle(123, rst_n=False),  # 复位也清除在途数据
        Cycle(28, 2047), Cycle(valid=False), Cycle(valid=False),  # 重启后首笔仍须两拍
    ])
    return cycles


def replay_rtl(repo, directory, cycles, verilator):
    """执行所绑定的 RTL。输出每拍有效位、两种码制、饱和及拒绝标志。"""
    # SAR 生产接口是偏移二进制，OFFSET 端口对应无符号传输的 12 位寄存器字。
    with (directory / "stimulus.txt").open("w", encoding="utf-8") as file:
        for cycle in cycles:
            file.write(
                f"{int(cycle.rst_n)} {int(cycle.valid)} {int(cycle.cancel)} "
                f"{cycle.sample + CODE_ZERO} {cycle.offset & 0xfff} {cycle.gain}\n"
            )

    testbench = Path(__file__).with_name("calibration_verify_tb.sv")
    shutil.copyfile(repo / DSP_RTL, directory / DSP_RTL.name)
    shutil.copyfile(testbench, directory / testbench.name)
    # 构建在工作区外运行。临时副本保留文件名以通过 HDL 文件名检查。
    run_checked(
        [verilator, "--binary", "--timing", "--assert", "-Wall", "--compiler", "clang",
         "-CFLAGS", "-std=c++20", "-j", "2", "--top-module", testbench.stem,
         f"-GMAP_K={MAP_K}", f"-GMAP_SHIFT={MAP_SHIFT}", "--Mdir", "obj",
         DSP_RTL.name, testbench.name],
        directory,
    )
    output = run_checked([f"./obj/V{testbench.stem}"], directory)
    frozen_version = re.search(r"Verilator (\d+\.\d+\b)", output)
    if f"RTL_CALIBRATION_TRACE cycles={len(cycles)}\n" not in output:
        raise RuntimeError("RTL trace missing the expected completion count")

    rows = (directory / "observed.txt").read_text(encoding="utf-8").splitlines()
    if len(rows) != len(cycles):
        raise RuntimeError(f"RTL trace size {len(rows)} differs from stimulus size {len(cycles)}")
    return rows, frozen_version.group(1) if frozen_version else "unknown"


# =============================================================================
# 3. 逐拍比较与统计回执
# =============================================================================
def check_trace(cycles, rows, reference):
    """核对接受、拒绝及两个周期延迟，返回真实 RTL 输出和覆盖数。"""
    # sys_error_corr 在接收沿登记样本，输出提交落后两个完整周期。
    history = [None, None]
    sample_pairs = {}
    coverage = {
        "cycles": len(cycles), "model_comparisons": 0, "clip_low": 0, "clip_high": 0,
        "gain_zero_rejections": 0, "cancel_cycles": 0, "reset_cycles": 0,
    }
    for index, (input_cycle, row) in enumerate(zip(cycles, rows, strict=True)):
        fields = row.split()
        stamp, valid = map(int, fields[:2])
        y_twos, y_ob = (int(word, 16) for word in fields[2:4])
        low, high, reject = map(int, fields[4:])
        if not input_cycle.rst_n or input_cycle.cancel:
            # 取消和复位优先于接收及到期输出，已进入两级的样本一起丢弃。
            history = [None, None]
            due = None
            expect_reject = False
            coverage["reset_cycles"] += int(not input_cycle.rst_n)
            coverage["cancel_cycles"] += int(input_cycle.cancel)
        else:
            expect_reject = input_cycle.valid and input_cycle.gain == 0
            coverage["gain_zero_rejections"] += int(expect_reject)
            due = history.pop(0)
            history.append(input_cycle if input_cycle.valid and not expect_reject else None)

        if stamp != index or valid != int(due is not None) or reject != int(expect_reject):
            raise RuntimeError(f"cycle {index}: valid/reject/latency mismatch ({row})")
        # 无效拍允许输出数据保持旧值，但饱和和拒绝标志仍逐拍核对。
        if due is None:
            if low or high:
                raise RuntimeError(f"cycle {index}: invalid output drove a saturation flag ({row})")
            continue

        expect_y, expect_low, expect_high, _ = reference(
            due.sample, due.offset, due.gain, gain_numerator=MAP_K, gain_shift=MAP_SHIFT,
        )
        if (y_twos, y_ob, low, high) != (
            expect_y & 0xfffff, expect_y + CODE_ZERO, int(expect_low), int(expect_high),
        ):
            raise RuntimeError(f"cycle {index}: code/saturation mismatch, expected {expect_y} ({row})")
        coverage["model_comparisons"] += 1
        coverage["clip_low"] += low
        coverage["clip_high"] += high
        if due.group is not None:
            # 残差按真正从 HDL 读回的补码计算；先过整数模型逐字断言。
            signed_rtl = y_twos if y_twos < CODE_ZERO else y_twos - 2 * CODE_ZERO
            sample_pairs.setdefault(due.group, []).append((due.ideal, signed_rtl))

    if any(coverage[key] == 0 for key in ("clip_low", "clip_high", "gain_zero_rejections",
                                          "cancel_cycles", "reset_cycles")):
        raise RuntimeError("boundary vectors did not exercise the required control and clipping paths")
    coverage["model_mismatches"] = 0
    return sample_pairs, coverage


def check_manual_order(vector, rows, first_cycle, reference):
    """核对图 96 的独立一笔激励，比较同一工程增益下的整数结果。"""
    # [来源] Rev. B p.64 图 96 从 ADC 依次经过 OFFSET CORRECTION 加法
    # 和 GAIN CORRECTION 乘法。两边共用示例增益，比较只针对运算次序。
    # 分数形式保留 129/128 的精确值，差异不能被浮点表示或向半步舍入解释。
    gain_factor = 1 + Fraction((vector.gain - UNITY_GAIN_CODE) * MAP_K, 1 << MAP_SHIFT)
    manual_result = (vector.sample + vector.offset) * gain_factor
    if manual_result.denominator != 1 or not MIN_CODE <= manual_result <= MAX_CODE:
        raise RuntimeError("Figure 96 probe must have an exact, in-range manual-order result")

    # [观察] 独立三拍子窗应仅在第二个完整周期结束时返回一笔有效结果。
    # 夹位、拒绝或流水错误要报测试失败，运算顺序差异由下方单独裁定。
    if len(rows) != 3:
        raise RuntimeError("Figure 96 probe is missing a clock sample")
    for index, row in enumerate(rows):
        stamp, valid, twos, offset_binary, low, high, reject = row.split()
        if int(stamp) != first_cycle + index or int(valid) != int(index == 2):
            raise RuntimeError(f"Figure 96 probe valid/latency mismatch ({row})")
        if int(low) or int(high) or int(reject):
            raise RuntimeError(f"Figure 96 probe clipped or rejected ({row})")

    # 末拍经过 RTL 的补码和偏移二进制输出，独立校验码制一致后再作比较。
    y_twos, y_ob = int(twos, 16), int(offset_binary, 16)
    actual = y_twos if y_twos < CODE_ZERO else y_twos - 2 * CODE_ZERO
    if not MIN_CODE <= actual <= MAX_CODE or actual + CODE_ZERO != y_ob:
        raise RuntimeError("Figure 96 probe output code format mismatch")
    reference_code = reference(
        vector.sample, vector.offset, vector.gain, gain_numerator=MAP_K, gain_shift=MAP_SHIFT,
    )[0]
    expected = manual_result.numerator
    # 严格模式最终使用 RTL 回读和图示比较，独立于系统校正软件参考是否匹配。
    return {
        "status": "MATCHED" if actual == expected else "OPEN_MISMATCH",
        "manual_reference": {
            "document": "ADI AD4080 Rev. B",
            "page": 64,
            "figure": 96,
            "caption": "Digital Filter Selection Options",
            "url": "https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf#page=64",
            "diagram_path": "ADC -> OFFSET CORRECTION (+) -> GAIN CORRECTION (×) -> filter selection",
        },
        "input_sample_lsb20": vector.sample,
        "offset_code": vector.offset,
        "gain_code": vector.gain,
        "comparison_scope": "operation order using a common engineering gain factor",
        "k_gain": MAP_K,
        "shift": MAP_SHIFT,
        "engineering_gain_factor": float(gain_factor),
        "manual_order_formula": "(x + OFFSET) * engineering_gain_factor",
        "manual_order_expected_lsb20": expected,
        "frozen_sys_corr_result_lsb20": reference_code,
        "rtl_observed_lsb20": actual,
        "rtl_minus_manual_lsb20": actual - expected,
        "rtl_matches_frozen_sys_corr": actual == reference_code,
        "probe_cycles": len(rows),
        "valid_probe_outputs": 1,
    }


def check_manual_gain(probes, rows, first_cycle):
    """比较表 61 的三个名义因子，在正负 ±200000 LSB20 上逐码复验。"""
    # 印刷的名义因子由手册独立给出，使用分数避免浮点引入额外误差。
    nominal = {1: Fraction(984406, 1000000), 512: Fraction(1),
               1023: Fraction(1015594, 1000000)}
    observations = []
    for index, (probe, row) in enumerate(zip(probes, rows, strict=True)):
        stamp, valid, twos, offset_binary, low, high, reject = row.split()
        if (int(stamp), int(valid)) != (first_cycle + index, int(index >= 2)):
            raise RuntimeError("Table 61 nominal gain probe lost a clock or output")
        if int(low) or int(high) or int(reject):
            raise RuntimeError("Table 61 nominal gain probe clipped or rejected")
        if index < 2:
            continue
        # 连续已知输入的返回与两周期前的请求配对，不从软模型读取期望值。
        sample = probes[index - 2]
        expected = round(sample.sample * nominal[sample.gain])
        actual = int(offset_binary, 16) - CODE_ZERO
        if actual != expected or int(twos, 16) != actual % (2 * CODE_ZERO):
            raise RuntimeError(f"Table 61 nominal gain {sample.gain:#x}: {actual} != {expected}")
        observations.append({"input": sample.sample, "gain": sample.gain, "expected": expected, "rtl": actual})
    return {"page": 93, "table": 61, "latency_core_cycles": 2, "nominal_code_observations": observations}


# 留出数据使用自己的理想码和相同原始样本比较两种寄存器设置，避免重训带来的乐观误差。
# 此处是码域的标定残差；模拟噪声、实际 VREF 不确定度与源的标定误差需另记。
def error_metrics(pairs):
    """计算 signed LSB20 误差。直线斜率和零点偏差由残差对理想码回归。"""
    errors = [observed - ideal for ideal, observed in pairs]
    mean_error = statistics.fmean(errors)
    mean_ideal = statistics.fmean(ideal for ideal, _ in pairs)
    variance = math.fsum((ideal - mean_ideal) ** 2 for ideal, _ in pairs)
    # 用残差对理想值的协方差除以理想值的方差，分离偏移与残留斜率。
    slope_error = math.fsum(
        (ideal - mean_ideal) * error for (ideal, _), error in zip(pairs, errors, strict=True)
    ) / variance
    return {
        "samples": len(errors),
        "mean_error_lsb20": mean_error,
        "rms_error_lsb20": math.sqrt(statistics.fmean(error * error for error in errors)),
        "max_abs_error_lsb20": max(map(abs, errors)),
        "residual_slope_ppm": slope_error * 1e6,
        "fitted_zero_code_error_lsb20": mean_error - slope_error * mean_ideal,
    }


def results_by_method(cases, samples):
    """汇总实际训练与留出的结果，确保各组恢复完整且残差 RMS 降低。"""
    methods = {}
    for method, case in cases.items():
        result = case["fit"]
        methods[method] = {
            "estimate": result["estimate"],
            "registers": result["registers"],
            "training_capture_sha256": case["capture_sha256"],
            "training_stimulus": case["stimulus"],
        }
        # 保持原始训练样本与前后校正结果一一对应，留出对两个估算法共用。
        for split, expected_count in (("train", len(case["samples"])), ("holdout", HOLDOUT_COUNT)):
            metrics = {
                phase: error_metrics(samples[method, split, phase])
                for phase in ("before", "after")
            }
            if any(item["samples"] != expected_count for item in metrics.values()):
                raise RuntimeError(f"{method} {split}: incomplete RTL result")
            if metrics["after"]["rms_error_lsb20"] >= metrics["before"]["rms_error_lsb20"]:
                raise RuntimeError(f"{method} {split}: calibration did not reduce RMS error")
            methods[method][split] = metrics
    return methods


def main():
    """生成演示回执。源码与用户输入为只读，只有 --out 写出结果。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[3],
                        help="仓库根目录，包含 rtl/ad4080_dsp")
    parser.add_argument("--out", type=Path, required=True, help="JSON 回执路径")
    parser.add_argument("--verilator", default="verilator", help="PATH 中的命令或 Verilator 路径")
    parser.add_argument("--require-manual-order", action="store_true",
                        help="图 96 运算顺序存在实测差异时，仍写回执并以状态码 2 退出")
    args = parser.parse_args()
    repo = args.repo.resolve()
    verilator = shutil.which(args.verilator)
    if verilator is None:
        parser.error("Verilator executable not found")
    source_files = {source.as_posix(): repo / source for source in (DSP_RTL, DSP_MODEL)}
    for name in ("system_cal.py", "calibration_verify_tb.sv", "verify_rtl.py"):
        source_files[(CAL_SOURCE / name).as_posix()] = Path(__file__).with_name(name)
    output = args.out.resolve()
    # 把 --out 与冻结目录及执行源分开，避免误把校准回执写到源码路径。
    if output.is_relative_to(repo / "rtl/ad4080_dsp") or output in {
        path.resolve() for path in source_files.values()
    }:
        parser.error("--out must be separate from the calibration sources and the frozen DSP tree")
    # 从执行前绑定源摘要。若两次估算之间发生并行修改，本次不写闭环回执。
    source_hashes = {key: hashlib.sha256(path.read_bytes()).hexdigest() for key, path in source_files.items()}
    # run_path 直接读取冻结 Python 模型，不在已有 DSP 目录产生字节码缓存。
    reference = runpy.run_path(str(repo / DSP_MODEL))["sys_corr"]
    with tempfile.TemporaryDirectory(prefix="ad4080-calibration-") as temporary:
        directory = Path(temporary)
        cases = training_data(directory)
        holdout = holdout_samples([sample for case in cases.values() for sample in case["samples"]])
        cycles = exercise_cycles(cases, holdout)
        # 独立尾窗与已有向量共用同一个实际 RTL 二进制，原回归覆盖和残差不纳入这笔。
        # 零原码去掉输入斜率项，非零寄存器偏移留下可区分两种次序的交叉项。
        figure_96_probe = Cycle(sample=0, offset=1024, gain=0x300)
        # 只送入一个有效码；随后两个空拍让该码穿过原有两级流水。
        probe_cycles = [figure_96_probe, Cycle(valid=False), Cycle(valid=False)]
        gain_probes = [Cycle(sample=sample, offset=0, gain=gain)
                       for sample in (200000, -200000) for gain in (1, 512, 1023)]
        gain_probes += [Cycle(valid=False), Cycle(valid=False)]
        gain_start = len(cycles) + len(probe_cycles)
        rows, rtl_version = replay_rtl(repo, directory, cycles + probe_cycles + gain_probes, verilator)
        samples, coverage = check_trace(cycles, rows[:len(cycles)], reference)
        # 单列结果可让同一份回放分别服务于工程模型回归和器件符合性签收。
        manual_order = check_manual_order(figure_96_probe, rows[len(cycles):gain_start], len(cycles), reference)
        manual_gain = check_manual_gain(gain_probes, rows[gain_start:], gain_start)
        reports = results_by_method(cases, samples)

    if any(hashlib.sha256(path.read_bytes()).hexdigest() != source_hashes[key]
           for key, path in source_files.items()):
        raise RuntimeError("calibration verifier input source changed during replay")
    report = {
        "scope": "synthetic affine system calibration, declared engineering gain mapping",
        "injected": {"forward_gain": GAIN_INJECTED, "offset_lsb20": OFFSET_INJECTED_LSB20,
                     "vref_v": VREF_V, "lsb_v": LSB_V},
        "engineering_mapping": {"k_gain": MAP_K, "shift": MAP_SHIFT,
                                "formula": "1 + (GAIN - 512) * K_GAIN / 2**SHIFT"},
        "holdout": {"sample_count": HOLDOUT_COUNT, "rng_seed": HOLDOUT_SEED,
                    "selection": "uniform signed codes, unique, excludes training truth codes"},
        "tool_versions": {"adctoolbox": version("adctoolbox"), "verilator": rtl_version},
        "source_sha256": source_hashes,
        "coverage": coverage,
        "methods": reports,
        # 严格模式也要保留观察值，调用方才能区分已知次序偏差和工具失败。
        "manual_order_check": manual_order,
        "manual_gain_nominal_check": manual_gain,
    }
    output.write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    print(f"Verified {coverage['model_comparisons']} RTL outputs against sys_corr")
    print(f"Figure 96 order: {manual_order['status']} "
          f"(RTL {manual_order['rtl_observed_lsb20']}, manual order {manual_order['manual_order_expected_lsb20']})")
    print("PASS TABLE61_NOMINAL_GAIN codes=001,200,3ff signs=2 latency_core_cycles=2")
    return 2 if args.require_manual_order and manual_order["status"] == "OPEN_MISMATCH" else 0


if __name__ == "__main__":
    raise SystemExit(main())
