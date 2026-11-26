# ==============================================================================
# 文件名     : otp_packer.py
# 模块名     : ad4080_otp_cal.otp_packer
# 版本       : v1.0
# 功能       : 将标定后的 18×20 浮点权重按 rtl/sar_core/wsum_v21.sv 严格口径量化为
#              360×40-bit Q30 定点表，生成可编程 CRC-24 OTP 镜像，并导出 JSON /
#              OTP HEX / SystemVerilog $readmemh 初始化文件。
# 设计说明   :
#   * [Q30 量化口径] w_q30 = round(w_float * 2^30)，按 40-bit 无符号/有符号安全范围
#     [0, 2^39 - 1] 饱和限幅，匹配 wsum_v21.sv 的 `input logic [39:0] cfg_data`。
#   * [配置地址顺序] 严格对应 wsum_v21.sv 写协议：
#       - cfg_sel = 0 -> wt_main[sid][idx] (sid=0..17, idx=0..15)
#       - cfg_sel = 1 -> wt_sub [sid][idx] (sid=0..17, idx=0..3)
#   * [CRC-24 可编程] 默认使用标准 24-bit 多项式 0x864CFB（CRC-24/OpenPGP），
#     并开放 poly/init/xorout 参数，等评估板抓包辨识商用 AD4080 多项式后一行切换。
# 变更记录   :
#   2026-11-26  v1.0  初版实现。
# ==============================================================================
"""AD4080 360×40-bit Q30 权重定点化、CRC-24 OTP 打包与 RTL 导出模块。"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import struct
import numpy as np

from .architecture import (
    MAIN_PER_SLICE,
    NUM_SLICES,
    Q30_SCALE,
    SUB_PER_SLICE,
    WEIGHTS_PER_SLICE,
)

# 40-bit 有符号正数上限（留出最高符号位，防止 wsum_v21 加法溢出）
MAX_Q30_40B: int = (1 << 39) - 1
DEFAULT_CRC24_POLY: int = 0x864CFB
DEFAULT_CRC24_INIT: int = 0xB704CE


@dataclass(frozen=True)
class OTPWeightImage:
    """完整的 AD4080 OTP 校准权重镜像（含 epoch、失调、360 权重与 CRC-24）。"""

    epoch: int
    offset_q30: int
    weights_q30_18x20: np.ndarray  # shape (18, 20), int64
    crc24_poly: int
    crc24_init: int
    crc24_value: int

    def to_wsum_config_stream(self) -> list[dict[str, int]]:
        """展开为逐拍写入 wsum_v21.sv 配置口的操作列表。"""
        # [写序约束] 先逐片写入 16 个主阵权重（cfg_sel=0），再写 4 个子阵权重（cfg_sel=1），
        # 与 rtl/sar_core/wsum_v21.sv 配置口地址映射保持一一对应，便于直接喂入 SPI/TB。
        ops: list[dict[str, int]] = []
        for sid in range(NUM_SLICES):
            for idx in range(MAIN_PER_SLICE):
                ops.append(
                    {
                        "cfg_sel": 0,
                        "cfg_sid": sid,
                        "cfg_idx": idx,
                        "cfg_data": int(self.weights_q30_18x20[sid, idx]),
                    }
                )
            for idx in range(SUB_PER_SLICE):
                ops.append(
                    {
                        "cfg_sel": 1,
                        "cfg_sid": sid,
                        "cfg_idx": idx,
                        "cfg_data": int(self.weights_q30_18x20[sid, MAIN_PER_SLICE + idx]),
                    }
                )
        return ops


# ==============================================================================
# 1. Q30 量化 / 反量化与可编程 CRC-24
# ==============================================================================
def quantize_weights_q30(weights_18x20: np.ndarray) -> np.ndarray:
    """将 shape=(18, 20) 浮点权重按四舍五入量化为 40-bit Q30 定点整数。"""
    # [边界保护] 拒绝非 (18, 20) 形状的错位输入，防止写入错误物理片；
    # 限幅至 [0, 2^39-1] 保留最高符号位，避免 wsum_v21 累加树发生有符号溢出。
    arr = np.asarray(weights_18x20, dtype=np.float64)
    if arr.shape != (NUM_SLICES, WEIGHTS_PER_SLICE):
        raise ValueError(f"权重形状必须为 (18, 20)，收到 {arr.shape}。")
    q = np.rint(arr * float(Q30_SCALE)).astype(np.int64)
    return np.clip(q, 0, MAX_Q30_40B)


def dequantize_weights_q30(weights_q30_18x20: np.ndarray) -> np.ndarray:
    """将 40-bit Q30 定点权重还原为浮点值（单位：Cu_main）。"""
    return np.asarray(weights_q30_18x20, dtype=np.float64) / float(Q30_SCALE)


def crc24_compute(
    payload: bytes,
    poly: int = DEFAULT_CRC24_POLY,
    init: int = DEFAULT_CRC24_INIT,
    xorout: int = 0x000000,
) -> int:
    """按 MSB-first 计算可编程 24-bit CRC（与 ad4080_crc24_service 同口径）。"""
    crc = init & 0xFFFFFF
    poly_24 = poly & 0xFFFFFF
    for byte in payload:
        crc ^= (byte << 16)
        for _ in range(8):
            if crc & 0x800000:
                crc = ((crc << 1) ^ poly_24) & 0xFFFFFF
            else:
                crc = (crc << 1) & 0xFFFFFF
    return (crc ^ xorout) & 0xFFFFFF


def _serialize_payload(epoch: int, offset_q30: int, weights_q30_18x20: np.ndarray) -> bytes:
    """将 epoch、offset 与 360×40-bit 权重按大端确定性序列化为字节流（供 CRC-24 计算）。"""
    buf = bytearray()
    buf.extend(struct.pack(">Iq", int(epoch) & 0xFFFFFFFF, int(offset_q30)))
    for sid in range(NUM_SLICES):
        for j in range(WEIGHTS_PER_SLICE):
            val = int(weights_q30_18x20[sid, j]) & 0xFFFFFFFFFF
            buf.extend(val.to_bytes(5, byteorder="big", signed=False))
    return bytes(buf)


# ==============================================================================
# 2. OTP 打包、校验与多格式导出
# ==============================================================================
def pack_otp_image(
    weights_18x20: np.ndarray,
    offset_float: float = 0.0,
    epoch: int = 1,
    crc24_poly: int = DEFAULT_CRC24_POLY,
    crc24_init: int = DEFAULT_CRC24_INIT,
) -> OTPWeightImage:
    """打包 360×40-bit Q30 权重镜像并附加 24-bit CRC 校验尾。"""
    w_q30 = quantize_weights_q30(weights_18x20)
    off_q30 = int(round(float(offset_float) * float(Q30_SCALE)))
    payload = _serialize_payload(epoch, off_q30, w_q30)
    crc_val = crc24_compute(payload, poly=crc24_poly, init=crc24_init)
    return OTPWeightImage(
        epoch=epoch,
        offset_q30=off_q30,
        weights_q30_18x20=w_q30,
        crc24_poly=crc24_poly,
        crc24_init=crc24_init,
        crc24_value=crc_val,
    )


def verify_otp_image(image: OTPWeightImage) -> bool:
    """重新序列化镜像载荷并比对 CRC-24，检测任意单比特篡改或损坏。"""
    payload = _serialize_payload(image.epoch, image.offset_q30, image.weights_q30_18x20)
    expected = crc24_compute(payload, poly=image.crc24_poly, init=image.crc24_init)
    return expected == (image.crc24_value & 0xFFFFFF)


def export_otp_artifacts(image: OTPWeightImage, out_dir: str | Path) -> dict[str, Path]:
    """导出 3 种工程交付物：JSON 镜像、SystemVerilog 40-bit $readmemh、wsum 配置流 JSON。

    Args:
        image: 已打包的 OTPWeightImage。
        out_dir: 输出目录（自动创建）。

    Returns:
        包含三个生成文件路径的字典。
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    # 1. 完整 JSON 镜像（供上位机、SPI 下载器与离线校验）
    json_path = out / "otp_image.json"
    json_doc = {
        "format": "ad4080_otp_q30_v1",
        "epoch": image.epoch,
        "offset_q30": image.offset_q30,
        "crc24_poly": f"0x{image.crc24_poly:06X}",
        "crc24_init": f"0x{image.crc24_init:06X}",
        "crc24_value": f"0x{image.crc24_value:06X}",
        "weights_q30_18x20": image.weights_q30_18x20.tolist(),
    }
    json_path.write_text(json.dumps(json_doc, indent=2), encoding="utf-8")

    # 2. SystemVerilog $readmemh 文件（360 行 10-hex-char 40-bit Q30 + 末尾 1 行 CRC-24）
    mem_path = out / "wsum_weights_q30.hex"
    hex_lines = [
        f"{int(image.weights_q30_18x20[sid, j]) & 0xFFFFFFFFFF:010X}"
        for sid in range(NUM_SLICES)
        for j in range(WEIGHTS_PER_SLICE)
    ]
    hex_lines.append(f"{image.crc24_value & 0xFFFFFF:010X}")
    mem_path.write_text("\n".join(hex_lines) + "\n", encoding="utf-8")

    # 3. wsum_v21.sv 配置口写入向量表（cfg_sel / cfg_sid / cfg_idx / cfg_data）
    cfg_path = out / "wsum_v21_cfg_stream.json"
    cfg_path.write_text(
        json.dumps(image.to_wsum_config_stream(), indent=2), encoding="utf-8"
    )

    return {
        "otp_json": json_path,
        "sv_readmemh": mem_path,
        "wsum_cfg_stream": cfg_path,
    }


def load_otp_image_json(json_path: str | Path) -> OTPWeightImage:
    """从导出的 otp_image.json 反序列化为 OTPWeightImage 对象。"""
    raw = json.loads(Path(json_path).read_text(encoding="utf-8"))
    return OTPWeightImage(
        epoch=int(raw["epoch"]),
        offset_q30=int(raw["offset_q30"]),
        weights_q30_18x20=np.asarray(raw["weights_q30_18x20"], dtype=np.int64),
        crc24_poly=int(raw["crc24_poly"], 16),
        crc24_init=int(raw["crc24_init"], 16),
        crc24_value=int(raw["crc24_value"], 16),
    )
