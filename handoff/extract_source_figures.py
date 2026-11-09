# =============================================================================
# 文件名     : extract_source_figures.py
# 模块名     : handoff.extract_source_figures
# 版本       : v1.0
# 功能       : 从用户提供的原件截取任务书所需原图，并记录页码、裁剪与摘要。
# 设计说明   :
#   [授权] 按本任务明确要求使用少量原图片段，不复制原文全文或修改电路几何。
#   [定位] 页码为PDF从1开始计数；专利Sheet号另在manifest登记。
#   [保真] 只裁剪页眉／空白，不加电路或改变符号；缩放与源分辨率分别记录。
#   [复现] 专利PDF不随本包复制，可通过命令行显式给出已获授权的原件。
# 变更记录   : 初始版本，暂无变更记录。
# =============================================================================

"""原件节选与来源登记；图版权仍归原权利人。

输入必须是已核对版本的原件：专利从命令行提供，论文／讲稿读取仓库已有资料。
另一版本的合并页、重排页或OCR导出PDF不能仅靠同一文件名直接套用裁剪坐标。

输出的PNG保持原图颜色、节点、标号和方向；只去页眉或外围空白，横放原稿可转正。
灰度阈值只用于寻找墨迹包围框，不对输出像素作二值化，避免抹掉彩色与浅灰子图。
每个矩形都需同源页目视核对，程序不据图号自动判断完整电路范围。

source_figures.json的pdf_page从1计数，文字页与附图页分别登记。
clip_fraction是相对页宽／高的坐标，clip_pt是PDF点，ink_crop_pixels是渲染后像素。
rotation_deg是裁剪后转正角，output_size_px是最终像素尺寸，二者不改变连接关系。
source_sha256和output_sha256分别标识原件与节选，不用文件名或修改时间替代身份。
render_scale表示PDF点到像素的比例；扫描原件放大不增加原生信息量。

text_excerpts只登记已逐条核对的短句和位置，不把OCR全文当作已校对原文发布。
原件版本改变时必须重新核对短句、分幅和页码，不能沿用旧图上的原文定位。
缺原件时抛出FileNotFoundError，空分幅抛出ValueError，不以自绘或搜索缩略图代替。
公开记录只包含原件basename，不把维护者的绝对路径带入交付包。
"""

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pymupdf
from PIL import Image

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent


def digest(path):
    """逐字节绑定原件，不用文件名代替来源身份。"""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def crop(path, page_number, fractions, scale, target, rotation=0):
    """在声明页的归一矩形内渲染，再裁去外圈空白。

    Args:
        path: 用户提供的PDF原件路径。
        page_number: PDF页码，从1开始，不能拿印刷栏号代替。
        fractions: 左、上、右、下的页宽／页高比例，均为0到1。
        scale: PDF点到输出像素的比例；不是原始位图信息量的提升。
        target: 输出PNG路径，保留原图方向与连接关系。
        rotation: 原稿横放时的转正角，单位度；不改变图内节点相对关系。

    Returns:
        页尺寸、裁剪坐标、输出尺寸及原件／图片摘要。

    分幅矩形须目视核对，墨迹定框只去空白，不能自动判定电路是否完整。
    """
    doc = pymupdf.open(path)
    page = doc[page_number - 1]
    w, h = page.rect.width, page.rect.height
    clip = pymupdf.Rect(fractions[0] * w, fractions[1] * h, fractions[2] * w, fractions[3] * h)
    pixmap = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), clip=clip, alpha=False)
    image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
    ink = np.asarray(image.convert("L")) < 210
    rows, columns = np.where(ink)
    if not len(rows):
        raise ValueError("empty source crop")
    # 只保留既定分幅里的墨迹及20像素边距；不重新排列子图。
    box = (
        max(0, int(columns.min()) - 20),
        max(0, int(rows.min()) - 20),
        min(image.width, int(columns.max()) + 21),
        min(image.height, int(rows.max()) + 21),
    )
    result = image.crop(box).rotate(rotation, expand=True)
    result.save(target)
    return {
        "source_basename": path.name,
        "source_sha256": digest(path),
        "pdf_page": page_number,
        "page_size_pt": [w, h],
        "clip_fraction": list(fractions),
        "clip_pt": list(clip),
        "render_scale": scale,
        "rotation_deg": rotation,
        "ink_crop_pixels": list(box),
        "output_size_px": list(result.size),
        "output_sha256": digest(target),
    }


def main():
    """接受原件路径，输出有限节选，不下载或打包完整原文。"""
    parser = argparse.ArgumentParser(description=__doc__)
    literature = Path.home() / "Downloads/ISSCC2024_9.8_参考文献全集"
    parser.add_argument(
        "--dem-pdf",
        type=Path,
        default=literature / "[11]_US10511316B2_Dynamic_Element_Matching.pdf",
    )
    parser.add_argument(
        "--dither-pdf",
        type=Path,
        default=literature / "[10]_US10505561B2_Dither_Method_and_ADC.pdf",
    )
    args = parser.parse_args()
    out = HERE / "figures"
    out.mkdir(exist_ok=True)
    # Sheet编号来自逐页目视；不根据图号推算PDF页码。
    entries = [
        (
            "source_dem_fig21.png",
            args.dem_pdf,
            21,
            (0.0, 0.12, 1.0, 1.0),
            1.0,
            "US10511316B2 Fig.21; Sheet 19 of 29",
        ),
        (
            "source_dither_fig19.png",
            args.dither_pdf,
            22,
            (0.08, 0.18, 0.90, 0.64),
            1.0,
            "US10505561B2 Fig.19 diagram; Sheet 20 of 21; figure-label whitespace excluded",
        ),
        (
            "source_dither_slide17.png",
            ROOT / "references/ISSCC2024_SLIDES.pdf",
            17,
            (0.57, 0.20, 0.995, 0.86),
            5.0,
            "ISSCC 2024 slide 17 right-hand injection diagram",
        ),
        (
            "source_caaz_fig6.png",
            ROOT / "references/PKU_VLSI2025_C32_1_PAPER.pdf",
            2,
            (0.503, 0.653, 0.94, 0.947),
            6.0,
            "VLSI 2025 C32-1 Fig.6, printed page 2",
        ),
    ]
    manifest = {
        "scope": "user-requested original figure excerpts; no circuit redrawing",
        "figures": {},
    }
    for name, source, number, clip, scale, description in entries:
        if not source.is_file():
            raise FileNotFoundError(source.name)
        record = crop(source, number, clip, scale, out / name)
        record["description"] = description
        record["rights"] = (
            "Original rights holders; excerpt for mechanism explanation; not covered by repository software license"
        )
        manifest["figures"][name] = record
    # 电容结构、重排开关和控制编码分别取图，不用单张框图替代电路说明。
    detailed = [
        (
            "source_dem_fig11.png",
            args.dem_pdf,
            12,
            (0.0, 0.12, 1.0, 1.0),
            1.0,
            -90,
            "US10511316B2 Fig.11; PDF page 12; sliced capacitor network",
        ),
        (
            "source_dem_fig20.png",
            args.dem_pdf,
            20,
            (0.0, 0.12, 1.0, 1.0),
            1.0,
            -90,
            "US10511316B2 Fig.20; PDF page 20; multiplexer/shuffler circuit",
        ),
        (
            "source_dither_fig3.png",
            args.dither_pdf,
            5,
            (0.07, 0.13, 0.95, 0.68),
            1.0,
            0,
            "US10505561B2 Fig.3; PDF page 5; basic subtractive dither",
        ),
        (
            "source_dither_fig5.png",
            args.dither_pdf,
            7,
            (0.07, 0.13, 0.95, 0.68),
            1.0,
            0,
            "US10505561B2 Fig.5; PDF page 7; split dither and output correction",
        ),
        (
            "source_isscc_architecture.png",
            ROOT / "references/ISSCC2024_PAPER.pdf",
            2,
            (0.01, 0.036, 0.492, 0.347),
            7.0,
            0,
            "ISSCC 2024 Fig.9.8.1; PDF page 2, printed page 183",
        ),
        (
            "source_caaz_fig5.png",
            ROOT / "references/PKU_VLSI2025_C32_1_PAPER.pdf",
            2,
            (0.061, 0.653, 0.501, 0.947),
            6.0,
            0,
            "VLSI 2025 C32-1 Fig.5; PDF and printed page 2",
        ),
        (
            "source_caaz_slide35.png",
            ROOT / "references/PKU_VLSI2025_C32_1_SLIDES.pdf",
            35,
            (0.06, 0.25, 0.94, 0.92),
            5.0,
            0,
            "VLSI 2025 PDF page 35, slide 34; shared AZ and difference",
        ),
    ]
    for name, source, number, clip, scale, rotation, description in detailed:
        record = crop(source, number, clip, scale, out / name, rotation)
        record["description"] = description
        record["rights"] = "Original rights holders; user-requested mechanism excerpt"
        manifest["figures"][name] = record
    # 短句的文字页与附图页分开登记；不能把Fig.21图页当作正文位置。
    manifest["text_excerpts"] = [
        {
            "source": "US10511316B2",
            "pdf_page": 42,
            "printed_location": "column 21, closing paragraph of Fig.21 description",
            "quote": "This shuffling scheme further reduces the impact of size errors between the cells and gives rise to further improvements in INL errors.",
        },
        {
            "source": "ISSCC2024_PAPER.pdf",
            "pdf_page": 1,
            "printed_location": "page 182, DEM/dither paragraph",
            "quote": "Dither is used to complement the DEM linearization of the DAC.",
        },
        {
            "source": "US10505561B2",
            "pdf_page": 26,
            "printed_location": "column 5, Fig.3 description",
            "quote": "The dither signal is formed in the digital domain by a pseudo-random number generator 40.",
        },
        {
            "source": "PKU_VLSI2025_C32_1_PAPER.pdf",
            "pdf_page": 1,
            "printed_location": "Architecture and Circuit Implementation, Fig.6 discussion",
            "quotes": [
                "AZ is only performed in the even cycle",
                "two consecutive conversion results contain the same sampled noise",
            ],
        },
    ]
    (HERE / "source_figures.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    )
    print({name: row["output_size_px"] for name, row in manifest["figures"].items()})


if __name__ == "__main__":
    main()
