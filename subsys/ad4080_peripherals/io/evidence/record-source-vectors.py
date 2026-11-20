#!/usr/bin/env python3
# =============================================================================
# 文件名     : record-source-vectors.py
# 模块名     : AD4080 FIFO 读记录独立向量
# 版本       : v1.0
# 功能       : 按 Rev. B 图 87–89 生成逐边沿/逐引脚的逻辑记录。
# 设计说明   : SPI 采用图示长度和 ADI 参考固件的读法。LVDS 尾位由调用者
#              提供，专门覆盖尾字段串化边界，取值独立于器件 CRC 算法。
# 变更记录   : 初始版本，暂无变更记录。
# =============================================================================
"""以 JSON 输出四组独立记录。输入样本为 20 位二补码的位型。"""

import json


# =============================================================================
# 1. 输入向量
# =============================================================================
# 正样本末位 1、负样本末位 0。两字反转可以区分第一个/最后一个字错挂。
# 尾位型也变化，过滤全零补位、位反序、错过第一位和漏最后一位等故障。
INPUTS = (
    ("N1_positive", (0x135A9,), 0x38BC42),
    ("N1_negative", (0xE62B4,), 0xD36587),
    ("N2_positive_then_negative", (0x135A9, 0xE62B4), 0xA5176C),
    ("N2_negative_then_positive", (0xE62B4, 0x135A9), 0x2E904D),
)
HEADER = 0xAA


# =============================================================================
# 2. 三种物理表示
# =============================================================================
def result_bytes(word):
    """把 20 位码解释为有符号整数，再用三字节大端格式编码。"""
    # 使用有符号数学转换，避免复用 DUT 的复制符号位实现作为参考模型。
    signed = word - (1 << 20) if word >= (1 << 19) else word
    return signed.to_bytes(3, "big", signed=True)


def record(name, raw_words, injected_tail):
    """返回逐模式物理记录和计数。拒绝样本解码与源向量不一致的结果。"""
    n = len(raw_words)
    stream1 = bytes([HEADER]) + b"".join(map(result_bytes, raw_words))

    # 图 88 逐字节的高半字节先发。图示第二个 beat A..D 是 D16..19，
    # 因此各 beat 从 A 到 D 取半字节的位 0..3。头的每位同时送四根线。
    header_bits = f"{HEADER:08b}"
    nibble_payload = stream1[1:].hex()
    pins = [header_bits + "".join(str((int(nibble, 16) >> j) & 1) for nibble in nibble_payload)
            for j in range(4)]
    physical_abcd_beats = "".join(format(int("".join(beat), 2), "x") for beat in zip(*pins))

    # LVDS 的字段在同一条 DA 上连续发送。两个相邻线位形成一对逻辑 DDR 槽位。
    # 尾用给定的从早到晚位型，不计算厂商 CRC，也不从字节反射猜测尾位序。
    da_stream_hex = f"{HEADER:02x}" + "".join(f"{word:05x}" for word in raw_words) + f"{injected_tail:06x}"
    da_bits = "".join(f"{int(digit, 16):04b}" for digit in da_stream_hex)
    lvds_edge_pairs = [int(da_bits[i:i + 2], 2) for i in range(0, len(da_bits), 2)]

    # 反向验收 SPI 线重排和记录长度。这样夹具自身也会暴露腿序/边界错误。
    unpacked = "".join(format(int("".join(reversed(beat)), 2), "x") for beat in zip(*pins))
    assert unpacked[8:] == nibble_payload
    assert len(stream1) * 8 == 8 + 24 * n
    assert all(len(pin) == 8 + 6 * n for pin in pins)
    assert len(lvds_edge_pairs) == 4 + 10 * n + 12
    assert (int.from_bytes(stream1[1:4], "big", signed=True) & 0xFFFFF) == raw_words[0]
    assert (int.from_bytes(stream1[-3:], "big", signed=True) & 0xFFFFF) == raw_words[-1]

    return {
        "case": name,
        "samples_hex": [f"{word:05X}" for word in raw_words],
        "spi1_dclk": 8 + 24 * n,
        "spi1_sdob_hex": stream1.hex().upper(),
        "spi4_dclk": 8 + 6 * n,
        "spi4_pins": dict(zip("ABCD", pins)),
        "spi4_physical_ABCD_beats_hex": physical_abcd_beats.upper(),
        "lvds_ddr_edges": len(da_bits),
        "lvds_da_hex": da_stream_hex.upper(),
        "lvds_edge_pairs": lvds_edge_pairs,
        "lvds_injected_tail_wire_hex": f"{injected_tail:06X}",
    }


if __name__ == "__main__":
    print(json.dumps([record(*entry) for entry in INPUTS], indent=2))
