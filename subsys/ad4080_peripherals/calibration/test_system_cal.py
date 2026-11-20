# =============================================================================
# 文件名     : test_system_cal.py
# 模块名     : test_system_cal
# 版本       : v1.0
# 功能       : 检查系统校准 API、入口、异常退出和未参与拟合的留出数据。
# 设计说明   :
#   * 注入的正向误差为 +0.5% 和 +80 码。VREF、输入电平与正弦幅值均为
#     自建已知参考。正弦路径执行本机实际 ADCToolbox 定频拟合。
#   * K_GAIN/SHIFT 为现有数字通路的测试绑定。对字节和系数作断言；位精确
#     样本运算由独立 RTL 验证执行，留出评估只用浮点仿射关系。
#   * 不需要 pytest，使用该文件的直接 Python 入口即可执行。
# -----------------------------------------------------------------------------
# 变更记录
# -----------------------------------------------------------------------------
# 初始版本，暂无变更记录。
# =============================================================================

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

from system_cal import (
    CODE_FORMATS,
    CalibrationError,
    decode_codes,
    fit_dc,
    fit_sine,
    quantize_registers,
)

# =============================================================================
# 1. 可复现训练和留出参考
# =============================================================================
VREF_V = 3.0
LSB_V = 2 * VREF_V / 2**20
CHANNEL_GAIN = 1.005
CHANNEL_BIAS_CODES = 80.0
FS_HZ = 40_000_000
TRAIN_PEAK_V = 1.1
TRAIN_DC_V = 0.2
TRAIN_FIN_HZ = 410_156.25  # 2048 点的第 21 个 bin


def _dc_capture(bias: float = CHANNEL_BIAS_CODES) -> tuple[np.ndarray, np.ndarray]:
    """生成两个差分 V 电平和与之配对的有符号码；每电平 128 笔。"""
    voltage = np.repeat([-1.2, 1.4], 128)
    # [量化] 以确定性的近 ±0.5 码 dither 做重复捕获，减少训练均值的量化偏差。
    dither = np.tile((np.arange(128) - 63.5) / 128, 2)
    raw = np.rint(CHANNEL_GAIN * voltage / LSB_V + bias + dither).astype(np.int64)
    return voltage, raw


def _tone(
    fin_hz: float, peak_v: float, dc_v: float, count: int, phase_rad: float
) -> np.ndarray:
    """生成参考差分电压；实际 ADC 样本在此波形上另行注入误差。"""
    theta = (2 * np.pi * fin_hz / FS_HZ) * np.arange(count) + phase_rad
    return dc_v + peak_v * np.sin(theta)


def _capture_raw(reference_v: np.ndarray) -> np.ndarray:
    return np.rint(CHANNEL_GAIN * reference_v / LSB_V + CHANNEL_BIAS_CODES).astype(np.int64)


def _encode(codes: np.ndarray, code_format: str) -> np.ndarray:
    # [独立] 夹具直接打包，不借助被测解码器去生成它本来要验证的输入。
    if code_format == "wire_twos20":
        return codes & 0xFFFFF
    if code_format == "offset_binary":
        return codes + (1 << 19)
    if code_format == "spi24_sign_extended":
        return codes & 0xFFFFFF
    return codes


def _fit_training_sine(codes: np.ndarray, **overrides: float):
    settings = {
        "vref_v": VREF_V,
        "fs_hz": float(FS_HZ),
        "fin_hz": TRAIN_FIN_HZ,
        "reference_peak_v": TRAIN_PEAK_V,
        "reference_dc_v": TRAIN_DC_V,
    }
    settings.update(overrides)
    return fit_sine(codes, **settings)


# =============================================================================
# 2. 系数可辨识性、独立留出和异常范围
# =============================================================================
class CalibrationTests(unittest.TestCase):
    def test_dc_formats_and_holdout_voltages(self) -> None:
        voltage, raw = _dc_capture()
        # [留出] 四个新电平包含零点，均未经训练。不对该组重新拟合。
        holdout_v = np.array([-1.61, -0.42, 0.0, 1.76])
        holdout_ideal = holdout_v / LSB_V
        holdout_raw = _capture_raw(holdout_v)
        for code_format in CODE_FORMATS:
            with self.subTest(code_format=code_format):
                estimate = fit_dc(
                    voltage[::-1], _encode(raw[::-1], code_format),
                    vref_v=VREF_V, code_format=code_format,
                )
                self.assertEqual(estimate.sample_count, 256)
                self.assertEqual(estimate.lsb_v, LSB_V)
                self.assertAlmostEqual(estimate.forward_gain, CHANNEL_GAIN, places=7)
                self.assertAlmostEqual(
                    estimate.forward_offset_codes, CHANNEL_BIAS_CODES, delta=0.02
                )
                self.assertIs(estimate.details["positive_mean_slope_verified"], True)
                corrected = (estimate.correction_gain * holdout_raw
                             + estimate.correction_offset_codes)
                self.assertLess(float(np.max(np.abs(corrected - holdout_ideal))), 0.6)
                self.assertGreater(estimate.residual_rmse_codes, 0)

    def test_absolute_sine_with_independent_holdout(self) -> None:
        # [偏置] 参考 DC 特意非零。若误把拟合到的原码 DC 直接当成通道 b，
        # 下列约 80 码断言会因源 DC 的贡献而失败。
        train_v = _tone(TRAIN_FIN_HZ, TRAIN_PEAK_V, TRAIN_DC_V, 2048, 0.31)
        estimate = _fit_training_sine(_capture_raw(train_v))
        self.assertAlmostEqual(estimate.forward_gain, CHANNEL_GAIN, delta=1e-6)
        self.assertAlmostEqual(estimate.forward_offset_codes, CHANNEL_BIAS_CODES, delta=0.08)
        self.assertIs(estimate.details["positive_gain_assumed"], True)
        self.assertLess(estimate.residual_rmse_codes, 0.32)

        # [留出] 新频率、幅值、DC 与相位。原误差超过数百码，训练得到的
        # 参数应直接推广，不能以另一次正弦拟合吸收剩余误差。
        withheld_v = _tone(1_337_125, 1.46, -0.31, 4096, 1.13)
        ideal_codes = withheld_v / LSB_V
        raw = _capture_raw(withheld_v)
        self.assertGreater(float(np.max(np.abs(raw - ideal_codes))), 700)
        corrected = estimate.correction_gain * raw + estimate.correction_offset_codes
        self.assertLess(float(np.max(np.abs(corrected - ideal_codes))), 0.7)

        plan = quantize_registers(estimate, k_gain=1, shift=15)
        self.assertIsNotNone(plan)
        self.assertEqual((plan.offset_code, plan.gain_code), (-80, 349))
        self.assertLess(float(np.max(np.abs(plan.applied_gain * raw + plan.offset_code
                                          - ideal_codes))), 1.2)

    def test_invalid_code_fields_and_rails(self) -> None:
        # [闭包] 码制转换接受合法轨端；拟合将其视作潜在截顶，不能靠舍入或掩码藏掉。
        signed = np.array([-524288, -1, 0, 1, 524287])
        for code_format in CODE_FORMATS:
            with self.subTest(code_format=code_format):
                np.testing.assert_array_equal(
                    decode_codes(_encode(signed, code_format), code_format), signed
                )
        invalid = [
            ([float("nan")], "signed20"), ([1.5], "signed20"), ([524288], "signed20"),
            ([True], "signed20"), ([1 + 2j], "signed20"), (["12"], "signed20"),
            ([-1], "wire_twos20"), ([1 << 20], "offset_binary"),
            ([0x080000], "spi24_sign_extended"), ([0xF00000], "spi24_sign_extended"),
        ]
        for raw, code_format in invalid:
            with self.subTest(raw=raw, code_format=code_format):
                with self.assertRaises(CalibrationError):
                    decode_codes(raw, code_format)
        with self.assertRaisesRegex(CalibrationError, "截顶"):
            fit_dc([-1, -1, 1, 1], [-524288, -2, 2, 2], vref_v=VREF_V)

    def test_rejects_invalid_dc_and_sine_captures(self) -> None:
        # [逆向] 两组行倒序可以，随电压增大而码字均值下降会使物理斜率反号。
        for voltage, raw in (
            ([-1, -1, 1], [-5, -4, 8]),
            ([-1, -1, 0, 0, 1, 1], [-5, -4, 0, 1, 8, 9]),
            ([-1, -1, 1, 1], [8, 9, -5, -4]),
            ([-1, -1, 1, 1], [4, 4, 4, 4]),
            ([-3, -3, 1, 1], [-5, -4, 8, 9]),
            ([-1, -1, np.inf, np.inf], [-5, -4, 8, 9]),
        ):
            with self.subTest(voltage=voltage, raw=raw):
                with self.assertRaises(CalibrationError):
                    fit_dc(voltage, raw, vref_v=VREF_V)

        # [秩] 分别构造欠采样、常量、近零频率、奈奎斯特端、越域参考。
        for raw, options in (
            ([0, 1, 2], {}),
            ([5, 5, 5, 5], {}),
            ([1, 3, 7, 4], {"fin_hz": 1e-7}),
            ([1, 3, 7, 4], {"fin_hz": FS_HZ / 2}),
            ([1, 3, 7, 4], {"reference_dc_v": 2.0}),
            ([1, 3, 7, 4], {"reference_peak_v": float("nan")}),
            ([1, 3, 524287, 4], {}),
        ):
            with self.subTest(raw=raw, options=options):
                with self.assertRaises(CalibrationError):
                    _fit_training_sine(np.asarray(raw), **options)

    def test_quantization_mapping_and_writable_range(self) -> None:
        estimate = fit_dc(*_dc_capture(), vref_v=VREF_V)
        self.assertIsNone(quantize_registers(estimate))
        plan = quantize_registers(estimate, k_gain=1, shift=15)
        self.assertEqual((plan.offset_code, plan.gain_code), (-80, 349))
        # [寄存器] 负偏置低十二位按二补码解释，检查高字节保留位没有被扩展。
        self.assertEqual(plan.writes, {"0x25": 0xB0, "0x26": 0x0F, "0x27": 0x5D, "0x28": 0x01})
        # [实测表] 选择与名义算术映射不同的码，确认调用者的表优先定义倍率。
        table_plan = quantize_registers(estimate, gain_table={310: 0.99, 345: 0.995, 512: 1.0})
        self.assertEqual(table_plan.gain_code, 345)
        self.assertEqual(table_plan.applied_gain, 0.995)
        self.assertIsNone(table_plan.k_gain)

        # [重算] 粗步距与较大零偏会让先舍 OFFSET 的做法差一码。
        biased = fit_dc(*_dc_capture(800), vref_v=VREF_V)
        coarse = quantize_registers(biased, k_gain=1, shift=8)
        self.assertEqual((coarse.gain_code, coarse.offset_code), (511, -797))
        self.assertNotEqual(coarse.offset_code, round(biased.correction_offset_codes))

        for options in ({"k_gain": 1}, {"k_gain": 0, "shift": 15},
                        {"k_gain": 1, "shift": 31}, {"k_gain": 1, "shift": 25},
                        {"gain_table": {512: 1.01, 350: 0.995}},
                        {"gain_table": {0: 0.99, 512: 1.0}},
                        {"gain_table": {512: 1.0}, "k_gain": 1, "shift": 15}):
            with self.subTest(options=options):
                with self.assertRaises(CalibrationError):
                    quantize_registers(estimate, **options)
        for bias in (-2300, 2300):
            with self.subTest(bias=bias):
                overflow = fit_dc(*_dc_capture(bias), vref_v=VREF_V)
                with self.assertRaisesRegex(CalibrationError, "OFFSET"):
                    quantize_registers(overflow, k_gain=1, shift=15)

    def test_gain_table_applies_offset_before_gain(self) -> None:
        # [区分顺序] 约 9.16 mV 的零偏仍在可调范围。训练电平远离轨端，
        # 同一增益档下两种顺序会差八码，不能由舍入或饱和掩盖。
        injected_bias = 1600.0
        estimate = fit_dc(*_dc_capture(injected_bias), vref_v=VREF_V)
        rtl = quantize_registers(estimate, k_gain=1, shift=15)
        assert rtl is not None
        # 同一增益倍率作为实测表的合成夹具。邻近低一码确保涵盖实际目标，
        # 令两个分支的选定倍率一致，便能单独对照 OFFSET 选号。
        measured = quantize_registers(estimate, gain_table={
            rtl.gain_code - 1: rtl.applied_gain - 2**-15,
            rtl.gain_code: rtl.applied_gain,
            512: 1.0,
        })
        assert measured is not None
        self.assertEqual((rtl.gain_code, rtl.applied_gain),
                         (measured.gain_code, measured.applied_gain))
        self.assertEqual((rtl.offset_code, measured.offset_code), (-1592, -1600))
        # 零输入时原码为注入的零偏，各自只在对应节点上消除零偏。
        self.assertLess(abs(rtl.applied_gain * injected_bias + rtl.offset_code), 0.5)
        self.assertLess(abs(measured.applied_gain * (injected_bias + measured.offset_code)), 0.5)
        self.assertGreater(abs(measured.applied_gain * (injected_bias + rtl.offset_code)), 7)


# =============================================================================
# 3. CSV 命令行与 JSON 交接
# =============================================================================
class CliTests(unittest.TestCase):
    def _run_cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        script = Path(__file__).with_name("system_cal.py")
        return subprocess.run([sys.executable, str(script), *args], capture_output=True,
                              text=True, check=False, timeout=30)

    def test_dc_estimate_then_explicit_gain_table(self) -> None:
        voltage, signed = _dc_capture()
        codes = _encode(signed, "spi24_sign_extended")
        with tempfile.TemporaryDirectory() as tmp:
            csv_path = Path(tmp) / "dc.csv"
            gain_path = Path(tmp) / "gain.csv"
            # [协议] 线上码可用十六进制，导出允许 BOM 与额外时间或序号列。
            csv_path.write_text("index,vin_diff_v,code\n" + "".join(
                f"{i},{v},{code:#08x}\n" for i, (v, code) in enumerate(zip(voltage, codes))
            ), encoding="utf-8-sig")
            gain_path.write_text("gain_code,effective_gain\n310,0.99\n345,0.995\n512,1.0\n")
            args = ("dc", "--csv", str(csv_path), "--vref-v", "3", "--code-format",
                    "spi24_sign_extended")
            unbound = self._run_cli(*args)
            self.assertEqual(unbound.returncode, 0, unbound.stderr)
            self.assertIsNone(json.loads(unbound.stdout)["registers"])
            bound = self._run_cli(*args, "--gain-table", str(gain_path))
            self.assertEqual(bound.returncode, 0, bound.stderr)
            self.assertEqual(json.loads(bound.stdout)["registers"]["gain_code"], 345)
            # [验收] 每组重复码有量化波动，零容差必须阻止生成可写计划。
            rejected = self._run_cli(*args, "--max-rmse-codes", "0")
            self.assertEqual(rejected.returncode, 2)
            self.assertEqual(rejected.stdout, "")

    def test_sine_cli_with_bound_registers_and_bad_csv(self) -> None:
        train_v = _tone(TRAIN_FIN_HZ, TRAIN_PEAK_V, TRAIN_DC_V, 2048, 0.31)
        with tempfile.TemporaryDirectory() as tmp:
            csv_path = Path(tmp) / "tone.csv"
            csv_path.write_text("code\n" + "".join(f"{code}\n" for code in _capture_raw(train_v)))
            args = ("sine", "--csv", str(csv_path), "--vref-v", str(VREF_V), "--fs-hz",
                    str(FS_HZ), "--fin-hz", str(TRAIN_FIN_HZ), "--reference-peak-v",
                    str(TRAIN_PEAK_V), "--reference-dc-v", str(TRAIN_DC_V), "--k-gain", "1",
                    "--shift", "15")
            result = self._run_cli(*args)
            self.assertEqual(result.returncode, 0, result.stderr)
            record = json.loads(result.stdout)
            self.assertEqual(record["registers"]["offset_code"], -80)
            self.assertEqual(record["registers"]["gain_code"], 349)
            self.assertIs(record["estimate"]["details"]["positive_gain_assumed"], True)

            # [异常] 解析拒绝时不输出半份寄存器计划，供上层区分采集错误和结果文档。
            csv_path.write_text("no_code\n123\n")
            bad = self._run_cli(*args)
            self.assertEqual(bad.returncode, 2)
            self.assertEqual(bad.stdout, "")


if __name__ == "__main__":
    unittest.main()
