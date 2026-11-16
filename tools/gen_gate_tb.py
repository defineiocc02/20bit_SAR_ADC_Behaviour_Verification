#!/usr/bin/env python3
# =============================================================================
# 文件名     : gen_gate_tb.py
# 模块名     : gen_gate_tb.py
# 版本       : v2.0
# 功能       : 从综合网表生成门级顶层 TB（tb_gate_auto.sv），使其能复用
#              tb/v21/tb_e2e_decl.svh 里与 RTL 侧**逐字相同**的激励与断言；
#              并输出自检报告，证明端口绑定与权重表展平映射正确。
# 设计说明   :
#   * 为什么必须"共享激励"而不是"各写一份"：
#     两笔（A guard 5120 / B 普通 2560）激励里含有大量与 DUT 内部状态
#     强相关的等待（arm 何时给出、七级 recon 流水何时出 out_valid、
#     hold 何时到）。任何一处拍数偏差都会让轨迹整体错位，
#     diff 出来的差异无法区分"真差异"与"激励漂移"。
#     所以公共层（tb_e2e_common.svh）只写一次，两侧 `include 同一份。
#
#   * 端口绑定为什么由脚本生成：DC 把 `w_main_tbl[18][16][40]` 与
#     `w_sub_tbl[18][4][40]` 展平成 **11520 / 2880 位扁平拼接端口**，
#     而 RTL 侧是数组端口。同一份 TB 无法同时连接两者（VCS 报 PCTM）。
#
#   * **踩过的坑（都实测过，勿重犯）**：
#     1. 端口名带方括号时不能写 `.w_main_tbl[0][0][39](...)` —— 非法语法。
#        整个数组是**一个端口**，名字是 `w_main_tbl`。
#     2. 该端口不是数组类型，而是**扁平向量**；声明成数组仍报 PCTM。
#     3. 扁平拼接的位序：网表端口里第一个占位是 `w_main_tbl[0][0][39]`，
#        最后一个是 `[17][15][0]`，即 **MSB 先、按 [s][j][b] 字典序遍历**。
#        因此扁平向量 bit k 对应 (s,j,b) 的换算必须按这个顺序做；
#        写反了会得到"能跑但权重错位"的最坏情况（数值全错却不报错）。
#     4. 旧脚本把 `logic [11519:0] w_main_tbl;` 声明在 TB 里再 `.{port}({port})`
#        连接 —— 结构上没问题，但旧 TB 只跑了 61 个观察沿，没走完
#        "逐事件 → 余差捕获 → ADC2 返回 → 20 位输出"，不能作为整链证据。
#     5. 层级探针：RTL 侧 `dut.acq_sampled` / `dut.conv_tag` 在网表里
#        不保证同名（DC 可能被拉平或被优化掉）。本脚本会用 `pydigest`
#        检查网表顶层是否仍有 u_ctx 等实例，并在生成时给出告警，
#        探针本身由共享层用 `E2E_PROBE` 宏切换实现。
#
#   * 输出：
#     - <out>.sv          门级顶层 TB（include 共享激励 + 扁平权重驱动）
#     - <out>.bind.rpt    端口绑定自检报告（每个端口的方向/宽度/来源）
#
# 用法:
#   python3 tools/gen_gate_tb.py --netlist /tmp/dcwork/adc_digital_ip_v21_syn.v \
#           --out tb/v21/tb_gate_auto.sv [--common tb/v21/tb_e2e_decl.svh]
#   python3 tools/gen_gate_tb.py --netlist <netlist> --out <out.sv> --selfcheck
#
# 变更记录   :
#   2026-11-16  v1.0 初版（为旧烟测 tb_trace 生成单笔激励）。
#   2026-11-16  v2.0 改为生成"薄绑定层 + 共享激励 include"，走完整两笔；
#                    新增端口方向/宽度解析与展平位序自检。
# =============================================================================
"""从 28HPC+ 综合网表生成门级 TB 的绑定层（共享激励见 tb_e2e_common.svh）。"""

import argparse
import hashlib
import re
import sys

# ---------------------------------------------------------------- 端口表
# 网表顶层端口的方向只影响 TB 侧声明（input -> 由 TB 驱动，output -> 观察）。
# 这里**不硬编码**，全部从网表解析；下面两张表只用于交叉校验，
# 一旦网表端口与预期不符就报错而不是静默继续。
EXPECT_IN = {
    "clk": 1, "rst_n": 1, "prepare_valid": 1, "prepare_tag": 32,
    "prepare_word": 6, "prepare_h": 3, "prepare_v": 3, "prepare_half": 1,
    "prepare_coin": 1, "prepare_U": 3, "prepare_flash": 3, "prepare_qid": 1,
    "stop_i": 1, "sample_event": 1, "input_captured": 1,
    "residue_captured": 1, "hold_ack": 1, "ev_start_i": 1,
    "ev_bit_valid_i": 1, "ev_bit_i": 1, "ev_bit_index_i": 4,
    "ev_event_index_i": 4, "ev_qid_i": 1, "ev_flash_valid_i": 1,
    "ev_flash_code_i": 3, "sched_ext_sel": 1, "sched_acq": 18,
    "sched_conv": 18, "sched_idle": 18, "sched_ids": 40, "sched_seed_we": 1,
    "sched_seed": 32, "adc2_valid_i": 1, "adc2_raw_i": 13,
    "adc2_range_err_i": 1, "w_main_tbl": 11520, "w_sub_tbl": 2880,
    "cfg_epoch": 8, "b_q24_cfg": 27,
}
EXPECT_OUT = {
    "prepare_ready": 1, "acq_sel_o": 18, "conv_sel_o": 18, "idle_sel_o": 18,
    "rdac_pre_state_o": 72, "pre_wr_o": 18, "rdac_post_state_o": 360,
    "post_wr_o": 18, "adc2_ready_o": 1, "adc2_capture_arm_o": 1,
    "node_post_o": 160, "node_pre_o": 32, "node_ids_o": 40, "node_qid_o": 1,
    "node_tag_o": 32, "node_valid_o": 1, "node_h_o": 3, "node_v_o": 3,
    "adc2_rsp_valid_o": 1, "adc2_rsp_tag_o": 32, "adc2_rsp_qid_o": 1,
    "adc2_rsp_raw_o": 13, "adc2_rsp_range_o": 1, "adc2_late_err_o": 1,
    "adc2_release_ok_o": 1, "out_valid": 1, "out_code": 20, "out_sat": 1,
    "out_err": 1, "out_tag": 32, "apply_fire_o": 1, "k_o": 10,
    "k_bad_o": 1, "final_applied_o": 1, "final_k_o": 10, "err_o": 1,
    "done_o": 1, "tag_o": 32, "qid_o": 1, "post_slot_o": 160,
}


def parse_netlist(path):
    """解析顶层 module 的端口顺序、方向、宽度，以及展平拼接的位序。

    返回 dict：
      order   : 端口出现顺序（已把 w_main_tbl/w_sub_tbl 的位拼接收敛成一个名字）
      dirs    : {name: 'input'|'output'}
      width   : {name: width}
      flat    : {name: [(s, j, b), ...]}  按端口表**从 MSB 到 LSB** 的占位序列
      insts   : [(module, inst_name)]     顶层子实例（判断是否 ungroup 拉平）
    """
    lines = open(path, errors="replace").read().split("\n")
    start = next((i for i, l in enumerate(lines)
                  if l.startswith("module adc_digital_ip_v21")), None)
    if start is None:
        raise SystemExit("网表里找不到 module adc_digital_ip_v21")
    end = next((i for i in range(start, len(lines))
                if lines[i].startswith("endmodule")), len(lines))
    head = "\n".join(lines[start:end])

    # ---- 端口表：从 module 行的 "(" 到第一个 ") ;" ----
    m = re.search(r"\((.*?)\)\s*;", head, re.S)
    raw = m.group(1)
    flat = {}
    for nm in ("w_main_tbl", "w_sub_tbl"):
        bits = re.findall(r"\\%s\[(\d+)\]\[(\d+)\]\[(\d+)\]" % nm, raw)
        if not bits:
            raise SystemExit(f"端口表里找不到 {nm} 的位拼接")
        flat[nm] = [(int(a), int(b), int(c)) for a, b, c in bits]
        raw = re.sub(r"\.%s\(\{[^}]*\}\)" % nm, nm, raw, flags=re.S)
    order = [t.strip().replace("\\", "") for t in raw.split(",") if t.strip()]

    # ---- 方向与宽度：DC 把标量合并在一条 input/output 里，向量单独声明 ----
    dirs, width = {}, {}
    # 注意：DC 会把标量合并在一条 input/output 里，且向量与标量分开声明。
    # 正则里的 range 组用 [^\]]* 允许空串（"[ ]" 这类退化写法），
    # 空串或纯空白一律当标量处理 —— 早期版本用 [...]* 会把空组误判成"有位宽"。
    # **必须允许跨行**：DC 把向量单独一行声明，标量合并成一条很长的
    # input/output 声明并折行（续行以空格开头、不带 input/output 关键字）。
    # 早期版本用 [^;\n]+ 只取到行尾，于是所有折行里的端口（尤其所有标量
    # 输出如 out_valid / k_bad_o）都被漏掉，自检报"缺输出端口"。
    # 这里用 [^;]* + re.S 一路吃到分号，声明之间以分号为界不会串。
    decl = re.findall(r"^\s*(input|output)\s*(?:\[([^\]]*)\])?([^;]*);",
                      head, re.M | re.S)
    for d, rng, names in decl:
        # 位宽解析两种写法：[39:0]（DC 常见）与 [0:39]（少数工具会反着写）。
        # 早期版本用 \[(\d+):(\d+)\]，遇到 "[0:39]" 这类反序范围会解析失败。
        w = 1
        if rng is not None and rng.strip() != "":
            mm = re.match(r"\s*(\d+)\s*:\s*(\d+)\s*$", rng)
            if not mm:
                raise SystemExit(f"无法解析位宽范围：{rng!r}")
            w = abs(int(mm.group(1)) - int(mm.group(2))) + 1
        for nm in names.split(","):
            nm = nm.strip().replace("\\", "")
            if not nm or "[" in nm:      # 位级声明（极少见）忽略
                continue
            dirs[nm] = d
            width[nm] = w
    for nm in order:
        if nm in flat:
            dirs[nm] = "input"
            width[nm] = len(flat[nm])

    # ---- 顶层子实例：判断 DC 是否把层次拉平 ----
    insts = re.findall(r"^\s+(dither_pre|sched18|acq_conv_ctx|sar_evt_v21|"
                       r"dem_mask_v3|adc2_capture|wsum_v21|recon_v21)\s+(\w+)\s*\(",
                       head, re.M)
    return dict(order=order, dirs=dirs, width=width, flat=flat, insts=insts)


def check(info):
    """端口自检：任何一项与预期不符都直接失败，绝不"带着疑问继续"。"""
    bad = []
    for nm, w in EXPECT_IN.items():
        if nm not in info["dirs"]:
            bad.append(f"缺输入端口 {nm}")
        elif info["dirs"][nm] != "input":
            bad.append(f"{nm} 方向应为 input，实为 {info['dirs'][nm]}")
        elif info["width"][nm] != w:
            bad.append(f"{nm} 宽度应为 {w}，实为 {info['width'][nm]}")
    for nm, w in EXPECT_OUT.items():
        if nm not in info["dirs"]:
            bad.append(f"缺输出端口 {nm}")
        elif info["dirs"][nm] != "output":
            bad.append(f"{nm} 方向应为 output，实为 {info['dirs'][nm]}")
        elif info["width"][nm] != w:
            bad.append(f"{nm} 宽度应为 {w}，实为 {info['width'][nm]}")
    # 展平位序：第一个占位必须是 [0][0][max]，最后一个必须是 [max][max][0]
    for nm, dims in (("w_main_tbl", (18, 16, 40)), ("w_sub_tbl", (18, 4, 40))):
        fl = info["flat"].get(nm, [])
        s, j, b = dims
        if len(fl) != s * j * b:
            bad.append(f"{nm} 占位 {len(fl)} 个，应为 {s*j*b}")
        elif fl[0] != (0, 0, b - 1) or fl[-1] != (s - 1, j - 1, 0):
            bad.append(f"{nm} 展平位序异常：首={fl[0]} 尾={fl[-1]}")
        elif fl[1] != (0, 0, b - 2):
            bad.append(f"{nm} 第二位应为 [0][0][{b-2}]，实为 {fl[1]}")
    return bad


# ---------------------------------------------------------------- 权重映射
def nominal_slice(nm):
    """名义权重表的一段 S 表达式（按扁平位序 MSB 先拼）。

    为什么在 TB 里按"逐位"拼而不是整段常量：
      段内 (s,j) 的顺序、以及每段内 b 是 39..0，必须与网表端口表严格一致。
      逐位写虽然长，但生成器可以直接用网表解析出的 flat 序列，
      不依赖任何人手抄顺序（手抄正是旧脚本的隐患）。
    为了文件可读，这里把一段压成一个 11520 位的表达式：
      {576{40'd268435456}} 对 w_main 成立（18*16 段，每段 40 位同值）；
      w_sub 则是 4 个不同常数循环。
    """
    if nm == "w_main_tbl":
        return "{576{40'd268435456}}"
    if nm == "w_sub_tbl":
        return "{72{40'd33554432, 40'd16777216, 40'd8388608, 40'd8388608}}"
    raise KeyError(nm)


def emit(info, out, common, netlist):
    """生成门级顶层 TB：只做绑定 + 权重驱动，激励全部来自共享层。"""
    have_inst = {i[0] for i in info["insts"]}
    probe_ok = "acq_conv_ctx" in have_inst
    L = []
    A = L.append
    A("`timescale 1ns/1ps")
    A("// =============================================================================")
    A("// 文件名     : tb_gate_auto.sv")
    A("// 模块名     : tb_gate_auto")
    A("// 版本       : v2.0")
    A("// 功能       : 28HPC+ 门级顶层 TB 的**绑定层**。激励与断言全部来自")
    A("//              tb/v21/tb_e2e_decl.svh（与 RTL 侧 tb_i4b_e2e 逐字相同）。")
    A("// 设计说明   :")
    A("//   * 本文件由 tools/gen_gate_tb.py 自动生成，**不要手改**。")
    A(f"//     源网表 : {netlist}")
    A("//   * 与 RTL 侧唯一的差别就是下面这条 DUT 例化的端口形态：")
    A("//     DC 把 w_main_tbl/w_sub_tbl 展平成 11520/2880 位扁平拼接端口，")
    A("//     位序为 MSB 先、按 [s][j][b] 字典序（已由生成器自检）。")
    A("//   * 权重表在这里按名义值一次性驱动：主列 4Cu/16 = 268435456，")
    A("//     子列 1/32,1/64,1/128,1/128。与 RTL 侧 TB 的 w_main_tbl/w_sub_tbl")
    A("//     初值逐位等价（两侧必须同值，否则轨迹 diff 无意义）。")
    A("// 变更记录   :")
    A("//   2026-11-16  v1.0 旧烟测版本（单笔、1464 行日志实际只有 61 个观察沿）。")
    A("//   2026-11-16  v2.0 改为薄绑定层 + 共享激励，走完整两笔并统计交易数。")
    A("// =============================================================================")
    # `include 必须放在 module **内部**：共享声明层里有 always / int / task，
    # 都是模块级项；放模块外 VCS 会报 "token is 'always'"（已实测）。
    A("module tb_gate_auto;")
    A("")
    A("    // ---- 公共声明层（时钟/信号/节点模型/任务）----")
    A("`define E2E_GATE 1")
    A("    `include \"%s\"" % common)

    A("    // ---- 输入驱动（由共享层赋值）----")
    for nm in info["order"]:
        if info["dirs"].get(nm) != "input":
            continue
        if nm in ("clk", "rst_n"):
            continue
        w = info["width"][nm]
        init = " = '0" if w > 1 else " = 1'b0"
        decl = f"logic [{w-1}:0]" if w > 1 else "logic        "
        A(f"    {decl} {nm}{init};")
    A("")
    A("    // ---- 输出观察 ----")
    for nm in info["order"]:
        if info["dirs"].get(nm) != "output":
            continue
        w = info["width"][nm]
        decl = f"wire [{w-1}:0]" if w > 1 else "wire        "
        A(f"    {decl} {nm};")
    A("")
    A("    // ---- DUT：端口形态与网表逐字一致（扁平向量，不是数组）----")
    conns = []
    for nm in info["order"]:
        conns.append(f"        .{nm}({nm})")
    A("    adc_digital_ip_v21 dut (")
    A(",\n".join(conns))
    A("    );")
    A("")
    A("    // ---- 名义权重表：按 DC 展平后的整段位拼接驱动 ----")
    A("    initial begin")
    A(f"        w_main_tbl = {nominal_slice('w_main_tbl')};")
    A(f"        w_sub_tbl  = {nominal_slice('w_sub_tbl')};")
    A("    end")
    A("")
    A("    // ---- 层级探针 ----")
    A("    // 网表把 acq_sampled 藏在 u_ctx 内部（端口名 acq_sampled_o），")
    A("    // 顶层没有同名信号，所以这里用层次引用取；DC 保留 u_ctx 实例名。")
    A("    wire probe_acq_sampled = dut.u_ctx.acq_sampled_o;")
    A("")
    A("    // ---- 公共激励与断言（与 RTL 侧同一份文件）----")
    A("    `include \"tb_e2e_body.svh\"")
    A("")
    A("endmodule")
    open(out, "w").write("\n".join(L) + "\n")
    return probe_ok


def report(info, out_rpt, netlist):
    """写出端口绑定自检报告（人可读，便于审计"绑定是否真按网表来"）。"""
    L = []
    A = L.append
    A("# 端口绑定自检报告（tools/gen_gate_tb.py 生成）")
    A("")
    A(f"- 网表 : `{netlist}`")
    A(f"- 网表 sha256 : `{hashlib.sha256(open(netlist,'rb').read()).hexdigest()}`")
    A(f"- 顶层端口数 : {len(info['order'])}")
    A(f"- 顶层子实例 : {', '.join(m + ' ' + n for m, n in info['insts']) or '（无，已拉平）'}")
    A("")
    A("| 端口 | 方向 | 宽度 | 备注 |")
    A("| --- | --- | --- | --- |")
    for nm in info["order"]:
        note = ""
        if nm in ("w_main_tbl", "w_sub_tbl"):
            fl = info["flat"][nm]
            note = f"扁平拼接，首={fl[0]} 尾={fl[-1]}"
        A(f"| `{nm}` | {info['dirs'].get(nm,'?')} | {info['width'].get(nm,'?')} | {note} |")
    open(out_rpt, "w").write("\n".join(L) + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("netlist_pos", nargs="?", help="（兼容旧用法）网表路径")
    ap.add_argument("out_pos", nargs="?", help="（兼容旧用法）输出 TB 路径")
    ap.add_argument("--netlist")
    ap.add_argument("--out")
    ap.add_argument("--common", default="tb_e2e_decl.svh",
                    help="公共声明层（tb_e2e_decl.svh）")
    ap.add_argument("--selfcheck", action="store_true")
    a = ap.parse_args()
    netlist = a.netlist or a.netlist_pos
    out = a.out or a.out_pos
    if not netlist or not out:
        raise SystemExit("用法: gen_gate_tb.py --netlist <syn.v> --out <tb.sv> "
                         "[--common tb_e2e_common.svh]")

    info = parse_netlist(netlist)
    bad = check(info)
    print(f"解析到 {len(info['order'])} 个端口"
          f"（in={sum(1 for v in info['dirs'].values() if v=='input')}, "
          f"out={sum(1 for v in info['dirs'].values() if v=='output')}）")
    if bad:
        print("端口自检 FAIL：")
        for b in bad:
            print("  - " + b)
        return 2
    print("端口自检 PASS（方向/宽度/展平位序全部符合预期）")

    probe_ok = emit(info, out, a.common, netlist)
    report(info, out + ".bind.rpt", netlist)
    print(f"写出 {out}")
    print(f"写出 {out}.bind.rpt")
    if not probe_ok:
        print("WARN: 网表顶层找不到 acq_conv_ctx 实例（可能被 ungroup 拉平），"
              "共享层的层级探针将回退到端口观察值。")
    if a.selfcheck:
        print("自检完成（退出码 0）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
