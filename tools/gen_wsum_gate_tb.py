#!/usr/bin/env python3
"""**接口变更警告（2026-11-16）**：权重表已从 14,400 根端口线改成
wsum_v21 内部寄存器堆 + 51 根配置口。本生成器仍按旧的扁平端口形态生成，
对当前 RTL 生成的网表**不适用**。等用新 RTL 重新综合后按新接口更新。
"""
"""从综合网表生成 wsum_v21 的门级 TB（权重表扁平端口适配）。

设计说明
--------
DC 把 `w_main_tbl[18][16][40]` 与 `w_sub_tbl[18][4][40]` 展平成
**扁平拼接端口**，源文件里写作：

    .w_main_tbl({ \\w_main_tbl[0][0][39] , \\w_main_tbl[0][0][38] , ... })

Verilog 拼接 `{a, b, c}` 中 **a 是最高位**，所以：
  - `\\w_main_tbl[0][0][39]`   -> 扁平向量的 **bit 11519**（MSB）
  - `\\w_main_tbl[17][15][0]`  -> 扁平向量的 **bit 0**（LSB）

**这是本脚本 v1.0 的错误所在**：早期版本写成
    w_main_flat[(s*16+j)*40 +: 40] = w_main_tbl[s][j]
把 [0][0] 放在最低 40 位，行列顺序整个接反。固定 ids=0 时
"最后一行有响应"正好是接反的表现，却被误判成"综合折叠了 17 行"。

正确写法（v2.0）：
    w_main_flat[11519 - (s*16+j)*40 -: 40] = w_main_tbl[s][j]
    w_sub_flat [2879  - (s*4 +j)*40 -: 40] = w_sub_tbl [s][j]

映射**从网表端口名自动生成并自检**，不猜展开顺序。

用法:
    python3 tools/gen_wsum_gate_tb.py <netlist_or_module.v> <out_tb.sv>
"""
import re
import sys

SRC_TB = "tb/v21/tb_wsum_scan.sv"


def parse_ports(path):
    """返回 (普通端口列表, w_main 拼接项, w_sub 拼接项)。"""
    src = open(path).read()
    i = src.index("module wsum_v21 (")
    j = src.index(");", i)
    blk = src[i:j]
    mains, subs = [], []
    m = re.search(r"\.w_main_tbl\(\{(.*?)\}\)", blk, re.S)
    if m:
        mains = [x.strip().replace("\\", "") for x in m.group(1).split(",") if x.strip()]
    m = re.search(r"\.w_sub_tbl\(\{(.*?)\}\)", blk, re.S)
    if m:
        subs = [x.strip().replace("\\", "") for x in m.group(1).split(",") if x.strip()]
    # 普通端口：去掉两个数组拼接后按逗号切
    plain = re.sub(r"\.w_main_tbl\(\{[^}]*\}\)", "w_main_tbl", blk, flags=re.S)
    plain = re.sub(r"\.w_sub_tbl\(\{[^}]*\}\)", "w_sub_tbl", plain, flags=re.S)
    raw = plain[plain.index("(") + 1:]
    ports = [p.strip().replace("\\", "") for p in raw.split(",") if p.strip()]
    return ports, mains, subs


def check_order(mains, subs):
    """自检：拼接首项必须是 [0][0][39]，末项必须是 [17][15][0]。"""
    ok = True
    if mains:
        if mains[0] != "w_main_tbl[0][0][39]" or mains[-1] != "w_main_tbl[17][15][0]":
            print(f"  WARN w_main 顺序异常: 首={mains[0]} 末={mains[-1]}")
            ok = False
        else:
            print("  OK  w_main: 首 [0][0][39] / 末 [17][15][0]（MSB→LSB）")
    if subs:
        if subs[0] != "w_sub_tbl[0][0][39]" or subs[-1] != "w_sub_tbl[17][3][0]":
            print(f"  WARN w_sub 顺序异常: 首={subs[0]} 末={subs[-1]}")
            ok = False
        else:
            print("  OK  w_sub : 首 [0][0][39] / 末 [17][3][0]（MSB→LSB）")
    return ok


def main():
    if len(sys.argv) < 3:
        raise SystemExit("用法: gen_wsum_gate_tb.py <verilog> <out.sv>")
    net, out = sys.argv[1], sys.argv[2]
    ports, mains, subs = parse_ports(net)
    print(f"端口 {len(ports)}: {ports}")
    print(f"w_main 拼接项 {len(mains)}，w_sub 拼接项 {len(subs)}")
    check_order(mains, subs)

    tb = open(SRC_TB).read()
    tb = tb.replace("module tb_wsum_scan;", "module tb_wsum_gate;")
    # 注意：源 TB 的声明行是 "    logic [39:0]  w_main_tbl [0:17][0:15];"
    # （两个空格），v1.0 用单空格匹配所以替换没生效，导致
    # w_main_flat 未声明（VCS 报 IND）。这里改用正则匹配。
    tb = re.sub(
        r"[ \t]*logic \[39:0\]\s+w_main_tbl\s*\[0:17\]\[0:15\];\n"
        r"[ \t]*logic \[39:0\]\s+w_sub_tbl\s*\[0:17\]\[0:3\];",
        "    // 网表侧权重表是扁平拼接端口（18*16*40 / 18*4*40）\n"
        "    logic [11519:0] w_main_flat;\n"
        "    logic [2879:0]  w_sub_flat;\n"
        "    // 局部数组视图：TB 激励按 [sid][j] 写，再打包到扁平向量\n"
        "    logic [39:0]  w_main_tbl [0:17][0:15];\n"
        "    logic [39:0]  w_sub_tbl  [0:17][0:3];", tb)
    assert "w_main_flat;" in tb, "扁平向量声明未插入"
    pack = [
        "    // 打包（v2.0 修正）：网表端口拼接首位是 MSB。",
        "    //   w_main_tbl[0][0][39]  -> flat[11519]",
        "    //   w_main_tbl[17][15][0] -> flat[0]",
        "    // v1.0 曾写成 [(s*16+j)*40 +: 40]，把 [0][0] 放在最低位，",
            "    // 行列顺序整个接反，造成最后一行有响应的假象。",
        "    always_comb begin",
        "        w_main_flat = '0;",
        "        w_sub_flat  = '0;",
        "        for (int s = 0; s < 18; s++) begin",
        "            for (int j = 0; j < 16; j++)",
        "                w_main_flat[11519 - (s*16 + j)*40 -: 40] = w_main_tbl[s][j];",
        "            for (int j = 0; j < 4; j++)",
        "                w_sub_flat[2879 - (s*4 + j)*40 -: 40] = w_sub_tbl[s][j];",
        "        end",
        "    end",
        "",
    ]
    # always_comb 必须放在 dut 例化之前、且在所有声明之后
    tb = tb.replace("    wsum_v21 dut (", "\n".join(pack) + "    wsum_v21 dut (")
    tb = tb.replace(".w_main_tbl(w_main_tbl),", ".w_main_tbl(w_main_flat),")
    tb = tb.replace(".w_sub_tbl(w_sub_tbl),", ".w_sub_tbl(w_sub_flat),")

    open(out, "w").write(tb)
    print(f"写出 {out}")


if __name__ == "__main__":
    main()
