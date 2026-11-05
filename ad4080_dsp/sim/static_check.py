#!/usr/bin/env python3
"""对 AD4080 顶层测试连接进行有限的文本交叉检查。

检查 TB 与 ad4080_top 的端口名和位宽文本、TB 使用的寄存器地址是否
出现在 ad4080_regs 的 case 中，以及顶层子模块实例的命名端口。

这是正则/字符级检查，不运行 HDL 仿真或 elaboration，也不计算参数和宏
展开后的位宽。结果须与 TB_NOTES.md 一起阅读；退出码 0 表示本检查
未发现失败，1 表示存在失败，不构成 HDL 协议或物理实现验证。
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RTL = ROOT.parent / "rtl"
TB = ROOT / "tb" / "ad4080_top_tb.sv"

results = []  # (ok, name, detail)


def add(ok, name, detail=""):
    """将 ``(ok, name, detail)`` 追加到模块级结果列表，不执行被记录的检查。"""
    results.append((ok, name, detail))


def strip_comments(s):
    """用正则删除 // 和 /* */ 注释，避免说明文字参与端口匹配。

    这不是 SystemVerilog 词法器，不在此函数中验证或保留字符串语义。
    """
    s = re.sub(r"/\*.*?\*/", " ", s, flags=re.S)
    s = re.sub(r"//[^\n]*", " ", s)
    return s


def read(p):
    """读取 UTF-8 文件并剥离注释，解码错误使用替代字符。"""
    return strip_comments(p.read_text(encoding="utf-8", errors="replace"))


def match_paren(s, start):
    """返回 s 中与 start 处左圆括号匹配的位置，未匹配时返回 -1。

    调用方须保证 ``s[start] == "("``；本函数按字符计数，不解析 HDL。
    """
    depth = 0
    for i in range(start, len(s)):
        if s[i] == "(":
            depth += 1
        elif s[i] == ")":
            depth -= 1
            if depth == 0:
                return i
    return -1


def split_top_commas(s):
    """按顶层逗号分割文本，保留嵌套括号中的逗号。"""
    out, depth, cur = [], 0, ""
    for ch in s:
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        if ch == "," and depth == 0:
            out.append(cur)
            cur = ""
        else:
            cur += ch
    if cur.strip():
        out.append(cur)
    return out


def module_ports(path):
    """读取模块头的端口名与位宽文本，不计算实际 elaboration 位宽。

    Args:
        path: 待按 UTF-8 读取的 SystemVerilog 文件路径。

    Returns:
        ``{module_name: [(port_name, width_or_None), ...]}``；位宽保留为
        去空格后的文本，无显式位宽时为 None。
    """
    txt = read(path)
    res = {}
    for m in re.finditer(r"\bmodule\s+(\w+)", txt):
        name = m.group(1)
        # 模块头在最外层第一个分号处结束，嵌套参数/端口括号不提前终止。
        i = m.end()
        depth = 0
        end = len(txt)
        # 先定位完整头部，再单独去掉参数块。
        while i < len(txt):
            c = txt[i]
            if c in "([{":
                depth += 1
            elif c in ")]}":
                depth -= 1
            elif c == ";" and depth == 0:
                end = i
                break
            i += 1
        header = txt[m.end() : end]
        # 去掉 #( ... ) 参数块，仅保留端口声明。
        p = header.find("#")
        if p >= 0:
            q = header.find("(", p)
            if q >= 0:
                e = match_paren(header, q)
                if e > 0:
                    header = header[:p] + header[e + 1 :]
        op = header.find("(")
        cp = header.rfind(")")
        if op < 0 or cp < 0:
            res[name] = []
            continue
        ports = []
        cur_dir = None
        cur_w = None
        for chunk in split_top_commas(header[op + 1 : cp]):
            c = " ".join(chunk.split())
            if not c:
                continue
            mm = re.match(r"^(input|output|inout)\b", c)
            if mm:
                cur_dir = mm.group(1)
                c2 = c[len(cur_dir) :].strip()
                wm = re.search(r"(\[[^\]]*\])", c2)
                cur_w = wm.group(1).replace(" ", "") if wm else None
                # 提取位宽文本后，从剩余文本读取端口名。
                c2 = re.sub(r"\[[^\]]*\]", " ", c2)
                toks = c2.split()
                nm = toks[-1] if toks else ""
                nm = nm.split("=")[0].strip()
                if nm:
                    ports.append((nm, cur_w))
            else:
                # 连续端口声明沿用上一个方向和位宽，例如 sdo_oe 或 ser_frame。
                c2 = re.sub(r"\[[^\]]*\]", " ", c)
                toks = c2.split()
                if toks:
                    nm = toks[-1].split("=")[0].strip()
                    if re.fullmatch(r"[A-Za-z_]\w*", nm):
                        ports.append((nm, cur_w))
        res[name] = ports
    return res


# A: 核对顶层 TB 的命名连接及位宽文本，不展开参数。
tb_txt = read(TB)
top_txt = RTL / "ad4080_top.sv"

m = re.search(r"\bad4080_top\b", tb_txt)
top_conns = []
if m:
    # 从模块名之后寻找实例端口表。
    seg = tb_txt[m.end() :]
    p = seg.find("(")
    depth = 0
    # 定位含 .port(...) 的连接括号。
    idx = seg.find(".")
    inst = seg[seg.find("(") :]
    # 本检查从 .clk 起截取到第一个 );，只覆盖当前 TB 使用的命名连接形式。
    k = seg.find(".clk")
    if k > 0:
        q = seg.find(");", k)
        body = seg[k:q]
        top_conns = re.findall(r"\.(\w+)\s*\(", body)

rtl_txt = read(top_txt)
rtl_mods = module_ports(top_txt)
rtl_ports = rtl_mods.get("ad4080_top", [])
rtl_names = [n for n, _ in rtl_ports]
rtl_w = dict(rtl_ports)

missing = [n for n in top_conns if n not in rtl_names]
extra = [n for n in rtl_names if n not in top_conns]

add(
    not missing,
    "A1 TB 连接的端口在 RTL 中全部存在",
    "缺失: " + ", ".join(missing) if missing else "共 %d 个端口全部命中" % len(top_conns),
)
add(not extra, "A2 RTL 端口在 TB 中全部连接", "未连接: " + ", ".join(extra) if extra else "无遗漏")

# 位宽仅比较文本表达式，不计算其 elaboration 数值。
wbad = []
for n in top_conns:
    if n not in rtl_w:
        continue
    # 查找该信号的 logic 声明，可带显式位宽。
    dm = re.search(rf"logic\s*(\[[^\]]*\])?\s*{re.escape(n)}\s*[;,=]", tb_txt)
    tw = dm.group(1).replace(" ", "") if (dm and dm.group(1)) else None
    rw = rtl_w[n]
    if rw is not None and tw is not None and tw != rw:
        wbad.append(f"{n}: TB{tw} vs RTL{rw}")
    if rw is not None and tw is None:
        wbad.append(f"{n}: TB 无位宽 vs RTL{rw}")
    if rw is None and tw is not None:
        wbad.append(f"{n}: TB{tw} vs RTL 无位宽")
add(not wbad, "A3 端口位宽一致", "; ".join(wbad) if wbad else "全部一致")

# B: 收集 case 地址并检查 TB 引用，不证明寄存器读写语义。
regs_txt = read(RTL / "ad4080_regs.sv")
wr_addrs = set(re.findall(r"16'h([0-9A-Fa-f]{4})\s*:", regs_txt))
# 读译码位于第二个 case 块；此处合并收集文件内全部地址标签。
tb_addrs = sorted(set(re.findall(r"spi_(?:write|read)\s*\(\s*15'h([0-9A-Fa-f]{4})", tb_txt)))
tb_addrs = sorted({a.upper() for a in tb_addrs})
wr_addrs_u = {a.upper() for a in wr_addrs}
addr_bad = [a for a in tb_addrs if a not in wr_addrs_u]
add(
    not addr_bad,
    "B1 TB 使用的寄存器地址在 ad4080_regs 中有 case 分支",
    "未实现: " + ", ".join(addr_bad)
    if addr_bad
    else "检查 %d 个地址: %s" % (len(tb_addrs), ", ".join("0x" + a for a in tb_addrs)),
)

# C: 检查顶层子模块实例所用的命名端口，不证明连接完备或协议正确。
all_mods = {}
for f in sorted(RTL.glob("*.sv")):
    all_mods.update(module_ports(f))

inst_bad = []
inst_cnt = 0
for mname, ports in all_mods.items():
    if mname == "ad4080_top":
        continue
    pnames = [n for n, _ in ports]
    for im in re.finditer(rf"\b{re.escape(mname)}\b", rtl_txt):
        # 排除 module NAME 定义本身，只检查实例。
        prev = rtl_txt[max(0, im.start() - 12) : im.start()]
        if re.search(r"module\s*$", prev):
            continue
        j = im.end()
        while j < len(rtl_txt) and rtl_txt[j].isspace():
            j += 1
        if j >= len(rtl_txt):
            continue
        if rtl_txt[j] == "#":
            q = rtl_txt.find("(", j)
            if q < 0:
                continue
            e = match_paren(rtl_txt, q)
            if e < 0:
                continue
            j = e + 1
        pm = re.match(r"\s*(\w+)\s*\(", rtl_txt[j:])
        if not pm:
            continue
        inst_name = pm.group(1)
        pstart = j + pm.end() - 1
        pend = match_paren(rtl_txt, pstart)
        if pend < 0:
            continue
        body = rtl_txt[pstart:pend]
        conns = re.findall(r"\.(\w+)\s*\(", body)
        miss = [c for c in conns if c not in pnames]
        if miss:
            inst_bad.append("{}({}) 端口不存在: {}".format(mname, inst_name, ", ".join(miss)))
        inst_cnt += 1

add(
    not inst_bad,
    "C1 顶层子模块实例端口名合法",
    "; ".join(inst_bad) if inst_bad else "检查 %d 个实例" % inst_cnt,
)

# 汇总有限文本检查结果，不将其标为仿真通过。
print("=" * 72)
print(" AD4080 top TB 静态自检（非仿真）")
print(" RTL: %s" % (RTL / "ad4080_top.sv"))
print(f" TB : {TB}")
print("=" * 72)
npass = sum(1 for ok, _, _ in results if ok)
nfail = len(results) - npass
for ok, name, detail in results:
    print("[{}] {}".format("PASS" if ok else "FAIL", name))
    if detail:
        print(f"       {detail}")
print("-" * 72)
print("PASS=%d FAIL=%d" % (npass, nfail))
sys.exit(0 if nfail == 0 else 1)
