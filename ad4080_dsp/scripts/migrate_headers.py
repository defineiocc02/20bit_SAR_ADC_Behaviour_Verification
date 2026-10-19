#!/usr/bin/env python3
"""一次性迁移脚本：把既有 RTL 文件头改写为 docs/coding_standard.md 规定的格式。

规则：
  1. 保留原文件中 `module` 之前的全部注释文字（去掉纯装饰线），
     作为「设计说明」并入新文件头，不丢信息。
  2. 生成 @file/@brief/@details/@author/@date/@version +
     手册依据/时钟域/复位/延迟/吞吐/可综合性/依赖/修订记录。
  3. 在 module 之前补 `timescale 与 `default_nettype none，
     文件末尾补 `default_nettype wire。

用法：
    python3 scripts/migrate_headers.py --dry-run rtl/ad4080_top.sv
    python3 scripts/migrate_headers.py rtl/*.sv
"""

from __future__ import annotations

import sys
from pathlib import Path

# 每个文件的元数据。键必须与文件名一致。
META: dict[str, dict[str, str]] = {
    "ad4080_dsp_chain.sv": {
        "brief": "数字处理链：校正 -> 滤波选择 -> 可选 FIFO",
        "details": "三条滤波器并联但只有被选中的那条收到 valid_in；\n"
                   "FILTER_SEL 00 直通 / 01 SINC1 / 10 SINC5 / 11 SINC5+补偿。",
        "manual": "AD4080 Rev.B p.64-66 Table 28/29/62",
        "cdc": "clk 单时钟",
        "reset": "rst_n 异步低有效",
        "lat": "校正 2 clk + 滤波器延迟（见各滤波器文件头）",
        "thr": "1 sample/clk",
        "synth": "是；三条滤波器常驻，面积可优化（见 synthesis_review.md）",
        "deps": "sys_error_corr, sinc1_decim, sinc5_decim, sinc5_comp, result_fifo",
        "rev": "1.0.0 2026-10-19 初版\n"
               "//   2.0.0 2026-10-19 运行时 dec_sel；未选中滤波器不翻转",
    },
    "ad4080_top.sv": {
        "brief": "AD4080 数字子系统顶层",
        "details": "集成校正、滤波、FIFO 捕获、事件检测、GPIO 路由、LVDS 并串与配置 SPI。\n"
                   "单时钟域；异步引脚（sclk/cs_n/cnv/gpio）先 2FF 同步再进逻辑。",
        "manual": "AD4080 Rev.B p.14-95（逐条对照见 docs/ad4080_traceability.md）",
        "cdc": "clk 单时钟；sclk/cs_n/cnv_p/cnv_n/gpio_i 为异步输入，2FF 同步",
        "reset": "rst_n 异步低有效",
        "lat": "各子模块延迟见其文件头；顶层不额外加流水",
        "thr": "1 sample/clk（由 valid_in 节奏决定）",
        "synth": "是；16K FIFO 需替换为工艺 SRAM 宏（见 synthesis_review.md）",
        "deps": "ad4080_regs, spi_slave, cnv_ctrl, ad4080_dsp_chain,\n"
                "event_detect, fifo_capture, gpio_ctrl, lvds_framer",
        "rev": "1.0.0 2026-10-19 初版数字子集\n"
               "//   2.0.0 2026-10-19 按手册补齐寄存器/事件/GPIO/LVDS；数据通路改补码",
    },
    "ad4080_regs.sv": {
        "brief": "AD4080 配置寄存器堆（按 Rev.B 逐位实现）",
        "details": "实现 0x00/0x10-0x1C/0x1D-0x29 的读写、复位值与 W1C 语义；\n"
                   "写 0x1C 产生 FIFO arm 脉冲；WATERMARK 写值在输出侧钳位。",
        "manual": "AD4080 Rev.B p.74-94 Table 32-62",
        "cdc": "clk 单时钟（SPI 侧已由 spi_slave 过采样同步）",
        "reset": "rst_n 异步低有效；复位值逐寄存器对照手册",
        "lat": "写 1 clk；读组合",
        "thr": "1 次访问/clk",
        "synth": "是（纯寄存器 + 地址译码）",
        "deps": "无",
        "rev": "1.0.0 2026-10-19 初版子集\n"
               "//   2.0.0 2026-10-19 补齐 GENERAL_CONFIG/GPIO/LVDS/状态 W1C/watermark 钳位",
    },
    "spi_slave.sv": {
        "brief": "AD4080 配置 SPI 从机（Mode 3，含 CRC-8 与 SEND_STATUS）",
        "details": "16-bit 指令（1 命令 + 15 地址）+ 8-bit 数据；可选 8-bit CRC。\n"
                   "SCLK/CS 由 clk 过采样（2FF + 边沿检测），无第二时钟域。",
        "manual": "AD4080 Rev.B p.34-43, p.81-82",
        "cdc": "sclk/cs_n 异步，2FF 同步；要求 clk >= 8 x sclk",
        "reset": "rst_n 异步低有效",
        "lat": "写：最后一拍提交；读：数据相第一拍装载",
        "thr": "1 字节/帧",
        "synth": "是；CRC 为逐位异或，面积约 8 个触发器 + 少量组合",
        "deps": "无",
        "rev": "1.0.0 2026-10-19 初版（无 CRC）\n"
               "//   2.0.0 2026-10-19 增加 CRC-8、SEND_STATUS、时钟数检查",
    },
    "cnv_ctrl.sv": {
        "brief": "CNV 输入选择、同步与 tCONV 违反检测",
        "details": "CMOS/LVDS 由 0x16[0] 选择；2FF 同步后取上升沿；\n"
                   "忙时再次 CNV 记为 ADC_CNV_ERR（数字侧代理指标）。",
        "manual": "AD4080 Rev.B p.33, p.45, p.83",
        "cdc": "cnv_p/cnv_n 异步，2FF 同步",
        "reset": "rst_n 异步低有效",
        "lat": "同步 2 clk + 边沿检测 1 clk",
        "thr": "1 pulse/clk",
        "synth": "是",
        "deps": "无",
        "rev": "1.0.0 2026-10-19 初版\n"
               "//   2.0.0 2026-10-19 增加 busy 检测与 cnv_err",
    },
    "event_detect.sv": {
        "brief": "HI/LO 阈值与迟滞事件检测（补码）",
        "details": "阈值 12-bit 补码，LSB=1.46484 mV=256 ADC LSB，直接对齐 data[19:8]；\n"
                   "迟滞把清除点向回推，避免阈值附近抖动；标志粘滞、W1C 清除。",
        "manual": "AD4080 Rev.B p.54-56, p.90-92 Table 55-59",
        "cdc": "clk 单时钟",
        "reset": "rst_n 异步低有效",
        "lat": "sample_valid -> flag 1 clk",
        "thr": "1 sample/clk",
        "synth": "是（两个 13-bit 比较器）",
        "deps": "无",
        "rev": "1.0.0 2026-10-19 初版（无符号比较，错误）\n"
               "//   2.0.0 2026-10-19 改为补码比较；增加 hi/lo 脉冲输出",
    },
    "fifo_capture.sv": {
        "brief": "结果 FIFO 四种触发模式的捕获控制",
        "details": "mode 00 关闭 / 01 立即 / 10 事件后最近 WATERMARK /\n"
                   "11 环形保留全部 16K（事件后再写 WATERMARK 个）。",
        "manual": "AD4080 Rev.B p.57-63 Table 27, p.90-91 Table 55-56",
        "cdc": "clk 单时钟",
        "reset": "rst_n 异步低有效",
        "lat": "arm 后按模式计数；读延迟由 result_fifo 决定（1 clk）",
        "thr": "1 sample/clk",
        "synth": "是；组合比较链为关键路径候选",
        "deps": "result_fifo",
        "rev": "1.0.0 2026-10-19 初版\n"
               "//   2.0.0 2026-10-19 mode 11 四倍数对齐；读侧 captured 门控；event 改名 event_in",
    },
    "gpio_ctrl.sv": {
        "brief": "四路 GPIO 方向与功能路由（手册 SEL 编码）",
        "details": "SEL 为 4 bit：0000 SDO / 0001 FIFO_FULL / 0010 READ_DONE /\n"
                   "0011 FILTER_RDY(低) / 0100 HI / 0101 LO / 0110 ALERT(低) /\n"
                   "0111 GPIO_DATA / 1000 FILTER_SYNC(低) / 1001 EXT_EVENT。",
        "manual": "AD4080 Rev.B p.87-89 Table 52-54",
        "cdc": "gpio_i 已在顶层 2FF 同步；本模块为纯组合",
        "reset": "无状态（纯组合）",
        "lat": "组合",
        "thr": "组合",
        "synth": "是",
        "deps": "无",
        "rev": "1.0.0 2026-10-19 初版（3-bit SEL，编码错误）\n"
               "//   2.0.0 2026-10-19 改为 4-bit SEL 并补全 1000/1001 输入功能",
    },
    "lvds_framer.sv": {
        "brief": "LVDS 结果并串（1/2 lane，空闲重复上一帧）",
        "details": "MSB first；单 lane 20 个 bit_en/帧，双 lane 每 lane 10 个；\n"
                   "无新数据时重复上一帧（手册 Figure 97 行为）。SDR 数字模型。",
        "manual": "AD4080 Rev.B p.45-53, p.66 Figure 97",
        "cdc": "clk 单时钟；bit_en 为位速率使能",
        "reset": "rst_n 异步低有效",
        "lat": "valid_in -> 下一帧起点装载",
        "thr": "1 bit/lane/bit_en",
        "synth": "是；dco 为寄存输出",
        "deps": "无",
        "rev": "1.0.0 2026-10-19 初版（SDR，单 lane，只发一次）\n"
               "//   2.0.0 2026-10-19 增加 2 lane、重复帧、dco 寄存",
    },
    "result_fifo.sv": {
        "brief": "同步 FIFO 存储体（默认 16K x 20）",
        "details": "同步写、同步读、寄存器化读口；full/empty 由指针模减得到；\n"
                   "overflow/underflow 粘滞；full 时可选覆盖最旧（mode 11）。",
        "manual": "AD4080 Rev.B p.57（16K sample depth）",
        "cdc": "clk 单时钟",
        "reset": "rst_n 异步低有效；存储器内容不复位",
        "lat": "读 1 clk",
        "thr": "1 写 + 1 读/clk",
        "synth": "是；ASIC 需替换为 SRAM 宏（当前为 reg 数组 + ram_style）",
        "deps": "无",
        "rev": "1.0.0 2026-10-19 初版\n"
               "//   2.0.0 2026-10-19 增加 overwrite_en；复位统一为异步",
    },
    "sinc1_decim.sv": {
        "brief": "SINC1 抽取滤波器（运行时抽取率，补码）",
        "details": "块平均 y=(1/D)Σx；D=2^(code+1)，code>9 夹到 1024；\n"
                   "累加位宽 IN_W+11，四舍五入后输出。",
        "manual": "AD4080 Rev.B p.20 Table 7, p.64-66 Table 28/29/62",
        "cdc": "clk 单时钟",
        "reset": "rst_n 异步低有效；filter_sync 清相位",
        "lat": "D 个输入 + 1 clk",
        "thr": "1 sample/clk 输入，1/D 输出",
        "synth": "是；D=1024 时累加器 31 bit",
        "deps": "无",
        "rev": "1.0.0 2026-10-19 初版（编译期 DECIM，无符号）\n"
               "//   2.0.0 2026-10-19 运行时 dec_sel；改补码；块边界锁存",
    },
    "sinc5_decim.sv": {
        "brief": "SINC5（5 级 CIC）抽取滤波器（运行时抽取率，补码）",
        "details": "5 级积分 -> 抽取 D -> 5 级梳状；H(z)=((1-z^-D)/(1-z^-1))^5；\n"
                   "ACC_W=61（D=256），输出右移 5log2D 并饱和。",
        "manual": "AD4080 Rev.B p.20 Table 7, p.64-66 Table 28/29/62",
        "cdc": "clk 单时钟",
        "reset": "rst_n 异步低有效；filter_sync 清积分/梳状",
        "lat": "D 个输入 + 2 clk",
        "thr": "1 sample/clk 输入，1/D 输出",
        "synth": "是；5 级积分加法链是关键路径（见 synthesis_review.md）",
        "deps": "无",
        "rev": "1.0.0 2026-10-19 初版（编译期 DECIM，无符号）\n"
               "//   2.0.0 2026-10-19 运行时 dec_sel；改补码；归一化用锁存码",
    },
    "sinc5_comp.sv": {
        "brief": "SINC5 + 补偿滤波器（总抽取 2N，补码）",
        "details": "sinc5(N) 后接 2 倍抽取的 3 抽头 FIR（Q1.15）；\n"
                   "系数手册未给出，默认 (1,0,0)，结构上已实现额外 2 倍抽取。",
        "manual": "AD4080 Rev.B p.64-66 Table 28/29/62（系数未公开）",
        "cdc": "clk 单时钟",
        "reset": "rst_n 异步低有效",
        "lat": "N 个输入 + 2 clk（再 2 倍抽取）",
        "thr": "1 sample/clk 输入，1/(2N) 输出",
        "synth": "是；3 个乘法器（默认系数下被常量折叠）",
        "deps": "sinc5_decim",
        "rev": "1.0.0 2026-10-19 初版（缺 2 倍抽取，负系数零扩展）\n"
               "//   2.0.0 2026-10-19 增加 2 倍抽取；系数改补码切片",
    },
    "sys_error_corr.sv": {
        "brief": "系统误差校正 y = m·x + c（补码）",
        "details": "OFFSET 12-bit 补码（1 LSB=1 ADC LSB），GAIN 10-bit（0x200=1.0，\n"
                   "两端 ±1.5594%）；delta=x·(GAIN-512)·512>>>24；输出饱和并报 clip。",
        "manual": "AD4080 Rev.B p.20 Table 7, p.71, p.93 Table 60/61",
        "cdc": "clk 单时钟",
        "reset": "rst_n 异步低有效",
        "lat": "2 clk",
        "thr": "1 sample/clk",
        "synth": "是；20x11 乘法 + 20x10 乘法，建议单拍内完成或用 DSP",
        "deps": "无",
        "rev": "1.0.0 2026-10-19 初版（无符号，K=511）\n"
               "//   2.0.0 2026-10-19 改补码；K=512；合并重复表达式",
    },
    "cal_two_point.sv": {
        "brief": "两点增益/失调校准（常数除法）",
        "details": "由两点 (x1,y1)/(x2,y2) 求 m、c，再折算成 GAIN/OFFSET 寄存器值；\n"
                   "x1/x2 为编译期常数，除法退化为常数乘法。",
        "manual": "AD4080 Rev.B p.71（系统误差校正流程）",
        "cdc": "clk 单时钟",
        "reset": "rst_n 异步低有效",
        "lat": "3 clk",
        "thr": "1 次/3 clk",
        "synth": "是；两个常数乘法器",
        "deps": "无",
        "rev": "1.0.0 2026-10-19 初版\n"
               "//   2.0.0 2026-10-19 测试点改补码；INV_DX_K 位宽修正；512 字面量修正",
    },
}


def strip_prefix(text: str) -> tuple[str, str]:
    """Split into (comment_prefix, remainder starting at module)."""
    import re
    m = re.search(r"^module\s+\w+", text, re.MULTILINE)
    if not m:
        raise ValueError("no module declaration")
    return text[: m.start()], text[m.start():]


def clean_notes(prefix: str, fname: str) -> list[str]:
    """Turn the old banner block into plain prose lines."""
    out: list[str] = []
    for raw in prefix.splitlines():
        line = raw.strip()
        if not line.startswith("//"):
            continue
        body = line[2:]
        if body.startswith(" "):
            body = body[1:]
        if not body.strip():
            out.append("")
            continue
        if set(body) <= set("=-*#/"):
            continue
        if body.startswith(fname):
            continue
        if any(body.startswith(k) for k in
               ("手册依据", "时钟域", "复位", "延迟", "吞吐", "可综合性", "依赖", "修订记录")):
            continue
        out.append(body)
    # collapse leading/trailing blank lines
    while out and not out[0]:
        out.pop(0)
    while out and not out[-1]:
        out.pop()
    return out


def build_header(fname: str, meta: dict[str, str], notes: list[str]) -> str:
    bar = "//" + "=" * 77
    sub = "//" + "-" * 77
    lines = [
        bar,
        f"// @file    {fname}",
        f"// @brief   {meta['brief']}",
        "// @details " + meta["details"].replace("\n", "\n//          "),
        "// @project AD4080 数字子系统",
        "// @author  AD4080 Digital Team",
        "// @date    2026-10-19",
        "// @version 2.0.0",
        sub,
        f"// 手册依据 : {meta['manual']}",
        f"// 时钟域   : {meta['cdc']}",
        f"// 复位     : {meta['reset']}",
        f"// 延迟     : {meta['lat']}",
        f"// 吞吐     : {meta['thr']}",
        f"// 可综合性 : {meta['synth']}",
        "// 依赖     : " + meta["deps"].replace("\n", "\n//            "),
        "// 修订记录 :",
        "//   " + meta["rev"],
        bar,
    ]
    if notes:
        lines += ["// 设计说明（迁移自原文件头，保留原始意图）："]
        for n in notes:
            lines.append("//   " + n if n else "//")
        lines.append(bar)
    return "\n".join(lines) + "\n\n"


def migrate(path: Path, dry: bool) -> str:
    text = path.read_text(encoding="utf-8")
    fname = path.name
    if fname not in META:
        return f"SKIP {fname} (no metadata)"
    for tag in ("@file", "@brief", "@details", "@author", "@date", "@version",
                "手册依据", "时钟域", "复位", "可综合性"):
        if tag in text[:4000] and "@version 2.0.0" in text[:4000]:
            return f"SKIP {fname} (already migrated)"

    prefix, body = strip_prefix(text)
    notes = clean_notes(prefix, fname)
    header = build_header(fname, META[fname], notes)

    body = body.replace("`timescale", "`timescale")  # no-op
    if "`timescale" not in body:
        body = "`timescale 1ns/1ps\n\n" + body
    if "`default_nettype none" not in body:
        body = body.replace("`timescale 1ns/1ps\n",
                            "`timescale 1ns/1ps\n`default_nettype none\n", 1)
    if not body.rstrip().endswith("`default_nettype wire"):
        body = body.rstrip() + "\n\n`default_nettype wire\n"

    new = header + body
    if not dry:
        path.write_text(new, encoding="utf-8")
    return f"{'DRY ' if dry else 'OK  '} {fname}  (notes kept: {len(notes)} lines)"


def main(argv: list[str]) -> int:
    dry = "--dry-run" in argv
    files = [Path(a) for a in argv if not a.startswith("--")]
    if not files:
        print(__doc__)
        return 2
    for p in files:
        try:
            print(migrate(p, dry))
        except Exception as exc:  # noqa: BLE001 - migration tool reports and continues
            print(f"FAIL {p}: {exc}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
