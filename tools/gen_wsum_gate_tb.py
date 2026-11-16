#!/usr/bin/env python3
"""从综合网表生成 wsum_v21 的门级 TB（权重表扁平端口适配）。

为什么需要
----------
DC 把 `w_main_tbl[18][16][40]` 与 `w_sub_tbl[18][4][40]` 展平成
**扁平拼接端口**（源文件里写作 `.w_main_tbl({ \\w_main_tbl[0][0][39], ... })`），
而 RTL 侧是数组端口。同一份 TB 无法同时连接两者（VCS 报 PCTM）。

本脚本做的是：保持 TB 的**激励与记录逻辑完全不变**（直接复用
tb_wsum_scan.sv 的激励段），只替换端口连接层 —— 把数组拆成
逐 bit 赋值给扁平向量。这样两侧的激励是同一份代码，比对才有意义。

用法:
    python3 tools/gen_wsum_gate_tb.py <netlist_or_module.v> <out_tb.sv>
"""
import re
import sys

SRC_TB = "tb/v21/tb_wsum_scan.sv"


def parse_ports(path):
    src = open(path).read()
    i = src.index("module wsum_v21 (")
    j = src.index(");", i)
    blk = src[i:j]
    # 把 `.w_main_tbl({ ... })` 整体换成一个端口名
    for nm in ("w_main_tbl", "w_sub_tbl"):
        blk = re.sub(r"\.%s\(\{[^}]*\}\)" % nm, nm, blk, flags=re.S)
    raw = blk[blk.index("(") + 1:]
    ports = [p.strip().replace("\\", "") for p in raw.split(",") if p.strip()]
    return ports


def main():
    if len(sys.argv) < 3:
        raise SystemExit("用法: gen_wsum_gate_tb.py <verilog> <out.sv>")
    net, out = sys.argv[1], sys.argv[2]
    ports = parse_ports(net)
    print(f"端口 {len(ports)}: {ports}")

    tb = open(SRC_TB).read()
    # 1) 模块名
    tb = tb.replace("module tb_wsum_scan;", "module tb_wsum_gate;")
    # 2) 把数组声明换成扁平向量
    tb = tb.replace(
        "    logic [39:0] w_main_tbl [0:17][0:15];\n"
        "    logic [39:0] w_sub_tbl  [0:17][0:3];",
        "    // 网表侧权重表是扁平拼接端口（18*16*40 / 18*4*40）\n"
        "    logic [11519:0] w_main_flat;\n"
        "    logic [2879:0]  w_sub_flat;\n"
        "    // 局部数组视图：TB 激励仍按 [sid][j] 写，最后打包到扁平向量\n"
        "    logic [39:0] w_main_tbl [0:17][0:15];\n"
        "    logic [39:0] w_sub_tbl  [0:17][0:3];")
    # 3) 端口连接：权重表接扁平向量
    tb = tb.replace(".w_main_tbl(w_main_tbl),", ".w_main_tbl(w_main_flat),")
    tb = tb.replace(".w_sub_tbl(w_sub_tbl),", ".w_sub_tbl(w_sub_flat),")
    # 4) 每拍把数组打包成扁平向量（位序：先 [0][0][39..0]，再 [0][1][39..0] ...）
    pack = []
    pack.append("    // 打包：网表端口位序为 [sid][j][bit]，从最高位开始")
    pack.append("    always_comb begin")
    pack.append("        w_main_flat = '0;")
    pack.append("        w_sub_flat  = '0;")
    pack.append("        for (int s = 0; s < 18; s++) begin")
    pack.append("            for (int j = 0; j < 16; j++)")
    pack.append("                w_main_flat[(s*16 + j)*40 +: 40] = w_main_tbl[s][j];")
    pack.append("            for (int j = 0; j < 4; j++)")
    pack.append("                w_sub_flat[(s*4 + j)*40 +: 40] = w_sub_tbl[s][j];")
    pack.append("        end")
    pack.append("    end")
    pack.append("")
    # 插在 dut 例化之前
    tb = tb.replace("    wsum_v21 dut (", "\n".join(pack) + "\n    wsum_v21 dut (")
    open(out, "w").write(tb)
    print(f"写出 {out}")


if __name__ == "__main__":
    main()
