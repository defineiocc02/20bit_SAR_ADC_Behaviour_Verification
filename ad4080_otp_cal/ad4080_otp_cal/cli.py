# ==============================================================================
# 文件名     : cli.py
# 模块名     : ad4080_otp_cal.cli
# 版本       : v1.0
# 功能       : 提供 `ad4080-otp-cal` 命令行入口（demo / verify-otp），一键跑通
#              失配注入 -> 相干正弦采集 -> 360 权重标定 -> Q30 OTP 打包 -> 频谱对比。
# 设计说明   : 零外部交互依赖，所有参数均可命令行覆写，便于 CI 与实验室批处理。
# 变更记录   :
#   2026-11-26  v1.0  初版实现。
# ==============================================================================
"""AD4080 360 权重正弦标定与 Q30 OTP 打包命令行入口。"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
import numpy as np

from .architecture import (
    ADC_BITS,
    inject_capacitor_mismatch,
    reconstruct_wsum_v21,
    simulate_ad4080_capture,
)
from .otp_packer import (
    dequantize_weights_q30,
    export_otp_artifacts,
    load_otp_image_json,
    pack_otp_image,
    verify_otp_image,
)
from .sine_calibration import calibrate_360_weights_sine
from .spectrum import analyze_spectrum, find_coherent_frequency


# ==============================================================================
# 1. 子命令实现：端到端闭环演示（demo）与 OTP 完整性校验（verify-otp）
# ==============================================================================
def _cmd_demo(args: argparse.Namespace) -> int:
    """执行端到端闭环演示并导出 Q30 OTP 产物。"""
    # [步骤 1] 计算严格相干的单音正弦频率，避免 FFT 泄漏干扰 SNDR/SFDR 基准
    n_fft = int(args.samples)
    fs_hz = float(args.fs)
    fin_hz, k_bin = find_coherent_frequency(fs_hz, float(args.fin), n_fft)

    # [步骤 2] 留出 0.88 满量程幅值，为 6-bit dither 注入与电容失配预留不削波裕量
    t = np.arange(n_fft, dtype=np.float64) / fs_hz
    vin = 0.88 * np.sin(2.0 * np.pi * fin_hz * t + 0.37)

    # [步骤 3] 注入真实电容失配与跨片梯度，按二维 DEM + Dither 模拟采集
    w_true = inject_capacitor_mismatch(sigma_rel=float(args.mismatch_sigma), seed=int(args.seed))
    ds = simulate_ad4080_capture(
        vin=vin,
        true_weights_18x20=w_true,
        fs_hz=fs_hz,
        fin_hz=fin_hz,
        noise_rms=float(args.noise_rms),
        seed=int(args.seed) + 1,
    )

    # [步骤 4] 评估未校准基线 -> 执行 360 物理权重正弦标定 -> Q30 定点打包与 CRC-24 校验
    fs_half = float(1 << (ADC_BITS - 1))
    m_before = analyze_spectrum(ds.raw_codes_uncal, fs_hz=fs_hz, full_scale_peak=fs_half, use_window=False)

    cal = calibrate_360_weights_sine(ds, fin_hz=fin_hz)
    otp_img = pack_otp_image(cal.weights_18x20, offset_float=cal.offset_est, epoch=1)
    assert verify_otp_image(otp_img), "OTP CRC-24 自检失败！"

    # [步骤 5] 用经过 Q30 量化后的片上真实定点权重重新重构，检验定点量化无明显精度损失
    w_q30_float = dequantize_weights_q30(otp_img.weights_q30_18x20)
    _, codes_q30 = reconstruct_wsum_v21(
        ds.active_ids,
        ds.post_mask,
        ds.pre_mask,
        ds.adc2_residue,
        w_q30_float,
        offset=-cal.offset_est,
    )
    m_after = analyze_spectrum(codes_q30, fs_hz=fs_hz, full_scale_peak=fs_half, use_window=False)

    # [步骤 6] 导出 JSON 镜像、SystemVerilog $readmemh 与 wsum_v21 配置流
    paths = export_otp_artifacts(otp_img, Path(args.out_dir))

    print(f"=== AD4080 360-Weight Sine Calibration & Q30 OTP Demo ({m_after.backend}) ===")
    print(f"Fs = {fs_hz/1e6:.2f} MSPS | Fin = {fin_hz/1e6:.6f} MHz (bin {k_bin}/{n_fft})")
    print(f"Matrix rank = {cal.matrix_rank}/363 | Cond = {cal.condition_number:.1f}")
    print(f"Train RMS = {cal.train_rms_error:.3e} | Holdout RMS = {cal.holdout_rms_error:.3e}")
    print(f"[Uncalibrated] SNDR={m_before.sndr_db:6.2f} dB | SFDR={m_before.sfdr_db:6.2f} dB | ENOB={m_before.enob:5.2f} bit")
    print(f"[Q30 OTP Cal ] SNDR={m_after.sndr_db:6.2f} dB | SFDR={m_after.sfdr_db:6.2f} dB | ENOB={m_after.enob:5.2f} bit")
    print(f"OTP CRC-24 = 0x{otp_img.crc24_value:06X} (VERIFIED)")
    for k, v in paths.items():
        print(f"  -> {k}: {v}")
    return 0


def _cmd_verify_otp(args: argparse.Namespace) -> int:
    """加载并校对外部 otp_image.json 的 CRC-24 完整性。"""
    img = load_otp_image_json(args.otp_json)
    ok = verify_otp_image(img)
    print(f"OTP epoch={img.epoch}, CRC-24=0x{img.crc24_value:06X}, status={'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ad4080-otp-cal", description="AD4080 360 权重正弦标定与 OTP 打包工具")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_demo = sub.add_parser("demo", help="运行端到端失配注入 -> 正弦标定 -> Q30 OTP 打包演示")
    p_demo.add_argument("--samples", type=int, default=4096, help="相干 FFT 采样点数（默认 4096）")
    p_demo.add_argument("--fs", type=float, default=40e6, help="采样率 Hz（默认 40e6）")
    p_demo.add_argument("--fin", type=float, default=1e6, help="目标正弦频率 Hz（默认 1e6）")
    p_demo.add_argument("--mismatch-sigma", type=float, default=1.5e-3, help="电容失配 1-sigma（默认 1.5e-3）")
    p_demo.add_argument("--noise-rms", type=float, default=1.0e-5, help="等效输入噪声 RMS（默认 1.0e-5）")
    p_demo.add_argument("--seed", type=int, default=20261126, help="随机种子")
    p_demo.add_argument("--out-dir", type=str, default="./ad4080_otp_out", help="产物输出目录")
    p_demo.set_defaults(func=_cmd_demo)

    p_ver = sub.add_parser("verify-otp", help="校对 otp_image.json 的 CRC-24 完整性")
    p_ver.add_argument("otp_json", type=str, help="otp_image.json 文件路径")
    p_ver.set_defaults(func=_cmd_verify_otp)

    ns = parser.parse_args(argv)
    return int(ns.func(ns))


if __name__ == "__main__":
    sys.exit(main())
