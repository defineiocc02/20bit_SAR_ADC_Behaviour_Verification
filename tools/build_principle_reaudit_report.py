"""Build this source-bound standalone audit; no external TeX project files."""

import argparse
import hashlib
import json
import re
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("--repo", type=Path, required=True)
ap.add_argument("--out", type=Path, required=True)
a = ap.parse_args()
repo = a.repo.resolve()
frozen = json.loads(
    (repo / "docs/evidence/20261002/principle_reaudit/input_manifest.json").read_text()
)
for entry in frozen["rtl"]:
    if hashlib.sha256((repo / entry["path"]).read_bytes()).hexdigest() != entry["sha256"]:
        raise ValueError(f"Source differs from frozen report: {entry['path']}")
esc = {
    "\\": r"\textbackslash{}",
    "&": r"\&",
    "%": r"\%",
    "$": r"\$",
    "#": r"\#",
    "_": r"\_",
    "{": r"\{",
    "}": r"\}",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
}
mathmap = {
    "δ": r"\ensuremath{\delta}",
    "β": r"\ensuremath{\beta}",
    "Σ": r"\ensuremath{\sum}",
    "±": r"\ensuremath{\pm}",
    "×": r"\ensuremath{\times}",
    "≤": r"\ensuremath{\le}",
    "≥": r"\ensuremath{\ge}",
    "∈": r"\ensuremath{\in}",
    "√": r"\ensuremath{\sqrt{\;}}",
    "Ĝ": r"\ensuremath{\hat{G}}",
    "Ω": r"\ensuremath{\Omega}",
    "γ": r"\ensuremath{\gamma}",
    "≈": r"\ensuremath{\approx}",
    "→": r"\ensuremath{\rightarrow}",
    "−": r"\ensuremath{-}",
    "∼": r"\ensuremath{\sim}",
    "①": "(1)",
    "②": "(2)",
    "③": "(3)",
    "④": "(4)",
}
code_map = {
    "δ": "delta",
    "β": "beta",
    "Σ": "sum",
    "±": "+/-",
    "×": "x",
    "≤": "<=",
    "≥": ">=",
    "∈": "in",
    "√": "sqrt",
    "Ĝ": "Ghat",
    "Ω": "ohm",
    "γ": "gamma",
    "≈": "~",
    "→": "->",
    "−": "-",
    "∼": "~",
}


def plain(s):
    pieces = []
    pos = 0
    for m in re.finditer(r"[A-Za-z0-9][A-Za-z0-9_/.:,+=><-]{16,}", s):
        pieces.append("".join(mathmap.get(c, esc.get(c, c)) for c in s[pos : m.start()]))
        pieces.append(r"\code{" + m.group() + "}")
        pos = m.end()
    pieces.append("".join(mathmap.get(c, esc.get(c, c)) for c in s[pos:]))
    return "".join(pieces)


def inline(s):
    pattern = r"(`[^`]+`|\[[^\]]+\]\([^\)]+\)|\*\*[^*]+\*\*)"
    out = []
    pos = 0
    for m in re.finditer(pattern, s):
        out.append(plain(s[pos : m.start()]))
        t = m.group()
        if t.startswith("`"):
            value = (
                t[1:-1]
                .replace(str(repo), "REPO")
                .replace(
                    "/Users/zhaoyi/Obsidan/项目库/outputs/sar_adc_principle_reaudit_20261002",
                    "AUDIT_OUT",
                )
            )
            value = "".join(code_map.get(c, c) for c in value)
            # url's balanced braces are not used in these source locators.
            if "{" in value or "}" in value:
                out.append(r"\texttt{" + plain(value) + "}")
            else:
                out.append(r"\code{" + value.replace("%", r"\%").replace("#", r"\#") + "}")
        elif t.startswith("**"):
            out.append(r"\textbf{" + inline(t[2:-2]) + "}")
        else:
            mm = re.fullmatch(r"\[([^\]]+)\]\(([^\)]+)\)", t)
            label, url = mm.groups()
            if url.startswith("http"):
                out.append(r"\href{" + url + "}{" + inline(label) + "}")
            else:
                out.append(inline(label) + r"（\code{" + url + "}）")
        pos = m.end()
    out.append(plain(s[pos:]))
    return "".join(out)


def cells(row):
    protected = []

    def save(m):
        protected.append(m.group())
        return f"@@CODE{len(protected)-1}@@"

    row = re.sub(r"`[^`]+`", save, row)
    cols = row.strip().strip("|").split("|")
    for n in range(len(cols)):
        for i, t in enumerate(protected):
            cols[n] = cols[n].replace(f"@@CODE{i}@@", t)
        cols[n] = cols[n].strip()
    return cols


def convert(s):
    lines = s.splitlines()
    out = []
    i = 0
    while i < len(lines):
        t = lines[i]
        if i == 0 and t.startswith("# "):
            i += 1
            continue
        if not t.strip():
            out.append("\n")
            i += 1
            continue
        if t.startswith("!["):
            i += 1
            continue  # Original self-contained vectors below.
        if t.startswith("$$"):
            val = t[2:]
            i += 1
            if val.endswith("$$"):
                val = val[:-2]
            else:
                while i < len(lines) and not lines[i].endswith("$$"):
                    val += "\n" + lines[i]
                    i += 1
                if i < len(lines):
                    val += "\n" + lines[i][:-2]
                    i += 1
            out.append(r"\[" + val + r"\]")
            continue
        if t.startswith("```"):
            content = []
            i += 1
            while i < len(lines) and not lines[i].startswith("```"):
                content.append(lines[i])
                i += 1
            out.append(
                r"\begin{quote}\small\ttfamily\raggedright"
                + "\n"
                + r"\\".join(inline(x) for x in content)
                + "\n"
                + r"\end{quote}"
            )
            i += 1
            continue
        if t.startswith("|"):
            rows = []
            while i < len(lines) and lines[i].startswith("|"):
                rows.append(cells(lines[i]))
                i += 1
            head = rows[0]
            body = rows[2:]
            assert all(len(x) == len(head) for x in rows), head
            n = len(head)
            if (any("模块" in x for x in head) and len(body) > 8) or n >= 6:
                for row in body:
                    out.append(r"\subsection*{" + inline(row[0]) + "}")
                    for label, value in zip(head[1:], row[1:], strict=False):
                        out.append(
                            r"\noindent\textbf{" + inline(label) + "}：" + inline(value) + r"\par"
                        )
            else:
                frac = {
                    3: [0.25, 0.32, 0.39],
                    4: [0.19, 0.24, 0.27, 0.26],
                    5: [0.2, 0.15, 0.17, 0.16, 0.28],
                    6: [0.16, 0.16, 0.16, 0.16, 0.16, 0.16],
                }.get(n, [0.96 / n] * n)
                pre = r"\begingroup\small\setlength{\tabcolsep}{2pt}\renewcommand{\arraystretch}{1.25}"
                spec = (
                    "@{}"
                    + "".join(
                        ">{\\raggedright\\arraybackslash}p{" + str(round(x, 3)) + r"\linewidth}"
                        for x in frac
                    )
                    + "@{}"
                )
                out.append(
                    pre
                    + r"\begin{longtable}{"
                    + spec
                    + "}\n"
                    + r"\toprule"
                    + "\n"
                    + " & ".join(r"\textbf{" + inline(x) + "}" for x in head)
                    + r"\\\midrule\endhead"
                )
                out.extend(
                    " & ".join(inline(x) for x in row) + r"\\\addlinespace[2pt]" for row in body
                )
                out.append(r"\bottomrule\end{longtable}\endgroup")
            continue
        m = re.match(r"^(#{2,4})\s+(.*)", t)
        if m:
            lev, title = m.groups()
            title = re.sub(r"^\d+(?:\.\d+)*[. ]\s*", "", title)
            cmd = {2: "section", 3: "subsection", 4: "subsubsection"}[len(lev)]
            out.append("\\" + cmd + "{" + inline(title) + "}")
            i += 1
            continue
        if re.match(r"^[-*] ", t) or re.match(r"^\d+\. ", t):
            content = []
            while i < len(lines) and (
                re.match(r"^[-*] ", lines[i]) or re.match(r"^\d+\. ", lines[i])
            ):
                content.append(re.sub(r"^(?:[-*]|\d+\.)\s+", "", lines[i]))
                i += 1
            out.append(r"\begin{itemize}")
            out.extend(r"\item " + inline(x) for x in content)
            out.append(r"\end{itemize}")
            continue
        if t.startswith("> "):
            t = t[2:]
        out.append(inline(t))
        i += 1
    return "\n".join(out)


preamble = r"""\documentclass[UTF8,fontset=fandol,10pt,a4paper,openany]{ctexrep}
\usepackage[margin=22mm,headheight=15pt]{geometry}
\usepackage{amsmath,amssymb,booktabs,longtable,array,graphicx,xcolor,hyperref,fancyhdr,enumitem,tikz}
\usetikzlibrary{arrows.meta,positioning}
\hypersetup{colorlinks=true,linkcolor=blue!45!black,urlcolor=blue!45!black}
\setlength{\parindent}{2em}\setlength{\parskip}{3pt}\setlength{\emergencystretch}{3em}
\setlist{nosep,leftmargin=2em}\raggedright\setlength{\parindent}{2em}
\pagestyle{fancy}\fancyhf{}\fancyhead[L]{SAR ADC 原理、RTL 功能与综合电路再核对}\fancyhead[R]{2026-10-02}\fancyfoot[C]{\thepage}
\newcommand{\code}[1]{\nolinkurl{#1}}
\title{20-bit SAR ADC 原理再核对\\RTL 逐模块功能与综合电路评估\\\large 对既有115页报告的独立复核补充}
\author{基于原始论文、专利页图、冻结源码与可复现见证}
\date{2026 年 10 月 2 日\\源基线：\code{71935ab54238c7ed638f5fa66c5ffb58e10287bb}\\生产 RTL 未修改；无新综合、布线或 ASIC 签核}
\begin{document}\maketitle
\begin{abstract}
本报告重新核对原文与全部24个生产RTL模块、两个参数头文件，推导静态电荷模型与精确整数重构，并区分原文明示、工程选择、模拟宏边界和历史实测。新的限域见证澄清ADC2增量舍入与绝对中心舍入、配置合法域与MAC工作域、模拟错误归属及15T/1T捕获时窗。综合结构公式与历史P5网表对应，权重库候选62316FF占历史全核FF的93.72\%，真实布线最差路径互连占77.875\%。历史核心25ns setup/hold通过，外部OOC hold仍失败；默认P7未取得本轮新物理结果。文中提供全部模块功能、位宽/状态/运算树与硬件风险、三幅原创数据图及综合、STA、CTS、布线的分阶段验收计划。已有数字功能证据不能推出论文模拟指标、全部专利实施例或ASIC640MHz/PPA优势。
\end{abstract}\tableofcontents
\chapter{集成复核与工程判断}
"""
main = (repo / "docs/rtl/PRINCIPLE_AND_SYNTHESIS_REAUDIT_20261002.md").read_text()
# PNG figures have original embedded vector substitutes, without external files.
main = main[: main.index("## 9. 原始数据图")]
parts = [preamble, convert(main), r"\chapter{原创数据图与证据身份}"]
parts.append(r"""以下矢量图直接嵌入本独立源文件，数字来自封存JSON/CSV；完整高分辨率PNG与PDF及其生成器另行交付。图1/2是历史P5，图3是本轮有限控制wrapper，无模拟波形、建立测量或MCP签核结论。
\section{历史P5状态存储分解}
\begin{center}\begin{tikzpicture}[x=1cm,y=1cm]
\fill[blue!70] (0,0) rectangle (10.840282,0.8);
\fill[blue!40] (10.840282,0) rectangle (11.070926,0.8);
\fill[teal!65] (11.070926,0) rectangle (11.246345,0.8);
\fill[orange!80] (11.246345,0) rectangle (12,0.8);
\node[text=white] at (5.4,0.4) {系数数据 60,066 FF};
\draw[->] (0,-0.2)--(12.3,-0.2);
\foreach \pos/\label in {0/0,3.609457/20000,7.218914/40000,10.828371/60000}{\draw (\pos,-.2)--(\pos,-.35) node[below]{\small\label};}
\node[align=left,anchor=north west] at (0,-1.1) {loaded 1,278；row cache 972；其他 4,176\\权重库合计 62,316 / 全核 66,492 = 93.72\%};
\end{tikzpicture}\end{center}
这是源级位数与历史\code{u_wstore}实测一致的分解，不是ASIC标准单元面积或功耗。原始来源为\code{71e7d5a}的P5 Vivado报告，当前源有限token相同不能替代新物理签核。
\section{两条不同端点关键路径的延迟组成}
\begin{center}\begin{tikzpicture}[x=.44cm,y=1cm]
\fill[blue!65] (0,1.4) rectangle (8.404,2.1);\fill[orange!75] (8.404,1.4) rectangle (14.312,2.1);
\fill[blue!65] (0,0) rectangle (5.291,.7);\fill[orange!75] (5.291,0) rectangle (23.914,.7);
\node[anchor=east] at (-.3,1.75) {post-synth};\node[anchor=east] at (-.3,.35) {post-route};
\node[text=white] at (4.2,1.75) {8.404};\node at (11.3,1.75) {5.908};
\node[text=white] at (2.65,.35) {5.291};\node at (14.6,.35) {18.623};
\node[anchor=west] at (14.5,1.75) {14.312 ns};\node[anchor=west] at (24.1,.35) {23.914 ns};
\draw[->] (0,-.3)--(27,-.3);\foreach \v in {0,5,10,15,20,25}{\draw (\v,-.3)--(\v,-.45) node[below]{\small\v};}
\end{tikzpicture}\end{center}
蓝色为cell；橙色为net。post-synth为divider端点和估计互连，post-route为ID到rails端点和实际布线。真实路径route占77.875\%。不可用其倒数报告Fmax，也不能把不同端点的两行当成同一逻辑被布线恶化的精确因果实验。
\section{有限接受轨迹的15T/1T证据}
\begin{center}\begin{tikzpicture}[x=.2cm,y=.25cm]
\draw[->] (0,0)--(60,0);\draw[->] (0,0)--(0,18);
\foreach \n in {1,...,57}{\fill[blue!65] (\n,15) circle[x radius=.15cm,y radius=.12cm];\fill[orange!80] (\n,1) rectangle +(1,1);}
\foreach \n in {1,10,20,30,40,50,57}{\node[below] at (\n,-1) {\small\n};}
\node[left] at (-1,15) {15T};\node[left] at (-1,1) {1T};
\node[anchor=west] at (4,10) {蓝：context；橙：fine code};
\end{tikzpicture}\end{center}
CSV有1926沿、57接受；全部context更新后15T、fine code锁存后1T。首接受edge66/ID3：context在edge51 quiet/P0更新，fine code在edge65/P14更新。stageA为影子观察FF；未实例化真实weight/recon数据通路，未覆盖完整cfg-clear/re-enable或全序列形式证明。故不能据此直接写15/16周期例外。
\appendix
""")
for name, title in [
    ("calibration_audit.md", "校准原理与九模块功能详解"),
    ("control_patent_audit.md", "控制、采样与专利原理详解"),
    ("synthesis_structure_audit.md", "全部24模块及综合结构详解"),
]:
    parts.append(r"\chapter{" + title + "}")
    parts.append(convert((repo / "docs/rtl/reaudit_20261002" / name).read_text()))
parts.append(r"\end{document}")
a.out.parent.mkdir(parents=True, exist_ok=True)
a.out.write_text("\n".join(parts) + "\n")
print("STANDALONE_LATEX_CREATED " + str(a.out))
