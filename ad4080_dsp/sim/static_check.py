#!/usr/bin/env python3
"""static_check.py -- 无仿真器时的端口/寄存器静态自检

检查项
  A. TB 里 ad4080_top 的端口连接名 vs rtl/ad4080_top.sv 的端口声明（缺失/多余/位宽）
  B. TB 用到的 SPI 寄存器地址 vs rtl/ad4080_regs.sv 的读写 case 分支
  C. ad4080_top 内部每个子模块实例的端口名 vs 该子模块的端口声明

这不是仿真，只做名字/位宽的文本级交叉检查。结论必须与 TB_NOTES.md 一起看。
退出码: 0 全过, 1 有 FAIL
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RTL = ROOT.parent / "rtl"
TB = ROOT / "tb" / "ad4080_top_tb.sv"

results = []  # (ok, name, detail)


def add(ok, name, detail=""):
    """记录一条自检结果与说明。"""
    results.append((ok, name, detail))


def strip_comments(s):
    """去掉 // 与 /* */ 注释，避免注释文本干扰端口匹配。"""
    s = re.sub(r"/\*.*?\*/", " ", s, flags=re.S)
    s = re.sub(r"//[^\n]*", " ", s)
    return s


def read(p):
    """读取文件并剥离注释后返回文本。"""
    return strip_comments(p.read_text(encoding="utf-8", errors="replace"))


def match_paren(s, start):
    """s[start] == '(' ; return index of matching ')'"""
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
    """按顶层逗号切分，忽略括号内部的逗号。"""
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
    """Return {module: [(name, width_or_None), ...]} for all modules in file"""
    txt = read(path)
    res = {}
    for m in re.finditer(r"\bmodule\s+(\w+)", txt):
        name = m.group(1)
        # header ends at first ';' at depth 0
        i = m.end()
        depth = 0
        end = len(txt)
        # skip parameter block if present
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
        # remove '#( ... )' parameter block
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
                # drop width from text
                c2 = re.sub(r"\[[^\]]*\]", " ", c2)
                toks = c2.split()
                nm = toks[-1] if toks else ""
                nm = nm.split("=")[0].strip()
                if nm:
                    ports.append((nm, cur_w))
            else:
                # continuation: "sdo_oe" or "ser_data, ser_frame"
                c2 = re.sub(r"\[[^\]]*\]", " ", c)
                toks = c2.split()
                if toks:
                    nm = toks[-1].split("=")[0].strip()
                    if re.fullmatch(r"[A-Za-z_]\w*", nm):
                        ports.append((nm, cur_w))
        res[name] = ports
    return res


# ---------------------------------------------------------------- A: top ports
tb_txt = read(TB)
top_txt = RTL / "ad4080_top.sv"

m = re.search(r"\bad4080_top\b", tb_txt)
top_conns = []
if m:
    # find "#(" after name then the instance port list
    seg = tb_txt[m.end() :]
    p = seg.find("(")
    depth = 0
    # locate the connection paren that contains ".port("
    idx = seg.find(".")
    inst = seg[seg.find("(") :]
    # simpler: take text up to the first ');' after '.clk'
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

# width compare (text level)
wbad = []
for n in top_conns:
    if n not in rtl_w:
        continue
    # find TB declaration of this net: "logic [..] name" or "logic name"
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

# ------------------------------------------------------- B: register addresses
regs_txt = read(RTL / "ad4080_regs.sv")
wr_addrs = set(re.findall(r"16'h([0-9A-Fa-f]{4})\s*:", regs_txt))
# read mux is the second case block; collect all case labels in file already done.
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

# ------------------------------------------------- C: submodule instantiations
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
        # skip the module definition itself ("module NAME")
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

# ---------------------------------------------------------------- report
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
