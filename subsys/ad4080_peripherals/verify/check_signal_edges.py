#!/usr/bin/env python3
# =============================================================================
# 文件名     : check_signal_edges.py
# 模块名     : AD4080 外围产品台架边沿复核
# 版本       : v1.0
# 功能       : 从 PIN_EDGE 独立复算 Fig.83、Fig.99、Fig.89 以及工程单字记录。
# 设计说明   : 图的离散事件来自 Rev. B p55/p60/p68，台架周期和数值刺激是本包
#              的工程合同。CSV expected 列绝不参与判定，等待拍和物理 PAD/DCO
#              延迟不作为已闭合的器件时序。连续 IO 使用实际采到的每个时钟拍。
# 变更记录   : 初始版本，暂无变更记录。
# =============================================================================
"""用法为 check_signal_edges.py simulation.log 或 headered.csv，可加 --json。"""

import argparse
import csv
from dataclasses import dataclass
import json
from pathlib import Path
import re
import sys


# =============================================================================
# 1. 台架口径与原始行读取
# =============================================================================
CORE_PS = 10_000
CNV_PS = 60_000      # Fig.83/99 台架的已译码 CNV 每 6 个 core 拍，本值不是商品速率
IO_PS = 22_000       # 逻辑 DDR 位对采送拍，物理 DCO/PAD 频率与建立时间另行测量
SINC1_INPUTS = (100, 300, 500, 700, 900, 1100)
# 单字例专查头末同时装入首字、末字。它独立于 Fig.99 的三笔实际 SRAM 写入。
SINGLE_WORD = 0x00123
CRC_POLY = 0x864CFB  # 产品试验注入的工程 CRC24，省略 x^24，覆盖 0xAA + 全部 20 位字
CRC_INIT = 0xB704CE  # 正序无反射，最终异或为 0
CASES = {"FIG83", "FIG99", "FIG89", "FIG89_W1"}
IO_FIELDS = {"BEAT_VALID", "BEAT_READY", "BEAT_PAIR", "BEAT_KIND", "BEAT_FIRST", "BEAT_LAST",
             "FIRST_WORD_LOAD", "LAST_WORD_LOAD", "FIFO_FULL", "FIFO_READ_DONE", "PHYSICAL_READS"}


@dataclass(frozen=True)
class Sample:
    time_ps: int
    observed: str
    status: str
    line: int


def integer(text):
    # 原日志为十进制。CSV 中同时接受明确的 0x/0b，避免编辑器导出形式改变判据。
    return int(text, 10) if re.fullmatch(r"[+-]?\d+", text) else int(text, 0)


def read_rows(path):
    rows = {}
    for line_no, raw in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        text = raw.strip()
        # 完整仿真文本含失败时，已打印的一段正例不能用于归档一场失败运行。
        if re.search(r"Assertion failed|Verilog \$stop|^%?Error:|^Aborting", text):
            raise ValueError(f"第 {line_no} 行有台架失败 {text}")
        # 只从日志提取 PIN_EDGE，CSV 表头的 index/edge_index、status/verdict 等不视为采样。
        if not re.match(r'^"?PIN_EDGE"?(?:\s*,|$)', text):
            continue
        row = next(csv.reader([text], strict=True))
        if len(row) == 9 and row[1:3] == ["case", "clock"]:
            continue
        if len(row) != 9:
            raise ValueError(f"PIN_EDGE 第 {line_no} 行需要 9 列")
        _, case, clock, edge, stamp, name, observed, _expected, status = map(str.strip, row)
        if case not in CASES or not clock or not name or status not in ("PASS", "UNBOUND"):
            raise ValueError(f"PIN_EDGE 第 {line_no} 行的场景、信号或状态无效 {case}/{clock}/{name}/{status}")
        try:
            index, time_ps = int(edge, 10), int(stamp, 10)
        except ValueError as exc:
            raise ValueError(f"PIN_EDGE 第 {line_no} 行需要十进制索引和 ps 时间") from exc
        key = (case, clock, index, name)
        if min(index, time_ps) < 0 or key in rows:
            raise ValueError(f"PIN_EDGE 第 {line_no} 行负索引、负时间或重复观测 {key}")
        rows[key] = Sample(time_ps, observed, status, line_no)
    if not rows:
        raise ValueError("日志中没有 PIN_EDGE 采样")
    return rows


class Trace:
    """逐拍观测与已核项目。夹具写出的 PASS 标签自己不能证明数值。"""

    def __init__(self, rows):
        self.rows = rows
        self.used = set()

    def row(self, case, clock, index, signal):
        key = (case, clock, index, signal)
        if key not in self.rows:
            raise ValueError(f"缺少 {case}/{clock} #{index} {signal}")
        self.used.add(key)
        return self.rows[key]

    def number(self, case, clock, index, signal, *, witness=False):
        sample = self.row(case, clock, index, signal)
        # 有效位对、状态和数据要有可核的测量。预取计数及等待中的 valid 是观测见证，
        # 其逐拍幅值没有指定厂商时差；可读数但保留 UNBOUND 的地位。
        if sample.status != "PASS" and not witness:
            raise ValueError(f"第 {sample.line} 行 {case}/{clock} #{index} {signal} 未给受验测量")
        try:
            return integer(sample.observed)
        except ValueError as exc:
            raise ValueError(f"第 {sample.line} 行 {case}/{clock} #{index} {signal} 的观察值非整数") from exc

    def expect(self, case, clock, index, signal, required, *, witness=False):
        # required 来自调用处的手册/夹具模型，永远无需读取同一 CSV 行的 expected。
        got = self.number(case, clock, index, signal, witness=witness)
        if got != required:
            raise ValueError(f"{case}/{clock} #{index} {signal} 实测 {got}，独立期望 {required}")
        return got

    def times(self, case, clock, start, length, period, phase, fields=None):
        by_index = {}
        # 复钟后的一笔独立端口观察可以复用索引。只有指定字段参加连续 IO 窗口判定。
        for (which, domain, index, name), row in self.rows.items():
            if which == case and domain == clock and (fields is None or name in fields):
                by_index.setdefault(index, set()).add(row.time_ps)
        if not by_index:
            raise ValueError(f"缺少 {case}/{clock} 整个观测窗口")
        indices = sorted(by_index)
        stop = start + length if length is not None else indices[-1] + 1
        # 区间计数及端点足以验证无缺拍，不按输入最大的索引分配整段内存。
        if len(indices) != stop - start or indices[0] != start or indices[-1] != stop - 1:
            raise ValueError(f"{case}/{clock} 索引缺行、跳拍或平移，实际 {indices}，要求 {start}…{stop - 1}")
        stamps = {}
        for index in indices:
            times = by_index[index]
            if len(times) != 1:
                raise ValueError(f"{case}/{clock} #{index} 同一沿存在不同 ps 时间")
            stamps[index] = next(iter(times))
        # 原始时刻也须逐拍连续，即使导出器对删掉的时钟重新编号，也不能掩盖间隙。
        for index in indices:
            if not phase(stamps[index]) or stamps[index] != stamps[start] + (index - start) * period:
                raise ValueError(f"{case}/{clock} #{index} 时间戳不符合台架 {period} ps 周期或采样相位")
        return stamps


# =============================================================================
# 2. Fig.83 与 Fig.99 的 CNV 观察沿
# =============================================================================
def cnv_times(trace, case, length):
    return trace.times(case, "CNV", 1, length, CNV_PS, lambda t: t % CORE_PS == CORE_PS // 2)


def fig83(trace):
    case = "FIG83"
    times = cnv_times(trace, case, 8)
    above = {}
    sticky = 0
    # INPUT_ABOVE 是台架给定越阈标签。该检查还不测模拟阈值和比较器传播。
    # p55 的事件由首次越阈采样后两个 CNV 沿兑现。恢复时只撤 ALERT，W1C 前保留检测位。
    for edge in times:
        above[edge] = trace.expect(case, "CNV", edge, "INPUT_ABOVE", int(3 <= edge < 6))
        active = above.get(edge - 2, 0)
        sticky |= active
        trace.expect(case, "CNV", edge, "ALERT_N", 1 - active)
        trace.expect(case, "CNV", edge, "HI_STATUS", sticky)
    trace.expect(case, "SPI", 1, "HI_STATUS_BEFORE_W1C", 1)
    trace.expect(case, "SPI", 2, "HI_STATUS_AFTER_W1C", 0)
    before = trace.row(case, "SPI", 1, "HI_STATUS_BEFORE_W1C").time_ps
    after = trace.row(case, "SPI", 2, "HI_STATUS_AFTER_W1C").time_ps
    if not times[8] < before < after:
        raise ValueError("Fig.83 的 SPI sticky/W1C 读回须按顺序发生在 CNV 观测之后")
    return {"sample_edges": len(times), "assert_edge": 5, "restore_edge": 8}


def fig99(trace):
    case = "FIG99"
    times = cnv_times(trace, case, 10)
    written = []
    # 仅针对 p68 的 SINC1 D=2 WM=3 示例。p69 通式和 p67 另一个建立窗不替换该示例。
    expected_words = tuple((a + b) // 2 for a, b in zip(SINC1_INPUTS[::2], SINC1_INPUTS[1::2]))
    for edge in times:
        advance = int(edge in (6, 8, 10))
        trace.expect(case, "CNV", edge, "FILTER_SYNC_N", int(edge != 1))
        trace.expect(case, "CNV", edge, "FILTER_RESULT_RDY_N", int(edge not in (5, 7, 9)))
        trace.expect(case, "CNV", edge, "FIFO_WRITE", advance)
        trace.expect(case, "CNV", edge, "FIFO_FULL", int(edge == 10))
        if advance:
            addr = len(written)
            trace.expect(case, "CNV", edge, "FIFO_WRITE_ADDR", addr)
            written.append(trace.expect(case, "CNV", edge, "FIFO_WRITE_DATA", expected_words[addr]))
        elif any((case, "CNV", edge, signal) in trace.rows for signal in ("FIFO_WRITE_ADDR", "FIFO_WRITE_DATA")):
            raise ValueError(f"Fig.99 在无写入的 E{edge} 报告 SRAM 地址或数据")
    low = trace.number(case, "CNV", 1, "FILTER_SYNC_STABLE_PS")
    high = trace.number(case, "CNV", 2, "FILTER_SYNC_STABLE_PS")
    # E1 先见低，E2 前已释放。p68 要求提前至少 15 ns 与 5 ns；释放不能早于采低的 E1。
    if not 15_000 <= low <= times[1] or not 5_000 <= high < times[2] - times[1]:
        raise ValueError(f"Fig.99 SYNC 建立窗无效，E1 低 {low} ps，E2 高 {high} ps")
    return times[10], tuple(written), {"sync_setup_low_ps": low, "sync_setup_high_ps": high,
                                      "write_edges": [6, 8, 10], "fifo_full_edge": 10}


# =============================================================================
# 3. 真实 IO 时钟上的记录窗口
# =============================================================================
def engineering_crc(message, length):
    # 工程 CRC 由多项式长除重建。与台架逐位累加器不同；初始状态乘以 x^length。
    dividend = (message << 24) ^ (CRC_INIT << length)
    divisor = (1 << 24) | CRC_POLY
    while dividend.bit_length() > 24:
        dividend ^= divisor << (dividend.bit_length() - 25)
    return dividend


def decode_frame(bits, written):
    # 先对物理写见证解字段，再核工程尾。尾正确不能掩盖错误样本、字节序或 CRC 范围。
    samples = tuple(int(bits[i:i + 20], 2) for i in range(8, len(bits) - 24, 20))
    if bits[:8] != "10101010" or samples != tuple(word & 0xFFFFF for word in written):
        raise ValueError(f"Fig.89 读回头或样本无效，头 {bits[:8]}，样本 {samples}，实际写入 {written}")
    tail = int(bits[-24:], 2)
    expected = engineering_crc(int(bits[:-24], 2), len(bits) - 24)
    if tail != expected:
        raise ValueError(f"Fig.89 工程 CRC 实测 0x{tail:06X}，独立长除为 0x{expected:06X}")
    return f"0x{tail:06X}", samples


def fig89(trace, case, written):
    times = trace.times(case, "IO", 0, None, IO_PS, lambda t: t % (IO_PS // 2) == 0, IO_FIELDS)
    valid = [i for i in times if trace.number(case, "IO", i, "BEAT_VALID", witness=True) == 1]
    count = len(written)
    data_end = 4 + 10 * count   # 图89的 8 位头、20N 位样本、24 位尾，按实际位对采样
    pairs = data_end + 12
    if not valid or valid != list(range(valid[0], valid[0] + pairs)):
        raise ValueError(f"{case} 需要连续 {pairs} 个有效 IO 位对，实测 {valid}")
    first, last = valid[0], valid[-1]
    # 记录窗口从请求前开始。至少保留首发前一个实测拍，空拍总数留给跨域实现。
    if first == 0:
        raise ValueError(f"{case} 缺少首发前的 IO 见证")
    first_load, last_load = first + 3, first + 3 + 10 * (count - 1)
    before = -1
    prefetch_seen = False
    fields = []
    for i in sorted(times):
        active = first <= i <= last
        # 活动位对必须是实际带有效的观测。两端空拍可检查无伪交付，时长保留 UNBOUND。
        trace.expect(case, "IO", i, "BEAT_VALID", int(active), witness=not active)
        trace.expect(case, "IO", i, "BEAT_READY", 1)
        trace.expect(case, "IO", i, "FIRST_WORD_LOAD", int(i == first_load))
        trace.expect(case, "IO", i, "LAST_WORD_LOAD", int(i == last_load))
        # 台架采的是同一 IO 沿接受前的传输，与接受后 GPIO。首/末字进移位器才变标志，
        # 因此 3 是末头位对索引，下一实际位对才为 D19/D18。预取不能提前撤 FULL。
        trace.expect(case, "IO", i, "FIFO_FULL", int(i < first_load))
        trace.expect(case, "IO", i, "FIFO_READ_DONE", int(i >= last_load))
        reads = trace.number(case, "IO", i, "PHYSICAL_READS", witness=True)
        if not max(before, 0) <= reads <= count:
            raise ValueError(f"{case} IO #{i} 实际 SRAM 读计数越界或倒退 {before}->{reads}")
        prefetch_seen |= i < first_load and reads > 0
        before = reads
        if active:
            j = i - first
            trace.expect(case, "IO", i, "BEAT_FIRST", int(j == 0))
            trace.expect(case, "IO", i, "BEAT_LAST", int(j == pairs - 1))
            trace.expect(case, "IO", i, "BEAT_KIND", 0 if j < 4 else 1 if j < data_end else 2)
            pair = trace.number(case, "IO", i, "BEAT_PAIR")
            if not 0 <= pair <= 3:
                raise ValueError(f"{case} IO #{i} BEAT_PAIR 超出 2 位")
            fields.append(f"{pair:02b}")
    if not prefetch_seen or before != count:
        raise ValueError(f"{case} 无首字前实际预取的见证，或该记录实际 SRAM 读数不等于 {count}")
    tail, readback = decode_frame("".join(fields), written)
    trace.expect(case, "CORE", 1, "RECORD_DONE_AFTER_CRC", 1)
    done = trace.row(case, "CORE", 1, "RECORD_DONE_AFTER_CRC").time_ps
    # 截止是观察到 CORE 结案后的本域下降沿。若漏掉结案前最后的 I/O 拍，差距可达整周期。
    if done <= times[last] or not -(CORE_PS // 2) <= done - times[max(times)] < IO_PS:
        raise ValueError(f"{case} CORE 完成在尾之前，或 IO 追踪在完成附近缺拍")
    return {"first_pair_index": first, "last_pair_index": last, "first_word_load_index": first_load,
            "last_word_load_index": last_load, "logical_pairs": pairs, "crc_engineering": tail,
            "readback_raw20": readback, "trace_start_ps": times[0], "done_core_ps": done,
            "prefetch_before_full_clears": prefetch_seen}


# =============================================================================
# 4. 归档接口与未绑定边界
# =============================================================================
def audit(path):
    trace = Trace(read_rows(path))
    event = fig83(trace)
    full, written, filter_state = fig99(trace)
    multi = fig89(trace, "FIG89", written)
    single = fig89(trace, "FIG89_W1", (SINGLE_WORD,))
    if not full < multi["trace_start_ps"] < multi["done_core_ps"] < single["trace_start_ps"]:
        raise ValueError("Fig.99 满记录、Fig.89 三字完成、随后单字窗口的时间关系无效")
    # SPI 状态是另外一次 CSR 读，两个位可有相同 ps 时间，不作为独立串行 IO 拍。
    trace.expect("FIG89", "SPI", 1, "FIFO_FULL_CSR", 0)
    trace.expect("FIG89", "SPI", 2, "FIFO_READ_DONE_CSR", 1)
    trace.expect("FIG89", "STOPPED_IO", 1, "CLEAR_FIFO_READ_DONE", 0)
    trace.expect("FIG89", "IO", 2, "RESUME_FIFO_READ_DONE", 0)
    full_csr = trace.row("FIG89", "SPI", 1, "FIFO_FULL_CSR").time_ps
    done_csr = trace.row("FIG89", "SPI", 2, "FIFO_READ_DONE_CSR").time_ps
    clear = trace.row("FIG89", "STOPPED_IO", 1, "CLEAR_FIFO_READ_DONE").time_ps
    resume = trace.row("FIG89", "IO", 2, "RESUME_FIFO_READ_DONE").time_ps
    if full_csr != done_csr or not multi["done_core_ps"] < done_csr < clear < resume < single["trace_start_ps"]:
        raise ValueError("Fig.89 的同一 CSR 读回、停 I/O 清除、复钟观测的时刻顺序无效")
    # 即便逻辑序列都对，物理未验字段仍须标明，防止把作图占位解释为已达成测量。
    phy = trace.row("FIG89", "PHY", 0, "PAD_DCO_ALIGNMENT")
    if phy.observed != "UNBOUND" or phy.status != "UNBOUND":
        raise ValueError("PAD_DCO_ALIGNMENT 必须明确保留 UNBOUND")
    # 未消费的附带字段只统计，不提升为已核证据。UNBOUND 包括等拍时长与物理时差。
    return {"scope": "product-pin-edge-fixture", "fig83": event, "fig99": filter_state,
            "fig89": multi, "fig89_w1": single, "fifo_write_data": written,
            "cnv_period_ps": CNV_PS, "io_period_ps": IO_PS, "sinc1_d2_stimulus": SINC1_INPUTS,
            "crc_fixture": {"poly": hex(CRC_POLY), "init": hex(CRC_INIT), "xorout": 0,
                            "reflection": False, "coverage": "0xAA + N * 20 位"},
            "unbound": ["商品 FIFO CRC24 参数", "等待周期数", "PAD/DCO 延迟与采收相位"],
            "unreviewed_fields": sorted({key[3] for key in trace.rows.keys() - trace.used})}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("input", type=Path)
    p.add_argument("--json", action="store_true", help="输出有明确范围的 JSON 摘要")
    args = p.parse_args()
    try:
        result = audit(args.input)
    except (OSError, csv.Error, ValueError) as exc:
        print(f"AD4080_SIGNAL_EDGES FAIL {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print("AD4080_SIGNAL_EDGES PASS Fig83=8CNV Fig99=10CNV Fig89=46+26连续逻辑位对，实际写/读合格")
        print("ENGINEERING 配置 SINC1 D=2 WM=3 + 原始 WM=1，CRC poly=0x864CFB init=0xB704CE xor=0 msb-first")
        print("UNBOUND " + "、".join(result["unbound"]))
        if result["unreviewed_fields"]:
            print("UNREVIEWED " + "、".join(result["unreviewed_fields"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
