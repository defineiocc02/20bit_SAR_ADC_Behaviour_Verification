# =============================================================================
# 文件名     : capture.py
# 模块名     : AD4080 FIFO 抓包解析
# 版本       : v1.0
# 功能       : 读取离线清单与 CSV，校验头、样本数、位序及来源身份。
# 设计说明   : SPI 按图 87/88 的参考读长解码，图示范围之外的位原样保留。
#              LVDS 按图 89 从已定位的单线记录剥离 24 个尾位。
# 变更记录   : 初始版本，暂无变更记录。
# =============================================================================
"""解析完整 FIFO 记录。CSV 时间单位为 ns，位串按时间从左至右。"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import hashlib
import io
import json
from pathlib import Path
import re


# =============================================================================
# 1. 输入身份及共用校验
# =============================================================================
HEADER = "10101010"
MODES = {"lvds", "spi1", "spi4"}
SOURCE_KINDS = {"synthetic", "silicon_capture", "adi_reference"}


@dataclass(frozen=True)
class Record:
    """已定位的线记录。words 为未缩放的 20 位二补码位型。"""

    record_id: str
    mode: str
    split: str
    words: tuple[int, ...]
    crc_wire: str | None
    extra_wire: str
    encoding: str
    file_sha256: str | None = None
    discarded_before: int = 0
    discarded_after: int = 0

    def summary(self):
        # 直接报尾的时间位序，避免把十六进制打印顺序误当成 CRC 寄存器顺序。
        if self.mode == "lvds":
            # LVDS 的头、有效载荷和尾全部按边沿计，和同等频率的 SPI 周期计数不同。
            unit, units, extra = "dco_edges", 8 + 20 * len(self.words) + 24, 0
        elif self.mode == "spi1":
            unit, units, extra = "dclk_cycles", 8 + 24 * len(self.words), len(self.extra_wire)
        else:
            # 四线的后缀以空白分隔物理引脚组。一组对应一个 DCLK，不按位数计时。
            unit, units, extra = "dclk_cycles", 8 + 6 * len(self.words), len(self.extra_wire.split())
        return {
            "id": self.record_id,
            "mode": self.mode,
            "split": self.split,
            "encoding": self.encoding,
            "header": "0xAA",
            "watermark": len(self.words),
            "wire_unit": unit,
            "documented_wire_units": units,
            "spi_extra_clocks": extra,
            "samples_raw20": [f"0x{word:05X}" for word in self.words],
            "crc_wire_bits": self.crc_wire,
            "spi_extra_wire": self.extra_wire,
            "file_sha256": self.file_sha256,
            "discarded_before": self.discarded_before,
            "discarded_after": self.discarded_after,
        }


def uint(value, width, field):
    """数值型输入使用十进制 JSON 整数或带 0x / 0b 前缀的字符串。"""
    if type(value) is str:
        try:
            value = int(value, 0)
        except ValueError as exc:
            raise ValueError(f"{field} 需要整数或带进制前缀的字符串") from exc
    # Python 将布尔视为整数子类。对抓包字段严格排除 true/false，保持位宽输入显式。
    if type(value) is not int or not 0 <= value < (1 << width):
        raise ValueError(f"{field} 超出无符号 {width} 位范围")
    return value


def read_json(path):
    # 清单的哈希针对原字节。解析重排和签入换行都会产生不同身份。
    payload = path.read_bytes()
    # 部分 PC 导出文本包含 UTF-8 BOM，解码时兼容它，哈希仍保留原字节。
    value = json.loads(payload.decode("utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} 顶层需要 JSON 对象")
    return value, hashlib.sha256(payload).hexdigest()


def bit_string(value, field):
    if not isinstance(value, str):
        raise ValueError(f"{field} 需要按时间排列的位串")
    # 空白可用于在长记录中人为分组，不影响线上时间顺序或有效位数。
    bits = "".join(value.split())
    if not bits or set(bits) - {"0", "1"}:
        raise ValueError(f"{field} 只接受 0、1 和空白")
    return bits


def source_info(value, manifest_sha):
    # 工具能核对声明、格式及文件哈希。来源声明仍须由台架操作者或原厂独立证明。
    if not isinstance(value, dict) or not isinstance(value.get("kind"), str) or value["kind"] not in SOURCE_KINDS:
        raise ValueError("source.kind 必须为 synthetic、silicon_capture 或 adi_reference")
    if value.get("device") != "AD4080":
        raise ValueError("source.device 必须明确为 AD4080")
    for key in ("reference", "session"):
        if not isinstance(value.get(key), str) or not value[key].strip():
            raise ValueError(f"source.{key} 必须给出证据引用或采集会话")
    return {"declared": value, "identity_status": "declared_not_authenticated",
            "manifest_sha256": manifest_sha}


# =============================================================================
# 2. 外部时钟/数据交换格式
# =============================================================================
def csv_rows(spec, root, required):
    # 外部记录路径相对清单位置解析，整目录搬迁后能继续核对文件身份。
    path = root / spec["path"]
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    expected = spec.get("sha256")
    if expected is not None and (type(expected) is not str or expected.lower() != digest):
        raise ValueError(f"{path.name} SHA-256 与清单声明不符")
    # DictReader 允许重复列名，显式拒绝后每个采样信号只有一个来源。
    reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig")))
    fields = reader.fieldnames or []
    if set(required) - set(fields) or len(fields) != len(set(fields)):
        raise ValueError(f"{path.name} CSV 列需要 {', '.join(required)}，列名须唯一")
    rows = []
    previous = None
    for row_index, row in enumerate(reader):
        # 事件表中两个同时间行的先后无法还原。完整状态需在同一行描述同一时刻。
        try:
            timestamp = Decimal(row["time_ns"])
        except (InvalidOperation, TypeError) as exc:
            raise ValueError(f"CSV row {row_index} time_ns 无效") from exc
        if not timestamp.is_finite() or (previous is not None and timestamp <= previous):
            raise ValueError(f"CSV row {row_index} time_ns 需要有限且严格递增")
        rows.append(row)
        previous = timestamp
    if not rows:
        raise ValueError(f"{path.name} CSV 没有数据行")
    return rows, digest


def digital_row(row, pins, row_index):
    # 对两级数字导出的歧义电平直接报错，避免把 X/Z 当作芯片真正传出的尾位。
    values = tuple(row[key] for key in pins)
    if any(value not in {"0", "1"} for value in values):
        raise ValueError(f"CSV row {row_index} 的数字电平必须为 0 或 1")
    return values


def spi_events(spec, root, mode):
    """用 DCS 对 SPI 事务定界，在 Mode 3 的上升沿采物理引脚。"""
    pins = ("sdob",) if mode == "spi1" else ("sdoa", "sdob", "sdoc", "sdod")
    # 每个拍保留实际引脚顺序。之后从图 88 转置为逻辑字，保留物理映射证据。
    digital = ("dcs", "dclk", *pins)
    rows, digest = csv_rows(spec, root, ("time_ns", *digital))
    transactions, active = [], None
    old_cs, old_clock, *old_data = digital_row(rows[0], digital, 0)
    # 起始已经在事务内的导出无法核对完整头，按未捕到 DCS 处理。
    if old_cs != "1":
        raise ValueError("SPI CSV 的首行需在 DCS 高期间，以支持完整事务定界")
    for index, row in enumerate(rows[1:], 1):
        cs, clock, *data = digital_row(row, digital, index)
        if cs != old_cs:
            # 不用 CSV 行序替代 DCS 与 DCLK 的物理先后，临界同时间戳应重抓。
            if clock != "1" or clock != old_clock:
                raise ValueError(f"SPI CSV row {index} 的 DCS 转换需要 DCLK 保持空闲高")
            if cs == "0":
                active = []
            else:
                transactions.append(active)
                active = None
        elif cs == "0" and old_clock == "0" and clock == "1":
            # 逻辑门限相同时间戳无法证明上升沿的稳定数据，避免误判 CRC。
            if data != old_data:
                raise ValueError(f"SPI CSV row {index} 的数据恰在 DCLK 采样沿变化")
            active.append("".join(data))
        old_cs, old_clock, old_data = cs, clock, data
    if active is not None:
        raise ValueError("SPI CSV 最后一条事务缺少 DCS 释放")
    # 多事务可按明确索引重复引用同一数据文件，避免用第一个偶然匹配的包代替预期包。
    if "transaction" not in spec and len(transactions) != 1:
        raise ValueError("SPI CSV 需恰好有一条事务或明确 transaction 索引")
    which = spec.get("transaction", 0)
    if type(which) is not int or not 0 <= which < len(transactions):
        raise ValueError("SPI transaction 超出捕获范围")
    return transactions[which], digest


def lvds_edges(spec, root):
    """取已选窗口内每个 DCO 边沿对应的稳定 DA 读值。"""
    rows, digest = csv_rows(spec, root, ("time_ns", "dco_edge", "da"))
    # CSV 行号从第一条数据开始数，结束位不含。支持同一采集文件的多个帧窗口。
    bounds = spec.get("rows", [0, len(rows)])
    if (not isinstance(bounds, list) or len(bounds) != 2 or any(type(i) is not int for i in bounds)
            or not 0 <= bounds[0] < bounds[1] <= len(rows)):
        raise ValueError("LVDS rows 需要给出有效的半开数据行窗口")
    edges, data = [], []
    # 时间表有帧前后空隙时，只对选中的 DDR 临界段要求完整交替关系。
    for index in range(*bounds):
        row = rows[index]
        edge = row["dco_edge"]
        if edge not in ("fall", "rise") or (edges and edges[-1] == edge):
            raise ValueError(f"LVDS CSV row {index} 的 DCO 边沿必须交替 fall/rise")
        edges.append(edge)
        data.extend(digital_row(row, ("da",), index))
    # 起始绝对极性由采集端确定。禁止按预计的 0xAA 自动翻相或删掉一条边沿。
    return "".join(data), digest


# =============================================================================
# 3. Rev. B 三种线记录拆分
# =============================================================================
def lvds_fields(bits, count, align):
    # 用配置 watermark 校验整个帧。仅从位数推 N 会把截断定长样本误认成短记录。
    size = 8 + 20 * count + 24
    offset = 0
    if align:
        # 8 位交替头会出现在样本内部。扫描仅接收一个完整候选，且显式回报裁去位数。
        offsets = [i for i in range(max(0, len(bits) - size + 1)) if bits.startswith(HEADER, i)]
        if len(offsets) != 1:
            raise ValueError(f"LVDS 窗口内完整的 0xAA 候选有 {len(offsets)} 个")
        offset = offsets[0]
    elif len(bits) != size:
        raise ValueError(f"LVDS watermark={count} 需要 {size} 位，实际 {len(bits)} 位")
    frame = bits[offset:offset + size]
    if not frame.startswith(HEADER):
        raise ValueError("LVDS 记录头不等于 0xAA，检查窗口和差分极性")
    # 图 89 结果紧密拼接成 20N 位，跨字节的奇数样本没有隐式补零。
    words = tuple(int(frame[i:i + 20], 2) for i in range(8, size - 24, 20))
    return words, frame[-24:], offset, len(bits) - offset - size


def raw20(spi_words):
    # 正负各校验一遍符号填充关系。截断成 20 位之前先留住错误证据。
    words = []
    for i, word in enumerate(spi_words):
        fill = 0xF if word & 0x80000 else 0x0
        if word >> 20 != fill:
            raise ValueError(f"SPI 样本 {i} 的 24 位符号扩展无效")
        words.append(word & 0xFFFFF)
    return tuple(words)


def spi_fields(beats, count, mode):
    slots = 24 if mode == "spi1" else 6
    size = 8 + slots * count
    if len(beats) < size:
        raise ValueError(f"SPI watermark={count} 至少需要 {size} 个 DCLK，实际 {len(beats)}")
    # 对每条 lane 单独解同步字，数据线接反或遗漏会在头阶段保留出线故障证据。
    if any("".join(lane) != HEADER for lane in zip(*beats[:8])):
        raise ValueError("SPI 每条已选 lane 的记录头都必须为 0xAA")
    # Fig. 88 物理 A/B/C/D 顺序对应半字节低位到高位，重组时每拍反转引脚序。
    payload = "".join(beat[::-1] for beat in beats[8:size])
    words = raw20(int(payload[i:i + 24], 2) for i in range(0, len(payload), 24))
    # 超出图示读长的位在报告中保留。即使碰好有 24 位，也仅是待鉴定的尾部。
    suffix = "".join(beats[size:]) if mode == "spi1" else " ".join(beats[size:])
    return words, suffix


def decoded_fields(spec, count, mode):
    if uint(spec["header"], 8, "header") != 0xAA:
        raise ValueError("decoded 记录头必须为 0xAA")
    values = spec.get("samples")
    if not isinstance(values, list) or len(values) != count:
        raise ValueError("decoded 样本数须等于 watermark")
    # 解码输入仍要求 SPI 全部 24 位，以便和原始捕获采用同一符号扩展检查。
    width = 20 if mode == "lvds" else 24
    words = tuple(uint(value, width, "sample") for value in values)
    if mode == "lvds":
        # 整数只是便捷书写。CRC wire 从左至右组成高到低的 24 位显示数。
        tail = f"{uint(spec['crc_wire'], 24, 'crc_wire'):024b}"
        return words, tail
    if "crc_wire" in spec:
        raise ValueError("SPI 的未知后缀请通过原始事件或线位提交")
    return raw20(words), None


def parse_record(value, root):
    # format 用于验证帧，train/holdout 只在 CRC 拟合时参与对应一侧。
    rid, mode, split = value["id"], value["mode"], value["split"]
    count, spec = value["watermark"], value["capture"]
    if not isinstance(rid, str) or not rid or mode not in MODES or split not in ("train", "holdout", "format"):
        raise ValueError("id、mode 或 split 无效")
    if type(count) is not int or not 1 <= count <= 16384:
        raise ValueError("watermark 应为 1 至 16,384 的整数字数")
    encoding = spec["encoding"]
    # 内嵌输入由清单整体哈希覆盖，CSV 额外回报原表哈希和选窗丢弃量。
    digest, before, after = None, 0, 0
    crc, suffix = None, ""
    if encoding == "decoded":
        words, crc = decoded_fields(spec, count, mode)
    elif mode == "lvds":
        if encoding == "lvds_edges_csv":
            bits, digest = lvds_edges(spec, root)
        elif encoding == "wire_bits":
            bits = bit_string(spec["bits"], "bits")
        else:
            raise ValueError("LVDS encoding 应为 decoded、wire_bits 或 lvds_edges_csv")
        align = spec.get("scan_header", False)
        if type(align) is not bool:
            raise ValueError("scan_header 需要 true 或 false")
        words, crc, before, after = lvds_fields(bits, count, align)
    else:
        if encoding == "spi_events_csv":
            beats, digest = spi_events(spec, root, mode)
        elif mode == "spi1" and encoding == "wire_bits":
            beats = bit_string(spec["bits"], "bits")
        elif mode == "spi4" and encoding == "abcd_beats":
            stream = "".join(spec["beats"].split())
            if not re.fullmatch(r"[0-9a-fA-F]+", stream):
                raise ValueError("abcd_beats 各个十六进制字符对应物理 ABCD 一拍")
            beats = [f"{int(beat, 16):04b}" for beat in stream]
        else:
            raise ValueError("不支持该 SPI 模式的 encoding")
        words, suffix = spi_fields(beats, count, mode)
    return Record(rid, mode, split, words, crc, suffix, encoding, digest, before, after)


def load_capture(path: Path):
    """返回来源声明及解析后的记录。报错携带记录身份，便于定位捕获条件。"""
    data, digest = read_json(path)
    if data.get("format") != "ad4080-fifo-capture-v1":
        raise ValueError("清单 format 需要 ad4080-fifo-capture-v1")
    source = source_info(data.get("source"), digest)
    items = data.get("records")
    if not isinstance(items, list) or not items:
        raise ValueError("清单必须含非空 records 列表")
    records, identities = [], set()
    for i, item in enumerate(items):
        try:
            # 单一会话固定捕获配置。任何一条坏帧都由用户显式检查后再从清单移除。
            record = parse_record(item, path.parent)
        except (ValueError, KeyError, TypeError, AttributeError, OSError) as exc:
            label = item.get("id", i) if isinstance(item, dict) else i
            raise ValueError(f"record {label}: {exc}") from exc
        # 样本内容可重复以量化重复性。身份重复会让留出证据和错误定位失去指代。
        if record.record_id in identities:
            raise ValueError(f"重复记录 id {record.record_id}")
        identities.add(record.record_id)
        records.append(record)
    return source, records
