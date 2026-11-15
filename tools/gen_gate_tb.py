#!/usr/bin/env python3
"""从综合网表生成门级 testbench，并输出逐拍轨迹用于与 RTL 比对。

为什么需要这个脚本
------------------
DC 把 `w_main_tbl[18][16][40]` 与 `w_sub_tbl[18][4][40]` 展开成
**扁平拼接端口**（源文件里写成 `.w_main_tbl({ \\w_main_tbl[0][0][39], ... })`），
而 RTL 用数组端口。同一份 TB 无法同时连接两者 —— VCS 会报 PCTM。
所以门级 TB 必须按网表的真实端口形态逐个连接。

踩过的坑（都实测过）
------------------
1. 端口名带方括号时不能写 `.w_main_tbl[0][0][39](...)` —— 非法语法。
   整个数组是**一个端口**，名字是 `w_main_tbl`。
2. 该端口不是数组类型，而是 **11520 位的扁平向量**
   （18*16*40）；`w_sub_tbl` 是 2880 位。声明成数组仍报 PCTM。
3. 激励必须与 RTL 侧**完全一致**，否则轨迹无法比对。
   本脚本生成同一套激励，两侧各跑一次，再 diff 轨迹。

用法:
    python3 tools/gen_gate_tb.py <netlist.v> <out_tb.sv> [--trace]
"""
import re
import sys


def parse_ports(path):
    lines = open(path).read().split("\n")
    start = None
    for i, l in enumerate(lines):
        if l.startswith("module adc_digital_ip_v21"):
            start = i
            break
    if start is None:
        raise SystemExit("找不到 module adc_digital_ip_v21")
    buf = ""
    for i in range(start, len(lines)):
        buf += lines[i] + "\n"
        if ");" in lines[i]:
            break
    m = re.search(r"\((.*?)\);", buf, re.S)
    raw = m.group(1).replace("\n", " ")
    # DC 写 `.w_main_tbl({ \w_main_tbl[0][0][39], ... })`：
    # 这是**一个**端口，名字 w_main_tbl；里面是它的位拼接。
    for nm in ("w_main_tbl", "w_sub_tbl"):
        raw = re.sub(r"\.%s\(\{[^}]*\}\)" % nm, nm, raw)
    ports = [p.strip().replace("\\", "") for p in raw.split(",") if p.strip()]
    return ports


INPUTS = [
    "prepare_valid", "prepare_tag", "prepare_word", "prepare_h", "prepare_v",
    "prepare_half", "prepare_coin", "prepare_U", "prepare_flash",
    "prepare_qid", "stop_i", "sample_event", "input_captured",
    "residue_captured", "hold_ack", "ev_start_i", "ev_bit_valid_i",
    "ev_bit_i", "ev_bit_index_i", "ev_event_index_i", "ev_qid_i",
    "ev_flash_valid_i", "ev_flash_code_i", "sched_ext_sel",
    "sched_acq", "sched_conv", "sched_idle", "sched_ids",
    "sched_seed_we", "sched_seed", "adc2_valid_i", "adc2_raw_i",
    "adc2_range_err_i", "cfg_epoch", "b_q24_cfg",
]

IN_W = {
    "prepare_tag": 32, "prepare_word": 6, "prepare_h": 3, "prepare_v": 3,
    "prepare_U": 3, "prepare_flash": 3, "ev_bit_index_i": 4,
    "ev_event_index_i": 4, "ev_flash_code_i": 3, "sched_acq": 18,
    "sched_conv": 18, "sched_idle": 18, "sched_ids": 40, "sched_seed": 32,
    "adc2_raw_i": 13, "cfg_epoch": 8, "b_q24_cfg": 27,
}

OUT_W = {
    "acq_sel_o": 18, "conv_sel_o": 18, "idle_sel_o": 18,
    "rdac_pre_state_o": 72, "pre_wr_o": 18, "rdac_post_state_o": 360,
    "post_wr_o": 18, "node_post_o": 160, "node_pre_o": 32, "node_ids_o": 40,
    "node_tag_o": 32, "node_h_o": 3, "node_v_o": 3, "adc2_rsp_tag_o": 32,
    "adc2_rsp_raw_o": 13, "out_code": 20, "out_tag": 32, "k_o": 10,
    "final_k_o": 10, "tag_o": 32, "post_slot_o": 160,
}

# 轨迹里记录的关键字段（两侧必须完全一致，否则 diff 无意义）
TRACE = [
    ("prepare_ready", "%0b"), ("out_valid", "%0b"), ("out_code", "%0h"),
    ("out_sat", "%0b"), ("out_err", "%0b"), ("out_tag", "%0h"),
    ("acq_sel_o", "%0h"), ("conv_sel_o", "%0h"), ("idle_sel_o", "%0h"),
    ("pre_wr_o", "%0h"), ("post_wr_o", "%0h"), ("adc2_capture_arm_o", "%0b"),
    ("adc2_rsp_valid_o", "%0b"), ("adc2_rsp_tag_o", "%0h"),
    ("apply_fire_o", "%0b"), ("k_o", "%0h"), ("final_applied_o", "%0b"),
    ("final_k_o", "%0h"), ("err_o", "%0b"), ("done_o", "%0b"),
    ("node_valid_o", "%0b"), ("node_ids_o", "%0h"),
]


def main():
    if len(sys.argv) < 3:
        raise SystemExit("用法: gen_gate_tb.py <netlist.v> <out.sv>")
    netlist, out = sys.argv[1], sys.argv[2]
    ports = parse_ports(netlist)
    print(f"解析到 {len(ports)} 个端口")

    L = []
    A = L.append
    A("`timescale 1ns/1ps")
    A("// 自动生成：不要手改。由 tools/gen_gate_tb.py 从综合网表生成。")
    A("// 端口形态与网表严格一致（权重表是 11520/2880 位扁平向量）。")
    A("module tb_gate;")
    A("")
    A("    logic clk = 0;")
    A("    always #5 clk = ~clk;")
    A("    logic rst_n = 0;")
    A("")
    for nm in INPUTS:
        if nm not in ports:
            continue
        w = IN_W.get(nm)
        A(f"    logic [{w-1}:0] {nm} = '0;" if w else f"    logic {nm} = 1'b0;")
    for nm in ports:
        if nm in INPUTS or nm in ("clk", "rst_n", "w_main_tbl", "w_sub_tbl"):
            continue
        w = OUT_W.get(nm, 1)
        A(f"    wire [{w-1}:0] {nm};" if w > 1 else f"    wire {nm};")
    A("")
    A("    // 权重表：DC 展开后的扁平拼接端口，位宽 18*16*40 / 18*4*40")
    A("    logic [11519:0] w_main_tbl;")
    A("    logic [2879:0]  w_sub_tbl;")
    A("")

    conns = []
    for p in ports:
        if p in ("clk", "rst_n") or p in INPUTS or p in ("w_main_tbl", "w_sub_tbl"):
            conns.append(f"        .{p}({p})")
        else:
            conns.append(f"        .{p}({p})")
    A("    adc_digital_ip_v21 dut (")
    A(",\n".join(conns))
    A("    );")
    A("")
    A("    int fd;")
    A("")
    A("    // 逐字段写轨迹：早前手数格式符个数，改字段必然对不上（踩过 MEAFFS）")
    A("    always @(negedge clk) if (rst_n) begin")
    A('        $fdisplay(fd, "T %0d", $time);')
    for nm, fmt in TRACE:
        if nm in ports:
            A(f'        $fdisplay(fd, "{nm} {fmt}", {nm});')
    A('        $fdisplay(fd, "---");')
    A("    end")
    A("")
    A("    initial begin")
    A("        reg [1023:0] opath;")
    A('        if (!$value$plusargs("OUT=%s", opath)) opath = "trace_gate.txt";')
    A('        fd = $fopen(opath, "w");')
    A('        if (fd == 0) begin $display("FATAL: cannot open %0s", opath); $finish; end')
    A("")
    A("        // 名义权重：主列 4Cu/16 = 268435456；子列 1/32,1/64,1/128,1/128")
    A("        w_main_tbl = {576{40'd268435456}};")
    A("        w_sub_tbl  = {72{40'd33554432, 40'd16777216, 40'd8388608, 40'd8388608}};")
    A("")
    A("        repeat (3) @(negedge clk);")
    A("        rst_n = 1;")
    A("        repeat (4) @(negedge clk);")
    A("")
    A("        // 一笔完整事务：与 RTL 侧 tb_trace 的激励逐字一致")
    A("        @(negedge clk);")
    A("        prepare_valid = 1; prepare_tag = 32'hA1; prepare_word = 6'd63;")
    A("        prepare_h = 3'd0; prepare_v = 3'd3; prepare_qid = 1'b0;")
    A("        prepare_U = 3'd5; prepare_flash = 3'd7;")
    A("        @(posedge clk); @(negedge clk);")
    A("        prepare_valid = 0;")
    A("        sample_event = 1; input_captured = 1;")
    A("        @(posedge clk); @(negedge clk);")
    A("        sample_event = 0; input_captured = 0;")
    A("")
    A("        // 逐事件应用：先赋值、跨 posedge、再撤（与 e2e 一致）")
    A("        for (int i = 0; i < 5; i++) begin")
    A("            ev_bit_valid_i = 1; ev_bit_i = (i == 0) ? 1'b1 : 1'b0;")
    A("            ev_bit_index_i = 4'(9 - i); ev_event_index_i = 4'(1 + i);")
    A("            ev_flash_valid_i = (i == 0); ev_flash_code_i = 3'd7;")
    A("            @(posedge clk); @(negedge clk);")
    A("        end")
    A("        ev_bit_valid_i = 0; ev_flash_valid_i = 0;")
    A("        repeat (12) @(negedge clk);")
    A("")
    A("        adc2_valid_i = 1; adc2_raw_i = 13'd5120;")
    A("        @(posedge clk); @(negedge clk);")
    A("        adc2_valid_i = 0;")
    A("        hold_ack = 1;")
    A("        repeat (24) @(negedge clk);")
    A("        hold_ack = 0;")
    A("        repeat (12) @(negedge clk);")
    A("")
    A("        $fclose(fd);")
    A('        $display("GATE_TRACE_DONE k=%0d out_valid=%0b out_code=0x%05X",'
      " k_o, out_valid, out_code);")
    A("        $finish;")
    A("    end")
    A("")
    A("endmodule")

    open(out, "w").write("\n".join(L) + "\n")
    print(f"写出 {out}")


if __name__ == "__main__":
    main()
