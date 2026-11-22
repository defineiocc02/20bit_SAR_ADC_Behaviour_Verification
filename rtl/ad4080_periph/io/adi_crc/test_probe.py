# =============================================================================
# 文件名     : test_probe.py
# 模块名     : FIFO CRC 离线工具的独立回归
# 版本       : v1.0
# 功能       : 用 GF(2) 长除法产生合成尾，覆盖原始 CSV、训练、留出和反例。
# 设计说明   : 合成协议参数刻意独立于器件。长除构造不调用被测的 LFSR 或消元。
# 变更记录   : 初始版本，暂无变更记录。
# =============================================================================
"""所有记录均为 synthetic，仅验证工具的区别能力。"""

import csv
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from capture import parse_record
from crc24 import feed
from fifo_crc_probe import analyze


POLY = 0x6812AD
INIT = 0x34C917
XOROUT = 0xA8CA6D
# 训练同时含同长度差分和跨长度方程，留出加入从未见过的三字长度。
TRAIN = [(0x135A9,), (0xE62B4,), (0x24B61,), (0x764EF, 0xDB192), (0x093AB, 0x8C601)]
HOLDOUT = [(0xB19E3,), (0x5B92F, 0x812AC), (0x7BE21, 0x982AB, 0x00042)]


# =============================================================================
# 1. 与 LFSR 独立的多项式参考与造样
# =============================================================================
def remainder(message, length, poly, init):
    # 直接式 CRC 等价于除 (M << 24) xor (init << 位数)。逐项消最高位，独立于 DUT LFSR。
    generator = (1 << 24) | poly
    numerator = (message << 24) ^ (init << length)
    for bit in range(numerator.bit_length() - 1, 23, -1):
        if numerator & (1 << bit):
            numerator ^= generator << (bit - 24)
    return numerator


def wire_tail(words, flipped=False):
    if flipped:
        # 这一测试包含 0xAA、24 位符号填充、输入逐字节反射和 24 位增广。
        payload = bytearray([0xAA])
        for raw20 in words:
            signed = raw20 - (1 << 20) if raw20 > 0x7FFFF else raw20
            payload += signed.to_bytes(3, "big", signed=True)
        bits = "".join(f"{value:08b}"[::-1] for value in payload) + "0" * 24
    else:
        bits = "".join(f"{word:020b}" for word in words)
    value = remainder(int(bits, 2), len(bits), POLY, INIT)
    if flipped:
        # 反射整个内部余数后再作 XOR。最后把三个 CRC 字节逆序接在线尾。
        reflected = sum(((value >> i) & 1) << (23 - i) for i in range(24))
        return int.from_bytes((reflected ^ XOROUT).to_bytes(3, "big")[::-1], "big")
    return value ^ XOROUT


def frame_record(record_id, words, split, tail):
    # 解码型导出使用先发线位居高位的整数。这样反射例能检验解释层和解析层的分工。
    return {"id": record_id, "mode": "lvds", "watermark": len(words), "split": split,
            "capture": {"encoding": "decoded", "header": "0xAA", "samples": list(words), "crc_wire": tail}}


def make_records(training, holdout, flipped=False):
    return [frame_record(f"{split}-{i}", words, split, wire_tail(words, flipped))
            for split, cases in (("train", training), ("holdout", holdout))
            for i, words in enumerate(cases)]


def profile(name="poly-derive", **fields):
    # 逐条替换一个假设，改变位序或覆盖后可定位是哪个条件造成拒绝或别名。
    return {"name": name, "poly": "derive", "coverage": "payload", "packing": "raw20",
            "refin": False, "refout": False, "wire_order": "msb", "augment_bits": 0,
            "init": None, "xorout": None, **fields}


# =============================================================================
# 2. 清单、真实格式转换及错误定位
# =============================================================================
class ProbeTest(unittest.TestCase):
    def setUp(self):
        # 每条测试隔离原始 CSV 和清单，避免同名输入或缓存哈希借用上一次结果。
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.base = Path(scratch.name)

    def write(self, name, value):
        path = self.base / name
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def manifest(self, records, kind="synthetic"):
        # 模拟采集单也有来源会话，防止电气归档时和原始实物 CSV 混在一起。
        return self.write("capture.json", {"format": "ad4080-fifo-capture-v1", "records": records,
                                            "source": {"kind": kind, "device": "AD4080",
                                                       "reference": "local independent unit fixture",
                                                       "session": "test-session"}})

    def hypotheses(self, *profiles):
        return self.write("hypotheses.json", {"format": "ad4080-crc24-hypotheses-v1", "profiles": profiles})

    def test_lvds_edge_csv_sha_and_order(self):
        # 跨两个 20 位字包含正负边界，同时选择一组偏离首字的边沿相位。
        words, tail = (0x135A9, 0xE62B4), 0xABC678
        text = f"{0xAA:08b}" + "".join(f"{word:020b}" for word in words) + f"{tail:024b}"
        path = self.base / "lvds.csv"
        # 从逻辑窗口外各留一个边沿，检查 half-open 行坐标只选真正记录。
        padded = "0" + text + "0"
        with path.open("w", newline="") as handle:
            out = csv.writer(handle)
            out.writerow(["time_ns", "dco_edge", "da"])
            for i, bit in enumerate(padded):
                out.writerow([i * 5, "rise" if i % 2 else "fall", bit])
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        item = {"id": "lvds", "mode": "lvds", "watermark": 2, "split": "format",
                "capture": {"encoding": "lvds_edges_csv", "path": path.name, "sha256": digest,
                            "rows": [1, len(padded) - 1]}}
        parsed = parse_record(item, self.base)
        self.assertEqual(parsed.words, words)
        self.assertEqual(parsed.crc_wire, f"{tail:024b}")
        self.assertEqual(parsed.file_sha256, digest)
        item["capture"]["sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            parse_record(item, self.base)

    def test_header_scan_accepts_only_single_complete_alignment(self):
        # 两段合法头都必须报歧义，禁止在含两次突发的窗口静默挑中一条。
        raw = "10101010" + "0" * 44
        item = {"id": "lvds-raw", "mode": "lvds", "watermark": 1, "split": "format",
                "capture": {"encoding": "wire_bits", "bits": "000" + raw + "00", "scan_header": True}}
        parsed = parse_record(item, self.base)
        self.assertEqual((parsed.discarded_before, parsed.discarded_after), (3, 2))
        item["capture"]["bits"] = raw + raw
        with self.assertRaisesRegex(ValueError, "候选有 2 个"):
            parse_record(item, self.base)

    def spi_csv(self, mode, beats):
        path = self.base / f"{mode}.csv"
        physical = ["sdob"] if mode == "spi1" else ["sdoa", "sdob", "sdoc", "sdod"]
        with path.open("w", newline="") as handle:
            out = csv.writer(handle)
            out.writerow(["time_ns", "dcs", "dclk", *physical])
            idle = "0" * len(physical)
            out.writerow([0, 1, 1, *idle])
            out.writerow([5, 0, 1, *idle])
            for i, beat in enumerate(beats):
                # 状态在下降沿后的低相位设置，随后维持过采样上升沿。
                out.writerow([10 + 100 * i, 0, 0, *beat])
                out.writerow([60 + 100 * i, 0, 1, *beat])
            out.writerow([10 + 100 * len(beats), 1, 1, *idle])
        return path

    def test_spi1_and_spi4_physical_prefix_and_unknown_extra(self):
        # 采用独立数据表的已知物理 ABCD，额外一拍用于验证超出图示时钟数的保留。
        bits = "".join(f"{byte:08b}" for byte in bytes.fromhex("aa0135a9fe62b4"))
        physical = "F0F0F0F008CA59F764D2"
        quad = [f"{int(nibble, 16):04b}" for nibble in physical]
        for mode, wire, extra in (("spi1", [*bits, "1"], "1"), ("spi4", quad + ["1001"], "1001")):
            path = self.spi_csv(mode, wire)
            item = {"id": mode, "mode": mode, "split": "format", "watermark": 2,
                    "capture": {"encoding": "spi_events_csv", "path": path.name}}
            parsed = parse_record(item, self.base)
            self.assertEqual(parsed.words, (0x135A9, 0xE62B4))
            self.assertEqual(parsed.extra_wire, extra)
            self.assertIsNone(parsed.crc_wire)

    def test_spi_sign_extension_and_bad_lane_header_reject(self):
        # 第二个负样本的高四位应为 F，直接截断会掩盖该错误。
        item = {"id": "spi", "mode": "spi1", "split": "format", "watermark": 2,
                "capture": {"encoding": "decoded", "header": 0xAA, "samples": [0x0135A9, 0x0E62B4]}}
        with self.assertRaisesRegex(ValueError, "符号扩展"):
            parse_record(item, self.base)
        item.update({"mode": "spi4", "capture": {"encoding": "abcd_beats", "beats": "70F0F0F008CA59F764D2"}})
        with self.assertRaisesRegex(ValueError, "每条已选 lane"):
            parse_record(item, self.base)

    def test_rejects_wrong_sample_count_and_wrong_chip_identity(self):
        # 具有相似 20 位转换字的另一器件也会被来源校验驱逐，不能借用其 CRC。
        item = frame_record("count", (1,), "train", 0)
        item["watermark"] = 2
        with self.assertRaisesRegex(ValueError, "样本数"):
            analyze(self.manifest([item]))
        path = self.manifest([item])
        data = json.loads(path.read_text())
        data["source"]["device"] = "AD4082"
        with self.assertRaisesRegex(ValueError, "AD4080"):
            analyze(self.write("wrong-device.json", data))

    # =========================================================================
    # 3. 参数解、先验有限域与共享常量产生的别名
    # =========================================================================
    def test_derive_poly_and_train_unique_holdout_pass(self):
        # 一个伪先验候选和真正同长度推导并列，有助于回归确认工具会排除坏多项式。
        cap = self.manifest(make_records(TRAIN, HOLDOUT))
        h = self.hypotheses(profile(), profile("wrong-poly", poly=[0x864CFB]))
        result = analyze(cap, h)
        self.assertEqual(result["fit"]["status"], "unique_within_declared_hypotheses")
        self.assertEqual(result["silicon_binding"], "synthetic_only")
        proper, wrong = result["profiles"]
        self.assertEqual(proper["derivation"]["degree"], 24)
        self.assertEqual(proper["distinct_holdout_messages"], 3)
        good = proper["models"][0]
        self.assertEqual((good["status"], good["poly"]), ("holdout_pass", f"0x{POLY:06X}"))
        self.assertEqual(good["remaining"], {"rank": 48, "free_degrees": 0,
                                              "representative_init": f"0x{INIT:06X}",
                                              "representative_xorout": f"0x{XOROUT:06X}"})
        self.assertEqual(wrong["models"][0]["status"], "train_rejected")

    def test_matching_holdout_can_add_fit_constraints(self):
        # 只有同长训练时，init 和 xorout 共享 24 个自由度。新长度会限制这些自由度。
        cap = self.manifest(make_records(TRAIN[:3], HOLDOUT[1:]))
        report = analyze(cap, self.hypotheses(profile()))
        model = report["profiles"][0]["models"][0]
        self.assertEqual(model["training"]["free_degrees"], 24)
        self.assertEqual(model["remaining"]["free_degrees"], 0)
        self.assertEqual(report["fit"]["status"], "holdout_contributed_fit_constraints")

    def test_holdout_flip_and_capture_conflict(self):
        # 首次翻位来自新消息，使训练仍通过且新验收失败。其次追加重复消息冲突。
        items = make_records(TRAIN, HOLDOUT)
        items[-1]["capture"]["crc_wire"] ^= 1
        h = self.hypotheses(profile())
        self.assertEqual(analyze(self.manifest(items), h)["fit"]["status"], "no_surviving_model")
        # 同载荷异尾既可能是采集错误，也可能是会话配置不一致，两者都不得绑定。
        duplicate = frame_record("replayed", TRAIN[0], "holdout", wire_tail(TRAIN[0]) ^ 1)
        report = analyze(self.manifest([*items, duplicate]), h)
        self.assertEqual(report["fit"]["status"], "capture_conflict")
        self.assertEqual(report["repeated_payload_conflicting_tails"], [["train-0", "replayed"]])

    def test_fixed_header_and_refout_aliases_remain_ambiguous(self):
        # 制造来源宣称与数据实质不一致的案例，验证标记也不会令工具自动认证。
        cap = self.manifest(make_records(TRAIN, HOLDOUT), kind="silicon_capture")
        h = self.hypotheses(profile(), profile("included-constant", coverage="header_payload"),
                            profile("wire-reflection-alias", refout=True, wire_order="lsb"))
        out = analyze(cap, h)
        self.assertEqual(out["fit"]["surviving_models"], 3)
        self.assertEqual(out["fit"]["status"], "ambiguous")
        self.assertEqual(out["silicon_binding"], "source_review_required")
        self.assertEqual(out["source"]["identity_status"], "declared_not_authenticated")

    def test_reflected_padded_and_byte_swapped_hypothesis(self):
        # 固定种子并只解 xorout，交叉覆盖参数部分已知的正规用法。
        cap = self.manifest(make_records(TRAIN, HOLDOUT, flipped=True))
        h = self.hypotheses(profile("reflected-padded", poly=[POLY], coverage="header_payload",
                                   packing="sign_extend24", refin=True, refout=True,
                                   wire_order="byte_reverse", augment_bits=24, init=INIT),
                            profile("wrong-coverage", coverage="last_sample", poly=[POLY]))
        out = analyze(cap, h)
        self.assertEqual(out["fit"]["status"], "unique_within_declared_hypotheses")
        self.assertEqual(out["profiles"][0]["models"][0]["remaining"]["representative_xorout"],
                         f"0x{XOROUT:06X}")

    def test_duplicate_holdout_and_poly_derivation_limit(self):
        # 重复捕获可以复核确定性，只有新消息构成预测，单条也不足以反推多项式。
        cap = self.manifest(make_records(TRAIN, TRAIN[:1]))
        out = analyze(cap, self.hypotheses(profile()))
        self.assertEqual(out["fit"]["status"], "no_distinct_holdout")
        self.assertEqual(out["lvds_fit_records"]["holdout_repeats_training"], ["holdout-0"])
        cap = self.manifest(make_records(TRAIN[:1], HOLDOUT))
        out = analyze(cap, self.hypotheses(profile()))
        self.assertEqual(out["fit"]["status"], "candidate_space_incomplete")

    def test_crc_arithmetic_public_check_string(self):
        # 通用 CRC-24/OPENPGP 的检验字符串仅作算术基准，与测试板协议无关。
        string = "".join(f"{b:08b}" for b in b"123456789")
        self.assertEqual(feed(string, 0x864CFB, 0xB704CE), 0x21CF02)


if __name__ == "__main__":
    unittest.main()
