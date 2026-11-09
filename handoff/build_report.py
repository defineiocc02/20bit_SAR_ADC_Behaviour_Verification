# =============================================================================
# 文件名     : build_report.py
# 模块名     : handoff.build_report
# 版本       : v1.0
# 功能       : 绘制六个月计划图，将阅读稿转成LaTeX并编译、记录版面证据。
# 设计说明   :
#   [范围] 只读取既有实验图和任务书，不运行ADC仿真或任何EDA签核。
#   [依赖] 重转Markdown需要md2latex.py；--pdf-only可直接编译已交付TeX。
#   [来源] 所有图像逐字节引用，计划图只表达安排，不能被解释为完成状态。
#   [构建] XeLaTeX禁shell escape并运行三遍；临时中间文件自动清理。
# 变更记录   : 初始版本，暂无变更记录。
# =============================================================================

"""生成计划图和可复现PDF；统计只验证文档，不验证芯片。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import matplotlib.pyplot as plt
import pymupdf
from matplotlib import font_manager

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent


def digest(path):
    """返回文件SHA256；摘要反映实际字节，不复用其他版本的记录。"""
    return hashlib.sha256(path.read_bytes()).hexdigest()


# =============================================================================
# 1. 计划图：月份是相对里程碑，不是已完成进度
# =============================================================================
def plan_figure():
    """按146 mm物理宽度出图，数字和中文分别采用可用的真实字体。

    Returns:
        计划、验收链和系数负例三类图的PNG／PDF／SVG项目内文件路径。

    只呈现任务依赖与验收点，不生成任何虚构的性能曲线或通过状态。
    正确系数的数值从各模式原始summary读取，错配来自显式负例JSON。
    不同映射模式各自训练，图不暗示一组权重可任意跨模式使用。
    诊断图不能作为DEM关／开的公平性能对照或论文收益复现。
    配置负例只改变重构系数组，其输入、固定电容池和ADC2原码保持同组归档。
    错误系数字段本身可能合法，不能以未溢出代替模式匹配验收。
    """
    for family in ("Arial", "Hiragino Sans GB"):
        font_manager.findfont(font_manager.FontProperties(family=family), fallback_to_default=False)
    plt.rcParams.update(
        {
            "font.family": ["Arial", "Hiragino Sans GB"],
            "font.size": 8,
            "pdf.fonttype": 42,
            "axes.unicode_minus": False,
        }
    )
    fig, ax = plt.subplots(figsize=(146 / 25.4, 73 / 25.4))
    fig.subplots_adjust(left=0.23, right=0.985, top=0.86, bottom=0.22)
    tasks = ["调度／DEM", "八片dither", "CAAZ", "整体集成", "接口／验证"]
    # 各模块依次完成行为、RTL与物理交付；接口与回归贯穿全程。
    spans = [(0.5, 1), (1.5, 2), (3.5, 1), (4.5, 2), (0.5, 6)]
    colors = ["#9a9a9a", "#0072B2", "#D55E00", "#009E73", "#666666"]
    for i, ((start, width), color) in enumerate(zip(spans, colors, strict=True)):
        ax.broken_barh([(start, width)], (4 - i - 0.25, 0.5), facecolors=color)
    for edge in range(1, 7):
        ax.axvline(edge + 0.5, color="#dddddd", lw=0.4, zorder=0)
    # 里程碑标的是计划验收时点；原有数据和未完成项在正文分别列出。
    for month, label in [(1, "DEM GDS"), (3, "dither GDS"), (4, "CAAZ GDS"), (6, "整体 GDS")]:
        ax.plot(month, 4.65, marker="v", color="black", ms=4, clip_on=False)
        ax.text(month, 5.02, label, ha="center", va="bottom", fontsize=7)
    ax.set(xlim=(0.5, 6.5), ylim=(-0.55, 4.7), yticks=range(5), yticklabels=tasks[::-1])
    ax.set_xticks(range(1, 7), [f"M{i}" for i in range(1, 7)])
    ax.tick_params(axis="both", length=0)
    for spine in ax.spines.values():
        spine.set_visible(False)
    fig.text(0.23, 0.06, "Python／RTL回归、原文核对、版本与原始证据：M1–M6持续进行", fontsize=7)
    output = []
    for ext in ("png", "pdf", "svg"):
        path = HERE / "figures" / f"nine_month_plan.{ext}"
        fig.savefig(path, dpi=400)
        output.append(path)
    plt.close(fig)
    # 签核链只画流程关系；所有方框都是待执行要求，没有PASS色标。
    fig, ax = plt.subplots(figsize=(146 / 25.4, 31 / 25.4))
    ax.set(xlim=(0, 146), ylim=(0, 31))
    ax.axis("off")
    nodes = [
        "SPEC冻结\n接口／算术",
        "RTL验证\n仿真／形式",
        "综合与实现\n等价／STA",
        "最终GDS\nDRC／LVS／PI",
    ]
    for i, label in enumerate(nodes):
        x = 2 + 37 * i
        ax.add_patch(plt.Rectangle((x, 8), 30, 17, fill=False, lw=0.75))
        ax.text(x + 15, 16.5, label, ha="center", va="center", fontsize=8)
        if i < 3:
            ax.annotate(
                "", (x + 37, 16.5), (x + 30, 16.5), arrowprops={"arrowstyle": "->", "lw": 0.75}
            )
    ax.text(73, 2, "每步绑定同一版本与原始证据；PI包括电源连通、IR与EM", ha="center", fontsize=7)
    fig.subplots_adjust(left=0, right=1, bottom=0, top=1)
    for ext in ("png", "pdf", "svg"):
        path = HERE / "figures" / f"signoff_flow.{ext}"
        fig.savefig(path, dpi=400)
        output.append(path)
    plt.close(fig)
    # 只使用已归档的正确配置与明确错配诊断；错配不作为“无DEM”基线。
    diagnostics = json.loads((HERE / "coefficient_mode_diagnostic.json").read_text())
    correct, wrong, labels = [], [], []
    for sigma in (2, 3):
        summary = json.loads(
            (ROOT / f"task2/evidence/eight_negative/sigma_{sigma}pct/summary.json").read_text()
        )
        for actual, other, label in [
            ("no_dem", "fig21_dem", "无DEM"),
            ("fig21_dem", "no_dem", "Fig.21"),
        ]:
            labels.append(f"{sigma}%／{label}")
            correct.append(summary["groups"][actual]["tone"]["off"]["lms"]["sndr_db"])
            wrong.append(diagnostics[f"{sigma}pct_{actual}_using_{other}_weights"]["sndr_db"])
    fig, ax = plt.subplots(figsize=(146 / 25.4, 43 / 25.4))
    fig.subplots_adjust(left=0.1, right=0.99, bottom=0.25, top=0.98)
    for positions, values, color, label in [
        ([i - 0.18 for i in range(4)], correct, "#0072B2", "匹配系数"),
        ([i + 0.18 for i in range(4)], wrong, "#999999", "错配系数负例"),
    ]:
        ax.bar(positions, values, 0.36, color=color, label=label)
        for x, value in zip(positions, values, strict=True):
            ax.text(x, value + 1, f"{value:.1f}", ha="center", fontsize=7)
    ax.set(
        xticks=range(4), xticklabels=labels, ylim=(0, 150), yticks=[0, 50, 100], ylabel="SNDR（dB）"
    )
    ax.spines[["top", "right"]].set_visible(False)
    ax.legend(fontsize=7, loc="upper center", ncol=2, frameon=False)
    # 图是配置错误演示，未提出新的算法性能或硬件通过结论。
    for ext in ("png", "pdf", "svg"):
        path = HERE / "figures" / f"coefficient_mode.{ext}"
        fig.savefig(path, dpi=400)
        output.append(path)
    plt.close(fig)
    return output


# =============================================================================
# 2. 转换与编译：路径由当前项目解析，不嵌入维护者的工作目录
# =============================================================================
def convert(converter, temporary):
    """读取MD并生成TeX；任务及技术说明主节分别分页，统一表格宽度。

    Args:
        converter: 已存在的md2latex.py路径，不下载或修改第三方工具。
        temporary: 本次私有临时目录，退出时由调用方清理。

    长表采用本任务书的有限行数和宽度；若正文增加，须重新检查分页。
    """
    source = (HERE / "NINE_MONTH_PLAN.md").read_text()
    body = source.split("\n", 1)[1]
    body = re.sub(r"^(#{2,6}) ", lambda m: m[1][1:] + " ", body, flags=re.M)
    assert len(re.findall(r"^# ", body, re.M)) == 25, "twenty-five task and technical sections"
    input_md = temporary / "body.md"
    input_md.write_text(body)
    target = HERE / "NINE_MONTH_PLAN.tex"
    subprocess.run(
        [
            sys.executable,
            str(converter),
            str(input_md),
            "--template-file",
            str(HERE / "report_template.tex"),
            "--out",
            str(target),
            "--force",
        ],
        check=True,
        cwd=HERE,
    )
    tex = target.read_text()
    parts = tex.split(r"\section{")
    assert len(parts) == 26
    tex = (
        parts[0]
        + r"\section{"
        + parts[1]
        + "".join("\n\\clearpage\n\\section{" + p for p in parts[2:])
    )
    # ASCII单词之间允许换行，避免转换器的不断行空格推挤表格。
    tex = re.sub(r"(?<=[A-Za-z0-9])~(?=[A-Za-z0-9])", " ", tex)
    tex = tex.replace(r"\begin{table}[H]", r"\begin{table}[H]" + "\n\\small")
    column = r">{\raggedright\arraybackslash}X"
    for weights in [(0.6, 1.4), (0.65, 1.25, 1.1), (0.45, 1.25, 1.25, 1.05)]:
        weighted = "".join(
            r">{\hsize=" + str(w) + r"\hsize\linewidth=\hsize\raggedright\arraybackslash}X"
            for w in weights
        )
        tex = tex.replace("{" + column * len(weights) + "}", "{" + weighted + "}")
    # 在当前工作树编译，图仍指向原归档文件；不复制或重新渲染科学数据。
    assert tex.count(r"\includegraphics") == 17
    target.write_text(tex)
    return target


def compile_pdf(engine, temporary):
    """运行三遍XeLaTeX，返回最后日志；失败立即停止，不保留旧PDF当新结果。"""
    environment = dict(os.environ)
    environment["PATH"] = str(engine.parent) + os.pathsep + environment.get("PATH", "")
    commands = [
        str(engine),
        "-no-shell-escape",
        "-interaction=nonstopmode",
        "-halt-on-error",
        f"-output-directory={temporary}",
        "NINE_MONTH_PLAN.tex",
    ]
    for _ in range(3):
        run = subprocess.run(commands, cwd=HERE, env=environment, text=True, capture_output=True)
        if run.returncode:
            raise RuntimeError(run.stdout[-9000:] + run.stderr[-2000:])
    log = (temporary / "NINE_MONTH_PLAN.log").read_text(errors="replace")
    # 原始日志属于文档构建证据；不把它混成数字仿真或STA日志。
    # 发布日志只替换机器路径，保留所有诊断；原始内容可由同一命令重建。
    public_log = log.replace(str(temporary), "${BUILD}")
    public_log = public_log.replace(str(ROOT), "${REPO}").replace(str(Path.home()), "${HOME}")
    (HERE / "build.log").write_text(public_log)
    for marker in ("Missing character:", "Overfull \\vbox", "Undefined control sequence"):
        if marker in log:
            raise RuntimeError(f"document check failed: {marker}")
    shutil.copy2(temporary / "NINE_MONTH_PLAN.pdf", HERE / "NINE_MONTH_PLAN.pdf")
    return log


# =============================================================================
# 3. 几何量与身份记录，目视检查另行执行
# =============================================================================
def record(log):
    """记录页数、嵌图dpi和文件身份；程序检查不替代逐页目视。"""
    pdf = pymupdf.open(HERE / "NINE_MONTH_PLAN.pdf")
    pages = []
    images = []
    for number, page in enumerate(pdf, 1):
        for image in page.get_image_info():
            box = pymupdf.Rect(image["bbox"])
            images.append(
                {"page": number, "dpi": image["width"] / box.width * 72, "bbox": list(box)}
            )
        pages.append(
            {
                "page": number,
                "characters": len(page.get_text()),
                "images": len(page.get_image_info()),
            }
        )
    # 只断言所有图落入PDF和基本分辨率；页数变动需人工判断是否出现孤页。
    assert len(images) == 17 and min(i["dpi"] for i in images) >= 300
    files = [
        HERE / p
        for p in [
            "NINE_MONTH_PLAN.md",
            "NINE_MONTH_PLAN.tex",
            "NINE_MONTH_PLAN.pdf",
            "report_template.tex",
            "build_report.py",
            "source_figures.json",
            "behavioral_audit.json",
            "coefficient_mode_diagnostic.json",
        ]
    ]
    # 把实际嵌入的旧图也绑定到文档身份，防止同名换图后仍沿用旧构建结论。
    image_paths = re.findall(
        r"\\includegraphics\[[^]]+\]\{([^}]+)\}", (HERE / "NINE_MONTH_PLAN.tex").read_text()
    )
    files.extend((HERE / p).resolve() for p in image_paths)
    result = {
        "scope": "document build only; no new ADC/RTL/physical verification",
        "pages": pages,
        "images": images,
        "overfull_hbox": len(re.findall(r"Overfull \\hbox", log)),
        "source_sha256": {str(p.relative_to(ROOT)): digest(p) for p in files},
    }
    (HERE / "build_record.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps(
            {"pages": len(pdf), "images": len(images), "overfull_hbox": result["overfull_hbox"]}
        )
    )


def main():
    """默认重转并编译；--pdf-only只需现有TeX、字体、图片与XeLaTeX。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf-only", action="store_true")
    parser.add_argument(
        "--converter",
        type=Path,
        default=Path.home() / ".agents/skills/md2latex/scripts/md2latex.py",
    )
    parser.add_argument(
        "--engine",
        type=Path,
        default=Path(
            shutil.which("xelatex") or Path.home() / "Library/TinyTeX/bin/universal-darwin/xelatex"
        ),
    )
    args = parser.parse_args()
    if not args.engine.is_file() or not args.pdf_only and not args.converter.is_file():
        raise SystemExit(
            "需要已有XeLaTeX；重转MD另需--converter指定md2latex.py。不会自动安装工具。"
        )
    (HERE / "figures").mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="sar-taskbook-") as directory:
        temporary = Path(directory)
        if not args.pdf_only:
            plan_figure()
            convert(args.converter, temporary)
        record(compile_pdf(args.engine, temporary))


if __name__ == "__main__":
    main()
