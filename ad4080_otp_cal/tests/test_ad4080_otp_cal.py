# ==============================================================================
# 文件名     : test_ad4080_otp_cal.py
# 模块名     : tests.test_ad4080_otp_cal
# 版本       : v1.0
# 功能       : 验证 AD4080 360 物理权重正弦标定、Q30 OTP 打包/防篡改 CRC-24、
#              频谱指标提升与 CLI 端到端闭环。
# 设计说明   : 基于标准库 unittest，兼容 pytest，零额外测试框架依赖。
# 变更记录   :
#   2026-11-26  v1.0  初版测试。
# ==============================================================================
"""ad4080_otp_cal 全套单元与端到端闭环测试。"""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
import numpy as np

from ad4080_otp_cal import (
    ADC_BITS,
    TOTAL_WEIGHTS,
    analyze_spectrum,
    calibrate_360_weights_sine,
    dequantize_weights_q30,
    export_otp_artifacts,
    find_coherent_frequency,
    inject_capacitor_mismatch,
    nominal_weights_18x20,
    pack_otp_image,
    reconstruct_wsum_v21,
    simulate_ad4080_capture,
    verify_otp_image,
)
from ad4080_otp_cal.cli import main as cli_main
from ad4080_otp_cal.otp_packer import OTPWeightImage, load_otp_image_json


# ==============================================================================
# 1. 核心算法与端到端 OTP 闭环测试套件
# ==============================================================================
class TestAD4080OtpCalibration(unittest.TestCase):
    """AD4080 360 物理权重正弦标定与 OTP 打包核心测试集。"""

    def test_coherent_frequency_coprime_odd(self) -> None:
        """相干频点必须为奇数且与 N_FFT 互质。"""
        # [测试意图] 确认任意目标频率都会收敛到合法相干频点，避免频谱泄漏
        fin, k_bin = find_coherent_frequency(40e6, 1.0e6, 4096)
        self.assertEqual(k_bin % 2, 1)
        self.assertEqual(np.gcd(k_bin, 4096), 1)
        self.assertAlmostEqual(fin, k_bin * 40e6 / 4096.0, places=6)

    def test_end_to_end_sine_calibration_and_q30_otp(self) -> None:
        """注入 0.2% 电容失配后，正弦标定 + Q30 OTP 应显著拉升 SNDR 与 SFDR。"""
        # [场景设定] 40 MSPS / ~1 MHz 相干单音 + 0.2% 随机失配 + 跨片梯度
        n_fft = 4096
        fs_hz = 40e6
        fin_hz, _ = find_coherent_frequency(fs_hz, 1.0e6, n_fft)
        t = np.arange(n_fft, dtype=np.float64) / fs_hz
        vin = 0.88 * np.sin(2.0 * np.pi * fin_hz * t + 0.25)

        w_true = inject_capacitor_mismatch(sigma_rel=2.0e-3, seed=12345)
        ds = simulate_ad4080_capture(
            vin=vin,
            true_weights_18x20=w_true,
            fs_hz=fs_hz,
            fin_hz=fin_hz,
            noise_rms=8.0e-6,
            seed=54321,
        )

        # [基线对比] 分别统计未校准与 Q30 OTP 定点校准后的动态指标
        fs_half = float(1 << (ADC_BITS - 1))
        m_uncal = analyze_spectrum(ds.raw_codes_uncal, fs_hz=fs_hz, full_scale_peak=fs_half, use_window=False)

        cal = calibrate_360_weights_sine(ds, fin_hz=fin_hz)
        otp = pack_otp_image(cal.weights_18x20, offset_float=cal.offset_est, epoch=7)
        self.assertTrue(verify_otp_image(otp))
        self.assertEqual(len(otp.to_wsum_config_stream()), TOTAL_WEIGHTS)

        # [定点回灌] 用 Q30 定点反量化后的片上权重重构，验证精度大幅跃升且无过拟合
        w_q30 = dequantize_weights_q30(otp.weights_q30_18x20)
        _, codes_q30 = reconstruct_wsum_v21(
            ds.active_ids, ds.post_mask, ds.pre_mask, ds.adc2_residue, w_q30, offset=-cal.offset_est
        )
        m_cal = analyze_spectrum(codes_q30, fs_hz=fs_hz, full_scale_peak=fs_half, use_window=False)

        # [验收门槛] 校准后 SNDR 至少提升 15 dB 且达到 >90 dB、SFDR >100 dB
        self.assertGreater(m_cal.sndr_db - m_uncal.sndr_db, 15.0)
        self.assertGreater(m_cal.sndr_db, 90.0)
        self.assertGreater(m_cal.sfdr_db, 100.0)
        self.assertLess(cal.holdout_rms_error, 5.0e-5)

    def test_otp_crc24_detects_single_bit_flip(self) -> None:
        """翻转 OTP 任意单个比特后，CRC-24 必须可靠检出错误。"""
        # [异常路径] 模拟 OTP 烧录或读出发生单比特翻转，验证 ROM_CRC_ERR 拦截能力
        img = pack_otp_image(nominal_weights_18x20(), offset_float=0.0, epoch=3)
        self.assertTrue(verify_otp_image(img))

        corrupted_w = img.weights_q30_18x20.copy()
        corrupted_w[9, 12] ^= 1  # 翻转第 9 片第 12 单元的最低位
        bad_img = OTPWeightImage(
            epoch=img.epoch,
            offset_q30=img.offset_q30,
            weights_q30_18x20=corrupted_w,
            crc24_poly=img.crc24_poly,
            crc24_init=img.crc24_init,
            crc24_value=img.crc24_value,
        )
        self.assertFalse(verify_otp_image(bad_img))

    def test_cli_demo_and_verify_roundtrip(self) -> None:
        """CLI demo 导出的 JSON/HEX/配置流应能被 verify-otp 完整回读通过。"""
        with tempfile.TemporaryDirectory() as tmp:
            rc = cli_main(["demo", "--samples", "2048", "--out-dir", tmp])
            self.assertEqual(rc, 0)
            otp_json = Path(tmp) / "otp_image.json"
            self.assertTrue(otp_json.exists())
            self.assertTrue((Path(tmp) / "wsum_weights_q30.hex").exists())
            self.assertTrue((Path(tmp) / "wsum_v21_cfg_stream.json").exists())

            rc_ver = cli_main(["verify-otp", str(otp_json)])
            self.assertEqual(rc_ver, 0)
            loaded = load_otp_image_json(otp_json)
            self.assertTrue(verify_otp_image(loaded))


if __name__ == "__main__":
    unittest.main()
